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
import anthropic
from supabase import create_client

load_dotenv()

BOT_TOKEN      = os.getenv("BOT_TOKEN")
OPENROUTER_KEY = os.getenv("OPENROUTER_KEY")
ADMIN_IDS      = [int(x) for x in os.getenv("ADMIN_IDS","").split(",") if x.strip()]
SUPABASE_URL   = os.getenv("SUPABASE_URL")
SUPABASE_KEY   = os.getenv("SUPABASE_KEY")

MODEL_TEXT   = os.getenv("MODEL", "google/gemini-2.0-flash-001")
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

# Anthropic — для AI валидации прайсов
ANTHROPIC_KEY = os.getenv("ANTHROPIC_KEY")
claude = anthropic.AsyncAnthropic(api_key=ANTHROPIC_KEY) if ANTHROPIC_KEY else None

# ── States ─────────────────────────────────────────────────────────────────────
class Reg(StatesGroup):
    role     = State()
    city     = State()
    supplier = State()   # название поставщика (обязательно для снабженца)

class Edit(StatesGroup):
    city     = State()
    supplier = State()

# ── AI prompts ─────────────────────────────────────────────────────────────────
TODAY = str(date.today())

VALIDATE_PROMPT = """Ты — валидатор и обогатитель прайс-листов геоматериалов.
Получишь JSON-массив сырых строк из Excel. Нормализуй и максимально заполни каждое поле.
Ответь ТОЛЬКО валидным JSON-массивом без markdown:

[{
  "product":       "геотекстиль иглопробивной 200 г/м²",
  "category":      "геотекстиль",
  "technology":    "иглопробивной",
  "density":       200,
  "width":         4.5,
  "roll_length":   50,
  "thickness":     null,
  "color":         null,
  "material":      "ПЭТ",
  "tolerance":     "±15%",
  "application":   null,
  "price":         26.79,
  "unit":          "м²",
  "price_per_roll": 6032.55,
  "price_date":    "2026-02-01",
  "original":      "Геотекстиль Пошхим 200"
}]

ПРАВИЛА:
product: нижний регистр, без торговых марок в кавычках, включай технологию если известна
  ✓ "геотекстиль иглопробивной 200 г/м²"
  ✗ "геотекстиль пошхим 200"

technology — выводи из названия raw_name:
  слова: иглопробивной, термосклеенный, термоскреплённый, тканый, каландрированный,
         экструзионный, гладкая, профилированная, растянутая, сварная → заполняй
  Если не указано явно → null (не угадывай)

material — определяй по категории и названию:
  Иглопробивной геотекстиль → обычно ПЭТ или смесь ПП+ПЭТ
  Термосклеенный геотекстиль → обычно ПП
  Спанбонд → ПП
  Геомембрана ПВД → LDPE/ПВД
  Геомембрана HDPE → HDPE
  Если не уверен → null

tolerance: ищи в оригинале: "±15%", "до 15%", "±5 г/м²" → заполняй строкой
  null если нет

category: геотекстиль | георешетка | геомембрана | дренаж | спанбонд | ватин | термовойлок | прочее
density: число г/м² из названия
width, roll_length: из данных Excel (не меняй)
thickness: мм для мембран
price_per_roll: price × width × roll_length если оба есть, иначе null
price_date: используй переданную дату
НЕ меняй цены. НЕ добавляй несуществующие строки."""


def python_normalize(raw_rows: list[dict], price_date: str) -> list[dict]:
    """Python-нормализация без AI — фоллбек если AI недоступен."""
    import re
    result = []
    for row in raw_rows:
        name = row.get("raw_name", "")
        # Убрать торговые марки в кавычках
        product = re.sub(r'["\'«»][^"\'«»]+["\'«»]', "", name).strip().lower()
        product = re.sub(r"\s+", " ", product).strip()
        # Плотность из имени
        m = re.search(r"(\d+(?:\.\d+)?)\s*(?:г/м[²2])?\s*$", name.strip())
        density = float(m.group(1)) if m else None
        w  = row.get("width")
        rl = row.get("roll_length")
        p  = row.get("price")
        result.append({
            "product":          row.get("product") or product or name.lower().strip(),
            "product_original": name,
            "category":         row.get("category"),
            "technology":       row.get("technology"),
            "density":          density,
            "width":            row.get("width") if row.get("width") is not None else w,
            "roll_length":      row.get("roll_length") if row.get("roll_length") is not None else rl,
            "thickness":        row.get("thickness"),
            "color":            row.get("color"),
            "material":         row.get("material"),
            "tolerance":        row.get("tolerance"),
            "application":      row.get("application"),
            "price":            p,
            "unit":             row.get("unit", "м²"),
            "price_per_roll":   row.get("price_per_roll") or (round(p * w * rl, 2) if (p and w and rl) else None),
            "price_date":       price_date,
            "original":         name,
        })
    return result


async def ai_validate(raw_rows: list[dict], price_date: str) -> list[dict]:
    """Шаг 2: AI проверяет и нормализует. При ошибке — Python фоллбек."""
    BATCH = 60
    result = []
    ai_failed = False

    for i in range(0, len(raw_rows), BATCH):
        if ai_failed:
            # AI уже сломался — нормализуем Python-ом
            result.extend(python_normalize(raw_rows[i:i + BATCH], price_date))
            continue

        batch = raw_rows[i:i + BATCH]
        payload = json.dumps(batch, ensure_ascii=False)
        try:
            if claude:
                # Claude через Anthropic SDK
                r = await claude.messages.create(
                    model=MODEL_CLAUDE,
                    max_tokens=8000,
                    system=VALIDATE_PROMPT,
                    messages=[{"role": "user", "content": f"price_date: {price_date}\n\n{payload}"}],
                )
                raw_text = r.content[0].text.strip()
            else:
                # Фоллбек на OpenRouter
                r = await ai.chat.completions.create(
                    model=MODEL_TEXT,
                    messages=[
                        {"role": "system", "content": VALIDATE_PROMPT},
                        {"role": "user",   "content": f"price_date: {price_date}\n\n{payload}"},
                    ],
                    max_tokens=8000,
                    temperature=0,
                )
                raw_text = r.choices[0].message.content.strip()
            # Очистка markdown-оберток
            for fence in ["```json", "```"]:
                raw_text = raw_text.replace(fence, "")
            raw_text = raw_text.strip()
            parsed = json.loads(raw_text)
            result.extend(parsed)
            log.info(f"AI validate batch {i//BATCH+1}: {len(parsed)} rows OK")
        except json.JSONDecodeError as e:
            log.warning(f"AI validate JSON error batch {i//BATCH+1}: {e} — fallback to Python")
            ai_failed = True
            result.extend(python_normalize(batch, price_date))
        except Exception as e:
            log.warning(f"AI validate error batch {i//BATCH+1}: {e} — fallback to Python")
            ai_failed = True
            result.extend(python_normalize(batch, price_date))

    return result



# ── Pipeline: Python parse → AI validate → save ──────────────────────────────

import re as _re

_CAT_MAP = {
    "геотекстиль эко": "геотекстиль",
    "геотекстиль":     "геотекстиль",
    "георешетка":      "георешетка",
    "геомембрана":     "геомембрана",
    "термовойлок":     "прочее",
    "ватин":           "прочее",
    "анкера":          "прочее",
    "дренаж":          "дренаж",
    "спанбонд":        "спанбонд",
}
_SKIP_SHEETS = {"содержание", "оглавление", "для формирования цены", "contents", "sheet1"}


def _density(name: str):
    m = _re.search(r"(\d+(?:\.\d+)?)\s*(?:г/м[²2])?\s*$", name.strip())
    if m:
        return float(m.group(1))
    m2 = _re.search(r"\s(\d{2,4})\s*$", name.strip())
    return float(m2.group(1)) if m2 else None


def python_parse_excel(raw_bytes: bytes) -> tuple[list[dict], str | None]:
    """
    Шаг 1: Python читает Excel и конвертирует каждый лист в текст.
    Не делает никаких предположений о структуре — просто извлекает данные.
    Возвращает (raw_rows_as_text_blocks, price_date).
    """
    xl = pd.ExcelFile(BytesIO(raw_bytes))
    blocks = []      # список (sheet_name, text_block)
    found_date = None

    # Сначала ищем дату в ЛЮБОМ листе включая содержание
    for sheet in xl.sheet_names:
        try:
            df_tmp = pd.read_excel(xl, sheet_name=sheet, header=None)
            for i in range(min(8, len(df_tmp))):
                for val in df_tmp.iloc[i].values:
                    if hasattr(val, "strftime"):
                        found_date = val.strftime("%Y-%m-%d")
                        break
                if found_date:
                    break
        except Exception:
            pass
        if found_date:
            break

    for sheet in xl.sheet_names:
        if sheet.lower().strip() in _SKIP_SHEETS:
            continue
        try:
            df = pd.read_excel(xl, sheet_name=sheet, header=None)
        except Exception:
            continue

        # Убираем полностью пустые строки и столбцы
        df = df.dropna(how="all").dropna(axis=1, how="all")
        if df.empty:
            continue

        # Конвертируем в читаемый текст с заголовком листа
        text = f"=== Лист: {sheet} ===\n"
        text += df.to_string(index=False, header=False, na_rep="")
        blocks.append((sheet, text))

    return blocks, found_date


async def ai_parse_sheet(sheet_name: str, text: str, price_date: str) -> list[dict]:
    """
    AI парсит ОДИН лист прайса любой структуры.
    Батчи по 60 строк с перекрытием 10 строк для сохранения контекста.
    """
    lines = text.split("\n")
    # Первые строки — заголовок таблицы (имя листа + строка колонок)
    header = lines[:2]
    data_lines = lines[2:]

    BATCH   = 60   # строк в батче
    OVERLAP = 10   # перекрытие — последние N строк предыдущего батча
    result  = []
    prev_tail = []  # хвост предыдущего батча для контекста

    # Если данных мало — один запрос
    if len(data_lines) <= BATCH:
        chunks = [data_lines]
    else:
        chunks = []
        i = 0
        while i < len(data_lines):
            chunks.append(data_lines[i:i+BATCH])
            i += BATCH - OVERLAP

    for batch_n, chunk in enumerate(chunks):
        # Контекст: заголовок + хвост предыдущего батча + текущий батч
        context = header + prev_tail + chunk
        chunk_text = "\n".join(context)
        prev_tail = chunk[-OVERLAP:] if len(chunk) >= OVERLAP else chunk

        prompt = f"""Ты парсишь прайс-лист. Лист называется "{sheet_name}".
Дата прайса: {price_date}

Извлеки ВСЕ товарные позиции с ценами из таблицы ниже.
Если строка — продолжение предыдущего товара (другой размер/вариант) — создай отдельную запись.

Ответь ТОЛЬКО валидным JSON-массивом без markdown, комментариев и пояснений:
[{{
  "product": "геотекстиль иглопробивной 200 г/м²",
  "category": "геотекстиль",
  "technology": "иглопробивной",
  "density": 200,
  "width": 4.5,
  "roll_length": 50,
  "thickness": null,
  "color": null,
  "material": "ПЭТ",
  "tolerance": "до 15%",
  "application": null,
  "price": 26.79,
  "unit": "м²",
  "price_per_roll": 6032.55,
  "price_date": "{price_date}",
  "original": "Геотекстиль Пошхим 200"
}}]

ПРАВИЛА:
- product: нижний регистр, без торговых марок в кавычках
- category: геотекстиль|георешетка|геомембрана|дренаж|спанбонд|ватин|термовойлок|анкер|прочее
- technology: иглопробивной|термосклеенный|каландрированный|сварная|гладкая|п-образный|г-образный|null
- material: ПЭ|ПЭТ|ПП|HDPE|LDPE|ПВД|сталь|null
- tolerance: строка вида "±15%" или "до 15%" или null
- density: г/м² числом или null
- width: ширина в метрах или диаметр в мм для анкеров или null
- roll_length: намотка в метрах или длина в мм для анкеров или null
- thickness: толщина в мм или null
- price: ДИЛЕРСКАЯ цена (последняя колонка с ценой, минимальная, наибольший объём) — число
- unit: м²|м.п.|рулон|кг|шт — определи из контекста
- price_per_roll: цена за рулон если можно вычислить = price × width × roll_length, иначе null
- ПРОПУСКАЙ строки без цены, заголовки, пустые строки
- НЕ придумывай данные которых нет в таблице

ТАБЛИЦА:
{chunk_text}"""

        try:
            if claude:
                r = await claude.messages.create(
                    model=MODEL_CLAUDE,
                    max_tokens=4000,
                    messages=[{"role": "user", "content": prompt}],
                )
                raw_text = r.content[0].text.strip()
            else:
                r = await ai.chat.completions.create(
                    model=MODEL_TEXT,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=4000,
                    temperature=0,
                )
                raw_text = r.choices[0].message.content.strip()

            # Очистка
            for fence in ["```json", "```"]:
                raw_text = raw_text.replace(fence, "")
            raw_text = raw_text.strip()

            parsed = json.loads(raw_text)
            result.extend(parsed)
            log.info(f"  Sheet '{sheet_name}' batch {batch_n+1}/{len(chunks)}: {len(parsed)} rows")

        except json.JSONDecodeError as e:
            log.warning(f"  Sheet '{sheet_name}' batch {batch_n+1} JSON error: {e} | raw={raw_text[:200]}")
        except Exception as e:
            log.warning(f"  Sheet '{sheet_name}' batch {batch_n+1} error: {e}")

    return result






# ── AI prompts ─────────────────────────────────────────────────────────────────
TODAY = str(date.today())

# ── Direct Excel parser (structured, no AI) ───────────────────────────────────
_CAT_MAP = {
    "геотекстиль эко": "геотекстиль",
    "геотекстиль":     "геотекстиль",
    "георешетка":      "георешетка",
    "геомембрана":     "геомембрана",
    "термовойлок":     "прочее",
    "ватин":           "прочее",
    "анкера":          "прочее",
    "дренаж":          "дренаж",
    "спанбонд":        "спанбонд",
}
_SKIP_SHEETS = {"содержание", "оглавление", "для формирования цены", "contents", "sheet1"}

def _extract_density(name: str) -> float | None:
    import re
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:г/м²|г/м2|г)?\s*$", name.strip())
    if m:
        return float(m.group(1))
    m2 = re.search(r"\s(\d{2,4})\s*$", name.strip())
    return float(m2.group(1)) if m2 else None

def parse_excel_direct(raw_bytes: bytes, price_date: str = None) -> list[dict]:
    """
    Структурный парсер для прайсов с колонками:
    0=название, 3=ширина, 4=намотка, 10=цена ДИЛЕР
    Возвращает список dict совместимых с save_prices().
    """
    import re
    xl = pd.ExcelFile(BytesIO(raw_bytes))
    results = []
    found_date = price_date

    for sheet in xl.sheet_names:
        if sheet.lower().strip() in _SKIP_SHEETS:
            continue
        try:
            df = pd.read_excel(xl, sheet_name=sheet, header=None)
        except Exception:
            continue

        category = _CAT_MAP.get(sheet.lower().strip(), "прочее")

        # Ищем дату в первых 5 строках
        if not found_date:
            for i in range(min(5, len(df))):
                for val in df.iloc[i].values:
                    if hasattr(val, "strftime"):
                        found_date = val.strftime("%Y-%m-%d")
                        break
                if found_date:
                    break

        # Найти строку заголовков (содержит "Ширина" и "Намотка")
        header_row = None
        price_col  = None
        width_col  = None
        roll_col   = None
        name_col   = 0

        for i, row in df.iterrows():
            row_str = " ".join(str(v) for v in row.values if pd.notna(v))
            if "Ширина" in row_str and ("Намотка" in row_str or "намотк" in row_str.lower()):
                header_row = i
                # Определяем индексы нужных колонок
                for j, val in enumerate(row.values):
                    s = str(val).lower() if pd.notna(val) else ""
                    if "ширина" in s:
                        width_col = j
                    elif "намотк" in s:
                        roll_col = j
                    elif "дилер" in s:
                        price_col = j
                # Fallback: известная структура (col 3, 4, 10)
                if width_col  is None: width_col  = 3
                if roll_col   is None: roll_col   = 4
                if price_col  is None: price_col  = 10
                break

        if header_row is None:
            # Попробуем AI-парсинг для этого листа
            continue

        current_name    = None
        current_density = None

        for i in range(header_row + 1, len(df)):
            row = df.iloc[i]

            # Обновить текущее название
            name_val = row.iloc[name_col] if pd.notna(row.iloc[name_col]) else None
            if name_val and str(name_val).strip() not in ("nan", ""):
                current_name    = str(name_val).strip()
                current_density = _extract_density(current_name)

            if not current_name:
                continue

            try:
                width    = float(row.iloc[width_col])  if (width_col  < len(row) and pd.notna(row.iloc[width_col]))  else None
                roll_len = float(row.iloc[roll_col])   if (roll_col   < len(row) and pd.notna(row.iloc[roll_col]))   else None
                price    = float(row.iloc[price_col])  if (price_col  < len(row) and pd.notna(row.iloc[price_col]))  else None
            except (ValueError, TypeError):
                continue

            if not price or price <= 0:
                continue
            if width is None and roll_len is None:
                continue

            ppr = round(price * width * roll_len, 2) if (width and roll_len) else None

            # Нормализация имени
            product = re.sub(r'"[^"]+"\'|«[^»]+»', "", current_name).strip().lower()
            product = re.sub(r"\s+", " ", product).strip()

            results.append({
                "product":          product,
                "product_original": current_name,
                "category":         category,
                "density":          current_density,
                "width":            width,
                "roll_length":      roll_len,
                "price":            price,
                "unit":             "м²",
                "price_per_roll":   ppr,
                "price_date":       found_date or str(date.today()),
                "original":         current_name,
            })

    return results

PARSE_PROMPT = f"""Ты — парсер прайс-листов геоматериалов и технических тканей.
Извлеки ВСЕ товарные позиции. Ответь ТОЛЬКО валидным JSON-массивом без markdown:

[{{
  "product":       "геотекстиль иглопробивной 200 г/м²",
  "category":      "геотекстиль",
  "technology":    "иглопробивной",
  "density":       200,
  "width":         4.5,
  "roll_length":   150,
  "thickness":     null,
  "color":         "чёрный",
  "material":      "ПЭ",
  "tolerance":     "±15%",
  "application":   "дорожный",
  "price":         18.50,
  "unit":          "м²",
  "price_per_roll": 12487.5,
  "price_date":    "{TODAY}",
  "original":      "Геотекстиль 200 г/кв.м иглопробивной — 18.50 руб/м²"
}}]

═══ ПРАВИЛА ПОЛЕЙ ═══

product: нижний регистр, включает технологию и плотность
  пример: "геотекстиль иглопробивной 200 г/м²", "геомембрана пвд 1.0 мм гладкая"

category: геотекстиль | георешетка | геомембрана | дренаж | спанбонд | ватин | термовойлок | прочее

technology — технология производства, определяй из названия/описания:
  Геотекстиль: иглопробивной | термосклеенный | термоскреплённый | тканый | вязаный | каландрированный | экструзионный
  Геомембрана: гладкая | профилированная | текстурированная | армированная
  Георешетка: растянутая | сварная | экструзионная
  Спанбонд/ватин: термосклеенный | иглопробивной
  Если не указана → null

material — сырьё:
  ПП / PP — полипропилен (спанбонд, часть геотекстиля)
  ПЭ / PE — полиэтилен (геомембраны ПВД/ПНД, часть геотекстиля)
  ПЭТ / PET — полиэтилентерефталат (иглопробивной геотекстиль)
  HDPE — полиэтилен высокой плотности (мембраны HDPE)
  LDPE / ПВД — полиэтилен низкой плотности (мембраны ПВД)
  Смесь ПП+ПЭТ, Смесь ПП+ПЭ — если явно указано
  null — если неизвестно

color: чёрный | белый | серый | зелёный | синий | … или null

tolerance: погрешность/допуск по плотности если указан
  пример: "±15%", "до 15%", "±5%"
  null — если не указан

application: область применения если указана:
  дорожный | садовый | строительный | гидроизоляционный | дренажный | геотехнический | универсальный
  null — если не указана

density: г/м² числом или null
width: ширина рулона в метрах числом или null
roll_length: намотка в метрах числом или null
thickness: толщина мм (для мембран) числом или null
price: число без валюты (18.50)
unit: м² | рулон | кг | шт | м.п. — если непонятно → м²
price_per_roll: цена за рулон если указана явно, иначе null
price_date: YYYY-MM-DD из заголовка прайса, иначе {TODAY}
original: строка как в источнике

ПРОПУСКАЙ строки с ценой 0 или без цены."""

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
            "technology":       item.get("technology"),
            "density":          item.get("density"),
            "width":            item.get("width"),
            "roll_length":      item.get("roll_length"),
            "thickness":        item.get("thickness"),
            "color":            item.get("color"),
            "material":         item.get("material"),
            "tolerance":        item.get("tolerance"),
            "application":      item.get("application"),
            "price":            price,
            "unit":             item.get("unit", "м²"),
            "price_per_roll":   ppr,
            "supplier_id":      user["telegram_id"],
            "supplier_name":    user.get("supplier_name") or user["name"],
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
def fmt_result(items: list, query: str, norm: str) -> tuple:
    """Возвращает (текст, клавиатура)."""
    if not items:
        return (
            f"❌ По запросу *{query}* ничего не найдено.\n"
            f"_Искал: «{norm}»_\n\n"
            "Попробуйте другое название.",
            None
        )

    by_city: dict[str, dict] = {}
    for item in items:
        city = item["city"]
        if city not in by_city or item["price"] < by_city[city]["price"]:
            by_city[city] = item

    sorted_items = sorted(by_city.values(), key=lambda x: x["price"])
    medals = ["🥇", "🥈", "🥉"]
    txt_lines = [f"📦 *{norm}*\n"]

    for i, item in enumerate(sorted_items[:10]):
        prefix = medals[i] if i < 3 else "  •"
        dt  = (item.get("price_date") or "")[:10]
        sup = item.get("supplier_name") or "—"

        attrs = []
        if item.get("technology"):
            attrs.append(item["technology"])
        if item.get("density"):
            tol = f' {item["tolerance"]}' if item.get("tolerance") else ""
            attrs.append(f'{item["density"]} г/м²{tol}')
        if item.get("width") and item.get("roll_length"):
            attrs.append(f'рул. {item["width"]}м × {int(item["roll_length"])}м')
        elif item.get("width"):
            attrs.append(f'ш. {item["width"]} м')
        elif item.get("roll_length"):
            attrs.append(f'нам. {int(item["roll_length"])} м')
        if item.get("thickness"):
            attrs.append(f'{item["thickness"]} мм')
        if item.get("material"):
            attrs.append(item["material"])
        if item.get("color"):
            attrs.append(item["color"])

        d  = item.get("density")
        w  = item.get("width")
        rl = item.get("roll_length")
        weight_str = ""
        if d and w and rl:
            kg = round(float(d) * float(w) * float(rl) / 1000, 1)
            weight_str = f"  ⚖️ ~{kg} кг/рул\n"

        ppr = item.get("price_per_roll")
        roll_str = f"  💰 {int(ppr):,} р/рул\n".replace(",", " ") if ppr else ""
        attr_str = f"  _{'  ·  '.join(attrs)}_\n" if attrs else ""

        txt_lines.append(
            f"{prefix} *{item['city']}* — *{item['price']:.2f} р/{item['unit']}*\n"
            f"{attr_str}"
            f"{roll_str}"
            f"{weight_str}"
            f"  🏢 {sup} · 📅 {dt}"
        )

    if len(by_city) > 10:
        txt_lines.append(f"\n_...ещё {len(by_city)-10} городов_")

    # Inline кнопки
    city_btns = [
        InlineKeyboardButton(
            text=f"📈 {item['city']}",
            callback_data=f"hist:{norm[:28]}:{item['city'][:18]}"
        ) for item in sorted_items[:3]
    ]
    kb = InlineKeyboardMarkup(inline_keyboard=[
        city_btns,
        [
            InlineKeyboardButton(text="🔄 Обновить", callback_data=f"search:{norm[:38]}"),
            InlineKeyboardButton(text="🌍 Все города", callback_data=f"allcities:{norm[:36]}"),
        ],
    ]) if city_btns else None

    return "\n".join(txt_lines), kb

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
        role_label = {"snabzhenets":"📦 Снабженец","manager":"🔍 Менеджер","admin":"⚙ Администратор"}.get(user["role"], user["role"])
        tip = "Отправляйте прайсы — файл, фото или текст." if user["role"] == "snabzhenets" \
              else "Напишите название товара для поиска цены."
        await msg.answer(
            f"Вы в системе как *{role_label}* ({user['city']}).\n\n{tip}\n\n_/myinfo — профиль и смена роли_",
            parse_mode="Markdown",
            reply_markup=profile_kb(user["role"]),
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
    await state.update_data(city=city)

    if role == "snabzhenets":
        await msg.answer(
            f"Город: *{city}*\n\n"
            "Теперь укажите *название поставщика*\n"
            "_например: ООО Геотекс, ИП Иванов, Армпласт_",
            parse_mode="Markdown",
        )
        await state.set_state(Reg.supplier)
    else:
        supabase.table("users").insert({
            "telegram_id":   msg.from_user.id,
            "username":      msg.from_user.username or "",
            "name":          msg.from_user.full_name,
            "role":          role,
            "city":          city,
            "supplier_name": "",
        }).execute()
        await state.clear()
        await msg.answer(
            f"✅ Зарегистрированы как *Менеджер*, г. {city}\n\n"
            "Напишите название товара и я найду лучшую цену.\n"
            "_геотекстиль 200_  /  _мембрана 1мм_  /  _спанбонд 60_",
            parse_mode="Markdown",
        )


@router.message(Reg.supplier)
async def reg_supplier(msg: Message, state: FSMContext):
    data    = await state.get_data()
    city    = data["city"]
    sup     = msg.text.strip()

    supabase.table("users").insert({
        "telegram_id":   msg.from_user.id,
        "username":      msg.from_user.username or "",
        "name":          msg.from_user.full_name,
        "role":          "snabzhenets",
        "city":          city,
        "supplier_name": sup,
    }).execute()
    await state.clear()
    await msg.answer(
        f"✅ Зарегистрированы как *Снабженец*\n\n"
        f"📍 Город: {city}\n"
        f"🏢 Поставщик: *{sup}*\n\n"
        "Отправляйте прайсы — Excel, фото, текст.\n"
        "Нейронка распознает позиции, характеристики и цены.\n\n"
        "_Изменить город: /setcity_\n"
        "_Изменить поставщика: /setsupplier_",
        parse_mode="Markdown",
    )

# ── /myinfo, /setcity, /setsupplier commands ──────────────────────────────────

def profile_kb(role: str) -> InlineKeyboardMarkup:
    """Кнопки профиля с переключением роли."""
    other_role = "manager" if role == "snabzhenets" else "snabzhenets"
    other_label = "🔍 Стать менеджером" if other_role == "manager" else "📦 Стать снабженцем"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=other_label, callback_data=f"switchrole:{other_role}")],
        [
            InlineKeyboardButton(text="📍 Сменить город",      callback_data="edit:city"),
            InlineKeyboardButton(text="🏢 Сменить поставщика", callback_data="edit:supplier"),
        ],
    ])


@router.message(F.text == "/myinfo")
async def cmd_myinfo(msg: Message):
    user = await get_user(msg.from_user.id)
    if not user:
        await msg.answer("Начните с /start")
        return
    role_label = {"snabzhenets": "📦 Снабженец", "manager": "🔍 Менеджер", "admin": "⚙ Администратор"}.get(user["role"], user["role"])
    sup  = user.get("supplier_name") or "—"
    await msg.answer(
        f"👤 *Ваш профиль*\n\n"
        f"Роль: {role_label}\n"
        f"Город: {user['city']}\n"
        f"Поставщик: {sup}\n",
        parse_mode="Markdown",
        reply_markup=profile_kb(user["role"]),
    )


@router.callback_query(F.data.startswith("switchrole:"))
async def cb_switchrole(cb: CallbackQuery):
    new_role = cb.data.split(":")[1]
    if new_role not in ("snabzhenets", "manager"):
        await cb.answer("Недопустимая роль"); return

    user = await get_user(cb.from_user.id)
    if not user:
        await cb.answer("Сначала /start"); return

    # Если переключается в снабженца — проверить supplier_name
    if new_role == "snabzhenets" and not user.get("supplier_name"):
        await cb.answer("Сначала укажите поставщика: /setsupplier", show_alert=True)
        return

    supabase.table("users").update({"role": new_role}).eq("telegram_id", cb.from_user.id).execute()
    await cb.answer("Роль изменена!")

    role_label = "📦 Снабженец" if new_role == "snabzhenets" else "🔍 Менеджер"
    tip = "Отправляйте прайсы — файл, фото или текст." if new_role == "snabzhenets" else "Напишите название товара для поиска цены."
    await cb.message.edit_text(
        f"✅ Роль изменена на *{role_label}*\n\n{tip}",
        parse_mode="Markdown",
        reply_markup=profile_kb(new_role),
    )


@router.callback_query(F.data == "edit:city")
async def cb_edit_city(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    await cb.message.answer("Введите новый город:")
    await state.set_state(Edit.city)


@router.callback_query(F.data == "edit:supplier")
async def cb_edit_supplier(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    await cb.message.answer("Введите название поставщика:")
    await state.set_state(Edit.supplier)


@router.message(F.text.startswith("/setcity"))
async def cmd_setcity(msg: Message, state: FSMContext):
    user = await get_user(msg.from_user.id)
    if not user:
        await msg.answer("Начните с /start"); return
    inline = msg.text.replace("/setcity", "").strip()
    if inline:
        supabase.table("users").update({"city": inline}).eq("telegram_id", msg.from_user.id).execute()
        await msg.answer(f"✅ Город изменён на *{inline}*", parse_mode="Markdown")
    else:
        await msg.answer("Введите новый город:")
        await state.set_state(Edit.city)


@router.message(Edit.city)
async def edit_city(msg: Message, state: FSMContext):
    city = msg.text.strip()
    supabase.table("users").update({"city": city}).eq("telegram_id", msg.from_user.id).execute()
    await state.clear()
    await msg.answer(f"✅ Город изменён на *{city}*", parse_mode="Markdown")


@router.message(F.text.startswith("/setsupplier"))
async def cmd_setsupplier(msg: Message, state: FSMContext):
    user = await get_user(msg.from_user.id)
    if not user:
        await msg.answer("Начните с /start"); return
    inline = msg.text.replace("/setsupplier", "").strip()
    if inline:
        supabase.table("users").update({"supplier_name": inline}).eq("telegram_id", msg.from_user.id).execute()
        await msg.answer(f"✅ Поставщик изменён на *{inline}*", parse_mode="Markdown")
    else:
        await msg.answer("Введите название поставщика:")
        await state.set_state(Edit.supplier)


@router.message(Edit.supplier)
async def edit_supplier(msg: Message, state: FSMContext):
    sup = msg.text.strip()
    supabase.table("users").update({"supplier_name": sup}).eq("telegram_id", msg.from_user.id).execute()
    await state.clear()
    await msg.answer(f"✅ Поставщик изменён на *{sup}*", parse_mode="Markdown")


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
            raw_bytes = raw.read()

            # Шаг 1: Python читает Excel → список текстовых блоков по листам
            await msg.answer("📊 Читаю файл...")
            blocks, price_date = python_parse_excel(raw_bytes)

            if not blocks:
                await msg.answer("❌ Не удалось прочитать файл. Попробуйте Excel или CSV.")
                return

            await msg.answer(
                f"✅ Листов: {len(blocks)}\n"
                f"⏳ AI разбирает структуру и извлекает позиции...",
            )

            # Шаг 2: AI парсит каждый лист независимо
            all_items = []
            sheet_counts = []
            for sheet_name, text in blocks:
                sheet_items = await ai_parse_sheet(sheet_name, text, price_date or TODAY)
                all_items.extend(sheet_items)
                sheet_counts.append(f"{sheet_name}: {len(sheet_items)}")
                log.info(f"Sheet '{sheet_name}': {len(sheet_items)} items")
            log.info(f"Total: {len(all_items)} | " + " | ".join(sheet_counts))

            if not all_items:
                await msg.answer("❌ AI не смог извлечь позиции. Попробуйте ещё раз.")
                return

            # Шаг 3: сохраняем в базу
            count = await save_prices(all_items, user, fname)
            await msg.answer(ok_msg(count, user["city"], price_date), parse_mode="Markdown")
            return
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
        log.error(f"document error: {e}", exc_info=True)
        await msg.answer(f"❌ Ошибка: {str(e)[:200]}")

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
            text, kb = fmt_result(items, msg.text.strip(), norm)
            await msg.answer(text, parse_mode="Markdown", reply_markup=kb)
        except Exception as e:
            log.error(f"search error: {e}")
            await msg.answer("❌ Ошибка поиска.")

# ── Callback buttons ──────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("hist:"))
async def cb_history(cb: CallbackQuery):
    parts = cb.data.split(":")
    product = parts[1] if len(parts) > 1 else ""
    city    = parts[2] if len(parts) > 2 else None
    await cb.answer()
    items = await get_history(product, city if city else None)
    text  = fmt_history(items, product, city)
    await cb.message.answer(text, parse_mode="Markdown")


@router.callback_query(F.data.startswith("search:"))
async def cb_search(cb: CallbackQuery):
    query = cb.data.replace("search:", "")
    await cb.answer("Обновляю...")
    items, norm = await search_prices(query)
    text, kb = fmt_result(items, query, norm)
    await cb.message.answer(text, parse_mode="Markdown", reply_markup=kb)


@router.callback_query(F.data.startswith("allcities:"))
async def cb_allcities(cb: CallbackQuery):
    query = cb.data.replace("allcities:", "")
    await cb.answer()
    items, norm = await search_prices(query)
    if not items:
        await cb.message.answer(f"❌ Ничего не найдено по «{norm}»")
        return
    by_city: dict[str, dict] = {}
    for item in items:
        city = item["city"]
        if city not in by_city or item["price"] < by_city[city]["price"]:
            by_city[city] = item
    sorted_items = sorted(by_city.values(), key=lambda x: x["price"])
    lines = [f"📦 *{norm}* — все города\n"]
    for item in sorted_items:
        dt = (item.get("price_date") or "")[:10]
        lines.append(f"• *{item['city']}* — {item['price']:.2f} р/{item['unit']}  _{item['supplier_name']} · {dt}_")
    await cb.message.answer("\n".join(lines), parse_mode="Markdown")


# ── Admin commands ────────────────────────────────────────────────────────────

def is_admin(tg_id: int) -> bool:
    return tg_id in ADMIN_IDS


@router.message(F.text.startswith("/setrole"))
async def cmd_setrole(msg: Message):
    if not is_admin(msg.from_user.id):
        await msg.answer("⛔ Нет доступа.")
        return
    # /setrole @username role  или  /setrole 123456789 role
    parts = msg.text.split()
    if len(parts) < 3:
        await msg.answer("Формат: `/setrole <telegram_id> <snabzhenets|manager|admin>`", parse_mode="Markdown")
        return
    try:
        tg_id = int(parts[1])
    except ValueError:
        await msg.answer("Укажите числовой Telegram ID")
        return
    role = parts[2].lower()
    if role not in ("snabzhenets", "manager", "admin"):
        await msg.answer("Роль: snabzhenets | manager | admin")
        return
    supabase.table("users").update({"role": role}).eq("telegram_id", tg_id).execute()
    await msg.answer(f"✅ Роль пользователя `{tg_id}` изменена на *{role}*", parse_mode="Markdown")


@router.message(F.text == "/users")
async def cmd_users(msg: Message):
    if not is_admin(msg.from_user.id):
        await msg.answer("⛔ Нет доступа.")
        return
    r = supabase.table("users").select("*").order("created_at", desc=True).execute()
    if not r.data:
        await msg.answer("Пользователей нет.")
        return
    lines = ["👥 *Пользователи:*\n"]
    for u in r.data[:30]:
        role_emoji = "📦" if u["role"]=="snabzhenets" else "🔍" if u["role"]=="manager" else "⚙"
        sup = f" · {u['supplier_name']}" if u.get("supplier_name") else ""
        lines.append(f"{role_emoji} `{u['telegram_id']}` {u['name']}{sup} — {u['city']}")
    await msg.answer("\n".join(lines), parse_mode="Markdown")


# ── Run ────────────────────────────────────────────────────────────────────────
async def main():
    log.info("Starting bot via OpenRouter / %s", MODEL_TEXT)
    await dp.start_polling(bot, skip_updates=True)

if __name__ == "__main__":
    asyncio.run(main())
