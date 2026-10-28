"""
بخش پشتیبانی آبان ترید.

کاربر: دکمه‌ی «پشتیبانی» -> مکالمه‌ی باز (یا ساخت مکالمه‌ی جدید) -> ارسال متن / عکس -> ذخیره‌ی پیام + ارسال برای ادمین
       -> «❌ پایان مکالمه پشتیبانی» مکالمه را می‌بندد.
ادمین: آرشیو چت‌ها، مکالمات باز / بسته، جستجو، مشاهده‌ی کل تاریخچه (صفحه‌ها در admin_extra.py).
فقط ادمین آرشیو را مشاهده می‌کند؛ همه‌ی پیام‌های کاربر در دیتابیس ذخیره می‌شوند.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from telegram.constants import ParseMode
from telegram.error import TelegramError

import asyncio

import broadcaster
import config
import support_ai
from database import db
from ui import NO, Ctx, back_row, btn, esc, kb, ts_text

log = logging.getLogger("support")

INTRO_TEXT = (
    "سلام،\n"
    "تلاش ما این است که در اولین فرصت به همه‌ی پیام‌ها پاسخ دهیم؛ لطفاً تا آن زمان شکیبا باشید.\n"
    "\n"
    "لطفاً هر پیام، پرسش، پیشنهاد یا انتقادی که دارید را یکجا و کامل ارسال کنید."
)
SENT_TEXT = (
    "✅پیام شما با موفقیت ارسال شد. در صورت داشتن پیام دیگر، آن را ارسال کنید.\n"
    "\n"
    " برای بستن مکالمه پشتیبانی دکمه (❌ پایان مکالمه پشتیبانی) را بزنید."
)
CLOSED_TEXT = "بخش مکالمه پشتیبانی با موفقیت بسته شد!"
END_LABEL = "❌ پایان مکالمه پشتیبانی"
FILE_NOT_ALLOWED = f"{NO} فقط متن یا عکس قابل ارسال است؛ فایل پذیرفته نمی‌شود."
MAX_TEXT = 3000
MAX_CAPTION = 1000

AI_ESCALATED = "escalated"          # ستون support_tickets.ai_state: مکالمه به ادمین ارجاع شده و دیگر AI پاسخ نمی‌دهد
AI_INTRO_LINE = "\n\nابتدا دستیار هوشمند تلاش می‌کند پاسخ شما را بدهد و اگر لازم باشد پیام شما برای ادمین ارسال می‌شود."
ESCALATE_BTN = "پاسخ کافی نبود؛ ارجاع به ادمین"
ESCALATED_TEXT = (
    "✅ درخواست شما برای بررسی به ادمین ارجاع شد. در صورت داشتن پیام دیگر، آن را ارسال کنید.\n"
    "\n"
    " برای بستن مکالمه پشتیبانی دکمه (❌ پایان مکالمه پشتیبانی) را بزنید."
)


# ----------------------------------------------------------------------------
# دیتابیس
# ----------------------------------------------------------------------------
def get_open(uid: int) -> Optional[Dict[str, Any]]:
    return db.q1(
        "SELECT * FROM support_tickets WHERE user_id = ? AND status = 'open' ORDER BY id DESC LIMIT 1", (uid,)
    )


def open_ticket(uid: int, username: str, first_name: str) -> Dict[str, Any]:
    """اگر مکالمه‌ی باز داشت همان، وگرنه مکالمه‌ی جدید."""
    t = get_open(uid)
    if t is not None:
        if (username or "") != (t.get("username") or "") or (first_name or "") != (t.get("first_name") or ""):
            db.x("UPDATE support_tickets SET username = ?, first_name = ? WHERE id = ?", (username, first_name, t["id"]))
            t["username"], t["first_name"] = username, first_name
        return t
    tid = db.insert_id(
        "INSERT INTO support_tickets(user_id, username, first_name, status) VALUES(?, ?, ?, 'open')",
        (uid, username, first_name),
    )
    return get_ticket(tid) or {"id": tid, "user_id": uid, "username": username, "first_name": first_name, "status": "open"}


def get_ticket(tid: int) -> Optional[Dict[str, Any]]:
    return db.q1("SELECT * FROM support_tickets WHERE id = ?", (tid,))


def add_message(tid: int, uid: int, mtype: str, text: str, file_id: str = "", sender: str = "user") -> int:
    return db.insert_id(
        "INSERT INTO support_messages(ticket_id, sender, sender_id, message_type, text, file_id) "
        "VALUES(?, ?, ?, ?, ?, ?)",
        (tid, sender, uid, mtype, text or None, file_id or None),
    )


def add_admin_message(tid: int, text: str) -> int:
    """پاسخ ادمین هم در همان مکالمه ذخیره می‌شود تا در متن کامل مکالمه نمایش داده شود."""
    return add_message(tid, config.ADMIN_UID, "text", text, "", sender="admin")


def close_ticket(tid: int) -> bool:
    return db.x(
        f"UPDATE support_tickets SET status = 'closed', closed_at = {db.NOW} WHERE id = ? AND status = 'open'", (tid,)
    ).rowcount > 0


def set_ai_state(tid: int, state: str) -> None:
    db.x("UPDATE support_tickets SET ai_state = ? WHERE id = ?", (state, tid))


def messages(tid: int) -> List[Dict[str, Any]]:
    return db.q("SELECT * FROM support_messages WHERE ticket_id = ? ORDER BY id", (tid,))


def message_count(tid: int) -> int:
    r = db.q1("SELECT COUNT(*) AS n FROM support_messages WHERE ticket_id = ?", (tid,))
    return int((r or {}).get("n") or 0)


def _like(s: str) -> str:
    return "%" + s.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _where(status: Optional[str], query: Optional[str]) -> Tuple[str, list]:
    # مکالمه‌ی خالی (کاربر دکمه را زده اما پیامی نفرستاده است) در آرشیو نمایش داده نمی‌شود
    conds = ["EXISTS (SELECT 1 FROM support_messages m0 WHERE m0.ticket_id = t.id)"]
    params: list = []
    if status in ("open", "closed"):
        conds.append("t.status = ?")
        params.append(status)
    q = (query or "").strip().lstrip("@")
    if q:
        conds.append(
            "(LOWER(COALESCE(t.first_name, '')) LIKE ? ESCAPE '\\' OR LOWER(COALESCE(t.username, '')) LIKE ? ESCAPE '\\' "
            "OR CAST(t.user_id AS TEXT) LIKE ? ESCAPE '\\')"
        )
        params += [_like(q)] * 3
    return "WHERE " + " AND ".join(conds), params


def list_tickets(status: Optional[str] = None, offset: int = 0, limit: int = 6, query: Optional[str] = None) -> List[Dict[str, Any]]:
    """مکالمات مرتب‌شده بر اساس آخرین پیام؛ هر ردیف: خلاصه‌ی آخرین پیام + زمان آخرین پیام."""
    where, params = _where(status, query)
    rows = db.q(
        "SELECT * FROM ("
        "SELECT t.id, t.user_id, t.username, t.first_name, t.status, t.created_at, t.closed_at, t.ai_state, "
        "(SELECT MAX(m.created_at) FROM support_messages m WHERE m.ticket_id = t.id) AS last_at, "
        "(SELECT m.message_type FROM support_messages m WHERE m.ticket_id = t.id ORDER BY m.id DESC LIMIT 1) AS last_type, "
        "(SELECT m.text FROM support_messages m WHERE m.ticket_id = t.id ORDER BY m.id DESC LIMIT 1) AS last_text, "
        "(SELECT COUNT(*) FROM support_messages m WHERE m.ticket_id = t.id) AS msg_count "
        f"FROM support_tickets t {where}) x "
        "ORDER BY COALESCE(x.last_at, x.created_at) DESC, x.id DESC LIMIT ? OFFSET ?",
        params + [limit, offset],
    )
    return rows


def count_tickets(status: Optional[str] = None, query: Optional[str] = None) -> int:
    where, params = _where(status, query)
    r = db.q1(f"SELECT COUNT(*) AS n FROM support_tickets t {where}", params)
    return int((r or {}).get("n") or 0)


# ----------------------------------------------------------------------------
# نمایش
# ----------------------------------------------------------------------------
def conversation_text(t: Dict[str, Any], budget: int = 3200) -> Tuple[str, bool]:
    """
    متن کامل مکالمه‌ی یک تیکت (پیام‌های کاربر و ادمین به ترتیب زمان، هرکدام با تاریخ و ساعت شمسی).
    اگر از سقف طول پیام تلگرام بیشتر شود، قدیمی‌ترین پیام‌ها حذف می‌شوند. خروجی: (متن HTML امن، آیا قدیمی‌ها حذف شده؟)
    """
    blocks: List[str] = []
    used = 0
    cut = False
    for m in reversed(messages(t["id"])):
        body = esc((m.get("text") or "")[:1500])
        if m.get("message_type") == "photo":
            body = "[تصویر]" + (f" {body}" if body else "")
        who = {"admin": "ادمین", "ai": "هوش مصنوعی"}.get(m.get("sender") or "", "کاربر")
        block = f"{ts_text(m.get('created_at'))} | {who}:\n{body}"
        if used + len(block) > budget and blocks:
            cut = True
            break
        blocks.append(block)
        used += len(block) + 2
    return "\n\n".join(reversed(blocks)), cut


def admin_post(t: Dict[str, Any], title: str = "پیام جدید پشتیبانی", note: str = "") -> str:
    """پست کامل برای ادمین: مشخصات کاربر + کل مکالمه‌ی تا این لحظه (کاربر و ادمین) با تاریخ و ساعت."""
    hist, cut = conversation_text(t)
    head = (
        (f"{note}\n\n" if note else "")
        + f"{title}\n"
        f"کاربر: {esc(user_label(t))}\n"
        f"آیدی: <code>{t['user_id']}</code> | مکالمه: #{t['id']}\n"
        f"وضعیت: {'باز' if t.get('status') == 'open' else 'بسته'}"
    )
    return head + "\n\n" + ("(پیام‌های قدیمی‌تر نمایش داده نشد)\n\n" if cut else "") + (hist or "پیامی ثبت نشده است.")


def user_label(t: Dict[str, Any]) -> str:
    name = (t.get("first_name") or "").strip() or "-"
    un = (t.get("username") or "").strip()
    return f"{name} (@{un})" if un else name


def preview(text: Optional[str], n: int = 45) -> str:
    s = " ".join((text or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def last_line(t: Dict[str, Any]) -> str:
    if (t.get("last_type") or "") == "photo":
        cap = preview(t.get("last_text"), 35)
        return "[تصویر]" + (f" {cap}" if cap else "")
    return preview(t.get("last_text")) or "-"


# ----------------------------------------------------------------------------
# جریان کاربر
# ----------------------------------------------------------------------------
def active(ctx: Ctx) -> bool:
    st = ctx.user_data.get("state")
    return bool(st) and st.get("kind") == "support"


def _user_names(ctx: Ctx) -> Tuple[str, str]:
    u = ctx.update.effective_user
    if u is None:
        return "", ""
    name = " ".join(x for x in ((u.first_name or "").strip(), (u.last_name or "").strip()) if x)
    return (getattr(u, "username", None) or ""), name


async def start(ctx: Ctx) -> None:
    """دکمه‌ی «پشتیبانی»: مکالمه‌ی باز ادامه پیدا می‌کند، وگرنه مکالمه‌ی جدید ساخته می‌شود."""
    username, name = _user_names(ctx)
    t = open_ticket(ctx.uid, username, name)
    ctx.user_data["state"] = {"kind": "support"}
    ctx.user_data.pop("support", None)
    rows = []
    if message_count(t["id"]) > 0:
        rows.append([btn(END_LABEL, "u:supx")])
    rows.append(back_row("u:menu"))
    intro = INTRO_TEXT + (AI_INTRO_LINE if support_ai.enabled() else "")
    await ctx.show(intro, kb(*rows), parse_mode=None)


async def close(ctx: Ctx) -> None:
    t = get_open(ctx.uid)
    if t is not None:
        close_ticket(t["id"])
    ctx.user_data.pop("state", None)
    ctx.user_data.pop("support", None)
    await ctx.show(CLOSED_TEXT, kb(back_row("u:menu")), parse_mode=None)


def admin_markup(tid: int):
    from webapp.activation import admin_panel_url
    return kb([btn("مشاهده و پاسخ در پنل مدیریت", url=admin_panel_url(), style="primary")])


async def _forward_to_admin(
    bot, t: Dict[str, Any], mtype: str, text: str, file_id: str, title: str = "پیام جدید پشتیبانی", note: str = "",
) -> None:
    """با هر پیام ارجاعی، کل مکالمه (کاربر، هوش مصنوعی و ادمین) با تاریخ و ساعت در یک پست برای ادمین ارسال می‌شود."""
    try:
        if mtype == "photo" and file_id:
            await bot.send_photo(chat_id=config.ADMIN_UID, photo=file_id)
        await broadcaster.send_html(
            bot, config.ADMIN_UID, admin_post(t, title, note), admin_markup(t["id"]), priority=broadcaster.PRIO_HIGH,
        )
    except TelegramError as e:
        log.warning("forward to admin failed: %s", e)


async def _confirm(ctx: Ctx, text: str = SENT_TEXT) -> None:
    st = ctx.user_data.setdefault("support", {})
    sent = await ctx.send(text, kb([btn(END_LABEL, "u:supx")]), parse_mode=None)
    st["mid"] = sent.message_id if sent is not None else None


async def _send_ai_reply(ctx: Ctx, reply: str) -> None:
    st = ctx.user_data.setdefault("support", {})
    markup = kb([btn(ESCALATE_BTN, "u:supa")], [btn(END_LABEL, "u:supx")])
    sent = await ctx.send(reply, markup, parse_mode=None)
    st["mid"] = sent.message_id if sent is not None else None


async def _typing(ctx: Ctx) -> None:
    try:
        await ctx.bot.send_chat_action(chat_id=ctx.uid, action="typing")
    except TelegramError:
        pass


_locks: Dict[int, asyncio.Lock] = {}


def _lock_for(uid: int) -> asyncio.Lock:
    if len(_locks) > 5000:
        for k in [k for k, lk in _locks.items() if not lk.locked()][:2500]:
            _locks.pop(k, None)
    lk = _locks.get(uid)
    if lk is None:
        lk = _locks[uid] = asyncio.Lock()
    return lk


def _history(tid: int) -> List[Tuple[str, str]]:
    """چند پیام آخر مکالمه برای زمینه‌ی هوش مصنوعی (قدیمی به جدید)."""
    out: List[Tuple[str, str]] = []
    for m in messages(tid)[-support_ai.HISTORY_ITEMS:]:
        who = {"admin": "ادمین", "ai": "دستیار"}.get(m.get("sender") or "", "کاربر")
        body = m.get("text") or ""
        if m.get("message_type") == "photo":
            body = "[تصویر] " + body
        out.append((who, body))
    return out


async def _record(ctx: Ctx, mtype: str, text: str, file_id: str = "") -> None:
    """
    پیام کاربر ذخیره می‌شود؛ متن‌های ساده اول به دستیار هوشمند داده می‌شود. اگر پاسخ داد (یا سوال شفاف‌سازی پرسید)
    پیام برای ادمین ارسال نمی‌شود؛ وگرنه (حیطه‌ی غیرمجاز / عدم اطمینان / خطا / عکس) برای ادمین ارجاع می‌شود.
    """
    username, name = _user_names(ctx)
    async with _lock_for(ctx.uid):          # پیام‌های پشت‌سرهم یک کاربر به‌ترتیب پردازش می‌شوند
        t = open_ticket(ctx.uid, username, name)
        history = _history(t["id"]) if mtype == "text" else []
        add_message(t["id"], ctx.uid, mtype, text, file_id)
        verdict: Optional[support_ai.Verdict] = None
        if mtype == "text" and (t.get("ai_state") or "") != AI_ESCALATED:
            await _typing(ctx)
            verdict = await support_ai.answer(ctx.uid, text, history)
        if verdict is not None and verdict.kind in ("answer", "clarify"):
            add_message(t["id"], 0, "text", verdict.reply, "", sender="ai")
            await _send_ai_reply(ctx, verdict.reply)
            return
        title, note = "پیام جدید پشتیبانی", ""
        if verdict is not None and verdict.kind == "escalate":
            title = f"پیام جدید پشتیبانی ({support_ai.ESCALATE_LABEL})"
            note = f"{support_ai.ESCALATE_LABEL}\nدلیل ارجاع: {esc(verdict.reason)}"
        try:
            set_ai_state(t["id"], AI_ESCALATED)
        except Exception:
            log.exception("set_ai_state failed")
        t["ai_state"] = AI_ESCALATED
        await _forward_to_admin(ctx.bot, t, mtype, text, file_id, title, note)
        await _confirm(ctx)


async def escalate(ctx: Ctx) -> None:
    """دکمه‌ی «پاسخ کافی نبود؛ ارجاع به ادمین» زیر پاسخ هوش مصنوعی."""
    t = get_open(ctx.uid)
    if t is None or message_count(t["id"]) == 0:
        await ctx.answer("مکالمه‌ی بازی برای ارجاع وجود ندارد.", alert=True)
        return
    ctx.user_data["state"] = {"kind": "support"}
    if (t.get("ai_state") or "") == AI_ESCALATED:
        await ctx.answer("درخواست شما قبلاً برای ادمین ارسال شده است.", alert=True)
        return
    await ctx.answer()
    set_ai_state(t["id"], AI_ESCALATED)
    t["ai_state"] = AI_ESCALATED
    await _forward_to_admin(
        ctx.bot, t, "text", "", "", f"درخواست بررسی ادمین ({support_ai.ESCALATE_LABEL})",
        f"{support_ai.ESCALATE_LABEL}\nدلیل ارجاع: کاربر پاسخ خودکار را کافی ندانست.",
    )
    await _confirm(ctx, ESCALATED_TEXT)


async def handle_user_text(ctx: Ctx) -> bool:
    """متن کاربر در حالت پشتیبانی (هیچ‌وقت به‌عنوان دستور ربات تفسیر نمی‌شود). True یعنی پردازش شد."""
    if not active(ctx):
        return False
    msg = ctx.update.effective_message
    text = (msg.text or "").strip() if msg else ""
    if not text:
        return True
    if len(text) > MAX_TEXT:
        await ctx.send(f"{NO} پیام بسیار طولانی است (حداکثر {MAX_TEXT} کاراکتر). آن را کوتاه‌تر کنید و دوباره ارسال کنید.", parse_mode=None)
        return True
    await _record(ctx, "text", text)
    return True


async def handle_user_media(ctx: Ctx) -> bool:
    """عکس کاربر در حالت پشتیبانی (کپشن به‌عنوان متن ذخیره می‌شود)؛ فایل پذیرفته نمی‌شود."""
    if not active(ctx):
        return False
    msg = ctx.update.effective_message
    if msg is None:
        return True
    if msg.photo:
        cap = (msg.caption or "").strip()[:MAX_CAPTION]
        await _record(ctx, "photo", cap, msg.photo[-1].file_id)
    else:
        await ctx.send(FILE_NOT_ALLOWED, parse_mode=None)
    return True
