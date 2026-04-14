import asyncio, base64, json, logging, os
from datetime import date
from io import BytesIO

import pandas as pd
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from dotenv import load_dotenv
from openai import AsyncOpenAI
from supabase import create_client

load_dotenv()

BOT_TOKEN      = os.getenv("BOT_TOKEN")
OPENROUTER_KEY = os.getenv("OPENROUTER_KEY")
SUPABASE_URL   = os.getenv("SUPABASE_URL")
SUPABASE_KEY   = os.getenv("SUPABASE_KEY")

MODEL_TEXT   = os.getenv("MODEL", "google/gemini-2.0-flash-001")
MODEL_VISION = os.getenv("MODEL_VISION", "google/gemini-2.0-flash-001")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

bot      = Bot(token=BOT_TOKEN)
dp       = Dispatcher(storage=MemoryStorage())
router   = Router()
dp.include_router(router)
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
ai       = AsyncOpenAI(
    api_key=OPENROUTER_KEY,
    base_url="https://openrouter.ai/api/v1",
    default_headers={"X-Title": "Snabzhenets Bot"},
)

# ── States ─────────────────────────────────────────────────────────────────────
class Reg(StatesGroup):
    role = State()
    city = State()

# ── AI prompts ─────────────────────────────────────────────────────────────────
TODAY = str(date.today())

PARSE_PROMPT = f"""Ты — парсер прайс-листов строительных материалов (геотекстиль, георешётка, геомембрана, спанбонд, дренаж и др.).

Извлеки ВСЕ товарные позиции с ценами. Ответь ТОЛЬКО валидным JSON-массивом без markdown:

[{{
  "product":       "геотекстиль 200 г/м²",
  "category":      "геотекстиль",
  "density":       200,
  "width":         4.5,
  "roll_length":   150,
  "thickness":     null,
  "color":         null,
  "material":      "PP",
  "price":         18.50,
  "unit":          "м²",
  "price_per_roll": 12487.5,
  "price_date":    "{TODAY}",
  "original":      "Геотекстиль 200 г/кв.м — 18-50 руб"
}}]

Правила полей:
- product: нижний регистр, нормализованное название с характеристиками
- category: геотекстиль | георешетка | геомембрана | дренаж | спанбонд | прочее
- density: плотность г/м² — число или null
- width: ширина рулона в метрах — число или null
- roll_length: намотка/длина рулона в метрах — число или null
- thickness: толщина в мм (для мембран) — число или null
- color: цвет (чёрный, белый...) или null
- material: PP, PET, HDPE, ПВД... или null
- price: цена числом без валюты и пробелов (18.50)
- unit: м² | рулон | кг | шт | м.п. | пог.м (если непонятно → м²)
- price_per_roll: цена за рулон если указана явно, иначе null
- price_date: дата прайса YYYY-MM-DD — ищи в заголовке/колонтитуле, если нет → {TODAY}
- original: строка как в источнике
- ПРОПУСКАЙ строки с нулевой или отсутствующей ценой"""

NORMALIZE_PROMPT = """Нормализуй поисковый запрос для базы строительных геоматериалов.
Исправь опечатки, раскрой сокращения, добавь характеристики если понятны.
Верни ТОЛЬКО нормализованный запрос одной строкой, без пояснений.
Примеры: «гтекстиль 200» → «геотекстиль 200», «спанб 60» → «спанбонд 60»"""

# ── AI helpers ─────────────────────────────────────────────────────────────────
def clean_json(raw: str) -> list:
    raw = raw.strip()
    for fence in ["```json", "```"]:
        raw = raw.replace(fence, "")
    raw = raw.strip()
    return json.loads(raw)

async def ai_parse_text(content: str) -> list:
    r = await ai.chat.completions.create(
        model=MODEL_TEXT,
        messages=[
            {"role": "system", "content": PARSE_PROMPT},
            {"role": "user",   "content": content[:14000]},
        ],
        max_tokens=4000, temperature=0,
    )
    return clean_json(r.choices[0].message.content)

async def ai_parse_image(b64: str) -> list:
    r = await ai.chat.completions.create(
        model=MODEL_VISION,
        messages=[{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
            {"type": "text", "text": PARSE_PROMPT},
        ]}],
        max_tokens=4000, temperature=0,
    )
    return clean_json(r.choices[0].message.content)

async def ai_normalize(query: str) -> str:
    r = await ai.chat.completions.create(
        model=MODEL_TEXT,
        messages=[
            {"role": "system", "content": NORMALIZE_PROMPT},
            {"role": "user",   "content": query},
        ],
        max_tokens=60, temperature=0,
    )
    return r.choices[0].message.content.strip().lower()

# ── DB helpers ─────────────────────────────────────────────────────────────────
async def get_user(tg_id: int) -> dict | None:
    r = supabase.table("users").select("*").eq("telegram_id", tg_id).execute()
    return r.data[0] if r.data else None

async def save_prices(items: list, user: dict, source: str = None) -> int:
    rows = []
    for item in items:
        try:
            price = float(str(item.get("price", 0)).replace(",", ".").replace(" ", ""))
            if price <= 0:
                continue
        except (ValueError, TypeError):
            continue

        # price_per_roll: явная или вычисленная
        ppr = item.get("price_per_roll")
        if not ppr:
            w = item.get("width")
            rl = item.get("roll_length")
            if w and rl:
                ppr = round(price * float(w) * float(rl), 2)

        rows.append({
            "product":          item.get("product", "").lower().strip(),
            "product_original": item.get("original", item.get("product", "")),
            "category":         item.get("category"),
            "density":          item.get("density"),
            "width":            item.get("width"),
            "roll_length":      item.get("roll_length"),
            "thickness":        item.get("thickness"),
            "color":            item.get("color"),
            "material":         item.get("material"),
            "price":            price,
            "unit":             item.get("unit", "м²"),
            "price_per_roll":   ppr,
            "supplier_id":      user["telegram_id"],
            "supplier_name":    user["name"],
            "city":             user["city"],
            "price_date":       item.get("price_date", TODAY),
            "source_file":      source,
        })

    if rows:
        supabase.table("prices").insert(rows).execute()
    return len(rows)

async def search_prices(raw_query: str) -> tuple[list, str]:
    norm = await ai_normalize(raw_query)
    words = norm.split()

    # Пробуем полный нормализованный запрос
    r = supabase.table("prices_latest").select("*") \
        .ilike("product", f"%{norm}%") \
        .order("price_date", desc=True).limit(300).execute()
    items = r.data

    # Фоллбек: первые два слова
    if not items and len(words) >= 2:
        q2 = " ".join(words[:2])
        r2 = supabase.table("prices_latest").select("*") \
            .ilike("product", f"%{q2}%") \
            .order("price_date", desc=True).limit(300).execute()
        items = r2.data

    # Фоллбек: только первое слово
    if not items and words:
        r3 = supabase.table("prices_latest").select("*") \
            .ilike("product", f"%{words[0]}%") \
            .order("price_date", desc=True).limit(300).execute()
        items = r3.data

    return items, norm

async def get_history(product: str, city: str = None) -> list:
    q = supabase.table("prices_history").select("*").ilike("product", f"%{product}%")
    if city:
        q = q.eq("city", city)
    r = q.order("price_date", desc=True).limit(30).execute()
    return r.data

# ── Format ─────────────────────────────────────────────────────────────────────
def fmt_result(items: list, query: str, norm: str) -> str:
    if not items:
        return (
            f"❌ По запросу *{query}* ничего не найдено.\n"
            f"_Искал: «{norm}»_\n\n"
            "Попробуйте другое название."
        )

    by_city: dict[str, dict] = {}
    for item in items:
        city = item["city"]
        if city not in by_city or item["price"] < by_city[city]["price"]:
            by_city[city] = item

    sorted_items = sorted(by_city.values(), key=lambda x: x["price"])
    medals = ["🥇", "🥈", "🥉"]
    lines = [f"📦 *{norm}*\n"]

    for i, item in enumerate(sorted_items[:10]):
        prefix = medals[i] if i < 3 else "  •"
        dt = (item.get("price_date") or "")[:10]
        attrs = []
        if item.get("density"):     attrs.append(f'{item["density"]} г/м²')
        if item.get("width"):       attrs.append(f'{item["width"]} м шир')
        if item.get("roll_length"): attrs.append(f'{item["roll_length"]} м нам')
        if item.get("thickness"):   attrs.append(f'{item["thickness"]} мм')
        attr_str = f"  _{'  ·  '.join(attrs)}_\n" if attrs else ""

        ppr = item.get("price_per_roll")
        roll_str = f"  _{int(ppr):,} р/рул_".replace(",", " ") if ppr else ""

        lines.append(
            f"{prefix} *{item['city']}* — {item['price']:.2f} р/{item['unit']}\n"
            f"{attr_str}"
            f"{roll_str}"
            f"  └ {item['supplier_name']} · {dt}"
        )

    if len(by_city) > 10:
        lines.append(f"\n_...ещё {len(by_city)-10} городов_")
    lines.append("\n_Для истории: /history <товар> [город]_")
    return "\n".join(lines)

def fmt_history(items: list, product: str, city: str = None) -> str:
    loc = f" — {city}" if city else ""
    if not items:
        return f"❌ Нет истории по «{product}»{loc}"

    lines = [f"📈 *{product}*{loc}\n"]
    grouped: dict[str, list] = {}
    for item in items:
        k = f"{item['city']} · {item['supplier_name']}"
        grouped.setdefault(k, []).append(item)

    for key, rows in grouped.items():
        lines.append(f"*{key}*")
        for r in rows:
            dt = (r.get("price_date") or "")[:10]
            prev = r.get("prev_price")
            delta = ""
            if prev:
                d = r["price"] - float(prev)
                if abs(d) > 0.01:
                    delta = f"  {'▲' if d > 0 else '▼'} {abs(d):.2f}"
            lines.append(f"  {dt}   {r['price']:.2f} р/{r['unit']}{delta}")
        lines.append("")

    return "\n".join(lines)

def ok_msg(count: int, city: str, price_date: str = None) -> str:
    return (
        f"✅ Прайс загружен!\n\n"
        f"📊 Позиций: *{count}*\n"
        f"📍 Город: {city}\n"
        f"📅 Дата прайса: {price_date or TODAY}"
    )

# ── Registration ───────────────────────────────────────────────────────────────
@router.message(CommandStart())
async def cmd_start(msg: Message, state: FSMContext):
    user = await get_user(msg.from_user.id)
    if user:
        role_label = "Снабженец" if user["role"] == "snabzhenets" else "Менеджер"
        tip = "Отправляйте прайсы — файл, фото или текст." if user["role"] == "snabzhenets" \
              else "Напишите название товара для поиска цены."
        await msg.answer(
            f"Вы в системе как *{role_label}* ({user['city']}).\n\n{tip}",
            parse_mode="Markdown"
        )
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📦 Снабженец — загружаю прайсы", callback_data="role_snabzhenets")],
        [InlineKeyboardButton(text="🔍 Менеджер — ищу цены",         callback_data="role_manager")],
    ])
    await msg.answer("Добро пожаловать!\n\nВыберите роль:", reply_markup=kb)
    await state.set_state(Reg.role)

@router.callback_query(Reg.role)
async def cb_role(cb: CallbackQuery, state: FSMContext):
    await state.update_data(role=cb.data.replace("role_", ""))
    await cb.message.edit_text("Укажите ваш город (например: *Нижний Новгород*):", parse_mode="Markdown")
    await state.set_state(Reg.city)

@router.message(Reg.city)
async def reg_city(msg: Message, state: FSMContext):
    data = await state.get_data()
    role = data["role"]
    city = msg.text.strip()

    supabase.table("users").insert({
        "telegram_id": msg.from_user.id,
        "username":    msg.from_user.username or "",
        "name":        msg.from_user.full_name,
        "role":        role,
        "city":        city,
    }).execute()
    await state.clear()

    if role == "snabzhenets":
        await msg.answer(
            f"✅ Зарегистрированы как *Снабженец*, г. {city}\n\n"
            "Отправляйте прайсы в любом виде:\n"
            "• Excel / CSV файл\n"
            "• Фото или скриншот прайса\n"
            "• Текст сообщением\n\n"
            "Нейронка сама распознает позиции, характеристики, цены и дату.",
            parse_mode="Markdown",
        )
    else:
        await msg.answer(
            f"✅ Зарегистрированы как *Менеджер*, г. {city}\n\n"
            "Напишите название товара:\n"
            "_геотекстиль 200_\n"
            "_геомембрана 1мм_\n"
            "_спанбонд 60_\n\n"
            "Для истории цен: `/history геотекстиль 200 Казань`",
            parse_mode="Markdown",
        )

# ── History command ────────────────────────────────────────────────────────────
@router.message(F.text.startswith("/history"))
async def cmd_history(msg: Message):
    user = await get_user(msg.from_user.id)
    if not user:
        await msg.answer("Начните с /start")
        return

    parts = msg.text.replace("/history", "").strip().rsplit(" ", 1)
    if not parts or not parts[0]:
        await msg.answer("Формат: `/history геотекстиль 200 Казань`\n"
                         "или: `/history геотекстиль 200`", parse_mode="Markdown")
        return

    product = parts[0].strip()
    city    = parts[1].strip() if len(parts) > 1 else None
    items   = await get_history(product, city)
    await msg.answer(fmt_history(items, product, city), parse_mode="Markdown")

# ── Document ───────────────────────────────────────────────────────────────────
@router.message(F.document)
async def on_document(msg: Message):
    user = await get_user(msg.from_user.id)
    if not user or user["role"] != "snabzhenets":
        return

    await msg.answer("⏳ Нейронка обрабатывает файл...")
    try:
        file  = await bot.get_file(msg.document.file_id)
        raw   = await bot.download_file(file.file_path)
        fname = msg.document.file_name or "price"

        if fname.lower().endswith((".xlsx", ".xls")):
            df      = pd.read_excel(BytesIO(raw.read()))
            content = df.to_string(index=False)
        elif fname.lower().endswith(".csv"):
            df      = pd.read_csv(BytesIO(raw.read()), encoding="utf-8-sig")
            content = df.to_string(index=False)
        else:
            content = raw.read().decode("utf-8", errors="ignore")

        items = await ai_parse_text(content)
        count = await save_prices(items, user, fname)
        date_from = items[0].get("price_date") if items else None
        await msg.answer(ok_msg(count, user["city"], date_from), parse_mode="Markdown")

    except json.JSONDecodeError:
        await msg.answer("❌ Нейронка не смогла разобрать структуру.\n"
                         "Попробуйте Excel или CSV с чёткими колонками.")
    except Exception as e:
        log.error(f"document error: {e}")
        await msg.answer("❌ Ошибка обработки файла.")

# ── Photo ──────────────────────────────────────────────────────────────────────
@router.message(F.photo)
async def on_photo(msg: Message):
    user = await get_user(msg.from_user.id)
    if not user or user["role"] != "snabzhenets":
        return

    await msg.answer("⏳ Нейронка читает прайс с фото...")
    try:
        photo = msg.photo[-1]
        file  = await bot.get_file(photo.file_id)
        raw   = await bot.download_file(file.file_path)
        b64   = base64.b64encode(raw.read()).decode()
        items = await ai_parse_image(b64)
        count = await save_prices(items, user, "photo")
        date_from = items[0].get("price_date") if items else None
        await msg.answer(ok_msg(count, user["city"], date_from), parse_mode="Markdown")

    except json.JSONDecodeError:
        await msg.answer("❌ Не удалось распознать. Сделайте более чёткий снимок.")
    except Exception as e:
        log.error(f"photo error: {e}")
        await msg.answer("❌ Ошибка обработки фото.")

# ── Text ───────────────────────────────────────────────────────────────────────
@router.message(F.text & ~F.text.startswith("/"))
async def on_text(msg: Message):
    user = await get_user(msg.from_user.id)
    if not user:
        await msg.answer("Пожалуйста, начните с /start")
        return

    if user["role"] == "snabzhenets":
        await msg.answer("⏳ Парсю прайс из текста...")
        try:
            items = await ai_parse_text(msg.text)
            count = await save_prices(items, user, "text")
            if count:
                date_from = items[0].get("price_date") if items else None
                await msg.answer(ok_msg(count, user["city"], date_from), parse_mode="Markdown")
            else:
                await msg.answer(
                    "Позиций с ценами не найдено.\n\n"
                    "Пример: `Геотекстиль 200 г/м², ш.4.5м, нам.150м — 18.50 р/м²`",
                    parse_mode="Markdown"
                )
        except Exception as e:
            log.error(f"text parse error: {e}")
            await msg.answer("❌ Не удалось распознать. Проверьте формат.")

    else:
        await msg.answer("🔍 Ищу...")
        try:
            items, norm = await search_prices(msg.text)
            await msg.answer(fmt_result(items, msg.text.strip(), norm), parse_mode="Markdown")
        except Exception as e:
            log.error(f"search error: {e}")
            await msg.answer("❌ Ошибка поиска.")

# ── Run ────────────────────────────────────────────────────────────────────────
async def main():
    log.info("Starting bot via OpenRouter / %s", MODEL_TEXT)
    await dp.start_polling(bot, skip_updates=True)

if __name__ == "__main__":
    asyncio.run(main())
