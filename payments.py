"""
تاریخچه‌ی پرداخت‌ها (آبان ترید): لایه‌ی دیتابیس جدول payments_history.

قواعد:
  - هر تراکنش (BSC خودکار یا دستی) یک رکورد دارد؛ tx_hash یکتا است (هش تکراری هیچ‌وقت پذیرفته نمی‌شود).
  - وضعیت‌ها: approved (تأیید و اشتراک فعال) | pending (در انتظار بررسی ادمین) | suspended (معلق: مبلغ بیشتر) | rejected (رد شده)
  - تغییر وضعیت همیشه اتمیک است (UPDATE ... WHERE status IN (...)) تا یک تراکنش دو بار پردازش نشود.
  - مبلغ‌ها با Decimal مقایسه می‌شوند تا خطای اعشار float رخ ندهد.
این ماژول هیچ پیامی نمی‌فرستد؛ اعلان‌ها در subscription.py هستند.
"""
from __future__ import annotations

import logging
import re
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Sequence, Tuple

from sqlalchemy.exc import IntegrityError

from database import _norm_digits, db

log = logging.getLogger("payments")

APPROVED, PENDING, SUSPENDED, REJECTED = "approved", "pending", "suspended", "rejected"
STATUSES = (APPROVED, PENDING, SUSPENDED, REJECTED)
STATUS_FA = {APPROVED: "تأیید شده", PENDING: "در انتظار", SUSPENDED: "معلق", REJECTED: "رد شده"}
STATUS_ICON = {APPROVED: "✅", PENDING: "⏳", SUSPENDED: "⚠️", REJECTED: "❌"}
METHOD_FA = {"bsc": "BSC (خودکار)", "auto": "خودکار (بلاک‌چین)", "manual": "دستی (رسید)"}
SERVICE_BOT, SERVICE_WEBAPP = "bot", "webapp"
SERVICE_FA = {SERVICE_BOT: "ربات", SERVICE_WEBAPP: "تحلیل ساختاری"}

# مقایسه‌ی مبلغ: اختلاف کمتر از این مقدار «برابر» حساب می‌شود (تتر ۶ رقم اعشار مفید دارد)
AMOUNT_EPS = Decimal("0.000001")
DUP_NOTE = "هش تکراری وارد شد: "

EQUAL, GREATER, LESS = "equal", "greater", "less"


class DuplicateHash(Exception):
    """هش تراکنش قبلاً ثبت شده است؛ existing = رکورد قبلی (در صورت پیدا شدن)."""

    def __init__(self, existing: Optional[Dict[str, Any]] = None):
        super().__init__("duplicate tx hash")
        self.existing = existing


# ----------------------------------------------------------------------------
# ابزار
# ----------------------------------------------------------------------------
def norm_hash(h: str) -> str:
    return (h or "").strip().lower()


def to_decimal(v: Any) -> Optional[Decimal]:
    try:
        d = Decimal(str(v).strip())
    except (InvalidOperation, ValueError):
        return None
    return d if d.is_finite() else None


def parse_amount(raw: str) -> Optional[Decimal]:
    """مبلغ واردشده توسط ادمین (ارقام فارسی / جداکننده‌ی هزارگان مجاز)؛ فقط مقدار مثبت."""
    s = _norm_digits((raw or "").strip()).replace("٫", ".").replace("٬", "").replace(",", "")
    s = re.sub(r"(?i)\s*(usdt|\$|دلار|تتر)\s*$", "", s).strip()
    d = to_decimal(s)
    if d is None or d <= 0 or d > Decimal("100000000"):
        return None
    return d


def compare(amount: Any, expected: Any) -> str:
    """مقایسه‌ی مبلغ واریزی با مبلغ اشتراک: equal | greater | less."""
    a, e = to_decimal(amount) or Decimal(0), to_decimal(expected) or Decimal(0)
    if abs(a - e) < AMOUNT_EPS:
        return EQUAL
    return GREATER if a > e else LESS


def _row(r: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if r is None:
        return None
    r = dict(r)
    r["amount"] = float(r.get("amount") or 0.0)
    r["expected_amount"] = float(r.get("expected_amount") or 0.0)
    for k in ("tx_hash", "sender_address", "receiver_address", "admin_note", "network"):
        r[k] = r.get(k) or ""
    r["service_type"] = r.get("service_type") or SERVICE_BOT
    return r


# ----------------------------------------------------------------------------
# خواندن
# ----------------------------------------------------------------------------
def get(pid: int) -> Optional[Dict[str, Any]]:
    return _row(db.q1("SELECT * FROM payments_history WHERE id = ?", (int(pid),)))


def by_request(request_id: int) -> Optional[Dict[str, Any]]:
    return _row(db.q1("SELECT * FROM payments_history WHERE request_id = ? ORDER BY id DESC LIMIT 1", (int(request_id),)))


def find_by_hash(tx_hash: str, exclude_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """رکورد دارای همین هش (بدون حساسیت به حروف). جدول قدیمی bsc_payments هم بررسی می‌شود."""
    h = norm_hash(tx_hash)
    if not h:
        return None
    if exclude_id is None:
        r = db.q1("SELECT * FROM payments_history WHERE LOWER(tx_hash) = ? LIMIT 1", (h,))
    else:
        r = db.q1("SELECT * FROM payments_history WHERE LOWER(tx_hash) = ? AND id != ? LIMIT 1", (h, int(exclude_id)))
    if r:
        return _row(r)
    legacy = db.q1("SELECT id, user_id, amount, created_at FROM bsc_payments WHERE LOWER(tx_hash) = ? LIMIT 1", (h,))
    if legacy:
        return _row({
            "id": 0, "user_id": legacy["user_id"], "amount": legacy["amount"], "expected_amount": legacy["amount"],
            "status": APPROVED, "method": "bsc", "tx_hash": h, "created_at": legacy["created_at"],
        })
    return None


def count(user_id: Optional[int] = None, service_type: Optional[str] = None) -> int:
    where, params = [], []
    if user_id is not None:
        where.append("user_id = ?")
        params.append(int(user_id))
    if service_type:
        where.append("service_type = ?")
        params.append(service_type)
    cond = (" WHERE " + " AND ".join(where)) if where else ""
    r = db.q1("SELECT COUNT(*) AS n FROM payments_history" + cond, params)
    return int((r or {}).get("n") or 0)


def list_recent(limit: int = 10, offset: int = 0, service_type: Optional[str] = None) -> List[Dict[str, Any]]:
    if service_type:
        rows = db.q(
            "SELECT * FROM payments_history WHERE service_type = ? ORDER BY id DESC LIMIT ? OFFSET ?",
            (service_type, int(limit), int(offset)),
        )
    else:
        rows = db.q("SELECT * FROM payments_history ORDER BY id DESC LIMIT ? OFFSET ?", (int(limit), int(offset)))
    return [_row(r) for r in rows]


def list_by_user(user_id: int, limit: int = 10, service_type: Optional[str] = None) -> List[Dict[str, Any]]:
    if service_type:
        rows = db.q(
            "SELECT * FROM payments_history WHERE user_id = ? AND service_type = ? ORDER BY id DESC LIMIT ?",
            (int(user_id), service_type, int(limit)),
        )
    else:
        rows = db.q("SELECT * FROM payments_history WHERE user_id = ? ORDER BY id DESC LIMIT ?", (int(user_id), int(limit)))
    return [_row(r) for r in rows]


def _like(s: str) -> str:
    return "%" + s.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def search(query: str, limit: int = 10, service_type: Optional[str] = None) -> List[Dict[str, Any]]:
    """جستجو بر اساس آیدی عددی کاربر، آدرس ولت فرستنده یا هش تراکنش."""
    q = _norm_digits((query or "").strip())
    if not q:
        return []
    conds = ["LOWER(COALESCE(sender_address, '')) LIKE ? ESCAPE '\\'", "LOWER(COALESCE(tx_hash, '')) LIKE ? ESCAPE '\\'"]
    params: list = [_like(q), _like(q)]
    if q.isdigit():
        conds.insert(0, "user_id = ?")
        params.insert(0, int(q))
    where = "(" + " OR ".join(conds) + ")"
    if service_type:
        where += " AND service_type = ?"
        params.append(service_type)
    rows = db.q(
        f"SELECT * FROM payments_history WHERE {where} ORDER BY id DESC LIMIT ?", params + [int(limit)]
    )
    return [_row(r) for r in rows]


# ----------------------------------------------------------------------------
# نوشتن
# ----------------------------------------------------------------------------
def create(
    user_id: int, amount: float, expected: float, status: str, method: str, tx_hash: Optional[str] = None,
    sender: Optional[str] = None, receiver: Optional[str] = None, note: str = "", request_id: Optional[int] = None,
    service_type: str = SERVICE_BOT, network: str = "",
) -> int:
    """رکورد جدید. اگر tx_hash تکراری باشد DuplicateHash برمی‌گرداند (و چیزی ثبت نمی‌شود)."""
    h = norm_hash(tx_hash) or None
    if h and find_by_hash(h):
        raise DuplicateHash(find_by_hash(h))
    try:
        return db.insert_id(
            "INSERT INTO payments_history(user_id, amount, expected_amount, status, method, tx_hash, sender_address, "
            "receiver_address, admin_note, request_id, service_type, network) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (int(user_id), float(amount or 0), float(expected or 0), status, method, h, sender or None,
             receiver or None, note or None, request_id,
             service_type if service_type in (SERVICE_BOT, SERVICE_WEBAPP) else SERVICE_BOT,
             (network or "")[:40]),
        )
    except IntegrityError:
        raise DuplicateHash(find_by_hash(h) if h else None)


def ensure_for_request(req, expected: float, receiver: str) -> Dict[str, Any]:
    """برای درخواست دستیِ بدون رکورد (درخواست‌های قبل از به‌روزرسانی) یک رکورد «در انتظار» می‌سازد."""
    p = by_request(req.id)
    if p is not None:
        return p
    pid = create(req.user_id, 0, expected, PENDING, "manual", receiver=receiver, request_id=req.id)
    return get(pid)  # type: ignore[return-value]


def has_info(p: Dict[str, Any]) -> bool:
    """اطلاعات دستیِ ادمین (هش، فرستنده، مبلغ) ثبت شده است؟ (حالت «هش تکراری» هم ثبت‌شده حساب می‌شود)"""
    return bool(p["sender_address"]) and (bool(p["tx_hash"]) or is_dup(p))


def is_dup(p: Dict[str, Any]) -> bool:
    return not p["tx_hash"] and p["admin_note"].startswith(DUP_NOTE)


def set_info(pid: int, tx_hash: str, sender: str, amount: Decimal) -> Dict[str, Any]:
    """
    ثبت اطلاعات توسط ادمین (فقط برای رکورد pending). خروجی: رکورد به‌روزشده.
    هش تکراری: آدرس و مبلغ ذخیره می‌شود ولی هش نه؛ DuplicateHash برمی‌گردد (ادمین باید رد کند).
    """
    h = norm_hash(tx_hash)
    existing = find_by_hash(h, exclude_id=pid)
    if existing is not None:
        db.x(
            "UPDATE payments_history SET tx_hash = NULL, sender_address = ?, amount = ?, admin_note = ? "
            "WHERE id = ? AND status = 'pending'",
            (sender, float(amount), DUP_NOTE + h, int(pid)),
        )
        raise DuplicateHash(existing)
    try:
        cur = db.x(
            "UPDATE payments_history SET tx_hash = ?, sender_address = ?, amount = ?, "
            "admin_note = CASE WHEN admin_note LIKE ? THEN NULL ELSE admin_note END "
            "WHERE id = ? AND status = 'pending'",
            (h, sender, float(amount), DUP_NOTE + "%", int(pid)),
        )
    except IntegrityError:
        raise DuplicateHash(find_by_hash(h))
    if cur.rowcount <= 0:
        raise ValueError("not pending")
    return get(pid)  # type: ignore[return-value]


def set_status(pid: int, new: str, allowed_from: Sequence[str], note: Optional[str] = None) -> bool:
    """تغییر اتمیک وضعیت؛ فقط اگر وضعیت فعلی در allowed_from باشد True برمی‌گرداند."""
    allowed = tuple(allowed_from)
    ph = ",".join("?" * len(allowed))
    if note is None:
        cur = db.x(f"UPDATE payments_history SET status = ? WHERE id = ? AND status IN ({ph})", (new, int(pid), *allowed))
    else:
        cur = db.x(
            f"UPDATE payments_history SET status = ?, admin_note = ? WHERE id = ? AND status IN ({ph})",
            (new, note, int(pid), *allowed),
        )
    return cur.rowcount > 0


def case_of(p: Dict[str, Any]) -> str:
    """نتیجه‌ی مقایسه برای رکورد دستی: missing | dup | equal | greater | less."""
    if not has_info(p):
        return "missing"
    if is_dup(p):
        return "dup"
    return compare(p["amount"], p["expected_amount"])
