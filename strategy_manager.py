"""
استراتژی‌های آماده (آبان ترید).

هر استراتژی دو بخش جداگانه دارد (LONG و SHORT) که هر کدام تنظیمات مخصوص خودش را دارد:
  - روشن/خاموش بودن همه‌ی ۱۸ موتور
  - مقدار پارامترها
  - تایم‌فریم ورود و تأیید
فعال‌سازی یک استراتژی، ربات را روی «حالت جداگانه» می‌برد و پروفایل LONG و SHORT را دقیقاً طبق همان استراتژی
تنظیم می‌کند (هر پارامتری که در استراتژی نیست به پیش‌فرض برمی‌گردد تا رفتار استراتژی دقیق باشد؛ فقط تنظیمات
عملیاتی هوش مصنوعی مثل مدل و سقف مصرف دست‌نخورده می‌مانند).

استراتژی‌های پیش‌فرض: پامپ و دامپ، شکار انفجار، اسکالپ سریع.
استراتژی سفارشی: «ذخیره‌ی تنظیمات فعلی به‌عنوان استراتژی جدید».
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

import config
from database import db

log = logging.getLogger("strategy")

# شناسه‌ی موتورها: st ema rsi srsi obv adx atr vol sq div cd sr poc lq fvg sm mb ai
_ENGINE_TITLES = {eid: title for eid, (title, _k) in config.ENGINES.items()}


def _profile(on: set, params: Dict[str, Any], tf: Tuple[str, str]) -> Dict[str, Any]:
    settings: Dict[str, str] = {}
    for eid, (_title, key) in config.ENGINES.items():
        settings[key] = "1" if eid in on else "0"
    for k, v in params.items():
        spec = config.PARAMS[k]
        settings[k] = str(int(v)) if spec.kind == "int" else config.fmt_number(float(v))
    return {"tf_entry": tf[0], "tf_final": tf[1], "settings": settings}


# ----------------------------------------------------------------------------
# تعریف استراتژی‌های پیش‌فرض
# ----------------------------------------------------------------------------
_PUMP_COMMON = dict(
    st_period=7, st_mult=2.0, ema_fast=20, ema_slow=100, rsi_period=14, obv_ma=20, adx_period=14, adx_min=18,
    atr_period=14, tp1_mult=1.5, tp2_mult=3.0, tp3_mult=5.0, min_atr_pct=0.5, vol_ma=20, vol_ratio=1.3,
    cd_min_score=55, cd_lookback=5, poc_lookback=100, poc_bins=24, poc_min_dist=0.2, poc_score=15,
    poc_sl_buffer=0.2, poc_sl_min=0.5, ai_min_score=60, ai_sent_veto=75,
)
_PUMP_LONG = dict(_PUMP_COMMON, rsi_ob=70, rsi_os=35, sl_mult=1.5)
_PUMP_SHORT = dict(
    _PUMP_COMMON, rsi_ob=65, rsi_os=30, sl_mult=1.2,
    div_rsi=14, div_pivots=3, div_gap=5, div_strength=2, div_age=8, div_lookback=120,
    sr_count=6, sr_min_dist=0.3, sr_min_gap=8, sr_strength=3, sr_touches=2, sr_near=0.8, sr_lookback=200,
)
_BREAKOUT = dict(
    st_period=10, st_mult=3.0, ema_fast=50, ema_slow=200, rsi_period=14, rsi_ob=70, rsi_os=30,
    srsi_rsi=14, srsi_stoch=14, srsi_k=3, srsi_d=3, obv_ma=20, adx_period=14, adx_min=15, atr_period=14,
    sl_mult=1.5, tp1_mult=2.0, tp2_mult=4.0, tp3_mult=6.0, min_atr_pct=0.5, vol_ma=20, vol_ratio=1.0,
    sq_pre=1, sq_bb_period=20, sq_bb_mult=2.0, sq_threshold=0.8, sq_min_bars=5, sq_fresh=4, sq_vol_ratio=1.25,
    poc_lookback=100, poc_bins=24, poc_min_dist=0.2, poc_score=15, poc_sl_buffer=0.2, poc_sl_min=0.5,
    ai_min_score=60, ai_sent_veto=75,
)
_SCALP = dict(
    st_period=7, st_mult=1.5, ema_fast=9, ema_slow=21, rsi_period=7, rsi_ob=70, rsi_os=30,
    srsi_rsi=7, srsi_stoch=7, srsi_k=2, srsi_d=2, obv_ma=10, adx_period=7, adx_min=20, atr_period=7,
    sl_mult=0.8, tp1_mult=0.8, tp2_mult=1.5, tp3_mult=2.5, min_atr_pct=0.3, vol_ma=10, vol_ratio=1.2,
    cd_min_score=50, cd_lookback=3, ai_min_score=55, ai_sent_veto=70,
)

BUILTIN: List[Dict[str, Any]] = [
    {
        "key": "pump_dump",
        "name": "پامپ و دامپ (Pump & Dump)",
        "description": "شکار پامپ (LONG) و شکار دامپ (SHORT) به‌صورت همزمان. تایم‌فریم: ورود 15m، تأیید 1h.",
        "long": _profile({"st", "ema", "rsi", "obv", "adx", "atr", "vol", "cd", "poc", "lq", "sm", "mb", "ai"}, _PUMP_LONG, ("15m", "1h")),
        "short": _profile(
            {"st", "ema", "rsi", "obv", "adx", "atr", "vol", "div", "cd", "sr", "poc", "lq", "sm", "mb", "ai"}, _PUMP_SHORT, ("15m", "1h"),
        ),
    },
    {
        "key": "breakout",
        "name": "شکار انفجار (Breakout Hunter)",
        "description": "قبل از شکست ناگهانی (صعودی یا نزولی) و وقتی بازار فشرده شده سیگنال می‌دهد. تایم‌فریم: ورود 15m، تأیید 1h.",
        "long": _profile({"st", "ema", "rsi", "srsi", "obv", "adx", "atr", "vol", "sq", "poc", "fvg", "sm", "mb", "ai"}, _BREAKOUT, ("15m", "1h")),
        "short": _profile({"st", "ema", "rsi", "srsi", "obv", "adx", "atr", "vol", "sq", "poc", "fvg", "sm", "mb", "ai"}, _BREAKOUT, ("15m", "1h")),
    },
    {
        "key": "scalp",
        "name": "اسکالپ سریع (Fast Scalp)",
        "description": "حرکت‌های کوچک و سریع (۱۰ تا ۳۰ دقیقه). تایم‌فریم: ورود 5m، تأیید 15m.",
        "long": _profile({"st", "ema", "rsi", "srsi", "obv", "adx", "atr", "vol", "cd", "lq", "sm", "ai"}, _SCALP, ("5m", "15m")),
        "short": _profile({"st", "ema", "rsi", "srsi", "obv", "adx", "atr", "vol", "cd", "lq", "sm", "ai"}, _SCALP, ("5m", "15m")),
    },
]


# ----------------------------------------------------------------------------
# اعتبارسنجی و ساخت پروفایل کامل
# ----------------------------------------------------------------------------
def full_profile(stored: Dict[str, Any]) -> Dict[str, str]:
    """
    مقدارهای کامل یک جهت: همه‌ی کلیدهای موتور به پیش‌فرض برمی‌گردند (به‌جز تنظیمات عملیاتی AI) و بعد
    مقدارهای خود استراتژی روی آن‌ها قرار می‌گیرد.
    """
    full: Dict[str, str] = {}
    for k in config.ENGINE_KEYS:
        if k in config.AI_EXTRA_KEYS:
            continue
        spec = config.PARAMS.get(k)
        if spec is not None and spec.group == "ai":
            continue
        full[k] = config.DEFAULT_SETTINGS[k]
    # تنظیمات عملیاتی AI (مدل، قابلیت‌ها، سقف مصرف) با فعال‌سازی استراتژی ذخیره‌شده بازنویسی نمی‌شوند
    keep = config.AI_EXTRA_KEYS | {"ai_daily_max", "ai_interval", "ai_max_len"}
    full.update({
        k: str(v) for k, v in (stored.get("settings") or {}).items() if config.is_engine_key(k) and k not in keep
    })
    return full


def validate_profile(full: Dict[str, str], label: str) -> List[str]:
    errors: List[str] = []

    def getter(k: str) -> float:
        raw = full.get(k)
        if raw is None:
            raw = config.DEFAULT_SETTINGS.get(k, "0")
        try:
            return float(raw)
        except (TypeError, ValueError):
            return float(config.DEFAULT_SETTINGS.get(k, "0") or 0)

    for k, v in full.items():
        spec = config.PARAMS.get(k)
        if spec is None:
            continue
        val = config.parse_number(spec, str(v))
        if val is None:
            errors.append(f"{label}/{k}: مقدار نامعتبر ({v}).")
            continue
        err = config.validate_param(k, float(val), getter)
        if err:
            errors.append(f"{label}/{k}: {err}")
    on_st = full.get("en_supertrend", "0") in ("1", "true", "True")
    on_ema = full.get("en_ema", "0") in ("1", "true", "True")
    if not on_st and not on_ema:
        errors.append(f"{label}: حداقل یکی از Supertrend یا EMA باید روشن باشد.")
    return errors


def validate_strategy(long_p: Dict[str, Any], short_p: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    for label, p in (("LONG", long_p), ("SHORT", short_p)):
        if p.get("tf_entry") not in config.ENTRY_TF_OPTIONS:
            errors.append(f"{label}: تایم‌فریم ورود نامعتبر ({p.get('tf_entry')}).")
        if p.get("tf_final") not in config.FINAL_TF_OPTIONS:
            errors.append(f"{label}: تایم‌فریم تأیید نامعتبر ({p.get('tf_final')}).")
        errors += validate_profile(full_profile(p), label)
    return errors


# ----------------------------------------------------------------------------
# دیتابیس
# ----------------------------------------------------------------------------
def seed_builtin() -> None:
    """استراتژی‌های پیش‌فرض اگر در دیتابیس نباشند اضافه می‌شوند."""
    for s in BUILTIN:
        if db.q1("SELECT id FROM strategies WHERE strategy_key = ?", (s["key"],)):
            continue
        db.x(
            "INSERT INTO strategies(strategy_key, strategy_name, description, long_settings_json, "
            "short_settings_json, is_active, is_builtin) VALUES(?, ?, ?, ?, ?, 0, 1)",
            (s["key"], s["name"], s["description"], json.dumps(s["long"], ensure_ascii=False),
             json.dumps(s["short"], ensure_ascii=False)),
        )


def list_all() -> List[Dict[str, Any]]:
    return db.q("SELECT * FROM strategies ORDER BY is_builtin DESC, id")


def get(key: str) -> Optional[Dict[str, Any]]:
    return db.q1("SELECT * FROM strategies WHERE strategy_key = ?", (key,))


def active_key() -> str:
    r = db.q1("SELECT strategy_key FROM strategies WHERE is_active = 1 ORDER BY id LIMIT 1")
    return (r or {}).get("strategy_key") or ""


def _load(row: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    return json.loads(row["long_settings_json"] or "{}"), json.loads(row["short_settings_json"] or "{}")


def activate(key: str) -> Tuple[bool, str]:
    """تنظیمات استراتژی را روی ربات اعمال می‌کند (حالت جداگانه: LONG و SHORT). همه‌چیز اول بررسی می‌شود."""
    row = get(key)
    if row is None:
        return False, "این استراتژی پیدا نشد."
    try:
        long_p, short_p = _load(row)
    except (ValueError, TypeError):
        return False, "تنظیمات این استراتژی خراب است."
    errors = validate_strategy(long_p, short_p)
    if errors:
        return False, "تنظیمات استراتژی معتبر نیست و هیچ تغییری اعمال نشد:\n" + "\n".join("- " + e for e in errors[:8])
    db.set("eng_mode", "separate")
    db.ensure_side_profiles()
    items: List[Tuple[str, str, Optional[str]]] = []
    for side, p in (("LONG", long_p), ("SHORT", short_p)):
        for k, v in full_profile(p).items():
            items.append((k, v, side))
    for side, p in (("LONG", long_p), ("SHORT", short_p)):       # هر جهت تایم‌فریم مستقل خودش را دارد
        items += [
            ("tf_entry", p["tf_entry"], side), ("tf_final", p["tf_final"], side),
            ("tf_entry_on", "1", side), ("tf_final_on", "1", side),
        ]
    db.set_many_e(items)
    db.set_many({
        "tf_entry": long_p["tf_entry"], "tf_final": long_p["tf_final"],
        "tf_entry_on": "1", "tf_final_on": "1", "active_strategy": key,
    })
    db.x("UPDATE strategies SET is_active = 0")
    db.x("UPDATE strategies SET is_active = 1 WHERE strategy_key = ?", (key,))
    return True, f"استراتژی «{row['strategy_name']}» روی هر دو جهت LONG و SHORT اعمال شد."


def save_current(name: str, description: str = "") -> Tuple[bool, str]:
    """تنظیمات فعلی موتورها (LONG و SHORT) به‌عنوان یک استراتژی سفارشی ذخیره می‌شود."""
    name = " ".join((name or "").split())
    if not 2 <= len(name) <= 40:
        return False, "نام استراتژی باید بین ۲ تا ۴۰ کاراکتر باشد."
    if db.q1("SELECT id FROM strategies WHERE strategy_name = ?", (name,)):
        return False, "یک استراتژی با این نام وجود دارد."
    key = f"custom_{int(time.time())}"
    profiles = {}
    for side in ("LONG", "SHORT"):
        profiles[side] = {
            "tf_entry": db.get_e("tf_entry", side),
            "tf_final": db.get_e("tf_final", side),
            "settings": {k: db.get_e(k, side) for k in sorted(config.ENGINE_KEYS)},
        }
    db.x(
        "INSERT INTO strategies(strategy_key, strategy_name, description, long_settings_json, short_settings_json, "
        "is_active, is_builtin) VALUES(?, ?, ?, ?, ?, 0, 0)",
        (key, name, description or "استراتژی سفارشی (ذخیره‌شده از تنظیمات فعلی)",
         json.dumps(profiles["LONG"], ensure_ascii=False), json.dumps(profiles["SHORT"], ensure_ascii=False)),
    )
    return True, key


def delete(key: str) -> Tuple[bool, str]:
    row = get(key)
    if row is None:
        return False, "این استراتژی پیدا نشد."
    if int(row.get("is_builtin") or 0):
        return False, "استراتژی‌های پیش‌فرض قابل حذف نیستند."
    db.x("DELETE FROM strategies WHERE strategy_key = ? AND is_builtin = 0", (key,))
    if db.get("active_strategy") == key:
        db.set("active_strategy", "")
    return True, "استراتژی حذف شد."


def summary(row: Dict[str, Any]) -> str:
    """خلاصه‌ی متنی هر جهت: موتورهای فعال + تایم‌فریم + چند پارامتر مهم."""
    try:
        long_p, short_p = _load(row)
    except (ValueError, TypeError):
        return "تنظیمات خراب است."
    lines: List[str] = []
    for label, p in (("LONG", long_p), ("SHORT", short_p)):
        st = p.get("settings") or {}
        on = [t for eid, (t, k) in config.ENGINES.items() if st.get(k) in ("1", "true", "True")]
        off = [t for eid, (t, k) in config.ENGINES.items() if st.get(k) not in ("1", "true", "True")]
        lines.append(f"{label} | ورود {p.get('tf_entry', '-')} / تأیید {p.get('tf_final', '-')}")
        lines.append("فعال: " + "، ".join(on))
        if off:
            lines.append("غیرفعال: " + "، ".join(off))
        lines.append("")
    return "\n".join(lines).strip()
