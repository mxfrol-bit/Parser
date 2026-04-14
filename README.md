# Снабженец — агрегатор прайсов геоматериалов

## Файлы

| Файл | Назначение |
|---|---|
| `bot.py` | Telegram-бот (Railway worker) |
| `admin.html` | Веб-админка (открыть в браузере) |
| `schema.sql` | SQL схема Supabase (вставить в SQL Editor) |
| `requirements.txt` | Python зависимости |
| `Procfile` | Railway — запуск бота |
| `.env.example` | Переменные окружения |

## Деплой

### 1. Supabase
SQL Editor → вставить `schema.sql` целиком → Run

### 2. Railway Variables
```
BOT_TOKEN       = от @BotFather
OPENROUTER_KEY  = sk-or-v1-...
ANTHROPIC_KEY   = sk-ant-api03-...
SUPABASE_URL    = https://xxxx.supabase.co
SUPABASE_KEY    = eyJhbGci... (service_role)
ADMIN_IDS       = ваш_telegram_id
```

### 3. admin.html
Открыть в браузере → ⚙ Supabase → ввести URL + KEY + пароль

## Роли

| Роль | Бот | Команды |
|---|---|---|
| snabzhenets | Загружает прайсы | /setcity /setsupplier /myinfo |
| manager | Ищет цены | поиск текстом |
| admin | Всё выше + управление | /users /setrole |

## Пайплайн загрузки Excel

1. Python читает все листы → 122+ строк
2. Claude AI нормализует названия, извлекает характеристики
3. Supabase сохраняет с историей

## Ответ менеджеру

```
🥇 Нижний Новгород — 26.79 р/м²
  плотность 200 г/м² · рулон 4.5м × 50м
  💰 6 028 р/рул
  ⚖️ ~45 кг/рул
  🏢 ООО Армпласт · 📅 01.02.2026

[📈 НН] [📈 Казань] [📈 Самара]
[🔄 Обновить] [🌍 Все города]
```
