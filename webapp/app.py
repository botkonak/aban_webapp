"""برنامه‌ی FastAPI وب‌اپ تحلیل ساختاری آبان ترید (همان پورت ربات تلگرام)."""
from __future__ import annotations

import asyncio
import hmac
import ipaddress
import json
import logging
import mimetypes
import re
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set
from urllib.parse import urlencode

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from telegram import Update

import config
import payment_flow
import payments
import plans
from database import db
from webapp import accounts, activation, admin_extra, content, logos, notifications, security_ext, subscription_web, ticker
from webapp import security as web_security
from webapp.auth import (
    ADMIN_LINK_TYPE,
    COOKIE_NAME,
    SESSION_TYPE,
    LoginLimiter,
    TokenError,
    decode_token,
    session_token,
)
from webapp.jalali import TEHRAN, jalali_dt, jalali_from_db, jalali_ts
from webapp.market import (
    SCANNER_TFS,
    SYMBOL_TFS,
    TF_LABEL,
    TF_MINUTES,
    market,
    norm_symbol,
)
from webapp.repo import repo
from webapp.runtime import runtime
from webapp.scanner import ScanRequest, scanner
from webapp.uploads import read_image

log = logging.getLogger("webapp.app")

WEB_DIR = Path(__file__).resolve().parent / "web"
UPLOAD_ROOT = Path(config.WEBAPP_UPLOAD_DIR)
if not UPLOAD_ROOT.is_absolute():
    UPLOAD_ROOT = config.BASE_DIR / UPLOAD_ROOT
GUIDE_DIR = UPLOAD_ROOT / "guides"
GUIDE_DIR.mkdir(parents=True, exist_ok=True)

MAX_GUIDE_BYTES = 5 * 1024 * 1024
MAX_MESSAGE_CHARS = 4000
SLUG_RE = re.compile(r"^[a-z0-9_-]{2,32}$")

login_limiter = LoginLimiter()
login_limiter_global = LoginLimiter(limit=120, window=300, block=300)


# ----------------------------------------------------------------------------
# مدل‌های ورودی
# ----------------------------------------------------------------------------
class LoginBody(BaseModel):
    username: str = ""
    password: str = ""


class ScanBody(BaseModel):
    timeframe: Optional[str] = None
    depth: Optional[int] = None
    mode: Optional[str] = None
    symbols_mode: Optional[str] = None
    symbol: str = ""
    strategy_keys: Optional[List[str]] = None
    exchanges: Optional[List[str]] = None
    exchanges_all: Optional[bool] = None


class SettingsBody(BaseModel):
    notify_on: Optional[bool] = None
    notify_template: Optional[str] = None
    scan_timeframe: Optional[str] = None
    scan_depth: Optional[int] = None
    scan_mode: Optional[str] = None
    scan_symbols_mode: Optional[str] = None
    scan_symbol: Optional[str] = None
    scan_strategy_keys: Optional[List[str]] = None
    scan_exchanges: Optional[List[str]] = None
    exchanges_all: Optional[bool] = None


class FavoriteBody(BaseModel):
    kind: str = "symbol"
    value: str = ""


class AccessBody(BaseModel):
    enabled: bool


class PasswordBody(BaseModel):
    days: int = 0


class SubscriptionBody(BaseModel):
    action: str
    days: int = 0


class SingleMessageBody(BaseModel):
    text: str = ""


class MessageBody(BaseModel):
    mode: str = "single"
    text: str = ""
    user_id: Optional[int] = None
    sub_filter: str = "all"
    joined_days: int = 0


class PaymentActionBody(BaseModel):
    action: str
    note: str = ""
    tx_hash: str = ""
    amount: Optional[float] = None
    network: str = ""
    sender: str = ""


class StrategyToggleBody(BaseModel):
    active: bool


class ExchangeBody(BaseModel):
    name: str = ""
    slug: str = ""
    ccxt_id: str = ""
    api_url: str = ""
    priority: int = 100
    is_active: bool = True
    is_visible: bool = True


class ExchangeFlagBody(BaseModel):
    field: str
    value: bool


class AdminSettingsBody(BaseModel):
    web_notify_template: Optional[str] = None
    web_default_timeframe: Optional[str] = None


class TickerItemBody(BaseModel):
    symbol: str = ""
    label: Optional[str] = ""
    exchange: Optional[str] = ""
    enabled: bool = True


class TickerBody(BaseModel):
    enabled: Optional[bool] = None
    speed: Optional[int] = None
    show_change: Optional[bool] = None
    source: Optional[str] = None
    items: Optional[List[TickerItemBody]] = None


# ----------------------------------------------------------------------------
# احراز هویت
# ----------------------------------------------------------------------------
def _session_from_request(request: Request) -> Optional[Dict[str, Any]]:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        auth = request.headers.get("authorization") or ""
        if auth.lower().startswith("bearer "):
            token = auth[7:].strip()
    if not token:
        return None
    try:
        payload = decode_token(token, SESSION_TYPE)
    except TokenError:
        return None
    try:
        uid = int(payload.get("sub") or 0)
    except (TypeError, ValueError):
        return None
    role = payload.get("role")
    if role == "admin":
        if uid != config.ADMIN_UID:
            return None
        return {"uid": uid, "role": "admin"}
    if role != "user":
        return None
    row = repo.webapp_user(uid)
    if not row or not int(row.get("access_enabled") or 0) or not (row.get("password_hash") or ""):
        return None
    return {"uid": uid, "role": "user"}


def _require_user(request: Request) -> Dict[str, Any]:
    auth = _session_from_request(request)
    if auth is None:
        raise HTTPException(401, "برای ادامه وارد شوید.")
    return auth


def _require_admin(request: Request) -> Dict[str, Any]:
    auth = _session_from_request(request)
    if auth is None:
        raise HTTPException(401, "برای ادامه وارد شوید.")
    if auth.get("role") != "admin":
        raise HTTPException(403, "این بخش فقط برای مدیر است.")
    return auth


def _require_premium(auth: Dict[str, Any]) -> None:
    if auth.get("role") == "admin":
        return
    if not db.has_active_webapp_sub(int(auth["uid"])):
        raise HTTPException(403, "برای دسترسی به این بخش، اشتراک تحلیل ساختاری فعال لازم است.")


def _secure_request(request: Request) -> bool:
    proto = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower()
    return request.url.scheme == "https" or proto == "https"


def _set_session(response: Response, request: Request, token: str, days: int) -> None:
    response.set_cookie(
        COOKIE_NAME, token, max_age=max(1, int(days)) * 86400,
        httponly=True, samesite="lax", secure=bool(config.WEBAPP_COOKIE_SECURE or _secure_request(request)), path="/",
    )


def _clear_session(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/")


def _trusted_proxies() -> List[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    out = []
    for item in str(config.WEBAPP_TRUSTED_PROXIES or "").split(","):
        item = item.strip()
        if not item:
            continue
        try:
            out.append(ipaddress.ip_network(item, strict=False))
        except ValueError:
            log.warning("invalid WEBAPP_TRUSTED_PROXIES entry ignored: %s", item)
    return out


_TRUSTED_PROXIES = _trusted_proxies()


def _is_trusted_proxy(host: str) -> bool:
    try:
        ip = ipaddress.ip_address(str(host or "").strip())
    except ValueError:
        return False
    return any(ip in net for net in _TRUSTED_PROXIES)


def _client_key(request: Request) -> str:
    """IP کاربر را فقط از زنجیره‌ی پروکسی مورد اعتماد می‌خواند تا جعل هدر ممکن نباشد."""
    peer = request.client.host if request.client else "unknown"
    if not _is_trusted_proxy(peer):
        return peer or "unknown"
    raw = request.headers.get("x-forwarded-for") or ""
    chain = [part.strip() for part in raw.split(",") if part.strip()]
    for candidate in reversed(chain):
        try:
            ip = ipaddress.ip_address(candidate)
        except ValueError:
            continue
        if not _is_trusted_proxy(str(ip)):
            return str(ip)
    return chain[0] if chain else peer


# ----------------------------------------------------------------------------
# اطلاعات کاربر / اشتراک
# ----------------------------------------------------------------------------
def _user_row(uid: int) -> Dict[str, Any]:
    return db.q1("SELECT * FROM users WHERE user_id = ?", (int(uid),)) or {}


def _display_name(uid: int, row: Optional[Dict[str, Any]] = None) -> str:
    if uid == config.ADMIN_UID:
        return "مدیریت آبان"
    row = row if row is not None else _user_row(uid)
    name = " ".join(x for x in ((row.get("first_name") or ""), (row.get("last_name") or "")) if x).strip()
    if name:
        return name
    if row.get("username"):
        return str(row["username"])
    if row.get("phone_number"):
        return str(row["phone_number"])
    return "کاربر آبان"


def _days_left(until: float) -> int:
    rem = float(until or 0) - time.time()
    return max(0, int((rem + 86399) // 86400)) if rem > 0 else 0


def _subscription_info(uid: int, role: str = "user") -> Dict[str, Any]:
    if role == "admin":
        return {
            "active": True, "unlimited": True, "until": 0, "until_jalali": "-",
            "days_left": 0, "mode": "admin", "amount": 0, "days": 0,
            "configured": False, "wallet": "", "destinations": {"cards": [], "wallets": []},
        }
    until = db.webapp_sub_until(uid)
    now = time.time()
    discount = plans.final_amount(plans.PLAN_WEBAPP)
    networks = [
        {
            "key": r["network_key"],
            "title": plans.network_title(r),
            "label": plans.network_button_label(r),
            "wallet": plans.network_wallet(r),
            "amount": plans.network_amount(r, plans.PLAN_WEBAPP),
        }
        for r in plans.network_rows(plans.PLAN_WEBAPP, enabled_only=True)
        if plans.network_wallet(r)
    ]
    return {
        "active": until > now,
        "unlimited": False,
        "until": until,
        "until_jalali": jalali_ts(until) if until else "-",
        "days_left": _days_left(until),
        "mode": plans.plan_mode(plans.PLAN_WEBAPP),
        "amount": plans.plan_amount(plans.PLAN_WEBAPP),
        "discount_amount": discount,
        "days": plans.plan_days(plans.PLAN_WEBAPP),
        "renew_days": plans.plan_renew_days(plans.PLAN_WEBAPP),
        "configured": plans.configured(plans.PLAN_WEBAPP),
        "wallet": plans.network_wallet(plans.default_network(plans.PLAN_WEBAPP)),
        "networks": networks,
        "template": plans.plan_template(plans.PLAN_WEBAPP),
        "destinations": plans.checkout_destinations(plans.PLAN_WEBAPP),
        "free": {
            "on": plans.free_on(plans.PLAN_WEBAPP),
            "days": plans.free_days(plans.PLAN_WEBAPP),
            "claimed": plans.free_claim(uid, plans.PLAN_WEBAPP) is not None,
        },
    }


def _bot_url() -> str:
    username = (config.BOT_USERNAME or "").strip().lstrip("@")
    return f"https://t.me/{username}" if username else ""


def _user_context(auth: Dict[str, Any]) -> Dict[str, Any]:
    uid = int(auth["uid"])
    role = auth.get("role") or "user"
    row = _user_row(uid)
    return {
        "uid": uid,
        "role": role,
        "is_admin": role == "admin",
        "name": _display_name(uid, row),
        "username": row.get("username") or "",
        "first_name": row.get("first_name") or "",
        "last_name": row.get("last_name") or "",
        "phone": row.get("phone_number") or "",
        "joined_at": jalali_from_db(row.get("joined_at")) if row.get("joined_at") else "-",
        "verified": bool(row.get("is_verified")),
        "subscription": _subscription_info(uid, role),
        "site": config.WEBAPP_PUBLIC_HOST,
        "bot_url": _bot_url(),
    }


def _safe_json(raw: str, default):
    try:
        value = json.loads(raw or "")
        return value if isinstance(value, type(default)) else default
    except (TypeError, ValueError):
        return default


def _active_exchange_slugs() -> List[str]:
    return [str(r["slug"]) for r in repo.exchange_rows(active_only=True)]


def _visible_exchanges() -> List[Dict[str, Any]]:
    return [
        {"slug": str(r["slug"]), "name": str(r["name"]), "priority": int(r["priority"] or 100)}
        for r in repo.exchange_rows(visible_only=True, active_only=True)
    ]


def _clean_exchanges(values: Sequence[str], all_flag: bool) -> List[str]:
    allowed = set(_active_exchange_slugs())
    out: List[str] = []
    for slug in values or []:
        slug = str(slug).strip().lower()
        if slug in allowed and slug not in out:
            out.append(slug)
    return out


def _today_start() -> float:
    now = datetime.now(TEHRAN)
    return now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def _scan_payload(req: ScanRequest) -> Dict[str, Any]:
    return {
        "timeframe": req.timeframe,
        "depth": req.depth,
        "mode": req.mode,
        "symbols_mode": req.symbols_mode,
        "symbol": req.symbol,
        "strategy_keys": list(req.strategy_keys),
        "exchanges": list(req.exchanges),
        "exchanges_all": bool(req.exchanges_all),
    }


def _strategy_scope(auth: Dict[str, Any]) -> Optional[Set[str]]:
    """کلیدهای مجاز برای کاربر رایگان؛ None یعنی دسترسی کامل."""
    if auth.get("role") == "admin":
        return None
    return plans.free_web_strategies_for(int(auth["uid"]))


def _scan_request_from(auth: Dict[str, Any], body: ScanBody, *, single: bool = False) -> ScanRequest:
    uid = int(auth["uid"])
    cfg = repo.scan_config(uid)
    timeframe = (body.timeframe or cfg["timeframe"] or "1h").strip()
    if timeframe not in TF_MINUTES:
        timeframe = "1h"
    depth = cfg["depth"] if body.depth is None else int(body.depth)
    depth = max(0, min(depth, 120))
    mode = (body.mode or cfg["mode"] or "any").strip().lower()
    mode = "all" if mode == "all" else "any"
    symbols_mode = "single" if single else (body.symbols_mode or cfg["symbols_mode"] or "all").strip().lower()
    if symbols_mode not in ("all", "favorites", "single"):
        symbols_mode = "all"
    exchanges_all = cfg["exchanges_all"] if body.exchanges_all is None else bool(body.exchanges_all)
    exchanges = cfg["exchanges"] if body.exchanges is None else list(body.exchanges)
    exchanges = _clean_exchanges(exchanges, exchanges_all)
    keys = cfg["strategy_keys"] if body.strategy_keys is None else list(body.strategy_keys)
    keys = [str(k).strip() for k in keys if str(k).strip()]
    scope = _strategy_scope(auth)
    if scope is not None:
        keys = [k for k in keys if k in scope] if keys else sorted(scope)
    return ScanRequest(
        timeframe=timeframe, depth=depth, mode=mode, symbols_mode=symbols_mode,
        symbol=(body.symbol or cfg.get("symbol") or "").strip(),
        strategy_keys=keys, exchanges=exchanges, exchanges_all=exchanges_all, user_id=uid,
    )


def _decorate_signals(uid: int, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    fav = set(repo.favorites(uid).get("signal") or [])
    out: List[Dict[str, Any]] = []
    for item in items:
        row = dict(item)
        row["timeframe_label"] = TF_LABEL.get(row.get("timeframe", ""), row.get("timeframe", ""))
        sid = str(row.get("id") or "")
        row["is_favorite"] = bool(sid and sid != "0" and sid in fav)
        row.setdefault("overlay", {})
        out.append(row)
    return out


async def _run_scan(auth: Dict[str, Any], req: ScanRequest, *, record: bool = False) -> Dict[str, Any]:
    _require_premium(auth)
    if not req.strategy_keys:
        raise HTTPException(400, "حداقل یک استراتژی برای اسکن انتخاب کنید.")
    if req.symbols_mode == "single" and not norm_symbol(req.symbol):
        raise HTTPException(400, "نماد مورد نظر را وارد کنید.")
    started = time.perf_counter()
    try:
        results = await asyncio.wait_for(scanner.scan(req, persist=True, use_cache=True), timeout=240)
    except asyncio.TimeoutError:
        raise HTTPException(504, "زمان اسکن بیش از حد طولانی شد؛ تعداد استراتژی‌ها یا نمادها را کاهش دهید.") from None
    took = round(time.perf_counter() - started, 2)
    uid = int(auth["uid"])
    if record:
        try:
            repo.add_search(uid, _scan_payload(req), len(results))
        except Exception:
            log.exception("save search history failed")
    return {
        "count": len(results),
        "results": _decorate_signals(uid, results),
        "filters": _scan_payload(req),
        "timeframe_label": TF_LABEL.get(req.timeframe, req.timeframe),
        "took": took,
    }


# ----------------------------------------------------------------------------
# پاسخ‌های ثابت
# ----------------------------------------------------------------------------
def _error_detail(exc: HTTPException) -> str:
    return str(exc.detail)


def _guide_url(path: str) -> str:
    path = (path or "").strip()
    if not path:
        return ""
    if path.startswith("http://") or path.startswith("https://") or path.startswith("/"):
        return path
    return "/" + path.lstrip("/")


def _strategy_public(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "key": row["strategy_key"],
        "title": row["title"],
        "category": row["category"],
        "strategy_type": row["strategy_type"],
        "signal_type": row["signal_type"],
        "text_color": row.get("text_color") or ("green" if row["signal_type"] == "حمایت" else "red"),
        "button_color": row.get("button_color") or "success",
        "description": row.get("description") or "",
        "active": bool(row.get("is_active", 1)),
        "guide_image": _guide_url(row.get("guide_image") or ""),
        "guide_width": int(row.get("guide_width") or 0),
        "guide_height": int(row.get("guide_height") or 0),
    }


def _payment_admin(row: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(row)
    net = plans.NETWORK_BY_KEY.get(str(out.get("network") or ""))
    out["asset"] = str((net or {}).get("asset") or "")
    out["is_card"] = (
        not str(out.get("network") or "").strip()
        and bool(re.fullmatch(r"\d{16}", str(out.get("receiver_address") or "")))
    )
    if out.get("request_id"):
        req = db.get_sub_request(int(out["request_id"]))
        out["has_media"] = bool(req and req.photo_id)
        out["request_text"] = (req.text if req else "") or ""
        out["request_status"] = (req.status if req else "") or ""
    else:
        out["has_media"] = False
        out["request_text"] = ""
        out["request_status"] = ""
    return out


def _payment_summary(uid: int) -> Dict[str, Any]:
    rows = db.q(
        "SELECT service_type, status, amount, created_at FROM payments_history "
        "WHERE user_id = ? ORDER BY id",
        (int(uid),),
    )
    out = {
        "bot": {"amount": 0.0, "start": "-", "count": 0},
        "webapp": {"amount": 0.0, "start": "-", "count": 0},
    }
    for row in rows:
        service = "webapp" if row.get("service_type") == payments.SERVICE_WEBAPP else "bot"
        if row.get("status") != payments.APPROVED:
            continue
        out[service]["amount"] += float(row.get("amount") or 0)
        out[service]["count"] += 1
        if out[service]["start"] == "-":
            out[service]["start"] = jalali_from_db(row.get("created_at"))
    out["bot"]["amount"] = round(out["bot"]["amount"], 6)
    out["webapp"]["amount"] = round(out["webapp"]["amount"], 6)
    return out


# ----------------------------------------------------------------------------
# ساخت برنامه
# ----------------------------------------------------------------------------
def create_app(application=None) -> FastAPI:
    if application is not None:
        runtime.attach(application)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            plans.ensure_defaults()
        except Exception:
            log.exception("plans defaults failed")
        try:
            repo.ensure_defaults()
        except Exception:
            log.exception("webapp defaults failed")
        try:
            content.ensure_defaults()
        except Exception:
            log.exception("content defaults failed")
        GUIDE_DIR.mkdir(parents=True, exist_ok=True)
        tasks = [asyncio.create_task(logos.refresh_loop(), name="webapp-logos")]
        if runtime.bot is not None:
            tasks.append(asyncio.create_task(notifications.notification_loop(runtime.bot), name="webapp-notify"))
        try:
            yield
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            try:
                await market.close()
            except Exception:
                pass

    app = FastAPI(
        title=config.WEBAPP_TITLE,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.add_middleware(GZipMiddleware, minimum_size=1200)
    app.add_middleware(web_security.UploadLimitMiddleware, require_admin=_require_admin, require_user=_require_user)

    @app.middleware("http")
    async def _headers(request: Request, call_next):
        if "tgWebAppData" in request.query_params:
            clean = [(k, v) for k, v in request.query_params.multi_items() if k != "tgWebAppData"]
            query = urlencode(clean)
            target = request.url.replace(query=query)
            return RedirectResponse(str(target), status_code=307)
        blocked, seconds_left = await web_security.enforce(_client_key(request), request.url.path)
        if blocked:
            return JSONResponse(
                {"error": f"دسترسی شما محدود شده است؛ {max(1, seconds_left // 60)} دقیقه دیگر تلاش کنید.", "status": 429},
                status_code=429,
            )
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        if _secure_request(request):
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        if request.url.path.startswith("/assets/") and response.status_code == 200:
            response.headers.setdefault("Cache-Control", "no-cache")
        if request.url.path.startswith("/api/"):
            # پاسخ‌های API نام کاربری/رمز/توکن/OTP دارند؛ کش شدنشان از طریق پروکسی
            # یا مرورگر مشترک آن‌ها را برای نفر بعدی نگه می‌دارد.
            response.headers.setdefault("Cache-Control", "no-store")
        if request.url.path.startswith("/uploads/"):
            response.headers.setdefault(
                "Content-Security-Policy",
                "default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'; sandbox",
            )
        if request.method in ("POST", "PUT", "PATCH", "DELETE") and request.url.path.startswith("/api/admin/"):
            try:
                auth = _session_from_request(request)
                if auth and auth.get("role") == "admin":
                    security_ext.log_audit(
                        int(auth["uid"]), f"{request.method} {request.url.path}",
                        ip=_client_key(request),
                    )
            except Exception:
                log.exception("admin audit middleware failed")
        return response

    @app.exception_handler(HTTPException)
    async def _http_error(request: Request, exc: HTTPException):
        return JSONResponse({"error": _error_detail(exc), "status": exc.status_code}, status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError):
        return JSONResponse({"error": "درخواست نامعتبر است.", "status": 422}, status_code=422)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        log.exception("unhandled webapp error: %s", exc)
        return JSONResponse({"error": "خطای غیرمنتظره رخ داد؛ کمی بعد دوباره تلاش کنید.", "status": 500}, status_code=500)

    assets_dir = WEB_DIR / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    static_dir = WEB_DIR / "static"
    static_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
    for folder in ("branding", "media", "guides"):
        public_dir = UPLOAD_ROOT / folder
        public_dir.mkdir(parents=True, exist_ok=True)
        app.mount(f"/uploads/{folder}", StaticFiles(directory=str(public_dir)), name=f"uploads-{folder}")

    # ------------------------------------------------------------------
    # سلامت و وب‌هوک تلگرام
    # ------------------------------------------------------------------
    @app.get("/health")
    async def health():
        db_ok = True
        try:
            db.q1("SELECT 1 AS x")
        except Exception:
            db_ok = False
        payload = {
            "status": "ok" if db_ok else "degraded",
            "db": db_ok,
            "bot": runtime.bot is not None,
            "uptime": int(time.time() - runtime.started_at),
        }
        return JSONResponse(payload, status_code=200 if db_ok else 503)

    @app.post("/telegram/webhook/{secret_path}")
    async def telegram_webhook(secret_path: str, request: Request):
        if secret_path != config.WEBHOOK_PATH:
            raise HTTPException(404, "not found")
        if runtime.application is None or runtime.bot is None:
            raise HTTPException(503, "bot not ready")
        if config.WEBHOOK_SECRET:
            token = request.headers.get("x-telegram-bot-api-secret-token", "")
            if not hmac.compare_digest(str(token), str(config.WEBHOOK_SECRET)):
                raise HTTPException(403, "forbidden")
        try:
            payload = await request.json()
            update = Update.de_json(payload, runtime.bot)
            if update is None:
                return {"ok": True}
            await runtime.application.update_queue.put(update)
        except Exception:
            log.exception("telegram webhook failed")
            raise HTTPException(400, "bad update") from None
        return {"ok": True}

    # ------------------------------------------------------------------
    # احراز هویت
    # ------------------------------------------------------------------
    @app.get("/api/auth/session")
    async def auth_session(request: Request):
        auth = _session_from_request(request)
        if auth is None:
            return {"authenticated": False}
        return {"authenticated": True, "user": _user_context(auth)}

    async def _do_login(body: LoginBody, request: Request):
        key = _client_key(request)
        blocked = max(login_limiter.blocked_for(key), login_limiter_global.blocked_for("all"))
        if blocked > 0:
            raise HTTPException(429, f"تلاش‌های ناموفق زیاد بوده است؛ {max(1, blocked // 60)} دقیقه دیگر دوباره تلاش کنید.")
        password = (body.password or "").strip()
        username = (body.username or "").strip()
        if not password:
            raise HTTPException(400, "رمز عبور را وارد کنید.")
        row = None
        if username:
            candidate = repo.find_by_username(username)
            if candidate is not None:
                from webapp.auth import verify_password

                if verify_password(password, candidate.get("password_hash") or ""):
                    row = candidate
        else:
            row = repo.find_password(password)
        if row is None:
            login_limiter.hit(key)
            login_limiter_global.hit("all")
            raise HTTPException(401, "نام کاربری یا رمز عبور نادرست است." if username else "رمز عبور نادرست است یا برای چند حساب مشترک است؛ نام کاربری را وارد کنید.")
        uid = int(row["user_id"])
        if uid == config.ADMIN_UID:
            raise HTTPException(401, "ورود مدیر فقط از طریق لینک اختصاصی ربات انجام می‌شود.")
        if not int(row.get("access_enabled") or 0):
            raise HTTPException(403, "دسترسی شما به وب‌اپ غیرفعال شده است. با پشتیبانی در ارتباط باشید.")
        blocked_row = db.q1(
            "SELECT \"until\" FROM blocked_users WHERE user_id = ? AND \"until\" > ? LIMIT 1",
            (uid, time.time()),
        )
        if blocked_row is not None:
            raise HTTPException(403, "دسترسی شما موقتاً مسدود شده است؛ با پشتیبانی در ارتباط باشید.")
        login_limiter.clear(key)
        repo.mark_login(uid)
        repo.ensure_username(uid)
        response = JSONResponse({"ok": True, "redirect": "/"})
        _set_session(response, request, session_token(uid, "user"), config.WEBAPP_SESSION_DAYS)
        return response

    @app.post("/api/auth/login")
    async def auth_login(body: LoginBody, request: Request):
        return await _do_login(body, request)

    @app.post("/api/login")
    async def api_login(body: LoginBody, request: Request):
        return await _do_login(body, request)

    @app.post("/api/auth/logout")
    async def auth_logout():
        response = JSONResponse({"ok": True})
        _clear_session(response)
        return response

    # ------------------------------------------------------------------
    # کاربر
    # ------------------------------------------------------------------
    @app.get("/api/me")
    async def api_me(request: Request):
        auth = _require_user(request)
        return _user_context(auth)

    @app.get("/api/dashboard")
    async def api_dashboard(request: Request):
        auth = _require_user(request)
        uid = int(auth["uid"])
        is_admin = auth.get("role") == "admin"
        premium = is_admin or db.has_active_webapp_sub(uid)
        stats = repo.stats()
        latest_rows = repo.latest_signals(24) if premium else []
        scope = _strategy_scope(auth)
        if scope is not None:
            # سیگنال استراتژی‌های غیرمجاز نباید در داشبورد کاربر رایگان دیده شود.
            latest_rows = [r for r in latest_rows if str(r.get("strategy_key") or "") in scope]
        latest = _decorate_signals(uid, latest_rows[:8])
        today = _today_start()
        signals_today = int(db.q1(
            "SELECT COUNT(*) AS n FROM structural_signals WHERE candle_ts >= ?",
            (today,),
        )["n"] or 0) if premium else 0
        week = []
        for i in range(6, -1, -1):
            start = today - i * 86400
            n = int(db.q1(
                "SELECT COUNT(*) AS n FROM structural_signals WHERE candle_ts >= ? AND candle_ts < ?",
                (start, start + 86400),
            )["n"] or 0) if premium else 0
            week.append(n)
        if premium:
            mix = db.q1(
                "SELECT COALESCE(SUM(CASE WHEN signal_type = 'حمایت' THEN 1 ELSE 0 END), 0) AS s, "
                "COALESCE(SUM(CASE WHEN signal_type = 'مقاومت' THEN 1 ELSE 0 END), 0) AS r "
                "FROM structural_signals WHERE is_active = 1"
            ) or {"s": 0, "r": 0}
        else:
            mix = {"s": 0, "r": 0}
        history, total = repo.search_rows(uid, 0, 4)
        try:
            fng = await market.fear_greed()
            fng_history = await market.fear_greed_history(30)
        except Exception:
            fng, fng_history = {"value": 50, "label": "خنثی"}, []
        public_stats = {
            "strategies": stats.get("strategies", 0),
            "categories": stats.get("categories", 0),
            "searches_24h": repo.user_count_searches_24h(uid),
        }
        if is_admin:
            public_stats.update(stats)
        public_stats.update({"signals_today": signals_today, "signals_week": week})
        return {
            "user": _user_context(auth),
            "premium": premium,
            "fear_greed": fng,
            "fear_history": fng_history,
            "stats": public_stats,
            "mix": {"support": int(mix["s"] or 0), "resistance": int(mix["r"] or 0)},
            "latest": latest,
            "recent_searches": history,
            "recent_searches_total": total,
        }

    @app.get("/api/strategies")
    async def api_strategies(request: Request):
        auth = _require_user(request)
        scope = _strategy_scope(auth)
        groups = repo.strategy_groups(active_only=True)
        out_groups = []
        for group in groups:
            items = [
                _strategy_public({**s, "strategy_key": s["key"]})
                for s in group["strategies"]
                if scope is None or s["key"] in scope
            ]
            if not items:
                continue
            out_groups.append({**group, "strategies": items, "count": len(items)})
        return {
            "groups": out_groups,
            "total": sum(g["count"] for g in out_groups),
            "favorites": repo.favorites(int(auth["uid"])).get("strategy") or [],
            "restricted": scope is not None,
        }

    @app.get("/api/exchanges")
    async def api_exchanges(request: Request):
        auth = _require_user(request)
        cfg = repo.scan_config(int(auth["uid"]))
        return {
            "exchanges": _visible_exchanges(),
            "selected": cfg["exchanges"],
            "all": cfg["exchanges_all"],
        }

    @app.get("/api/fear-greed")
    async def api_fear_greed(request: Request):
        _require_user(request)
        return await market.fear_greed()

    @app.get("/api/fear-greed/history")
    async def api_fear_greed_history(request: Request):
        _require_user(request)
        return {"items": await market.fear_greed_history(30)}

    @app.get("/api/ticker")
    async def api_ticker(request: Request):
        """نوار قیمت زندهٔ وب‌اپ؛ پیکربندی آن در پیشخوان مدیریت است."""
        _require_user(request)
        try:
            return await asyncio.wait_for(ticker.snapshot(), timeout=35)
        except asyncio.TimeoutError:
            return {"enabled": False, "items": [], "error": "دریافت قیمت‌ها زمان‌بر شد."}

    @app.get("/api/symbols/search")
    async def api_symbol_search(request: Request, q: str = "", limit: int = 20):
        _require_user(request)
        limit = max(1, min(int(limit), 50))
        try:
            items = await asyncio.wait_for(market.search(q, limit), timeout=40)
        except asyncio.TimeoutError:
            raise HTTPException(504, "دریافت فهرست نمادها زمان‌بر شد؛ دوباره تلاش کنید.") from None
        return {"results": items, "query": q}

    @app.get("/api/symbols/quick")
    async def api_symbols_quick(request: Request, limit: int = 16):
        _require_user(request)
        limit = max(1, min(int(limit), 40))
        try:
            items = await asyncio.wait_for(market.search("", limit), timeout=40)
        except asyncio.TimeoutError:
            raise HTTPException(504, "دریافت فهرست نمادها زمان‌بر شد؛ دوباره تلاش کنید.") from None
        return {"results": items}

    @app.get("/api/assets/{base}/logo")
    async def api_asset_logo(base: str):
        name = logos.normalize_base(base)
        fallback = WEB_DIR / "assets" / "icons" / "coin-default.svg"
        if not name or not logos.BASE_RE.match(name):
            return FileResponse(fallback, media_type="image/svg+xml", headers={"Cache-Control": "public, max-age=86400"})
        url = logos.cached_base(name)
        if url.startswith("https://"):
            return RedirectResponse(url, status_code=307, headers={"Cache-Control": "public, max-age=604800, immutable"})
        return FileResponse(fallback, media_type="image/svg+xml", headers={"Cache-Control": "public, max-age=86400"})

    @app.get("/api/symbol/{symbol}/header")
    async def api_symbol_header(symbol: str, request: Request):
        auth = _require_user(request)
        cfg = repo.scan_config(int(auth["uid"]))
        try:
            ref = await asyncio.wait_for(market.resolve(symbol, cfg["exchanges"], cfg["exchanges_all"]), timeout=45)
        except asyncio.TimeoutError:
            raise HTTPException(504, "دریافت اطلاعات نماد زمان‌بر شد؛ دوباره تلاش کنید.") from None
        if ref is None:
            raise HTTPException(404, "این نماد در صرافی‌های انتخاب‌شده پیدا نشد.")
        try:
            data = await asyncio.wait_for(market.header(ref), timeout=45)
        except asyncio.TimeoutError:
            raise HTTPException(504, "دریافت اطلاعات نماد زمان‌بر شد؛ دوباره تلاش کنید.") from None
        data["analysis_timeframes"] = [{"key": k, "label": TF_LABEL[k]} for k in SYMBOL_TFS]
        data["chart_timeframes"] = [{"key": k, "label": TF_LABEL[k]} for k in ("5m", "15m", "30m", "1h", "4h", "6h", "12h", "1d", "1w")]
        data["base_asset"] = ref.base
        data["quote_asset"] = ref.quote
        data["logo_url"] = logos.endpoint_url(ref.base)
        logos.remember_symbol(ref.display, ref.base, ref.quote, ref.exchange_name())
        data["favorite"] = ref.display in (repo.favorites(int(auth["uid"])).get("symbol") or [])
        data["locked"] = auth.get("role") != "admin" and not db.has_active_webapp_sub(int(auth["uid"]))
        return data

    @app.get("/api/symbol/{symbol}/price")
    async def api_symbol_price(symbol: str, request: Request):
        auth = _require_user(request)
        cfg = repo.scan_config(int(auth["uid"]))
        try:
            ref = await asyncio.wait_for(market.resolve(symbol, cfg["exchanges"], cfg["exchanges_all"]), timeout=30)
            if ref is None:
                raise HTTPException(404, "این نماد در صرافی‌های انتخاب‌شده پیدا نشد.")
            tick = await asyncio.wait_for(market.ticker(ref), timeout=30)
        except asyncio.TimeoutError:
            raise HTTPException(504, "دریافت قیمت لحظه‌ای زمان‌بر شد؛ دوباره تلاش کنید.") from None
        price = float(tick.get("last") or 0)
        if price <= 0:
            raise HTTPException(503, "قیمت لحظه‌ای این نماد در دسترس نیست.")
        return {
            "symbol": ref.display,
            "price": price,
            "change_pct": float(tick.get("pct") or 0),
            "quote_volume": float(tick.get("quote_volume") or 0),
            "at": int(time.time()),
        }

    @app.get("/api/symbol/{symbol}/candles")
    async def api_symbol_candles(symbol: str, request: Request, tf: str = "1d", limit: int = 800):
        auth = _require_user(request)
        _require_premium(auth)
        tf = tf if tf in TF_MINUTES else "1d"
        limit = max(60, min(int(limit), 1000))
        cfg = repo.scan_config(int(auth["uid"]))
        try:
            ref = await asyncio.wait_for(market.resolve(symbol, cfg["exchanges"], cfg["exchanges_all"]), timeout=45)
            if ref is None:
                raise HTTPException(404, "این نماد در صرافی‌های انتخاب‌شده پیدا نشد.")
            candles = await asyncio.wait_for(market.candles(ref, tf, limit), timeout=45)
        except asyncio.TimeoutError:
            raise HTTPException(504, "دریافت کندل‌ها زمان‌بر شد؛ دوباره تلاش کنید.") from None
        if candles is None:
            raise HTTPException(503, "داده‌ی کندل برای این نماد در دسترس نیست.")
        rows = [
            [int(candles.t[i]), float(candles.o[i]), float(candles.h[i]),
             float(candles.l[i]), float(candles.c[i]), float(candles.v[i])]
            for i in range(len(candles.t))
        ]
        return {
            "symbol": ref.display,
            "exchange": ref.exchange_name(),
            "timeframe": tf,
            "timeframe_label": TF_LABEL.get(tf, tf),
            "candles": rows,
        }

    @app.get("/api/symbols/signalled")
    async def api_symbols_signalled(request: Request, timeframe: str = "", limit: int = 60):
        """نمادهایی که لااقل یک سیگنال ساختاری فعال دارند (برای پیمایش بالای چارت)."""
        auth = _require_user(request)
        if auth.get("role") != "admin" and not db.has_active_webapp_sub(int(auth["uid"])):
            return {"locked": True, "items": [], "total": 0}
        limit = max(5, min(int(limit), 200))
        where = ["is_active = 1"]
        params: List[Any] = []
        if timeframe in TF_MINUTES:
            where.append("timeframe = ?")
            params.append(timeframe)
        cfg = repo.scan_config(int(auth["uid"]))
        names: List[str] = []
        if not cfg.get("exchanges_all") and cfg.get("exchanges"):
            for slug in cfg["exchanges"]:
                row = repo.exchange(str(slug))
                if row and row.get("name"):
                    names.append(str(row["name"]))
        if names:
            where.append("exchange IN (" + ",".join(["?"] * len(names)) + ")")
            params += names
        rows = db.q(
            "SELECT symbol, exchange, timeframe, COUNT(*) AS n, MAX(candle_ts) AS latest "
            "FROM structural_signals WHERE " + " AND ".join(where) +
            " GROUP BY symbol, exchange, timeframe ORDER BY latest DESC LIMIT ?",
            params + [limit],
        )
        items = [
            {
                "symbol": r["symbol"],
                "exchange": r.get("exchange") or "",
                "timeframe": r.get("timeframe") or "",
                "signals": int(r["n"] or 0),
                "latest": float(r["latest"] or 0),
            }
            for r in rows
        ]
        return {"locked": False, "items": items, "total": len(items)}

    @app.get("/api/symbol/{symbol}/signals")
    async def api_symbol_signals(symbol: str, request: Request, timeframe: str = "1h", page: int = 1, page_size: int = 50):
        auth = _require_user(request)
        uid = int(auth["uid"])
        if auth.get("role") != "admin" and not db.has_active_webapp_sub(uid):
            return {
                "locked": True,
                "results": [],
                "count": 0,
                "total": 0,
                "page": 1,
                "page_size": 50,
                "timeframe": timeframe,
                "subscription": _subscription_info(uid),
                "bot_url": _bot_url(),
            }
        if timeframe not in TF_MINUTES:
            timeframe = "1h"
        cfg = repo.scan_config(uid)
        try:
            ref = await asyncio.wait_for(market.resolve(symbol, cfg["exchanges"], cfg["exchanges_all"]), timeout=45)
        except asyncio.TimeoutError:
            raise HTTPException(504, "دریافت اطلاعات نماد زمان‌بر شد؛ دوباره تلاش کنید.") from None
        if ref is None:
            raise HTTPException(404, "این نماد در صرافی‌های انتخاب‌شده پیدا نشد.")
        depth = max(10, min(int(cfg["depth"] or 40), 120))
        scope = _strategy_scope(auth)
        started = time.perf_counter()
        try:
            items = await asyncio.wait_for(
                scanner.scan_symbol(
                    ref.display, timeframe=timeframe, depth=depth,
                    strategy_keys=sorted(scope) if scope is not None else None,
                    user_id=uid, exchanges=cfg["exchanges"], exchanges_all=cfg["exchanges_all"],
                ),
                timeout=120,
            )
        except asyncio.TimeoutError:
            raise HTTPException(504, "تحلیل این نماد زمان‌بر شد؛ دوباره تلاش کنید.") from None
        total = len(items)
        page = max(1, int(page))
        page_size = max(10, min(int(page_size), 250))
        start = (page - 1) * page_size
        sliced = items[start:start + page_size]
        return {
            "locked": False,
            "symbol": ref.display,
            "exchange": ref.exchange_name(),
            "exchange_slug": ref.primary,
            "timeframe": timeframe,
            "timeframe_label": TF_LABEL.get(timeframe, timeframe),
            "page": page,
            "page_size": page_size,
            "total": total,
            "count": len(sliced),
            "results": _decorate_signals(uid, sliced),
            "took": round(time.perf_counter() - started, 2),
        }

    @app.post("/api/scanner/scan")
    async def api_scan(body: ScanBody, request: Request):
        auth = _require_user(request)
        req = _scan_request_from(auth, body)
        return await _run_scan(auth, req, record=True)

    @app.get("/api/history")
    async def api_history(request: Request, page: int = 1, page_size: int = 10):
        auth = _require_user(request)
        page = max(1, int(page))
        page_size = max(1, min(int(page_size), 50))
        items, total = repo.search_rows(int(auth["uid"]), (page - 1) * page_size, page_size)
        return {"items": items, "total": total, "page": page, "page_size": page_size}

    @app.post("/api/history/{sid}/rerun")
    async def api_history_rerun(sid: int, request: Request):
        auth = _require_user(request)
        item = repo.get_search(int(auth["uid"]), int(sid))
        if item is None:
            raise HTTPException(404, "این جستجو پیدا نشد.")
        filters = item.get("filters") or {}
        keys = [str(k).strip() for k in (filters.get("strategy_keys") or []) if str(k).strip()]
        scope = _strategy_scope(auth)
        if scope is not None:
            keys = [k for k in keys if k in scope] if keys else sorted(scope)
        req = ScanRequest(
            timeframe=filters.get("timeframe") or "1h",
            depth=int(filters.get("depth") or 0),
            mode=filters.get("mode") or "any",
            symbols_mode=filters.get("symbols_mode") or "all",
            symbol=filters.get("symbol") or "",
            strategy_keys=keys,
            exchanges=list(filters.get("exchanges") or []),
            exchanges_all=bool(filters.get("exchanges_all", True)),
            user_id=int(auth["uid"]),
        )
        return await _run_scan(auth, req, record=False)

    @app.delete("/api/history/{sid}")
    async def api_history_delete(sid: int, request: Request):
        auth = _require_user(request)
        if not repo.delete_search(int(auth["uid"]), int(sid)):
            raise HTTPException(404, "این جستجو پیدا نشد.")
        return {"ok": True}

    @app.delete("/api/history")
    async def api_history_clear(request: Request):
        auth = _require_user(request)
        return {"ok": True, "deleted": repo.clear_searches(int(auth["uid"]))}

    @app.get("/api/settings")
    async def api_settings(request: Request):
        auth = _require_user(request)
        uid = int(auth["uid"])
        raw = repo.user_settings(uid)
        settings = {
            "notify_on": raw.get("notify_on") == "1",
            "notify_template": raw.get("notify_template") or config.DEFAULT_SETTINGS["web_notify_template"],
            "scan_timeframe": raw.get("scan_timeframe") or "1h",
            "scan_depth": int(raw.get("scan_depth") or 10),
            "scan_mode": raw.get("scan_mode") or "any",
            "scan_symbols_mode": raw.get("scan_symbols_mode") or "all",
            "scan_symbol": raw.get("scan_symbol") or "",
            "scan_strategy_keys": _safe_json(raw.get("scan_strategy_keys") or "[]", []),
            "scan_exchanges": _safe_json(raw.get("scan_exchanges") or "[]", []),
            "exchanges_all": raw.get("exchanges_all", "1") == "1",
        }
        return {
            "settings": settings,
            "exchanges": _visible_exchanges(),
            "favorites": repo.favorites(uid),
            "subscription": _subscription_info(uid, auth.get("role") or "user"),
            "user": _user_context(auth),
        }

    @app.put("/api/settings")
    async def api_save_settings(body: SettingsBody, request: Request):
        auth = _require_user(request)
        uid = int(auth["uid"])
        raw = repo.user_settings(uid)
        values: Dict[str, Any] = {}
        if body.notify_on is not None:
            values["notify_on"] = bool(body.notify_on)
        if body.notify_template is not None:
            template = body.notify_template.strip()[:2000]
            values["notify_template"] = template or config.DEFAULT_SETTINGS["web_notify_template"]
        if body.scan_timeframe is not None:
            values["scan_timeframe"] = body.scan_timeframe if body.scan_timeframe in TF_MINUTES else "1h"
        if body.scan_depth is not None:
            values["scan_depth"] = max(0, min(int(body.scan_depth), 120))
        if body.scan_mode is not None:
            values["scan_mode"] = "all" if body.scan_mode == "all" else "any"
        if body.scan_symbols_mode is not None:
            values["scan_symbols_mode"] = body.scan_symbols_mode if body.scan_symbols_mode in ("all", "favorites", "single") else "all"
        if body.scan_symbol is not None:
            values["scan_symbol"] = norm_symbol(body.scan_symbol)
        mode = values.get("scan_symbols_mode", raw.get("scan_symbols_mode") or "all")
        if mode == "single":
            target = values.get("scan_symbol", norm_symbol(raw.get("scan_symbol") or ""))
            if not target:
                raise HTTPException(400, "برای حالت «تک نماد»، نام نماد را وارد کنید.")
        if body.scan_strategy_keys is not None:
            known = {str(r["strategy_key"]) for r in repo.strategy_rows(active_only=False)}
            values["scan_strategy_keys"] = [k for k in body.scan_strategy_keys if k in known]
        if body.scan_exchanges is not None:
            values["scan_exchanges"] = _clean_exchanges(body.scan_exchanges, False)
        if body.exchanges_all is not None:
            values["exchanges_all"] = bool(body.exchanges_all)
        repo.save_user_settings(uid, values)
        return await api_settings(request)

    @app.get("/api/favorites")
    async def api_favorites(request: Request):
        auth = _require_user(request)
        return repo.favorites(int(auth["uid"]))

    @app.post("/api/favorites")
    async def api_toggle_favorite(body: FavoriteBody, request: Request):
        auth = _require_user(request)
        kind = body.kind if body.kind in ("strategy", "symbol", "signal") else "symbol"
        value = (body.value or "").strip()
        if kind == "symbol":
            value = norm_symbol(value)
        elif kind == "strategy" and not repo.strategy(value):
            raise HTTPException(404, "این استراتژی پیدا نشد.")
        if not value:
            raise HTTPException(400, "مقدار مورد نظر خالی است.")
        active = repo.toggle_favorite(int(auth["uid"]), kind, value)
        return {"ok": True, "active": active, "kind": kind, "value": value}

    # ------------------------------------------------------------------
    # پنل مدیریت
    # ------------------------------------------------------------------
    @app.get("/api/admin/overview")
    async def admin_overview(request: Request):
        _require_admin(request)
        stats = repo.stats()
        stats["pending_requests"] = db.count_pending_requests()
        recent_users, total_users = repo.user_rows("", "all", "all", 0, 6)
        payments_list, payments_total = repo.payment_rows("all", "all", 0, 6)
        return {
            "stats": stats,
            "users_total": total_users,
            "recent_users": recent_users,
            "recent_payments": [_payment_admin(p) for p in payments_list],
            "payments_total": payments_total,
            "messages": repo.message_rows(5),
            "exchanges": repo.exchange_rows(),
            "support_open": int(db.q1(
                "SELECT COUNT(*) AS n FROM support_tickets t WHERE t.status = 'open' "
                "AND EXISTS (SELECT 1 FROM support_messages m WHERE m.ticket_id = t.id)"
            )["n"] or 0),
            "plans": {
                "bot": {"mode": plans.plan_mode(plans.PLAN_BOT), "configured": plans.configured(plans.PLAN_BOT)},
                "webapp": {"mode": plans.plan_mode(plans.PLAN_WEBAPP), "configured": plans.configured(plans.PLAN_WEBAPP)},
            },
        }

    @app.get("/api/admin/users")
    async def admin_users(
        request: Request, q: str = "", sub: str = "all", access: str = "all",
        page: int = 1, page_size: int = 25,
    ):
        _require_admin(request)
        page = max(1, int(page))
        page_size = max(5, min(int(page_size), 100))
        sub_filter = sub if sub in ("all", "vip", "expired", "none", "web_vip", "web_expired") else "all"
        access_filter = access if access in ("all", "on", "off") else "all"
        rows, total = repo.user_rows(q.strip(), sub_filter, access_filter, (page - 1) * page_size, page_size)
        return {"items": rows, "total": total, "page": page, "page_size": page_size}

    @app.get("/api/admin/users/{uid}")
    async def admin_user(uid: int, request: Request):
        _require_admin(request)
        detail = repo.user_detail(int(uid))
        if detail is None:
            raise HTTPException(404, "کاربر پیدا نشد.")
        summary = _payment_summary(int(uid))
        detail["bot_sub"].update({
            "start": summary["bot"]["start"],
            "amount": summary["bot"]["amount"],
            "payments": summary["bot"]["count"],
        })
        detail["webapp_sub"].update({
            "start": summary["webapp"]["start"],
            "amount": summary["webapp"]["amount"],
            "payments": summary["webapp"]["count"],
        })
        row = _user_row(int(uid))
        detail["phone"] = row.get("phone_number") or detail.get("phone") or ""
        return detail

    @app.post("/api/admin/users/{uid}/access")
    async def admin_user_access(uid: int, body: AccessBody, request: Request):
        _require_admin(request)
        if not repo.user_detail(int(uid)):
            raise HTTPException(404, "کاربر پیدا نشد.")
        repo.set_access(int(uid), bool(body.enabled))
        return {"ok": True, "message": "دسترسی کاربر فعال شد." if body.enabled else "دسترسی کاربر غیرفعال شد."}

    @app.post("/api/admin/users/{uid}/password")
    async def admin_user_password(uid: int, body: PasswordBody, request: Request):
        _require_admin(request)
        if not repo.user_detail(int(uid)):
            raise HTTPException(404, "کاربر پیدا نشد.")
        days = max(0, min(int(body.days or 0), 3650))
        repo.ensure_webapp_user(int(uid))
        repo.set_access(int(uid), True)
        if days:
            db.grant_webapp_sub(int(uid), days)
        password = activation.issue_password(int(uid))
        sent = await activation.send_activation(runtime.bot, int(uid), password)
        until = db.webapp_sub_until(int(uid))
        return {
            "ok": True,
            "password": password,
            "sent": bool(sent),
            "message": (
                "رمز جدید صادر شد و پیام راهنما برای کاربر ارسال شد."
                if sent else
                "رمز جدید صادر شد اما ارسال پیام به کاربر ممکن نبود؛ رمز در همین صفحه نمایش داده می‌شود."
            ),
            "until_jalali": jalali_ts(until) if until else "-",
        }

    @app.post("/api/admin/users/{uid}/subscription")
    async def admin_user_subscription(uid: int, body: SubscriptionBody, request: Request):
        _require_admin(request)
        if not repo.user_detail(int(uid)):
            raise HTTPException(404, "کاربر پیدا نشد.")
        action = (body.action or "").strip().lower()
        days = max(1, min(int(body.days or 0), 3650))
        if action == "add":
            until = db.grant_webapp_sub(int(uid), days)
            if not repo.has_password(int(uid)):
                repo.ensure_webapp_user(int(uid))
                repo.set_access(int(uid), True)
                password = activation.issue_password(int(uid))
                sent = await activation.send_activation(runtime.bot, int(uid), password)
                return {
                    "ok": True, "until_jalali": jalali_ts(until), "password": password, "sent": bool(sent),
                    "message": "اشتراک فعال شد و رمز ورود برای کاربر ارسال شد." if sent
                    else "اشتراک فعال شد اما ارسال پیام ممکن نبود؛ رمز را به کاربر اطلاع دهید.",
                }
            await runtime.send_text(
                int(uid),
                f"{days} روز به اشتراک تحلیل ساختاری شما اضافه شد.\nپایان اشتراک: {jalali_ts(until)}",
            )
            return {"ok": True, "until_jalali": jalali_ts(until), "message": f"{days} روز به اشتراک کاربر اضافه شد."}
        if action == "reduce":
            until = db.reduce_webapp_sub(int(uid), days)
            if until is None:
                raise HTTPException(400, "این کاربر اشتراک فعالی ندارد.")
            await runtime.send_text(
                int(uid),
                f"{days} روز از اشتراک تحلیل ساختاری شما کاهش یافت.\nپایان اشتراک: {jalali_ts(until) if until else 'پایان یافته'}",
            )
            return {"ok": True, "until_jalali": jalali_ts(until) if until else "-", "message": f"{days} روز از اشتراک کاربر کاهش یافت."}
        if action == "remove":
            db.remove_webapp_sub(int(uid))
            await runtime.send_text(int(uid), "اشتراک تحلیل ساختاری شما توسط مدیریت حذف شد.")
            return {"ok": True, "until_jalali": "-", "message": "اشتراک کاربر حذف شد."}
        raise HTTPException(400, "عملیات نامعتبر است.")

    @app.post("/api/admin/users/{uid}/message")
    async def admin_user_message(uid: int, body: SingleMessageBody, request: Request):
        auth = _require_admin(request)
        text = (body.text or "").strip()
        if not text:
            raise HTTPException(400, "متن پیام خالی است.")
        if len(text) > MAX_MESSAGE_CHARS:
            raise HTTPException(400, f"متن پیام باید کمتر از {MAX_MESSAGE_CHARS} کاراکتر باشد.")
        ok, err = await runtime.send_text(int(uid), text)
        repo.log_message(int(auth["uid"]), "single", json.dumps({"user_id": int(uid)}), text, 1 if ok else 0, 0 if ok else 1)
        if not ok:
            return {"ok": False, "message": "ارسال پیام انجام نشد: " + (err or "کاربر در دسترس نیست.")}
        return {"ok": True, "message": "پیام برای کاربر ارسال شد."}

    @app.delete("/api/admin/users/{uid}")
    async def admin_delete_user(uid: int, request: Request):
        _require_admin(request)
        if int(uid) == config.ADMIN_UID:
            raise HTTPException(400, "حساب مدیر قابل حذف نیست.")
        if not repo.user_detail(int(uid)):
            raise HTTPException(404, "کاربر پیدا نشد.")
        repo.delete_user(int(uid))
        return {"ok": True, "message": "کاربر به طور کامل حذف شد."}

    @app.get("/api/admin/payments")
    async def admin_payments(
        request: Request, status: str = "all", service: str = "all", page: int = 1, page_size: int = 20,
    ):
        _require_admin(request)
        page = max(1, int(page))
        page_size = max(5, min(int(page_size), 100))
        status = status if status in ("all", *payments.STATUSES) else "all"
        service = service if service in ("all", payments.SERVICE_BOT, payments.SERVICE_WEBAPP) else "all"
        rows, total = repo.payment_rows(service, status, (page - 1) * page_size, page_size)
        return {
            "items": [_payment_admin(p) for p in rows],
            "total": total, "page": page, "page_size": page_size,
            "statuses": [{"key": k, "label": v} for k, v in payments.STATUS_FA.items()],
            "services": [
                {"key": "all", "label": "همه"},
                {"key": payments.SERVICE_BOT, "label": payments.SERVICE_FA[payments.SERVICE_BOT]},
                {"key": payments.SERVICE_WEBAPP, "label": payments.SERVICE_FA[payments.SERVICE_WEBAPP]},
            ],
        }

    @app.get("/api/admin/payments/{pid}")
    async def admin_payment_detail(pid: int, request: Request):
        _require_admin(request)
        p = payments.get(int(pid))
        if p is None:
            raise HTTPException(404, "پرداخت پیدا نشد.")
        plan = plans.PLAN_WEBAPP if p.get("service_type") == payments.SERVICE_WEBAPP else plans.PLAN_BOT
        data = _payment_admin(p)
        data["plan"] = plan
        data["networks"] = [
            {
                "key": r["network_key"],
                "title": plans.network_title(r),
                "asset": r.get("asset") or "",
                "wallet": plans.network_wallet(r),
                "amount": plans.network_amount(r, plan),
                "enabled": bool(r.get("enabled")),
            }
            for r in plans.network_rows(plan)
        ]
        return {"item": data}

    @app.get("/api/admin/requests/{rid}")
    async def admin_request(rid: int, request: Request):
        _require_admin(request)
        req = db.get_sub_request(int(rid))
        if req is None:
            raise HTTPException(404, "درخواست پیدا نشد.")
        return {
            "id": req.id,
            "user_id": req.user_id,
            "text": req.text or "",
            "status": req.status,
            "reason": req.reason or "",
            "service_type": req.service_type,
            "created_at": jalali_from_db(req.created_at),
            "has_media": bool(req.photo_id),
            "media_kind": req.photo_id.partition(":")[0] if req.photo_id else "",
        }

    RECEIPT_IMAGE_TYPES = {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
        ".webp": "image/webp", ".gif": "image/gif", ".bmp": "image/bmp",
    }

    def _receipt_response(data: bytes, media_type: str, rid: int):
        # رسید فقط تصویر است؛ هر نوع دیگری با attachment و sandbox فرستاده می‌شود تا
        # فایل HTML/SVG آپلودشده در دامنه‌ی پنل مدیر اجرا نشود.
        if media_type in RECEIPT_IMAGE_TYPES.values():
            headers = {
                "Content-Disposition": f'inline; filename="receipt-{rid}"',
                "Content-Security-Policy": "default-src 'none'; img-src 'self' data:; sandbox",
                "X-Content-Type-Options": "nosniff",
                "Cache-Control": "private, no-store",
            }
        else:
            media_type = "application/octet-stream"
            headers = {
                "Content-Disposition": f'attachment; filename="receipt-{rid}.bin"',
                "Content-Security-Policy": "default-src 'none'; sandbox",
                "X-Content-Type-Options": "nosniff",
                "Cache-Control": "private, no-store",
            }
        return Response(bytes(data), media_type=media_type, headers=headers)

    @app.get("/api/admin/requests/{rid}/media")
    async def admin_request_media(rid: int, request: Request):
        _require_admin(request)
        req = db.get_sub_request(int(rid))
        if req is None or ":" not in (req.photo_id or ""):
            raise HTTPException(404, "فایل رسید پیدا نشد.")
        kind, _, file_id = req.photo_id.partition(":")
        if kind == "local":
            rel = file_id.split("?", 1)[0]
            if not rel.startswith("/uploads/receipts/"):
                raise HTTPException(404, "فایل رسید پیدا نشد.")
            target = (UPLOAD_ROOT / rel[len("/uploads/"):]).resolve()
            receipts_root = (UPLOAD_ROOT / "receipts").resolve()
            if receipts_root not in target.parents or not target.is_file():
                raise HTTPException(404, "فایل رسید پیدا نشد.")
            ext = target.suffix.lower()
            media_type = RECEIPT_IMAGE_TYPES.get(ext, "application/octet-stream")
            return _receipt_response(target.read_bytes(), media_type, rid)
        if runtime.bot is None:
            raise HTTPException(503, "ربات در دسترس نیست.")
        try:
            tg_file = await runtime.bot.get_file(file_id)
            data = await tg_file.download_as_bytearray()
        except Exception as exc:
            raise HTTPException(502, f"دریافت فایل از تلگرام ناموفق بود: {exc}") from None
        if kind == "p":
            media_type = "image/jpeg"
        else:
            media_type = RECEIPT_IMAGE_TYPES.get(Path(tg_file.file_path or "").suffix.lower(), "application/octet-stream")
        return _receipt_response(bytes(data), media_type, rid)

    @app.post("/api/admin/payments/{pid}/action")
    async def admin_payment_action(pid: int, body: PaymentActionBody, request: Request):
        _require_admin(request)
        action = (body.action or "").strip().lower()
        note = (body.note or "").strip()[:500]
        bot = runtime.bot
        if action == "approve":
            p = payments.get(int(pid))
            require_tx = bool(p and p.get("method") not in ("bsc", "auto"))
            ok, message, p = await payment_flow.approve(
                bot, int(pid),
                tx_hash=(body.tx_hash or "").strip(),
                amount=body.amount,
                network_key=(body.network or "").strip(),
                sender=(body.sender or "").strip(),
                note=note,
                require_tx=require_tx,
            )
        elif action == "reject":
            ok, message = await payment_flow.reject(bot, int(pid), note)
        elif action == "suspend":
            ok, message = await payment_flow.suspend(bot, int(pid), note)
        else:
            raise HTTPException(400, "عملیات نامعتبر است.")
        if not ok:
            raise HTTPException(400, message)
        return {"ok": True, "message": message, "payment": _payment_admin(payments.get(int(pid)) or {})}

    @app.get("/api/admin/strategies")
    async def admin_strategies(request: Request, q: str = "", category: str = "all", page: int = 1, page_size: int = 30):
        _require_admin(request)
        page = max(1, int(page))
        page_size = max(10, min(int(page_size), 100))
        where, params = [], []
        if q.strip():
            like = "%" + q.strip().lower() + "%"
            where.append("(LOWER(s.title) LIKE ? OR LOWER(s.strategy_key) LIKE ?)")
            params += [like, like]
        if category and category != "all":
            where.append("s.category = ?")
            params.append(category)
        cond = (" WHERE " + " AND ".join(where)) if where else ""
        total = int(db.q1("SELECT COUNT(*) AS n FROM structural_strategies s" + cond, params)["n"] or 0)
        rows = db.q(
            "SELECT s.*, g.title AS guide_title, g.description AS guide_description, "
            "g.image_path AS guide_image_path, g.image_width AS guide_image_width, g.image_height AS guide_image_height "
            "FROM structural_strategies s LEFT JOIN strategy_guides g ON g.strategy_key = s.strategy_key"
            + cond + " ORDER BY s.sort_order, s.id LIMIT ? OFFSET ?",
            params + [page_size, (page - 1) * page_size],
        )
        items = []
        for row in rows:
            item = _strategy_public(row)
            item["guide_title"] = row.get("guide_title") or ""
            item["guide_description"] = row.get("guide_description") or ""
            item["guide_image_path"] = _guide_url(row.get("guide_image_path") or "")
            item["guide_image_width"] = int(row.get("guide_image_width") or 0)
            item["guide_image_height"] = int(row.get("guide_image_height") or 0)
            items.append(item)
        categories = [r["category"] for r in db.q(
            "SELECT DISTINCT category FROM structural_strategies ORDER BY category"
        )]
        return {
            "items": items, "total": total, "page": page, "page_size": page_size,
            "categories": categories,
            "active_count": int(db.q1("SELECT COUNT(*) AS n FROM structural_strategies WHERE is_active = 1")["n"] or 0),
        }

    @app.post("/api/admin/strategies/{key}/toggle")
    async def admin_strategy_toggle(key: str, body: StrategyToggleBody, request: Request):
        _require_admin(request)
        if not repo.set_strategy_active(key, bool(body.active)):
            raise HTTPException(404, "استراتژی پیدا نشد.")
        try:
            scanner.clear_cache()
        except Exception:
            pass
        return {"ok": True, "active": bool(body.active)}

    @app.post("/api/admin/strategies/{key}/guide")
    async def admin_strategy_guide(
        key: str,
        request: Request,
        title: str = Form(""),
        description: str = Form(""),
        width: int = Form(420),
        height: int = Form(300),
        image: Optional[UploadFile] = File(None),
    ):
        _require_admin(request)
        row = repo.strategy(key)
        if row is None:
            raise HTTPException(404, "استراتژی پیدا نشد.")
        width = max(120, min(int(width or 420), 1200))
        height = max(90, min(int(height or 300), 1200))
        title = (title or "").strip()[:200]
        description = (description or "").strip()[:2000]
        image_path = None
        if image is not None and image.filename:
            ext, _, data = await read_image(image, max_bytes=MAX_GUIDE_BYTES)
            name = f"{uuid.uuid4().hex}{ext}"
            (GUIDE_DIR / name).write_bytes(data)
            image_path = f"/uploads/guides/{name}"
        repo.set_guide(key, title, description, image_path, width, height)
        if image_path is not None and row.get("guide_image") and row.get("guide_image") != image_path:
            old = GUIDE_DIR / Path(str(row.get("guide_image"))).name
            try:
                if old.exists() and old.parent == GUIDE_DIR:
                    old.unlink()
            except OSError:
                pass
        return {"ok": True, "message": "راهنمای استراتژی ذخیره شد.", "guide_image": image_path or _guide_url(row.get("guide_image") or "")}

    @app.delete("/api/admin/strategies/{key}/guide")
    async def admin_strategy_guide_delete(key: str, request: Request):
        _require_admin(request)
        row = repo.strategy(key)
        if row is None:
            raise HTTPException(404, "استراتژی پیدا نشد.")
        db.x("DELETE FROM strategy_guides WHERE strategy_key = ?", (key,))
        db.x("UPDATE structural_strategies SET guide_image = '', guide_width = 0, guide_height = 0 WHERE strategy_key = ?", (key,))
        if row.get("guide_image"):
            path = GUIDE_DIR / Path(str(row.get("guide_image"))).name
            try:
                if path.exists() and path.parent == GUIDE_DIR:
                    path.unlink()
            except OSError:
                pass
        return {"ok": True, "message": "راهنمای استراتژی حذف شد."}

    @app.get("/api/admin/exchanges")
    async def admin_exchanges(request: Request):
        _require_admin(request)
        rows = repo.exchange_rows()
        return {"items": [dict(r) for r in rows]}

    @app.post("/api/admin/exchanges")
    async def admin_exchange_create(body: ExchangeBody, request: Request):
        _require_admin(request)
        data = _exchange_data(body)
        eid = repo.save_exchange(data)
        if eid is None:
            raise HTTPException(409, "نامک (slug) تکراری است یا اطلاعات کامل نیست.")
        try:
            market.clear_cache()
        except Exception:
            pass
        return {"ok": True, "id": eid}

    @app.put("/api/admin/exchanges/{eid}")
    async def admin_exchange_update(eid: int, body: ExchangeBody, request: Request):
        _require_admin(request)
        current = db.q1("SELECT * FROM exchanges WHERE id = ?", (int(eid),))
        if current is None:
            raise HTTPException(404, "صرافی پیدا نشد.")
        data = _exchange_data(body, current)
        slug_owner = repo.exchange(data["slug"])
        if slug_owner and int(slug_owner["id"]) != int(eid):
            raise HTTPException(409, "این نامک قبلاً استفاده شده است.")
        rid = repo.save_exchange(data, int(eid))
        if rid is None:
            raise HTTPException(400, "ذخیره اطلاعات صرافی انجام نشد.")
        try:
            market.clear_cache()
        except Exception:
            pass
        return {"ok": True}

    @app.post("/api/admin/exchanges/{eid}/flag")
    async def admin_exchange_flag(eid: int, body: ExchangeFlagBody, request: Request):
        _require_admin(request)
        if body.field not in ("is_active", "is_visible"):
            raise HTTPException(400, "فیلد نامعتبر است.")
        if not repo.set_exchange_flag(int(eid), body.field, bool(body.value)):
            raise HTTPException(404, "صرافی پیدا نشد.")
        try:
            market.clear_cache()
        except Exception:
            pass
        return {"ok": True}

    @app.delete("/api/admin/exchanges/{eid}")
    async def admin_exchange_delete(eid: int, request: Request):
        _require_admin(request)
        if not repo.delete_exchange(int(eid)):
            raise HTTPException(404, "صرافی پیدا نشد.")
        try:
            market.clear_cache()
        except Exception:
            pass
        return {"ok": True, "message": "صرافی حذف شد."}

    @app.post("/api/admin/exchanges/sync")
    async def admin_exchanges_sync(request: Request):
        _require_admin(request)
        try:
            result = await asyncio.wait_for(market.sync(), timeout=120)
        except asyncio.TimeoutError:
            raise HTTPException(504, "همگام‌سازی صرافی‌ها زمان‌بر شد.") from None
        try:
            scanner.clear_cache()
        except Exception:
            pass
        return {"ok": not result.get("errors"), **result}

    @app.get("/api/admin/settings")
    async def admin_settings(request: Request):
        _require_admin(request)
        settings = {
            "web_notify_template": db.get("web_notify_template") or config.DEFAULT_SETTINGS["web_notify_template"],
            "web_default_timeframe": db.get("web_default_timeframe") or "1h",
        }
        return {
            "settings": settings,
            "defaults": {
                "web_sub_template": config.DEFAULT_SETTINGS["web_sub_template"],
                "web_notify_template": config.DEFAULT_SETTINGS["web_notify_template"],
            },
            "site": config.WEBAPP_PUBLIC_HOST,
        }

    @app.put("/api/admin/settings")
    async def admin_settings_save(body: AdminSettingsBody, request: Request):
        _require_admin(request)
        updates: Dict[str, str] = {}
        if body.web_notify_template is not None:
            updates["web_notify_template"] = body.web_notify_template.strip()[:2000] or config.DEFAULT_SETTINGS["web_notify_template"]
        if body.web_default_timeframe is not None:
            updates["web_default_timeframe"] = body.web_default_timeframe if body.web_default_timeframe in TF_MINUTES else "1h"
        if updates:
            db.set_many(updates)
        return await admin_settings(request)

    @app.get("/api/admin/ticker")
    async def admin_ticker(request: Request):
        _require_admin(request)
        cfg = ticker.get_config()
        return {
            "config": cfg,
            "sources": ticker.source_options(),
            "preview": await ticker.snapshot(),
            "max_items": ticker.MAX_ITEMS,
        }

    @app.put("/api/admin/ticker")
    async def admin_ticker_save(body: TickerBody, request: Request):
        _require_admin(request)
        payload = body.model_dump(exclude_none=True)
        if "items" in payload and payload["items"] is not None:
            payload["items"] = [i if isinstance(i, dict) else dict(i) for i in payload["items"]]
        merged = {**ticker.get_config(), **payload}
        cfg = ticker.save_config(merged)
        return {
            "config": cfg,
            "sources": ticker.source_options(),
            "preview": await ticker.snapshot(force=True),
            "max_items": ticker.MAX_ITEMS,
            "message": "نوار قیمت‌ها ذخیره شد و در وب‌اپ کاربر اعمال شد.",
        }

    @app.get("/api/admin/messages")
    async def admin_messages_history(request: Request):
        _require_admin(request)
        rows = repo.message_rows(40)
        for row in rows:
            row["created_at_fa"] = jalali_from_db(row.get("created_at"))
        return {"items": rows}

    @app.post("/api/admin/messages")
    async def admin_messages_send(body: MessageBody, request: Request):
        auth = _require_admin(request)
        text = (body.text or "").strip()
        if not text:
            raise HTTPException(400, "متن پیام خالی است.")
        if len(text) > MAX_MESSAGE_CHARS:
            raise HTTPException(400, f"متن پیام باید کمتر از {MAX_MESSAGE_CHARS} کاراکتر باشد.")
        mode = (body.mode or "single").strip().lower()
        admin_id = int(auth["uid"])
        if mode == "single":
            if not body.user_id:
                raise HTTPException(400, "کاربر مقصد را انتخاب کنید.")
            ok, err = await runtime.send_text(int(body.user_id), text)
            repo.log_message(admin_id, "single", json.dumps({"user_id": int(body.user_id)}), text, 1 if ok else 0, 0 if ok else 1)
            if not ok:
                raise HTTPException(502, "ارسال پیام انجام نشد: " + (err or "کاربر در دسترس نیست."))
            return {"ok": True, "mode": "single", "message": "پیام ارسال شد."}
        if mode == "bulk":
            ids = repo.users_for_message("all")
            target_type = "bulk"
            filter_json = "{}"
        elif mode == "targeted":
            sub_filter = body.sub_filter if body.sub_filter in ("all", "active", "expired", "none") else "all"
            joined = max(0, min(int(body.joined_days or 0), 365))
            ids = repo.users_for_message("all", sub_filter, joined)
            target_type = "targeted"
            filter_json = json.dumps({"sub_filter": sub_filter, "joined_days": joined}, ensure_ascii=False)
        else:
            raise HTTPException(400, "نوع ارسال نامعتبر است.")
        if not ids:
            raise HTTPException(400, "کاربری با این شرایط پیدا نشد.")
        job = runtime.start_job(text, ids, admin_id, target_type)
        return {
            "ok": True, "mode": mode, "job_id": job.id, "total": job.total,
            "filter": filter_json,
        }

    @app.get("/api/admin/messages/jobs/{job_id}")
    async def admin_message_job(job_id: str, request: Request):
        _require_admin(request)
        job = runtime.jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "کار ارسال پیدا نشد.")
        return job.snapshot()

    @app.post("/api/admin/messages/jobs/{job_id}/cancel")
    async def admin_message_job_cancel(job_id: str, request: Request):
        _require_admin(request)
        job = runtime.jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "کار ارسال پیدا نشد.")
        job.cancel()
        return {"ok": True}

    # ------------------------------------------------------------------
    # مدیریت محتوا، سئو، ظاهر، ثبت‌نام، امنیت و خرید اشتراک
    # ------------------------------------------------------------------
    content.register(
        app,
        require_admin=_require_admin,
        upload_root=UPLOAD_ROOT,
        web_dir=WEB_DIR,
    )
    accounts.register(
        app,
        require_admin=_require_admin,
        require_user=_require_user,
        client_ip=_client_key,
        set_session=_set_session,
    )
    security_ext.register(
        app,
        require_admin=_require_admin,
        client_ip=_client_key,
    )
    subscription_web.register(
        app,
        require_user=_require_user,
        client_ip=_client_key,
        upload_root=UPLOAD_ROOT,
    )

    # ------------------------------------------------------------------
    # مسیرهای تکمیلی مدیریت (برندینگ، پشتیبانی، پلن‌ها، بکاپ، امنیت)
    # ------------------------------------------------------------------
    admin_extra.register(
        app,
        require_admin=_require_admin,
        payment_admin=_payment_admin,
        require_user=_require_user,
        client_ip=_client_key,
        upload_root=UPLOAD_ROOT,
    )

    # ------------------------------------------------------------------
    # صفحه‌های HTML
    # ------------------------------------------------------------------
    def _spa_html(path: str, title: str = "", robots: str = "") -> HTMLResponse:
        try:
            document = (WEB_DIR / "index.html").read_text(encoding="utf-8")
            meta = content.site_meta(path)
            if title:
                meta["title"] = title + " | " + content.setting("site_name", config.WEBAPP_TITLE)
            if robots:
                meta["robots"] = robots
            return HTMLResponse(content.inject_spa_head(document, meta))
        except Exception:
            log.exception("spa meta injection failed")
            return HTMLResponse((WEB_DIR / "index.html").read_text(encoding="utf-8"))

    @app.get("/login", response_class=HTMLResponse)
    async def login_page(request: Request):
        auth = _session_from_request(request)
        if auth is not None:
            return RedirectResponse("/admin" if auth["role"] == "admin" else "/", status_code=303)
        return FileResponse(WEB_DIR / "login.html")

    @app.get("/register", response_class=HTMLResponse)
    async def register_page(request: Request):
        auth = _session_from_request(request)
        if auth is not None:
            return RedirectResponse("/admin" if auth["role"] == "admin" else "/", status_code=303)
        return FileResponse(WEB_DIR / "register.html")

    @app.get("/admin", response_class=HTMLResponse)
    async def admin_page(request: Request):
        token = request.query_params.get("token")
        if token:
            try:
                payload = decode_token(token, ADMIN_LINK_TYPE)
                if int(payload.get("sub") or 0) != config.ADMIN_UID:
                    raise TokenError("wrong admin")
            except TokenError:
                return RedirectResponse("/login?admin=1&state=invalid", status_code=303)
            response = RedirectResponse("/admin", status_code=303)
            _set_session(response, request, session_token(config.ADMIN_UID, "admin"), 1)
            return response
        auth = _session_from_request(request)
        if auth is None:
            return RedirectResponse("/login?admin=1", status_code=303)
        if auth["role"] != "admin":
            return RedirectResponse("/", status_code=303)
        response = FileResponse(WEB_DIR / "admin.html")
        response.headers["X-Robots-Tag"] = "noindex, nofollow"
        return response

    @app.get("/admin/{path:path}", response_class=HTMLResponse)
    async def admin_section(path: str, request: Request):
        auth = _session_from_request(request)
        if auth is None:
            return RedirectResponse("/login?admin=1", status_code=303)
        if auth["role"] != "admin":
            return RedirectResponse("/", status_code=303)
        response = FileResponse(WEB_DIR / "admin.html")
        response.headers["X-Robots-Tag"] = "noindex, nofollow"
        return response

    @app.get("/symbol/{symbol}", response_class=HTMLResponse)
    async def symbol_page(symbol: str):
        return _spa_html(f"/symbol/{symbol}", f"تحلیل {symbol}", "noindex,follow")

    @app.get("/subscribe", response_class=HTMLResponse)
    async def subscribe_page():
        return _spa_html("/subscribe", "خرید و تمدید اشتراک", "noindex,follow")

    @app.get("/settings/profile", response_class=HTMLResponse)
    async def profile_page():
        return _spa_html("/settings/profile", "پروفایل کاربری", "noindex,follow")

    @app.get("/", response_class=HTMLResponse)
    async def index_page():
        return _spa_html("/")

    @app.api_route("/{path:path}", methods=["GET"])
    async def spa_fallback(path: str):
        if path.startswith(("api/", "uploads/", "assets/", "telegram/")) or "." in path:
            raise HTTPException(404, "not found")
        return FileResponse(WEB_DIR / "index.html")

    return app


def _exchange_data(body: ExchangeBody, current: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    current = current or {}
    name = (body.name or current.get("name") or "").strip()[:64]
    slug = (body.slug or current.get("slug") or "").strip().lower()
    if not name or not SLUG_RE.match(slug):
        raise HTTPException(400, "نام و نامک صرافی را درست وارد کنید (نامک با حروف انگلیسی و خط تیره).")
    api_url = (body.api_url if body.api_url is not None else current.get("api_url") or "").strip()[:300]
    if api_url and not (api_url.startswith("https://") or api_url.startswith("http://")):
        raise HTTPException(400, "آدرس API باید با http یا https شروع شود.")
    return {
        "name": name,
        "slug": slug,
        "ccxt_id": (body.ccxt_id or "").strip().lower()[:40],
        "api_url": api_url,
        "priority": max(1, min(int(body.priority or 100), 9999)),
        "is_active": bool(body.is_active),
        "is_visible": bool(body.is_visible),
    }
