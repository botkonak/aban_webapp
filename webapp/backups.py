"""مدیریت فایل‌های بکاپ: ساخت کامل/بخشی، تاریخچه، دانلود، بازیابی و پاک‌سازی."""
from __future__ import annotations

import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import backup
import config
from database import db
from ui import TEHRAN

log = logging.getLogger("webapp.backups")

BACKUP_DIR = Path(config.BASE_DIR) / "data" / "backups"
BACKUP_DIR.mkdir(parents=True, exist_ok=True)
MAX_KEEP = 100


def _row(r: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if r is None:
        return None
    from webapp.jalali import jalali_from_db

    out = dict(r)
    out["size"] = int(float(out.get("size") or 0))
    try:
        import json

        tables = json.loads(out.get("tables_json") or "[]")
        out["tables"] = tables if isinstance(tables, list) else []
    except Exception:
        out["tables"] = []
    out["created_at_fa"] = jalali_from_db(out.get("created_at"))
    return out


def _new_filename(kind: str) -> str:
    stamp = datetime.now(TEHRAN).strftime("%Y-%m-%d_%H-%M-%S")
    suffix = "partial" if kind != "full" else "full"
    name = f"aban_trade_{suffix}_{stamp}.json"
    n = 1
    while (BACKUP_DIR / name).exists():
        n += 1
        name = f"aban_trade_{suffix}_{stamp}_{n}.json"
    return name


def _insert(filename: str, kind: str, tables: List[str], size: int, status: str, admin_uid: int) -> int:
    import json

    return db.insert_id(
        "INSERT INTO backup_history(filename, kind, tables_json, size, status, created_by) "
        "VALUES(?, ?, ?, ?, ?, ?)",
        (filename, kind, json.dumps(tables, ensure_ascii=False), int(size), status, int(admin_uid)),
    )


def create(admin_uid: int, sections: Optional[List[str]] = None) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    sections = [str(s) for s in (sections or []) if s]
    tables = list(backup.section_tables(sections)) if sections else list(backup.BACKUP_TABLES)
    if not tables:
        return False, "هیچ بخشی برای بکاپ انتخاب نشده است.", None
    try:
        raw = backup.export_bytes(tables)
    except Exception as exc:
        log.exception("backup export failed")
        return False, f"ساخت بکاپ انجام نشد: {str(exc)[:150]}", None
    kind = "full" if not sections else ",".join(sections)
    name = _new_filename(kind)
    path = BACKUP_DIR / name
    try:
        path.write_bytes(raw)
    except OSError as exc:
        return False, f"ذخیره‌ی فایل بکاپ انجام نشد: {exc}", None
    bid = _insert(name, kind, tables, len(raw), "ready", admin_uid)
    prune()
    return True, "بکاپ با موفقیت ساخته شد.", get(bid)


def get(backup_id: int) -> Optional[Dict[str, Any]]:
    return _row(db.q1("SELECT * FROM backup_history WHERE id = ?", (int(backup_id),)))


def rows(offset: int = 0, limit: int = 20) -> Tuple[List[Dict[str, Any]], int]:
    total = int(db.q1("SELECT COUNT(*) AS n FROM backup_history")["n"] or 0)
    data = db.q(
        "SELECT * FROM backup_history ORDER BY id DESC LIMIT ? OFFSET ?",
        (int(limit), int(offset)),
    )
    return [_row(r) for r in data], total


def path_of(row: Dict[str, Any]) -> Optional[Path]:
    name = Path(str(row.get("filename") or "")).name
    if not name:
        return None
    p = BACKUP_DIR / name
    return p if p.exists() else None


def delete(backup_id: int) -> bool:
    row = get(backup_id)
    if row is None:
        return False
    p = path_of(row)
    try:
        if p is not None:
            p.unlink()
    except OSError:
        log.warning("backup file delete failed: %s", p, exc_info=True)
    db.x("DELETE FROM backup_history WHERE id = ?", (int(backup_id),))
    return True


def restore_id(backup_id: int) -> Tuple[bool, str]:
    row = get(backup_id)
    if row is None:
        return False, "بکاپ پیدا نشد."
    p = path_of(row)
    if p is None:
        return False, "فایل این بکاپ روی سرور موجود نیست."
    try:
        raw = p.read_bytes()
    except OSError as exc:
        return False, f"خواندن فایل بکاپ انجام نشد: {exc}"
    ok, data = backup.validate(raw)
    if not ok:
        return False, str(data)
    return backup.restore(data)


def restore_upload(raw: bytes, admin_uid: int) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    ok, data = backup.validate(raw)
    if not ok:
        return False, str(data), None
    name = _new_filename("upload")
    try:
        (BACKUP_DIR / name).write_bytes(raw)
    except OSError as exc:
        return False, f"ذخیره‌ی فایل بکاپ انجام نشد: {exc}", None
    tables = list(data.keys())
    # ابتدا بازیابی انجام می‌شود؛ اگر بکاپ شامل backup_history باشد، ردیف همین آپلود
    # پیش از درج پاک می‌شد و تاریخچه ناقص می‌ماند. درج بعد از بازیابی این مشکل را ندارد.
    ok2, message = backup.restore(data)
    bid = _insert(name, "upload", tables, len(raw), "ready" if ok2 else "failed", admin_uid)
    prune()
    return ok2, message, get(bid)


def prune() -> None:
    try:
        keep = max(3, min(db.get_int("backup_keep") or 30, MAX_KEEP))
    except Exception:
        keep = 30
    old = db.q(
        "SELECT id, filename FROM backup_history ORDER BY id DESC LIMIT ? OFFSET ?", (1000, keep)
    )
    for row in old:
        p = BACKUP_DIR / Path(str(row.get("filename") or "")).name
        try:
            if p.exists():
                p.unlink()
        except OSError:
            pass
        db.x("DELETE FROM backup_history WHERE id = ?", (int(row["id"]),))


def auto_backup_due() -> bool:
    if not db.get_bool("backup_auto_on"):
        return False
    days = max(1, db.get_int("backup_auto_days") or 7)
    last = float(db.get("backup_auto_at") or 0)
    return time.time() - last >= days * 86400


def auto_backup() -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    ok, message, row = create(int(config.ADMIN_UID), None)
    if ok:
        db.set("backup_auto_at", str(time.time()))
    return ok, message, row


async def auto_loop() -> None:
    """هر ساعت بررسی می‌کند که بکاپ خودکار به‌موقع رسیده باشد یا نه."""
    import asyncio

    await asyncio.sleep(300)
    while True:
        try:
            if auto_backup_due():
                ok, message, _row = await asyncio.to_thread(auto_backup)
                if ok:
                    log.info("automatic backup created")
                else:
                    log.warning("automatic backup failed: %s", message)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("automatic backup loop failed")
        await asyncio.sleep(3600)
