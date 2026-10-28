"""تأیید/رد/تعلیق پرداخت‌های هر دو پلن از پنل مدیریت وب‌اپ (با ثبت دستی هش و مبلغ)."""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, Optional, Tuple

import crypto_verify
import payments
import plans
from database import db
from webapp import activation

log = logging.getLogger("payment_flow")


def _plan_of(p: Dict[str, Any]) -> str:
    return plans.PLAN_WEBAPP if p.get("service_type") == payments.SERVICE_WEBAPP else plans.PLAN_BOT


def _tx_chain(network_key: str, plan: str, tx_hash: str) -> Tuple[str, str]:
    """(chain، هش نرمال‌شده) بر اساس شبکه‌ی انتخابی یا شبکه‌ی پیش‌فرض پلن."""
    row = plans.network_row(plan, network_key) if network_key else plans.default_network(plan)
    chain = (row or {}).get("chain") or ("bsc" if tx_hash.lower().startswith("0x") else "")
    normalized, err = crypto_verify.normalize_hash(chain, tx_hash) if chain else (tx_hash.strip(), "")
    if err:
        return "", err
    return chain, normalized


def _is_card_payment(p: Dict[str, Any]) -> bool:
    """کارت‌به‌کارت: مقصد یک شماره کارت ۱۶ رقمی است و شبکه‌ی ارزی ندارد."""
    return not (p.get("network") or "").strip() and bool(
        re.fullmatch(r"\d{16}", str(p.get("receiver_address") or ""))
    )


def _set_info(
    pid: int, *, tx_hash: str, amount: Optional[float], network_key: str, sender: str, note: str, plan: str
) -> Tuple[bool, str]:
    p = payments.get(pid)
    if p is None:
        return False, "این تراکنش پیدا نشد."
    card_like = _is_card_payment(p)
    fields: Dict[str, Any] = {}
    if tx_hash.strip():
        if card_like:
            # کارت‌به‌کارت هش بلاک‌چین ندارد؛ شماره پیگیری بانکی همان‌جا ثبت و یکتا می‌شود.
            ref = tx_hash.strip()
            if not re.fullmatch(r"[A-Za-z0-9_\-]{4,100}", ref):
                return False, "شماره پیگیری باید ۴ تا ۱۰۰ کاراکتر انگلیسی/عدد (بدون فاصله) باشد."
            existing = payments.find_by_hash(ref, exclude_id=pid)
            if existing is not None:
                return False, f"این شماره پیگیری قبلاً برای تراکنش #{existing.get('id')} ثبت شده است."
            fields["tx_hash"] = ref
        else:
            chain, normalized = _tx_chain(network_key, plan, tx_hash)
            if not chain:
                return False, normalized
            existing = payments.find_by_hash(normalized, exclude_id=pid)
            if existing is not None:
                ref = f" (تراکنش #{existing['id']} کاربر {existing['user_id']})" if existing.get("id") else ""
                return False, "این هش تراکنش قبلاً ثبت شده است" + ref + "."
            fields["tx_hash"] = normalized
    if amount is not None:
        if float(amount) <= 0:
            return False, "مبلغ واریزی باید بزرگ‌تر از صفر باشد."
        fields["amount"] = float(amount)
    if network_key and not card_like:
        row = plans.network_row(plan, network_key)
        if row is None:
            return False, "شبکه‌ی پرداخت انتخاب‌شده معتبر نیست."
        fields["network"] = row["network_key"]
        if not (row.get("wallet") or "").strip():
            return False, "کیف پول این شبکه هنوز در تنظیمات ثبت نشده است."
        fields["receiver_address"] = row["wallet"]
        # مبلغ مورد انتظار همان زمانِ خرید باید حفظ شود؛ تغییر قیمت/تخفیف بعدی نباید مبنای تأیید را عوض کند.
        if float(p.get("expected_amount") or 0) <= 0:
            fields["expected_amount"] = float(plans.payable_amount(plan, row))
    if sender.strip():
        fields["sender_address"] = sender.strip()[:200]
    if note.strip():
        fields["admin_note"] = note.strip()[:500]
    if fields.get("tx_hash"):
        fields["method"] = "manual"
    if fields:
        sets = ", ".join(f"{k} = ?" for k in fields)
        cur = db.x(
            f"UPDATE payments_history SET {sets} WHERE id = ? AND status IN ('pending', 'suspended')",
            tuple(fields.values()) + (int(pid),),
        )
        if cur.rowcount <= 0:
            return False, "این تراکنش دیگر در وضعیت قابل ویرایش نیست."
    return True, ""


async def approve(
    bot,
    pid: int,
    *,
    tx_hash: str = "",
    amount: Optional[float] = None,
    network_key: str = "",
    sender: str = "",
    note: str = "",
    require_tx: bool = False,
) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    """تأیید یک پرداخت؛ برای پرداخت دستی، هش و مبلغ توسط ادمین ثبت می‌شود."""
    p = payments.get(pid)
    if p is None:
        return False, "این تراکنش پیدا نشد.", None
    if p["status"] not in (payments.PENDING, payments.SUSPENDED):
        return False, "این تراکنش قبلاً بررسی شده است.", p

    plan = _plan_of(p)
    network_key = network_key or p.get("network") or ""
    ok, err = _set_info(
        int(pid), tx_hash=tx_hash, amount=amount, network_key=network_key,
        sender=sender, note=note, plan=plan,
    )
    if not ok:
        return False, err, payments.get(pid)

    p = payments.get(pid) or p
    card_like = _is_card_payment(p)
    if require_tx and not card_like and not p.get("tx_hash"):
        return False, "برای تأیید پرداخت دستی، هش تراکنش (TxID) را وارد کنید.", p

    expected = float(p.get("expected_amount") or 0)
    paid = float(p.get("amount") or 0)
    if expected > 0:
        if paid <= 0:
            return False, "مبلغ واریزی ثبت نشده است؛ برای تأیید، مبلغ دریافتی را وارد کنید.", p
        if payments.compare(paid, expected) == payments.LESS:
            return False, "مبلغ واریزی کمتر از مبلغ اشتراک است؛ تأیید انجام نشد.", p

    prev = p["status"]
    if not payments.set_status(int(pid), payments.APPROVED, (payments.PENDING, payments.SUSPENDED), note or None):
        return False, "این تراکنش قبلاً بررسی شده است.", payments.get(pid)

    days = plans.plan_days(plan)
    try:
        if plan == plans.PLAN_WEBAPP:
            if p.get("request_id"):
                db.decide_sub_request(int(p["request_id"]), "approved")
            result = await activation.activate(int(p["user_id"]), days, bot=bot)
            until = float(result.get("until") or 0)
            message = result.get("message") or "اشتراک تحلیل ساختاری فعال شد."
            if not result.get("sent") and result.get("password"):
                message += f" رمز عبور کاربر: {result.get('password', '')}"
        else:
            until = db.grant_sub(int(p["user_id"]), days)
            if p.get("request_id"):
                db.decide_sub_request(int(p["request_id"]), "approved")
            import subscription

            sent = False
            try:
                sent = bool(await subscription.notify_approved(bot, int(p["user_id"]), until))
            except Exception:
                log.warning("approval notice to %s failed", p["user_id"], exc_info=True)
            message = (
                f"اشتراک ربات فعال شد و به کاربر اطلاع داده شد (پایان: {plans_time(until)})."
                if sent else
                f"اشتراک ربات فعال شد، اما ارسال پیام به کاربر ممکن نبود (پایان: {plans_time(until)})."
            )
    except Exception:
        log.exception("payment %s activation failed", pid)
        payments.set_status(int(pid), prev, (payments.APPROVED,), "activation failed")
        if p.get("request_id"):
            db.x(
                "UPDATE sub_requests SET status = 'pending', reason = '', decided_at = NULL "
                "WHERE id = ? AND status = 'approved'",
                (int(p["request_id"]),),
            )
        return False, "فعال‌سازی اشتراک انجام نشد؛ تراکنش به حالت قبل برگشت. دوباره تلاش کنید.", payments.get(pid)

    return True, message, payments.get(pid)


def plans_time(until: float) -> str:
    from webapp.jalali import jalali_ts

    return jalali_ts(until) if until else "-"


async def reject(bot, pid: int, note: str = "") -> Tuple[bool, str]:
    p = payments.get(pid)
    if p is None:
        return False, "این تراکنش پیدا نشد."
    import subscription

    return await subscription.reject_payment(bot, int(pid), note)


async def suspend(bot, pid: int, note: str = "") -> Tuple[bool, str]:
    p = payments.get(pid)
    if p is None:
        return False, "این تراکنش پیدا نشد."
    import subscription

    return await subscription.suspend_payment(bot, int(pid), note)


def create_manual(
    user_id: int, plan: str, amount: float, tx_hash: str, network_key: str, note: str = "", sender: str = ""
) -> Tuple[bool, str, int]:
    """ثبت یک پرداخت دستی جدید برای کاربر (بعد از تأیید، از همین رکورد استفاده می‌شود)."""
    plan = plans.normalize_plan(plan)
    if db.get_user(int(user_id)) is None:
        return False, "کاربر پیدا نشد.", 0
    row = plans.network_row(plan, network_key)
    if row is None:
        return False, "شبکه‌ی پرداخت معتبر نیست.", 0
    if not (row.get("wallet") or "").strip():
        return False, "کیف پول این شبکه ثبت نشده است.", 0
    if float(amount or 0) <= 0:
        return False, "مبلغ واریزی باید بزرگ‌تر از صفر باشد.", 0
    chain, normalized = _tx_chain(network_key, plan, tx_hash)
    if not chain:
        return False, normalized, 0
    try:
        pid = payments.create(
            int(user_id),
            float(amount),
            float(plans.payable_amount(plan, row)),
            payments.PENDING,
            "manual",
            normalized,
            sender or "",
            row["wallet"],
            note,
            None,
            payments.SERVICE_WEBAPP if plan == plans.PLAN_WEBAPP else payments.SERVICE_BOT,
            network_key,
        )
    except payments.DuplicateHash:
        return False, "این هش تراکنش قبلاً در سیستم ثبت شده است.", 0
    return True, "", pid
