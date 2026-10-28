"""آبان ترید: تأیید شماره کاربر با دکمه Request Contact + منوی اصلی کاربر + تنظیمات کاربر."""
from __future__ import annotations

import logging
import re

from telegram import KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove

import config
import force_subscribe
import market_overview
import plans
import subscription
from database import db
from ui import NO, OK, STYLE_GREEN, Ctx, back_row, btn, esc, kb, on_off

log = logging.getLogger("auth")

PHONE_TEXT = (
    "عضویت شما تأیید شد " + OK + "\n"
    "برای تکمیل ثبت‌نام، لطفاً شماره‌ی خود را با دکمه‌ی پایین برای ما ارسال کنید."
)
PHONE_BUTTON = "اشتراک شماره موبایل"


def contact_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [[KeyboardButton(PHONE_BUTTON, request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def display_name(ctx: Ctx) -> str:
    """نام اکانت تلگرام کاربر (نام، وگرنه نام کاربری)."""
    u = ctx.update.effective_user
    if u is None:
        return ""
    name = " ".join(x for x in ((u.first_name or "").strip(), (u.last_name or "").strip()) if x)
    return name or (f"@{u.username}" if getattr(u, "username", None) else "")


def menu_markup(uid: int):
    """منوی کاربر؛ دکمه‌های خرید/تمدید و امکانات غیرمجاز اشتراک رایگان نمایش داده نمی‌شوند."""
    rows = [
        [btn("دریافت سیگنال جدید", "u:sig")],
    ]
    if plans.bot_feature_allowed(uid, "analysis"):
        rows.append([btn("آنالیز با AI", "u:ana", style="danger")])
    rows += [
        [btn("ورود به وب‌اپ تحلیل ساختاری", "u:web", style="primary")],
        [btn("وضعیت حساب", "u:st")],
    ]
    label = subscription.button_label(uid)
    if label:
        rows.append([subscription.sub_btn(label)])
    if plans.bot_feature_allowed(uid, "settings"):
        rows.append([btn("تنظیمات", "u:set", style=STYLE_GREEN)])
    # دکمه‌های پشتیبانی و راهنما اینجا آبی (primary) شدند
    rows.append([
        btn("پشتیبانی", "u:sup", style="primary"), 
        btn("راهنما", "u:hl", style="primary")
    ])
    return kb(*rows)


def menu_text(name: str) -> str:
    hello = (
        f"سلام {esc(name)}، به {config.BRAND} خوش آمدید!" if name else f"سلام، به {config.BRAND} خوش آمدید!"
    )
    text = (
        f"{hello}\n"
        "برای دریافت سیگنال جدید، دکمه‌ی «دریافت سیگنال جدید» را بزنید تا بازار برای شما بررسی شود."
    )
    try:
        market = market_overview.line()     # فقط خواندن از حافظه؛ هیچ محاسبه‌ی سنگینی ندارد
    except Exception:
        market = ""
    return f"{text}\n\n{market}" if market else text


async def show_menu(ctx: Ctx, note: str = "") -> None:
    """منوی اصلی (همیشه پیام جدید ارسال می‌شود)."""
    prefix = f"{note}\n\n" if note else ""
    await ctx.show(prefix + menu_text(display_name(ctx)), menu_markup(ctx.uid))


async def send_menu(ctx: Ctx, note: str = "") -> None:
    """منوی اصلی در یک پیام جدید (برای دکمه‌ی منو زیر سیگنال و اعلان‌ها)."""
    prefix = f"{note}\n\n" if note else ""
    await ctx.send(prefix + menu_text(display_name(ctx)), menu_markup(ctx.uid))


async def ask_phone(ctx: Ctx) -> None:
    """درخواست شماره (کیبورد ریپلای باید در یک پیام جدید ارسال شود)."""
    await ctx.send(PHONE_TEXT, contact_keyboard(), parse_mode=None)


async def _drop_reply_keyboard(ctx: Ctx) -> None:
    """کیبورد شماره را برمی‌دارد؛ برای این کار یک پیام تأیید ارسال می‌شود."""
    await ctx.send("شماره‌ی شما ثبت شد.", ReplyKeyboardRemove(), parse_mode=None)


def normalize_phone(raw: str) -> str:
    digits = re.sub(r"\D", "", raw or "")
    return f"+{digits}" if digits else ""


async def handle_contact(ctx: Ctx) -> None:
    msg = ctx.update.effective_message
    contact = msg.contact if msg else None
    if contact is None:
        return
    uid = ctx.uid
    db.ensure_user(uid)

    # شماره فقط یک بار ثبت می‌شود
    if db.is_verified(uid):
        await _drop_reply_keyboard(ctx)
        await send_menu(ctx, f"{OK} شماره‌ی شما قبلاً ثبت شده است.")
        return

    # فقط شماره‌ی خود کاربر پذیرفته می‌شود
    if contact.user_id != uid:
        await ctx.send(
            NO + " این شماره متعلق به شما نیست. لطفاً فقط با دکمه‌ی پایین، شماره‌ی خودتان را ارسال کنید.",
            contact_keyboard(),
            parse_mode=None,
        )
        return

    ok, statuses = await force_subscribe.check(ctx.bot, uid, force=True)
    if not ok:
        text, markup = force_subscribe.join_page(statuses, display_name(ctx))
        await ctx.send(text, markup, parse_mode=None)
        return

    phone = normalize_phone(contact.phone_number)
    if not phone:
        await ctx.send(NO + " شماره درست دریافت نشد، دوباره امتحان کنید.", contact_keyboard(), parse_mode=None)
        return

    db.verify_user(uid, phone)
    await _drop_reply_keyboard(ctx)
    await send_menu(ctx, f"{OK} ثبت‌نام با موفقیت انجام شد.")
