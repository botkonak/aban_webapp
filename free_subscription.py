"""چرخه‌ی اشتراک رایگان: هشدار نزدیک پایان و بازگردانی وضعیت قبلی پس از پایان دوره."""
from __future__ import annotations

import asyncio
import logging

import admin_notify
import broadcaster
import plans
from ui import btn, jalali_ts, kb

log = logging.getLogger("free_subscription")

PLAN_TITLE = {plans.PLAN_BOT: "اشتراک رایگان ربات", plans.PLAN_WEBAPP: "اشتراک رایگان تحلیل ساختاری"}


def _user_markup(plan: str):
    rows = []
    if plan == plans.PLAN_WEBAPP:
        from webapp.auth import login_url

        rows.append([btn("ورود به وب‌اپ تحلیل ساختاری", url=login_url())])
    rows.append([btn("منوی اصلی", "u:mn")])
    return kb(*rows)


async def _warn(bot, row) -> None:
    uid = int(row["user_id"])
    plan = plans.normalize_plan(row.get("plan"))
    title = PLAN_TITLE.get(plan, "اشتراک رایگان")
    text = (
        f"⏳ {title} شما تا {jalali_ts(float(row.get('ends_at') or 0))} فعال است.\n"
        "برای ادامه‌ی دسترسی، اشتراک را از منوی خرید تمدید کنید."
    )
    try:
        await broadcaster.send_html(bot, uid, text, _user_markup(plan))
    except Exception:
        log.debug("free warning message failed for %s", uid)
    plans.mark_free_warned(int(row["id"]))
    await admin_notify.notify(
        bot,
        f"⏳ هشدار پایان {title}\nکاربر: <code>{uid}</code>\nپایان: {jalali_ts(float(row.get('ends_at') or 0))}",
        kind="free_warn",
        dedupe_key=f"free_warn:{row['id']}",
        cooldown=86400,
    )


async def _expire(bot, row) -> None:
    uid = int(row["user_id"])
    plan = plans.normalize_plan(row.get("plan"))
    title = PLAN_TITLE.get(plan, "اشتراک رایگان")
    result = plans.restore_expired_claim(row)
    if result["paid_extension"]:
        text = f"✅ {title} شما به پایان رسید؛ اشتراک خریداری‌شده‌ی شما بدون تغییر باقی ماند."
    elif float(result["until"] or 0) > 0:
        text = (
            f"⌛ {title} شما به پایان رسید و دسترسی قبلی شما بازگردانده شد "
            f"(فعال تا {jalali_ts(float(result['until']))})."
        )
    else:
        text = f"⌛ {title} شما به پایان رسید. برای ادامه‌ی دسترسی، اشتراک را تمدید کنید."
    try:
        await broadcaster.send_html(bot, uid, text, _user_markup(plan))
    except Exception:
        log.debug("free expiry message failed for %s", uid)
    await admin_notify.notify(
        bot,
        f"⌛ پایان {title}\nکاربر: <code>{uid}</code>"
        + ("\nاشتراک خریداری‌شده حفظ شد." if result["paid_extension"] else "\nدسترسی قبلی بازگردانده شد."),
        kind="free_expired",
        dedupe_key=f"free_expired:{row['id']}",
        cooldown=86400,
    )


async def process_cycle(bot) -> None:
    for row in plans.free_pending_warnings():
        await _warn(bot, row)
    for row in plans.free_pending_expired():
        await _expire(bot, row)


async def free_loop(app) -> None:
    await asyncio.sleep(120)
    while True:
        try:
            await process_cycle(app.bot)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("free subscription loop failed")
        await asyncio.sleep(1800)
