# Contest Bot — Telegram-бот для конкурсов + Mini App

## Стек
Python 3.11+, aiogram 3, Vercel (serverless, webhook-режим + статика для Mini App),
Supabase (Postgres), CryptoBot (Crypto Pay API) для оплаты подписки.

## Как это работает
- `/start` — бот шлёт приветствие (текст/медиа, редактируется через `/edit_welcome`)
  и кнопку `web_app`, открывающую Mini App.
- Реф-ссылка (`t.me/бот?start=c_XXXX`) triggers `/start`, кнопка открывает Mini App
  сразу на странице конкретного конкурса (`?ref=XXXX`) — чек-лист условий целиком там.
- Ты как админ — тоже через Mini App (`?admin=1`): список конкурсов, создание нового,
  генерация реф-ссылки. У владельца бота (`ADMIN_ID`) подписка не нужна.
- Если у админа (не владельца) нет активной подписки — Mini App показывает paywall,
  создаёт инвойс в CryptoBot, после оплаты подписка активируется на 30 дней.
- Все запросы Mini App идут на `/api?action=...` (единый серверлес-файл — так обходим
  ограничение Vercel на несколько entrypoint-файлов у Python-раннера).

## Деплой

### 1. Supabase
1. Создай проект на supabase.com.
2. В SQL Editor выполни содержимое `sql/schema.sql`.
3. Кнопка **Connect** → вкладка **Direct connection** → **Connection Method: Transaction pooler**
   → скопируй строку (порт **6543**, хост `*.pooler.supabase.com`) — это `DATABASE_URL`.
   Пароль в URL не должен содержать квадратных скобок; спецсимволы (например `@`) закодируй как `%40`.

### 2. Telegram-бот
1. Создай бота через @BotFather, получи `BOT_TOKEN`.
2. Узнай свой user_id (например, через @userinfobot) — это `ADMIN_ID`.
3. В @BotFather: `/mybots` → выбери бота → **Bot Settings → Menu Button** (или **Mini App**) →
   укажи URL твоего Vercel-деплоя (например `https://<домен>.vercel.app/`) — это регистрирует Mini App.

### 3. CryptoBot
1. Открой @CryptoBot → **Crypto Pay** → **Create App**, получи API-токен — это `CRYPTOBOT_TOKEN`.

### 4. Vercel
1. `vercel link` в папке проекта, затем `vercel env add` для каждой переменной:
   - `BOT_TOKEN`, `ADMIN_ID`, `DATABASE_URL`
   - `CRON_SECRET` (любая случайная строка)
   - `MINIAPP_URL` — базовый URL деплоя, например `https://<домен>.vercel.app`
   - `CRYPTOBOT_TOKEN`
2. Deploy: `vercel --prod`

### 5. Установка вебхука Telegram
```bash
curl "https://api.telegram.org/bot<BOT_TOKEN>/setWebhook?url=https://<домен>.vercel.app/api?action=telegram_webhook"
```

### 6. Крон-финализация конкурсов (внешний, т.к. Vercel Hobby ограничивает встроенный cron раз в сутки)
На [cron-job.org](https://cron-job.org) создай задачу:
- URL: `https://<домен>.vercel.app/api?action=cron`
- Метод: GET, каждые 15 минут
- Заголовок: `Authorization: Bearer <CRON_SECRET>`

## Команды бота (чат)
- `/admin` — меню администратора + кнопка входа в Mini App
- `/edit_welcome` — изменить текст/медиа приветствия по `/start`
- `/new_contest`, `/contests` — старый чат-флоу создания конкурса, оставлен как запасной вариант;
  основной способ — Mini App

## Что уже реализовано
- Приветствие с медиа, редактируемое из чата
- Mini App: страница конкурса с чек-листом условий (авто-проверка подписки + скриншоты через чат бота)
- Mini App: админ-панель — список конкурсов, создание нового, реф-ссылки
- Подписка через CryptoBot (фиатный инвойс в рублях), опрос статуса оплаты с фронта
- Автоматическая финализация по крону — раскладка участников по местам, рассылка результатов

## Упрощения / что стоит доделать
- Подтверждение оплаты сейчас идёт поллингом с фронта (`check_payment`), а не вебхуком от CryptoBot —
  надёжнее, но не мгновенно. Можно добавить `action=cryptobot_webhook` и настроить Webhook в CryptoBot App.
- Редактирование/удаление уже созданных конкурсов из Mini App
- Красивое форматирование дедлайна с учётом часового пояса участника
