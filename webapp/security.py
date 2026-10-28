"""محدودیت نرخ درخواست و ضداسپم مخصوص وب‌اپ (کاملاً مستقل از محدودیت‌های ربات)."""
from __future__ import annotations

import inspect
import logging
import re
import time
from typing import Any, Callable, Dict, Tuple

from starlette.exceptions import HTTPException
from starlette.formparsers import MultiPartException
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

import admin_notify
from database import db

log = logging.getLogger("webapp.security")

SKIP_PREFIXES = (
    "/health", "/assets/", "/uploads/", "/telegram/", "/favicon",
    "/api/admin/", "/api/auth/session",
)


class UploadLimitMiddleware:
    _FILE_LIMITS = {
        "/api/admin/media/upload": 5 * 1024 * 1024,
        "/api/admin/branding/logo": 3 * 1024 * 1024,
        "/api/admin/backups/upload": 40 * 1024 * 1024,
        "/api/subscription/request": 5 * 1024 * 1024,
    }

    def __init__(
        self,
        app: ASGIApp,
        require_admin: Callable[[Request], Any],
        require_user: Callable[[Request], Any],
    ) -> None:
        self.app = app
        self.require_admin = require_admin
        self.require_user = require_user

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] != "POST":
            await self.app(scope, receive, send)
            return
        path = scope["path"].rstrip("/")
        file_limit = self._FILE_LIMITS.get(path)
        if re.fullmatch(r"/api/admin/strategies/[^/]+/guide", path):
            file_limit = 5 * 1024 * 1024
        if file_limit is None:
            await self.app(scope, receive, send)
            return
        limit = file_limit + 64 * 1024
        request = Request(scope)
        require_auth = self.require_admin if path.startswith("/api/admin/") else self.require_user
        try:
            auth = require_auth(request)
            if inspect.isawaitable(auth):
                await auth
        except HTTPException as exc:
            if exc.status_code not in (401, 403):
                raise
            await JSONResponse(
                {"error": exc.detail, "status": exc.status_code},
                status_code=exc.status_code,
                headers=exc.headers,
            )(scope, receive, send)
            return

        lengths = request.headers.getlist("content-length")
        if lengths:
            value = lengths[0].strip(" \t")
            if len(lengths) != 1 or not value or not value.isascii() or not value.isdecimal():
                await JSONResponse(
                    {"error": "طول محتوای درخواست نامعتبر است.", "status": 400}, status_code=400
                )(scope, receive, send)
                return
            value = value.lstrip("0") or "0"
            maximum = str(limit)
            if len(value) > len(maximum) or (len(value) == len(maximum) and value > maximum):
                await JSONResponse(
                    {"error": "حجم درخواست بارگذاری از حد مجاز بیشتر است.", "status": 413}, status_code=413
                )(scope, receive, send)
                return

        received = 0
        exceeded = False
        response_started = False
        response_replaced = False
        pending_start: Message | None = None

        async def limited_receive() -> Message:
            nonlocal received, exceeded
            if exceeded:
                raise MultiPartException("حجم درخواست بارگذاری از حد مجاز بیشتر است.")
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    exceeded = True
                    raise MultiPartException("حجم درخواست بارگذاری از حد مجاز بیشتر است.")
            return message

        async def reject_oversize() -> None:
            nonlocal response_started, response_replaced
            if response_replaced:
                return
            if response_started:
                raise MultiPartException("حجم درخواست بارگذاری از حد مجاز بیشتر است.")
            response_started = response_replaced = True
            await JSONResponse(
                {"error": "حجم درخواست بارگذاری از حد مجاز بیشتر است.", "status": 413}, status_code=413
            )(scope, receive, send)

        async def guarded_send(message: Message) -> None:
            nonlocal pending_start, response_started
            if response_replaced:
                return
            if exceeded:
                await reject_oversize()
                return
            if message["type"] == "http.response.start":
                if not response_started:
                    pending_start = message
                return
            if pending_start is not None:
                response_started = True
                await send(pending_start)
                pending_start = None
            await send(message)

        try:
            await self.app(scope, limited_receive, guarded_send)
        except MultiPartException:
            if not exceeded:
                raise
            await reject_oversize()
        else:
            if exceeded:
                await reject_oversize()
            elif pending_start is not None:
                await send(pending_start)


class WindowLimiter:
    def __init__(self) -> None:
        self._hits: Dict[str, list] = {}
        self._blocked: Dict[str, float] = {}

    def blocked_for(self, key: str) -> int:
        return max(0, int(self._blocked.get(key, 0) - time.time()))

    def hit(self, key: str, limit: int, window: int, block: int) -> bool:
        """True اگر همین درخواست باعث شروع بلاک جدید شود."""
        now = time.time()
        if self.blocked_for(key) > 0:
            return False
        arr = [t for t in self._hits.get(key, []) if now - t < window]
        arr.append(now)
        self._hits[key] = arr
        if len(arr) > max(1, limit):
            self._blocked[key] = now + max(1, block)
            self._hits[key] = []
            return True
        return False

    def clear(self, key: str) -> None:
        self._hits.pop(key, None)
        self._blocked.pop(key, None)

    def snapshot(self, limit: int = 50) -> Dict[str, Any]:
        now = time.time()
        blocked = [
            {"key": k, "seconds_left": max(0, int(until - now))}
            for k, until in self._blocked.items()
            if until > now
        ]
        blocked.sort(key=lambda x: -x["seconds_left"])
        return {
            "blocked": blocked[:limit],
            "tracked": len(self._hits),
            "blocked_count": len(blocked),
        }


limiter = WindowLimiter()


def settings() -> Dict[str, Any]:
    return {
        "on": db.get_bool("wsec_on"),
        "max": db.get_int("wsec_max"),
        "window": db.get_int("wsec_window"),
        "block": db.get_int("wsec_block"),
        "notify": db.get_bool("wsec_notify"),
    }


def db_block_left(ip: str) -> int:
    """مدت بلاک دائمی/دستی ثبت‌شده در دیتابیس (پیش از هر بررسی دیگر اعمال می‌شود)."""
    try:
        row = db.q1("SELECT blocked_until FROM ip_blocks WHERE ip = ?", (str(ip or ""),))
    except Exception:
        return 0
    until = float((row or {}).get("blocked_until") or 0)
    return max(0, int(until - time.time()))


def _persist_block(ip: str, seconds: int, reason: str) -> None:
    try:
        until = time.time() + max(60, int(seconds))
        db.x(
            "INSERT INTO ip_blocks(ip, reason, blocked_until) VALUES(?, ?, ?) "
            "ON CONFLICT(ip) DO UPDATE SET reason = excluded.reason, blocked_until = excluded.blocked_until",
            (str(ip or ""), str(reason or "")[:300], until),
        )
    except Exception:
        log.exception("persist ip block failed")


async def enforce(ip: str, path: str) -> Tuple[bool, int]:
    """خروجی: (مسدود؟، ثانیه‌های باقی‌مانده). روی درخواست‌های پنل ادمین اعمال نمی‌شود."""
    if path.startswith("/health"):
        return False, 0
    blocked = db_block_left(ip)
    if blocked > 0:
        return True, blocked
    cfg = settings()
    if not cfg["on"]:
        return False, 0
    if any(path.startswith(p) for p in SKIP_PREFIXES):
        return False, 0
    left = limiter.blocked_for(ip)
    if left > 0:
        return True, left
    newly = limiter.hit(ip, cfg["max"], cfg["window"], cfg["block"])
    left = limiter.blocked_for(ip)
    if left > 0:
        admin_notify.log_event(0, "rate_limit", f"درخواست بیش از حد: {path}", ip, "warning")
        if newly:
            _persist_block(ip, left, f"محدودیت خودکار — {path[:120]}")
        if newly and cfg["notify"]:
            await admin_notify.notify(
                _bot(),
                "🚨 محدودیت درخواست وب‌اپ فعال شد\n"
                f"IP: <code>{_esc(ip)}</code>\n"
                f"مسیر: <code>{_esc(path[:120])}</code>\n"
                f"مدت بلاک: {max(1, left // 60)} دقیقه",
                kind="web_rate_limit",
                dedupe_key=f"rl:{ip}",
                cooldown=600,
            )
        return True, left
    return False, 0


def clear(ip: str) -> None:
    limiter.clear(ip)
    try:
        db.x("DELETE FROM ip_blocks WHERE ip = ?", (str(ip or ""),))
    except Exception:
        log.exception("clear ip block failed")


def stats() -> Dict[str, Any]:
    return limiter.snapshot()


def _bot():
    try:
        from webapp.runtime import runtime

        return runtime.bot
    except Exception:
        return None


def _esc(text: str) -> str:
    return (
        str(text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )
