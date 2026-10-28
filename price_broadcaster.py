"""
ارسال دوره‌ای قیمت به کانال‌ها (آبان ترید).

هر کانال: لیست ارزها / آیتم‌ها، بازه‌ی زمانی (ساعت)، قالب مخصوص خودش (اختیاری) و وضعیت فعال/غیرفعال دارد.
منابع قیمت:
  - ۱۴ ارز دیجیتال ثابت: از صرافی (live_price.get_tickers)؛ هر متغیر با فرمت  $<قیمت> (<درصد تغییر ۲۴ ساعته>)
  - طلا، سکه، انس و ارزهای رایج: از 24tala.ir (با کش ۱ تا ۵ دقیقه‌ای)
اگر مقداری موجود نباشد  ---  نمایش داده می‌شود.
آیتم‌هایی که برای کانال انتخاب نشده‌اند (به‌همراه خط/بخش مربوط) از پیام حذف می‌شوند.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set, Tuple

import broadcaster
import config
import force_subscribe
import live_price
import templates
from database import db
from signal_engine import engine
from ui import is_flooded, now_jalali_parts, to_en

log = logging.getLogger("price")

AlertHook = Callable[[str, str], Awaitable[None]]
alert_hook: Optional[AlertHook] = None

# ----------------------------------------------------------------------------
# آیتم‌ها: کلید -> (عنوان)
# ----------------------------------------------------------------------------
CRYPTO: List[Tuple[str, str, Tuple[str, ...]]] = [
    ("btc", "Bitcoin", ("BTC",)), ("bch", "Bitcoin Cash", ("BCH",)), ("eth", "Ethereum", ("ETH",)),
    ("xrp", "XRP", ("XRP",)), ("trx", "TRON", ("TRX",)), ("bnb", "BNB", ("BNB",)),
    ("sol", "Solana", ("SOL",)), ("doge", "Dogecoin", ("DOGE",)), ("ada", "Cardano", ("ADA",)),
    ("xlm", "Stellar", ("XLM",)), ("ltc", "Litecoin", ("LTC",)), ("zec", "Zcash", ("ZEC",)),
    ("xmr", "Monero", ("XMR",)),
    ("gram", "Gram (Toncoin)", ("GRAM", "TON")),     # Toncoin به Gram تغییر نام داده است؛ نماد فعلی صرافی استفاده می‌شود
]
# طلا / سکه: کلید گروه -> (عنوان، نام در API، متغیرهای قالب)
GOLD: List[Tuple[str, str, str, Tuple[str, ...]]] = [
    ("gold_18", "طلای ۱۸ عیار", "geram18", ("gold_18", "gold_18_high", "gold_18_low", "gold_18_prev", "gold_18_bubble")),
    ("abshode", "طلای آبشده (مثقال)", "abshode", ("abshode", "abshode_bubble")),
    ("seke_emami", "سکه امامی", "seke_emami", ("seke_emami", "seke_emami_bubble")),
    ("seke_bahar", "سکه بهار آزادی", "seke_bahar", ("seke_bahar", "seke_bahar_bubble")),
    ("seke_nim", "نیم سکه", "seke_nim", ("seke_nim", "seke_nim_bubble")),
    ("seke_rob", "ربع سکه", "seke_rob", ("seke_rob", "seke_rob_bubble")),
    ("seke_grami", "سکه گرمی", "seke_grami", ("seke_grami", "seke_grami_bubble")),
    ("ounce_gold", "انس جهانی طلا", "ounce", ("ounce_gold",)),
    ("ounce_silver", "انس نقره", "ounce_silver", ("ounce_silver",)),
]
# ارزهای رایج: کلید -> (عنوان، نام‌های ممکن در API)
FX: List[Tuple[str, str, Tuple[str, ...]]] = [
    ("usd", "دلار آمریکا", ("usd", "dollar", "us_dollar")), ("eur", "یورو", ("eur", "euro")),
    ("gbp", "پوند انگلیس", ("gbp", "pound")), ("aed", "درهم امارات", ("aed", "dirham")),
    ("try", "لیر ترکیه", ("try", "lira", "tl")), ("cny", "یوآن چین", ("cny", "yuan")),
    ("cad", "دلار کانادا", ("cad",)), ("aud", "دلار استرالیا", ("aud",)),
    ("chf", "فرانک سوئیس", ("chf", "franc")), ("jpy", "ین ژاپن", ("jpy", "yen")),
    ("sar", "ریال عربستان", ("sar",)), ("qar", "ریال قطر", ("qar",)),
    ("kwd", "دینار کویت", ("kwd",)), ("omr", "ریال عمان", ("omr",)),
    ("iqd", "دینار عراق", ("iqd",)), ("rub", "روبل روسیه", ("rub", "ruble")),
    ("azn", "منات آذربایجان", ("azn", "manat")), ("gel", "لاری گرجستان", ("gel", "lari")),
    ("afn", "افغانی", ("afn", "afghani")), ("pkr", "روپیه پاکستان", ("pkr", "rupee")),
]

GROUPS: Dict[str, Tuple[str, str]] = {   # کلید -> (دسته، عنوان)
    **{k: ("c", t) for k, t, _b in CRYPTO},
    **{k: ("g", t) for k, t, _a, _v in GOLD},
    **{k: ("f", t) for k, t, _a in FX},
}
GROUP_TITLES = {"c": "ارزهای دیجیتال", "g": "طلا و سکه", "f": "ارزهای رایج"}
ALL_KEYS: List[str] = list(GROUPS)
GROUP_VARS: Dict[str, Tuple[str, ...]] = {
    **{k: (k,) for k, _t, _b in CRYPTO},
    **{k: v for k, _t, _a, v in GOLD},
    **{k: (k,) for k, _t, _a in FX},
}
PRICE_PLACEHOLDERS: Set[str] = {"date", "time"} | {v for vs in GROUP_VARS.values() for v in vs}


def keys_of(cat: str) -> List[str]:
    return [k for k, (c, _t) in GROUPS.items() if c == cat]


def excluded_vars(selected: Set[str]) -> Set[str]:
    return {v for k, vs in GROUP_VARS.items() if k not in selected for v in vs}


# ----------------------------------------------------------------------------
# فرمت قیمت
# ----------------------------------------------------------------------------
def fmt_usd(p: float) -> str:
    if p >= 1000:
        s = f"{p:,.0f}"
    elif p >= 10:
        s = f"{p:,.2f}"
    elif p >= 1:
        s = f"{p:,.3f}"
    elif p >= 0.1:
        s = f"{p:.4f}"
    else:
        s = f"{p:.6f}".rstrip("0").rstrip(".") or "0"
    return "$" + s


def fmt_crypto(price: float, pct: Optional[float]) -> str:
    return fmt_usd(price) + (f" ({pct:+.2f}%)" if pct is not None else "")


def fmt_toman(x: float) -> str:
    return f"{x:,.2f}" if abs(x) < 100 else f"{x:,.0f}"


def fmt_bubble(x: float) -> str:
    return f"{x:.2f}%" if abs(x) < 100 else f"{x:,.0f}"


# ----------------------------------------------------------------------------
# 24tala.ir (کش + تجزیه‌ی تحمل‌پذیر نسبت به ساختار JSON)
# ----------------------------------------------------------------------------
_PRICE_F = ("price", "p", "current", "last", "value", "now", "rate", "sell", "last_price")
_HIGH_F = ("high", "h", "max", "highest", "day_high", "high_price")
_LOW_F = ("low", "l", "min", "lowest", "day_low", "low_price")
_PREV_F = ("prev", "previous", "yesterday", "prev_price", "previous_price", "close_prev", "last_close", "yesterday_price", "y")
_BUBBLE_F = ("bubble", "bubble_pct", "haab", "hobab", "bubble_price", "bubble_value", "gap")
_ID_FIELDS = ("key", "symbol", "name", "slug", "id", "code", "type")


def to_number(v: Any) -> Optional[float]:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        s = to_en(v).replace(",", "").replace("٬", "").replace("٫", ".").strip()
        s = re.sub(r"[^\d.\-+eE]", "", s)
        try:
            return float(s)
        except ValueError:
            return None
    if isinstance(v, dict):
        for f in _PRICE_F:
            for k, val in v.items():
                if str(k).lower() == f:
                    r = to_number(val)
                    if r is not None:
                        return r
    return None


# ----------------------------------------------------------------------------
# توابع کمکی برای ساختار جدید API (data.items[])
# ----------------------------------------------------------------------------
def _get_items(data: Any) -> List[Dict]:
    """استخراج data.items[] از پاسخ API جدید."""
    if not isinstance(data, dict):
        return []
    inner = data.get("data") or data
    if isinstance(inner, dict):
        items = inner.get("items")
        if isinstance(items, list):
            return [i for i in items if isinstance(i, dict)]
    return []


def _find_by_code(items: List[Dict], code: str) -> Optional[Dict]:
    """پیدا کردن آیتم با فیلد code (مقایسه‌ی case-insensitive)."""
    code_l = str(code).lower()
    for item in items:
        if str(item.get("code", "")).lower() == code_l:
            return item
    return None


def _item_price(item: Dict) -> Optional[float]:
    """قیمت اصلی آیتم: اول price، بعد sell، بعد current."""
    for f in ("price", "sell", "current", "last", "value"):
        v = to_number(item.get(f))
        if v is not None:
            return v
    return None


def _item_field(item: Dict, fields: Tuple[str, ...]) -> Optional[float]:
    """اولین فیلد موجود با مقدار عددی از لیست fields."""
    lower = {str(k).lower(): v for k, v in item.items()}
    for f in fields:
        if f in lower:
            v = to_number(lower[f])
            if v is not None:
                return v
    return None


# ----------------------------------------------------------------------------
# سازگاری با ساختار قدیمی (fallback)
# ----------------------------------------------------------------------------
def find_item(data: Any, names: Set[str]) -> Any:
    """کم‌عمق‌ترین مقدار با کلید برابر یکی از names (یا آیتمی که فیلد شناسه‌اش برابر باشد) در JSON تو‌در‌تو."""
    queue: List[Any] = [data]
    while queue:
        nxt: List[Any] = []
        for node in queue:
            if isinstance(node, dict):
                for k, v in node.items():
                    if str(k).lower() in names:
                        return v
                if any(str(node.get(f, "")).lower() in names for f in _ID_FIELDS):
                    return node
                nxt.extend(v for v in node.values() if isinstance(v, (dict, list)))
            elif isinstance(node, list):
                nxt.extend(node)
        queue = nxt
    return None


def pick_field(item: Any, fields: Tuple[str, ...]) -> Optional[float]:
    if not isinstance(item, dict):
        return None
    lower = {str(k).lower(): v for k, v in item.items()}
    for f in fields:
        if f in lower:
            r = to_number(lower[f])
            if r is not None:
                return r
    return None


class _Cache:
    def __init__(self) -> None:
        self.data: Dict[str, Tuple[float, Any]] = {}
        self.lock: Optional[asyncio.Lock] = None

    def get_lock(self) -> asyncio.Lock:
        if self.lock is None:
            self.lock = asyncio.Lock()
        return self.lock


_cache = _Cache()


async def _fetch_json(url: str) -> Any:
    import aiohttp

    headers = {"Accept": "application/json", "User-Agent": "AbanTradeBot/3.0"}
    token = (config.TALA_API_TOKEN or "").strip()
    if token and "24tala.ir" in url:
        headers["X-Api-Token"] = token
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=12)) as s:
        async with s.get(url, headers=headers) as r:
            r.raise_for_status()
            return await r.json(content_type=None)


async def tala_data(kind: str) -> Any:
    """kind: gold | fx. کش ۱ تا ۵ دقیقه‌ای؛ اگر دریافت نشد آخرین داده‌ی سالم (تا ۳۰ دقیقه) برمی‌گردد."""
    url = config.TALA_API_URL if kind == "gold" else config.TALA_FX_URL
    now = time.time()
    hit = _cache.data.get(kind)
    if hit and now - hit[0] < config.TALA_CACHE_SECONDS:
        return hit[1]
    async with _cache.get_lock():
        hit = _cache.data.get(kind)
        if hit and time.time() - hit[0] < config.TALA_CACHE_SECONDS:
            return hit[1]
        try:
            data = await _fetch_json(url)
            _cache.data[kind] = (time.time(), data)
            return data
        except Exception as e:
            log.warning("24tala %s fetch failed: %s", kind, e)
            if hit and time.time() - hit[0] < 1800:
                return hit[1]
            return None


def _toman(v: Optional[float]) -> Optional[float]:
    return None if v is None else (v / 10.0 if config.TALA_PRICES_IN_RIAL else v)


def parse_gold(data: Any, selected: Set[str]) -> Dict[str, str]:
    """پارس قیمت طلا، سکه، انس از پاسخ API جدید (data.items[]) با fallback به ساختار قدیمی."""
    out: Dict[str, str] = {}
    items = _get_items(data)
    for key, _title, api, vars_ in GOLD:
        if key not in selected:
            continue
        item = _find_by_code(items, api) if items else (find_item(data, {api.lower()}) if data is not None else None)
        if item is None:
            continue
        usd = key.startswith("ounce")
        conv = (lambda x: x) if usd else _toman
        price = _item_price(item) if isinstance(item, dict) else to_number(item)
        price = conv(price)
        if price is not None:
            out[vars_[0]] = f"{price:,.2f}" if usd else fmt_toman(price)
        if key == "gold_18":
            for var, fields in (("gold_18_high", _HIGH_F), ("gold_18_low", _LOW_F), ("gold_18_prev", _PREV_F)):
                v = conv(_item_field(item, fields) if isinstance(item, dict) else pick_field(item, fields))
                if v is not None:
                    out[var] = fmt_toman(v)
        if not usd and len(vars_) > 1:
            bub = _item_field(item, _BUBBLE_F) if isinstance(item, dict) else pick_field(item, _BUBBLE_F)
            if bub is not None:
                out[vars_[-1]] = fmt_bubble(bub)
    return out


def parse_fx(data: Any, selected: Set[str]) -> Dict[str, str]:
    """پارس قیمت ارزهای رایج از پاسخ API جدید (data.items[]) با fallback به ساختار قدیمی."""
    out: Dict[str, str] = {}
    items = _get_items(data)
    for key, _title, names in FX:
        if key not in selected or data is None:
            continue
        item = _find_by_code(items, key) if items else find_item(data, set(names))
        if item is None:
            continue
        price = _item_price(item) if isinstance(item, dict) else to_number(item)
        price = _toman(price)
        if price is not None:
            out[key] = fmt_toman(price)
    return out


# ----------------------------------------------------------------------------
# ارزهای دیجیتال (Toobit از طریق live_price)
# ----------------------------------------------------------------------------
def crypto_pair(bases: Tuple[str, ...]) -> Optional[str]:
    for b in bases:
        for pair in engine.symbols:
            if (engine.bases.get(pair) or pair.split("/")[0]).upper() == b:
                return pair
    return None


async def crypto_values(selected: Set[str]) -> Dict[str, str]:
    if not engine.symbols:
        try:
            await engine.get_symbols()
        except Exception:
            pass
    pairs: Dict[str, str] = {}
    for key, _t, bases in CRYPTO:
        if key in selected:
            pair = crypto_pair(bases)
            if pair:
                pairs[key] = pair
    if not pairs:
        return {}
    ticks = await live_price.get_tickers(list(pairs.values()))
    return {k: fmt_crypto(*ticks[p]) for k, p in pairs.items() if p in ticks}


# ----------------------------------------------------------------------------
# ساخت پیام
# ----------------------------------------------------------------------------
def render_template(template_text: str, context: Dict[str, Any], excluded=()) -> str:
    """جایگزینی متغیرهای پویا؛ متغیری که مقدارش موجود نباشد با --- پر می‌شود."""
    return templates.render_template(template_text, context, excluded=excluded)


async def build_message(template_text: str, selected: Set[str]) -> str:
    selected = {k for k in selected if k in GROUPS}
    ctx: Dict[str, Any] = {}
    ctx["date"], ctx["time"] = now_jalali_parts()
    need_gold = any(GROUPS[k][0] == "g" for k in selected)
    need_fx = any(GROUPS[k][0] == "f" for k in selected)
    gold_data, fx_data, crypto = await asyncio.gather(
        tala_data("gold") if need_gold else asyncio.sleep(0),
        tala_data("fx") if need_fx else asyncio.sleep(0),
        crypto_values(selected),
        return_exceptions=True,
    )
    for res in (crypto,):
        if isinstance(res, dict):
            ctx.update(res)
        elif isinstance(res, BaseException):
            log.warning("crypto prices failed: %s", res)
    if need_gold and not isinstance(gold_data, BaseException):
        ctx.update(parse_gold(gold_data, selected))
    if need_fx and not isinstance(fx_data, BaseException):
        ctx.update(parse_fx(fx_data, selected))
    return render_template(template_text, ctx, excluded=excluded_vars(selected))


# ----------------------------------------------------------------------------
# دیتابیس کانال‌های قیمت
# ----------------------------------------------------------------------------
def _row(r: Dict[str, Any]) -> Dict[str, Any]:
    try:
        syms = json.loads(r.get("symbols") or "[]")
    except ValueError:
        syms = []
    r["selected"] = {k for k in syms if k in GROUPS}
    r["interval_hours"] = float(r.get("interval_hours") or 1.0)
    r["last_sent_at"] = float(r.get("last_sent_at") or 0.0)
    r["enabled"] = bool(r.get("enabled"))
    r["template"] = r.get("template") or ""
    return r


def list_channels() -> List[Dict[str, Any]]:
    return [_row(r) for r in db.q("SELECT * FROM price_channels ORDER BY id")]


def get_channel(cid: int) -> Optional[Dict[str, Any]]:
    r = db.q1("SELECT * FROM price_channels WHERE id = ?", (cid,))
    return _row(r) if r else None


def add_channel(channel_id: str, title: str) -> int:
    return db.insert_id(
        "INSERT INTO price_channels(channel_id, channel_title, symbols, interval_hours, template, enabled, last_sent_at) "
        "VALUES(?, ?, ?, 1, '', 1, 0)",
        (str(channel_id), title, json.dumps(ALL_KEYS)),
    )


def update_channel(cid: int, **fields) -> None:
    allowed = {"channel_id", "channel_title", "symbols", "interval_hours", "template", "enabled", "last_sent_at"}
    sets, vals = [], []
    for k, v in fields.items():
        if k in allowed:
            sets.append(f"{k} = ?")
            vals.append(json.dumps(sorted(v, key=ALL_KEYS.index)) if k == "symbols" else v)
    if sets:
        vals.append(cid)
        db.x(f"UPDATE price_channels SET {', '.join(sets)} WHERE id = ?", vals)


def delete_channel(cid: int) -> bool:
    return db.x("DELETE FROM price_channels WHERE id = ?", (cid,)).rowcount > 0


def channel_exists(channel_id: str) -> bool:
    return db.q1("SELECT id FROM price_channels WHERE channel_id = ?", (str(channel_id),)) is not None


def template_for(row: Dict[str, Any]) -> str:
    return row["template"].strip() or db.get_msg_template("price")


# ----------------------------------------------------------------------------
# ارسال
# ----------------------------------------------------------------------------
async def _alert(key: str, text: str) -> None:
    hook = alert_hook
    if hook is None:
        return
    try:
        await hook(key, text)
    except Exception:  # pragma: no cover
        log.debug("price alert hook failed", exc_info=True)


async def send_channel(bot, row: Dict[str, Any]) -> bool:
    """پیام قیمت یک کانال را می‌سازد و می‌فرستد. خروجی: موفق یا نه."""
    text = await build_message(template_for(row), row["selected"])
    mid = await broadcaster.send_html(bot, force_subscribe.chat_ref(row["channel_id"]), text)
    now = time.time()
    if mid is None:
        # ۵ دقیقه‌ی دیگر دوباره تلاش می‌شود
        update_channel(row["id"], last_sent_at=now - row["interval_hours"] * 3600 + 300)
        await _alert(
            f"price_fail_{row['id']}",
            f"ارسال قیمت به کانال «{row['channel_title'] or row['channel_id']}» انجام نشد.\n"
            "مطمئن شوید ربات در آن کانال ادمین است و دسترسی ارسال پیام دارد.",
        )
        return False
    update_channel(row["id"], last_sent_at=now)
    return True


async def tick(bot) -> int:
    if is_flooded():
        return 0
    now = time.time()
    sent = 0
    for row in list_channels():
        if not row["enabled"] or not row["selected"]:
            continue
        if now - row["last_sent_at"] >= max(0.1, row["interval_hours"]) * 3600:
            if await send_channel(bot, row):
                sent += 1
            await asyncio.sleep(1.0)
    return sent


async def broadcast_loop(app) -> None:
    """حلقه‌ی پس‌زمینه: کانال‌ها، ارزها و بازه‌ی زمانی از دیتابیس خوانده می‌شوند و قیمت‌ها به‌موقع ارسال می‌شوند."""
    await asyncio.sleep(40)
    while True:
        try:
            await tick(app.bot)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("price broadcast loop error")
        await asyncio.sleep(30)