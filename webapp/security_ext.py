"""توسعه‌ی امنیت وب‌اپ: رویدادها، بلاک IP و کاربر، لاگ تغییرات مدیریت و نمای کلی."""
from __future__ import annotations

import ipaddress
import json
import logging
import time
from typing import Any, Callable, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

import admin_notify
from database import db
from webapp import security
from webapp.jalali import jalali_from_db

log = logging.getLogger("webapp.security_ext")


class BlockIpBody(BaseModel):
    ip: str = ""
    reason: str = ""
    hours: float = 24


class BlockUserBody(BaseModel):
    user_id: int = 0
    reason: str = ""
    hours: float = 24


class ResolveBody(BaseModel):
    resolved: bool = True


def log_audit(
    admin_id: int, action: str, *, target_type: str = "", target_id: Any = "",
    details: Any = "", ip: str = "",
) -> None:
    try:
        if isinstance(details, (dict, list)):
            details = json.dumps(details, ensure_ascii=False)
        db.x(
            "INSERT INTO admin_audit_log(admin_id, action, target_type, target_id, details, ip) "
            "VALUES(?, ?, ?, ?, ?, ?)",
            (
                int(admin_id or 0), str(action or "action")[:120], str(target_type or "")[:60],
                str(target_id or "")[:80], str(details or "")[:2000], str(ip or "")[:64],
            ),
        )
    except Exception:
        log.exception("audit log failed")


def _valid_ip(raw: str) -> str:
    value = str(raw or "").strip()
    if not value:
        raise HTTPException(400, "آدرس IP را وارد کنید.")
    try:
        ipaddress.ip_address(value)
    except ValueError:
        raise HTTPException(400, "آدرس IP معتبر نیست.") from None
    return value


def _event_row(row: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(row)
    out["resolved"] = bool(out.get("resolved") or 0)
    out["severity"] = out.get("severity") or "info"
    out["created_at_fa"] = jalali_from_db(out.get("created_at"))
    return out


def register(app: FastAPI, *, require_admin: Callable[[Request], Dict[str, Any]], client_ip: Callable[[Request], str]) -> None:
    _require_admin = require_admin
    _ip = client_ip

    # ---------------------------- نمای کلی ----------------------------
    @app.get("/api/admin/security/overview")
    async def security_overview(request: Request):
        _require_admin(request)
        now = time.time()
        total_events = int(db.q1("SELECT COUNT(*) AS n FROM security_events")["n"] or 0)
        unresolved = int(db.q1("SELECT COUNT(*) AS n FROM security_events WHERE resolved = 0")["n"] or 0)
        critical = int(db.q1(
            "SELECT COUNT(*) AS n FROM security_events WHERE severity = 'critical' AND resolved = 0"
        )["n"] or 0)
        warning = int(db.q1(
            "SELECT COUNT(*) AS n FROM security_events WHERE severity = 'warning' AND resolved = 0"
        )["n"] or 0)
        active_blocks = int(db.q1(
            "SELECT COUNT(*) AS n FROM ip_blocks WHERE blocked_until > ?", (now,)
        )["n"] or 0)
        blocked_users = int(db.q1(
            "SELECT COUNT(*) AS n FROM blocked_users WHERE \"until\" > ?", (now,)
        )["n"] or 0)
        audit_count = int(db.q1("SELECT COUNT(*) AS n FROM admin_audit_log")["n"] or 0)
        recent = [_event_row(r) for r in db.q(
            "SELECT * FROM security_events ORDER BY id DESC LIMIT 12"
        )]
        return {
            "events": {
                "total": total_events, "unresolved": unresolved,
                "critical": critical, "warning": warning,
            },
            "blocks": {"ip": active_blocks, "users": blocked_users},
            "audit": audit_count,
            "limiter": security.stats(),
            "web": security.settings(),
            "recent": recent,
        }

    # ---------------------------- رویدادها ----------------------------
    @app.get("/api/admin/security/events")
    async def security_events(
        request: Request, page: int = 1, page_size: int = 30,
        severity: str = "all", resolved: str = "all", q: str = "",
    ):
        _require_admin(request)
        page = max(1, int(page))
        page_size = max(5, min(int(page_size), 100))
        where: List[str] = []
        params: List[Any] = []
        if severity in ("info", "warning", "critical"):
            where.append("severity = ?")
            params.append(severity)
        if resolved in ("0", "1"):
            where.append("resolved = ?")
            params.append(int(resolved))
        if q.strip():
            like = "%" + q.strip().lower() + "%"
            where.append(
                "(LOWER(kind) LIKE ? OR LOWER(detail) LIKE ? OR LOWER(ip) LIKE ? OR CAST(user_id AS TEXT) LIKE ?)"
            )
            params += [like, like, like, like]
        cond = (" WHERE " + " AND ".join(where)) if where else ""
        total = int(db.q1("SELECT COUNT(*) AS n FROM security_events" + cond, params)["n"] or 0)
        rows = db.q(
            "SELECT * FROM security_events" + cond + " ORDER BY id DESC LIMIT ? OFFSET ?",
            params + [page_size, (page - 1) * page_size],
        )
        return {"items": [_event_row(r) for r in rows], "total": total, "page": page, "page_size": page_size}

    @app.post("/api/admin/security/events/{eid}/resolve")
    async def security_event_resolve(eid: int, body: ResolveBody, request: Request):
        auth = _require_admin(request)
        cur = db.x(
            "UPDATE security_events SET resolved = ? WHERE id = ?",
            (1 if body.resolved else 0, int(eid)),
        )
        if cur.rowcount <= 0:
            raise HTTPException(404, "رویداد پیدا نشد.")
        log_audit(int(auth["uid"]), "resolve_security_event", target_type="security_event", target_id=eid,
                  details={"resolved": bool(body.resolved)}, ip=_ip(request))
        return {"ok": True, "message": "وضعیت رویداد ذخیره شد."}

    # ---------------------------- بلاک IP ----------------------------
    @app.get("/api/admin/security/ip-blocks")
    async def ip_blocks(request: Request, q: str = ""):
        _require_admin(request)
        where, params = "", []
        if q.strip():
            like = "%" + q.strip().lower() + "%"
            where = " WHERE LOWER(ip) LIKE ? OR LOWER(reason) LIKE ?"
            params = [like, like]
        rows = db.q("SELECT * FROM ip_blocks" + where + " ORDER BY blocked_until DESC", params)
        now = time.time()
        for row in rows:
            row["until"] = float(row.get("blocked_until") or 0)
            row["active"] = row["until"] > now
            row["seconds_left"] = max(0, int(row["until"] - now))
            row["created_at_fa"] = jalali_from_db(row.get("created_at"))
        return {"items": rows, "total": len(rows)}

    def _block_until(hours: float) -> float:
        try:
            h = float(hours or 0)
        except (TypeError, ValueError):
            h = 24
        h = max(0.01, min(h, 24 * 365))
        return time.time() + h * 3600

    @app.post("/api/admin/security/ip-blocks")
    async def ip_block_create(body: BlockIpBody, request: Request):
        auth = _require_admin(request)
        ip = _valid_ip(body.ip)
        until = _block_until(body.hours)
        reason = str(body.reason or "").strip()[:300]
        db.x(
            "INSERT INTO ip_blocks(ip, reason, blocked_until) VALUES(?, ?, ?) "
            "ON CONFLICT(ip) DO UPDATE SET reason = excluded.reason, blocked_until = excluded.blocked_until",
            (ip, reason, until),
        )
        admin_notify.log_event(0, "ip_block_admin", f"{ip} — {reason}", _ip(request), "warning")
        log_audit(int(auth["uid"]), "block_ip", target_type="ip", target_id=ip,
                  details={"hours": body.hours, "reason": reason}, ip=_ip(request))
        return {"ok": True, "message": "آدرس IP مسدود شد."}

    @app.put("/api/admin/security/ip-blocks/{bid}")
    async def ip_block_update(bid: int, body: BlockIpBody, request: Request):
        auth = _require_admin(request)
        row = db.q1("SELECT * FROM ip_blocks WHERE id = ?", (int(bid),))
        if row is None:
            raise HTTPException(404, "بلاک IP پیدا نشد.")
        ip = _valid_ip(body.ip or row.get("ip"))
        reason = str(body.reason or row.get("reason") or "").strip()[:300]
        until = _block_until(body.hours)
        clash = db.q1("SELECT id FROM ip_blocks WHERE ip = ? AND id != ? LIMIT 1", (ip, int(bid)))
        if clash is not None:
            raise HTTPException(409, "این IP از قبل در فهرست بلاک وجود دارد.")
        db.x("UPDATE ip_blocks SET ip = ?, reason = ?, blocked_until = ? WHERE id = ?", (ip, reason, until, int(bid)))
        log_audit(int(auth["uid"]), "update_ip_block", target_type="ip", target_id=ip,
                  details={"hours": body.hours, "reason": reason}, ip=_ip(request))
        return {"ok": True, "message": "بلاک IP به‌روزرسانی شد."}

    @app.delete("/api/admin/security/ip-blocks/{bid}")
    async def ip_block_delete(bid: int, request: Request):
        auth = _require_admin(request)
        row = db.q1("SELECT * FROM ip_blocks WHERE id = ?", (int(bid),))
        if row is None:
            raise HTTPException(404, "بلاک IP پیدا نشد.")
        db.x("DELETE FROM ip_blocks WHERE id = ?", (int(bid),))
        security.limiter.clear(str(row.get("ip") or ""))
        log_audit(int(auth["uid"]), "unblock_ip", target_type="ip", target_id=row.get("ip"), ip=_ip(request))
        return {"ok": True, "message": "بلاک IP برداشته شد."}

    # ---------------------------- بلاک کاربر ----------------------------
    @app.get("/api/admin/security/user-blocks")
    async def user_blocks(request: Request):
        _require_admin(request)
        rows = db.q(
            "SELECT b.*, u.first_name, u.last_name, u.username AS tg_username, u.phone_number "
            "FROM blocked_users b LEFT JOIN users u ON u.user_id = b.user_id ORDER BY b.\"until\" DESC"
        )
        now = time.time()
        for row in rows:
            row["until"] = float(row.get("until") or 0)
            row["active"] = row["until"] > now
            row["seconds_left"] = max(0, int(row["until"] - now))
            row["created_at_fa"] = jalali_from_db(row.get("created_at"))
        return {"items": rows, "total": len(rows)}

    @app.post("/api/admin/security/user-blocks")
    async def user_block_create(body: BlockUserBody, request: Request):
        auth = _require_admin(request)
        uid = int(body.user_id or 0)
        # کاربران وب‌اپ شناسه‌ی منفی دارند؛ فقط صفر شناسه‌ی نامعتبر است.
        if uid == 0 or db.get_user(uid) is None:
            raise HTTPException(404, "کاربر پیدا نشد.")
        until = _block_until(body.hours)
        reason = str(body.reason or "").strip()[:300]
        db.x(
            "INSERT INTO blocked_users(user_id, \"until\", reason) VALUES(?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET \"until\" = excluded.\"until\", reason = excluded.reason",
            (uid, until, reason),
        )
        log_audit(int(auth["uid"]), "block_user", target_type="user", target_id=uid,
                  details={"hours": body.hours, "reason": reason}, ip=_ip(request))
        return {"ok": True, "message": "کاربر مسدود شد."}

    @app.delete("/api/admin/security/user-blocks/{uid}")
    async def user_block_delete(uid: int, request: Request):
        auth = _require_admin(request)
        db.x("DELETE FROM blocked_users WHERE user_id = ?", (int(uid),))
        log_audit(int(auth["uid"]), "unblock_user", target_type="user", target_id=uid, ip=_ip(request))
        return {"ok": True, "message": "بلاک کاربر برداشته شد."}

    # ---------------------------- لاگ تغییرات مدیریت ----------------------------
    @app.get("/api/admin/audit-log")
    async def audit_log(request: Request, page: int = 1, page_size: int = 30, q: str = ""):
        _require_admin(request)
        page = max(1, int(page))
        page_size = max(5, min(int(page_size), 100))
        where, params = "", []
        if q.strip():
            like = "%" + q.strip().lower() + "%"
            where = " WHERE LOWER(action) LIKE ? OR LOWER(target_type) LIKE ? OR LOWER(details) LIKE ? OR LOWER(ip) LIKE ?"
            params = [like, like, like, like]
        total = int(db.q1("SELECT COUNT(*) AS n FROM admin_audit_log" + where, params)["n"] or 0)
        rows = db.q(
            "SELECT * FROM admin_audit_log" + where + " ORDER BY id DESC LIMIT ? OFFSET ?",
            params + [page_size, (page - 1) * page_size],
        )
        for row in rows:
            row["created_at_fa"] = jalali_from_db(row.get("created_at"))
        return {"items": rows, "total": total, "page": page, "page_size": page_size}
