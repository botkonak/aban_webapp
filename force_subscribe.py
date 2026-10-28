"""عضویت اجباری: بررسی عضویت کاربر در کانال‌های فعال با getChatMember."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Awaitable, Callable, Dict, List, Optional, Tuple, Union

from telegram.error import BadRequest, Forbidden, RetryAfter, TelegramError

import config
from database import db
from models import Channel
from ui import NO, OK, btn, kb, retry_seconds

log = logging.getLogger("subscribe")

# uid -> زمان انقضای کش (فقط نتیجه «عضو همه کانال‌ها»)
_cache: Dict[int, float] = {}
# مشکل‌های پیکربندی کانال (مثلاً ربات ادمین نیست) برای اطلاع به ادمین
problem_hook: Optional[Callable[[Channel, str], Awaitable[None]]] = None


def join_text(name: str = "") -> str:
    """متن صفحه‌ی عضویت اجباری؛ به‌جای «رفیق» از نام اکانت تلگرام کاربر استفاده می‌شود."""
    name = (name or "").strip()
    hello = f"سلام {name}، به {config.BRAND} خوش آمدید!" if name else f"سلام، به {config.BRAND} خوش آمدید!"
    return (
        f"{hello}\n"
        "برای استفاده از ربات، لطفاً در کانال‌های زیر عضو شوید:\n\n"
        "بعد از عضویت، دکمه «بررسی مجدد» را بزنید."
    )


def clear_cache(uid: Optional[int] = None) -> None:
    if uid is None:
        _cache.clear()
    else:
        _cache.pop(uid, None)


def chat_ref(channel_id: str) -> Union[int, str]:
    s = str(channel_id).strip()
    if s.startswith("@"):
        return s
    try:
        return int(s)
    except ValueError:
        return s


async def _report(ch: Channel, err: str) -> None:
    hook = problem_hook
    if hook is not None:
        try:
            await hook(ch, err)
        except Exception:  # pragma: no cover
            log.debug("problem hook failed", exc_info=True)


async def _is_member(bot, ch: Channel, uid: int) -> bool:
    ref = chat_ref(ch.channel_id)
    for attempt in (1, 2):
        try:
            m = await bot.get_chat_member(chat_id=ref, user_id=uid)
            status = m.status
            if status in ("creator", "administrator", "member"):
                return True
            if status == "restricted":
                return bool(getattr(m, "is_member", False))
            return False
        except RetryAfter as e:
            if attempt == 1:
                await asyncio.sleep(min(retry_seconds(e), 5.0))
                continue
            return True
        except (BadRequest, Forbidden) as e:
            msg = str(e)
            low = msg.lower()
            if "user not found" in low or "participant_id_invalid" in low:
                return False
            # ربات در کانال ادمین نیست یا کانال پیدا نمی‌شود: قفل نشدن کاربران
            log.warning("membership check failed for %s: %s", ch.channel_id, msg)
            await _report(ch, msg)
            return True
        except TelegramError as e:
            log.warning("membership check error for %s: %s", ch.channel_id, e)
            return True
    return True


async def check(bot, uid: int, force: bool = False) -> Tuple[bool, List[Tuple[Channel, bool]]]:
    """(همه عضو هستند؟، وضعیت هر کانال)"""
    channels = db.active_channels()
    if not channels:
        return True, []
    now = time.monotonic()
    if not force and _cache.get(uid, 0.0) > now:
        return True, [(c, True) for c in channels]
    results = await asyncio.gather(*(_is_member(bot, c, uid) for c in channels))
    statuses = list(zip(channels, results))
    ok = all(results)
    if ok and config.MEMBER_CACHE_TTL > 0:
        if len(_cache) > 50000:
            for k in [k for k, exp in _cache.items() if exp <= now]:
                _cache.pop(k, None)
        _cache[uid] = now + config.MEMBER_CACHE_TTL
    else:
        _cache.pop(uid, None)
    return ok, statuses


def channel_link(ch: Channel) -> Optional[str]:
    if ch.invite_link:
        return ch.invite_link
    if ch.channel_id.startswith("@"):
        return f"https://t.me/{ch.channel_id[1:]}"
    return None


def join_page(statuses: List[Tuple[Channel, bool]], name: str = ""):
    """متن و دکمه‌های صفحه عضویت اجباری."""
    rows = []
    for ch, joined in statuses:
        label = f"{OK if joined else NO} {ch.channel_title or ch.channel_id}"
        link = channel_link(ch)
        rows.append([btn(label, url=link) if link else btn(label, "u:noop")])
    rows.append([btn("بررسی مجدد", "u:chk")])
    return join_text(name), kb(*rows)
