"""خرید و تمدید اشتراک (ربات و تحلیل ساختاری): شبکه‌های پرداخت، پرداخت خودکار چندشبکه‌ای،
دستی، تخفیف و دوره‌ی رایگان — مشترک بین ربات و پنل مدیریت وب‌اپ."""
from __future__ import annotations

import logging
import math
import re
import time
from typing import Any, Dict, List, Optional, Tuple

import admin_notify
import broadcaster
import config
import crypto_verify
import payments
import plans
import templates
from database import db
from models import SubRequest
from ui import (
    NO, OK, STYLE_RED, WARN, Ctx, back_row, btn, esc, fmt_duration, jalali_ts, kb, menu_markup_for,
)

log = logging.getLogger("subscription")

Page = Tuple[str, object]

MAX_TX_TEXT = 1000
BTN_BUY = "خرید اشتراک"
BTN_RENEW = "تمدید اشتراک"
PLAN_TITLE = {
    plans.PLAN_BOT: "اشتراک ربات",
    plans.PLAN_WEBAPP: "اشتراک تحلیل ساختاری",
}
PLAN_SERVICE = {
    plans.PLAN_BOT: payments.SERVICE_BOT,
    plans.PLAN_WEBAPP: payments.SERVICE_WEBAPP,
}


# ----------------------------------------------------------------------------
# وضعیت و قواعد
# ----------------------------------------------------------------------------
def configured(plan: str = plans.PLAN_BOT) -> bool:
    return plans.configured(plan)


def enabled(plan: str = plans.PLAN_BOT) -> bool:
    return plans.plan_enabled(plan)


def enforced() -> bool:
    return plans.bot_access_enforced()


# نماهنگ تخفیف برای پنل ادمین ربات (admin_panel این‌ها را از subscription صدا می‌زند).
def discount_active(plan: str = plans.PLAN_BOT) -> bool:
    return plans.discount_active(plan)


def discount_until(plan: str = plans.PLAN_BOT) -> float:
    return plans.discount_until(plan)


def discount_pct(plan: str = plans.PLAN_BOT) -> float:
    return plans.discount_pct(plan)


def final_amount(plan: str = plans.PLAN_BOT, amount: Optional[float] = None) -> Optional[float]:
    return plans.final_amount(plan, amount)


def signal_allowed(uid: int) -> bool:
    return plans.bot_feature_allowed(uid, "signals")


def settings_allowed(uid: int) -> bool:
    return plans.bot_feature_allowed(uid, "settings")


def days_left(uid: int) -> int:
    rem = db.sub_until(uid) - time.time()
    return max(0, int(math.ceil(rem / 86400.0))) if rem > 0 else 0


def sub_btn(label: str):
    """دکمه‌ی خرید / تمدید اشتراک (قرمز)."""
    return btn(label, "u:sub", style=STYLE_RED)


def button_label(uid: int) -> Optional[str]:
    """متن دکمه‌ی خرید/تمدید در منوی کاربر؛ None یعنی دکمه نمایش داده نشود."""
    if uid == config.ADMIN_UID or not enforced():
        return None
    until = db.sub_until(uid)
    now = time.time()
    if until <= 0:
        return BTN_BUY
    if until <= now:
        return BTN_RENEW
    if until - now <= plans.plan_renew_days(plans.PLAN_BOT) * 86400.0:
        return BTN_RENEW
    return None


def status_lines(uid: int) -> List[str]:
    """سطرهای وضعیت اشتراک برای صفحه‌ی «وضعیت حساب» کاربر (HTML امن)."""
    until = db.sub_until(uid)
    now = time.time()
    lines: List[str] = []
    if until <= 0:
        if enabled():
            lines.append(f"اشتراک: {NO} ندارد")
    elif until > now:
        lines.append(f"اشتراک: {OK} فعال")
        lines.append(f"پایان اشتراک: {jalali_ts(until)} ({days_left(uid)} روز مانده است)")
    else:
        lines.append(f"اشتراک: {NO} منقضی شده است")
        lines.append(f"پایان اشتراک: {jalali_ts(until)}")
    claim = plans.free_claim(uid, plans.PLAN_BOT)
    if claim and float(claim.get("ends_at") or 0) > now:
        lines.append(f"{WARN} دوره‌ی رایگان تا {jalali_ts(float(claim['ends_at']))}")
    return lines


def admin_line(uid: int) -> str:
    until = db.sub_until(uid)
    now = time.time()
    if until <= 0:
        return f"اشتراک: {NO} ندارد"
    if until > now:
        return f"اشتراک: {OK} فعال تا {jalali_ts(until)} ({days_left(uid)} روز مانده است)"
    return f"اشتراک: {NO} منقضی شده در {jalali_ts(until)}"


# ----------------------------------------------------------------------------
# مبلغ، تخفیف و متن خرید
# ----------------------------------------------------------------------------
def current_expected(plan: str = plans.PLAN_BOT, network: Optional[Dict[str, Any]] = None) -> float:
    # منبع یکتا: plans.payable_amount مبلغ همان شبکه را با تخفیفِ فعال برمی‌گرداند
    # (تخفیف ۱۰۰٪ → 0.0 حفظ می‌شود، چون payable_amount مقدار نهایی صفر را معتبر می‌داند).
    return float(plans.payable_amount(plan, network))


def render_purchase(plan: str, template: str, wallet: str, amount: float) -> str:
    final = plans.final_amount(plan, amount)
    text = templates.render_sub(template, wallet, amount, final)
    if final is not None:
        left = plans.discount_until(plan) - time.time()
        if plans.discount_kind(plan) == plans.DISC_FIXED:
            label = f"{config.fmt_number(plans.discount_value(plan))} دلار تخفیف"
        else:
            label = f"{config.fmt_number(plans.discount_pct(plan))}٪ تخفیف"
        text += f"\n\n🎁 {label} فقط تا {fmt_duration(max(0, left))} دیگر"
    return text


def _default_network(plan: str) -> Optional[Dict[str, Any]]:
    row = plans.default_network(plan)
    if row is not None:
        return row
    rows = [r for r in plans.network_rows(plan, enabled_only=True) if plans.network_wallet(r)]
    return rows[0] if rows else None


def purchase_text(plan: str, network: Optional[Dict[str, Any]] = None) -> str:
    row = network or _default_network(plan)
    wallet = plans.network_wallet(row) or plans.legacy_wallet(plan)
    # مبلغ پایه (بدون تخفیف) به متن خرید داده می‌شود تا خط‌خوردگی/قیمت‌تخفیف‌دار درست نمایش داده شود؛
    # پیش‌تر همین‌جا current_expected (که خودش تخفیف‌دار است) رد می‌شد و تخفیف دوبار اعمال می‌گشت.
    amount = float(plans.network_amount(row, plan))
    return render_purchase(plan, plans.plan_template(plan), wallet, amount)


def _network_lines(plan: str) -> List[Dict[str, Any]]:
    return [r for r in plans.network_rows(plan, enabled_only=True) if plans.network_wallet(r)]


def card_lines(cards: List[Dict[str, Any]]) -> List[str]:
    """خط‌های نمایش مقصدهای کارت‌به‌کارت در صفحه‌ی خرید (ورودی: plans.checkout_destinations)."""
    out: List[str] = []
    for c in cards:
        digits = re.sub(r"\D", "", str(c.get("card_number") or ""))
        if len(digits) != 16:
            continue
        who = " · ".join(esc(x) for x in (c.get("bank_name"), c.get("holder_name")) if x)
        out.append(f"💳 {digits[:4]} {digits[4:8]} {digits[8:12]} {digits[12:]}" + (f" — {who}" if who else ""))
        if c.get("shaba"):
            out.append(f"شبا: {esc(c['shaba'])}")
    return out


def card_label(c: Dict[str, Any]) -> str:
    digits = re.sub(r"\D", "", str(c.get("card_number") or ""))
    base = f"کارت {digits[:4]}…{digits[-4:]}" if len(digits) == 16 else "کارت‌به‌کارت"
    bank = (c.get("bank_name") or "").strip()
    return f"{base} — {bank}" if bank else base


def _free_available(uid: int, plan: str) -> bool:
    return (
        plans.free_on(plan)
        and plans.free_claim(uid, plan) is None
        and not (db.has_active_sub(uid) if plan == plans.PLAN_BOT else db.has_active_webapp_sub(uid))
    )


# ----------------------------------------------------------------------------
# صفحه‌های کاربر
# ----------------------------------------------------------------------------
def choice_page(uid: int, note: str = "") -> Page:
    """انتخاب نوع اشتراک: ربات یا تحلیل ساختاری (فقط گزینه‌های فعال نمایش داده می‌شوند)."""
    prefix = f"{note}\n\n" if note else ""
    rows = []
    lines = [prefix + "خرید اشتراک", "", "نوع اشتراک مورد نظر خود را انتخاب کنید:"]
    if plans.configured(plans.PLAN_BOT):
        label = BTN_RENEW if db.has_active_sub(uid) else BTN_BUY
        rows.append([btn(label, "u:subbot")])
        until = db.sub_until(uid)
        lines.append(
            f"اشتراک ربات: {config.fmt_number(plans.plan_amount(plans.PLAN_BOT))} دلار"
            + (f" | فعال تا {jalali_ts(until)}" if until > time.time() else "")
        )
    elif plans.plan_enabled(plans.PLAN_BOT) and plans.plan_amount(plans.PLAN_BOT) > 0:
        rows.append([btn("خرید اشتراک ربات", "u:subbot")])
    if plans.configured(plans.PLAN_WEBAPP):
        label = BTN_RENEW if db.has_active_webapp_sub(uid) else BTN_BUY
        rows.append([btn(label + " تحلیل ساختاری", "u:wsub")])
        until = db.webapp_sub_until(uid)
        lines.append(
            f"اشتراک تحلیل ساختاری: {config.fmt_number(plans.plan_amount(plans.PLAN_WEBAPP))} دلار"
            + (f" | فعال تا {jalali_ts(until)}" if until > time.time() else "")
        )
    elif plans.plan_enabled(plans.PLAN_WEBAPP) and plans.plan_amount(plans.PLAN_WEBAPP) > 0:
        rows.append([btn("خرید اشتراک تحلیل ساختاری", "u:wsub")])
    if _free_available(uid, plans.PLAN_BOT):
        rows.append([btn(f"🎁 {plans.free_days(plans.PLAN_BOT)} روز رایگان ربات", "u:free:bot")])
    if _free_available(uid, plans.PLAN_WEBAPP):
        rows.append([btn(f"🎁 {plans.free_days(plans.PLAN_WEBAPP)} روز رایگان تحلیل ساختاری", "u:free:webapp")])
    if not rows:
        return "خرید اشتراک فعلاً در دسترس نیست.", kb(back_row("u:menu"))
    rows.append(back_row("u:menu"))
    return "\n".join(lines), kb(*rows)


def purchase_page(uid: int, plan: str = plans.PLAN_BOT, note: str = "") -> Page:
    plan = plans.normalize_plan(plan)
    prefix = f"{note}\n\n" if note else ""
    title = PLAN_TITLE[plan]
    rows: List[List[Any]] = []
    lines = [prefix + title]
    if plan == plans.PLAN_BOT and db.has_active_sub(uid):
        lines.append(f"وضعیت: {OK} فعال تا {jalali_ts(db.sub_until(uid))} ({days_left(uid)} روز مانده)")
    elif plan == plans.PLAN_WEBAPP and db.has_active_webapp_sub(uid):
        rem = db.webapp_sub_until(uid) - time.time()
        left = max(0, int(math.ceil(rem / 86400.0))) if rem > 0 else 0
        lines.append(
            f"وضعیت: {OK} فعال تا {jalali_ts(db.webapp_sub_until(uid))} ({left} روز مانده)"
        )
    claim = plans.free_claim(uid, plan)
    if claim and float(claim.get("ends_at") or 0) > time.time():
        lines.append(f"{WARN} دوره‌ی رایگان فعال تا {jalali_ts(float(claim['ends_at']))}")

    mode = plans.plan_mode(plan)
    networks = _network_lines(plan)
    dests = plans.checkout_destinations(plan)
    cards = dests["cards"]
    card_txt = card_lines(cards)
    pending = db.pending_request_of(uid, service_type=PLAN_SERVICE[plan])
    if not networks and not card_txt and not plans.configured(plan):
        lines.append("\nخرید این اشتراک در حال حاضر در دسترس نیست.")
    else:
        lines.append("")
        lines.append("شبکه‌ی پرداخت را انتخاب کنید:")
        for row in networks:
            amount = current_expected(plan, row)
            rows.append([
                btn(
                    f"{plans.network_button_label(row)} · {config.fmt_number(amount)} {amount_unit(row['network_key'])}",
                    f"u:subn:{plan}:{row['network_key']}",
                )
            ])
        for c in cards:
            if len(re.sub(r"\D", "", str(c.get("card_number") or ""))) != 16:
                continue
            rows.append([
                btn(
                    f"{card_label(c)} · {config.fmt_number(current_expected(plan, None))} دلار",
                    f"u:subc:{plan}:{c['id']}",
                )
            ])
        active = _default_network(plan)
        if active is not None:
            lines.append("")
            lines.append(purchase_text(plan, active))
        if card_txt:
            lines.append("")
            lines.append("یا با کارت‌به‌کارت واریز کنید (رسید را ارسال می‌کنید):")
            lines.extend(card_txt)
        if mode == plans.MODE_MANUAL:
            lines.append("\nپس از واریز، رسید یا اطلاعات تراکنش را ارسال کنید تا ادمین بررسی کند.")
        elif mode == plans.MODE_AUTO:
            lines.append("\nپس از واریز، هش تراکنش (TxID) را ارسال کنید تا به‌صورت خودکار بررسی شود.")
    if _free_available(uid, plan):
        rows.append([btn(f"🎁 دریافت {plans.free_days(plan)} روز رایگان", f"u:free:{plan}")])
    if pending:
        lines.append(f"\n{OK} درخواست قبلی شما در انتظار بررسی ادمین است.")
    rows.append(back_row("u:sub"))
    return "\n".join(lines), kb(*rows)


def need_sub_page(uid: int, text: Optional[str] = None) -> Page:
    label = button_label(uid) or BTN_BUY
    rows = [[sub_btn(label)], back_row("u:menu")]
    return (
        text or ("برای دریافت سیگنال باید اشتراک فعال داشته باشید.\n"
                 "با دکمه‌ی زیر اشتراک تهیه کنید."),
        kb(*rows),
    )


# ----------------------------------------------------------------------------
# جریان انتخاب شبکه و ارسال تراکنش
# ----------------------------------------------------------------------------
def _flow(ctx: Ctx) -> Dict[str, Any]:
    flow = ctx.user_data.get("sub_flow") or {}
    plan = plans.normalize_plan(flow.get("plan") or plans.PLAN_BOT)
    network = flow.get("network") or ""
    card = None
    cid = str(flow.get("card") or "")
    if not network and cid.isdigit():
        found = plans.get_card(int(cid), active_only=True)
        if found is not None and plans.normalize_plan(found.get("plan") or "") == plan:
            card = found
    if card is not None:
        return {"plan": plan, "network": "", "row": None, "card": card}
    row = plans.network_row(plan, network) if network else _default_network(plan)
    if row is None:
        row = _default_network(plan)
    return {"plan": plan, "network": (row or {}).get("network_key", ""), "row": row, "card": None}


async def select_card(ctx: Ctx, plan: str, cid: str) -> None:
    """نمایش مقصد کارت‌به‌کارت و ورود به جریان ارسال رسید (واریز ارزی ندارد؛ بررسی دستی)."""
    plan = plans.normalize_plan(plan)
    card = plans.get_card(int(cid), active_only=True) if str(cid).isdigit() else None
    if card is None or plans.normalize_plan(card.get("plan") or "") != plan:
        await ctx.answer("این کارت در دسترس نیست.", alert=True)
        return
    await ctx.answer()
    ctx.user_data["sub_flow"] = {"plan": plan, "network": "", "card": str(int(cid))}
    digits = re.sub(r"\D", "", str(card.get("card_number") or ""))
    pretty = f"{digits[:4]} {digits[4:8]} {digits[8:12]} {digits[12:]}" if len(digits) == 16 else digits
    text = (
        f"{PLAN_TITLE[plan]} — کارت‌به‌کارت\n\n"
        f"مبلغ: {config.fmt_number(current_expected(plan, None))} دلار\n"
        f"شماره کارت:\n<code>{esc(pretty)}</code>\n"
    )
    who = " · ".join(x for x in ((card.get("bank_name") or "").strip(), (card.get("holder_name") or "").strip()) if x)
    if who:
        text += f"به نام {esc(who)}\n"
    if (card.get("shaba") or "").strip():
        text += f"شبا: <code>{esc(card['shaba'])}</code>\n"
    text += "\nپس از واریز، شماره‌ی مرجع/اطلاعات تراکنش یا تصویر رسید را ارسال کنید تا ادمین بررسی و تأیید کند."
    rows = [[btn("ارسال رسید / اطلاعات تراکنش", "u:stx")], back_row(f"u:subp:{plan}")]
    await ctx.show(text, kb(*rows))


async def select_network(ctx: Ctx, plan: str, network_key: str) -> None:
    plan = plans.normalize_plan(plan)
    row = plans.network_row(plan, network_key)
    if row is None or not int(row.get("enabled") or 0) or not plans.network_wallet(row):
        await ctx.answer("این شبکه‌ی پرداخت در دسترس نیست.", alert=True)
        return
    await ctx.answer()
    ctx.user_data["sub_flow"] = {"plan": plan, "network": network_key}
    mode = plans.plan_mode(plan)
    amount = current_expected(plan, row)
    text = (
        f"{PLAN_TITLE[plan]} — {plans.network_title(row)}\n\n"
        f"مبلغ: {config.fmt_number(amount)} {amount_unit(network_key)}\n"
        f"آدرس کیف پول:\n<code>{esc(plans.network_wallet(row))}</code>\n\n"
    )
    if mode == plans.MODE_AUTO:
        text += (
            "پس از واریز، هش تراکنش (TxID) را از کیف پول کپی کنید و دکمه‌ی زیر را بزنید. "
            "تراکنش به‌صورت خودکار روی شبکه بررسی می‌شود."
        )
        rows = [[btn("ثبت هش تراکنش (TxID)", "u:trx")]]
    else:
        text += (
            "پس از واریز، اطلاعات تراکنش (هش یا متن) و/یا تصویر رسید را ارسال کنید. "
            "ادمین تراکنش را بررسی و تأیید می‌کند."
        )
        rows = [[btn("ارسال رسید / اطلاعات تراکنش", "u:stx")]]
    rows.append(back_row(f"u:subp:{plan}"))
    await ctx.show(text, kb(*rows))


def _tx_data(ctx: Ctx) -> dict:
    return ctx.user_data.setdefault("sub_tx", {"text": "", "media": "", "mid": None})


def tx_page(ctx: Ctx, note: str = "") -> Page:
    d = _tx_data(ctx)
    flow = _flow(ctx)
    has_text, has_media = bool(d["text"]), bool(d["media"])
    prefix = f"{note}\n\n" if note else ""
    title = PLAN_TITLE[flow["plan"]]
    net = plans.network_title(flow["row"]) if flow["row"] else (card_label(flow["card"]) if flow["card"] else "")
    text = (
        f"{prefix}{title} — {net}\n\n"
        "هش تراکنش، متن یا تصویر رسید را همین‌جا ارسال کنید. می‌توانید چند پیام پشت سر هم بفرستید.\n\n"
        f"متن: {OK + ' ثبت شد' if has_text else NO + ' ندارد'}\n"
        f"تصویر / فایل: {OK + ' ثبت شد' if has_media else NO + ' ندارد'}\n\n"
        "پس از آماده شدن، «ثبت درخواست» را بزنید."
    )
    rows = []
    if has_text or has_media:
        rows.append([btn("ثبت درخواست", "u:stxs")])
        rows.append([btn("پاک کردن و شروع دوباره", "u:stx")])
    rows.append(back_row(f"u:subp:{flow['plan']}"))
    return text, kb(*rows)


async def start_tx(ctx: Ctx) -> None:
    flow = _flow(ctx)
    plan = flow["plan"]
    if not plans.plan_enabled(plan):
        await ctx.show(*purchase_page(ctx.uid, plan))
        return
    if db.pending_request_of(ctx.uid, service_type=PLAN_SERVICE[plan]):
        await ctx.show(*purchase_page(ctx.uid, plan))
        return
    if flow["row"] is None and flow["card"] is None:
        await ctx.show(*purchase_page(ctx.uid, plan, "شبکه‌ی پرداخت را انتخاب کنید."))
        return
    ctx.user_data["sub_tx"] = {"text": "", "media": "", "mid": ctx.q.message.message_id if ctx.q and ctx.q.message else None}
    ctx.user_data["state"] = {"kind": "sub_tx"}
    await ctx.show(*tx_page(ctx))


async def _refresh(ctx: Ctx, note: str = "") -> None:
    d = _tx_data(ctx)
    text, markup = tx_page(ctx, note)
    sent = await ctx.show_input(d.get("mid"), text, markup)
    if sent is not None:
        d["mid"] = sent.message_id


def _active(ctx: Ctx) -> bool:
    st = ctx.user_data.get("state")
    return bool(st) and st.get("kind") == "sub_tx"


async def handle_user_text(ctx: Ctx) -> bool:
    """متن کاربر در حالت ارسال تراکنش. True یعنی پردازش شد."""
    st = ctx.user_data.get("state")
    if st and st.get("kind") == "auto_tx":
        msg = ctx.update.effective_message
        tx_hash = (msg.text or "").strip() if msg else ""
        await process_auto_tx(ctx, tx_hash)
        return True
    if not _active(ctx):
        return False
    msg = ctx.update.effective_message
    raw = (msg.text or "").strip() if msg else ""
    d = _tx_data(ctx)
    if not raw:
        return True
    merged = (d["text"] + "\n" + raw).strip() if d["text"] else raw
    if len(merged) > MAX_TX_TEXT:
        await _refresh(ctx, f"{NO} متن بسیار طولانی شد (حداکثر {MAX_TX_TEXT} کاراکتر).")
        return True
    d["text"] = merged
    await _refresh(ctx)
    return True


async def handle_user_media(ctx: Ctx) -> bool:
    """عکس/فایل کاربر در حالت ارسال تراکنش (کپشن به متن اضافه می‌شود)."""
    if not _active(ctx):
        return False
    msg = ctx.update.effective_message
    if msg is None:
        return True
    d = _tx_data(ctx)
    if msg.photo:
        d["media"] = "p:" + msg.photo[-1].file_id
    elif msg.document:
        d["media"] = "d:" + msg.document.file_id
    else:
        return False
    cap = (msg.caption or "").strip()
    note = ""
    if cap:
        merged = (d["text"] + "\n" + cap).strip() if d["text"] else cap
        if len(merged) <= MAX_TX_TEXT:
            d["text"] = merged
        else:
            note = f"{NO} کپشن بسیار طولانی بود و اضافه نشد."
    await _refresh(ctx, note)
    return True


async def submit_tx(ctx: Ctx) -> None:
    """ثبت درخواست خرید دستی و خبر دادن به ادمین."""
    flow = _flow(ctx)
    plan, row, card = flow["plan"], flow["row"], flow["card"]
    if (row is None and card is None) or not plans.plan_enabled(plan):
        await ctx.show(*purchase_page(ctx.uid, plan))
        return
    d = ctx.user_data.get("sub_tx") or {}
    text, media = (d.get("text") or "").strip(), d.get("media") or ""
    if not text and not media:
        await ctx.answer(f"{NO} اول اطلاعات تراکنش را ارسال کنید.", alert=True)
        return
    if db.pending_request_of(ctx.uid, service_type=PLAN_SERVICE[plan]):
        await ctx.show(*purchase_page(ctx.uid, plan))
        return
    dest = card_label(card) if card else plans.network_title(row)
    rid = db.add_sub_request(ctx.uid, f"مقصد: {dest}\n{text}".strip(), media, service_type=PLAN_SERVICE[plan])
    expected = current_expected(plan, row)
    receiver = plans.network_wallet(row) if row else re.sub(r"\D", "", str(card.get("card_number") or ""))
    tx_ref = ""
    if card:
        token = text.split("\n")[0].strip()
        if 4 <= len(token) <= 64 and re.fullmatch(r"[A-Za-z0-9_-]+", token):
            tx_ref = token
    pid = 0
    try:
        pid = payments.create(
            ctx.uid, 0, expected, payments.PENDING, "manual", tx_hash=tx_ref or None,
            receiver=receiver, request_id=rid,
            service_type=PLAN_SERVICE[plan], network=flow["network"],
        )
    except payments.DuplicateHash:
        db.x("DELETE FROM sub_requests WHERE id = ? AND status = 'pending'", (int(rid),))
        await ctx.answer(f"{NO} این شماره پیگیری قبلاً در سیستم ثبت شده است.", alert=True)
        return
    except Exception:
        log.exception("payment record for request %s failed", rid)
        db.x("DELETE FROM sub_requests WHERE id = ? AND status = 'pending'", (int(rid),))
        await ctx.answer(f"{NO} ثبت درخواست ناموفق بود؛ لطفاً دوباره تلاش کنید.", alert=True)
        return
    ctx.user_data.pop("sub_tx", None)
    ctx.user_data.pop("state", None)
    await notify_admin_new(ctx.bot, rid, ctx.uid, pid, plan)
    await ctx.show(
        f"{OK} درخواست شما ثبت شد.\nپس از بررسی ادمین، نتیجه در همین‌جا به شما اطلاع داده می‌شود.",
        kb(back_row("u:menu")),
    )


# ----------------------------------------------------------------------------
# پرداخت خودکار (همه‌ی شبکه‌ها)
# ----------------------------------------------------------------------------
_busy: set = set()


def auto_hash_hint(row: Dict[str, Any]) -> str:
    chain = (row or {}).get("chain") or ""
    if chain in ("bsc", "arbitrum"):
        return "کد هش تراکنش (TxID) که با 0x شروع می‌شود را ارسال کنید:"
    if chain == "tron":
        return "هش تراکنش TRON (۶۴ کاراکتر هگز) را ارسال کنید:"
    if chain == "ton":
        return "هش تراکنش TON (هگز ۶۴ کاراکتری یا Base64) را ارسال کنید:"
    return "هش تراکنش را ارسال کنید:"


async def start_bsc_tx(ctx: Ctx) -> None:
    """شروع فرآیند ثبت هش تراکنش برای پرداخت خودکار."""
    flow = _flow(ctx)
    plan, row = flow["plan"], flow["row"]
    if row is None or plans.plan_mode(plan) != plans.MODE_AUTO:
        await ctx.answer("پرداخت خودکار برای این اشتراک فعال نیست.", alert=True)
        return
    ctx.user_data["state"] = {"kind": "auto_tx"}
    await ctx.show(auto_hash_hint(row), kb(back_row(f"u:subp:{plan}")), parse_mode=None)


async def process_auto_tx(ctx: Ctx, tx_hash: str) -> None:
    """بررسی هش تراکنش روی شبکه‌ی انتخاب‌شده، مقایسه‌ی مبلغ و ثبت در تاریخچه‌ی پرداخت‌ها."""
    flow = _flow(ctx)
    plan, row = flow["plan"], flow["row"]
    uid = ctx.uid
    back = kb(back_row(f"u:subp:{plan}"))
    if row is None or plans.plan_mode(plan) != plans.MODE_AUTO:
        ctx.user_data.pop("state", None)
        await ctx.send("پرداخت خودکار در حال حاضر فعال نیست.", kb(back_row("u:menu")), parse_mode=None)
        return
    h, err = crypto_verify.normalize_hash(row.get("chain") or "", tx_hash)
    if not h:
        await ctx.send(f"{NO} {err}", back, parse_mode=None)
        return
    if uid in _busy:
        await ctx.send("بررسی تراکنش قبلی شما هنوز در حال انجام است؛ چند لحظه صبر کنید.", back, parse_mode=None)
        return

    async def duplicate(existing) -> None:
        await ctx.send(TEXT_DUP_USER, back, parse_mode=None)
        await notify_admin_duplicate(ctx.bot, uid, h, existing, plan)

    existing = payments.find_by_hash(h)
    if existing is not None:
        await duplicate(existing)
        return

    _busy.add(uid)
    try:
        wallet = plans.network_wallet(row)
        expected = current_expected(plan, row)
        await ctx.send(
            f"در حال استعلام تراکنش از شبکه {plans.network_title(row)}... (چند لحظه صبر کنید)",
            back, parse_mode=None,
        )
        ver = await crypto_verify.verify_transfer(row, h, wallet)
        if not ver.ok:
            await ctx.send(f"{NO} {ver.error}", back, parse_mode=None)
            return
        amount = ver.amount
        if amount <= 0:
            await ctx.send(f"{NO} مبلغ این تراکنش صفر است و قابل قبول نیست.", back, parse_mode=None)
            return
        case = payments.compare(amount, expected)
        status = {
            payments.EQUAL: payments.APPROVED, payments.GREATER: payments.SUSPENDED, payments.LESS: payments.REJECTED,
        }[case]
        try:
            pid = payments.create(
                uid, float(amount), expected, status, "auto", h, ver.sender, ver.receiver or wallet,
                service_type=PLAN_SERVICE[plan], network=row["network_key"],
            )
        except payments.DuplicateHash as e:
            await duplicate(e.existing)
            return
        p = payments.get(pid) or {}
        ctx.user_data.pop("state", None)
        if status == payments.APPROVED:
            ok, message = await _activate_plan(ctx.bot, uid, plan, pid)
            if not ok:
                await ctx.send(message, _user_markup(), parse_mode=None)
                return
            await ctx.send(f"{OK} پرداخت شما با موفقیت تأیید شد.", kb(back_row("u:menu")), parse_mode=None)
        elif status == payments.SUSPENDED:
            await _announce_suspended(ctx.bot, p, plan)
        else:
            await _announce_less(ctx.bot, p, plan)
        await notify_admin_payment(ctx.bot, p, "تراکنش خودکار جدید ثبت شد", plan)
    except Exception:
        log.exception("auto payment processing failed")
        await ctx.send(f"{NO} خطای غیرمنتظره رخ داد؛ کمی بعد دوباره امتحان کنید.", back, parse_mode=None)
    finally:
        _busy.discard(uid)


async def _activate_plan(bot, uid: int, plan: str, pid: int) -> Tuple[bool, str]:
    days = plans.plan_days(plan)
    try:
        if plan == plans.PLAN_WEBAPP:
            from webapp import activation

            result = await activation.activate(int(uid), days, bot=bot)
            message = result["message"]
            if not result["sent"] and result.get("password"):
                message += f" رمز عبور کاربر: {result['password']}"
        else:
            until = db.grant_sub(int(uid), days)
            sent = False
            try:
                sent = bool(await notify_approved(bot, int(uid), until))
            except Exception:
                log.warning("activation notice to %s failed", uid, exc_info=True)
            message = (
                "اشتراک ربات فعال شد." if sent
                else "اشتراک ربات فعال شد، اما ارسال پیام به کاربر ممکن نبود."
            )
        return True, message
    except Exception:
        log.exception("activation failed for payment %s", pid)
        payments.set_status(int(pid), payments.PENDING, (payments.APPROVED,), "activation failed")
        return False, "پرداخت ثبت شد اما فعال‌سازی خودکار انجام نشد؛ ادمین بررسی می‌کند."


# ----------------------------------------------------------------------------
# دوره‌ی رایگان
# ----------------------------------------------------------------------------
async def claim_free(ctx: Ctx, plan: str) -> None:
    plan = plans.normalize_plan(plan)
    ok, err, info = plans.claim_free(ctx.uid, plan)
    if not ok:
        await ctx.answer(err, alert=True)
        return
    await ctx.answer()
    until = float(info.get("until") or 0)
    if plan == plans.PLAN_WEBAPP:
        from webapp import activation
        from webapp.auth import login_url

        password = (info.get("password") or "").strip()
        if password:
            await ctx.send(activation.activation_text(ctx.uid, password), activation.activation_markup())
        else:
            await ctx.send(
                f"{OK} دوره‌ی رایگان تحلیل ساختاری فعال شد.\n"
                f"پایان: {jalali_ts(until)}\n"
                "با همان رمز قبلی وارد شوید.",
                kb([btn("ورود به وب‌اپ تحلیل ساختاری", url=login_url())], [btn("منوی اصلی", "u:mn")]),
            )
    else:
        await ctx.send(
            f"{OK} دوره‌ی رایگان ربات فعال شد.\nپایان: {jalali_ts(until)}",
            menu_markup_for(ctx.uid),
        )
    await admin_notify.notify(
        ctx.bot,
        f"🎁 فعال‌سازی دوره‌ی رایگان\nپلن: {PLAN_TITLE[plan]}\nکاربر: <code>{ctx.uid}</code>\n"
        f"پایان: {jalali_ts(until)}",
        kind="free_claim",
        dedupe_key=f"free_claim:{ctx.uid}:{plan}",
        cooldown=3600,
    )


# ----------------------------------------------------------------------------
# اعلان‌ها (ادمین در پنل وب‌اپ)
# ----------------------------------------------------------------------------
async def notify_admin_new(bot, rid: int, uid: int, pid: int = 0, plan: str = plans.PLAN_BOT) -> None:
    p = payments.get(int(pid)) if pid else None
    expected = float((p or {}).get("expected_amount") or current_expected(plan))
    network = str((p or {}).get("network") or "")
    text = (
        f"درخواست خرید جدید — {PLAN_TITLE[plan]} (پرداخت دستی، در انتظار بررسی)\n"
        f"کاربر: <code>{uid}</code>\nشماره درخواست: {rid}\n"
        f"مبلغ: {_money(expected, network)}"
        + (f"\nشماره پیگیری تراکنش: #{pid}" if pid else "")
    )
    await admin_notify.notify(bot, text, kind="new_payment", dedupe_key=f"new_payment:{rid}", cooldown=0)


async def notify_admin_payment(bot, p: dict, text: str, plan: str = plans.PLAN_BOT) -> None:
    lines = [
        f"{text} — {PLAN_TITLE[plan]}",
        "",
        f"شماره پیگیری: #{p['id']}",
        f"کاربر: <code>{p['user_id']}</code>",
        f"روش: {esc(_method_fa(p['method']))}",
        f"شبکه: {esc(_network_title(p.get('network') or ''))}",
        f"مبلغ واریزی: {_money(p['amount'], p.get('network') or '')} | "
        f"مبلغ اشتراک: {_money(p['expected_amount'], p.get('network') or '')}",
    ]
    if p.get("tx_hash"):
        lines.append(f"هش: <code>{esc(p['tx_hash'])}</code>")
    await admin_notify.notify(
        bot, "\n".join(lines), kind="payment",
        dedupe_key=f"payment:{p['id']}:{p.get('status')}", cooldown=0,
    )


async def notify_admin_duplicate(bot, uid: int, tx_hash: str, existing: Optional[dict], plan: str = plans.PLAN_BOT) -> None:
    ref = ""
    if existing and existing.get("id"):
        ref = f"\nمربوط به تراکنش #{existing['id']} (کاربر <code>{existing['user_id']}</code>)"
    text = (
        f"{WARN} تلاش برای ثبت هش تکراری — {PLAN_TITLE[plan]}\n"
        f"کاربر: <code>{uid}</code>\nهش: <code>{esc(tx_hash)}</code>{ref}"
    )
    await admin_notify.notify(
        bot, text, kind="duplicate_hash", dedupe_key=f"dup:{uid}:{tx_hash}", cooldown=300,
    )


async def notify_approved(bot, uid: int, until: float) -> Optional[int]:
    text = (
        f"{OK} اشتراک شما فعال شد.\n"
        f"پایان اشتراک: {jalali_ts(until)}"
    )
    return await broadcaster.send_html(bot, uid, text, menu_markup_for(uid))


async def notify_extended(bot, uid: int, days: int, until: float) -> None:
    text = (
        f"{OK} {days} روز به اشتراک شما اضافه شد.\n"
        f"پایان اشتراک: {jalali_ts(until)}"
    )
    await broadcaster.send_html(bot, uid, text, menu_markup_for(uid))


async def notify_rejected(bot, uid: int, reason: str) -> None:
    text = f"{NO} درخواست خرید اشتراک شما تأیید نشد.\nدلیل: {esc(reason)}"
    markup = kb([sub_btn(BTN_BUY)], [btn("منوی اصلی", "u:mn")])
    await broadcaster.send_html(bot, uid, text, markup)


async def notify_expired(bot, uid: int) -> None:
    text = "اشتراک شما به پایان رسید و دریافت سیگنال برای شما غیرفعال شد.\nبرای ادامه، اشتراک خود را تمدید کنید."
    markup = kb([sub_btn(BTN_RENEW)], [btn("منوی اصلی", "u:mn")]) if enforced() else menu_markup_for(uid)
    await broadcaster.send_plain(bot, uid, text, markup)


# ----------------------------------------------------------------------------
# ابزار متن و پیام‌ها
# ----------------------------------------------------------------------------
def _n(x) -> str:
    try:
        return config.fmt_number(round(float(x), 6))
    except (TypeError, ValueError):
        return str(x)


def amount_unit(network_key: str) -> str:
    """واحد نمایش مبلغ: دارایی همان شبکه (USDT/ARB/BNB/TRX/DOGS) و برای کارت، دلار."""
    item = plans.NETWORK_BY_KEY.get(network_key or "")
    return str((item or {}).get("asset") or "دلار")


def _money(amount, network_key: str = "") -> str:
    return f"{_n(amount)} {amount_unit(network_key)}"


def _user_markup() -> object:
    return kb([btn("پشتیبانی", "u:sup")], [btn("منوی اصلی", "u:mn")])


def _method_fa(method: str) -> str:
    return payments.METHOD_FA.get(method, method)


def _network_title(key: str) -> str:
    item = plans.NETWORK_BY_KEY.get(key or "")
    if item is None:
        return key or "-"
    return f"{item['asset']} · {item['network_label']}"


def text_suspended_user(expected: float, paid: float, network: str = "") -> str:
    return (
        "اشتراک شما به وضعیت معلق ثبت شد.\n"
        f"مبلغ اشتراک فعلی: {_money(expected, network)}.\n"
        f"مبلغ واریزی شما: {_money(paid, network)}.\n"
        "لطفا از طریق دکمه پشتیبانی در ارتباط باشید."
    )


def text_less_user(expected: float, paid: float, network: str = "") -> str:
    return (
        f"مبلغ واریزی شما ({_money(paid, network)}) کمتر از مبلغ اشتراک ({_money(expected, network)}) است. "
        "اشتراک فعال نشد. برای بررسی با پشتیبانی در ارتباط باشید."
    )


TEXT_DUP_USER = f"{NO} این هش تراکنش قبلاً در سیستم ثبت شده است و دوباره قابل استفاده نیست."
TEXT_DUP_REJECT_USER = (
    f"{NO} درخواست شما تأیید نشد؛ این هش تراکنش قبلاً در سیستم ثبت شده است. برای بررسی با پشتیبانی در ارتباط باشید."
)
TEXT_GENERIC_REJECT_USER = (
    f"{NO} تراکنش شما پس از بررسی ادمین تأیید نشد. برای اطلاعات بیشتر با پشتیبانی در ارتباط باشید."
)


async def _announce_suspended(bot, p: dict, plan: str) -> None:
    await broadcaster.send_plain(
        bot, p["user_id"],
        text_suspended_user(p["expected_amount"], p["amount"], p.get("network") or ""),
        _user_markup(),
    )
    await notify_admin_payment(bot, p, "تراکنش با مبلغ اضافه ثبت شد (معلق)", plan)


async def _announce_less(bot, p: dict, plan: str) -> None:
    await broadcaster.send_plain(
        bot, p["user_id"],
        text_less_user(p["expected_amount"], p["amount"], p.get("network") or ""),
        _user_markup(),
    )
    await notify_admin_payment(bot, p, "تراکنش با مبلغ کمتر ثبت شد (رد شد)", plan)


# ----------------------------------------------------------------------------
# تأیید / معلق / رد (از پنل مدیریت وب‌اپ)
# ----------------------------------------------------------------------------
def _plan_of(p: Dict[str, Any]) -> str:
    return plans.PLAN_WEBAPP if p.get("service_type") == payments.SERVICE_WEBAPP else plans.PLAN_BOT


async def approve_payment(bot, pid: int, note: str = "", announce_admin: bool = False) -> Tuple[bool, str]:
    p = payments.get(pid)
    if p is None:
        return False, "این تراکنش پیدا نشد."
    plan = _plan_of(p)
    if not payments.set_status(pid, payments.APPROVED, (payments.PENDING, payments.SUSPENDED), note or None):
        return False, "این تراکنش قبلاً بررسی شده است."
    ok, message = await _activate_plan(bot, int(p["user_id"]), plan, int(pid))
    if not ok:
        payments.set_status(int(pid), payments.PENDING, (payments.APPROVED,), "activation failed")
        return False, message
    if p.get("request_id"):
        db.decide_sub_request(int(p["request_id"]), "approved")
    if announce_admin:
        p = payments.get(pid) or p
        await notify_admin_payment(bot, p, "تراکنش تأیید شد ✅", plan)
    return True, message


async def suspend_payment(bot, pid: int, note: str = "") -> Tuple[bool, str]:
    p = payments.get(pid)
    if p is None:
        return False, "این تراکنش پیدا نشد."
    plan = _plan_of(p)
    if not payments.set_status(pid, payments.SUSPENDED, (payments.PENDING,), note or None):
        return False, "این تراکنش در وضعیت «در انتظار» نیست."
    await _announce_suspended(bot, payments.get(pid) or p, plan)
    return True, "تراکنش معلق شد و به کاربر اطلاع داده شد."


async def reject_payment(bot, pid: int, note: str = "") -> Tuple[bool, str]:
    p = payments.get(pid)
    if p is None:
        return False, "این تراکنش پیدا نشد."
    plan = _plan_of(p)
    case = payments.case_of(p) if p["status"] == payments.PENDING else "generic"
    if not payments.set_status(pid, payments.REJECTED, (payments.PENDING, payments.SUSPENDED), note or None):
        return False, "این تراکنش قبلاً بررسی شده است."
    if p.get("request_id"):
        db.decide_sub_request(int(p["request_id"]), "rejected", note or case)
    p = payments.get(pid) or p
    if case == payments.LESS:
        await _announce_less(bot, p, plan)
    elif case == "dup":
        await broadcaster.send_plain(bot, p["user_id"], TEXT_DUP_REJECT_USER, _user_markup())
    else:
        await broadcaster.send_plain(bot, p["user_id"], TEXT_GENERIC_REJECT_USER, _user_markup())
    return True, "تراکنش رد شد و به کاربر اطلاع داده شد."


# ----------------------------------------------------------------------------
# سازگاری: رسید درخواست
# ----------------------------------------------------------------------------
def request_summary(req: SubRequest) -> str:
    from ui import jalali_date

    status = {"pending": "در انتظار بررسی", "approved": "تأیید شده است", "rejected": "رد شده است"}.get(req.status, req.status)
    lines = [
        f"درخواست شماره {req.id}",
        f"کاربر: <code>{req.user_id}</code>",
        f"تاریخ: {jalali_date(req.created_at)}",
        f"وضعیت: {esc(status)}",
        f"تصویر / فایل: {OK + ' دارد' if req.photo_id else NO + ' ندارد'}",
        "",
        "متن کاربر:",
        f"<pre>{esc(req.text)}</pre>" if req.text else "ندارد",
    ]
    if req.status == "rejected" and req.reason:
        lines += ["", f"دلیل رد: {esc(req.reason)}"]
    return "\n".join(lines)
