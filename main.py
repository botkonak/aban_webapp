"""نقطه ورود آبان ترید: ربات تلگرام + وب‌اپ تحلیل ساختاری روی یک پورت (FastAPI + Uvicorn)."""
from __future__ import annotations

import asyncio
import logging
import sys

import uvicorn
from telegram import BotCommand, BotCommandScopeChat, Update
from telegram.ext import Application

import broadcaster
import config
import free_subscription
import keep_alive
import market_overview
import performance_tracker
import plans
import price_broadcaster
import strategy_manager
import telegram_handler
from ai_engine import ai
from database import db
from live_price import live
from signal_engine import engine
from webapp import backups
from webapp.app import create_app
from webapp.repo import repo
from webapp.runtime import runtime

log = logging.getLogger("main")

USER_COMMANDS = [
    BotCommand("start", "شروع"),
    BotCommand("signal", "دریافت سیگنال جدید"),
    BotCommand("status", "وضعیت حساب"),
]
ADMIN_COMMANDS = [
    BotCommand("start", "شروع"),
    BotCommand("settings", "تنظیمات"),
    BotCommand("channels", "مدیریت کانال‌ها"),
    BotCommand("signal", "ارسال سیگنال فوری"),
]


def setup_logging() -> None:
    logging.basicConfig(
        level=getattr(logging, config.LOG_LEVEL, logging.INFO),
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        stream=sys.stdout,
    )
    for noisy in ("httpx", "httpcore", "telegram.ext", "tornado.access", "asyncio", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


async def post_init(app: Application) -> None:
    telegram_handler.set_bot(app.bot)
    runtime.attach(app)
    try:
        await app.bot.set_my_commands(USER_COMMANDS)
        await app.bot.set_my_commands(ADMIN_COMMANDS, scope=BotCommandScopeChat(chat_id=config.ADMIN_UID))
    except Exception as e:  # ادمین ممکن است هنوز /start نزده باشد
        log.warning("set_my_commands failed: %s", e)

    await engine.start()
    broadcaster.start_queue()
    loop = asyncio.get_running_loop()
    tasks = [
        loop.create_task(engine.warm_loop(), name="engine-warm"),
        loop.create_task(telegram_handler.auto_signal_loop(app), name="auto-signal"),
        loop.create_task(telegram_handler.user_signal_loop(app), name="user-signals"),
        loop.create_task(telegram_handler.pre_alert_loop(app), name="pre-alert"),
        loop.create_task(telegram_handler.sub_expiry_loop(app), name="sub-expiry"),
        loop.create_task(live.run(app.bot), name="live-price"),
        loop.create_task(performance_tracker.performance_tracker_loop(app), name="performance-tracker"),
        loop.create_task(price_broadcaster.broadcast_loop(app), name="price-broadcaster"),
        loop.create_task(market_overview.market_loop(app), name="market-overview"),
        loop.create_task(free_subscription.free_loop(app), name="free-subscription"),
        loop.create_task(backups.auto_loop(), name="auto-backup"),
        loop.create_task(keep_alive.keep_alive(), name="keep-alive"),
    ]
    app.bot_data["tasks"] = tasks
    log.info(
        "bot initialized | admin=%s | webapp=%s/%s",
        config.ADMIN_UID, config.WEBAPP_URL, config.WEBHOOK_PATH,
    )


async def post_stop(app: Application) -> None:
    tasks = app.bot_data.get("tasks") or []
    for t in tasks:
        t.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    await broadcaster.stop_queue()
    await engine.close()
    await ai.close()


async def post_shutdown(app: Application) -> None:
    db.close()


def _webhook_url() -> str:
    base = (config.WEBAPP_URL or config.WEBHOOK_URL or "").rstrip("/")
    return f"{base}/telegram/webhook/{config.WEBHOOK_PATH}"


async def run() -> None:
    application = (
        Application.builder()
        .token(config.BOT_TOKEN)
        .updater(None)
        .concurrent_updates(True)
        .connect_timeout(10)
        .read_timeout(15)
        .write_timeout(15)
        .pool_timeout(10)
        .post_init(post_init)
        .post_stop(post_stop)
        .post_shutdown(post_shutdown)
        .build()
    )
    telegram_handler.register_handlers(application)

    await application.initialize()
    await application.start()
    webhook = _webhook_url()
    try:
        if webhook.startswith("https://"):
            await application.bot.set_webhook(
                webhook,
                secret_token=config.WEBHOOK_SECRET,
                allowed_updates=[Update.MESSAGE, Update.CALLBACK_QUERY],
                drop_pending_updates=False,
                max_connections=100,
            )
            log.info("telegram webhook set: %s", webhook)
        else:
            log.warning(
                "آدرس عمومی HTTPS برای وب‌هوک تنظیم نشده است (WEBAPP_URL=%s)؛ "
                "ربات پیام‌ها را دریافت نمی‌کند تا دامنه‌ی HTTPS تنظیم شود.",
                config.WEBAPP_URL,
            )
    except Exception as exc:
        log.error("set_webhook failed: %s", exc)

    fastapi_app = create_app(application)
    uv_config = uvicorn.Config(
        fastapi_app,
        host=config.WEBAPP_HOST,
        port=config.WEBAPP_PORT,
        log_level=config.LOG_LEVEL.lower(),
        access_log=False,
        # IP واقعی کاربر در _client_key با زنجیره‌ی X-Forwarded-For و فهرست پروکسی‌های
        # مورد اعتماد تعیین می‌شود؛ اعتماد سراسری به هدر جعل IP را ممکن می‌کرد.
        proxy_headers=False,
        loop="asyncio",
    )
    if config.SSL_CERT and config.SSL_KEY:
        uv_config.ssl_certfile = config.SSL_CERT
        uv_config.ssl_keyfile = config.SSL_KEY
    server = uvicorn.Server(uv_config)
    try:
        await server.serve()
    finally:
        try:
            await application.stop()
        except Exception:
            log.exception("application.stop failed")
        try:
            await application.shutdown()
        except Exception:
            log.exception("application.shutdown failed")


def main() -> None:
    setup_logging()
    if not config.BOT_TOKEN:
        log.error("BOT_TOKEN داخل فایل .env خالی است.")
        sys.exit(1)

    db.init()
    strategy_manager.seed_builtin()
    try:
        plans.ensure_defaults()
    except Exception:
        log.exception("payment plans defaults failed")
    try:
        repo.ensure_defaults()
    except Exception:
        log.exception("webapp defaults failed")
    if not plans.configured(plans.PLAN_WEBAPP):
        log.info("اشتراک تحلیل ساختاری هنوز در پنل مدیریت وب‌اپ تنظیم نشده است.")

    if config.USE_UVLOOP and sys.platform != "win32":
        try:
            import uvloop

            asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
        except ImportError:
            pass

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
