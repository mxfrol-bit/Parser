import asyncio, base64, json, logging, os, re, io
from datetime import date, datetime
from io import BytesIO

import pandas as pd
import anthropic
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    BufferedInputFile, CallbackQuery,
    InlineKeyboardButton, InlineKeyboardMarkup, Message,
)
from dotenv import load_dotenv
from openai import AsyncOpenAI
from supabase import create_client

load_dotenv()

BOT_TOKEN      = os.getenv("BOT_TOKEN")
OPENROUTER_KEY = os.getenv("OPENROUTER_KEY")
ANTHROPIC_KEY  = os.getenv("ANTHROPIC_KEY")
SUPABASE_URL   = os.getenv("SUPABASE_URL")
SUPABASE_KEY   = os.getenv("SUPABASE_KEY")
ADMIN_IDS      = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]

MODEL_TEXT   = os.getenv("MODEL",        "google/gemini-2.0-flash-001")
MODEL_VISION = os.getenv("MODEL_VISION", "google/gemini-2.0-flash-001")
MODEL_CLAUDE = os.getenv("MODEL_CLAUDE", "claude-haiku-4-5-20251001")

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
claude = anthropic.AsyncAnthropic(api_key=ANTHROPIC_KEY) if ANTHROPIC_KEY else None

TODAY = str(date.today())
_SKIP_SHEETS = {"содержание", "оглавление", "для формирования цены", "contents", "sheet1"}
_CAT_MAP = {
    "геотекстиль эко":"геотекстиль","геотекстиль":"геотекстиль",
    "георешетка":"георешетка","геомембрана":"геомембрана",
    "термовойлок":"термовойлок","ватин":"ватин","анкера":"прочее",
    "дренаж":"дренаж","спанбонд":"спанбонд",
}

# ── States ─────────────────────────────────────────────────────────────────────
class Reg(StatesGroup):
    role     = State()
    city     = State()
    supplier = State()

class Edit(StatesGroup):
    city     = State()
    supplier = State()

# ── DB helpers ─────────────────────────────────────────────────────────────────
async def get_user(tg_id: int) -> dict | None:
    r = supabase.table("users").select("*").eq("telegram_id", tg_id).execute()
    return r.data[0] if r.data else None

async def save_prices(items: list, user: dict, source: str = None) -> int:
    rows = []
    for item in items:
        try:
            price = float(str(item.get("price", 0)).replace(",", ".").replace(" ", ""))
            if price <= 0: continue
        except (ValueError, TypeError):
            continue
        w  = item.get("width")
        rl = item.get("roll_length")
        ppr = item.get("price_per_roll")
        if not ppr and w and rl:
            try: ppr = round(float(w) * float(rl) * price, 2)
            except: pass
        rows.append({
            "product":          (item.get("product") or "").lower().strip(),
            "product_original": item.get("original") or item.get("product_original") or item.get("product"),
            "category":         item.get("category"),
            "technology":       item.get("technology"),
            "density":          item.get("density"),
            "width":            w,
            "roll_length":      rl,
            "thickness":        item.get("thickness"),
            "color":            item.get("color"),
            "material":         item.get("material"),
            "tolerance":        item.get("tolerance"),
            "application":      item.get("application"),
            "price":            price,
            "unit":             item.get("unit") or "м²",
            "price_per_roll":   ppr,
            "supplier_id":      user["telegram_id"],
            "supplier_name":    user.get("supplier_name") or user["name"],
            "city":             user["city"],
            "price_date":       item.get("price_date") or TODAY,
            "source_file":      source,
        })
    if rows:
        supabase.table("prices").insert(rows).execute()
    return len(rows)

async def search_prices(raw_query: str) -> tuple[list, str]:
    norm = await ai_normalize(raw_query)
    words = norm.split()
    for q in [norm, " ".join(words[:2]) if len(words)>=2 else None, words[0] if words else None]:
        if not q: continue
        r = supabase.table("prices_latest").select("*") \
            .ilike("product", f"%{q}%") \
            .order("price_date", desc=True).limit(300).execute()
        if r.data:
            return r.data, norm
    return [], norm

async def get_history(product: str, city: str = None) -> list:
    q = supabase.table("prices_history").select("*").ilike("product", f"%{product}%")
    if city: q = q.eq("city", city)
    return q.order("price_date", desc=True).limit(30).execute().data

async def get_upload_history(tg_id: int) -> list:
    r = supabase.table("prices") \
        .select("source_file, price_date, uploaded_at, id") \
        .eq("supplier_id", tg_id) \
        .order("uploaded_at", desc=True).limit(50).execute()
    # Group by source_file + price_date
    seen, result = set(), []
    for row in r.data:
        key = (row.get("source_file"), row.get("price_date"))
        if key not in seen:
            seen.add(key)
            result.append(row)
    return result[:10]

# ── AI helpers ─────────────────────────────────────────────────────────────────
SYNONYMS = {
    "гео": "геотекстиль", "гт": "геотекстиль", "геотекст": "геотекстиль",
    "мембрана": "геомембрана", "пвд": "геомембрана пвд", "пнд": "геомембрана пнд",
    "решетка": "георешетка", "геореш": "георешетка",
    "спанб": "спанбонд", "вата": "ватин",
}

async def ai_normalize(query: str) -> str:
    q = query.lower().strip()
    for k, v in SYNONYMS.items():
        if q.startswith(k):
            q = q.replace(k, v, 1)
            break
    r = await ai.chat.completions.create(
        model=MODEL_TEXT,
        messages=[{"role": "user", "content":
            f"Нормализуй поисковый запрос для базы геоматериалов.\n"
            f"Исправь опечатки, раскрой сокращения.\n"
            f"Запрос: «{q}»\n"
            f"Верни ТОЛЬКО нормализованный запрос одной строкой без пояснений."
        }],
        max_tokens=60, temperature=0,
    )
    return r.choices[0].message.content.strip().lower()

def python_parse_excel(raw_bytes: bytes) -> tuple[list, str | None]:
    xl = pd.ExcelFile(BytesIO(raw_bytes))
    blocks, found_date = [], None
    # Ищем дату во всех листах
    for sheet in xl.sheet_names:
        try:
            df_tmp = pd.read_excel(xl, sheet_name=sheet, header=None)
            for i in range(min(8, len(df_tmp))):
                for val in df_tmp.iloc[i].values:
                    if hasattr(val, "strftime"):
                        found_date = val.strftime("%Y-%m-%d")
                        break
                if found_date: break
        except: pass
        if found_date: break

    for sheet in xl.sheet_names:
        if sheet.lower().strip() in _SKIP_SHEETS: continue
        try:
            df = pd.read_excel(xl, sheet_name=sheet, header=None)
            df = df.dropna(how="all").dropna(axis=1, how="all")
            if df.empty: continue
            text = f"=== Лист: {sheet} ===\n" + df.to_string(index=False, header=False, na_rep="")
            blocks.append((sheet, text))
        except: continue
    return blocks, found_date

async def ai_parse_sheet(sheet_name: str, text: str, price_date: str) -> list[dict]:
    lines = text.split("\n")
    header, data_lines = lines[:2], lines[2:]
    BATCH, OVERLAP = 60, 10
    result, prev_tail = [], []

    chunks = [data_lines] if len(data_lines) <= BATCH else []
    if not chunks:
        i = 0
        while i < len(data_lines):
            chunks.append(data_lines[i:i+BATCH])
            i += BATCH - OVERLAP

    for batch_n, chunk in enumerate(chunks):
        context = header + prev_tail + chunk
        chunk_text = "\n".join(context)
        prev_tail = chunk[-OVERLAP:] if len(chunk) >= OVERLAP else chunk

        prompt = f"""Парсишь прайс-лист. Лист: "{sheet_name}". Дата: {price_date}

Извлеки ВСЕ товарные позиции. Строки-продолжения (другой размер/вариант) — отдельные записи.
Ответь ТОЛЬКО валидным JSON-массивом:

[{{"product":"геотекстиль иглопробивной 200 г/м²","category":"геотекстиль","technology":"иглопробивной","density":200,"width":4.5,"roll_length":50,"thickness":null,"color":null,"material":"ПЭТ","tolerance":"до 15%","application":null,"price":26.79,"unit":"м²","price_per_roll":6032.55,"price_date":"{price_date}","original":"Геотекстиль 200"}}]

- product: нижний регистр, без торговых марок
- category: геотекстиль|георешетка|геомембрана|дренаж|спанбонд|ватин|термовойлок|анкер|прочее
- technology: иглопробивной|термосклеенный|каландрированный|сварная|гладкая|п-образный|г-образный|null
- material: ПЭ|ПЭТ|ПП|HDPE|LDPE|ПВД|сталь|null
- price: ДИЛЕРСКАЯ (последняя колонка с ценой / максимальный объём)
- unit: м²|м.п.|рулон|кг|шт
- price_per_roll: price×width×roll_length если есть, иначе null
- ПРОПУСКАЙ заголовки и строки без цены

ТАБЛИЦА:
{chunk_text}"""

        try:
            if claude:
                r = await claude.messages.create(
                    model=MODEL_CLAUDE, max_tokens=4000,
                    messages=[{"role":"user","content":prompt}],
                )
                raw_text = r.content[0].text.strip()
            else:
                r = await ai.chat.completions.create(
                    model=MODEL_TEXT, max_tokens=4000, temperature=0,
                    messages=[{"role":"user","content":prompt}],
                )
                raw_text = r.choices[0].message.content.strip()

            for fence in ["```json","```"]: raw_text = raw_text.replace(fence, "")
            parsed = json.loads(raw_text.strip())
            result.extend(parsed)
            log.info(f"Sheet '{sheet_name}' batch {batch_n+1}/{len(chunks)}: {len(parsed)} rows")
        except json.JSONDecodeError as e:
            log.warning(f"Sheet '{sheet_name}' batch {batch_n+1} JSON error: {e}")
        except Exception as e:
            log.warning(f"Sheet '{sheet_name}' batch {batch_n+1} error: {e}")

    return result

PARSE_PROMPT = f"""Парсишь прайс строительных материалов. Дата: {TODAY}
Ответь ТОЛЬКО JSON-массивом без markdown:
[{{"product":"геотекстиль 200 г/м²","category":"геотекстиль","technology":"иглопробивной","density":200,"width":4.5,"roll_length":50,"thickness":null,"color":null,"material":"ПЭТ","tolerance":"до 15%","application":null,"price":26.79,"unit":"м²","price_per_roll":6032.55,"price_date":"{TODAY}","original":"как в источнике"}}]
Правила: product нижний регистр, price=дилерская/максимальный объём, ПРОПУСКАЙ строки без цены."""

async def ai_parse_text(content: str) -> list:
    r = await ai.chat.completions.create(
        model=MODEL_TEXT,
        messages=[{"role":"system","content":PARSE_PROMPT},{"role":"user","content":content[:14000]}],
        max_tokens=4000, temperature=0,
    )
    raw = r.choices[0].message.content.strip().replace("```json","").replace("```","").strip()
    return json.loads(raw)

async def ai_parse_image(b64: str) -> list:
    r = await ai.chat.completions.create(
        model=MODEL_VISION,
        messages=[{"role":"user","content":[
            {"type":"image_url","image_url":{"url":f"data:image/jpeg;base64,{b64}"}},
            {"type":"text","text":PARSE_PROMPT},
        ]}],
        max_tokens=4000, temperature=0,
    )
    raw = r.choices[0].message.content.strip().replace("```json","").replace("```","").strip()
    return json.loads(raw)

# ── Format helpers ─────────────────────────────────────────────────────────────
def fmt(n, d=2):
    return f"{float(n):,.{d}f}".replace(",", " ") if n is not None else "—"

def fmtD(s):
    if not s: return "—"
    try: return datetime.fromisoformat(str(s)[:10]).strftime("%d.%m.%y")
    except: return str(s)[:10]

def ok_msg(count: int, city: str, price_date: str = None, sheet_report: str = "") -> str:
    lines = [
        f"✅ *Прайс загружен!*\n",
        f"📊 Позиций: *{count}*",
        f"📍 Город: {city}",
        f"📅 Дата прайса: {price_date or TODAY}",
    ]
    if sheet_report:
        lines.append(f"\n{sheet_report}")
    return "\n".join(lines)

def fmt_result(items: list, query: str, norm: str) -> tuple:
    if not items:
        return (
            f"❌ По запросу *{query}* ничего не найдено\n"
            f"_Искал: «{norm}»_\n\n"
            "Попробуйте другое название или /catalog",
            None
        )

    by_city: dict[str, dict] = {}
    for item in items:
        city = item["city"]
        if city not in by_city or item["price"] < by_city[city]["price"]:
            by_city[city] = item

    sorted_items = sorted(by_city.values(), key=lambda x: x["price"])
    medals = ["🥇","🥈","🥉"]
    lines = [f"📦 *{norm}*\n"]

    for i, item in enumerate(sorted_items[:10]):
        prefix = medals[i] if i < 3 else "  •"
        dt  = fmtD(item.get("price_date"))
        sup = item.get("supplier_name") or "—"

        attrs = []
        if item.get("technology"): attrs.append(item["technology"])
        if item.get("density"):
            tol = f' {item["tolerance"]}' if item.get("tolerance") else ""
            attrs.append(f'{item["density"]} г/м²{tol}')
        if item.get("width") and item.get("roll_length"):
            attrs.append(f'рул. {item["width"]}м × {int(item["roll_length"])}м')
        elif item.get("width"):   attrs.append(f'ш. {item["width"]} м')
        elif item.get("roll_length"): attrs.append(f'нам. {int(item["roll_length"])} м')
        if item.get("thickness"): attrs.append(f'{item["thickness"]} мм')
        if item.get("material"):  attrs.append(item["material"])

        d   = item.get("density"); w = item.get("width"); rl = item.get("roll_length")
        weight_str = ""
        if d and w and rl:
            kg = round(float(d)*float(w)*float(rl)/1000, 1)
            weight_str = f"  ⚖️ ~{kg} кг/рул\n"

        ppr = item.get("price_per_roll")
        roll_str = f"  💰 {fmt(ppr, 0)} р/рул\n" if ppr else ""
        attr_str = f"  _{' · '.join(attrs)}_\n" if attrs else ""

        lines.append(
            f"{prefix} *{item['city']}* — *{fmt(item['price'])} р/{item['unit']}*\n"
            f"{attr_str}{roll_str}{weight_str}"
            f"  🏢 {sup} · 📅 {dt}"
        )

    if len(by_city) > 10:
        lines.append(f"\n_...ещё {len(by_city)-10} городов_")

    # Кнопки
    city_btns = [
        InlineKeyboardButton(text=f"📈 {item['city']}", callback_data=f"hist:{norm[:28]}:{item['city'][:18]}")
        for item in sorted_items[:3]
    ]
    kb = InlineKeyboardMarkup(inline_keyboard=[
        city_btns,
        [
            InlineKeyboardButton(text="🔄 Обновить",    callback_data=f"search:{norm[:38]}"),
            InlineKeyboardButton(text="🌍 Все города",  callback_data=f"allcities:{norm[:36]}"),
            InlineKeyboardButton(text="📥 Excel",       callback_data=f"excel:{norm[:36]}"),
        ],
    ]) if city_btns else None

    return "\n".join(lines), kb

def fmt_history(items: list, product: str, city: str = None) -> str:
    loc = f" — {city}" if city else ""
    if not items: return f"❌ Нет истории по «{product}»{loc}"
    lines = [f"📈 *{product}*{loc}\n"]
    grouped: dict[str, list] = {}
    for item in items:
        k = f"{item['city']} · {item['supplier_name']}"
        grouped.setdefault(k, []).append(item)
    for key, rows in grouped.items():
        lines.append(f"*{key}*")
        for r in rows:
            prev = r.get("prev_price")
            d = r["price"] - float(prev) if prev else None
            delta = f"  {'▲' if d>0 else '▼'} {fmt(abs(d))}" if d and abs(d)>0.01 else ""
            lines.append(f"  {fmtD(r['price_date'])}   {fmt(r['price'])} р/{r['unit']}{delta}")
        lines.append("")
    return "\n".join(lines)

# ── Excel export ───────────────────────────────────────────────────────────────
async def export_to_excel(items: list, query: str) -> BytesIO:
    by_city: dict[str, dict] = {}
    for item in items:
        city = item["city"]
        if city not in by_city or item["price"] < by_city[city]["price"]:
            by_city[city] = item
    rows = sorted(by_city.values(), key=lambda x: x["price"])

    df = pd.DataFrame([{
        "Товар":         r.get("product",""),
        "Категория":     r.get("category",""),
        "Технология":    r.get("technology",""),
        "Плотность г/м²":r.get("density",""),
        "Ширина м":      r.get("width",""),
        "Намотка м":     r.get("roll_length",""),
        "Толщина мм":    r.get("thickness",""),
        "Материал":      r.get("material",""),
        "Допуск":        r.get("tolerance",""),
        "Цена р/м²":     r.get("price",""),
        "Цена р/рул":    r.get("price_per_roll",""),
        "Ед. изм.":      r.get("unit",""),
        "Город":         r.get("city",""),
        "Поставщик":     r.get("supplier_name",""),
        "Дата прайса":   r.get("price_date",""),
    } for r in rows])

    buf = BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Цены")
        ws = writer.sheets["Цены"]
        for col in ws.columns:
            ws.column_dimensions[col[0].column_letter].width = max(
                len(str(col[0].value or "")),
                max((len(str(c.value or "")) for c in col), default=0)
            ) + 3
    buf.seek(0)
    return buf

# ── Profile keyboard ───────────────────────────────────────────────────────────
def profile_kb(role: str) -> InlineKeyboardMarkup:
    other_role  = "manager" if role == "snabzhenets" else "snabzhenets"
    other_label = "🔍 Стать менеджером" if other_role == "manager" else "📦 Стать снабженцем"
    rows = [[InlineKeyboardButton(text=other_label, callback_data=f"switchrole:{other_role}")]]
    if role == "snabzhenets":
        rows.append([
            InlineKeyboardButton(text="📍 Сменить город",      callback_data="edit:city"),
            InlineKeyboardButton(text="🏢 Сменить поставщика", callback_data="edit:supplier"),
        ])
        rows.append([
            InlineKeyboardButton(text="📋 История загрузок", callback_data="uploads:list"),
        ])
    return InlineKeyboardMarkup(inline_keyboard=rows)

# ── Registration ───────────────────────────────────────────────────────────────
@router.message(CommandStart())
async def cmd_start(msg: Message, state: FSMContext):
    user = await get_user(msg.from_user.id)
    if user:
        role_label = {"snabzhenets":"📦 Снабженец","manager":"🔍 Менеджер","admin":"⚙ Администратор"}.get(user["role"], user["role"])
        tip = "Отправляйте прайсы — файл, фото или текст." if user["role"] == "snabzhenets" \
              else "Напишите название товара для поиска цены."
        await msg.answer(
            f"Вы в системе как *{role_label}* ({user['city']}).\n\n{tip}\n\n_/myinfo — профиль и смена роли_",
            parse_mode="Markdown", reply_markup=profile_kb(user["role"]),
        )
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📦 Снабженец — загружаю прайсы", callback_data="role_snabzhenets")],
        [InlineKeyboardButton(text="🔍 Менеджер — ищу цены",         callback_data="role_manager")],
    ])
    await msg.answer("Добро пожаловать в *Снабженец* 👋\n\nВыберите роль:", parse_mode="Markdown", reply_markup=kb)
    await state.set_state(Reg.role)

@router.callback_query(Reg.role)
async def cb_role(cb: CallbackQuery, state: FSMContext):
    await state.update_data(role=cb.data.replace("role_", ""))
    await cb.message.edit_text("Укажите ваш город:\n_например: Нижний Новгород_", parse_mode="Markdown")
    await state.set_state(Reg.city)

@router.message(Reg.city)
async def reg_city(msg: Message, state: FSMContext):
    data = await state.get_data()
    city = msg.text.strip()
    await state.update_data(city=city)
    if data["role"] == "snabzhenets":
        await msg.answer(f"Город: *{city}*\n\nВведите *название поставщика*:\n_ООО Армпласт, ИП Иванов..._", parse_mode="Markdown")
        await state.set_state(Reg.supplier)
    else:
        supabase.table("users").insert({
            "telegram_id":msg.from_user.id,"username":msg.from_user.username or "",
            "name":msg.from_user.full_name,"role":"manager","city":city,"supplier_name":"",
        }).execute()
        await state.clear()
        await msg.answer(
            f"✅ Вы *Менеджер*, г. {city}\n\nНапишите название товара:\n_геотекстиль 200 / мембрана 1мм / спанбонд 60_",
            parse_mode="Markdown",
        )

@router.message(Reg.supplier)
async def reg_supplier(msg: Message, state: FSMContext):
    data = await state.get_data()
    sup  = msg.text.strip()
    supabase.table("users").insert({
        "telegram_id":msg.from_user.id,"username":msg.from_user.username or "",
        "name":msg.from_user.full_name,"role":"snabzhenets",
        "city":data["city"],"supplier_name":sup,
    }).execute()
    await state.clear()
    await msg.answer(
        f"✅ *Снабженец* зарегистрирован!\n\n📍 Город: {data['city']}\n🏢 Поставщик: *{sup}*\n\n"
        "Отправляйте прайсы — Excel, фото, текст.\nНейронка сама разберёт структуру.\n\n"
        "_/myinfo — профиль · /setcity · /setsupplier_",
        parse_mode="Markdown",
    )

# ── Profile commands ───────────────────────────────────────────────────────────
def profile_text(user: dict) -> str:
    role_label = {"snabzhenets":"📦 Снабженец","manager":"🔍 Менеджер","admin":"⚙ Администратор"}.get(user["role"], user["role"])
    sup = user.get("supplier_name") or "—"
    return f"👤 *Ваш профиль*\n\nРоль: {role_label}\nГород: {user['city']}\nПоставщик: {sup}"

@router.message(F.text == "/myinfo")
async def cmd_myinfo(msg: Message):
    user = await get_user(msg.from_user.id)
    if not user: await msg.answer("Начните с /start"); return
    await msg.answer(profile_text(user), parse_mode="Markdown", reply_markup=profile_kb(user["role"]))

@router.callback_query(F.data.startswith("switchrole:"))
async def cb_switchrole(cb: CallbackQuery, state: FSMContext):
    new_role = cb.data.split(":")[1]
    user = await get_user(cb.from_user.id)
    if not user: await cb.answer("Сначала /start"); return

    if new_role == "snabzhenets" and not user.get("supplier_name"):
        # Не алерт — просим ввести поставщика прямо здесь
        await cb.answer()
        await cb.message.answer(
            "Введите *название поставщика* для завершения смены роли:\n"
            "_ООО Армпласт, ИП Иванов..._",
            parse_mode="Markdown",
        )
        await state.update_data(pending_role="snabzhenets")
        await state.set_state(Edit.supplier)
        return

    supabase.table("users").update({"role": new_role}).eq("telegram_id", cb.from_user.id).execute()
    user["role"] = new_role
    await cb.answer("Роль изменена!")
    await cb.message.edit_text(profile_text(user), parse_mode="Markdown", reply_markup=profile_kb(new_role))

@router.callback_query(F.data == "edit:city")
async def cb_edit_city(cb: CallbackQuery, state: FSMContext):
    await cb.answer(); await cb.message.answer("Введите новый город:"); await state.set_state(Edit.city)

@router.callback_query(F.data == "edit:supplier")
async def cb_edit_supplier(cb: CallbackQuery, state: FSMContext):
    await cb.answer(); await cb.message.answer("Введите название поставщика:"); await state.set_state(Edit.supplier)

@router.message(Edit.city)
async def edit_city(msg: Message, state: FSMContext):
    city = msg.text.strip()
    supabase.table("users").update({"city":city}).eq("telegram_id",msg.from_user.id).execute()
    await state.clear()
    await msg.answer(f"✅ Город: *{city}*", parse_mode="Markdown")

@router.message(Edit.supplier)
async def edit_supplier(msg: Message, state: FSMContext):
    sup  = msg.text.strip()
    data = await state.get_data()
    updates = {"supplier_name": sup}
    # Если ждали поставщика чтобы сменить роль
    if data.get("pending_role"):
        updates["role"] = data["pending_role"]
    supabase.table("users").update(updates).eq("telegram_id", msg.from_user.id).execute()
    await state.clear()
    user = await get_user(msg.from_user.id)
    role_label = {"snabzhenets":"📦 Снабженец","manager":"🔍 Менеджер"}.get(user["role"], user["role"])
    await msg.answer(
        f"✅ Поставщик: *{sup}* · Роль: {role_label}\n\nОтправляйте прайсы — файл, фото или текст.",
        parse_mode="Markdown",
        reply_markup=profile_kb(user["role"]),
    )

@router.message(F.text.startswith("/setcity"))
async def cmd_setcity(msg: Message, state: FSMContext):
    inline = msg.text.replace("/setcity","").strip()
    if inline:
        supabase.table("users").update({"city":inline}).eq("telegram_id",msg.from_user.id).execute()
        await msg.answer(f"✅ Город: *{inline}*", parse_mode="Markdown")
    else:
        await msg.answer("Введите новый город:")
        await state.set_state(Edit.city)

@router.message(F.text.startswith("/setsupplier"))
async def cmd_setsupplier(msg: Message, state: FSMContext):
    inline = msg.text.replace("/setsupplier","").strip()
    if inline:
        supabase.table("users").update({"supplier_name":inline}).eq("telegram_id",msg.from_user.id).execute()
        await msg.answer(f"✅ Поставщик: *{inline}*", parse_mode="Markdown")
    else:
        await msg.answer("Введите название поставщика:")
        await state.set_state(Edit.supplier)

# ── Upload history & delete ────────────────────────────────────────────────────
@router.callback_query(F.data == "uploads:list")
async def cb_uploads_list(cb: CallbackQuery):
    user = await get_user(cb.from_user.id)
    if not user: await cb.answer(); return
    await cb.answer()
    uploads = await get_upload_history(cb.from_user.id)
    if not uploads:
        await cb.message.answer("Загрузок пока нет.")
        return
    lines = ["📋 *Ваши загрузки:*\n"]
    for u in uploads[:8]:
        fname = u.get("source_file") or "текст"
        dt    = fmtD(u.get("uploaded_at","")[:10])
        pd_   = fmtD(u.get("price_date"))
        lines.append(f"• {fname[:30]} · {pd_} · загр. {dt}")
    # Кнопка удалить последний
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🗑 Удалить последний прайс", callback_data="uploads:delete_last"),
    ]])
    await cb.message.answer("\n".join(lines), parse_mode="Markdown", reply_markup=kb)

@router.callback_query(F.data == "uploads:delete_last")
async def cb_delete_last(cb: CallbackQuery):
    await cb.answer()
    user = await get_user(cb.from_user.id)
    if not user: return
    uploads = await get_upload_history(cb.from_user.id)
    if not uploads:
        await cb.message.answer("Нет загрузок для удаления.")
        return
    last = uploads[0]
    fname = last.get("source_file")
    pd_   = last.get("price_date")
    # Удаляем все записи этой загрузки
    q = supabase.table("prices").delete().eq("supplier_id", cb.from_user.id)
    if fname: q = q.eq("source_file", fname)
    if pd_:   q = q.eq("price_date", pd_)
    q.execute()
    await cb.message.answer(f"🗑 Удалён прайс: *{fname or 'текст'}* от {fmtD(pd_)}", parse_mode="Markdown")

# ── History command ────────────────────────────────────────────────────────────
@router.message(F.text.startswith("/history"))
async def cmd_history(msg: Message):
    user = await get_user(msg.from_user.id)
    if not user: await msg.answer("Начните с /start"); return
    parts = msg.text.replace("/history","").strip().rsplit(" ",1)
    if not parts or not parts[0]:
        await msg.answer("Формат: `/history геотекстиль 200 Казань`", parse_mode="Markdown"); return
    product = parts[0].strip()
    city    = parts[1].strip() if len(parts) > 1 else None
    items   = await get_history(product, city)
    await msg.answer(fmt_history(items, product, city), parse_mode="Markdown")

# ── Catalog command ────────────────────────────────────────────────────────────
@router.message(F.text == "/catalog")
async def cmd_catalog(msg: Message):
    r = supabase.table("prices_latest").select("category").execute()
    cats = {}
    for row in r.data:
        c = row.get("category") or "прочее"
        cats[c] = cats.get(c, 0) + 1
    lines = ["📂 *Каталог материалов:*\n"]
    for cat, cnt in sorted(cats.items(), key=lambda x:-x[1]):
        lines.append(f"• {cat} — {cnt} позиций")
    lines.append("\n_Напишите название для поиска_")
    await msg.answer("\n".join(lines), parse_mode="Markdown")

# ── Admin commands ─────────────────────────────────────────────────────────────
@router.message(F.text == "/users")
async def cmd_users(msg: Message):
    if msg.from_user.id not in ADMIN_IDS: await msg.answer("⛔ Нет доступа."); return
    r = supabase.table("users").select("*").order("created_at",desc=True).execute()
    lines = ["👥 *Пользователи:*\n"]
    for u in r.data[:30]:
        emoji = "📦" if u["role"]=="snabzhenets" else "🔍" if u["role"]=="manager" else "⚙"
        sup   = f" · {u['supplier_name']}" if u.get("supplier_name") else ""
        lines.append(f"{emoji} `{u['telegram_id']}` {u['name']}{sup} — {u['city']}")
    await msg.answer("\n".join(lines), parse_mode="Markdown")

@router.message(F.text.startswith("/setrole"))
async def cmd_setrole(msg: Message):
    if msg.from_user.id not in ADMIN_IDS: await msg.answer("⛔ Нет доступа."); return
    parts = msg.text.split()
    if len(parts) < 3: await msg.answer("Формат: `/setrole <telegram_id> <роль>`", parse_mode="Markdown"); return
    try: tg_id = int(parts[1])
    except: await msg.answer("Укажите числовой Telegram ID"); return
    role = parts[2].lower()
    if role not in ("snabzhenets","manager","admin"): await msg.answer("Роль: snabzhenets|manager|admin"); return
    supabase.table("users").update({"role":role}).eq("telegram_id",tg_id).execute()
    await msg.answer(f"✅ Роль `{tg_id}` → *{role}*", parse_mode="Markdown")

# ── Callbacks ──────────────────────────────────────────────────────────────────
@router.callback_query(F.data.startswith("hist:"))
async def cb_history(cb: CallbackQuery):
    parts = cb.data.split(":")
    product = parts[1] if len(parts)>1 else ""
    city    = parts[2] if len(parts)>2 else None
    await cb.answer()
    items = await get_history(product, city)
    await cb.message.answer(fmt_history(items, product, city), parse_mode="Markdown")

@router.callback_query(F.data.startswith("search:"))
async def cb_search(cb: CallbackQuery):
    query = cb.data.replace("search:","")
    await cb.answer("Обновляю...")
    items, norm = await search_prices(query)
    text, kb = fmt_result(items, query, norm)
    await cb.message.answer(text, parse_mode="Markdown", reply_markup=kb)

@router.callback_query(F.data.startswith("allcities:"))
async def cb_allcities(cb: CallbackQuery):
    query = cb.data.replace("allcities:","")
    await cb.answer()
    items, norm = await search_prices(query)
    if not items: await cb.message.answer(f"❌ Ничего не найдено по «{norm}»"); return
    by_city: dict[str, dict] = {}
    for item in items:
        city = item["city"]
        if city not in by_city or item["price"] < by_city[city]["price"]:
            by_city[city] = item
    sorted_items = sorted(by_city.values(), key=lambda x: x["price"])
    lines = [f"📦 *{norm}* — все города\n"]
    for item in sorted_items:
        dt  = fmtD(item.get("price_date"))
        sup = item.get("supplier_name") or "—"
        lines.append(f"• *{item['city']}* — {fmt(item['price'])} р/{item['unit']}  _{sup} · {dt}_")
    await cb.message.answer("\n".join(lines), parse_mode="Markdown")

@router.callback_query(F.data.startswith("excel:"))
async def cb_excel(cb: CallbackQuery):
    query = cb.data.replace("excel:","")
    await cb.answer("Формирую Excel...")
    items, norm = await search_prices(query)
    if not items: await cb.message.answer("Нет данных для экспорта."); return
    buf = await export_to_excel(items, norm)
    fname = f"prices_{norm[:20].replace(' ','_')}_{TODAY}.xlsx"
    await cb.message.answer_document(
        BufferedInputFile(buf.read(), filename=fname),
        caption=f"📥 *{norm}* — {len(set(i['city'] for i in items))} городов",
        parse_mode="Markdown",
    )

# ── Document handler ───────────────────────────────────────────────────────────
@router.message(F.document)
async def on_document(msg: Message):
    user = await get_user(msg.from_user.id)
    if not user or user["role"] not in ("snabzhenets","admin"): return
    await msg.answer("⏳ Нейронка обрабатывает файл...")
    try:
        file  = await bot.get_file(msg.document.file_id)
        raw   = await bot.download_file(file.file_path)
        fname = msg.document.file_name or "price"

        if fname.lower().endswith((".xlsx",".xls")):
            raw_bytes = raw.read()
            await msg.answer("📊 Читаю файл...")
            blocks, price_date = python_parse_excel(raw_bytes)
            if not blocks:
                await msg.answer("❌ Не удалось прочитать файл."); return
            await msg.answer(f"✅ Листов: {len(blocks)}\n⏳ AI разбирает структуру...")
            all_items, sheet_counts = [], []
            for sheet_name, text in blocks:
                sheet_items = await ai_parse_sheet(sheet_name, text, price_date or TODAY)
                all_items.extend(sheet_items)
                sheet_counts.append(f"  • {sheet_name}: {len(sheet_items)}")
                log.info(f"Sheet '{sheet_name}': {len(sheet_items)} items")
            if not all_items:
                await msg.answer("❌ AI не смог извлечь позиции."); return
            report = "\n".join(sheet_counts)
            count = await save_prices(all_items, user, fname)
            await msg.answer(ok_msg(count, user["city"], price_date, report), parse_mode="Markdown")

        elif fname.lower().endswith(".csv"):
            df      = pd.read_csv(BytesIO(raw.read()), encoding="utf-8-sig")
            content = df.to_string(index=False)
            items   = await ai_parse_text(content)
            count   = await save_prices(items, user, fname)
            await msg.answer(ok_msg(count, user["city"]), parse_mode="Markdown")

        else:
            content = raw.read().decode("utf-8", errors="ignore")
            items   = await ai_parse_text(content)
            count   = await save_prices(items, user, fname)
            await msg.answer(ok_msg(count, user["city"]), parse_mode="Markdown")

    except Exception as e:
        log.error(f"document error: {e}", exc_info=True)
        await msg.answer(f"❌ Ошибка: {str(e)[:300]}")

# ── Photo handler ──────────────────────────────────────────────────────────────
@router.message(F.photo)
async def on_photo(msg: Message):
    user = await get_user(msg.from_user.id)
    if not user or user["role"] not in ("snabzhenets","admin"): return
    await msg.answer("⏳ Нейронка читает прайс с фото...")
    try:
        photo = msg.photo[-1]
        file  = await bot.get_file(photo.file_id)
        raw   = await bot.download_file(file.file_path)
        b64   = base64.b64encode(raw.read()).decode()
        items = await ai_parse_image(b64)
        count = await save_prices(items, user, "photo")
        await msg.answer(ok_msg(count, user["city"]), parse_mode="Markdown")
    except json.JSONDecodeError:
        await msg.answer("❌ Не удалось распознать. Сделайте более чёткий снимок.")
    except Exception as e:
        log.error(f"photo error: {e}", exc_info=True)
        await msg.answer(f"❌ Ошибка: {str(e)[:200]}")

# ── Text handler ───────────────────────────────────────────────────────────────
@router.message(F.text & ~F.text.startswith("/"))
async def on_text(msg: Message):
    user = await get_user(msg.from_user.id)
    if not user: await msg.answer("Начните с /start"); return

    if user["role"] in ("snabzhenets","admin"):
        await msg.answer("⏳ Парсю прайс из текста...")
        try:
            items = await ai_parse_text(msg.text)
            count = await save_prices(items, user, "text")
            if count:
                await msg.answer(ok_msg(count, user["city"]), parse_mode="Markdown")
            else:
                await msg.answer("Позиций с ценами не найдено.\n\nПример: `Геотекстиль 200 г/м² — 26.79 р/м²`", parse_mode="Markdown")
        except Exception as e:
            log.error(f"text parse error: {e}", exc_info=True)
            await msg.answer("❌ Не удалось распознать.")
    else:
        await msg.answer("🔍 Ищу...")
        try:
            items, norm = await search_prices(msg.text)
            text, kb = fmt_result(items, msg.text.strip(), norm)
            await msg.answer(text, parse_mode="Markdown", reply_markup=kb)
        except Exception as e:
            log.error(f"search error: {e}", exc_info=True)
            await msg.answer("❌ Ошибка поиска. Попробуйте ещё раз.")

# ── Run ────────────────────────────────────────────────────────────────────────
async def main():
    log.info("Starting Snabzhenets bot...")
    await dp.start_polling(bot, skip_updates=True)

if __name__ == "__main__":
    asyncio.run(main())
