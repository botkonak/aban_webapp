"""
رصد عملکرد سیگنال‌ها (آبان ترید).

این بخش هیچ معامله‌ای انجام نمی‌دهد و برای باز کردن پوزیشن به صرافی وصل نمی‌شود؛ فقط بعد از صدور هر سیگنال
قیمت لحظه‌ای همان نماد را (از live_price.py) هر ۵ دقیقه چک می‌کند و ثبت می‌کند:
    آیا به TP1 / TP2 / TP3 رسید؟  آیا به SL خورد؟  بالاترین و پایین‌ترین قیمت بعد از صدور چقدر بود؟
آمار برای نمایش به ادمین و برای مشاور موتورها (engine_advisor.py) استفاده می‌شود.

قواعد وضعیت:
  active -> tp1_hit -> tp2_hit -> tp3_hit | sl_hit | expired
  - رسیدن به SL قبل از هر TP: sl_hit (ناموفق).
  - بعد از رسیدن به TP1 سیگنال «موفق» حساب می‌شود؛ ادامه‌ی رصد فقط برای ارتقا به TP2 / TP3 است و برگشت قیمت
    به SL بعد از TP1 موفقیت را لغو نمی‌کند (فرض: حد ضرر به نقطه‌ی ورود منتقل شده است) و رصد همان‌جا تموم می‌شود.
  - بعد از ۷ روز بدون رسیدن به هیچ هدف / حد ضرر: expired.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import live_price
from database import db, utc_cutoff
from models import Signal

log = logging.getLogger("perf")

CHECK_INTERVAL = 300             # ثانیه (هر ۵ دقیقه)
EXPIRE_DAYS = 7
OPEN_STATUSES = ("active", "tp1_hit", "tp2_hit")
WIN_STATUSES = ("tp1_hit", "tp2_hit", "tp3_hit")
STATUS_LABELS = {
    "active": "فعال", "tp1_hit": "TP1 خورد", "tp2_hit": "TP2 خورد", "tp3_hit": "TP3 خورد",
    "sl_hit": "SL خورد", "expired": "منقضی",
}
# فیلترهای وضعیت پنل ادمین: کلید -> (عنوان، لیست وضعیت‌ها)
STATUS_FILTERS: Dict[str, Tuple[str, Tuple[str, ...]]] = {
    "all": ("همه", ()),
    "act": ("فعال", ("active", "tp1_hit", "tp2_hit")),
    "win": ("موفق", WIN_STATUSES),
    "loss": ("ناموفق", ("sl_hit",)),
    "exp": ("منقضی", ("expired",)),
}
PERIOD_FILTERS: Dict[str, Tuple[str, float]] = {
    "t": ("امروز", 1), "w": ("۷ روز", 7), "m": ("۳۰ روز", 30), "a": ("همه", 0),
}


def add_signal(signal_id: int, sig: Signal, uid: int = 0) -> int:
    """موقع صدور سیگنال یک رکورد رصد می‌سازد (uid = 0: سیگنال ادمین / کانال)."""
    return db.insert_id(
        "INSERT INTO signal_performance(signal_id, user_id, pair, side, entry, sl, tp1, tp2, tp3, status, best_tp, "
        "result_pct, max_price, min_price) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', 0, 0, ?, ?)",
        (signal_id, int(uid), sig.pair, sig.side, sig.entry, sig.sl, sig.tp1, sig.tp2, sig.tp3, sig.entry, sig.entry),
    )


def _pct(side: str, entry: float, price: float) -> float:
    if not entry:
        return 0.0
    return round((price - entry) / entry * 100.0 * (1.0 if side == "LONG" else -1.0), 4)


def _epoch(created_at: Any) -> float:
    from database import ts_str

    try:
        return datetime.strptime(ts_str(created_at), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp()
    except Exception:
        return time.time()


def step(row: Dict[str, Any], price: Optional[float], now: Optional[float] = None) -> Dict[str, Any]:
    """
    یک سیگنال را با قیمت فعلی ارزیابی می‌کند (تابع خالص؛ بدون دیتابیس).
    خروجی: {status, best_tp, result_pct, max_price, min_price, closed, first_tp}
    """
    now = time.time() if now is None else now
    side = row["side"]
    entry = float(row["entry"])
    levels = [float(row["tp1"]), float(row["tp2"]), float(row["tp3"])]
    sl = float(row["sl"])
    best = int(row.get("best_tp") or 0)
    max_p = float(row.get("max_price") or entry)
    min_p = float(row.get("min_price") or entry)
    status = row["status"]
    result = float(row.get("result_pct") or 0.0)
    closed = False
    first_tp = False

    if price is not None and price > 0:
        max_p, min_p = max(max_p, price), min(min_p, price)
        if side == "LONG":
            reached = sum(1 for t in levels if price >= t)
            sl_hit = price <= sl
        else:
            reached = sum(1 for t in levels if price <= t)
            sl_hit = price >= sl
        if reached > best:
            if best == 0:
                first_tp = True
            best = reached
        if best == 0:
            if sl_hit:
                status, result, closed = "sl_hit", _pct(side, entry, sl), True
        else:
            status = f"tp{best}_hit"
            result = _pct(side, entry, levels[best - 1])
            if best == 3 or sl_hit:
                closed = True

    if not closed and now - _epoch(row["created_at"]) >= EXPIRE_DAYS * 86400:
        closed = True
        if best == 0:
            status = "expired"
            result = _pct(side, entry, price) if price else result
    return {
        "status": status, "best_tp": best, "result_pct": result, "max_price": max_p, "min_price": min_p,
        "closed": closed, "first_tp": first_tp,
    }


async def check_once() -> int:
    rows = db.q(
        "SELECT * FROM signal_performance WHERE status IN ('active', 'tp1_hit', 'tp2_hit') "
        "AND closed_at IS NULL ORDER BY id"
    )
    if not rows:
        return 0
    pairs = sorted({r["pair"] for r in rows})
    res = await asyncio.gather(*(live_price.get_last_price(p) for p in pairs), return_exceptions=True)
    prices = {p: (px if isinstance(px, float) else None) for p, px in zip(pairs, res)}
    now = time.time()
    n = 0
    for row in rows:
        u = step(row, prices.get(row["pair"]), now)
        sets = "status = ?, best_tp = ?, result_pct = ?, max_price = ?, min_price = ?"
        params: list = [u["status"], u["best_tp"], u["result_pct"], u["max_price"], u["min_price"]]
        if u["first_tp"]:
            sets += ", first_tp_at = ?"
            params.append(now)
        if u["closed"]:
            sets += f", closed_at = {db.NOW}"
        params.append(row["id"])
        db.x(f"UPDATE signal_performance SET {sets} WHERE id = ?", params)
        n += 1
    return n


async def performance_tracker_loop(app) -> None:
    await asyncio.sleep(45)
    while True:
        try:
            await check_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("performance tracker error")
        await asyncio.sleep(CHECK_INTERVAL)


# ----------------------------------------------------------------------------
# آمار و لیست (پنل ادمین)
# ----------------------------------------------------------------------------
def _filters(
    status: str = "all", period: str = "a", pair: str = "", side: str = "", uid: Optional[int] = None,
) -> Tuple[str, list]:
    conds: List[str] = []
    params: list = []
    if uid is not None:
        conds.append("COALESCE(user_id, 0) = ?")
        params.append(int(uid))
    sts = STATUS_FILTERS.get(status, STATUS_FILTERS["all"])[1]
    if sts:
        conds.append("status IN (" + ",".join("?" * len(sts)) + ")")
        params += list(sts)
    days = PERIOD_FILTERS.get(period, PERIOD_FILTERS["a"])[1]
    if days:
        conds.append("created_at >= ?")
        params.append(utc_cutoff(days=days))
    q = (pair or "").strip().upper().replace("USDT", "").strip("/: ")
    if q:
        conds.append("UPPER(pair) LIKE ?")
        params.append(q + "/%")
    if side in ("LONG", "SHORT"):
        conds.append("side = ?")
        params.append(side)
    return ("WHERE " + " AND ".join(conds)) if conds else "", params


def stats(
    status: str = "all", period: str = "a", pair: str = "", side: str = "", uid: Optional[int] = None,
) -> Dict[str, Any]:
    where, params = _filters(status, period, pair, side, uid)
    r = db.q1(
        "SELECT COUNT(*) AS total, "
        "COALESCE(SUM(CASE WHEN best_tp >= 1 THEN 1 ELSE 0 END), 0) AS wins, "
        "COALESCE(SUM(CASE WHEN status = 'sl_hit' THEN 1 ELSE 0 END), 0) AS losses, "
        "COALESCE(SUM(CASE WHEN status = 'expired' THEN 1 ELSE 0 END), 0) AS expired, "
        "COALESCE(SUM(CASE WHEN status IN ('active', 'tp1_hit', 'tp2_hit') AND closed_at IS NULL THEN 1 ELSE 0 END), 0) AS active, "
        "COALESCE(AVG(CASE WHEN closed_at IS NOT NULL THEN result_pct END), 0) AS avg_pct "
        f"FROM signal_performance {where}",
        params,
    ) or {}
    wins, losses = int(r.get("wins") or 0), int(r.get("losses") or 0)
    return {
        "total": int(r.get("total") or 0), "wins": wins, "losses": losses,
        "expired": int(r.get("expired") or 0), "active": int(r.get("active") or 0),
        "rate": round(wins / (wins + losses) * 100.0, 1) if (wins + losses) else 0.0,
        "avg_pct": round(float(r.get("avg_pct") or 0.0), 2),
    }


def count(status: str = "all", period: str = "a", pair: str = "", uid: Optional[int] = None) -> int:
    where, params = _filters(status, period, pair, "", uid)
    return int((db.q1(f"SELECT COUNT(*) AS n FROM signal_performance {where}", params) or {}).get("n") or 0)


def list_signals(
    status: str = "all", period: str = "a", pair: str = "", offset: int = 0, limit: int = 8,
    uid: Optional[int] = None,
) -> List[Dict[str, Any]]:
    where, params = _filters(status, period, pair, "", uid)
    return db.q(
        f"SELECT * FROM signal_performance {where} ORDER BY id DESC LIMIT ? OFFSET ?", params + [limit, offset]
    )


def all_signals(
    status: str = "all", period: str = "a", pair: str = "", uid: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """همه‌ی رکوردهای فیلترشده (همه‌ی صفحه‌ها) برای Export."""
    where, params = _filters(status, period, pair, "", uid)
    return db.q(f"SELECT * FROM signal_performance {where} ORDER BY id DESC", params)


def delete_filtered(status: str = "all", period: str = "a", pair: str = "", uid: Optional[int] = None) -> int:
    """رکوردهای فیلترشده را پاک می‌کند (پاک کردن لیست). خروجی: تعداد رکورد پاک‌شده."""
    where, params = _filters(status, period, pair, "", uid)
    return int(db.x(f"DELETE FROM signal_performance {where}", params).rowcount or 0)


def side_summary(days: int = 7, uid: Optional[int] = None) -> Dict[str, Dict[str, Any]]:
    """خلاصه‌ی عملکرد برای مشاور موتورها (به‌تفکیک جهت)؛ uid: فقط سیگنال‌های همان کاربر (0 = ادمین)."""
    out: Dict[str, Dict[str, Any]] = {}
    cutoff = utc_cutoff(days=days)
    for side in ("LONG", "SHORT"):
        if uid is None:
            rows = db.q("SELECT * FROM signal_performance WHERE side = ? AND created_at >= ?", (side, cutoff))
        else:
            rows = db.q(
                "SELECT * FROM signal_performance WHERE side = ? AND created_at >= ? AND COALESCE(user_id, 0) = ?",
                (side, cutoff, int(uid)),
            )
        wins = [r for r in rows if int(r.get("best_tp") or 0) >= 1]
        losses = [r for r in rows if r["status"] == "sl_hit"]
        closed = [r for r in rows if r.get("closed_at") is not None]
        hours = []
        for r in wins:
            if r.get("first_tp_at"):
                hours.append((float(r["first_tp_at"]) - _epoch(r["created_at"])) / 3600.0)
        out[side] = {
            "total": len(rows),
            "wins": len(wins),
            "losses": len(losses),
            "success_rate": round(len(wins) / (len(wins) + len(losses)) * 100.0, 1) if (wins or losses) else 0.0,
            "avg_profit_pct": round(sum(float(r.get("result_pct") or 0) for r in closed) / len(closed), 2) if closed else 0.0,
            "avg_hours_to_tp": round(sum(hours) / len(hours), 1) if hours else None,
        }
    return out
