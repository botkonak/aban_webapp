"""
هندلر آبان ترید: پیام‌ها و Callback ها، ارسال سیگنال (به کاربران و کانال)، هشدار قبل از سیگنال،
محدودیت درخواست، ضد اسپم، انقضای اشتراک و ثبت هندلرها.
"""
from __future__ import annotations

import asyncio
import logging
import random
import user_help
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Deque, Dict, List, Optional, Tuple
from signal_image import generate_signal_image

from telegram import Update
from telegram.error import NetworkError, RetryAfter, TelegramError
from telegram.ext import (
    Application,
    ApplicationHandlerStop,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import admin_panel
import analyst
import blocks
import broadcaster
import config
import force_subscribe
import plans
import price_broadcaster
import subscription
import support
import templates
import user_auth
import user_panel
from ai_engine import ai
from database import db
from live_price import live
from models import Candidate, Channel, ScanResult, Signal
from signal_engine import engine
from ui import (
    NO, NO_PREVIEW, OK, Ctx, back_row, btn, esc, flood_remaining, fmt_duration, home_btn, is_flooded, jalali_date, jalali_ts, kb,
    menu_markup_for, retry_seconds, set_flood, signal_markup_for,
)

log = logging.getLogger("handler")

_cooldown: Dict[int, float] = {}
_flood: Dict[int, float] = {}
_alerts: Dict[str, float] = {}
_events: Dict[int, Deque[float]] = {}        # ضد اسپم: زمان رویدادهای هر کاربر
_blocked_notice: Dict[int, float] = {}       # آخرین باری که پیام «مسدود شدن» به کاربر داده شد
_rl_hist: Dict[int, Deque[float]] = {}       # محدودیت درخواست سیگنال: زمان درخواست‌های هر کاربر
_rl_until: Dict[int, float] = {}
_rl_global: Deque[float] = deque()
_pre_sent: Dict[str, float] = {}             # هشدار قبل از سیگنال: کلید نماد/جهت -> زمان آخرین ارسال
_pre_watch: Dict[str, Tuple[str, str, float]] = {}   # نمادهای هشدارگرفته که منتظر کامل شدن سیگنال‌اند: کلید -> (pair, side, انقضا)
_pre_last = 0.0
_scan_busy: set = set()                       # کاربرانی که اسکن سیگنالشان در جریان است
_bot = None

BLOCKED_TEXT = "به دلیل ارسال بیش‌ازحد درخواست (اسپم)، دسترسی شما موقتاً محدود شده است."
WAIT_TEXT = "لطفاً چند لحظه صبر کنید."
BOT_OFF_TEXT = "ربات در حال حاضر خاموش است."
NO_SIGNAL_TEXT = (
    f"{NO} اکنون سیگنال معتبری پیدا نشد.\n"
    "چند دقیقه‌ی دیگر دوباره امتحان کنید؛ بازار به‌طور مداوم بررسی می‌شود."
)
NOT_READY_TEXT = f"{NO} فعلاً سیگنالی آماده نیست، کمی بعد دوباره سر بزنید."
ERROR_TEXT = f"{NO} اکنون ارتباط با صرافی مشکل دارد، چند لحظه‌ی دیگر دوباره امتحان کنید."
SCANNING_TEXT = "در حال اسکن بازار، یک لحظه صبر کنید..."


def set_bot(bot) -> None:
    global _bot
    _bot = bot


def is_admin(uid: int) -> bool:
    return uid == config.ADMIN_UID


# ============================================================================
# ضد اسپم و محدودیت درخواست
# ============================================================================
def _purge(store: dict, limit: int = 20000) -> None:
    if len(store) > limit:
        for k in list(store)[: len(store) // 2]:
            store.pop(k, None)


def flooding(uid: int) -> bool:
    """فاصله‌ی حداقلی بین دو رویداد یک کاربر (ادمین مستثنی است). رویداد زودتر از این فاصله بی‌صدا نادیده گرفته می‌شود."""
    if is_admin(uid) or config.USER_FLOOD_GAP <= 0:
        return False
    now = time.monotonic()
    if now - _flood.get(uid, -1e9) < config.USER_FLOOD_GAP:
        return True
    _flood[uid] = now
    _purge(_flood)
    return False


def spam_hit(uid: int) -> bool:
    """هر پیام / کلیک ثبت می‌شود؛ بیشتر از as_max رویداد در as_window ثانیه یعنی اسپم."""
    now = time.monotonic()
    h = _events.get(uid)
    if h is None:
        h = _events[uid] = deque()
        _purge(_events)
    window = max(1, db.get_int("as_window"))
    while h and h[0] < now - window:
        h.popleft()
    h.append(now)
    if len(h) > max(1, db.get_int("as_max")):
        h.clear()
        return True
    return False


def rate_wait(uid: int) -> float:
    """
    محدودیت درخواست سیگنال. خروجی: 0 یعنی مجاز است، وگرنه ثانیه‌هایی که باید صبر کند.
    دو سطح دارد: هر کاربر (rl_max در rl_window ثانیه) و کل کاربرها (rl_global در دقیقه).
    تلگرام آی‌پی کاربر را به ربات نمی‌دهد؛ سقف کل جایگزین محدودیت سطح آی‌پی است.
    """
    if is_admin(uid):
        return 0.0
    now = time.monotonic()
    until = _rl_until.get(uid, 0.0)
    if until > now:
        return until - now
    g = _rl_global
    while g and g[0] < now - 60:
        g.popleft()
    if len(g) >= max(1, db.get_int("rl_global")):
        return 5.0
    h = _rl_hist.get(uid)
    if h is None:
        h = _rl_hist[uid] = deque()
        _purge(_rl_hist)
        _purge(_rl_until)
    window = max(1, db.get_int("rl_window"))
    while h and h[0] < now - window:
        h.popleft()
    if len(h) >= max(1, db.get_int("rl_max")):
        block = max(1, db.get_int("rl_block"))
        _rl_until[uid] = now + block
        h.clear()
        return float(block)
    h.append(now)
    g.append(now)
    return 0.0


async def _tell_blocked(ctx: Ctx, remaining: float) -> None:
    if ctx.is_callback:
        await ctx.answer(BLOCKED_TEXT)
        return
    now = time.monotonic()
    if now - _blocked_notice.get(ctx.uid, -1e9) < 30:
        return
    _blocked_notice[ctx.uid] = now
    _purge(_blocked_notice)
    await ctx.send(f"{BLOCKED_TEXT}\nزمان باقی‌مانده: {fmt_duration(remaining)}", menu_markup_for(ctx.uid), parse_mode=None)


async def guard(ctx: Ctx) -> bool:
    """
    ورودی همه‌ی هندلرهای کاربر: بلاک بودن، ضد اسپم و فاصله‌ی حداقلی بین رویدادها.
    False یعنی ادامه ندهید (پیام لازم قبلاً به کاربر داده شده است). ادمین همیشه مجاز است.
    """
    uid = ctx.uid
    if is_admin(uid) or uid == 0:
        return True
    rem = db.block_remaining(uid)
    if rem > 0:
        await _tell_blocked(ctx, rem)
        return False
    if spam_hit(uid):
        minutes = max(1, db.get_int("as_block"))
        db.block_user(uid, time.time() + minutes * 60, "spam")
        log.info("user %s blocked for spam (%d min)", uid, minutes)
        await _tell_blocked(ctx, minutes * 60)
        return False
    if not support.active(ctx) and flooding(uid):     # در پشتیبانی چند پیام / عکس پشت هم مجاز است
        return False
    return True


# ============================================================================
# اعلان به ادمین (محدود شده تا اسپم نشود)
# ============================================================================
async def notify_admin_once(key: str, text: str, cooldown: int = 3600, markup=None) -> None:
    now = time.monotonic()
    if _bot is None or is_flooded() or now - _alerts.get(key, -1e9) < cooldown:
        return
    _alerts[key] = now
    try:
        # اعلان ادمین با اولویت بالا در صف پیام‌ها؛ Flood Wait داخل صف مدیریت می‌شود
        mid = await broadcaster.send_plain(
            _bot, config.ADMIN_UID, text, markup or kb([btn("پنل مدیریت", "a:hm")]),
            priority=broadcaster.PRIO_HIGH, wait=20,
        )
        if mid is None:
            log.debug("admin notify %s was not delivered", key)
    except Exception as e:
        log.debug("admin notify failed: %s", e)


async def on_channel_problem(ch: Channel, err: str) -> None:
    await notify_admin_once(
        f"chan:{ch.id}",
        f"{NO} مشکل در بررسی عضویت کانال «{ch.channel_title or ch.channel_id}»:\n{err}\n\n"
        "مطمئن شوید ربات در این کانال ادمین است. تا وقتی درست نشود، عضویت این کانال بررسی نمی‌شود.",
    )


async def on_price_alert(key: str, text: str) -> None:
    """مشکل ارسال قیمت به کانال‌ها برای ادمین."""
    markup = kb([btn("مدیریت کانال‌های قیمت", "a:pc")], [btn("پنل مدیریت", "a:hm")])
    await notify_admin_once(f"price:{key}", f"{NO} {text}", cooldown=1800, markup=markup)


async def on_ai_alert(key: str, text: str) -> None:
    """هشدار زیرسیستم هوش مصنوعی برای ادمین (کلید نامعتبر، محدودیت نرخ، خطای کامل)."""
    markup = kb([btn("مدیریت کلیدهای هوش مصنوعی", "a:aik")], [btn("پنل مدیریت", "a:hm")])
    await notify_admin_once(f"ai:{key}", text, cooldown=900, markup=markup)


# ============================================================================
# ساخت متن سیگنال
# ============================================================================
def build_channel_text(sig: Signal) -> str:
    """
    متن پست کانال: از قالب کانال همان جهت (CH_LONG / CH_SHORT) ساخته می‌شود؛ قالب کانال {lastprice} ندارد.
    اگر قالب کانال خالی باشد از قالب همان جهت در ربات استفاده می‌شود (بدون {lastprice}).
    آخرین خط پست آیدی کانال (ch_sig_tag) است. بلوک‌های محتوا فقط در همین سیگنال ادمین استفاده می‌شوند.
    """
    template = db.get_template("CH_" + sig.side).strip() or templates.strip_lastprice(db.get_template(sig.side))
    text = templates.render_signal(sig, template, config.BOT_USERNAME, blocks.pick, live=False)
    return templates.append_tag(text, db.get("ch_sig_tag"))


def build_user_signal_text(sig: Signal) -> Tuple[str, Optional[str]]:
    """
    (متن آماده‌ی ارسال، None) برای سیگنال کاربر. 
    چون متن به عنوان کپشن عکس ارسال می‌شود، قیمت زنده غیرفعال است.
    """
    template = db.get_template(sig.side)
    # live=False تا از توکن لایو استفاده نکنه و مستقیم قیمت ورود رو بذاره
    text = templates.render_signal(sig, template, config.BOT_USERNAME, lambda _t: "", live=False)
    return text, None


def track_live(sig: Signal, base: Optional[str], recipients) -> None:
    """ثبت پیام‌های ارسال‌شده برای به‌روزرسانی زنده‌ی {lastprice} (فقط کاربران دارای این امکان)."""
    if not base or not recipients:
        return
    allowed = [(uid, mid) for uid, mid in recipients if plans.bot_feature_allowed(uid, "live")]
    if allowed:
        live.register(sig.pair, sig.decimals, base, sig.entry, allowed)


def signal_markup(uid: int):
    """دکمه‌های زیر سیگنال کاربر: «دریافت سیگنال جدید» و «منوی اصلی»."""
    return signal_markup_for(uid)


# ============================================================================
# ارسال گروهی (هشدار)
# ============================================================================
async def _send_many(
    bot,
    ids: List[int],
    text: str,
    markup_for: Callable[[int], object] = menu_markup_for,
    check_channels: bool = True,
) -> Tuple[int, int, List[Tuple[int, int]]]:
    """ارسال یک متن HTML به لیست کاربران با سقف سرعت. خروجی: (ارسال‌شده، کل، [(uid, message_id)])"""
    total = len(ids)
    has_channels = check_channels and bool(db.active_channels())
    per = config.BROADCAST_PER_SECOND

    async def one(uid: int):
        if has_channels and not is_admin(uid):
            ok, _ = await force_subscribe.check(bot, uid)
            if not ok:
                return None
        mid = await broadcaster.send_html(bot, uid, text, markup_for(uid))
        return (uid, mid) if mid else None

    recipients: List[Tuple[int, int]] = []
    for i in range(0, total, per):
        t0 = time.monotonic()
        res = await asyncio.gather(*(one(u) for u in ids[i:i + per]), return_exceptions=True)
        recipients.extend(r for r in res if isinstance(r, tuple))
        if i + per < total:
            await asyncio.sleep(max(0.0, 1.0 - (time.monotonic() - t0)))
    return len(recipients), total, recipients


async def post_to_channel(bot, sig: Signal) -> bool:
    """انتشار سیگنال ادمین در کانال تعیین‌شده (عکس + متن)."""
    cid = db.get("ch_sig_id")
    if not cid:
        return False
        
    # ساخت متن کانال
    text = build_channel_text(sig)
    # ساخت عکس
    img_buf = await asyncio.to_thread(generate_signal_image, sig)
    
    # ارسال عکس با متن به عنوان کپشن
    mid = await broadcaster.send_photo_html(bot, force_subscribe.chat_ref(cid), img_buf, text)
    
    if mid is None:
        await notify_admin_once(
            "chsig_fail",
            f"{NO} ارسال سیگنال به کانال «{db.get('ch_sig_title') or cid}» انجام نشد.\n"
            "مطمئن شوید ربات در آن کانال ادمین است و دسترسی ارسال پیام دارد.",
            cooldown=1800,
            markup=kb([btn("تنظیمات ارسال به کانال", "a:chs")], [btn("پنل مدیریت", "a:hm")]),
        )
        return False
    return True


async def warn_no_channel() -> None:
    await notify_admin_once(
        "no_channel",
        f"{NO} کانال سیگنال تعیین نشده است و به همین دلیل سیگنال ادمین ارسال نمی‌شود.\n"
        "از «تنظیمات > ارسال سیگنال به کانال» کانال را تعیین کنید.",
        cooldown=6 * 3600,
        markup=kb([btn("تنظیمات ارسال به کانال", "a:chs")], [btn("پنل مدیریت", "a:hm")]),
    )


@dataclass
class Delivery:
    channel: Optional[bool] = None            # None یعنی کانالی در کار نبود

    def report(self, sig: Signal) -> str:
        if self.channel:
            return f"{OK} سیگنال {sig.symbol} {sig.side} در کانال ارسال شد."
        return f"{NO} سیگنال {sig.symbol} {sig.side} پیدا شد اما ارسال به کانال انجام نشد."


async def deliver_signal(bot, sig: Signal) -> Delivery:
    """سیگنال ادمین فقط به کانال تعیین‌شده ارسال می‌شود (هیچ کاربری در چت خصوصی دریافت نمی‌کند)."""
    d = Delivery()
    d.channel = await post_to_channel(bot, sig)
    db.set("last_broadcast_ts", str(time.time()))
    _pre_watch.pop(f"{sig.pair}:{sig.side}", None)       # این نماد سیگنال شد؛ دیگر لازم نیست منتظرش بمانیم
    return d


# ============================================================================
# نتیجه‌ی اسکن برای یک کاربر
# ============================================================================
def result_markup(uid: int):
    """دکمه‌های زیر پیام‌های نتیجه (خطا / سیگنال پیدا نشد)."""
    if is_admin(uid):
        return kb([home_btn(uid)])
    return kb([btn("دریافت سیگنال جدید", "u:sig")], back_row("u:menu"))


async def _finish_message(ctx: Ctx, status, text: str, html: bool, markup=None) -> Optional[int]:
    """نتیجه را در پیام جدید ارسال می‌کند (پیام وضعیت ویرایش نمی‌شود). خروجی: شناسه‌ی پیام."""
    if html:
        return await broadcaster.send_html(ctx.bot, ctx.uid, text, markup)
    msg = await ctx.send(text, markup, parse_mode=None)
    return msg.message_id if msg else None


async def _notice(ctx: Ctx, text: str) -> None:
    """هشدار کوتاه: در کال‌بک بالای صفحه‌ی چت (Toast) نمایش داده می‌شود، در پیام عادی با دکمه‌ی منو."""
    if ctx.is_callback:
        await ctx.answer(text)
    else:
        await ctx.send(text, menu_markup_for(ctx.uid), parse_mode=None)


# ============================================================================
# منطق دسترسی کاربر
# ============================================================================
async def gate(ctx: Ctx, force: bool = False) -> bool:
    """عضویت اجباری + تأیید شماره. اگر کاربر آماده نباشد صفحه‌ی مناسب را نشان می‌دهد."""
    db.ensure_user(ctx.uid)
    ok, statuses = await force_subscribe.check(ctx.bot, ctx.uid, force=force)
    if not ok:
        text, markup = force_subscribe.join_page(statuses, user_auth.display_name(ctx))
        await ctx.show(text, markup, parse_mode=None)
        return False
    if not db.is_verified(ctx.uid):
        await user_auth.ask_phone(ctx)
        return False
    return True


async def user_entry(ctx: Ctx) -> None:
    await ctx.answer()
    if await gate(ctx):
        await user_auth.show_menu(ctx)


# ============================================================================
# سیگنال
# ============================================================================
async def user_signal(ctx: Ctx) -> None:
    """دریافت سیگنال جدید توسط کاربر: اسکن با تنظیمات مستقل همان کاربر و ارسال فقط برای خودش."""
    if is_admin(ctx.uid):
        await admin_signal(ctx)
        return
    uid = ctx.uid

    if not engine.bot_on():
        await _notice(ctx, BOT_OFF_TEXT)
        return

    if uid in _scan_busy:
        await _notice(ctx, SCANNING_TEXT)
        return

    if rate_wait(uid) > 0:
        await _notice(ctx, WAIT_TEXT)
        return

    now = time.monotonic()
    if config.USER_SCAN_COOLDOWN - (now - _cooldown.get(uid, -1e9)) > 0:
        await _notice(ctx, WAIT_TEXT)
        return

    _scan_busy.add(uid)         # قبل از اولین await، تا دو درخواست هم‌زمان هر دو رد نشوند
    try:
        await ctx.answer()
        if not await gate(ctx):
            return
        if not subscription.signal_allowed(uid):
            await ctx.show(*subscription.need_sub_page(uid))
            return

        _cooldown[uid] = now
        _purge(_cooldown)

        status = await ctx.send(SCANNING_TEXT, menu_markup_for(uid), parse_mode=None)
        res: ScanResult = await engine.find_signal(record=False, uid=uid)

        if res.signal is not None:
            text, base = build_user_signal_text(res.signal)
            img_buf = await asyncio.to_thread(generate_signal_image, res.signal)
            
            # ارسال عکس به همراه متن به کاربر
            mid = await broadcaster.send_photo_html(ctx.bot, uid, img_buf, text, signal_markup(uid))
            
            if mid:
                # track_live(res.signal, base, [(uid, mid)])  <--- حذف شد چون کپشن عکس لایو نمیشه
                engine.record(res.signal, uid)
                db.user(uid).set("last_broadcast_ts", str(time.time()))
                user_panel.reset_schedule(uid)
            else:
                await _finish_message(ctx, status, ERROR_TEXT, html=False, markup=result_markup(uid))
            return
        if res.reason == "bot_off":
            await _finish_message(ctx, status, BOT_OFF_TEXT, html=False, markup=result_markup(uid))
        elif res.reason == "no_template":
            await notify_admin_once(
                "no_template",
                f"{NO} قالب LONG و SHORT هنوز پر نشده و به همین دلیل سیگنالی ارسال نمی‌شود.\n"
                "از /settings > تنظیمات قالب، قالب‌ها را پر کنید.",
                cooldown=6 * 3600,
            )
            await _finish_message(ctx, status, NOT_READY_TEXT, html=False, markup=result_markup(uid))
        elif res.reason == "error":
            await _finish_message(ctx, status, ERROR_TEXT, html=False, markup=result_markup(uid))
        else:
            await _finish_message(ctx, status, NO_SIGNAL_TEXT, html=False, markup=result_markup(uid))
    finally:
        _scan_busy.discard(uid)

async def admin_signal(ctx: Ctx) -> None:
    """ارسال سیگنال فوری ادمین (با تنظیمات موتورهای ادمین) فقط به کانال تعیین‌شده."""
    await ctx.answer()
    ctx.user_data.pop("state", None)
    panel = kb([btn("پنل مدیریت", "a:hm")])
    
    if not engine.bot_on():
        await ctx.show(
            f"{NO} {BOT_OFF_TEXT}\nاز صفحه‌ی اصلی پنل آن را روشن کنید تا سیگنال صادر شود.", panel, parse_mode=None,
        )
        return
        
    if not db.get_bool("admin_sig_on"):
        await ctx.show(
            f"{NO} سوییچ «سیگنال کانال» خاموش است؛ تا زمانی که آن را روشن نکنید، سیگنالی به کانال ارسال نمی‌شود.\n"
            "از صفحه‌ی اصلی پنل آن را روشن کنید.",
            panel,
            parse_mode=None,
        )
        return

    if not db.get("ch_sig_id"):
        await ctx.show(
            f"{NO} کانال سیگنال تعیین نشده است؛ سیگنال ادمین فقط به کانال ارسال می‌شود.\n"
            "از «تنظیمات > ارسال سیگنال به کانال» کانال را تعیین کنید.",
            kb([btn("تنظیمات ارسال به کانال", "a:chs")], [btn("پنل مدیریت", "a:hm")]),
            parse_mode=None,
        )
        return
        
    status = await ctx.send("در حال اسکن بازار...", panel, parse_mode=None)
    res = await engine.find_signal(record=True)
    if res.signal is not None:
        delivery = await deliver_signal(ctx.bot, res.signal)
        msg = delivery.report(res.signal)
    elif res.reason == "bot_off":
        msg = f"{NO} {BOT_OFF_TEXT}"
    elif res.reason == "no_template":
        msg = f"{NO} قالب LONG و SHORT هنوز پر نشده است. از /settings > تنظیمات قالب آن‌ها را پر کنید."
    elif res.reason == "error":
        msg = f"{NO} ارتباط با صرافی برقرار نشد. {engine.last_error}".strip()
    else:
        msg = f"{NO} اکنون سیگنال معتبری پیدا نشد."
    await _finish_message(ctx, status, msg, html=False, markup=panel)

async def user_status(ctx: Ctx) -> None:
    await ctx.answer()
    if not await gate(ctx):
        return
    uid = ctx.uid
    u = db.get_user(uid)
    ok, statuses = await force_subscribe.check(ctx.bot, uid)
    lines = [
        "وضعیت حساب شما",
        "",
        f"شناسه: <code>{uid}</code>",
        f"شماره: {esc(u.phone_number) if u and u.phone_number else '-'}",
        f"تاریخ عضویت: {jalali_date(u.joined_at) if u else '-'}",
        f"ثبت‌نام: {OK} تأیید شده است",
    ]
    sub = subscription.status_lines(uid)
    if sub:
        lines += [""] + sub
    web_until = db.webapp_sub_until(uid)
    if web_until > 0:
        if web_until > time.time():
            lines.append(f"اشتراک تحلیل ساختاری: {OK} فعال تا {jalali_ts(web_until)}")
        else:
            lines.append(f"اشتراک تحلیل ساختاری: {NO} منقضی شده در {jalali_ts(web_until)}")
    lines.append("")
    if statuses:
        lines.append("عضویت در کانال‌ها:")
        for ch, joined in statuses:
            lines.append(f"{OK if joined else NO} {esc(ch.channel_title or ch.channel_id)}")
    else:
        lines.append("فعلاً کانال اجباری‌ای تعریف نشده است.")
    rows = []
    label = subscription.button_label(uid)
    if label:
        rows.append([subscription.sub_btn(label)])
    rows.append(back_row("u:menu"))
    await ctx.show("\n".join(lines), kb(*rows))


# ============================================================================
# هندلرها
# ============================================================================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx = Ctx(update, context)
    if is_admin(ctx.uid):
        await admin_panel.send_home(ctx)
        return
    if not await guard(ctx):
        return
    ctx.user_data.pop("state", None)
    ctx.user_data.pop("sub_tx", None)
    await user_entry(ctx)


async def cmd_signal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx = Ctx(update, context)
    if not await guard(ctx):
        return
    await user_signal(ctx)


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx = Ctx(update, context)
    if is_admin(ctx.uid):
        await admin_panel.send_home(ctx)
        return
    if not await guard(ctx):
        return
    await user_status(ctx)


def _admin_only(fn):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        ctx = Ctx(update, context)
        if not is_admin(ctx.uid):
            if await guard(ctx):
                await ctx.send(f"{NO} این بخش فقط برای ادمین است.", menu_markup_for(ctx.uid), parse_mode=None)
            return
        await fn(ctx)

    return wrapper


cmd_settings = _admin_only(admin_panel.cmd_settings)
cmd_channels = _admin_only(admin_panel.cmd_channels)


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx = Ctx(update, context)
    if is_admin(ctx.uid):
        if not await admin_panel.handle_text(ctx):
            await admin_panel.send_home(ctx)
        return
    if not await guard(ctx):
        return
    if await support.handle_user_text(ctx):          # حالت پشتیبانی: پیام هیچ‌وقت به‌عنوان دستور تفسیر نمی‌شود
        return
    if await analyst.handle_user_text(ctx):
        return
    if await subscription.handle_user_text(ctx):
        return
    if await user_panel.handle_user_text(ctx):
        return
    await user_entry(ctx)


async def on_media(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """عکس / فایل: ادمین برای پیام گروهی و Import، کاربر برای رسید تراکنش اشتراک."""
    ctx = Ctx(update, context)
    if is_admin(ctx.uid):
        if not await admin_panel.handle_media(ctx):
            await admin_panel.send_home(ctx)
        return
    if not await guard(ctx):
        return
    if await support.handle_user_media(ctx):
        return
    if await subscription.handle_user_media(ctx):
        return
    if await user_panel.handle_user_media(ctx):
        return
    await user_entry(ctx)


async def on_support_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    در حالت پشتیبانی هر پیامی که با / شروع شود (به‌جز /start) هم یک پیام پشتیبانی حساب می‌شود و
    به‌عنوان دستور ربات اجرا نمی‌شود. /start کاربر را از حالت پشتیبانی خارج می‌کند.
    """
    ctx = Ctx(update, context)
    if is_admin(ctx.uid) or not support.active(ctx):
        return
    msg = update.effective_message
    text = (msg.text or "").strip() if msg else ""
    if not text or text.split()[0].split("@")[0].lower() == "/start":
        return
    if await guard(ctx):
        await support.handle_user_text(ctx)
    raise ApplicationHandlerStop


async def on_contact(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx = Ctx(update, context)
    if is_admin(ctx.uid):
        return
    if not await guard(ctx):
        return
    await user_auth.handle_contact(ctx)


async def handle_user_callback(ctx: Ctx, parts) -> None:
    act = parts[0] if parts else ""
    uid = ctx.uid
    if act not in ("stx", "stxs", "trx", "supa", "wstx", "wstxs", "wtrx"):
        ctx.user_data.pop("state", None)
        ctx.user_data.pop("sub_tx", None)
        ctx.user_data.pop("web_sub_tx", None)

    if act == "noop":
        await ctx.answer()
    elif act == "chk":
        ok, statuses = await force_subscribe.check(ctx.bot, uid, force=True)
        if not ok:
            await ctx.answer(f"{NO} هنوز در همه‌ی کانال‌ها عضو نشده‌اید.", alert=True)
            text, markup = force_subscribe.join_page(statuses, user_auth.display_name(ctx))
            await ctx.show(text, markup, parse_mode=None)
            return
        await ctx.answer()
        db.ensure_user(uid)
        if not db.is_verified(uid):
            await ctx.show(f"عضویت شما تأیید شد {OK}", kb(back_row("u:menu")), parse_mode=None)
            await user_auth.ask_phone(ctx)
        else:
            await user_auth.show_menu(ctx)
    elif act == "sig":
        await user_signal(ctx)
    elif act == "st":
        await user_status(ctx)
    elif act == "menu":
        await ctx.answer()
        if await gate(ctx):
            await user_auth.show_menu(ctx)
    elif act == "mn":
        # دکمه‌ی «منوی اصلی» زیر سیگنال و اعلان‌ها: منو در پیام جدید می‌آید و پیام اصلی سر جاش می‌ماند
        await ctx.answer()
        if await gate(ctx):
            await user_auth.send_menu(ctx)
    elif act == "sub":
        await ctx.answer()
        if await gate(ctx):
            await ctx.show(*subscription.choice_page(uid))
    elif act == "subbot":
        await ctx.answer()
        if await gate(ctx):
            ctx.user_data["sub_flow"] = {"plan": plans.PLAN_BOT, "network": ""}
            await ctx.show(*subscription.purchase_page(uid, plans.PLAN_BOT))
    elif act == "wsub":
        await ctx.answer()
        if await gate(ctx):
            ctx.user_data["sub_flow"] = {"plan": plans.PLAN_WEBAPP, "network": ""}
            await ctx.show(*subscription.purchase_page(uid, plans.PLAN_WEBAPP))
    elif act == "subp":
        await ctx.answer()
        if await gate(ctx):
            plan = plans.normalize_plan(parts[1] if len(parts) > 1 else plans.PLAN_BOT)
            await ctx.show(*subscription.purchase_page(uid, plan))
    elif act == "subn":
        if await gate(ctx):
            plan = plans.normalize_plan(parts[1] if len(parts) > 1 else plans.PLAN_BOT)
            key = parts[2] if len(parts) > 2 else ""
            await subscription.select_network(ctx, plan, key)
    elif act == "subc":
        if await gate(ctx):
            plan = plans.normalize_plan(parts[1] if len(parts) > 1 else plans.PLAN_BOT)
            cid = parts[2] if len(parts) > 2 else ""
            await subscription.select_card(ctx, plan, cid)
    elif act == "free":
        if await gate(ctx):
            plan = plans.normalize_plan(parts[1] if len(parts) > 1 else plans.PLAN_BOT)
            await subscription.claim_free(ctx, plan)
    elif act == "web":
        await ctx.answer()
        if await gate(ctx):
            if db.has_active_webapp_sub(uid):
                from webapp.auth import login_url, public_site
                until = db.webapp_sub_until(uid)
                text = (
                    "ورود به وب‌اپ تحلیل ساختاری\n\n"
                    f"اشتراک شما فعال است تا {jalali_ts(until)}.\n"
                    "برای ورود، روی دکمه زیر بزنید و رمز عبور خود را وارد نمایید.\n\n"
                    f"آدرس سایت: <code>{public_site()}</code>"
                )
                await ctx.send(
                    text,
                    kb([btn("ورود به وب‌اپ تحلیل ساختاری", url=login_url())], [btn("منوی اصلی", "u:mn")]),
                )
            else:
                text = "برای دسترسی به این بخش، ابتدا باید اشتراک تحلیل ساختاری را تهیه کنید."
                if plans.configured(plans.PLAN_WEBAPP):
                    await ctx.show(
                        text,
                        kb([btn(subscription.BTN_BUY, "u:wsub")], [btn("منوی اصلی", "u:mn")]),
                    )
                elif plans.free_on(plans.PLAN_WEBAPP) and plans.free_claim(uid, plans.PLAN_WEBAPP) is None:
                    await ctx.show(
                        text,
                        kb([btn(f"🎁 {plans.free_days(plans.PLAN_WEBAPP)} روز رایگان", "u:free:webapp")],
                           [btn("منوی اصلی", "u:mn")]),
                    )
                else:
                    await ctx.show(text, kb([btn("منوی اصلی", "u:mn")]))
    elif act == "stx":
        await ctx.answer()
        if await gate(ctx):
            await subscription.start_tx(ctx)
    elif act == "stxs":
        if await gate(ctx):
            await subscription.submit_tx(ctx)
    elif act == "trx":
        await ctx.answer()
        if await gate(ctx):
            await subscription.start_bsc_tx(ctx)
    elif act == "sup":
        await ctx.answer()
        if await gate(ctx):
            await support.start(ctx)
    elif act == "supx":
        await ctx.answer()
        if await gate(ctx):
            await support.close(ctx)
    elif act == "supa":
        if await gate(ctx):
            await support.escalate(ctx)
    elif act in ("ana", "ana2"):
        await ctx.answer()
        if await gate(ctx):
            await analyst.start(ctx, new_message=(act == "ana2"))
    elif act in ("anam", "anam2"):
        await ctx.answer()
        if await gate(ctx):
            await analyst.start_multi(ctx, new_message=(act == "anam2"))
    elif act == "set":
        await ctx.answer()
        if await gate(ctx):
            if not subscription.settings_allowed(uid):
                await ctx.show(*subscription.need_sub_page(uid, user_panel.SUB_TEXT))
            else:
                await user_panel.open_home(ctx)
    elif act == "hl":
        await ctx.answer()
        await ctx.show(*user_help.page_index())
    elif act == "hlt":
        await ctx.answer()
        await ctx.show(*user_help.page_section(parts[1] if len(parts) > 1 else ""))            
    else:
        await ctx.answer()


async def handle_settings_callback(ctx: Ctx, parts) -> None:
    """پنل تنظیمات مستقل کاربر: فقط ثبت‌نام‌شده‌ی دارای اشتراک فعال."""
    ctx.user_data.pop("sub_tx", None)
    if not await gate(ctx):
        return
    if not subscription.settings_allowed(ctx.uid):
        await ctx.answer()
        ctx.user_data.pop("state", None)
        await ctx.show(*subscription.need_sub_page(ctx.uid, user_panel.SUB_TEXT))
        return
    await user_panel.handle_callback(ctx, parts)


async def _fast_answer(ctx: Ctx) -> None:
    """اگر هندلر تا ۰٫۲ ثانیه پاسخی به callback نداده باشد، فوراً پاسخ خالی ارسال می‌شود."""
    try:
        await asyncio.sleep(0.2)
        await ctx.answer()
    except asyncio.CancelledError:
        pass


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if q is None:
        return
    ctx = Ctx(update, context)
    data = q.data or ""
    fast = asyncio.create_task(_fast_answer(ctx))   # اسپینر دکمه حداکثر ۰٫۲ ثانیه بعد برداشته می‌شود
    try:
        if not await guard(ctx):
            return
        if data.startswith("a:"):
            if not is_admin(ctx.uid):
                await ctx.answer(f"{NO} این بخش فقط برای ادمین است.", alert=True)
                return
            await admin_panel.handle_callback(ctx, data.split(":")[1:])
        elif data.startswith("u:"):
            if is_admin(ctx.uid) and data in ("u:mn", "u:menu"):
                await ctx.answer()
                await admin_panel.send_home(ctx)
                return
            await handle_user_callback(ctx, data.split(":")[1:])
        elif data.startswith("s:"):
            if is_admin(ctx.uid):
                await ctx.answer()
                await admin_panel.send_home(ctx)
                return
            await handle_settings_callback(ctx, data.split(":")[1:])
    finally:
        fast.cancel()
        await ctx.answer()  # شبکه‌ی اطمینان: اسپینر دکمه همیشه برداشته می‌شود


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    err = context.error
    if isinstance(err, (NetworkError, RetryAfter)):
        log.warning("network issue: %s", err)
        return
    log.error("unhandled error", exc_info=err)
    if isinstance(update, Update):
        try:
            text = f"{NO} یک مشکلی پیش آمد، لطفاً دوباره امتحان کنید."
            if update.callback_query:
                await update.callback_query.answer(text)
            elif update.effective_chat:
                uid = update.effective_user.id if update.effective_user else update.effective_chat.id
                await context.bot.send_message(
                    chat_id=update.effective_chat.id, text=text, reply_markup=menu_markup_for(uid)
                )
        except TelegramError:
            pass


# ============================================================================
# حلقه‌ی ارسال خودکار
# ============================================================================
async def auto_signal_loop(app: Application) -> None:
    """حلقه‌ی ارسال خودکار ادمین: سیگنال با تنظیمات ادمین ساخته می‌شود و فقط به کانال می‌رود."""
    bot = app.bot
    await asyncio.sleep(8)
    while True:
        try:
            if not engine.bot_on():
                await asyncio.sleep(20)
                continue
                
            # اگر سیگنال ادمین خاموش باشد، اسکن نمیکند و ارسال نمیشود
            if not db.get_bool("admin_sig_on"):
                await asyncio.sleep(20)
                continue

            if is_flooded():
                await asyncio.sleep(min(max(flood_remaining(), 1.0), 60.0))
                continue
            interval = max(1, db.get_int("send_interval")) * 60
            wait = db.get_float("last_broadcast_ts") + interval - time.time()
            if wait > 0:
                await asyncio.sleep(min(wait, 30.0))
                continue
            if not db.get("ch_sig_id"):
                await warn_no_channel()
                await asyncio.sleep(60)
                continue

            res = await engine.find_signal(record=True)
            if res.signal is not None:
                d = await deliver_signal(bot, res.signal)
                log.info("signal %s %s | channel=%s", res.signal.symbol, res.signal.side, d.channel)
                continue
            if res.reason == "no_template":
                await notify_admin_once(
                    "no_template",
                    f"{NO} قالب LONG و SHORT هنوز پر نشده و به همین دلیل سیگنالی ارسال نمی‌شود.\n"
                    "از /settings > تنظیمات قالب، قالب‌ها را پر کنید.",
                    cooldown=6 * 3600,
                )
            elif res.reason == "error":
                detail = " ".join((engine.last_error or "").split())[:300]
                await notify_admin_once("exchange_error", f"{NO} ارتباط با صرافی برقرار نیست. {detail}".strip())
            await asyncio.sleep(config.SCAN_RETRY_SECONDS)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("auto signal loop error")
            await asyncio.sleep(30)


# ============================================================================
# حلقه‌ی ارسال خودکار کاربران (هر کاربر با تنظیمات و فاصله‌ی ارسال خودش)
# ============================================================================
USER_LOOP_TICK = 5          # ثانیه
USER_BATCH = 150            # حداکثر کاربری که در هر دور وارد صف اسکن می‌شوند
USER_IDS_REFRESH = 60       # ثانیه


def _eligible_users() -> List[int]:
    """کاربران مجاز دریافت سیگنال: ثبت‌نام‌شده‌ها (با اشتراک فعال وقتی خرید اشتراک فعال است)."""
    return [u for u in db.user_ids_by("ver") if u != config.ADMIN_UID and subscription.signal_allowed(u)]


def _initial_next(uid: int, now: float) -> float:
    d = db.user(uid)
    interval = max(1, d.get_int("send_interval")) * 60
    due = d.get_float("last_broadcast_ts") + interval
    if due > now:
        return due
    return now + random.uniform(3.0, 3.0 + min(float(interval), 240.0))     # پخش شدن اسکن‌ها بعد از راه‌اندازی


async def _notify_model_reset(bot, uid: int, old: str, new: str) -> None:
    await broadcaster.send_plain(
        bot, uid,
        f"مدل هوش مصنوعی انتخابی شما ({old}) دیگر در لیست مدل‌های مجاز نیست؛ مدل شما به {new} تغییر کرد.",
        menu_markup_for(uid),
    )


async def _auto_user_scan(bot, uid: int) -> None:
    """یک اسکن خودکار برای یک کاربر؛ خطای یک کاربر روی دیگران اثر ندارد."""
    now = time.time()
    d = db.user(uid)
    interval = max(1, d.get_int("send_interval")) * 60
    nxt = now + interval
    _scan_busy.add(uid)
    try:
        old = d.fix_model()
        if old:
            await _notify_model_reset(bot, uid, old, d.get("ai_model"))
        ok, _ = await force_subscribe.check(bot, uid)
        if not ok:
            nxt = now + 300
            return
        res = await engine.find_signal(record=False, uid=uid)
        if res.signal is not None:
            text, base = build_user_signal_text(res.signal)
            img_buf = await asyncio.to_thread(generate_signal_image, res.signal)
            mid = await broadcaster.send_photo_html(bot, uid, img_buf, text, signal_markup(uid))
            if mid:
                track_live(res.signal, base, [(uid, mid)])
                engine.record(res.signal, uid)
                d.set("last_broadcast_ts", str(time.time()))
            else:
                nxt = now + max(interval, 1800)        # ارسال نشد (مثلاً ربات را مسدود کرده است)
        elif res.reason == "error":
            nxt = now + config.SCAN_RETRY_SECONDS
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception("auto user scan failed for %s", uid)
        nxt = now + 120
    finally:
        _scan_busy.discard(uid)
        user_panel.user_next[uid] = nxt


async def user_signal_loop(app: Application) -> None:
    """برای هر کاربر یک زمان‌بندی مستقل؛ اسکن‌ها با سقف هم‌زمانی در صف انجام می‌شوند."""
    bot = app.bot
    await asyncio.sleep(25)
    ids: List[int] = []
    refreshed = 0.0
    while True:
        try:
            await asyncio.sleep(USER_LOOP_TICK)
            if not engine.bot_on() or is_flooded() or not engine.sides_ready():
                continue
            now = time.time()
            if now - refreshed > USER_IDS_REFRESH:
                ids = _eligible_users()
                refreshed = now
                alive = set(ids)
                for u in [u for u in user_panel.user_next if u not in alive]:
                    user_panel.user_next.pop(u, None)
            due: List[Tuple[float, int]] = []
            for uid in ids:
                if uid in _scan_busy:
                    continue
                nxt = user_panel.user_next.get(uid)
                if nxt is None:
                    nxt = user_panel.user_next[uid] = _initial_next(uid, now)
                if nxt <= now:
                    due.append((nxt, uid))
            if not due:
                continue
            due.sort()
            batch = [u for _n, u in due[:USER_BATCH]]
            _scan_busy.update(batch)
            await asyncio.gather(*(_auto_user_scan(bot, u) for u in batch), return_exceptions=True)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("user signal loop error")
            await asyncio.sleep(30)


# ============================================================================
# هشدار قبل از سیگنال
# ============================================================================
def pre_alert_text(c: Candidate) -> str:
    return (
        "آماده‌باش! احتمال سیگنال جدید.\n"
        f"نماد: {esc(c.base)}\n"
        f"جهت احتمالی: {esc(c.side)}"
    )


def pre_alert_markup(uid: int):
    if is_admin(uid):
        return kb([home_btn(uid)])
    return kb([btn("دریافت سیگنال جدید", "u:sig")], [home_btn(uid)])


def pre_alert_recipients() -> List[int]:
    """هشدار آماده‌باش برای ادمین و کاربرانی که امکان «هشدار قبل از سیگنال» دارند."""
    ids = [config.ADMIN_UID]
    for uid in db.user_ids_by("ver"):
        if uid != config.ADMIN_UID and plans.bot_feature_allowed(uid, "pre"):
            ids.append(uid)
    return ids


def pick_pre_alert(now: Optional[float] = None) -> Optional[Candidate]:
    """بهترین کاندیدای آماده‌باش که شرایط ارسال هشدار را دارد (امتیاز بالا، تکراری نبودن، فاصله از هشدار قبلی)."""
    global _pre_last
    now = time.time() if now is None else now
    
    # اگر سیگنال ادمین خاموش باشد، هیچ هشداری صادر نمی‌شود
    if not db.get_bool("admin_sig_on"):
        return None
        
    if not db.get_bool("pre_alert_on") or not engine.bot_on():
        return None
    if now - _pre_last < db.get_int("pre_gap") * 60:
        return None
    cooldown = db.get_int("pre_cooldown") * 60
    min_score = db.get_float("pre_min_score")
    for k in [k for k, t in _pre_sent.items() if now - t > cooldown]:
        _pre_sent.pop(k, None)
    for c in engine.standby_candidates():       # به ترتیب امتیاز، نزولی
        if c.score < min_score:
            break
        if f"{c.pair}:{c.side}" in _pre_sent:
            continue
        return c
    return None


async def send_pre_alert(bot, c: Candidate) -> int:
    global _pre_last
    now = time.time()
    _pre_last = now
    _pre_sent[f"{c.pair}:{c.side}"] = now
    # بعد از هشدار، همین نماد زیر نظر می‌ماند و به‌محض کامل شدن شرایط، سیگنالش در کانال منتشر می‌شود
    _pre_watch[f"{c.pair}:{c.side}"] = (c.pair, c.side, now + config.PRE_WATCH_MINUTES * 60)
    sent, total, _ = await _send_many(bot, pre_alert_recipients(), pre_alert_text(c), pre_alert_markup)
    log.info("pre-alert %s %s (score %.0f) -> %d/%d users", c.base, c.side, c.score, sent, total)
    return sent


async def process_pre_watch(bot) -> Optional[Signal]:
    """نمادهای هشدارگرفته را بررسی می‌کند؛ هرکدام سیگنال کامل شد در کانال منتشر می‌شود."""
    now = time.time()
    for k in [k for k, (_p, _s, until) in _pre_watch.items() if until <= now]:
        _pre_watch.pop(k, None)
        
    # اگر سیگنال ادمین خاموش باشد، سیگنال کامل‌شده هم منتشر نمی‌شود
    if not _pre_watch or not engine.bot_on() or is_flooded() or not db.get_bool("admin_sig_on"):
        return None
    if not db.get("ch_sig_id"):
        await warn_no_channel()
        return None
    wanted = {(p, s) for p, s, _u in _pre_watch.values()}
    res = await engine.find_watched(wanted, record=True)
    if res.signal is None:
        return None
    d = await deliver_signal(bot, res.signal)
    log.info("watched signal %s %s | channel=%s", res.signal.symbol, res.signal.side, d.channel)
    return res.signal


async def pre_alert_loop(app: Application) -> None:
    await asyncio.sleep(30)
    while True:
        try:
            await asyncio.sleep(15)
            if is_flooded():
                continue
            c = pick_pre_alert()
            if c is not None:
                await send_pre_alert(app.bot, c)
            await process_pre_watch(app.bot)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("pre alert loop error")
            await asyncio.sleep(30)


# ============================================================================
# پایان اشتراک
# ============================================================================
async def process_expired(bot) -> int:
    """به کاربرهایی که اشتراک آن‌ها به پایان رسیده است یک بار اطلاع می‌دهد. خروجی: تعداد کاربران پردازش‌شده."""
    items = db.expired_unnotified()
    notify = subscription.enabled()
    for uid, until in items:
        if notify:
            try:
                await subscription.notify_expired(bot, uid)
            except Exception:
                log.debug("expiry notice failed for %s", uid, exc_info=True)
            await asyncio.sleep(0.05)
        db.mark_sub_notified(uid, until)
    return len(items)


async def sub_expiry_loop(app: Application) -> None:
    await asyncio.sleep(20)
    while True:
        try:
            if not is_flooded():
                await process_expired(app.bot)
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("subscription expiry loop error")
            await asyncio.sleep(60)


# ============================================================================
# ثبت هندلرها
# ============================================================================
def register_handlers(app: Application) -> None:
    private = filters.ChatType.PRIVATE
    app.add_handler(CommandHandler("start", cmd_start, filters=private))
    app.add_handler(CommandHandler("signal", cmd_signal, filters=private))
    app.add_handler(CommandHandler("status", cmd_status, filters=private))
    app.add_handler(CommandHandler("settings", cmd_settings, filters=private))
    app.add_handler(CommandHandler("channels", cmd_channels, filters=private))
    app.add_handler(MessageHandler(private & filters.COMMAND, on_support_command), group=-1)
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(private & filters.CONTACT, on_contact))
    app.add_handler(MessageHandler(private & (filters.PHOTO | filters.Document.ALL), on_media))
    app.add_handler(MessageHandler(private & filters.TEXT & ~filters.COMMAND, on_text))
    app.add_error_handler(on_error)
    force_subscribe.problem_hook = on_channel_problem
    ai.alert_hook = on_ai_alert
    price_broadcaster.alert_hook = on_price_alert
