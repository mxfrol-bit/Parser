# Снабженец — агрегатор прайсов геоматериалов

Система сбора и поиска цен через Telegram-бот + веб-каталог.

## Стек

| Слой | Технология |
|---|---|
| Бот | Python · aiogram 3 |
| AI парсинг | OpenRouter · Gemini 2.0 Flash |
| База данных | Supabase PostgreSQL |
| Хостинг | Railway |
| Веб-каталог | React (catalog.jsx) |

## Структура проекта

```
├── bot.py           # Telegram-бот (основной сервис)
├── catalog.jsx      # Веб-каталог для менеджеров (React)
├── schema.sql       # SQL схема Supabase
├── requirements.txt
├── Procfile         # Railway worker
└── .env.example
```

## Быстрый старт

### 1. Supabase
1. Создать проект → [supabase.com](https://supabase.com)
2. SQL Editor → вставить и запустить `schema.sql`
3. Settings → API → скопировать **Project URL** и **service_role** secret

### 2. Telegram
1. [@BotFather](https://t.me/botfather) → `/newbot` → скопировать токен

### 3. OpenRouter
1. [openrouter.ai](https://openrouter.ai) → Keys → создать ключ
2. По умолчанию используется `google/gemini-2.0-flash-001` (быстро, дёшево, есть vision)
3. Поменять модель через переменную `MODEL` в Railway

### 4. Railway
1. Новый проект → подключить GitHub
2. Variables → добавить из `.env.example`:
   - `BOT_TOKEN`
   - `OPENROUTER_KEY`
   - `SUPABASE_URL`
   - `SUPABASE_KEY`
3. Railway автоматически запустит `Procfile`

### 5. Веб-каталог
`catalog.jsx` — деплоить на Vercel/Railway как отдельный Next.js/Vite проект,
или открыть прямо в Claude как React артефакт.

При открытии нажать ⚙ Supabase и ввести **Project URL** + **anon key** (не service_role).

## Роли пользователей

| Роль | Что делает |
|---|---|
| **snabzhenets** | Загружает прайсы: Excel / фото / PDF / текст |
| **manager** | Ищет цены: пишет название товара |

Регистрация через `/start` → выбор роли → город.

## AI парсер извлекает

- `product` — нормализованное название
- `category` — геотекстиль | георешетка | геомембрана | дренаж | спанбонд
- `density` — плотность г/м²
- `width` — ширина рулона, м
- `roll_length` — намотка, м
- `thickness` — толщина мм (для мембран)
- `material` — PP, PET, HDPE, ПВД...
- `price_date` — дата прайса (ищет в заголовке, иначе сегодня)
- `price_per_roll` — цена за рулон

## База данных

```
prices        — вся история (никогда не перезаписывается)
prices_latest — вью: последняя цена от каждого снабженца
prices_history — вью: история с дельтой к предыдущей записи
users          — снабженцы и менеджеры
```

## Команды бота

```
/start          — регистрация
/history <товар> [город]   — история цен
```

Снабженец — просто отправить файл или текст.
Менеджер — написать название товара.
