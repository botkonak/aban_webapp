"""
Import / Export تنظیمات موتورها (فایل JSON).

ساختار فایل:
  {
    "version": 2,
    "mode": "global" | "separate",
    "settings": { ... }            # فقط در حالت مشترک (یک بخش)
    "long": { ... }, "short": { ... }   # فقط در حالت جداگانه (دو بخش)
  }
  هر بخش: { "<engine>": {"enabled": true, "params": {...}} ... }
  موتور candlestick علاوه بر params، بخش "patterns" و موتور ai بخش‌های "model" و "features" هم دارند.

Import همه‌چیز را اول بررسی می‌کند؛ اگر حتی یک مورد نامعتبر باشد هیچ تغییری اعمال نمی‌شود.
"""
from __future__ import annotations

import json
from typing import Dict, List, Optional, Tuple

import config
from database import db

FORMAT_VERSION = 2
MAX_FILE_BYTES = 200 * 1024

JSON_TO_ENGINE: Dict[str, str] = {v: k for k, v in config.ENGINE_JSON.items()}
FEATURE_JSON = {"sent": "sentiment", "confirm": "confirm", "explain": "explain"}
JSON_TO_FEATURE = {v: k for k, v in FEATURE_JSON.items()}


def _num(raw: str, kind: str):
    try:
        v = float(raw)
    except (TypeError, ValueError):
        v = 0.0
    return int(round(v)) if kind == "int" else v


def _bool(raw: str) -> bool:
    return str(raw) in ("1", "true", "True")


def _src(d):
    return db if d is None else d


def _is_user(d) -> bool:
    return d is not None and d is not db


def _engine_body(eid: str, side: Optional[str], d=None) -> dict:
    """تنظیمات فعلی یک موتور (برای یک جهت یا حالت مشترک)."""
    d = _src(d)
    _, key = config.ENGINES[eid]
    body: dict = {"enabled": d.get_e_bool(key, side)}
    body["params"] = {
        s.key: _num(d.get_e(s.key, side), s.kind) for s in config.PARAM_SPECS if s.group == eid
    }
    if eid == "cd":
        body["patterns"] = {pid: d.get_e_bool(f"cdp_{pid}", side) for pid in config.CANDLE_PATTERNS}
    if eid == "ai":
        body["model"] = d.get_e("ai_model", side)
        body["features"] = {
            FEATURE_JSON[fid]: d.get_e_bool(skey, side) for fid, (skey, _t) in config.AI_FEATURES.items()
        }
    return body


def _profile(side: Optional[str], d=None) -> dict:
    d = _src(d)
    user = _is_user(d)
    # فایل کاربر بخش AI ندارد (کاربر فقط مدل را از داخل ربات انتخاب می‌کند)
    out = {
        config.ENGINE_JSON[eid]: _engine_body(eid, side, d)
        for eid in config.ENGINES if not (user and eid == "ai")
    }
    out["timeframes"] = {
        "entry": d.get_e("tf_entry", side),
        "final": d.get_e("tf_final", side),
        "entry_on": d.get_e_bool("tf_entry_on", side),
        "final_on": d.get_e_bool("tf_final_on", side),
    }
    return out


def export_settings(d=None) -> dict:
    d = _src(d)
    mode = d.eng_mode()
    out: dict = {"version": FORMAT_VERSION, "mode": mode}
    if mode == "separate":
        out["long"] = _profile("LONG", d)
        out["short"] = _profile("SHORT", d)
    else:
        out["settings"] = _profile(None, d)
    return out


def export_bytes(d=None) -> bytes:
    return json.dumps(export_settings(d), ensure_ascii=False, indent=2).encode("utf-8")


# ----------------------------------------------------------------------------
# Import
# ----------------------------------------------------------------------------
def _to_bool(v) -> Optional[bool]:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)) and v in (0, 1):
        return bool(v)
    if isinstance(v, str) and v.strip().lower() in ("true", "false", "1", "0"):
        return v.strip().lower() in ("true", "1")
    return None


def _parse_timeframes(body, label: str, errors: List[str], changes: Dict[str, str]) -> None:
    if not isinstance(body, dict):
        errors.append(f"{label}/timeframes: باید یک آبجکت باشد.")
        return
    for k, v in body.items():
        if str(k).startswith("_"):
            continue
        if k == "entry":
            if v in config.ENTRY_TF_OPTIONS:
                changes["tf_entry"] = v
            else:
                errors.append(f"{label}/timeframes/entry: فقط یکی از " + " ".join(config.ENTRY_TF_OPTIONS))
        elif k == "final":
            if v in config.FINAL_TF_OPTIONS:
                changes["tf_final"] = v
            else:
                errors.append(f"{label}/timeframes/final: فقط یکی از " + " ".join(config.FINAL_TF_OPTIONS))
        elif k in ("entry_on", "final_on"):
            b = _to_bool(v)
            if b is None:
                errors.append(f"{label}/timeframes/{k}: فقط true یا false.")
            else:
                changes["tf_entry_on" if k == "entry_on" else "tf_final_on"] = "1" if b else "0"
        else:
            errors.append(f"{label}/timeframes: کلید ناشناخته «{k}».")


def _parse_profile(
    section, label: str, side: Optional[str], errors: List[str], user: bool = False,
) -> Dict[str, str]:
    """بخش یک پروفایل را بررسی می‌کند و {کلید تنظیم: مقدار} برمی‌گرداند."""
    changes: Dict[str, str] = {}
    if not isinstance(section, dict):
        errors.append(f"{label}: باید یک آبجکت باشد.")
        return changes
    for name, body in section.items():
        if str(name).startswith("_"):
            continue
        if name == "timeframes":
            _parse_timeframes(body, label, errors, changes)
            continue
        if user and name == "ai":
            continue          # کاربر اجازه‌ی تغییر تنظیمات AI را ندارد
        eid = JSON_TO_ENGINE.get(name)
        if eid is None:
            errors.append(f"{label}: موتور ناشناخته «{name}».")
            continue
        if not isinstance(body, dict):
            errors.append(f"{label}/{name}: باید یک آبجکت باشد.")
            continue
        _, tkey = config.ENGINES[eid]
        allowed = {"enabled", "params"}
        if eid == "cd":
            allowed |= {"patterns"}
        if eid == "ai":
            allowed |= {"model", "features"}
        for k in body:
            if not str(k).startswith("_") and k not in allowed:
                errors.append(f"{label}/{name}: بخش ناشناخته «{k}».")
        if "enabled" in body:
            b = _to_bool(body["enabled"])
            if b is None:
                errors.append(f"{label}/{name}/enabled: فقط true یا false.")
            else:
                changes[tkey] = "1" if b else "0"
        params = body.get("params", {})
        if not isinstance(params, dict):
            errors.append(f"{label}/{name}/params: باید یک آبجکت باشد.")
            params = {}
        for pk, pv in params.items():
            spec = config.PARAMS.get(pk)
            if spec is None or spec.group != eid:
                errors.append(f"{label}/{name}: پارامتر ناشناخته «{pk}».")
                continue
            if isinstance(pv, bool) or not isinstance(pv, (int, float, str)):
                errors.append(f"{label}/{name}/{pk}: باید عدد باشد.")
                continue
            val = config.parse_number(spec, str(pv))
            if val is None:
                errors.append(f"{label}/{name}/{pk}: عدد معتبر نیست ({pv}).")
                continue
            changes[pk] = str(int(val)) if spec.kind == "int" else config.fmt_number(float(val))
        if eid == "cd" and "patterns" in body:
            pats = body["patterns"]
            if not isinstance(pats, dict):
                errors.append(f"{label}/{name}/patterns: باید یک آبجکت باشد.")
            else:
                for pid, pv in pats.items():
                    if pid not in config.CANDLE_PATTERNS:
                        errors.append(f"{label}/{name}: الگوی ناشناخته «{pid}».")
                        continue
                    b = _to_bool(pv)
                    if b is None:
                        errors.append(f"{label}/{name}/patterns/{pid}: فقط true یا false.")
                    else:
                        changes[f"cdp_{pid}"] = "1" if b else "0"
        if eid == "ai":
            if "model" in body:
                m = body["model"]
                if not isinstance(m, str) or not m.strip() or len(m.strip()) > 100 or any(c.isspace() for c in m.strip()):
                    errors.append(f"{label}/{name}/model: اسم مدل نامعتبر است.")
                else:
                    changes["ai_model"] = m.strip()
            if "features" in body:
                feats = body["features"]
                if not isinstance(feats, dict):
                    errors.append(f"{label}/{name}/features: باید یک آبجکت باشد.")
                else:
                    for fk, fv in feats.items():
                        fid = JSON_TO_FEATURE.get(fk)
                        if fid is None:
                            errors.append(f"{label}/{name}: قابلیت ناشناخته «{fk}».")
                            continue
                        b = _to_bool(fv)
                        if b is None:
                            errors.append(f"{label}/{name}/features/{fk}: فقط true یا false.")
                        else:
                            changes[config.AI_FEATURES[fid][0]] = "1" if b else "0"
    return changes


def _cross_check(changes: Dict[str, str], label: str, current, errors: List[str]) -> None:
    """بررسی بازه‌ها و روابط بین پارامترها (ترکیب مقادیر جدید و فعلی)."""
    def getter(k: str) -> float:
        raw = changes.get(k)
        if raw is None:
            raw = current(k)
        try:
            return float(raw)
        except (TypeError, ValueError):
            return float(config.DEFAULT_SETTINGS.get(k, "0") or 0)

    for key in [k for k in changes if k in config.PARAMS]:
        err = config.validate_param(key, float(changes[key]), getter)
        if err:
            errors.append(f"{label}/{key}: {err}")
    en_st = changes.get("en_supertrend", current("en_supertrend"))
    en_ema = changes.get("en_ema", current("en_ema"))
    if not _bool(en_st) and not _bool(en_ema):
        errors.append(f"{label}: حداقل یکی از Supertrend یا EMA باید روشن بماند (جهت سیگنال از آن‌ها می‌آید).")
    if any(k in changes for k in ("tf_entry_on", "tf_final_on")):
        e_on = changes.get("tf_entry_on", current("tf_entry_on"))
        f_on = changes.get("tf_final_on", current("tf_final_on"))
        if not _bool(e_on) and not _bool(f_on):
            errors.append(f"{label}: حداقل یکی از تایم‌فریم‌های ورود یا تأیید باید فعال بماند.")


def import_settings(raw: bytes, d=None) -> Tuple[bool, str]:
    """خروجی: (موفق؟، پیام). هر خطا یعنی هیچ تغییری اعمال نشده است. d: تنظیمات ادمین (پیش‌فرض) یا یک کاربر."""
    d = _src(d)
    user = _is_user(d)
    if len(raw) > MAX_FILE_BYTES:
        return False, "فایل بسیار بزرگ است (حداکثر ۲۰۰ کیلوبایت)."
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError):
        return False, "فایل JSON معتبر نیست. ساختار فایل Export را دست نزنید و فقط مقدارها را عوض کنید."
    if not isinstance(data, dict):
        return False, "ساختار فایل درست نیست (باید یک آبجکت باشد)."

    mode = data.get("mode")
    if mode not in (None, "global", "separate"):
        return False, "مقدار mode فقط می‌تواند global یا separate باشد."
    if mode is None:
        mode = "separate" if ("long" in data or "short" in data) else "global"

    errors: List[str] = []
    profiles: Dict[Optional[str], Dict[str, str]] = {}
    if mode == "global":
        if "settings" not in data:
            return False, "بخش settings در فایل پیدا نشد (حالت مشترک یک بخش settings دارد)."
        profiles[None] = _parse_profile(data["settings"], "settings", None, errors, user)
        _cross_check(profiles[None], "settings", lambda k: d.get(k), errors)
    else:
        if "long" not in data and "short" not in data:
            return False, "در حالت جداگانه فایل باید بخش long و short داشته باشد."
        for sec, side in (("long", "LONG"), ("short", "SHORT")):
            if sec in data:
                ch = _parse_profile(data[sec], sec, side, errors, user)
                profiles[side] = ch
                _cross_check(ch, sec, lambda k, s=side: d.get_profile(k, s), errors)
    if errors:
        shown = errors[:12]
        extra = f"\n... و {len(errors) - 12} خطای دیگر" if len(errors) > 12 else ""
        return False, "فایل خطا دارد و هیچ تغییری اعمال نشد:\n" + "\n".join("- " + e for e in shown) + extra

    applied = 0
    note = ""
    if mode == "separate":
        d.set("eng_mode", "separate")
        d.ensure_side_profiles()
        long_ai = {k: v for k, v in profiles.get("LONG", {}).items() if k in config.AI_KEYS}
        short_ai = {k: v for k, v in profiles.get("SHORT", {}).items() if k in config.AI_KEYS}
        if not user and not d.get_bool("ai_sep") and long_ai and short_ai and long_ai != short_ai:
            note = (
                "\nتنظیمات هوش مصنوعی اکنون مشترک است؛ مقدار بخش short اعمال شد. "
                "برای تنظیم جداگانه، در صفحه‌ی هوش مصنوعی گزینه‌ی «تنظیمات جداگانه» را روشن کنید و دوباره Import کنید."
            )
        for side in ("LONG", "SHORT"):
            for k, v in profiles.get(side, {}).items():
                d.set_e(k, v, side)
                applied += 1
    else:
        d.set("eng_mode", "global")
        for k, v in profiles[None].items():
            d.set(k, v)
            applied += 1
    label = "جداگانه (LONG و SHORT)" if mode == "separate" else "مشترک"
    return True, f"حالت تنظیمات: {label}\nتعداد مقدار اعمال‌شده: {applied}{note}"
