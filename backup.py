"""
بکاپ و بازیابی دستی دیتابیس از پنل ادمین (آبان ترید).

  بکاپ:   همه‌ی جدول‌ها (تنظیمات، کاربران، قالب‌ها، کلیدهای AI، پشتیبانی، استراتژی‌ها، ...) -> یک فایل JSON
          با نام  aban_trade_backup_YYYY-MM-DD_HH-MM-SS.json  (تاریخ و ساعت تهران)
  بازیابی: فایل JSON بکاپ اول کامل بررسی می‌شود؛ بعد در یک تراکنش جدول‌های داخل فایل پاک و دوباره پر می‌شوند.
          اگر هر خطایی باشد هیچ تغییری اعمال نمی‌شود.
با DATABASE_URL هم روی SQLite و هم روی PostgreSQL کار می‌کند.
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Tuple

from database import BACKUP_TABLES, db, ts_str
from ui import TEHRAN

log = logging.getLogger("backup")

FORMAT = "aban-trade-backup"
VERSION = 1
MAX_FILE_BYTES = 256 * 1024 * 1024

# بکاپ بخشی: هر بخش جدول‌های مربوط به خودش را دارد (هم‌پوشانی مجاز است و حذف تکراری انجام می‌شود)
SECTIONS: Dict[str, Dict[str, Any]] = {
    "settings": {
        "label": "تنظیمات و قالب‌ها",
        "tables": (
            "settings", "templates", "message_templates", "symbol_filter", "force_channels",
            "registration_fields",
        ),
    },
    "users": {
        "label": "کاربران و دسترسی‌ها",
        "tables": (
            "users", "user_settings", "user_symbol_filter", "blocked_users",
            "webapp_users", "webapp_user_settings", "webapp_favorites", "free_sub_claims", "user_profiles",
        ),
    },
    "bot": {
        "label": "بخش ربات و سیگنال‌ها",
        "tables": (
            "blocks", "signal_history", "strategies", "signal_performance", "engine_suggestions",
            "analysis_requests", "price_channels", "ai_keys", "bsc_payments",
        ),
    },
    "webapp": {
        "label": "بخش تحلیل ساختاری",
        "tables": (
            "structural_strategies", "strategy_guides", "structural_signals", "search_history",
            "webapp_messages", "exchanges",
        ),
    },
    "support": {"label": "پشتیبانی", "tables": ("support_tickets", "support_messages")},
    "content": {
        "label": "مدیریت محتوا",
        "tables": ("pages", "posts", "categories", "menus", "menu_items", "media"),
    },
    "security": {
        "label": "امنیت و احراز هویت",
        "tables": ("security_events", "ip_blocks", "admin_audit_log", "otp_codes"),
    },
    "payments": {
        "label": "اشتراک‌ها و پرداخت‌ها",
        "tables": (
            "sub_requests", "payments_history", "payment_networks",
            "payment_cards", "payment_wallets",
        ),
    },
}


def section_tables(sections: List[str]) -> Tuple[str, ...]:
    wanted: List[str] = []
    for key in sections or []:
        spec = SECTIONS.get(str(key))
        if not spec:
            continue
        for t in spec["tables"]:
            if t in BACKUP_TABLES and t not in wanted:
                wanted.append(t)
    return tuple(t for t in BACKUP_TABLES if t in wanted)


def sections_public() -> List[Dict[str, Any]]:
    return [{"key": k, "label": v["label"], "count": len(v["tables"])} for k, v in SECTIONS.items()]


def backup_filename() -> str:
    return "aban_trade_backup_" + datetime.now(TEHRAN).strftime("%Y-%m-%d_%H-%M-%S") + ".json"


def _jsonable(v: Any) -> Any:
    if isinstance(v, datetime):
        return ts_str(v)
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (bytes, bytearray, memoryview)):
        return bytes(v).decode("utf-8", errors="replace")
    return v


def export_data(tables: Optional[List[str]] = None) -> Dict[str, Any]:
    """همه‌ی جدول‌ها یا فقط جدول‌های انتخاب‌شده را برمی‌گرداند."""
    selected = tuple(t for t in BACKUP_TABLES if t in set(tables)) if tables else BACKUP_TABLES
    out: Dict[str, List[Dict[str, Any]]] = {}
    for t in selected:
        rows = db.q(f"SELECT * FROM {t}")
        out[t] = [{k: _jsonable(v) for k, v in r.items()} for r in rows]
    return {
        "format": FORMAT,
        "version": VERSION,
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S") + " UTC",
        "database": db.dialect,
        "tables": out,
    }


def export_bytes(tables: Optional[List[str]] = None) -> bytes:
    return json.dumps(export_data(tables), ensure_ascii=False, indent=1).encode("utf-8")


def validate(raw: bytes) -> Tuple[bool, Any]:
    """فایل بکاپ را کامل بررسی می‌کند. خروجی: (موفق؟، دیکشنری جدول‌ها یا پیام خطا)."""
    if len(raw) > MAX_FILE_BYTES:
        return False, "فایل بسیار بزرگ است."
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError):
        return False, "فایل JSON معتبر نیست."
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        return False, "این فایل، بکاپ آبان ترید نیست."
    try:
        version = int(data.get("version") or 0)
    except (TypeError, ValueError):
        return False, "نسخه‌ی فایل بکاپ نامعتبر است."
    if version > VERSION:
        return False, "نسخه‌ی این بکاپ جدیدتر از نسخه‌ی ربات است."
    tables = data.get("tables")
    if not isinstance(tables, dict) or not tables:
        return False, "بخش tables در فایل پیدا نشد."
    errors: List[str] = []
    out: Dict[str, List[Dict[str, Any]]] = {}
    for t, rows in tables.items():
        if t not in BACKUP_TABLES:
            errors.append(f"جدول ناشناخته «{t}».")
            continue
        cols = set(db.table_columns(t))
        if not isinstance(rows, list):
            errors.append(f"{t}: باید لیست باشد.")
            continue
        clean: List[Dict[str, Any]] = []
        for i, r in enumerate(rows):
            if not isinstance(r, dict):
                errors.append(f"{t}[{i}]: باید آبجکت باشد.")
                break
            unknown = [k for k in r if k not in cols]
            if unknown:
                errors.append(f"{t}[{i}]: ستون ناشناخته «{unknown[0]}».")
                break
            clean.append(r)
        out[t] = clean
        if len(errors) >= 8:
            break
    if errors:
        return False, "فایل معتبر نیست و هیچ تغییری اعمال نشد:\n" + "\n".join("- " + e for e in errors[:8])
    return True, out


def summary(tables: Dict[str, List[Dict[str, Any]]]) -> str:
    return "\n".join(f"{t}: {len(rows)}" for t, rows in tables.items())


def restore(tables: Dict[str, List[Dict[str, Any]]]) -> Tuple[bool, str]:
    """جدول‌های فایل را در یک تراکنش جایگزین می‌کند (اگر خطایی شود هیچ تغییری اعمال نمی‌شود)."""
    order = [t for t in BACKUP_TABLES if t in tables]
    try:
        with db.transaction() as tx:
            for t in reversed(order):               # اول جدول‌های وابسته پاک می‌شوند
                tx.execute(f"DELETE FROM {t}")
            for t in order:
                groups: Dict[Tuple[str, ...], List[tuple]] = {}
                for r in tables[t]:
                    cols = tuple(sorted(r))
                    groups.setdefault(cols, []).append(tuple(r[c] for c in cols))
                for cols, vals in groups.items():
                    if not cols:
                        continue
                    q = ", ".join(f'"{c}"' if c == "until" else c for c in cols)
                    tx.executemany(f"INSERT INTO {t}({q}) VALUES({', '.join('?' * len(cols))})", vals)
    except Exception as e:
        log.exception("restore failed")
        return False, f"بازیابی انجام نشد و هیچ تغییری اعمال نشد: {str(e)[:150]}"
    try:
        db.reset_sequences()
        db.reload_caches()
    except Exception as e:
        log.exception("reload after restore failed")
        return False, f"اطلاعات بکاپ جایگزین شد اما بارگذاری دوباره‌ی تنظیمات ناموفق بود؛ ربات را ری‌استارت کنید: {str(e)[:150]}"
    try:
        import strategy_manager

        strategy_manager.seed_builtin()
    except Exception:
        log.exception("seed strategies after restore failed")
    return True, "بازیابی با موفقیت انجام شد."
