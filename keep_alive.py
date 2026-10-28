"""
Keep-Alive (آبان ترید): ربات به‌صورت دوره‌ای و تصادفی به خودش درخواست GET می‌فرستد تا سرویس بیدار بماند.

  - مسیر /health در وب‌سرور ربات ساخته می‌شود و 200 OK برمی‌گرداند (add_health_route).
  - keep_alive() در post_init راه‌اندازی می‌شود؛ هر بار فاصله‌ی تصادفی بین keep_alive_min و keep_alive_max (ثانیه)
    صبر می‌کند و به {WEBHOOK_URL}/health درخواست می‌فرستد. خطا فقط لاگ می‌شود و حلقه متوقف نمی‌شود.
  - تنظیمات (keep_alive_on / keep_alive_min / keep_alive_max) هر بار از دیتابیس خوانده می‌شود؛ تغییر از پنل ادمین
    بدون ری‌استارت اعمال می‌شود. اگر min از max بزرگ‌تر باشد خودکار جای آن‌ها عوض می‌شود.
"""
from __future__ import annotations

import asyncio
import logging
import random
from typing import Tuple

import config
from database import db

log = logging.getLogger("keepalive")

DEFAULT_MIN, DEFAULT_MAX = 300, 600
IDLE_CHECK = 30           # وقتی خاموش است هر چند ثانیه وضعیت دوباره چک می‌شود


def health_url() -> str:
    base = (config.WEBAPP_URL or config.WEBHOOK_URL or "").rstrip("/")
    return f"{base}/health"


def read_settings() -> Tuple[bool, int, int]:
    """(روشن؟، حداقل، حداکثر) از دیتابیس؛ اگر مقداری نبود پیش‌فرض (300 و 600)."""
    on = db.get_bool("keep_alive_on")
    lo = db.get_int("keep_alive_min") or DEFAULT_MIN
    hi = db.get_int("keep_alive_max") or DEFAULT_MAX
    if lo > hi:
        lo, hi = hi, lo
    return on, max(1, lo), max(1, hi)


async def _ping() -> None:
    import aiohttp

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as s:
        async with s.get(health_url()) as r:
            await r.read()
            if r.status != 200:
                log.warning("keep-alive: %s returned %s", health_url(), r.status)


async def keep_alive() -> None:
    await asyncio.sleep(20)
    while True:
        try:
            on, lo, hi = read_settings()
            if not on:
                await asyncio.sleep(IDLE_CHECK)
                continue
            await asyncio.sleep(random.uniform(lo, hi))
            if not db.get_bool("keep_alive_on"):      # وسط انتظار خاموش شده است
                continue
            await _ping()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning("keep-alive request failed: %s", e)
            await asyncio.sleep(5)


def add_health_route() -> bool:
    """در معماری جدید، مسیر /health داخل FastAPI ساخته می‌شود و این تابع استفاده نمی‌شود."""
    try:
        import tornado.web
        from telegram.ext._utils import webhookhandler as wh
    except Exception as e:  # pragma: no cover
        log.warning("health route not available: %s", e)
        return False

    class HealthHandler(tornado.web.RequestHandler):
        def get(self) -> None:
            self.set_status(200)
            self.set_header("Content-Type", "text/plain; charset=utf-8")
            self.write("OK")

        def head(self) -> None:
            self.set_status(200)

    app_cls = getattr(wh, "WebhookAppClass", None)
    if app_cls is None or getattr(app_cls, "_health_patched", False):
        return app_cls is not None
    orig_init = app_cls.__init__

    def patched_init(self, *a, **kw):
        orig_init(self, *a, **kw)
        self.add_handlers(r".*$", [(r"/health/?", HealthHandler)])

    app_cls.__init__ = patched_init
    app_cls._health_patched = True
    return True
