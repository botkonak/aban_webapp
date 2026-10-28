"""ابزارهای مشترک رابط کاربری آبان ترید: Ctx، کیبورد، تاریخ شمسی و ..."""
from __future__ import annotations

import asyncio
import html
import logging
import time
from datetime import datetime, timezone
from typing import Iterable, List, Optional, Sequence

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LinkPreviewOptions,
    Message,
    Update,
)
from telegram.constants import ParseMode
from telegram.error import BadRequest, RetryAfter, TelegramError
from telegram.ext import ContextTypes

import config
import fmt
from webapp.jalali import TEHRAN

log = logging.getLogger("ui")

NO_PREVIEW = LinkPreviewOptions(is_disabled=True)
OK = "✅"      # ✅
NO = "❌"      # ❌
WARN = "⚠️"   # ⚠️ (فقط برای وضعیت «خطا» در کلیدهای هوش مصنوعی)
BACK_LABEL = "برگشت"


# ----------------------------------------------------------------------------
# متن و کیبورد
# ----------------------------------------------------------------------------
def esc(s: object) -> str:
    return html.escape(str(s), quote=False)


STYLE_RED = "danger"
STYLE_GREEN = "success"


def btn(text: str, data: Optional[str] = None, url: Optional[str] = None, style: Optional[str] = None) -> InlineKeyboardButton:
    """style: None | "danger" (قرمز) | "success" (سبز) | "primary" (آبی) — رنگ دکمه (کلاینت‌های جدید تلگرام)."""
    kw = {"api_kwargs": {"style": style}} if style else {}
    if url:
        return InlineKeyboardButton(text, url=url, **kw)
    return InlineKeyboardButton(text, callback_data=data or "a:noop", **kw)


def kb(*rows: Iterable[InlineKeyboardButton]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([list(r) for r in rows if r])


def back_row(data: str, label: str = BACK_LABEL) -> List[InlineKeyboardButton]:
    return [btn(label, data)]


def home_btn(uid: int) -> InlineKeyboardButton:
    """دکمه‌ی منو برای پیام‌های مستقل (سیگنال، هشدار، اعلان‌ها): پیام جدیدِ منو می‌فرستد و پیام فعلی را خراب نمی‌کند."""
    if uid == config.ADMIN_UID:
        return btn("پنل مدیریت", "a:hm")
    return btn("منوی اصلی", "u:mn")


def menu_markup_for(uid: int) -> InlineKeyboardMarkup:
    return kb([home_btn(uid)])


def signal_markup_for(uid: int) -> InlineKeyboardMarkup:
    """دکمه‌های زیر سیگنال: کاربر «دریافت سیگنال جدید» + «منوی اصلی»؛ ادمین فقط «پنل مدیریت»."""
    if uid == config.ADMIN_UID:
        return kb([home_btn(uid)])
    return kb([btn("دریافت سیگنال جدید", "u:sig")], [home_btn(uid)])


def on_off(flag: bool) -> str:
    return f"{OK} فعال" if flag else f"{NO} غیرفعال"


_flood_until = 0.0


def is_flooded() -> bool:
    """true یعنی تلگرام محدودیت طولانی اعمال کرده و ارسال‌ها موقتاً متوقف هستند."""
    return time.monotonic() < _flood_until


def set_flood(seconds: float) -> None:
    global _flood_until
    _flood_until = max(_flood_until, time.monotonic() + float(seconds) + 1.0)


def reset_flood() -> None:
    global _flood_until
    _flood_until = 0.0


def flood_remaining() -> float:
    return max(0.0, _flood_until - time.monotonic())


def retry_seconds(err: object, default: float = 1.0) -> float:
    """RetryAfter.retry_after می‌تواند عدد یا timedelta باشد."""
    v = getattr(err, "retry_after", default)
    try:
        return float(v.total_seconds()) if hasattr(v, "total_seconds") else float(v)
    except Exception:
        return default


def chunk(items: Sequence, n: int) -> List[list]:
    return [list(items[i:i + n]) for i in range(0, len(items), n)]


def fmt_duration(seconds: float) -> str:
    """مدت زمان به فارسی: «۴۵ ثانیه»، «۱۲ دقیقه»، «۲ ساعت و ۱۵ دقیقه»."""
    s = max(0, int(seconds))
    if s < 60:
        return f"{s} ثانیه"
    if s < 3600:
        return f"{(s + 59) // 60} دقیقه"
    h, m = s // 3600, (s % 3600) // 60
    return f"{h} ساعت" + (f" و {m} دقیقه" if m else "")


def message_text_with_ticks(msg: Message) -> str:
    """متن پیام؛ هر فرمت‌بندی تلگرام (Bold، Italic، Underline، Strikethrough، Spoiler، Monospace، Code Block،
    Quote، لینک) به نشانه‌های متنی (**، __، ___، ~~، ||، `، ```، >، [متن](لینک)) تبدیل می‌شود تا ذخیره شود
    و موقع ارسال دوباره به فرمت تلگرام برگردد."""
    return fmt.entities_to_markers(msg.text or "", msg.entities)


def message_caption_markers(msg: Message) -> str:
    """مثل message_text_with_ticks اما برای کپشن عکس / فایل."""
    return fmt.entities_to_markers(msg.caption or "", msg.caption_entities)


# ----------------------------------------------------------------------------
# تاریخ شمسی
# ----------------------------------------------------------------------------
def gregorian_to_jalali(gy: int, gm: int, gd: int):
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


def jalali_date(ts: str) -> str:
    """تبدیل رشته UTC دیتابیس به تاریخ شمسی تهران: 1403/01/15"""
    if not ts:
        return "-"
    try:
        dt = datetime.strptime(ts[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).astimezone(TEHRAN)
        jy, jm, jd = gregorian_to_jalali(dt.year, dt.month, dt.day)
        return f"{jy:04d}/{jm:02d}/{jd:02d}"
    except Exception:
        return "-"


def jalali_ts(ts: float) -> str:
    """تبدیل epoch به تاریخ شمسی تهران: 1403/01/15"""
    try:
        dt = datetime.fromtimestamp(float(ts), tz=timezone.utc).astimezone(TEHRAN)
        jy, jm, jd = gregorian_to_jalali(dt.year, dt.month, dt.day)
        return f"{jy:04d}/{jm:02d}/{jd:02d}"
    except Exception:
        return "-"


def jalali_dt(ts: float) -> str:
    """تاریخ و ساعت شمسی تهران: 1403/01/15 - 14:30"""
    try:
        dt = datetime.fromtimestamp(float(ts), tz=timezone.utc).astimezone(TEHRAN)
        return f"{jalali_ts(ts)} - {dt.hour:02d}:{dt.minute:02d}"
    except Exception:
        return "-"


_FA_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")
_EN_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def to_fa(s: object) -> str:
    """ارقام انگلیسی -> فارسی."""
    return str(s).translate(_FA_DIGITS)


def to_en(s: object) -> str:
    """ارقام فارسی/عربی -> انگلیسی."""
    return str(s).translate(_EN_DIGITS)


try:  # کتابخانه‌ی waxt برای تاریخ و ساعت شمسی (اگر نصب نبود یا خطا داد، محاسبه‌ی داخلی استفاده می‌شود)
    from waxt.date_service import DateService as _DateService

    _svc = _DateService(timezone="Asia/Tehran", calendar="jalali")
except Exception:  # pragma: no cover
    _svc = None


def now_jalali_parts() -> tuple:
    """(تاریخ شمسی، ساعت) تهران با ارقام فارسی، مثل ('۱۴۰۵/۰۷/۱۳', '۲۲:۰۳')."""
    if _svc is not None:
        try:
            out = str(_svc.format_date(_svc.now(), include_time=True)).split()
            if len(out) >= 2:
                d, t = to_en(out[0]), to_en(out[-1])
                if len(d.split("/")) == 3 and ":" in t:
                    return to_fa(d), to_fa(t)
        except Exception:
            pass
    dt = datetime.now(TEHRAN)
    jy, jm, jd = gregorian_to_jalali(dt.year, dt.month, dt.day)
    return to_fa(f"{jy:04d}/{jm:02d}/{jd:02d}"), to_fa(f"{dt.hour:02d}:{dt.minute:02d}")


def _split_text(text: str) -> List[str]:
    from broadcaster import split_text

    return split_text(text or "")


async def _direct_send(bot, chat_id: int, text: str, markup, parse_mode) -> Optional[Message]:
    """ارسال مستقیم یک تکه (بدون صف) با مدیریت Flood Wait؛ تکه‌ها در Ctx.send تفکیک می‌شوند."""
    try:
        return await bot.send_message(
            chat_id=chat_id, text=text, reply_markup=markup,
            parse_mode=parse_mode, link_preview_options=NO_PREVIEW,
        )
    except RetryAfter as e:
        sec = retry_seconds(e)
        if sec > 30:
            set_flood(sec)
            return None
        await asyncio.sleep(sec + 0.3)
        try:
            return await bot.send_message(
                chat_id=chat_id, text=text, reply_markup=markup,
                parse_mode=parse_mode, link_preview_options=NO_PREVIEW,
            )
        except TelegramError as e2:
            log.warning("send retry failed to %s: %s", chat_id, e2)
            return None
    except TelegramError as e:
        log.warning("send failed to %s: %s", chat_id, e)
        return None


# ----------------------------------------------------------------------------
# Ctx: یک لایه ساده روی Update (پیام یا کال‌بک)
# ----------------------------------------------------------------------------
class Ctx:
    __slots__ = ("update", "context", "q", "uid", "_answered")

    def __init__(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        self.update = update
        self.context = context
        self.q = update.callback_query
        self.uid = update.effective_user.id if update.effective_user else 0
        self._answered = False

    @property
    def bot(self):
        return self.context.bot

    @property
    def user_data(self) -> dict:
        return self.context.user_data

    @property
    def is_callback(self) -> bool:
        return self.q is not None

    @property
    def first_name(self) -> str:
        u = self.update.effective_user
        return (u.first_name or "").strip() if u else ""

    async def answer(self, text: Optional[str] = None, alert: bool = False) -> None:
        """پاسخ به کال‌بک (فقط یک بار)؛ فوراً اسپینر دکمه را برمی‌دارد."""
        if self.q is None:
            return
        if self._answered:
            # کال‌بک قبلاً (مثلاً با پاسخ خودکار ۰٫۲ ثانیه‌ای) پاسخ داده شده؛ هشدار مهم به‌صورت پیام ارسال می‌شود
            if text and alert:
                await self.send(text, None, parse_mode=None)
            return
        self._answered = True
        try:
            await self.q.answer(text=text, show_alert=alert)
        except TelegramError:
            pass

    async def _edit(self, text: str, markup, parse_mode):
        """ویرایش پیام دکمه‌ی فشرده‌شده. خروجی: پیام (موفق) یا False (ویرایش ممکن نیست؛ باید پیام جدید فرستاد)."""
        for attempt in (1, 2):
            try:
                res = await self.q.edit_message_text(
                    text=text, reply_markup=markup, parse_mode=parse_mode, link_preview_options=NO_PREVIEW,
                )
                return res if isinstance(res, Message) else self.q.message
            except BadRequest as e:
                if "not modified" in str(e).lower():
                    return self.q.message
                return False        # پیام متن ندارد (عکس / فایل)، قدیمی است یا حذف شده است
            except RetryAfter as e:
                sec = retry_seconds(e)
                if sec > 30:
                    set_flood(sec)
                    return None
                if attempt == 2:
                    return False
                await asyncio.sleep(sec + 0.3)
            except TelegramError as e:
                log.debug("edit failed for %s: %s", self.uid, e)
                return False
        return False

    async def show(
        self,
        text: str,
        markup: Optional[InlineKeyboardMarkup] = None,
        parse_mode: Optional[str] = ParseMode.HTML,
    ) -> Optional[Message]:
        """
        نمایش صفحه: اگر از روی یک دکمه آمده باشیم پیام قبلی ویرایش می‌شود؛
        اگر پیام قبلی وجود نداشت یا قابل ویرایش نبود، پیام جدید ارسال می‌شود.
        """
        if self.q is not None and self.q.message is not None and not is_flooded():
            res = await self._edit(text, markup, parse_mode)
            if res is None:
                return None
            if res is not False:
                return res
        return await self.send(text, markup, parse_mode)

    async def show_input(
        self,
        mid: Optional[int],
        text: str,
        markup: Optional[InlineKeyboardMarkup] = None,
        parse_mode: Optional[str] = ParseMode.HTML,
    ) -> Optional[Message]:
        """نتیجه‌ی ورودی متنی: پیام راهنمای قبلی (mid) ویرایش می‌شود؛ اگر نشد پیام جدید ارسال می‌شود."""
        if mid and not is_flooded():
            try:
                res = await self.bot.edit_message_text(
                    chat_id=self.uid, message_id=mid, text=text, reply_markup=markup,
                    parse_mode=parse_mode, link_preview_options=NO_PREVIEW,
                )
                return res if isinstance(res, Message) else None
            except BadRequest as e:
                if "not modified" in str(e).lower():
                    return None
            except TelegramError as e:
                log.debug("input edit failed for %s: %s", self.uid, e)
        return await self.send(text, markup, parse_mode)

    async def send(
        self,
        text: str,
        markup=None,
        parse_mode: Optional[str] = ParseMode.HTML,
    ) -> Optional[Message]:
        if is_flooded():
            return None
        parts = _split_text(text)
        last: Optional[Message] = None
        for i, part in enumerate(parts):
            last = await _direct_send(
                self.bot, self.uid, part,
                markup if i == len(parts) - 1 else None, parse_mode,
            )
        return last


async def edit_by_id(
    bot,
    chat_id: int,
    message_id: int,
    text: str,
    markup: Optional[InlineKeyboardMarkup] = None,
    parse_mode: Optional[str] = ParseMode.HTML,
) -> bool:
    """ارسال صفحه‌ی جدید (پیام قبلی ویرایش نمی‌شود)؛ پارامتر message_id برای سازگاری حفظ شده است."""
    if is_flooded():
        return False
    parts = _split_text(text)
    ok = bool(parts)
    for i, part in enumerate(parts):
        sent = await _direct_send(
            bot, chat_id, part,
            markup if i == len(parts) - 1 else None, parse_mode,
        )
        ok = ok and sent is not None
    return ok


async def safe_delete(message) -> None:
    if message is None:
        return
    fn = getattr(message, "delete", None)
    if fn is None:
        return
    try:
        await fn()
    except TelegramError:
        pass


def jalali_dt_str(ts: str) -> str:
    """تبدیل رشته UTC دیتابیس به تاریخ و ساعت شمسی تهران: 1403/01/15 - 14:30"""
    if not ts:
        return "-"
    try:
        dt = datetime.strptime(str(ts)[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).astimezone(TEHRAN)
        jy, jm, jd = gregorian_to_jalali(dt.year, dt.month, dt.day)
        return f"{jy:04d}/{jm:02d}/{jd:02d} - {dt.hour:02d}:{dt.minute:02d}"
    except Exception:
        return "-"


def ts_text(ts) -> str:
    """زمان دیتابیس (رشته یا datetime) -> تاریخ و ساعت شمسی تهران."""
    from database import ts_str

    return jalali_dt_str(ts_str(ts))
