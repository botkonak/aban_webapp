"""موتور تحلیل ساختاری آبان ترید.

این ماژول کاملاً مستقل از موتور سیگنال ربات کار می‌کند و فقط روی کندل‌های OHLCV
الگوهای ساختاری (خط روند، کانال، حمایت/مقاومت، اردر بلاک، هارمونیک، الگوهای کلاسیک،
کندل استیک، ایچیموکو، واگرایی، چنگال اندروز، اندیکاتورها و ...) را تشخیص می‌دهد.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

GREEN = "#16b981"
RED = "#ef4d5a"
AMBER = "#f2b84b"
BLUE = "#4b8ef7"
PURPLE = "#9b7bf6"
CYAN = "#3ec7d6"
MUTED = "#8b93a7"


# ----------------------------------------------------------------------------
# مدل داده
# ----------------------------------------------------------------------------
@dataclass
class MarketCandles:
    t: np.ndarray
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray
    c: np.ndarray
    v: np.ndarray
    symbol: str = ""
    exchange: str = ""
    timeframe: str = ""

    def __len__(self) -> int:
        return int(len(self.c))

    @classmethod
    def from_rows(cls, rows: Sequence[Sequence[float]], **meta) -> "MarketCandles":
        arr = np.asarray(rows, dtype=float)
        if arr.ndim != 2 or arr.shape[0] == 0 or arr.shape[1] < 5:
            raise ValueError("invalid candles")
        arr = arr[np.argsort(arr[:, 0])]
        if arr.shape[1] < 6:
            arr = np.column_stack([arr, np.zeros(len(arr))])
        return cls(
            t=arr[:, 0].copy(), o=arr[:, 1].copy(), h=arr[:, 2].copy(),
            l=arr[:, 3].copy(), c=arr[:, 4].copy(), v=arr[:, 5].copy(), **meta,
        )


@dataclass
class Strategy:
    key: str
    title: str
    category: str
    strategy_type: str
    signal_type: str
    logic_key: str
    logic_params: str = ""
    description: str = ""

    @property
    def direction(self) -> str:
        return "LONG" if self.signal_type == "حمایت" else "SHORT"


@dataclass
class Detection:
    strategy: Strategy
    index: int
    price: float
    overlay: Dict[str, Any] = field(default_factory=dict)


# ----------------------------------------------------------------------------
# اندیکاتورها
# ----------------------------------------------------------------------------
def ema(x: np.ndarray, n: int) -> np.ndarray:
    x = np.asarray(x, dtype=float)
    out = np.full(len(x), np.nan)
    if len(x) == 0 or n <= 0:
        return out
    alpha = 2.0 / (n + 1.0)
    out[0] = x[0]
    for i in range(1, len(x)):
        out[i] = alpha * x[i] + (1 - alpha) * out[i - 1]
    return out


def sma(x: np.ndarray, n: int) -> np.ndarray:
    x = np.asarray(x, dtype=float)
    out = np.full(len(x), np.nan)
    if len(x) < n or n <= 0:
        return out
    cs = np.cumsum(np.insert(x, 0, 0.0))
    out[n - 1:] = (cs[n:] - cs[:-n]) / n
    return out


def sma_skip_nan(x: np.ndarray, n: int) -> np.ndarray:
    """میانگین ساده روی رشته‌ای که ابتدایش گرم‌کردنی و ناخوانا (NaN) است؛ مثل %D روی %K.

    sma معمولی با یک NaN در ابتدای رشته تمام خروجی را NaN می‌کند، پس شاخص‌های
    دو-مرحله‌ای (استوکستیک، میانگینِ روی RSI) هیچ‌وقت سیگنال نمی‌سازند.
    """
    x = np.asarray(x, dtype=float)
    out = np.full(len(x), np.nan)
    if n <= 0 or len(x) < n:
        return out
    window: List[float] = []
    for i in range(len(x)):
        v = float(x[i])
        if not np.isfinite(v):
            continue
        window.append(v)
        if len(window) > n:
            window.pop(0)
        if len(window) == n:
            out[i] = float(sum(window) / n)
    return out


def rma(x: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(x), np.nan)
    if len(x) == 0 or n <= 0:
        return out
    out[0] = x[0]
    alpha = 1.0 / n
    for i in range(1, len(x)):
        out[i] = alpha * x[i] + (1 - alpha) * out[i - 1]
    return out


def rsi(c: np.ndarray, n: int = 14) -> np.ndarray:
    c = np.asarray(c, dtype=float)
    out = np.full(len(c), np.nan)
    if len(c) <= n:
        return out
    d = np.diff(c, prepend=c[0])
    gain = np.where(d > 0, d, 0.0)
    loss = np.where(d < 0, -d, 0.0)
    ag, al = rma(gain, n), rma(loss, n)
    with np.errstate(divide="ignore", invalid="ignore"):
        rs = np.where(al == 0, np.inf if ag.any() else 0.0, ag / al)
        out = 100.0 - 100.0 / (1.0 + rs)
    out[np.isnan(out)] = 50.0
    return out


def atr(h: np.ndarray, l: np.ndarray, c: np.ndarray, n: int = 14) -> np.ndarray:
    pc = np.roll(c, 1)
    pc[0] = c[0]
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    return rma(tr, n)


def macd(c: np.ndarray, fast: int = 12, slow: int = 26, signal: int = 9) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    line = ema(c, fast) - ema(c, slow)
    sig = ema(line, signal)
    return line, sig, line - sig


def bollinger(c: np.ndarray, n: int = 20, mult: float = 2.0) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    mid = sma(c, n)
    out_std = np.full(len(c), np.nan)
    for i in range(n - 1, len(c)):
        out_std[i] = float(np.std(c[i - n + 1:i + 1]))
    return mid + mult * out_std, mid, mid - mult * out_std


def stochastic(h: np.ndarray, l: np.ndarray, c: np.ndarray, k: int = 14, d: int = 3) -> Tuple[np.ndarray, np.ndarray]:
    kk = np.full(len(c), np.nan)
    for i in range(k - 1, len(c)):
        hi = float(np.max(h[i - k + 1:i + 1]))
        lo = float(np.min(l[i - k + 1:i + 1]))
        kk[i] = 50.0 if hi == lo else (c[i] - lo) / (hi - lo) * 100.0
    return kk, sma_skip_nan(kk, d)


def cci(h: np.ndarray, l: np.ndarray, c: np.ndarray, n: int = 20) -> np.ndarray:
    tp = (h + l + c) / 3.0
    ma = sma(tp, n)
    out = np.full(len(c), np.nan)
    for i in range(n - 1, len(c)):
        md = float(np.mean(np.abs(tp[i - n + 1:i + 1] - ma[i])))
        out[i] = 0.0 if md == 0 else (tp[i] - ma[i]) / (0.015 * md)
    return out


def ichimoku(h: np.ndarray, l: np.ndarray, c: np.ndarray):
    n = len(c)

    def mid(win: int) -> np.ndarray:
        out = np.full(n, np.nan)
        for i in range(win - 1, n):
            out[i] = (float(np.max(h[i - win + 1:i + 1])) + float(np.min(l[i - win + 1:i + 1]))) / 2.0
        return out

    tenkan, kijun = mid(9), mid(26)
    span_a = np.full(n, np.nan)
    span_b = np.full(n, np.nan)
    for i in range(26, n):
        span_a[i] = (tenkan[i - 26] + kijun[i - 26]) / 2.0
    span_b[26:] = mid(52)[:-26]
    chikou = np.full(n, np.nan)
    if n > 26:
        chikou[:-26] = c[26:]
    return tenkan, kijun, span_a, span_b, chikou


def adx(h: np.ndarray, l: np.ndarray, c: np.ndarray, n: int = 14) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """اندیکس میانگین جهت‌دار (ADX) به‌همراه خطوط قدرت روند مثبت و منفی."""
    h = np.asarray(h, dtype=float)
    l = np.asarray(l, dtype=float)
    pc = np.roll(c, 1)
    pc[0] = c[0]
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    up = np.diff(h, prepend=h[0])
    dn = -np.diff(l, prepend=l[0])
    pdm = np.where((up > dn) & (up > 0), up, 0.0)
    mdm = np.where((dn > up) & (dn > 0), dn, 0.0)
    atrn = rma(tr, n)
    with np.errstate(divide="ignore", invalid="ignore"):
        safe = np.where(atrn <= 0, np.nan, atrn)
        pdi = np.nan_to_num(100.0 * rma(pdm, n) / safe)
        mdi = np.nan_to_num(100.0 * rma(mdm, n) / safe)
        den = pdi + mdi
        dx = np.where(den <= 0, 0.0, 100.0 * np.abs(pdi - mdi) / den)
    return np.nan_to_num(rma(dx, n)), pdi, mdi


def sar(h: np.ndarray, l: np.ndarray, c: np.ndarray, af_step: float = 0.02, af_max: float = 0.2) -> np.ndarray:
    """سار پارابولیک؛ سطح متحرک حد ضرر و مرز تغییر روند."""
    n = len(h)
    out = np.full(n, np.nan)
    if n < 2:
        return out
    bull = bool(h[1] >= h[0])
    af = float(af_step)
    ep = float(h[0] if bull else l[0])
    cur = float(l[0] if bull else h[0])
    out[0] = cur
    for i in range(1, n):
        prev = cur + af * (ep - cur)
        if bull:
            prev = min(prev, float(l[i - 1]), float(l[i - 2]) if i >= 2 else float(l[i - 1]))
            if float(l[i]) < prev:
                bull, cur, ep, af = False, ep, float(l[i]), float(af_step)
            else:
                if float(h[i]) > ep:
                    ep, af = float(h[i]), min(af + af_step, af_max)
                cur = prev
        else:
            prev = max(prev, float(h[i - 1]), float(h[i - 2]) if i >= 2 else float(h[i - 1]))
            if float(h[i]) > prev:
                bull, cur, ep, af = True, ep, float(h[i]), float(af_step)
            else:
                if float(l[i]) < ep:
                    ep, af = float(l[i]), min(af + af_step, af_max)
                cur = prev
        out[i] = cur
    return out


def vwap(t: np.ndarray, h: np.ndarray, l: np.ndarray, c: np.ndarray, v: np.ndarray) -> np.ndarray:
    """VWAP روزانه؛ میانگین وزنی قیمت به حجم از اولین کندل هر روز."""
    n = len(c)
    out = np.full(n, np.nan)
    tp = (np.asarray(h, dtype=float) + np.asarray(l, dtype=float) + np.asarray(c, dtype=float)) / 3.0
    vol = np.asarray(v, dtype=float)
    day = 86_400_000.0
    anchor = None
    pv = 0.0
    vv = 0.0
    for i in range(n):
        key = int(float(t[i]) // day) if np.isfinite(t[i]) else 0
        if key != anchor:
            anchor, pv, vv = key, 0.0, 0.0
        w = max(float(vol[i]), 0.0)
        pv += float(tp[i]) * w
        vv += w
        out[i] = pv / vv if vv > 0 else float(tp[i])
    return out


# ----------------------------------------------------------------------------
# پیوت و خط
# ----------------------------------------------------------------------------
def _pivots(h: np.ndarray, l: np.ndarray, strength: int = 3) -> Tuple[List[Tuple[int, float]], List[Tuple[int, float]]]:
    highs: List[Tuple[int, float]] = []
    lows: List[Tuple[int, float]] = []
    n = len(h)
    s = max(1, int(strength))
    for i in range(s, n - s):
        lo = i - s
        hi = i + s + 1
        hv = h[lo:hi]
        lv = l[lo:hi]
        if h[i] >= float(np.max(hv)) and h[i] > float(np.max(h[i - s:i])):
            highs.append((i, float(h[i])))
        if l[i] <= float(np.min(lv)) and l[i] < float(np.min(l[i - s:i])):
            lows.append((i, float(l[i])))
    return highs, lows


def _fit_line(points: Sequence[Tuple[int, float]], at: int) -> Optional[Tuple[float, float, Tuple[int, float], Tuple[int, float]]]:
    if len(points) < 2:
        return None
    (x1, y1), (x2, y2) = points[-2], points[-1]
    if x2 == x1:
        return None
    slope = (y2 - y1) / float(x2 - x1)
    value = y1 + slope * (at - x1)
    return float(value), float(slope), (x1, y1), (x2, y2)


def _line_between(p1: Tuple[int, float], p2: Tuple[int, float], at: int) -> float:
    if p2[0] == p1[0]:
        return float(p2[1])
    return float(p1[1] + (p2[1] - p1[1]) * (at - p1[0]) / (p2[0] - p1[0]))


def _project(value: float, slope: float, idx: int, target: int, use_log: bool = False) -> float:
    """مقدار یک خط روند در ایندکس مقصد؛ شیب لگاریتمی در صورت نیاز برگردانده می‌شود."""
    dist = float(target) - float(idx)
    if not np.isfinite(slope) or dist == 0:
        return float(value)
    if use_log:
        return float(math.exp(math.log(max(float(value), 1e-12)) + float(slope) * dist))
    return float(value + float(slope) * dist)


def _tpoint(ctx: "Context", idx: int, value: float) -> Dict[str, Any]:
    idx = max(0, min(idx, len(ctx.c) - 1))
    val = float(value)
    if not np.isfinite(val):
        val = float(ctx.c[idx])
    return {"time": int(ctx.t[idx] / 1000), "value": val}


def _safe(ctx: "Context", value: float, idx: int = -1) -> float:
    """بازگرداندن یک عدد قابل‌اتّصال به JSON؛ مقدار نامتناهی به قیمت همان کندل تبدیل می‌شود."""
    val = float(value)
    if np.isfinite(val):
        return val
    return float(ctx.c[max(0, min(idx, ctx.n - 1))])


def _line(ctx: "Context", p1: Tuple[int, float], p2: Tuple[int, float], color: str = MUTED,
          title: str = "", dashed: bool = False) -> Dict[str, Any]:
    return {
        "points": [_tpoint(ctx, p1[0], p1[1]), _tpoint(ctx, p2[0], p2[1])],
        "color": color, "title": title, "dashed": dashed,
    }


def _level(ctx: "Context", price: float, color: str, title: str = "") -> Dict[str, Any]:
    return {"price": _safe(ctx, price), "color": color, "title": title}


def _lpoints(ctx: "Context", seq: List[Tuple[int, float]], names: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """نقاط ساختار با برچسب (X، A، B، کف، قله و ...) تا الگو روی چارت شناخته شود."""
    names = names or []
    out: List[Dict[str, Any]] = []
    for i, x in enumerate(seq):
        idx = max(0, min(int(x[0]), len(ctx.c) - 1))
        out.append({
            "time": int(ctx.t[idx] / 1000), "value": float(x[1]),
            "label": names[i] if i < len(names) else "",
        })
    return out


def _zone(ctx: "Context", top: float, bottom: float, start: int, end: int, color: str, title: str = "") -> Dict[str, Any]:
    return {
        "top": _safe(ctx, top), "bottom": _safe(ctx, bottom),
        "from": int(ctx.t[max(0, min(start, len(ctx.c) - 1))] / 1000),
        "to": int(ctx.t[max(0, min(end, len(ctx.c) - 1))] / 1000),
        "color": color, "title": title,
    }


def _ray(ctx: "Context", p1: Tuple[int, float], p2: Tuple[int, float], extend: str = "right",
         color: str = MUTED, title: str = "", dashed: bool = False) -> Dict[str, Any]:
    """پرتو قیمتی؛ ادامهٔ خطِ عبوری از دو نقطه به راست/چپ چارت، آن‌طور که خط روند دستی کشیده می‌شود."""
    return {
        "points": [_tpoint(ctx, p1[0], p1[1]), _tpoint(ctx, p2[0], p2[1])],
        "extend": extend, "color": color, "title": title, "dashed": bool(dashed),
    }


def _label(ctx: "Context", idx: int, value: float, text: str, color: str = MUTED) -> Dict[str, Any]:
    """برچسب متنی روی چارت در مختصات یک کندل و قیمت مشخص."""
    pt = _tpoint(ctx, idx, value)
    return {"time": pt["time"], "value": pt["value"], "text": str(text), "color": color}


_FIB_COLORS = (
    (0.0, MUTED), (0.236, BLUE), (0.382, BLUE), (0.5, AMBER), (0.618, PURPLE),
    (0.707, CYAN), (0.786, CYAN), (0.886, AMBER), (1.0, MUTED), (1.13, BLUE),
    (1.272, GREEN), (1.414, CYAN), (1.618, RED), (2.0, GREEN), (2.618, AMBER),
    (3.618, PURPLE), (4.236, CYAN),
)


def _fib(ctx: "Context", p1: Tuple[int, float], p2: Tuple[int, float], title: str = "",
         ratios: Optional[Sequence[float]] = None) -> Dict[str, Any]:
    """شبکه فیبوناچی میان دو نقطه برای اندازه‌گیری عمق بازگشت و اهداف قیمتی."""
    seq = list(ratios) if ratios else [0.0, 0.382, 0.5, 0.618, 0.786, 1.0, 1.272, 1.618]
    levels: List[Dict[str, Any]] = []
    for r in seq:
        ratio = float(r)
        color = next((c for k, c in _FIB_COLORS if abs(k - ratio) < 1e-6), MUTED)
        levels.append({"ratio": ratio, "label": "%g%%" % (ratio * 100.0), "color": color})
    return {
        "title": title, "from": _tpoint(ctx, p1[0], p1[1]), "to": _tpoint(ctx, p2[0], p2[1]),
        "levels": levels,
    }


def _indicators(*tokens: Any) -> List[str]:
    """توکن‌های اعلامی اندیکاتور؛ کلاینت خود منحنی را از کندل‌های موجود می‌سازد."""
    out: List[str] = []
    for t in tokens:
        token = str(t)
        if token and token not in out:
            out.append(token)
    return out


def _sample_idx(count: int, keep: int = 14) -> List[int]:
    """نمونه‌برداری یکنواخت از یک بازه تا حجم داده پنل کوچک بماند."""
    n = int(count)
    if n <= 0:
        return []
    keep = max(2, min(int(keep), n))
    if n <= keep:
        return list(range(n))
    step = (n - 1) / float(keep - 1)
    return sorted({int(round(k * step)) for k in range(keep)} | {n - 1})


def _series(ctx: "Context", arr: Sequence[float], start: int, end: int, title: str,
            color: str = MUTED, dashed: bool = False, keep: int = 14) -> Dict[str, Any]:
    """یک رشتهٔ پنل از مقادیر شاخص در بازه‌ای مشخص؛ مقادیر ناخوانا حذف می‌شوند."""
    a = max(0, min(int(start), ctx.n - 1))
    b = max(a, min(int(end), ctx.n - 1))
    pts: List[Dict[str, Any]] = []
    for off in _sample_idx(b - a + 1, keep):
        k = a + int(off)
        val = float(arr[k])
        if not np.isfinite(val):
            continue
        pts.append({"time": int(ctx.t[k] / 1000), "value": val})
    return {"title": title, "color": color, "dashed": bool(dashed), "points": pts}


def _plevel(value: float, color: str, title: str = "") -> Dict[str, Any]:
    """سطح افقی داخل پنل اندیکاتور (مثل آستانه ۷۰ آر‌اس‌آی)."""
    return {"value": float(value), "color": color, "title": title}


def _panel(key: str, title: str, series: Sequence[Dict[str, Any]],
           levels: Optional[Sequence[Dict[str, Any]]] = None, height: int = 0, zero: bool = False) -> Dict[str, Any]:
    """پنل مستقل برای اعدادی که خودِ تشخیص مهم‌اند (مختصات آن‌ها محور شاخص است، نه قیمت)."""
    out: Dict[str, Any] = {
        "key": key, "title": title,
        "series": [s for s in (series or []) if s and s.get("points")],
    }
    if levels:
        out["levels"] = list(levels)
    if height:
        out["height"] = int(height)
    if zero:
        out["zero"] = True
    if not out["series"] and not out.get("levels"):
        return {}
    return out


def _overlay(lines=None, levels=None, zones=None, points=None, marker=None,
             rays=None, labels=None, fib=None, panels=None, indicators=None) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key, val in (("lines", lines), ("rays", rays), ("levels", levels), ("zones", zones),
                     ("points", points), ("labels", labels), ("fib", fib), ("panels", panels)):
        if val:
            out[key] = val if isinstance(val, list) else [val]
    if marker:
        out["markers"] = [marker]
    if indicators:
        tokens = indicators if isinstance(indicators, (list, tuple)) else [indicators]
        ind = _indicators(*tokens)
        if ind:
            out["indicators"] = ind
    return out


_OVERLAY_LISTS = ("lines", "rays", "levels", "zones", "points", "markers", "labels", "fib", "panels")

# هندسهٔ قابل برچسب روی چارت؛ «markers» و «panels» همیشه متعلق به خودِ استراتژی‌اند.
_GEOM_KEYS = ("lines", "rays", "levels", "zones", "points", "labels", "fib")


def _mark_context(ov: Dict[str, Any]) -> Dict[str, Any]:
    """برچسب زدن ابزارهای عمومیِ اطراف سیگنال.

    سقف/کف پنجره، منحنی‌های EMA پیش‌فرض، پرتو ادامهٔ روند و شبکهٔ فیبوناچیِ آخرین جنبش،
    هندسهٔ خودِ استراتژی نیستند؛ اگر روی لایه بمانند، کاربر لایهٔ یک الگوی کندلی را با
    خطوط روندِ استراتژی دیگر قاطی می‌کند. `_trim_overlay` همین برچسب‌ها را پاک می‌کند.
    """
    for key in _GEOM_KEYS:
        for item in ov.get(key) or []:
            if isinstance(item, dict):
                item["ctx"] = 1
    tokens = ov.get("indicators")
    if tokens:
        ov["ctx_indicators"] = list(tokens)
        ov.pop("indicators")
    return ov


def _mark_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    for item in items:
        if isinstance(item, dict):
            item["ctx"] = 1
    return items


def _has_own_geometry(ov: Dict[str, Any]) -> bool:
    """آیا استراتژی خودش خط/سطح/ناحیهٔ معنادار تولید کرده؟"""
    for key in _GEOM_KEYS:
        vals = ov.get(key) or []
        if any(isinstance(x, dict) and not x.get("ctx") for x in vals):
            return True
    return False


# الگوهای کندلی فقط «علامت روی کندل» می‌خواهند: فلش، کادرِ الگو، پاره‌خط‌های کوتاه سقف/کف
# و برچسب‌ها. پرتوها، سطوح افقی سراسری، شبکهٔ فیبوناچی و منحنی‌های اندیکاتور حذف می‌شوند.
_PATTERN_ONLY_LOGIC = {"candlestick": ("rays", "levels", "fib", "indicators")}

# منحنی‌های پنل قیمت که «موضوع» هیچ الگوی ساختاری نیستند؛ کلاینت آن‌ها را روی کل
# پنجره می‌کشد و روی چارت کاربر شبیه خطوط بی‌ربط استراتژی‌های دیگر می‌شود.
# بولینجر و ایچیموکو حذف نمی‌شوند چون خودِ موضوع آن استراتژی‌هااند.
_CONTEXT_CURVE_TOKENS = ("ema", "sma", "vwap", "sar")


def _drop_context_curves(tokens: List[Any], own_titles: str) -> List[str]:
    """حذف توکن منحنی‌های عمومی، مگر اینکه خودِ استراتژی همان خانواده را رسم کرده باشد."""
    kept: List[str] = []
    for t in tokens or []:
        name = str(t).split(":")[0].strip().lower()
        if name in _CONTEXT_CURVE_TOKENS and name not in own_titles:
            continue
        kept.append(str(t))
    return kept


def _own_line_titles(items: List[Any]) -> str:
    parts: List[str] = []
    for it in items or []:
        if isinstance(it, dict):
            parts.append(str(it.get("title") or ""))
    return " ".join(parts).lower()


def _trim_overlay(s: "Strategy", ov: Dict[str, Any]) -> Dict[str, Any]:
    """فیلتر نهایی هر overlay: فقط هندسهٔ خودِ استراتژی روی چارت می‌ماند.

    استراتژی‌ای که خودش هندسه ندارد (مثلاً SIGNALهای شاخص‌محورِ ساده یا منطق fallback)
    ابزار عمومی را نگه می‌دارد تا نمودار بی‌محتوا نماند.
    """
    out = dict(ov or {})
    if _has_own_geometry(out):
        own_titles = ""
        for key in _GEOM_KEYS:
            vals = out.get(key)
            if not isinstance(vals, list):
                continue
            mine = [x for x in vals if not (isinstance(x, dict) and x.get("ctx"))]
            if mine:
                out[key] = mine
                if key == "lines":
                    own_titles = _own_line_titles(mine)
            else:
                out.pop(key, None)
        out.pop("ctx_indicators", None)
        curves = _drop_context_curves(out.get("indicators"), own_titles)
        if curves:
            out["indicators"] = _indicators(*curves)
        else:
            out.pop("indicators", None)
    else:
        ctx_tokens = out.pop("ctx_indicators", None)
        if ctx_tokens:
            kept = _indicators(*(out.get("indicators") or []), *ctx_tokens)
            if kept:
                out["indicators"] = kept
    for key in _PATTERN_ONLY_LOGIC.get(str(getattr(s, "logic_key", "") or ""), ()):
        out.pop(key, None)
    for key in _OVERLAY_LISTS:
        vals = out.get(key)
        if not vals:
            out.pop(key, None)
            continue
        # برچسب داخلی دیگر لازم نیست؛ در پاسخ وب هم نباید دیده شود.
        if isinstance(vals, list):
            for item in vals:
                if isinstance(item, dict):
                    item.pop("ctx", None)
    return out


def _merge_overlay(dst: Dict[str, Any], src: Dict[str, Any]) -> Dict[str, Any]:
    """ادغام دو overlay با حذف تکراری‌ها؛ برای افزودن ابزار نموداری به overlayهای آماده."""
    for key in _OVERLAY_LISTS:
        vals = (src or {}).get(key) or []
        if not isinstance(vals, list) or not vals:
            continue
        kept = dst.setdefault(key, [])
        seen = {repr(x) for x in kept}
        for item in vals:
            if repr(item) in seen:
                continue
            seen.add(repr(item))
            kept.append(item)
    ind = (src or {}).get("indicators") or []
    if ind:
        merged = _indicators(*(dst.get("indicators") or []), *ind)
        if merged:
            dst["indicators"] = merged
    return dst


def _cap_overlay(ov: Dict[str, Any], cap: int = 40, ind_cap: int = 24) -> Dict[str, Any]:
    """برش لیست‌های overlay به مرتبط‌ترین عنصرها.

    کلاینت هم سقف ۴۰ دارد، اما اگر تشخیصی هزار عنصر تولید کند حجم پاسخ وب بالا می‌رود؛
    هندسهٔ اصلیِ الگو (خط/سطح/ناحیه) از سر لیست و نشانه‌های زمانی (نقطه/برچسب/فلش) از
    انتهای لیست نگه داشته می‌شوند تا نزدیک‌ترین‌ها به کندل سیگنال باقی بمانند.
    """
    for key in _OVERLAY_LISTS:
        vals = ov.get(key)
        if isinstance(vals, list) and len(vals) > cap:
            ov[key] = vals[-cap:] if key in ("points", "markers", "labels") else vals[:cap]
    ind = ov.get("indicators")
    if isinstance(ind, list) and len(ind) > ind_cap:
        ov["indicators"] = ind[:ind_cap]
    return ov


def _marker(ctx: "Context", idx: int, direction: str, text: str = "") -> Dict[str, Any]:
    return {
        "time": int(ctx.t[idx] / 1000),
        "position": "belowBar" if direction == "LONG" else "aboveBar",
        "color": GREEN if direction == "LONG" else RED,
        "shape": "arrowUp" if direction == "LONG" else "arrowDown",
        "text": text,
    }


def _signal_context(ctx: "Context", i: int, lookback: int = 40) -> Dict[str, Any]:
    """بافت قیمتی یک سیگنال شاخص-محور: سطوح سقف/کف، نقاط چرخش و روند EMA."""
    a = max(0, i - lookback)
    span = slice(a, i + 1)
    lo_i = a + int(np.argmin(ctx.l[span]))
    hi_i = a + int(np.argmax(ctx.h[span]))
    highs, lows = ctx.pivots(3)
    marked = sorted(
        [(x[0], x[1], "کف") for x in lows if a <= x[0] <= i]
        + [(x[0], x[1], "قله") for x in highs if a <= x[0] <= i],
        key=lambda x: x[0],
    )[-6:]
    e20 = ctx.E(20)
    e50 = ctx.E(50)
    from_i = max(a, i - 20)
    lines: List[Dict[str, Any]] = []
    rays: List[Dict[str, Any]] = []
    zones: List[Dict[str, Any]] = []
    # منحنی واقعی میانگین‌ها روی چارت (نه یک خط صافِ دو-نقطه‌ای)
    lines += _curve(ctx, e20, a, i, BLUE, "EMA 20", True, keep=9)
    lines += _curve(ctx, e50, a, i, PURPLE, "EMA 50", True, keep=8)
    if np.isfinite(e20[from_i]) and np.isfinite(e20[i]):
        slope = (float(e20[i]) - float(e20[max(0, i - 10)])) / float(max(1, min(i, 10)))
        far = min(ctx.n - 1, i + max(6, lookback // 3))
        if slope and np.isfinite(slope):
            rays.append(_ray(ctx, (i, float(e20[i])), (far, float(e20[i]) + slope * (far - i)),
                             "right", BLUE, "ادامه روند (EMA 20)", True))
    if np.isfinite(e20[i]) and np.isfinite(e50[i]):
        zones.append(_zone(ctx, max(float(e20[i]), float(e50[i])), min(float(e20[i]), float(e50[i])),
                          a, i, MUTED, "فاصلهٔ EMA 20 تا 50"))
    return _mark_context(_overlay(
        lines=lines,
        rays=rays,
        levels=[
            _level(ctx, float(ctx.h[hi_i]), RED, f"سقف {lookback} کندل"),
            _level(ctx, float(ctx.l[lo_i]), GREEN, f"کف {lookback} کندل"),
        ],
        zones=zones,
        points=[{"time": int(ctx.t[x[0]] / 1000), "value": float(x[1]), "label": x[2]} for x in marked],
        indicators=_indicators("ema:20", "ema:50"),
    ))


def _marker_overlay(ctx: "Context", i: int, direction: str, text: str = "",
                    lookback: int = 40, add: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Overlay برای سیگنال‌های شاخص‌محور: نشانهٔ جهت به‌همراه ساختار قیمتی اطراف آن."""
    out = _signal_context(ctx, i, lookback)
    out["markers"] = [_marker(ctx, i, direction, text)]
    if add:
        out = _merge_overlay(out, add)
    return _cap_overlay(out)


def _impulse_span(ctx: "Context", i: int, lookback: int = 40) -> Tuple[int, float, int, float]:
    """کف و قلهٔ آخرین جنبش معنادار قیمت در پنجرهٔ اطراف سیگنال؛ مبنای فیبوناچی و سطوح."""
    a = max(0, i - int(lookback))
    span = slice(a, i + 1)
    lo_i = a + int(np.argmin(ctx.l[span]))
    hi_i = a + int(np.argmax(ctx.h[span]))
    return lo_i, float(ctx.l[lo_i]), hi_i, float(ctx.h[hi_i])


def _index_toolkit(ctx: "Context", i: int, direction: str, color: str, lookback: int = 40,
                   curves: Sequence[Tuple[int, str, str]] = (),
                   extra_ind: Sequence[str] = ()) -> Dict[str, Any]:
    """ابزار مشترک سیگنال‌های شاخص‌محور (RSI، MACD، CCI، استوکستیک، حجم، بولینگر).

    شاخص تنها «زمان» سیگنال را می‌دهد و برای نمایش یک استراتژی کافی نیست؛ این تابع
    ساختار قیمتیِ همان سیگنال را هم رسم می‌کند: سطوح حمایت/مقاومت پنجره، ناحیهٔ دامنهٔ
    نوسان، منحنی‌های میانگین متحرک به‌جای خط صاف، شبکهٔ فیبوناچی روی آخرین جنبش و پرتو
    هدف اندازه‌گیری‌شده. خروجی برای `_merge_overlay` مناسب است.
    """
    lo_i, lo_p, hi_i, hi_p = _impulse_span(ctx, i, lookback)
    a = max(0, i - int(lookback))
    rng = max(hi_p - lo_p, 1e-9)
    up = direction in ("up", "buy", "long", "above")
    target = (lo_p + rng * 1.618) if up else (hi_p - rng * 1.618)
    retr = (hi_p - rng * 0.618) if up else (lo_p + rng * 0.618)
    far = _far_index(ctx, i, 16)
    lines: List[Dict[str, Any]] = []
    inds: List[str] = []
    for period, ttl, col in curves:
        lines += _curve(ctx, ctx.E(period), a, i, col, ttl, True, keep=8)
        inds.append(f"ema:{period}")
    return _mark_context(_overlay(
        lines=lines,
        rays=[_ray(ctx, (i, float(ctx.c[i])), (far, target), "right", color,
                   "هدف اندازه‌گیری آخرین جنبش", True)],
        levels=[_level(ctx, hi_p, RED, f"سقف {int(lookback)} کندل"),
                _level(ctx, lo_p, GREEN, f"کف {int(lookback)} کندل"),
                _level(ctx, retr, PURPLE, "بازگشت ۶۱.۸٪ جنبش"),
                _level(ctx, target, color, "هدف ۱۶۱.۸٪")],
        zones=[_zone(ctx, hi_p, lo_p, a, i, MUTED, f"دامنه نوسان {int(lookback)} کندل")],
        points=_lpoints(ctx, [(lo_i, lo_p), (hi_i, hi_p)], ["کف جنبش", "قله جنبش"]),
        fib=[_fib(ctx, (lo_i, lo_p), (hi_i, hi_p), "فیبوناچی آخرین جنبش",
                  [0.382, 0.5, 0.618, 0.786, 1.0, 1.272, 1.618])],
        indicators=_indicators(*(inds + list(extra_ind))),
    ))


def _stop_levels(ctx: "Context", i: int) -> Tuple[List[Dict[str, Any]], List[str]]:
    """سطوح مدیریت ریسک روی چارت (SAR و VWAP) به‌همراه اندیکاتورهای اعلامی آن‌ها.

    این سطوح برای همهٔ استراتژی‌ها یکسان‌اند و هندسهٔ خودِ الگو نیستند، پس برچسب
    عمومی می‌گیرند و فقط روی لایه‌های بی‌هندسه (اندیکاتورهای خالص) نگه داشته می‌شوند.
    """
    levels: List[Dict[str, Any]] = []
    k = max(0, min(i, ctx.n - 1))
    s = float(ctx.SAR()[k])
    if np.isfinite(s) and s > 0:
        levels.append(_level(ctx, s, AMBER, "حد ضرر (SAR)"))
    w = float(ctx.VWAP()[k])
    if np.isfinite(w) and w > 0:
        levels.append(_level(ctx, w, CYAN, "VWAP روزانه"))
    return _mark_items(levels), _indicators("sar:0.02:0.2", "vwap")


def _far_index(ctx: "Context", i: int, ahead: int = 12) -> int:
    """ایندکس انتهایی مناسب برای امتداد دادن خطوط به سمت راست چارت."""
    return max(i, min(ctx.n - 1, int(i) + int(ahead)))


def _curve(ctx: "Context", arr: Sequence[float], start: int, end: int, color: str = MUTED,
           title: str = "", dashed: bool = False, keep: int = 10) -> List[Dict[str, Any]]:
    """منحنی روی چارت: یک رشتهٔ شاخص (میانگین متحرک، ابر ایچیموکو، بولینگر) به‌شکل چند پاره‌خط پیوسته.

    کلاینت تنها پاره‌خطِ دو-نقطه‌ای می‌کشد، پس برای دیدن «خط منحنی» واقعی شاخص، بازه به
    نمونه‌های یکنواخت تقسیم و قطعه‌های متوالی ساخته می‌شود؛ تعداد قطعه‌ها محدود می‌ماند
    تا حجم بارِ پاسخ وب کم بماند (برچسب روی آخرین قطعه، نزدیک‌ترین بخش به کندل جاری).
    """
    a = max(0, min(int(start), ctx.n - 1))
    b = max(a, min(int(end), ctx.n - 1))
    pts: List[Tuple[int, float]] = []
    for off in _sample_idx(b - a + 1, keep):
        k = a + int(off)
        val = float(arr[k])
        if not np.isfinite(val):
            continue
        if pts and k == pts[-1][0]:
            continue
        pts.append((k, val))
    if not pts:
        return []
    if len(pts) == 1:
        k, v = pts[0]
        return [_line(ctx, (k, v), (min(b, k + 1), v), color, title, dashed)]
    last = len(pts) - 1
    return [_line(ctx, pts[x], pts[x + 1], color, title if x == last - 1 else "", dashed)
            for x in range(last)]


# ----------------------------------------------------------------------------
# پارامترها
# ----------------------------------------------------------------------------
def parse_params(raw: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for part in (raw or "").split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def _f(params: Dict[str, str], key: str, default: float = 0.0) -> float:
    try:
        return float(params.get(key, default))
    except (TypeError, ValueError):
        return default


def _s(params: Dict[str, str], key: str, default: str = "") -> str:
    return str(params.get(key, default))


# ----------------------------------------------------------------------------
# Context: محاسبات مشترک هر نماد/تایم‌فریم
# ----------------------------------------------------------------------------
class Context:
    def __init__(self, candles: MarketCandles):
        self.candles = candles
        self.t = candles.t
        self.o, self.h, self.l, self.c, self.v = candles.o, candles.h, candles.l, candles.c, candles.v
        self.n = len(candles)
        self.atr = atr(self.h, self.l, self.c, 14)
        self._ema: Dict[int, np.ndarray] = {}
        self._sma: Dict[int, np.ndarray] = {}
        self._rsi: Dict[int, np.ndarray] = {}
        self._pivots: Dict[int, Tuple[List[Tuple[int, float]], List[Tuple[int, float]]]] = {}
        self._macd = macd(self.c)
        self._rsi14 = rsi(self.c, 14)
        self._boll = bollinger(self.c, 20, 2.0)
        self._stoch = stochastic(self.h, self.l, self.c)
        self._cci = cci(self.h, self.l, self.c)
        self._ich = ichimoku(self.h, self.l, self.c)
        self._vol_avg: Dict[int, np.ndarray] = {}
        self._adx: Dict[int, Tuple[np.ndarray, np.ndarray, np.ndarray]] = {}
        self._sar_arr: Optional[np.ndarray] = None
        self._vwap_arr: Optional[np.ndarray] = None

    def E(self, n: int) -> np.ndarray:
        if n not in self._ema:
            self._ema[n] = ema(self.c, n)
        return self._ema[n]

    def S(self, n: int) -> np.ndarray:
        if n not in self._sma:
            self._sma[n] = sma(self.c, n)
        return self._sma[n]

    def R(self, n: int = 14) -> np.ndarray:
        if n not in self._rsi:
            self._rsi[n] = rsi(self.c, n)
        return self._rsi[n]

    def ADX(self, n: int = 14) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        if n not in self._adx:
            self._adx[n] = adx(self.h, self.l, self.c, n)
        return self._adx[n]

    def SAR(self) -> np.ndarray:
        if self._sar_arr is None:
            self._sar_arr = sar(self.h, self.l, self.c)
        return self._sar_arr

    def VWAP(self) -> np.ndarray:
        if self._vwap_arr is None:
            self._vwap_arr = vwap(self.t, self.h, self.l, self.c, self.v)
        return self._vwap_arr

    def strength(self, i: int) -> float:
        """عدد قدرت روند (ADX) در کندل مورد نظر برای درج روی چارت."""
        d = self.ADX(14)[0]
        v = float(d[min(max(i, 0), self.n - 1)])
        return v if np.isfinite(v) else 0.0

    def pivots(self, strength: int = 3):
        if strength not in self._pivots:
            self._pivots[strength] = _pivots(self.h, self.l, strength)
        return self._pivots[strength]

    def vip_avg(self, window: int) -> np.ndarray:
        if window not in self._vol_avg:
            v = self.v
            out = np.full(self.n, np.nan)
            for i in range(window, self.n):
                out[i] = float(np.mean(v[i - window:i]))
            self._vol_avg[window] = out
        return self._vol_avg[window]

    def av(self, i: int) -> float:
        a = self.atr[min(max(i, 0), self.n - 1)]
        if not np.isfinite(a) or a <= 0:
            a = abs(float(self.c[i])) * 0.005 or 1e-9
        return float(a)

    def line_value(self, i: int, which: str = "low", slope: str = "any",
                   strength: int = 3, use_log: bool = False) -> Optional[Tuple[float, Tuple[int, float], Tuple[int, float], float]]:
        """خط روند از دو پیوت آخر قبل از i (با تأیید پیوت)."""
        highs, lows = self.pivots(strength)
        pts = lows if which == "low" else highs
        valid = [(x, y) for x, y in pts if x <= i - strength]
        if len(valid) < 2:
            return None
        best = None
        for j in range(len(valid) - 1, 0, -1):
            p1, p2 = valid[j - 1], valid[j]
            if p2[0] - p1[0] < 2:
                continue
            y1 = math.log(max(p1[1], 1e-12)) if use_log else p1[1]
            y2 = math.log(max(p2[1], 1e-12)) if use_log else p2[1]
            sl = (y2 - y1) / (p2[0] - p1[0])
            if slope == "up" and sl <= 1e-12:
                continue
            if slope == "down" and sl >= -1e-12:
                continue
            val = y1 + sl * (i - p1[0])
            if use_log:
                val = math.exp(val)
            best = (float(val), p1, p2, float(sl))
            break
        return best

    def sr_levels(self, i: int, tol_atr: float = 0.8, min_touches: int = 2,
                  strength: int = 3) -> List[Dict[str, Any]]:
        highs, lows = self.pivots(strength)
        pts = [p for p in highs + lows if p[0] <= i - strength]
        if not pts:
            return []
        tol = max(self.av(i) * max(0.1, tol_atr), abs(float(self.c[i])) * 1e-5)
        clusters: List[Dict[str, Any]] = []
        for idx, price in sorted(pts, key=lambda x: x[1]):
            placed = False
            for cl in clusters:
                if abs(price - cl["price"]) <= tol:
                    k = cl["count"]
                    cl["price"] = (cl["price"] * k + price) / (k + 1)
                    cl["count"] += 1
                    cl["idx"].append(idx)
                    placed = True
                    break
            if not placed:
                clusters.append({"price": price, "count": 1, "idx": [idx]})
        out = [c for c in clusters if c["count"] >= min_touches]
        out.sort(key=lambda c: abs(c["price"] - float(self.c[i])))
        return out


# ----------------------------------------------------------------------------
# تشخیص‌ها
# ----------------------------------------------------------------------------
def _trendline_touch(ctx: Context, s: Strategy, p: Dict[str, str], start: int, end: int) -> List[Detection]:
    side = _s(p, "side", "support")
    touch = int(_f(p, "touch", 1))
    use_log = s.logic_key.startswith("log_")
    slope = "any"
    title = s.title
    if "صعودی" in title:
        slope = "up"
    if "نزولی" in title:
        slope = "down"
    out: List[Detection] = []
    for i in range(start, end + 1):
        which = "low" if side == "support" else "high"
        fit = ctx.line_value(i, which, slope, use_log=use_log)
        if not fit:
            continue
        value, p1, p2, sl = fit
        a = ctx.av(i)
        if side == "support":
            hit = ctx.l[i] <= value + a * 0.35 and ctx.c[i] >= value - a * 0.15
        else:
            hit = ctx.h[i] >= value - a * 0.35 and ctx.c[i] <= value + a * 0.15
        if not hit:
            continue
        if touch and abs(ctx.c[i] - value) > a * 1.2:
            continue
        color = GREEN if side == "support" else RED
        name = "حمایت داینامیک" if side == "support" else "مقاومت داینامیک"
        stops, ind = _stop_levels(ctx, i)
        far = _far_index(ctx, i, 15)
        labels = [_label(ctx, i, float(ctx.h[i] if side == "support" else ctx.l[i]), "واکنش به خط روند", color)]
        extra_ind: List[str] = []
        if s.logic_key.startswith("special_"):
            avg = ctx.vip_avg(20)[i]
            if np.isfinite(avg) and avg > 0:
                labels.append(_label(ctx, i, float(ctx.c[i]), f"تأیید حجم {ctx.v[i] / float(avg):.1f}×", AMBER))
            extra_ind = _indicators("volume")
        # همهٔ چرخش‌های نشسته روی همین خط روند + history واکنش در نقاط p1 و p2
        touches = _line_touches(ctx, i, p1, p2, max(a * 0.6, abs(value) * 0.004), side != "support")[-6:]
        touch_pts = _lpoints(ctx, touches, [f"لمس {k + 1}" for k in range(len(touches))])
        base = float(np.min(ctx.l[max(0, i - 30):i + 1])) if side == "support" else float(np.max(ctx.h[max(0, i - 30):i + 1]))
        labels.append(_label(ctx, i, float(value),
                             f"شیب خط {sl:+.4g} · {len(touches)} لمس", PURPLE))
        out.append(Detection(s, i, float(ctx.c[i]), _cap_overlay(_overlay(
            lines=([_line(ctx, p1, p2, color, "خط روند"),
                    _line(ctx, (p2[0], p2[1]), (i, value), color, "", True),
                    _line(ctx, (i, float(ctx.c[i])), (far, base), MUTED, "واکنش/هدف بازگشت", True)]
                   + _mark_items(_curve(ctx, ctx.E(50), max(0, i - 25), i, BLUE, "EMA 50", True, keep=7))),
            rays=[
                _ray(ctx, p1, p2, "right", color, "امتداد خط روند"),
                _ray(ctx, (i, value), (far, _project(value, sl, i, far, use_log)), "right", MUTED,
                     "پیش‌بینی خط روند", True),
            ],
            levels=[_level(ctx, value, color, name),
                    _level(ctx, _project(value, sl, i, far, use_log), MUTED, "خط روند در ادامه"),
                    _level(ctx, base, MUTED, "کف/سقف واکنش"), *stops],
            zones=[_zone(ctx, value + a * 0.35, value - a * 0.35, max(0, p2[0]), far, color, "ناحیه واکنش")],
            points=_lpoints(ctx, [p1, p2], ["تکیه‌گاه ۱", "تکیه‌گاه ۲"]) + touch_pts,
            labels=labels,
            fib=[_fib(ctx, (i, base), (i, value), "عمق واکنش به خط روند",
                      [0.0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0])],
            marker=_marker(ctx, i, s.direction),
            indicators=_indicators("ema:20", "ema:50", "ema:200", "adx:14", *ind, *extra_ind),
        ), cap=36)))
        break
    return out


def _trendline_break(ctx: Context, s: Strategy, p: Dict[str, str], start: int, end: int) -> List[Detection]:
    direction = _s(p, "dir", "up")
    use_log = s.logic_key.startswith("log_")
    special = s.logic_key.startswith("special_") or int(_f(p, "volume", 0)) == 1
    which = "low" if direction == "down" else "high"
    slope = "up" if direction == "down" else "any"
    out: List[Detection] = []
    for i in range(max(start, 4), end + 1):
        fit = ctx.line_value(i, which, slope, use_log=use_log)
        if not fit:
            fit = ctx.line_value(i, which, "any", use_log=use_log)
        if not fit:
            continue
        value, p1, p2, sl = fit
        a = ctx.av(i)
        prev_fit = ctx.line_value(i - 1, which, slope, use_log=use_log) or fit
        prev_value = prev_fit[0]
        vol_ok = True
        if special:
            avg = ctx.vip_avg(20)[i]
            vol_ok = bool(np.isfinite(avg) and avg > 0 and ctx.v[i] >= avg * 1.2)
        if not vol_ok:
            continue
        if direction == "down":
            broke = ctx.c[i] < value - a * 0.05 and ctx.c[i - 1] >= prev_value - a * 0.05
        else:
            broke = ctx.c[i] > value + a * 0.05 and ctx.c[i - 1] <= prev_value + a * 0.05
        if not broke:
            continue
        color = RED if direction == "down" else GREEN
        base = float(np.min(ctx.l[max(0, i - 40):i + 1])) if direction == "up" else float(np.max(ctx.h[max(0, i - 40):i + 1]))
        anchor_end = float(ctx.c[i])
        far = _far_index(ctx, i, 20)
        stops, ind = _stop_levels(ctx, i)
        labels = [_label(ctx, i, anchor_end, "شکست خط روند", color),
                  _label(ctx, i, value, f"مقدار خط {value:.4g}", MUTED),
                  _label(ctx, i, base, f"قدرت روند ADX {ctx.strength(i):.0f}", PURPLE)]
        extra_ind: List[str] = []
        if special:
            avg = ctx.vip_avg(20)[i]
            if np.isfinite(avg) and avg > 0:
                labels.append(_label(ctx, i, float(ctx.l[i] if direction == "down" else ctx.h[i]),
                                     f"حجم {ctx.v[i] / float(avg):.1f}× متوسط", AMBER))
            extra_ind = _indicators("volume")
        out.append(Detection(s, i, float(ctx.c[i]), _overlay(
            lines=[_line(ctx, p1, p2, color, "خط روند"), _line(ctx, (p2[0], p2[1]), (i, value), color, "", True)],
            rays=[
                _ray(ctx, p1, p2, "both", MUTED, "امتداد خط شکسته‌شده", True),
                _ray(ctx, (i - 1, float(ctx.c[i - 1])), (i, anchor_end), "right", color, "مسیر شکست"),
            ],
            levels=[_level(ctx, value, color, "شکست خط روند"),
                    _level(ctx, _project(value, sl, i, far, use_log), MUTED, "مقدار خط در ادامه"),
                    *stops],
            zones=[_zone(ctx, value + a * 0.4, value - a * 0.4, max(0, p2[0]), far, color, "ناحیه تأیید/پولبک")],
            points=_lpoints(ctx, [p1, p2], ["تکیه‌گاه ۱", "تکیه‌گاه ۲"]),
            labels=labels,
            fib=[_fib(ctx, (i, base), (i, anchor_end), "هدف شکست (اندازه‌گیری حرکت)", [0.5, 1.0, 1.272, 1.618])],
            marker=_marker(ctx, i, s.direction),
            indicators=_indicators("ema:20", "ema:50", "ema:200", "adx:14", *ind, *extra_ind),
        )))
        break
    return out


def _trendline_pullback(ctx: Context, s: Strategy, p: Dict[str, str], start: int, end: int) -> List[Detection]:
    side = _s(p, "side", "support")
    use_log = s.logic_key.startswith("log_")
    out: List[Detection] = []
    for i in range(max(start, 4), end + 1):
        which = "low" if side == "support" else "high"
        slope = "up" if side == "support" else "down"
        fit = ctx.line_value(i, which, slope, use_log=use_log)
        if not fit:
            continue
        value, p1, p2, sl = fit
        a = ctx.av(i)
        near = ctx.l[i] <= value + a * 0.4 if side == "support" else ctx.h[i] >= value - a * 0.4
        bounce = ctx.c[i] > ctx.o[i] if side == "support" else ctx.c[i] < ctx.o[i]
        if near and bounce:
            color = GREEN if side == "support" else RED
            far = _far_index(ctx, i, 18)
            stops, ind = _stop_levels(ctx, i)
            base = float(np.min(ctx.l[max(0, i - 30):i + 1]))
            head = float(np.max(ctx.h[max(0, i - 30):i + 1]))
            out.append(Detection(s, i, float(ctx.c[i]), _overlay(
                lines=[_line(ctx, p1, p2, color, "خط روند"),
                       _line(ctx, (p2[0], p2[1]), (i, value), color, "پولبک", True)],
                rays=[_ray(ctx, p1, p2, "right", color, "امتداد خط روند"),
                      _ray(ctx, (i, value), (far, _project(value, sl, i, far, use_log)), "right", MUTED,
                           "مقدار خط در ادامه", True)],
                levels=[_level(ctx, value, color, "پولبک به خط روند"),
                        _level(ctx, _project(value, sl, i, far, use_log), MUTED, "خط روند (ادامه)"),
                        *stops],
                zones=[_zone(ctx, value + a * 0.4, value - a * 0.4, max(0, p2[0]), far, color, "ناحیه پولبک")],
                points=_lpoints(ctx, [p1, p2], ["تکیه‌گاه ۱", "تکیه‌گاه ۲"]),
                labels=[_label(ctx, i, float(ctx.c[i]), "پولبک موفق", color),
                        _label(ctx, i, base, f"ADX {ctx.strength(i):.0f}", PURPLE)],
                fib=[_fib(ctx, (max(0, i - 30), base), (i, head), "عمق پولبک روی موج",
                          [0.382, 0.5, 0.618, 0.786, 1.0])],
                marker=_marker(ctx, i, s.direction),
                indicators=_indicators("ema:20", "ema:50", "ema:200", "adx:14", *ind),
            )))
            break
    return out


def _sr_touch(ctx: Context, s: Strategy, p: Dict[str, str], start: int, end: int) -> List[Detection]:
    side = _s(p, "side", "support")
    want_touch = int(_f(p, "touch", 1)) == 1
    out: List[Detection] = []
    for i in range(start, end + 1):
        levels = ctx.sr_levels(i)
        a = ctx.av(i)
        for lv in levels:
            price = lv["price"]
            if side == "support":
                hit = ctx.l[i] <= price + a * 0.2 and ctx.c[i] >= price - a * 0.05
                # «تاچ» یعنی سایه سطح را زد و بسته بیرون از آن ماند (رفض واقعی)
                if want_touch:
                    hit = hit and ctx.l[i] <= price and ctx.c[i] >= price + a * 0.15
            else:
                hit = ctx.h[i] >= price - a * 0.2 and ctx.c[i] <= price + a * 0.05
                if want_touch:
                    hit = hit and ctx.h[i] >= price and ctx.c[i] <= price - a * 0.15
            if hit:
                color = GREEN if side == "support" else RED
                name = "حمایت" if side == "support" else "مقاومت"
                far = _far_index(ctx, i, 20)
                others = [x for x in levels if x is not lv]
                touch_idx = [int(x) for x in list(lv["idx"])[-4:]]
                pts = _lpoints(ctx, [(x, price) for x in touch_idx],
                               [f"برخورد {k + 1}" for k in range(len(touch_idx))])
                stops, ind = _stop_levels(ctx, i)
                extreme = float(ctx.h[i] if side == "support" else ctx.l[i])
                reject = float(ctx.c[i])
                reflect = 2.0 * extreme - price
                vol_panel = _indicator_panel(
                    ctx, i, "volume", "حجم هنگام واکنش به سطح",
                    [(ctx.v, "حجم", color, False)],
                    [(float(ctx.vip_avg(20)[i]) if np.isfinite(ctx.vip_avg(20)[i]) else float(np.mean(ctx.v)),
                      AMBER, "میانگین حجم ۲۰")],
                    lookback=25, height=70,
                )
                out.append(Detection(s, i, float(ctx.c[i]), _cap_overlay(_overlay(
                    lines=([_line(ctx, (max(0, min(lv["idx"])), price), (far, price), color,
                                    f"خط {name} با {lv['count']} برخورد"),
                            _line(ctx, (i, price), (far, reflect), color, "اندازه‌گیری بازتاب", True)]
                           + _mark_items(_curve(ctx, ctx.E(20), max(0, i - 20), i, BLUE, "EMA 20", True, keep=6))),
                    levels=[_level(ctx, price, color, f"{name} ({lv['count']} برخورد)"),
                            _level(ctx, price - a * 0.8 if side == "support" else price + a * 0.8,
                                   MUTED, "حد اعتبار سطح"),
                            _level(ctx, extreme, MUTED, "افق واکنش"),
                            _level(ctx, reflect, color, "هدف بازتاب"),
                            *[_level(ctx, x["price"], MUTED, "سطح مقابل") for x in others[:2]],
                            *stops],
                    zones=[_zone(ctx, price + a * 0.25, price - a * 0.25, max(0, min(lv["idx"])), far, color,
                                "ناحیه " + name)],
                    rays=[_ray(ctx, (max(0, min(lv["idx"])), price), (i, price), "right", color, f"تداوم {name}"),
                          _ray(ctx, (i, extreme), (far, reflect), "right", MUTED,
                               "پرتو ادامهٔ بازتاب", True)],
                    points=pts,
                    labels=[_label(ctx, i, extreme, f"واکنش به {name}", color),
                            _label(ctx, i, price, f"فاصله {abs(reject - price) / a:.2f} ATR", AMBER)],
                    fib=[_fib(ctx, (i, price), (i, extreme), "عمق واکنش به سطح",
                              [0.0, 0.236, 0.382, 0.5, 0.618, 1.0])],
                    panels=[x for x in [vol_panel] if x],
                    marker=_marker(ctx, i, s.direction),
                    indicators=_indicators("volume", "vwap", "ema:20", *ind),
                ), cap=36)))
                return out
    return out


def _sr_break(ctx: Context, s: Strategy, p: Dict[str, str], start: int, end: int) -> List[Detection]:
    direction = _s(p, "dir", "up")
    out: List[Detection] = []
    for i in range(max(start, 2), end + 1):
        levels = ctx.sr_levels(i)
        a = ctx.av(i)
        for lv in levels:
            price = lv["price"]
            if direction == "up":
                broke = ctx.c[i] > price + a * 0.05 and ctx.c[i - 1] <= price + a * 0.05
            else:
                broke = ctx.c[i] < price - a * 0.05 and ctx.c[i - 1] >= price - a * 0.05
            if broke:
                color = GREEN if direction == "up" else RED
                far = _far_index(ctx, i, 20)
                base = float(np.min(ctx.l[max(0, i - 40):i + 1])) if direction == "up" else float(np.max(ctx.h[max(0, i - 40):i + 1]))
                stops, ind = _stop_levels(ctx, i)
                out.append(Detection(s, i, float(ctx.c[i]), _overlay(
                    levels=[_level(ctx, price, color, "شکست سطح"),
                            _level(ctx, price + (a * 2.0 if direction == "up" else -a * 2.0), MUTED, "حد اعتبار شکست"),
                            *stops],
                    zones=[_zone(ctx, price + a * 0.3, price - a * 0.3, max(0, min(lv["idx"])), far, color, "ناحیه پولبک به سطح")],
                    rays=[_ray(ctx, (max(0, min(lv["idx"])), price), (i, price), "right", MUTED, "سطح شکسته‌شده", True),
                          _ray(ctx, (i - 1, float(ctx.c[i - 1])), (i, float(ctx.c[i])), "right", color, "مسیر شکست")],
                    points=_lpoints(ctx, [(int(x), price) for x in list(lv["idx"])[-3:]],
                                   [f"برخورد {k + 1}" for k in range(min(3, len(lv["idx"])))]),
                    labels=[_label(ctx, i, float(ctx.c[i]), f"شکست با {lv['count']} برخورد", color)],
                    fib=[_fib(ctx, (i, base), (i, float(ctx.c[i])), "هدف شکست سطح", [0.5, 1.0, 1.272, 1.618])],
                    marker=_marker(ctx, i, s.direction),
                    indicators=_indicators("volume", "vwap", "ema:20", *ind),
                )))
                return out
    return out


def _sr_range(ctx: Context, s: Strategy, p: Dict[str, str], start: int, end: int) -> List[Detection]:
    side = _s(p, "side", "above")
    out: List[Detection] = []
    for i in range(max(start, 30), end + 1):
        levels = ctx.sr_levels(i)
        if len(levels) < 2:
            continue
        supports = [x for x in levels if x["price"] <= ctx.c[i]]
        resists = [x for x in levels if x["price"] >= ctx.c[i]]
        a = ctx.av(i)
        picked = None
        if side == "above" and supports:
            picked = max(supports, key=lambda x: x["price"])
            if not (0 < ctx.c[i] - picked["price"] <= a * 2.5):
                continue
        if side == "below" and resists:
            picked = min(resists, key=lambda x: x["price"])
            if not (0 < picked["price"] - ctx.c[i] <= a * 2.5):
                continue
        if picked is None:
            continue
        color = GREEN if side == "above" else RED
        name = "حمایت" if side == "above" else "مقاومت"
        pool = resists if side == "above" else supports
        if pool:
            other_price = min(x["price"] for x in pool) if side == "above" else max(x["price"] for x in pool)
        else:
            other_price = picked["price"] + (a * 2.0 if side == "above" else -a * 2.0)
        top = max(picked["price"], other_price)
        bottom = min(picked["price"], other_price)
        far = _far_index(ctx, i, 20)
        stops, ind = _stop_levels(ctx, i)
        mid_r = (top + bottom) / 2.0
        out.append(Detection(s, i, float(ctx.c[i]), _cap_overlay(_overlay(
            lines=[_line(ctx, (max(0, min(picked["idx"])), picked["price"]), (far, picked["price"]), color,
                         f"مرز {name} رنج"),
                   _line(ctx, (max(0, min(picked["idx"])), other_price), (far, other_price), MUTED,
                         "مرز مقابل رنج", True),
                   _line(ctx, (max(0, min(picked["idx"])), mid_r), (far, mid_r), BLUE, "mid رنج", True)]
                  + _mark_items(_curve(ctx, ctx.E(20), max(0, i - 20), i, CYAN, "EMA 20", True, keep=6)),
            levels=[_level(ctx, picked["price"], color, f"{name} کف/سقف رنج"),
                    _level(ctx, other_price, MUTED, "سطح مقابل رنج"),
                    _level(ctx, mid_r, BLUE, "میانه رنج"),
                    _level(ctx, float(ctx.c[i]), BLUE, "قیمت داخل رنج"),
                    _level(ctx, bottom - a * 0.6 if side == "above" else top + a * 0.6, RED,
                           "حد اعتبار رنج"),
                    *stops],
            zones=[_zone(ctx, top, bottom, max(0, min(picked["idx"])), far, MUTED, "ناحیه رنج"),
                   _zone(ctx, picked["price"] + a * 0.3, picked["price"] - a * 0.3,
                         max(0, min(picked["idx"])), far, color, "ناحیه مرزی رنج")],
            rays=[_ray(ctx, (max(0, min(picked["idx"])), picked["price"]), (i, picked["price"]), "right", color,
                     f"مرز {name}"),
                  _ray(ctx, (i, float(ctx.c[i])), (far, other_price), "right", color,
                       "هدف حرکت داخل رنج", True)],
            points=_lpoints(ctx, [(int(x), picked["price"]) for x in list(picked["idx"])[-3:]]
                            + [(int(x), other_price) for x in list(picked["idx"])[-2:]],
                            [f"برخورد {k + 1}" for k in range(min(3, len(picked["idx"])))]),
            labels=[_label(ctx, i, float(ctx.c[i]), f"عرض رنج {(top - bottom) / a:.1f} ATR", AMBER),
                    _label(ctx, i, mid_r, f"فاصله از میانه {(float(ctx.c[i]) - mid_r) / a:+.2f} ATR", BLUE)],
            fib=[_fib(ctx, (i, bottom), (i, top), "تقسیم رنج (محل قیمت)",
                      [0.0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0])],
            marker=_marker(ctx, i, s.direction),
            indicators=_indicators("volume", "vwap", "ema:20", "boll:20:2", *ind),
        ), cap=36)))
        return out
    return out


def _channel_touch(ctx: Context, s: Strategy, p: Dict[str, str], start: int, end: int) -> List[Detection]:
    side = _s(p, "side", "upper")
    direction = _s(p, "dir", "up")
    out: List[Detection] = []
    for i in range(max(start, 5), end + 1):
        fit_low = ctx.line_value(i, "low", direction)
        fit_high = ctx.line_value(i, "high", direction)
        if not fit_low or not fit_high:
            continue
        low_v, lp1, lp2, low_sl = fit_low
        high_v, hp1, hp2, high_sl = fit_high
        if high_v <= low_v:
            continue
        a = ctx.av(i)
        hit = ctx.h[i] >= high_v - a * 0.4 if side == "upper" else ctx.l[i] <= low_v + a * 0.4
        if not hit:
            continue
        color = RED if side == "upper" else GREEN
        far = _far_index(ctx, i, 20)
        hi_far = _project(high_v, high_sl, i, far, False)
        lo_far = _project(low_v, low_sl, i, far, False)
        mid_i = (high_v + low_v) / 2.0
        mid_far = (hi_far + lo_far) / 2.0
        stops, ind = _stop_levels(ctx, i)
        out.append(Detection(s, i, float(ctx.c[i]), _overlay(
            lines=[
                _line(ctx, hp1, hp2, RED, "سقف کانال"),
                _line(ctx, lp1, lp2, GREEN, "کف کانال"),
                _line(ctx, (hp2[0], high_v), (i, high_v), RED, "", True),
                _line(ctx, (lp2[0], low_v), (i, low_v), GREEN, "", True),
                _line(ctx, (i, mid_i), (far, mid_far), BLUE, "میدلاین کانال", True),
            ],
            rays=[
                _ray(ctx, hp1, hp2, "right", RED, "امتداد سقف کانال"),
                _ray(ctx, lp1, lp2, "right", GREEN, "امتداد کف کانال"),
                _ray(ctx, (i, mid_i), (far, mid_far), "right", BLUE, "هدف میدلاین", True),
            ],
            levels=[_level(ctx, high_v if side == "upper" else low_v, color, "سقف/کف کانال"),
                    _level(ctx, low_v if side == "upper" else high_v, MUTED, "مرز مقابل کانال"),
                    _level(ctx, mid_i, BLUE, "میدلاین کانال"),
                    *stops],
            zones=[_zone(ctx, high_v, low_v, max(0, min(lp2[0], hp2[0])), far, MUTED, "بدنه کانال")],
            points=_lpoints(ctx, [hp1, hp2, lp1, lp2], ["قله ۱", "قله ۲", "کف ۱", "کف ۲"]),
            labels=[_label(ctx, i, float(ctx.c[i]), f"عرض کانال {(high_v - low_v) / a:.1f} ATR", AMBER)],
            fib=[_fib(ctx, (i, low_v), (i, high_v), "تقسیم‌بندی داخل کانال", [0.382, 0.5, 0.618, 1.0])],
            marker=_marker(ctx, i, s.direction),
            indicators=_indicators("ema:50", "ema:200", "adx:14", *ind),
        )))
        return out
    return out


def _order_block(ctx: Context, s: Strategy, p: Dict[str, str], start: int, end: int) -> List[Detection]:
    side = _s(p, "side", "demand")
    out: List[Detection] = []
    for i in range(max(start, 40), end + 1):
        a = ctx.av(i)
        best = None
        for j in range(i - 40, max(0, i - 3)):
            body = abs(ctx.c[j] - ctx.o[j])
            if body < a * 0.5:
                continue
            if side == "demand" and ctx.c[j] < ctx.o[j]:
                move = float(np.max(ctx.h[j + 1:min(j + 4, i + 1)]) - ctx.c[j]) if j + 1 < i + 1 else 0
                if move >= a * 0.9:
                    best = (j, float(ctx.l[j]), float(ctx.h[j]))
                    break
            if side == "supply" and ctx.c[j] > ctx.o[j]:
                move = float(ctx.c[j] - np.min(ctx.l[j + 1:min(j + 4, i + 1)])) if j + 1 < i + 1 else 0
                if move >= a * 0.9:
                    best = (j, float(ctx.l[j]), float(ctx.h[j]))
                    break
        if not best:
            continue
        j, bottom, top = best
        color = GREEN if side == "demand" else RED
        name = "ناحیه تقاضا" if side == "demand" else "ناحیه عرضه"
        touched = (side == "demand" and ctx.l[i] <= top and ctx.c[i] >= bottom) or \
                  (side == "supply" and ctx.h[i] >= bottom and ctx.c[i] <= top)
        if not touched:
            continue
        far = _far_index(ctx, i, 25)
        stops, ind = _stop_levels(ctx, i)
        height = max(top - bottom, a * 0.05)
        target = top + height * 1.272 if side == "demand" else bottom - height * 1.272
        anchor = float(ctx.l[i]) if side == "demand" else float(ctx.h[i])
        out.append(Detection(s, i, float(ctx.c[i]), _overlay(
            lines=[_line(ctx, (j, top), (i, top), color, "سقف اردر بلاک", True),
                   _line(ctx, (j, bottom), (i, bottom), color, "کف اردر بلاک", True)],
            rays=[_ray(ctx, (j, anchor), (i, float(ctx.c[i])), "right", color, "مسیر واکنش از OB"),
                  _ray(ctx, (i, target), (far, target), "right", MUTED, "حد هدف", True)],
            levels=[_level(ctx, top, color, "سقف OB"), _level(ctx, bottom, color, "کف OB"),
                    _level(ctx, target, MUTED, "هدف حرکتی OB"), *stops],
            zones=[_zone(ctx, top, bottom, j, far, color, name)],
            points=_lpoints(ctx, [(j, float(ctx.h[j])), (j, float(ctx.l[j]))], ["کندل مرجوعی", "کندل مرجوعی"]),
            labels=[_label(ctx, i, anchor, name, color),
                    _label(ctx, j, float(ctx.c[j]), f"عرض {height / a:.1f} ATR", AMBER)],
            fib=[_fib(ctx, (i, bottom), (i, top), "اندازه‌گیری حرکت از OB", [1.0, 1.272, 1.618, 2.0])],
            marker=_marker(ctx, i, s.direction, name),
            indicators=_indicators("volume", "vwap", *ind),
        )))
        return out
    return out


def _stop_hunt(ctx: Context, s: Strategy, p: Dict[str, str], start: int, end: int) -> List[Detection]:
    direction = _s(p, "dir", "buy")
    out: List[Detection] = []
    for i in range(max(start, 10), end + 1):
        highs, lows = ctx.pivots(3)
        a = ctx.av(i)
        if direction == "buy":
            piv = [x for x in lows if x[0] <= i - 3][-1:]
            if not piv:
                continue
            idx, price = piv[0]
            wick = min(ctx.o[i], ctx.c[i]) - ctx.l[i]
            body = max(abs(ctx.c[i] - ctx.o[i]), a * 0.05)
            if ctx.l[i] < price and ctx.c[i] > price and wick >= body * 1.5:
                far = _far_index(ctx, i, 25)
                stops, ind = _stop_levels(ctx, i)
                target = price + (ctx.c[i] - ctx.l[i]) * 2.0
                out.append(Detection(s, i, float(ctx.c[i]), _overlay(
                    levels=[_level(ctx, price, GREEN, "استاپ هانت"),
                            _level(ctx, float(ctx.l[i]), RED, "کف شدو (نقطه خروج خریداران)"),
                            _level(ctx, target, MUTED, "هدف بازگشت"), *stops],
                    lines=[_line(ctx, (idx, price), (i, price), GREEN, "", True),
                           _line(ctx, (idx, float(ctx.l[idx])), (i, float(ctx.l[i])), RED, "خط اتصال کف‌ها", True)],
                    rays=[_ray(ctx, (idx, price), (i, price), "right", GREEN, "تداوم سطح شکارشده", True),
                          _ray(ctx, (i, float(ctx.l[i])), (far, float(ctx.l[i])), "right", RED, "حد اعتبار", True),
                          _ray(ctx, (i, float(ctx.c[i])), (far, target), "right", GREEN, "مسیر بازگشت")],
                    zones=[_zone(ctx, price, float(ctx.l[i]), idx, i, GREEN, "ناحیه شکار استاپ")],
                    points=_lpoints(ctx, [(idx, price), (i, float(ctx.l[i]))], ["کف پیشین", "شدو شکست"]),
                    labels=[_label(ctx, i, float(ctx.l[i]), f"شدو {wick / a:.1f} ATR", RED)],
                    marker=_marker(ctx, i, s.direction),
                    indicators=_indicators("volume", "vwap", *ind),
                )))
                return out
        else:
            piv = [x for x in highs if x[0] <= i - 3][-1:]
            if not piv:
                continue
            idx, price = piv[0]
            wick = ctx.h[i] - max(ctx.o[i], ctx.c[i])
            body = max(abs(ctx.c[i] - ctx.o[i]), a * 0.05)
            if ctx.h[i] > price and ctx.c[i] < price and wick >= body * 1.5:
                far = _far_index(ctx, i, 25)
                stops, ind = _stop_levels(ctx, i)
                target = price - (ctx.h[i] - ctx.c[i]) * 2.0
                out.append(Detection(s, i, float(ctx.c[i]), _overlay(
                    levels=[_level(ctx, price, RED, "استاپ هانت"),
                            _level(ctx, float(ctx.h[i]), GREEN, "قله شدو (نقطه خروج فروشندگان)"),
                            _level(ctx, target, MUTED, "هدف بازگشت"), *stops],
                    lines=[_line(ctx, (idx, price), (i, price), RED, "", True),
                           _line(ctx, (idx, float(ctx.h[idx])), (i, float(ctx.h[i])), GREEN, "خط اتصال قله‌ها", True)],
                    rays=[_ray(ctx, (idx, price), (i, price), "right", RED, "تداوم سطح شکارشده", True),
                          _ray(ctx, (i, float(ctx.h[i])), (far, float(ctx.h[i])), "right", GREEN, "حد اعتبار", True),
                          _ray(ctx, (i, float(ctx.c[i])), (far, target), "right", RED, "مسیر بازگشت")],
                    zones=[_zone(ctx, float(ctx.h[i]), price, idx, i, RED, "ناحیه شکار استاپ")],
                    points=_lpoints(ctx, [(idx, price), (i, float(ctx.h[i]))], ["قله پیشین", "شدو شکست"]),
                    labels=[_label(ctx, i, float(ctx.h[i]), f"شدو {wick / a:.1f} ATR", GREEN)],
                    marker=_marker(ctx, i, s.direction),
                    indicators=_indicators("volume", "vwap", *ind),
                )))
                return out
    return out


# ---- هارمونیک ----
_HARMONIC_RANGES = {
    "gartley": {"ab": (0.55, 0.68), "bc": (0.35, 0.90), "cd": (1.20, 1.75), "ad": (0.70, 0.86)},
    "bat": {"ab": (0.33, 0.55), "bc": (0.35, 0.90), "cd": (1.55, 2.70), "ad": (0.82, 0.94)},
    "butterfly": {"ab": (0.70, 0.85), "bc": (0.35, 0.90), "cd": (1.50, 2.35), "ad": (1.20, 1.70)},
    "crab": {"ab": (0.33, 0.68), "bc": (0.35, 0.90), "cd": (2.10, 3.80), "ad": (1.50, 1.72)},
    "cypher": {"ab": (0.33, 0.68), "bc": (1.05, 1.50), "cd": (0.70, 0.90), "ad": (0.25, 0.60)},
    "shark": {"ab": (0.30, 0.68), "bc": (1.05, 1.70), "cd": (1.20, 2.10), "ad": (0.80, 1.10)},
    "white_swan": {"ab": (0.33, 0.68), "bc": (0.35, 0.90), "cd": (1.50, 2.60), "ad": (1.10, 1.70)},
}


def _in_range(v: float, lo: float, hi: float, tol: float = 0.15) -> bool:
    if not np.isfinite(v):
        return False
    span = max(abs(hi - lo), 1e-9)
    return (lo - tol * span) <= v <= (hi + tol * span)


def _harmonic(ctx: Context, s: Strategy, p: Dict[str, str], start: int, end: int) -> List[Detection]:
    pattern = _s(p, "pattern", "gartley")
    direction = _s(p, "dir", "bull")
    ranges = _HARMONIC_RANGES.get(pattern, _HARMONIC_RANGES["gartley"])
    highs, lows = ctx.pivots(3)
    swings = sorted([(int(i), float(v), True) for i, v in highs] + [(int(i), float(v), False) for i, v in lows],
                    key=lambda z: (z[0], not z[2]))
    if len(swings) < 5:
        return []
    out: List[Detection] = []
    for i in range(start, end + 1):
        conf = [x for x in swings if x[0] <= i - 3]
        if len(conf) < 5:
            continue
        seq5 = conf[-5:]
        # پنج نقطه باید دقیقاً به‌ترتیب بالا/پایین متناوب باشند و هر پا روی کندل جدا بنشیند
        types = [x[2] for x in seq5]
        if not (types[0] == types[2] == types[4] and types[1] == types[3] and types[0] != types[1]):
            continue
        if any(seq5[k][0] == seq5[k - 1][0] for k in range(1, 5)):
            continue
        bull_d_low = not types[4]
        if direction == "bull" and not bull_d_low:
            continue
        if direction == "bear" and bull_d_low:
            continue
        seq = [(x[0], x[1]) for x in seq5]
        X, A, B, C, D = [x[1] for x in seq]
        xa = abs(A - X)
        ab = abs(B - A)
        bc = abs(C - B)
        cd = abs(D - C)
        ad = abs(D - A)
        if min(xa, ab, bc, cd, ad) <= 0:
            continue
        r_ab, r_bc, r_cd, r_ad = ab / xa, bc / ab, cd / bc, ad / xa
        ok = (
            _in_range(r_ab, *ranges["ab"]) and _in_range(r_bc, *ranges["bc"]) and
            _in_range(r_cd, *ranges["cd"]) and _in_range(r_ad, *ranges["ad"])
        )
        if not ok:
            continue
        if not (seq[3][0] < i and i - seq[4][0] <= 8):
            continue
        color = GREEN if direction == "bull" else RED
        pts = _lpoints(ctx, seq, ["X", "A", "B", "C", "D"])
        far = _far_index(ctx, i, 22)
        prd_lo = A + (X - A) * ranges["ad"][0]
        prd_hi = A + (X - A) * ranges["ad"][1]
        zone_top, zone_bottom = max(prd_lo, prd_hi), min(prd_lo, prd_hi)
        stops, sind = _stop_levels(ctx, i)
        # مسیر X→D روی چارت + منحنی روندی که الگو در دل آن شکل گرفته
        lines = [
            _line(ctx, seq[0], seq[1], color, "X-A"),
            _line(ctx, seq[1], seq[2], color, "A-B"),
            _line(ctx, seq[2], seq[3], color, "B-C"),
            _line(ctx, seq[3], seq[4], color, "C-D"),
            _line(ctx, seq[0], seq[4], MUTED, "X-D", True),
            _line(ctx, seq[0], seq[2], PURPLE, "X-B (مجموع موج اول)", True),
            _line(ctx, seq[1], seq[3], AMBER, "A-C (دامنه اصلاح)", True),
            _line(ctx, seq[2], seq[4], BLUE, "B-D (حرکت traded)", True),
        ]
        lines += _mark_items(_curve(ctx, ctx.E(50), seq[0][0], i, CYAN, "EMA 50", True, keep=8))
        out.append(Detection(s, i, float(ctx.c[i]), _overlay(
            lines=lines,
            rays=[
                _ray(ctx, seq[3], seq[4], "right", color, "ادامه موج D"),
                _ray(ctx, seq[0], seq[1], "right", MUTED, "مبنا X-A", True),
                _ray(ctx, seq[2], seq[3], "right", PURPLE, "امتداد BC (حد شکست الگو)", True),
            ],
            levels=[_level(ctx, float(ctx.c[i]), color, f"PRD {pattern.upper()}"),
                    _level(ctx, X, MUTED, "انتظار X (حد شکست)"),
                    _level(ctx, C, AMBER, "قله/کف C"),
                    _level(ctx, prd_lo, color, "کف ناحیه PRD"),
                    _level(ctx, prd_hi, color, "سقف ناحیه PRD"),
                    *stops],
            zones=[_zone(ctx, zone_top, zone_bottom, seq[4][0], far, color, "ناحیه ورود (PRD)"),
                   _zone(ctx, max(A, X), min(A, X), seq[0][0], seq[1][0], MUTED, "موج مبنا XA")],
            points=pts,
            labels=[
                _label(ctx, seq[1][0], A, "A", color),
                _label(ctx, seq[2][0], B, f"AB/XA {r_ab:.2f}", BLUE),
                _label(ctx, seq[3][0], C, f"BC/AB {r_bc:.2f}", PURPLE),
                _label(ctx, seq[4][0], D, f"CD/AB {r_cd:.2f} · AD/XA {r_ad:.2f}", AMBER),
                _label(ctx, i, D, pattern.upper(), color),
            ],
            fib=[
                _fib(ctx, seq[0], seq[1], f"XA → PRD {pattern.upper()}",
                     [0.618, 0.786, ranges["ad"][0], ranges["ad"][1], 1.0]),
                _fib(ctx, seq[1], seq[2], "AB → بازگشت BC", [0.382, 0.5, 0.618, 0.786, 0.886]),
                _fib(ctx, seq[2], seq[3], "BC → امتداد CD",
                     [ranges["cd"][0], 1.0, ranges["cd"][1], 1.618, 2.618]),
                _fib(ctx, seq[1], seq[4], "AB → اهداف CD", [1.272, 1.618, 2.0, 2.618]),
            ],
            marker=_marker(ctx, i, s.direction, pattern.upper()),
            indicators=_indicators("ema:50", "sar:0.02:0.2", "vwap", *sind),
        )))
        return out
    return out


# ---- الگوهای کندلی ----
def _candle_parts(ctx: Context, i: int):
    body = abs(ctx.c[i] - ctx.o[i])
    rng = max(ctx.h[i] - ctx.l[i], 1e-12)
    upper = ctx.h[i] - max(ctx.c[i], ctx.o[i])
    lower = min(ctx.c[i], ctx.o[i]) - ctx.l[i]
    return body, rng, upper, lower


def _downtrend(ctx: Context, i: int) -> bool:
    return i >= 8 and ctx.c[i - 1] < ctx.c[i - 5] and ctx.E(20)[i - 1] <= ctx.E(50)[i - 1] * 1.01


def _uptrend(ctx: Context, i: int) -> bool:
    return i >= 8 and ctx.c[i - 1] > ctx.c[i - 5] and ctx.E(20)[i - 1] >= ctx.E(50)[i - 1] * 0.99


def _candlestick(ctx: Context, s: Strategy, p: Dict[str, str], start: int, end: int) -> List[Detection]:
    name = _s(p, "pattern", "hammer")
    out: List[Detection] = []
    for i in range(max(start, 3), end + 1):
        body, rng, upper, lower = _candle_parts(ctx, i)
        green = ctx.c[i] >= ctx.o[i]
        red = not green
        hit = False
        if name == "hammer":
            hit = body <= rng * 0.34 and lower >= body * 1.8 and upper <= rng * 0.12 and _downtrend(ctx, i)
        elif name == "hanging_man":
            hit = body <= rng * 0.34 and lower >= body * 1.8 and upper <= rng * 0.12 and _uptrend(ctx, i)
        elif name == "inverted_hammer":
            hit = body <= rng * 0.34 and upper >= body * 1.8 and lower <= rng * 0.12 and _downtrend(ctx, i)
        elif name == "shooting_star":
            hit = body <= rng * 0.34 and upper >= body * 1.8 and lower <= rng * 0.12 and _uptrend(ctx, i)
        elif name == "bullish_engulfing":
            prev_body = abs(ctx.c[i - 1] - ctx.o[i - 1])
            hit = (green and ctx.c[i - 1] < ctx.o[i - 1]
                   and ctx.o[i] <= ctx.c[i - 1] and ctx.c[i] >= ctx.o[i - 1] and body > prev_body)
        elif name == "bearish_engulfing":
            prev_body = abs(ctx.c[i - 1] - ctx.o[i - 1])
            hit = (red and ctx.c[i - 1] > ctx.o[i - 1]
                   and ctx.o[i] >= ctx.c[i - 1] and ctx.c[i] <= ctx.o[i - 1] and body > prev_body)
        elif name == "piercing":
            mid = (ctx.o[i - 1] + ctx.c[i - 1]) / 2.0
            hit = ctx.c[i - 1] < ctx.o[i - 1] and green and ctx.o[i] <= ctx.c[i - 1] and ctx.c[i] >= mid and ctx.c[i] < ctx.o[i - 1]
        elif name == "dark_cloud":
            mid = (ctx.o[i - 1] + ctx.c[i - 1]) / 2.0
            hit = ctx.c[i - 1] > ctx.o[i - 1] and red and ctx.o[i] >= ctx.c[i - 1] and ctx.c[i] <= mid and ctx.c[i] > ctx.o[i - 1]
        elif name == "harami":
            prev = abs(ctx.c[i - 1] - ctx.o[i - 1])
            hit = prev > rng * 0.5 and body < prev * 0.8 and max(ctx.o[i], ctx.c[i]) <= max(ctx.o[i - 1], ctx.c[i - 1]) and min(ctx.o[i], ctx.c[i]) >= min(ctx.o[i - 1], ctx.c[i - 1])
        elif name == "morning_star":
            hit = i >= 2 and ctx.c[i - 2] < ctx.o[i - 2] and abs(ctx.c[i - 1] - ctx.o[i - 1]) < abs(ctx.c[i - 2] - ctx.o[i - 2]) * 0.5 and green and ctx.c[i] > (ctx.o[i - 2] + ctx.c[i - 2]) / 2.0
        elif name == "evening_star":
            hit = i >= 2 and ctx.c[i - 2] > ctx.o[i - 2] and abs(ctx.c[i - 1] - ctx.o[i - 1]) < abs(ctx.c[i - 2] - ctx.o[i - 2]) * 0.5 and red and ctx.c[i] < (ctx.o[i - 2] + ctx.c[i - 2]) / 2.0
        elif name == "gap_up":
            hit = ctx.o[i] > ctx.h[i - 1] and green
        elif name == "gap_down":
            hit = ctx.o[i] < ctx.l[i - 1] and red
        if not hit:
            continue
        color = GREEN if green else RED
        first = i - 2 if name in ("morning_star", "evening_star") else \
            i - 1 if name in ("bullish_engulfing", "bearish_engulfing", "piercing", "dark_cloud",
                              "harami", "gap_up", "gap_down") else i
        pat_lo = float(np.min(ctx.l[first:i + 1]))
        pat_hi = float(np.max(ctx.h[first:i + 1]))
        far = _far_index(ctx, i, 15)
        neck = float((ctx.o[i - 1] + ctx.c[i - 1]) / 2.0) if name in ("piercing", "dark_cloud", "harami") else \
            float(ctx.h[i - 1] if name == "gap_up" else ctx.l[i - 1] if name == "gap_down" else pat_hi if green else pat_lo)
        roles = ["کندل اول", "کندل دوم", "تأیید"][:i - first + 1]
        stops, ind = _stop_levels(ctx, i)
        gap_zone = _zone(ctx, float(ctx.o[i]), float(ctx.h[i - 1] if name == "gap_up" else ctx.l[i - 1]),
                        i - 1, i, AMBER, "گپ (Gap)") if name in ("gap_up", "gap_down") else None
        zones = [_zone(ctx, pat_hi, pat_lo, first, far, color, "جعبه الگوی کندلی")]
        if gap_zone:
            zones.append(gap_zone)
        # روندی که الگو در بطن آن معنا دارد؛ ابزار عمومیِ اطراف الگو است، نه هندسهٔ خودش،
        # پس برچسب ctx می‌گیرد و در `_trim_overlay` از لایهٔ الگوی کندلی بیرون می‌ماند.
        trend = ctx.E(20) if green else ctx.E(50)
        tstart = max(0, i - 26)
        lines = [_line(ctx, (first, pat_hi), (i, pat_hi), color, "سقف الگو", True),
                 _line(ctx, (first, pat_lo), (i, pat_lo), color, "کف الگو", True)]
        lines += _mark_items(_curve(ctx, trend, tstart, i, BLUE if green else RED,
                                    "EMA 20 (روند صعودی)" if green else "EMA 50 (روند نزولی)", True, keep=8))
        range_h = max(pat_hi - pat_lo, 1e-9)
        out.append(Detection(s, i, float(ctx.c[i]), _overlay(
            lines=lines,
            rays=[_ray(ctx, (i, float(ctx.c[i])), (far, float(ctx.c[i])), "right", color, "جهت پیش‌روی الگو"),
                  _ray(ctx, (i, pat_lo if green else pat_hi), (far, pat_lo if green else pat_hi), "right",
                       MUTED, "حد اعتبار (باخت)", True),
                  _ray(ctx, (i, pat_hi if green else pat_lo),
                       (far, pat_hi + range_h if green else pat_lo - range_h), "right",
                       CYAN, "پرتو اندازه‌حرکت (قد الگو)", True)],
            levels=[_level(ctx, pat_hi, color, "سقف الگو"), _level(ctx, pat_lo, color, "کف الگو"),
                    _level(ctx, neck, BLUE, "خط گردن/میانه کندل قبل"),
                    _level(ctx, pat_hi + range_h if green else pat_lo - range_h, CYAN, "هدف اندازه‌حرکت"),
                    *stops],
            zones=zones,
            points=_lpoints(ctx, [(k, float(ctx.c[k])) for k in range(first, i + 1)], roles),
            labels=[_label(ctx, i, float(ctx.h[i] if green else ctx.l[i]), name.replace("_", " "), color),
                    _label(ctx, i, float(ctx.l[i] if green else ctx.h[i]),
                           f"شدو/بدنه {min(99.0, max(upper, lower) / max(body, 1e-9)):.1f}", AMBER),
                    _label(ctx, i, pat_hi + range_h if green else pat_lo - range_h,
                           f"هدف {range_h / max(ctx.av(i), 1e-9):.1f} ATR", CYAN)],
            fib=[_fib(ctx, (first, pat_lo), (i, pat_hi), "پروجکشن الگوی کندلی",
                       [0.0, 0.5, 1.0, 1.272, 1.618])],
            marker=_marker(ctx, i, s.direction, name),
            indicators=_indicators("volume", "ema:20", "ema:50", *ind),
        )))
        return out
    return out


# ---- ایچیموکو ----
def _ichimoku(ctx: Context, s: Strategy, p: Dict[str, str], start: int, end: int) -> List[Detection]:
    tenkan, kijun, span_a, span_b, chikou = ctx._ich
    key = s.logic_key
    out: List[Detection] = []
    for i in range(max(start, 60), end + 1):
        if not all(np.isfinite(x[i]) for x in (tenkan, kijun, span_a, span_b)):
            continue
        cloud_top = max(span_a[i], span_b[i])
        cloud_bottom = min(span_a[i], span_b[i])
        hit = False
        details: Dict[str, Any] = {}
        far = _far_index(ctx, i, 26)
        cloud_zone = _zone(ctx, cloud_top, cloud_bottom, max(0, i - 26), i, PURPLE, "ابر کومو")
        if key == "ichimoku_cross":
            direction = _s(p, "dir", "up")
            cloud = _s(p, "cloud", "above")
            cross = (tenkan[i - 1] <= kijun[i - 1] and tenkan[i] > kijun[i]) if direction == "up" else (tenkan[i - 1] >= kijun[i - 1] and tenkan[i] < kijun[i])
            cond = ctx.c[i] > cloud_top if cloud == "above" else ctx.c[i] < cloud_bottom
            hit = cross and cond
            details = {
                "rays": [_ray(ctx, (i - 9, tenkan[i - 9]), (i, tenkan[i]), "right", BLUE, "ادامه تنکنسن"),
                         _ray(ctx, (i - 26, kijun[i - 26]), (i, kijun[i]), "right", AMBER, "ادامه کیجونسن")],
                "zones": [cloud_zone, _zone(ctx, cloud_top, cloud_bottom, i, far, PURPLE, "ابر آینده"),
                          _zone(ctx, float(cloud_top), float(cloud_bottom), max(0, i - 60), max(0, i - 26),
                                MUTED, "ابر ۲۶ کندل پیش")],
                "points": _lpoints(ctx, [(i, float(tenkan[i])), (i, float(kijun[i])),
                                         (i - 1, float(tenkan[i - 1])), (i - 1, float(kijun[i - 1]))],
                                  ["تقاطع TK", "تقاطع KK", "TK پیش", "KK پیش"]),
                "levels": [_level(ctx, float(cloud_top if cloud == "above" else cloud_bottom), PURPLE,
                                   "لبه ابرِ ملاک")],
                "labels": [_label(ctx, i, float(tenkan[i]), "تقاطع تنکنسن/کیجونسن", BLUE),
                           _label(ctx, i, cloud_top if cloud == "above" else cloud_bottom,
                                  "بالای ابر" if cloud == "above" else "زیر ابر", PURPLE)],
            }
        elif key == "ichimoku_kijun_touch":
            cloud = _s(p, "cloud", "above")
            cond = ctx.c[i] < cloud_bottom if cloud == "below" else ctx.c[i] > cloud_top
            hit = (ctx.l[i] <= kijun[i] + ctx.av(i) * 0.2 and ctx.h[i] >= kijun[i] - ctx.av(i) * 0.2) and cond
            details = {
                "levels": [_level(ctx, kijun[i], AMBER, "کیجونسن"), _level(ctx, cloud_top, PURPLE, "سقف ابر"),
                           _level(ctx, cloud_bottom, PURPLE, "کف ابر"),
                           _level(ctx, float(ctx.h[i]), RED, "سقف کندل واکنش"),
                           _level(ctx, float(ctx.l[i]), GREEN, "کف کندل واکنش")],
                "rays": [_ray(ctx, (i - 26, kijun[i - 26]), (i, kijun[i]), "right", AMBER, "ادامه کیجونسن")],
                "zones": [cloud_zone, _zone(ctx, float(kijun[i]) + ctx.av(i) * 0.2,
                                            float(kijun[i]) - ctx.av(i) * 0.2, i - 26, far, AMBER, "ناحیه کیجونسن")],
                "points": _lpoints(ctx, [(i, float(ctx.l[i] if s.direction == "LONG" else ctx.h[i]))], ["واکنش"]),
                "labels": [_label(ctx, i, float(kijun[i]), "پولبک به کیجونسن", AMBER)],
                "fib": [_fib(ctx, (i, float(cloud_bottom)), (i, float(cloud_top)),
                             "موضع قیمت داخل ابر", [0.0, 0.382, 0.5, 0.618, 1.0])],
            }
        elif key == "ichimoku_cloud_break":
            side = _s(p, "side", "up")
            want_green = _s(p, "color", "") == "green"
            cloud_is_green = span_a[i] > span_b[i]
            if side == "up":
                hit = ctx.c[i - 1] <= cloud_top and ctx.c[i] > cloud_top
            else:
                hit = ctx.c[i - 1] >= cloud_bottom and ctx.c[i] < cloud_bottom
            # رنگ ابر بخشی از خودِ استراتژی است («شکست ابر قرمز به بالا» ≠ شکست ابر سبز)
            if _s(p, "color", "") and want_green != cloud_is_green:
                hit = False
            ccol = GREEN if cloud_is_green else RED
            details = {
                "zones": [cloud_zone, _zone(ctx, cloud_top, cloud_bottom, i, far, ccol, "ابر آینده")],
                "lines": [_line(ctx, (max(0, i - 26), float(cloud_top)), (i, float(cloud_top)), PURPLE,
                                 "لبه بالایی ابر"),
                          _line(ctx, (max(0, i - 26), float(cloud_bottom)), (i, float(cloud_bottom)), PURPLE,
                                 "لبه پایینی ابر")],
                "levels": [_level(ctx, cloud_top if side == "up" else cloud_bottom, ccol,
                                   "لبه ابر شکسته‌شده"),
                           _level(ctx, float(cloud_top), PURPLE, "سقف ابر"),
                           _level(ctx, float(cloud_bottom), PURPLE, "کف ابر")],
                "rays": [_ray(ctx, (i - 1, float(ctx.c[i - 1])), (i, float(ctx.c[i])), "right",
                             GREEN if side == "up" else RED, "مسیر شکست ابر"),
                         _ray(ctx, (i, cloud_top if side == "up" else cloud_bottom), (far, cloud_top if side == "up" else cloud_bottom),
                              "right", MUTED, "پولبک به لبه ابر", True)],
                "points": _lpoints(ctx, [(i, float(cloud_top)), (i, float(cloud_bottom))], ["سقف ابر", "کف ابر"]),
                "labels": [_label(ctx, i, float(ctx.c[i]), "شکست ابر کومو", PURPLE),
                           _label(ctx, far, float(cloud_top if side == "up" else cloud_bottom),
                                  f"ابر {'سبز' if cloud_is_green else 'قرمز'} · ضخامت "
                                  f"{(cloud_top - cloud_bottom) / max(ctx.av(i), 1e-9):.1f} ATR", ccol)],
            }
        elif key == "ichimoku_future_cloud":
            color = _s(p, "color", "green")
            now_green = span_a[i] > span_b[i]
            prev_green = span_a[i - 1] > span_b[i - 1]
            hit = (now_green and not prev_green) if color == "green" else (not now_green and prev_green)
            details = {
                "lines": [
                    _line(ctx, (i, span_a[i]), (far, span_a[i]), GREEN if now_green else RED, "سنکو A"),
                    _line(ctx, (i, span_b[i]), (far, span_b[i]), RED if now_green else GREEN, "سنکو B"),
                ],
                "rays": [_ray(ctx, (max(0, i - 26), span_a[i]), (i, span_a[i]), "right", GREEN if now_green else RED,
                              "ادامه سنکو A", True),
                         _ray(ctx, (max(0, i - 26), span_b[i]), (i, span_b[i]), "right", RED if now_green else GREEN,
                              "ادامه سنکو B", True)],
                "zones": [_zone(ctx, max(span_a[i], span_b[i]), min(span_a[i], span_b[i]), i, far,
                                GREEN if now_green else RED, "ابر آینده")],
                "levels": [_level(ctx, span_a[i], GREEN if now_green else RED, "سنکو A"),
                           _level(ctx, span_b[i], RED if now_green else GREEN, "سنکو B")],
                "points": _lpoints(ctx, [(i, float(span_a[i])), (i, float(span_b[i]))], ["A آینده", "B آینده"]),
                "labels": [_label(ctx, i, max(span_a[i], span_b[i]),
                                  "ابر آینده سبز" if now_green else "ابر آینده قرمز",
                                  GREEN if now_green else RED)],
            }
        elif key == "ichimoku_lagging_exit":
            side = _s(p, "side", "up")
            if i >= 26:
                lag_now = ctx.c[i - 26]
                lag_prev = ctx.c[i - 27]
                top_prev = max(span_a[i - 1], span_b[i - 1])
                bot_prev = min(span_a[i - 1], span_b[i - 1])
                if side == "up":
                    hit = lag_prev <= top_prev and lag_now > cloud_top
                else:
                    hit = lag_prev >= bot_prev and lag_now < cloud_bottom
                details = {
                    "lines": [_line(ctx, (i - 26, lag_now), (i, lag_now), BLUE, "Lagging (چیکو)", True)],
                    "rays": [_ray(ctx, (i - 26, lag_now), (i, lag_now), "right", BLUE, "خروج چیکو از ابر"),
                             _ray(ctx, (i, float(ctx.c[i])), (far, float(ctx.c[i])), "right",
                                  GREEN if side == "up" else RED, "مسیر قیمت", True)],
                    "zones": [cloud_zone],
                    "levels": [_level(ctx, cloud_top if side == "up" else cloud_bottom, PURPLE, "لبه ابر"),
                               _level(ctx, lag_now, BLUE, "مقدار چیکو")],
                    "points": _lpoints(ctx, [(i - 26, float(lag_now)), (i, float(ctx.c[i]))], ["چیکو", "قیمت فعلی"]),
                    "labels": [_label(ctx, i, float(lag_now), "خروج چیکو از ابر", BLUE)],
                }
        elif key == "ichimoku_tenkan_kijun_range":
            cloud = _s(p, "cloud", "above")
            cond = ctx.c[i] < cloud_bottom if cloud == "below" else ctx.c[i] > cloud_top
            lo, hi = min(tenkan[i], kijun[i]), max(tenkan[i], kijun[i])
            hit = lo <= ctx.c[i] <= hi and cond
            details = {
                "levels": [_level(ctx, lo, BLUE, "تنکنسن"), _level(ctx, hi, AMBER, "کیجونسن"),
                           _level(ctx, cloud_top if cloud == "above" else cloud_bottom, PURPLE, "لبه ملاک ابر")],
                "rays": [_ray(ctx, (i - 26, lo), (i, lo), "right", BLUE, "ادامه تنکنسن"),
                         _ray(ctx, (i - 26, hi), (i, hi), "right", AMBER, "ادامه کیجونسن"),
                         _ray(ctx, (i, float(ctx.c[i])), (far, float(ctx.c[i])), "right", CYAN,
                              "قیمت در فاصلهٔ TK تا KK", True)],
                "zones": [cloud_zone, _zone(ctx, hi, lo, i - 26, far, CYAN, "ناحیه TK/KK")],
                "points": _lpoints(ctx, [(i, float(lo)), (i, float(hi)), (i, float(ctx.c[i]))],
                                   ["TK", "KK", "قیمت"]),
                "labels": [_label(ctx, i, float(ctx.c[i]), "قیمت بین تنکنسن و کیجونسن", CYAN),
                           _label(ctx, far, float((lo + hi) / 2.0),
                                  f"عرض TK-KK {abs(hi - lo) / max(ctx.av(i), 1e-9):.2f} ATR", AMBER)],
                "fib": [_fib(ctx, (i, float(lo)), (i, float(hi)), "تقسیم فاصلهٔ TK تا KK",
                            [0.0, 0.236, 0.382, 0.5, 0.618, 1.0])],
            }
        if hit:
            tk_start = max(0, i - 26)
            # اجزای مشترک همهٔ استراتژی‌های ایچیموکو: دو خط اصلی به‌شکل منحنی، ضخامت ابر
            # و برچسب خودِ استراتژی روی کندل محرک
            common = _overlay(
                lines=(_curve(ctx, tenkan, tk_start, i, BLUE, "تنکنسن", False, keep=8)
                       + _curve(ctx, kijun, tk_start, i, AMBER, "کیجونسن", False, keep=8)),
                levels=[_level(ctx, float(cloud_top), PURPLE, "سقف ابر"),
                        _level(ctx, float(cloud_bottom), PURPLE, "کف ابر"),
                        _level(ctx, float(tenkan[i]), BLUE, "تنکنسن"),
                        _level(ctx, float(kijun[i]), AMBER, "کیجونسن")],
                rays=[_ray(ctx, (i, float(tenkan[i])), (far, float(tenkan[i])), "right", BLUE,
                           "امتداد تنکنسن", True),
                      _ray(ctx, (i, float(kijun[i])), (far, float(kijun[i])), "right", AMBER,
                           "امتداد کیجونسن", True)],
                points=_lpoints(ctx, [(i, float(ctx.c[i]))], ["قیمت سیگنال"]),
                fib=[_fib(ctx, (i, float(cloud_bottom)), (i, float(cloud_top)),
                          "ضخامت ابر کومو", [0.0, 0.382, 0.5, 0.618, 1.0])],
                labels=[_label(ctx, i, float(ctx.c[i]), s.title[:38], MUTED)],
                indicators=_indicators("ichimoku", "ema:50"),
            )
            base = _cap_overlay(_merge_overlay(
                _overlay(marker=_marker(ctx, i, s.direction), **details), common))
            out.append(Detection(s, i, float(ctx.c[i]), base))
            return out
    return out


# ---- اندیکاتورها ----
def _indicator_panel(ctx: "Context", i: int, key: str, title: str,
                     series: Sequence[Tuple[Sequence[float], str, str, bool]],
                     levels: Sequence[Tuple[float, str, str]] = (),
                     lookback: int = 30, zero: bool = False, height: int = 90) -> Dict[str, Any]:
    """پنل شاخصی اطراف کندل سیگنال؛ اعداد تشخیص‌داده‌شده روی محور خود اندیکاتور."""
    a = max(0, i - int(lookback))
    built = [_series(ctx, arr, a, i, name, color, dash) for arr, name, color, dash in series]
    return _panel(key, title, built, [_plevel(v, c, t) for v, c, t in levels], height, zero)


def _rsi_cross(ctx: Context, s: Strategy, p: Dict[str, str], start: int, end: int) -> List[Detection]:
    level = _f(p, "level", 50)
    direction = _s(p, "dir", "up")
    period = int(max(2, _f(p, "period", 14)))
    r = ctx.R(period)
    out: List[Detection] = []
    for i in range(max(start, 20), end + 1):
        up = r[i - 1] <= level < r[i]
        down = r[i - 1] >= level > r[i]
        if (direction == "up" and up) or (direction == "down" and down):
            color = GREEN if direction == "up" else RED
            panel = _indicator_panel(
                ctx, i, "rsi", f"RSI {period}",
                [(r, f"RSI {period}", color, False)],
                [(level, AMBER, f"آستانه {level:g}"), (70, RED, "اشباع خرید"), (30, GREEN, "اشباع فروش")],
            )
            add = _merge_overlay(
                _index_toolkit(ctx, i, direction, color, 40,
                               curves=((20, "EMA 20", BLUE), (50, "EMA 50", PURPLE)),
                               extra_ind=("rsi:%d" % period, "ema:20", "ema:50")),
                _overlay(
                    levels=[_level(ctx, float(ctx.c[i]), color, f"تقاطع RSI {level:g}")],
                    panels=[x for x in [panel] if x],
                    rays=[_ray(ctx, (i, float(ctx.c[i])), (_far_index(ctx, i, 15), float(ctx.c[i])),
                                 "right", color, "خط سیگنال RSI", True)],
                    points=_lpoints(ctx, [(i, float(ctx.c[i]))], [f"RSI {r[i]:.0f}"]),
                    labels=[_label(ctx, i, float(ctx.c[i]), f"RSI {r[i]:.0f} عبور از {level:g}", color),
                            _label(ctx, i, float(ctx.l[i] if direction == "up" else ctx.h[i]),
                                   f"RSI {r[i]:.0f} · ADX {ctx.strength(i):.0f}", PURPLE)],
                    indicators=_indicators("rsi:%d" % period),
                ))
            out.append(Detection(s, i, float(ctx.c[i]),
                                 _marker_overlay(ctx, i, s.direction, f"RSI {level:g}", add=add)))
            return out
    return out


def _rsi_ma_cross(ctx: Context, s: Strategy, p: Dict[str, str], start: int, end: int) -> List[Detection]:
    direction = _s(p, "dir", "up")
    period = int(max(2, _f(p, "period", 14)))
    span = int(max(2, _f(p, "ma", 14)))
    r = ctx.R(period)
    ma = sma(r, span)
    out: List[Detection] = []
    for i in range(max(start, 40), end + 1):
        if not all(np.isfinite(x) for x in (r[i], r[i - 1], ma[i], ma[i - 1])):
            continue
        up = r[i - 1] <= ma[i - 1] and r[i] > ma[i]
        down = r[i - 1] >= ma[i - 1] and r[i] < ma[i]
        if (direction == "up" and up) or (direction == "down" and down):
            color = GREEN if direction == "up" else RED
            panel = _indicator_panel(
                ctx, i, "rsi", f"RSI {period} و میانگین {span} روزهٔ آن",
                [(r, f"RSI {period}", color, False), (ma, f"SMA {span} روی RSI", PURPLE, True)],
                [(float(r[i]), color, f"مقدار RSI {r[i]:.0f}"), (50, MUTED, "میانه ۵۰")],
            )
            add = _merge_overlay(
                _index_toolkit(ctx, i, direction, color, 40,
                               curves=((20, "EMA 20", BLUE), (50, "EMA 50", PURPLE)),
                               extra_ind=("rsi:%d" % period, "ema:20", "ema:50")),
                _overlay(
                    levels=[_level(ctx, float(ctx.c[i]), color, "تقاطع RSI با میانگین خود"),
                            _level(ctx, float(ctx.c[i]) + ctx.av(i), MUTED, "حد بالای اعتبار سیگنال"),
                            _level(ctx, float(ctx.c[i]) - ctx.av(i), MUTED, "حد پایین اعتبار سیگنال")],
                    panels=[x for x in [panel] if x],
                    points=_lpoints(ctx, [(i, float(ctx.c[i]))], ["تقاطع RSI/MA"]),
                    rays=[_ray(ctx, (i, float(ctx.c[i])), (_far_index(ctx, i, 15), float(ctx.c[i])),
                                    "right", color, "خط سیگنال RSI/MA", True)],
                    zones=[_zone(ctx, float(ctx.c[i]) + ctx.av(i) * 0.5,
                                 float(ctx.c[i]) - ctx.av(i) * 0.5, max(0, i - 20), i, color,
                                 "ناحیهٔ فعال سیگنال RSI/MA")],
                    labels=[_label(ctx, i, float(ctx.c[i]), f"RSI {r[i]:.0f} / MA {ma[i]:.0f}", color)],
                    indicators=_indicators("rsi:%d" % period),
                ))
            out.append(Detection(s, i, float(ctx.c[i]), _marker_overlay(ctx, i, s.direction, "RSI/MA", add=add)))
            return out
    return out


def _macd_signal_cross(ctx: Context, s: Strategy, p: Dict[str, str], start: int, end: int) -> List[Detection]:
    direction = _s(p, "dir", "up")
    line, sig, hist = ctx._macd
    out: List[Detection] = []
    for i in range(max(start, 40), end + 1):
        up = line[i - 1] <= sig[i - 1] and line[i] > sig[i]
        down = line[i - 1] >= sig[i - 1] and line[i] < sig[i]
        if (direction == "up" and up) or (direction == "down" and down):
            color = GREEN if direction == "up" else RED
            panel = _indicator_panel(
                ctx, i, "macd", "MACD 12/26/9",
                [(line, "MACD", color, False), (sig, "Signal", AMBER, True), (hist, "Histogram", BLUE, False)],
                [(float(sig[i]), AMBER, f"خط سیگنال {sig[i]:.4g}"), (0.0, MUTED, "خط صفر")],
                zero=True,
            )
            add = _merge_overlay(
                _index_toolkit(ctx, i, direction, color, 40,
                               curves=((20, "EMA 20", BLUE), (50, "EMA 50", PURPLE), (200, "EMA 200", AMBER)),
                               extra_ind=("macd:12:26:9", "ema:20", "ema:50", "ema:200")),
                _overlay(
                    panels=[x for x in [panel] if x],
                    points=_lpoints(ctx, [(i, float(ctx.c[i]))], ["تقاطع MACD/سیگنال"]),
                    labels=[_label(ctx, i, float(ctx.c[i]), f"MACD {line[i]:.4g} ↔ {sig[i]:.4g}", color)],
                    zones=[_zone(ctx, float(ctx.c[i]) + ctx.av(i) * 0.45, float(ctx.c[i]) - ctx.av(i) * 0.45,
                                 max(0, i - 20), i, color, "ناحیهٔ فعال تقاطع MACD")],
                    indicators=_indicators("macd:12:26:9", "ema:200"),
                ))
            out.append(Detection(s, i, float(ctx.c[i]), _marker_overlay(ctx, i, s.direction, "MACD", add=add)))
            return out
    return out


def _macd_histogram(ctx: Context, s: Strategy, p: Dict[str, str], start: int, end: int) -> List[Detection]:
    direction = _s(p, "dir", "up")
    line, sig, hist = ctx._macd
    out: List[Detection] = []
    for i in range(max(start, 40), end + 1):
        # دو عنوان کاتالوگ با یک منطق‌کی: «هیستوگرام و خط سیگنال» و «هیستوگرام و خط مکدی»
        vs_sig = (hist[i - 1] <= sig[i - 1] and hist[i] > sig[i]) if direction == "up" else (hist[i - 1] >= sig[i - 1] and hist[i] < sig[i])
        vs_line = (hist[i - 1] <= line[i - 1] and hist[i] > line[i]) if direction == "up" else (hist[i - 1] >= line[i - 1] and hist[i] < line[i])
        if vs_sig or vs_line:
            color = GREEN if direction == "up" else RED
            panel = _indicator_panel(
                ctx, i, "macd", "هیستوگرام MACD",
                [(hist, "Histogram", color, False), (sig, "Signal", AMBER, True), (line, "MACD", BLUE, True)],
                [(float(hist[i]), color, f"هیستوگرام {hist[i]:.4g}"), (float(sig[i]), AMBER, "خط سیگنال"),
                 (0.0, MUTED, "خط صفر")],
                zero=True,
            )
            add = _merge_overlay(
                _index_toolkit(ctx, i, direction, color, 40,
                               curves=((20, "EMA 20", BLUE), (50, "EMA 50", PURPLE)),
                               extra_ind=("macd:12:26:9", "ema:20", "ema:50")),
                _overlay(
                    panels=[x for x in [panel] if x],
                    levels=[_level(ctx, float(ctx.c[i]) + ctx.av(i) * 1.2, color,
                                   "مرز شتاب صعودی"),
                            _level(ctx, float(ctx.c[i]) - ctx.av(i) * 1.2, MUTED,
                                   "مرز شتاب نزولی")]
                           if vs_sig else [],
                    points=_lpoints(ctx, [(i, float(ctx.c[i]))],
                                    ["عبور هیستوگرام از سیگنال" if vs_sig else "عبور هیستوگرام از MACD"]),
                    rays=[_ray(ctx, (i, float(ctx.c[i])), (_far_index(ctx, i, 15), float(ctx.c[i])),
                                 "right", color, "خط سیگنال هیستوگرام", True)],
                    zones=[_zone(ctx, float(ctx.h[i]) + ctx.av(i) * 0.4, float(ctx.l[i]) - ctx.av(i) * 0.4,
                                 max(0, i - 18), i, color, "ناحیهٔ شتاب قیمت")],
                    labels=[_label(ctx, i, float(ctx.c[i]),
                                   ("عبور از سیگنال" if vs_sig else "عبور از خط MACD")
                                   + f" · هیستوگرام {hist[i]:.4g}", color)],
                    indicators=_indicators("macd:12:26:9"),
                ))
            out.append(Detection(s, i, float(ctx.c[i]), _marker_overlay(ctx, i, s.direction, "MACD", add=add)))
            return out
    return out


def _macd_zero_cross(ctx: Context, s: Strategy, p: Dict[str, str], start: int, end: int) -> List[Detection]:
    direction = _s(p, "dir", "up")
    line, sig, hist = ctx._macd
    out: List[Detection] = []
    for i in range(max(start, 40), end + 1):
        up = line[i - 1] <= 0 < line[i]
        down = line[i - 1] >= 0 > line[i]
        if (direction == "up" and up) or (direction == "down" and down):
            color = GREEN if direction == "up" else RED
            panel = _indicator_panel(
                ctx, i, "macd", "MACD و خط صفر",
                [(line, "MACD", color, False), (sig, "Signal", AMBER, True), (hist, "Histogram", BLUE, True)],
                [(0.0, MUTED, "خط صفر"), (float(line[i]), color, f"مقدار {line[i]:.4g}")],
                zero=True,
            )
            add = _merge_overlay(
                _index_toolkit(ctx, i, direction, color, 45,
                               curves=((50, "EMA 50", PURPLE), (200, "EMA 200", AMBER)),
                               extra_ind=("macd:12:26:9", "ema:50", "ema:200", "adx:14")),
                _overlay(
                    panels=[x for x in [panel] if x],
                    points=_lpoints(ctx, [(i, float(ctx.c[i]))], ["عبور از صفر"]),
                    labels=[_label(ctx, i, float(ctx.c[i]), f"MACD {'مثبت' if direction == 'up' else 'منفی'} شد", color)],
                    levels=[_level(ctx, float(ctx.E(200)[i]), AMBER, "EMA 200 (مرز فاز)"),
                            _level(ctx, float(ctx.c[i]) + ctx.av(i) * 1.5, color, "هدف کوتاه")],
                    rays=[_ray(ctx, (i, float(ctx.c[i])), (_far_index(ctx, i, 18), float(ctx.c[i])),
                                 "right", color, "آغاز فاز جدید", True)],
                    zones=[_zone(ctx, max(float(ctx.E(50)[i]), float(ctx.E(200)[i])),
                                 min(float(ctx.E(50)[i]), float(ctx.E(200)[i])),
                                 max(0, i - 20), i, color, "فاصلهٔ EMA 50 تا 200")],
                    indicators=_indicators("macd:12:26:9", "ema:200", "adx:14"),
                ))
            out.append(Detection(s, i, float(ctx.c[i]), _marker_overlay(ctx, i, s.direction, "MACD 0", add=add)))
            return out
    return out


def _price_ema_cross(ctx: Context, s: Strategy, p: Dict[str, str], start: int, end: int) -> List[Detection]:
    period = int(max(2, _f(p, "period", 50)))
    direction = _s(p, "dir", "up")
    e = ctx.E(period)
    out: List[Detection] = []
    for i in range(max(start, period + 2), end + 1):
        up = ctx.c[i - 1] <= e[i - 1] and ctx.c[i] > e[i]
        down = ctx.c[i - 1] >= e[i - 1] and ctx.c[i] < e[i]
        if (direction == "up" and up) or (direction == "down" and down):
            color = GREEN if direction == "up" else RED
            far = _far_index(ctx, i, 20)
            slope = float(e[i]) - float(e[max(0, i - 10)])
            stops, ind = _stop_levels(ctx, i)
            a0 = max(0, i - 30)
            ov = _merge_overlay(
                _index_toolkit(ctx, i, direction, color, 45, extra_ind=("ema:%d" % period, "ema:200", "adx:14")),
                _overlay(
                    lines=(_curve(ctx, e, a0, i, BLUE, f"EMA {period}", False, keep=10)
                           + _curve(ctx, ctx.E(200), a0, i, AMBER, "EMA 200", True, keep=8)),
                    rays=[_ray(ctx, (a0, e[a0]), (i, e[i]), "right", BLUE, f"امتداد EMA {period}"),
                          _ray(ctx, (i, float(ctx.c[i])), (far, float(ctx.c[i])), "right", color,
                               "مسیر پس از مقطع", True)],
                    levels=[_level(ctx, float(e[i]), BLUE, f"EMA {period}"),
                            _level(ctx, float(e[i]) + slope, MUTED, f"EMA {period} در ادامه"),
                            _level(ctx, float(ctx.E(200)[i]), AMBER, "EMA 200"),
                            _level(ctx, float(np.max(ctx.h[a0:i + 1])), RED, "سقف پیشِ مقطع"),
                            _level(ctx, float(np.min(ctx.l[a0:i + 1])), GREEN, "کف پیشِ مقطع"),
                            *stops],
                    zones=[_zone(ctx, float(e[i]) + ctx.av(i) * 0.25, float(e[i]) - ctx.av(i) * 0.25,
                                 a0, far, color, "ناحیهٔ مقطع با میانگین"),
                           _zone(ctx, float(max(ctx.c[i], e[i])), float(min(ctx.c[i], e[i])),
                                 i, far, color, "فاصلهٔ قیمت تا میانگین")],
                    points=_lpoints(ctx, [(i, float(e[i]))], [f"مقطع EMA {period}"]),
                    labels=[_label(ctx, i, float(ctx.c[i]), f"قیمت/میانگین EMA {period}", color),
                            _label(ctx, far, float(e[i]) + slope * 2, f"شیب EMA {period}: {slope:+.4g}", BLUE)],
                    fib=[_fib(ctx, (a0, float(np.min(ctx.l[a0:i + 1]))), (i, float(np.max(ctx.h[a0:i + 1]))),
                           f"بازگشت قیمت به EMA {period}", [0.382, 0.5, 0.618, 1.0])],
                    marker=_marker(ctx, i, s.direction, f"EMA {period}"),
                    indicators=_indicators(f"ema:{period}", "ema:200", "adx:14", *ind),
                ))
            ov = _cap_overlay(_merge_overlay(_signal_context(ctx, i, 45), ov))
            out.append(Detection(s, i, float(ctx.c[i]), ov))
            return out
    return out


def _ma_triple_cross(ctx: Context, s: Strategy, p: Dict[str, str], start: int, end: int) -> List[Detection]:
    direction = _s(p, "dir", "up")
    e9, e12, e15 = ctx.E(9), ctx.E(12), ctx.E(15)
    out: List[Detection] = []
    for i in range(max(start, 20), end + 1):
        up = e9[i - 1] <= e12[i - 1] <= e15[i - 1] and e9[i] > e12[i] > e15[i]
        down = e9[i - 1] >= e12[i - 1] >= e15[i - 1] and e9[i] < e12[i] < e15[i]
        if (direction == "up" and up) or (direction == "down" and down):
            color = GREEN if direction == "up" else RED
            a0 = max(0, i - 20)
            far = _far_index(ctx, i, 18)
            ribbon_top = max(float(e9[i]), float(e12[i]), float(e15[i]))
            ribbon_bottom = min(float(e9[i]), float(e12[i]), float(e15[i]))
            slope = float(e9[i]) - float(e9[max(0, i - 5)])
            stops, ind = _stop_levels(ctx, i)
            out.append(Detection(s, i, float(ctx.c[i]), _cap_overlay(_overlay(
                lines=(_curve(ctx, e9, a0, i, BLUE, "EMA 9", False, keep=8)
                       + _curve(ctx, e12, a0, i, AMBER, "EMA 12", False, keep=7)
                       + _curve(ctx, e15, a0, i, PURPLE, "EMA 15", False, keep=7)),
                rays=[_ray(ctx, (a0, e9[a0]), (i, e9[i]), "right", BLUE, "ادامه EMA 9"),
                      _ray(ctx, (a0, e12[a0]), (i, e12[i]), "right", AMBER, "ادامه EMA 12"),
                      _ray(ctx, (a0, e15[a0]), (i, e15[i]), "right", PURPLE, "ادامه EMA 15"),
                      _ray(ctx, (i, float(ctx.c[i])), (far, float(ctx.c[i]) + slope), "right", color,
                           "شعاع بازگشت Ribbon", True)],
                levels=[_level(ctx, float(e9[i]), BLUE, "EMA 9"), _level(ctx, float(e12[i]), AMBER, "EMA 12"),
                        _level(ctx, float(e15[i]), PURPLE, "EMA 15"),
                        _level(ctx, float(ctx.c[i]) + slope, color, "هدف ادامهٔ روند"), *stops],
                zones=[_zone(ctx, ribbon_top, ribbon_bottom, a0, far, color, "نوار میانگین‌ها (Ribbon)")],
                points=_lpoints(ctx, [(i, ribbon_top), (i, ribbon_bottom), (i, float(ctx.c[i]))],
                                ["سقف Ribbon", "کف Ribbon", "قیمت هنگام تقاطع"]),
                labels=[_label(ctx, i, float(ctx.c[i]), f"تقاطع ۹/۱۲/۱5 (ADX {ctx.strength(i):.0f})", color)],
                fib=[_fib(ctx, (i, ribbon_bottom), (i, ribbon_top), "هدف بر پایه Ribbon", [1.0, 1.272, 1.618]),
                     _fib(ctx, (a0, float(np.min(ctx.l[a0:i + 1]))), (i, float(np.max(ctx.h[a0:i + 1]))),
                          "نوسان مبنای Ribbon", [0.382, 0.5, 0.618, 1.0])],
                marker=_marker(ctx, i, s.direction),
                indicators=_indicators("ema:9", "ema:12", "ema:15", "adx:14", "volume", *ind),
            ), cap=36)))
            return out
    return out


def _cci_cross(ctx: Context, s: Strategy, p: Dict[str, str], start: int, end: int) -> List[Detection]:
    level = _f(p, "level", 100)
    direction = _s(p, "dir", "up")
    arr = ctx._cci
    out: List[Detection] = []
    for i in range(max(start, 30), end + 1):
        up = arr[i - 1] <= level < arr[i]
        down = arr[i - 1] >= level > arr[i]
        if (direction == "up" and up) or (direction == "down" and down):
            color = GREEN if direction == "up" else RED
            panel = _indicator_panel(
                ctx, i, "cci", "CCI 20",
                [(arr, "CCI", color, False)],
                [(level, AMBER, f"آستانه {level:g}"), (0.0, MUTED, "خط صفر"),
                 (100, RED, "+100"), (-100, GREEN, "-100")],
                zero=True,
            )
            add = _merge_overlay(
                _index_toolkit(ctx, i, direction, color, 40,
                               curves=((20, "EMA 20", BLUE),),
                               extra_ind=("cci:20", "ema:20", "adx:14")),
                _overlay(
                    levels=[_level(ctx, float(ctx.c[i]), color, f"CCI {level:g}")],
                    panels=[x for x in [panel] if x],
                    points=_lpoints(ctx, [(i, float(ctx.c[i]))], [f"CCI {arr[i]:.0f}"]),
                    rays=[_ray(ctx, (i, float(ctx.c[i])), (_far_index(ctx, i, 15), float(ctx.c[i])),
                                 "right", color, "خط سیگنال CCI", True)],
                    labels=[_label(ctx, i, float(ctx.c[i]), f"CCI {arr[i]:.0f} عبور از {level:g}", color),
                            _label(ctx, i, float(ctx.h[i] if direction == "up" else ctx.l[i]),
                                   f"قدرت روند {ctx.strength(i):.0f}", PURPLE)],
                    zones=[_zone(ctx, float(ctx.h[i]), float(ctx.l[i]), max(0, i - 15), i, color,
                                 "کندل شکست آستانه CCI")],
                    indicators=_indicators("cci:20", "adx:14"),
                ))
            out.append(Detection(s, i, float(ctx.c[i]), _marker_overlay(ctx, i, s.direction, "CCI", add=add)))
            return out
    return out


def _stoch_cross(ctx: Context, s: Strategy, p: Dict[str, str], start: int, end: int) -> List[Detection]:
    zone = _s(p, "zone", "os")
    k, d = ctx._stoch
    out: List[Detection] = []
    for i in range(max(start, 20), end + 1):
        if not all(np.isfinite(x) for x in (k[i], d[i], k[i - 1], d[i - 1])):
            continue
        cross_up = k[i - 1] <= d[i - 1] and k[i] > d[i]
        cross_down = k[i - 1] >= d[i - 1] and k[i] < d[i]
        if (zone == "os" and cross_up and min(k[i], d[i]) <= 30) or \
           (zone == "ob" and cross_down and max(k[i], d[i]) >= 70):
            color = GREEN if zone == "os" else RED
            panel = _indicator_panel(
                ctx, i, "stoch", "Stochastic 14/3",
                [(k, "%K", color, False), (d, "%D", PURPLE, True)],
                [(80, RED, "اشباع خرید ۸۰"), (20, GREEN, "اشباع فروش ۲۰"), (float(k[i]), AMBER, f"K {k[i]:.0f}")],
            )
            add = _merge_overlay(
                _index_toolkit(ctx, i, "up" if zone == "os" else "down", color, 40,
                               curves=((20, "EMA 20", BLUE),),
                               extra_ind=("stoch:14:3", "ema:20", "rsi:14")),
                _overlay(
                    levels=[_level(ctx, float(ctx.c[i]), color, "تقاطع K/D در ناحیه اشباع"),
                            _level(ctx, float(np.max(ctx.h[max(0, i - 14):i + 1])), RED, "سقف نوسان ۱۴ کندل"),
                            _level(ctx, float(np.min(ctx.l[max(0, i - 14):i + 1])), GREEN, "کف نوسان ۱۴ کندل")],
                    panels=[x for x in [panel] if x],
                    points=_lpoints(ctx, [(i, float(ctx.c[i])),
                                          (i, float(np.max(ctx.h[max(0, i - 14):i + 1]))),
                                          (i, float(np.min(ctx.l[max(0, i - 14):i + 1])))],
                                  ["تقاطع K/D", "مرز %K", "کف %K"]),
                    rays=[_ray(ctx, (i, float(ctx.c[i])), (_far_index(ctx, i, 15), float(ctx.c[i])),
                                 "right", color, "بازگشت از اشباع", True)],
                    labels=[_label(ctx, i, float(ctx.c[i]), f"Stoch K {k[i]:.0f} / D {d[i]:.0f}", color)],
                    zones=[_zone(ctx, float(np.max(ctx.h[max(0, i - 14):i + 1])),
                                 float(np.min(ctx.l[max(0, i - 14):i + 1])),
                                 max(0, i - 14), i, color,
                                 "ناحیه اشباع فروش (K&Do زیر ۳۰)" if zone == "os"
                                 else "ناحیه اشباع خرید (K&D بالای ۷۰)")],
                    fib=[_fib(ctx, (i, float(np.min(ctx.l[max(0, i - 14):i + 1]))),
                              (i, float(np.max(ctx.h[max(0, i - 14):i + 1]))),
                              "نوسان ۱۴ کندل (مبنای٪K)", [0.2, 0.5, 0.8, 1.0])],
                    indicators=_indicators("stoch:14:3", "rsi:14"),
                ))
            out.append(Detection(s, i, float(ctx.c[i]), _marker_overlay(ctx, i, s.direction, "Stoch", add=add)))
            return out
    return out


def _bollinger_touch(ctx: Context, s: Strategy, p: Dict[str, str], start: int, end: int) -> List[Detection]:
    side = _s(p, "side", "upper")
    upper, mid, lower = ctx._boll
    out: List[Detection] = []
    for i in range(max(start, 25), end + 1):
        green = ctx.c[i] >= ctx.o[i]
        red = not green
        band = upper if side == "upper" else lower
        if not ((side == "upper" and ctx.h[i] >= upper[i] and red) or (side == "lower" and ctx.l[i] <= lower[i] and green)):
            continue
        color = RED if side == "upper" else GREEN
        a0 = max(0, i - 24)
        far = _far_index(ctx, i, 18)
        slope = float(band[i]) - float(band[a0])
        stops, ind = _stop_levels(ctx, i)
        width = float(upper[i]) - float(lower[i])
        midw = float(np.mean([float(upper[k]) - float(lower[k]) for k in range(a0, i + 1)]))
        lines = (_curve(ctx, upper, a0, i, RED, "باند بالا", True, keep=8)
                 + _curve(ctx, lower, a0, i, GREEN, "باند پایین", True, keep=8)
                 + _curve(ctx, mid, a0, i, BLUE, "میانگین بولینگر", False, keep=8))
        out.append(Detection(s, i, float(ctx.c[i]), _cap_overlay(_overlay(
            lines=lines,
            rays=[_ray(ctx, (a0, band[a0]), (i, band[i]), "right", color, "ادامه باند لمسی"),
                  _ray(ctx, (a0, mid[a0]), (i, mid[i]), "right", BLUE, "بازگشت به میانگین", True)],
            levels=[_level(ctx, float(band[i]), color, "باند لمسی"), _level(ctx, float(mid[i]), BLUE, "میانه باند"),
                    _level(ctx, float(mid[i]) + slope, MUTED, "مقدار باند در ادامه"),
                    _level(ctx, float(upper[i]) if side == "upper" else float(mid[i]), MUTED,
                           "باند مخالف"), *stops],
            zones=[_zone(ctx, float(upper[i]), float(lower[i]), a0, far, MUTED, "ناحیه باندها"),
                   _zone(ctx, float(ctx.h[i]), float(ctx.l[i]), i, far, color,
                         "کندل برخورد با باند")],
            points=_lpoints(ctx, [(i, float(ctx.h[i] if side == "upper" else ctx.l[i])),
                                   (i, float(mid[i]))], ["برخورد به باند", "میانه باند"]),
            labels=[_label(ctx, i, float(band[i]),
                           f"عرض باندها {width / max(ctx.av(i), 1e-9):.1f} ATR", AMBER),
                    _label(ctx, far, float(mid[i]),
                           "فشردگی (Squeeze)" if width < midw * 0.75 else "انبساط باندها", PURPLE)],
            fib=[_fib(ctx, (i, float(lower[i])), (i, float(upper[i])), "تقسیم عرض باندها",
                   [0.0, 0.25, 0.5, 0.75, 1.0])],
            marker=_marker(ctx, i, s.direction),
            indicators=_indicators("boll:20:2", "sma:20", "ema:20", *ind),
        ))))
        return out
    return out


def _volume_spike(ctx: Context, s: Strategy, p: Dict[str, str], start: int, end: int) -> List[Detection]:
    window = int(max(5, _f(p, "window", 30)))
    mult = max(1.1, _f(p, "mult", 2))
    avg = ctx.vip_avg(window)
    out: List[Detection] = []
    for i in range(max(start, window + 2), end + 1):
        if not np.isfinite(avg[i]) or avg[i] <= 0:
            continue
        if ctx.v[i] >= avg[i] * mult:
            ratio = float(ctx.v[i]) / float(avg[i])
            panel = _indicator_panel(
                ctx, i, "volume", f"حجم در مقایسه با میانگین {window} کندل",
                [(ctx.v, "حجم", AMBER, False), (avg, f"میانگین {window}", MUTED, True)],
                [(float(avg[i]) * mult, RED, f"آستانه {mult:g}×"), (float(avg[i]), MUTED, "میانگین")],
                lookback=min(60, window), height=70,
            )
            far = _far_index(ctx, i, 20)
            w = ctx.VWAP()[i]
            a0 = max(0, i - min(40, max(10, window)))
            levels = [_level(ctx, float(ctx.c[i]), AMBER, f"حجم {mult:g} برابر"),
                      _level(ctx, float(ctx.h[i]), RED, "سقف کندل حجم غیرعادی"),
                      _level(ctx, float(ctx.l[i]), GREEN, "کف کندل حجم غیرعادی")]
            if np.isfinite(w):
                levels += _mark_items([_level(ctx, float(w), CYAN, "VWAP روزانه")])
            # پیشینهٔ جهش‌های حجم در همان پنجره: ستون‌های مقایسه روی چارت
            prior = [(k, float(ctx.v[k]) / float(avg[k])) for k in range(a0, i)
                     if np.isfinite(avg[k]) and avg[k] > 0 and ctx.v[k] >= avg[k] * mult][:6]
            spike_pts = _lpoints(ctx, [(k, float(ctx.h[k])) for k, _ in prior],
                                 [f"حجم {rr:.1f}×" for _, rr in prior])
            vcol = GREEN if ctx.c[i] >= ctx.o[i] else RED
            ov = _overlay(
                lines=_mark_items(
                    _curve(ctx, ctx.E(20), a0, i, BLUE, "EMA 20", True, keep=8)
                    + (_curve(ctx, ctx.VWAP(), a0, i, CYAN, "VWAP", True, keep=8)
                       if np.isfinite(w) else [])),
                levels=levels,
                panels=[x for x in [panel] if x],
                zones=[_zone(ctx, float(ctx.h[i]), float(ctx.l[i]), i, far, AMBER, "کندل حجم غیرعادی"),
                       _zone(ctx, float(np.max(ctx.h[a0:i + 1])), float(np.min(ctx.l[a0:i + 1])),
                             a0, i, MUTED, f"دامنه {i - a0} کندل قبل از جهش")],
                rays=[_ray(ctx, (i, float(ctx.c[i])), (far, float(ctx.c[i])), "right", AMBER,
                           "ادامه حرکت با حجم", True),
                      _ray(ctx, (i, float(ctx.h[i])), (far, float(ctx.h[i]) + (float(ctx.h[i]) - float(ctx.l[i]))),
                           "right", vcol, "پرتو ادامهٔ کندل پرحجم", True)],
                points=_lpoints(ctx, [(i, float(ctx.c[i]))], [f"حجم {ratio:.1f}×"]) + spike_pts,
                labels=[_label(ctx, i, float(ctx.h[i]), f"حجم {ratio:.1f}× میانگین {window}", AMBER),
                        _label(ctx, i, float(ctx.l[i]),
                               f"بدنه/دامنه {abs(float(ctx.c[i]) - float(ctx.o[i])) / max(ctx.h[i] - ctx.l[i], 1e-9):.2f}",
                               MUTED)],
                fib=[_fib(ctx, (i, float(ctx.l[i])), (i, float(ctx.h[i])),
                         "اندازه‌گیری حرکت پس از جهش حجم", [0.5, 1.0, 1.272, 1.618, 2.0])],
                marker=_marker(ctx, i, s.direction, "Volume"),
                indicators=_indicators("volume", "vwap", "ema:20"),
            )
            out.append(Detection(s, i, float(ctx.c[i]), _cap_overlay(ov)))
            return out
    return out


# ---- واگرایی ----
def _divergence_hits(ctx: Context, s: Strategy, p: Dict[str, str], bullish: bool, use_macd: bool,
                     confirmed_required: bool, start: int, end: int) -> List[Detection]:
    highs, lows = ctx.pivots(3)
    ind = ctx._macd[2] if use_macd else ctx.R(14)
    pts = lows if bullish else highs
    out: List[Detection] = []
    for i in range(start, end + 1):
        conf = [x for x in pts if x[0] <= i - 3]
        if len(conf) < 2:
            continue
        (i1, p1), (i2, p2) = conf[-2], conf[-1]
        if i2 - i1 < 4 or i - i1 > 80 or i - i2 > 30:
            continue
        if bullish:
            price_cond = p2 < p1 - ctx.av(i2) * 0.1
            ind_cond = ind[i2] > ind[i1]
            hidden = p2 > p1 + ctx.av(i2) * 0.1 and ind[i2] < ind[i1]
        else:
            price_cond = p2 > p1 + ctx.av(i2) * 0.1
            ind_cond = ind[i2] < ind[i1]
            hidden = p2 < p1 - ctx.av(i2) * 0.1 and ind[i2] > ind[i1]
        kind = _s(p, "kind", "regular")
        ok = (price_cond and ind_cond) if kind == "regular" else (hidden if kind == "hidden" else price_cond and ind_cond)
        if not ok:
            continue
        if confirmed_required:
            if bullish:
                conf_ok = ctx.c[i] > float(np.max(ctx.h[i2:i])) if i > i2 else False
            else:
                conf_ok = ctx.c[i] < float(np.min(ctx.l[i2:i])) if i > i2 else False
            if not conf_ok:
                continue
        color = GREEN if bullish else RED
        far = _far_index(ctx, i, 20)
        ind_name = "MACD Histogram" if use_macd else "RSI 14"
        ind_panel = _panel(
            "macd" if use_macd else "rsi", f"واگرایی روی {ind_name}",
            [_series(ctx, ind, max(0, i1 - 6), min(ctx.n - 1, i2 + 6), ind_name, MUTED, True, keep=16),
             {"title": "پایِ واگرایی", "color": color, "dashed": False,
              "points": [_tpoint(ctx, i1, float(ind[i1])), _tpoint(ctx, i2, float(ind[i2]))]}],
            levels=([(0.0, MUTED, "خط صفر")] if use_macd else [(70, RED, "اشباع خرید"), (30, GREEN, "اشباع فروش")]),
            zero=use_macd, height=90,
        )
        confirm = float(np.max(ctx.h[i2:i + 1])) if bullish else float(np.min(ctx.l[i2:i + 1]))
        out.append(Detection(s, i, float(ctx.c[i]), _overlay(
            lines=[_line(ctx, (i1, p1), (i2, p2), color, "واگرایی")],
            rays=[_ray(ctx, (i1, p1), (i2, p2), "right", color, "امتداد پایِ واگرایی"),
                  _ray(ctx, (i2, confirm), (far, confirm), "right", MUTED, "خط تأیید واگرایی", True)],
            levels=[_level(ctx, p2, color, "نقطه دوم واگرایی"), _level(ctx, confirm, MUTED, "خط تأیید")],
            zones=[_zone(ctx, max(p1, p2, confirm), min(p1, p2), i1, far, color, "ناحیه واگرایی")],
            points=_lpoints(ctx, [(i1, p1), (i2, p2)],
                            ["کف ۱", "کف ۲"] if bullish else ["قله ۱", "قله ۲"]),
            labels=[
                _label(ctx, i1, p1, f"{ind_name} {ind[i1]:.3g}", MUTED),
                _label(ctx, i2, p2, f"{ind_name} {ind[i2]:.3g}", color),
                _label(ctx, i, float(ctx.c[i]),
                       ("قیمت کف پایین‌تر / شاخص کف بالاتر" if bullish else "قیمت قله بالاتر / شاخص قله پایین‌تر")
                       + (" (مخفی)" if kind == "hidden" else ""), color),
            ],
            fib=[_fib(ctx, (i2, p2), (i1, p1), "هدف واگرایی", [0.5, 0.618, 1.0, 1.272])],
            panels=[x for x in [ind_panel] if x],
            marker=_marker(ctx, i, s.direction, "Divergence"),
            indicators=_indicators("macd:12:26:9" if use_macd else "rsi:14", "ema:20"),
        )))
        return out
    return out


def _rsi_divergence(ctx: Context, s: Strategy, p: Dict[str, str], start: int, end: int) -> List[Detection]:
    direction = _s(p, "dir", "bull")
    return _divergence_hits(ctx, s, p, direction == "bull", False, int(_f(p, "confirmed", 0)) == 1, start, end)


def _macd_divergence(ctx: Context, s: Strategy, p: Dict[str, str], start: int, end: int) -> List[Detection]:
    direction = _s(p, "dir", "bull")
    return _divergence_hits(ctx, s, p, direction == "bull", True, int(_f(p, "confirmed", 0)) == 1, start, end)


def _triple_divergence(ctx: Context, s: Strategy, p: Dict[str, str], start: int, end: int) -> List[Detection]:
    bullish = _s(p, "dir", "bull") == "bull"
    highs, lows = ctx.pivots(3)
    pts = lows if bullish else highs
    ind = ctx.R(14)
    out: List[Detection] = []
    for i in range(start, end + 1):
        conf = [x for x in pts if x[0] <= i - 3]
        if len(conf) < 3:
            continue
        seq = conf[-3:]
        (a, pa), (b, pb), (c_, pc) = seq
        if not (a < b < c_ and i - a <= 120):
            continue
        if bullish:
            price_ok = pb < pa and pc < pb
            ind_ok = ind[b] > ind[a] and ind[c_] > ind[b]
        else:
            price_ok = pb > pa and pc > pb
            ind_ok = ind[b] < ind[a] and ind[c_] < ind[b]
        if not (price_ok and ind_ok):
            continue
        color = GREEN if bullish else RED
        far = _far_index(ctx, i, 22)
        tri_panel = _panel(
            "rsi", "واگرایی سه‌گانه روی RSI 14",
            [_series(ctx, ind, max(0, a - 5), min(ctx.n - 1, c_ + 5), "RSI 14", MUTED, True, keep=18),
             {"title": "پای‌های RSI", "color": color, "dashed": False,
              "points": [_tpoint(ctx, a, float(ind[a])), _tpoint(ctx, b, float(ind[b])),
                         _tpoint(ctx, c_, float(ind[c_]))]}],
            levels=[_plevel(70, RED, "اشباع خرید"), _plevel(30, GREEN, "اشباع فروش")],
            height=90,
        )
        out.append(Detection(s, i, float(ctx.c[i]), _overlay(
            lines=[_line(ctx, (a, pa), (b, pb), color, "واگرایی ۱"), _line(ctx, (b, pb), (c_, pc), color, "واگرایی ۲")],
            rays=[_ray(ctx, (b, pb), (c_, pc), "right", color, "امتداد پای سوم"),
                  _ray(ctx, (a, pa), (c_, pc), "right", MUTED, "خط روند نقاط واگرایی", True)],
            levels=[_level(ctx, pc, color, "سومین نقطه واگرایی"),
                    _level(ctx, float(np.max(ctx.h[c_:i + 1])) if bullish else float(np.min(ctx.l[c_:i + 1])),
                           MUTED, "خط تأیید")],
            zones=[_zone(ctx, max(pa, pb, pc), min(pa, pb, pc), a, far, color, "ناحیه واگرایی سه‌گانه")],
            points=_lpoints(ctx, seq, ["موج ۱", "موج ۲", "موج ۳"]),
            labels=[_label(ctx, c_, pc, f"RSI {ind[c_]:.0f} در برابر {ind[a]:.0f}", color),
                    _label(ctx, i, float(ctx.c[i]), "واگرایی سه‌گانه", color)],
            fib=[_fib(ctx, (c_, pc), (a, pa), "هدف واگرایی سه‌گانه", [0.5, 0.618, 1.0, 1.272, 1.618])],
            panels=[x for x in [tri_panel] if x],
            marker=_marker(ctx, i, s.direction, "Triple Divergence"),
            indicators=_indicators("rsi:14", "ema:20"),
        )))
        return out
    return out


# ---- چنگال اندروز ----
def _andrews(ctx: Context, s: Strategy, p: Dict[str, str], start: int, end: int) -> List[Detection]:
    direction = _s(p, "dir", "up")
    event = _s(p, "event", "touch")
    side = _s(p, "side", "upper")
    highs, lows = ctx.pivots(3)
    # سه‌نقطهٔ چنگال اندروز باید از چرخش‌های متناوبِ قله/کف ساخته شود (کف-قله-کف برای چنگال
    # صعودی و بالعکس). پیش‌تر نوع پیوَت از روی سایهٔ همان کندل حدس زده می‌شد که تقریباً
    # همیشه «کف» درمی‌آمد و هر شش استراتژی چنگال نزولی هرگز فعال نمی‌شدند.
    sw = sorted([(int(i), float(v), True) for i, v in highs]
                + [(int(i), float(v), False) for i, v in lows], key=lambda z: (z[0], z[2]))
    alt: List[Tuple[int, float, bool]] = []
    for idx, val, is_high in sw:
        if not alt:
            alt.append((int(idx), float(val), is_high))
            continue
        last = alt[-1]
        if is_high == last[2]:
            if (val > last[1]) if is_high else (val < last[1]):
                alt[-1] = (int(idx), float(val), is_high)
            continue
        if int(idx) == last[0]:
            continue
        alt.append((int(idx), float(val), is_high))
    anchor_high = direction == "down"
    out: List[Detection] = []
    for i in range(max(start, 10), end + 1):
        for j in range(len(alt) - 2, 1, -1):
            p0, p1, p2 = alt[j - 1], alt[j], alt[j + 1]
            if p2[0] > i - 3:
                continue
            if p0[2] != anchor_high:
                continue
            mid = ((p1[0] + p2[0]) / 2.0, (p1[1] + p2[1]) / 2.0)
            if mid[0] == p0[0]:
                continue
            slope = (mid[1] - p0[1]) / (mid[0] - p0[0])
            if direction == "up" and slope <= 0:
                continue
            if direction == "down" and slope >= 0:
                continue
            med = p0[1] + slope * (i - p0[0])
            up_line = p1[1] + slope * (i - p1[0])
            dn_line = p2[1] + slope * (i - p2[0])
            upper, lower = max(up_line, dn_line), min(up_line, dn_line)
            target = med if s.logic_key == "andrews_median" else (upper if side == "upper" else lower)
            a = ctx.av(i)
            if event == "break":
                # جهت شکست باید با جهت سیگنال استراتژی هم‌خوان باشد؛ پیش‌تر هر شکستی
                # (حتی شکست خلاف جهت) سیگنال می‌داد.
                if s.direction == "LONG":
                    hit = ctx.c[i] > target + a * 0.05 and ctx.c[i - 1] <= target + a * 0.05
                else:
                    hit = ctx.c[i] < target - a * 0.05 and ctx.c[i - 1] >= target - a * 0.05
            else:
                hit = abs(ctx.c[i] - target) <= a * 0.7 or ctx.h[i] >= target - a * 0.2 and ctx.l[i] <= target + a * 0.2
            if not hit:
                continue
            color = GREEN if direction == "up" else RED
            end_idx = _far_index(ctx, i, 18)
            up_far = p1[1] + slope * (end_idx - p1[0])
            dn_far = p2[1] + slope * (end_idx - p2[0])
            med_far = p0[1] + slope * (end_idx - p0[0])
            lines = [
                _line(ctx, (p0[0], p0[1]), (end_idx, med_far), color, "میدلاین (Median)",
                      s.logic_key != "andrews_median"),
                _line(ctx, (p1[0], p1[1]), (end_idx, up_far), RED, "سقف چنگال"),
                _line(ctx, (p2[0], p2[1]), (end_idx, dn_far), GREEN, "کف چنگال"),
            ]
            rays = [
                _ray(ctx, (p0[0], p0[1]), (i, med), "right", color, "ادامه میدلاین",
                     s.logic_key != "andrews_median"),
                _ray(ctx, (p1[0], p1[1]), (i, up_line), "right", RED, "ادامه سقف چنگال"),
                _ray(ctx, (p2[0], p2[1]), (i, dn_line), "right", GREEN, "ادامه کف چنگال"),
            ]
            if event == "break":
                rays.append(_ray(ctx, (i - 1, float(ctx.c[i - 1])), (i, float(ctx.c[i])), "right", color, "مسیر شکست"))
            stops, ind = _stop_levels(ctx, i)
            out.append(Detection(s, i, float(ctx.c[i]), _overlay(
                lines=lines,
                rays=rays,
                levels=[_level(ctx, target, color, "خط هدف چنگال"),
                        _level(ctx, med, BLUE, "میدلاین"), _level(ctx, upper, RED, "سقف چنگال"),
                        _level(ctx, lower, GREEN, "کف چنگال"), *stops],
                zones=[_zone(ctx, upper, lower, p0[0], end_idx, MUTED, "بدنه چنگال")],
                points=_lpoints(ctx, [p0, p1, p2], ["A (مبنا)", "B", "C"]),
                labels=[_label(ctx, i, float(target),
                               f"{'شکست' if event == 'break' else 'رسیدن'} به {'میدلاین' if s.logic_key == 'andrews_median' else ('سقف' if side == 'upper' else 'کف')}",
                               color),
                        _label(ctx, p0[0], float(p0[1]), f"شیب چنگال {slope:+.4g}", MUTED)],
                fib=[_fib(ctx, (i, lower), (i, upper), "تقسیم‌بندی داخل چنگال", [0.382, 0.5, 0.618, 1.0])],
                marker=_marker(ctx, i, s.direction),
                indicators=_indicators("ema:50", "adx:14", *ind),
            )))
            return out
    return out


# ---- الگوهای کلاسیک ----
def _line_touches(ctx: "Context", i: int, p1: Tuple[int, float], p2: Tuple[int, float],
                  tol: float, use_high: bool, lookback: int = 45) -> List[Tuple[int, float]]:
    """چرخش‌هایی که روی یک خطِ هندسی افتاده‌اند؛ همین لمس‌ها مرزهای الگوی کلاسیک را تأیید می‌کنند."""
    if p2[0] == p1[0]:
        return []
    highs, lows = ctx.pivots(3)
    a = max(0, i - int(lookback))
    out: List[Tuple[int, float]] = []
    for idx, val in (highs if use_high else lows):
        if idx < a or idx > i - 3:
            continue
        if abs(float(val) - _line_between(p1, p2, int(idx))) <= tol:
            out.append((int(idx), float(val)))
    return out


def _classic(ctx: Context, s: Strategy, p: Dict[str, str], start: int, end: int) -> List[Detection]:
    kind = _s(p, "kind", "triangle")
    direction = _s(p, "dir", "up")
    suspicious = int(_f(p, "suspicious", 0)) == 1
    breakout = int(_f(p, "breakout", 1)) == 1
    out: List[Detection] = []
    for i in range(max(start, 35), end + 1):
        win = 40
        hi_idx = i - win
        fit_hi = ctx.line_value(i, "high", "any")
        fit_lo = ctx.line_value(i, "low", "any")
        if not fit_hi or not fit_lo:
            continue
        hv, hp1, hp2, hs = fit_hi
        lv, lp1, lp2, ls = fit_lo
        a = ctx.av(i)
        if hv <= lv:
            continue
        width = hv - lv
        width_prev = None
        hp = ctx.line_value(i - 5, "high", "any")
        lp = ctx.line_value(i - 5, "low", "any")
        if hp and lp:
            width_prev = hp[0] - lp[0]
        converging = width_prev is not None and width < width_prev * 0.92
        parallel = abs(hs - ls) <= max(0.0002, abs(hs) * 0.3 + 0.0002)
        if kind == "triangle" and not converging:
            continue
        if kind == "wedge" and not (converging and abs(hs - ls) > 0.0002):
            continue
        if kind == "rectangle" and abs(hs) > max(0.0002, a * 0.01) and abs(ls) > max(0.0002, a * 0.01):
            continue
        if kind == "flag":
            impulse = abs(ctx.c[i - 15] - ctx.c[i - 35])
            if impulse < a * 2.0 or width > a * 2.2:
                continue
        if direction == "up":
            hit = ctx.c[i] > hv + (0 if breakout else -a * 0.2)
            near = ctx.h[i] >= hv - a * 0.25
        else:
            hit = ctx.c[i] < lv - (0 if breakout else -a * 0.2)
            near = ctx.l[i] <= lv + a * 0.25
        ht = _line_touches(ctx, i, hp1, hp2, max(a * 0.55, width * 0.05), True)
        lt = _line_touches(ctx, i, lp1, lp2, max(a * 0.55, width * 0.05), False)
        # حالت «شکل‌گیری» (suspicious=0;breakout=0): الگو فقط رسم و تأیید می‌شود و هنوز
        # شکستی رخ نداده؛ این سطرهای کاتالوگ پیش‌تر پشت دو continue پیاپی هرگز فعال نمی‌شدند.
        formation = (not breakout) and (not suspicious)
        if formation:
            if len(ht) < 2 or len(lt) < 2:
                continue
            if not (lv - a * 0.5 <= ctx.c[i] <= hv + a * 0.5):
                continue
            midw = (hv + lv) / 2.0
            if direction == "up" and ctx.c[i] < midw:
                continue
            if direction == "down" and ctx.c[i] > midw:
                continue
            hit = False
        else:
            if not hit and not (suspicious and near):
                continue
        color = GREEN if direction == "up" else RED
        far = _far_index(ctx, i, 25)
        trigger = hv if direction == "up" else lv
        target = hv + width if direction == "up" else lv - width
        hs_far = _line_between(hp1, hp2, far)
        ls_far = _line_between(lp1, lp2, far)
        state = "شکست" if hit else ("مشکوک" if suspicious else "در حال شکل‌گیری")
        label_txt = f"{state} {kind}"
        stops, ind = _stop_levels(ctx, i)
        pat_start = max(0, min(hp1[0], lp1[0]))
        mid_a = (float(hp1[1]) + float(lp1[1])) / 2.0
        mid_b = (hv + lv) / 2.0
        conv = "همگرا" if kind in ("triangle", "wedge") else ("موازی" if kind == "rectangle" else "کج")
        lines = [
            _line(ctx, hp1, hp2, RED, "سقف الگو"),
            _line(ctx, lp1, lp2, GREEN, "کف الگو"),
            _line(ctx, hp2, (far, hs_far), RED, "امتداد سقف تا لبه چارت", True),
            _line(ctx, lp2, (far, ls_far), GREEN, "امتداد کف تا لبه چارت", True),
            _line(ctx, (pat_start, mid_a), (i, mid_b), MUTED, "میدلاین الگو", True),
            _line(ctx, (i, trigger), (far, target), color, "شیب اندازه‌گیری حرکت", True),
        ]
        lines += _mark_items(_curve(ctx, ctx.E(50), max(0, i - 38), i, BLUE, "EMA 50", True, keep=6))
        touch_pts = [(x, y) for x, y in (ht[:3] + lt[:3])]
        touch_names = [f"لمس سقف {k + 1}" for k in range(min(3, len(ht)))] + \
                      [f"لمس کف {k + 1}" for k in range(min(3, len(lt)))]
        vm = float(ctx.vip_avg(20)[i])
        vm = vm if np.isfinite(vm) and vm > 0 else float(np.mean(ctx.v[max(0, i - 20):i + 1]))
        vol_panel = _indicator_panel(
            ctx, i, "volume", "حجم (تأیید شکست الگو)",
            [(ctx.v, "حجم", color, False)],
            [(vm, AMBER, "میانگین حجم ۲۰")],
        ) if kind != "wedge" else {}
        out.append(Detection(s, i, float(ctx.c[i]), _overlay(
            lines=lines,
            rays=[_ray(ctx, hp1, hp2, "right", RED, "امتداد سقف الگو", not hit),
                  _ray(ctx, lp1, lp2, "right", GREEN, "امتداد کف الگو", not hit),
                  _ray(ctx, (i, float(ctx.c[i])), (far, target), "right", color,
                       "پرتو هدف شکست" if hit else "پرتو هدف (در صورت شکست)", not hit),
                  _ray(ctx, (pat_start, mid_a), (i, mid_b), "right", MUTED, "ادامه میدلاین", True)],
            levels=[_level(ctx, trigger, color, "خط شکست الگو"), _level(ctx, target, MUTED, "هدف الگو"),
                    _level(ctx, hv, RED, "سقف جاری"), _level(ctx, lv, GREEN, "کف جاری"),
                    _level(ctx, mid_b, MUTED, "میانه الگو"), *stops],
            zones=[_zone(ctx, hv, lv, pat_start, i, color, f"بدنه الگوی {kind}"),
                   _zone(ctx, trigger + a * 0.25, trigger - a * 0.25, i, far, color,
                         "ناحیه تأیید شکست")],
            points=_lpoints(ctx, [hp1, hp2, lp1, lp2] + touch_pts,
                            ["قله ۱", "قله ۲", "کف ۱", "کف ۲"] + touch_names),
            labels=[_label(ctx, i, float(ctx.c[i]), label_txt, color),
                    _label(ctx, i, trigger, f"عرض الگو {width / a:.1f} ATR · {conv}", AMBER),
                    _label(ctx, far, target, f"هدف {abs(target - trigger) / a:.1f} ATR", color)],
            fib=[_fib(ctx, (i, lv if direction == "up" else hv), (i, hv if direction == "up" else lv),
                      "اندازه‌گیری هدف الگو", [0.5, 1.0, 1.272, 1.618]),
                 _fib(ctx, (pat_start, mid_a), (i, mid_b), "عمق الگو نسبت به میدلاین",
                      [0.0, 0.382, 0.5, 0.618, 1.0])],
            panels=[x for x in [vol_panel] if x],
            marker=_marker(ctx, i, s.direction, kind),
            indicators=_indicators("volume", "ema:50", "adx:14", *ind),
        )))
        return out
    return out


def _pullback(ctx: Context, s: Strategy, p: Dict[str, str], start: int, end: int) -> List[Detection]:
    kind = _s(p, "kind", "support")
    direction = _s(p, "dir", "up")
    out: List[Detection] = []
    for i in range(max(start, 12), end + 1):
        a = ctx.av(i)
        if kind == "trendline":
            fit = ctx.line_value(i, "low" if direction == "up" else "high", "up" if direction == "up" else "down")
            if not fit:
                continue
            value, p1, p2, sl = fit
            near = ctx.l[i] <= value + a * 0.4 if direction == "up" else ctx.h[i] >= value - a * 0.4
            resume = ctx.c[i] > ctx.o[i] if direction == "up" else ctx.c[i] < ctx.o[i]
            far = _far_index(ctx, i, 20)
            stops, ind = _stop_levels(ctx, i)
            details = {
                "lines": [_line(ctx, p1, p2, GREEN if direction == "up" else RED, "خط روند پولبک")],
                "rays": [_ray(ctx, p1, p2, "right", GREEN if direction == "up" else RED, "امتداد خط روند"),
                         _ray(ctx, (i, value), (far, _project(value, sl, i, far, False)), "right", MUTED,
                              "مقدار خط در ادامه", True)],
                "zones": [_zone(ctx, value + a * 0.35, value - a * 0.35, p2[0], far,
                                GREEN if direction == "up" else RED, "ناحیه پولبک")],
                "points": _lpoints(ctx, [p1, p2], ["تکیه‌گاه ۱", "تکیه‌گاه ۲"]),
                "labels": [_label(ctx, i, float(ctx.c[i]), "پولبک به خط روند", GREEN if direction == "up" else RED),
                           _label(ctx, i, value, f"ADX {ctx.strength(i):.0f}", PURPLE)],
                "levels": [_level(ctx, value, GREEN if direction == "up" else RED, "پولبک به خط روند"), *stops],
                "indicators": _indicators("ema:20", "ema:50", "adx:14", *ind),
            }
            anchor = (p1, p2)
        else:
            levels = ctx.sr_levels(i)
            if not levels:
                continue
            target = None
            for lv in levels:
                if kind == "support" and lv["price"] <= ctx.c[i]:
                    target = lv
                    break
                if kind == "resistance" and lv["price"] >= ctx.c[i]:
                    target = lv
                    break
            if not target:
                continue
            price = target["price"]
            near = ctx.l[i] <= price + a * 0.3 and ctx.c[i] >= price - a * 0.05 if kind == "support" else ctx.h[i] >= price - a * 0.3 and ctx.c[i] <= price + a * 0.05
            resume = ctx.c[i] > ctx.o[i] if kind == "support" else ctx.c[i] < ctx.o[i]
            color = GREEN if kind == "support" else RED
            far = _far_index(ctx, i, 20)
            touch_idx = [int(x) for x in list(target["idx"])[-3:]]
            stops, ind = _stop_levels(ctx, i)
            details = {
                "levels": [_level(ctx, price, color, "پولبک"), _level(ctx, float(ctx.c[i]), MUTED, "قیمت فعلی"), *stops],
                "zones": [_zone(ctx, price + a * 0.3, price - a * 0.3, max(0, min(touch_idx) if touch_idx else i), far,
                                color, "ناحیه پولبک")],
                "rays": [_ray(ctx, (max(0, min(touch_idx) if touch_idx else i), price), (i, price), "right", color,
                             "تداوم سطح پولبک")],
                "points": _lpoints(ctx, [(x, price) for x in touch_idx],
                                   [f"برخورد {k + 1}" for k in range(len(touch_idx))]),
                "labels": [_label(ctx, i, float(ctx.c[i]), f"پولبک به {'حمایت' if kind == 'support' else 'مقاومت'}", color)],
                "indicators": _indicators("ema:20", "volume", "vwap", *ind),
            }
            win_a = max(0, i - 40)
            anchor = [(win_a, float(np.min(ctx.l[win_a:i + 1]))),
                      (win_a + int(np.argmax(ctx.h[win_a:i + 1])), float(np.max(ctx.h[win_a:i + 1])))]
        if near and resume:
            base = _overlay(marker=_marker(ctx, i, s.direction), fib=[
                _fib(ctx, anchor[0], anchor[1], "عمق پولبک روی موج", [0.382, 0.5, 0.618, 0.786, 1.0])], **details)
            out.append(Detection(s, i, float(ctx.c[i]), base))
            return out
    return out


def _rectangle(ctx: Context, s: Strategy, p: Dict[str, str], start: int, end: int) -> List[Detection]:
    side = _s(p, "side", "upper")
    direction = _s(p, "dir", "up")
    is_break = "break" in s.logic_key
    out: List[Detection] = []
    for i in range(max(start, 40), end + 1):
        highs = float(np.max(ctx.h[i - 40:i]))
        lows = float(np.min(ctx.l[i - 40:i]))
        a = ctx.av(i)
        if highs - lows > a * 3.2 or highs - lows <= 0:
            continue
        if is_break:
            if direction == "up":
                hit = ctx.c[i] > highs + a * 0.05 and ctx.c[i - 1] <= highs + a * 0.05
            else:
                hit = ctx.c[i] < lows - a * 0.05 and ctx.c[i - 1] >= lows - a * 0.05
            if not hit:
                continue
            color = GREEN if direction == "up" else RED
        else:
            target = highs if side == "upper" else lows
            hit = (ctx.h[i] >= highs - a * 0.15) if side == "upper" else (ctx.l[i] <= lows + a * 0.15)
            if not hit:
                continue
        # رنگ مرز مستطیل به سمتی است که لمس شده؛ رنگ شکست به جهت شکست.
        color = (GREEN if direction == "up" else RED) if is_break else (RED if side == "upper" else GREEN)
        marker_dir = ("LONG" if direction == "up" else "SHORT") if is_break else s.direction
        far = _far_index(ctx, i, 25)
        stops, ind = _stop_levels(ctx, i)
        height = highs - lows
        target_price = (highs + height) if (is_break and direction == "up") else \
            (lows - height) if is_break else (highs if side == "upper" else lows)
        out.append(Detection(s, i, float(ctx.c[i]), _overlay(
            lines=[_line(ctx, (i - 40, highs), (i, highs), RED, "سقف مستطیل"),
                   _line(ctx, (i - 40, lows), (i, lows), GREEN, "کف مستطیل"),
                   _line(ctx, (i - 40, (highs + lows) / 2.0), (i, (highs + lows) / 2.0), BLUE, "میدلاین مستطیل", True)],
            rays=[_ray(ctx, (i - 40, highs), (i, highs), "right", RED, "امتداد سقف مستطیل", not is_break),
                  _ray(ctx, (i - 40, lows), (i, lows), "right", GREEN, "امتداد کف مستطیل", not is_break),
                  _ray(ctx, (i, float(ctx.c[i])), (far, target_price), "right", color,
                       "هدف شکست مستطیل" if is_break else "هدف میدلاین")],
            levels=[_level(ctx, highs, RED, "سقف مستطیل"), _level(ctx, lows, GREEN, "کف مستطیل"),
                    _level(ctx, (highs + lows) / 2.0, BLUE, "میدلاین مستطیل"),
                    _level(ctx, target_price, MUTED, "هدف مستطیل"), *stops],
            zones=[_zone(ctx, highs, lows, i - 40, far, MUTED, "ناحیه مستطیل")],
            points=_lpoints(ctx, [(i - 40, highs), (i - 40, lows), (i, float(ctx.c[i]))],
                            ["شروع سقف", "شروع کف", "قیمت فعلی"]),
            labels=[_label(ctx, i, float(ctx.c[i]),
                           ("شکست مستطیل به " + ("بالا" if direction == "up" else "پایین")) if is_break
                           else ("رسیدن به " + ("سقف" if side == "upper" else "کف") + " مستطیل"), color),
                    _label(ctx, i, lows, f"عرض رنج {height / a:.1f} ATR", AMBER)],
            fib=[_fib(ctx, (i, lows), (i, highs), "اندازه‌گیری هدف شکست", [1.0, 1.272, 1.618])],
            marker=_marker(ctx, i, marker_dir),
            indicators=_indicators("volume", "ema:20", *ind),
        )))
        return out
    return out


def _hunter(ctx: Context, s: Strategy, p: Dict[str, str], start: int, end: int) -> List[Detection]:
    buy = _s(p, "dir", "buy") == "buy"
    r = ctx.R(14)
    e20 = ctx.E(20)
    out: List[Detection] = []
    for i in range(max(start, 40), end + 1):
        trend_up = buy and r[i] > 50 and r[i - 3] < r[i] and ctx.c[i] > e20[i] and r[i - 1] < r[i]
        trend_dn = (not buy) and r[i] < 50 and r[i - 3] > r[i] and ctx.c[i] < e20[i] and r[i - 1] > r[i]
        if not (trend_up or trend_dn):
            continue
        color = GREEN if trend_up else RED
        far = _far_index(ctx, i, 18)
        panel = _indicator_panel(
            ctx, i, "rsi", "هانتر: روند RSI نسبت به ۵۰",
            [(r, "RSI 14", color, False)],
            [(50, BLUE, "خط روند ۵۰"), (float(r[i]), color, f"RSI {r[i]:.0f}"),
             (70, RED, "اشباع خرید"), (30, GREEN, "اشباع فروش")],
        )
        adx_arr, pdi, mdi = ctx.ADX(14)
        di_panel = _indicator_panel(
            ctx, i, "adx", "قدرت روند (ADX/DI)",
            [(adx_arr, "ADX", PURPLE, False), (pdi, "+DI", GREEN, True), (mdi, "-DI", RED, True)],
            [(float(adx_arr[i]), PURPLE, f"ADX {adx_arr[i]:.0f}"), (25, MUTED, "آستانه ۲۵")],
        )
        add = _overlay(
            levels=[_level(ctx, float(e20[i]), BLUE, "EMA 20 (مبنا هانتر)"),
                    _level(ctx, float(ctx.c[i]), color, "پولبک در روند RSI")],
            rays=[_ray(ctx, (i, float(e20[i])), (far, float(e20[i]) + (float(e20[i]) - float(e20[max(0, i - 10)]))),
                       "right", BLUE, "ادامه EMA 20", True),
                  _ray(ctx, (i, float(ctx.c[i])), (far, float(ctx.c[i])), "right", color, "ادامه حرکت", True)],
            zones=[_zone(ctx, float(e20[i]) + ctx.av(i) * 0.3, float(e20[i]) - ctx.av(i) * 0.3,
                         max(0, i - 15), far, color, "ناحیه پولبک به EMA 20")],
            points=_lpoints(ctx, [(i, float(ctx.c[i]))], ["پولبک هانتر"]),
            labels=[_label(ctx, i, float(ctx.c[i]),
                           f"RSI {r[i]:.0f} | ADX {adx_arr[i]:.0f} | {'روند صعودی' if trend_up else 'روند نزولی'}", color)],
            panels=[x for x in [panel, di_panel] if x],
            indicators=_indicators("rsi:14", "ema:20", "adx:14"),
        )
        out.append(Detection(s, i, float(ctx.c[i]), _marker_overlay(ctx, i, s.direction, "Hunter", add=add)))
        return out
    return out


def _abcd(ctx: Context, s: Strategy, p: Dict[str, str], start: int, end: int) -> List[Detection]:
    bullish = _s(p, "dir", "bull") == "bull"
    highs, lows = ctx.pivots(3)
    swings = sorted(highs + lows, key=lambda x: x[0])
    out: List[Detection] = []
    for i in range(max(start, 10), end + 1):
        conf = [x for x in swings if x[0] <= i - 3]
        if len(conf) < 4:
            continue
        A, B, C, D = conf[-4:]
        ab = abs(B[1] - A[1])
        bc = abs(C[1] - B[1])
        cd = abs(D[1] - C[1])
        if min(ab, bc, cd) <= 0:
            continue
        if not (_in_range(cd / ab, 0.75, 1.35, 0.2) and _in_range(bc / ab, 0.38, 0.9, 0.2)):
            continue
        if bullish and not (D[1] < C[1]):
            continue
        if not bullish and not (D[1] > C[1]):
            continue
        if i - D[0] > 8:
            continue
        color = GREEN if bullish else RED
        far = _far_index(ctx, i, 20)
        a = ctx.av(i)
        projection = float(D[1]) + (float(D[1]) - float(C[1]))
        out.append(Detection(s, i, float(ctx.c[i]), _overlay(
            lines=[_line(ctx, A, B, color, "A-B"), _line(ctx, B, C, color, "B-C"), _line(ctx, C, D, color, "C-D"),
                   _line(ctx, A, D, MUTED, "خط A-D", True)],
            rays=[_ray(ctx, C, D, "right", color, "ادامه موج CD"),
                  _ray(ctx, (D[0], projection), (far, projection), "right", MUTED, "هدف ۱.۲۷۲", True)],
            levels=[_level(ctx, float(D[1]), color, "نقطه D (ورود)"),
                    _level(ctx, projection, MUTED, "هدف امتداد CD"),
                    _level(ctx, float((C[1] + D[1]) / 2.0), BLUE, "میانه موج CD")],
            zones=[_zone(ctx, max(float(C[1]), float(D[1])) + a * 0.2, min(float(C[1]), float(D[1])) - a * 0.2,
                         C[0], far, color, "ناحیه تکمیل الگو")],
            points=_lpoints(ctx, [A, B, C, D], ["A", "B", "C", "D"]),
            labels=[_label(ctx, B[0], float(B[1]), f"BC/AB {bc / ab:.2f}", BLUE),
                    _label(ctx, D[0], float(D[1]), f"CD/AB {cd / ab:.2f}", color)],
            fib=[_fib(ctx, A, B, "AB → بازگشت BC", [0.382, 0.5, 0.618, 0.786, 1.0]),
                 _fib(ctx, B, C, "BC → امتداد CD", [0.618, 1.0, 1.272, 1.618])],
            marker=_marker(ctx, i, s.direction, "AB=CD"),
            indicators=_indicators("ema:50", "volume"),
        )))
        return out
    return out


def _generic(ctx: Context, s: Strategy, p: Dict[str, str], start: int, end: int) -> List[Detection]:
    direction = _s(p, "dir", "up")
    out: List[Detection] = []
    e20, e50 = ctx.E(20), ctx.E(50)
    for i in range(max(start, 55), end + 1):
        up = e20[i - 1] <= e50[i - 1] and e20[i] > e50[i]
        down = e20[i - 1] >= e50[i - 1] and e20[i] < e50[i]
        if (direction == "up" and up) or (direction == "down" and down):
            color = GREEN if direction == "up" else RED
            a0 = max(0, i - 30)
            far = _far_index(ctx, i, 25)
            s20 = (float(e20[i]) - float(e20[a0])) / max(1, i - a0)
            s50 = (float(e50[i]) - float(e50[a0])) / max(1, i - a0)
            stops, ind = _stop_levels(ctx, i)
            add = _overlay(
                lines=[_line(ctx, (a0, e20[a0]), (i, e20[i]), BLUE, "EMA 20"),
                       _line(ctx, (a0, e50[a0]), (i, e50[i]), AMBER, "EMA 50")],
                rays=[_ray(ctx, (a0, e20[a0]), (i, e20[i]), "right", BLUE, "ادامه EMA 20"),
                      _ray(ctx, (a0, e50[a0]), (i, e50[i]), "right", AMBER, "ادامه EMA 50")],
                levels=[_level(ctx, float(e20[i]), BLUE, "EMA 20"), _level(ctx, float(e50[i]), AMBER, "EMA 50"), *stops],
                zones=[_zone(ctx, max(float(e20[i]), float(e50[i])), min(float(e20[i]), float(e50[i])),
                            a0, far, color, "ناحیه بین میانگین‌ها")],
                points=_lpoints(ctx, [(i, float(e20[i])), (i, float(e50[i]))], ["تقاطع EMA 20", "EMA 50"]),
                labels=[_label(ctx, i, float(ctx.c[i]),
                               f"تقاطع 20/50 | ADX {ctx.strength(i):.0f}", color)],
                fib=[_fib(ctx, (a0, float(np.min(ctx.l[a0:i + 1]))), (i, float(np.max(ctx.h[a0:i + 1]))),
                          "هدف بر پایه موج اخیر", [0.5, 1.0, 1.272, 1.618])],
                indicators=_indicators("ema:20", "ema:50", "adx:14", *ind),
            )
            out.append(Detection(s, i, float(ctx.c[i]),
                                 _marker_overlay(ctx, i, s.direction, f"ADX {ctx.strength(i):.0f}", add=add)))
            return out
    return out


DETECTORS = {
    "rsi_cross": _rsi_cross,
    "rsi_ma_cross": _rsi_ma_cross,
    "macd_signal_cross": _macd_signal_cross,
    "macd_histogram": _macd_histogram,
    "macd_zero_cross": _macd_zero_cross,
    "price_ema_cross": _price_ema_cross,
    "ma_triple_cross": _ma_triple_cross,
    "cci_cross": _cci_cross,
    "stoch_cross": _stoch_cross,
    "bollinger_touch": _bollinger_touch,
    "volume_spike": _volume_spike,
    "rsi_divergence": _rsi_divergence,
    "macd_divergence": _macd_divergence,
    "triple_divergence": _triple_divergence,
    "harmonic": _harmonic,
    "candlestick": _candlestick,
    "ichimoku_cross": _ichimoku,
    "ichimoku_kijun_touch": _ichimoku,
    "ichimoku_cloud_break": _ichimoku,
    "ichimoku_future_cloud": _ichimoku,
    "ichimoku_lagging_exit": _ichimoku,
    "ichimoku_tenkan_kijun_range": _ichimoku,
    "andrews_median": _andrews,
    "andrews_break": _andrews,
    "andrews_touch": _andrews,
    "classic_pattern": _classic,
    "abcd_pattern": _abcd,
    "trendline_touch": _trendline_touch,
    "trendline_break": _trendline_break,
    "trendline_pullback": _trendline_pullback,
    "log_trendline_touch": _trendline_touch,
    "log_trendline_break": _trendline_break,
    "log_trendline_pullback": _trendline_pullback,
    "special_trendline_touch": _trendline_touch,
    "special_trendline_break": _trendline_break,
    "special_trendline_pullback": _trendline_pullback,
    "channel_touch": _channel_touch,
    "sr_touch": _sr_touch,
    "sr_break": _sr_break,
    "sr_range": _sr_range,
    "order_block": _order_block,
    "stop_hunt": _stop_hunt,
    "pullback": _pullback,
    "rectangle_boundary": _rectangle,
    "rectangle_break": _rectangle,
    "hunter": _hunter,
    "generic_trend": _generic,
}


def scan(candles: MarketCandles, strategies: Iterable[Strategy], depth: int = 0) -> List[Detection]:
    """اجرای استراتژی‌های انتخابی روی کندل‌ها؛ depth = 0 یعنی فقط کندل آخر، وگرنه N کندل آخر."""
    if len(candles) < 40:
        return []
    depth = max(0, min(int(depth or 0), 120))
    end = len(candles) - 1
    start = end if depth == 0 else max(35, end - depth)
    ctx = Context(candles)
    out: List[Detection] = []
    for s in strategies:
        fn = DETECTORS.get(s.logic_key)
        if fn is None:
            fn = _generic
        try:
            hits = fn(ctx, s, parse_params(s.logic_params), start, end)
        except Exception:
            hits = []
        for det in hits:
            det.overlay = _trim_overlay(s, det.overlay)
        out.extend(hits)
    out.sort(key=lambda d: d.index, reverse=True)
    return out
