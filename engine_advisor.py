"""
مشاور موتورها (آبان ترید): پیشنهاد تنظیمات بهینه‌ی موتورها با هوش مصنوعی.

- فقط وقتی ادمین دکمه‌ی «دریافت پیشنهاد از AI» را بزند شروع می‌شود؛ هیچ حالت خودکاری وجود ندارد.
- AI هیچ‌وقت خودش تنظیمات را تغییر نمی‌دهد؛ فقط پیشنهاد می‌دهد و ادمین تصمیم نهایی را می‌گیرد.
- قبل از ذخیره و قبل از اعمال، همه‌ی پیشنهادها اعتبارسنجی می‌شوند (محدوده‌ی مقدار، روابط منطقی، نام پارامتر،
  سازگاری با حالت مشترک / جداگانه). پیشنهاد نامعتبر رد می‌شود و دلیلش به ادمین نشان داده می‌شود.
- موقع اعمال فقط پارامترهای موجود در پیشنهاد به‌روز می‌شوند؛ بقیه‌ی پارامترها و موتورها دست‌نخورده می‌مانند (deep_merge).
- از همان سیستم کلیدهای AI (با چرخش خودکار) استفاده می‌شود.
"""
from __future__ import annotations

import asyncio
import copy
import json
import logging
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

import config
import performance_tracker
from ai_engine import SYSTEM_PROMPT, AICallError, ai, parse_json_object
from database import db, ts_str

log = logging.getLogger("advisor")

SIDES = ("long", "short")
ENGINE_SHORT = {
    "st": "Supertrend", "ema": "EMA", "rsi": "RSI", "srsi": "StochRSI", "obv": "OBV", "adx": "ADX",
    "atr": "ATR", "vol": "Volume", "sq": "Squeeze", "div": "Diverg", "cd": "Candle", "sr": "S/R",
    "poc": "POC", "ai": "AI", "lq": "Liquidity", "fvg": "FVG", "sm": "SmartMoney", "mb": "Momentum",
}
# محدوده‌های پیشنهادی (با محدوده‌ی خود ربات اشتراک‌گیری می‌شوند؛ بقیه‌ی پارامترها همان محدوده‌ی ربات را دارند)
ADVISOR_RANGES: Dict[str, Tuple[float, float]] = {
    "rsi_ob": (50, 90), "rsi_os": (10, 50), "adx_min": (10, 40), "vol_ratio": (0.5, 3.0),
    "st_period": (5, 20), "st_mult": (1.0, 5.0), "ema_fast": (10, 100), "ema_slow": (100, 300),
    "sl_mult": (0.5, 3.0), "tp1_mult": (0.5, 3.0), "tp2_mult": (1.0, 5.0), "tp3_mult": (2.0, 10.0),
    "sq_threshold": (0.5, 1.0), "div_strength": (1, 5),
}


class AdvisorError(Exception):
    pass


def _src(d):
    return db if d is None else d


def _is_user(d) -> bool:
    return d is not None and d is not db


def tunable_specs(user: bool = False) -> List[config.ParamSpec]:
    """پارامترهای قابل پیشنهاد؛ تنظیمات AI فقط دست ادمین است و برای کاربر پیشنهاد داده نمی‌شود."""
    return [s for s in config.PARAM_SPECS if s.group in config.ENGINES and not (user and s.group == "ai")]


def allowed_range(key: str) -> Tuple[float, float]:
    spec = config.PARAMS[key]
    lo, hi = float(spec.min), float(spec.max)
    if key in ADVISOR_RANGES:
        lo, hi = max(lo, ADVISOR_RANGES[key][0]), min(hi, ADVISOR_RANGES[key][1])
    return lo, hi


def _num(v: float, kind: str):
    return int(round(v)) if kind == "int" else float(v)


def _fmt(v: Any) -> str:
    if isinstance(v, bool):
        return "روشن" if v else "خاموش"
    if isinstance(v, (int, float)):
        return config.fmt_number(float(v))
    return str(v)


# ----------------------------------------------------------------------------
# جمع‌آوری داده است
# ----------------------------------------------------------------------------
async def collect_market_data(d=None) -> Dict[str, Any]:
    """میانگین ADX / ATR% / RSI همه‌ی نمادهای فعال (از کندل‌های کش‌شده‌ی موتور) + وضعیت کلی بازار."""
    from signal_engine import adx as adx_f, atr as atr_f, engine, rsi as rsi_f

    d = _src(d)
    symbols = engine.allowed_symbols(d)
    tf1, _tf2 = engine.timeframes(d, "LONG")
    tf = tf1 or "15m"
    candles = await asyncio.gather(*(engine._get_candles(s, tf) for s in symbols), return_exceptions=True)
    adxs: List[float] = []
    atrs: List[float] = []
    rsis: List[float] = []
    for c in candles:
        if c is None or isinstance(c, BaseException) or len(c) < 60:
            continue
        try:
            a = float(adx_f(c.h, c.l, c.c, 14)[0][-1])
            at = float(atr_f(c.h, c.l, c.c, 14)[-1]) / float(c.c[-1]) * 100.0
            r = float(rsi_f(c.c, 14)[-1])
        except Exception:
            continue
        if all(np.isfinite(x) for x in (a, at, r)):
            adxs.append(a)
            atrs.append(at)
            rsis.append(r)
    avg_adx = float(np.mean(adxs)) if adxs else 0.0
    state = "trending" if avg_adx > 25 else ("ranging" if avg_adx < 20 else "neutral")
    if not adxs:
        state = "unknown"
    return {
        "avg_adx": round(avg_adx, 1),
        "avg_atr_pct": round(float(np.mean(atrs)), 2) if atrs else 0.0,
        "avg_rsi": round(float(np.mean(rsis)), 1) if rsis else 0.0,
        "active_symbols_count": len(symbols),
        "analyzed_symbols": len(adxs),
        "market_state": state,
        "timeframe": tf,
    }


def collect_performance_data(uid: int = 0) -> Dict[str, Dict[str, Any]]:
    """عملکرد ۷ روز اخیر به‌تفکیک جهت (از جدول signal_performance) فقط برای سیگنال‌های همان کاربر (0 = ادمین)."""
    return performance_tracker.side_summary(7, uid)


def side_settings(side: str, d=None) -> Dict[str, Dict[str, Any]]:
    """تنظیمات فعلی یک جهت به‌صورت {شناسه موتور: {enabled, پارامترها...}} (برای کاربر بدون موتور AI)."""
    d = _src(d)
    s = side.upper()
    out: Dict[str, Dict[str, Any]] = {}
    for eid, (_title, key) in config.ENGINES.items():
        if eid == "ai" and _is_user(d):
            continue
        body: Dict[str, Any] = {"enabled": d.get_e_bool(key, s)}
        for spec in config.PARAM_SPECS:
            if spec.group == eid:
                body[spec.key] = _num(d.get_e_float(spec.key, s), spec.kind)
        out[eid] = body
    return out


def collect_current_settings(d=None) -> Dict[str, Any]:
    d = _src(d)
    return {"mode": d.eng_mode(), "long": side_settings("LONG", d), "short": side_settings("SHORT", d)}


def deep_merge(base: Dict[str, Any], updates: Dict[str, Any]) -> Dict[str, Any]:
    """فقط کلیدهای موجود در updates به‌روز می‌شوند؛ بقیه‌ی base دست‌نخورده می‌ماند (کپی جدید برمی‌گرداند)."""
    out = copy.deepcopy(base)
    for k, v in (updates or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


# ----------------------------------------------------------------------------
# پرامپت
# ----------------------------------------------------------------------------
INSTRUCTION = """شما یک تحلیلگر حرفه‌ای بازار فیوچرز ارز دیجیتال هستید.
وظیفه‌ی شما این است که بر اساس اطلاعات زیر، بهترین تنظیمات برای موتورهای تحلیل تکنیکال را پیشنهاد دهید.

قوانین مهم:
- فقط پارامترهایی را پیشنهاد بدهید که نیاز به تغییر دارند.
- اگر پارامتری مناسب است، آن را در خروجی نیاورید.
- موتورهای غیرفعال را دست نزنید مگر اینکه دلیل قوی داشته باشید.
- اگر پیشنهاد می‌دهید که یک موتور روشن یا خاموش شود، حتماً دلیلش را بگویید (با کلید "enabled": true یا false داخل همان موتور).
- همه‌ی پیشنهادات باید در محدوده‌ی مجاز باشند.
- خروجی را دقیقاً به فرمت JSON مشخص‌شده برگردانید."""

OUTPUT_FORMAT_TEMPLATE = """خروجی را دقیقاً به این فرمت JSON برگردانید:

{
  "analysis": "تحلیل کوتاه از وضعیت بازار و دلیل پیشنهادات",
  "suggestions": {
    "long": {
      "rsi": {"rsi_ob": 68, "rsi_os": 32},
      "adx": {"adx_min": 22}
    },
    "short": {
      "rsi": {"rsi_ob": 72},
      "adx": {"adx_min": 20}
    }
  },
  "reasoning": {
    "rsi": "چون نرخ موفقیت SHORT پایین است، آستانه RSI را بالاتر ببرید",
    "adx": "چون ADX بالای 20 است، آستانه ADX را بالاتر ببرید"
  }
}

نکته: فقط پارامترهایی را بیار که نیاز به تغییر دارند. کلیدهای موتورها فقط از این لیست باشند: """


def output_format(user: bool = False) -> str:
    return OUTPUT_FORMAT_TEMPLATE + ", ".join(e for e in config.ENGINES if not (user and e == "ai"))


def build_prompt(
    market: Dict[str, Any], perf: Dict[str, Dict[str, Any]], current: Dict[str, Any], user: bool = False,
) -> Tuple[str, str]:
    """پرامپت پویا: دستور + داده‌های لحظه‌ای + فرمت خروجی. خروجی: (system, user)."""
    ranges = "\n".join(
        f"- {s.key}: بین {config.fmt_number(allowed_range(s.key)[0])} تا {config.fmt_number(allowed_range(s.key)[1])}"
        + (" (عدد صحیح)" if s.kind == "int" else "")
        for s in tunable_specs(user)
    )

    def perf_line(side: str) -> str:
        p = perf.get(side.upper()) or {}
        tp = p.get("avg_hours_to_tp")
        return (
            f"- {side.upper()}: {p.get('total', 0)} سیگنال | {p.get('success_rate', 0)}% موفق | "
            f"میانگین سود {p.get('avg_profit_pct', 0)}%"
            + (f" | میانگین زمان رسیدن به TP: {tp} ساعت" if tp is not None else "")
        )

    mode_note = (
        "حالت تنظیمات «مشترک» است: long و short یکسان‌اند و پیشنهادهای شما برای هر دو جهت باید یکسان باشد."
        if current.get("mode") == "global"
        else "حالت تنظیمات «جداگانه» است: برای long و short می‌توانید پیشنهادهای متفاوت بدهید."
    )
    data = f"""📊 وضعیت فعلی بازار:
- میانگین ADX: {market.get('avg_adx')}
- میانگین ATR: {market.get('avg_atr_pct')}%
- میانگین RSI: {market.get('avg_rsi')}
- تعداد نمادهای فعال: {market.get('active_symbols_count')}
- وضعیت کلی بازار: {market.get('market_state')}

📈 عملکرد اخیر (۷ روز گذشته):
{perf_line('LONG')}
{perf_line('SHORT')}

⚙️ تنظیمات فعلی ({mode_note}):
{json.dumps(current, ensure_ascii=False)}

📋 محدوده‌ی مجاز پارامترها:
{ranges}"""
    prompt = f"{INSTRUCTION}\n\n{data}\n\n{output_format(user)}"
    return SYSTEM_PROMPT, prompt


# ----------------------------------------------------------------------------
# تجزیه و اعتبارسنجی
# ----------------------------------------------------------------------------
def parse_ai_response(raw: str) -> Dict[str, Any]:
    """پاسخ JSON هوش مصنوعی را به دیکشنری تبدیل می‌کند؛ اگر معتبر نباشد AdvisorError."""
    data = parse_json_object(raw or "")
    if data is None:
        raise AdvisorError("پاسخ هوش مصنوعی JSON معتبر نبود.")
    sugg = data.get("suggestions")
    if not isinstance(sugg, dict):
        raise AdvisorError("پاسخ هوش مصنوعی بخش suggestions نداشت.")
    reasoning = data.get("reasoning")
    return {
        "analysis": str(data.get("analysis") or "").strip(),
        "suggestions": sugg,
        "reasoning": {str(k): str(v) for k, v in reasoning.items()} if isinstance(reasoning, dict) else {},
    }


def _flat_current(side: str, d=None) -> Dict[str, float]:
    d = _src(d)
    s = side.upper()
    flat: Dict[str, float] = {sp.key: d.get_e_float(sp.key, s) for sp in tunable_specs()}
    for _eid, (_t, key) in config.ENGINES.items():
        flat[key] = 1.0 if d.get_e_bool(key, s) else 0.0
    return flat


def _to_bool(v: Any) -> Optional[bool]:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)) and v in (0, 1):
        return bool(v)
    if isinstance(v, str) and v.strip().lower() in ("true", "false", "1", "0"):
        return v.strip().lower() in ("true", "1")
    return None


def validate_suggestions(
    suggestions: Dict[str, Any], d=None,
) -> Tuple[Dict[str, Dict[str, Dict[str, Any]]], List[str]]:
    """
    پیشنهادهای AI را قبل از اعمال بررسی می‌کند. خروجی: (پیشنهادهای معتبر، لیست دلیل رد شدن‌ها).
    پیشنهاد معتبر: {"long": {موتور: {پارامتر: مقدار, "enabled": bool}}, "short": {...}}
    (در حالت مشترک long و short یکسان برمی‌گردند). مقدارهایی که با مقدار فعلی برابرند حذف می‌شوند.
    """
    rejected: List[str] = []
    d = _src(d)
    user = _is_user(d)
    mode = d.eng_mode()
    raw: Dict[str, Dict[str, Dict[str, Any]]] = {"long": {}, "short": {}}

    # ۱) نام موتور / پارامتر، نوع و محدوده
    for side in SIDES:
        sec = suggestions.get(side) if isinstance(suggestions, dict) else None
        if sec is None:
            continue
        if not isinstance(sec, dict):
            rejected.append(f"{side}: ساختار نامعتبر.")
            continue
        for eid, body in sec.items():
            if eid not in config.ENGINES:
                rejected.append(f"{side}: موتور ناشناخته «{eid}».")
                continue
            if user and eid == "ai":
                rejected.append(f"{side}: تنظیمات هوش مصنوعی توسط ادمین مدیریت می‌شود و قابل تغییر نیست.")
                continue
            if not isinstance(body, dict):
                rejected.append(f"{side}/{eid}: ساختار نامعتبر.")
                continue
            for k, v in body.items():
                if k == "enabled":
                    b = _to_bool(v)
                    if b is None:
                        rejected.append(f"{side}/{eid}/enabled: فقط true یا false.")
                    else:
                        raw[side].setdefault(eid, {})["enabled"] = b
                    continue
                spec = config.PARAMS.get(k)
                if spec is None or spec.group != eid:
                    rejected.append(f"{side}/{eid}: پارامتر ناشناخته «{k}».")
                    continue
                if isinstance(v, bool) or not isinstance(v, (int, float, str)):
                    rejected.append(f"{side}/{eid}/{k}: باید عدد باشد.")
                    continue
                val = config.parse_number(spec, str(v))
                if val is None:
                    rejected.append(f"{side}/{eid}/{k}: عدد معتبر نیست ({v}).")
                    continue
                lo, hi = allowed_range(k)
                if not lo <= val <= hi:
                    rejected.append(
                        f"{side}/{eid}/{k}: مقدار {config.fmt_number(float(val))} خارج از محدوده‌ی مجاز "
                        f"({config.fmt_number(lo)} تا {config.fmt_number(hi)})."
                    )
                    continue
                raw[side].setdefault(eid, {})[k] = _num(float(val), spec.kind)

    # ۲) سازگاری با حالت مشترک: long و short باید یکسان باشند
    if mode == "global":
        merged: Dict[str, Dict[str, Any]] = {}
        for eid in set(raw["long"]) | set(raw["short"]):
            keys = set(raw["long"].get(eid, {})) | set(raw["short"].get(eid, {}))
            for k in keys:
                a, b = raw["long"].get(eid, {}).get(k), raw["short"].get(eid, {}).get(k)
                if a is not None and b is not None and a != b:
                    rejected.append(
                        f"{eid}/{k}: حالت تنظیمات مشترک است اما مقدار long ({_fmt(a)}) و short ({_fmt(b)}) فرق دارند."
                    )
                    continue
                merged.setdefault(eid, {})[k] = a if a is not None else b
        raw = {"long": merged, "short": copy.deepcopy(merged)}

    # ۳) حذف مقدارهای بدون تغییر + بررسی روابط منطقی (بین مقدارهای جدید و فعلی)
    valid: Dict[str, Dict[str, Dict[str, Any]]] = {"long": {}, "short": {}}
    for side in (("long",) if mode == "global" else SIDES):
        cur = _flat_current(side, d)
        cand: Dict[str, Dict[str, Any]] = {}
        for eid, body in raw[side].items():
            for k, v in body.items():
                old = bool(cur[config.ENGINES[eid][1]]) if k == "enabled" else cur[k]
                if (k == "enabled" and v == old) or (k != "enabled" and float(v) == float(old)):
                    continue
                cand.setdefault(eid, {})[k] = v
        for _ in range(6):
            flat = dict(cur)
            for eid, body in cand.items():
                for k, v in body.items():
                    if k == "enabled":
                        flat[config.ENGINES[eid][1]] = 1.0 if v else 0.0
                    else:
                        flat[k] = float(v)
            dropped = False
            for eid in list(cand):
                for k in list(cand[eid]):
                    if k == "enabled":
                        continue
                    err = config.validate_param(k, float(cand[eid][k]), lambda x, f=flat: f.get(x, 0.0))
                    if err:
                        rejected.append(f"{side}/{eid}/{k}: {err}")
                        del cand[eid][k]
                        dropped = True
                if not cand[eid]:
                    del cand[eid]
            en_st, en_ema = flat[config.ENGINES["st"][1]], flat[config.ENGINES["ema"][1]]
            if not en_st and not en_ema:
                for eid in ("st", "ema"):
                    if cand.get(eid, {}).get("enabled") is False:
                        rejected.append(f"{side}/{eid}/enabled: حداقل یکی از Supertrend یا EMA باید روشن بماند.")
                        del cand[eid]["enabled"]
                        if not cand[eid]:
                            del cand[eid]
                        dropped = True
            if not dropped:
                break
        valid[side] = cand
    if mode == "global":
        valid["short"] = copy.deepcopy(valid["long"])
    return valid, rejected


def build_changes(valid: Dict[str, Dict[str, Dict[str, Any]]], d=None) -> List[Dict[str, Any]]:
    """جدول مقایسه‌ای: [{side, engine, key, old, new}] (مقدار فعلی از دیتابیس)."""
    d = _src(d)
    changes: List[Dict[str, Any]] = []
    sides = ("long",) if d.eng_mode() == "global" else SIDES
    for side in sides:
        cur = side_settings(side, d)
        for eid, body in valid.get(side, {}).items():
            for k, v in body.items():
                changes.append({
                    "side": "ALL" if d.eng_mode() == "global" else side.upper(),
                    "engine": eid, "key": k, "old": cur.get(eid, {}).get(k), "new": v,
                })
    return changes


def format_table(changes: List[Dict[str, Any]], d=None) -> str:
    """جدول مقایسه‌ای فعلی vs پیشنهادی (متن ساده برای <pre>)."""
    if not changes:
        return ""
    sep = _src(d).eng_mode() == "separate"
    head = (f"{'جهت':<6}" if sep else "") + f"{'Engine':<11}{'Param':<14}{'Now':>8}  {'AI':>8}"
    rows = [head]
    for c in changes:
        rows.append(
            (f"{c['side']:<6}" if sep else "")
            + f"{ENGINE_SHORT.get(c['engine'], c['engine']):<11}{c['key']:<14}"
            f"{_fmt(c['old']):>8}  {_fmt(c['new']):>8}"
        )
    return "\n".join(rows)


# ----------------------------------------------------------------------------
# ذخیره / تاریخچه
# ----------------------------------------------------------------------------
def store(
    analysis: str, valid: Dict[str, Any], reasoning: Dict[str, str], rejected: List[str], uid: int = 0,
) -> int:
    old = {"changes": build_changes(valid, db.scope(uid))}
    return db.insert_id(
        "INSERT INTO engine_suggestions(user_id, analysis_text, suggestions_json, reasoning_json, old_json, "
        "rejected_json, status) VALUES(?, ?, ?, ?, ?, ?, 'pending')",
        (int(uid), analysis, json.dumps(valid, ensure_ascii=False), json.dumps(reasoning, ensure_ascii=False),
         json.dumps(old, ensure_ascii=False), json.dumps(rejected, ensure_ascii=False)),
    )


def get(sid: int, uid: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """پیشنهاد با شناسه؛ اگر uid داده شود فقط وقتی برمی‌گردد که متعلق به همان کاربر (0 = ادمین) باشد."""
    r = db.q1("SELECT * FROM engine_suggestions WHERE id = ?", (sid,))
    if r is None:
        return None
    if uid is not None and int(r.get("user_id") or 0) != int(uid):
        return None
    r["created_at"] = ts_str(r.get("created_at"))
    r["applied_at"] = ts_str(r.get("applied_at"))
    for k in ("suggestions_json", "reasoning_json", "old_json", "rejected_json"):
        try:
            r[k[:-5]] = json.loads(r.get(k) or "null")
        except ValueError:
            r[k[:-5]] = None
    return r


def history(limit: int = 15, uid: int = 0) -> List[Dict[str, Any]]:
    return db.q(
        "SELECT id, status, created_at FROM engine_suggestions WHERE COALESCE(user_id, 0) = ? "
        "ORDER BY id DESC LIMIT ?", (int(uid), limit),
    )


def latest_id(uid: int = 0) -> Optional[int]:
    r = db.q1(
        "SELECT id FROM engine_suggestions WHERE COALESCE(user_id, 0) = ? ORDER BY id DESC LIMIT 1", (int(uid),)
    )
    return int(r["id"]) if r else None


def reject(sid: int, uid: int = 0) -> bool:
    return db.x(
        "UPDATE engine_suggestions SET status = 'rejected' "
        "WHERE id = ? AND status = 'pending' AND COALESCE(user_id, 0) = ?", (sid, int(uid)),
    ).rowcount > 0


# ----------------------------------------------------------------------------
# درخواست از AI
# ----------------------------------------------------------------------------
def ai_ready() -> Tuple[bool, str]:
    if not ai.master_on():
        return False, "سوییچ اصلی هوش مصنوعی خاموش است (تنظیمات > مدیریت کلیدهای هوش مصنوعی)."
    if ai.temporarily_off():
        return False, "هوش مصنوعی به‌خاطر خطای کلیدها موقتاً خاموش است؛ از صفحه‌ی مدیریت کلیدها دوباره فعالش کنید."
    if not ai.available_keys():
        return False, "کلید سالمی (با Account ID یا آدرس پیش‌فرض) برای هوش مصنوعی وجود ندارد."
    return True, ""


async def request_suggestion(uid: int = 0) -> Tuple[Optional[int], str]:
    """
    فرآیند کامل: جمع‌آوری داده -> پرامپت -> AI -> تجزیه -> اعتبارسنجی -> ذخیره.
    خروجی: (شناسه‌ی پیشنهاد یا None، پیام خطا). فقط با دکمه‌ی ادمین فراخوانی می‌شود.
    """
    ok, msg = ai_ready()
    if not ok:
        return None, msg
    d = db.scope(uid)
    try:
        market = await collect_market_data(d)
    except Exception:
        log.exception("collect market data failed")
        market = {
            "avg_adx": 0, "avg_atr_pct": 0, "avg_rsi": 0, "active_symbols_count": 0, "market_state": "unknown",
        }
    system, prompt = build_prompt(
        market, collect_performance_data(uid), collect_current_settings(d), user=bool(uid),
    )
    model = (d.get("ai_model") or config.AI_MODELS[0]).strip()
    try:
        raw = await ai.request(system, prompt, 1800, model, timeout=60)
    except AICallError as e:
        return None, f"خطا از سرویس هوش مصنوعی: {e.msg}"
    except Exception as e:
        log.exception("advisor ai request failed")
        return None, f"خطای غیرمنتظره در ارتباط با هوش مصنوعی: {str(e)[:120]}"
    try:
        parsed = parse_ai_response(raw)
    except AdvisorError as e:
        return None, str(e)
    valid, rejected = validate_suggestions(parsed["suggestions"], d)
    sid = store(parsed["analysis"], valid, parsed["reasoning"], rejected, uid)
    return sid, ""


# ----------------------------------------------------------------------------
# اعمال
# ----------------------------------------------------------------------------
def apply_suggestions(sid: int, uid: int = 0) -> Tuple[bool, str]:
    """
    پیشنهاد در انتظار را اعمال می‌کند. دوباره اعتبارسنجی می‌شود (تنظیمات ممکن است از زمان پیشنهاد عوض شده باشد) و
    فقط کلیدهای موجود در پیشنهاد به‌روز می‌شوند؛ بقیه‌ی تنظیمات دست‌نخورده می‌ماند.
    """
    row = get(sid, uid)
    if row is None:
        return False, "این پیشنهاد پیدا نشد."
    if row["status"] != "pending":
        return False, "این پیشنهاد قبلاً بررسی شده است."
    d = db.scope(uid)
    saved = row.get("suggestions") or {}
    valid, rejected = validate_suggestions(saved, d)
    mode = d.eng_mode()
    if not any(valid.get(s) for s in SIDES):
        return False, "هیچ پیشنهاد معتبری برای اعمال باقی نمانده (تنظیمات فعلی با پیشنهاد هم‌خوانی دارد یا نامعتبر است)."
    items: List[Tuple[str, str, Optional[str]]] = []
    for side in (("long",) if mode == "global" else SIDES):
        # deep_merge: تنظیمات فعلی + پیشنهاد (فقط کلیدهای پیشنهادی عوض می‌شوند)
        merged = deep_merge(side_settings(side, d), valid.get(side, {}))
        target_side = None if mode == "global" else side.upper()
        for eid, body in valid.get(side, {}).items():
            for k in body:
                v = merged[eid][k]
                if k == "enabled":
                    items.append((config.ENGINES[eid][1], "1" if v else "0", target_side))
                else:
                    spec = config.PARAMS[k]
                    items.append((k, str(int(v)) if spec.kind == "int" else config.fmt_number(float(v)), target_side))
    d.set_many_e(items)
    db.x(f"UPDATE engine_suggestions SET status = 'applied', applied_at = {db.NOW} WHERE id = ?", (sid,))
    note = f"\n{len(rejected)} مورد به‌خاطر نامعتبر شدن اعمال نشد." if rejected else ""
    return True, f"{len(items)} مقدار اعمال شد.{note}"
