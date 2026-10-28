"""مسیرهای مدیریت وب‌اپ: برندینگ، پشتیبانی، پلن‌ها، کلیدهای API، پرداخت دستی، بکاپ، امنیت و وضعیت بازار."""
from __future__ import annotations

import json
import logging
import mimetypes
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Literal, Optional

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, ConfigDict, StrictBool

import backup
import config
import market_overview
import payment_flow
import payments
import plans
import support
from database import db
from webapp import backups, security
from webapp.jalali import jalali_from_db, jalali_ts
from webapp.repo import repo
from webapp.runtime import runtime
from webapp.uploads import read_image

log = logging.getLogger("webapp.admin_extra")

BRAND_KEYS = (
    "site_name", "site_slogan", "site_title", "site_logo",
    "login_title", "login_subtitle", "login_heading", "login_note",
    "login_button", "login_footer", "login_error",
)
BRAND_LIMITS = {
    "site_name": 60, "site_slogan": 120, "site_title": 120, "site_logo": 300,
    "login_title": 200, "login_subtitle": 400, "login_heading": 80, "login_note": 300,
    "login_button": 40, "login_footer": 300, "login_error": 200,
}
MAX_LOGO_BYTES = 3 * 1024 * 1024


class ChartSettingsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    theme: Literal["dark", "light"]
    showRealtimePrice: StrictBool
    showVolumePanel: StrictBool

    def settings(self) -> Dict[str, Any]:
        return {
            **self.model_dump(),
            "disableSymbolSearch": True,
            "enableDrawingTools": True,
        }


def _chart_settings() -> Dict[str, Any]:
    defaults = config.DEFAULT_CHART_SETTINGS.copy()
    try:
        stored = json.loads(db.get("chart_settings"))
        if not isinstance(stored, dict):
            return defaults
        # Legacy extras (including locked flags) never reach the response.
        body = ChartSettingsBody(**{
            key: stored.get(key, defaults[key])
            for key in ("theme", "showRealtimePrice", "showVolumePanel")
        })
    except (TypeError, ValueError):
        return defaults
    return body.settings()


class BrandingBody(BaseModel):
    site_name: Optional[str] = None
    site_slogan: Optional[str] = None
    site_title: Optional[str] = None
    site_logo: Optional[str] = None
    login_title: Optional[str] = None
    login_subtitle: Optional[str] = None
    login_heading: Optional[str] = None
    login_note: Optional[str] = None
    login_button: Optional[str] = None
    login_footer: Optional[str] = None
    login_error: Optional[str] = None


class MaintenanceBody(BaseModel):
    on: Optional[bool] = None
    text: Optional[str] = None


class PlanBody(BaseModel):
    mode: Optional[str] = None
    amount: Optional[float] = None
    days: Optional[int] = None
    renew_days: Optional[int] = None
    template: Optional[str] = None
    free_on: Optional[bool] = None
    free_days: Optional[int] = None
    free_features: Optional[List[str]] = None
    free_strategies: Optional[List[str]] = None
    free_categories: Optional[List[str]] = None


class NetworkBody(BaseModel):
    wallet: Optional[str] = None
    amount: Optional[float] = None
    enabled: Optional[bool] = None


class DiscountBody(BaseModel):
    enabled: bool = False
    kind: Optional[str] = None          # percent | fixed (پیش‌فرض: درصد)
    pct: float = 0
    fixed: float = 0
    days: int = 1
    start_now: Optional[bool] = None    # «اعمال از همین لحظه»
    starts_at: Optional[float] = None   # شروع زمان‌بندی‌شده (epoch) در صورت نبود start_now
    ends_at: Optional[float] = None     # پایان صریح (epoch) در صورت نبود days
    restart: Optional[bool] = None      # نام پیشین start_now؛ برای سازگاری پذیرفته می‌شود


class ApiKeysBody(BaseModel):
    nodereal_api_key: Optional[str] = None
    tronscan_api_key: Optional[str] = None
    tonapi_api_key: Optional[str] = None
    arbiscan_api_key: Optional[str] = None


class ManualPaymentBody(BaseModel):
    user_id: int
    plan: str = "webapp"
    amount: float
    tx_hash: str
    network: str
    note: str = ""
    sender: str = ""


class SupportReplyBody(BaseModel):
    text: str = ""
    reopen: bool = False


class SupportStatusBody(BaseModel):
    status: str


class SecurityBody(BaseModel):
    rl_max: Optional[int] = None
    rl_window: Optional[int] = None
    rl_block: Optional[int] = None
    rl_global: Optional[int] = None
    as_max: Optional[int] = None
    as_window: Optional[int] = None
    as_block: Optional[int] = None
    wsec_on: Optional[bool] = None
    wsec_max: Optional[int] = None
    wsec_window: Optional[int] = None
    wsec_block: Optional[int] = None
    wsec_notify: Optional[bool] = None


class BackupBody(BaseModel):
    sections: List[str] = []


class ClearIpBody(BaseModel):
    ip: str = ""


class CardBody(BaseModel):
    plan: str = plans.PLAN_WEBAPP
    holder_name: str = ""
    card_number: str = ""
    shaba: str = ""            # اختیاری؛ در صورت وارد شدن اعتبارسنجی می‌شود
    bank_code: str = ""
    bank_name: str = ""
    note: str = ""
    is_active: Optional[bool] = None
    sort_order: Optional[int] = None


class WalletBody(BaseModel):
    plan: str = plans.PLAN_WEBAPP
    network: str = ""
    chain: str = ""
    asset: str = ""
    network_label: str = ""
    address: str = ""
    amount: float = 0
    note: str = ""
    is_active: Optional[bool] = None
    sort_order: Optional[int] = None


class DestinationActiveBody(BaseModel):
    active: bool = True


def _text(key: str) -> str:
    return db.get(key) or str(config.DEFAULT_SETTINGS.get(key, ""))


def public_branding() -> Dict[str, Any]:
    return {
        "site_name": _text("site_name"),
        "site_slogan": _text("site_slogan"),
        "site_title": _text("site_title"),
        "logo": _text("site_logo"),
        "login": {
            "title": _text("login_title"),
            "subtitle": _text("login_subtitle"),
            "heading": _text("login_heading"),
            "note": _text("login_note"),
            "button": _text("login_button"),
            "footer": _text("login_footer"),
            "error": _text("login_error"),
        },
        "maintenance": {
            "on": db.get_bool("maint_on"),
            "text": _text("maint_text"),
            "at": db.get_float("maint_at"),
        },
    }


def _admin_branding() -> Dict[str, Any]:
    data = public_branding()
    data.update({
        "defaults": {k: config.DEFAULT_SETTINGS.get(k, "") for k in BRAND_KEYS},
        "site": config.WEBAPP_PUBLIC_HOST,
    })
    return data


def _mask_key(value: str) -> str:
    v = (value or "").strip()
    if not v:
        return ""
    if len(v) <= 8:
        return "••••••••"
    return f"{v[:4]}••••{v[-4:]}"


def _support_user(uid: int) -> Dict[str, Any]:
    row = db.q1("SELECT * FROM users WHERE user_id = ?", (int(uid),)) or {}
    name = " ".join(x for x in ((row.get("first_name") or ""), (row.get("last_name") or "")) if x).strip()
    return {
        "uid": int(uid),
        "name": name or (row.get("username") or "") or (row.get("phone_number") or "") or "-",
        "username": row.get("username") or "",
        "phone": row.get("phone_number") or "",
        "verified": bool(row.get("is_verified")),
        "joined_at": jalali_from_db(row.get("joined_at")) if row.get("joined_at") else "-",
        "bot_until": db.sub_until(int(uid)),
        "bot_until_fa": jalali_ts(db.sub_until(int(uid))) if db.sub_until(int(uid)) else "-",
        "web_until": db.webapp_sub_until(int(uid)),
        "web_until_fa": jalali_ts(db.webapp_sub_until(int(uid))) if db.webapp_sub_until(int(uid)) else "-",
    }


def register(
    app: FastAPI,
    *,
    require_admin: Callable[[Request], Dict[str, Any]],
    payment_admin: Callable[[Dict[str, Any]], Dict[str, Any]],
    require_user: Callable[[Request], Dict[str, Any]],
    client_ip: Callable[[Request], str],
    upload_root: Path,
) -> None:
    _require_admin = require_admin
    _require_user = require_user
    branding_dir = Path(upload_root) / "branding"
    branding_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Global chart display settings (no subscription or strategy changes).
    # ------------------------------------------------------------------
    @app.get("/api/chart-settings")
    async def chart_settings(request: Request):
        _require_user(request)
        return _chart_settings()

    @app.get("/api/admin/chart-settings")
    async def admin_chart_settings(request: Request):
        _require_admin(request)
        return _chart_settings()

    @app.put("/api/admin/chart-settings")
    async def admin_chart_settings_save(body: ChartSettingsBody, request: Request):
        _require_admin(request)
        settings = body.settings()
        db.set("chart_settings", json.dumps(settings))
        return settings

    # ------------------------------------------------------------------
    # عمومی: برندینگ، حالت تعمیر، وضعیت بازار
    # ------------------------------------------------------------------
    @app.get("/api/public/config")
    async def public_config():
        return {
            **public_branding(),
            "bot_url": _bot_url(),
            "site": config.WEBAPP_PUBLIC_HOST,
        }

    @app.get("/api/market/status")
    async def market_status(request: Request):
        _require_user(request)
        state = db.get("mkt_state")
        available = state in market_overview.STATE_FA
        return {
            "available": available,
            "fresh": market_overview.is_fresh(),
            "state": state if available else "",
            "state_fa": market_overview.STATE_FA.get(state, "نامشخص"),
            "rsi": db.get_float("mkt_rsi"),
            "adx": db.get_float("mkt_adx"),
            "bull": db.get_float("mkt_bull"),
            "bear": db.get_float("mkt_bear"),
            "count": db.get_int("mkt_count"),
            "timeframe": db.get("mkt_tf") or "",
            "at": db.get_float("mkt_at"),
            "at_fa": jalali_ts(db.get_float("mkt_at")) if db.get_float("mkt_at") else "",
        }

    # ------------------------------------------------------------------
    # برندینگ
    # ------------------------------------------------------------------
    @app.get("/api/admin/branding")
    async def admin_branding(request: Request):
        _require_admin(request)
        return _admin_branding()

    @app.put("/api/admin/branding")
    async def admin_branding_save(body: BrandingBody, request: Request):
        _require_admin(request)
        updates: Dict[str, str] = {}
        for key in BRAND_KEYS:
            value = getattr(body, key)
            if value is None:
                continue
            value = str(value).strip()
            if key == "site_title" and not value:
                value = str(config.DEFAULT_SETTINGS.get("site_title", ""))
            if key == "site_logo":
                if value and not value.startswith("/uploads/branding/"):
                    raise HTTPException(400, "مسیر لوگو معتبر نیست؛ از دکمه‌ی بارگذاری استفاده کنید.")
            updates[key] = value[: BRAND_LIMITS.get(key, 200)]
        if updates:
            db.set_many(updates)
        return {"ok": True, "message": "تنظیمات ظاهر سایت ذخیره شد.", "branding": _admin_branding()}

    @app.post("/api/admin/branding/logo")
    async def admin_branding_logo(request: Request, image: UploadFile = File(...)):
        _require_admin(request)
        if not image.filename:
            raise HTTPException(400, "فایلی انتخاب نشده است.")
        ext, _, data = await read_image(image, max_bytes=MAX_LOGO_BYTES)
        name = f"logo-{uuid.uuid4().hex}{ext}"
        try:
            (branding_dir / name).write_bytes(data)
        except OSError as exc:
            raise HTTPException(500, f"ذخیره‌ی لوگو انجام نشد: {exc}") from None
        old = _text("site_logo")
        db.set("site_logo", f"/uploads/branding/{name}")
        if old.startswith("/uploads/branding/"):
            old_path = branding_dir / Path(old).name
            try:
                if old_path.exists() and old_path != branding_dir / name:
                    old_path.unlink()
            except OSError:
                pass
        return {"ok": True, "message": "لوگو بارگذاری شد.", "logo": f"/uploads/branding/{name}"}

    @app.delete("/api/admin/branding/logo")
    async def admin_branding_logo_delete(request: Request):
        _require_admin(request)
        old = _text("site_logo")
        db.set("site_logo", "")
        if old.startswith("/uploads/branding/"):
            path = branding_dir / Path(old).name
            try:
                if path.exists():
                    path.unlink()
            except OSError:
                pass
        return {"ok": True, "message": "لوگو حذف شد."}

    # ------------------------------------------------------------------
    # حالت تعمیر و نگهداری
    # ------------------------------------------------------------------
    @app.get("/api/admin/maintenance")
    async def admin_maintenance(request: Request):
        _require_admin(request)
        return {
            "on": db.get_bool("maint_on"),
            "text": _text("maint_text"),
            "at": db.get_float("maint_at"),
            "at_fa": jalali_ts(db.get_float("maint_at")) if db.get_float("maint_at") else "",
        }

    @app.put("/api/admin/maintenance")
    async def admin_maintenance_save(body: MaintenanceBody, request: Request):
        _require_admin(request)
        updates: Dict[str, str] = {}
        if body.text is not None:
            text = body.text.strip()[:500]
            updates["maint_text"] = text or str(config.DEFAULT_SETTINGS["maint_text"])
        if body.on is not None:
            updates["maint_on"] = "1" if body.on else "0"
            if body.on and not db.get_bool("maint_on"):
                updates["maint_at"] = str(time.time())
        if updates:
            db.set_many(updates)
        return {"ok": True, "message": "وضعیت حالت تعمیر ذخیره شد.", **await admin_maintenance(request)}

    # ------------------------------------------------------------------
    # پشتیبانی
    # ------------------------------------------------------------------
    @app.get("/api/admin/support")
    async def admin_support_list(
        request: Request, status: str = "open", q: str = "", page: int = 1, page_size: int = 20,
    ):
        _require_admin(request)
        status = status if status in ("open", "closed", "all") else "open"
        page = max(1, int(page))
        page_size = max(5, min(int(page_size), 100))
        items = support.list_tickets(
            None if status == "all" else status, (page - 1) * page_size, page_size, q.strip()
        )
        total = support.count_tickets(None if status == "all" else status, q.strip())
        out = []
        for t in items:
            row = dict(t)
            row["user"] = _support_user(int(t["user_id"]))
            row["last_line"] = support.last_line(t)
            row["created_at_fa"] = jalali_from_db(t.get("created_at"))
            row["last_at_fa"] = jalali_from_db(t.get("last_at")) if t.get("last_at") else "-"
            out.append(row)
        return {"items": out, "total": total, "page": page, "page_size": page_size}

    @app.get("/api/admin/support/{tid}")
    async def admin_support_detail(tid: int, request: Request):
        _require_admin(request)
        t = support.get_ticket(int(tid))
        if t is None:
            raise HTTPException(404, "مکالمه‌ی پشتیبانی پیدا نشد.")
        msgs = []
        for m in support.messages(int(tid)):
            msg = dict(m)
            msg["created_at_fa"] = jalali_from_db(m.get("created_at"))
            msg["has_media"] = bool(m.get("file_id")) and (m.get("message_type") in ("photo", "document"))
            msg["media_url"] = f"/api/admin/support/{tid}/messages/{m['id']}/media" if msg["has_media"] else ""
            msgs.append(msg)
        return {
            "ticket": {
                "id": int(t["id"]),
                "user_id": int(t["user_id"]),
                "status": t.get("status"),
                "ai_state": t.get("ai_state") or "",
                "created_at": t.get("created_at"),
                "created_at_fa": jalali_from_db(t.get("created_at")),
                "closed_at_fa": jalali_from_db(t.get("closed_at")) if t.get("closed_at") else "-",
            },
            "user": _support_user(int(t["user_id"])),
            "messages": msgs,
        }

    @app.post("/api/admin/support/{tid}/reply")
    async def admin_support_reply(tid: int, body: SupportReplyBody, request: Request):
        _require_admin(request)
        t = support.get_ticket(int(tid))
        if t is None:
            raise HTTPException(404, "مکالمه‌ی پشتیبانی پیدا نشد.")
        text = (body.text or "").strip()
        if not text:
            raise HTTPException(400, "متن پاسخ خالی است.")
        if len(text) > support.MAX_TEXT:
            raise HTTPException(400, f"متن پاسخ باید کمتر از {support.MAX_TEXT} کاراکتر باشد.")
        ok, err = await runtime.send_text(int(t["user_id"]), text)
        if not ok:
            raise HTTPException(502, "ارسال پاسخ انجام نشد: " + (err or "کاربر در دسترس نیست."))
        mid = support.add_admin_message(int(tid), text)
        if body.reopen and (t.get("status") or "open") != "open":
            db.x("UPDATE support_tickets SET status = 'open', closed_at = NULL WHERE id = ?", (int(tid),))
            db.x("UPDATE support_tickets SET ai_state = ? WHERE id = ?", (support.AI_ESCALATED, int(tid)))
        return {"ok": True, "message": "پاسخ برای کاربر ارسال و در مکالمه ثبت شد.", "message_id": mid}

    @app.post("/api/admin/support/{tid}/status")
    async def admin_support_status(tid: int, body: SupportStatusBody, request: Request):
        _require_admin(request)
        t = support.get_ticket(int(tid))
        if t is None:
            raise HTTPException(404, "مکالمه‌ی پشتیبانی پیدا نشد.")
        status = (body.status or "").strip().lower()
        if status not in ("open", "closed"):
            raise HTTPException(400, "وضعیت نامعتبر است.")
        if status == "closed":
            db.x("UPDATE support_tickets SET status = 'closed', closed_at = ? WHERE id = ?", (time.time(), int(tid)))
        else:
            db.x("UPDATE support_tickets SET status = 'open', closed_at = NULL WHERE id = ?", (int(tid),))
        return {"ok": True, "message": "وضعیت مکالمه به‌روزرسانی شد.", "status": status}

    @app.get("/api/admin/support/{tid}/messages/{mid}/media")
    async def admin_support_media(tid: int, mid: int, request: Request):
        _require_admin(request)
        m = db.q1(
            "SELECT * FROM support_messages WHERE id = ? AND ticket_id = ?", (int(mid), int(tid))
        )
        if m is None or not m.get("file_id") or runtime.bot is None:
            raise HTTPException(404, "فایل پیام پیدا نشد.")
        try:
            tg_file = await runtime.bot.get_file(m["file_id"])
            data = await tg_file.download_as_bytearray()
        except Exception as exc:
            raise HTTPException(502, f"دریافت فایل از تلگرام ناموفق بود: {exc}") from None
        mime = mimetypes.guess_type(tg_file.file_path or "")[0] or "application/octet-stream"
        if (m.get("message_type") or "") == "photo":
            mime = "image/jpeg"
        return Response(bytes(data), media_type=mime, headers={"Content-Disposition": f'inline; filename="support-{mid}"'})

    # ------------------------------------------------------------------
    # پلن‌ها، شبکه‌ها، تخفیف و اشتراک رایگان
    # ------------------------------------------------------------------
    def _strategy_options() -> List[Dict[str, Any]]:
        return [
            {"key": r["strategy_key"], "title": r["title"], "category": r["category"]}
            for r in db.q(
                "SELECT strategy_key, title, category FROM structural_strategies "
                "WHERE is_active = 1 ORDER BY sort_order, id"
            )
        ]

    def _category_names() -> set:
        return {
            (r["category"] or "").strip()
            for r in db.q("SELECT DISTINCT category FROM structural_strategies")
            if (r["category"] or "").strip()
        }

    @app.get("/api/admin/plans")
    async def admin_plans(request: Request):
        _require_admin(request)
        return {
            "plans": [plans.public_plan(p) for p in plans.PLANS],
            "free_features": [{"key": k, "label": v} for k, v in plans.FREE_BOT_FEATURES],
            "strategies": _strategy_options(),
            # فهرست دسته‌بندی‌ها با تعداد استراتژی‌های فعال هر دسته، برای انتخاب گروهی در فرم رایگان.
            "categories": plans.web_category_options(),
        }

    @app.put("/api/admin/plans/{plan}")
    async def admin_plan_save(plan: str, body: PlanBody, request: Request):
        _require_admin(request)
        plan = plans.normalize_plan(plan)
        updates: Dict[str, str] = {}
        if body.mode is not None:
            mode = body.mode if body.mode in plans.MODES else plans.MODE_OFF
            db.set(plans._keys(plan)["mode"], mode)
        if body.amount is not None:
            updates[plans._keys(plan)["amount"]] = str(max(0.0, round(float(body.amount), 2)))
        if body.days is not None:
            updates[plans._keys(plan)["days"]] = str(max(1, min(int(body.days), 3650)))
        if body.renew_days is not None:
            updates[plans._keys(plan)["renew"]] = str(max(0, min(int(body.renew_days), 365)))
        if body.template is not None:
            updates[plans._keys(plan)["template"]] = (
                body.template.strip()[:3000] or str(config.DEFAULT_SETTINGS.get(plans._keys(plan)["template"], ""))
            )
        if body.free_on is not None:
            updates["free_bot_on" if plan == plans.PLAN_BOT else "free_web_on"] = "1" if body.free_on else "0"
        if body.free_days is not None:
            updates["free_bot_days" if plan == plans.PLAN_BOT else "free_web_days"] = str(
                max(1, min(int(body.free_days), 3650))
            )
        if body.free_features is not None and plan == plans.PLAN_BOT:
            allowed = {k for k, _ in plans.FREE_BOT_FEATURES}
            keys = [k for k in body.free_features if k in allowed]
            updates["free_bot_features"] = ",".join(dict.fromkeys(keys)) or plans.NONE_SENTINEL
        if body.free_strategies is not None and plan == plans.PLAN_WEBAPP:
            valid = {r["strategy_key"] for r in db.q("SELECT strategy_key FROM structural_strategies")}
            keys = [k for k in body.free_strategies if k in valid]
            updates["free_web_strategies"] = ",".join(dict.fromkeys(keys)) or plans.NONE_SENTINEL
        if body.free_categories is not None and plan == plans.PLAN_WEBAPP:
            valid_cats = _category_names()
            cats = [c for c in body.free_categories if (c or "").strip() in valid_cats]
            updates["free_web_categories"] = ",".join(dict.fromkeys((c or "").strip() for c in cats)) or plans.NONE_SENTINEL
        if updates:
            db.set_many(updates)
        return {"ok": True, "message": "تنظیمات پلن ذخیره شد.", "plan": plans.public_plan(plan)}

    @app.put("/api/admin/plans/{plan}/networks/{key}")
    async def admin_plan_network(plan: str, key: str, body: NetworkBody, request: Request):
        _require_admin(request)
        plan = plans.normalize_plan(plan)
        row = plans.network_row(plan, key)
        if row is None:
            raise HTTPException(404, "شبکه‌ی پرداخت پیدا نشد.")
        fields: Dict[str, Any] = {}
        if body.wallet is not None:
            fields["wallet"] = body.wallet.strip()[:200]
        if body.amount is not None:
            fields["amount"] = max(0.0, round(float(body.amount), 2))
        if body.enabled is not None:
            fields["enabled"] = bool(body.enabled)
        new_wallet = fields.get("wallet", row.get("wallet") or "")
        new_amount = float(fields.get("amount", row.get("amount") or 0))
        new_enabled = fields.get("enabled", bool(row.get("enabled")))
        if new_enabled and (not str(new_wallet).strip() or new_amount <= 0):
            raise HTTPException(400, "برای فعال‌سازی شبکه، آدرس کیف پول و مبلغ را وارد کنید.")
        if not plans.update_network(plan, key, **fields):
            raise HTTPException(400, "ذخیره‌ی شبکه انجام نشد.")
        return {"ok": True, "message": "تنظیمات شبکه ذخیره شد.", "network": plans.network_row(plan, key)}

    @app.put("/api/admin/plans/{plan}/discount")
    async def admin_plan_discount(plan: str, body: DiscountBody, request: Request):
        _require_admin(request)
        plan = plans.normalize_plan(plan)
        kind = (body.kind or "").strip().lower()
        if kind not in plans.DISC_KINDS:
            kind = plans.discount_kind(plan)
        value = float(body.fixed or 0) if kind == plans.DISC_FIXED else float(body.pct or 0)
        has_window = int(body.days or 0) > 0 or float(body.ends_at or 0) > 0 or float(body.starts_at or 0) > 0
        if body.enabled and value <= 0:
            raise HTTPException(400, "مقدار تخفیف (درصد یا مبلغ ثابت) باید بزرگ‌تر از صفر باشد.")
        if body.enabled and not has_window:
            raise HTTPException(400, "برای تخفیف یک مدت (روز) یا تاریخ پایان مشخص کنید.")
        # «شروع از حالا» (کلید جدید start_now)؛ نام پیشین restart همچنان پذیرفته می‌شود.
        if body.start_now is not None:
            start_now = bool(body.start_now)
        elif body.restart is not None:
            start_now = bool(body.restart)
        else:
            start_now = False
        plans.set_discount(
            plan, bool(body.enabled),
            pct=float(body.pct or 0), days=int(body.days or 0),
            kind=kind, fixed=float(body.fixed or 0),
            start_now=start_now, starts_at=float(body.starts_at or 0), ends_at=float(body.ends_at or 0),
        )
        return {"ok": True, "message": "تنظیمات تخفیف ذخیره شد.", "plan": plans.public_plan(plan)}

    @app.get("/api/admin/free-claims")
    async def admin_free_claims(request: Request, plan: str = "all", page: int = 1, page_size: int = 25):
        _require_admin(request)
        page = max(1, int(page))
        page_size = max(5, min(int(page_size), 100))
        where, params = [], []
        if plan in (plans.PLAN_BOT, plans.PLAN_WEBAPP):
            where.append("f.plan = ?")
            params.append(plan)
        cond = (" WHERE " + " AND ".join(where)) if where else ""
        total = int(db.q1("SELECT COUNT(*) AS n FROM free_sub_claims f" + cond, params)["n"] or 0)
        rows = db.q(
            "SELECT f.*, u.first_name, u.last_name, u.username FROM free_sub_claims f "
            "LEFT JOIN users u ON u.user_id = f.user_id" + cond + " ORDER BY f.id DESC LIMIT ? OFFSET ?",
            params + [page_size, (page - 1) * page_size],
        )
        items = []
        for r in rows:
            name = " ".join(x for x in ((r.get("first_name") or ""), (r.get("last_name") or "")) if x).strip()
            snap_strat = (r.get("strategies") or "").strip()
            snap_cats = (r.get("categories") or "").strip()
            cat_list = [] if snap_cats in ("", plans.NONE_SENTINEL) else [x for x in snap_cats.split(",") if x]
            strat_list = [] if snap_strat in ("", plans.NONE_SENTINEL) else [x for x in snap_strat.split(",") if x]
            is_web = plans.normalize_plan(r.get("plan")) == plans.PLAN_WEBAPP
            items.append({
                "id": int(r["id"]),
                "user_id": int(r["user_id"]),
                "name": name or (r.get("username") or "") or "-",
                "plan": r["plan"],
                "starts_at_fa": jalali_ts(float(r.get("starts_at") or 0)) if r.get("starts_at") else "-",
                "ends_at_fa": jalali_ts(float(r.get("ends_at") or 0)) if r.get("ends_at") else "-",
                "active": float(r.get("ends_at") or 0) > time.time() and not int(r.get("restored") or 0),
                "restored": bool(r.get("restored")),
                "features": [x for x in (r.get("features") or "").split(",") if x and x != plans.NONE_SENTINEL],
                "strategies": strat_list,
                "categories": cat_list,
                "all_strategies": bool(is_web and not snap_strat and not snap_cats),
                "strategy_count": len(plans._resolve_free_web_keys(snap_strat, snap_cats)) if is_web else 0,
            })
        return {"items": items, "total": total, "page": page, "page_size": page_size}

    # ------------------------------------------------------------------
    # مقصدهای پرداخت (کارت بانکی + کیف‌پول ارزی)
    # ------------------------------------------------------------------
    @app.get("/api/admin/payment-destinations")
    async def admin_destinations(request: Request, plan: str = "all"):
        _require_admin(request)
        plan = plan if plan in plans.PLANS else "all"
        return {
            "plans": [plans.public_plan(p) for p in plans.PLANS],
            "cards": [plans.card_view(r, admin=True) for r in plans.list_cards(plan or None)],
            "wallets": [plans.wallet_view(r, admin=True) for r in plans.list_wallets(plan or None)],
        }

    @app.post("/api/admin/cards")
    async def admin_card_create(body: CardBody, request: Request):
        _require_admin(request)
        plan = plans.normalize_plan(body.plan)
        ok, err, row = plans.save_card(
            None, plan,
            holder_name=body.holder_name, card_number=body.card_number, shaba=body.shaba,
            bank_code=body.bank_code, bank_name=body.bank_name, note=body.note,
            is_active=body.is_active, sort_order=body.sort_order,
        )
        if not ok:
            raise HTTPException(400, err)
        return {"ok": True, "message": "کارت مقصد ثبت شد.", "card": plans.card_view(row, admin=True)}

    @app.put("/api/admin/cards/{cid}")
    async def admin_card_save(cid: int, body: CardBody, request: Request):
        _require_admin(request)
        old = db.q1("SELECT plan FROM payment_cards WHERE id = ?", (int(cid),))
        if not old:
            raise HTTPException(404, "کارت مقصد پیدا نشد.")
        ok, err, row = plans.save_card(
            int(cid), old["plan"],
            holder_name=body.holder_name, card_number=body.card_number, shaba=body.shaba,
            bank_code=body.bank_code, bank_name=body.bank_name, note=body.note,
            is_active=body.is_active, sort_order=body.sort_order,
        )
        if not ok:
            raise HTTPException(400, err)
        return {"ok": True, "message": "کارت مقصد به‌روزرسانی شد.", "card": plans.card_view(row, admin=True)}

    @app.post("/api/admin/cards/{cid}/active")
    async def admin_card_active(cid: int, body: DestinationActiveBody, request: Request):
        _require_admin(request)
        ok, err = plans.set_card_active(int(cid), bool(body.active))
        if not ok:
            raise HTTPException(400, err)
        return {"ok": True, "message": "وضعیت کارت تغییر کرد."}

    @app.delete("/api/admin/cards/{cid}")
    async def admin_card_delete(cid: int, request: Request):
        _require_admin(request)
        if not plans.delete_card(int(cid)):
            raise HTTPException(404, "کارت مقصد پیدا نشد.")
        return {"ok": True, "message": "کارت مقصد حذف شد."}

    @app.post("/api/admin/wallets")
    async def admin_wallet_create(body: WalletBody, request: Request):
        _require_admin(request)
        plan = plans.normalize_plan(body.plan)
        ok, err, row = plans.save_wallet(
            None, plan,
            network=body.network, chain=body.chain, asset=body.asset,
            network_label=body.network_label, address=body.address, amount=body.amount,
            note=body.note, is_active=body.is_active, sort_order=body.sort_order,
        )
        if not ok:
            raise HTTPException(400, err)
        return {"ok": True, "message": "کیف‌پول مقصد ثبت شد.", "wallet": plans.wallet_view(row, admin=True)}

    @app.put("/api/admin/wallets/{wid}")
    async def admin_wallet_save(wid: int, body: WalletBody, request: Request):
        _require_admin(request)
        old = db.q1("SELECT plan FROM payment_wallets WHERE id = ?", (int(wid),))
        if not old:
            raise HTTPException(404, "کیف‌پول مقصد پیدا نشد.")
        ok, err, row = plans.save_wallet(
            int(wid), old["plan"],
            network=body.network, chain=body.chain, asset=body.asset,
            network_label=body.network_label, address=body.address, amount=body.amount,
            note=body.note, is_active=body.is_active, sort_order=body.sort_order,
        )
        if not ok:
            raise HTTPException(400, err)
        return {"ok": True, "message": "کیف‌پول مقصد به‌روزرسانی شد.", "wallet": plans.wallet_view(row, admin=True)}

    @app.post("/api/admin/wallets/{wid}/active")
    async def admin_wallet_active(wid: int, body: DestinationActiveBody, request: Request):
        _require_admin(request)
        ok, err = plans.set_wallet_active(int(wid), bool(body.active))
        if not ok:
            raise HTTPException(400, err)
        return {"ok": True, "message": "وضعیت کیف‌پول تغییر کرد."}

    @app.delete("/api/admin/wallets/{wid}")
    async def admin_wallet_delete(wid: int, request: Request):
        _require_admin(request)
        if not plans.delete_wallet(int(wid)):
            raise HTTPException(404, "کیف‌پول مقصد پیدا نشد.")
        return {"ok": True, "message": "کیف‌پول مقصد حذف شد."}

    # ------------------------------------------------------------------
    # کلیدهای API
    # ------------------------------------------------------------------
    KEY_SPECS = (
        ("nodereal_api_key", "NodeReal (BSC)"),
        ("tronscan_api_key", "Tronscan (TRON)"),
        ("tonapi_api_key", "TonAPI (TON)"),
        ("arbiscan_api_key", "Arbiscan (Arbitrum)"),
    )

    def _keys_public() -> Dict[str, Any]:
        return {
            "keys": [
                {
                    "key": k,
                    "label": label,
                    "present": bool((db.get(k) or "").strip()),
                    "masked": _mask_key(db.get(k)),
                }
                for k, label in KEY_SPECS
            ]
        }

    @app.get("/api/admin/api-keys")
    async def admin_api_keys(request: Request):
        _require_admin(request)
        return _keys_public()

    @app.put("/api/admin/api-keys")
    async def admin_api_keys_save(body: ApiKeysBody, request: Request):
        _require_admin(request)
        updates: Dict[str, str] = {}
        for key, _label in KEY_SPECS:
            value = getattr(body, key)
            if value is None or "•" in value:
                continue
            value = value.strip()[:500]
            updates[key] = value
            if key == "nodereal_api_key":
                updates["bscscan_api_key"] = value
        if updates:
            db.set_many(updates)
        return {"ok": True, "message": "کلیدهای API ذخیره شد.", **_keys_public()}

    # ------------------------------------------------------------------
    # پرداخت دستی
    # ------------------------------------------------------------------
    @app.post("/api/admin/payments/manual")
    async def admin_manual_payment(body: ManualPaymentBody, request: Request):
        _require_admin(request)
        plan = plans.normalize_plan(body.plan)
        ok, message, pid = payment_flow.create_manual(
            int(body.user_id), plan, float(body.amount), body.tx_hash, body.network,
            body.note, body.sender,
        )
        if not ok:
            raise HTTPException(400, message)
        ok2, msg2, p = await payment_flow.approve(runtime.bot, int(pid), note=body.note, require_tx=True)
        if not ok2:
            db.x("DELETE FROM payments_history WHERE id = ? AND status = 'pending'", (int(pid),))
            raise HTTPException(400, msg2)
        return {"ok": True, "message": msg2, "payment": payment_admin(p or {})}

    # ------------------------------------------------------------------
    # بکاپ و بازیابی
    # ------------------------------------------------------------------
    @app.get("/api/admin/backups")
    async def admin_backups(request: Request, page: int = 1, page_size: int = 20):
        _require_admin(request)
        page = max(1, int(page))
        page_size = max(5, min(int(page_size), 100))
        rows, total = backups.rows((page - 1) * page_size, page_size)
        return {
            "items": rows, "total": total, "page": page, "page_size": page_size,
            "sections": backup.sections_public(),
            "keep": db.get_int("backup_keep"),
            "auto": {"on": db.get_bool("backup_auto_on"), "days": db.get_int("backup_auto_days")},
        }

    @app.post("/api/admin/backups")
    async def admin_backup_create(body: BackupBody, request: Request):
        auth = _require_admin(request)
        valid = {s["key"] for s in backup.sections_public()}
        sections = [s for s in body.sections if s in valid]
        if body.sections and not sections:
            raise HTTPException(400, "بخش انتخاب‌شده معتبر نیست.")
        ok, message, row = await asyncio.to_thread(backups.create, int(auth["uid"]), sections or None)
        if not ok:
            raise HTTPException(400, message)
        return {"ok": True, "message": message, "item": row}

    @app.get("/api/admin/backups/{bid}/download")
    async def admin_backup_download(bid: int, request: Request):
        _require_admin(request)
        row = backups.get(int(bid))
        if row is None:
            raise HTTPException(404, "بکاپ پیدا نشد.")
        path = backups.path_of(row)
        if path is None:
            raise HTTPException(404, "فایل بکاپ روی سرور موجود نیست.")
        return FileResponse(path, media_type="application/json", filename=row["filename"])

    @app.post("/api/admin/backups/{bid}/restore")
    async def admin_backup_restore(bid: int, request: Request):
        _require_admin(request)
        ok, message = await asyncio.to_thread(backups.restore_id, int(bid))
        if not ok:
            raise HTTPException(400, message)
        _after_restore()
        return {"ok": True, "message": message}

    @app.post("/api/admin/backups/upload")
    async def admin_backup_upload(request: Request, file: UploadFile = File(...)):
        auth = _require_admin(request)
        data = await file.read(backup.MAX_FILE_BYTES + 1)
        if len(data) > backup.MAX_FILE_BYTES:
            raise HTTPException(400, "حجم فایل بکاپ بیش از حد مجاز است.")
        ok, message, row = await asyncio.to_thread(backups.restore_upload, data, int(auth["uid"]))
        if not ok:
            raise HTTPException(400, message)
        _after_restore()
        return {"ok": True, "message": message, "item": row}

    @app.delete("/api/admin/backups/{bid}")
    async def admin_backup_delete(bid: int, request: Request):
        _require_admin(request)
        if not backups.delete(int(bid)):
            raise HTTPException(404, "بکاپ پیدا نشد.")
        return {"ok": True, "message": "بکاپ حذف شد."}

    def _after_restore() -> None:
        try:
            plans.ensure_defaults()
        except Exception:
            log.exception("plans defaults after restore failed")
        try:
            repo.ensure_defaults()
        except Exception:
            log.exception("webapp defaults after restore failed")
        try:
            from webapp import content as site_content

            site_content.ensure_defaults()
        except Exception:
            log.exception("content defaults after restore failed")
        try:
            from webapp.scanner import scanner

            scanner.clear_cache()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # امنیت
    # ------------------------------------------------------------------
    @app.get("/api/admin/security")
    async def admin_security(request: Request):
        _require_admin(request)
        events = db.q("SELECT * FROM security_events ORDER BY id DESC LIMIT 50")
        for e in events:
            e["created_at_fa"] = jalali_from_db(e.get("created_at"))
        return {
            "bot": {
                "rl_max": db.get_int("rl_max"), "rl_window": db.get_int("rl_window"),
                "rl_block": db.get_int("rl_block"), "rl_global": db.get_int("rl_global"),
                "as_max": db.get_int("as_max"), "as_window": db.get_int("as_window"),
                "as_block": db.get_int("as_block"),
            },
            "web": security.settings(),
            "stats": security.stats(),
            "events": events,
        }

    @app.put("/api/admin/security")
    async def admin_security_save(body: SecurityBody, request: Request):
        _require_admin(request)
        updates: Dict[str, str] = {}
        for key in ("rl_max", "rl_window", "rl_block", "rl_global", "as_max", "as_window", "as_block"):
            value = getattr(body, key)
            if value is None:
                continue
            if key.endswith("max"):
                value = max(1, min(int(value), 100000))
            elif key.endswith("window"):
                value = max(1, min(int(value), 86400))
            else:
                value = max(0, min(int(value), 86400))
            updates[key] = str(value)
        for key in ("wsec_max", "wsec_window", "wsec_block"):
            value = getattr(body, key)
            if value is None:
                continue
            if key == "wsec_max":
                value = max(1, min(int(value), 100000))
            elif key == "wsec_window":
                value = max(1, min(int(value), 86400))
            else:
                value = max(1, min(int(value), 86400))
            updates[key] = str(value)
        if body.wsec_on is not None:
            updates["wsec_on"] = "1" if body.wsec_on else "0"
        if body.wsec_notify is not None:
            updates["wsec_notify"] = "1" if body.wsec_notify else "0"
        if updates:
            db.set_many(updates)
        return {"ok": True, "message": "تنظیمات امنیتی ذخیره شد.", **await admin_security(request)}

    @app.post("/api/admin/security/clear")
    async def admin_security_clear(body: ClearIpBody, request: Request):
        _require_admin(request)
        ip = (body.ip or "").strip()
        if not ip:
            raise HTTPException(400, "آدرس IP را وارد کنید.")
        security.clear(ip)
        return {"ok": True, "message": "محدودیت این IP برداشته شد."}

    # رویدادهای امنیتی در security_ext ثبت می‌شوند (فیلتر شدت/وضعیت/جستجو)


def _bot_url() -> str:
    username = (config.BOT_USERNAME or "").strip().lstrip("@")
    return f"https://t.me/{username}" if username else ""


def _payment_admin(row: Dict[str, Any]) -> Dict[str, Any]:
    if not row:
        return {}
    out = dict(row)
    out["created_at"] = out.get("created_at") or ""
    return out
