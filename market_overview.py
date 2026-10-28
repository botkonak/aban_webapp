"""
وضعیت کلی بازار برای منوی اصلی کاربران (آبان ترید).

یک حلقه‌ی پس‌زمینه هر MARKET_INTERVAL ثانیه (پیش‌فرض ۱۵ دقیقه) از روی کندل‌های کش‌شده‌ی موتور (بدون درخواست اضافه به صرافی؛
همان تایم‌فریمی که موتور خودش گرم نگه می‌دارد) این شاخص‌ها را برای همه‌ی نمادهای فعال حساب می‌کند و در جدول settings می‌نویسد:
    میانگین RSI | میانگین ADX | درصد نمادهای صعودی (EMA سریع > EMA کند) | درصد نمادهای نزولی
وضعیت کلی:
    bull    میانگین RSI بالای ۵۵ و بیش از ۶۰٪ نمادها صعودی
    bear    میانگین RSI زیر ۴۵ و بیش از ۶۰٪ نمادها نزولی
    neutral بقیه‌ی حالت‌ها
منو فقط مقدار ذخیره‌شده را از حافظه می‌خواند (line()) و هیچ محاسبه‌ی سنگینی در مسیر کاربر انجام نمی‌شود.
"""
from __future__ import annotations

import asyncio
import logging
import math
import time
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

import config
import templates
from database import db
from signal_engine import Candles, adx as adx_f, engine, rsi as rsi_f
from ui import to_fa

log = logging.getLogger("market")

STATE_FA = {"bull": "صعودی 📈", "bear": "نزولی 📉", "neutral": "خنثی ➖"}
BATCH = 40                       # تعداد نمادی که هم‌زمان کندلشان گرفته می‌شود
RSI_BULL, RSI_BEAR = 55.0, 45.0
SHARE_MIN = 60.0                 # درصد حداقل نمادها در یک جهت

_lock: Optional[asyncio.Lock] = None


def _ema_last(c: np.ndarray, n: int) -> float:
    return float(pd.Series(c).ewm(span=n, adjust=False).mean().iloc[-1])


def symbol_metrics(c: Candles, ema_fast: int, ema_slow: int) -> Optional[Tuple[float, float, int]]:
    """(RSI، ADX، جهت EMA: +1 / -1 / 0) یک نماد؛ اگر کندل کافی نباشد None."""
    if len(c) < max(ema_slow, 30) + 10:
        return None
    r = float(rsi_f(c.c, 14)[-1])
    a = float(adx_f(c.h, c.l, c.c, 14)[0][-1])
    ef, es = _ema_last(c.c, ema_fast), _ema_last(c.c, ema_slow)
    if not all(math.isfinite(x) for x in (r, a, ef, es)):
        return None
    return r, a, (1 if ef > es else -1 if ef < es else 0)


def classify(avg_rsi: float, bull_pct: float, bear_pct: float) -> str:
    if avg_rsi > RSI_BULL and bull_pct > SHARE_MIN:
        return "bull"
    if avg_rsi < RSI_BEAR and bear_pct > SHARE_MIN:
        return "bear"
    return "neutral"


def summarize(items: List[Tuple[float, float, int]]) -> Dict[str, Any]:
    n = len(items)
    avg_rsi = sum(i[0] for i in items) / n
    avg_adx = sum(i[1] for i in items) / n
    bull = sum(1 for i in items if i[2] == 1) / n * 100.0
    bear = sum(1 for i in items if i[2] == -1) / n * 100.0
    return {"rsi": avg_rsi, "adx": avg_adx, "bull": bull, "bear": bear, "count": n, "state": classify(avg_rsi, bull, bear)}


def market_timeframe() -> str:
    """تایم‌فریمی که موتور ادمین همین حالا کش و گرم نگه می‌دارد (تایم‌فریم نهایی، وگرنه ورود)."""
    e, f = engine.timeframes(None, "LONG")
    return f or e or "1h"


async def compute() -> Optional[Dict[str, Any]]:
    """شاخص‌های بازار را از روی نمادهای فعال حساب می‌کند (بدون مسدود کردن Event Loop)."""
    symbols = engine.allowed_symbols()
    if not symbols:
        return None
    tf = market_timeframe()
    ef = max(2, db.get_e_int("ema_fast", "LONG"))
    es = max(ef + 1, db.get_e_int("ema_slow", "LONG"))
    items: List[Tuple[float, float, int]] = []
    for i in range(0, len(symbols), BATCH):
        batch = symbols[i:i + BATCH]
        res = await asyncio.gather(*(engine._get_candles(s, tf) for s in batch), return_exceptions=True)
        for c in res:
            if c is None or isinstance(c, BaseException):
                continue
            try:
                m = symbol_metrics(c, ef, es)
            except Exception:
                continue
            if m is not None:
                items.append(m)
            await asyncio.sleep(0)          # بین نمادها به Event Loop فرصت می‌دهد
    if len(items) < config.MARKET_MIN_SYMBOLS:
        return None
    out = summarize(items)
    out["tf"] = tf
    return out


def store(res: Dict[str, Any]) -> None:
    db.set_many({
        "mkt_state": res["state"],
        "mkt_rsi": f"{res['rsi']:.2f}",
        "mkt_adx": f"{res['adx']:.2f}",
        "mkt_bull": f"{res['bull']:.2f}",
        "mkt_bear": f"{res['bear']:.2f}",
        "mkt_count": str(res["count"]),
        "mkt_at": str(time.time()),
        "mkt_tf": res.get("tf") or "",
    })


async def refresh() -> bool:
    """یک بار محاسبه و ذخیره؛ هم‌زمانی با خودش را نمی‌پذیرد. خروجی: موفق بودن."""
    global _lock
    if _lock is None:
        _lock = asyncio.Lock()
    if _lock.locked():
        return False
    async with _lock:
        res = await compute()
        if res is None:
            return False
        store(res)
        log.info(
            "market %s | RSI %.1f ADX %.1f bull %.0f%% bear %.0f%% (%d symbols, %s)",
            res["state"], res["rsi"], res["adx"], res["bull"], res["bear"], res["count"], res.get("tf"),
        )
        return True


async def market_loop(app=None) -> None:
    """حلقه‌ی پس‌زمینه (هر ۱۵ دقیقه). خطا فقط لاگ می‌شود و حلقه متوقف نمی‌شود."""
    await asyncio.sleep(45)             # اول کندل‌ها توسط warm_loop موتور گرم می‌شوند
    while True:
        try:
            if engine.bot_on() and db.get_bool("mkt_on"):
                await refresh()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("market loop error")
        await asyncio.sleep(config.MARKET_INTERVAL)


# ----------------------------------------------------------------------------
# نمایش در منو
# ----------------------------------------------------------------------------
def values() -> Optional[Dict[str, str]]:
    """مقدارهای ذخیره‌شده برای قالب؛ None اگر داده‌ای نیست."""
    state = db.get("mkt_state")
    if state not in STATE_FA:
        return None
    return {
        "state": STATE_FA[state],
        "rsi": to_fa(f"{db.get_float('mkt_rsi'):.0f}"),
        "adx": to_fa(f"{db.get_float('mkt_adx'):.0f}"),
        "bull": to_fa(f"{db.get_float('mkt_bull'):.0f}"),
        "bear": to_fa(f"{db.get_float('mkt_bear'):.0f}"),
    }


def is_fresh() -> bool:
    at = db.get_float("mkt_at")
    return at > 0 and time.time() - at <= config.MARKET_STALE_SECONDS


def render(force: bool = False) -> str:
    """متن HTML خط وضعیت بازار؛ خالی اگر خاموش است یا داده‌ی تازه‌ای نیست (force=True برای پیش‌نمایش ادمین)."""
    if not force and not (db.get_bool("mkt_on") and is_fresh()):
        return ""
    ctx = values()
    if ctx is None:
        return ""
    tpl = db.get("mkt_template") or config.DEFAULT_MARKET_TEMPLATE
    try:
        return templates.render_template(tpl, ctx)
    except Exception:
        log.exception("market template render failed")
        return templates.render_template(config.DEFAULT_MARKET_TEMPLATE, ctx)


def line() -> str:
    return render()
