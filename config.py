"""تنظیمات پروژه آبان ترید / Aban Trade (خواندن از .env + پیش‌فرض‌ها)."""
from __future__ import annotations

import json
import os
import re
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

# نام برند (در متن‌های ربات استفاده می‌شود)
BRAND = "آبان ترید"
BRAND_EN = "Aban Trade"


def _int(name: str, default: int) -> int:
    try:
        return int(str(os.getenv(name, default)).strip())
    except (TypeError, ValueError):
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(str(os.getenv(name, default)).strip())
    except (TypeError, ValueError):
        return default


def _bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _persisted_secret(filename: str, length: int) -> str:
    """Secret پایدار برای وقتی که متغیر محیطی تنظیم نشده است (تا ری‌استارت نشست‌ها را باطل نکند)."""
    path = BASE_DIR / "data" / filename
    try:
        if path.exists():
            value = path.read_text(encoding="utf-8").strip()
            if value:
                return value
        path.parent.mkdir(parents=True, exist_ok=True)
        value = secrets.token_urlsafe(length)
        path.write_text(value, encoding="utf-8")
        try:
            path.chmod(0o600)
        except OSError:
            pass
        return value
    except OSError:
        return secrets.token_urlsafe(length)


def _env_secret(name: str, filename: str, length: int) -> str:
    return os.getenv(name, "").strip() or _persisted_secret(filename, length)


# ----------------------------------------------------------------------------
# .env
# ----------------------------------------------------------------------------
BOT_TOKEN: str = os.getenv("BOT_TOKEN", "").strip()
BOT_USERNAME: str = os.getenv("BOT_USERNAME", "abantrade_bot").strip().lstrip("@")
ADMIN_UID: int = _int("ADMIN_UID", 6210410341)

WEBHOOK_URL: str = os.getenv("WEBHOOK_URL", "").strip().rstrip("/")
WEBHOOK_PATH: str = os.getenv("WEBHOOK_PATH", "aniru-futures-hook").strip().strip("/")
WEBHOOK_SECRET: str = _env_secret("WEBHOOK_SECRET", "webhook_secret", 32)
WEBHOOK_LISTEN: str = os.getenv("WEBHOOK_LISTEN", "127.0.0.1").strip()
WEBHOOK_PORT: int = _int("WEBHOOK_PORT", 8080)
SSL_CERT: str = os.getenv("SSL_CERT", "").strip()
SSL_KEY: str = os.getenv("SSL_KEY", "").strip()

# ---- وب‌اپ تحلیل ساختاری (FastAPI + همان سرویس) ----
WEBAPP_ENABLED: bool = _bool("WEBAPP_ENABLED", True)
WEBAPP_URL: str = (
    os.getenv("WEBAPP_URL", "").strip().rstrip("/")
    or os.getenv("RENDER_EXTERNAL_URL", "").strip().rstrip("/")
    or WEBHOOK_URL
    or "https://abantrade.ir"
)
WEBAPP_TITLE: str = os.getenv("WEBAPP_TITLE", "آبان ترید | تحلیل ساختاری").strip()
WEBAPP_SECRET: str = _env_secret("WEBAPP_SECRET", "webapp_secret", 48)
WEBAPP_SESSION_DAYS: int = max(1, _int("WEBAPP_SESSION_DAYS", 30))
WEBAPP_ADMIN_LINK_MINUTES: int = max(1, _int("WEBAPP_ADMIN_LINK_MINUTES", 20))
WEBAPP_COOKIE_SECURE: bool = _bool("WEBAPP_COOKIE_SECURE", WEBAPP_URL.lower().startswith("https://"))
# فقط وقتی اتصال از این آدرس‌ها (پروکسی معکوس/شبکه داخلی Render) باشد، هدر X-Forwarded-For
# برای تشخیص IP کاربر باور می‌شود؛ درخواست مستقیم از اینترنت نمی‌تواند IP جعل کند.
WEBAPP_TRUSTED_PROXIES: str = os.getenv(
    "WEBAPP_TRUSTED_PROXIES",
    "127.0.0.0/8,::1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,100.64.0.0/10",
).strip()
WEBAPP_SCAN_CONCURRENCY: int = max(1, min(_int("WEBAPP_SCAN_CONCURRENCY", 10), 40))
WEBAPP_CANDLE_LIMIT: int = max(120, min(_int("WEBAPP_CANDLE_LIMIT", 320), 1000))
WEBAPP_SCAN_HISTORY_LIMIT: int = max(50, _int("WEBAPP_SCAN_HISTORY_LIMIT", 500))
WEBAPP_FEAR_GREED_URL: str = os.getenv(
    "WEBAPP_FEAR_GREED_URL", "https://api.alternative.me/fng/?limit=1&format=json"
).strip()
WEBAPP_UPLOAD_DIR: str = os.getenv("WEBAPP_UPLOAD_DIR", "data/webapp_uploads").strip()
# آدرسی که در پیام‌های ربات به کاربر نشان داده می‌شود (بدون https)
WEBAPP_PUBLIC_HOST: str = os.getenv("WEBAPP_PUBLIC_HOST", "").strip() or re.sub(
    r"^https?://", "", WEBAPP_URL
).split("/")[0]
WEBAPP_PORT: int = max(1, _int("PORT", _int("WEBAPP_PORT", 8000)))
WEBAPP_HOST: str = os.getenv("WEBAPP_HOST", "0.0.0.0").strip()

# آدرس دیتابیس: sqlite:///data/bot.db (پیش‌فرض) یا postgresql://user:pass@host:5432/dbname (Supabase / Neon / ...)
# با عوض کردن همین متغیر محیطی، بدون هیچ تغییری در کد، دیتابیس از SQLite به PostgreSQL عوض می‌شود.
DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///data/bot.db").strip() or "sqlite:///data/bot.db"
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").strip().upper()
USE_UVLOOP: bool = _bool("USE_UVLOOP", True)

# صرافی و اسکن
EXCHANGE_ID: str = os.getenv("EXCHANGE_ID", "toobit").strip().lower()
# حداقل ۳۰۰ تا کندل لازم است (EMA کند + کندل‌های گرم‌کردن)
CANDLE_LIMIT: int = max(300, min(_int("CANDLE_LIMIT", 300), 1000))
SCAN_CONCURRENCY: int = max(1, min(_int("SCAN_CONCURRENCY", 8), 40))
WARM_INTERVAL: int = max(10, _int("WARM_INTERVAL", 20))
SCAN_RETRY_SECONDS: int = max(15, _int("SCAN_RETRY_SECONDS", 60))
MIN_24H_VOLUME_USDT: float = _float("MIN_24H_VOLUME_USDT", 0.0)
MAX_ENTRY_DRIFT_ATR: float = _float("MAX_ENTRY_DRIFT_ATR", 0.8)
USER_SCAN_COOLDOWN: int = max(0, _int("USER_SCAN_COOLDOWN", 8))
USER_FLOOD_GAP: float = max(0.0, _float("USER_FLOOD_GAP", 0.6))
MEMBER_CACHE_TTL: int = max(0, _int("MEMBER_CACHE_TTL", 180))
BROADCAST_PER_SECOND: int = max(1, min(_int("BROADCAST_PER_SECOND", 25), 30))

LIVE_EDITS_PER_SECOND: int = max(1, min(_int("LIVE_EDITS_PER_SECOND", 20), 30))
EXCHANGE_INFO_URL: str = os.getenv("EXCHANGE_INFO_URL", "https://api.toobit.com/api/v1/exchangeInfo").strip()

TALA_API_URL: str = os.getenv("TALA_API_URL", "https://24tala.ir/api_v1.php?r=metals").strip()
TALA_FX_URL: str = os.getenv("TALA_FX_URL", "https://24tala.ir/api_v1.php?r=fx").strip()
TALA_API_TOKEN: str = os.getenv("TALA_API_TOKEN", "").strip()
TALA_PRICES_IN_RIAL: bool = _bool("TALA_PRICES_IN_RIAL", False)
TALA_CACHE_SECONDS: int = max(60, min(_int("TALA_CACHE_SECONDS", 120), 300))

# آنالیز با AI: فاصله‌ی حداقلی بین دو درخواست هر کاربر (ثانیه)
ANALYSIS_COOLDOWN: int = max(0, _int("ANALYSIS_COOLDOWN", 20))

# هوش مصنوعی: آدرس سرویس AI فقط برای کلیدهایی که Account ID ندارند (Fallback؛ از داخل ربات هم قابل تغییر است).
# آدرس اصلی هر کلید از Account ID خودش ساخته می‌شود.
# اگر آدرس Fallback شامل {model} باشد جای آن اسم مدل قرار می‌گیرد، وگرنه مدل داخل بدنه‌ی درخواست فرستاده می‌شود.
AI_WORKER_URL: str = os.getenv("AI_WORKER_URL", "").strip()
AI_TIMEOUT: int = max(5, min(_int("AI_TIMEOUT", 20), 60))
# مدل‌های پیشنهادی (ادمین می‌تواند اسم هر مدل دیگه‌ای را هم دستی وارد کند)
AI_MODELS: List[str] = [
    "@cf/meta/llama-3.1-8b-instruct-fast",
    "@cf/meta/llama-3.3-70b-instruct-fp8-fast",
    "@cf/google/gemma-4-26b-a4b-it",
    "@cf/zai-org/glm-4.7-flash",
]
AI_MAX_KEYS: int = 20
AI_MAX_MODELS: int = 12
# آدرس API کلودفلر؛ Account ID هر کلید در همین آدرس قرار می‌گیرد (AI_WORKER_URL فقط Fallback هست)
CF_AI_ENDPOINT: str = "https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/{model}"

MAX_CHANNELS: int = 5
# بعد از هشدار آماده‌باش، نماد تا این مدت (دقیقه) زیر نظر می‌ماند و اگر سیگنال کامل شد در کانال منتشر می‌شود
PRE_WATCH_MINUTES: int = max(5, _int("PRE_WATCH_MINUTES", 120))
# اسکن سیگنال کاربران: حداکثر اسکن هم‌زمان (بقیه در صف می‌مانند) و فاصله‌ی حداقلی درخواست مشاور موتورها
USER_SCAN_CONCURRENCY: int = max(1, min(_int("USER_SCAN_CONCURRENCY", 3), 10))
USER_ADVISOR_COOLDOWN: int = max(0, _int("USER_ADVISOR_COOLDOWN", 30))

# ---- صف پیام‌ها (broadcaster.py) ----
# حداکثر زمانی که یک درخواست ارسال در صف منتظر می‌ماند (ثانیه)؛ بعد از آن لغو می‌شود (پیام کهنه ارسال نمی‌شود)
QUEUE_MAX_WAIT: int = max(10, _int("QUEUE_MAX_WAIT", 180))
# سقف طول صف؛ وقتی پر شد پیام‌های کم‌اولویت (ارسال گروهی) رد می‌شوند
QUEUE_MAX_SIZE: int = max(1000, _int("QUEUE_MAX_SIZE", 20000))

# ---- وضعیت بازار در منوی اصلی (market_overview.py) ----
MARKET_INTERVAL: int = max(60, _int("MARKET_INTERVAL", 900))              # هر ۱۵ دقیقه
MARKET_STALE_SECONDS: int = max(MARKET_INTERVAL * 2, _int("MARKET_STALE_SECONDS", 3 * 3600))
MARKET_MIN_SYMBOLS: int = 3                                               # حداقل نماد تحلیل‌شده برای ثبت نتیجه

# ---- پاسخ خودکار هوشمند پشتیبانی (support_ai.py) ----
AI_SUPPORT_DAILY_PER_USER: int = max(1, _int("AI_SUPPORT_DAILY_PER_USER", 40))
AI_SUPPORT_CONCURRENCY: int = max(1, min(_int("AI_SUPPORT_CONCURRENCY", 3), 10))
USERS_PER_PAGE: int = 8
SF_PAGE_SIZE: int = 24          # تعداد نماد در هر صفحه‌ی فیلتر نمادها

# دسته‌ی مجازی برای نمادهایی که categories ندارند
NONE_CAT = "__none__"
NONE_CAT_LABEL = "بدون دسته‌بندی"

# دسته‌های بازار سنتی (سهام / TradFi / فارکس / ...) به‌طور پیش‌فرض غیرفعال هستند
_TRADITIONAL_RE = re.compile(
    r"\b(tradfi|stocks?|forex|fx|equit\w*|etfs?|commodit\w*|precious\s*metals?|metals?)\b", re.I
)


def is_traditional_category(name: str) -> bool:
    return bool(_TRADITIONAL_RE.search(name or ""))


# ----------------------------------------------------------------------------
# مقادیر ثابت
# ----------------------------------------------------------------------------
ENTRY_TF_OPTIONS: List[str] = ["5m", "15m", "1h", "4h"]
FINAL_TF_OPTIONS: List[str] = ["15m", "1h", "4h", "1d"]

BLOCK_TYPES: List[str] = ["block1", "block2", "block3"]

ALLOWED_PLACEHOLDERS = {
    "symbol", "entry", "lastprice", "side",
    "sl", "tp1", "tp2", "tp3",
    "risk", "leverage",
    "rr",
    "block1", "block2", "block3",
    "botid",
    "aiinfo",
}
REQUIRED_PLACEHOLDERS = ("tp1", "tp2", "tp3")
# قالب کانال: دقیقاً مثل قالب LONG/SHORT اما بدون {lastprice} (قیمت داخل کانال زنده نمی‌شود)
CHANNEL_PLACEHOLDERS = ALLOWED_PLACEHOLDERS - {"lastprice"}
# قالب خرید اشتراک
SUB_PLACEHOLDERS = {"wallet", "amount"}
SUB_REQUIRED_PLACEHOLDERS = ("wallet", "amount")

DEFAULT_SUB_TEMPLATE = (
    "خرید اشتراک\n\n"
    "مبلغ اشتراک: {amount} دلار\n"
    "آدرس کیف پول:\n"
    "`{wallet}`\n\n"
    "بعد از واریز، دکمه «ارسال تراکنش» را بزنید و اطلاعات تراکنش (متن و تصویر رسید) را برای ما ارسال کنید."
)

# قالب پیش‌فرض سیگنال کانال (CH_LONG / CH_SHORT) و ربات (LONG / SHORT)؛ از پنل ادمین قابل ویرایش است.
DEFAULT_CHANNEL_TEMPLATE = (
    "`{symbol}` - {side} - {leverage}X\n"
    "Entry {entry}\n"
    "\n"
    "TP¹ `{tp1}`\n"
    "TP² `{tp2}`\n"
    "TP³ `{tp3}`\n"
    "\n"
    "❌STOP `{sl}`\n"
    "\n"
    "Risk {risk}\n"
    "R:R  {rr}\n"
    "\n"
    "{aiinfo}\n"
    "\n"
    "{block1}\n"
    "{block2}\n"
    "{block3}\n"
    "\n"
    "{botid}"
)
DEFAULT_BOT_TEMPLATE = (
    "`{symbol}` - {side} - {leverage}X\n"
    "Last Price(2s) {lastprice}\n"
    "Entry {entry}\n"
    "\n"
    "TP¹ `{tp1}`\n"
    "TP² `{tp2}`\n"
    "TP³ `{tp3}`\n"
    "\n"
    "❌STOP `{sl}`\n"
    "\n"
    "Risk {risk}\n"
    "R:R  {rr}\n"
    "\n"
    "{aiinfo}\n"
    "\n"
    "{block1}\n"
    "{block2}\n"
    "{block3}\n"
    "\n"
    "{botid}"
)
DEFAULT_SIGNAL_TEMPLATES: Dict[str, str] = {
    "LONG": DEFAULT_BOT_TEMPLATE,
    "SHORT": DEFAULT_BOT_TEMPLATE,
    "CH_LONG": DEFAULT_CHANNEL_TEMPLATE,
    "CH_SHORT": DEFAULT_CHANNEL_TEMPLATE,
}

# قالب پیام قیمت (کانال‌های قیمت) و قالب آنالیز AI
DEFAULT_PRICE_TEMPLATE = """📅 {date} - {time}
━━━━━━━━━━━━━━━━━━━━

🪙 طلا و سکه
━━━━━━━━━━━━━━━━━━━━
🥇 طلای ۱۸ عیار
   {gold_18} تومان
   بالاترین: {gold_18_high}
   پایین‌ترین: {gold_18_low}
   روز قبل: {gold_18_prev}

🟡 طلای آبشده (مثقال)
   {abshode} تومان

🥇 سکه امامی
   {seke_emami} تومان
   حباب: {seke_emami_bubble}

🥇 سکه بهار آزادی
   {seke_bahar} تومان

🥇 نیم سکه
   {seke_nim} تومان

🥇 ربع سکه
   {seke_rob} تومان

🥇 سکه گرمی
   {seke_grami} تومان

🌍 انس جهانی طلا
   {ounce_gold} دلار

⚪ انس نقره
   {ounce_silver} دلار

━━━━━━━━━━━━━━━━━━━━
💵 ارزهای رایج
━━━━━━━━━━━━━━━━━━━━
🇺🇸 دلار آمریکا: {usd} تومان
🇪🇺 یورو: {eur} تومان
🇬🇧 پوند انگلیس: {gbp} تومان
🇦🇪 درهم امارات: {aed} تومان
🇹🇷 لیر ترکیه: {try} تومان

━━━━━━━━━━━━━━━━━━━━
💎 ارزهای دیجیتال
━━━━━━━━━━━━━━━━━━━━
✅ Bitcoin: {btc}
✅ Bitcoin Cash: {bch}
✅ Ethereum: {eth}
✅ XRP: {xrp}
✅ TRON: {trx}
✅ BNB: {bnb}
✅ Solana: {sol}
✅ Dogecoin: {doge}
✅ Cardano: {ada}
✅ Stellar: {xlm}
✅ Litecoin: {ltc}
✅ Zcash: {zec}
✅ Monero: {xmr}
✅ Gram: {gram}

━━━━━━━━━━━━━━━━━━━━
🕐 آخرین به‌روزرسانی: {date} - {time}"""

DEFAULT_ANALYSIS_TEMPLATE = """🧠 تحلیل هوش مصنوعی
━━━━━━━━━━━━━━━━━━━━
🪙 نماد: {symbol}
📅 تاریخ: {date} - {time}

📊 قیمت فعلی: {price}
📈 تغییر ۲۴ ساعته: {change_24h}

🔍 تحلیل تکنیکال:
━━━━━━━━━━━━━━━━━━━━
• روند کلی: {trend}
• RSI: {rsi}
• MACD: {macd}
• حجم معاملات: {volume}
• مقاومت نزدیک: {resistance}
• حمایت نزدیک: {support}

🧠 تحلیل هوش مصنوعی:
━━━━━━━━━━━━━━━━━━━━
{ai_analysis}

📌 پیشنهاد:
━━━━━━━━━━━━━━━━━━━━
🟢 سیگنال: {signal}
🎯 هدف اول: {tp1}
🎯 هدف دوم: {tp2}
🛑 حد ضرر: {sl}
⚖️ ریسک به ریوارد: {rr}

━━━━━━━━━━━━━━━━━━━━
📌 این تحلیل صرفاً جنبه‌ی آموزشی دارد و توصیه‌ی مالی و سرمایه‌گذاری نیست.
لطفا قبل از هر تصمیمی بررسی شود."""

# قالب خط «وضعیت بازار» در منوی اصلی کاربر؛ Placeholder ها: {state} {rsi} {adx} {bull} {bear}
MARKET_PLACEHOLDERS = {"state", "rsi", "adx", "bull", "bear"}
DEFAULT_MARKET_TEMPLATE = (
    "وضعیت کلی بازار: {state} | میانگین RSI بازار: {rsi} | درصد نمادهای صعودی: {bull}٪"
)

DEFAULT_MSG_TEMPLATES: Dict[str, str] = {
    "price": DEFAULT_PRICE_TEMPLATE,
    "analysis": DEFAULT_ANALYSIS_TEMPLATE,
}

# مهاجرت پیش‌فرض‌ها: دیتابیس‌های قدیمی فقط یک بار به این مقدارها می‌روند (نسخه -> {کلید: مقدار})
DEFAULTS_MIGRATION: Dict[int, Dict[str, str]] = {
    3: {"en_squeeze": "1", "sq_pre": "1"},
    4: {"en_lq": "1", "en_fvg": "1", "en_sm": "1", "en_mb": "1"},
}
DEFAULTS_VERSION: int = max(DEFAULTS_MIGRATION)

# الگوهای کندلی: id -> (عنوان الگوی صعودی، عنوان الگوی نزولی)
CANDLE_PATTERNS: Dict[str, tuple] = {
    "hammer": ("Hammer", "Shooting Star"),
    "engulf": ("Bullish Engulfing", "Bearish Engulfing"),
    "piercing": ("Piercing Line", "Dark Cloud Cover"),
    "harami": ("Bullish Harami", "Bearish Harami"),
    "star": ("Morning Star", "Evening Star"),
    "soldiers": ("Three White Soldiers", "Three Black Crows"),
}


# ----------------------------------------------------------------------------
# مشخصات پارامترها
# ----------------------------------------------------------------------------
@dataclass(frozen=True)
class ParamSpec:
    key: str
    label: str
    kind: str          # "int" | "float"
    min: float
    max: float
    default: str
    group: str         # engine id / "rsk" / "snd" / "live" / "rl" / "as" / "pre" / "sub"
    unit: str = ""


PARAM_SPECS: List[ParamSpec] = [
    # Supertrend
    ParamSpec("st_period", "دوره Supertrend", "int", 1, 100, "10", "st"),
    ParamSpec("st_mult", "ضریب Supertrend", "float", 0.5, 10, "3", "st"),
    # EMA
    ParamSpec("ema_fast", "EMA سریع", "int", 2, 200, "50", "ema"),
    ParamSpec("ema_slow", "EMA کند", "int", 5, 240, "200", "ema"),
    # RSI
    ParamSpec("rsi_period", "دوره RSI", "int", 2, 100, "14", "rsi"),
    ParamSpec("rsi_ob", "حد اشباع خرید RSI (وتو خرید)", "float", 50, 100, "70", "rsi"),
    ParamSpec("rsi_os", "حد اشباع فروش RSI (وتو فروش)", "float", 0, 50, "30", "rsi"),
    # Stochastic RSI
    ParamSpec("srsi_rsi", "دوره RSI در StochRSI", "int", 2, 100, "14", "srsi"),
    ParamSpec("srsi_stoch", "دوره Stochastic", "int", 2, 100, "14", "srsi"),
    ParamSpec("srsi_k", "هموارسازی K", "int", 1, 20, "3", "srsi"),
    ParamSpec("srsi_d", "هموارسازی D", "int", 1, 20, "3", "srsi"),
    # OBV
    ParamSpec("obv_ma", "دوره میانگین OBV", "int", 2, 200, "20", "obv"),
    # ADX
    ParamSpec("adx_period", "دوره ADX", "int", 2, 100, "14", "adx"),
    ParamSpec("adx_min", "حداقل ADX برای روند", "float", 5, 60, "20", "adx"),
    # ATR / خروج
    ParamSpec("atr_period", "دوره ATR", "int", 2, 100, "14", "atr"),
    ParamSpec("sl_mult", "ضریب حد ضرر (ATR)", "float", 0.1, 20, "1.5", "atr"),
    ParamSpec("tp1_mult", "ضریب TP1 (ATR)", "float", 0.1, 30, "1", "atr"),
    ParamSpec("tp2_mult", "ضریب TP2 (ATR)", "float", 0.1, 40, "2", "atr"),
    ParamSpec("tp3_mult", "ضریب TP3 (ATR)", "float", 0.1, 60, "3", "atr"),
    ParamSpec("min_atr_pct", "حداقل نوسان (ATR%)", "float", 0.1, 5, "0.5", "atr", "%"),
    # وقتی موتور ATR خاموش باشد، حد ضرر و سود با درصد ثابت حساب می‌شود
    ParamSpec("fx_sl", "حد ضرر ثابت (فقط وقتی ATR خاموش است)", "float", 0.1, 50, "1.5", "atr", "%"),
    ParamSpec("fx_tp1", "TP1 ثابت (فقط وقتی ATR خاموش است)", "float", 0.1, 100, "1", "atr", "%"),
    ParamSpec("fx_tp2", "TP2 ثابت (فقط وقتی ATR خاموش است)", "float", 0.1, 100, "2", "atr", "%"),
    ParamSpec("fx_tp3", "TP3 ثابت (فقط وقتی ATR خاموش است)", "float", 0.1, 100, "3", "atr", "%"),
    # حجم
    ParamSpec("vol_ma", "دوره میانگین حجم", "int", 2, 200, "20", "vol"),
    ParamSpec("vol_ratio", "نسبت حداقل حجم به میانگین", "float", 0.1, 5, "1", "vol"),
    # Squeeze (فشردگی)
    ParamSpec("sq_bb_period", "دوره باندهای بولینگر", "int", 5, 100, "20", "sq"),
    ParamSpec("sq_bb_mult", "ضریب باندهای بولینگر", "float", 0.5, 5, "2", "sq"),
    ParamSpec("sq_pre", "زمان صدور سیگنال (1 = قبل از انفجار، 0 = بعد از شکست)", "int", 0, 1, "1", "sq"),
    ParamSpec("sq_threshold", "آستانه فشردگی (نسبت پهنای باند به میانگین)", "float", 0.1, 1, "0.8", "sq"),
    ParamSpec("sq_min_bars", "حداقل مدت فشردگی", "int", 2, 100, "5", "sq", "کندل"),
    ParamSpec("sq_fresh", "حداکثر فاصله شکست از پایان فشردگی", "int", 1, 20, "4", "sq", "کندل"),
    ParamSpec("sq_vol_ratio", "حداقل حجم برای تأیید انفجار (فقط حالت بعد از شکست)", "float", 0.5, 10, "1.25", "sq"),
    # Divergence (واگرایی RSI)
    ParamSpec("div_rsi", "دوره RSI واگرایی", "int", 2, 100, "14", "div"),
    ParamSpec("div_pivots", "تعداد کف/سقف مورد بررسی", "int", 2, 5, "2", "div"),
    ParamSpec("div_gap", "حداقل فاصله بین دو کف/سقف", "int", 2, 100, "5", "div", "کندل"),
    ParamSpec("div_strength", "قدرت کف/سقف (کندل دو طرف)", "int", 1, 10, "3", "div", "کندل"),
    ParamSpec("div_age", "حداکثر قدمت آخرین کف/سقف", "int", 1, 50, "10", "div", "کندل"),
    ParamSpec("div_lookback", "دوره بازگشتی جستجو", "int", 20, 290, "100", "div", "کندل"),
    # الگوهای کندلی
    ParamSpec("cd_min_score", "حداقل امتیاز الگو برای تأیید", "int", 10, 100, "60", "cd"),
    ParamSpec("cd_lookback", "دوره بازگشتی جستجوی الگو", "int", 1, 10, "3", "cd", "کندل"),
    # حمایت و مقاومت خودکار
    ParamSpec("sr_count", "تعداد سقف/کف مورد بررسی", "int", 2, 30, "6", "sr"),
    ParamSpec("sr_min_dist", "حداقل فاصله قیمتی برای تشخیص سطح", "float", 0.05, 5, "0.3", "sr", "%"),
    ParamSpec("sr_min_gap", "حداقل فاصله زمانی بین دو سطح", "int", 1, 200, "8", "sr", "کندل"),
    ParamSpec("sr_strength", "قدرت کف/سقف (کندل دو طرف)", "int", 1, 10, "3", "sr", "کندل"),
    ParamSpec("sr_touches", "حداقل برخورد برای سطح قوی", "int", 1, 10, "2", "sr"),
    ParamSpec("sr_near", "فاصله‌ی نزدیکی به سطح (وتو)", "float", 0.1, 5, "0.8", "sr", "ATR"),
    ParamSpec("sr_lookback", "دوره بازگشتی جستجوی سطوح", "int", 30, 290, "200", "sr", "کندل"),
    # Volume POC
    ParamSpec("poc_lookback", "دوره بازگشتی POC", "int", 20, 290, "100", "poc", "کندل"),
    ParamSpec("poc_bins", "تعداد بین‌های پروفایل حجم", "int", 5, 100, "24", "poc"),
    ParamSpec("poc_min_dist", "حداقل فاصله قیمت از POC برای وتو", "float", 0, 5, "0.2", "poc", "%"),
    ParamSpec("poc_score", "امتیاز POC", "int", 0, 40, "15", "poc"),
    ParamSpec("poc_sl_buffer", "فاصله‌ی حد ضرر پشت POC", "float", 0, 5, "0.2", "poc", "ATR"),
    ParamSpec("poc_sl_min", "حداقل فاصله حد ضرر مبتنی بر POC", "float", 0.1, 5, "0.5", "poc", "ATR"),
    # Liquidity Sweep
    ParamSpec("lq_lookback", "دوره بازگشتی جستجوی سقف/کف", "int", 20, 290, "100", "lq", "کندل"),
    ParamSpec("lq_strength", "قدرت سقف/کف", "int", 1, 10, "3", "lq", "کندل"),
    ParamSpec("lq_fresh", "حداکثر قدمت Sweep", "int", 1, 20, "5", "lq", "کندل"),
    ParamSpec("lq_max_wick", "حداقل نسبت سایه به بدنه", "float", 0.5, 5, "2", "lq"),
    ParamSpec("lq_vol_ratio", "حداقل نسبت حجم Sweep", "float", 0.5, 5, "1.5", "lq"),
    # Fair Value Gaps
    ParamSpec("fvg_lookback", "دوره بازگشتی جستجوی FVG", "int", 20, 290, "100", "fvg", "کندل"),
    ParamSpec("fvg_min_size", "حداقل اندازه FVG", "float", 0.05, 5, "0.3", "fvg", "%"),
    ParamSpec("fvg_max_age", "حداکثر قدمت FVG", "int", 5, 100, "30", "fvg", "کندل"),
    ParamSpec("fvg_max_count", "حداکثر تعداد FVG مورد بررسی", "int", 1, 10, "3", "fvg"),
    # Smart Money
    ParamSpec("sm_lookback", "دوره بررسی Smart Money", "int", 10, 100, "30", "sm", "کندل"),
    ParamSpec("sm_vol_ratio", "حداقل نسبت حجم به میانگین", "float", 1, 10, "2", "sm"),
    ParamSpec("sm_body_ratio", "حداقل نسبت بدنه به دامنه", "float", 0.3, 1, "0.7", "sm"),
    # Momentum Breakout
    ParamSpec("mb_ema_fast", "دوره EMA سریع", "int", 3, 50, "9", "mb", "کندل"),
    ParamSpec("mb_ema_slow", "دوره EMA کند", "int", 5, 100, "21", "mb", "کندل"),
    ParamSpec("mb_rsi_long", "حداقل RSI برای LONG", "float", 50, 80, "60", "mb"),
    ParamSpec("mb_rsi_short", "حداکثر RSI برای SHORT", "float", 20, 50, "40", "mb"),
    ParamSpec("mb_vol_ratio", "حداقل نسبت حجم", "float", 1, 5, "1.3", "mb"),
    # هوش مصنوعی (AI)
    ParamSpec("ai_min_score", "آستانه امتیاز برای فراخوانی AI", "float", 50, 100, "70", "ai"),
    ParamSpec("ai_daily_max", "حداکثر مصرف روزانه", "int", 1, 100000, "200", "ai", "درخواست"),
    ParamSpec("ai_interval", "فاصله زمانی بین درخواست‌ها", "int", 0, 600, "3", "ai", "ثانیه"),
    ParamSpec("ai_max_len", "حداکثر طول توضیحات تولیدی", "int", 20, 300, "90", "ai", "کاراکتر"),
    ParamSpec("ai_sent_veto", "حداقل اطمینان مخالفت احساسات برای رد سیگنال", "int", 50, 100, "70", "ai", "%"),
    ParamSpec("ai_key_cooldown", "مدت غیرفعال‌سازی موقت کلید خطادار", "int", 1, 1440, "60", "aik", "دقیقه"),
    # ریسک و اهرم
    ParamSpec("risk_min", "حداقل ریسک", "float", 0.1, 100, "1", "rsk", "%"),
    ParamSpec("risk_max", "حداکثر ریسک", "float", 0.1, 100, "3", "rsk", "%"),
    ParamSpec("lev_min", "حداقل اهرم", "int", 1, 125, "5", "rsk", "X"),
    ParamSpec("lev_max", "حداکثر اهرم", "int", 1, 125, "20", "rsk", "X"),
    # ارسال
    ParamSpec("send_interval", "فاصله ارسال سیگنال", "int", 1, 1440, "15", "snd", "دقیقه"),
    # قیمت زنده
    ParamSpec("live_interval", "فاصله‌ی به‌روزرسانی قیمت", "int", 1, 3600, "2", "live", "ثانیه"),
    ParamSpec("live_duration", "مدت به‌روزرسانی هر سیگنال", "int", 1, 1440, "60", "live", "دقیقه"),
    # محدودیت درخواست
    ParamSpec("rl_max", "حداکثر درخواست سیگنال هر کاربر در بازه", "int", 1, 100, "5", "rl"),
    ParamSpec("rl_window", "بازه‌ی محدودیت درخواست", "int", 5, 3600, "60", "rl", "ثانیه"),
    ParamSpec("rl_block", "مدت محدودیت موقت", "int", 5, 86400, "120", "rl", "ثانیه"),
    ParamSpec("rl_global", "سقف کل درخواست سیگنال همه‌ی کاربرها در دقیقه", "int", 5, 10000, "120", "rl"),
    # ضد اسپم
    ParamSpec("as_max", "حداکثر پیام/کلیک در بازه", "int", 3, 200, "20", "as"),
    ParamSpec("as_window", "بازه‌ی بررسی اسپم", "int", 2, 600, "10", "as", "ثانیه"),
    ParamSpec("as_block", "مدت مسدودسازی موقت", "int", 1, 1440, "10", "as", "دقیقه"),
    # هشدار قبل از سیگنال
    ParamSpec("pre_min_score", "حداقل امتیاز برای ارسال هشدار", "float", 50, 100, "75", "pre"),
    ParamSpec("pre_cooldown", "فاصله هشدار تکراری برای یک نماد", "int", 10, 1440, "180", "pre", "دقیقه"),
    ParamSpec("pre_gap", "حداقل فاصله بین دو هشدار", "int", 1, 240, "10", "pre", "دقیقه"),
    # اشتراک
    ParamSpec("sub_amount", "مبلغ اشتراک", "float", 0.01, 1000000, "0", "sub", "دلار"),
    ParamSpec("sub_days", "مدت اشتراک", "int", 1, 365, "30", "sub", "روز"),
    ParamSpec("sub_renew_days", "نمایش دکمه تمدید از چند روز مانده به پایان", "int", 1, 30, "7", "sub", "روز"),
    ParamSpec("sub_disc_pct", "درصد تخفیف اشتراک", "float", 1, 99, "10", "subd", "%"),
    ParamSpec("sub_disc_days", "دوره‌ی تخفیف (مدت نمایش و اعمال)", "int", 1, 365, "3", "subd", "روز"),
    # Keep-Alive
    ParamSpec("keep_alive_min", "حداقل فاصله‌ی درخواست Keep-Alive", "int", 30, 86400, "300", "ka", "ثانیه"),
    ParamSpec("keep_alive_max", "حداکثر فاصله‌ی درخواست Keep-Alive", "int", 30, 86400, "600", "ka", "ثانیه"),
]
PARAMS: Dict[str, ParamSpec] = {p.key: p for p in PARAM_SPECS}

# موتورها: id -> (عنوان، کلید فعال/غیرفعال). همه‌ی موتورها قابل خاموش/روشن‌اند.
ENGINES: Dict[str, tuple] = {
    "st": ("Supertrend", "en_supertrend"),
    "ema": ("EMA 50/200", "en_ema"),
    "rsi": ("RSI", "en_rsi"),
    "srsi": ("Stochastic RSI", "en_stochrsi"),
    "obv": ("OBV", "en_obv"),
    "adx": ("ADX", "en_adx"),
    "atr": ("ATR و حد سود/ضرر", "en_atr"),
    "vol": ("فیلتر حجم", "en_vol"),
    "sq": ("Squeeze (فشردگی)", "en_squeeze"),
    "div": ("Divergence (واگرایی)", "en_diverg"),
    "cd": ("الگوهای کندلی", "en_candle"),
    "sr": ("حمایت و مقاومت خودکار", "en_sr"),
    "poc": ("Volume POC", "en_poc"),
    "lq": ("Liquidity Sweep", "en_lq"),
    "fvg": ("Fair Value Gaps", "en_fvg"),
    "sm": ("Smart Money Signals", "en_sm"),
    "mb": ("Momentum Breakout", "en_mb"),
    "ai": ("هوش مصنوعی (AI)", "en_ai"),
}
TOGGLE_ENGINES = list(ENGINES.keys())

# نام موتورها داخل فایل JSON (Import/Export)
ENGINE_JSON: Dict[str, str] = {
    "st": "supertrend", "ema": "ema", "rsi": "rsi", "srsi": "stoch_rsi", "obv": "obv",
    "adx": "adx", "atr": "atr", "vol": "volume", "sq": "squeeze", "div": "divergence",
    "cd": "candlestick", "sr": "support_resistance", "poc": "volume_poc", "ai": "ai",
    "lq": "liquidity_sweep", "fvg": "fair_value_gaps", "sm": "smart_money", "mb": "momentum_breakout",
}

# قابلیت‌های AI: id -> کلید تنظیم (تحلیل احساسات / تأیید نهایی سیگنال / تولید توضیحات)
AI_FEATURES: Dict[str, tuple] = {
    "sent": ("ai_sent_on", "تحلیل احساسات"),
    "confirm": ("ai_confirm_on", "تأیید نهایی سیگنال"),
    "explain": ("ai_explain_on", "تولید توضیحات سیگنال"),
}
AI_EXTRA_KEYS = {"ai_model"} | {k for k, _ in AI_FEATURES.values()}

# کلیدهای تنظیمات موتورها (در حالت «جداگانه» برای هر جهت یک نسخه‌ی مستقل با پسوند @LONG / @SHORT دارند)
ENGINE_TOGGLE_KEYS = {k for _, k in ENGINES.values()}
ENGINE_PARAM_KEYS = {p.key for p in PARAM_SPECS if p.group in ENGINES}
PATTERN_KEYS = {f"cdp_{pid}" for pid in CANDLE_PATTERNS}
ENGINE_KEYS = ENGINE_TOGGLE_KEYS | ENGINE_PARAM_KEYS | PATTERN_KEYS | AI_EXTRA_KEYS
# تنظیمات AI: وقتی ادمین «تنظیمات AI مشترک» را انتخاب کند، حتی تو حالت جداگانه از نسخه‌ی مشترک خوانده می‌شوند
AI_KEYS = {"en_ai"} | AI_EXTRA_KEYS | {p.key for p in PARAM_SPECS if p.group == "ai"}
# تغییر این کلیدها کش تحلیل را باطل می‌کند (AI بعد از تحلیل اعمال می‌شود و تنظیماتش کش را باطل نمی‌کند)
ANALYSIS_KEYS = (ENGINE_KEYS - AI_KEYS) | {"tf_entry", "tf_final", "tf_entry_on", "tf_final_on", "eng_mode"}

# ----------------------------------------------------------------------------
# تنظیمات مستقل هر کاربر (per-user)
#   - تنظیمات موتورها (به‌جز تنظیمات AI که فقط دست ادمین است) + فقط «مدل AI»
#   - تایم‌فریم‌ها (در حالت جداگانه برای هر جهت مستقل)، ریسک و اهرم، تنظیمات ارسال
# ----------------------------------------------------------------------------
USER_ENGINE_KEYS = (ENGINE_KEYS - AI_KEYS) | {"ai_model"}
USER_TF_KEYS = {"tf_entry", "tf_final", "tf_entry_on", "tf_final_on"}
USER_BASE_KEYS = USER_TF_KEYS | {
    "eng_mode", "eng_view", "risk_min", "risk_max", "lev_min", "lev_max",
    "send_interval", "dedupe_on", "last_broadcast_ts",
}
USER_KEYS = USER_ENGINE_KEYS | USER_BASE_KEYS


def is_engine_key(key: str) -> bool:
    return key in ENGINE_KEYS


# Global chart display settings; symbol search and drawing tools are locked.
DEFAULT_CHART_SETTINGS = {
    "theme": "dark",
    "showRealtimePrice": True,
    "showVolumePanel": True,
    "disableSymbolSearch": True,
    "enableDrawingTools": True,
}


DEFAULT_SETTINGS: Dict[str, str] = {
    "bot_on": "1",
    "admin_sig_on": "1",         # ارسال سیگنال ادمین به کانال (0 = خاموش)
    "tf_entry": "15m",
    "tf_final": "4h",
    "tf_entry_on": "1",
    "tf_final_on": "1",
    "eng_mode": "global",
    "eng_view": "LONG",
    "en_supertrend": "1",
    "en_ema": "1",
    "en_rsi": "1",
    "en_stochrsi": "1",
    "en_obv": "1",
    "en_adx": "1",
    "en_atr": "1",
    "en_vol": "1",
    # Squeeze روشن است و پیش‌فرض «قبل از انفجار» (sq_pre=1) سیگنال می‌دهد: وقتی بازار فشرده شده و هنوز شکست اتفاق نیفتاده است
    "en_squeeze": "1",
    "en_diverg": "1",
    "en_candle": "1",
    "en_sr": "1",
    "en_poc": "1",
    "en_lq": "1",
    "en_fvg": "1",
    "en_sm": "1",
    "en_mb": "1",
    "en_ai": "1",                # موتور AI (به‌ازای هر جهت در حالت جداگانه)
    "ai_on": "0",                # سوییچ کل زیرسیستم هوش مصنوعی (پیش‌فرض خاموش تا کلید ثبت شود)
    "ai_sep": "0",               # در حالت تنظیمات جداگانه: تنظیمات AI هم جداگانه باشد؟ (0 = مشترک)
    "ai_off_until": "0",         # خاموشی موقت AI بعد از خطای همه‌ی کلیدها (epoch)
    "ai_url": AI_WORKER_URL,
    "ai_model": AI_MODELS[0],
    "ai_models": json.dumps(AI_MODELS, ensure_ascii=False),   # مدل‌های مجاز برای انتخاب کاربران
    "ai_sent_on": "1",
    "ai_confirm_on": "1",
    "ai_explain_on": "1",
    "dedupe_on": "1",
    "live_on": "1",
    "last_broadcast_ts": "0",
    "pre_alert_on": "0",
    # --- تنظیمات خرید اشتراک ---
    "sub_mode": "off",              # off | manual | bsc
    "sub_wallet": "",               # آدرس ولت برای خرید دستی
    "bsc_wallet": "",               # آدرس ولت برای شبکه BSC (BEP20)
    "bscscan_api_key": "",          # کلید API سایت BscScan
    "sub_template": (
        "خرید / تمدید اشتراک\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "مبلغ قابل پرداخت: {amount} دلار\n\n"
        "آدرس کیف پول مقصد:\n"
        "`{wallet}`\n\n"
        "توجه: لطفاً فقط از مبلغ و آدرسی که در بالا نوشته شده استفاده کنید. "
        "در غیر این صورت تراکنش شما تأیید نخواهد شد."
    ),
    "sub_disc_on": "0",         # تخفیف اشتراک (روشن/خاموش)
    "sub_disc_start": "0",      # زمان شروع تخفیف (epoch)
    "sub_disc_kind": "percent", # percent | fixed
    "sub_disc_fixed": "0",      # مبلغ ثابت تخفیف (دلار)؛ فقط وقتی kind=fixed
    "sub_disc_end": "0",        # پایان صریح تخفیف (epoch)؛ صفر یعنی «شروع + مدت»
    # --- اشتراک تحلیل ساختاری (وب‌اپ) ---
    "web_sub_mode": "off",          # off | manual | bsc
    "web_sub_amount": "0",
    "web_sub_days": "30",
    "web_sub_wallet": "",
    "web_bsc_wallet": "",
    "web_sub_template": (
        "خرید / تمدید اشتراک تحلیل ساختاری\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "مبلغ قابل پرداخت: {amount} دلار\n\n"
        "آدرس کیف پول مقصد:\n"
        "`{wallet}`\n\n"
        "پس از واریز، اطلاعات تراکنش را برای بررسی ارسال کنید. "
        "پس از تأیید، رمز ورود و آدرس وب‌اپ برای شما ارسال می‌شود."
    ),
    "web_notify_template": (
        "سیگنال ساختاری\n"
        "نماد: {symbol}\n"
        "استراتژی: {strategy}\n"
        "نوع: {type}\n"
        "تایم‌فریم: {timeframe}\n"
        "تاریخ: {date}\n"
        "قیمت: {price}"
    ),
    "web_default_timeframe": "1h",
    "chart_settings": json.dumps(DEFAULT_CHART_SETTINGS),
    # -----------------------------------------------------------------
    # --- وضعیت بازار در منوی اصلی ---
    "mkt_on": "1",                  # نمایش خط وضعیت بازار در منوی کاربران
    "mkt_template": DEFAULT_MARKET_TEMPLATE,
    "mkt_state": "",                # bull | bear | neutral (خالی = هنوز محاسبه نشده)
    "mkt_rsi": "0",
    "mkt_adx": "0",
    "mkt_bull": "0",
    "mkt_bear": "0",
    "mkt_count": "0",
    "mkt_at": "0",                  # زمان آخرین محاسبه (epoch)
    # --- پاسخ خودکار هوشمند پشتیبانی ---
    "ai_sup_on": "1",
    "ch_sig_id": "",
    "ch_sig_title": "",
    "ch_sig_tag": "",           # آیدی نمایشی کانال (@username یا لینک) که آخر هر پست سیگنال می‌آید
    "keep_alive_on": "1",
    "active_strategy": "",      # کلید آخرین استراتژی آماده‌ای که اعمال شده است
    "defaults_ver": "0",
    "tpl_defaults_ver": "0",
    # --- برندینگ وب‌اپ (از پنل مدیریت وب‌اپ) ---
    "site_name": "آبان ترید",
    "site_slogan": "تحلیل ساختاری بازار",
    "site_title": "آبان ترید | تحلیل ساختاری",
    "site_logo": "",                 # مسیر فایل لوگو مثل /uploads/branding/logo.png (خالی = لوگوی پیش‌فرض)
    "content_privacy": (
        "<p>آبان ترید به حریم خصوصی کاربران خود احترام می‌گذارد. اطلاعات حساب، شماره تماس و سابقه‌ی "
        "استفاده‌ی شما تنها برای ارائه‌ی خدمات و بهبود تجربه‌ی کاربری استفاده می‌شود و در اختیار "
        "اشخاص ثالث قرار نمی‌گیرد.</p>"
        "<p>پیام‌های پشتیبانی و تراکنش‌ها صرفاً برای پیگیری وضعیت اشتراک نگهداری می‌شوند. "
        "در صورت درخواست حذف حساب، با پشتیبانی در ارتباط باشید.</p>"
    ),
    "content_contact": (
        "<p>برای ارتباط با تیم پشتیبانی آبان ترید می‌توانید از طریق ربات تلگرام یا پشتیبانی داخل وب‌اپ "
        "پیام ارسال کنید. پاسخ‌گویی در ساعات کاری انجام می‌شود.</p>"
    ),
    "content_about": (
        "<p>آبان ترید یک پلتفرم تحلیل ساختاری بازار است که با تکیه بر بیش از ۲۰۰ استراتژی "
        "مقاومت و حمایت، به معامله‌گران کمک می‌کند تصمیم‌های دقیق‌تری بگیرند.</p>"
        "<p>خدمات ما شامل اسکنر چند نمادی، تحلیل چند تایم‌فریمی، تاریخچه‌ی تحلیل‌ها و "
        "سیگنال‌های ساختاری است.</p>"
    ),
    "login_title": "تصمیم‌های دقیق‌تر، با ساختار واقعی بازار",
    "login_subtitle": "بیش از ۲۰۰ استراتژی ساختاری، نمودار حرفه‌ای و تحلیل چند تایم‌فریمی؛ همه در یک فضای کاری یکپارچه.",
    "login_heading": "خوش آمدید",
    "login_note": "برای ورود به فضای تحلیل، رمز عبوری را که از ربات دریافت کرده‌اید وارد کنید.",
    "login_button": "ورود به حساب",
    "login_footer": "رمز عبور فقط پس از فعال‌سازی اشتراک تحلیل ساختاری برای شما ارسال می‌شود.",
    "login_error": "رمز عبور نادرست است.",
    # --- حالت تعمیر و نگهداری ---
    "maint_on": "0",
    "maint_text": "سایت در حال به‌روزرسانی است؛ لطفاً ساعاتی دیگر دوباره مراجعه کنید.",
    "maint_at": "0",
    # --- اشتراک رایگان ---
    "free_bot_on": "0",
    "free_bot_days": "3",
    "free_bot_features": "signals,analysis,multi,settings,ai,live",
    "free_web_on": "0",
    "free_web_days": "3",
    "free_web_strategies": "",          # خالی = همه‌ی استراتژی‌های فعال
    "free_web_categories": "",          # دسته‌بندی‌های رایگان (در کنار کلیدهای صریح)؛ خالی = بدون دسته
    # --- تخفیف اشتراک تحلیل ساختاری ---
    "web_disc_on": "0",
    "web_disc_pct": "10",
    "web_disc_days": "3",
    "web_disc_start": "0",
    "web_disc_kind": "percent", # percent | fixed
    "web_disc_fixed": "0",      # مبلغ ثابت تخفیف (دلار)؛ فقط وقتی kind=fixed
    "web_disc_end": "0",        # پایان صریح تخفیف (epoch)؛ صفر یعنی «شروع + مدت»
    "web_sub_renew_days": "7",
    # --- کلیدهای API بلاک‌چین ---
    "nodereal_api_key": "",
    "tronscan_api_key": "",
    "tonapi_api_key": "",
    "arbiscan_api_key": "",
    # --- امنیت وب‌اپ (مستقل از ربات) ---
    "wsec_on": "1",
    "wsec_max": "90",
    "wsec_window": "60",
    "wsec_block": "180",
    "wsec_notify": "1",
    # --- بکاپ وب‌اپ ---
    "backup_keep": "30",
    "backup_auto_on": "0",
    "backup_auto_days": "7",
    # --- ظاهر و چیدمان محتوا ---
    "default_content_width": "normal",     # narrow | normal | wide | full
    "sidebar_enabled": "0",
    "sidebar_position": "right",           # right | left
    "sidebar_pages": "0",                  # نمایش پیش‌فرض سایدبار در صفحات
    "sidebar_posts": "1",                  # نمایش پیش‌فرض سایدبار در مقاله‌ها
    "date_format": "jalali_long",          # jalali_long | jalali_medium | jalali_short | jalali_numeric | jalali_with_time | custom
    "date_format_custom": "{weekday} {day} {month} {year}",
    # --- سئوی وب، تلگرام و تنظیمات فنی ---
    "seo_meta_title": "",
    "seo_meta_description": "",
    "seo_og_image": "",
    "seo_robots_default": "index,follow",
    "seo_canonical_base": "",
    "google_site_verification": "",
    "robots_txt": "",
    "tg_bot_username": "",             # خالی = استفاده از BOT_USERNAME محیط سرور
    "tg_bot_name_guide": "",
    "tg_bot_about_guide": "",
    "tg_bot_description_guide": "",
    "tg_share_app_name": "",
    # --- ثبت‌نام و ورود با کد یکبار مصرف ---
    "registration_open": "1",
    "registration_password_min": "8",
    "otp_login_enabled": "0",
    "ippanel_api_key": "",
    "ippanel_sender": "",
    "ippanel_pattern_code": "",
    "otp_code_length": "5",
    "otp_expiry_seconds": "120",
    "otp_max_attempts": "3",
    "otp_resend_cooldown": "60",
    "otp_daily_limit_per_phone": "10",
}
for _pid in CANDLE_PATTERNS:
    DEFAULT_SETTINGS[f"cdp_{_pid}"] = "1"
for _p in PARAM_SPECS:
    DEFAULT_SETTINGS[_p.key] = _p.default


_BACK_BY_GROUP = {
    "rsk": "a:rsk", "snd": "a:snd", "live": "a:live",
    "rl": "a:rl", "as": "a:rl", "pre": "a:pre", "sub": "a:sub", "subd": "a:subd", "aik": "a:aik", "ka": "a:ka",
}


def param_back(key: str) -> str:
    """کال‌بک صفحه‌ای که پارامتر در آن قرار دارد."""
    g = PARAMS[key].group
    return _BACK_BY_GROUP.get(g, f"a:engp:{g}")


def parse_number(spec: ParamSpec, raw: str) -> Optional[float]:
    raw = (raw or "").strip()
    trans = {ord(ch): str(i) for i, ch in enumerate("۰۱۲۳۴۵۶۷۸۹")}
    trans.update({ord(ch): str(i) for i, ch in enumerate("٠١٢٣٤٥٦٧٨٩")})
    trans[ord("٫")] = "."
    trans[ord("٬")] = None
    raw = raw.translate(trans)
    # «1,000» جداکننده‌ی هزارگان است (نه ممیز)؛ بقیه‌ی کاماها ممیز حساب می‌شوند («2,5» = 2.5)
    if re.fullmatch(r"-?\d{1,3}(,\d{3})+(\.\d+)?", raw):
        raw = raw.replace(",", "")
    raw = raw.replace(",", ".")
    try:
        val = float(raw)
    except ValueError:
        return None
    if val != val or val in (float("inf"), float("-inf")):
        return None
    if spec.kind == "int":
        if abs(val - round(val)) > 1e-9:
            return None
        val = int(round(val))
    return val


def validate_param(key: str, value: float, get: Callable[[str], float]) -> Optional[str]:
    """اعتبارسنجی مقدار و روابط بین پارامترها. در صورت خطا متن خطا برمی‌گرداند."""
    spec = PARAMS[key]
    if value < spec.min or value > spec.max:
        lo = int(spec.min) if spec.kind == "int" or float(spec.min).is_integer() else spec.min
        hi = int(spec.max) if spec.kind == "int" or float(spec.max).is_integer() else spec.max
        return f"عدد باید بین {lo} تا {hi} باشد."

    def cur(k: str) -> float:
        return value if k == key else get(k)

    if cur("ema_fast") >= cur("ema_slow"):
        if key in ("ema_fast", "ema_slow"):
            return "EMA سریع باید کوچک‌تر از EMA کند باشد."
    if key in ("mb_ema_fast", "mb_ema_slow") and cur("mb_ema_fast") >= cur("mb_ema_slow"):
        return "EMA سریع باید کوچک‌تر از EMA کند باشد."
    if key in ("rsi_ob", "rsi_os") and cur("rsi_os") >= cur("rsi_ob"):
        return "حد اشباع فروش باید کمتر از حد اشباع خرید باشد."
    if key in ("tp1_mult", "tp2_mult", "tp3_mult"):
        if not (cur("tp1_mult") < cur("tp2_mult") < cur("tp3_mult")):
            return "ضریب‌های TP باید به ترتیب TP1 < TP2 < TP3 باشند."
    if key in ("fx_tp1", "fx_tp2", "fx_tp3"):
        if not (cur("fx_tp1") < cur("fx_tp2") < cur("fx_tp3")):
            return "درصدهای TP باید به ترتیب TP1 < TP2 < TP3 باشند."
    if key in ("risk_min", "risk_max") and cur("risk_min") > cur("risk_max"):
        return "حداقل ریسک نباید از حداکثر ریسک بیشتر باشد."
    if key in ("lev_min", "lev_max") and cur("lev_min") > cur("lev_max"):
        return "حداقل اهرم نباید از حداکثر اهرم بیشتر باشد."
    return None


def fmt_number(x: float) -> str:
    """نمایش عدد بدون صفرهای اضافی."""
    if float(x).is_integer():
        return str(int(x))
    return f"{x:.6f}".rstrip("0").rstrip(".")