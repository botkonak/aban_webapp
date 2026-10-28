"""
موتور تحلیل و تولید سیگنال.

لایه‌ها (همه‌ی موتورها قابل خاموش/روشن‌اند؛ موتور خاموش اصلاً در محاسبه نمی‌آید):
  جهت: Supertrend + EMA | رژیم: ADX | مومنتوم: RSI + StochRSI | حجم: فیلتر حجم + OBV
  فشردگی: Squeeze (باندهای بولینگر؛ پیش‌فرض: سیگنال قبل از انفجار) | واگرایی RSI | الگوهای کندلی
  ساختار: حمایت/مقاومت خودکار (وتو) + Volume POC (وتو، امتیاز، حد ضرر پویا)
  خروج: ATR (یا درصد ثابت وقتی ATR خاموش است) + Fibonacci برای امتیازدهی
  هوش مصنوعی: تأیید نهایی و توضیح کوتاه سیگنال (ai_engine.py)

حالت آماده‌باش (standby): کاندیدایی که فقط «یک» شرط کم دارد. سیگنال نیست؛ فقط برای هشدار قبل از سیگنال.

قیمت ورود (ENTRY) همیشه last price تیکر همان نماد است.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import time
from dataclasses import dataclass, fields
from decimal import Decimal
from typing import Dict, FrozenSet, List, Optional, Tuple

import numpy as np
import pandas as pd

import config
from ai_engine import AIVerdict, ai
from database import Database, db
from models import Candidate, ScanResult, Signal

log = logging.getLogger("engine")

TF_MS: Dict[str, int] = {
    "1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000, "30m": 1_800_000,
    "1h": 3_600_000, "2h": 7_200_000, "4h": 14_400_000, "6h": 21_600_000,
    "12h": 43_200_000, "1d": 86_400_000,
}

try:  # ثابت‌های دقت قیمت در ccxt
    from ccxt.base.decimal_to_precision import DECIMAL_PLACES, TICK_SIZE
except Exception:  # pragma: no cover
    TICK_SIZE, DECIMAL_PLACES = 4, 2

try:  # خطاهای شبکه‌ی ccxt (timeout / قطعی / rate limit)
    from ccxt.base.errors import NetworkError as CcxtNetworkError
    from ccxt.base.errors import RateLimitExceeded as CcxtRateLimit
except Exception:  # pragma: no cover
    class CcxtNetworkError(Exception):  # type: ignore[no-redef]
        pass

    class CcxtRateLimit(CcxtNetworkError):  # type: ignore[no-redef]
        pass


# ============================================================================
# اندیکاتورها
# ============================================================================
def _ema(x: np.ndarray, n: int) -> np.ndarray:
    return pd.Series(x).ewm(span=n, adjust=False).mean().to_numpy()


def _rma(x: np.ndarray, n: int) -> np.ndarray:
    return pd.Series(x).ewm(alpha=1.0 / n, adjust=False).mean().to_numpy()


def _sma(x: np.ndarray, n: int) -> np.ndarray:
    return pd.Series(x).rolling(n, min_periods=1).mean().to_numpy()


def rsi(close: np.ndarray, n: int) -> np.ndarray:
    delta = np.diff(close, prepend=close[0])
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    ag, al = _rma(gain, n), _rma(loss, n)
    safe_al = np.where(al == 0, 1.0, al)
    out = 100.0 - 100.0 / (1.0 + ag / safe_al)
    out = np.where(al == 0, np.where(ag == 0, 50.0, 100.0), out)
    return out


def stoch_rsi(close: np.ndarray, rsi_n: int, stoch_n: int, k_n: int, d_n: int) -> Tuple[np.ndarray, np.ndarray]:
    r = rsi(close, rsi_n)
    s = pd.Series(r)
    lo = s.rolling(stoch_n, min_periods=1).min().to_numpy()
    hi = s.rolling(stoch_n, min_periods=1).max().to_numpy()
    rng = hi - lo
    st = np.where(rng > 1e-12, (r - lo) / np.where(rng > 1e-12, rng, 1.0) * 100.0, 50.0)
    k = _sma(st, k_n)
    d = _sma(k, d_n)
    return k, d


def true_range(h: np.ndarray, l: np.ndarray, c: np.ndarray) -> np.ndarray:
    pc = np.concatenate(([c[0]], c[:-1]))
    return np.maximum.reduce([h - l, np.abs(h - pc), np.abs(l - pc)])


def atr(h: np.ndarray, l: np.ndarray, c: np.ndarray, n: int) -> np.ndarray:
    return _rma(true_range(h, l, c), n)


def adx(h: np.ndarray, l: np.ndarray, c: np.ndarray, n: int) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    up = np.diff(h, prepend=h[0])
    down = -np.diff(l, prepend=l[0])
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)
    a = atr(h, l, c, n)
    safe_a = np.where(a == 0, np.nan, a)
    plus_di = 100.0 * _rma(plus_dm, n) / safe_a
    minus_di = 100.0 * _rma(minus_dm, n) / safe_a
    denom = plus_di + minus_di
    dx = np.where(denom > 0, 100.0 * np.abs(plus_di - minus_di) / np.where(denom > 0, denom, 1.0), 0.0)
    dx = np.nan_to_num(dx, nan=0.0)
    plus_di = np.nan_to_num(plus_di, nan=0.0)
    minus_di = np.nan_to_num(minus_di, nan=0.0)
    return _rma(dx, n), plus_di, minus_di


def supertrend_dir(h: np.ndarray, l: np.ndarray, c: np.ndarray, period: int, mult: float) -> np.ndarray:
    """جهت Supertrend: +1 صعودی، -1 نزولی."""
    n = len(c)
    a = atr(h, l, c, period)
    hl2 = (h + l) / 2.0
    upper = (hl2 + mult * a).tolist()
    lower = (hl2 - mult * a).tolist()
    cl = c.tolist()
    fu, fl = upper[:], lower[:]
    trend = [1] * n
    trend[0] = 1 if cl[0] >= hl2[0] else -1
    for i in range(1, n):
        if upper[i] < fu[i - 1] or cl[i - 1] > fu[i - 1]:
            fu[i] = upper[i]
        else:
            fu[i] = fu[i - 1]
        if lower[i] > fl[i - 1] or cl[i - 1] < fl[i - 1]:
            fl[i] = lower[i]
        else:
            fl[i] = fl[i - 1]
        if trend[i - 1] == -1 and cl[i] > fu[i - 1]:
            trend[i] = 1
        elif trend[i - 1] == 1 and cl[i] < fl[i - 1]:
            trend[i] = -1
        else:
            trend[i] = trend[i - 1]
    return np.asarray(trend, dtype=np.int8)


def obv(c: np.ndarray, v: np.ndarray) -> np.ndarray:
    d = np.diff(c, prepend=c[0])
    return np.cumsum(np.where(d > 0, v, np.where(d < 0, -v, 0.0)))


def volume_poc(
    h: np.ndarray, l: np.ndarray, c: np.ndarray, v: np.ndarray, lookback: int = 100, bins: int = 24,
) -> Optional[float]:
    """نقطه کنترل (POC) پروفایل حجم روی آخرین lookback کندل."""
    hh, ll, cc, vv = h[-lookback:], l[-lookback:], c[-lookback:], v[-lookback:]
    if len(cc) < 5:
        return None
    lo, hi = float(ll.min()), float(hh.max())
    if not (hi > lo):
        return None
    tp = (hh + ll + cc) / 3.0
    hist, edges = np.histogram(tp, bins=max(2, int(bins)), range=(lo, hi), weights=vv)
    if hist.sum() <= 0:
        return None
    i = int(np.argmax(hist))
    return float((edges[i] + edges[i + 1]) / 2.0)


def fib_zone(h: np.ndarray, l: np.ndarray, price: float, direction: int, lookback: int = 60) -> bool:
    """آیا قیمت داخل ناحیه طلایی فیبوناچی (0.382 تا 0.618) آخرین موج هم‌جهت است؟"""
    hh, ll = h[-lookback:], l[-lookback:]
    i_hi, i_lo = int(np.argmax(hh)), int(np.argmin(ll))
    hi, lo = float(hh[i_hi]), float(ll[i_lo])
    rng = hi - lo
    if rng <= 0:
        return False
    if direction == 1 and i_lo < i_hi:  # موج صعودی -> اصلاح
        a, b = hi - 0.618 * rng, hi - 0.382 * rng
        return a <= price <= b
    if direction == -1 and i_hi < i_lo:  # موج نزولی -> اصلاح
        a, b = lo + 0.382 * rng, lo + 0.618 * rng
        return a <= price <= b
    return False


# ---------------------------------------------------------------------------
# کف و سقف (Pivot)
# ---------------------------------------------------------------------------
def pivot_idx(arr: np.ndarray, strength: int, lookback: int, kind: str) -> List[int]:
    """
    اندیس کف‌ها (kind="low") یا سقف‌های (kind="high") تأییدشده در آخرین lookback کندل.
    کف/سقف وقتی تأیید می‌شود که `strength` کندل بعد از آن هم بسته شده باشد.
    """
    n = len(arr)
    s = max(1, int(strength))
    w = 2 * s + 1
    if n < w:
        return []
    win = np.lib.stride_tricks.sliding_window_view(arr, w)
    center = win[:, s][:, None]
    if kind == "low":
        ok = (win[:, :s] > center).all(axis=1) & (win[:, s + 1:] >= center).all(axis=1)
    else:
        ok = (win[:, :s] < center).all(axis=1) & (win[:, s + 1:] <= center).all(axis=1)
    idx = np.flatnonzero(ok) + s
    first = n - max(1, int(lookback))
    return [int(i) for i in idx if i >= first]


# ---------------------------------------------------------------------------
# موتورهای پیشرفته: Liquidity Sweep / Fair Value Gaps / Smart Money / Momentum Breakout
# خروجی همه: +1 (سیگنال LONG)، -1 (سیگنال SHORT) یا None (بدون سیگنال)
# ---------------------------------------------------------------------------
def _vol_avg_before(v: np.ndarray, j: int, period: int) -> float:
    """میانگین حجم `period` کندل قبل از کندل j."""
    lo = max(0, j - max(1, int(period)))
    if j <= lo:
        return float("nan")
    return float(np.mean(v[lo:j]))


def liquidity_sweep(
    o: np.ndarray, h: np.ndarray, l: np.ndarray, c: np.ndarray, v: np.ndarray,
    lookback: int, strength: int, fresh: int, max_wick: float, vol_ratio: float, vol_ma: int,
) -> Optional[int]:
    """
    جاروب نقدینگی: شکستِ کاذبِ سقف/کفِ تأییدشده با سایه‌ی بلند و حجم بالا.
    Sweep صعودی (سقف جاروب شد و قیمت زیرش بسته شد) -> SHORT | Sweep نزولی -> LONG.
    """
    n = len(c)
    st = max(1, int(strength))
    fr = max(1, int(fresh))
    if n < 2 * st + fr + 2:
        return None
    lb = min(int(lookback), n - 1)
    piv_hi = pivot_idx(h, st, lb, "high")
    piv_lo = pivot_idx(l, st, lb, "low")
    for j in range(n - 1, max(n - 1 - fr, 1), -1):
        va = _vol_avg_before(v, j, vol_ma)
        if not _finite(va) or va <= 0 or v[j] < vol_ratio * va:
            continue
        body = max(abs(float(c[j] - o[j])), float(c[j]) * 1e-6)
        upper = float(h[j] - max(o[j], c[j]))
        if upper / body >= max_wick:
            for p in piv_hi:
                if p + st < j and c[j] < h[p] < h[j]:
                    return -1            # سقف جاروب شد و قیمت زیرش بسته شد
        lower = float(min(o[j], c[j]) - l[j])
        if lower / body >= max_wick:
            for p in piv_lo:
                if p + st < j and l[j] < l[p] < c[j]:
                    return 1             # کف جاروب شد و قیمت بالاترش بسته شد
    return None


def fair_value_gaps(
    h: np.ndarray, l: np.ndarray, c: np.ndarray, lookback: int, min_size: float, max_age: int, max_count: int,
) -> Optional[int]:
    """
    شکاف ارزش منصفانه: اگر قیمت فعلی داخل جدیدترین FVGهای معتبر باشد، جهت شکاف برمی‌گرداند
    (FVG صعودی -> LONG | FVG نزولی -> SHORT). min_size بر حسب درصد قیمت است.
    """
    n = len(c)
    if n < 5:
        return None
    close = float(c[-1])
    if not (_finite(close) and close > 0):
        return None
    min_gap = float(min_size) / 100.0 * close
    first = max(2, n - int(lookback))
    last_age = int(max_age)
    idx = np.arange(first, n)
    if len(idx) == 0:
        return None
    bull_gap = l[idx] - h[idx - 2]
    bear_gap = l[idx - 2] - h[idx]
    found: List[Tuple[int, int, float, float]] = []   # (i, جهت، کران پایین، کران بالا)
    for k in range(len(idx) - 1, -1, -1):
        i = int(idx[k])
        if n - 1 - i > last_age:
            break
        if bull_gap[k] > 0 and bull_gap[k] >= min_gap:
            found.append((i, 1, float(h[i - 2]), float(l[i])))
        elif bear_gap[k] > 0 and bear_gap[k] >= min_gap:
            found.append((i, -1, float(h[i]), float(l[i - 2])))
        if len(found) >= max(1, int(max_count)):
            break
    for _, d, lo, hi in found:          # جدیدترین اول
        if lo <= close <= hi:
            return d
    return None


def smart_money(
    o: np.ndarray, h: np.ndarray, l: np.ndarray, c: np.ndarray, v: np.ndarray,
    lookback: int, vol_ratio: float, body_ratio: float,
) -> Optional[int]:
    """پول هوشمند: کندل آخر با حجم بسیار بالا و بدنه‌ی قوی. جهت = جهت کندل (+1 صعودی / -1 نزولی)."""
    n = len(c)
    if n < 3:
        return None
    va = _vol_avg_before(v, n - 1, lookback)
    if not _finite(va) or va <= 0 or float(v[-1]) / va < vol_ratio:
        return None
    rng = float(h[-1] - l[-1])
    if not _finite(rng) or rng <= 0:
        return None
    body = abs(float(c[-1] - o[-1]))
    if body / rng < body_ratio:
        return None
    if c[-1] > o[-1]:
        return 1
    if c[-1] < o[-1]:
        return -1
    return None


def momentum_breakout(
    c: np.ndarray, v: np.ndarray, ema_fast: int, ema_slow: int, rsi_long: float, rsi_short: float,
    vol_ratio: float, rsi_period: int, vol_ma: int,
) -> Optional[int]:
    """شکست مومنتوم: تقاطع EMA سریع/کند در کندل آخر + RSI + حجم. صعودی -> LONG | نزولی -> SHORT."""
    n = len(c)
    if n < max(int(ema_slow), int(rsi_period)) + 3:
        return None
    ef, es = _ema(c, int(ema_fast)), _ema(c, int(ema_slow))
    f1, s1, f0, s0 = float(ef[-1]), float(es[-1]), float(ef[-2]), float(es[-2])
    if not _finite(f1, s1, f0, s0):
        return None
    up = f1 > s1 and f0 <= s0
    down = f1 < s1 and f0 >= s0
    if not (up or down):
        return None
    va = _vol_avg_before(v, n - 1, vol_ma)
    if not _finite(va) or va <= 0 or float(v[-1]) < vol_ratio * va:
        return None
    r = float(rsi(c, int(rsi_period))[-1])
    if not _finite(r):
        return None
    if up and r >= rsi_long:
        return 1
    if down and r <= rsi_short:
        return -1
    return None


# ---------------------------------------------------------------------------
# Squeeze (فشردگی)
# ---------------------------------------------------------------------------
def squeeze_info(
    h: np.ndarray, l: np.ndarray, c: np.ndarray, v: np.ndarray,
    bb_period: int, bb_mult: float, threshold: float, min_bars: int, fresh: int,
    vol_ma: int, vol_ratio: float,
) -> Tuple[str, float, float]:
    """(وضعیت، سقف جعبه‌ی فشردگی، کف جعبه‌ی فشردگی) — وضعیت‌ها در squeeze_state توضیح داده شدن."""
    nan = float("nan")
    n = len(c)
    if n < bb_period + 10:
        return "none", nan, nan
    s = pd.Series(c)
    sma = s.rolling(bb_period).mean()
    sd = s.rolling(bb_period).std(ddof=0)
    bw = (2.0 * bb_mult * sd / sma).to_numpy()
    ref = pd.Series(bw).rolling(120, min_periods=bb_period).mean().to_numpy()
    valid = np.isfinite(bw) & np.isfinite(ref) & (ref > 0)
    flag = np.where(valid, bw <= threshold * np.where(valid, ref, 1.0), False)
    idx = np.flatnonzero(flag)
    if idx.size == 0:
        return "none", nan, nan

    def run_start(e: int) -> int:
        k = e
        while k >= 0 and flag[k]:
            k -= 1
        return k + 1

    # شکست: یکی از کندل‌های اخیر (حداکثر fresh کندل پیش) بیرون جعبه‌ی فشردگی بسته شده است
    for j in range(max(1, n - 1 - fresh), n):
        prev = idx[idx <= j - 1]
        if prev.size == 0:
            continue
        e = int(prev[-1])
        st = run_start(e)
        if e - st + 1 < min_bars or j - 1 - e > fresh:
            continue
        hi, lo = float(h[st:e + 1].max()), float(l[st:e + 1].min())
        if j - 1 > e and ((c[e + 1:j] > hi).any() or (c[e + 1:j] < lo).any()):
            continue          # شکست قبلاً اتفاق افتاده (قدیمی)
        cj = float(c[j])
        if not (cj > hi or cj < lo):
            continue
        up = cj > hi
        last = float(c[-1])
        if (up and last <= hi) or (not up and last >= lo):
            return "wait", hi, lo     # شکست برگشت خورد
        avg = float(_sma(v, vol_ma)[j - 1])
        burst = float(v[j:].max())
        ok = avg > 0 and burst >= vol_ratio * avg
        return ("break_" if ok else "weak_") + ("up" if up else "dn"), hi, lo

    # شکستی نیست: هنوز داخل فشردگی هستیم یا تازه تمام شده است
    e = int(idx[-1])
    st = run_start(e)
    if e - st + 1 < min_bars:
        return "none", nan, nan
    hi, lo = float(h[st:e + 1].max()), float(l[st:e + 1].min())
    if e == n - 1:
        return "active", hi, lo
    if n - 1 - e <= fresh and lo <= float(c[-1]) <= hi:
        return "wait", hi, lo
    return "none", nan, nan


def squeeze_state(
    h: np.ndarray, l: np.ndarray, c: np.ndarray, v: np.ndarray,
    bb_period: int, bb_mult: float, threshold: float, min_bars: int, fresh: int,
    vol_ma: int, vol_ratio: float,
) -> str:
    """
    none      فشردگی‌ای وجود ندارد (یا قدیمی/کوتاه است)
    active    اکنون در فشردگی هستیم (آماده‌باش)
    wait      فشردگی پایان یافته اما هنوز قیمت از محدوده‌اش خارج نشده است
    break_up  شکست را به بالا همراه با حجم کافی (انفجار)
    break_dn  شکست را به پایین همراه با حجم کافی
    weak_up / weak_dn   شکست بدون حجم کافی
    فشردگی = پهنای باند بولینگر (نسبت به قیمت) کمتر از threshold برابر میانگین پهنای باند اخیر.
    """
    return squeeze_info(
        h, l, c, v, bb_period, bb_mult, threshold, min_bars, fresh, vol_ma, vol_ratio,
    )[0]


# ---------------------------------------------------------------------------
# واگرایی RSI
# ---------------------------------------------------------------------------
def divergence(
    h: np.ndarray, l: np.ndarray, c: np.ndarray, rsi_period: int,
    n_pivots: int, min_gap: int, strength: int, max_age: int, lookback: int,
) -> Tuple[bool, bool]:
    """
    (واگرایی مثبت، واگرایی منفی)
      مثبت: آخرین کف قیمت پایین‌تر از یکی از کف‌های قبلی اما RSI همان کف بالاتر
      منفی: آخرین سقف قیمت بالاتر از یکی از سقف‌های قبلی اما RSI همان سقف پایین‌تر
    n_pivots تعداد کف/سقف اخیر مورد بررسی (آخرین با بقیه مقایسه می‌شود) و min_gap حداقل فاصله‌ی دو کف/سقف.
    """
    n = len(c)
    r = rsi(c, rsi_period)
    bull = bear = False
    lows = pivot_idx(l, strength, lookback, "low")[-n_pivots:]
    if len(lows) >= 2 and n - 1 - lows[-1] <= max_age:
        j = lows[-1]
        for i in lows[:-1]:
            if j - i >= min_gap and l[j] < l[i] and r[j] > r[i]:
                bull = True
                break
    highs = pivot_idx(h, strength, lookback, "high")[-n_pivots:]
    if len(highs) >= 2 and n - 1 - highs[-1] <= max_age:
        j = highs[-1]
        for i in highs[:-1]:
            if j - i >= min_gap and h[j] > h[i] and r[j] < r[i]:
                bear = True
                break
    return bull, bear


# ---------------------------------------------------------------------------
# حمایت و مقاومت خودکار
# ---------------------------------------------------------------------------
def sr_levels(
    h: np.ndarray, l: np.ndarray, strength: int, lookback: int, count: int,
    min_dist_pct: float, min_gap: int,
) -> List[Tuple[float, int]]:
    """
    سطوح کلیدی: [(قیمت سطح، تعداد برخورد)] صعودی.
    count: تعداد سقف و کف اخیر؛ min_gap: حداقل فاصله‌ی زمانی (کندل) بین دو سقف/کف؛
    min_dist_pct: سقف/کف‌هایی که از این درصد به هم نزدیک‌ترند یک سطح حساب می‌شوند.
    """
    def pick(idx: List[int]) -> List[int]:
        kept: List[int] = []
        for i in reversed(idx):
            if all(abs(i - k) >= min_gap for k in kept):
                kept.append(i)
                if len(kept) >= count:
                    break
        return kept

    pts = [float(h[i]) for i in pick(pivot_idx(h, strength, lookback, "high"))]
    pts += [float(l[i]) for i in pick(pivot_idx(l, strength, lookback, "low"))]
    pts.sort()
    levels: List[Tuple[float, int]] = []
    for p in pts:
        if levels and levels[-1][0] > 0 and abs(p - levels[-1][0]) / levels[-1][0] * 100.0 < min_dist_pct:
            price, k = levels[-1]
            levels[-1] = ((price * k + p) / (k + 1), k + 1)
        else:
            levels.append((p, 1))
    return levels


# ---------------------------------------------------------------------------
# الگوهای کندلی
# ---------------------------------------------------------------------------
def _ctx(c: np.ndarray, i0: int, dirn: int) -> bool:
    """زمینه‌ی قبل از الگو: برای الگوی صعودی افت قبلی، برای نزولی رشد قبلی."""
    if i0 < 4:
        return False
    return bool(c[i0 - 1] < c[i0 - 4]) if dirn == 1 else bool(c[i0 - 1] > c[i0 - 4])


def _geo(o, h, l, c, k):
    top, bot = max(o[k], c[k]), min(o[k], c[k])
    return abs(c[k] - o[k]), h[k] - l[k], h[k] - top, bot - l[k]   # body, range, upper wick, lower wick


def _p_hammer(o, h, l, c, i, avg):
    body, rng, up, lo = _geo(o, h, l, c, i)
    if rng <= 0 or rng < 0.7 * avg or body > 0.35 * rng:
        return 0.0, 0.0
    bull = bear = 0.0
    if lo >= 0.55 * rng and up <= 0.15 * rng:
        bull = 50 + 20 * min(1.0, (lo / rng - 0.55) / 0.25) + (20 if _ctx(c, i, 1) else 0) + (5 if c[i] > o[i] else 0)
    if up >= 0.55 * rng and lo <= 0.15 * rng:
        bear = 50 + 20 * min(1.0, (up / rng - 0.55) / 0.25) + (20 if _ctx(c, i, -1) else 0) + (5 if c[i] < o[i] else 0)
    return min(bull, 100.0), min(bear, 100.0)


def _p_engulf(o, h, l, c, i, avg):
    b1, b0 = abs(c[i - 1] - o[i - 1]), abs(c[i] - o[i])
    if b0 < 0.5 * avg or b0 <= b1 or b1 <= 0:
        return 0.0, 0.0
    bull = bear = 0.0
    if c[i - 1] < o[i - 1] and c[i] > o[i] and o[i] <= c[i - 1] and c[i] >= o[i - 1]:
        bull = 60 + 20 * min(1.0, b0 / b1 - 1.0) + (20 if _ctx(c, i - 1, 1) else 0)
    if c[i - 1] > o[i - 1] and c[i] < o[i] and o[i] >= c[i - 1] and c[i] <= o[i - 1]:
        bear = 60 + 20 * min(1.0, b0 / b1 - 1.0) + (20 if _ctx(c, i - 1, -1) else 0)
    return min(bull, 100.0), min(bear, 100.0)


def _p_piercing(o, h, l, c, i, avg):
    b1 = abs(c[i - 1] - o[i - 1])
    if b1 < 0.6 * avg:
        return 0.0, 0.0
    mid = (o[i - 1] + c[i - 1]) / 2.0
    half = max(abs(o[i - 1] - mid), 1e-12)
    bull = bear = 0.0
    if c[i - 1] < o[i - 1] and c[i] > o[i] and o[i] <= c[i - 1] and mid < c[i] < o[i - 1]:
        bull = 55 + 25 * min(1.0, (c[i] - mid) / half) + (20 if _ctx(c, i - 1, 1) else 0)
    if c[i - 1] > o[i - 1] and c[i] < o[i] and o[i] >= c[i - 1] and o[i - 1] < c[i] < mid:
        bear = 55 + 25 * min(1.0, (mid - c[i]) / half) + (20 if _ctx(c, i - 1, -1) else 0)
    return min(bull, 100.0), min(bear, 100.0)


def _p_harami(o, h, l, c, i, avg):
    b1, b0 = abs(c[i - 1] - o[i - 1]), abs(c[i] - o[i])
    if b1 < 0.6 * avg or b0 > 0.6 * b1:
        return 0.0, 0.0
    t1, d1 = max(o[i - 1], c[i - 1]), min(o[i - 1], c[i - 1])
    if not (max(o[i], c[i]) <= t1 and min(o[i], c[i]) >= d1):
        return 0.0, 0.0
    q = 25 * (1.0 - b0 / b1)
    bull = bear = 0.0
    if c[i - 1] < o[i - 1] and c[i] > o[i]:
        bull = 45 + q + (20 if _ctx(c, i - 1, 1) else 0)
    if c[i - 1] > o[i - 1] and c[i] < o[i]:
        bear = 45 + q + (20 if _ctx(c, i - 1, -1) else 0)
    return min(bull, 100.0), min(bear, 100.0)


def _p_star(o, h, l, c, i, avg):
    b2, b1 = abs(c[i - 2] - o[i - 2]), abs(c[i - 1] - o[i - 1])
    if b2 < 0.7 * avg or b1 > 0.4 * b2:
        return 0.0, 0.0
    mid = (o[i - 2] + c[i - 2]) / 2.0
    half = max(abs(o[i - 2] - mid), 1e-12)
    top1, bot1 = max(o[i - 1], c[i - 1]), min(o[i - 1], c[i - 1])
    bull = bear = 0.0
    if c[i - 2] < o[i - 2] and top1 <= mid and c[i] > o[i] and c[i] > mid:
        bull = 65 + 15 * min(1.0, (c[i] - mid) / half) + (20 if _ctx(c, i - 2, 1) else 0)
    if c[i - 2] > o[i - 2] and bot1 >= mid and c[i] < o[i] and c[i] < mid:
        bear = 65 + 15 * min(1.0, (mid - c[i]) / half) + (20 if _ctx(c, i - 2, -1) else 0)
    return min(bull, 100.0), min(bear, 100.0)


def _p_soldiers(o, h, l, c, i, avg):
    ks = (i - 2, i - 1, i)
    bodies = [abs(c[k] - o[k]) for k in ks]
    if min(bodies) < 0.5 * avg:
        return 0.0, 0.0
    q = 10 * min(1.0, max(0.0, sum(bodies) / (3 * avg) - 0.5))
    bull = bear = 0.0
    if all(c[k] > o[k] for k in ks) and c[i] > c[i - 1] > c[i - 2] \
            and o[i - 1] >= o[i - 2] and o[i - 1] <= c[i - 2] and o[i] >= o[i - 1] and o[i] <= c[i - 1] \
            and all(_geo(o, h, l, c, k)[2] <= 0.4 * bodies[n] for n, k in enumerate(ks)):
        bull = 70 + q + (20 if _ctx(c, i - 2, 1) else 0)
    if all(c[k] < o[k] for k in ks) and c[i] < c[i - 1] < c[i - 2] \
            and o[i - 1] <= o[i - 2] and o[i - 1] >= c[i - 2] and o[i] <= o[i - 1] and o[i] >= c[i - 1] \
            and all(_geo(o, h, l, c, k)[3] <= 0.4 * bodies[n] for n, k in enumerate(ks)):
        bear = 70 + q + (20 if _ctx(c, i - 2, -1) else 0)
    return min(bull, 100.0), min(bear, 100.0)


_PATTERN_FUNCS = {
    "hammer": (_p_hammer, 0),
    "engulf": (_p_engulf, 1),
    "piercing": (_p_piercing, 1),
    "harami": (_p_harami, 1),
    "star": (_p_star, 2),
    "soldiers": (_p_soldiers, 2),
}


def candle_best(
    o: np.ndarray, h: np.ndarray, l: np.ndarray, c: np.ndarray, lookback: int, enabled: FrozenSet[str],
) -> Tuple[float, str, float, str]:
    """بهترین امتیاز الگوی صعودی/نزولی در آخرین lookback کندل: (امتیاز صعودی، نام، امتیاز نزولی، نام)."""
    n = len(c)
    avg = float(np.mean((h - l)[-20:]))
    if avg <= 0:
        return 0.0, "", 0.0, ""
    best_b, name_b, best_r, name_r = 0.0, "", 0.0, ""
    for i in range(max(4, n - max(1, lookback)), n):
        for pid in enabled:
            spec = _PATTERN_FUNCS.get(pid)
            if spec is None:
                continue
            fn, _ = spec
            bull, bear = fn(o, h, l, c, i, avg)
            if bull > best_b:
                best_b, name_b = bull, config.CANDLE_PATTERNS[pid][0]
            if bear > best_r:
                best_r, name_r = bear, config.CANDLE_PATTERNS[pid][1]
    return best_b, name_b, best_r, name_r


# ============================================================================
# داده کندل و پارامترها
# ============================================================================
@dataclass
class Candles:
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray
    c: np.ndarray
    v: np.ndarray
    last_open: int  # زمان باز شدن آخرین کندل بسته‌شده (ms)

    def __len__(self) -> int:
        return len(self.c)


@dataclass
class Params:
    """پروفایل تنظیمات موتورها برای یک جهت (در حالت مشترک هر دو جهت یک شیء‌اند)."""
    # روشن/خاموش
    en_st: bool
    en_ema: bool
    en_rsi: bool
    en_srsi: bool
    en_obv: bool
    en_adx: bool
    en_atr: bool
    en_vol: bool
    en_sq: bool
    en_div: bool
    en_cd: bool
    en_sr: bool
    en_poc: bool
    en_lq: bool
    en_fvg: bool
    en_sm: bool
    en_mb: bool
    # پارامترها
    st_period: int
    st_mult: float
    ema_fast: int
    ema_slow: int
    rsi_period: int
    rsi_ob: float
    rsi_os: float
    srsi_rsi: int
    srsi_stoch: int
    srsi_k: int
    srsi_d: int
    obv_ma: int
    adx_period: int
    adx_min: float
    atr_period: int
    min_atr_pct: float
    vol_ma: int
    vol_ratio: float
    sq_bb_period: int
    sq_bb_mult: float
    sq_threshold: float
    sq_min_bars: int
    sq_fresh: int
    sq_vol_ratio: float
    sq_pre: bool           # True: سیگنال «قبل از انفجار» (حین فشردگی)؛ False: بعد از شکست همراه حجم
    div_rsi: int
    div_pivots: int
    div_gap: int
    div_strength: int
    div_age: int
    div_lookback: int
    cd_min_score: int
    cd_lookback: int
    cd_patterns: FrozenSet[str]
    sr_count: int
    sr_min_dist: float
    sr_min_gap: int
    sr_strength: int
    sr_touches: int
    sr_near: float
    sr_lookback: int
    poc_lookback: int
    poc_bins: int
    poc_min_dist: float
    poc_score: int
    poc_sl_buffer: float
    poc_sl_min: float
    lq_lookback: int
    lq_strength: int
    lq_fresh: int
    lq_max_wick: float
    lq_vol_ratio: float
    fvg_lookback: int
    fvg_min_size: float
    fvg_max_age: int
    fvg_max_count: int
    sm_lookback: int
    sm_vol_ratio: float
    sm_body_ratio: float
    mb_ema_fast: int
    mb_ema_slow: int
    mb_rsi_long: float
    mb_rsi_short: float
    mb_vol_ratio: float

    @property
    def min_bars(self) -> int:
        return self.ema_slow + 50


def load_params(d: Database, side: Optional[str] = None) -> Params:
    """پروفایل موتورهای یک جهت. در حالت مشترک مقدار side بی‌اثر است."""
    def gi(k: str) -> int:
        return d.get_e_int(k, side)

    def gf(k: str) -> float:
        return d.get_e_float(k, side)

    def gb(k: str) -> bool:
        return d.get_e_bool(k, side)

    patterns = frozenset(pid for pid in config.CANDLE_PATTERNS if gb(f"cdp_{pid}"))
    return Params(
        en_st=gb("en_supertrend"), en_ema=gb("en_ema"), en_rsi=gb("en_rsi"), en_srsi=gb("en_stochrsi"),
        en_obv=gb("en_obv"), en_adx=gb("en_adx"), en_atr=gb("en_atr"), en_vol=gb("en_vol"),
        en_sq=gb("en_squeeze"), en_div=gb("en_diverg"), en_cd=gb("en_candle"), en_sr=gb("en_sr"),
        en_poc=gb("en_poc"),
        en_lq=gb("en_lq"), en_fvg=gb("en_fvg"), en_sm=gb("en_sm"), en_mb=gb("en_mb"),
        st_period=gi("st_period"), st_mult=gf("st_mult"),
        ema_fast=gi("ema_fast"), ema_slow=gi("ema_slow"),
        rsi_period=gi("rsi_period"), rsi_ob=gf("rsi_ob"), rsi_os=gf("rsi_os"),
        srsi_rsi=gi("srsi_rsi"), srsi_stoch=gi("srsi_stoch"), srsi_k=gi("srsi_k"), srsi_d=gi("srsi_d"),
        obv_ma=gi("obv_ma"), adx_period=gi("adx_period"), adx_min=gf("adx_min"),
        atr_period=gi("atr_period"), min_atr_pct=gf("min_atr_pct"),
        vol_ma=gi("vol_ma"), vol_ratio=gf("vol_ratio"),
        sq_bb_period=gi("sq_bb_period"), sq_bb_mult=gf("sq_bb_mult"), sq_threshold=gf("sq_threshold"),
        sq_min_bars=gi("sq_min_bars"), sq_fresh=gi("sq_fresh"), sq_vol_ratio=gf("sq_vol_ratio"),
        sq_pre=gi("sq_pre") == 1,
        div_rsi=gi("div_rsi"), div_pivots=gi("div_pivots"), div_gap=gi("div_gap"),
        div_strength=gi("div_strength"), div_age=gi("div_age"), div_lookback=gi("div_lookback"),
        cd_min_score=gi("cd_min_score"), cd_lookback=gi("cd_lookback"), cd_patterns=patterns,
        sr_count=gi("sr_count"), sr_min_dist=gf("sr_min_dist"), sr_min_gap=gi("sr_min_gap"),
        sr_strength=gi("sr_strength"), sr_touches=gi("sr_touches"), sr_near=gf("sr_near"),
        sr_lookback=gi("sr_lookback"),
        poc_lookback=gi("poc_lookback"), poc_bins=gi("poc_bins"), poc_min_dist=gf("poc_min_dist"),
        poc_score=gi("poc_score"), poc_sl_buffer=gf("poc_sl_buffer"), poc_sl_min=gf("poc_sl_min"),
        lq_lookback=gi("lq_lookback"), lq_strength=gi("lq_strength"), lq_fresh=gi("lq_fresh"),
        lq_max_wick=gf("lq_max_wick"), lq_vol_ratio=gf("lq_vol_ratio"),
        fvg_lookback=gi("fvg_lookback"), fvg_min_size=gf("fvg_min_size"),
        fvg_max_age=gi("fvg_max_age"), fvg_max_count=gi("fvg_max_count"),
        sm_lookback=gi("sm_lookback"), sm_vol_ratio=gf("sm_vol_ratio"), sm_body_ratio=gf("sm_body_ratio"),
        mb_ema_fast=gi("mb_ema_fast"), mb_ema_slow=gi("mb_ema_slow"),
        mb_rsi_long=gf("mb_rsi_long"), mb_rsi_short=gf("mb_rsi_short"), mb_vol_ratio=gf("mb_vol_ratio"),
    )


def params_fingerprint(*items) -> str:
    """اثر انگشت پروفایل تنظیمات: کاربرانی که تنظیمات یکسان دارند نتیجه‌ی تحلیل کش‌شده را به‌اشتراک می‌گذارند."""
    out: list = []
    for it in items:
        if isinstance(it, Params):
            row = []
            for f in fields(it):
                v = getattr(it, f.name)
                row.append((f.name, tuple(sorted(v)) if isinstance(v, frozenset) else v))
            out.append(tuple(row))
        else:
            out.append(it)
    return hashlib.md5(repr(out).encode("utf-8")).hexdigest()


def _finite(*vals: float) -> bool:
    return all(v is not None and math.isfinite(v) for v in vals)


# ============================================================================
# جهت روند و ارزیابی
# ============================================================================
def _trend(h: np.ndarray, l: np.ndarray, c: np.ndarray, p: Params) -> Tuple[int, float, float]:
    """
    (جهت، EMA سریع، EMA کند). جهت: +1 / -1 / 0 (ناهم‌جهت، داده ناکافی یا هر دو موتور جهت خاموش).
    فقط موتورهای روشن شرکت می‌کنند؛ اگر هر دو روشن باشند باید هم‌جهت باشند.
    """
    nan = float("nan")
    if len(c) < p.min_bars:
        return 0, nan, nan
    dirs: List[int] = []
    ef = es = nan
    if p.en_ema:
        ef, es = float(_ema(c, p.ema_fast)[-1]), float(_ema(c, p.ema_slow)[-1])
        if not _finite(ef, es) or ef == es:
            return 0, ef, es
        dirs.append(1 if ef > es else -1)
    if p.en_st:
        dirs.append(int(supertrend_dir(h, l, c, p.st_period, p.st_mult)[-1]))
    if not dirs or any(d != dirs[0] for d in dirs):
        return 0, ef, es
    return dirs[0], ef, es


def _div_flags(pc: Candles, p: Params) -> Tuple[bool, bool]:
    return divergence(
        pc.h, pc.l, pc.c, p.div_rsi, p.div_pivots, p.div_gap, p.div_strength, p.div_age,
        min(p.div_lookback, len(pc) - 1),
    )


# شرط‌هایی که اگر تنها شرط کم باشند، کاندیدا «آماده‌باش» حساب می‌شود
_Miss = Tuple[str, bool]


def _score(
    pair: str, base: str, pc: Candles, cc: Optional[Candles], p: Params, dirn: int,
    ef: float, es: float, misses0: List[_Miss],
) -> Optional[Candidate]:
    """امتیازدهی و اعمال فیلترها برای یک جهت مشخص. خروجی: سیگنال‌پذیر، آماده‌باش یا None."""
    o, h, l, c, v = pc.o, pc.h, pc.l, pc.c, pc.v
    close = float(c[-1])
    a = float(atr(h, l, c, p.atr_period)[-1])
    if not (_finite(close, a) and close > 0 and a > 0):
        return None
    # فیلتر نوسان (بخش موتور ATR): اگر نوسان کمتر از حداقل باشد، نماد خواب است.
    # در حالت «قبل از انفجار» (Squeeze روشن + sq_pre) نوسان طبیعتاً وسط فشردگی کمه؛ پس کف نوسان ۴۰٪ کمتر اعمال می‌شود.
    min_atr = p.min_atr_pct * (0.4 if (p.en_sq and p.sq_pre) else 1.0)
    if p.en_atr and (a / close) * 100.0 < min_atr:
        return None
    # تأیید چند تایم‌فریمی: روند تایم‌فریم نهایی باید هم‌جهت باشد
    if cc is not None and _trend(cc.h, cc.l, cc.c, p)[0] != dirn:
        return None

    misses: List[_Miss] = list(misses0)
    trend_pending = bool(misses0)
    reasons: List[str] = []
    earned = 0.0
    possible = 0.0

    def add(pts: float, max_pts: float) -> None:
        nonlocal earned, possible
        earned += max(0.0, min(pts, max_pts))
        possible += max_pts

    def bonus(pts: float) -> None:
        nonlocal earned
        earned += pts

    def penalty(pts: float) -> None:
        nonlocal earned
        earned = max(0.0, earned - pts)

    def miss(name: str, standby_ok: bool = True) -> None:
        misses.append((name, standby_ok))

    # ---------------- جهت ----------------
    add(0.0 if trend_pending else 20.0, 20.0)
    if not trend_pending:
        srcs = [n for n, on in (("Supertrend", p.en_st), ("EMA", p.en_ema)) if on]
        reasons.append(f"روند {'صعودی' if dirn == 1 else 'نزولی'} ({' و '.join(srcs)})")
    if p.en_ema and _finite(ef, es):
        add(min(max((ef - es) * dirn, 0.0) / a, 6.0) / 6.0 * 10.0, 10.0)

    # ---------------- Squeeze ----------------
    sq_ctx = False
    if p.en_sq:
        state, box_hi, box_lo = squeeze_info(
            h, l, c, v, p.sq_bb_period, p.sq_bb_mult, p.sq_threshold, p.sq_min_bars, p.sq_fresh,
            p.vol_ma, p.sq_vol_ratio,
        )
        want, weak = ("break_up", "weak_up") if dirn == 1 else ("break_dn", "weak_dn")
        sq_ctx = state != "none"
        if p.sq_pre:
            # قبل از انفجار: بازار فشرده شده (یا تازه از فشردگی درآمده) و هنوز شکست اتفاق نیفتاده است.
            # شکست انجام‌شده یعنی دیر شده است؛ پس سیگنال نمی‌دهیم.
            if state in ("active", "wait"):
                span = box_hi - box_lo
                lean = (close - box_lo) / span if _finite(span) and span > 0 else 0.5
                if (dirn == 1 and lean >= 0.5) or (dirn == -1 and lean <= 0.5):
                    add(15.0, 15.0)
                    reasons.append("فشردگی باندها (قبل از شکست)")
                else:
                    add(5.0, 15.0)
                    miss("sq")           # قیمت سمت مخالف جعبه‌ی فشردگی: آماده‌باش
            else:
                add(0.0, 15.0)
                miss("sq", False)        # فشردگی نیست یا شکست قبلاً انجام شده (دیر)
        elif state == want:
            add(15.0, 15.0)
            reasons.append("فشردگی باندها و شکست همراه با حجم")
        elif state in (weak, "active", "wait"):
            add(0.0, 15.0)
            miss("sq")           # فشردگی هست یا شکست بدون حجم: آماده‌باش
        else:
            add(0.0, 15.0)
            miss("sq", False)    # فشردگی نیست (یا شکست خلاف جهت): سیگنال ندیم

    # ---------------- رژیم: ADX ----------------
    adx_v = 0.0
    if p.en_adx:
        adx_arr, pdi, mdi = adx(h, l, c, p.adx_period)
        adx_v = float(adx_arr[-1])
        if not _finite(adx_v):
            return None
        # وسط فشردگی ADX طبیعتاً پایینه؛ پس موقع Squeeze این شرط نادیده گرفته می‌شود
        if adx_v < p.adx_min and not sq_ctx:
            miss("adx")
        elif adx_v >= p.adx_min:
            reasons.append(f"قدرت روند (ADX {adx_v:.0f})")
        add(min(adx_v, 50.0) / 50.0 * 20.0, 20.0)
        di_ok = (pdi[-1] > mdi[-1]) if dirn == 1 else (mdi[-1] > pdi[-1])
        add(5.0 if di_ok else 0.0, 5.0)

    # ---------------- مومنتوم ----------------
    rsi_v = 0.0
    if p.en_rsi:
        rsi_v = float(rsi(c, p.rsi_period)[-1])
        if not _finite(rsi_v):
            return None
        r_dir = rsi_v if dirn == 1 else 100.0 - rsi_v
        if (dirn == 1 and rsi_v > p.rsi_ob) or (dirn == -1 and rsi_v < p.rsi_os):
            miss("rsi_veto", False)
        elif r_dir <= 50.0 and not trend_pending:
            miss("rsi")
        add(15.0 * (1.0 - abs(r_dir - 60.0) / 30.0), 15.0)
    if p.en_srsi:
        k, dd = stoch_rsi(c, p.srsi_rsi, p.srsi_stoch, p.srsi_k, p.srsi_d)
        kv, dv = float(k[-1]), float(dd[-1])
        if not _finite(kv, dv):
            return None
        if (dirn == 1 and not kv > dv) or (dirn == -1 and not kv < dv):
            miss("srsi")
        else:
            reasons.append("تأیید StochRSI")
        add(min(abs(kv - dv) / 15.0, 1.0) * 10.0, 10.0)

    # ---------------- حجم ----------------
    vol_avg = float(_sma(v, p.vol_ma)[-1])
    vol_last = float(v[-1])
    if not _finite(vol_avg, vol_last):
        return None
    if p.en_vol:
        # در فشردگی حجم طبیعتاً کمه؛ شرط حجم همان تأیید انفجار Squeeze هست
        if vol_last < vol_avg * p.vol_ratio and not sq_ctx:
            miss("vol")
        elif vol_last >= vol_avg * p.vol_ratio:
            reasons.append("حجم بالاتر از میانگین")
        if vol_avg > 0:
            add(min(vol_last / vol_avg, 2.5) / 2.5 * 10.0, 10.0)
    if p.en_obv:
        ob = obv(c, v)
        ob_ma = _ema(ob, p.obv_ma)
        slope = float(ob[-1] - ob[-6]) if len(ob) > 6 else 0.0
        if dirn == 1:
            obv_ok = bool(ob[-1] > ob_ma[-1] and slope >= 0)
        else:
            obv_ok = bool(ob[-1] < ob_ma[-1] and slope <= 0)
        if not obv_ok:
            miss("obv")
        else:
            reasons.append("تأیید OBV")
        add(10.0 if obv_ok else 0.0, 10.0)

    # ---------------- واگرایی RSI (تأیید قوی) ----------------
    if p.en_div:
        bull, bear = _div_flags(pc, p)
        mine, opp = (bull, bear) if dirn == 1 else (bear, bull)
        if mine:
            bonus(15.0)
            reasons.append("واگرایی مثبت RSI" if dirn == 1 else "واگرایی منفی RSI")
        if opp:
            penalty(8.0)

    # ---------------- الگوهای کندلی ----------------
    if p.en_cd and p.cd_patterns:
        bs, bn, rs_, rn = candle_best(o, h, l, c, p.cd_lookback, p.cd_patterns)
        mine_s, mine_n, opp_s = (bs, bn, rs_) if dirn == 1 else (rs_, rn, bs)
        if mine_s >= p.cd_min_score:
            bonus(12.0 * mine_s / 100.0)
            reasons.append(f"الگوی کندلی {mine_n}")
        if opp_s >= p.cd_min_score:
            penalty(10.0)        # الگوی مخالف جهت: هشدار و کاهش امتیاز

    # ---------------- حمایت و مقاومت خودکار (وتو) ----------------
    if p.en_sr:
        lv = sr_levels(
            h, l, p.sr_strength, min(p.sr_lookback, len(c) - 1), p.sr_count, p.sr_min_dist, p.sr_min_gap,
        )
        strong = [pr for pr, k in lv if k >= p.sr_touches]
        if dirn == 1:
            ahead = [pr - close for pr in strong if pr > close]
        else:
            ahead = [close - pr for pr in strong if pr < close]
        room = (min(ahead) / a) if ahead else None
        if room is not None and room < p.sr_near:
            miss("sr", False)    # مقاومت قوی جلوی LONG / حمایت قوی جلوی SHORT
            add(0.0, 10.0)
        else:
            add(10.0 if room is None else 10.0 * min(1.0, room / 3.0), 10.0)
            reasons.append("فضای کافی تا مقاومت بعدی" if dirn == 1 else "فضای کافی تا حمایت بعدی")

    # ---------------- Volume POC (امتیاز + وتو + HTF) ----------------
    poc_val = 0.0
    if p.en_poc:
        poc_e = volume_poc(h, l, c, v, p.poc_lookback, p.poc_bins)
        poc_h = volume_poc(cc.h, cc.l, cc.c, cc.v, p.poc_lookback, p.poc_bins) if cc is not None else None
        refs = [x for x in (poc_e, poc_h) if x is not None]
        if refs and p.poc_score > 0:
            each = float(p.poc_score) / len(refs)
            wrong = False
            right = 0
            for ref in refs:
                # مثبت یعنی POC در جهت مخالف سیگنال قرار گرفته (LONG: قیمت زیر POC / SHORT: قیمت بالای POC)
                wrong_pct = (ref - close) / close * 100.0 * dirn
                if wrong_pct > p.poc_min_dist:
                    wrong = True
                    add(0.0, each)
                elif wrong_pct <= 0:
                    right += 1
                    add(each, each)
                else:
                    add(0.0, each)
            if wrong:
                miss("poc", False)
            elif right:
                reasons.append("قرارگیری قیمت در سمت درست POC")
        poc_val = float(poc_e) if poc_e is not None else 0.0

    # ---------------- موتورهای پیشرفته ----------------
    if p.en_lq:
        sig = liquidity_sweep(
            o, h, l, c, v, p.lq_lookback, p.lq_strength, p.lq_fresh, p.lq_max_wick, p.lq_vol_ratio, p.vol_ma,
        )
        if sig is not None:
            if sig == dirn:
                bonus(20.0)
                reasons.append("Liquidity Sweep هم‌جهت")
            else:
                penalty(15.0)
                miss("lq", False)        # جاروب نقدینگی خلاف جهت: وتو
    if p.en_fvg:
        sig = fair_value_gaps(h, l, c, p.fvg_lookback, p.fvg_min_size, p.fvg_max_age, p.fvg_max_count)
        if sig is not None:
            if sig == dirn:
                bonus(15.0)
                reasons.append("قرارگیری قیمت در FVG هم‌جهت")
            else:
                penalty(10.0)
                miss("fvg", False)       # FVG خلاف جهت: وتو
    if p.en_sm:
        sig = smart_money(o, h, l, c, v, p.sm_lookback, p.sm_vol_ratio, p.sm_body_ratio)
        if sig is not None:
            if sig == dirn:
                bonus(15.0)
                reasons.append("ورود پول هوشمند هم‌جهت")
            else:
                penalty(10.0)            # فقط کاهش امتیاز
    if p.en_mb:
        sig = momentum_breakout(
            c, v, p.mb_ema_fast, p.mb_ema_slow, p.mb_rsi_long, p.mb_rsi_short, p.mb_vol_ratio,
            p.rsi_period, p.vol_ma,
        )
        if sig is not None:
            if sig == dirn:
                bonus(15.0)
                reasons.append("شکست مومنتوم هم‌جهت")
            else:
                penalty(10.0)            # فقط کاهش امتیاز

    # ---------------- فیبوناچی (بخش ATR) ----------------
    if p.en_atr:
        fib = fib_zone(h, l, close, dirn)
        add(5.0 if fib else 0.0, 5.0)
        if fib:
            reasons.append("ناحیه طلایی فیبوناچی")

    score = round(max(0.0, min(100.0, earned / possible * 100.0)), 2) if possible > 0 else 0.0

    standby = False
    wait = ""
    if len(misses) > 1:
        return None
    if len(misses) == 1:
        if not misses[0][1]:
            return None
        standby, wait = True, misses[0][0]
    return Candidate(
        pair=pair, base=base, side="LONG" if dirn == 1 else "SHORT", score=score, atr=a, close=close,
        adx=adx_v, rsi=rsi_v, poc=poc_val, standby=standby, wait=wait, reasons=tuple(reasons),
    )


def evaluate(
    pair: str, base: str, pc: Candles, cc: Optional[Candles], pl: Params, ps: Params, tf_ms: int,
) -> Optional[Candidate]:
    """
    تحلیل کامل یک نماد روی کندل‌های بسته‌شده. pl پروفایل LONG و ps پروفایل SHORT (در حالت مشترک یک شیء).
    خروجی: کاندیدای سیگنال، کاندیدای آماده‌باش (standby=True) یا None.
    اگر روند مشخص نباشد (EMA و Supertrend هم‌جهت نباشند) هیچ سیگنالی صادر نمی‌شود.
    """
    n = len(pc)
    if n < max(pl.min_bars, ps.min_bars):
        return None
    # نماد متوقف/منقضی: آخرین کندل بسیار قدیمی است
    if pc.last_open + 4 * tf_ms < time.time() * 1000:
        return None

    h, l, c = pc.h, pc.l, pc.c
    dl, efl, esl = _trend(h, l, c, pl)
    if dl == 1:
        cand = _score(pair, base, pc, cc, pl, 1, efl, esl, [])
        # در حالت جداگانه پروفایل SHORT تنظیمات روند خودش را دارد؛ اگر LONG رد شد، SHORT هم بررسی شود
        if cand is not None or ps is pl:
            return cand
    if ps is pl:
        ds, efs, ess = dl, efl, esl
    else:
        ds, efs, ess = _trend(h, l, c, ps)
    if ds == -1:
        return _score(pair, base, pc, cc, ps, -1, efs, ess, [])

    # روند مشخص نیست (EMA و Supertrend هم‌جهت نیستند): سیگنال نمی‌دهیم.
    # (واگرایی RSI به‌تنهایی دیگر کاندیدای آماده‌باش نمیسازه؛ چون باعث سیگنال خلاف روند میشد.)
    return None


def evaluate_side(
    pair: str, base: str, pc: Candles, cc: Optional[Candles], p: Params, side: str, tf_ms: int,
) -> Optional[Candidate]:
    """تحلیل فقط یک جهت (LONG / SHORT) با کندل‌ها و پروفایل همان جهت (برای تایم‌فریم مستقل هر جهت)."""
    if len(pc) < p.min_bars:
        return None
    if pc.last_open + 4 * tf_ms < time.time() * 1000:
        return None
    dirn = 1 if side == "LONG" else -1
    d, ef, es = _trend(pc.h, pc.l, pc.c, p)
    if d != dirn:
        return None
    return _score(pair, base, pc, cc, p, dirn, ef, es, [])


# ============================================================================
# موتور
# ============================================================================
def decimals_of(step: float) -> int:
    try:
        exp = Decimal(str(step)).normalize().as_tuple().exponent
        return max(0, -int(exp))
    except Exception:
        return 4


def magnitude_decimals(price: float) -> int:
    if price >= 1000:
        return 2
    if price >= 100:
        return 3
    if price >= 1:
        return 4
    if price >= 0.1:
        return 5
    if price >= 0.01:
        return 6
    return 8


AN_CACHE_MAX = 60000     # سقف کش نتیجه‌ی تحلیل (اشتراکی بین کاربرانی که تنظیمات یکسان دارند)


class SignalEngine:
    def __init__(self, database: Database):
        self.db = database
        self.ex = None
        self.symbols: List[str] = []
        self.bases: Dict[str, str] = {}
        self.pair_cats: Dict[str, List[str]] = {}   # نماد -> دسته‌بندی‌ها (از فیلد categories صرافی)
        self._markets_at = 0.0
        self._ohlcv: Dict[Tuple[str, str], Tuple[Candles, float]] = {}
        self._inflight: Dict[Tuple[str, str], asyncio.Future] = {}
        self._an_cache: Dict[Tuple[str, str], Tuple[tuple, Optional[Candidate]]] = {}
        self._fail_until: Dict[str, float] = {}
        self._sem: Optional[asyncio.Semaphore] = None
        self._user_sem: Optional[asyncio.Semaphore] = None
        self._lock: Optional[asyncio.Lock] = None
        self._markets_lock: Optional[asyncio.Lock] = None
        self.last_error: str = ""
        self.ready = False
        self._scan_ok = 0
        self._scan_fail = 0
        self.last_scan: List[Candidate] = []     # نتیجه‌ی آخرین تحلیل (شامل آماده‌باش‌ها)
        self.last_scan_at = 0.0

    # ------------------------------------------------------------------
    async def start(self) -> None:
        import ccxt.async_support as ccxt_async

        cls = getattr(ccxt_async, config.EXCHANGE_ID, None)
        if cls is None:
            raise RuntimeError(f"صرافی {config.EXCHANGE_ID} در ccxt پیدا نشد؛ ccxt را به‌روزرسانی کنید.")
        self._sem = asyncio.Semaphore(config.SCAN_CONCURRENCY)
        self._user_sem = asyncio.Semaphore(config.USER_SCAN_CONCURRENCY)
        self._lock = asyncio.Lock()
        self._markets_lock = asyncio.Lock()
        self.ex = cls({
            "enableRateLimit": True,
            "timeout": 20000,
            "options": {"defaultType": "swap"},
        })

    async def close(self) -> None:
        if self.ex is not None:
            try:
                await self.ex.close()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # نمادها
    # ------------------------------------------------------------------
    async def get_symbols(self, force: bool = False) -> List[str]:
        if not force and self.symbols and time.time() - self._markets_at < 6 * 3600:
            return self.symbols
        async with self._markets_lock:
            if not force and self.symbols and time.time() - self._markets_at < 6 * 3600:
                return self.symbols
            try:
                markets = await self.ex.load_markets(True)
            except Exception as e:
                self.last_error = f"load_markets: {e}"
                log.warning("load_markets failed: %s", e)
                return self.symbols  # قبلی (اگر بود)
            syms: List[str] = []
            bases: Dict[str, str] = {}
            cats: Dict[str, List[str]] = {}
            known = 0
            for sym, m in markets.items():
                if not m.get("swap"):
                    continue
                if m.get("linear") is False:
                    continue
                if (m.get("settle") or m.get("quote")) != "USDT":
                    continue
                if m.get("active") is False:
                    continue
                info = m.get("info") if isinstance(m.get("info"), dict) else {}
                status = info.get("status")
                if status and str(status).upper() != "TRADING":
                    continue  # فقط قراردادهای قابل معامله
                syms.append(sym)
                bases[sym] = m.get("base") or sym.split("/")[0]
                raw = info.get("categories")
                if isinstance(raw, list):
                    known += 1
                    cats[sym] = self._clean_cats(raw)
                else:
                    cats[sym] = []
            if syms and known == 0:
                # ccxt فیلد categories را در info نگه نداشته است؛ مستقیم از exchangeInfo می‌خوانیم
                direct = await self._fetch_categories_direct()
                for sym in syms:
                    cid = str((markets.get(sym) or {}).get("id"))
                    if cid in direct:
                        cats[sym] = direct[cid]
            if config.MIN_24H_VOLUME_USDT > 0 and syms:
                syms = await self._filter_by_volume(syms)
            self.symbols = sorted(syms)
            self.bases = bases
            self.pair_cats = cats
            self._markets_at = time.time()
            self.ready = bool(self.symbols)
            self.last_error = ""
            log.info("loaded %d futures symbols (%d categories)", len(self.symbols), len(self.category_map()))
            return self.symbols

    @staticmethod
    def _clean_cats(raw) -> List[str]:
        out: List[str] = []
        for c in raw or []:
            name = str(c).strip()
            if name and name not in out:
                out.append(name)
        return out

    async def _fetch_categories_direct(self) -> Dict[str, List[str]]:
        """exchangeInfo را مستقیم می‌خواند: {symbol_id: [categories]} برای contracts."""
        out: Dict[str, List[str]] = {}
        try:
            import aiohttp

            timeout = aiohttp.ClientTimeout(total=25)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(config.EXCHANGE_INFO_URL) as resp:
                    resp.raise_for_status()
                    data = await resp.json(content_type=None)
            for c in (data or {}).get("contracts") or []:
                raw = c.get("categories")
                out[str(c.get("symbol"))] = self._clean_cats(raw if isinstance(raw, list) else [])
            log.info("categories loaded directly from exchangeInfo (%d contracts)", len(out))
        except Exception as e:
            log.warning("direct exchangeInfo fetch failed: %s", e)
        return out

    # ------------------------------------------------------------------
    # فیلتر نمادها
    # ------------------------------------------------------------------
    def pair_categories(self, pair: str) -> List[str]:
        return self.pair_cats.get(pair) or [config.NONE_CAT]

    def category_map(self) -> Dict[str, List[str]]:
        """نام دسته -> لیست مرتب نمادها (نمادهای بدون دسته در NONE_CAT)."""
        out: Dict[str, List[str]] = {}
        for pair in self.symbols:
            for c in self.pair_categories(pair):
                out.setdefault(c, []).append(pair)
        for c in out:
            out[c].sort(key=lambda p: self.bases.get(p, p))
        return out

    def is_allowed(self, pair: str, d=None) -> bool:
        """نماد وقتی مجاز است که خودش تیک داشته باشد و همه‌ی دسته‌هایش فعال باشند (فیلتر ادمین یا فیلتر یک کاربر)."""
        src = self.db if d is None else d
        if not src.sym_enabled(pair):
            return False
        return all(src.cat_enabled(c) for c in self.pair_categories(pair))

    def allowed_symbols(self, d=None) -> List[str]:
        return [s for s in self.symbols if self.is_allowed(s, d)]

    async def refresh_markets(self) -> List[str]:
        return await self.get_symbols(force=True)

    async def _filter_by_volume(self, syms: List[str]) -> List[str]:
        try:
            tickers = await self.ex.fetch_tickers(syms)
        except Exception as e:
            log.warning("fetch_tickers failed, volume filter skipped: %s", e)
            return syms
        keep = []
        for s in syms:
            t = tickers.get(s) or {}
            qv = t.get("quoteVolume")
            if qv is None or float(qv) >= config.MIN_24H_VOLUME_USDT:
                keep.append(s)
        return keep

    # ------------------------------------------------------------------
    # کندل‌ها (کش تا بسته شدن کندل بعدی)
    # ------------------------------------------------------------------
    async def _get_candles(self, pair: str, tf: str) -> Optional[Candles]:
        key = (pair, tf)
        now = time.time()
        hit = self._ohlcv.get(key)
        if hit and now < hit[1]:
            return hit[0]
        if self._fail_until.get(pair, 0.0) > now:
            return None
        fut = self._inflight.get(key)
        if fut is None:
            fut = asyncio.ensure_future(self._fetch(pair, tf))
            self._inflight[key] = fut
            fut.add_done_callback(lambda _f, k=key: self._inflight.pop(k, None))
        return await asyncio.shield(fut)

    async def _fetch(self, pair: str, tf: str) -> Optional[Candles]:
        tf_ms = TF_MS.get(tf)
        if not tf_ms:
            return None
        rows = None
        err: Optional[Exception] = None
        async with self._sem:
            for attempt in (1, 2):
                try:
                    rows = await self.ex.fetch_ohlcv(pair, tf, None, config.CANDLE_LIMIT)
                    err = None
                    break
                except CcxtNetworkError as e:  # timeout / قطعی / rate limit: یک بار دوباره
                    err = e
                    if attempt == 1:
                        await asyncio.sleep(2.0 if isinstance(e, CcxtRateLimit) else 0.8)
                except Exception as e:
                    err = e
                    break
        if rows is None:
            self._fail_until[pair] = time.time() + 60
            self._scan_fail += 1
            self.last_error = f"fetch_ohlcv {pair} {tf}: {err}"
            log.debug("fetch_ohlcv %s %s failed: %s", pair, tf, err)
            return None
        try:
            arr = np.asarray(rows, dtype=float)
            if arr.ndim != 2 or arr.shape[0] < 10 or arr.shape[1] < 6:
                return None
            arr = arr[np.argsort(arr[:, 0])]
            now_ms = time.time() * 1000
            # آخرین کندل اگر هنوز بسته نشده باشد حذف می‌شود
            if arr[-1, 0] + tf_ms > now_ms:
                arr = arr[:-1]
            if arr.shape[0] < 10:
                return None
            candles = Candles(
                o=arr[:, 1].copy(), h=arr[:, 2].copy(), l=arr[:, 3].copy(),
                c=arr[:, 4].copy(), v=arr[:, 5].copy(), last_open=int(arr[-1, 0]),
            )
        except Exception as e:
            log.debug("parse ohlcv %s %s failed: %s", pair, tf, e)
            return None
        now = time.time()
        computed = (candles.last_open + 2 * tf_ms) / 1000.0 + 3.0
        if candles.last_open + 4 * tf_ms < now * 1000:
            valid_until = now + 300.0          # نماد متوقف؛ کمتر بررسی شود
        elif computed - now > 5.0:
            valid_until = computed             # تا بسته شدن کندل بعدی معتبر است
        else:
            valid_until = now + 20.0           # کندل تازه در راه است
        self._ohlcv[(pair, tf)] = (candles, valid_until)
        self._scan_ok += 1
        return candles

    # ------------------------------------------------------------------
    # تحلیل
    # ------------------------------------------------------------------
    def timeframes(self, d=None, side: Optional[str] = None) -> Tuple[Optional[str], Optional[str]]:
        """(تایم‌فریم ورود، تایم‌فریم تأیید) طبق تنظیمات ادمین یا یک کاربر (در حالت جداگانه‌ی کاربر برای هر جهت)."""
        src = self.db if d is None else d
        e_on, f_on = src.get_e_bool("tf_entry_on", side), src.get_e_bool("tf_final_on", side)
        e, f = src.get_e("tf_entry", side), src.get_e("tf_final", side)
        if e_on and f_on:
            return e, (f if f != e else None)
        if e_on:
            return e, None
        if f_on:
            return f, None
        return None, None

    def bot_on(self) -> bool:
        return self.db.get_bool("bot_on")

    async def _get_pair(
        self, pair: str, tf1: str, tf2: Optional[str],
    ) -> Optional[Tuple[Candles, Optional[Candles]]]:
        if tf2:
            pc, cc = await asyncio.gather(self._get_candles(pair, tf1), self._get_candles(pair, tf2))
            if pc is None or cc is None:
                return None
            return pc, cc
        pc = await self._get_candles(pair, tf1)
        if pc is None:
            return None
        return pc, None

    def _cache_put(self, ck: Tuple[str, str], key: tuple, cand: Optional[Candidate]) -> None:
        cache = self._an_cache
        if len(cache) >= AN_CACHE_MAX:
            for k in list(cache)[: AN_CACHE_MAX // 3]:
                cache.pop(k, None)
        cache[ck] = (key, cand)

    async def _analyze_symbol(
        self, pair: str, pl: Params, ps: Params,
        tfl: Tuple[Optional[str], Optional[str]], tfs: Tuple[Optional[str], Optional[str]], fp: str,
    ) -> Optional[Candidate]:
        base = self.bases.get(pair) or pair.split("/")[0]
        r_l = await self._get_pair(pair, tfl[0], tfl[1])
        if r_l is None:
            return None
        pcl, ccl = r_l
        ck = (fp, pair)
        hit = self._an_cache.get(ck)
        if tfl == tfs:
            key = (pcl.last_open, ccl.last_open if ccl else 0)
            if hit and hit[0] == key:
                return hit[1]
            cand = evaluate(pair, base, pcl, ccl, pl, ps, TF_MS[tfl[0]])
        else:
            # تایم‌فریم مستقل برای هر جهت: هر جهت با کندل‌ها و پروفایل خودش تحلیل می‌شود
            r_s = await self._get_pair(pair, tfs[0], tfs[1])
            if r_s is None:
                return None
            pcs, ccs = r_s
            key = (pcl.last_open, ccl.last_open if ccl else 0, pcs.last_open, ccs.last_open if ccs else 0)
            if hit and hit[0] == key:
                return hit[1]
            cand = evaluate_side(pair, base, pcl, ccl, pl, "LONG", TF_MS[tfl[0]])
            if cand is None:
                cand = evaluate_side(pair, base, pcs, ccs, ps, "SHORT", TF_MS[tfs[0]])
        self._cache_put(ck, key, cand)
        await asyncio.sleep(0)  # به event loop فرصت اجرا بدهید
        return cand

    async def analyze_for(self, d=None) -> List[Candidate]:
        """تحلیل همه‌ی نمادهای مجاز با تنظیمات ادمین (d=None) یا یک کاربر؛ شامل کاندیداهای آماده‌باش هم هست."""
        src = self.db if d is None else d
        symbols = await self.get_symbols()
        symbols = [s for s in symbols if self.is_allowed(s, src)]  # فقط نمادهای فعال در فیلتر
        if not symbols:
            return []
        separate = src.eng_mode() == "separate"
        tfl = self.timeframes(src, "LONG")
        tfs = self.timeframes(src, "SHORT") if separate else tfl
        if not tfl[0] or not tfs[0]:
            return []
        pl = load_params(src, "LONG")
        ps = load_params(src, "SHORT") if separate else pl
        fp = params_fingerprint(pl, ps, tfl, tfs)

        async def one(sym: str) -> Optional[Candidate]:
            try:
                return await self._analyze_symbol(sym, pl, ps, tfl, tfs, fp)
            except Exception:
                log.exception("analyze %s failed", sym)
                return None

        res = await asyncio.gather(*(one(s) for s in symbols))
        return [r for r in res if r is not None]

    async def analyze_all(self) -> List[Candidate]:
        """تحلیل همه‌ی نمادهای فعال با تنظیمات ادمین؛ خروجی شامل کاندیداهای آماده‌باش هم هست (c.standby)."""
        out = await self.analyze_for(None)
        self.last_scan = out
        self.last_scan_at = time.time()
        return out

    def sides_ready(self) -> set:
        return {s for s in ("LONG", "SHORT") if self.db.get_template(s).strip()}

    def standby_candidates(self) -> List[Candidate]:
        """نمادهایی که تو آخرین تحلیل «آماده‌باش» بودن (برای هشدار قبل از سیگنال)، به ترتیب امتیاز."""
        if not self.bot_on() or time.time() - self.last_scan_at > 600:
            return []
        ready = self.sides_ready()
        recent = self.db.recent_symbols(24) if self.db.get_bool("dedupe_on") else set()
        out = [
            c for c in self.last_scan
            if c.standby and c.side in ready and c.pair not in recent and self.is_allowed(c.pair)
        ]
        out.sort(key=lambda c: c.score, reverse=True)
        return out

    async def self_test(self) -> None:
        """یک بررسی سریع در شروع کار؛ نتیجه در لاگ نوشته می‌شود تا مشکل اتصال/فرمت زود دیده شود."""
        syms = await self.get_symbols()
        if not syms:
            log.error("self-test: هیچ نماد فیوچرزی از %s گرفته نشد (%s)", config.EXCHANGE_ID, self.last_error)
            return
        pair = "BTC/USDT:USDT" if "BTC/USDT:USDT" in syms else syms[0]
        tf1, _ = self.timeframes()
        try:
            rows = await self.ex.fetch_ohlcv(pair, tf1 or "15m", None, config.CANDLE_LIMIT)
            px = await self.last_price(pair)
            log.info("self-test OK: %s | %d symbols | %d candles (%s) | last=%s",
                     pair, len(syms), len(rows or []), tf1, px)
            if rows is not None and len(rows) < config.CANDLE_LIMIT * 0.9:
                log.warning("self-test: صرافی فقط %d کندل داد؛ حداقل ۲۵۰ کندل لازم است.", len(rows))
        except Exception as e:
            self.last_error = f"self-test: {e}"
            log.error("self-test FAILED for %s: %s", pair, e)

    async def warm_loop(self) -> None:
        """کش داده و تحلیل را گرم نگه می‌دارد تا دکمه سیگنال فوری جواب بدهد (وقتی ربات خاموش است تحلیلی انجام نمی‌شود)."""
        await asyncio.sleep(3)
        try:
            await self.self_test()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("self-test crashed")
        while True:
            try:
                if self.bot_on():
                    await self.analyze_all()
                else:
                    self.last_scan = []
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("warm loop error")
            await asyncio.sleep(config.WARM_INTERVAL)

    # ------------------------------------------------------------------
    # قیمت لحظه‌ای: همیشه last price
    # ------------------------------------------------------------------
    async def last_price(self, pair: str) -> Optional[float]:
        for attempt in (1, 2):
            try:
                t = await self.ex.fetch_ticker(pair)
                px = t.get("last")
                if px is not None and float(px) > 0:
                    return float(px)
                break
            except CcxtNetworkError as e:
                log.debug("fetch_ticker %s failed (attempt %d): %s", pair, attempt, e)
                if attempt == 1:
                    await asyncio.sleep(0.8)
            except Exception as e:
                log.debug("fetch_ticker %s failed: %s", pair, e)
                break
        try:
            lp = await self.ex.fetch_last_prices([pair])
            item = lp.get(pair) or {}
            px = item.get("price")
            if px is not None and float(px) > 0:
                return float(px)
        except Exception as e:
            log.debug("fetch_last_prices %s failed: %s", pair, e)
        return None

    # ------------------------------------------------------------------
    # ساخت سیگنال
    # ------------------------------------------------------------------
    def _price_decimals(self, pair: str, price: float) -> int:
        try:
            m = self.ex.markets.get(pair) or {}
            prec = (m.get("precision") or {}).get("price")
            mode = getattr(self.ex, "precisionMode", None)
            if prec:
                if mode == TICK_SIZE:
                    return decimals_of(float(prec))
                if mode == DECIMAL_PLACES:
                    return int(prec)
        except Exception:
            pass
        return magnitude_decimals(price)

    def _risk_leverage(self, sl_pct: float, score: float, d=None) -> Tuple[float, int]:
        """
        اهرم: هرچه فاصله حد ضرر (نوسان) بیشتر، اهرم کمتر؛ طوری که خوردن SL حدود نصف مارجین را بزند.
        ریسک: ترکیب کم‌نوسانی و قدرت سیگنال بین حداقل و حداکثر ریسک.
        """
        d = self.db if d is None else d
        r_min, r_max = d.get_float("risk_min"), d.get_float("risk_max")
        l_min, l_max = d.get_int("lev_min"), d.get_int("lev_max")
        if r_min > r_max:
            r_min, r_max = r_max, r_min
        if l_min > l_max:
            l_min, l_max = l_max, l_min
        sl_pct = max(sl_pct, 0.01)
        lev = int(round(50.0 / sl_pct))
        lev = max(l_min, min(l_max, lev))
        vol_score = min(1.0, max(0.0, 1.0 - (sl_pct - 0.5) / (6.0 - 0.5)))
        strength = min(1.0, max(0.0, score / 100.0))
        risk = r_min + (r_max - r_min) * (0.5 * vol_score + 0.5 * strength)
        risk = round(min(r_max, max(r_min, risk)), 1)
        return risk, lev

    def build_signal(self, cand: Candidate, entry: float, d=None) -> Optional[Signal]:
        d = self.db if d is None else d
        side = cand.side
        a = cand.atr
        if d.get_e_bool("en_atr", side):
            sl_dist = d.get_e_float("sl_mult", side) * a
            offs = [d.get_e_float(k, side) * a for k in ("tp1_mult", "tp2_mult", "tp3_mult")]
        else:
            # موتور ATR خاموش است: حد ضرر و سود با درصد ثابت
            sl_dist = entry * d.get_e_float("fx_sl", side) / 100.0
            offs = [entry * d.get_e_float(k, side) / 100.0 for k in ("fx_tp1", "fx_tp2", "fx_tp3")]
        # حد ضرر پویا: پشت POC (اگر POC سمت محافظ قیمت باشد و از حد ضرر معمولی نزدیک‌تر)
        if d.get_e_bool("en_poc", side) and cand.poc > 0 and a > 0:
            buf = d.get_e_float("poc_sl_buffer", side) * a
            min_d = d.get_e_float("poc_sl_min", side) * a
            dist = None
            if side == "LONG" and cand.poc < entry:
                dist = entry - (cand.poc - buf)
            elif side == "SHORT" and cand.poc > entry:
                dist = (cand.poc + buf) - entry
            if dist is not None:
                dist = max(dist, min_d)
                if dist < sl_dist:
                    sl_dist = dist
        if side == "LONG":
            raw = [entry - sl_dist, entry + offs[0], entry + offs[1], entry + offs[2]]
        else:
            raw = [entry + sl_dist, entry - offs[0], entry - offs[1], entry - offs[2]]
        if raw[0] <= 0 or min(raw) <= 0:
            return None

        base_dec = self._price_decimals(cand.pair, entry)
        chosen = None
        for extra in range(0, 6):
            dec = base_dec + extra
            sl, t1, t2, t3 = (round(x, dec) for x in raw)
            e = round(entry, dec)
            if side == "LONG":
                ok = sl < e < t1 < t2 < t3
            else:
                ok = sl > e > t1 > t2 > t3
            if ok:
                chosen = (dec, e, sl, t1, t2, t3)
                break
        if not chosen:
            return None
        dec, e, sl, t1, t2, t3 = chosen
        sl_pct = abs(e - sl) / e * 100.0
        risk, lev = self._risk_leverage(sl_pct, cand.score, d)
        return Signal(
            symbol=cand.base, pair=cand.pair, side=side, entry=e,
            sl=sl, tp1=t1, tp2=t2, tp3=t3, risk=risk, leverage=lev,
            decimals=dec, score=cand.score,
        )

    # ------------------------------------------------------------------
    # پیدا کردن بهترین سیگنال
    # ------------------------------------------------------------------
    async def _ai_review(self, cand: Candidate, sig: Signal, uid: int = 0) -> AIVerdict:
        """بررسی AI؛ هر مشکلی (تایم‌اوت، خطا، خاموشی) یعنی سیگنال بدون AI ادامه پیدا کند."""
        try:
            return await asyncio.wait_for(ai.review(cand, sig, uid), timeout=config.AI_TIMEOUT * 2.5)
        except asyncio.TimeoutError:
            log.warning("AI review timed out for %s", cand.pair)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("AI review failed for %s", cand.pair)
        return AIVerdict()

    def record(self, sig: Signal, uid: int = 0) -> None:
        """ثبت سیگنال صادرشده در تاریخچه + رصد عملکرد (uid = 0: ادمین / کانال)."""
        sid = self.db.record_signal(sig.pair, sig.side, uid)
        try:   # رصد عملکرد سیگنال (فقط ثبت قیمت‌ها؛ هیچ معامله‌ای انجام نمی‌شود)
            import performance_tracker

            performance_tracker.add_signal(sid, sig, uid)
        except Exception:
            log.exception("performance record failed for %s", sig.pair)

    async def find_signal(self, record: bool = True, uid: int = 0) -> ScanResult:
        """
        بهترین سیگنال را پیدا می‌کند. uid = 0: با تنظیمات ادمین (برای کانال)؛ uid != 0: با تنظیمات مستقل همان کاربر.
        اسکن ادمین پشت قفل جداگانه است و اسکن کاربران با سقف هم‌زمانی (بقیه در صف) انجام می‌شود.
        """
        if self._lock is None or self._user_sem is None:
            return ScanResult(None, "error")
        if not self.bot_on():
            return ScanResult(None, "bot_off")
        d = self.db.scope(uid)
        gate = self._lock if not uid else self._user_sem
        async with gate:
            sides_ready = self.sides_ready()
            if not sides_ready:
                return ScanResult(None, "no_template")
            ok0, fail0 = self._scan_ok, self._scan_fail
            try:
                cands = await (self.analyze_all() if not uid else self.analyze_for(d))
            except Exception:
                log.exception("analyze failed (uid=%s)", uid)
                return ScanResult(None, "error")
            if not self.symbols:
                return ScanResult(None, "error")
            if not cands and self._scan_ok == ok0 and self._scan_fail - fail0 >= 5:
                return ScanResult(None, "error")  # همه‌ی درخواست‌های کندل شکست خورده‌اند

            recent = self.db.recent_symbols(24, uid) if d.get_bool("dedupe_on") else set()
            cands = [
                c for c in cands
                if not c.standby and c.side in sides_ready and c.pair not in recent and self.is_allowed(c.pair, d)
            ]
            cands.sort(key=lambda c: c.score, reverse=True)

            for cand in cands[:6]:
                sig = await self._make_signal(cand, d, uid)
                if sig is None:
                    continue
                if record:
                    self.record(sig, uid)
                return ScanResult(sig, "ok")
            return ScanResult(None, "none")

    async def _make_signal(self, cand: Candidate, d, uid: int = 0) -> Optional[Signal]:
        """کاندیدا -> سیگنال نهایی (قیمت لحظه‌ای، فاصله‌ی ورود، ساخت حد ضرر / هدف‌ها، تأیید AI)."""
        price = await self.last_price(cand.pair)
        if price is None:
            return None
        # قیمت لحظه‌ای نباید بسیار از کلوز تحلیل دور شده باشد
        if abs(price - cand.close) > config.MAX_ENTRY_DRIFT_ATR * cand.atr:
            return None
        sig = self.build_signal(cand, price, d)
        if sig is None:
            return None
        verdict = await self._ai_review(cand, sig, uid)
        if verdict.rejected:
            log.info("AI rejected %s %s: %s", cand.pair, cand.side, verdict.reason)
            return None
        sig.ai_info = verdict.info
        return sig

    async def find_watched(self, wanted: set, record: bool = True) -> ScanResult:
        """
        نمادهایی که هشدار آماده‌باش گرفته‌اند ({(pair, side)}): اگر در آخرین تحلیل ادمین از حالت آماده‌باش
        درآمده و سیگنال کامل شده‌اند، همان نماد بلافاصله (بدون انتظار برای فاصله‌ی ارسال) سیگنال می‌شود.
        """
        if self._lock is None or not wanted or not self.bot_on():
            return ScanResult(None, "none")
        if time.time() - self.last_scan_at > 600:
            return ScanResult(None, "none")
        async with self._lock:
            sides_ready = self.sides_ready()
            if not sides_ready:
                return ScanResult(None, "no_template")
            recent = self.db.recent_symbols(24, 0) if self.db.get_bool("dedupe_on") else set()
            cands = [
                c for c in self.last_scan
                if (c.pair, c.side) in wanted and not c.standby and c.side in sides_ready
                and c.pair not in recent and self.is_allowed(c.pair)
            ]
            cands.sort(key=lambda c: c.score, reverse=True)
            for cand in cands:
                sig = await self._make_signal(cand, self.db, 0)
                if sig is None:
                    continue
                if record:
                    self.record(sig, 0)
                return ScanResult(sig, "ok")
            return ScanResult(None, "none")


engine = SignalEngine(db)
