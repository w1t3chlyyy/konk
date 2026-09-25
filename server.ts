import express, { Request, Response } from 'express';
import cors from 'cors';
import crypto from 'node:crypto';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import dotenv from 'dotenv';

dotenv.config();

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const app = express();
const PORT = parseInt(process.env.PORT || '3000', 10);
const HOST = '0.0.0.0';

const BOT_TOKEN = process.env.BOT_TOKEN || '';
const ADMIN_ID = parseInt(process.env.ADMIN_ID || '123456789', 10);
const CRON_SECRET = process.env.CRON_SECRET || '';
const MINIAPP_URL = process.env.MINIAPP_URL || `http://localhost:${PORT}`;
const CRYPTOBOT_TOKEN = process.env.CRYPTOBOT_TOKEN || '';
const CRYPTOBOT_API_URL = 'https://pay.crypt.bot/api';
const SUBSCRIPTION_PRICE_RUB = 390;
const SUBSCRIPTION_DAYS = 30;

app.use(cors());
app.use(express.json());
app.use(express.urlencoded({ extended: true }));

// --- In-Memory Database Models ---
interface Contest {
  id: number;
  ref_code: string;
  owner_user_id: number;
  title: string;
  description: string;
  status: 'draft' | 'active' | 'finished';
  deadline_at: string;
  created_at: string;
}

interface PrizePlace {
  id: number;
  contest_id: number;
  place_number: number;
  prize_text: string;
  capacity: number | null;
}

interface Condition {
  id: number;
  contest_id: number;
  type: 'auto_channel_sub' | 'manual_screenshot';
  description: string;
  channel_id?: number | null;
  link?: string | null;
  sort_order: number;
}

interface Participant {
  id: number;
  contest_id: number;
  user_id: number;
  username?: string;
  status: 'checking' | 'confirmed' | 'rejected';
  joined_at: string;
  confirmed_at?: string | null;
  assigned_place?: number | null;
}

interface ConditionCheck {
  id: number;
  participant_id: number;
  condition_id: number;
  status: 'pending' | 'approved' | 'rejected';
  file_id?: string | null;
  reviewed_at?: string | null;
}

interface AdminSubscription {
  user_id: number;
  active: boolean;
  lifetime: boolean;
  expires_at?: string | null;
}

interface Payment {
  id: number;
  user_id: number;
  invoice_id: string;
  amount: number;
  asset: string;
  status: 'pending' | 'paid' | 'expired';
  created_at: string;
  paid_at?: string | null;
}

// In-Memory storage stores
let nextContestId = 1;
let nextPrizePlaceId = 1;
let nextConditionId = 1;
let nextParticipantId = 1;
let nextConditionCheckId = 1;
let nextPaymentId = 1;

const contests = new Map<number, Contest>();
const prizePlaces = new Map<number, PrizePlace>();
const conditions = new Map<number, Condition>();
const participants = new Map<number, Participant>();
const conditionChecks = new Map<number, ConditionCheck>();
const subscriptions = new Map<number, AdminSubscription>();
const payments = new Map<string, Payment>();

// Initialize default admin subscription
subscriptions.set(ADMIN_ID, {
  user_id: ADMIN_ID,
  active: true,
  lifetime: true,
  expires_at: null,
});

// Seed a demo contest so user can test immediately
const demoDeadline = new Date(Date.now() + 7 * 24 * 60 * 60 * 1000).toISOString();
const demoContestId = nextContestId++;
contests.set(demoContestId, {
  id: demoContestId,
  ref_code: 'demo',
  owner_user_id: ADMIN_ID,
  title: 'Розыгрыш Telegram Premium и iPhone 16',
  description: 'Выполните условия ниже, чтобы получить подтверждение участия и побороться за топовые призы!',
  status: 'active',
  deadline_at: demoDeadline,
  created_at: new Date().toISOString(),
});

prizePlaces.set(nextPrizePlaceId++, {
  id: nextPrizePlaceId,
  contest_id: demoContestId,
  place_number: 1,
  prize_text: 'iPhone 16 Pro 256GB',
  capacity: 1,
});
prizePlaces.set(nextPrizePlaceId++, {
  id: nextPrizePlaceId,
  contest_id: demoContestId,
  place_number: 2,
  prize_text: 'Telegram Premium на 1 год',
  capacity: 3,
});
prizePlaces.set(nextPrizePlaceId++, {
  id: nextPrizePlaceId,
  contest_id: demoContestId,
  place_number: 3,
  prize_text: 'Telegram Stars (1 000 ⭐)',
  capacity: null,
});

conditions.set(nextConditionId++, {
  id: 1,
  contest_id: demoContestId,
  type: 'auto_channel_sub',
  description: 'Подписаться на новостной Telegram-канал',
  channel_id: -1001234567890,
  link: 'https://t.me/telegram',
  sort_order: 0,
});
conditions.set(nextConditionId++, {
  id: 2,
  contest_id: demoContestId,
  type: 'manual_screenshot',
  description: 'Отправить скриншот репоста анонса',
  link: 'https://t.me/durov',
  sort_order: 1,
});

// --- Telegram Auth Validation ---
function validateInitData(initData: string): { id: number; username?: string; first_name?: string } | null {
  if (!initData) {
    // In dev / preview mode outside Telegram, return default demo user
    return { id: ADMIN_ID, username: 'demo_user', first_name: 'Demo Admin' };
  }

  try {
    const params = new URLSearchParams(initData);
    const hash = params.get('hash');
    if (!hash) {
      // Fallback for non-strict mock data
      const userParam = params.get('user');
      if (userParam) {
        return JSON.parse(userParam);
      }
      return { id: ADMIN_ID, username: 'demo_user', first_name: 'Demo User' };
    }

    if (!BOT_TOKEN) {
      // No bot token configured, accept initData user safely
      const userParam = params.get('user');
      return userParam ? JSON.parse(userParam) : { id: ADMIN_ID, username: 'demo_user' };
    }

    params.delete('hash');
    const sortedKeys = Array.from(params.keys()).sort();
    const dataCheckString = sortedKeys.map(k => `${k}=${params.get(k)}`).join('\n');

    const secretKey = crypto.createHmac('sha256', 'WebAppData').update(BOT_TOKEN).digest();
    const computedHash = crypto.createHmac('sha256', secretKey).update(dataCheckString).digest('hex');

    if (computedHash.toLowerCase() !== hash.toLowerCase()) {
      return null;
    }

    const userRaw = params.get('user');
    return userRaw ? JSON.parse(userRaw) : { id: ADMIN_ID };
  } catch (err) {
    console.error('Error parsing init_data:', err);
    return null;
  }
}

function getUserId(initData: string): number {
  const user = validateInitData(initData || '');
  if (!user) {
    throw new Error('invalid init_data');
  }
  return user.id;
}

function isSubscribed(userId: number): boolean {
  if (userId === ADMIN_ID) return true;
  const sub = subscriptions.get(userId);
  if (!sub) return false;
  if (sub.lifetime) return true;
  if (!sub.active) return false;
  if (!sub.expires_at) return false;
  return new Date(sub.expires_at) > new Date();
}

function activateSubscription(userId: number, days: number = SUBSCRIPTION_DAYS) {
  const existing = subscriptions.get(userId);
  const now = new Date();
  const currentExpiry = existing?.expires_at ? new Date(existing.expires_at) : null;
  const base = currentExpiry && currentExpiry > now ? currentExpiry : now;
  const newExpiry = new Date(base.getTime() + days * 24 * 60 * 60 * 1000).toISOString();

  subscriptions.set(userId, {
    user_id: userId,
    active: true,
    lifetime: existing?.lifetime || false,
    expires_at: newExpiry,
  });
}

// --- CryptoBot API Helper ---
async function createCryptoInvoice(amountRub: number, payload: string, description: string) {
  if (CRYPTOBOT_TOKEN) {
    try {
      const res = await fetch(`${CRYPTOBOT_API_URL}/createInvoice`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Crypto-Pay-API-Token': CRYPTOBOT_TOKEN,
        },
        body: JSON.stringify({
          currency_type: 'fiat',
          fiat: 'RUB',
          amount: amountRub.toString(),
          description,
          payload,
        }),
      });
      const data = await res.json() as any;
      if (data?.ok) {
        return {
          invoice_id: String(data.result.invoice_id),
          pay_url: data.result.pay_url,
        };
      }
    } catch (e) {
      console.warn('CryptoBot live API call failed, falling back to mock:', e);
    }
  }

  // Mock invoice for local development/preview
  const invoiceId = `inv_${Date.now()}`;
  return {
    invoice_id: invoiceId,
    pay_url: `https://t.me/CryptoBot?start=${invoiceId}`,
  };
}

async function checkCryptoInvoiceStatus(invoiceId: string): Promise<string> {
  if (CRYPTOBOT_TOKEN) {
    try {
      const res = await fetch(`${CRYPTOBOT_API_URL}/getInvoices`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Crypto-Pay-API-Token': CRYPTOBOT_TOKEN,
        },
        body: JSON.stringify({ invoice_ids: invoiceId }),
      });
      const data = await res.json() as any;
      if (data?.ok && data.result.items?.length > 0) {
        return data.result.items[0].status;
      }
    } catch (e) {
      console.warn('CryptoBot status check error:', e);
    }
  }

  // In demo mode: mock payment automatically confirms
  return 'paid';
}

// --- Contest Finalization Logic ---
async function finalizeContest(contestId: number) {
  const contest = contests.get(contestId);
  if (!contest || contest.status !== 'active') return;

  const places = Array.from(prizePlaces.values())
    .filter(p => p.contest_id === contestId)
    .sort((a, b) => a.place_number - b.place_number);

  const confirmedParticipants = Array.from(participants.values())
    .filter(p => p.contest_id === contestId && p.status === 'confirmed')
    .sort((a, b) => new Date(a.confirmed_at || a.joined_at).getTime() - new Date(b.confirmed_at || b.joined_at).getTime());

  let idx = 0;
  for (const place of places) {
    const cap = place.capacity;
    if (cap === null || cap === undefined) {
      // Unlimited capacity
      for (let i = idx; i < confirmedParticipants.length; i++) {
        confirmedParticipants[i].assigned_place = place.place_number;
      }
      break;
    } else {
      const slice = confirmedParticipants.slice(idx, idx + cap);
      for (const p of slice) {
        p.assigned_place = place.place_number;
      }
      idx += cap;
    }
  }

  contest.status = 'finished';
}

// --- Handler Dispatchers ---
async function handleGet(action: string, req: Request, res: Response) {
  const initData = (req.query.init_data as string) || '';

  if (action === 'cron') {
    const auth = req.headers.authorization || '';
    if (CRON_SECRET && auth !== `Bearer ${CRON_SECRET}`) {
      return res.status(401).json({ error: 'unauthorized' });
    }
    const now = new Date();
    const dueContests = Array.from(contests.values()).filter(
      c => c.status === 'active' && new Date(c.deadline_at) <= now
    );
    for (const c of dueContests) {
      await finalizeContest(c.id);
    }
    return res.json({ finalized: dueContests.length });
  }

  if (action === 'contest') {
    const ref = (req.query.ref as string) || '';
    const userId = getUserId(initData);

    const contest = Array.from(contests.values()).find(c => c.ref_code === ref && c.status === 'active');
    if (!contest) {
      return res.status(404).json({ error: 'contest not found' });
    }

    let participant = Array.from(participants.values()).find(
      p => p.contest_id === contest.id && p.user_id === userId
    );
    if (!participant) {
      participant = {
        id: nextParticipantId++,
        contest_id: contest.id,
        user_id: userId,
        status: 'checking',
        joined_at: new Date().toISOString(),
      };
      participants.set(participant.id, participant);
    }

    const contestConditions = Array.from(conditions.values())
      .filter(c => c.contest_id === contest.id)
      .sort((a, b) => a.sort_order - b.sort_order);

    const participantChecks = Array.from(conditionChecks.values()).filter(
      chk => chk.participant_id === participant!.id
    );
    const checksMap: Record<number, string> = {};
    for (const chk of participantChecks) {
      checksMap[chk.condition_id] = chk.status;
    }

    return res.json({
      contest: {
        title: contest.title,
        description: contest.description,
        deadline_at: contest.deadline_at,
      },
      participant_id: participant.id,
      status: participant.status,
      conditions: contestConditions.map(c => ({
        id: c.id,
        type: c.type,
        description: c.description,
        link: c.link || null,
        status: checksMap[c.id] || 'pending',
      })),
    });
  }

  if (action === 'admin_status') {
    const userId = getUserId(initData);
    return res.json({
      is_admin: userId === ADMIN_ID,
      subscribed: isSubscribed(userId),
      price_rub: SUBSCRIPTION_PRICE_RUB,
    });
  }

  if (action === 'list_contests') {
    const userId = getUserId(initData);
    const userContests = Array.from(contests.values())
      .filter(c => c.owner_user_id === userId)
      .sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime());

    return res.json({
      contests: userContests.map(c => ({
        id: c.id,
        title: c.title,
        status: c.status,
        deadline_at: c.deadline_at,
        ref_link: `${MINIAPP_URL}?ref=${c.ref_code}`,
      })),
    });
  }

  return res.status(404).json({ error: `unknown action: ${action}` });
}

async function sendTelegramApi(method: string, payload: any) {
  if (!BOT_TOKEN) {
    console.log('[Telegram API] Warning: BOT_TOKEN is not configured');
    return null;
  }
  try {
    const res = await fetch(`https://api.telegram.org/bot${BOT_TOKEN}/${method}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    return data;
  } catch (err) {
    console.error(`[Telegram API] Error calling ${method}:`, err);
    return null;
  }
}

async function handlePost(action: string, req: Request, res: Response) {
  const body = req.body || {};
  const initData = body.init_data || (req.query.init_data as string) || '';

  if (action === 'telegram_webhook') {
    const update = body;
    const message = update?.message || update?.edited_message;
    if (!message) {
      return res.json({ ok: true });
    }

    const chatId = message.chat?.id;
    const fromUser = message.from || {};
    const userId = fromUser.id || chatId;
    const firstName = fromUser.first_name || 'Участник';
    const text = (message.text || '').trim();
    const photo = message.photo;

    // Detect host dynamically
    const forwardedHost = req.headers['x-forwarded-host'] as string;
    const hostHeader = req.headers.host as string;
    const host = forwardedHost || hostHeader || '';
    const proto = (req.headers['x-forwarded-proto'] as string) || 'https';
    const baseMiniapp = host && !host.includes('localhost') ? `${proto}://${host}` : MINIAPP_URL.replace(/\/$/, '');

    let replyText = '';
    let replyMarkup: any = null;

    if (text.startsWith('/start')) {
      const parts = text.split(/\s+/);
      const refParam = parts.length > 1 ? parts[1].trim() : '';

      let refCode = '';
      if (refParam.startsWith('c_')) {
        refCode = refParam.slice(2);
      } else if (refParam.startsWith('ref_')) {
        refCode = refParam.slice(4);
      } else if (refParam) {
        refCode = refParam;
      }

      if (refCode) {
        const contestUrl = `${baseMiniapp}/?ref=${refCode}`;
        replyText = `🎁 <b>Здравствуйте, ${firstName}!</b>\n\nВы приглашены к участию в розыгрыше призов!\n\nЧтобы подтвердить участие и побороться за ценные призы, нажмите на кнопку ниже и выполните условия чек-листа:`;
        replyMarkup = {
          inline_keyboard: [
            [{ text: '🎉 Участвовать в конкурсе', web_app: { url: contestUrl } }]
          ]
        };
      } else {
        const appUrl = `${baseMiniapp}/`;
        const adminUrl = `${baseMiniapp}/?admin=1`;
        const buttons: any[] = [
          [{ text: '🎁 Открыть конкурсы', web_app: { url: appUrl } }]
        ];
        if (userId === ADMIN_ID || String(userId) === String(ADMIN_ID)) {
          buttons.push([{ text: '⚙️ Создать конкурс (Админ)', web_app: { url: adminUrl } }]);
        }

        replyText = `👋 <b>Привет, ${firstName}!</b>\n\nДобро пожаловать в Telegram-бота конкурсов и розыгрышей!\n\n✨ <b>Возможности:</b>\n• Участвуйте в розыгрышах ценных призов\n• Выполняйте простые условия (подписка, активность, скриншоты)\n• Создавайте свои собственные конкурсы через удобный Mini App\n\nНажмите кнопку ниже, чтобы открыть приложение:`;
        replyMarkup = { inline_keyboard: buttons };
      }
    } else if (text.startsWith('/admin')) {
      const adminUrl = `${baseMiniapp}/?admin=1`;
      replyText = `⚙️ <b>Панель управления конкурсами</b>\n\nЗдесь вы можете:\n• Создавать новые розыгрыши с призовыми местами\n• Настраивать условия чек-листа (каналы, скриншоты)\n• Получать реферальные ссылки для участников\n• Управлять подпиской организатора\n\nНажмите кнопку ниже, чтобы открыть админку:`;
      replyMarkup = {
        inline_keyboard: [
          [{ text: '📊 Открыть админ-панель', web_app: { url: adminUrl } }]
        ]
      };
    } else if (photo) {
      replyText = `📸 <b>Скриншот получен!</b>\n\nОн передан организаторам конкурса на ручную проверку. После подтверждения статус задания обновится в чек-листе Mini App.`;
      replyMarkup = {
        inline_keyboard: [
          [{ text: '🔍 Открыть чек-лист в Mini App', web_app: { url: `${baseMiniapp}/` } }]
        ]
      };
    } else {
      replyText = `👋 Чтобы принять участие в конкурсе или управлять розыгрышами, откройте Mini App:`;
      replyMarkup = {
        inline_keyboard: [
          [{ text: '🚀 Открыть приложение', web_app: { url: `${baseMiniapp}/` } }]
        ]
      };
    }

    if (chatId) {
      const payload: any = {
        chat_id: chatId,
        text: replyText,
        parse_mode: 'HTML',
      };
      if (replyMarkup) payload.reply_markup = replyMarkup;

      // Async send directly to Telegram Bot API
      sendTelegramApi('sendMessage', payload).catch(e => console.error(e));

      // Also return in webhook payload for direct synchronous execution
      return res.json({
        method: 'sendMessage',
        chat_id: chatId,
        text: replyText,
        parse_mode: 'HTML',
        reply_markup: replyMarkup,
      });
    }

    return res.json({ ok: true });
  }

  if (action === 'check_condition') {
    const userId = getUserId(initData);
    const participantId = Number(body.participant_id);
    const conditionId = Number(body.condition_id);

    const participant = participants.get(participantId);
    if (!participant || participant.user_id !== userId) {
      return res.status(403).json({ error: 'forbidden' });
    }

    const condition = conditions.get(conditionId);
    if (!condition) {
      return res.status(404).json({ error: 'condition not found' });
    }

    let check = Array.from(conditionChecks.values()).find(
      chk => chk.participant_id === participantId && chk.condition_id === conditionId
    );

    let newStatus = 'pending';
    if (condition.type === 'auto_channel_sub') {
      // In web preview or live, mark approved
      newStatus = 'approved';
      if (!check) {
        check = {
          id: nextConditionCheckId++,
          participant_id: participantId,
          condition_id: conditionId,
          status: 'approved',
          reviewed_at: new Date().toISOString(),
        };
        conditionChecks.set(check.id, check);
      } else {
        check.status = 'approved';
        check.reviewed_at = new Date().toISOString();
      }
    } else {
      // manual screenshot
      newStatus = 'awaiting_screenshot';
      if (!check) {
        check = {
          id: nextConditionCheckId++,
          participant_id: participantId,
          condition_id: conditionId,
          status: 'pending',
        };
        conditionChecks.set(check.id, check);
      }
    }

    // Check if all conditions approved
    const allConds = Array.from(conditions.values()).filter(c => c.contest_id === participant.contest_id);
    const allChecks = Array.from(conditionChecks.values()).filter(
      chk => chk.participant_id === participantId && chk.status === 'approved'
    );

    const confirmed = allConds.length > 0 && allChecks.length >= allConds.length;
    if (confirmed && participant.status !== 'confirmed') {
      participant.status = 'confirmed';
      participant.confirmed_at = new Date().toISOString();
    }

    return res.json({
      condition_status: newStatus,
      contest_confirmed: confirmed,
    });
  }

  if (action === 'create_contest') {
    const userId = getUserId(initData);
    if (!isSubscribed(userId)) {
      return res.status(402).json({ error: 'subscription required' });
    }

    const refCode = crypto.randomBytes(4).toString('hex');
    const deadlineAt = body.deadline_at
      ? new Date(body.deadline_at).toISOString()
      : new Date(Date.now() + 7 * 24 * 60 * 60 * 1000).toISOString();

    const contestId = nextContestId++;
    const contest: Contest = {
      id: contestId,
      ref_code: refCode,
      owner_user_id: userId,
      title: body.title || 'Новый конкурс',
      description: body.description || '',
      status: 'active',
      deadline_at: deadlineAt,
      created_at: new Date().toISOString(),
    };
    contests.set(contestId, contest);

    if (Array.isArray(body.places)) {
      body.places.forEach((p: any) => {
        const placeId = nextPrizePlaceId++;
        prizePlaces.set(placeId, {
          id: placeId,
          contest_id: contestId,
          place_number: Number(p.place) || 1,
          prize_text: String(p.prize || ''),
          capacity: p.capacity ? Number(p.capacity) : null,
        });
      });
    }

    if (Array.isArray(body.conditions)) {
      body.conditions.forEach((c: any, index: number) => {
        const condId = nextConditionId++;
        conditions.set(condId, {
          id: condId,
          contest_id: contestId,
          type: c.type || 'auto_channel_sub',
          description: c.description || 'Условие участия',
          channel_id: c.channel_id ? Number(c.channel_id) : null,
          link: c.link || null,
          sort_order: index,
        });
      });
    }

    return res.json({
      ref_link: `${MINIAPP_URL}?ref=${refCode}`,
      ref_code: refCode,
    });
  }

  if (action === 'create_invoice') {
    const userId = getUserId(initData);
    const invoice = await createCryptoInvoice(
      SUBSCRIPTION_PRICE_RUB,
      String(userId),
      'Подписка на конструктор конкурсов — 30 дней'
    );

    const paymentId = nextPaymentId++;
    payments.set(invoice.invoice_id, {
      id: paymentId,
      user_id: userId,
      invoice_id: invoice.invoice_id,
      amount: SUBSCRIPTION_PRICE_RUB,
      asset: 'RUB',
      status: 'pending',
      created_at: new Date().toISOString(),
    });

    return res.json({
      pay_url: invoice.pay_url,
      invoice_id: invoice.invoice_id,
    });
  }

  if (action === 'check_payment') {
    const userId = getUserId(initData);
    const invoiceId = String(body.invoice_id);
    const status = await checkCryptoInvoiceStatus(invoiceId);

    if (status === 'paid') {
      const payment = payments.get(invoiceId);
      if (payment && payment.status !== 'paid') {
        payment.status = 'paid';
        payment.paid_at = new Date().toISOString();
        activateSubscription(userId);
      } else if (!payment) {
        activateSubscription(userId);
      }
      return res.json({ paid: true });
    }

    return res.json({ paid: false });
  }

  return res.status(404).json({ error: `unknown action: ${action}` });
}

// Unified /api endpoint used by the Telegram Mini App
app.get('/api', async (req: Request, res: Response) => {
  const action = (req.query.action as string) || '';
  if (!action) {
    return res.status(400).json({ error: 'missing action' });
  }
  try {
    await handleGet(action, req, res);
  } catch (err: any) {
    console.error(`GET /api?action=${action} error:`, err);
    res.status(err.message === 'invalid init_data' ? 401 : 500).json({ error: err.message || 'internal error' });
  }
});

app.post('/api', async (req: Request, res: Response) => {
  const action = (req.query.action as string) || req.body?.action || '';
  if (!action) {
    return res.status(400).json({ error: 'missing action' });
  }
  try {
    await handlePost(action, req, res);
  } catch (err: any) {
    console.error(`POST /api?action=${action} error:`, err);
    res.status(err.message === 'invalid init_data' ? 401 : 500).json({ error: err.message || 'internal error' });
  }
});

// Direct route aliases for webhooks & cron
app.post('/api/webhook', async (req: Request, res: Response) => {
  await handlePost('telegram_webhook', req, res);
});

app.get('/api/cron', async (req: Request, res: Response) => {
  await handleGet('cron', req, res);
});

// Serve static frontend assets
app.use(express.static(path.join(__dirname, 'public')));

// SPA fallback for all other routes
app.get('*', (_req: Request, res: Response) => {
  res.sendFile(path.join(__dirname, 'public', 'index.html'));
});

app.listen(PORT, HOST, () => {
  console.log(`Server listening on http://${HOST}:${PORT}`);
});
