"""ارسال اختیاری سیگنال‌های ساختاری به تلگرام بر اساس تنظیمات هر کاربر."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, Optional

import config
from database import db
from webapp.jalali import jalali_dt
from webapp.market import TF_LABEL, market
from webapp.repo import Repo, repo
from webapp.scanner import ScanRequest, Scanner, scanner

log = logging.getLogger("webapp.notifications")

TICK = 120
MIN_AGE = 3 * 3600


def _format(template: str, item: Dict[str, Any]) -> str:
    price = item.get("price") or 0
    try:
        price_text = f"{float(price):,.8f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        price_text = str(price)
    values = {
        "symbol": item.get("symbol", ""),
        "strategy": item.get("strategy", ""),
        "type": item.get("signal_type", ""),
        "timeframe": TF_LABEL.get(item.get("timeframe", ""), item.get("timeframe", "")),
        "date": item.get("date", ""),
        "price": price_text,
        "exchange": item.get("exchange", ""),
    }
    text = template or config.DEFAULT_SETTINGS.get("web_notify_template", "")
    for key, value in values.items():
        text = text.replace("{" + key + "}", str(value))
    return text


def _markup(symbol: str):
    from ui import btn, kb
    from webapp.auth import public_site

    base = public_site().rstrip("/")
    if not base.startswith("http"):
        base = "https://" + base
    return kb(
        [btn("مشاهده در وب‌اپ", url=f"{base}/symbol/{symbol}")],
        [btn("منوی اصلی", "u:mn")],
    )


async def _send(bot, uid: int, text: str, symbol: str) -> bool:
    try:
        import broadcaster

        return bool(await broadcaster.send_html(bot, int(uid), text, _markup(symbol)))
    except Exception as exc:
        log.warning("notify %s failed: %s", uid, exc)
        return False


async def _notify_user(bot, uid: int, settings: Dict[str, str], cfg: Dict[str, Any]) -> None:
    if cfg["symbols_mode"] == "favorites" and not repo.favorites(uid).get("symbol"):
        return
    if not cfg["strategy_keys"] and cfg["mode"] == "all":
        return
    req = ScanRequest(
        timeframe=cfg["timeframe"], depth=cfg["depth"], mode=cfg["mode"],
        symbols_mode=cfg["symbols_mode"], symbol=cfg["symbol"],
        strategy_keys=cfg["strategy_keys"], exchanges=cfg["exchanges"],
        exchanges_all=cfg["exchanges_all"], user_id=uid,
    )
    results = await scanner.scan(req, persist=True, use_cache=True)
    last_id = int(settings.get("last_notified_id") or 0)
    max_id = max([int(r.get("id") or 0) for r in results] + [last_id])
    if last_id <= 0:
        repo.save_user_settings(uid, {"last_notified_id": max_id})
        return
    fresh = [
        r for r in results
        if int(r.get("id") or 0) > last_id
        and float(r.get("candle_ts") or 0) >= time.time() - max(MIN_AGE, 2 * _tf_sec(cfg["timeframe"]))
    ]
    fresh = fresh[:10]
    if not fresh:
        repo.save_user_settings(uid, {"last_notified_id": max_id})
        return
    template = settings.get("notify_template") or config.DEFAULT_SETTINGS["web_notify_template"]
    sent = 0
    for item in fresh:
        text = _format(template, item)
        sent += 1 if await _send(bot, uid, text, item["symbol"]) else 0
        await asyncio.sleep(0.05)
    repo.save_user_settings(uid, {"last_notified_id": max_id})
    if sent:
        log.info("webapp signals sent to %s (%d)", uid, sent)


def _tf_sec(tf: str) -> int:
    return {"5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "4h": 14400,
            "6h": 21600, "12h": 43200, "1d": 86400, "1w": 604800}.get(tf, 3600)


async def notification_loop(bot) -> None:
    await asyncio.sleep(90)
    last_cleanup = 0.0
    while True:
        try:
            if time.time() - last_cleanup > 86400:
                repo.db.x("DELETE FROM structural_signals WHERE candle_ts < ?", (time.time() - 45 * 86400,))
                last_cleanup = time.time()
            rows = repo.db.q(
                "SELECT w.user_id FROM webapp_users w WHERE w.access_enabled = 1 AND w.password_hash <> ''"
            )
            for row in rows:
                uid = int(row["user_id"])
                # uid منفی = ثبت‌نام وب؛ این کاربر چت تلگرام ندارد و پیامی قابل ارسال نیست
                if uid == config.ADMIN_UID or uid < 0 or not db.has_active_webapp_sub(uid):
                    continue
                settings = repo.user_settings(uid)
                if settings.get("notify_on") != "1":
                    continue
                try:
                    await _notify_user(bot, uid, settings, repo.scan_config(uid))
                except Exception:
                    log.exception("webapp notify %s failed", uid)
                await asyncio.sleep(0.5)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("webapp notification loop error")
        await asyncio.sleep(TICK)
