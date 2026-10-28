"""تاریخ شمسی و ابزارهای نمایش اعداد برای وب‌اپ (مستقل از تلگرام)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

# منبع یکتای منطقه‌ی زمانی؛ ماژول ui (ربات تلگرام) همین مقدار را import می‌کند.
TEHRAN = timezone(timedelta(hours=3, minutes=30), name="Asia/Tehran")


def gregorian_to_jalali(gy: int, gm: int, gd: int) -> tuple[int, int, int]:
    g_d_m = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]
    if gy > 1600:
        jy = 979
        gy -= 1600
    else:
        jy = 0
        gy -= 621
    gy2 = gy + 1 if gm > 2 else gy
    days = (
        365 * gy + (gy2 + 3) // 4 - (gy2 + 99) // 100 + (gy2 + 399) // 400
        - 80 + gd + g_d_m[gm - 1]
    )
    jy += 33 * (days // 12053)
    days %= 12053
    jy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        jy += (days - 1) // 365
        days = (days - 1) % 365
    if days < 186:
        jm = 1 + days // 31
        jd = 1 + days % 31
    else:
        jm = 7 + (days - 186) // 30
        jd = 1 + (days - 186) % 30
    return jy, jm, jd


def jalali_parts(dt: datetime) -> tuple[int, int, int]:
    local = dt.astimezone(TEHRAN)
    return gregorian_to_jalali(local.year, local.month, local.day)


def jalali_ts(ts: float) -> str:
    try:
        jy, jm, jd = jalali_parts(datetime.fromtimestamp(float(ts), tz=timezone.utc))
        return f"{jy:04d}/{jm:02d}/{jd:02d}"
    except (TypeError, ValueError, OSError):
        return "-"


def jalali_dt(ts: float) -> str:
    try:
        local = datetime.fromtimestamp(float(ts), tz=timezone.utc).astimezone(TEHRAN)
        jy, jm, jd = gregorian_to_jalali(local.year, local.month, local.day)
        return f"{jy:04d}/{jm:02d}/{jd:02d} - {local.hour:02d}:{local.minute:02d}"
    except (TypeError, ValueError, OSError):
        return "-"


def parse_db_ts(value) -> float:
    """زمان دیتابیس (رشته UTC یا datetime) -> epoch."""
    if value is None:
        return 0.0
    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    try:
        return datetime.strptime(str(value)[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return 0.0


def jalali_from_db(value) -> str:
    return jalali_dt(parse_db_ts(value))


def fa_num(value) -> str:
    return str(value).translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹"))


JALALI_WEEKDAYS = ("شنبه", "یکشنبه", "دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه", "جمعه")
JALALI_MONTHS = (
    "فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور",
    "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند",
)
JALALI_FORMATS = {
    "jalali_long": "{weekday} {day} {month} {year}",
    "jalali_medium": "{day} {month} {year}",
    "jalali_short": "{year}/{month_num}/{day_num}",
    "jalali_numeric": "{year}-{month_num}-{day_num}",
    "jalali_with_time": "{weekday} {day} {month} {year} ساعت {time}",
}
DEFAULT_DATE_FORMAT = "jalali_long"
DEFAULT_CUSTOM_FORMAT = "{weekday} {day} {month} {year}"


def _as_local(value):
    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(TEHRAN)
    if isinstance(value, str) and value.strip() and not value.strip().replace(".", "", 1).isdigit():
        value = parse_db_ts(value)
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc).astimezone(TEHRAN)
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _date_tokens(local: datetime) -> dict:
    jy, jm, jd = gregorian_to_jalali(local.year, local.month, local.day)
    return {
        "weekday": JALALI_WEEKDAYS[(local.weekday() + 2) % 7],
        "day": str(jd),
        "day_num": f"{jd:02d}",
        "month": JALALI_MONTHS[jm - 1],
        "month_num": f"{jm:02d}",
        "year": str(jy),
        "time": f"{local.hour:02d}:{local.minute:02d}",
        "hour": f"{local.hour:02d}",
        "minute": f"{local.minute:02d}",
    }


# فهرست توکن‌های الگوی تاریخ، ساخته‌شده از خودِ توکن‌ساز؛ پنل مدیریت (webapp/content.py) همین
# فهرست را نمایش می‌دهد، بنابراین افزودن توکن جدید هرگز با مستندات اداری از هم جدا نمی‌ماند.
DATE_TOKENS = tuple(_date_tokens(datetime.now(TEHRAN)).keys())


def format_jalali(value, fmt: str | None = None, custom: str | None = None) -> str:
    """تبدیل تاریخ (epoch / datetime / رشته‌ی دیتابیس) به شمسی بر اساس تنظیمات سراسری."""
    local = _as_local(value)
    if local is None:
        return "-"
    if fmt is None or custom is None:
        try:
            from database import db

            fmt = fmt or db.get("date_format") or DEFAULT_DATE_FORMAT
            custom = custom if custom is not None else (db.get("date_format_custom") or DEFAULT_CUSTOM_FORMAT)
        except Exception:
            fmt = fmt or DEFAULT_DATE_FORMAT
            custom = custom if custom is not None else DEFAULT_CUSTOM_FORMAT
    tokens = _date_tokens(local)
    if fmt == "custom":
        template = custom or DEFAULT_CUSTOM_FORMAT
    else:
        template = JALALI_FORMATS.get(fmt or "", JALALI_FORMATS[DEFAULT_DATE_FORMAT])
    out = template
    for key, value_ in tokens.items():
        out = out.replace("{" + key + "}", value_)
    return fa_num(out)


def format_date_filter(value) -> str:
    return format_jalali(value)


def format_date_short(value) -> str:
    return format_jalali(value, "jalali_short")



def fmt_price(value, digits: int | None = None) -> str:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "-"
    if v == 0:
        return "0"
    if digits is None:
        av = abs(v)
        digits = 2 if av >= 100 else 4 if av >= 1 else 6 if av >= 0.01 else 8
    return f"{v:,.{digits}f}".rstrip("0").rstrip(".")
