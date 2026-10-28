"""تولید کاتالوگ استراتژی‌های ساختاری از فایل list.txt و نگاشت هر استراتژی به قاعده‌ی تشخیص.

فایل list.txt منبع قطعی عنوان‌ها، دسته‌ها و نوع سیگنال (حمایت/مقاومت) است.
هر استراتژی یک logic_key و پارامترهای تشخیص می‌گیرد تا موتور ساختاری (structural_engine.py) آن را اجرا کند.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent
LIST_PATH = BASE_DIR / "list.txt"
CATALOG_VERSION = "1"

CATEGORY_EN = {
    "خط روند": "trendline",
    "مقاومت و حمایت": "support_resistance",
    "کانال": "channel",
    "عرضه و تقاضا و اردر بلاک": "order_block",
    "استاپ هانت": "stop_hunt",
    "الگوی هارمونیک": "harmonic",
    "لگاریتمی": "logarithmic",
    "واگرایی MACD": "macd_divergence",
    "واگرایی RSI": "rsi_divergence",
    "واگرایی 3گانه": "triple_divergence",
    "چنگال اندروز": "andrews",
    "الگو کلاسیک": "classic",
    "الگو کندل استیک": "candlestick",
    "هانتر (روند RSI)": "hunter",
    "روند ویژه": "special_trend",
    "پولبک": "pullback",
    "مستطیل و سقف و کف": "rectangle",
    "ایچیموکو": "ichimoku",
    "MACD": "macd",
    "میانگین متحرک": "moving_average",
    "الگوی ab=cd": "abcd",
    "CCI": "cci",
    "Stochastic": "stochastic",
    "Bollinger Band": "bollinger",
    "RSI": "rsi",
    "حجم": "volume",
}

IMAGE_DIMS = {
    "trendline": (460, 300), "support_resistance": (420, 300), "channel": (470, 300),
    "order_block": (460, 320), "stop_hunt": (400, 280), "harmonic": (500, 380),
    "logarithmic": (480, 320), "macd_divergence": (500, 300), "rsi_divergence": (500, 300),
    "triple_divergence": (520, 320), "andrews": (500, 340), "classic": (480, 320),
    "candlestick": (320, 220), "hunter": (420, 300), "special_trend": (440, 300),
    "pullback": (440, 300), "rectangle": (420, 280), "ichimoku": (500, 340),
    "macd": (460, 280), "moving_average": (460, 280), "abcd": (440, 300),
    "cci": (420, 260), "stochastic": (440, 280), "bollinger": (440, 280),
    "rsi": (420, 280), "volume": (420, 260),
}

_NORM_TRANS = str.maketrans({
    "ي": "ی", "ك": "ک", "–": "-", "—": "-", "−": "-", "‌": " ",
    "۰": "0", "۱": "1", "۲": "2", "۳": "3", "۴": "4",
    "۵": "5", "۶": "6", "۷": "7", "۸": "8", "۹": "9",
})


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").translate(_NORM_TRANS)).strip()


def _key(category: str, title: str) -> str:
    cat = CATEGORY_EN.get(category, "strategy")
    digest = hashlib.md5(f"{category}|{title}".encode("utf-8")).hexdigest()[:10]
    return f"{cat}_{digest}"


def parse_list_file(path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """خواندن list.txt و برگرداندن استراتژی‌های یکتا به ترتیب فایل."""
    p = Path(path or LIST_PATH)
    text = p.read_text(encoding="utf-8-sig", errors="replace")
    lines = [normalize(x) for x in text.splitlines()]
    lines = [x for x in lines if x]
    out: List[Dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    category = ""
    i = 0
    order = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("نوع استراتژی:"):
            i += 1
            continue
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        if nxt.startswith("نوع استراتژی:"):
            title = line
            spec = nxt
            m = re.search(r"نوع استراتژی:\s*(.+?)\s+نوع:\s*(.+)$", spec)
            if m:
                strategy_type, signal_type = normalize(m.group(1)), normalize(m.group(2))
            else:
                strategy_type, signal_type = category, "حمایت"
            if signal_type not in ("حمایت", "مقاومت"):
                signal_type = "حمایت"
            dedupe = (category, title, signal_type)
            if category and dedupe not in seen:
                seen.add(dedupe)
                logic_key, params = logic_for(category, title, signal_type)
                cat_en = CATEGORY_EN.get(category, "strategy")
                w, h = IMAGE_DIMS.get(cat_en, (400, 300))
                out.append({
                    "strategy_key": _key(category, title),
                    "category": category,
                    "title": title,
                    "strategy_type": strategy_type or category,
                    "signal_type": signal_type,
                    "text_color": "green" if signal_type == "حمایت" else "red",
                    "button_color": "success" if signal_type == "حمایت" else "danger",
                    "logic_key": logic_key,
                    "logic_params": params,
                    "description": f"استراتژی «{title}» از دسته «{strategy_type or category}» با سیگنال {signal_type}.",
                    "guide_width": w,
                    "guide_height": h,
                    "sort_order": order,
                })
                order += 1
            i += 2
            continue
        category = line
        i += 1
    return out


# ----------------------------------------------------------------------------
# نگاشت عنوان به قاعده‌ی تشخیص
# ----------------------------------------------------------------------------
def _num(pattern: str, text: str, default: float = 0) -> float:
    m = re.search(pattern, text)
    if not m:
        return default
    try:
        return float(m.group(1))
    except (TypeError, ValueError):
        return default


def logic_for(category: str, title: str, signal_type: str) -> tuple[str, str]:
    t = normalize(title)
    cat = CATEGORY_EN.get(category, "generic")
    up = "up" if "به بالا" in t else "down" if "به پایین" in t else ""

    if cat == "rsi" or category == "RSI":
        if "میانگین" in t:
            return "rsi_ma_cross", f"period=14;ma=14;dir={up or 'up'}"
        level = _num(r"تقاطع\s*([0-9]+)", t, 50)
        return "rsi_cross", f"level={level:g};dir={up or 'up'}"

    if category == "MACD":
        if "هیستوگرام" in t:
            return "macd_histogram", f"dir={up or ('up' if 'مثبت' in t else 'down')}"
        if "تغییر" in t:
            return "macd_zero_cross", f"dir={'up' if 'مثبت' in t else 'down'}"
        return "macd_signal_cross", f"dir={up or 'up'}"

    if category == "میانگین متحرک":
        if "9 12 15" in t or "9 12 15" in t.replace("  ", " "):
            return "ma_triple_cross", f"dir={up or 'up'}"
        period = _num(r"EMA\s*([0-9]+)", t, 50)
        if "EMA" in t:
            return "price_ema_cross", f"period={period:g};dir={up or 'up'}"
        return "ma_triple_cross", f"dir={up or 'up'}"

    if category == "CCI":
        level = _num(r"\(([+-]?[0-9]+)\)", t, 100)
        return "cci_cross", f"level={level:g};dir={up or 'up'}"

    if category == "Stochastic":
        return "stoch_cross", f"zone={'ob' if 'اشباع خرید' in t else 'os'}"

    if category == "Bollinger Band":
        return "bollinger_touch", f"side={'upper' if 'سقف' in t else 'lower'}"

    if category == "حجم":
        window = _num(r"([0-9]+)\s*کندلی", t, 30)
        mult = _num(r"([0-9.]+)\s*برابر", t, 2)
        return "volume_spike", f"window={window:g};mult={mult:g}"

    if cat == "macd_divergence" or cat == "rsi_divergence":
        kind = "regular" if "RD" in t.upper() else "hidden"
        confirmed = "قطعی" in t
        direction = "bull" if ("+" in t or signal_type == "حمایت") else "bear"
        base = "macd_divergence" if cat == "macd_divergence" else "rsi_divergence"
        return base, f"kind={kind};confirmed={int(confirmed)};dir={direction}"

    if cat == "triple_divergence":
        return "triple_divergence", f"dir={'bull' if signal_type == 'حمایت' else 'bear'}"

    if cat == "harmonic":
        name = "gartley"
        for key, en in (
            ("گارتلی", "gartley"), ("بت", "bat"), ("پروانه", "butterfly"), ("CRAB", "crab"),
            ("CYPHER", "cypher"), ("نن استار", "shark"), ("شارک", "shark"), ("قوی سفید", "white_swan"),
        ):
            if key.lower() in t.lower():
                name = en
                break
        direction = "bull" if signal_type == "حمایت" else "bear"
        return "harmonic", f"pattern={name};dir={direction}"

    if cat == "abcd":
        return "abcd_pattern", f"dir={'bull' if signal_type == 'حمایت' else 'bear'}"

    if cat == "candlestick":
        mapping = {
            "hammer": "hammer", "hanging man": "hanging_man",
            "bullish engulfing": "bullish_engulfing", "bearish engulfing": "bearish_engulfing",
            "piersing": "piercing", "piercing": "piercing", "dark cload": "dark_cloud", "dark cloud": "dark_cloud",
            "morning star": "morning_star", "evening star": "evening_star",
            "inverted hammer": "inverted_hammer", "shooting star": "shooting_star",
            "harami": "harami", "downside gap tasuli": "gap_down", "upside gap tasuli": "gap_up",
        }
        low = t.lower()
        # نام‌های بلندتر پیشتر بررسی می‌شوند، وگرنه "Inverted hammer" با "hammer" یکی می‌شود
        name = next((v for k, v in sorted(mapping.items(), key=lambda kv: -len(kv[0])) if k in low), "hammer")
        return "candlestick", f"pattern={name}"

    if category == "ایچیموکو":
        # «قیمت بین تنکنسن و کیجونسن» یک شرایط مستقل است و نباید به تقاطع دو خط تبدیل شود
        if "قیمت بین" in t:
            return "ichimoku_tenkan_kijun_range", f"cloud={'below' if 'زیر ابر' in t or 'پایین ابر' in t else 'above'}"
        if "تنکنسن" in t and "کیجونسن" in t:
            return "ichimoku_cross", f"dir={'up' if 'به بالا' in t else 'down'};cloud={'above' if 'بالای ابر' in t else 'below'}"
        if "کیجون" in t:
            return "ichimoku_kijun_touch", f"cloud={'below' if 'زیر ابر' in t else 'above'}"
        if "ابر کومو سبز" in t:
            return "ichimoku_cloud_break", "side=down;color=green"
        if "ابر کومو قرمز" in t:
            return "ichimoku_cloud_break", "side=up;color=red"
        if "آینده" in t:
            return "ichimoku_future_cloud", f"color={'red' if signal_type == 'مقاومت' else 'green'}"
        if "lagging" in t.lower():
            return "ichimoku_lagging_exit", f"side={'down' if signal_type == 'مقاومت' else 'up'}"
        return "ichimoku_tenkan_kijun_range", f"cloud={'below' if 'زیر ابر' in t else 'above'}"

    if cat == "trendline" or cat == "logarithmic" or cat == "special_trend":
        log = cat == "logarithmic"
        special = cat == "special_trend"
        prefix = "log_" if log else "special_" if special else ""
        if "پولبک" in t:
            return f"{prefix}trendline_pullback", f"side={'resistance' if signal_type == 'مقاومت' else 'support'}"
        if "شکست" in t and ("روند" in t or "داینامیک" in t):
            direction = "down" if "به پایین" in t else "up" if "به بالا" in t else ("down" if signal_type == "مقاومت" else "up")
            return f"{prefix}trendline_break", f"dir={direction};volume={int('ویژه' in t)}"
        if "رسیدن" in t or "تاچ" in t or "کف روند" in t or "سقف" in t:
            side = "resistance" if signal_type == "مقاومت" else "support"
            touch = int("تاچ" in t or "لمس" in t)
            return f"{prefix}trendline_touch", f"side={side};touch={touch}"
        return f"{prefix}trendline_touch", f"side={'resistance' if signal_type == 'مقاومت' else 'support'}"

    if category == "مقاومت و حمایت":
        if "شکست" in t:
            return "sr_break", f"dir={'up' if 'مقاومت' in t else 'down'}"
        if "رنج" in t:
            return "sr_range", f"side={'above' if 'بالای' in t else 'below'}"
        touch = int("تاچ" in t)
        return "sr_touch", f"side={'resistance' if 'مقاومت' in t else 'support'};touch={touch}"

    if category == "کانال":
        if "مستطیل" in t:
            return "rectangle_boundary", f"side={'upper' if 'سقف' in t else 'lower'}"
        touch = int("تاچ" in t)
        return "channel_touch", f"side={'upper' if 'سقف' in t else 'lower'};dir={'up' if 'صعودی' in t else 'down'};touch={touch}"

    if category == "عرضه و تقاضا و اردر بلاک":
        return "order_block", f"side={'supply' if 'عرضه' in t else 'demand'}"

    if category == "استاپ هانت":
        return "stop_hunt", f"dir={'buy' if 'خرید' in t else 'sell'}"

    if category == "چنگال اندروز":
        direction = "up" if "صعودی" in t else "down"
        if "میدلاین" in t:
            event = "break" if "شکست" in t else "touch"
            return "andrews_median", f"dir={direction};event={event}"
        if "شکست" in t:
            return "andrews_break", f"dir={direction};side={'upper' if 'سقف' in t else 'lower'}"
        return "andrews_touch", f"dir={direction};side={'upper' if 'سقف' in t else 'lower'}"

    if category == "الگو کلاسیک":
        if "مثلث" in t:
            kind = "triangle"
        elif "پرچم" in t:
            kind = "flag"
        elif "کنج" in t:
            kind = "wedge"
        elif "مستطیل" in t:
            kind = "rectangle"
        else:
            kind = "triangle"
        # جهت صریح («شکست به بالا/به پایین») بر واژهٔ نوع الگو اولویت دارد، وگرنه
        # «کنج صعودی - شکست به پایین» و «... به بالا» هر دو dir=up می‌شدند
        if "به بالا" in t:
            direction = "up"
        elif "به پایین" in t:
            direction = "down"
        elif "نزولی" in t:
            direction = "down"
        else:
            direction = "up"
        suspicious = int("مشکوک" in t)
        breakout = int("شکست" in t)
        return "classic_pattern", f"kind={kind};dir={direction};suspicious={suspicious};breakout={breakout}"

    if category == "هانتر (روند RSI)":
        return "hunter", f"dir={'buy' if 'خرید' in t else 'sell'}"

    if category == "پولبک":
        touch = int("تاچ" in t)
        if "خط روند" in t:
            return "pullback", f"kind=trendline;dir={'up' if 'صعودی' in t else 'down'};touch={touch}"
        if "حمایت" in t:
            return "pullback", f"kind=support;dir=up;touch={touch}"
        return "pullback", f"kind=resistance;dir=down;touch={touch}"

    if category == "مستطیل و سقف و کف":
        if "شکست" in t:
            # جهت شکست از متن عنوان می‌آید؛ اگر عنوان جهت را نگفته باشد، نوع سیگنال تعیین‌کننده است.
            direction = "up" if "بالا" in t else "down" if "پایین" in t else ("up" if signal_type == "حمایت" else "down")
            return "rectangle_break", f"dir={direction}"
        return "rectangle_boundary", f"side={'upper' if 'سقف' in t else 'lower'}"

    if cat == "volume":
        window = _num(r"([0-9]+)\s*کندلی", t, 30)
        mult = _num(r"([0-9.]+)\s*برابر", t, 2)
        return "volume_spike", f"window={window:g};mult={mult:g}"

    return "generic_trend", f"dir={'up' if signal_type == 'حمایت' else 'down'}"


def catalog_stats(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    cats: Dict[str, int] = {}
    for it in items:
        cats[it["category"]] = cats.get(it["category"], 0) + 1
    return {"total": len(items), "categories": len(cats), "by_category": cats}
