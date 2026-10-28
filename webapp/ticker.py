"""نوار قیمت زندهٔ وب‌اپ (Live Price Bar).

پیکربندی کامل در پیشخوان مدیریت نگه داشته می‌شود و قیمت‌ها از منبع انتخابی
(صرافی) خوانده می‌شوند. مقدار در یک کلید تنظیمات به شکل JSON ذخیره می‌شود تا
بدون مهاجرت دیتابیس روی استقرارهای موجود کار کند.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Dict, List, Optional

from database import db
from webapp.market import market
from webapp.repo import repo

log = logging.getLogger("aban.ticker")

SETTING_KEY = "ticker_config"
CACHE_SECONDS = 30
MAX_ITEMS = 30

DEFAULT_ITEMS: List[Dict[str, Any]] = [
    {"symbol": "BTCUSDT", "label": "BTC"},
    {"symbol": "ETHUSDT", "label": "ETH"},
    {"symbol": "SOLUSDT", "label": "SOL"},
    {"symbol": "XRPUSDT", "label": "XRP"},
    {"symbol": "BNBUSDT", "label": "BNB"},
    {"symbol": "DOGEUSDT", "label": "DOGE"},
]

DEFAULTS: Dict[str, Any] = {
    "enabled": True,
    "speed": 48,            # ثانیه برای هر دور کامل نوار
    "show_change": True,    # درصد تغییر ۲۴ ساعت
    "source": "binance",    # slug صرافیِ منبع؛ خالی یعنی خودکار
    "items": DEFAULT_ITEMS,
}

_cache: Dict[str, Any] = {"at": 0.0, "data": None}
_lock = asyncio.Lock()


def _clean_symbol(value: Any) -> str:
    return "".join(ch for ch in str(value or "").upper() if ch.isalnum())[:24]


def _clean_label(value: Any, fallback: str) -> str:
    text = str(value or "").strip() or fallback
    return text[:24]


def normalize(raw: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """پیکربندی خام را به ساختار معتبر و کامل تبدیل می‌کند."""
    cfg = dict(DEFAULTS)
    if isinstance(raw, dict):
        cfg["enabled"] = bool(raw.get("enabled", cfg["enabled"]))
        cfg["show_change"] = bool(raw.get("show_change", cfg["show_change"]))
        try:
            cfg["speed"] = max(8, min(int(float(raw.get("speed", cfg["speed"]))), 240))
        except (TypeError, ValueError):
            pass
        src = str(raw.get("source") or "").strip().lower()
        cfg["source"] = src

    items: List[Dict[str, Any]] = []
    seen = set()
    raw_items = (raw or {}).get("items")
    for row in raw_items if isinstance(raw_items, list) else []:
        if not isinstance(row, dict):
            continue
        symbol = _clean_symbol(row.get("symbol"))
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        exchange = str(row.get("exchange") or "").strip().lower()[:24]
        items.append({
            "symbol": symbol,
            "label": _clean_label(row.get("label"), symbol),
            "exchange": exchange,
            "enabled": bool(row.get("enabled", True)),
        })
        if len(items) >= MAX_ITEMS:
            break
    if not items:
        items = [
            {"symbol": r["symbol"], "label": r["label"], "exchange": "", "enabled": True}
            for r in DEFAULT_ITEMS
        ]
    cfg["items"] = items
    return cfg


def get_config() -> Dict[str, Any]:
    try:
        raw = json.loads(db.get(SETTING_KEY) or "{}")
    except (TypeError, ValueError):
        raw = {}
    return normalize(raw if isinstance(raw, dict) else {})


def save_config(payload: Dict[str, Any]) -> Dict[str, Any]:
    cfg = normalize(payload)
    db.set(SETTING_KEY, json.dumps(cfg, ensure_ascii=False))
    _cache["at"] = 0.0
    _cache["data"] = None
    return cfg


def source_options() -> List[Dict[str, str]]:
    out = [{"slug": "", "name": "خودکار (صرافی اصلی نماد)"}]
    try:
        for row in repo.exchange_rows(active_only=True):
            if int(row.get("is_visible") or 0):
                out.append({"slug": str(row["slug"]), "name": str(row.get("name") or row["slug"])})
    except Exception:
        log.debug("ticker source options failed", exc_info=True)
    return out


async def _price(symbol: str, exchange_slug: str) -> Dict[str, Any]:
    slugs = [exchange_slug] if exchange_slug else None
    ref = await asyncio.wait_for(market.resolve(symbol, slugs, not slugs), timeout=25)
    if ref is None and exchange_slug:
        ref = await asyncio.wait_for(market.resolve(symbol, None, True), timeout=25)
    if ref is None:
        return {"price": 0.0, "pct": 0.0, "exchange": "", "known": False}
    tick = await asyncio.wait_for(market.ticker(ref), timeout=20) or {}
    return {
        "price": float(tick.get("last") or 0.0),
        "pct": float(tick.get("pct") or 0.0),
        "exchange": str(ref.primary or ""),
        "known": True,
    }


async def snapshot(force: bool = False) -> Dict[str, Any]:
    """قیمت‌های لحظه‌ای آیتم‌های فعال؛ با کش کوتاه تا صرافی زیر فشار نیفتد."""
    cfg = get_config()
    base = {
        "enabled": cfg["enabled"],
        "speed": cfg["speed"],
        "show_change": cfg["show_change"],
        "source": cfg["source"],
        "updated": round(time.time()),
    }
    if not cfg["enabled"]:
        return {**base, "items": []}
    now = time.time()
    if not force and _cache["data"] and now - _cache["at"] < CACHE_SECONDS:
        return {**base, "items": _cache["data"], "cached": True}
    async with _lock:
        now = time.time()
        if not force and _cache["data"] and now - _cache["at"] < CACHE_SECONDS:
            return {**base, "items": _cache["data"], "cached": True}
        active = [i for i in cfg["items"] if i.get("enabled")][:MAX_ITEMS]
        source = cfg["source"] if repo.exchange(cfg["source"]) else ""
        results = await asyncio.gather(
            *[_price(i["symbol"], i.get("exchange") or source) for i in active],
            return_exceptions=True,
        )
        items: List[Dict[str, Any]] = []
        for item, res in zip(active, results):
            row = dict(item)
            if isinstance(res, Exception):
                log.debug("ticker %s failed: %s", item["symbol"], res)
                row.update({"price": 0.0, "pct": 0.0, "exchange": "", "known": False})
            else:
                row.update(res)
            items.append(row)
        good = [r for r in items if r["known"]]
        _cache["at"] = time.time()
        _cache["data"] = items if good else (_cache["data"] or items)
        return {**base, "items": _cache["data"]}
