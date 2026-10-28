"""
بخش‌های جدید پنل ادمین آبان ترید:
  پشتیبانی | استراتژی‌های آماده | عملکرد سیگنال‌ها | مشاور موتورها | کانال‌های قیمت | قالب‌های قیمت و آنالیز
  Keep-Alive | بکاپ و بازیابی | اشتراک دستی (افزایش / کاهش / حذف / افزایش گروهی)

این ماژول از admin_panel.py ایمپورت می‌شود (نه برعکس)؛ صفحه‌ها در PAGES و ورودی‌های متنی در TEXT_HANDLERS
ثبت می‌شوند و دکمه‌ها از طریق handle_callback پردازش می‌شوند.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Callable, Dict, List, Optional, Tuple

from telegram.error import TelegramError

import analyst
import backup
import broadcaster
import config
import market_overview
import payments
import support_ai
from ai_engine import ai
import engine_advisor as adv
import fmt
import force_subscribe
import performance_tracker as perf
import scoped_pages as sp
import price_broadcaster as pb
import strategy_manager as stm
import subscription
import support
import templates as tpl
from database import db
from ui import (
    NO, OK, WARN, Ctx, back_row, btn, chunk, esc, jalali_dt, jalali_ts, kb, message_text_with_ticks, on_off,
    ts_text,
)

log = logging.getLogger("admin_extra")


Page = Tuple[str, Optional[object]]


def _note(note: str) -> str:
    return f"{note}\n\n" if note else ""


def _to_int(s: str, default: int = 0) -> int:
    return int(s) if s and s.lstrip("-").isdigit() else default


def _set_state(ctx: Ctx, kind: str, back: str, prompt: str, **extra) -> None:
    mid = ctx.q.message.message_id if ctx.q and ctx.q.message else None
    ctx.user_data["state"] = {"kind": kind, "back": back, "prompt": prompt, "mid": mid, **extra}


async def _prompt(ctx: Ctx, kind: str, back: str, prompt: str, **extra) -> None:
    _set_state(ctx, kind, back, prompt, **extra)
    m = await ctx.show(prompt, kb(back_row(back)))
    if m is not None and ctx.user_data.get("state"):
        ctx.user_data["state"]["mid"] = m.message_id


def _admin_panel():
    import admin_panel   # ایمپورت دیرهنگام (جلوگیری از وابستگی حلقوی)

    return admin_panel


# ============================================================================
# پشتیبانی
# ============================================================================
def page_sup_ticket(tid: str = "0", note: str = "", *_a) -> Page:
    """پست کامل مکالمه برای ادمین (بعد از ارسال پاسخ)."""
    t = support.get_ticket(_to_int(tid))
    if t is None:
        return f"{NO} این مکالمه پیدا نشد.", kb(back_row("a:home"))
    return support.admin_post(t, "مکالمه‌ی پشتیبانی", note), support.admin_markup(t["id"])


# ============================================================================
# استراتژی‌های آماده
# ============================================================================
def page_stg(note: str = "", *_a) -> Page:
    items = stm.list_all()
    active = stm.active_key()
    lines = [f"{_note(note)}استراتژی‌های آماده", ""]
    cur = next((s for s in items if s["strategy_key"] == active), None)
    lines.append(f"استراتژی فعال: {esc(cur['strategy_name']) if cur else 'هیچ‌یک (تنظیمات دستی)'}")
    lines += [
        "",
        "هر استراتژی دو بخش جداگانه (LONG و SHORT) دارد. با فعال‌سازی، ربات روی حالت «جداگانه» می‌رود و تنظیمات "
        "موتورها و تایم‌فریم دقیقاً طبق استراتژی اعمال می‌شود.",
    ]
    rows = []
    for s in items:
        mark = f"{OK} " if s["strategy_key"] == active else ""
        tag = "" if int(s.get("is_builtin") or 0) else " (سفارشی)"
        rows.append([btn(f"{mark}{s['strategy_name']}{tag}", f"a:stgi:{s['strategy_key']}")])
    rows.append([btn("ذخیره‌ی تنظیمات فعلی به‌عنوان استراتژی جدید", "a:stgn")])
    rows.append(back_row("a:home"))
    return "\n".join(lines), kb(*rows)


def page_stg_item(key: str = "", note: str = "", *_a) -> Page:
    s = stm.get(key)
    if s is None:
        return page_stg(f"{NO} این استراتژی پیدا نشد.")
    active = stm.active_key() == key
    text = (
        f"{_note(note)}{esc(s['strategy_name'])}\n"
        f"وضعیت: {on_off(active)}\n\n"
        f"{esc(s.get('description') or '')}\n\n"
        f"<pre>{esc(stm.summary(s))}</pre>"
    )
    rows = [[btn("فعال‌سازی مجدد" if active else "فعال‌سازی", f"a:stgac:{key}")]]
    if not int(s.get("is_builtin") or 0):
        rows.append([btn("حذف استراتژی", f"a:stgx:{key}")])
    rows.append(back_row("a:stg"))
    return text, kb(*rows)


def page_stg_confirm(key: str = "", *_a) -> Page:
    s = stm.get(key)
    if s is None:
        return page_stg(f"{NO} این استراتژی پیدا نشد.")
    return (
        f"فعال‌سازی «{esc(s['strategy_name'])}»\n\n"
        "ربات روی حالت «جداگانه» (LONG / SHORT) می‌رود، موتورها و پارامترها و تایم‌فریم‌ها دقیقاً طبق این استراتژی "
        "تنظیم می‌شوند و تنظیمات فعلی موتورها جایگزین می‌شوند (تنظیمات عملیاتی هوش مصنوعی مثل مدل و سقف مصرف دست نمی‌خورند).\n"
        "اگر تنظیمات فعلی ارزش دارد، اول «ذخیره‌ی تنظیمات فعلی به‌عنوان استراتژی جدید» را بزنید.\n\nادامه می‌دهید؟",
        kb([btn(f"{OK} بله، فعال کنید", f"a:stgay:{key}")], back_row(f"a:stgi:{key}")),
    )


def page_stg_delete(key: str = "", *_a) -> Page:
    s = stm.get(key)
    if s is None or int(s.get("is_builtin") or 0):
        return page_stg(f"{NO} این استراتژی قابل حذف نیست.")
    return f"آیا از حذف استراتژی «{esc(s['strategy_name'])}» اطمینان دارید؟", kb(
        [btn(f"{OK} بله، حذف کنید", f"a:stgxd:{key}")], back_row(f"a:stgi:{key}")
    )


# ============================================================================
# عملکرد سیگنال‌ها
# ============================================================================
_perf_symbol = ""        # فیلتر نماد (ادمین یکی است)


def page_perf(status: str = "all", period: str = "a", page: str = "0", note: str = "", *_a) -> Page:
    return sp.perf_page(0, "a", status, period, page, _perf_symbol, note, "a:home")


def page_perf_clear(status: str = "all", period: str = "a", *_a) -> Page:
    return sp.perf_clear_confirm(0, "a", status, period, _perf_symbol)


# ============================================================================
# مشاور موتورها
# ============================================================================
def page_adv(note: str = "", *_a) -> Page:
    return sp.adv_home(0, "a", "a:home", note)


def page_adv_item(sid: str = "0", note: str = "", *_a) -> Page:
    return sp.adv_item(0, "a", sid, note, "a:home")


def page_adv_confirm(sid: str = "0", *_a) -> Page:
    return sp.adv_confirm(0, "a", sid)


def page_adv_history(*_a) -> Page:
    return sp.adv_history(0, "a")


# ============================================================================
# کانال‌های قیمت
# ============================================================================
INTERVAL_SPEC = config.ParamSpec("interval", "بازه", "float", 0.25, 720, "1", "pc")


def _hours_txt(h: float) -> str:
    return f"{config.fmt_number(h)} ساعت"


def page_pc(note: str = "", *_a) -> Page:
    chs = pb.list_channels()
    lines = [f"{_note(note)}مدیریت کانال‌های قیمت ({len(chs)})", ""]
    if not chs:
        lines.append("هنوز کانالی اضافه نشده است.")
    lines += [
        "",
        "هر کانال ارزها، بازه‌ی زمانی و قالب مخصوص خودش را دارد. ربات باید در کانال ادمین (با دسترسی ارسال پیام) باشد.",
    ]
    rows = [[btn(f"{OK if c['enabled'] else NO} {c['channel_title'] or c['channel_id']}", f"a:pci:{c['id']}")] for c in chs]
    rows.append([btn("افزودن کانال", "a:pca")])
    rows.append(back_row("a:home"))
    return "\n".join(lines), kb(*rows)


def page_pc_item(cid: str = "0", note: str = "", *_a) -> Page:
    c = pb.get_channel(_to_int(cid))
    if c is None:
        return page_pc(f"{NO} این کانال پیدا نشد.")
    last = "-" if not c["last_sent_at"] else ts_text(time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(c["last_sent_at"])))
    text = (
        f"{_note(note)}کانال قیمت: {esc(c['channel_title'] or '-')}\n\n"
        f"آیدی: <code>{esc(c['channel_id'])}</code>\n"
        f"وضعیت: {on_off(c['enabled'])}\n"
        f"آیتم‌های انتخاب‌شده: {len(c['selected'])} از {len(pb.ALL_KEYS)}\n"
        f"بازه‌ی ارسال: هر {_hours_txt(c['interval_hours'])}\n"
        f"قالب: {'اختصاصی این کانال' if c['template'].strip() else 'قالب پیش‌فرض قیمت'}\n"
        f"آخرین ارسال: {last}"
    )
    rows = [
        [btn("ویرایش ارزها", f"a:pce:{c['id']}:c")],
        [btn("ویرایش بازه‌ی زمانی", f"a:pcn:{c['id']}"), btn("ویرایش قالب پیام", f"a:pct:{c['id']}")],
    ]
    if c["template"].strip():
        rows.append([btn("بازگشت به قالب پیش‌فرض", f"a:pctd:{c['id']}")])
    rows += [
        [btn("پیش‌نمایش", f"a:pcp:{c['id']}"), btn("ارسال همین اکنون", f"a:pcnow:{c['id']}")],
        [btn(f"فعال / غیرفعال: {OK if c['enabled'] else NO}", f"a:pcw:{c['id']}")],
        [btn("حذف کانال", f"a:pcx:{c['id']}")],
        back_row("a:pc"),
    ]
    return text, kb(*rows)


def page_pc_symbols(cid: str = "0", cat: str = "c", note: str = "", *_a) -> Page:
    c = pb.get_channel(_to_int(cid))
    if c is None:
        return page_pc(f"{NO} این کانال پیدا نشد.")
    cat = cat if cat in pb.GROUP_TITLES else "c"
    sel = c["selected"]
    tabs = [
        btn(f"{OK} {t}" if k == cat else t, f"a:pce:{c['id']}:{k}") for k, t in pb.GROUP_TITLES.items()
    ]
    keys = pb.keys_of(cat)
    boxes = [
        btn(f"{OK if k in sel else NO} {pb.GROUPS[k][1]}", f"a:pcs:{c['id']}:{cat}:{k}") for k in keys
    ]
    n_cat = sum(1 for k in keys if k in sel)
    text = (
        f"{_note(note)}ویرایش ارزها — {esc(c['channel_title'] or c['channel_id'])}\n\n"
        f"{pb.GROUP_TITLES[cat]}: {n_cat} از {len(keys)} انتخاب‌شده\n"
        "روی هر آیتم بزنید تا روشن / خاموش شود. آیتم‌های خاموش (به‌همراه خط و بخش مربوط) در پیام این کانال نمایش داده نمی‌شوند."
    )
    return text, kb(
        tabs,
        *chunk(boxes, 2),
        [btn("انتخاب همه", f"a:pcg:{c['id']}:{cat}:1"), btn("هیچ‌یک", f"a:pcg:{c['id']}:{cat}:0")],
        back_row(f"a:pci:{c['id']}"),
    )


def page_pc_delete(cid: str = "0", *_a) -> Page:
    c = pb.get_channel(_to_int(cid))
    if c is None:
        return page_pc(f"{NO} این کانال پیدا نشد.")
    return f"آیا از حذف کانال قیمت «{esc(c['channel_title'] or c['channel_id'])}» اطمینان دارید؟", kb(
        [btn(f"{OK} بله، حذف کنید", f"a:pcxd:{c['id']}")], back_row(f"a:pci:{c['id']}")
    )


# ============================================================================
# قالب‌های قیمت و آنالیز
# ============================================================================
MT_TITLES = {"price": "قالب قیمت", "analysis": "قالب آنالیز"}


def _price_help() -> str:
    return (
        "Placeholder ها:\n"
        "{date} {time}\n"
        "ارزهای دیجیتال: " + " ".join("{%s}" % k for k, _t, _b in pb.CRYPTO) + "\n"
        "طلا و سکه: " + " ".join("{%s}" % v for k, _t, _a, vs in pb.GOLD for v in vs) + "\n"
        "ارزهای رایج: " + " ".join("{%s}" % k for k, _t, _a in pb.FX) + "\n\n"
        "- اگر مقداری موجود نباشد به‌جای آن --- نمایش داده می‌شود.\n"
        "- آیتم‌هایی که برای کانال انتخاب نشده‌اند، همراه با خط و بخش مربوط حذف می‌شوند.\n"
        "\n" + fmt.HELP
    )


def _analysis_help() -> str:
    return (
        "Placeholder ها:\n" + " ".join("{%s}" % v for v in sorted(analyst.ANALYSIS_PLACEHOLDERS)) + "\n\n"
        "- اگر تحلیل AI موجود نباشد کل بخش {ai_analysis} حذف می‌شود.\n"
        "- اگر مقدار RSI / MACD و ... موجود نباشد --- نمایش داده می‌شود.\n"
        "- ایموجی خط {signal} (🟢 / 🔴 / ⚪) خودکار با جهت سیگنال عوض می‌شود.\n\n" + fmt.HELP
    )


def page_mt(note: str = "", *_a) -> Page:
    return f"{_note(note)}قالب‌های قیمت و آنالیز\n\nیکی را انتخاب کنید:", kb(
        [btn("قالب پیام قیمت", "a:mtv:price")],
        [btn("قالب آنالیز AI", "a:mtv:analysis")],
        back_row("a:home"),
    )


def page_mt_view(kind: str = "price", note: str = "", *_a) -> Page:
    if kind not in MT_TITLES:
        return page_mt()
    cur = db.get_msg_template(kind)
    shown = cur if len(cur) <= 1800 else cur[:1800] + "\n…"
    helptxt = _price_help() if kind == "price" else _analysis_help()
    text = f"{_note(note)}{MT_TITLES[kind]}\n\n<pre>{esc(shown)}</pre>\n\n{esc(helptxt)}"
    if len(text) > 4000:
        text = f"{_note(note)}{MT_TITLES[kind]}\n\n<pre>{esc(cur[:1200])}\n…</pre>\n\n{esc(helptxt[:1500])}"
    return text, kb(
        [btn("ویرایش قالب", f"a:mte:{kind}"), btn("پیش‌نمایش", f"a:mtp:{kind}")],
        [btn("بازگشت به پیش‌فرض", f"a:mtd:{kind}")],
        back_row("a:mt"),
    )


# ============================================================================
# Keep-Alive
# ============================================================================
def _sec(key: str) -> str:
    return f"{db.get_int(key)} ثانیه"


def page_ka(note: str = "", *_a) -> Page:
    on = db.get_bool("keep_alive_on")
    import keep_alive as ka

    _on, lo, hi = ka.read_settings()
    text = (
        f"{_note(note)}تنظیمات Keep-Alive\n\n"
        f"وضعیت: {on_off(on)}\n"
        f"حداقل فاصله: {_sec('keep_alive_min')}\n"
        f"حداکثر فاصله: {_sec('keep_alive_max')}\n"
        f"آدرس: {esc(ka.health_url())}\n\n"
        "ربات به‌صورت دوره‌ای و تصادفی (بین حداقل و حداکثر) به آدرس /health خودش درخواست می‌فرستد تا سرویس همیشه "
        "بیدار بماند. تغییرات بدون ری‌استارت اعمال می‌شود. اگر حداقل از حداکثر بزرگ‌تر باشد خودکار جای آن‌ها عوض می‌شود."
    )
    return text, kb(
        [btn(f"Keep-Alive: {OK if on else NO}", "a:kat")],
        [btn(f"حداقل فاصله: {_sec('keep_alive_min')}", "a:pr:keep_alive_min")],
        [btn(f"حداکثر فاصله: {_sec('keep_alive_max')}", "a:pr:keep_alive_max")],
        back_row("a:set"),
    )


# ============================================================================
# تاریخچه‌ی پرداخت‌ها
# ============================================================================
PH_PER_PAGE = 10


def _n(x) -> str:
    return subscription._n(x)


def _ph_label(p: dict) -> str:
    amount = subscription._money(p["amount"], p.get("network") or "") if (p["amount"] or p["status"] != payments.PENDING) else "—"
    return (
        f"{payments.STATUS_ICON.get(p['status'], '')} {payments.STATUS_FA.get(p['status'], p['status'])} | "
        f"{p['user_id']} | {amount} | {ts_text(p.get('created_at'))[5:]}"
    )


def payment_text(p: dict) -> str:
    """جزئیات کامل یک تراکنش (HTML امن)."""
    filled = bool(p["amount"]) or p["status"] != payments.PENDING or payments.has_info(p)
    note = p["admin_note"]
    lines = [
        f"شماره پیگیری: #{p['id']}",
        f"وضعیت: {payments.STATUS_ICON.get(p['status'], '')} {esc(payments.STATUS_FA.get(p['status'], p['status']))}",
        f"آیدی کاربر: <code>{p['user_id']}</code>",
        f"مبلغ واریزی: {subscription._money(p['amount'], p.get('network') or '') if filled else 'هنوز ثبت نشده'}",
        f"مبلغ اشتراک (مورد انتظار): {subscription._money(p['expected_amount'], p.get('network') or '')}",
        f"روش پرداخت: {esc(payments.METHOD_FA.get(p['method'], p['method']))}",
        f"هش تراکنش: {'<code>' + esc(p['tx_hash']) + '</code>' if p['tx_hash'] else '-'}",
        f"آدرس ولت فرستنده: {'<code>' + esc(p['sender_address']) + '</code>' if p['sender_address'] else '-'}",
        f"آدرس ولت گیرنده: {'<code>' + esc(p['receiver_address']) + '</code>' if p['receiver_address'] else '-'}",
        f"تاریخ و ساعت: {ts_text(p.get('created_at'))}",
    ]
    if note:
        lines.append(f"یادداشت ادمین: {esc(note)}")
    if payments.is_dup(p):
        lines += ["", f"{WARN} این هش قبلاً در سیستم ثبت شده است و امکان ادامه وجود ندارد؛ فقط می‌توانید تراکنش را رد کنید."]
    elif p["status"] == payments.PENDING and p["method"] == "manual":
        c = payments.case_of(p)
        verdict = {
            "missing": "اطلاعات تراکنش (هش، آدرس فرستنده، مبلغ) هنوز ثبت نشده است.",
            payments.EQUAL: f"{OK} مبلغ واریزی دقیقاً برابر مبلغ اشتراک است.",
            payments.GREATER: f"{WARN} مبلغ واریزی بیشتر از مبلغ اشتراک است (باید معلق شود).",
            payments.LESS: f"{NO} مبلغ واریزی کمتر از مبلغ اشتراک است (باید رد شود).",
        }.get(c, "")
        if verdict:
            lines += ["", verdict]
    return "\n".join(lines)


def payment_action_rows(p: dict, r: str) -> list:
    """
    دکمه‌های اقدام. r = q: صفحه‌ی درخواست خرید دستی (روند دقیق: اول ثبت اطلاعات، بعد اقدام مطابق مقایسه‌ی مبلغ)
                  h: صفحه‌ی جزئیات تاریخچه (تأیید دستی / رد برای وضعیت در انتظار و معلق)
    """
    pid, st = p["id"], p["status"]
    rows: list = []
    if st == payments.PENDING and p["method"] == "manual":
        c = payments.case_of(p)
        if r == "q":
            if c == "missing":
                rows.append([btn("ثبت اطلاعات تراکنش (هش، فرستنده، مبلغ)", f"a:phin:{pid}:q")])
            else:
                if c == payments.EQUAL:
                    rows.append([btn(f"{OK} تأیید و فعال‌سازی اشتراک", f"a:phok:{pid}:q")])
                elif c == payments.GREATER:
                    rows.append([btn("معلق کردن", f"a:phsp:{pid}:q")])
                else:                                  # less یا dup
                    rows.append([btn("رد کردن", f"a:phrj:{pid}:q")])
                rows.append([btn("ویرایش اطلاعات تراکنش", f"a:phin:{pid}:q")])
            return rows
        if c == "missing":
            rows.append([btn("ثبت اطلاعات تراکنش (هش، فرستنده، مبلغ)", f"a:phin:{pid}:h")])
        else:
            rows.append([btn("ویرایش اطلاعات تراکنش", f"a:phin:{pid}:h")])
        if p.get("request_id"):
            rows.append([btn("مشاهده درخواست و رسید", f"a:subr:{p['request_id']}")])
    if st in (payments.PENDING, payments.SUSPENDED) and r == "h":
        rows.append([btn("تأیید دستی و فعال‌سازی اشتراک", f"a:phap:{pid}:h")])
        rows.append([btn("رد کردن", f"a:phrj:{pid}:h")])
    return rows


def _ret_page(pid: int, r: str, note: str = "") -> Page:
    p = payments.get(pid)
    if r == "q" and p is not None and p.get("request_id"):
        return _admin_panel().page_sub_request(str(p["request_id"]), note)
    return page_ph_item(str(pid), note)


def page_ph(page: str = "0", note: str = "", *_a) -> Page:
    total = payments.count(service_type=payments.SERVICE_BOT)
    pages = max(1, (total + PH_PER_PAGE - 1) // PH_PER_PAGE)
    pg = min(max(0, _to_int(page)), pages - 1)
    rows_db = payments.list_recent(
        PH_PER_PAGE, pg * PH_PER_PAGE, service_type=payments.SERVICE_BOT
    )
    head = (
        f"{_note(note)}تاریخچه پرداخت‌ها\n"
        f"تعداد کل تراکنش‌ها: {total} | صفحه {pg + 1} از {pages}\n\n"
        f"{payments.STATUS_ICON['approved']} تأیید شده | {payments.STATUS_ICON['pending']} در انتظار | "
        f"{payments.STATUS_ICON['suspended']} معلق | {payments.STATUS_ICON['rejected']} رد شده\n"
        "برای دیدن جزئیات هر تراکنش روی آن بزنید."
    )
    if not rows_db:
        head += "\n\nهنوز تراکنشی ثبت نشده است."
    rows = [[btn(_ph_label(p), f"a:phi:{p['id']}")] for p in rows_db]
    nav = []
    if pg > 0:
        nav.append(btn("قبلی", f"a:ph:{pg - 1}"))
    if pg < pages - 1:
        nav.append(btn("بعدی", f"a:ph:{pg + 1}"))
    if nav:
        rows.append(nav)
    rows.append([btn("جستجو (آیدی کاربر، آدرس فرستنده یا هش)", "a:phs")])
    rows.append(back_row("a:home"))
    return head, kb(*rows)


def page_ph_results(items: List[dict], title: str) -> Page:
    if not items:
        return f"{NO} {esc(title)}: تراکنشی پیدا نشد.", kb([btn("جستجوی دوباره", "a:phs")], back_row("a:ph:0"))
    rows = [[btn(_ph_label(p), f"a:phi:{p['id']}")] for p in items]
    rows.append([btn("جستجوی دوباره", "a:phs")])
    rows.append(back_row("a:ph:0"))
    return f"{esc(title)} ({len(items)} تراکنش):", kb(*rows)


def page_ph_item(pid: str = "0", note: str = "", *_a) -> Page:
    p = payments.get(_to_int(pid))
    if p is None:
        return page_ph("0", f"{NO} این تراکنش پیدا نشد.")
    if p.get("service_type") == payments.SERVICE_WEBAPP:
        return page_ph("0", f"{NO} این تراکنش مربوط به اشتراک تحلیل ساختاری است؛ آن را از پنل مدیریت وب‌اپ بررسی کنید.")
    rows = payment_action_rows(p, "h")
    rows.append(back_row("a:ph:0"))
    return f"{_note(note)}جزئیات تراکنش\n\n{payment_text(p)}", kb(*rows)


def page_ph_reject(pid: str = "0", r: str = "h", *_a) -> Page:
    p = payments.get(_to_int(pid))
    if p is not None and p.get("service_type") == payments.SERVICE_WEBAPP:
        return page_ph("0", f"{NO} این تراکنش مربوط به اشتراک تحلیل ساختاری است؛ آن را از پنل مدیریت وب‌اپ بررسی کنید.")
    if p is None or p["status"] not in (payments.PENDING, payments.SUSPENDED):
        return page_ph("0", f"{NO} این تراکنش قابل رد کردن نیست.")
    back = f"a:subr:{p['request_id']}" if r == "q" and p.get("request_id") else f"a:phi:{p['id']}"
    return (
        f"آیا از رد تراکنش #{p['id']} (کاربر <code>{p['user_id']}</code>) اطمینان دارید؟\n"
        "اشتراک فعال نمی‌شود و به کاربر اطلاع داده می‌شود.",
        kb([btn(f"{OK} بله، رد شود", f"a:phrjy:{p['id']}:{r}")], back_row(back)),
    )


# ============================================================================
# وضعیت بازار در منوی اصلی
# ============================================================================
def page_mkt(note: str = "", *_a) -> Page:
    on = db.get_bool("mkt_on")
    at = db.get_float("mkt_at")
    state = db.get("mkt_state")
    lines = [
        f"{_note(note)}وضعیت بازار در منوی اصلی",
        "",
        f"نمایش در منوی کاربران: {on_off(on)}",
        f"آخرین محاسبه: {jalali_dt(at) if at else 'هنوز محاسبه نشده'}",
    ]
    if state in market_overview.STATE_FA:
        lines += [
            f"وضعیت کلی: {market_overview.STATE_FA[state]}",
            f"میانگین RSI: {db.get_float('mkt_rsi'):.1f} | میانگین ADX: {db.get_float('mkt_adx'):.1f}",
            f"نمادهای صعودی: {db.get_float('mkt_bull'):.0f}٪ | نزولی: {db.get_float('mkt_bear'):.0f}٪ "
            f"(از {db.get_int('mkt_count')} نماد)",
        ]
    lines += [
        f"تایم‌فریم محاسبه: {esc(market_overview.market_timeframe())}",
        "",
        "هر ۱۵ دقیقه از روی کندل‌های کش‌شده‌ی نمادهای فعال محاسبه می‌شود (بدون درخواست اضافه به صرافی). "
        "صعودی: میانگین RSI بالای ۵۵ و بیش از ۶۰٪ نمادها صعودی | نزولی: میانگین RSI زیر ۴۵ و بیش از ۶۰٪ نمادها نزولی | بقیه خنثی.",
        "اگر آخرین محاسبه قدیمی‌تر از ۳ ساعت باشد، خط بازار در منو نمایش داده نمی‌شود.",
        "",
        "قالب فعلی:",
        f"<pre>{esc(db.get('mkt_template') or config.DEFAULT_MARKET_TEMPLATE)}</pre>",
        "Placeholder ها: {state} {rsi} {adx} {bull} {bear}",
    ]
    prev = market_overview.render(force=True)
    if prev:
        lines += ["", "پیش‌نمایش:", prev]
    return "\n".join(lines), kb(
        [btn(f"نمایش در منو: {OK if on else NO}", "a:mktt")],
        [btn("ویرایش قالب متن", "a:mkte"), btn("بازگشت به قالب پیش‌فرض", "a:mktd")],
        [btn("محاسبه‌ی دوباره همین حالا", "a:mktr")],
        back_row("a:set"),
    )


# ============================================================================
# پاسخ خودکار هوشمند پشتیبانی
# ============================================================================
def page_aisup(note: str = "", *_a) -> Page:
    on = db.get_bool("ai_sup_on")
    master = ai.master_on()
    active = on and master
    lines = [
        f"{_note(note)}پاسخ خودکار هوشمند پشتیبانی",
        "",
        f"وضعیت قابلیت: {on_off(on)}",
        f"سوییچ اصلی هوش مصنوعی: {on_off(master)}",
        f"کلیدهای سالم: {len(ai.available_keys())} از {len(db.ai_keys())}",
        f"سقف روزانه‌ی پاسخ خودکار هر کاربر: {config.AI_SUPPORT_DAILY_PER_USER}",
        "",
        "پیام‌های متنی کاربر در بخش پشتیبانی اول به دستیار هوشمند داده می‌شود: مفاهیم کریپتو، اصطلاحات معاملاتی، اندیکاتورها و "
        "عملکرد بخش کاربری ربات را مستقیم پاسخ می‌دهد و پیام برای شما ارسال نمی‌شود. مسائل مالی و اشتراک، خطاهای فنی، "
        "مشاوره‌ی سرمایه‌گذاری، موضوعات نامرتبط، سوال‌هایی که مطمئن نیست و عکس‌ها با برچسب «نیاز به بررسی ادمین» برای شما ارسال می‌شوند. "
        "اگر هوش مصنوعی پاسخ ندهد هم پیام خودکار به شما ارجاع می‌شود. همه‌ی پاسخ‌ها در مکالمه ذخیره می‌شوند (فرستنده: هوش مصنوعی).",
    ]
    if on and not master:
        lines += ["", f"{WARN} سوییچ اصلی هوش مصنوعی خاموش است؛ تا روشن نشود همه‌ی پیام‌ها مستقیم برای شما ارسال می‌شود."]
    elif active:
        lines += ["", f"{OK} فعال است."]
    return "\n".join(lines), kb(
        [btn(f"پاسخ خودکار پشتیبانی: {OK if on else NO}", "a:aisupt")],
        [btn("مدیریت کلیدهای هوش مصنوعی", "a:aik")],
        back_row("a:set"),
    )


# ============================================================================
# ثبت صفحه‌ها
# ============================================================================
PAGES: Dict[str, Callable[..., Page]] = {
    "stg": page_stg,
    "stgi": page_stg_item,
    "stgac": page_stg_confirm,
    "stgx": page_stg_delete,
    "perf": page_perf,
    "perfx": page_perf_clear,
    "adv": page_adv,
    "advs": page_adv_item,
    "advac": page_adv_confirm,
    "advh": page_adv_history,
    "pc": page_pc,
    "pci": page_pc_item,
    "pce": page_pc_symbols,
    "pcx": page_pc_delete,
    "mt": page_mt,
    "mtv": page_mt_view,
    "ka": page_ka,
    "ph": page_ph,
    "phi": page_ph_item,
    "phrj": page_ph_reject,
    "mkt": page_mkt,
    "aisup": page_aisup,
}


# ============================================================================
# ورودی‌های متنی ادمین
# ============================================================================
Result = Tuple[bool, object]


async def _t_sup_reply(ctx: Ctx, st: dict, raw: str) -> Result:
    text = message_text_with_ticks(ctx.update.effective_message).strip()
    if not text:
        return False, "متن خالی است."
    if len(text) > 3500:
        return False, "متن بسیار طولانی است (حداکثر ۳۵۰۰ کاراکتر)."
    t = support.get_ticket(st["tid"])
    if t is None:
        return False, "این مکالمه پیدا نشد."
    markup = kb([btn("پشتیبانی", "u:sup")], [btn("منوی اصلی", "u:mn")])
    mid = await broadcaster.send_html(ctx.bot, t["user_id"], "پاسخ پشتیبانی:\n\n" + fmt.to_html(text), markup)
    note = f"{OK} پاسخ برای کاربر ارسال شد." if mid else f"{NO} پیام ارسال نشد (احتمالاً کاربر ربات را مسدود کرده است)."
    support.add_admin_message(t["id"], text)
    return True, page_sup_ticket(str(t["id"]), note)


async def _t_stg_save(ctx: Ctx, st: dict, raw: str) -> Result:
    ok, res = stm.save_current(raw)
    if not ok:
        return False, res
    return True, page_stg(f"{OK} تنظیمات فعلی به‌عنوان استراتژی جدید ذخیره شد.")


async def _t_perf_sym(ctx: Ctx, st: dict, raw: str) -> Result:
    global _perf_symbol
    q = raw.strip().upper().replace("USDT", "").strip("/: ")
    if not q or len(q) > 20 or not q.replace("_", "").isalnum():
        return False, "اسم نماد معتبر ارسال کنید؛ مثلاً BTC"
    _perf_symbol = q
    return True, page_perf("all", "a", "0")


async def _t_pc_add(ctx: Ctx, st: dict, raw: str) -> Result:
    ap = _admin_panel()
    ref = ap.parse_channel_ref(raw)
    if ref is None:
        return False, "آیدی یا یوزرنیم معتبر نیست. مثال: @mychannel یا -1001234567890"
    info, err = await ap.resolve_channel(ctx.bot, ref)
    if err:
        return False, err
    chat_id, title, _link, warn, can_post, _un = info
    if not can_post:
        return False, "ربات باید در این کانال ادمین (با دسترسی ارسال پیام) باشد. اول ربات را ادمین کنید و دوباره ارسال کنید."
    if pb.channel_exists(str(chat_id)):
        return False, "این کانال قبلاً به‌عنوان کانال قیمت اضافه شده است."
    cid = pb.add_channel(str(chat_id), title)
    return True, page_pc_item(str(cid), f"{OK} کانال اضافه شد (همه‌ی آیتم‌ها انتخاب شدند؛ از «ویرایش ارزها» می‌توانید تغییر دهید).")


async def _t_pc_interval(ctx: Ctx, st: dict, raw: str) -> Result:
    val = config.parse_number(INTERVAL_SPEC, raw)
    if val is None:
        return False, "فقط عدد معتبر ارسال کنید (ساعت؛ مثلاً 1 یا 0.5)."
    if not INTERVAL_SPEC.min <= val <= INTERVAL_SPEC.max:
        return False, "عدد باید بین 0.25 تا 720 ساعت باشد."
    pb.update_channel(st["cid"], interval_hours=float(val))
    return True, page_pc_item(str(st["cid"]), f"{OK} بازه‌ی ارسال ذخیره شد.")


async def _t_pc_tpl(ctx: Ctx, st: dict, raw: str) -> Result:
    text = message_text_with_ticks(ctx.update.effective_message).strip()
    ok, msg = tpl.validate_msg_template(text, pb.PRICE_PLACEHOLDERS)
    if not ok:
        return False, msg
    pb.update_channel(st["cid"], template=text)
    return True, page_pc_item(str(st["cid"]), f"{OK} قالب اختصاصی کانال ذخیره شد.")


async def _t_mt_edit(ctx: Ctx, st: dict, raw: str) -> Result:
    kind = st["mt"]
    text = message_text_with_ticks(ctx.update.effective_message).strip()
    allowed = pb.PRICE_PLACEHOLDERS if kind == "price" else analyst.ANALYSIS_PLACEHOLDERS
    ok, msg = tpl.validate_msg_template(text, allowed)
    if not ok:
        return False, msg
    db.set_msg_template(kind, text)
    return True, page_mt_view(kind, f"{OK} {MT_TITLES[kind]} ذخیره شد.")


async def _t_ph_search(ctx: Ctx, st: dict, raw: str) -> Result:
    q = raw.strip()
    if not q or len(q) > 140:
        return False, "عبارت جستجو معتبر نیست (آیدی عددی، آدرس فرستنده یا هش تراکنش)."
    return True, page_ph_results(
        payments.search(q, 10, service_type=payments.SERVICE_BOT), f"نتیجه‌ی جستجو برای «{q[:60]}»"
    )


async def _t_ph_info(ctx: Ctx, st: dict, raw: str) -> Result:
    pid, r = st["pid"], st.get("r", "h")
    lines = [ln.strip() for ln in raw.replace("\r", "").split("\n") if ln.strip()]
    if len(lines) != 3:
        return False, "دقیقاً سه خط ارسال کنید: خط اول هش تراکنش، خط دوم آدرس ولت فرستنده، خط سوم مبلغ واریزی."
    tx_hash, sender, amount_raw = lines
    if not re.fullmatch(r"[A-Za-z0-9]{10,128}", tx_hash):
        return False, "خط اول (هش تراکنش) معتبر نیست؛ فقط حروف انگلیسی و عدد (بین ۱۰ تا ۱۲۸ کاراکتر)، بدون فاصله."
    if not re.fullmatch(r"\S{10,120}", sender):
        return False, "خط دوم (آدرس ولت فرستنده) معتبر نیست؛ بین ۱۰ تا ۱۲۰ کاراکتر و بدون فاصله."
    amount = payments.parse_amount(amount_raw)
    if amount is None:
        return False, "خط سوم (مبلغ واریزی) باید یک عدد مثبت باشد؛ مثلاً 10 یا 12.5"
    p = payments.get(pid)
    if p is None or p["status"] != payments.PENDING:
        return False, "این تراکنش دیگر در وضعیت «در انتظار» نیست."
    if p.get("service_type") == payments.SERVICE_WEBAPP:
        return False, "این تراکنش مربوط به اشتراک تحلیل ساختاری است؛ از پنل مدیریت وب‌اپ بررسی کنید."
    try:
        p = payments.set_info(pid, tx_hash, sender, amount)
    except payments.DuplicateHash as e:
        ex = e.existing or {}
        ref = f" (تراکنش #{ex['id']})" if ex.get("id") else ""
        return True, _ret_page(
            pid, r,
            f"{WARN} این هش قبلاً در سیستم ثبت شده است{ref}؛ امکان ادامه وجود ندارد و باید تراکنش را رد کنید.",
        )
    except ValueError:
        return False, "این تراکنش دیگر در وضعیت «در انتظار» نیست."
    msg = {
        payments.EQUAL: "مبلغ واریزی دقیقاً برابر مبلغ اشتراک است.",
        payments.GREATER: "مبلغ واریزی بیشتر از مبلغ اشتراک است.",
        payments.LESS: "مبلغ واریزی کمتر از مبلغ اشتراک است.",
    }[payments.compare(p["amount"], p["expected_amount"])]
    return True, _ret_page(pid, r, f"{OK} اطلاعات ذخیره شد. {msg}")


async def _t_ph_note(ctx: Ctx, st: dict, raw: str) -> Result:
    pid, r = st["pid"], st.get("r", "h")
    p = payments.get(pid)
    if p is not None and p.get("service_type") == payments.SERVICE_WEBAPP:
        return False, "این تراکنش مربوط به اشتراک تحلیل ساختاری است؛ از پنل مدیریت وب‌اپ بررسی کنید."
    note = raw.strip()
    if note == "-":
        note = ""
    if len(note) > 300:
        return False, "یادداشت بسیار طولانی است (حداکثر ۳۰۰ کاراکتر)."
    ok, msg = await subscription.approve_payment(ctx.bot, pid, note or "تأیید دستی توسط ادمین")
    return True, _ret_page(pid, r, f"{OK if ok else NO} {esc(msg)}")


async def _t_mkt_tpl(ctx: Ctx, st: dict, raw: str) -> Result:
    text = message_text_with_ticks(ctx.update.effective_message).strip()
    if len(text) > 500:
        return False, "قالب بسیار طولانی است (حداکثر ۵۰۰ کاراکتر)."
    ok, msg = tpl.validate_msg_template(text, config.MARKET_PLACEHOLDERS)
    if not ok:
        return False, msg
    db.set("mkt_template", text)
    return True, page_mkt(f"{OK} قالب ذخیره شد.")


async def _t_analyze(ctx: Ctx, st: dict, raw: str) -> Result:  # pragma: no cover - مسیر ادمین مستقیم در admin_panel
    return False, ""


async def _t_db_restore_text(ctx: Ctx, st: dict, raw: str) -> Result:
    return False, "فایل JSON بکاپ را به‌صورت Document ارسال کنید."


TEXT_HANDLERS: Dict[str, Callable] = {
    "sup_reply": _t_sup_reply,
    "stg_save": _t_stg_save,
    "perf_sym": _t_perf_sym,
    "pc_add": _t_pc_add,
    "pc_interval": _t_pc_interval,
    "pc_tpl": _t_pc_tpl,
    "mt_edit": _t_mt_edit,
    "db_restore": _t_db_restore_text,
    "ph_search": _t_ph_search,
    "ph_info": _t_ph_info,
    "ph_note": _t_ph_note,
    "mkt_tpl": _t_mkt_tpl,
}


def handle_restore_file(ctx: Ctx, raw: bytes) -> Result:
    """فایل بکاپ دریافت شد: کامل بررسی می‌شود و صفحه‌ی تأیید نشان داده می‌شود (هنوز چیزی اعمال نمی‌شود)."""
    ok, res = backup.validate(raw)
    if not ok:
        return False, res
    ctx.user_data["restore_tables"] = res
    text = (
        "فایل بکاپ معتبر است.\n\n<pre>" + esc(backup.summary(res)) + "</pre>\n\n"
        "با تأیید، داده‌ی جدول‌های بالا کاملاً با محتوای این فایل جایگزین می‌شود (داده‌ی فعلی همین جدول‌ها پاک می‌شود).\n"
        "پیشنهاد: اول یک بکاپ از وضعیت فعلی دریافت کنید.\n\nادامه می‌دهید؟"
    )
    return True, (text, kb([btn(f"{OK} بله، بازیابی کنید", "a:rstok")], back_row("a:hm")))


# ============================================================================
# دکمه‌ها (کال‌بک)
# ============================================================================
async def handle_callback(ctx: Ctx, act: str, args: List[str]) -> bool:
    """True یعنی این دکمه توسط این ماژول پردازش شد."""
    global _perf_symbol

    # ---------- پشتیبانی ----------
    if act == "supr":
        tid = _to_int(args[0] if args else "")
        t = support.get_ticket(tid)
        await ctx.answer()
        if t is None:
            await ctx.show(f"{NO} این مکالمه پیدا نشد.", kb(back_row("a:home")))
            return True
        # پیام مکالمه ویرایش نمی‌شود تا متن کامل مکالمه روی صفحه بماند؛ راهنمای پاسخ در پیام جدید می‌آید
        prompt = f"متن پاسخ برای {esc(support.user_label(t))} را ارسال کنید:\n\n{esc(fmt.HELP)}"
        sent = await ctx.send(prompt, kb(back_row("a:home")))
        ctx.user_data["state"] = {
            "kind": "sup_reply", "back": "a:home", "prompt": prompt,
            "mid": sent.message_id if sent else None, "tid": tid,
        }
        return True
    if act == "supp":
        tid = _to_int(args[0] if args else "")
        photos = [m for m in support.messages(tid) if m.get("message_type") == "photo" and m.get("file_id")]
        if not photos:
            await ctx.answer(f"{NO} تصویری برای این مکالمه نیست.", alert=True)
            return True
        await ctx.answer()
        shown = photos[-10:]
        for i, m in enumerate(shown):
            last = i == len(shown) - 1
            cap = f"{ts_text(m.get('created_at'))}" + (f"\n{(m.get('text') or '')[:900]}" if m.get("text") else "")
            try:
                await ctx.bot.send_photo(
                    chat_id=ctx.uid, photo=m["file_id"], caption=cap,
                    reply_markup=support.admin_markup(tid) if last else None,
                )
            except TelegramError as e:
                log.warning("send support photo failed: %s", e)
        return True

    # ---------- استراتژی‌ها ----------
    if act == "stgay":
        key = args[0] if args else ""
        await ctx.answer()
        ok, msg = stm.activate(key)
        await ctx.show(*page_stg_item(key, f"{OK if ok else NO} {esc(msg)}"))
        return True
    if act == "stgxd":
        key = args[0] if args else ""
        await ctx.answer()
        ok, msg = stm.delete(key)
        await ctx.show(*page_stg(f"{OK if ok else NO} {esc(msg)}"))
        return True
    if act == "stgn":
        await ctx.answer()
        await _prompt(
            ctx, "stg_save", "a:stg",
            "یک اسم برای استراتژی جدید ارسال کنید (۲ تا ۴۰ کاراکتر).\n"
            "تنظیمات فعلی هر دو جهت LONG و SHORT (موتورها، پارامترها و تایم‌فریم) با همین اسم ذخیره می‌شود.",
        )
        return True

    # ---------- عملکرد سیگنال‌ها ----------
    if act == "perfs":
        await ctx.answer()
        await _prompt(ctx, "perf_sym", "a:perf:all:a:0", "اسم نماد را ارسال کنید؛ مثلاً BTC")
        return True
    if act == "perfc":
        _perf_symbol = ""
        await ctx.answer()
        await ctx.show(*page_perf("all", "a", "0"))
        return True
    if act == "perfxd":
        status = args[0] if args else "all"
        period = args[1] if len(args) > 1 else "a"
        await ctx.answer()
        n = perf.delete_filtered(status, period, _perf_symbol, uid=0)
        await ctx.show(*page_perf(status, period, "0", f"{OK} {n} رکورد پاک شد."))
        return True
    if act == "perfe":
        status = args[0] if args else "all"
        period = args[1] if len(args) > 1 else "a"
        back = f"a:perf:{status}:{period}:0"
        try:
            rows = perf.all_signals(status, period, _perf_symbol, uid=0)
            data = sp.export_text(rows, status, period, _perf_symbol).encode("utf-8")
        except Exception:
            log.exception("perf export build failed")
            await ctx.answer(f"{NO} ساخت فایل انجام نشد، دوباره امتحان کنید.", alert=True)
            return True
        if rows:
            await ctx.answer()
        else:
            await ctx.answer(f"{WARN} رکوردی با این فیلتر وجود ندارد.", alert=True)
        try:
            await ctx.bot.send_document(
                chat_id=ctx.uid, document=data, filename=sp.export_filename(),
                caption=f"خروجی عملکرد سیگنال‌ها | {len(rows)} رکورد", reply_markup=kb(back_row(back)),
            )
        except Exception:
            log.exception("perf export send failed")
            await ctx.show(f"{NO} ارسال فایل انجام نشد، دوباره امتحان کنید.", kb(back_row(back)))
        return True

    # ---------- مشاور موتورها ----------
    if act == "advq":
        await ctx.answer()
        ok, why = adv.ai_ready()
        if not ok:
            await ctx.show(*page_adv(f"{NO} {esc(why)}"))
            return True
        await ctx.show("در حال جمع‌آوری داده‌ها و دریافت پیشنهاد از هوش مصنوعی... (ممکن است تا یک دقیقه طول بکشد)", kb(back_row("a:adv")))
        sid, err = await adv.request_suggestion()
        if sid is None:
            await ctx.show(*page_adv(f"{NO} {esc(err)}"))
        else:
            await ctx.show(*page_adv_item(str(sid)))
        return True
    if act == "advy":
        sid = _to_int(args[0] if args else "")
        await ctx.answer()
        ok, msg = adv.apply_suggestions(sid)
        await ctx.show(*page_adv_item(str(sid), f"{OK if ok else NO} {esc(msg)}"))
        return True
    if act == "advr":
        sid = _to_int(args[0] if args else "")
        await ctx.answer()
        ok = adv.reject(sid)
        await ctx.show(*page_adv_item(str(sid), f"{OK} پیشنهاد رد شد." if ok else f"{NO} این پیشنهاد در انتظار نیست."))
        return True

    # ---------- کانال‌های قیمت ----------
    if act == "pca":
        await ctx.answer()
        await _prompt(
            ctx, "pc_add", "a:pc",
            "آیدی عددی یا یوزرنیم کانالی که قیمت‌ها در آن ارسال می‌شود را ارسال کنید.\n"
            "مثال: @mychannel یا -1001234567890\n\nربات باید در کانال ادمین (با دسترسی ارسال پیام) باشد.",
        )
        return True
    if act in ("pcn", "pct"):
        cid = _to_int(args[0] if args else "")
        c = pb.get_channel(cid)
        await ctx.answer()
        if c is None:
            await ctx.show(*page_pc(f"{NO} این کانال پیدا نشد."))
            return True
        if act == "pcn":
            await _prompt(
                ctx, "pc_interval", f"a:pci:{cid}",
                f"بازه‌ی ارسال قیمت را به ساعت ارسال کنید (بین 0.25 تا 720؛ مثلاً 1 یا 0.5).\nمقدار فعلی: {_hours_txt(c['interval_hours'])}",
                cid=cid,
            )
        else:
            await _prompt(
                ctx, "pc_tpl", f"a:pci:{cid}",
                "قالب اختصاصی این کانال را در یک پیام ارسال کنید.\n\n" + esc(_price_help()), cid=cid,
            )
        return True
    if act == "pctd":
        cid = _to_int(args[0] if args else "")
        pb.update_channel(cid, template="")
        await ctx.answer()
        await ctx.show(*page_pc_item(str(cid), f"{OK} کانال به قالب پیش‌فرض برگشت."))
        return True
    if act == "pcw":
        cid = _to_int(args[0] if args else "")
        c = pb.get_channel(cid)
        await ctx.answer()
        if c is not None:
            pb.update_channel(cid, enabled=0 if c["enabled"] else 1)
        await ctx.show(*page_pc_item(str(cid)))
        return True
    if act in ("pcs", "pcg"):
        cid = _to_int(args[0] if args else "")
        cat = args[1] if len(args) > 1 else "c"
        c = pb.get_channel(cid)
        await ctx.answer()
        if c is None:
            await ctx.show(*page_pc(f"{NO} این کانال پیدا نشد."))
            return True
        sel = set(c["selected"])
        keys = pb.keys_of(cat)
        if act == "pcs":
            key = args[2] if len(args) > 2 else ""
            if key in keys:
                sel ^= {key}
        else:
            if (args[2] if len(args) > 2 else "1") == "1":
                sel |= set(keys)
            else:
                sel -= set(keys)
        pb.update_channel(cid, symbols=sel)
        await ctx.show(*page_pc_symbols(str(cid), cat))
        return True
    if act in ("pcp", "pcnow"):
        cid = _to_int(args[0] if args else "")
        c = pb.get_channel(cid)
        await ctx.answer()
        if c is None:
            await ctx.show(*page_pc(f"{NO} این کانال پیدا نشد."))
            return True
        if act == "pcp":
            await ctx.show("در حال ساخت پیش‌نمایش...", kb(back_row(f"a:pci:{cid}")))
            try:
                text = await pb.build_message(pb.template_for(c), c["selected"])
            except Exception:
                log.exception("price preview failed")
                await ctx.show(*page_pc_item(str(cid), f"{NO} پیش‌نمایش ساخته نشد."))
                return True
            await ctx.show(f"پیش‌نمایش (با قیمت‌های لحظه‌ای):\n\n{text}", kb(back_row(f"a:pci:{cid}")))
            return True
        await ctx.show("در حال ارسال...", kb(back_row(f"a:pci:{cid}")))
        ok = await pb.send_channel(ctx.bot, c)
        await ctx.show(*page_pc_item(str(cid), f"{OK} قیمت‌ها برای کانال ارسال شد." if ok else f"{NO} ارسال انجام نشد؛ دسترسی ربات در کانال را بررسی کنید."))
        return True
    if act == "pcxd":
        cid = _to_int(args[0] if args else "")
        pb.delete_channel(cid)
        await ctx.answer()
        await ctx.show(*page_pc(f"{OK} کانال قیمت حذف شد."))
        return True

    # ---------- قالب‌های قیمت / آنالیز ----------
    if act == "mte":
        kind = args[0] if args else ""
        await ctx.answer()
        if kind not in MT_TITLES:
            await ctx.show(*page_mt())
            return True
        helptxt = _price_help() if kind == "price" else _analysis_help()
        await _prompt(
            ctx, "mt_edit", f"a:mtv:{kind}",
            f"{MT_TITLES[kind]} را در یک پیام ارسال کنید.\n\n{esc(helptxt)}", mt=kind,
        )
        return True
    if act == "mtd":
        kind = args[0] if args else ""
        await ctx.answer()
        if kind in MT_TITLES:
            db.reset_msg_template(kind)
        await ctx.show(*page_mt_view(kind, f"{OK} قالب به حالت پیش‌فرض برگشت."))
        return True
    if act == "mtp":
        kind = args[0] if args else ""
        await ctx.answer()
        if kind == "price":
            await ctx.show("در حال ساخت پیش‌نمایش...", kb(back_row("a:mtv:price")))
            try:
                text = await pb.build_message(db.get_msg_template("price"), set(pb.ALL_KEYS))
            except Exception:
                log.exception("price template preview failed")
                await ctx.show(*page_mt_view("price", f"{NO} پیش‌نمایش ساخته نشد."))
                return True
        elif kind == "analysis":
            text = _analysis_sample()
        else:
            await ctx.show(*page_mt())
            return True
        await ctx.show(f"پیش‌نمایش:\n\n{text}", kb(back_row(f"a:mtv:{kind}")))
        return True

    # ---------- Keep-Alive ----------
    if act == "kat":
        new = db.toggle("keep_alive_on")
        await ctx.answer()
        await ctx.show(*page_ka(f"{OK} Keep-Alive {'فعال' if new else 'غیرفعال'} شد."))
        return True

    # ---------- بکاپ / بازیابی ----------
    if act == "bkp":
        await ctx.answer()
        try:
            data = await asyncio.to_thread(backup.export_bytes)
            await ctx.bot.send_document(
                chat_id=ctx.uid,
                document=data,
                filename=backup.backup_filename(),
                caption="بکاپ کامل دیتابیس (تنظیمات، قالب‌ها، کاربران، کلیدهای AI و ...). برای بازگردانی از دکمه‌ی Restore استفاده کنید.",
                reply_markup=kb(back_row("a:hm")),
            )
        except Exception:
            log.exception("backup failed")
            await ctx.show(f"{NO} ساخت یا ارسال بکاپ انجام نشد.", kb(back_row("a:hm")))
        return True
    if act == "rst":
        await ctx.answer()
        await _prompt(
            ctx, "db_restore", "a:hm",
            "فایل JSON بکاپ (همانی که با دکمه‌ی Backup دریافت کرده‌اید) را به‌صورت Document ارسال کنید.\n\n"
            "- اول کل فایل بررسی می‌شود و بعد از تأیید شما اعمال می‌شود.\n"
            "- اگر فایل نامعتبر باشد هیچ تغییری اعمال نمی‌شود.",
        )
        return True
    if act == "rstok":
        tables = ctx.user_data.pop("restore_tables", None)
        await ctx.answer()
        if not tables:
            await ctx.show(f"{NO} فایلی برای بازیابی در انتظار نیست؛ دوباره Restore را بزنید.", kb(back_row("a:hm")))
            return True
        await ctx.show("در حال بازیابی دیتابیس...", None)
        ok, msg = await asyncio.to_thread(backup.restore, tables)
        if ok:
            force_subscribe.clear_cache()
        await ctx.show(f"{OK if ok else NO} {esc(msg)}", kb(back_row("a:hm")))
        return True

    # ---------- تاریخچه‌ی پرداخت‌ها ----------
    if act == "phs":
        await ctx.answer()
        await _prompt(
            ctx, "ph_search", "a:ph:0",
            "آیدی عددی کاربر، آدرس ولت فرستنده یا هش تراکنش (کامل یا بخشی از آن) را ارسال کنید:",
        )
        return True
    if act in ("phin", "phap", "phok", "phsp", "phrjy"):
        pid = _to_int(args[0] if args else "")
        r = "q" if len(args) > 1 and args[1] == "q" else "h"
        p = payments.get(pid)
        await ctx.answer()
        if p is None:
            await ctx.show(*page_ph("0", f"{NO} این تراکنش پیدا نشد."))
            return True
        if p.get("service_type") == payments.SERVICE_WEBAPP:
            await ctx.show(*page_ph("0", f"{NO} این تراکنش مربوط به اشتراک تحلیل ساختاری است؛ آن را از پنل مدیریت وب‌اپ بررسی کنید."))
            return True
        if act == "phin":
            if p["status"] != payments.PENDING:
                await ctx.show(*_ret_page(pid, r, f"{NO} این تراکنش دیگر در وضعیت «در انتظار» نیست."))
                return True
            back = f"a:subr:{p['request_id']}" if r == "q" and p.get("request_id") else f"a:phi:{pid}"
            await _prompt(
                ctx, "ph_info", back,
                f"ثبت اطلاعات تراکنش #{pid} (کاربر <code>{p['user_id']}</code>)\n\n"
                "این سه مورد را در «یک پیام» و هر کدام در یک خط ارسال کنید:\n"
                "خط اول: هش تراکنش (TxID)\n"
                "خط دوم: آدرس ولت فرستنده\n"
                "خط سوم: مبلغ واریزی (به دلار)\n\n"
                f"مبلغ اشتراک مورد انتظار: {_n(p['expected_amount'])} دلار",
                pid=pid, r=r,
            )
            return True
        if act == "phap":
            if p["status"] not in (payments.PENDING, payments.SUSPENDED):
                await ctx.show(*_ret_page(pid, r, f"{NO} این تراکنش قبلاً بررسی شده است."))
                return True
            back = f"a:phi:{pid}"
            await _prompt(
                ctx, "ph_note", back,
                f"تأیید دستی تراکنش #{pid} (کاربر <code>{p['user_id']}</code>)\n\n"
                "با ارسال این پیام اشتراک کاربر فعال می‌شود (حتی اگر مبلغ واریزی با مبلغ اشتراک فرق داشته باشد).\n"
                "یادداشت خود را ارسال کنید (حداکثر ۳۰۰ کاراکتر) یا برای تأیید بدون یادداشت یک خط تیره (-) بفرستید:",
                pid=pid, r=r,
            )
            return True
        if act == "phok":
            if p["status"] != payments.PENDING or payments.case_of(p) != payments.EQUAL:
                await ctx.show(*_ret_page(pid, r, f"{NO} تأیید فقط برای تراکنش در انتظارِ دارای مبلغ برابر ممکن است."))
                return True
            ok, msg = await subscription.approve_payment(ctx.bot, pid)
        elif act == "phsp":
            if p["status"] != payments.PENDING or payments.case_of(p) != payments.GREATER:
                await ctx.show(*_ret_page(pid, r, f"{NO} معلق کردن فقط برای تراکنش در انتظارِ دارای مبلغ بیشتر ممکن است."))
                return True
            ok, msg = await subscription.suspend_payment(ctx.bot, pid)
        else:  # phrjy
            ok, msg = await subscription.reject_payment(ctx.bot, pid)
        await ctx.show(*_ret_page(pid, r, f"{OK if ok else NO} {esc(msg)}"))
        return True

    # ---------- وضعیت بازار در منوی اصلی ----------
    if act == "mktt":
        new = db.toggle("mkt_on")
        await ctx.answer()
        await ctx.show(*page_mkt(f"{OK} نمایش وضعیت بازار در منو {'فعال' if new else 'غیرفعال'} شد."))
        return True
    if act == "mkte":
        await ctx.answer()
        await _prompt(
            ctx, "mkt_tpl", "a:mkt",
            "قالب خط وضعیت بازار را در یک پیام ارسال کنید (حداکثر ۵۰۰ کاراکتر).\n"
            "Placeholder ها: {state} {rsi} {adx} {bull} {bear}\n"
            "مثال: وضعیت کلی بازار: {state} | میانگین RSI بازار: {rsi} | درصد نمادهای صعودی: {bull}٪\n\n" + esc(fmt.HELP),
        )
        return True
    if act == "mktd":
        await ctx.answer()
        db.set("mkt_template", config.DEFAULT_MARKET_TEMPLATE)
        await ctx.show(*page_mkt(f"{OK} قالب به حالت پیش‌فرض برگشت."))
        return True
    if act == "mktr":
        await ctx.answer()
        await ctx.show("در حال محاسبه‌ی وضعیت بازار... (ممکن است چند ثانیه طول بکشد)", kb(back_row("a:mkt")))
        try:
            ok = await market_overview.refresh()
        except Exception:
            log.exception("market refresh failed")
            ok = False
        await ctx.show(*page_mkt(f"{OK} وضعیت بازار به‌روزرسانی شد." if ok else f"{NO} محاسبه انجام نشد (داده‌ی کافی نیست یا محاسبه‌ی دیگری در جریان است)."))
        return True

    # ---------- پاسخ خودکار پشتیبانی ----------
    if act == "aisupt":
        new = db.toggle("ai_sup_on")
        await ctx.answer()
        await ctx.show(*page_aisup(f"{OK} پاسخ خودکار پشتیبانی {'فعال' if new else 'غیرفعال'} شد."))
        return True
    return False


def _analysis_sample() -> str:
    """پیش‌نمایش قالب آنالیز با داده‌ی نمونه."""
    from ui import now_jalali_parts

    d, t = now_jalali_parts()
    ctx = {
        "symbol": "BTC/USDT", "date": d, "time": t, "price": "$۶۷,۵۰۰", "change_24h": "+۲.۵۰٪", "trend": "صعودی 📈",
        "rsi": "۶۲ (ناحیه خرید)", "macd": "تقاطع صعودی", "volume": "بالاتر از میانگین", "resistance": "$۶۸,۲۰۰",
        "support": "$۶۵,۸۰۰",
        "ai_analysis": "با توجه به روند صعودی اخیر و تأیید اندیکاتورهای مومنتوم، به نظر می‌رسد قیمت بتواند به سمت مقاومت حرکت کند.\n\n⚠️ ریسک: در صورت شکست حمایت، احتمال اصلاح وجود دارد.",
        "signal": "خرید (LONG)", "tp1": "$۶۸,۲۰۰", "tp2": "$۷۰,۰۰۰", "sl": "$۶۵,۵۰۰", "rr": "۱:۲.۵",
    }
    return tpl.render_template(db.get_msg_template("analysis"), ctx, drop_if_missing=("ai_analysis",))
