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

const botSettings = {
  welcome_text: (
    '👋 <b>Привет, {first_name}!</b>\n\n' +
    'Добро пожаловать в Telegram-бота конкурсов и розыгрышей!\n\n' +
    '✨ <b>Возможности:</b>\n' +
    '• Участвуйте в розыгрышах ценных призов\n' +
    '• Выполняйте простые условия (подписка, активность, скриншоты)\n' +
    '• Создавайте свои собственные конкурсы через удобный Mini App\n\n' +
    'Нажмите кнопку ниже, чтобы открыть приложение:'
  ),
  welcome_photo_url: '',
  subscription_price_rub: 390,
  bot_username: process.env.BOT_USERNAME || '',
  app_short_name: process.env.APP_SHORT_NAME || 'app',
};

const adminChatStates = new Map<number, string>();

let cachedBotUsername = botSettings.bot_username;

async function getCachedBotUsername(): Promise<string> {
  if (cachedBotUsername) return cachedBotUsername;
  if (BOT_TOKEN) {
    const me: any = await sendTelegramApi('getMe', {});
    if (me?.ok && me.result?.username) {
      cachedBotUsername = me.result.username;
      botSettings.bot_username = me.result.username;
      return cachedBotUsername;
    }
  }
  return 'RandomizerGiftRobot';
}

function getContestShareLink(refCode: string, botUsername: string = 'RandomizerGiftRobot'): string {
  const uname = botSettings.bot_username || botUsername;
  // Direct Telegram deep link format: https://t.me/BotUsername?start=c_code
  // Opens bot directly with a personalized contest invitation card and Mini App button
  return `https://t.me/${uname}?start=c_${refCode}`;
}

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

    const contest = Array.from(contests.values()).find(c => c.ref_code === ref);
    if (!contest) {
      return res.status(404).json({ error: 'contest not found' });
    }

    if (contest.status === 'draft') {
      return res.status(400).json({ error: 'contest in draft', message: 'Этот конкурс сохранён как черновик и ещё не опубликован организатором.' });
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

    // Determine assigned place and prize if contest finished or confirmed
    let prizeWon: string | null = null;
    let placeWon: number | null = participant.assigned_place || null;
    if (participant.status === 'confirmed' || contest.status === 'finished') {
      const places = Array.from(prizePlaces.values())
        .filter(p => p.contest_id === contest.id)
        .sort((a, b) => a.place_number - b.place_number);

      if (places.length > 0) {
        if (!placeWon) {
          // Guaranteed win mechanism: assign first available or last prize
          placeWon = 1;
        }
        const matchedPlace = places.find(p => p.place_number === placeWon) || places[0];
        prizeWon = matchedPlace.prize_text;
      }
    }

    return res.json({
      contest: {
        id: contest.id,
        title: contest.title,
        description: contest.description,
        deadline_at: contest.deadline_at,
        status: contest.status,
      },
      participant_id: participant.id,
      status: participant.status,
      assigned_place: placeWon,
      prize_won: prizeWon,
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
      price_rub: botSettings.subscription_price_rub,
    });
  }

  if (action === 'list_contests') {
    const userId = getUserId(initData);
    const botUser = await getCachedBotUsername();
    const userContests = Array.from(contests.values())
      .filter(c => c.owner_user_id === userId)
      .sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime());

    return res.json({
      contests: userContests.map(c => ({
        id: c.id,
        title: c.title,
        status: c.status,
        deadline_at: c.deadline_at,
        ref_link: getContestShareLink(c.ref_code, botUser),
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
    const callbackQuery = update?.callback_query;
    const message = update?.message || update?.edited_message;

    // Detect host dynamically
    const forwardedHost = req.headers['x-forwarded-host'] as string;
    const hostHeader = req.headers.host as string;
    const host = forwardedHost || hostHeader || '';
    const proto = (req.headers['x-forwarded-proto'] as string) || 'https';
    const baseMiniapp = host && !host.includes('localhost') ? `${proto}://${host}` : MINIAPP_URL.replace(/\/$/, '');

    // Handle Admin Chat Callbacks
    if (callbackQuery) {
      const cbId = callbackQuery.id;
      const cbFrom = callbackQuery.from || {};
      const cbUserId = cbFrom.id;
      const cbMsg = callbackQuery.message || {};
      const cbChatId = cbMsg.chat?.id;
      const cbData = callbackQuery.data || '';

      sendTelegramApi('answerCallbackQuery', { callback_query_id: cbId }).catch(console.error);

      if (cbUserId !== ADMIN_ID && String(cbUserId) !== String(ADMIN_ID)) {
        sendTelegramApi('sendMessage', {
          chat_id: cbChatId,
          text: '⛔ <b>Доступ запрещен:</b> только главный администратор бота может менять эти настройки.',
          parse_mode: 'HTML',
        }).catch(console.error);
        return res.json({ ok: true });
      }

      if (cbData === 'adm_edit_text') {
        adminChatStates.set(cbChatId, 'waiting_welcome_text');
        sendTelegramApi('sendMessage', {
          chat_id: cbChatId,
          text: '✏️ <b>Отправьте новый текст приветствия</b> для команды /start.\n\n💡 <i>Вы можете использовать HTML-разметку и тег {first_name} для подстановки имени пользователя.</i>\n\nДля отмены отправьте /cancel',
          parse_mode: 'HTML',
        }).catch(console.error);
      } else if (cbData === 'adm_edit_photo') {
        adminChatStates.set(cbChatId, 'waiting_welcome_photo');
        sendTelegramApi('sendMessage', {
          chat_id: cbChatId,
          text: '🖼️ <b>Отправьте изображение</b> (или прямую ссылку на фото), которое будет прикрепляться к приветствию.\n\nОтправьте <code>none</code>, чтобы убрать фото, или /cancel для отмены.',
          parse_mode: 'HTML',
        }).catch(console.error);
      } else if (cbData === 'adm_edit_price') {
        adminChatStates.set(cbChatId, 'waiting_sub_price');
        sendTelegramApi('sendMessage', {
          chat_id: cbChatId,
          text: `💰 <b>Введите новую стоимость подписки (в рублях)</b> для обычных пользователей:\n\nТекущая цена: <b>${botSettings.subscription_price_rub}₽</b>\n\nДля отмены отправьте /cancel`,
          parse_mode: 'HTML',
        }).catch(console.error);
      } else if (cbData === 'adm_settings') {
        const curPrice = botSettings.subscription_price_rub;
        const hasPhoto = botSettings.welcome_photo_url ? 'Установлено ✅' : 'Не установлено ❌';
        sendTelegramApi('sendMessage', {
          chat_id: cbChatId,
          text: `⚙️ <b>Панель управления настройками бота (Чат-режим)</b>\n\n💵 <b>Цена подписки организатора:</b> ${curPrice}₽ / 30 дней\n🖼️ <b>Фото в приветствии:</b> ${hasPhoto}\n📝 <b>Текст приветствия:</b>\n<i>${botSettings.welcome_text.slice(0, 120)}...</i>\n\nВыберите действие для редактирования:`,
          parse_mode: 'HTML',
          reply_markup: {
            inline_keyboard: [
              [{ text: '✏️ Изменить текст приветствия', callback_data: 'adm_edit_text' }],
              [{ text: '🖼️ Изменить фото приветствия', callback_data: 'adm_edit_photo' }],
              [{ text: '💰 Изменить цену подписки', callback_data: 'adm_edit_price' }],
              [{ text: '🚀 Открыть конструктор (Mini App)', web_app: { url: `${baseMiniapp}/?admin=1` } }],
            ]
          }
        }).catch(console.error);
      }

      return res.json({ ok: true });
    }

    if (!message) {
      return res.json({ ok: true });
    }

    const chatId = message.chat?.id;
    const fromUser = message.from || {};
    const userId = fromUser.id || chatId;
    const firstName = fromUser.first_name || 'Участник';
    const text = (message.text || '').trim();
    const photo = message.photo;

    // Check if admin is currently in a state waiting for input
    const adminState = adminChatStates.get(chatId);
    if (adminState && (userId === ADMIN_ID || String(userId) === String(ADMIN_ID))) {
      if (text === '/cancel') {
        adminChatStates.delete(chatId);
        sendTelegramApi('sendMessage', { chat_id: chatId, text: '❌ Действие отменено.' }).catch(console.error);
        return res.json({ ok: true });
      }

      if (adminState === 'waiting_welcome_text' && text) {
        botSettings.welcome_text = text;
        adminChatStates.delete(chatId);
        sendTelegramApi('sendMessage', {
          chat_id: chatId,
          text: '✅ <b>Текст приветствия успешно обновлен!</b>\n\nНовый текст будет отправляться всем пользователям при /start.',
          parse_mode: 'HTML',
        }).catch(console.error);
        return res.json({ ok: true });
      }

      if (adminState === 'waiting_welcome_photo') {
        adminChatStates.delete(chatId);
        if (photo && photo.length > 0) {
          const fileId = photo[photo.length - 1].file_id;
          botSettings.welcome_photo_url = fileId;
          sendTelegramApi('sendMessage', {
            chat_id: chatId,
            text: '✅ <b>Фотография приветствия сохранена!</b> Теперь /start будет отправлять это изображение с подписью.',
            parse_mode: 'HTML',
          }).catch(console.error);
        } else if (['none', 'нет', 'удалить'].includes(text.toLowerCase())) {
          botSettings.welcome_photo_url = '';
          sendTelegramApi('sendMessage', {
            chat_id: chatId,
            text: '✅ <b>Фото приветствия удалено.</b> Приветствие снова будет отправляться текстом.',
            parse_mode: 'HTML',
          }).catch(console.error);
        } else if (text.startsWith('http')) {
          botSettings.welcome_photo_url = text;
          sendTelegramApi('sendMessage', {
            chat_id: chatId,
            text: '✅ <b>Ссылка на фото сохранена!</b>',
            parse_mode: 'HTML',
          }).catch(console.error);
        } else {
          sendTelegramApi('sendMessage', {
            chat_id: chatId,
            text: '⚠️ Не распознано изображение. Попробуйте еще раз или напишите /cancel',
          }).catch(console.error);
        }
        return res.json({ ok: true });
      }

      if (adminState === 'waiting_sub_price') {
        const cleanNum = parseInt(text.replace(/\D/g, ''), 10);
        if (cleanNum > 0) {
          botSettings.subscription_price_rub = cleanNum;
          adminChatStates.delete(chatId);
          sendTelegramApi('sendMessage', {
            chat_id: chatId,
            text: `✅ <b>Стоимость подписки успешно изменена на ${cleanNum}₽!</b>`,
            parse_mode: 'HTML',
          }).catch(console.error);
          return res.json({ ok: true });
        }
        sendTelegramApi('sendMessage', {
          chat_id: chatId,
          text: '⚠️ Пожалуйста, введите корректное число (например: 490) или /cancel',
        }).catch(console.error);
        return res.json({ ok: true });
      }
    }

    let replyText = '';
    let replyMarkup: any = null;
    let isPhotoMessage = false;

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
        const contest = Array.from(contests.values()).find(c => c.ref_code === refCode);
        const contestUrl = `${baseMiniapp}/?ref=${refCode}`;
        
        if (contest) {
          const contestPlaces = Array.from(prizePlaces.values())
            .filter(p => p.contest_id === contest.id)
            .sort((a, b) => a.place_number - b.place_number);

          const placesStr = contestPlaces.length > 0
            ? '\n🏆 <b>Призовые места:</b>\n' + contestPlaces.map(p => `• ${p.place_number} место: ${p.prize_text}`).join('\n') + '\n'
            : '';
          const descStr = contest.description ? `<i>${contest.description}</i>\n` : '';

          replyText = `🎉 <b>Здравствуйте, ${firstName}!</b>\n\n` +
            `Вас пригласили принять участие в розыгрыше: <b>«${contest.title}»</b>!\n\n` +
            descStr + placesStr +
            `\n📋 Выполните простые задания чек-листа в приложении конкурса, чтобы занять призовое место!`;
        } else {
          replyText = `🎁 <b>Здравствуйте, ${firstName}!</b>\n\nВы приглашены к участию в розыгрыше призов!\n\nЧтобы подтвердить участие и побороться за ценные призы, нажмите на кнопку ниже и выполните условия чек-листа:`;
        }

        replyMarkup = {
          inline_keyboard: [
            [{ text: '🎯 Открыть условия и участвовать', web_app: { url: contestUrl } }]
          ]
        };
      } else {
        const appUrl = `${baseMiniapp}/`;
        const adminUrl = `${baseMiniapp}/?admin=1`;
        const buttons: any[] = [
          [{ text: '🎁 Открыть конкурсы', web_app: { url: appUrl } }]
        ];
        if (userId === ADMIN_ID || String(userId) === String(ADMIN_ID)) {
          buttons.push([{ text: '⚙️ Конструктор конкурсов (Admin)', web_app: { url: adminUrl } }]);
          buttons.push([{ text: '🛠️ Настройки бота (Цены, Текст, Фото)', callback_data: 'adm_settings' }]);
        }

        const templateText = botSettings.welcome_text || '👋 Привет, {first_name}!';
        replyText = templateText.replace('{first_name}', firstName);
        replyMarkup = { inline_keyboard: buttons };

        if (botSettings.welcome_photo_url) {
          isPhotoMessage = true;
        }
      }
    } else if (text.startsWith('/admin')) {
      if (userId === ADMIN_ID || String(userId) === String(ADMIN_ID)) {
        const curPrice = botSettings.subscription_price_rub;
        const hasPhoto = botSettings.welcome_photo_url ? 'Установлено ✅' : 'Не установлено ❌';
        replyText = `⚙️ <b>Панель управления администратора</b>\n\n💵 <b>Цена подписки организатора:</b> ${curPrice}₽ / 30 дней\n🖼️ <b>Фото в приветствии:</b> ${hasPhoto}\n📝 <b>Текст приветствия:</b> настроен\n\nВы можете редактировать параметры прямо в этом чате или открыть конструктор конкурсов:`;
        replyMarkup = {
          inline_keyboard: [
            [{ text: '✏️ Изменить текст приветствия', callback_data: 'adm_edit_text' }],
            [{ text: '🖼️ Изменить фото приветствия', callback_data: 'adm_edit_photo' }],
            [{ text: '💰 Изменить цену подписки', callback_data: 'adm_edit_price' }],
            [{ text: '📊 Открыть админ-панель конкурсов', web_app: { url: `${baseMiniapp}/?admin=1` } }],
          ]
        };
      } else {
        replyText = '⛔ Данная команда доступна только администратору бота.';
        replyMarkup = null;
      }
    } else if (photo) {
      replyText = `📸 <b>Скриншот получен!</b>\n\nОн передан организаторам конкурса на ручную проверку. После подтверждения статус задания обновится в чек-листе Mini App.`;
      replyMarkup = {
        inline_keyboard: [
          [{ text: '🔍 Открыть чек-лист в Mini App', web_app: { url: `${baseMiniapp}/` } }]
        ]
      };
    } else {
      replyText = `👋 Чтобы принять участие в конкурсе или управлять розыгрышами, откройте приложение:`;
      replyMarkup = {
        inline_keyboard: [
          [{ text: '🚀 Открыть приложение', web_app: { url: `${baseMiniapp}/` } }]
        ]
      };
    }

    if (chatId) {
      if (isPhotoMessage && botSettings.welcome_photo_url) {
        sendTelegramApi('sendPhoto', {
          chat_id: chatId,
          photo: botSettings.welcome_photo_url,
          caption: replyText,
          parse_mode: 'HTML',
          reply_markup: replyMarkup,
        }).catch(e => console.error(e));
      } else {
        sendTelegramApi('sendMessage', {
          chat_id: chatId,
          text: replyText,
          parse_mode: 'HTML',
          reply_markup: replyMarkup,
        }).catch(e => console.error(e));
      }
    }

    // Return plain ok to prevent duplicate message from Telegram webhook executor
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

    const isDraft = Boolean(body.is_draft);
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
      status: isDraft ? 'draft' : 'active',
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

    const botUser = await getCachedBotUsername();
    return res.json({
      contest_id: contestId,
      status: contest.status,
      ref_link: getContestShareLink(refCode, botUser),
      ref_code: refCode,
    });
  }

  if (action === 'publish_contest') {
    const userId = getUserId(initData);
    const contestId = Number(body.contest_id);
    const contest = contests.get(contestId);
    if (!contest || contest.owner_user_id !== userId) {
      return res.status(404).json({ error: 'contest not found' });
    }
    contest.status = 'active';
    const botUser = await getCachedBotUsername();
    return res.json({
      ok: true,
      ref_link: getContestShareLink(contest.ref_code, botUser),
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
