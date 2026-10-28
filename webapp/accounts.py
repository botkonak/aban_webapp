"""ثبت‌نام، پروفایل کاربر، ورود با کد یکبار مصرف (ippanel) و مدیریت کاربران وب."""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import secrets
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import parse_qsl

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import admin_notify
import config
from database import db, utc_cutoff
from webapp.auth import generate_password, hash_password, session_token, verify_password
from webapp.jalali import jalali_from_db, parse_db_ts
from webapp.repo import repo
from webapp.security_ext import log_audit

log = logging.getLogger("webapp.accounts")

USERNAME_RE = re.compile(r"^[a-z0-9_]{3,32}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")
PHONE_RE = re.compile(r"^\+989\d{9}$")
FIELD_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{1,31}$")
FIELD_TYPES = ("text", "email", "password", "tel", "number")
RESERVED_FIELDS = ("username", "password")
FA_DIGITS = "۰۱۲۳۴۵۶۷۸۹"
AR_DIGITS = "٠١٢٣٤٥٦٧٨٩"

# وضعیت رکورد otp_codes.used: 0 = در انتظار تأیید، 1 = مصرف/منقضی،
# OTP_SEND_FAILED = کد ساخته شد ولی به سرویس پیامک تحویل داده نشد (سقف روزانه را مصرف نمی‌کند).
OTP_SEND_FAILED = 2
# پس از هر ارسال ناموفق، فاصله‌ی ارسال دوباره دو برابر می‌شود (حداکثر ۳۰ دقیقه) تا قطعی
# سرویس کاربر را قفل نکند، ولی رگبار درخواست هم ممکن نباشد.
OTP_FAILURE_BACKOFF_STEPS = 4
OTP_FAILURE_MAX_WAIT = 1800


# ----------------------------------------------------------------------------
# مدل‌های ورودی
# ----------------------------------------------------------------------------
class RegisterBody(BaseModel):
    values: Dict[str, str] = {}


class ProfileBody(BaseModel):
    values: Dict[str, str] = {}
    current_password: str = ""
    new_password: str = ""


class FieldBody(BaseModel):
    field_key: Optional[str] = None
    label: Optional[str] = None
    field_type: Optional[str] = None
    is_required: Optional[bool] = None
    is_active: Optional[bool] = None
    sort_order: Optional[int] = None
    validation_regex: Optional[str] = None


class RegistrationSettingsBody(BaseModel):
    registration_open: Optional[bool] = None
    registration_password_min: Optional[int] = None


class WebUserBody(BaseModel):
    access_enabled: Optional[bool] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None


class OtpBody(BaseModel):
    phone: str = ""


class OtpVerifyBody(BaseModel):
    phone: str = ""
    code: str = ""


class OtpSettingsBody(BaseModel):
    otp_login_enabled: Optional[bool] = None
    ippanel_api_key: Optional[str] = None
    ippanel_sender: Optional[str] = None
    ippanel_pattern_code: Optional[str] = None
    otp_code_length: Optional[int] = None
    otp_expiry_seconds: Optional[int] = None
    otp_max_attempts: Optional[int] = None
    otp_resend_cooldown: Optional[int] = None
    otp_daily_limit_per_phone: Optional[int] = None


class TelegramBody(BaseModel):
    init_data: str = ""


# ----------------------------------------------------------------------------
# ابزار
# ----------------------------------------------------------------------------
def _setting(key: str, default: str = "") -> str:
    value = db.get(key)
    return value if value not in (None, "") else str(config.DEFAULT_SETTINGS.get(key, default))


def _int_setting(key: str, default: int, low: int, high: int) -> int:
    try:
        value = int(float(_setting(key, str(default))))
    except (TypeError, ValueError):
        return default
    return max(low, min(value, high))


def _digits(raw: str) -> str:
    text = str(raw or "")
    for i, ch in enumerate(FA_DIGITS):
        text = text.replace(ch, str(i))
    for i, ch in enumerate(AR_DIGITS):
        text = text.replace(ch, str(i))
    return text


def norm_phone(raw: str) -> str:
    """نرمال‌سازی شماره موبایل ایران به قالب +989xxxxxxxxx (خروجی خالی = نامعتبر)."""
    text = _digits(raw).strip().replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
    if text.startswith("00"):
        text = "+" + text[2:]
    if text.startswith("09"):
        text = "+98" + text[1:]
    elif text.startswith("9") and len(text) == 10:
        text = "+98" + text
    elif text.startswith("+98"):
        text = "+98" + text[3:]
    elif text.startswith("0098"):
        text = "+" + text[2:]
    return text if PHONE_RE.match(text) else ""


def mask_phone(phone: str) -> str:
    p = str(phone or "")
    return p[:5] + "***" + p[-2:] if len(p) > 8 else "***"


def _fields(active_only: bool = True) -> List[Dict[str, Any]]:
    sql = "SELECT * FROM registration_fields"
    if active_only:
        sql += " WHERE is_active = 1"
    sql += " ORDER BY sort_order, id"
    rows = [dict(r) for r in db.q(sql)]
    for row in rows:
        row["is_required"] = bool(row.get("is_required"))
        row["is_active"] = bool(row.get("is_active"))
    return rows


def _field_map() -> Dict[str, Dict[str, Any]]:
    return {str(r["field_key"]): r for r in _fields(False)}


def _public_field(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "key": row["field_key"],
        "label": row["label"],
        "type": row["field_type"] if row["field_type"] in FIELD_TYPES else "text",
        "required": bool(row.get("is_required")),
        "order": int(row.get("sort_order") or 0),
    }


def _clean_values(raw: Dict[str, str], fields: List[Dict[str, Any]], *, creating: bool) -> Dict[str, str]:
    allowed = {str(f["field_key"]): f for f in fields if f.get("is_active")}
    values: Dict[str, str] = {}
    for key, value in (raw or {}).items():
        key = str(key or "").strip()
        if key not in allowed:
            continue
        values[key] = str(value or "").strip()[:500]
    for key, field in allowed.items():
        value = values.get(key, "")
        label = field.get("label") or key
        if field.get("is_required") and not value:
            raise HTTPException(400, f"«{label}» را وارد کنید.")
        regex = str(field.get("validation_regex") or "").strip()
        if value and regex:
            try:
                if not re.match(regex, value):
                    raise HTTPException(400, f"مقدار «{label}» معتبر نیست.")
            except re.error:
                pass
        if key == "username" and value and not USERNAME_RE.match(value.lower()):
            raise HTTPException(400, "نام کاربری باید ۳ تا ۳۲ کاراکتر و شامل حروف کوچک انگلیسی، عدد یا _ باشد.")
        if key == "password" and value:
            minimum = _int_setting("registration_password_min", 8, 4, 64)
            if len(value) < minimum:
                raise HTTPException(400, f"رمز عبور باید حداقل {minimum} کاراکتر باشد.")
        if field.get("field_type") == "email" and value and not EMAIL_RE.match(value):
            raise HTTPException(400, f"«{label}» معتبر نیست.")
        if field.get("field_type") == "tel" and value:
            normalized = norm_phone(value)
            if not normalized:
                raise HTTPException(400, f"«{label}» معتبر نیست.")
            values[key] = normalized
    return values


def _next_uid() -> int:
    row = db.q1("SELECT MIN(user_id) AS m FROM users")
    low = int((row or {}).get("m") or 0)
    return (low if low < 0 else 0) - 1


def _insert_user(values: Dict[str, str], password: Optional[str], *, verified: bool = False) -> int:
    username = (values.get("username") or "").strip().lower()
    first = (values.get("first_name") or "").strip()[:120]
    last = (values.get("last_name") or "").strip()[:120]
    phone = norm_phone(values.get("phone") or "")
    email = (values.get("email") or "").strip()[:200]
    for _ in range(6):
        uid = _next_uid()
        try:
            db.x(
                "INSERT INTO users(user_id, phone_number, is_verified, first_name, last_name, username, joined_at) "
                "VALUES(?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)",
                (int(uid), phone or None, 1 if verified else 0, first, last, username),
            )
        except Exception:
            continue
        try:
            db.x(
                "INSERT INTO webapp_users(user_id, access_enabled, username, email, phone, first_name, last_name) "
                "VALUES(?, 1, ?, ?, ?, ?, ?)",
                (int(uid), username, email, phone, first, last),
            )
        except Exception:
            db.x("DELETE FROM users WHERE user_id = ?", (int(uid),))
            raise
        if password:
            repo.set_password(int(uid), password)
        else:
            repo.ensure_username(int(uid))
        for key, value in values.items():
            if key in RESERVED_FIELDS or not value:
                continue
            db.x(
                "INSERT INTO user_profiles(user_id, field_key, value) VALUES(?, ?, ?) "
                "ON CONFLICT(user_id, field_key) DO UPDATE SET value = excluded.value",
                (int(uid), key, str(value)[:500]),
            )
        return int(uid)
    raise HTTPException(503, "ساخت حساب انجام نشد؛ دوباره تلاش کنید.")


def _find_user_by_phone(phone: str) -> Optional[Dict[str, Any]]:
    p = norm_phone(phone)
    if not p:
        return None
    return db.q1(
        "SELECT * FROM users WHERE REPLACE(REPLACE(REPLACE(COALESCE(phone_number,''), ' ', ''), '-', ''), '+98', '0') = ? "
        "ORDER BY user_id DESC LIMIT 1",
        (p.replace("+98", "0"),),
    )


def _assert_not_blocked(uid: int) -> None:
    row = db.q1(
        "SELECT \"until\" FROM blocked_users WHERE user_id = ? AND \"until\" > ? LIMIT 1",
        (int(uid), time.time()),
    )
    if row is not None:
        raise HTTPException(403, "دسترسی شما موقتاً مسدود شده است؛ با پشتیبانی در ارتباط باشید.")


def _profile_values(uid: int) -> Dict[str, str]:
    row = repo.webapp_user(int(uid)) or {}
    values = {
        "username": str(row.get("username") or ""),
        "email": str(row.get("email") or ""),
        "phone": str(row.get("phone") or ""),
        "first_name": str(row.get("first_name") or ""),
        "last_name": str(row.get("last_name") or ""),
    }
    for item in db.q("SELECT field_key, value FROM user_profiles WHERE user_id = ?", (int(uid),)):
        values[str(item["field_key"])] = str(item.get("value") or "")
    return values


# ----------------------------------------------------------------------------
# OTP
# ----------------------------------------------------------------------------
def _hash_code(code: str, salt: str) -> str:
    return hashlib.sha256((salt + ":" + code).encode("utf-8")).hexdigest()


def _random_code(length: int) -> str:
    return "".join(secrets.choice("0123456789") for _ in range(max(4, min(int(length), 8))))


def _ippanel_config() -> Tuple[str, str, str]:
    return (
        _setting("ippanel_api_key").strip(),
        _setting("ippanel_sender").strip(),
        _setting("ippanel_pattern_code").strip(),
    )


def _send_pattern(phone: str, code: str) -> Tuple[bool, str]:
    api_key, sender, pattern = _ippanel_config()
    if not api_key or not sender or not pattern:
        return False, "تنظیمات پیامک کامل نیست."
    try:
        from ippanel import Client

        client = Client(api_key)
        # آرگومان‌ها فقط با نام ارسال می‌شوند تا تغییر ترتیب پارامترها در SDK، کد را جابه‌جا نکند.
        result = client.send_pattern(
            pattern_code=pattern, sender=sender, recipient=phone, params={"otp": str(code)}
        )
        if isinstance(result, dict) and result.get("error"):
            log.warning("ippanel send failed: %s", str(result.get("error"))[:200])
            return False, "ارسال پیامک انجام نشد."
        return True, ""
    except ImportError:
        log.error("ippanel sdk is not installed")
        return False, "کتابخانه پیامک روی سرور نصب نیست."
    except Exception as exc:
        log.warning("ippanel send failed: %s", str(exc)[:300])
        return False, "ارسال پیامک انجام نشد."


def _row_age(row: Optional[Dict[str, Any]]) -> float:
    if not row:
        return 1e12
    ts = parse_db_ts(row.get("created_at"))
    return time.time() - ts if ts else 1e12


def request_otp(phone_raw: str, ip: str) -> Dict[str, Any]:
    if not db.get_bool("otp_login_enabled"):
        raise HTTPException(403, "ورود با کد یکبار مصرف فعال نیست.")
    phone = norm_phone(phone_raw)
    if not phone:
        raise HTTPException(400, "شماره موبایل معتبر نیست.")
    cooldown = _int_setting("otp_resend_cooldown", 60, 10, 3600)
    latest = db.q1("SELECT * FROM otp_codes WHERE phone = ? ORDER BY id DESC LIMIT 1", (phone,))
    # پنجره‌ی ۲۴ ساعته باید با رشته‌ی زمان دیتابیس مقایسه شود؛ مقدار عددیِ epoch باعث
    # می‌شود مقایسه‌ی متنی همه‌ی رکوردها را «امروز» بشمارد و سقف روزانه عملاً همیشگی شود.
    since = utc_cutoff(hours=24)
    failed_today = int(db.q1(
        "SELECT COUNT(*) AS n FROM otp_codes WHERE phone = ? AND created_at >= ? AND used = ?",
        (phone, since, OTP_SEND_FAILED),
    )["n"] or 0)
    wait = min(cooldown * (2 ** min(failed_today, OTP_FAILURE_BACKOFF_STEPS)), OTP_FAILURE_MAX_WAIT)
    age = _row_age(latest)
    if age < wait:
        raise HTTPException(429, f"برای درخواست دوباره {max(1, int(wait - age))} ثانیه صبر کنید.")
    daily = _int_setting("otp_daily_limit_per_phone", 10, 1, 100)
    delivered_today = int(db.q1(
        "SELECT COUNT(*) AS n FROM otp_codes WHERE phone = ? AND created_at >= ? AND used <> ?",
        (phone, since, OTP_SEND_FAILED),
    )["n"] or 0)
    if delivered_today >= daily:
        raise HTTPException(429, "سقف ارسال کد یکبار مصرف برای امروز پر شده است؛ فردا دوباره تلاش کنید.")
    length = _int_setting("otp_code_length", 5, 4, 8)
    expiry = _int_setting("otp_expiry_seconds", 120, 30, 1800)
    code = _random_code(length)
    salt = secrets.token_hex(8)
    db.x("UPDATE otp_codes SET used = 1 WHERE phone = ? AND used = 0", (phone,))
    db.x(
        "INSERT INTO otp_codes(phone, code_hash, attempts, expires_at) VALUES(?, ?, 0, ?)",
        (phone, salt + "$" + _hash_code(code, salt), time.time() + expiry),
    )
    ok, error = _send_pattern(phone, code)
    if not ok:
        # کد به سرویس پیامک تحویل داده نشد؛ رکورد در جدول می‌ماند (برای محاسبه‌ی فاصله‌ی ارسال)
        # ولی با used = OTP_SEND_FAILED علامت می‌خورد تا نه قابل تأیید باشد و نه سقف روزانه را مصرف کند.
        db.x("UPDATE otp_codes SET used = ? WHERE phone = ? AND used = 0", (OTP_SEND_FAILED, phone))
        admin_notify.log_event(0, "otp_send_failed", f"{mask_phone(phone)} — {error}", ip, "warning")
        log_audit(0, "otp_send_failed", target_type="phone", target_id=mask_phone(phone), details={"error": error}, ip=ip)
        raise HTTPException(502, "ارسال پیامک انجام نشد؛ کمی بعد دوباره تلاش کنید.")
    admin_notify.log_event(0, "otp_request", mask_phone(phone), ip)
    log_audit(0, "otp_request", target_type="phone", target_id=mask_phone(phone), ip=ip)
    return {"ok": True, "phone": mask_phone(phone), "expires_in": expiry, "cooldown": wait}


def verify_otp(phone_raw: str, code_raw: str, ip: str) -> Optional[int]:
    if not db.get_bool("otp_login_enabled"):
        raise HTTPException(403, "ورود با کد یکبار مصرف فعال نیست.")
    phone = norm_phone(phone_raw)
    code = _digits(code_raw).strip().replace(" ", "")
    if not phone or not code:
        raise HTTPException(400, "شماره موبایل و کد را وارد کنید.")
    row = db.q1(
        "SELECT * FROM otp_codes WHERE phone = ? AND used = 0 ORDER BY id DESC LIMIT 1", (phone,)
    )
    if row is None:
        raise HTTPException(401, "کد یکبار مصرف معتبر نیست یا منقضی شده است.")
    if float(row.get("expires_at") or 0) <= time.time():
        db.x("UPDATE otp_codes SET used = 1 WHERE id = ?", (int(row["id"]),))
        raise HTTPException(401, "کد یکبار مصرف منقضی شده است؛ دوباره درخواست کنید.")
    maximum = _int_setting("otp_max_attempts", 3, 1, 10)
    attempts = int(row.get("attempts") or 0)
    try:
        salt, digest = str(row.get("code_hash") or "").split("$", 1)
    except ValueError:
        salt, digest = "", ""
    if not hmac.compare_digest(_hash_code(code, salt), digest):
        attempts += 1
        too_many = attempts >= maximum
        db.x(
            "UPDATE otp_codes SET attempts = ?, used = ? WHERE id = ?",
            (attempts, 1 if too_many else 0, int(row["id"])),
        )
        admin_notify.log_event(0, "otp_failed", mask_phone(phone), ip, "warning")
        log_audit(0, "otp_verify_failed", target_type="phone", target_id=mask_phone(phone),
                  details={"attempts": attempts, "locked": too_many}, ip=ip)
        if too_many:
            raise HTTPException(429, "تعداد تلاش‌های ناموفق زیاد است؛ کد جدید درخواست کنید.")
        raise HTTPException(401, "کد یکبار مصرف نادرست است.")
    db.x("UPDATE otp_codes SET used = 1 WHERE id = ?", (int(row["id"]),))
    user = _find_user_by_phone(phone)
    if user is None:
        # ورود با کد نباید درِ ثبت‌نام بسته را باز کند؛ حساب جدید فقط وقتی مجاز است
        # که مدیر ثبت‌نام را باز کرده باشد.
        if not db.get_bool("registration_open"):
            raise HTTPException(403, "ثبت‌نام فعلاً بسته است؛ برای ساخت حساب با پشتیبانی در ارتباط باشید.")
        uid = _insert_user({"phone": phone}, generate_password(10), verified=True)
    else:
        uid = int(user["user_id"])
        _assert_not_blocked(uid)
        repo.ensure_webapp_user(uid)
        current = repo.webapp_user(uid) or {}
        # کاربری که مدیر دسترسی‌اش را بسته است با ورود OTP دوباره فعال نمی‌شود.
        if not int(current.get("access_enabled") or 0):
            raise HTTPException(403, "دسترسی شما به وب‌اپ غیرفعال شده است. با پشتیبانی در ارتباط باشید.")
        repo.ensure_username(uid)
        if not repo.has_password(uid):
            repo.set_password(uid, generate_password(10))
        db.x(
            "UPDATE webapp_users SET phone = ?, updated_at = CURRENT_TIMESTAMP WHERE user_id = ?",
            (phone, uid),
        )
        if not (user.get("phone_number") or ""):
            db.x("UPDATE users SET phone_number = ?, is_verified = 1 WHERE user_id = ?", (phone, uid))
    repo.mark_login(int(uid))
    admin_notify.log_event(int(uid), "otp_login", mask_phone(phone), ip)
    log_audit(0, "otp_login", target_type="user", target_id=uid, details={"phone": mask_phone(phone)}, ip=ip)
    return int(uid)


# ----------------------------------------------------------------------------
# Telegram initData
# ----------------------------------------------------------------------------
def validate_init_data(init_data: str, bot_token: Optional[str] = None) -> Dict[str, Any]:
    token = (bot_token or config.BOT_TOKEN or "").strip()
    if not token:
        raise HTTPException(503, "توکن ربات تنظیم نشده است.")
    raw = str(init_data or "").strip()
    if not raw:
        raise HTTPException(401, "اطلاعات ورود تلگرام ارسال نشده است.")
    try:
        pairs = parse_qsl(raw, keep_blank_values=True, strict_parsing=False)
    except ValueError:
        raise HTTPException(401, "اطلاعات ورود تلگرام معتبر نیست.") from None
    data = dict(pairs)
    received = data.pop("hash", "")
    if not received:
        raise HTTPException(401, "اطلاعات ورود تلگرام معتبر نیست.")
    check = "\n".join(f"{k}={data[k]}" for k in sorted(data))
    secret = hmac.new(b"WebAppData", token.encode("utf-8"), hashlib.sha256).digest()
    expected = hmac.new(secret, check.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received.lower()):
        raise HTTPException(401, "اطلاعات ورود تلگرام معتبر نیست.")
    try:
        auth_date = int(data.get("auth_date") or 0)
    except ValueError:
        auth_date = 0
    if auth_date <= 0 or time.time() - auth_date > 86400:
        raise HTTPException(401, "اطلاعات ورود تلگرام منقضی شده است.")
    try:
        user = json.loads(data.get("user") or "{}")
    except ValueError:
        user = {}
    if not isinstance(user, dict) or not user.get("id"):
        raise HTTPException(401, "اطلاعات کاربر تلگرام معتبر نیست.")
    return user


# ----------------------------------------------------------------------------
# ثبت مسیرها
# ----------------------------------------------------------------------------
def register(
    app: FastAPI,
    *,
    require_admin: Callable[[Request], Dict[str, Any]],
    require_user: Callable[[Request], Dict[str, Any]],
    client_ip: Callable[[Request], str],
    set_session: Callable[[Any, Request, str, int], None],
) -> None:
    _require_admin = require_admin
    _require_user = require_user
    _ip = client_ip

    # ---------------------------- ثبت‌نام ----------------------------
    @app.get("/api/register/fields")
    async def register_fields():
        fields = _fields(True)
        return {
            "open": db.get_bool("registration_open"),
            "fields": [_public_field(f) for f in fields],
            "password_min": _int_setting("registration_password_min", 8, 4, 64),
            "otp_enabled": db.get_bool("otp_login_enabled"),
        }

    @app.post("/api/register")
    async def register_user(body: RegisterBody, request: Request):
        if not db.get_bool("registration_open"):
            raise HTTPException(403, "ثبت‌نام فعلاً بسته است.")
        fields = _fields(True)
        values = _clean_values(body.values or {}, fields, creating=True)
        if not values.get("username") or not values.get("password"):
            raise HTTPException(400, "نام کاربری و رمز عبور الزامی است.")
        username = values["username"].strip().lower()
        if repo.username_taken(username):
            raise HTTPException(409, "این نام کاربری قبلاً انتخاب شده است.")
        if username == values["password"].strip().lower():
            raise HTTPException(400, "رمز عبور نباید با نام کاربری یکسان باشد.")
        phone = norm_phone(values.get("phone") or "")
        if phone:
            if _find_user_by_phone(phone) is not None:
                raise HTTPException(409, "این شماره موبایل قبلاً ثبت شده است.")
            values["phone"] = phone
        values["username"] = username
        uid = _insert_user(values, values["password"])
        admin_notify.log_event(uid, "register", "ثبت‌نام کاربر وب", _ip(request))
        return {"ok": True, "redirect": "/login", "message": "ثبت‌نام انجام شد؛ اکنون وارد شوید."}

    @app.post("/api/auth/telegram")
    async def login_telegram(body: TelegramBody, request: Request):
        user = validate_init_data(body.init_data)
        try:
            uid = int(user.get("id") or 0)
        except (TypeError, ValueError):
            uid = 0
        if uid == config.ADMIN_UID:
            token = session_token(uid, "admin")
        else:
            _assert_not_blocked(uid)
            row = repo.webapp_user(uid)
            if row is None or not int(row.get("access_enabled") or 0):
                raise HTTPException(403, "برای استفاده از وب‌اپ ابتدا از ربات اشتراک تهیه کنید.")
            token = session_token(uid, "user")
        repo.mark_login(uid)
        response = JSONResponse({"ok": True, "redirect": "/"})
        set_session(response, request, token, config.WEBAPP_SESSION_DAYS)
        return response

    @app.post("/api/auth/otp/request")
    async def otp_request(body: OtpBody, request: Request):
        return request_otp(body.phone, _ip(request))

    @app.post("/api/auth/otp/verify")
    async def otp_verify(body: OtpVerifyBody, request: Request):
        uid = verify_otp(body.phone, body.code, _ip(request))
        response = JSONResponse({"ok": True, "redirect": "/"})
        set_session(response, request, session_token(int(uid or 0), "user"), config.WEBAPP_SESSION_DAYS)
        return response

    # ---------------------------- پروفایل ----------------------------
    @app.get("/api/me/profile")
    async def my_profile(request: Request):
        auth = _require_user(request)
        fields = _fields(True)
        return {
            "user_id": int(auth["uid"]),
            "username": str((repo.webapp_user(int(auth["uid"])) or {}).get("username") or ""),
            "fields": [_public_field(f) for f in fields if f["field_key"] not in RESERVED_FIELDS],
            "values": _profile_values(int(auth["uid"])),
            "has_password": repo.has_password(int(auth["uid"])),
        }

    @app.put("/api/me/profile")
    async def save_profile(body: ProfileBody, request: Request):
        auth = _require_user(request)
        uid = int(auth["uid"])
        values = dict(body.values or {})
        values.pop("username", None)
        values.pop("password", None)
        fields = [f for f in _fields(True) if f["field_key"] not in RESERVED_FIELDS]
        cleaned = _clean_values(values, fields, creating=False)
        first = cleaned.get("first_name")
        last = cleaned.get("last_name")
        email = cleaned.get("email")
        phone = cleaned.get("phone")
        if phone:
            phone = norm_phone(phone)
            if not phone:
                raise HTTPException(400, "شماره موبایل معتبر نیست.")
            existing = _find_user_by_phone(phone)
            if existing is not None and int(existing["user_id"]) != uid:
                raise HTTPException(409, "این شماره موبایل برای حساب دیگری ثبت شده است.")
            cleaned["phone"] = phone
        sets, params = [], []
        for column, value in (("first_name", first), ("last_name", last), ("email", email), ("phone", phone)):
            if value is not None:
                sets.append(f"{column} = ?")
                params.append(value)
        if first is not None or last is not None or phone is not None:
            user_sets, user_params = [], []
            if first is not None:
                user_sets.append("first_name = ?")
                user_params.append(first)
            if last is not None:
                user_sets.append("last_name = ?")
                user_params.append(last)
            if phone:
                user_sets.append("phone_number = ?")
                user_params.append(phone)
            if user_sets:
                db.x(
                    f"UPDATE users SET {', '.join(user_sets)} WHERE user_id = ?",
                    tuple(user_params) + (uid,),
                )
        if sets:
            db.x(
                f"UPDATE webapp_users SET {', '.join(sets)}, updated_at = CURRENT_TIMESTAMP WHERE user_id = ?",
                tuple(params) + (uid,),
            )
        for key, value in cleaned.items():
            if key in ("first_name", "last_name", "email", "phone"):
                continue
            db.x(
                "INSERT INTO user_profiles(user_id, field_key, value) VALUES(?, ?, ?) "
                "ON CONFLICT(user_id, field_key) DO UPDATE SET value = excluded.value",
                (uid, key, str(value)[:500]),
            )
        if body.new_password:
            if repo.has_password(uid) and not repo.check_password(uid, body.current_password):
                raise HTTPException(400, "رمز عبور فعلی نادرست است.")
            minimum = _int_setting("registration_password_min", 8, 4, 64)
            if len(body.new_password) < minimum:
                raise HTTPException(400, f"رمز عبور جدید باید حداقل {minimum} کاراکتر باشد.")
            repo.set_password(uid, body.new_password)
            admin_notify.log_event(uid, "password_change", "تغییر رمز از پروفایل", _ip(request))
        return {"ok": True, "message": "پروفایل ذخیره شد."}

    # ---------------------------- مدیریت فیلدهای ثبت‌نام ----------------------------
    @app.get("/api/admin/registration-fields")
    async def admin_fields(request: Request):
        _require_admin(request)
        return {
            "items": _fields(False),
            "types": list(FIELD_TYPES),
            "reserved": list(RESERVED_FIELDS),
            "settings": {
                "registration_open": db.get_bool("registration_open"),
                "registration_password_min": _int_setting("registration_password_min", 8, 4, 64),
            },
        }

    @app.put("/api/admin/registration-settings")
    async def admin_registration_settings(body: RegistrationSettingsBody, request: Request):
        _require_admin(request)
        updates: Dict[str, str] = {}
        if body.registration_open is not None:
            updates["registration_open"] = "1" if body.registration_open else "0"
        if body.registration_password_min is not None:
            updates["registration_password_min"] = str(max(4, min(int(body.registration_password_min), 64)))
        if updates:
            db.set_many(updates)
        return {"ok": True, "message": "تنظیمات ثبت‌نام ذخیره شد."}

    def _field_values(body: FieldBody, current: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        current = current or {}
        key = str(body.field_key if body.field_key is not None else current.get("field_key") or "").strip().lower()
        if not key or not FIELD_KEY_RE.match(key):
            raise HTTPException(400, "کلید فیلد فقط با حروف کوچک انگلیسی، عدد و _ مجاز است.")
        if current and key != current.get("field_key") and current.get("field_key") in RESERVED_FIELDS:
            raise HTTPException(400, "کلید فیلدهای سیستمی قابل تغییر نیست.")
        label = str(body.label if body.label is not None else current.get("label") or "").strip()[:80]
        if not label:
            raise HTTPException(400, "عنوان فیلد را وارد کنید.")
        ftype = str(body.field_type if body.field_type is not None else current.get("field_type") or "text")
        if ftype not in FIELD_TYPES:
            raise HTTPException(400, "نوع فیلد معتبر نیست.")
        regex = str(body.validation_regex if body.validation_regex is not None else current.get("validation_regex") or "").strip()[:300]
        if regex:
            try:
                re.compile(regex)
            except re.error:
                raise HTTPException(400, "الگوی اعتبارسنجی معتبر نیست.") from None
        return {
            "field_key": key,
            "label": label,
            "field_type": ftype,
            "is_required": 1 if (body.is_required if body.is_required is not None else current.get("is_required", 0)) else 0,
            "is_active": 1 if (body.is_active if body.is_active is not None else current.get("is_active", 1)) else 0,
            "sort_order": max(0, min(int(body.sort_order if body.sort_order is not None else current.get("sort_order") or 0), 999)),
            "validation_regex": regex,
        }

    @app.post("/api/admin/registration-fields")
    async def admin_field_create(body: FieldBody, request: Request):
        _require_admin(request)
        values = _field_values(body)
        if db.q1("SELECT id FROM registration_fields WHERE field_key = ?", (values["field_key"],)):
            raise HTTPException(409, "این کلید قبلاً ثبت شده است.")
        fid = db.insert_id(
            "INSERT INTO registration_fields(field_key, label, field_type, is_required, is_active, sort_order, validation_regex) "
            "VALUES(?, ?, ?, ?, ?, ?, ?)",
            (values["field_key"], values["label"], values["field_type"], values["is_required"],
             values["is_active"], values["sort_order"], values["validation_regex"]),
        )
        return {"ok": True, "id": fid, "message": "فیلد ثبت‌نام اضافه شد."}

    @app.put("/api/admin/registration-fields/{fid}")
    async def admin_field_update(fid: int, body: FieldBody, request: Request):
        _require_admin(request)
        current = db.q1("SELECT * FROM registration_fields WHERE id = ?", (int(fid),))
        if current is None:
            raise HTTPException(404, "فیلد پیدا نشد.")
        values = _field_values(body, dict(current))
        if values["field_key"] != current.get("field_key") and db.q1(
            "SELECT id FROM registration_fields WHERE field_key = ? AND id != ?", (values["field_key"], int(fid))
        ):
            raise HTTPException(409, "این کلید قبلاً ثبت شده است.")
        if current.get("field_key") in RESERVED_FIELDS:
            values["is_active"] = 1
            values["is_required"] = 1
        db.x(
            "UPDATE registration_fields SET field_key = ?, label = ?, field_type = ?, is_required = ?, "
            "is_active = ?, sort_order = ?, validation_regex = ? WHERE id = ?",
            (values["field_key"], values["label"], values["field_type"], values["is_required"],
             values["is_active"], values["sort_order"], values["validation_regex"], int(fid)),
        )
        return {"ok": True, "message": "فیلد ثبت‌نام ذخیره شد."}

    @app.delete("/api/admin/registration-fields/{fid}")
    async def admin_field_delete(fid: int, request: Request):
        _require_admin(request)
        current = db.q1("SELECT * FROM registration_fields WHERE id = ?", (int(fid),))
        if current is None:
            raise HTTPException(404, "فیلد پیدا نشد.")
        if current.get("field_key") in RESERVED_FIELDS:
            raise HTTPException(400, "فیلدهای سیستمی قابل حذف نیستند.")
        db.x("DELETE FROM registration_fields WHERE id = ?", (int(fid),))
        return {"ok": True, "message": "فیلد حذف شد."}

    # ---------------------------- کاربران وب ----------------------------
    def _web_user_row(row: Dict[str, Any]) -> Dict[str, Any]:
        uid = int(row["user_id"])
        return {
            "user_id": uid,
            "username": row.get("username") or "",
            "email": row.get("email") or "",
            "phone": row.get("phone") or row.get("phone_number") or "",
            "first_name": row.get("first_name") or "",
            "last_name": row.get("last_name") or "",
            "access_enabled": bool(row.get("access_enabled") or 0),
            "has_password": bool(row.get("password_hash")),
            "joined_at": jalali_from_db(row.get("joined_at")),
            "last_login": jalali_from_db(row.get("last_login_at")) if row.get("last_login_at") else "-",
            "webapp_until": db.webapp_sub_until(uid),
        }

    @app.get("/api/admin/web-users")
    async def admin_web_users(request: Request, q: str = "", page: int = 1, page_size: int = 20):
        _require_admin(request)
        page = max(1, int(page))
        page_size = max(5, min(int(page_size), 100))
        where, params = "", []
        if q.strip():
            like = "%" + q.strip().lower() + "%"
            where = (
                " WHERE LOWER(COALESCE(w.username,'')) LIKE ? OR LOWER(COALESCE(w.email,'')) LIKE ? "
                "OR LOWER(COALESCE(w.phone,'')) LIKE ? OR LOWER(COALESCE(u.first_name,'')) LIKE ? "
                "OR LOWER(COALESCE(u.last_name,'')) LIKE ? OR CAST(w.user_id AS TEXT) LIKE ?"
            )
            params = [like] * 6
        base = " FROM webapp_users w LEFT JOIN users u ON u.user_id = w.user_id" + where
        total = int(db.q1("SELECT COUNT(*) AS n" + base, params)["n"] or 0)
        rows = db.q(
            "SELECT w.*, u.phone_number, u.first_name AS u_first, u.last_name AS u_last, u.joined_at" + base +
            " ORDER BY w.user_id DESC LIMIT ? OFFSET ?",
            params + [page_size, (page - 1) * page_size],
        )
        for row in rows:
            row["first_name"] = row.get("first_name") or row.get("u_first") or ""
            row["last_name"] = row.get("last_name") or row.get("u_last") or ""
        return {"items": [_web_user_row(r) for r in rows], "total": total, "page": page, "page_size": page_size}

    @app.put("/api/admin/web-users/{uid}")
    async def admin_web_user_update(uid: int, body: WebUserBody, request: Request):
        _require_admin(request)
        row = db.q1("SELECT * FROM webapp_users WHERE user_id = ?", (int(uid),))
        if row is None:
            raise HTTPException(404, "کاربر وب پیدا نشد.")
        if body.access_enabled is not None:
            repo.set_access(int(uid), bool(body.access_enabled))
        sets, params = [], []
        for column in ("first_name", "last_name", "email"):
            value = getattr(body, column)
            if value is not None:
                sets.append(f"{column} = ?")
                params.append(str(value).strip()[:200])
        if body.phone is not None:
            phone = norm_phone(body.phone) if body.phone.strip() else ""
            sets.append("phone = ?")
            params.append(phone)
            db.x("UPDATE users SET phone_number = ? WHERE user_id = ?", (phone or None, int(uid)))
        if sets:
            db.x(
                f"UPDATE webapp_users SET {', '.join(sets)}, updated_at = CURRENT_TIMESTAMP WHERE user_id = ?",
                tuple(params) + (int(uid),),
            )
        return {"ok": True, "message": "کاربر ذخیره شد."}

    # ---------------------------- تنظیمات OTP ----------------------------
    @app.get("/api/admin/otp")
    async def admin_otp(request: Request):
        _require_admin(request)
        return {
            "settings": {
                "otp_login_enabled": db.get_bool("otp_login_enabled"),
                "ippanel_sender": _setting("ippanel_sender"),
                "ippanel_pattern_code": _setting("ippanel_pattern_code"),
                "otp_code_length": _int_setting("otp_code_length", 5, 4, 8),
                "otp_expiry_seconds": _int_setting("otp_expiry_seconds", 120, 30, 1800),
                "otp_max_attempts": _int_setting("otp_max_attempts", 3, 1, 10),
                "otp_resend_cooldown": _int_setting("otp_resend_cooldown", 60, 10, 3600),
                "otp_daily_limit_per_phone": _int_setting("otp_daily_limit_per_phone", 10, 1, 100),
                "ippanel_api_key_set": bool(_setting("ippanel_api_key").strip()),
            },
        }

    @app.put("/api/admin/otp")
    async def admin_otp_save(body: OtpSettingsBody, request: Request):
        _require_admin(request)
        updates: Dict[str, str] = {}
        if body.otp_login_enabled is not None:
            updates["otp_login_enabled"] = "1" if body.otp_login_enabled else "0"
        for key in ("ippanel_sender", "ippanel_pattern_code"):
            value = getattr(body, key)
            if value is not None:
                updates[key] = str(value).strip()[:120]
        api_key = (body.ippanel_api_key or "").strip()
        if api_key and api_key not in ("********", "••••••••"):
            updates["ippanel_api_key"] = api_key[:300]
        ranges = {
            "otp_code_length": (4, 8), "otp_expiry_seconds": (30, 1800), "otp_max_attempts": (1, 10),
            "otp_resend_cooldown": (10, 3600), "otp_daily_limit_per_phone": (1, 100),
        }
        for key, (low, high) in ranges.items():
            value = getattr(body, key)
            if value is not None:
                updates[key] = str(max(low, min(int(value), high)))
        if updates:
            db.set_many(updates)
        return {"ok": True, "message": "تنظیمات پیامک ذخیره شد."}

    @app.post("/api/admin/otp/test")
    async def admin_otp_test(body: OtpBody, request: Request):
        auth = _require_admin(request)
        phone = norm_phone(body.phone)
        if not phone:
            raise HTTPException(400, "شماره موبایل معتبر نیست.")
        length = _int_setting("otp_code_length", 5, 4, 8)
        code = _random_code(length)
        ok, error = _send_pattern(phone, code)
        if not ok:
            admin_notify.log_event(int(auth["uid"]), "otp_test_failed", mask_phone(phone) + " — " + error, _ip(request), "warning")
            log_audit(int(auth["uid"]), "otp_test_failed", target_type="phone", target_id=mask_phone(phone),
                      details={"error": error}, ip=_ip(request))
            raise HTTPException(502, "ارسال پیامک آزمایشی انجام نشد؛ تنظیمات را بررسی کنید.")
        admin_notify.log_event(int(auth["uid"]), "otp_test", mask_phone(phone), _ip(request))
        log_audit(int(auth["uid"]), "otp_test", target_type="phone", target_id=mask_phone(phone), ip=_ip(request))
        return {"ok": True, "message": "پیامک آزمایشی ارسال شد."}
