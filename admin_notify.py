"""اعلان رویدادهای مهم به ادمین ربات (همراه دکمه‌ی ورود به پنل مدیریت وب‌اپ)."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, Optional

import config

log = logging.getLogger("admin_notify")

_recent: Dict[str, float] = {}
_DEDUPE_MAX = 500


def _cleanup(now: float) -> None:
    if len(_recent) <= _DEDUPE_MAX:
        return
    for key, ts in list(_recent.items()):
        if now - ts > 86400:
            _recent.pop(key, None)


def admin_markup(extra_rows=None):
    from ui import btn, kb
    from webapp.activation import admin_panel_url

    rows = [[btn("ورود به پنل مدیریت وب‌اپ", url=admin_panel_url(), style="primary")]]
    if extra_rows:
        rows.extend(extra_rows)
    return kb(*rows)


async def notify(
    bot,
    text: str,
    *,
    kind: str = "",
    dedupe_key: str = "",
    cooldown: float = 0.0,
    extra_rows=None,
) -> bool:
    """ارسال اعلان HTML به ادمین. dedupe_key/cooldown برای جلوگیری از ارسال تکراری است."""
    if bot is None:
        return False
    key = dedupe_key or kind
    now = time.time()
    if key and cooldown > 0:
        last = _recent.get(key, 0.0)
        if now - last < cooldown:
            return False
    try:
        import broadcaster

        mid = await broadcaster.send_html(
            bot, int(config.ADMIN_UID), text, admin_markup(extra_rows), priority=1
        )
        if mid is not None:
            if key:
                _recent[key] = now
                _cleanup(now)
            return True
        return False
    except Exception:
        log.exception("admin notify failed")
        return False


def fire(text: str, *, kind: str = "", dedupe_key: str = "", cooldown: float = 0.0, extra_rows=None) -> None:
    """نسخه‌ی غیرهمگام برای فراخوانی از کدهای همگام (API وب‌اپ)."""
    try:
        from webapp.runtime import runtime

        bot = runtime.bot
        if bot is None:
            return
        loop = asyncio.get_running_loop()
        loop.create_task(
            notify(bot, text, kind=kind, dedupe_key=dedupe_key, cooldown=cooldown, extra_rows=extra_rows),
            name="admin-notify",
        )
    except RuntimeError:
        log.debug("no running loop for admin notify")
    except Exception:
        log.exception("admin notify schedule failed")


def log_event(user_id: int, kind: str, detail: str = "", ip: str = "", severity: str = "info") -> None:
    """ثبت رویداد امنیتی وب‌اپ (مستقل از لاگ‌های ربات). severity: info | warning | critical"""
    try:
        from database import db

        level = severity if severity in ("info", "warning", "critical") else "info"
        db.x(
            "INSERT INTO security_events(user_id, kind, detail, ip, severity) VALUES(?, ?, ?, ?, ?)",
            (
                int(user_id or 0), str(kind or "event")[:60], str(detail or "")[:1000],
                str(ip or "")[:64], level,
            ),
        )
    except Exception:
        log.exception("security event log failed")
