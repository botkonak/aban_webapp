"""
پنل تنظیمات مستقل هر کاربر (آبان ترید).

هر کاربر (ثبت‌نام‌شده و دارای اشتراک فعال) تنظیمات موتورها، تایم‌فریم، ریسک و اهرم، تنظیمات ارسال، فیلتر نمادها،
مدل AI، مشاور موتورها و عملکرد سیگنال‌های خودش را دارد. همه‌ی داده‌ها فقط برای همان کاربر ذخیره می‌شود
(database.UserView) و هیچ اثری روی ادمین یا سایر کاربران ندارد.

کال‌بک‌ها: s:<act>:<args...>   (دسترسی و عضویت اجباری قبل از ورود به این ماژول در telegram_handler بررسی می‌شود)
"""
from __future__ import annotations

import hashlib
import logging
import time
from typing import Dict, List, Optional, Tuple

from telegram.error import TelegramError

import config
import engine_advisor as adv
import engine_io
import performance_tracker as perf
import plans
import scoped_pages as sp
import subscription
from admin_panel import ENGINE_HELP
from database import db
from signal_engine import engine
from ui import NO, OK, WARN, Ctx, back_row, btn, chunk, esc, kb, on_off

log = logging.getLogger("user_panel")

Page = Tuple[str, Optional[object]]

HOME = "s:home"
SUB_TEXT = "برای استفاده از تنظیمات باید اشتراک فعال داشته باشید.\nبا دکمه‌ی زیر اشتراک تهیه کنید."
SUB_AI_TEXT = "برای استفاده از امکانات هوش مصنوعی باید اشتراک فعال داشته باشید.\nبا دکمه‌ی زیر اشتراک تهیه کنید."

# زمان‌بندی اسکن خودکار هر کاربر (uid -> epoch)؛ telegram_handler.user_signal_loop آن را می‌خواند
user_next: Dict[int, float] = {}
_adv_busy: set = set()
_adv_last: Dict[int, float] = {}

USER_STATE_KINDS = ("uparam", "uimport", "uperf_sym")


def reset_schedule(uid: int) -> None:
    """بعد از تغییر «فاصله ارسال» یا ارسال سیگنال، زمان اسکن بعدی کاربر دوباره محاسبه می‌شود."""
    user_next.pop(uid, None)


def _note(note: str) -> str:
    return f"{note}\n\n" if note else ""


def _to_int(s: str, default: int = 0) -> int:
    return int(s) if s and str(s).lstrip("-").isdigit() else default


def _d(uid: int):
    return db.user(uid)


def _val(d, key: str, side: Optional[str] = None) -> str:
    spec = config.PARAMS[key]
    v = d.get_e_float(key, side)
    s = str(int(v)) if spec.kind == "int" else config.fmt_number(v)
    return f"{s}{spec.unit}" if spec.unit in ("%", "X") else (f"{s} {spec.unit}" if spec.unit else s)


def _view_side(d) -> Optional[str]:
    """جهت در حال ویرایش؛ در حالت مشترک None."""
    if d.eng_mode() != "separate":
        return None
    v = (d.get("eng_view") or "LONG").upper()
    return v if v in ("LONG", "SHORT") else "LONG"


def _side_row(d, ret: str) -> List:
    side = _view_side(d)
    if side is None:
        return []
    return [btn(f"{OK} {s}" if s == side else s, f"s:engs:{s}:{ret}") for s in ("LONG", "SHORT")]


def _side_title(side: Optional[str]) -> str:
    return f"\nپروفایل در حال ویرایش: {side}" if side else ""


def _param_ok(spec: config.ParamSpec) -> bool:
    """پارامترهایی که کاربر اجازه‌ی تغییرشان را دارد (همه‌ی موتورها به‌جز AI، ریسک و اهرم، تنظیمات ارسال)."""
    return (spec.group in config.ENGINES and spec.group != "ai") or spec.group in ("rsk", "snd")


def _param_back(spec: config.ParamSpec) -> str:
    return {"rsk": "s:rsk", "snd": "s:snd"}.get(spec.group, f"s:engp:{spec.group}")


# ============================================================================
# صفحه‌ها
# ============================================================================
def page_home(uid: int, note: str = "") -> Page:
    text = (
        f"{_note(note)}تنظیمات من\n\n"
        "همه‌ی این تنظیمات فقط روی سیگنال‌های خود شما اثر دارد و روی ادمین یا سایر کاربران هیچ تأثیری ندارد.\n"
        "سیگنال‌های شما با همین تنظیمات ساخته می‌شود و فقط برای خودتان ارسال می‌شود."
    )
    rows = [
        [btn("تنظیمات موتورها", "s:eng")],
        [btn("تنظیمات تایم‌فریم", "s:tf")],
        [btn("تنظیمات ریسک و اهرم", "s:rsk")],
        [btn("تنظیمات ارسال", "s:snd")],
        [btn("فیلتر نمادها", "s:sf")],
    ]
    if plans.bot_feature_allowed(uid, "ai"):
        rows.append([btn("مدل هوش مصنوعی", "s:ai")])
        rows.append([btn("مشاور موتورها", "s:adv")])
    rows.append([btn("عملکرد سیگنال‌ها", "s:perf:all:a:0")])
    rows.append(back_row("u:menu"))
    return text, kb(*rows)


async def open_home(ctx: Ctx, note: str = "") -> None:
    db.ensure_user_profile(ctx.uid)
    await ctx.show(*page_home(ctx.uid, note))


# ---------------- موتورها ----------------
def page_eng(uid: int, note: str = "") -> Page:
    d = _d(uid)
    mode = d.eng_mode()
    side = _view_side(d)
    lines = [
        "تنظیمات موتورها",
        "",
        f"حالت تنظیمات: {'مشترک (Global)' if mode == 'global' else 'جداگانه (Separate)'}",
    ]
    if side:
        lines.append(f"پروفایل در حال ویرایش: {side}")
    lines += ["", "وضعیت موتورها:"]
    toggles = []
    for eid in config.TOGGLE_ENGINES:
        title, key = config.ENGINES[eid]
        if eid == "ai":
            ai_on = db.get_bool("ai_on") and db.get_e_bool(key, side)
            lines.append(f"{title}: {on_off(ai_on)} (توسط ادمین تعیین می‌شود)")
            continue
        on = d.get_e_bool(key, side)
        lines.append(f"{title}: {on_off(on)}")
        toggles.append(btn(f"{title}: {OK if on else NO}", f"s:engt:{eid}"))
    lines += [
        "",
        "برای روشن / خاموش کردن روی هر موتور بزنید. برای تغییر پارامترها از لیست پایین موتور را انتخاب کنید.",
    ]
    if mode == "global":
        lines.append("در حالت مشترک تغییرات روی هر دو جهت LONG و SHORT اعمال می‌شود.")
    else:
        lines.append("در حالت جداگانه هر جهت تنظیمات و تایم‌فریم مستقل خودش را دارد؛ با دکمه‌ی LONG / SHORT بین دو پروفایل جابه‌جا شوید.")
    params = [btn(config.ENGINES[eid][0], f"s:engp:{eid}") for eid in config.ENGINES]
    rows = [
        [
            btn(f"{OK} مشترک" if mode == "global" else "مشترک", "s:engm:global"),
            btn(f"{OK} جداگانه" if mode == "separate" else "جداگانه", "s:engm:separate"),
        ],
    ]
    sr = _side_row(d, "eng")
    if sr:
        rows.append(sr)
    rows += [
        *chunk(toggles, 2),
        [btn("پارامترهای موتورها", "s:noop")],
        *chunk(params, 2),
        [btn("Import", "s:engi"), btn("Export", "s:engx")],
        back_row(HOME),
    ]
    return _note(note) + "\n".join(lines), kb(*rows)


def page_engine_params(uid: int, eid: str = "", note: str = "") -> Page:
    if eid not in config.ENGINES:
        return page_eng(uid)
    if eid == "cd":
        return page_candles(uid, note)
    if eid == "ai":
        return page_ai(uid, note)
    d = _d(uid)
    side = _view_side(d)
    title, key = config.ENGINES[eid]
    specs = [s for s in config.PARAM_SPECS if s.group == eid]
    on = d.get_e_bool(key, side)
    lines = [f"پارامترهای {esc(title)}{_side_title(side)}", "", f"وضعیت: {on_off(on)}"]
    if ENGINE_HELP.get(eid):
        lines += ["", esc(ENGINE_HELP[eid])]
    lines.append("")
    for s in specs:
        lines.append(f"{esc(s.label)}: {esc(_val(d, s.key, side))}")
    lines += ["", "برای تغییر هر مقدار، روی آن بزنید و عدد جدید را ارسال کنید."]
    rows = []
    sr = _side_row(d, f"engp:{eid}")
    if sr:
        rows.append(sr)
    rows.append([btn(f"{title}: {OK if on else NO}", f"s:engt:{eid}:p")])
    rows += [[btn(f"{s.label}: {_val(d, s.key, side)}", f"s:pr:{s.key}")] for s in specs]
    rows.append(back_row("s:eng"))
    return _note(note) + "\n".join(lines), kb(*rows)


def page_candles(uid: int, note: str = "") -> Page:
    d = _d(uid)
    side = _view_side(d)
    on = d.get_e_bool("en_candle", side)
    specs = [s for s in config.PARAM_SPECS if s.group == "cd"]
    lines = [
        f"الگوهای کندلی{_side_title(side)}", "",
        f"وضعیت: {on_off(on)}", "",
        "الگوی هم‌جهت با سیگنال تأیید قوی (امتیاز بیشتر) و الگوی مخالف هشدار و کاهش امتیاز حساب می‌شود.", "",
        "الگوهای فعال:",
    ]
    pat_btns = []
    for pid, (bull, bear) in config.CANDLE_PATTERNS.items():
        pon = d.get_e_bool(f"cdp_{pid}", side)
        lines.append(f"{bull} / {bear}: {on_off(pon)}")
        pat_btns.append(btn(f"{bull} / {bear}: {OK if pon else NO}", f"s:cdt:{pid}"))
    lines.append("")
    for s in specs:
        lines.append(f"{esc(s.label)}: {esc(_val(d, s.key, side))}")
    rows = []
    sr = _side_row(d, "engp:cd")
    if sr:
        rows.append(sr)
    rows.append([btn(f"الگوهای کندلی: {OK if on else NO}", "s:engt:cd:p")])
    rows += [[b] for b in pat_btns]
    rows += [[btn(f"{s.label}: {_val(d, s.key, side)}", f"s:pr:{s.key}")] for s in specs]
    rows.append(back_row("s:eng"))
    return _note(note) + "\n".join(lines), kb(*rows)


# ---------------- تایم‌فریم ----------------
def page_tf(uid: int, note: str = "") -> Page:
    d = _d(uid)
    mode = d.eng_mode()
    side = _view_side(d)
    e, f = d.get_e("tf_entry", side), d.get_e("tf_final", side)
    e_on, f_on = d.get_e_bool("tf_entry_on", side), d.get_e_bool("tf_final_on", side)
    text = (
        f"{_note(note)}تنظیمات تایم‌فریم{_side_title(side)}\n\n"
        f"حالت تنظیمات: {'مشترک (Global)' if mode == 'global' else 'جداگانه (Separate)'}\n"
        f"تایم‌فریم اولیه (ورود): {esc(e)} - {on_off(e_on)}\n"
        f"تایم‌فریم نهایی (تأیید): {esc(f)} - {on_off(f_on)}\n\n"
        "سیگنال وقتی صادر می‌شود که جهت هر دو تایم‌فریم یکی باشد.\n"
        "در حالت مشترک یک تایم‌فریم برای هر دو جهت است؛ در حالت جداگانه LONG و SHORT هرکدام تایم‌فریم مستقل خودشان را دارند "
        "(با دکمه‌ی LONG / SHORT بین دو پروفایل جابه‌جا شوید)."
    )

    def opts(options: List[str], cur: str, cb: str):
        return [btn(f"{OK} {o}" if o == cur else o, f"s:{cb}:{o}") for o in options]

    rows = [
        [
            btn(f"{OK} مشترک" if mode == "global" else "مشترک", "s:engm:global:tf"),
            btn(f"{OK} جداگانه" if mode == "separate" else "جداگانه", "s:engm:separate:tf"),
        ],
    ]
    sr = _side_row(d, "tf")
    if sr:
        rows.append(sr)
    rows += [
        [btn("تایم‌فریم اولیه (ورود)", "s:noop")],
        opts(config.ENTRY_TF_OPTIONS, e, "tfe"),
        [btn(f"تایم‌فریم اولیه: {on_off(e_on)}", "s:tft:e")],
        [btn("تایم‌فریم نهایی (تأیید)", "s:noop")],
        opts(config.FINAL_TF_OPTIONS, f, "tff"),
        [btn(f"تایم‌فریم نهایی: {on_off(f_on)}", "s:tft:f")],
        back_row(HOME),
    ]
    return text, kb(*rows)


# ---------------- هوش مصنوعی (فقط انتخاب مدل) ----------------
def page_ai(uid: int, note: str = "") -> Page:
    d = _d(uid)
    side = _view_side(d)
    master = db.get_bool("ai_on") and not _ai_off()
    eng_on = db.get_e_bool("en_ai", side)
    models = db.ai_models()
    model = d.get("ai_model")
    lines = [
        f"مدل هوش مصنوعی{_side_title(side)}", "",
        f"هوش مصنوعی (طبق تنظیمات ادمین): {on_off(master and eng_on)}",
        f"مدل شما: {esc(model)}", "",
        "قابلیت‌ها (توسط ادمین تعیین می‌شود):",
    ]
    for _fid, (skey, title) in config.AI_FEATURES.items():
        lines.append(f"{title}: {on_off(db.get_e_bool(skey, side))}")
    lines += [
        "",
        "شما فقط می‌توانید مدل هوش مصنوعی خودتان را از بین مدل‌های مجاز زیر انتخاب کنید؛ بقیه‌ی تنظیمات هوش مصنوعی "
        "(روشن / خاموش بودن، آستانه‌ها، سقف مصرف و کلیدها) فقط دست ادمین است.",
    ]
    rows = chunk([
        btn(f"{OK} {m.split('/')[-1]}" if m == model else m.split("/")[-1], f"s:aim:{i}")
        for i, m in enumerate(models)
    ], 2)
    rows.append(back_row(HOME))
    return _note(note) + "\n".join(lines), kb(*rows)


def _ai_off() -> bool:
    from ai_engine import ai
    return ai.temporarily_off()


# ---------------- ریسک و اهرم / ارسال ----------------
def page_risk(uid: int, note: str = "") -> Page:
    d = _d(uid)
    keys = ["risk_min", "risk_max", "lev_min", "lev_max"]
    lines = ["تنظیمات ریسک و اهرم", ""]
    for k in keys:
        lines.append(f"{esc(config.PARAMS[k].label)}: {esc(_val(d, k))}")
    lines += ["", "ریسک و اهرم هر سیگنال شما به‌صورت خودکار و بر اساس نوسان نماد و قدرت سیگنال، داخل همین بازه‌ها تعیین می‌شود."]
    rows = [[btn(f"{config.PARAMS[k].label}: {_val(d, k)}", f"s:pr:{k}")] for k in keys]
    return _note(note) + "\n".join(lines), kb(*rows, back_row(HOME))


def page_send(uid: int, note: str = "") -> Page:
    d = _d(uid)
    dd = d.get_bool("dedupe_on")
    text = (
        f"{_note(note)}تنظیمات ارسال\n\n"
        f"فاصله ارسال سیگنال: {esc(_val(d, 'send_interval'))}\n"
        f"جلوگیری از تکرار ۲۴ ساعته: {on_off(dd)}\n\n"
        "ربات هر چند دقیقه یک بار (به‌اندازه‌ی فاصله‌ی ارسال) بازار را با تنظیمات شما بررسی می‌کند و اگر سیگنال معتبری "
        "پیدا شود برای شما می‌فرستد. اگر «جلوگیری از تکرار» روشن باشد نمادی که در ۲۴ ساعت اخیر برای شما سیگنال شده "
        "دوباره برای شما سیگنال نمی‌شود."
    )
    return text, kb(
        [btn(f"فاصله ارسال: {_val(d, 'send_interval')}", "s:pr:send_interval")],
        [btn(f"جلوگیری از تکرار ۲۴ ساعته: {on_off(dd)}", "s:sndt")],
        back_row(HOME),
    )


# ---------------- فیلتر نمادها ----------------
def _h(s: str) -> str:
    return hashlib.md5(s.encode("utf-8")).hexdigest()[:8]


def _cat_label(name: str) -> str:
    return config.NONE_CAT_LABEL if name == config.NONE_CAT else name


def _sorted_cats(cmap: Dict[str, List[str]]) -> List[str]:
    names = sorted((n for n in cmap if n != config.NONE_CAT), key=lambda n: n.lower())
    if config.NONE_CAT in cmap:
        names.append(config.NONE_CAT)
    return names


def _find_cat(h: str, cmap: Dict[str, List[str]]) -> Optional[str]:
    for n in cmap:
        if _h(n) == h:
            return n
    return None


def page_sf(uid: int, note: str = "") -> Page:
    d = _d(uid)
    cmap = engine.category_map()
    if not engine.symbols:
        text = (
            f"{_note(note)}فیلتر نمادها\n\n"
            "هنوز لیست نمادها از صرافی دریافت نشده است. چند لحظه‌ی دیگر دوباره امتحان کنید."
        )
        return text, kb([btn("تلاش دوباره", "s:sf")], back_row(HOME))
    total, allowed = len(engine.symbols), len(engine.allowed_symbols(d))
    lines = [
        "فیلتر نمادها", "",
        f"کل نمادهای فیوچرز: {total}",
        f"نمادهای فعال برای سیگنال‌های شما: {allowed}", "",
        "فقط نمادهایی برای شما سیگنال می‌گیرند که خودشان تیک داشته باشند و همه‌ی دسته‌های آن‌ها فعال باشد. "
        "دسته‌های بازار سنتی (Stock / TradFi / Forex) به‌صورت پیش‌فرض غیرفعال‌اند. "
        "روی هر دسته بزنید تا نمادهای آن را مشاهده کنید:",
    ]
    cat_btns = []
    for n in _sorted_cats(cmap):
        pairs = cmap[n]
        ticked = sum(1 for p in pairs if d.sym_enabled(p))
        label = f"{OK if d.cat_enabled(n) else NO} {_cat_label(n)} ({ticked}/{len(pairs)})"
        cat_btns.append(btn(label, f"s:sfc:{_h(n)}:0"))
    rows = chunk(cat_btns, 2)
    rows += [
        [btn("فعال‌سازی همه‌ی دسته‌ها", "s:sfa:1"), btn("غیرفعال‌سازی همه‌ی دسته‌ها", "s:sfa:0")],
        [btn("بازنشانی به پیش‌فرض", "s:sfd")],
        back_row(HOME),
    ]
    return _note(note) + "\n".join(lines), kb(*rows)


def page_sf_cat(uid: int, h: str = "", page: str = "0", note: str = "") -> Page:
    d = _d(uid)
    cmap = engine.category_map()
    name = _find_cat(h, cmap)
    if name is None:
        return page_sf(uid, f"{NO} این دسته پیدا نشد (لیست نمادها تغییر کرده است).")
    pairs = cmap[name]
    size = config.SF_PAGE_SIZE
    pages = max(1, (len(pairs) + size - 1) // size)
    pg = min(max(0, _to_int(page)), pages - 1)
    ticked = sum(1 for p in pairs if d.sym_enabled(p))
    cat_on = d.cat_enabled(name)
    text = (
        f"{_note(note)}دسته: {esc(_cat_label(name))}\n"
        f"وضعیت دسته: {on_off(cat_on)}\n"
        f"تعداد نمادها: {len(pairs)} | تیک‌دار: {ticked}\n"
        f"صفحه {pg + 1} از {pages}\n\n"
        "اگر دسته غیرفعال باشد، هیچ‌یک از نمادهای آن برای شما سیگنال نمی‌گیرد. "
        "تیک هر نماد برای همه‌ی دسته‌های آن یکسان است."
    )
    sym_btns = [
        btn(f"{OK if d.sym_enabled(p) else NO} {engine.bases.get(p, p)}", f"s:sfs:{h}:{pg}:{_h(p)}")
        for p in pairs[pg * size:(pg + 1) * size]
    ]
    nav = []
    if pg > 0:
        nav.append(btn("قبلی", f"s:sfc:{h}:{pg - 1}"))
    if pg < pages - 1:
        nav.append(btn("بعدی", f"s:sfc:{h}:{pg + 1}"))
    rows = [
        [btn(f"دسته‌بندی: {on_off(cat_on)}", f"s:sfct:{h}:{pg}")],
        [btn("انتخاب همه (تیک‌دار)", f"s:sfca:{h}:1:{pg}"), btn("حذف همه‌ی تیک‌ها", f"s:sfca:{h}:0:{pg}")],
        *chunk(sym_btns, 3),
    ]
    if nav:
        rows.append(nav)
    rows.append(back_row("s:sf"))
    return text, kb(*rows)


def page_sf_reset() -> Page:
    return (
        "آیا از بازگرداندن فیلتر نمادهای خودتان به حالت پیش‌فرض اطمینان دارید؟\n"
        "همه‌ی نمادها تیک‌دار می‌شوند و فقط دسته‌های بازار سنتی (Stock / TradFi / Forex) غیرفعال می‌مانند.",
        kb([btn(f"{OK} بله، بازنشانی کنید", "s:sfdx")], back_row("s:sf")),
    )


# ============================================================================
# کال‌بک‌ها
# ============================================================================
async def _prompt(ctx: Ctx, kind: str, back: str, prompt: str, **extra) -> None:
    mid = ctx.q.message.message_id if ctx.q and ctx.q.message else None
    ctx.user_data["state"] = {"kind": kind, "back": back, "prompt": prompt, "mid": mid, **extra}
    m = await ctx.show(prompt, kb(back_row(back)))
    if m is not None:
        ctx.user_data["state"]["mid"] = m.message_id


def _perf_symbol(ctx: Ctx) -> str:
    return ctx.user_data.get("perf_sym") or ""


async def handle_callback(ctx: Ctx, parts: List[str]) -> None:
    """parts = اجزای callback_data بعد از 's'. دسترسی (ثبت‌نام + اشتراک) قبلاً بررسی شده است."""
    act = parts[0] if parts else "home"
    args = parts[1:]
    uid = ctx.uid
    ctx.user_data.pop("state", None)        # هر ناوبری، ورودی در انتظار را لغو می‌کند
    db.ensure_user_profile(uid)
    d = _d(uid)

    ai_actions = {"ai", "aim", "adv", "advs", "advac", "advh", "advq", "advy", "advr"}
    if (act in ai_actions or (act == "engp" and (args[0] if args else "") == "ai")) and not plans.bot_feature_allowed(uid, "ai"):
        await ctx.answer()
        await ctx.show(*subscription.need_sub_page(uid, SUB_AI_TEXT))
        return

    if act == "noop":
        await ctx.answer()
        return
    if act == "home":
        await ctx.answer()
        await ctx.show(*page_home(uid))
        return

    # ---------- موتورها ----------
    if act == "eng":
        await ctx.answer()
        await ctx.show(*page_eng(uid))
        return
    if act == "engp":
        await ctx.answer()
        await ctx.show(*page_engine_params(uid, args[0] if args else ""))
        return
    if act == "engm":
        mode = args[0] if args else "global"
        if mode == "separate":
            d.ensure_side_profiles()
            d.set("eng_mode", "separate")
            note = f"{OK} حالت جداگانه فعال شد؛ هر جهت از روی تنظیمات فعلی پروفایل مستقل خودش را ساخت."
        else:
            d.set("eng_mode", "global")
            note = f"{OK} حالت مشترک فعال شد؛ حالا یک پروفایل برای هر دو جهت استفاده می‌شود."
        await ctx.answer()
        if len(args) > 1 and args[1] == "tf":
            await ctx.show(*page_tf(uid, note))
        else:
            await ctx.show(*page_eng(uid, note))
        return
    if act == "engs":
        side = (args[0] if args else "LONG").upper()
        if side in ("LONG", "SHORT"):
            d.set("eng_view", side)
        ret = args[1] if len(args) > 1 else "eng"
        ret_args = args[2:]
        await ctx.answer()
        if ret == "tf":
            await ctx.show(*page_tf(uid))
        elif ret == "engp":
            await ctx.show(*page_engine_params(uid, ret_args[0] if ret_args else ""))
        else:
            await ctx.show(*page_eng(uid))
        return
    if act == "engt":
        eid = args[0] if args else ""
        meta = config.ENGINES.get(eid)
        side = _view_side(d)
        back_params = len(args) > 1 and args[1] == "p"
        if meta and eid != "ai":
            key = meta[1]
            if eid in ("st", "ema") and d.get_e_bool(key, side):
                other = config.ENGINES["ema" if eid == "st" else "st"][1]
                if not d.get_e_bool(other, side):
                    await ctx.answer(
                        f"{NO} حداقل یکی از Supertrend یا EMA باید روشن بماند، چون جهت سیگنال از آن‌ها می‌آید.",
                        alert=True,
                    )
                    return
            d.toggle_e(key, side)
        await ctx.answer()
        await ctx.show(*(page_engine_params(uid, eid) if back_params and meta and eid != "ai" else page_eng(uid)))
        return
    if act == "cdt":
        pid = args[0] if args else ""
        if pid in config.CANDLE_PATTERNS:
            d.toggle_e(f"cdp_{pid}", _view_side(d))
        await ctx.answer()
        await ctx.show(*page_candles(uid))
        return
    if act == "engx":
        await ctx.answer()
        try:
            await ctx.bot.send_document(
                chat_id=uid,
                document=engine_io.export_bytes(d),
                filename="aban_trade_my_engines.json",
                caption=(
                    f"تنظیمات موتورهای شما ({config.BRAND}). فایل را ویرایش کنید و با دکمه‌ی Import دوباره ارسال کنید.\n"
                    f"حالت فعلی: {'جداگانه (LONG و SHORT)' if d.eng_mode() == 'separate' else 'مشترک'}"
                ),
                reply_markup=kb(back_row("s:eng")),
            )
        except TelegramError as e:
            log.warning("user export send failed: %s", e)
            await ctx.show(*page_eng(uid, f"{NO} ارسال فایل ممکن نشد، دوباره امتحان کنید."))
        return
    if act == "engi":
        await ctx.answer()
        prompt = (
            "فایل JSON تنظیمات موتورها (همان فایل Export که ویرایش کرده‌اید) را همین‌جا ارسال کنید.\n\n"
            "- فایل را به‌صورت Document ارسال کنید (یا متن JSON را مستقیم paste کنید).\n"
            "- اول کل فایل بررسی می‌شود؛ اگر حتی یک مقدار نامعتبر باشد هیچ تغییری اعمال نمی‌شود.\n"
            "- حالت مشترک / جداگانه هم از روی فایل تعیین می‌شود."
        )
        await _prompt(ctx, "uimport", "s:eng", prompt)
        return

    # ---------- تایم‌فریم ----------
    if act == "tf":
        await ctx.answer()
        await ctx.show(*page_tf(uid))
        return
    if act in ("tfe", "tff"):
        opts = config.ENTRY_TF_OPTIONS if act == "tfe" else config.FINAL_TF_OPTIONS
        if args and args[0] in opts:
            d.set_e("tf_entry" if act == "tfe" else "tf_final", args[0], _view_side(d))
        await ctx.answer()
        await ctx.show(*page_tf(uid))
        return
    if act == "tft":
        side = _view_side(d)
        which = args[0] if args else "e"
        key, other = ("tf_entry_on", "tf_final_on") if which == "e" else ("tf_final_on", "tf_entry_on")
        if d.get_e_bool(key, side) and not d.get_e_bool(other, side):
            await ctx.answer(f"{NO} حداقل یکی از تایم‌فریم‌ها باید فعال بماند.", alert=True)
            return
        d.toggle_e(key, side)
        await ctx.answer()
        await ctx.show(*page_tf(uid))
        return

    # ---------- مدل AI ----------
    if act == "ai":
        await ctx.answer()
        old = d.fix_model()
        note = f"{WARN} مدل قبلی شما ({esc(old)}) از لیست مجاز حذف شده بود؛ مدل شما تغییر کرد." if old else ""
        await ctx.show(*page_ai(uid, note))
        return
    if act == "aim":
        idx = _to_int(args[0] if args else "", -1)
        models = db.ai_models()
        if 0 <= idx < len(models):
            d.set("ai_model", models[idx])
        await ctx.answer()
        await ctx.show(*page_ai(uid, f"{OK} مدل شما ذخیره شد."))
        return

    # ---------- ریسک / ارسال ----------
    if act == "rsk":
        await ctx.answer()
        await ctx.show(*page_risk(uid))
        return
    if act == "snd":
        await ctx.answer()
        await ctx.show(*page_send(uid))
        return
    if act == "sndt":
        d.toggle("dedupe_on")
        await ctx.answer()
        await ctx.show(*page_send(uid))
        return
    if act == "pr":
        key = args[0] if args else ""
        spec = config.PARAMS.get(key)
        await ctx.answer()
        if not spec or not _param_ok(spec) or key not in config.USER_KEYS:
            return
        side = _view_side(d) if config.is_engine_key(key) else None
        lo = int(spec.min) if float(spec.min).is_integer() else spec.min
        hi = int(spec.max) if float(spec.max).is_integer() else spec.max
        kind_txt = "عدد صحیح" if spec.kind == "int" else "عدد (می‌تواند اعشاری باشد)"
        prompt = (
            f"{esc(spec.label)}{_side_title(side)}\n\n"
            f"مقدار فعلی: {esc(_val(d, key, side))}\n"
            f"بازه‌ی مجاز: {lo} تا {hi}\n\n"
            f"مقدار جدید را به‌صورت {kind_txt} همین‌جا ارسال کنید:"
        )
        await _prompt(ctx, "uparam", _param_back(spec), prompt, key=key, side=side)
        return

    # ---------- فیلتر نمادها ----------
    if act == "sf":
        await ctx.answer()
        await ctx.show(*page_sf(uid))
        return
    if act == "sfc":
        await ctx.answer()
        await ctx.show(*page_sf_cat(uid, args[0] if args else "", args[1] if len(args) > 1 else "0"))
        return
    if act in ("sfct", "sfca", "sfs"):
        h = args[0] if args else ""
        cmap = engine.category_map()
        name = _find_cat(h, cmap)
        await ctx.answer()
        if name is None:
            await ctx.show(*page_sf(uid, f"{NO} این دسته پیدا نشد (لیست نمادها تغییر کرده است)."))
            return
        pairs = cmap[name]
        if act == "sfct":
            d.set_cats([name], not d.cat_enabled(name))
            pg = args[1] if len(args) > 1 else "0"
        elif act == "sfca":
            d.set_syms(pairs, (args[1] if len(args) > 1 else "1") == "1")
            pg = args[2] if len(args) > 2 else "0"
        else:
            pg = args[1] if len(args) > 1 else "0"
            target = next((p for p in pairs if _h(p) == (args[2] if len(args) > 2 else "")), None)
            if target is not None:
                d.set_syms([target], not d.sym_enabled(target))
        await ctx.show(*page_sf_cat(uid, h, pg))
        return
    if act == "sfa":
        on = (args[0] if args else "1") == "1"
        await ctx.answer()
        d.set_cats(list(engine.category_map().keys()), on)
        await ctx.show(*page_sf(uid, f"{OK} همه‌ی دسته‌ها {'فعال' if on else 'غیرفعال'} شدند."))
        return
    if act == "sfd":
        await ctx.answer()
        await ctx.show(*page_sf_reset())
        return
    if act == "sfdx":
        await ctx.answer()
        d.reset_filters()
        await ctx.show(*page_sf(uid, f"{OK} فیلتر نمادها به حالت پیش‌فرض برگشت."))
        return

    # ---------- مشاور موتورها ----------
    if act == "adv":
        await ctx.answer()
        await ctx.show(*sp.adv_home(uid, "s", HOME))
        return
    if act == "advs":
        await ctx.answer()
        await ctx.show(*sp.adv_item(uid, "s", args[0] if args else "0", "", HOME))
        return
    if act == "advac":
        await ctx.answer()
        await ctx.show(*sp.adv_confirm(uid, "s", args[0] if args else "0"))
        return
    if act == "advh":
        await ctx.answer()
        await ctx.show(*sp.adv_history(uid, "s"))
        return
    if act == "advq":
        ok, why = adv.ai_ready()
        if not ok:
            await ctx.answer()
            await ctx.show(*sp.adv_home(uid, "s", HOME, f"{NO} {esc(why)}"))
            return
        if uid in _adv_busy:
            await ctx.answer("درخواست قبلی شما هنوز در حال انجام است؛ چند لحظه صبر کنید.", alert=True)
            return
        wait = config.USER_ADVISOR_COOLDOWN - (time.monotonic() - _adv_last.get(uid, -1e9))
        if wait > 0:
            await ctx.answer(f"لطفاً {int(wait) + 1} ثانیه دیگر دوباره امتحان کنید.", alert=True)
            return
        await ctx.answer()
        _adv_busy.add(uid)
        try:
            await ctx.show(
                "در حال جمع‌آوری داده‌ها و دریافت پیشنهاد از هوش مصنوعی... (ممکن است تا یک دقیقه طول بکشد)",
                kb(back_row("s:adv")),
            )
            sid, err = await adv.request_suggestion(uid)
            _adv_last[uid] = time.monotonic()
            if len(_adv_last) > 20000:
                for k in list(_adv_last)[:10000]:
                    _adv_last.pop(k, None)
        finally:
            _adv_busy.discard(uid)
        if sid is None:
            await ctx.show(*sp.adv_home(uid, "s", HOME, f"{NO} {esc(err)}"))
        else:
            await ctx.show(*sp.adv_item(uid, "s", str(sid), "", HOME))
        return
    if act == "advy":
        sid = _to_int(args[0] if args else "")
        await ctx.answer()
        ok, msg = adv.apply_suggestions(sid, uid)
        await ctx.show(*sp.adv_item(uid, "s", str(sid), f"{OK if ok else NO} {esc(msg)}", HOME))
        return
    if act == "advr":
        sid = _to_int(args[0] if args else "")
        await ctx.answer()
        ok = adv.reject(sid, uid)
        await ctx.show(*sp.adv_item(
            uid, "s", str(sid), f"{OK} پیشنهاد رد شد." if ok else f"{NO} این پیشنهاد در انتظار نیست.", HOME,
        ))
        return

    # ---------- عملکرد سیگنال‌ها ----------
    if act == "perf":
        status = args[0] if args else "all"
        period = args[1] if len(args) > 1 else "a"
        pg = args[2] if len(args) > 2 else "0"
        await ctx.answer()
        await ctx.show(*sp.perf_page(uid, "s", status, period, pg, _perf_symbol(ctx), "", HOME))
        return
    if act == "perfs":
        await ctx.answer()
        await _prompt(ctx, "uperf_sym", "s:perf:all:a:0", "اسم نماد را ارسال کنید؛ مثلاً BTC")
        return
    if act == "perfc":
        ctx.user_data.pop("perf_sym", None)
        await ctx.answer()
        await ctx.show(*sp.perf_page(uid, "s", "all", "a", "0", "", "", HOME))
        return
    if act == "perfx":
        status = args[0] if args else "all"
        period = args[1] if len(args) > 1 else "a"
        await ctx.answer()
        await ctx.show(*sp.perf_clear_confirm(uid, "s", status, period, _perf_symbol(ctx)))
        return
    if act == "perfxd":
        status = args[0] if args else "all"
        period = args[1] if len(args) > 1 else "a"
        await ctx.answer()
        n = perf.delete_filtered(status, period, _perf_symbol(ctx), uid=uid)
        await ctx.show(*sp.perf_page(uid, "s", status, period, "0", _perf_symbol(ctx), f"{OK} {n} رکورد پاک شد.", HOME))
        return

    await ctx.answer()
    await ctx.show(*page_home(uid))


# ============================================================================
# ورودی‌های متنی / فایل کاربر
# ============================================================================
Result = Tuple[bool, object]


def _t_param(uid: int, st: dict, raw: str) -> Result:
    key = st["key"]
    side = st.get("side")
    spec = config.PARAMS.get(key)
    if spec is None or not _param_ok(spec) or key not in config.USER_KEYS:
        return False, "این پارامتر قابل تغییر نیست."
    d = _d(uid)
    val = config.parse_number(spec, raw)
    if val is None:
        return False, "فقط عدد معتبر ارسال کنید." if spec.kind == "float" else "فقط عدد صحیح ارسال کنید."
    err = config.validate_param(key, val, lambda k: d.get_e_float(k, side))
    if err:
        return False, err
    d.set_e(key, str(int(val)) if spec.kind == "int" else config.fmt_number(float(val)), side)
    if key == "send_interval":
        reset_schedule(uid)
    note = f"{OK} «{esc(spec.label)}» ذخیره شد."
    back = st["back"]
    if back == "s:rsk":
        return True, page_risk(uid, note)
    if back == "s:snd":
        return True, page_send(uid, note)
    return True, page_engine_params(uid, spec.group, note)


def _t_import(uid: int, raw_bytes: bytes) -> Result:
    ok, message = engine_io.import_settings(raw_bytes, _d(uid))
    if not ok:
        return False, message
    return True, page_eng(uid, f"{OK} تنظیمات وارد شد.\n{esc(message)}")


async def _respond(ctx: Ctx, st: dict, ok: bool, payload) -> None:
    if ok:
        if not st.get("keep"):
            ctx.user_data.pop("state", None)
        text, markup = payload
        await ctx.show_input(st.get("mid"), text, markup)
        return
    await ctx.show_input(st.get("mid"), f"{NO} {esc(payload)}\n\n{st['prompt']}", kb(back_row(st["back"])))


def _blocked(ctx: Ctx) -> bool:
    return not subscription.settings_allowed(ctx.uid)


async def handle_user_text(ctx: Ctx) -> bool:
    """متن کاربر در حالت‌های ورودی پنل تنظیمات. True یعنی پردازش شد."""
    st = ctx.user_data.get("state")
    if not st or st.get("kind") not in USER_STATE_KINDS:
        return False
    uid = ctx.uid
    if _blocked(ctx):
        ctx.user_data.pop("state", None)
        await ctx.show(*subscription.need_sub_page(uid, SUB_TEXT))
        return True
    msg = ctx.update.effective_message
    raw = (msg.text or "") if msg else ""
    kind = st["kind"]
    try:
        if kind == "uparam":
            ok, payload = _t_param(uid, st, raw)
        elif kind == "uperf_sym":
            q = raw.strip().upper().replace("USDT", "").strip("/: ")
            if not q or len(q) > 20 or not q.replace("_", "").isalnum():
                ok, payload = False, "اسم نماد معتبر ارسال کنید؛ مثلاً BTC"
            else:
                ctx.user_data["perf_sym"] = q
                ok, payload = True, sp.perf_page(uid, "s", "all", "a", "0", q, "", HOME)
        else:  # uimport (متن JSON)
            text = raw.strip()
            if not text.startswith("{"):
                ok, payload = False, "فایل JSON را به‌صورت Document ارسال کنید (یا متن JSON را که با { شروع می‌شود paste کنید)."
            else:
                ok, payload = _t_import(uid, text.encode("utf-8"))
    except Exception:
        log.exception("user text handler failed")
        ok, payload = False, "یک مشکل پیش آمد، دوباره امتحان کنید."
    await _respond(ctx, st, ok, payload)
    return True


async def handle_user_media(ctx: Ctx) -> bool:
    """فایل JSON کاربر در حالت Import. True یعنی پردازش شد."""
    st = ctx.user_data.get("state")
    msg = ctx.update.effective_message
    if not st or msg is None or st.get("kind") != "uimport":
        return False
    uid = ctx.uid
    if _blocked(ctx):
        ctx.user_data.pop("state", None)
        await ctx.show(*subscription.need_sub_page(uid, SUB_TEXT))
        return True
    doc = msg.document
    try:
        if doc is None:
            ok, payload = False, "فایل JSON را به‌صورت Document ارسال کنید."
        elif doc.file_size and doc.file_size > engine_io.MAX_FILE_BYTES:
            ok, payload = False, "فایل بسیار بزرگ است (حداکثر ۲۰۰ کیلوبایت)."
        else:
            try:
                f = await ctx.bot.get_file(doc.file_id)
                data = bytes(await f.download_as_bytearray())
            except TelegramError:
                ok, payload = False, "دریافت فایل ممکن نشد، دوباره ارسال کنید."
            else:
                ok, payload = _t_import(uid, data)
    except Exception:
        log.exception("user media handler failed")
        ok, payload = False, "یک مشکل پیش آمد، دوباره امتحان کنید."
    await _respond(ctx, st, ok, payload)
    return True
