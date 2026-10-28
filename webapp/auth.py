"""احراز هویت وب‌اپ: JWT سبک (HMAC-SHA256) و هش رمز عبور با PBKDF2 (بدون وابستگی خارجی)."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any, Dict, Optional

import config

COOKIE_NAME = "aban_session"
ADMIN_LINK_TYPE = "admin_link"
SESSION_TYPE = "session"
PASSWORD_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
PBKDF2_ITERATIONS = 260_000


class TokenError(ValueError):
    pass


def _b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64d(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def _sign(payload: str) -> str:
    return _b64e(hmac.new(config.WEBAPP_SECRET.encode("utf-8"), payload.encode("ascii"), hashlib.sha256).digest())


def encode_token(payload: Dict[str, Any]) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    h = _b64e(json.dumps(header, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    p = _b64e(json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    body = f"{h}.{p}"
    return f"{body}.{_sign(body)}"


def decode_token(token: str, expected_type: Optional[str] = None) -> Dict[str, Any]:
    try:
        h, p, s = (token or "").split(".")
    except ValueError:
        raise TokenError("bad token") from None
    body = f"{h}.{p}"
    if not hmac.compare_digest(s, _sign(body)):
        raise TokenError("bad signature")
    try:
        payload = json.loads(_b64d(p).decode("utf-8"))
    except Exception:
        raise TokenError("bad payload") from None
    if not isinstance(payload, dict):
        raise TokenError("bad payload")
    exp = float(payload.get("exp") or 0)
    if exp <= time.time():
        raise TokenError("expired")
    if expected_type and payload.get("type") != expected_type:
        raise TokenError("wrong type")
    return payload


def session_token(uid: int, role: str = "user") -> str:
    now = int(time.time())
    days = 1 if role == "admin" else config.WEBAPP_SESSION_DAYS
    return encode_token({
        "sub": str(int(uid)), "role": role, "type": SESSION_TYPE,
        "iat": now, "exp": now + days * 86400,
    })


def admin_link_token(uid: int) -> str:
    now = int(time.time())
    return encode_token({
        "sub": str(int(uid)), "role": "admin", "type": ADMIN_LINK_TYPE,
        "iat": now, "exp": now + config.WEBAPP_ADMIN_LINK_MINUTES * 60,
    })


def admin_login_url() -> str:
    base = config.WEBAPP_URL.rstrip("/")
    return f"{base}/admin?token={admin_link_token(config.ADMIN_UID)}"


def login_url() -> str:
    return f"{config.WEBAPP_URL.rstrip('/')}/login"


def public_site() -> str:
    return config.WEBAPP_PUBLIC_HOST or config.WEBAPP_URL.rstrip("/")


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${_b64e(salt)}${_b64e(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iters, salt_b64, digest_b64 = (stored or "").split("$")
        if algo != "pbkdf2_sha256":
            return False
        salt = _b64d(salt_b64)
        expected = _b64d(digest_b64)
        actual = hashlib.pbkdf2_hmac("sha256", (password or "").encode("utf-8"), salt, int(iters))
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def generate_password(length: int = 10) -> str:
    return "".join(secrets.choice(PASSWORD_ALPHABET) for _ in range(max(8, length)))


class LoginLimiter:
    """محدودیت ساده و درون‌حافظه‌ای برای تلاش ورود ناموفق."""

    def __init__(self, limit: int = 8, window: int = 300, block: int = 600):
        self.limit = limit
        self.window = window
        self.block = block
        self._hits: Dict[str, list[float]] = {}
        self._blocked_until: Dict[str, float] = {}

    def blocked_for(self, key: str) -> int:
        left = self._blocked_until.get(key, 0) - time.time()
        return max(0, int(left))

    def hit(self, key: str) -> None:
        now = time.time()
        arr = [t for t in self._hits.get(key, []) if now - t < self.window]
        arr.append(now)
        self._hits[key] = arr
        if len(arr) >= self.limit:
            self._blocked_until[key] = now + self.block
            self._hits[key] = []

    def clear(self, key: str) -> None:
        self._hits.pop(key, None)
        self._blocked_until.pop(key, None)
