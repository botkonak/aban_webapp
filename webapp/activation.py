"""فعال‌سازی اشتراک وب‌اپ، صدور رمز یکتا و پیام جامع راهنما (مشترک بین ربات و پنل ادمین)."""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional, Tuple

import config
from database import db
from webapp.auth import admin_link_token, login_url, public_site
from webapp.jalali import jalali_dt
from webapp.repo import repo

log = logging.getLogger("webapp.activation")


def ensure_access(uid: int) -> None:
    repo.ensure_webapp_user(uid)
    repo.set_access(uid, True)


def issue_password(uid: int, password: Optional[str] = None) -> str:
    ensure_access(uid)
    return repo.set_password(uid, password)


def activation_text(uid: int, password: str) -> str:
    until = db.webapp_sub_until(uid)
    until_text = jalali_dt(until) if until else "-"
    site = public_site()
    username = repo.ensure_username(int(uid))
    return (
        "اشتراک تحلیل ساختاری شما با موفقیت فعال شد.\n\n"
        f"نام کاربری: <code>{username}</code>\n"
        f"رمز عبور یکتا: <code>{password}</code>\n"
        f"پایان اشتراک: {until_text}\n\n"
        "راهنمای ورود:\n"
        "برای ورود، روی دکمه «ورود به وب‌اپ تحلیل ساختاری» بزنید یا آدرس زیر را در مرورگر گوشی یا لپ‌تاپ باز کنید "
        "و نام کاربری و رمز عبور بالا را وارد نمایید.\n\n"
        f"آدرس سایت: <code>{site}</code>\n\n"
        "این اطلاعات مخصوص شماست؛ آن را در اختیار دیگران قرار ندهید. "
        "در صورت فراموشی رمز، از دکمه «ورود با کد یکبار مصرف» (در صورت فعال بودن) استفاده کنید یا با پشتیبانی در ارتباط باشید."
    )


def access_text(uid: int) -> str:
    """پیام فعال‌سازی برای کاربری که از قبل رمز عبور دارد (رمز تغییر نمی‌کند)."""
    until = db.webapp_sub_until(uid)
    until_text = jalali_dt(until) if until else "-"
    site = public_site()
    username = repo.ensure_username(int(uid))
    return (
        "اشتراک تحلیل ساختاری شما با موفقیت فعال شد.\n\n"
        f"نام کاربری: <code>{username}</code>\n"
        f"پایان اشتراک: {until_text}\n\n"
        "با همان رمز عبور قبلی خود وارد وب‌اپ شوید. اگر رمز را فراموش کرده‌اید، "
        "از دکمه «ورود با کد یکبار مصرف» (در صورت فعال بودن) استفاده کنید یا با پشتیبانی در ارتباط باشید.\n\n"
        f"آدرس سایت: <code>{site}</code>"
    )


def activation_markup() -> Any:
    from ui import btn, kb

    return kb(
        [btn("ورود به وب‌اپ تحلیل ساختاری", url=login_url())],
        [btn("منوی اصلی", "u:mn")],
    )


async def send_activation(bot, uid: int, password: str) -> bool:
    if bot is None:
        return False
    try:
        import broadcaster

        text = activation_text(int(uid), password) if password else access_text(int(uid))
        mid = await broadcaster.send_html(bot, int(uid), text, activation_markup())
        return mid is not None
    except Exception as exc:
        log.warning("activation message to %s failed: %s", uid, exc)
        return False


async def activate(uid: int, days: int, bot=None, password: Optional[str] = None) -> Dict[str, Any]:
    """تمدید اشتراک وب‌اپ + صدور رمز (فقط اگر کاربر رمزی ندارد) + ارسال پیام جامع."""
    until = db.grant_webapp_sub(int(uid), max(1, int(days)))
    had_password = repo.has_password(int(uid))
    code = "" if had_password and not password else issue_password(int(uid), password)
    sent = await send_activation(bot, uid, code)
    if code:
        message = (
            "اشتراک فعال و رمز صادر شد و پیام برای کاربر ارسال شد."
            if sent else
            "اشتراک فعال و رمز صادر شد، اما ارسال پیام به کاربر ممکن نبود (ربات را مسدود کرده یا دسترسی ندارد)."
        )
    else:
        message = (
            "اشتراک فعال شد و پیام برای کاربر ارسال شد."
            if sent else
            "اشتراک فعال شد، اما ارسال پیام به کاربر ممکن نبود (ربات را مسدود کرده یا دسترسی ندارد)."
        )
    return {
        "uid": int(uid),
        "password": code,
        "until": until,
        "until_jalali": jalali_dt(until),
        "sent": sent,
        "message": message,
    }


def admin_panel_url() -> str:
    base = config.WEBAPP_URL.rstrip("/")
    return f"{base}/admin?token={admin_link_token(config.ADMIN_UID)}"


def admin_panel_markup() -> Any:
    from ui import btn, kb

    return kb([btn("ورود به پنل مدیریت وب‌اپ", url=admin_panel_url())])
