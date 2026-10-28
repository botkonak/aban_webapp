"""مدیریت پلن‌های اشتراک، شبکه‌های پرداخت، تخفیف و اشتراک رایگان (مشترک بین ربات و پنل وب‌اپ)."""
from __future__ import annotations

import logging
import re
import time
from typing import Any, Dict, List, Optional, Set, Tuple

import config
from database import db

log = logging.getLogger("plans")

PLAN_BOT = "bot"
PLAN_WEBAPP = "webapp"
PLANS: Tuple[str, ...] = (PLAN_BOT, PLAN_WEBAPP)

MODE_OFF = "off"
MODE_MANUAL = "manual"
MODE_AUTO = "auto"
MODES = (MODE_OFF, MODE_MANUAL, MODE_AUTO)

PLAN_LABELS = {
    PLAN_BOT: "اشتراک ربات",
    PLAN_WEBAPP: "اشتراک تحلیل ساختاری",
}

# شبکه‌های پرداخت پشتیبانی‌شده (هر پلن جداگانه فعال/غیرفعال می‌شود)
# کلید شبکه‌ها — منبع یکتا؛ crypto_verify و باقی ماژول‌ها همین مقدارها را مقایسه می‌کنند.
NET_USDT_BEP20 = "usdt_bep20"
NET_USDT_TRC20 = "usdt_trc20"
NET_ARB_ARBITRUM = "arb_arbitrum"
NET_BNB_BEP20 = "bnb_bep20"
NET_TRX_TRC20 = "trx_trc20"
NET_DOGS_TON = "dogs_ton"

# شناسه‌ی زنجیره‌ی هر شبکه
CHAIN_BSC = "bsc"
CHAIN_TRON = "tron"
CHAIN_ARBITRUM = "arbitrum"
CHAIN_TON = "ton"

# آدرس قرارداد (contract) توکن‌های رسمی. تنها منبع حقیقت: هم جدول payment_networks از همین
# مقدارها پر می‌شود و هم crypto_verify برای تأیید تراکنش همین‌ها را استفاده می‌کند، پس ویرایش
# بعدی هرگز نمی‌تواند مرحله‌ی تأیید را از جدول پلن‌ها جدا بیندازد.
CONTRACT_USDT_BEP20 = "0x55d398326f99059fF775485246999027B3197955"
CONTRACT_USDT_TRC20 = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
CONTRACT_ARB_ARBITRUM = "0x912CE59144191C1204E64559FE8253a0e49E6548"
CONTRACT_DOGS_TON = "EQCvxJy4eG8hyHBFsZ7eePxrRsUQSFE_jpptRAYBmcG_DOGS"

NETWORK_CATALOG: Tuple[Dict[str, Any], ...] = (
    {
        "network_key": NET_USDT_BEP20,
        "asset": "USDT",
        "network": "BSC",
        "network_label": "BEP20",
        "chain": CHAIN_BSC,
        "kind": "bep20",
        "contract": CONTRACT_USDT_BEP20,
        "decimals": 18,
        "sort_order": 10,
    },
    {
        "network_key": NET_USDT_TRC20,
        "asset": "USDT",
        "network": "TRX",
        "network_label": "TRC20",
        "chain": CHAIN_TRON,
        "kind": "trc20",
        "contract": CONTRACT_USDT_TRC20,
        "decimals": 6,
        "sort_order": 20,
    },
    {
        "network_key": NET_ARB_ARBITRUM,
        "asset": "ARB",
        "network": "Arbitrum",
        "network_label": "Arbitrum One",
        "chain": CHAIN_ARBITRUM,
        "kind": "erc20",
        "contract": CONTRACT_ARB_ARBITRUM,
        "decimals": 18,
        "sort_order": 30,
    },
    {
        "network_key": NET_BNB_BEP20,
        "asset": "BNB",
        "network": "BSC",
        "network_label": "BEP20",
        "chain": CHAIN_BSC,
        "kind": "native",
        "contract": "",
        "decimals": 18,
        "sort_order": 40,
    },
    {
        "network_key": NET_TRX_TRC20,
        "asset": "TRX",
        "network": "TRX",
        "network_label": "TRC20",
        "chain": CHAIN_TRON,
        "kind": "native",
        "contract": "",
        "decimals": 6,
        "sort_order": 50,
    },
    {
        "network_key": NET_DOGS_TON,
        "asset": "DOGS",
        "network": "TON",
        "network_label": "TON",
        "chain": CHAIN_TON,
        "kind": "jetton",
        "contract": CONTRACT_DOGS_TON,
        "decimals": 9,
        "sort_order": 60,
    },
)

NETWORK_BY_KEY = {n["network_key"]: n for n in NETWORK_CATALOG}

FREE_BOT_FEATURES: Tuple[Tuple[str, str], ...] = (
    ("signals", "دریافت سیگنال"),
    ("analysis", "آنالیز با AI"),
    ("multi", "تحلیل چند تایم‌فریمی"),
    ("settings", "تنظیمات موتورها و ریسک"),
    ("ai", "مشاور هوشمند و کلیدهای AI"),
    ("live", "قیمت زنده‌ی سیگنال"),
    ("pre", "هشدار قبل از سیگنال"),
)
FREE_BOT_FEATURE_LABELS = dict(FREE_BOT_FEATURES)
NONE_SENTINEL = "-"


# ----------------------------------------------------------------------------
# کلیدهای تنظیمات هر پلن
# ----------------------------------------------------------------------------
def _keys(plan: str) -> Dict[str, str]:
    if plan == PLAN_BOT:
        return {
            "mode": "sub_mode",
            "amount": "sub_amount",
            "days": "sub_days",
            "renew": "sub_renew_days",
            "disc_on": "sub_disc_on",
            "disc_kind": "sub_disc_kind",
            "disc_pct": "sub_disc_pct",
            "disc_fixed": "sub_disc_fixed",
            "disc_days": "sub_disc_days",
            "disc_start": "sub_disc_start",
            "disc_end": "sub_disc_end",
            "template": "sub_template",
            "legacy_wallet": "sub_wallet",
            "auto_wallet": "bsc_wallet",
        }
    return {
        "mode": "web_sub_mode",
        "amount": "web_sub_amount",
        "days": "web_sub_days",
        "renew": "web_sub_renew_days",
        "disc_on": "web_disc_on",
        "disc_kind": "web_disc_kind",
        "disc_pct": "web_disc_pct",
        "disc_fixed": "web_disc_fixed",
        "disc_days": "web_disc_days",
        "disc_start": "web_disc_start",
        "disc_end": "web_disc_end",
        "template": "web_sub_template",
        "legacy_wallet": "web_sub_wallet",
        "auto_wallet": "web_bsc_wallet",
    }


def normalize_plan(plan: str) -> str:
    return PLAN_WEBAPP if str(plan or "").strip().lower() in ("webapp", "web", "technical") else PLAN_BOT


# ----------------------------------------------------------------------------
# راه‌اندازی اولیه و مهاجرت تنظیمات قدیمی
# ----------------------------------------------------------------------------
def ensure_defaults() -> None:
    """شبکه‌های هر پلن را ایجاد و تنظیمات قدیمی (manual/bsc) را به مدل جدید منتقل می‌کند."""
    for plan in PLANS:
        k = _keys(plan)
        mode = (db.get(k["mode"]) or "").strip().lower()
        if mode == "bsc":
            db.set(k["mode"], MODE_AUTO)
            mode = MODE_AUTO
        elif mode not in MODES:
            if mode in ("1", "on", "true"):
                db.set(k["mode"], MODE_MANUAL)
            else:
                db.set(k["mode"], MODE_OFF)
            mode = (db.get(k["mode"]) or MODE_OFF).strip().lower()

        amount = db.get_float(k["amount"])
        legacy_wallet = db.get(k["legacy_wallet"]).strip()
        auto_wallet = db.get(k["auto_wallet"]).strip()

        for item in NETWORK_CATALOG:
            row = db.q1(
                "SELECT id, wallet, amount, enabled FROM payment_networks WHERE plan = ? AND network_key = ?",
                (plan, item["network_key"]),
            )
            if row is None:
                wallet = ""
                enabled = 0
                if item["network_key"] == NET_USDT_BEP20:
                    if mode == MODE_AUTO and auto_wallet:
                        wallet, enabled = auto_wallet, 1
                    elif mode == MODE_MANUAL and legacy_wallet:
                        wallet, enabled = legacy_wallet, 1
                db.x(
                    "INSERT INTO payment_networks"
                    "(plan, network_key, asset, network, network_label, chain, kind, contract, decimals, "
                    " wallet, amount, enabled, sort_order) "
                    "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        plan, item["network_key"], item["asset"], item["network"], item["network_label"],
                        item["chain"], item["kind"], item["contract"], item["decimals"],
                        wallet, amount, enabled, item["sort_order"],
                    ),
                )
                continue

            updates: List[Tuple[str, Any]] = []
            if not (row["wallet"] or "").strip():
                fill = ""
                if item["network_key"] == NET_USDT_BEP20:
                    fill = auto_wallet if mode == MODE_AUTO else (legacy_wallet if mode == MODE_MANUAL else "")
                if fill:
                    updates.append(("wallet", fill))
            if not float(row["amount"] or 0) and amount > 0:
                updates.append(("amount", amount))
            if (
                item["network_key"] == NET_USDT_BEP20 and mode == MODE_AUTO
                and not int(row["enabled"] or 0) and (auto_wallet or (row["wallet"] or "").strip())
            ):
                updates.append(("enabled", 1))
            if updates:
                sets = ", ".join(f"{c} = ?" for c, _ in updates)
                db.x(
                    f"UPDATE payment_networks SET {sets} WHERE id = ?",
                    tuple(v for _, v in updates) + (row["id"],),
                )

    _migrate_bot_discount_window()


def _migrate_bot_discount_window() -> None:
    """در نسخه‌ی قبلی `sub_disc_start` لحظه‌ی شروع نبود؛ اگر تخفیف روشن است و مقدار در آینده است، اکنون شود."""
    now = time.time()
    if db.get_bool("sub_disc_on"):
        start = db.get_float("sub_disc_start")
        if start <= 0 or start > now + 60 * 86400:
            db.set("sub_disc_start", str(now))


# ----------------------------------------------------------------------------
# دسترسی به شبکه‌ها
# ----------------------------------------------------------------------------
def network_rows(plan: str, enabled_only: bool = False) -> List[Dict[str, Any]]:
    plan = normalize_plan(plan)
    sql = "SELECT * FROM payment_networks WHERE plan = ?"
    if enabled_only:
        sql += " AND enabled = 1"
    sql += " ORDER BY sort_order, id"
    return [dict(r) for r in db.q(sql, (plan,))]


def network_row(plan: str, network_key: str) -> Optional[Dict[str, Any]]:
    r = db.q1(
        "SELECT * FROM payment_networks WHERE plan = ? AND network_key = ?",
        (normalize_plan(plan), network_key),
    )
    return dict(r) if r else None


def network_title(row: Dict[str, Any]) -> str:
    label = (row.get("network_label") or row.get("network") or "").strip()
    return f"{row.get('asset', '')} روی {label}".strip()


def network_button_label(row: Dict[str, Any]) -> str:
    return f"{row.get('asset', '')} · {row.get('network_label') or row.get('network') or ''}"


def network_amount(row: Optional[Dict[str, Any]], plan: str = "") -> float:
    if row and float(row.get("amount") or 0) > 0:
        return float(row["amount"])
    return plan_amount(plan or (row or {}).get("plan", PLAN_BOT))


def network_wallet(row: Optional[Dict[str, Any]]) -> str:
    return ((row or {}).get("wallet") or "").strip()


def update_network(plan: str, network_key: str, **fields: Any) -> bool:
    allowed = {"wallet", "amount", "enabled", "sort_order"}
    data = {k: v for k, v in fields.items() if k in allowed}
    if not data:
        return False
    if "enabled" in data:
        data["enabled"] = 1 if data["enabled"] else 0
    if "amount" in data:
        data["amount"] = max(0.0, float(data["amount"] or 0))
    if "wallet" in data:
        data["wallet"] = str(data["wallet"] or "").strip()
    sets = ", ".join(f"{k} = ?" for k in data)
    cur = db.x(
        f"UPDATE payment_networks SET {sets} WHERE plan = ? AND network_key = ?",
        tuple(data.values()) + (normalize_plan(plan), network_key),
    )
    return cur.rowcount > 0


# ----------------------------------------------------------------------------
# مقاصد پرداخت: کارت‌به‌کارت (کارت/شبا) و کیف‌پول‌های چندگانه
# ----------------------------------------------------------------------------
# شماره کارت یک راز است: کامل فقط به ادمین برگردانده می‌شود و در مسیر چک‌اوت کاربر
# تنها چهار رقم آخر + شبا + نام بانک نمایش داده می‌شود. هر مقصد فعال باید اعتبارسنجی
# شود و مقصد فعال تکراری (کارت/شبا یا شبکه/آدرس یکسان) پذیرفته نمی‌شود.
CARD_DIGITS_LEN = 16
EVM_ADDR_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
TRON_ADDR_RE = re.compile(r"^T[1-9A-HJ-NP-Za-km-z]{33}$")
TRON_HEX_ADDR_RE = re.compile(r"^41[0-9a-fA-F]{40}$")
TON_ADDR_RE = re.compile(r"^(EQ|UQ)[A-Za-z0-9_\-]{46}$")
TON_HEX_ADDR_RE = re.compile(r"^0:?[0-9a-fA-F]{64}$")


def _luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = ord(ch) - 48
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def normalize_card(raw: str) -> str:
    from database import _norm_digits
    return re.sub(r"\D", "", _norm_digits((raw or "").strip()))


def mask_card(digits: str) -> str:
    d = re.sub(r"\D", "", digits or "")
    if len(d) < 8:
        return d
    return f"{d[:6]}******{d[-4:]}"  # BIN (۶ رقم اول، ناساز) + ۴ رقم آخر


def validate_card(raw: str) -> Tuple[bool, str, str]:
    """کارت ۱۶ رقمی با کنترل‌رقم Luhn؛ -> (ok, پیام_خطا، شماره_نرمال‌شده)."""
    digits = normalize_card(raw)
    if not digits:
        return False, "شماره کارت وارد نشده است.", ""
    if len(digits) != CARD_DIGITS_LEN:
        return False, "شماره کارت باید ۱۶ رقم باشد.", digits
    if not _luhn_ok(digits):
        return False, "شماره کارت نامعتبر است (کنترل‌ارقام Luhn رد شد).", digits
    return True, "", digits


def _iban_mod97(value: str) -> int:
    total = 0
    for ch in value:
        if ch.isdigit():
            chunk = ch
        elif ch.isalpha():
            chunk = str(ord(ch.upper()) - 55)
        else:
            return -1
        for d in chunk:
            total = (total * 10 + (ord(d) - 48)) % 97
    return total


def normalize_shaba(raw: str) -> str:
    return re.sub(r"[^0-9A-Za-z]", "", (raw or "")).upper()


def validate_shaba(raw: str) -> Tuple[bool, str, str]:
    """شبا ایران: IR + ۲ رقم کنترل + ۲۲ رقم BBAN (مجموعاً ۲۶)؛ بررسی mod-97 == 1."""
    s = normalize_shaba(raw)
    if not s:
        return False, "شبا وارد نشده است.", ""
    if not s.startswith("IR"):
        if not s[:2].isdigit():  # بدون پیشوند، IR را اضافه کن
            s = "IR" + s
    if len(s) != 26:
        return False, "شبا باید ۲۶ کاراکتر باشد (IR + ۲ رقم کنترل + ۲۲ رقم BBAN).", s
    if not s[:2].isalpha() or not s[2:].isdigit():
        return False, "ساختار شبا نامعتبر است؛ بعد از IR فقط رقم مجاز است.", s
    if _iban_mod97(s[4:] + s[:4]) != 1:
        return False, "کنترل‌رقم شبا (IBAN) اشتباه است.", s
    return True, "", s


def validate_wallet_address(chain: str, address: str) -> Tuple[bool, str, str]:
    """اعتبار آدرس بر اساس زنجیره: طول و کاراکترهای مجاز هر شبکه."""
    a = (address or "").strip()
    chain = (chain or "").strip().lower()
    if not a:
        return False, "آدرس کیف پول وارد نشده است.", ""
    if chain in (CHAIN_BSC, CHAIN_ARBITRUM):
        if EVM_ADDR_RE.match(a):
            return True, "", a.lower()
        return False, "آدرس EVM باید با 0x و ۴۰ رقم هگز (مجموعاً ۴۲ کاراکتر) باشد.", a
    if chain == CHAIN_TRON:
        if TRON_ADDR_RE.match(a) or TRON_HEX_ADDR_RE.match(a):
            return True, "", a
        return False, "آدرس TRON باید با T و ۳۴ کاراکتر Base58 (یا هگز ۴۲ کاراکتری با پیشوند 41) باشد.", a
    if chain == CHAIN_TON:
        if TON_ADDR_RE.match(a) or TON_HEX_ADDR_RE.match(a):
            return True, "", a
        return False, "آدرس TON باید EQ/UQ + ۴۶ کاراکتر Base64 یا ۶۴ رقم هگز باشد.", a
    if len(a) < 20 or not re.match(r"^[0-9a-zA-Z:]+$", a):
        return False, "آدرس کیف پول نامعتبر است.", a
    return True, "", a


# ---------------- کارت‌ها ----------------
def list_cards(plan: Optional[str] = None, active_only: bool = False) -> List[Dict[str, Any]]:
    where, params = [], []
    if plan:
        where.append("plan = ?")
        params.append(normalize_plan(plan))
    if active_only:
        where.append("is_active = 1")
    cond = (" WHERE " + " AND ".join(where)) if where else ""
    return [dict(r) for r in db.q("SELECT * FROM payment_cards" + cond + " ORDER BY sort_order, id", params)]


def card_view(r: Dict[str, Any], *, admin: bool = False) -> Dict[str, Any]:
    masked = r.get("masked") or mask_card(r.get("card_number") or "")
    out = {
        "id": int(r["id"]),
        "plan": r.get("plan") or "",
        "holder_name": r.get("holder_name") or "",
        "bank_name": r.get("bank_name") or "",
        "bank_code": r.get("bank_code") or "",
        "shaba": r.get("shaba") or "",
        "masked": masked,
        "last4": (re.sub(r"\D", "", r.get("card_number") or ""))[-4:],
        "is_active": bool(r.get("is_active")),
        "sort_order": int(r.get("sort_order") or 0),
    }
    if admin:  # شماره کامل فقط به ادمین نشان داده می‌شود
        out["card_number"] = r.get("card_number") or ""
        out["note"] = r.get("note") or ""
    return out


def save_card(
    cid: Optional[int], plan: str, *, holder_name: str = "", card_number: str = "", shaba: str = "",
    bank_code: str = "", bank_name: str = "", note: str = "",
    is_active: Optional[bool] = None, sort_order: Optional[int] = None,
) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    plan = normalize_plan(plan)
    ok, err, digits = validate_card(card_number)
    if not ok:
        return False, err, None
    shaba_provided = bool((shaba or "").strip())
    iban = ""
    if shaba_provided:
        ok, err, iban = validate_shaba(shaba)
        if not ok:
            return False, err, None
    want_active = True if is_active is None else bool(is_active)
    if want_active:
        dup = db.q1(
            "SELECT id FROM payment_cards WHERE plan = ? AND is_active = 1 "
            "AND (card_number = ? OR (shaba <> '' AND shaba = ?)) AND id != ? LIMIT 1",
            (plan, digits, iban, int(cid or 0)),
        )
        if dup:
            return False, "این کارت/شبا قبلاً به‌عنوان مقصد فعال ثبت شده است.", None
    fields: Dict[str, Any] = {
        "holder_name": (holder_name or "").strip()[:80],
        "card_number": digits,
        "masked": mask_card(digits),
        "shaba": iban,
        "bank_code": (bank_code or "").strip()[:10],
        "bank_name": (bank_name or "").strip()[:60],
        "note": (note or "").strip()[:200],
    }
    if cid:
        sets = ", ".join(f"{k} = ?" for k in fields)
        params: tuple = tuple(fields.values())
        if is_active is not None:
            sets += ", is_active = ?"; params += (1 if want_active else 0,)
        if sort_order is not None:
            sets += ", sort_order = ?"; params += (int(sort_order),)
        db.x(f"UPDATE payment_cards SET {sets} WHERE id = ?", params + (int(cid),))
        row = db.q1("SELECT * FROM payment_cards WHERE id = ?", (int(cid),))
        return True, "", dict(row) if row else None
    cols = list(fields.keys()) + ["plan", "is_active"]
    vals = list(fields.values()) + [plan, 1 if want_active else 0]
    if sort_order is not None:
        cols.append("sort_order"); vals.append(int(sort_order))
    new_id = db.insert_id(
        f"INSERT INTO payment_cards({', '.join(cols)}) VALUES({', '.join('?' * len(cols))})", tuple(vals))
    row = db.q1("SELECT * FROM payment_cards WHERE id = ?", (new_id,))
    return True, "", dict(row) if row else None


def set_card_active(cid: int, active: bool) -> Tuple[bool, str]:
    r = db.q1("SELECT * FROM payment_cards WHERE id = ?", (int(cid),))
    if not r:
        return False, "کارت مقصد پیدا نشد."
    if active:
        dup = db.q1(
            "SELECT id FROM payment_cards WHERE plan = ? AND is_active = 1 "
            "AND (card_number = ? OR (shaba <> '' AND shaba = ?)) AND id != ? LIMIT 1",
            (r["plan"], r["card_number"], r["shaba"], int(cid)),
        )
        if dup:
            return False, "این کارت/شبا قبلاً به‌عنوان مقصد فعال ثبت شده است."
        if not (r["card_number"] or "").strip() and not (r["shaba"] or "").strip():
            return False, "این مقصد کامل نیست و قابل فعال‌سازی نیست."
    db.x("UPDATE payment_cards SET is_active = ? WHERE id = ?", (1 if active else 0, int(cid)))
    return True, ""


def delete_card(cid: int) -> bool:
    cur = db.x("DELETE FROM payment_cards WHERE id = ?", (int(cid),))
    return cur.rowcount > 0


def get_card(cid: int, active_only: bool = False) -> Optional[Dict[str, Any]]:
    sql = "SELECT * FROM payment_cards WHERE id = ?"
    if active_only:
        sql += " AND is_active = 1"
    r = db.q1(sql, (int(cid),))
    return dict(r) if r else None


# ---------------- کیف‌پول‌ها ----------------
def list_wallets(plan: Optional[str] = None, active_only: bool = False) -> List[Dict[str, Any]]:
    where, params = [], []
    if plan:
        where.append("plan = ?")
        params.append(normalize_plan(plan))
    if active_only:
        where.append("is_active = 1")
    cond = (" WHERE " + " AND ".join(where)) if where else ""
    return [dict(r) for r in db.q("SELECT * FROM payment_wallets" + cond + " ORDER BY sort_order, id", params)]


def wallet_view(r: Dict[str, Any], *, admin: bool = False) -> Dict[str, Any]:
    out = {
        "id": int(r["id"]),
        "plan": r.get("plan") or "",
        "network": r.get("network") or "",
        "chain": r.get("chain") or "",
        "asset": r.get("asset") or "",
        "network_label": r.get("network_label") or "",
        "address": r.get("address") or "",
        "amount": float(r.get("amount") or 0) or None,
        "is_active": bool(r.get("is_active")),
        "sort_order": int(r.get("sort_order") or 0),
    }
    if admin:
        out["note"] = r.get("note") or ""
    return out


def save_wallet(
    wid: Optional[int], plan: str, *, network: str = "", chain: str = "", asset: str = "",
    network_label: str = "", address: str = "", amount: float = 0, note: str = "",
    is_active: Optional[bool] = None, sort_order: Optional[int] = None,
) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    plan = normalize_plan(plan)
    chain_norm = (chain or "").strip().lower()
    ok, err, addr = validate_wallet_address(chain_norm, address)
    if not ok:
        return False, err, None
    want_active = True if is_active is None else bool(is_active)
    if want_active:
        dup = db.q1(
            "SELECT id FROM payment_wallets WHERE plan = ? AND is_active = 1 "
            "AND chain = ? AND LOWER(address) = LOWER(?) AND id != ? LIMIT 1",
            (plan, chain_norm, addr, int(wid or 0)),
        )
        if dup:
            return False, "این آدرس قبلاً به‌عنوان کیف‌پول فعال این شبکه ثبت شده است.", None
    fields: Dict[str, Any] = {
        "network": (network or "").strip()[:40],
        "chain": chain_norm,
        "asset": (asset or "").strip()[:20],
        "network_label": (network_label or "").strip()[:40],
        "address": addr,
        "amount": max(0.0, float(amount or 0)),
        "note": (note or "").strip()[:200],
    }
    if wid:
        sets = ", ".join(f"{k} = ?" for k in fields)
        params: tuple = tuple(fields.values())
        if is_active is not None:
            sets += ", is_active = ?"; params += (1 if want_active else 0,)
        if sort_order is not None:
            sets += ", sort_order = ?"; params += (int(sort_order),)
        db.x(f"UPDATE payment_wallets SET {sets} WHERE id = ?", params + (int(wid),))
        row = db.q1("SELECT * FROM payment_wallets WHERE id = ?", (int(wid),))
        return True, "", dict(row) if row else None
    cols = list(fields.keys()) + ["plan", "is_active"]
    vals = list(fields.values()) + [plan, 1 if want_active else 0]
    if sort_order is not None:
        cols.append("sort_order"); vals.append(int(sort_order))
    new_id = db.insert_id(
        f"INSERT INTO payment_wallets({', '.join(cols)}) VALUES({', '.join('?' * len(cols))})", tuple(vals))
    row = db.q1("SELECT * FROM payment_wallets WHERE id = ?", (new_id,))
    return True, "", dict(row) if row else None


def set_wallet_active(wid: int, active: bool) -> Tuple[bool, str]:
    r = db.q1("SELECT * FROM payment_wallets WHERE id = ?", (int(wid),))
    if not r:
        return False, "کیف‌پول مقصد پیدا نشد."
    if active:
        if not (r["address"] or "").strip():
            return False, "این کیف‌پول آدرس ندارد و قابل فعال‌سازی نیست."
        dup = db.q1(
            "SELECT id FROM payment_wallets WHERE plan = ? AND is_active = 1 "
            "AND chain = ? AND LOWER(address) = LOWER(?) AND id != ? LIMIT 1",
            (r["plan"], r["chain"], r["address"], int(wid)),
        )
        if dup:
            return False, "این آدرس قبلاً به‌عنوان کیف‌پول فعال این شبکه ثبت شده است."
    db.x("UPDATE payment_wallets SET is_active = ? WHERE id = ?", (1 if active else 0, int(wid)))
    return True, ""


def delete_wallet(wid: int) -> bool:
    cur = db.x("DELETE FROM payment_wallets WHERE id = ?", (int(wid),))
    return cur.rowcount > 0


def get_wallet(wid: int, active_only: bool = False) -> Optional[Dict[str, Any]]:
    sql = "SELECT * FROM payment_wallets WHERE id = ?"
    if active_only:
        sql += " AND is_active = 1"
    r = db.q1(sql, (int(wid),))
    return dict(r) if r else None


def checkout_destinations(plan: str) -> Dict[str, Any]:
    """مقصدهای فعال برای صفحه‌ی پرداخت. شماره کامل کارت و آدرس کیف‌پول در اینجا آمده‌اند،
    چون خریدار برای واریز به همین‌ها نیاز دارد؛ هیچ رازی (کلید API، یادداشت داخلی) برگردانده نمی‌شود."""
    plan = normalize_plan(plan)
    cards = [
        {
            "id": int(c["id"]),
            "holder_name": c.get("holder_name") or "",
            "bank_name": c.get("bank_name") or "",
            "card_number": re.sub(r"\D", "", c.get("card_number") or ""),
            "masked": c.get("masked") or mask_card(c.get("card_number") or ""),
            "last4": (re.sub(r"\D", "", c.get("card_number") or ""))[-4:],
            "shaba": c.get("shaba") or "",
        }
        for c in list_cards(plan, active_only=True)
    ]
    wallets = [
        {
            "id": int(w["id"]),
            "network": w.get("network") or "",
            "chain": w.get("chain") or "",
            "asset": w.get("asset") or "",
            "network_label": w.get("network_label") or "",
            "address": w.get("address") or "",
            "amount": float(w.get("amount") or 0) or None,
        }
        for w in list_wallets(plan, active_only=True)
    ]
    return {"cards": cards, "wallets": wallets}


# ----------------------------------------------------------------------------
# تنظیمات پلن
# ----------------------------------------------------------------------------
def plan_mode(plan: str) -> str:
    plan = normalize_plan(plan)
    mode = (db.get(_keys(plan)["mode"]) or "").strip().lower()
    if mode == "bsc":
        return MODE_AUTO
    return mode if mode in MODES else MODE_OFF


def set_plan_mode(plan: str, mode: str) -> None:
    mode = (mode or "").strip().lower()
    if mode not in MODES:
        mode = MODE_OFF
    db.set(_keys(normalize_plan(plan))["mode"], mode)


def plan_amount(plan: str) -> float:
    return db.get_float(_keys(normalize_plan(plan))["amount"])


def plan_days(plan: str) -> int:
    return max(1, db.get_int(_keys(normalize_plan(plan))["days"]))


def plan_renew_days(plan: str) -> int:
    return max(0, db.get_int(_keys(normalize_plan(plan))["renew"]))


def plan_template(plan: str) -> str:
    key = _keys(normalize_plan(plan))["template"]
    return db.get(key) or config.DEFAULT_SETTINGS.get(key, "")


def legacy_wallet(plan: str) -> str:
    plan = normalize_plan(plan)
    k = _keys(plan)
    if plan == PLAN_BOT and plan_mode(plan) == MODE_AUTO:
        return db.get(k["auto_wallet"]).strip()
    if plan == PLAN_WEBAPP and plan_mode(plan) == MODE_AUTO:
        return db.get(k["auto_wallet"]).strip()
    return db.get(k["legacy_wallet"]).strip()


def plan_enabled(plan: str) -> bool:
    return plan_mode(plan) != MODE_OFF


def configured(plan: str) -> bool:
    """آیا پلن قابل نمایش/خرید است؟ (حالت خاموش هرگز) — شبکه‌ی ارز، کارت بانکی یا کیف‌پول دستی کفایت می‌کند."""
    plan = normalize_plan(plan)
    if not plan_enabled(plan):
        return False
    if list_cards(plan, active_only=True) or list_wallets(plan, active_only=True):
        return True
    rows = network_rows(plan, enabled_only=True)
    if rows:
        return any(network_wallet(r) and network_amount(r, plan) > 0 for r in rows)
    return bool(legacy_wallet(plan)) and plan_amount(plan) > 0


def default_network(plan: str) -> Optional[Dict[str, Any]]:
    plan = normalize_plan(plan)
    rows = [r for r in network_rows(plan, enabled_only=True) if network_wallet(r)]
    if not rows:
        return None
    mode = plan_mode(plan)
    if mode == MODE_AUTO:
        for r in rows:
            if r["network_key"] == NET_USDT_BEP20:
                return r
    return rows[0]


# ----------------------------------------------------------------------------
# تخفیف
# ----------------------------------------------------------------------------
DISC_PERCENT = "percent"
DISC_FIXED = "fixed"
DISC_KINDS = (DISC_PERCENT, DISC_FIXED)


def discount_kind(plan: str) -> str:
    kind = (db.get(_keys(normalize_plan(plan))["disc_kind"]) or "").strip().lower()
    return kind if kind in DISC_KINDS else DISC_PERCENT


def discount_value(plan: str) -> float:
    """مقدار تخفیف بدون لحاظ کردن زمان: مبلغ ثابت یا درصد (بسته به نوع)."""
    k = _keys(normalize_plan(plan))
    return db.get_float(k["disc_fixed"]) if discount_kind(plan) == DISC_FIXED else db.get_float(k["disc_pct"])


def discount_pct(plan: str) -> float:
    """درصد تخفیفِ فعال؛ برای نوع «مبلغ ثابت» صفر برمی‌گردد."""
    k = _keys(normalize_plan(plan))
    return db.get_float(k["disc_pct"]) if (discount_active(plan) and discount_kind(plan) == DISC_PERCENT) else 0.0


def discount_end(plan: str) -> float:
    """پایان مؤثر تخفیف: پایان صریح اگر ثبت شده باشد، وگرنه «شروع + مدت»."""
    k = _keys(normalize_plan(plan))
    end = db.get_float(k["disc_end"])
    if end > 0:
        return end
    return db.get_float(k["disc_start"]) + db.get_int(k["disc_days"]) * 86400.0


def discount_until(plan: str) -> float:
    return discount_end(plan)


def discount_active(plan: str) -> bool:
    """تخفیف فقط در پنجره‌ی «شروع تا پایان» معتبر است؛ انقضا همین‌جا و هنگام محاسبه‌ی قیمت اعمال می‌شود."""
    k = _keys(normalize_plan(plan))
    if not db.get_bool(k["disc_on"]) or discount_value(plan) <= 0:
        return False
    start = db.get_float(k["disc_start"])
    now = time.time()
    return start > 0 and start <= now and now < discount_end(plan)


def apply_discount(plan: str, amount: float) -> float:
    """کسر تخفیف از یک مبلغ (درصد یا مبلغ ثابت)؛ مستقل از ارزیابی زمانی."""
    k = _keys(normalize_plan(plan))
    if discount_kind(plan) == DISC_FIXED:
        return round(max(0.0, float(amount) - db.get_float(k["disc_fixed"])), 2)
    pct = min(100.0, max(0.0, db.get_float(k["disc_pct"])))
    return round(float(amount) * (100.0 - pct) / 100.0, 2)


def final_amount(plan: str, amount: Optional[float] = None) -> Optional[float]:
    if not discount_active(plan):
        return None
    base = plan_amount(plan) if amount is None else float(amount)
    return apply_discount(plan, base)


def payable_amount(plan: str, row: Optional[Dict[str, Any]] = None) -> float:
    """منبع یکتای مبلغی که کاربر باید واقعاً بپردازد؛ تخفیف فعال روی مبلغ همان شبکه اعمال می‌شود.
    پیش‌تر مسیر وب‌اپ مبلغِ بدون تخفیف را به‌عنوان «مبلغ قابل پرداخت» ثبت می‌کرد و پرداختِ درستِ
    تخفیف‌دار را «کم» می‌شمرد؛ همه‌ی فراخوان‌ها اکنون از همین تابع عبور می‌کنند."""
    base = network_amount(row, plan)
    final = final_amount(plan, base)
    return float(base if final is None else final)


def discount_info(plan: str) -> Dict[str, Any]:
    """وضعیت کامل تخفیف برای پنل مدیریت (نوع، مقدار، شروع، پایان، فعال بودن)."""
    k = _keys(normalize_plan(plan))
    start = db.get_float(k["disc_start"])
    return {
        "on": db.get_bool(k["disc_on"]),
        "kind": discount_kind(plan),
        "pct": db.get_float(k["disc_pct"]),
        "fixed": db.get_float(k["disc_fixed"]),
        "days": db.get_int(k["disc_days"]),
        "start": start,
        "end": discount_end(plan) if start > 0 else 0.0,
        "active": discount_active(plan),
    }


def set_discount(
    plan: str, enabled: bool, pct: float = 0, days: int = 1, restart: bool = False, *,
    kind: Optional[str] = None, fixed: Optional[float] = None,
    start_now: Optional[bool] = None, starts_at: Optional[float] = None, ends_at: Optional[float] = None,
) -> None:
    """ذخیره‌ی تخفیف: (درصد یا مبلغ ثابت) + (شروع از حالا یا زمان‌بندی‌شده) + (مدت چند روزه یا پایان صریح).
    `restart` نام پیشین `start_now` بود و برای سازگاری پذیرفته می‌شود."""
    plan = normalize_plan(plan)
    k = _keys(plan)
    kind = (kind or "").strip().lower()
    if kind not in DISC_KINDS:
        kind = discount_kind(plan)
    pct = min(100.0, max(0.0, float(pct or 0)))
    fixed = max(0.0, float(fixed or 0))
    now = time.time()
    current_start = db.get_float(k["disc_start"])
    if start_now is None:
        start_now = bool(restart)
    if starts_at and float(starts_at) > 0:
        start = float(starts_at)
    elif start_now or current_start <= 0 or not discount_active(plan):
        start = now
    else:
        start = current_start
    if ends_at and float(ends_at) > 0:
        end = float(ends_at)
        days = max(1, int(round((end - start) / 86400.0))) if end > start else 1
    else:
        end = 0.0
        days = max(1, int(days or 1))
    db.set_many({
        k["disc_on"]: "1" if enabled else "0",
        k["disc_kind"]: kind,
        k["disc_pct"]: str(pct),
        k["disc_fixed"]: str(fixed),
        k["disc_days"]: str(days),
        k["disc_start"]: str(start if enabled else 0),
        k["disc_end"]: str(end) if (enabled and end > 0) else "0",
    })


# ----------------------------------------------------------------------------
# اشتراک رایگان
# ----------------------------------------------------------------------------
def free_on(plan: str) -> bool:
    return db.get_bool("free_bot_on" if normalize_plan(plan) == PLAN_BOT else "free_web_on")


def set_free_on(plan: str, on: bool) -> None:
    db.set("free_bot_on" if normalize_plan(plan) == PLAN_BOT else "free_web_on", "1" if on else "0")


def free_days(plan: str) -> int:
    return max(1, db.get_int("free_bot_days" if normalize_plan(plan) == PLAN_BOT else "free_web_days"))


def free_bot_feature_keys() -> List[str]:
    raw = (db.get("free_bot_features") or "").strip()
    if not raw:
        return [k for k, _ in FREE_BOT_FEATURES]
    if raw == NONE_SENTINEL:
        return []
    return _split_csv(raw)


def _active_web_strategy_rows() -> List[Dict[str, Any]]:
    return db.q(
        "SELECT strategy_key, category FROM structural_strategies WHERE is_active = 1 ORDER BY sort_order, id"
    )


def active_web_strategy_keys() -> List[str]:
    return [r["strategy_key"] for r in _active_web_strategy_rows()]


def web_category_options() -> List[Dict[str, Any]]:
    """دسته‌بندی‌های استراتژی‌های فعال با تعداد زیرمجموعه‌ها، برای فرم انتخاب ادمین."""
    counts: Dict[str, int] = {}
    for r in _active_web_strategy_rows():
        name = (r["category"] or "").strip()
        if name:
            counts[name] = counts.get(name, 0) + 1
    return [{"name": name, "count": counts[name]} for name in sorted(counts)]


def free_web_category_names() -> List[str]:
    """دسته‌بندی‌های انتخاب‌شده برای دوره‌ی رایگان وب‌اپ ('-' = هیچ)."""
    raw = (db.get("free_web_categories") or "").strip()
    if not raw or raw == NONE_SENTINEL:
        return []
    return _split_csv(raw)


def free_web_strategy_keys() -> List[str]:
    """کلیدهای مؤثر استراتژی‌های رایگان وب‌اپ: کلیدهای صریح + گسترش دسته‌بندی‌ها به استراتژی‌های فعالشان.
    خالی (بدون کلید و دسته) = همه‌ی استراتژی‌های فعال."""
    return _resolve_free_web_keys(db.get("free_web_strategies"), db.get("free_web_categories"))


def _resolve_free_web_keys(strat_raw: Any, cat_raw: Any) -> List[str]:
    """تک‌منبع گسترش انتخاب رایگان: کلیدهای صریح + اعضای زنده‌ی دسته‌ها (استراتژیِ افزوده‌شده‌ی بعدی هم پوشش می‌یابد)."""
    strat_raw = (strat_raw or "").strip()
    cat_raw = (cat_raw or "").strip()
    cats = [] if (not cat_raw or cat_raw == NONE_SENTINEL) else _split_csv(cat_raw)
    if not strat_raw and not cats:
        return active_web_strategy_keys()
    keys: Set[str] = set()
    if strat_raw and strat_raw != NONE_SENTINEL:
        keys.update(_split_csv(strat_raw))
    if cats:
        wanted = set(cats)
        keys.update(r["strategy_key"] for r in _active_web_strategy_rows() if (r["category"] or "").strip() in wanted)
    if not keys and strat_raw == NONE_SENTINEL and not cats:
        return []
    return sorted(keys)


def free_web_selection() -> Dict[str, Any]:
    """انتخاب خام ادمین (برای نمایش تیک‌ها در فرم) به‌همراه فهرست مؤثر استراتژی‌ها."""
    strat_raw = (db.get("free_web_strategies") or "").strip()
    cat_raw = (db.get("free_web_categories") or "").strip()
    return {
        "strategies": [] if strat_raw == NONE_SENTINEL else _split_csv(strat_raw),
        "categories": [] if cat_raw == NONE_SENTINEL else _split_csv(cat_raw),
        "effective": _resolve_free_web_keys(strat_raw, cat_raw),
    }


def _split_csv(raw: str) -> List[str]:
    out: List[str] = []
    for part in str(raw or "").replace("\n", ",").split(","):
        key = part.strip()
        if key and key not in out:
            out.append(key)
    return out


def free_claim(uid: int, plan: str) -> Optional[Dict[str, Any]]:
    r = db.q1(
        "SELECT * FROM free_sub_claims WHERE user_id = ? AND plan = ?",
        (int(uid), normalize_plan(plan)),
    )
    return dict(r) if r else None


def free_active(uid: int, plan: str) -> bool:
    row = free_claim(uid, plan)
    return bool(row and float(row.get("ends_at") or 0) > time.time())


def free_features(uid: int) -> Set[str]:
    row = free_claim(uid, PLAN_BOT)
    if not (row and float(row.get("ends_at") or 0) > time.time()):
        return set()
    raw = (row.get("features") or "").strip()
    if not raw:
        return set(free_bot_feature_keys())
    if raw == NONE_SENTINEL:
        return set()
    return set(_split_csv(raw))


def free_web_strategies_for(uid: int) -> Optional[Set[str]]:
    """None یعنی دسترسی کامل؛ مجموعه یعنی فقط همین کلیدها.
    کلیدهای صریح از اسنپ‌شات درخواست خوانده می‌شوند ولی دسته‌بندی‌ها هنگام ارزیابی به‌صورت زنده
    گسترش می‌یابند، تا استراتژی‌های تازه‌افزوده‌شده زیر آن دسته هم خودکار پوشش داده شوند."""
    row = free_claim(uid, PLAN_WEBAPP)
    if not (row and float(row.get("ends_at") or 0) > time.time()):
        return None
    if db.webapp_sub_until(uid) > float(row.get("ends_at") or 0) + 120:
        return None
    strat_raw = (row.get("strategies") or "").strip()
    cat_raw = (row.get("categories") or "").strip()
    cats = [] if (not cat_raw or cat_raw == NONE_SENTINEL) else _split_csv(cat_raw)
    if not strat_raw and not cats:
        return None  # «همه» → بدون محدودیت، شامل استراتژی‌های آینده
    keys: Set[str] = set()
    if strat_raw and strat_raw != NONE_SENTINEL:
        keys.update(_split_csv(strat_raw))
    if cats:
        wanted = set(cats)
        keys.update(r["strategy_key"] for r in _active_web_strategy_rows() if (r["category"] or "").strip() in wanted)
    if not keys and strat_raw == NONE_SENTINEL and not cats:
        return set()
    return keys


def bot_access_enforced() -> bool:
    return plan_enabled(PLAN_BOT) and configured(PLAN_BOT)


def bot_feature_allowed(uid: int, feature: str) -> bool:
    if int(uid) == int(config.ADMIN_UID):
        return True
    if not bot_access_enforced():
        return True
    if db.has_active_sub(uid):
        claim = free_claim(uid, PLAN_BOT)
        ends = float((claim or {}).get("ends_at") or 0)
        # اشتراک فعالِ رایگان فقط امکانات انتخاب‌شده را می‌دهد؛ اشتراک خریداری‌شده همه را.
        if not (claim and ends > time.time() and db.sub_until(uid) <= ends + 120):
            return True
    return str(feature) in free_features(uid)


def claim_free(uid: int, plan: str) -> Tuple[bool, str, Dict[str, Any]]:
    """فعال‌سازی دوره‌ی رایگان (فقط یک بار برای هر کاربر و هر پلن). خروجی: (موفق، پیام خطا، اطلاعات)."""
    uid = int(uid)
    plan = normalize_plan(plan)
    if not free_on(plan):
        return False, "دوره‌ی رایگان در حال حاضر فعال نیست.", {}
    if free_claim(uid, plan) is not None:
        return False, "شما قبلاً از دوره‌ی رایگان این پلن استفاده کرده‌اید.", {}
    if plan == PLAN_BOT and db.has_active_sub(uid):
        return False, "شما هم‌اکنون اشتراک فعال دارید.", {}
    if plan == PLAN_WEBAPP and db.has_active_webapp_sub(uid):
        return False, "شما هم‌اکنون اشتراک فعال تحلیل ساختاری دارید.", {}

    days = free_days(plan)
    now = time.time()
    ends = now + days * 86400.0
    prev = db.sub_until(uid) if plan == PLAN_BOT else db.webapp_sub_until(uid)
    if plan == PLAN_BOT:
        features = (",".join(free_bot_feature_keys())) or NONE_SENTINEL
        strategies = ""
        categories = ""
    else:
        # اسنپ‌شاتِ انتخابِ خام ادمین ذخیره می‌شود (خالی=همه، '-'=هیچ، csv=کلیدها/دسته‌ها) تا
        # ارزیابیِ بعدی بتواند دسته‌ها را زنده گسترش دهد و استراتژی‌های تازه‌افزوده هم پوشش یابند.
        features = NONE_SENTINEL
        strategies = (db.get("free_web_strategies") or "").strip()
        categories = (db.get("free_web_categories") or "").strip()
    try:
        db.insert_id(
            "INSERT INTO free_sub_claims"
            "(user_id, plan, starts_at, ends_at, prev_sub_until, features, strategies, categories, restored, warned) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?, 0, 0)",
            (uid, plan, now, ends, prev, features, strategies, categories),
        )
    except Exception:
        log.warning("free claim race for %s/%s", uid, plan)
        return False, "شما قبلاً از دوره‌ی رایگان این پلن استفاده کرده‌اید.", {}

    password = ""
    try:
        if plan == PLAN_BOT:
            until = db.grant_sub(uid, days)
        else:
            from webapp import activation

            until = db.grant_webapp_sub(uid, days)
            activation.ensure_access(uid)
            row = db.q1("SELECT password_hash FROM webapp_users WHERE user_id = ?", (uid,))
            if not (row and (row.get("password_hash") or "")):
                password = activation.issue_password(uid)
    except Exception:
        db.x("DELETE FROM free_sub_claims WHERE user_id = ? AND plan = ?", (uid, plan))
        log.exception("free claim grant failed for %s/%s", uid, plan)
        return False, "فعال‌سازی دوره‌ی رایگان انجام نشد؛ کمی بعد دوباره تلاش کنید.", {}
    return True, "", {"plan": plan, "days": days, "until": until, "password": password}


def free_claim_until(uid: int, plan: str) -> float:
    row = free_claim(uid, plan)
    return float((row or {}).get("ends_at") or 0)


def free_pending_warnings(now: Optional[float] = None, ahead: float = 86400.0) -> List[Dict[str, Any]]:
    now = time.time() if now is None else now
    return [
        dict(r) for r in db.q(
            "SELECT * FROM free_sub_claims WHERE restored = 0 AND warned = 0 AND ends_at > ? AND ends_at <= ?",
            (now, now + ahead),
        )
    ]


def mark_free_warned(claim_id: int) -> None:
    db.x("UPDATE free_sub_claims SET warned = 1 WHERE id = ?", (int(claim_id),))


def free_pending_expired(now: Optional[float] = None) -> List[Dict[str, Any]]:
    now = time.time() if now is None else now
    return [
        dict(r) for r in db.q(
            "SELECT * FROM free_sub_claims WHERE restored = 0 AND ends_at <= ? ORDER BY ends_at", (now,)
        )
    ]


def restore_expired_claim(row: Dict[str, Any]) -> Dict[str, Any]:
    """وضعیت قبلی کاربر را برمی‌گرداند؛ اگر اشتراک خریداری‌شده از پایان رایگان فراتر رفته باشد دست نمی‌زند."""
    uid = int(row["user_id"])
    plan = normalize_plan(row.get("plan"))
    ends = float(row.get("ends_at") or 0)
    prev = float(row.get("prev_sub_until") or 0)
    now = time.time()
    if plan == PLAN_BOT:
        current = db.sub_until(uid)
    else:
        current = db.webapp_sub_until(uid)
    paid_extension = current > ends + 120
    if not paid_extension:
        if plan == PLAN_BOT:
            db.set_sub_until(uid, prev if prev > now else 0)
            current = db.sub_until(uid)
        else:
            db.set_webapp_sub_until(uid, prev if prev > now else 0)
            current = db.webapp_sub_until(uid)
    db.x("UPDATE free_sub_claims SET restored = 1 WHERE id = ?", (int(row["id"]),))
    return {"uid": uid, "plan": plan, "paid_extension": paid_extension, "until": current}


def public_plan(plan: str) -> Dict[str, Any]:
    """اطلاعات پلن برای پنل مدیریت (شامل کلیدها و شبکه‌ها)."""
    plan = normalize_plan(plan)
    rows = []
    for r in network_rows(plan):
        d = dict(r)
        d["enabled"] = bool(d.get("enabled"))
        d["title"] = network_title(d)
        rows.append(d)
    return {
        "plan": plan,
        "label": PLAN_LABELS[plan],
        "mode": plan_mode(plan),
        "enabled": plan_enabled(plan),
        "configured": configured(plan),
        "amount": plan_amount(plan),
        "days": plan_days(plan),
        "renew_days": plan_renew_days(plan),
        "template": plan_template(plan),
        "discount": {
            **discount_info(plan),
            # «until» کلید سازگاری با فرم قبلی است (اکنون پایان مؤثر، صرف‌نظر از مدت/تاریخ).
            "until": discount_info(plan)["end"],
        },
        "free": {
            "on": free_on(plan),
            "days": free_days(plan),
            "features": free_bot_feature_keys() if plan == PLAN_BOT else [],
            "strategies": free_web_strategy_keys() if plan == PLAN_WEBAPP else [],
            "categories": free_web_category_names() if plan == PLAN_WEBAPP else [],
            "selected_strategies": free_web_selection()["strategies"] if plan == PLAN_WEBAPP else [],
            "selected_categories": free_web_selection()["categories"] if plan == PLAN_WEBAPP else [],
        },
        "networks": rows,
    }
