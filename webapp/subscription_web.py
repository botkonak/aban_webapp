"""خرید و تمدید اشتراک تحلیل ساختاری از داخل وب‌اپ (دستی و خودکار)."""
from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel

import admin_notify
import config
import crypto_verify
import payments
import plans
from database import db
from webapp.jalali import jalali_from_db, jalali_ts, parse_db_ts
from webapp.uploads import read_image

log = logging.getLogger("webapp.subscription")

RECEIPT_EXT = frozenset({".jpg", ".jpeg", ".png", ".webp"})
MAX_RECEIPT_BYTES = 5 * 1024 * 1024


def _days_left(until: float) -> int:
    rem = float(until or 0) - time.time()
    return max(0, int((rem + 86399) // 86400)) if rem > 0 else 0


def _plan_info(uid: int, role: str = "user") -> Dict[str, Any]:
    if role == "admin":
        return {
            "active": True, "unlimited": True, "until": 0, "until_jalali": "-", "days_left": 0,
            "amount": 0, "original_amount": 0, "discount_amount": 0, "days": 0, "configured": True,
            "networks": [], "mode": "admin", "pending": None, "requests": [],
            "destinations": {"cards": [], "wallets": []},
        }
    until = db.webapp_sub_until(uid)
    now = time.time()
    original = plans.plan_amount(plans.PLAN_WEBAPP)
    final = plans.final_amount(plans.PLAN_WEBAPP)
    networks = [
        {
            "key": r["network_key"],
            "title": plans.network_title(r),
            "label": plans.network_button_label(r),
            "asset": r.get("asset") or "",
            "network": r.get("network_label") or r.get("network") or "",
            "chain": r.get("chain") or "",
            "wallet": plans.network_wallet(r),
            "amount": plans.payable_amount(plans.PLAN_WEBAPP, r),
            "auto": (r.get("kind") or "") != "native",
        }
        for r in plans.network_rows(plans.PLAN_WEBAPP, enabled_only=True)
        if plans.network_wallet(r)
    ]
    requests = []
    for row in db.q(
        "SELECT * FROM sub_requests WHERE user_id = ? AND service_type = 'webapp' ORDER BY id DESC LIMIT 8",
        (int(uid),),
    ):
        requests.append({
            "id": int(row["id"]),
            "status": row.get("status") or "pending",
            "reason": row.get("reason") or "",
            "created_at": jalali_from_db(row.get("created_at")),
            "decided_at": jalali_from_db(row.get("decided_at")) if row.get("decided_at") else "",
            "has_receipt": bool(row.get("photo_id")),
        })
    pending = next((r for r in requests if r["status"] == "pending"), None)
    discount = plans.discount_info(plans.PLAN_WEBAPP)
    free_claim_row = plans.free_claim(uid, plans.PLAN_WEBAPP)
    free_ends = float((free_claim_row or {}).get("ends_at") or 0)
    free_now_active = bool(free_claim_row and free_ends > now and until <= free_ends + 120)
    free_scope = plans.free_web_strategies_for(uid)
    return {
        "active": until > now,
        "unlimited": False,
        "until": until,
        "until_jalali": jalali_ts(until) if until else "-",
        "days_left": _days_left(until),
        "amount": final if final is not None else original,
        "original_amount": original,
        "discount_amount": max(0.0, original - final) if final is not None else 0.0,
        "discount": discount,
        "days": plans.plan_days(plans.PLAN_WEBAPP),
        "renew_days": plans.plan_renew_days(plans.PLAN_WEBAPP),
        "configured": plans.configured(plans.PLAN_WEBAPP),
        "mode": plans.plan_mode(plans.PLAN_WEBAPP),
        "networks": networks,
        "pending": pending,
        "requests": requests,
        "template": plans.plan_template(plans.PLAN_WEBAPP),
        # مقصدهای پشتیبانی‌شده در صفحه‌ی پرداخت: کارت‌های بانکی و کیف‌پول‌های دستی (بخش ۶ سند).
        "destinations": plans.checkout_destinations(plans.PLAN_WEBAPP),
        "free": {
            "on": plans.free_on(plans.PLAN_WEBAPP),
            "days": plans.free_days(plans.PLAN_WEBAPP),
            "claimed": free_claim_row is not None,
            "active": free_now_active,
            "ends_at": free_ends if free_claim_row else 0,
            "ends_at_jalali": jalali_ts(free_ends) if free_ends else "-",
            # دسترسی رایگان فعلاً فقط به این استراتژی‌ها محدود است؛ None یعنی همه.
            "restricted": free_now_active and free_scope is not None,
            "strategy_count": None if free_scope is None else len(free_scope),
        },
    }


async def _save_receipt(receipt: UploadFile, root: Path) -> str:
    ext, _, data = await read_image(receipt, allowed_extensions=RECEIPT_EXT, max_bytes=MAX_RECEIPT_BYTES)
    now = datetime.now(timezone.utc)
    folder = root / "receipts" / f"{now.year:04d}" / f"{now.month:02d}"
    folder.mkdir(parents=True, exist_ok=True)
    stored = uuid.uuid4().hex + ext
    try:
        (folder / stored).write_bytes(data)
    except OSError as exc:
        raise HTTPException(500, f"ذخیره‌ی رسید انجام نشد: {exc}") from None
    return f"/uploads/receipts/{now.year:04d}/{now.month:02d}/{stored}"


def register(
    app: FastAPI,
    *,
    require_user: Callable[[Request], Dict[str, Any]],
    client_ip: Callable[[Request], str],
    upload_root: Path,
) -> None:
    _require_user = require_user
    _ip = client_ip
    receipts_root = Path(upload_root)
    receipts_root.mkdir(parents=True, exist_ok=True)

    @app.get("/api/subscription")
    async def subscription_info(request: Request):
        auth = _require_user(request)
        return _plan_info(int(auth["uid"]), auth.get("role") or "user")

    @app.post("/api/subscription/request")
    async def subscription_request(
        request: Request,
        network_key: str = Form(""),
        tx_hash: str = Form(""),
        receiver: str = Form(""),
        amount: str = Form(""),
        note: str = Form(""),
        text: str = Form(""),
        receipt: Optional[UploadFile] = File(None),
    ):
        auth = _require_user(request)
        uid = int(auth["uid"])
        pending_row = db.q1(
            "SELECT id FROM sub_requests WHERE user_id = ? AND service_type = 'webapp' AND status = 'pending' "
            "ORDER BY id DESC LIMIT 1",
            (uid,),
        )
        if pending_row:
            raise HTTPException(409, "درخواست قبلی شما در انتظار بررسی است؛ تا تعیین وضعیت، درخواست جدید ثبت نمی‌شود.")
        nk = (network_key or "").strip()
        row: Optional[Dict[str, Any]] = None
        dest_label = ""
        kind, sep, ident = nk.partition(":")
        kind = kind.lower()
        if sep and kind in ("card", "wallet"):
            # مقصدهای دستیِ پنل (کارت بانکی / کیف‌پول ارزی)؛ واریز ارزی خودکار ندارد و رسید دستی بررسی می‌شود.
            if not ident.isdigit():
                raise HTTPException(400, "مقصد پرداخت انتخاب‌شده معتبر نیست.")
            dest = plans.get_card(int(ident), True) if kind == "card" else plans.get_wallet(int(ident), True)
            if dest is None or plans.normalize_plan(dest["plan"]) != plans.PLAN_WEBAPP:
                raise HTTPException(400, "مقصد پرداخت انتخاب‌شده معتبر نیست.")
            plan_base = float(plans.payable_amount(plans.PLAN_WEBAPP, None))
            if kind == "card":
                wallet = plans.normalize_card(dest.get("card_number") or "")
                bank = (dest.get("bank_name") or "").strip()
                dest_label = f"کارت {' '.join(wallet[i:i + 4] for i in range(0, 16, 4))}" + (f" ({bank})" if bank else "")
                expected = plan_base
            else:
                wallet = str(dest.get("address") or "").strip()
                label = (dest.get("network_label") or dest.get("network") or "").strip()
                dest_label = f"{label} {wallet}".strip()
                amt = float(dest.get("amount") or 0)
                expected = amt if amt > 0 else plan_base
            if not wallet:
                raise HTTPException(400, "مقصد پرداخت انتخاب‌شده معتبر نیست.")
        else:
            row = plans.network_row(plans.PLAN_WEBAPP, nk)
            if row is None or not int(row.get("enabled") or 0):
                raise HTTPException(400, "شبکه‌ی پرداخت انتخاب‌شده معتبر نیست.")
            wallet = plans.network_wallet(row)
            if not wallet:
                raise HTTPException(400, "کیف پول این شبکه هنوز تنظیم نشده است.")
            expected = float(plans.payable_amount(plans.PLAN_WEBAPP, row))
            dest_label = plans.network_title(row)
        tx = (tx_hash or "").strip()
        chain = (row.get("chain") or "").strip().lower() if row else ""
        normalized, error = crypto_verify.normalize_hash(chain, tx) if (tx and chain) else ("", "")
        if tx and chain and error:
            raise HTTPException(400, error)
        if normalized and payments.find_by_hash(normalized):
            raise HTTPException(409, "این هش تراکنش قبلاً ثبت شده است.")

        receipt_path = ""
        if receipt is not None and receipt.filename:
            receipt_path = await _save_receipt(receipt, receipts_root)

        paid: Optional[float] = None
        sender = ""
        method = "manual"
        auto_checked = False
        if normalized and not receipt_path:
            try:
                result = await crypto_verify.verify_transfer(row, normalized, wallet)
            except Exception:
                log.exception("webapp auto verify failed")
                raise HTTPException(502, "بررسی خودکار تراکنش انجام نشد؛ کمی بعد دوباره تلاش کنید.") from None
            if not result.ok:
                raise HTTPException(400, result.error or "تراکنش تأیید نشد. هش را بررسی کنید یا رسید را بارگذاری کنید.")
            paid = float(result.amount or 0)
            sender = result.sender or ""
            method = "auto"
            auto_checked = True
            if paid + 1e-6 < expected:
                note = (note + " — مبلغ واریزی کمتر از مبلغ اشتراک است.").strip(" —")
        elif not receipt_path and not tx:
            raise HTTPException(400, "هش تراکنش یا تصویر رسید را وارد کنید.")
        elif receipt_path and tx:
            method = "manual"

        try:
            manual_amount = float(str(amount or "").replace(",", "").strip() or 0)
        except ValueError:
            manual_amount = 0
        if paid is None and manual_amount > 0:
            paid = manual_amount

        lines = [
            "درخواست اشتراک تحلیل ساختاری از وب‌اپ",
            f"مقصد: {dest_label}",
            f"مقصد واریز: {wallet}",
        ]
        if paid is not None:
            lines.append(f"مبلغ اعلام‌شده: {paid}")
        if normalized:
            lines.append(f"هش تراکنش: {normalized}")
        elif tx:
            lines.append(f"شماره مرجع/تراکنش: {tx[:200]}")
        if sender:
            lines.append(f"فرستنده: {sender}")
        if receiver.strip():
            lines.append(f"فرستنده اعلام‌شده: {receiver.strip()[:200]}")
        if text.strip():
            lines.append(f"توضیح کاربر: {text.strip()[:500]}")
        if note.strip():
            lines.append(f"یادداشت: {note.strip()[:300]}")
        if auto_checked:
            lines.append("وضعیت: تراکنش به‌صورت خودکار در بلاک‌چین تأیید شد.")
        elif receipt_path:
            lines.append("وضعیت: رسید برای بررسی دستی بارگذاری شد.")
        request_text = "\n".join(lines)[:4000]

        rid = db.add_sub_request(uid, request_text, ("local:" + receipt_path) if receipt_path else "", service_type="webapp")
        # برای مقصدهای دستی (کارت/کیف‌پول) شماره‌ی مرجع وارد‌شده به‌جای هش بلاک‌چین ذخیره می‌شود.
        stored_hash = normalized or ("" if row else tx)
        try:
            pid = payments.create(
                uid, float(paid or 0), expected, payments.PENDING, method, stored_hash or None,
                sender, wallet, note[:300], rid, payments.SERVICE_WEBAPP, (nk if row else ""),
            )
        except payments.DuplicateHash:
            db.decide_sub_request(int(rid), "rejected", "هش تراکنش تکراری")
            raise HTTPException(409, "این هش تراکنش قبلاً ثبت شده است.") from None
        except Exception:
            log.exception("create webapp payment failed")
            db.decide_sub_request(int(rid), "rejected", "خطای ثبت")
            raise HTTPException(500, "ثبت درخواست انجام نشد؛ دوباره تلاش کنید.") from None

        admin_notify.log_event(uid, "webapp_purchase_request", f"درخواست #{rid} — {method}", _ip(request))
        return {
            "ok": True,
            "request_id": int(rid),
            "payment_id": int(pid),
            "status": payments.PENDING,
            "message": "درخواست ثبت شد و پس از بررسی مدیر، اشتراک شما فعال می‌شود.",
        }
