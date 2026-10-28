"""
صفحه‌های مشترک «عملکرد سیگنال‌ها» و «مشاور موتورها» برای ادمین (uid = 0) و کاربران (uid = شناسه‌ی کاربر).

هر تابع پیشوند کال‌بک (pfx: "a" ادمین، "s" کاربر) و دکمه‌ی برگشت را می‌گیرد؛ داده‌ها فقط مربوط به همان uid هستند.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import engine_advisor as adv
import performance_tracker as perf
from database import db
from ui import NO, OK, TEHRAN, WARN, back_row, btn, esc, kb, ts_text

Page = Tuple[str, Optional[object]]

PERF_PER_PAGE = 8
ADV_STATUS = {"pending": "در انتظار تصمیم", "applied": "اعمال‌شده", "rejected": "ردشده"}
_LABELS = {"active": "ACT", "tp1_hit": "TP1", "tp2_hit": "TP2", "tp3_hit": "TP3", "sl_hit": "SL", "expired": "EXP"}


def _note(note: str) -> str:
    return f"{note}\n\n" if note else ""


def _to_int(s: str, default: int = 0) -> int:
    return int(s) if s and str(s).lstrip("-").isdigit() else default


# ============================================================================
# عملکرد سیگنال‌ها
# ============================================================================
def perf_page(
    uid: int, pfx: str, status: str = "all", period: str = "a", page: str = "0", symbol: str = "",
    note: str = "", back: str = "a:home",
) -> Page:
    status = status if status in perf.STATUS_FILTERS else "all"
    period = period if period in perf.PERIOD_FILTERS else "a"
    st = perf.stats(status, period, symbol, uid=uid)
    total = perf.count(status, period, symbol, uid=uid)
    pages = max(1, (total + PERF_PER_PAGE - 1) // PERF_PER_PAGE)
    pg = min(max(0, _to_int(page)), pages - 1)
    rows_db = perf.list_signals(status, period, symbol, pg * PERF_PER_PAGE, PERF_PER_PAGE, uid=uid)
    lines = []
    for r in rows_db:
        base = (r["pair"] or "").split("/")[0][:7]
        label = _LABELS.get(r["status"], r["status"])
        when = ts_text(r.get("created_at"))[5:]
        lines.append(f"{base:<7} {r['side'][0]} {when} {label:<3} {float(r.get('result_pct') or 0):+6.2f}%")
    flt = (
        f"وضعیت: {perf.STATUS_FILTERS[status][0]} | بازه: {perf.PERIOD_FILTERS[period][0]}"
        + (f" | نماد: {esc(symbol)}" if symbol else "")
    )
    text = (
        f"{_note(note)}عملکرد سیگنال‌ها\n{flt}\n\n"
        f"تعداد کل: {st['total']} | فعال: {st['active']}\n"
        f"موفق (TP خورده است): {st['wins']} | ناموفق (SL خورده است): {st['losses']} | منقضی: {st['expired']}\n"
        f"نرخ موفقیت: {st['rate']}% | میانگین سود/ضرر: {st['avg_pct']}%\n\n"
        + (f"<pre>{esc(chr(10).join(lines))}</pre>\nصفحه {pg + 1} از {pages}" if lines else "سیگنالی با این فیلتر ثبت نشده است.")
        + "\n\nرصد فقط با قیمت لحظه‌ای انجام می‌شود؛ ربات هیچ معامله‌ای انجام نمی‌دهد."
    )
    srow = [btn(f"{OK} {t}" if k == status else t, f"{pfx}:perf:{k}:{period}:0") for k, (t, _s) in perf.STATUS_FILTERS.items()]
    prow = [btn(f"{OK} {t}" if k == period else t, f"{pfx}:perf:{status}:{k}:0") for k, (t, _d) in perf.PERIOD_FILTERS.items()]
    keyboard = [srow[:3], srow[3:], prow]
    nav = []
    if pg > 0:
        nav.append(btn("قبلی", f"{pfx}:perf:{status}:{period}:{pg - 1}"))
    if pg < pages - 1:
        nav.append(btn("بعدی", f"{pfx}:perf:{status}:{period}:{pg + 1}"))
    if nav:
        keyboard.append(nav)
    keyboard.append([btn("فیلتر نماد", f"{pfx}:perfs")] + ([btn("حذف فیلتر نماد", f"{pfx}:perfc")] if symbol else []))
    last = []
    if uid == 0:    # فقط ادمین
        last.append(btn("Export", f"{pfx}:perfe:{status}:{period}"))
    last.append(btn("پاک کردن لیست", f"{pfx}:perfx:{status}:{period}"))
    keyboard.append(last)
    keyboard.append(back_row(back))
    return text, kb(*keyboard)


def perf_clear_confirm(uid: int, pfx: str, status: str = "all", period: str = "a", symbol: str = "") -> Page:
    status = status if status in perf.STATUS_FILTERS else "all"
    period = period if period in perf.PERIOD_FILTERS else "a"
    n = perf.count(status, period, symbol, uid=uid)
    flt = (
        f"وضعیت: {perf.STATUS_FILTERS[status][0]} | بازه: {perf.PERIOD_FILTERS[period][0]}"
        + (f" | نماد: {esc(symbol)}" if symbol else "")
    )
    back = f"{pfx}:perf:{status}:{period}:0"
    if n == 0:
        return f"{NO} با این فیلتر رکوردی وجود ندارد.\n{flt}", kb(back_row(back))
    return (
        f"پاک کردن لیست\n{flt}\n\n"
        f"{n} رکورد با فیلتر بالا برای همیشه حذف می‌شود (این کار قابل بازگشت نیست).\nمطمئن هستید؟",
        kb([btn(f"{OK} بله، پاک شود", f"{pfx}:perfxd:{status}:{period}")], back_row(back)),
    )


# ---------------- Export (txt) ----------------
def export_filename() -> str:
    return "aban_trade_signals_" + datetime.now(TEHRAN).strftime("%Y-%m-%d_%H-%M-%S") + ".txt"


def _p(x: Any) -> str:
    if x is None or x == "":
        return "-"
    try:
        s = f"{float(x):.10f}".rstrip("0").rstrip(".")
        return s or "0"
    except (TypeError, ValueError):
        return str(x)


def export_text(rows: List[Dict[str, Any]], status: str, period: str, symbol: str) -> str:
    """همه‌ی رکوردهای فیلترشده (نه فقط یک صفحه) به‌صورت متن خوانا."""
    status = status if status in perf.STATUS_FILTERS else "all"
    period = period if period in perf.PERIOD_FILTERS else "a"
    now = datetime.now(TEHRAN).strftime("%Y-%m-%d %H:%M:%S")
    head = [
        "گزارش عملکرد سیگنال‌ها - آبان ترید",
        f"زمان خروجی (تهران): {now}",
        f"فیلتر وضعیت: {perf.STATUS_FILTERS[status][0]}",
        f"فیلتر بازه: {perf.PERIOD_FILTERS[period][0]}",
        f"فیلتر نماد: {symbol or 'همه'}",
        f"تعداد رکورد: {len(rows)}",
        "=" * 40,
        "",
    ]
    if not rows:
        return "\n".join(head) + "رکوردی با این فیلتر وجود ندارد.\n"
    blocks: List[str] = []
    for r in rows:
        best = int(r.get("best_tp") or 0)
        blocks.append("\n".join([
            f"--- رکورد #{r['id']} ---",
            f"نماد: {r.get('pair') or '-'}",
            f"جهت: {r.get('side') or '-'}",
            f"قیمت ورود: {_p(r.get('entry'))}",
            f"حد ضرر: {_p(r.get('sl'))}",
            f"TP1: {_p(r.get('tp1'))}",
            f"TP2: {_p(r.get('tp2'))}",
            f"TP3: {_p(r.get('tp3'))}",
            f"وضعیت: {perf.STATUS_LABELS.get(r.get('status'), r.get('status'))}",
            f"بهترین TP رسیده: {'TP' + str(best) if best else 'هیچ‌کدام'}",
            f"درصد نتیجه: {float(r.get('result_pct') or 0):+.2f}%",
            f"بالاترین قیمت: {_p(r.get('max_price'))}",
            f"پایین‌ترین قیمت: {_p(r.get('min_price'))}",
            f"زمان ایجاد: {ts_text(r.get('created_at'))}",
            f"زمان بسته شدن: {ts_text(r['closed_at']) if r.get('closed_at') else '-'}",
        ]))
    return "\n".join(head) + "\n\n".join(blocks) + "\n"


# ============================================================================
# مشاور موتورها
# ============================================================================
def adv_home(uid: int, pfx: str, back: str, note: str = "") -> Page:
    last = adv.latest_id(uid)
    if uid:
        what = "عملکرد ۷ روز اخیر سیگنال‌های شما و تنظیمات فعلی موتورهای شما"
    else:
        what = "عملکرد ۷ روز اخیر سیگنال‌ها و تنظیمات فعلی همه‌ی موتورها"
    text = (
        f"{_note(note)}مشاور موتورها\n\n"
        f"با زدن «دریافت پیشنهاد از AI»، وضعیت فعلی بازار، {what} "
        "برای هوش مصنوعی ارسال می‌شود و پیشنهاد تنظیمات بهتر برمی‌گردد.\n"
        "هوش مصنوعی هیچ‌وقت خودش تنظیمات را تغییر نمی‌دهد؛ پیشنهادها اعتبارسنجی می‌شوند و فقط با تأیید شما اعمال می‌شوند "
        "(و فقط همان پارامترهای پیشنهادی عوض می‌شوند)."
    )
    ok, why = adv.ai_ready()
    if not ok:
        text += f"\n\n{WARN} {esc(why)}"
    rows = [[btn("دریافت پیشنهاد از AI", f"{pfx}:advq")]]
    if last:
        rows.append([btn("آخرین پیشنهاد", f"{pfx}:advs:{last}")])
    rows.append([btn("تاریخچه‌ی پیشنهادات", f"{pfx}:advh")])
    rows.append(back_row(back))
    return text, kb(*rows)


def adv_item(uid: int, pfx: str, sid: str = "0", note: str = "", back: str = "a:home") -> Page:
    r = adv.get(_to_int(sid), uid)
    if r is None:
        return adv_home(uid, pfx, back, f"{NO} این پیشنهاد پیدا نشد.")
    d = db.scope(uid)
    valid = r.get("suggestions") or {}
    changes = adv.build_changes(valid, d) if r["status"] == "pending" else (r.get("old") or {}).get("changes", [])
    table = adv.format_table(changes, d)
    reasoning = r.get("reasoning") or {}
    rejected = r.get("rejected") or []
    parts = [
        f"{_note(note)}پیشنهاد #{r['id']} | {ADV_STATUS.get(r['status'], r['status'])}",
        f"زمان: {ts_text(r['created_at'])}" + (f" | اعمال: {ts_text(r['applied_at'])}" if r.get("applied_at") else ""),
        "",
        "تحلیل AI:",
        esc((r.get("analysis_text") or "-")[:1100]),
        "",
    ]
    if table:
        parts += ["پیشنهادات (فعلی / پیشنهادی):", f"<pre>{esc(table[:1700])}</pre>"]
    else:
        parts.append("پیشنهاد معتبری برای تغییر وجود ندارد.")
    if reasoning:
        txt = "\n".join(f"{adv.ENGINE_SHORT.get(k, k)}: {v}" for k, v in reasoning.items())
        parts += ["", "دلایل AI:", esc(txt[:800])]
    if rejected:
        parts += ["", f"{WARN} پیشنهادهای ردشده (نامعتبر):", esc("\n".join("- " + x for x in rejected[:6])[:600])]
    rows = []
    if r["status"] == "pending":
        if table:
            rows.append([btn("اعمال پیشنهادات", f"{pfx}:advac:{r['id']}")])
        rows.append([btn("رد کردن", f"{pfx}:advr:{r['id']}")])
    rows.append(back_row(f"{pfx}:adv" if r["status"] == "pending" else f"{pfx}:advh"))
    return "\n".join(parts), kb(*rows)


def adv_confirm(uid: int, pfx: str, sid: str = "0") -> Page:
    mode = db.scope(uid).eng_mode()
    return (
        "با تأیید، فقط پارامترها / موتورهای داخل جدول پیشنهاد (طبق حالت فعلی: "
        f"{'مشترک' if mode == 'global' else 'جداگانه'}) تغییر می‌کنند و بقیه‌ی تنظیمات دست‌نخورده می‌مانند.\n"
        "قبل از اعمال دوباره اعتبارسنجی می‌شود.\n\nاعمال شود؟",
        kb([btn(f"{OK} بله، اعمال کنید", f"{pfx}:advy:{sid}")], back_row(f"{pfx}:advs:{sid}")),
    )


def adv_history(uid: int, pfx: str) -> Page:
    items = adv.history(15, uid)
    if not items:
        return "تاریخچه‌ی پیشنهادات\n\nهنوز پیشنهادی ثبت نشده است.", kb(back_row(f"{pfx}:adv"))
    rows = [
        [btn(f"#{r['id']} | {ADV_STATUS.get(r['status'], r['status'])} | {ts_text(r['created_at'])}", f"{pfx}:advs:{r['id']}")]
        for r in items
    ]
    rows.append(back_row(f"{pfx}:adv"))
    return "تاریخچه‌ی پیشنهادات\n\nیکی را برای مرور انتخاب کنید:", kb(*rows)
