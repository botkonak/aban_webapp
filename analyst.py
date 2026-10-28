"""
آنالیز ارز با هوش مصنوعی (آبان ترید).

کاربر / ادمین روی «آنالیز با AI» می‌زند، اسم ارز را می‌فرستد و یک گزارش کامل می‌گیرد:
قیمت، تغییر ۲۴ ساعته، تحلیل تکنیکال (روند، RSI، MACD، حجم، حمایت و مقاومت)، تحلیل متنی AI و پیشنهاد.
  - داده‌ی کندل از signal_engine فعلی گرفته می‌شود (تایم‌فریم 1h برای تحلیل و 4h برای روند بزرگ‌تر).
  - تحلیل متنی با ai_engine.ai.request (چرخش خودکار کلیدها)؛ اگر AI پاسخی ندهد، بخش «تحلیل هوش مصنوعی» حذف می‌شود
    و بقیه‌ی بخش‌ها نمایش داده می‌شوند.
  - قالب از پنل ادمین قابل ویرایش است (message_templates نوع analysis).
  - هر درخواست در جدول analysis_requests ذخیره می‌شود.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

import config
import live_price
import plans
import subscription
import templates
from ai_engine import SYSTEM_PROMPT, AICallError, ai, clean_info, parse_json_object
from database import db
from signal_engine import (
    Candles, adx as adx_f, atr as atr_f, engine, magnitude_decimals, rsi as rsi_f, sr_levels, supertrend_dir,
)
from ui import NO, Ctx, back_row, btn, home_btn, kb, now_jalali_parts, to_fa

log = logging.getLogger("analyst")

ANALYSIS_PLACEHOLDERS = {
    "symbol", "date", "time", "price", "change_24h", "trend", "rsi", "macd", "volume", "resistance", "support",
    "ai_analysis", "signal", "tp1", "tp2", "sl", "rr",
}
MAIN_TF, HTF = "1h", "4h"
# تحلیل جامع مولتی تایم‌فریم: (تایم‌فریم، عنوان فارسی)
MULTI_TFS = [("5m", "۵ دقیقه"), ("15m", "۱۵ دقیقه"), ("1h", "۱ ساعت"), ("4h", "۴ ساعت"), ("1d", "۱ روز")]
MULTI_MIN_TFS = 3                  # حداقل تایم‌فریمی که باید داده داشته باشند
MULTI_RISK_LINE = "⚠️ هشدار ریسک: این تحلیل صرفاً جنبه‌ی آموزشی دارد و توصیه‌ی مالی یا سرمایه‌گذاری نیست؛ همیشه با مدیریت سرمایه و ریسک وارد معامله شوید."

# اسم‌های فارسی / رایج -> نماد
ALIASES = {
    "بیتکوین": "BTC", "بیت کوین": "BTC", "اتریوم": "ETH", "ریپل": "XRP", "تتر": "USDT", "بایننس": "BNB",
    "سولانا": "SOL", "دوج": "DOGE", "دوجکوین": "DOGE", "کاردانو": "ADA", "ترون": "TRX", "استلار": "XLM",
    "لایت کوین": "LTC", "لایتکوین": "LTC", "مونرو": "XMR", "زیکش": "ZEC", "تون": "TON", "گرام": "GRAM",
    "بیت کوین کش": "BCH", "شیبا": "SHIB", "پپه": "PEPE",
}
_ICONS = {"LONG": "🟢", "SHORT": "🔴", "NEUTRAL": "⚪"}
_last_request: Dict[int, float] = {}
_busy: set = set()


class AnalysisError(Exception):
    """خطایی که پیامش مستقیم به کاربر نشان داده می‌شود."""


# ----------------------------------------------------------------------------
# نماد
# ----------------------------------------------------------------------------
def _clean_symbol(raw: str) -> str:
    s = (raw or "").strip()
    s = ALIASES.get(s, ALIASES.get(re.sub(r"\s+", " ", s), s))
    s = s.upper().replace("$", "").replace("#", "")
    s = re.sub(r"[\s_\-]+", "", s)
    for suf in ("/USDT:USDT", "/USDT", ":USDT", "USDT", "PERP"):
        if s.endswith(suf) and len(s) > len(suf):
            s = s[: -len(suf)]
            break
    return s.strip("/:")


def resolve_pair(raw: str) -> Optional[str]:
    """اسم ارز کاربر (BTC، btc/usdt، BTCUSDT، بیتکوین ...) -> نماد فیوچرز صرافی (مثل BTC/USDT:USDT)."""
    base = _clean_symbol(raw)
    if not base or not re.fullmatch(r"[A-Z0-9]{1,20}", base):
        return None
    cands = [base]
    if base == "TON":
        cands.append("GRAM")
    elif base == "GRAM":
        cands.append("TON")
    for b in cands:
        for pair in engine.symbols:
            if (engine.bases.get(pair) or pair.split("/")[0]).upper() == b:
                return pair
    return None


# ----------------------------------------------------------------------------
# اندیکاتورها و تحلیل
# ----------------------------------------------------------------------------
def _ema(x: np.ndarray, n: int) -> np.ndarray:
    return pd.Series(x).ewm(span=n, adjust=False).mean().to_numpy()


def macd(c: np.ndarray) -> Optional[Tuple[float, float, float, float]]:
    """(MACD، سیگنال، هیستوگرام، هیستوگرام چند کندل قبل) — اگر داده کم باشد None."""
    if len(c) < 40:
        return None
    line = _ema(c, 12) - _ema(c, 26)
    sig = _ema(line, 9)
    hist = line - sig
    return float(line[-1]), float(sig[-1]), float(hist[-1]), float(hist[-4])


def _fnum(x: float, dec: int = 0) -> str:
    return to_fa(f"{x:,.{dec}f}")


def _usd(x: Optional[float], dec: int) -> str:
    return "---" if x is None else "$" + _fnum(x, dec)


def _rsi_text(r: Optional[float]) -> str:
    if r is None or not np.isfinite(r):
        return ""
    zone = (
        "اشباع خرید" if r >= 70 else "ناحیه خرید" if r >= 55 else "خنثی" if r >= 45
        else "ناحیه فروش" if r > 30 else "اشباع فروش"
    )
    return f"{to_fa(f'{r:.0f}')} ({zone})"


def _macd_text(m: Optional[Tuple[float, float, float, float]]) -> str:
    if m is None:
        return ""
    line, sig, hist, hist_prev = m
    if hist > 0 >= hist_prev:
        return "تقاطع صعودی"
    if hist < 0 <= hist_prev:
        return "تقاطع نزولی"
    return "صعودی (بالای خط سیگنال)" if line > sig else "نزولی (زیر خط سیگنال)"


def _volume_text(v: np.ndarray) -> str:
    if len(v) < 21:
        return ""
    avg = float(np.mean(v[-21:-1]))
    if avg <= 0:
        return ""
    ratio = float(v[-1]) / avg
    return "بالاتر از میانگین" if ratio >= 1.2 else "پایین‌تر از میانگین" if ratio <= 0.8 else "نزدیک به میانگین"


def technicals(pc: Candles, cc: Optional[Candles], price: float) -> Dict[str, Any]:
    """همه‌ی اندیکاتورها و برآیند جهت (تابع خالص؛ بدون شبکه)."""
    c, h, l, v = pc.c, pc.h, pc.l, pc.v
    n = len(c)
    ema50 = float(_ema(c, 50)[-1])
    ema200 = float(_ema(c, 200)[-1]) if n >= 200 else None
    st = int(supertrend_dir(h, l, c, 10, 3.0)[-1])
    rsi_v = float(rsi_f(c, 14)[-1])
    m = macd(c)
    atr_v = float(atr_f(h, l, c, 14)[-1])
    adx_v = float(adx_f(h, l, c, 14)[0][-1])
    htf = 0
    if cc is not None and len(cc) >= 60:
        htf = int(supertrend_dir(cc.h, cc.l, cc.c, 10, 3.0)[-1])

    score = 0
    ema_dir = 0
    if ema200 is not None:
        ema_dir = 1 if ema50 > ema200 else -1
        score += 2 * ema_dir
    score += 2 * st
    score += htf
    if m is not None:
        score += 1 if m[2] > 0 else -1
    score += 1 if rsi_v > 55 else (-1 if rsi_v < 45 else 0)
    score += 1 if price > ema50 else -1
    if score >= 3 and rsi_v <= 75:
        direction = "LONG"
    elif score <= -3 and rsi_v >= 25:
        direction = "SHORT"
    else:
        direction = "NEUTRAL"

    if st == 1 and ema_dir >= 0 and (ema200 is not None or price > ema50):
        trend = "صعودی 📈"
    elif st == -1 and ema_dir <= 0 and (ema200 is not None or price < ema50):
        trend = "نزولی 📉"
    else:
        trend = "خنثی ➖"

    levels = sr_levels(h, l, 3, min(200, n - 1), 6, 0.3, 8)
    above = sorted(p for p, _k in levels if p > price)
    below = sorted((p for p, _k in levels if p < price), reverse=True)
    res = above[0] if above else (float(np.max(h[-50:])) if float(np.max(h[-50:])) > price else None)
    sup = below[0] if below else (float(np.min(l[-50:])) if float(np.min(l[-50:])) < price else None)
    return {
        "trend": trend, "rsi": rsi_v, "macd": m, "adx": adx_v, "atr": atr_v, "volume": _volume_text(v),
        "resistance": res, "support": sup, "direction": direction, "score": score,
    }


def proposal(direction: str, price: float, atr_v: float, d=None) -> Dict[str, Optional[float]]:
    """هدف اول / هدف دوم / حد ضرر بر اساس ضرایب ATR تنظیمات ربات؛ برای حالت خنثی همه None."""
    if direction not in ("LONG", "SHORT") or atr_v <= 0:
        return {"tp1": None, "tp2": None, "sl": None, "rr": None}
    src = db if d is None else d
    sl_m = src.get_e_float("sl_mult", direction) or 1.5
    t1 = src.get_e_float("tp1_mult", direction) or 1.0
    t2 = src.get_e_float("tp2_mult", direction) or 2.0
    sign = 1 if direction == "LONG" else -1
    sl = price - sign * sl_m * atr_v
    tp1 = price + sign * t1 * atr_v
    tp2 = price + sign * t2 * atr_v
    if sl <= 0:
        return {"tp1": None, "tp2": None, "sl": None, "rr": None}
    risk = abs(price - sl)
    return {"tp1": tp1, "tp2": tp2, "sl": sl, "rr": abs(tp2 - price) / risk if risk > 0 else None}


def _decimals(pair: str, price: float) -> int:
    try:
        d = engine._price_decimals(pair, price)
        return min(d, 8)
    except Exception:
        return magnitude_decimals(price)


# ----------------------------------------------------------------------------
# هوش مصنوعی
# ----------------------------------------------------------------------------
def _ai_usable() -> bool:
    return ai.master_on() and not ai.temporarily_off() and bool(ai.available_keys())


async def ai_commentary(
    symbol: str, price_txt: str, change_txt: str, t: Dict[str, Any], prop_txt: str, model: str = "",
) -> str:
    """تحلیل کوتاه + هشدار ریسک؛ هر مشکلی (خاموش بودن، خطا، پاسخ ناقص) یعنی رشته‌ی خالی (بخش حذف می‌شود)."""
    if not _ai_usable():
        return ""
    sig = {"LONG": "خرید", "SHORT": "فروش", "NEUTRAL": "بدون سیگنال"}[t["direction"]]
    adx_txt = to_fa("%.0f" % t["adx"])
    user = (
        f"نماد: {symbol} (فیوچرز USDT)\n"
        f"قیمت فعلی: {price_txt} | تغییر ۲۴ ساعته: {change_txt}\n"
        f"روند کلی: {t['trend']} | RSI: {_rsi_text(t['rsi'])} | MACD: {_macd_text(t['macd']) or '---'} | "
        f"ADX: {adx_txt} | حجم: {t['volume'] or '---'}\n"
        f"مقاومت نزدیک: {t['resistance'] if t['resistance'] is not None else '---'} | "
        f"حمایت نزدیک: {t['support'] if t['support'] is not None else '---'}\n"
        f"برآیند اندیکاتورها: {sig}\n{prop_txt}\n\n"
        "بر اساس همین داده‌ها فقط یک آبجکت JSON با این دو کلید برگردانید:\n"
        '"analysis": تحلیل کوتاه فارسی (۲ تا ۳ جمله، حداکثر ۳۵۰ کاراکتر) از وضعیت و حرکت محتمل قیمت\n'
        '"risk": هشدار ریسک کوتاه فارسی (یک جمله، حداکثر ۱۶۰ کاراکتر) مثل «در صورت شکست حمایت ... احتمال اصلاح تا ... وجود دارد»\n'
        "بدون سلام، مقدمه، ایموجی و مارک‌داون."
    )
    model = (model or db.get("ai_model") or config.AI_MODELS[0]).strip()
    try:
        raw = await asyncio.wait_for(ai.request(SYSTEM_PROMPT, user, 500, model), timeout=config.AI_TIMEOUT * 2.5)
    except (AICallError, asyncio.TimeoutError) as e:
        log.info("analysis AI skipped: %s", getattr(e, "msg", e))
        return ""
    except Exception:
        log.exception("analysis AI crashed")
        return ""
    data = parse_json_object(raw)
    if data is None:
        return ""
    analysis = clean_info(data.get("analysis", ""), 380)
    if not analysis:
        return ""
    risk = clean_info(data.get("risk", ""), 180)
    return analysis + (f"\n\n⚠️ ریسک: {risk}" if risk else "")


# ----------------------------------------------------------------------------
# تابع اصلی
# ----------------------------------------------------------------------------
async def _resolve_or_raise(symbol: str) -> str:
    if not engine.symbols:
        try:
            await engine.get_symbols()
        except Exception:
            pass
    if not engine.symbols:
        raise AnalysisError("ارتباط با صرافی برقرار نیست؛ چند لحظه‌ی دیگر دوباره امتحان کنید.")
    pair = resolve_pair(symbol)
    if pair is None:
        raise AnalysisError("این نماد در بازار فیوچرز پیدا نشد. فقط اسم ارز را ارسال کنید؛ مثلاً BTC یا ETH.")
    return pair


async def analyze_symbol(symbol: str, uid: int = 0) -> str:
    """تحلیل کامل یک ارز؛ خروجی: متن آماده‌ی ارسال (HTML تلگرام). خطاهای قابل‌نمایش: AnalysisError."""
    pair = await _resolve_or_raise(symbol)
    pc, cc = await asyncio.gather(engine._get_candles(pair, MAIN_TF), engine._get_candles(pair, HTF))
    if pc is None or len(pc) < 60:
        raise AnalysisError("داده‌ی کافی برای تحلیل این نماد گرفته نشد؛ کمی بعد دوباره امتحان کنید.")
    tick = (await live_price.get_tickers([pair])).get(pair)
    price = tick[0] if tick else float(pc.c[-1])
    pct = tick[1] if tick else None
    t = technicals(pc, cc, price)
    src = db.scope(uid)
    prop = proposal(t["direction"], price, t["atr"], src)
    dec = _decimals(pair, price)
    base = (engine.bases.get(pair) or pair.split("/")[0])

    price_txt = _usd(price, dec)
    change_txt = "---" if pct is None else to_fa(f"{pct:+.2f}") + "٪"
    prop_txt = (
        "پیشنهاد الگوریتمی: " + {"LONG": "خرید", "SHORT": "فروش", "NEUTRAL": "بدون سیگنال"}[t["direction"]]
        + (
            f" | هدف اول {prop['tp1']:.{dec}f} | هدف دوم {prop['tp2']:.{dec}f} | حد ضرر {prop['sl']:.{dec}f}"
            if prop["tp1"] is not None else ""
        )
    )
    ai_text = await ai_commentary(
        f"{base}/USDT", f"{price:.{dec}f}", change_txt, t, prop_txt, src.get("ai_model"),
    )
    date, tm = now_jalali_parts()
    direction = t["direction"]
    ctx = {
        "symbol": f"{base}/USDT",
        "date": date,
        "time": tm,
        "price": price_txt,
        "change_24h": change_txt,
        "trend": t["trend"],
        "rsi": _rsi_text(t["rsi"]),
        "macd": _macd_text(t["macd"]),
        "volume": t["volume"],
        "resistance": _usd(t["resistance"], dec) if t["resistance"] is not None else "",
        "support": _usd(t["support"], dec) if t["support"] is not None else "",
        "ai_analysis": ai_text,
        "signal": {"LONG": "خرید (LONG)", "SHORT": "فروش (SHORT)", "NEUTRAL": "بدون سیگنال"}[direction],
        "tp1": _usd(prop["tp1"], dec) if prop["tp1"] is not None else "",
        "tp2": _usd(prop["tp2"], dec) if prop["tp2"] is not None else "",
        "sl": _usd(prop["sl"], dec) if prop["sl"] is not None else "",
        "rr": ("۱:" + to_fa(f"{prop['rr']:.1f}")) if prop["rr"] else "",
    }
    return templates.render_template(
        _swap_signal_icon(db.get_msg_template("analysis"), direction), ctx, drop_if_missing=("ai_analysis",),
    )


# ----------------------------------------------------------------------------
# تحلیل جامع مولتی تایم‌فریم
# ----------------------------------------------------------------------------
_TREND_FA = {1: "صعودی", -1: "نزولی", 0: "خنثی"}
_TREND_ICON = {1: "🟢", -1: "🔴", 0: "⚪"}


def tf_snapshot(c: Candles) -> Optional[Dict[str, Any]]:
    """
    خلاصه‌ی یک تایم‌فریم (تابع خالص): روند (Supertrend + EMA)، مومنتوم (RSI) و نوسان (ATR%).
    trend: +1 صعودی / -1 نزولی / 0 خنثی. اگر داده کم باشد None.
    """
    n = len(c)
    if n < 60:
        return None
    cl, h, l = c.c, c.h, c.l
    st = int(supertrend_dir(h, l, cl, 10, 3.0)[-1])
    ema50 = float(_ema(cl, 50)[-1])
    ema200 = float(_ema(cl, 200)[-1]) if n >= 200 else None
    ema_dir = 0 if ema200 is None else (1 if ema50 > ema200 else -1 if ema50 < ema200 else 0)
    rsi_v = float(rsi_f(cl, 14)[-1])
    last = float(cl[-1])
    atr_pct = float(atr_f(h, l, cl, 14)[-1]) / last * 100.0 if last > 0 else float("nan")
    if not (np.isfinite(rsi_v) and np.isfinite(atr_pct)):
        return None
    if st == 1 and ema_dir >= 0:
        trend = 1
    elif st == -1 and ema_dir <= 0:
        trend = -1
    else:
        trend = 0
    return {"trend": trend, "st": st, "ema_dir": ema_dir, "rsi": rsi_v, "atr_pct": atr_pct}


def multi_confluence(snaps: Dict[str, Optional[Dict[str, Any]]]) -> Tuple[str, str]:
    """
    بررسی هم‌راستایی تایم‌فریم‌ها. خروجی: (کد، متن فارسی)
    کد: bull_all | bear_all | bull | bear | conflict | neutral | none
    """
    dirs = {tf: s["trend"] for tf, s in snaps.items() if s}
    total = len(dirs)
    if total == 0:
        return "none", "داده‌ای برای بررسی هم‌راستایی وجود ندارد."
    bull = sum(1 for d in dirs.values() if d == 1)
    bear = sum(1 for d in dirs.values() if d == -1)
    neu = total - bull - bear
    if bull == total:
        code, txt = "bull_all", f"هم‌راستایی کامل صعودی: هر {to_fa(total)} تایم‌فریم هم‌جهت و صعودی‌اند."
    elif bear == total:
        code, txt = "bear_all", f"هم‌راستایی کامل نزولی: هر {to_fa(total)} تایم‌فریم هم‌جهت و نزولی‌اند."
    elif bull and bear:
        code, txt = "conflict", (
            f"تناقض بین تایم‌فریم‌ها: {to_fa(bull)} صعودی، {to_fa(bear)} نزولی و {to_fa(neu)} خنثی؛ بازار جهت یکسانی ندارد."
        )
    elif bull:
        code, txt = "bull", f"گرایش صعودی: {to_fa(bull)} از {to_fa(total)} تایم‌فریم صعودی و بقیه خنثی‌اند، تناقضی دیده نمی‌شود."
    elif bear:
        code, txt = "bear", f"گرایش نزولی: {to_fa(bear)} از {to_fa(total)} تایم‌فریم نزولی و بقیه خنثی‌اند، تناقضی دیده نمی‌شود."
    else:
        code, txt = "neutral", "همه‌ی تایم‌فریم‌ها خنثی‌اند و روند مشخصی وجود ندارد."
    hi = sum(dirs.get(tf, 0) for tf in ("4h", "1d"))
    lo = sum(dirs.get(tf, 0) for tf in ("5m", "15m"))
    if hi and lo and (hi > 0) != (lo > 0):
        side = "صعودی" if hi > 0 else "نزولی"
        txt += f"\nروند تایم‌فریم‌های بالا {side} است اما تایم‌فریم‌های کوتاه‌مدت خلاف آن‌اند (احتمال اصلاح یا برگشت کوتاه‌مدت)."
    for tf, label in MULTI_TFS:
        s = snaps.get(tf)
        if not s:
            continue
        if s["rsi"] >= 70:
            txt += f"\nاشباع خرید در تایم‌فریم {label} (RSI {to_fa('%.0f' % s['rsi'])})."
        elif s["rsi"] <= 30:
            txt += f"\nاشباع فروش در تایم‌فریم {label} (RSI {to_fa('%.0f' % s['rsi'])})."
    return code, txt


def _multi_line(label: str, s: Optional[Dict[str, Any]]) -> str:
    if not s:
        return f"⚪ {label}: داده ناکافی"
    return (
        f"{_TREND_ICON[s['trend']]} {label}: {_TREND_FA[s['trend']]} | RSI {to_fa('%.0f' % s['rsi'])} | "
        f"ATR {to_fa('%.2f' % s['atr_pct'])}٪"
    )


def build_multi_report(
    base: str, price_txt: str, change_txt: str, snaps: Dict[str, Optional[Dict[str, Any]]], conf_text: str,
    ai_text: str, date: str, tm: str,
) -> str:
    """گزارش نهایی (HTML تلگرام)."""
    from ui import esc

    lines = [
        "🧭 تحلیل جامع مولتی تایم‌فریم",
        "━━━━━━━━━━━━━━━━━━━━",
        f"🪙 نماد: {esc(base)}/USDT",
        f"💰 قیمت فعلی: {esc(price_txt)} ({esc(change_txt)})",
        f"📅 {date} - {tm}",
        "",
        "📊 وضعیت تایم‌فریم‌ها:",
    ]
    lines += [esc(_multi_line(label, snaps.get(tf))) for tf, label in MULTI_TFS]
    lines += ["", "🔗 تحلیل هم‌راستایی:", esc(conf_text)]
    if ai_text:
        lines += ["", "🧠 جمع‌بندی هوش مصنوعی:", esc(ai_text)]
    lines += ["", "━━━━━━━━━━━━━━━━━━━━", esc(MULTI_RISK_LINE)]
    return "\n".join(lines)


async def ai_multi_commentary(
    symbol: str, price_txt: str, change_txt: str, snaps: Dict[str, Optional[Dict[str, Any]]], conf_text: str,
    model: str = "",
) -> str:
    """جمع‌بندی AI از هم‌راستایی ۵ تایم‌فریم؛ هر مشکلی یعنی رشته‌ی خالی (بخش حذف می‌شود)."""
    if not _ai_usable():
        return ""
    rows = []
    for tf, label in MULTI_TFS:
        s = snaps.get(tf)
        rows.append(
            f"{tf} ({label}): روند {_TREND_FA[s['trend']]} | RSI {s['rsi']:.0f} | ATR {s['atr_pct']:.2f}%"
            if s else f"{tf} ({label}): داده ناکافی"
        )
    user = (
        f"نماد: {symbol} (فیوچرز USDT)\nقیمت فعلی: {price_txt} | تغییر ۲۴ ساعته: {change_txt}\n"
        + "\n".join(rows)
        + f"\nبررسی الگوریتمی هم‌راستایی: {conf_text.replace(chr(10), ' ')}\n\n"
        "بر اساس همین داده‌ها فقط یک آبجکت JSON با یک کلید برگردانید:\n"
        '"analysis": جمع‌بندی کوتاه فارسی (۳ تا ۴ جمله، حداکثر ۵۵۰ کاراکتر) شامل: آیا تایم‌فریم‌ها هم‌راستا هستند یا تناقض دارند، '
        "بهترین سناریوی محتمل و شرایط مناسب برای ورود (مثلاً صبر برای اصلاح تا حمایت یا تأیید شکست)\n"
        "بدون سلام، مقدمه، ایموجی و مارک‌داون. سود تضمین نکنید."
    )
    model = (model or db.get("ai_model") or config.AI_MODELS[0]).strip()
    try:
        raw = await asyncio.wait_for(ai.request(SYSTEM_PROMPT, user, 600, model), timeout=config.AI_TIMEOUT * 2.5)
    except (AICallError, asyncio.TimeoutError) as e:
        log.info("multi analysis AI skipped: %s", getattr(e, "msg", e))
        return ""
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception("multi analysis AI crashed")
        return ""
    data = parse_json_object(raw)
    if data is None:
        return ""
    return clean_info(data.get("analysis", ""), 600)


async def analyze_multi(symbol: str, uid: int = 0) -> str:
    """تحلیل جامع ۵ تایم‌فریمی؛ خروجی: گزارش HTML تلگرام. خطاهای قابل‌نمایش: AnalysisError."""
    pair = await _resolve_or_raise(symbol)
    res = await asyncio.gather(*(engine._get_candles(pair, tf) for tf, _l in MULTI_TFS), return_exceptions=True)
    candles = {tf: (r if isinstance(r, Candles) else None) for (tf, _l), r in zip(MULTI_TFS, res)}
    snaps: Dict[str, Optional[Dict[str, Any]]] = {}
    for tf, _l in MULTI_TFS:
        c = candles[tf]
        try:
            snaps[tf] = tf_snapshot(c) if c is not None else None
        except Exception:
            log.exception("tf snapshot failed for %s %s", pair, tf)
            snaps[tf] = None
    if sum(1 for s in snaps.values() if s) < MULTI_MIN_TFS:
        raise AnalysisError("داده‌ی کافی برای تحلیل چند تایم‌فریمی این نماد گرفته نشد؛ کمی بعد دوباره امتحان کنید.")
    tick = (await live_price.get_tickers([pair])).get(pair)
    ref = next((candles[tf] for tf, _l in reversed(MULTI_TFS) if candles[tf] is not None), None)
    price = tick[0] if tick else (float(ref.c[-1]) if ref is not None else 0.0)
    if price <= 0:
        raise AnalysisError("قیمت لحظه‌ای این نماد گرفته نشد؛ کمی بعد دوباره امتحان کنید.")
    pct = tick[1] if tick else None
    dec = _decimals(pair, price)
    base = engine.bases.get(pair) or pair.split("/")[0]
    price_txt = _usd(price, dec)
    change_txt = "---" if pct is None else to_fa(f"{pct:+.2f}") + "٪"
    _code, conf_text = multi_confluence(snaps)
    src = db.scope(uid)
    ai_text = await ai_multi_commentary(
        f"{base}/USDT", f"{price:.{dec}f}", change_txt, snaps, conf_text, src.get("ai_model"),
    )
    date, tm = now_jalali_parts()
    return build_multi_report(base, price_txt, change_txt, snaps, conf_text, ai_text, date, tm)


def _swap_signal_icon(template_text: str, direction: str) -> str:
    """🟢 / 🔴 / ⚪ خطی که {signal} دارد بر اساس جهت عوض می‌شود."""
    icon = _ICONS.get(direction, "⚪")
    out = []
    for line in template_text.split("\n"):
        if "{signal}" in line.lower():
            for ic in _ICONS.values():
                line = line.replace(ic, icon)
        out.append(line)
    return "\n".join(out)


def save_request(uid: int, symbol: str, text: str, kind: str = "single") -> None:
    """kind: single (تک‌تایم‌فریم) | multi (مولتی تایم‌فریم)."""
    try:
        db.x(
            "INSERT INTO analysis_requests(user_id, symbol, result_text, kind) VALUES(?, ?, ?, ?)",
            (uid, symbol[:40], re.sub(r"<[^>]+>", "", text)[:4000], kind),
        )
    except Exception:
        log.exception("saving analysis request failed")


# ----------------------------------------------------------------------------
# جریان ربات (کاربر و ادمین)
# ----------------------------------------------------------------------------
ASK_TEXT = "نام ارزی را که می‌خواهید تحلیل شود ارسال کنید.\nمثال: BTC یا ETH یا SOL"
ASK_MULTI_TEXT = (
    "تحلیل جامع (مولتی تایم‌فریم): نام ارزی را که می‌خواهید در ۵ تایم‌فریم (۵ دقیقه، ۱۵ دقیقه، ۱ ساعت، ۴ ساعت، ۱ روز) "
    "بررسی شود ارسال کنید.\nمثال: BTC یا ETH یا SOL"
)
BUSY_TEXT = "در حال تحلیل ارز، چند لحظه صبر کنید..."
BUSY_MULTI_TEXT = "در حال تحلیل ارز در ۵ تایم‌فریم، چند لحظه صبر کنید..."
NEED_SUB_ANALYSIS = (
    "برای استفاده از آنالیز با هوش مصنوعی باید اشتراک فعال داشته باشید.\n"
    "با دکمه‌ی زیر اشتراک تهیه کنید."
)
NEED_SUB_MULTI = (
    "برای استفاده از تحلیل جامع (مولتی تایم‌فریم) باید اشتراک فعال داشته باشید.\n"
    "با دکمه‌ی زیر اشتراک تهیه کنید."
)
KINDS = ("analyze", "analyze_multi")


def _is_admin(uid: int) -> bool:
    return uid == config.ADMIN_UID


def _multi_allowed(uid: int) -> bool:
    """تحلیل جامع فقط برای ادمین و کاربرانی که امکان «تحلیل چند تایم‌فریمی» دارند."""
    return _is_admin(uid) or plans.bot_feature_allowed(uid, "multi")


def _pfx(uid: int) -> str:
    return "a" if _is_admin(uid) else "u"


def _result_markup(uid: int, multi: bool = False):
    p = _pfx(uid)
    rows = [[btn("تحلیل جامع ارز دیگر" if multi else "آنالیز ارز دیگر", f"{p}:{'anam2' if multi else 'ana2'}")]]
    if _multi_allowed(uid):
        rows.append([btn("آنالیز ساده" if multi else "تحلیل جامع (مولتی تایم‌فریم)", f"{p}:{'ana2' if multi else 'anam2'}")])
    rows.append([home_btn(uid)] if _is_admin(uid) else back_row("u:menu"))
    return kb(*rows)


async def start(ctx: Ctx, new_message: bool = False) -> None:
    """دکمه‌ی «آنالیز با AI»: اسم ارز پرسیده می‌شود (از روی نتیجه‌ی تحلیل، در پیام جدید تا نتیجه‌ی قبلی بماند)."""
    uid = ctx.uid
    show = ctx.send if new_message else ctx.show
    back = "a:hm" if _is_admin(uid) else "u:menu"
    if not _is_admin(uid):
        if not engine.bot_on():
            await ctx.answer("ربات در حال حاضر خاموش است.")
            return
        if not plans.bot_feature_allowed(uid, "analysis"):
            await show(*subscription.need_sub_page(uid, NEED_SUB_ANALYSIS))
            return
    ctx.user_data["state"] = {"kind": "analyze", "back": back}
    rows = []
    if _multi_allowed(uid):
        rows.append([btn("تحلیل جامع (مولتی تایم‌فریم)", f"{_pfx(uid)}:anam")])
    rows.append(back_row(back))
    await show(ASK_TEXT, kb(*rows), parse_mode=None)


async def start_multi(ctx: Ctx, new_message: bool = False) -> None:
    """دکمه‌ی «تحلیل جامع (مولتی تایم‌فریم)»: فقط ادمین و کاربران دارای اشتراک فعال."""
    uid = ctx.uid
    show = ctx.send if new_message else ctx.show
    back = "a:hm" if _is_admin(uid) else "u:menu"
    if not _is_admin(uid):
        if not engine.bot_on():
            await ctx.answer("ربات در حال حاضر خاموش است.")
            return
        if not plans.bot_feature_allowed(uid, "multi"):
            ctx.user_data.pop("state", None)
            await show(*subscription.need_sub_page(uid, NEED_SUB_MULTI))
            return
    ctx.user_data["state"] = {"kind": "analyze_multi", "back": back}
    await show(
        ASK_MULTI_TEXT,
        kb([btn("آنالیز ساده (تک‌تایم‌فریم)", f"{_pfx(uid)}:ana")], back_row(back)),
        parse_mode=None,
    )


async def run(ctx: Ctx, raw: str) -> None:
    """اسم ارز دریافت شد: تحلیل انجام می‌شود و نتیجه نمایش داده می‌شود."""
    uid = ctx.uid
    st = ctx.user_data.get("state") or {}
    multi = st.get("kind") == "analyze_multi"
    back = st.get("back") or ("a:hm" if _is_admin(uid) else "u:menu")
    raw = (raw or "").strip()
    if not raw or len(raw) > 40:
        await ctx.send(f"{NO} اسم ارز معتبر نیست. مثال: BTC", kb(back_row(back)), parse_mode=None)
        return
    if multi and not _multi_allowed(uid):
        ctx.user_data.pop("state", None)
        await ctx.send(*subscription.need_sub_page(uid, NEED_SUB_MULTI))
        return
    if not _is_admin(uid):
        if not engine.bot_on():
            ctx.user_data.pop("state", None)
            await ctx.send("ربات در حال حاضر خاموش است.", _result_markup(uid, multi), parse_mode=None)
            return
        if uid in _busy:
            await ctx.send("تحلیل قبلی هنوز به پایان نرسیده است؛ چند لحظه صبر کنید.", kb(back_row(back)), parse_mode=None)
            return
        wait = config.ANALYSIS_COOLDOWN - (time.monotonic() - _last_request.get(uid, -1e9))
        if wait > 0:
            await ctx.send(f"لطفاً {int(wait) + 1} ثانیه دیگر دوباره امتحان کنید.", kb(back_row(back)), parse_mode=None)
            return
        _busy.add(uid)
    status = await ctx.send(BUSY_MULTI_TEXT if multi else BUSY_TEXT, kb(back_row(back)), parse_mode=None)

    async def show(msg: str, markup, html: bool = False) -> None:
        mode = "HTML" if html else None
        await ctx.send(msg, markup, parse_mode=mode)

    try:
        try:
            text = await (analyze_multi if multi else analyze_symbol)(raw, 0 if _is_admin(uid) else uid)
        except AnalysisError as e:
            await show(f"{NO} {e}", kb(back_row(back)))
            return
        except Exception:
            log.exception("analysis failed for %s", raw)
            await show(f"{NO} تحلیل انجام نشد؛ چند لحظه‌ی دیگر دوباره امتحان کنید.", kb(back_row(back)))
            return
        _last_request[uid] = time.monotonic()      # فاصله‌ی حداقلی فقط بعد از تحلیل موفق (مصرف AI) اعمال می‌شود
        if len(_last_request) > 20000:
            for k in list(_last_request)[:10000]:
                _last_request.pop(k, None)
        ctx.user_data.pop("state", None)
        save_request(uid, _clean_symbol(raw), text, "multi" if multi else "single")
        await show(text, _result_markup(uid, multi), html=True)
    finally:
        _busy.discard(uid)


async def handle_user_text(ctx: Ctx) -> bool:
    """متن کاربر در حالت آنالیز (اسم ارز). True یعنی پردازش شد."""
    st = ctx.user_data.get("state")
    if not st or st.get("kind") not in KINDS:
        return False
    msg = ctx.update.effective_message
    await run(ctx, (msg.text or "") if msg else "")
    return True
