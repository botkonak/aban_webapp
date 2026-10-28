"""
پنل ادمین آبان ترید (تشخیص خودکار با UID).
قانون طلایی: هر صفحه‌ای که ورودی می‌گیرد یا تنظیم دارد، دکمه «برگشت» پایین صفحه دارد.
"""
from __future__ import annotations

import hashlib
import logging
import re
import time
from typing import Callable, Dict, List, Optional, Tuple

from telegram.error import TelegramError

import admin_extra
import analyst
import blocks as blk
import broadcaster
import config
import engine_io
import fmt
import force_subscribe
import payments
import subscription
import support
import templates as tpl
from ai_engine import ai, usage_profile
from database import db
from signal_engine import engine
from ui import (
    NO, OK, WARN, Ctx, back_row, btn, chunk, esc, fmt_duration, jalali_date, kb,
    message_caption_markers, message_text_with_ticks, on_off,
    STYLE_GREEN, STYLE_RED
)

log = logging.getLogger("admin")

Page = Tuple[str, Optional[object]]

ADMIN_HOME = (
    f"سلام ادمین عزیز، به پنل {config.BRAND} خوش آمدید!\n\n"
    "از دکمه‌های زیر استفاده کنید:"
)


def _note(note: str) -> str:
    return f"{note}\n\n" if note else ""


def _val(key: str, side: Optional[str] = None) -> str:
    spec = config.PARAMS[key]
    v = db.get_e_float(key, side)
    s = str(int(v)) if spec.kind == "int" else config.fmt_number(v)
    return f"{s}{spec.unit}" if spec.unit in ("%", "X") else (f"{s} {spec.unit}" if spec.unit else s)


def _view_side() -> Optional[str]:
    """جهت در حال ویرایش تنظیمات موتورها؛ در حالت مشترک None."""
    if db.eng_mode() != "separate":
        return None
    v = (db.get("eng_view") or "LONG").upper()
    return v if v in ("LONG", "SHORT") else "LONG"


def _side_for(key: str) -> Optional[str]:
    return _view_side() if config.is_engine_key(key) else None


def _side_row(ret: str) -> List:
    """دکمه‌ی دو حالته‌ی LONG / SHORT (فقط در حالت جداگانه)؛ ret = صفحه‌ای که بعد از تغییر باز می‌شود."""
    side = _view_side()
    if side is None:
        return []
    return [
        btn(f"{OK} {s}" if s == side else s, f"a:engs:{s}:{ret}") for s in ("LONG", "SHORT")
    ]


def _side_title(side: Optional[str]) -> str:
    return f"\nپروفایل در حال ویرایش: {side}" if side else ""


def _bot_state() -> str:
    return f"{OK} روشن" if db.get_bool("bot_on") else f"{NO} خاموش"


# ============================================================================
# صفحه‌ها
# ============================================================================
def page_home(*_a) -> Page:
    on = db.get_bool("bot_on")
    sig_on = db.get_bool("admin_sig_on")
    lines = [ADMIN_HOME, "", f"وضعیت ربات: {_bot_state()}"]
    if not on:
        lines.append("تا وقتی ربات خاموش است هیچ سیگنالی صادر نمی‌شود.")
    
    lines.append(f"وضعیت سیگنال ادمین (کانال): {'روشن' if sig_on else 'خاموش'}")

    from webapp.activation import admin_panel_url

    # تعیین رنگ دکمه بر اساس وضعیت
    sig_style = STYLE_GREEN if sig_on else STYLE_RED
    sig_label = "سیگنال کانال: روشن" if sig_on else "سیگنال کانال: خاموش"

    rows = [
        [btn("ورود به پنل مدیریت وب‌اپ", url=admin_panel_url(), style="primary")],
        [btn(f"ربات: {_bot_state()}", "a:bot")],
        [btn(sig_label, "a:adminsig", style=sig_style)],
        [btn("تنظیمات", "a:set")],
        [btn("کانال‌های عضویت اجباری", "a:ch")],
        [btn("ارسال سیگنال فوری", "a:sig"), btn("آنالیز با AI", "a:ana")],
        [btn("استراتژی‌های آماده", "a:stg")],
        [btn("عملکرد سیگنال‌ها", "a:perf:all:a:0"), btn("مشاور موتورها", "a:adv")],
        [btn("کانال‌های قیمت", "a:pc"), btn("قالب‌های قیمت و آنالیز", "a:mt")],
    ]
    return "\n".join(lines), kb(*rows)

def page_settings(*_a) -> Page:
    text = "تنظیمات\n\nیکی از بخش‌ها را انتخاب کنید:"
    return text, kb(
        [btn("تنظیمات تایم‌فریم", "a:tf")],
        [btn("تنظیمات موتورها", "a:eng")],
        [btn("مدیریت کلیدهای هوش مصنوعی", "a:aik")],
        [btn("تنظیمات ریسک و اهرم", "a:rsk")],
        [btn("تنظیمات ارسال", "a:snd")],
        [btn("ارسال سیگنال به کانال", "a:chs")],
        [btn("هشدار قبل از سیگنال", "a:pre")],
        [btn("فیلتر نمادها", "a:sf")],
        [btn("قیمت زنده‌ی سیگنال", "a:live")],
        [btn("تنظیمات قالب", "a:tpl")],
        [btn("وضعیت بازار در منوی اصلی", "a:mkt")],
        [btn("پاسخ خودکار هوشمند پشتیبانی", "a:aisup")],
        [btn("تنظیمات Keep-Alive", "a:ka")],
        back_row("a:home"),
    )


# ---------------- تایم‌فریم ----------------
def page_tf(note: str = "", *_a) -> Page:
    mode = db.eng_mode()
    side = _view_side()
    e, f = db.get_e("tf_entry", side), db.get_e("tf_final", side)
    e_on, f_on = db.get_e_bool("tf_entry_on", side), db.get_e_bool("tf_final_on", side)
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
        return [btn(f"{OK} {o}" if o == cur else o, f"a:{cb}:{o}") for o in options]

    rows = [
        [
            btn(f"{OK} مشترک" if mode == "global" else "مشترک", "a:engm:global:tf"),
            btn(f"{OK} جداگانه" if mode == "separate" else "جداگانه", "a:engm:separate:tf"),
        ],
    ]
    sr = _side_row("tf")
    if sr:
        rows.append(sr)
    rows += [
        [btn("تایم‌فریم اولیه (ورود)", "a:noop")],
        opts(config.ENTRY_TF_OPTIONS, e, "tfe"),
        [btn(f"تایم‌فریم اولیه: {on_off(e_on)}", "a:tft:e")],
        [btn("تایم‌فریم نهایی (تأیید)", "a:noop")],
        opts(config.FINAL_TF_OPTIONS, f, "tff"),
        [btn(f"تایم‌فریم نهایی: {on_off(f_on)}", "a:tft:f")],
        back_row("a:set"),
    ]
    return text, kb(*rows)


# ---------------- موتورها ----------------
ENGINE_HELP: Dict[str, str] = {
    "st": "جهت روند با Supertrend. حداقل یکی از Supertrend یا EMA باید همیشه روشن بماند.",
    "ema": "جهت روند با EMA سریع و کند. حداقل یکی از Supertrend یا EMA باید همیشه روشن بماند.",
    "rsi": "تأیید مومنتوم و وتوی اشباع خرید / فروش.",
    "srsi": "تأیید تقاطع K و D در Stochastic RSI.",
    "obv": "تأیید جریان حجم با OBV.",
    "adx": "حداقل قدرت روند. وسط فشردگی (Squeeze) این شرط نادیده گرفته می‌شود.",
    "atr": (
        "فیلتر حداقل نوسان و حد ضرر / حد سود بر اساس ATR. "
        "اگر خاموش باشد حد ضرر و حد سود با درصد ثابت (پارامترهای «ثابت») حساب می‌شود."
    ),
    "vol": "فیلتر حداقل حجم نسبت به میانگین. وسط فشردگی نادیده گرفته می‌شود.",
    "sq": (
        "پیش‌فرض (زمان صدور = 1): سیگنال «قبل از انفجار» صادر می‌شود؛ یعنی وقتی بازار فشرده شده (یا تازه از فشردگی درآمده) "
        "و هنوز شکست اتفاق نیفتاده و قیمت سمت درست جعبه‌ی فشردگی هست. شکستِ انجام‌شده یعنی دیر شده و سیگنال نمی‌دهد. "
        "اگر زمان صدور = 0 باشد، فقط بعد از شکست همراه با حجم سیگنال می‌دهد. "
        "فشردگی نباشد یعنی سیگنال هم نیست."
    ),
    "div": "واگرایی مثبت RSI تأیید قوی برای LONG و واگرایی منفی تأیید قوی برای SHORT (امتیاز بیشتر).",
    "sr": "اگر قیمت نزدیک مقاومت قوی باشد سیگنال LONG و اگر نزدیک حمایت قوی باشد سیگنال SHORT داده نمی‌شود.",
    "poc": (
        "وتو (قیمت در جهت مخالف POC سیگنال نمی‌گیرد)، امتیاز، POC تایم‌فریم نهایی "
        "و حد ضرر پویا پشت POC."
    ),
    "lq": (
        "جاروب نقدینگی: عبور سریع قیمت از سقف یا کف تأییدشده و بازگشت به زیر آن، همراه با سایه‌ی بلند و حجم بالا. "
        "جاروب سقف سیگنال SHORT و جاروب کف سیگنال LONG محسوب می‌شود. "
        "هم‌جهت: امتیاز مثبت | خلاف جهت: کاهش امتیاز و وتو."
    ),
    "fvg": (
        "شکاف ارزش منصفانه: شکاف قیمتی ناشی از حرکت سریع که قیمت معمولاً به آن بازمی‌گردد. "
        "وقتی قیمت داخل شکافِ معتبر باشد، جهت شکاف (صعودی = LONG، نزولی = SHORT) تعیین‌کننده است. "
        "هم‌جهت: امتیاز مثبت | خلاف جهت: کاهش امتیاز و وتو."
    ),
    "sm": (
        "پول هوشمند: کندل آخر با حجم بسیار بالا و بدنه‌ی قوی نشان‌دهنده‌ی ورود نهادهاست. "
        "هم‌جهت: امتیاز مثبت | خلاف جهت: فقط کاهش امتیاز."
    ),
    "mb": (
        "شکست مومنتوم: تقاطع EMA سریع و کند در کندل آخر همراه با تأیید RSI و حجم. "
        "هم‌جهت: امتیاز مثبت | خلاف جهت: فقط کاهش امتیاز."
    ),
}


def page_eng(note: str = "", *_a) -> Page:
    mode = db.eng_mode()
    side = _view_side()
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
        on = db.get_e_bool(key, side)
        lines.append(f"{title}: {on_off(on)}")
        toggles.append(btn(f"{title}: {OK if on else NO}", f"a:engt:{eid}"))
    lines += [
        "",
        "برای روشن / خاموش کردن روی هر موتور بزنید. برای تغییر پارامترها از لیست پایین موتور را انتخاب کنید.",
    ]
    if mode == "global":
        lines.append("در حالت مشترک تغییرات روی هر دو جهت LONG و SHORT اعمال می‌شود.")
    else:
        lines.append("در حالت جداگانه هر جهت تنظیمات مستقل خودش را دارد؛ با دکمه‌ی LONG / SHORT بین دو پروفایل جابه‌جا شوید.")
    params = [btn(config.ENGINES[eid][0], f"a:engp:{eid}") for eid in config.ENGINES]
    rows = [
        [
            btn(f"{OK} مشترک" if mode == "global" else "مشترک", "a:engm:global"),
            btn(f"{OK} جداگانه" if mode == "separate" else "جداگانه", "a:engm:separate"),
        ],
    ]
    sr = _side_row("eng")
    if sr:
        rows.append(sr)
    rows += [
        *chunk(toggles, 2),
        [btn("پارامترهای موتورها", "a:noop")],
        *chunk(params, 2),
        [btn("Import", "a:engi"), btn("Export", "a:engx")],
        back_row("a:set"),
    ]
    return _note(note) + "\n".join(lines), kb(*rows)


def page_engine_params(eid: str = "", note: str = "") -> Page:
    if eid not in config.ENGINES:
        return page_eng()
    if eid == "cd":
        return page_candles(note)
    if eid == "ai":
        return page_ai(note)
    side = _view_side()
    title, key = config.ENGINES[eid]
    specs = [s for s in config.PARAM_SPECS if s.group == eid]
    on = db.get_e_bool(key, side)
    lines = [f"پارامترهای {esc(title)}{_side_title(side)}", "", f"وضعیت: {on_off(on)}"]
    if ENGINE_HELP.get(eid):
        lines += ["", esc(ENGINE_HELP[eid])]
    lines.append("")
    for s in specs:
        lines.append(f"{esc(s.label)}: {esc(_val(s.key, side))}")
    lines += ["", "برای تغییر هر مقدار، روی آن بزنید و عدد جدید را ارسال کنید."]
    rows = []
    sr = _side_row(f"engp:{eid}")
    if sr:
        rows.append(sr)
    rows.append([btn(f"{title}: {OK if on else NO}", f"a:engt:{eid}:p")])
    rows += [[btn(f"{s.label}: {_val(s.key, side)}", f"a:pr:{s.key}")] for s in specs]
    rows.append(back_row("a:eng"))
    return _note(note) + "\n".join(lines), kb(*rows)


def page_candles(note: str = "") -> Page:
    side = _view_side()
    on = db.get_e_bool("en_candle", side)
    specs = [s for s in config.PARAM_SPECS if s.group == "cd"]
    lines = [
        f"الگوهای کندلی{_side_title(side)}", "",
        f"وضعیت: {on_off(on)}", "",
        "الگوی هم‌جهت با سیگنال تأیید قوی (امتیاز بیشتر) و الگوی مخالف هشدار و کاهش امتیاز حساب می‌شود.", "",
        "الگوهای فعال:",
    ]
    pat_btns = []
    for pid, (bull, bear) in config.CANDLE_PATTERNS.items():
        pon = db.get_e_bool(f"cdp_{pid}", side)
        lines.append(f"{bull} / {bear}: {on_off(pon)}")
        pat_btns.append(btn(f"{bull} / {bear}: {OK if pon else NO}", f"a:cdt:{pid}"))
    lines.append("")
    for s in specs:
        lines.append(f"{esc(s.label)}: {esc(_val(s.key, side))}")
    rows = []
    sr = _side_row("engp:cd")
    if sr:
        rows.append(sr)
    rows.append([btn(f"الگوهای کندلی: {OK if on else NO}", "a:engt:cd:p")])
    rows += [[b] for b in pat_btns]
    rows += [[btn(f"{s.label}: {_val(s.key, side)}", f"a:pr:{s.key}")] for s in specs]
    rows.append(back_row("a:eng"))
    return _note(note) + "\n".join(lines), kb(*rows)


# ---------------- هوش مصنوعی ----------------
def _mask(key: str) -> str:
    return key[:4] + "..." + key[-4:] if len(key) > 10 else "****"


def _status_icon(k) -> str:
    return {"ok": OK, "off": NO, "err": WARN}[ai.key_status(k)]


def page_ai(note: str = "") -> Page:
    side = _view_side()
    master = db.get_bool("ai_on")
    eng_on = db.get_e_bool("en_ai", side)
    model = db.get_e("ai_model", side) or config.AI_MODELS[0]
    keys = db.ai_keys()
    healthy = len(ai.available_keys())
    st = f"{OK} فعال" if master else f"{NO} غیرفعال"
    lines = [
        f"تنظیمات هوش مصنوعی{_side_title(side)}", "",
        f"سوییچ اصلی هوش مصنوعی: {st}",
        f"موتور هوش مصنوعی: {on_off(eng_on)}",
        f"مدل: {esc(model)}",
        f"کلیدهای سالم: {healthy} از {len(keys)}",
    ]
    no_acc = [k for k in keys if not k.account_id]
    if no_acc and not (db.get("ai_url") or config.AI_WORKER_URL).strip():
        lines.append(
            f"{WARN} {len(no_acc)} کلید Account ID ندارند و آدرس پیش‌فرض (Fallback) هم تنظیم نشده است؛ این کلیدها استفاده نمی‌شوند "
            "(از صفحه‌ی مدیریت کلیدها Account ID هر کلید را وارد کنید)."
        )
    if ai.temporarily_off():
        lines.append(f"{WARN} هوش مصنوعی به‌خاطر خطای همه‌ی کلیدها تا {fmt_duration(ai.off_until() - time.time())} دیگر موقتاً خاموش است.")
    if master and not keys:
        lines.append(f"{WARN} هنوز هیچ کلیدی ثبت نشده است؛ تا کلید نباشد هوش مصنوعی کاری نمی‌کند.")
    lines += ["", "قابلیت‌ها:"]
    feat_btns = []
    for fid, (skey, title) in config.AI_FEATURES.items():
        on = db.get_e_bool(skey, side)
        lines.append(f"{title}: {on_off(on)}")
        feat_btns.append(btn(f"{title}: {OK if on else NO}", f"a:aif:{fid}"))
    lines.append("")
    for s in (s for s in config.PARAM_SPECS if s.group == "ai"):
        lines.append(f"{esc(s.label)}: {esc(_val(s.key, side))}")
    lines.append(f"مصرف امروز: {ai.used_today(usage_profile(side))} درخواست")
    if db.eng_mode() == "separate":
        lines.append(f"تنظیمات هوش مصنوعی: {'جداگانه برای هر جهت' if db.get_bool('ai_sep') else 'مشترک بین دو جهت'}")
    lines += [
        "",
        "هوش مصنوعی فقط برای سیگنال‌هایی با امتیاز بالاتر از آستانه فراخوانی می‌شود و هیچ‌وقت جلوی ارسال سیگنال را نمی‌گیرد؛ "
        "اگر پاسخی دریافت نشود، سیگنال بدون هوش مصنوعی ارسال می‌شود.",
    ]
    rows = []
    sr = _side_row("engp:ai")
    if sr:
        rows.append(sr)
    rows.append([btn(f"سوییچ اصلی هوش مصنوعی: {OK if master else NO}", "a:aiont")])
    rows.append([btn(f"موتور هوش مصنوعی: {OK if eng_on else NO}", "a:engt:ai:p")])
    rows += [[b] for b in feat_btns]
    rows.append([btn("مدل هوش مصنوعی", "a:noop")])
    rows += chunk([
        btn(f"{OK} {m.split('/')[-1]}" if m == model else m.split("/")[-1], f"a:aim:{i}")
        for i, m in enumerate(db.ai_models())
    ], 2)
    rows.append([btn("مدل دلخواه", "a:aimc")])
    rows.append([btn("مدل‌های مجاز برای کاربران", "a:aimm")])
    rows += [[btn(f"{s.label}: {_val(s.key, side)}", f"a:pr:{s.key}")] for s in config.PARAM_SPECS if s.group == "ai"]
    if db.eng_mode() == "separate":
        rows.append([btn(f"تنظیمات جداگانه برای هر جهت: {OK if db.get_bool('ai_sep') else NO}", "a:aisep")])
    rows.append([btn("مدیریت کلیدهای هوش مصنوعی", "a:aik")])
    rows.append(back_row("a:eng"))
    return _note(note) + "\n".join(lines), kb(*rows)


def page_ai_models(note: str = "", *_a) -> Page:
    models = db.ai_models()
    lines = [
        f"مدل‌های مجاز هوش مصنوعی برای کاربران ({len(models)} از {config.AI_MAX_MODELS})", "",
        "کاربران فقط می‌توانند از بین همین مدل‌ها یکی را انتخاب کنند (بقیه‌ی تنظیمات AI فقط دست ادمین است). "
        "اولین مدل لیست، مدل پیش‌فرض کاربران است.",
        "اگر مدلی را که کاربری انتخاب کرده حذف کنید، مدل آن کاربر به اولین مدل لیست تغییر می‌کند و به او اطلاع داده می‌شود.", "",
    ]
    for i, m in enumerate(models, 1):
        lines.append(f"{i}) {esc(m)}")
    rows = [[btn(f"حذف {i}) {m.split('/')[-1]}", f"a:aimx:{i - 1}")] for i, m in enumerate(models, 1)]
    if len(models) < config.AI_MAX_MODELS:
        rows.append([btn("افزودن مدل", "a:aima")])
    rows.append(back_row("a:engp:ai"))
    return _note(note) + "\n".join(lines), kb(*rows)


def page_ai_model_delete(idx: str = "0", *_a) -> Page:
    models = db.ai_models()
    i = _to_int(idx, -1)
    if not 0 <= i < len(models):
        return page_ai_models(f"{NO} این مدل پیدا نشد.")
    if len(models) <= 1:
        return page_ai_models(f"{NO} حداقل یک مدل باید در لیست باقی بماند.")
    n = len(db.user_ids_with_models([models[i]]))
    return (
        f"آیا از حذف مدل «{esc(models[i])}» از لیست مجاز اطمینان دارید؟\n"
        f"{n} کاربر این مدل را انتخاب کرده‌اند؛ مدل آن‌ها به «{esc(models[1 if i == 0 else 0])}» تغییر می‌کند و به آن‌ها اطلاع داده می‌شود.",
        kb([btn(f"{OK} بله، حذف کنید", f"a:aimxd:{i}")], back_row("a:aimm")),
    )


def page_ai_keys(note: str = "", *_a) -> Page:
    keys = {k.slot: k for k in db.ai_keys()}
    cooldown = db.get_int("ai_key_cooldown")
    url = (db.get("ai_url") or config.AI_WORKER_URL).strip()
    lines = [
        f"مدیریت کلیدهای هوش مصنوعی (حداکثر {config.AI_MAX_KEYS} کلید)", "",
        "هر کلید Account ID مخصوص خودش را دارد و آدرس API از همان ساخته می‌شود.",
        f"آدرس پیش‌فرض (Fallback، فقط برای کلیدهای بدون Account ID): {esc(url) if url else 'تنظیم نشده است'}",
        f"مدت غیرفعال‌سازی موقت کلید خطادار: {cooldown} دقیقه", "",
        f"{OK} فعال | {NO} غیرفعال | {WARN} خطا (محدودیت نرخ یا شبکه؛ موقتاً کنار گذاشته شده است)", "",
        "کلیدها به‌صورت چرخشی استفاده می‌شوند. اگر یک کلید خطا بدهد، برای ادمین هشدار می‌آید، همان کلید موقتاً کنار می‌رود و ربات "
        "به کلید بعدی سوییچ می‌کند. اگر همه‌ی کلیدها خطا بدهند، هشدار نهایی می‌آید و هوش مصنوعی موقتاً خاموش می‌شود.",
    ]
    if ai.temporarily_off():
        lines += ["", f"{WARN} هوش مصنوعی تا {fmt_duration(ai.off_until() - time.time())} دیگر موقتاً خاموش است."]
    slot_btns = []
    for slot in range(1, config.AI_MAX_KEYS + 1):
        k = keys.get(slot)
        label = (
            f"{_status_icon(k)} {slot}) {k.name}" + ("" if k.account_id else " (بدون ID)") if k else f"{slot}) خالی"
        )
        slot_btns.append(btn(label, f"a:aiks:{slot}"))
    rows = chunk(slot_btns, 2)
    if ai.temporarily_off() or any(k.err_until > time.time() for k in keys.values()):
        rows.append([btn("فعال‌سازی مجدد دستی", "a:aires")])
    rows.append([btn("آدرس پیش‌فرض سرویس (Fallback)", "a:aiurl")])
    rows.append([btn(f"مدت غیرفعال‌سازی موقت: {cooldown} دقیقه", "a:pr:ai_key_cooldown")])
    rows.append(back_row("a:engp:ai"))
    return _note(note) + "\n".join(lines), kb(*rows)


def page_ai_slot(slot: str = "0", note: str = "") -> Page:
    try:
        n = int(slot)
    except ValueError:
        n = 0
    if not 1 <= n <= config.AI_MAX_KEYS:
        return page_ai_keys(f"{NO} این اسلات وجود ندارد.")
    k = db.ai_key(n)
    if k is None:
        return f"{_note(note)}اسلات {n}\n\nاین اسلات خالی است.", kb(
            [btn("افزودن کلید", f"a:aikc:{n}")], back_row("a:aik")
        )
    st = ai.key_status(k)
    st_txt = {"ok": f"{OK} فعال", "off": f"{NO} غیرفعال", "err": f"{WARN} خطا (موقتاً کنار گذاشته شده است)"}[st]
    lines = [
        f"اسلات {n}: {esc(k.name)}", "",
        f"وضعیت: {st_txt}",
        f"کلید: <code>{esc(_mask(k.api_key))}</code>",
        f"🆔 Account ID: <code>{esc(k.account_id)}</code>" if k.account_id else f"🆔 Account ID: تنظیم نشده (آدرس پیش‌فرض)",
        f"درخواست موفق: {k.ok_count} | خطا: {k.fail_count}",
    ]
    if st == "err":
        lines.append(f"بازگشت خودکار تا: {fmt_duration(k.err_until - time.time())} دیگر")
    if k.last_error:
        lines.append(f"آخرین خطا: {esc(k.last_error)}")
    return _note(note) + "\n".join(lines), kb(
        [btn("تست اتصال", f"a:aikv:{n}")],
        [btn("ویرایش کلید", f"a:aikc:{n}"), btn("تغییر نام", f"a:aikn:{n}")],
        [btn("Account ID", f"a:aika:{n}")],
        [btn("غیرفعال کردن" if k.enabled else "فعال کردن", f"a:aikt:{n}")],
        [btn("حذف کلید", f"a:aikx:{n}")],
        back_row("a:aik"),
    )


def page_ai_slot_delete(slot: str = "0", *_a) -> Page:
    try:
        n = int(slot)
    except ValueError:
        n = 0
    k = db.ai_key(n)
    if k is None:
        return page_ai_keys(f"{NO} این اسلات خالی است.")
    return f"آیا از حذف کلید «{esc(k.name)}» (اسلات {n}) اطمینان دارید؟", kb(
        [btn(f"{OK} بله، حذف کنید", f"a:aikxd:{n}")], back_row(f"a:aiks:{n}")
    )


# ---------------- ریسک و اهرم ----------------
def page_risk(note: str = "", *_a) -> Page:
    keys = ["risk_min", "risk_max", "lev_min", "lev_max"]
    lines = ["تنظیمات ریسک و اهرم", ""]
    for k in keys:
        lines.append(f"{esc(config.PARAMS[k].label)}: {esc(_val(k))}")
    lines += ["", "ریسک و اهرم هر سیگنال به‌صورت خودکار و بر اساس نوسان نماد و قدرت سیگنال، داخل همین بازه‌ها تعیین می‌شود."]
    rows = [[btn(f"{config.PARAMS[k].label}: {_val(k)}", f"a:pr:{k}")] for k in keys]
    return _note(note) + "\n".join(lines), kb(*rows, back_row("a:set"))


# ---------------- ارسال ----------------
def page_send(note: str = "", *_a) -> Page:
    dd = db.get_bool("dedupe_on")
    text = (
        "تنظیمات ارسال\n\n"
        f"فاصله ارسال سیگنال: {esc(_val('send_interval'))}\n"
        f"جلوگیری از تکرار ۲۴ ساعته: {on_off(dd)}"
    )
    return _note(note) + text, kb(
        [btn(f"فاصله ارسال: {_val('send_interval')}", "a:pr:send_interval")],
        [btn(f"جلوگیری از تکرار ۲۴ ساعته: {on_off(dd)}", "a:sndt")],
        back_row("a:set"),
    )


# ---------------- ارسال سیگنال به کانال ----------------
def page_chs(note: str = "", *_a) -> Page:
    cid, title, tag = db.get("ch_sig_id"), db.get("ch_sig_title"), db.get("ch_sig_tag")

    def st(side: str) -> str:
        return OK if db.get_template(side).strip() else NO

    lines = [
        "ارسال سیگنال به کانال", "",
        f"کانال: {esc(title or cid)} (<code>{esc(cid)}</code>)" if cid else "کانال: تعریف نشده است",
        f"آیدی انتهای هر پست: {esc(tag) if tag else '-'}",
        f"قالب کانال LONG: {st('CH_LONG')} | قالب کانال SHORT: {st('CH_SHORT')}", "",
        "سیگنال‌های ادمین (با تنظیمات موتورهای ادمین) فقط در همین کانال منتشر می‌شوند و در چت خصوصی هیچ کاربری ارسال نمی‌شوند؛ "
        "هر کاربر سیگنال مستقل خودش را با تنظیمات خودش دریافت می‌کند.",
        "این کانال به کانال‌های عضویت اجباری ربطی ندارد. ربات باید در کانال ادمین (با دسترسی ارسال پیام) باشد.",
        "اگر قالب کانال یک جهت خالی باشد، از قالب همان جهت در ربات استفاده می‌شود (بدون {lastprice}، چون قیمت داخل کانال به‌روز نمی‌شود).",
    ]
    if not cid:
        lines += ["", f"{WARN} کانالی تعیین نشده است؛ تا کانال تعیین نشود سیگنال ادمین ارسال نمی‌شود."]
    rows = [
        [btn("تعیین / تغییر کانال", "a:chse")],
        [btn("ویرایش آیدی انتهای پست", "a:chst")],
        [btn("قالب کانال LONG", "a:tplv:CH_LONG"), btn("قالب کانال SHORT", "a:tplv:CH_SHORT")],
    ]
    if cid:
        rows.append([btn("حذف کانال سیگنال", "a:chsx")])
    rows.append(back_row("a:set"))
    return _note(note) + "\n".join(lines), kb(*rows)


# ---------------- هشدار قبل از سیگنال ----------------
def page_pre(note: str = "", *_a) -> Page:
    on = db.get_bool("pre_alert_on")
    lines = [
        "هشدار قبل از سیگنال", "",
        f"وضعیت: {on_off(on)}",
        f"حداقل امتیاز برای ارسال هشدار: {esc(_val('pre_min_score'))}",
        f"فاصله هشدار تکراری برای یک نماد: {esc(_val('pre_cooldown'))}",
        f"حداقل فاصله بین دو هشدار: {esc(_val('pre_gap'))}", "",
        "وقتی نمادی فقط یک شرط تا سیگنال شدن کم داشته باشد و امتیازش از حد بالا باشد، پیام آماده‌باش ارسال می‌شود. نمونه:", "",
        "<pre>آماده‌باش! احتمال سیگنال جدید.\nنماد: BTC\nجهت احتمالی: LONG</pre>",
        "این هشدار فقط برای ادمین ارسال می‌شود (کاربران هشدار آماده‌باش ندارند).",
    ]
    return _note(note) + "\n".join(lines), kb(
        [btn(f"هشدار قبل از سیگنال: {OK if on else NO}", "a:pret")],
        [btn(f"حداقل امتیاز: {_val('pre_min_score')}", "a:pr:pre_min_score")],
        [btn(f"فاصله هشدار تکراری: {_val('pre_cooldown')}", "a:pr:pre_cooldown")],
        [btn(f"حداقل فاصله بین دو هشدار: {_val('pre_gap')}", "a:pr:pre_gap")],
        back_row("a:set"),
    )


# ---------------- محدودیت درخواست و ضد اسپم ----------------
def page_rl(note: str = "", *_a) -> Page:
    keys = ["rl_max", "rl_window", "rl_block", "rl_global", "as_max", "as_window", "as_block"]
    lines = [
        "محدودیت درخواست و ضد اسپم", "",
        "محدودیت درخواست سیگنال:",
        f"{esc(config.PARAMS['rl_max'].label)}: {esc(_val('rl_max'))} در {esc(_val('rl_window'))}",
        f"مدت محدودیت موقت: {esc(_val('rl_block'))}",
        f"سقف کل درخواست همه‌ی کاربرها: {esc(_val('rl_global'))} در دقیقه", "",
        "ضد اسپم:",
        f"بیشتر از {esc(_val('as_max'))} پیام یا کلیک در {esc(_val('as_window'))} = مسدودسازی موقت به مدت {esc(_val('as_block'))}", "",
        "تلگرام آی‌پی کاربر را به ربات نمی‌دهد، برای همین محدودیت سطح آی‌پی ممکن نیست؛ در عوض، علاوه بر محدودیت هر کاربر، "
        "یک سقف کل برای درخواست سیگنال همه‌ی کاربرها هم اعمال می‌شود.",
    ]
    rows = [[btn(f"{config.PARAMS[k].label}: {_val(k)}", f"a:pr:{k}")] for k in keys]
    rows.append(back_row("a:set"))
    return _note(note) + "\n".join(lines), kb(*rows)


# ---------------- خرید اشتراک ----------------
def page_sub(note: str = "", *_a) -> Page:
    mode = db.get("sub_mode")
    on = mode != "off"
    
    # آدرس‌ها
    wallet_manual = db.get("sub_wallet").strip()
    wallet_bsc = db.get("bsc_wallet").strip()
    
    lines = [
        "خرید اشتراک", "",
        f"وضعیت کلی: {OK if on else NO}",
        f"حالت فعال: {mode}",
    ]
    
    if mode == "manual":
        lines.append(f"آدرس ولت (دستی): <code>{esc(wallet_manual)}</code>" if wallet_manual else f"{WARN} آدرس ولت دستی تنظیم نشده است")
    elif mode == "bsc":
        lines.append(f"آدرس ولت (BSC): <code>{esc(wallet_bsc)}</code>" if wallet_bsc else f"{WARN} آدرس ولت BSC تنظیم نشده است")
    else:
        lines.append("ربات در حالت خاموش است و دکمه خرید برای کاربران نمایش داده نمی‌شود.")
        
    lines += [
        f"مبلغ: {esc(_val('sub_amount'))}",
        f"مدت اشتراک: {esc(_val('sub_days'))}",
        f"نمایش دکمه تمدید از: {esc(_val('sub_renew_days'))} مانده به پایان",
        f"کاربران دارای اشتراک فعال: {db.count_vip()} نفر",
        f"درخواست‌های باز (دستی): {db.count_pending_requests('bot')}", "",
    ]
    
    if on and not subscription.configured():
        lines += [f"{WARN} اطلاعات ولت یا مبلغ تنظیم نشده است؛ تا تنظیم نشوند دکمه‌ی خرید نمایش داده نمی‌شود.", ""]
    elif subscription.enforced():
        lines += [f"{WARN} تا وقتی خرید اشتراک روشن است، سیگنال فقط برای کاربرهای دارای اشتراک فعال (و ادمین) ارسال می‌شود.", ""]

    pending = db.count_pending_requests("bot")
    
    # دکمه‌های تغییر حالت
    mode_btns = [
        btn(f"{OK if mode == 'off' else ''} خاموش", "a:submode:off"),
        btn(f"{OK if mode == 'manual' else ''} دستی", "a:submode:manual"),
        btn(f"{OK if mode == 'bsc' else ''} BSC", "a:submode:bsc"),
    ]
    
    return _note(note) + "\n".join(lines), kb(
        mode_btns,
        [btn("آدرس ولت دستی", "a:subw"), btn("آدرس ولت BSC", "a:subbw")],
        [btn("کلید API (NodeReal)", "a:subapi")],
        [btn(f"مبلغ دلاری: {_val('sub_amount')}", "a:pr:sub_amount"), btn(f"مدت: {_val('sub_days')}", "a:pr:sub_days")],
        [btn(f"نمایش تمدید: {_val('sub_renew_days')}", "a:pr:sub_renew_days")],
        [btn(f"تخفیف: {OK if subscription.discount_active() else NO}", "a:subd")],
        [btn("قالب متن خرید اشتراک", "a:subtp")],
        [btn(f"درخواست‌های خرید دستی ({pending})", "a:subq")],
        back_row("a:set"),
    )

def page_sub_discount(note: str = "", *_a) -> Page:
    active = subscription.discount_active()
    amount = db.get_float("sub_amount")
    lines = [
        "تخفیف اشتراک", "",
        f"وضعیت: {on_off(active)}",
        f"درصد تخفیف: {esc(_val('sub_disc_pct'))}",
        f"دوره‌ی تخفیف: {esc(_val('sub_disc_days'))}",
        f"مبلغ اشتراک: {esc(_val('sub_amount'))}",
    ]
    if active:
        lines += [
            f"مبلغ با تخفیف: {config.fmt_number(subscription.final_amount() or 0)} دلار",
            f"زمان باقی‌مانده‌ی تخفیف: {fmt_duration(subscription.discount_until() - time.time())}",
        ]
    elif db.get_bool("sub_disc_on"):
        lines.append(f"{WARN} دوره‌ی تخفیف تمام شده است؛ برای شروع دوباره، دکمه‌ی تخفیف را خاموش و روشن کنید.")
    lines += [
        "",
        "مبلغ اشتراک را خودتان تعیین می‌کنید. با روشن کردن تخفیف، درصد تخفیف روی مبلغ اشتراک اعمال می‌شود و در متن خرید اشتراک "
        "قیمت اصلی خط‌خورده و قیمت با تخفیف نمایش داده می‌شود. شمارش دوره از لحظه‌ی روشن کردن شروع می‌شود و بعد از پایان دوره "
        "(مثلاً ۳ روز) تخفیف خودکار برداشته می‌شود.",
    ]
    if amount <= 0:
        lines += ["", f"{WARN} مبلغ اشتراک هنوز تعیین نشده است."]
    return _note(note) + "\n".join(lines), kb(
        [btn(f"تخفیف: {OK if active else NO}", "a:subdt")],
        [btn(f"درصد تخفیف: {_val('sub_disc_pct')}", "a:pr:sub_disc_pct")],
        [btn(f"دوره‌ی تخفیف: {_val('sub_disc_days')}", "a:pr:sub_disc_days")],
        back_row("a:sub"),
    )


SUB_TPL_HELP = (
    "Placeholder ها:\n"
    "{wallet} آدرس کیف پول\n"
    "{amount} مبلغ به دلار (تا ۲ رقم اعشار)\n\n"
    "- هر دو Placeholder حتماً باید در قالب باشند.\n"
    "- برای Monospace قبل و بعد از Placeholder بک‌تیک قرار دهید: `{wallet}`\n\n" + fmt.HELP
)


def page_sub_tpl(note: str = "", *_a) -> Page:
    cur = db.get("sub_template") or config.DEFAULT_SUB_TEMPLATE
    text = f"{_note(note)}قالب متن خرید اشتراک\n\n<pre>{esc(cur)}</pre>\n\n{esc(SUB_TPL_HELP)}"
    return text, kb(
        [btn("ویرایش قالب", "a:subte")],
        [btn("پیش‌نمایش", "a:subtv")],
        [btn("بازنشانی به قالب پیش‌فرض", "a:subtd")],
        back_row("a:sub"),
    )


def page_sub_tpl_preview(*_a) -> Page:
    cur = db.get("sub_template") or config.DEFAULT_SUB_TEMPLATE
    wallet_key = "bsc_wallet" if db.get("sub_mode") == "bsc" else "sub_wallet"
    wallet = db.get(wallet_key).strip() or "TXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX"
    amount = db.get_float("sub_amount") or 10.0
    return f"پیش‌نمایش (همین متن به کاربر نمایش داده می‌شود):\n\n{subscription.render_purchase(cur, wallet, amount)}", kb(back_row("a:subtp"))


def page_sub_requests(note: str = "", *_a) -> Page:
    items = db.list_sub_requests("pending", 40, service_type="bot")
    lines = [f"درخواست‌های خرید اشتراک ({len(items)} باز)", ""]
    if not items:
        lines.append("درخواست بازی وجود ندارد.")
    else:
        lines.append("یکی از درخواست‌ها را برای بررسی انتخاب کنید:")
    rows = [
        [btn(f"#{r.id} | {r.user_id} | {jalali_date(r.created_at)}", f"a:subr:{r.id}")] for r in items
    ]
    rows.append(back_row("a:sub"))
    return _note(note) + "\n".join(lines), kb(*rows)


def page_sub_request(rid: str = "0", note: str = "") -> Page:
    """
    صفحه‌ی درخواست خرید دستی. هر درخواست یک رکورد در تاریخچه‌ی پرداخت‌ها دارد:
    اول ادمین هش، آدرس فرستنده و مبلغ را ثبت می‌کند؛ بعد فقط دکمه‌ی مناسب نتیجه‌ی مقایسه‌ی مبلغ نمایش داده می‌شود
    (برابر: تأیید | بیشتر: معلق کردن | کمتر یا هش تکراری: رد کردن).
    """
    try:
        req = db.get_sub_request(int(rid))
    except ValueError:
        req = None
    if req is None:
        return page_sub_requests(f"{NO} این درخواست پیدا نشد.")
    if getattr(req, "service_type", "bot") == "webapp":
        return page_sub_requests(f"{NO} این درخواست مربوط به اشتراک تحلیل ساختاری است؛ آن را از پنل مدیریت وب‌اپ بررسی کنید.")
    pay = None
    try:
        if req.status == "pending":
            pay = payments.ensure_for_request(req, subscription.current_expected(), db.get("sub_wallet").strip())
        else:
            pay = payments.by_request(req.id)
    except Exception:
        log.exception("payment record for request %s failed", rid)
    rows = []
    body = subscription.request_summary(req)
    if pay is not None:
        body += "\n\nتراکنش مرتبط:\n" + admin_extra.payment_text(pay)
        if req.status == "pending":
            if pay["status"] == payments.PENDING:
                rows += admin_extra.payment_action_rows(pay, "q")
            else:
                body += "\n\nاین تراکنش معلق است؛ از «تاریخچه پرداخت‌ها» مدیریت کنید."
                rows.append([btn("مشاهده در تاریخچه پرداخت‌ها", f"a:phi:{pay['id']}")])
    if req.photo_id:
        rows.append([btn("مشاهده تصویر / فایل", f"a:subm:{req.id}")])
    rows.append(back_row("a:subq"))
    return _note(note) + body, kb(*rows)


# ---------------- قالب‌ها ----------------
def page_tpl(*_a) -> Page:
    def st(side: str) -> str:
        return OK if db.get_template(side).strip() else NO

    text = (
        "تنظیمات قالب\n\n"
        f"قالب LONG: {st('LONG')}\n"
        f"قالب SHORT: {st('SHORT')}\n\n"
        "تا وقتی قالب یک جهت خالی باشد، سیگنال آن جهت ارسال نمی‌شود.\n"
        "قالب مخصوص کانال از «ارسال سیگنال به کانال» تنظیم می‌شود."
    )
    return text, kb(
        [btn("ویرایش قالب LONG", "a:tplv:LONG")],
        [btn("ویرایش قالب SHORT", "a:tplv:SHORT")],
        *[[btn(f"مدیریت {blk.label(t)}", f"a:blk:{blk.block_number(t)}")] for t in config.BLOCK_TYPES],
        back_row("a:set"),
    )


PLACEHOLDER_HELP = (
    "Placeholder ها:\n"
    "{symbol} {entry} {lastprice} {side}\n"
    "{sl} {tp1} {tp2} {tp3}\n"
    "{risk} {leverage} {rr}\n"
    "{block1} {block2} {block3}\n"
    "{aiinfo} {botid}\n\n"
    "- {tp1} و {tp2} و {tp3} حتماً باید در قالب باشند.\n"
    "- {lastprice} قیمت لحظه‌ای (last price) همان نماد را نشان می‌دهد و زنده عوض می‌شود.\n"
    "- {rr} ریسک به ریوارد کل سیگنال بر اساس TP3 (مثال: 2.4)\n"
    "- {aiinfo} توضیح کوتاه هوش مصنوعی؛ اگر هوش مصنوعی خاموش باشد یا توضیحی ندهد، کل آن خط نمایش داده نمی‌شود.\n"
    "- هر Placeholder که در قالب نباشد، نمایش داده نمی‌شود.\n"
    "- برای Monospace قبل و بعد از Placeholder بک‌تیک قرار دهید: `{tp1}`\n\n" + fmt.HELP
)
PLACEHOLDER_HELP_CH = PLACEHOLDER_HELP.replace("{symbol} {entry} {lastprice} {side}", "{symbol} {entry} {side}").replace(
    "- {lastprice} قیمت لحظه‌ای (last price) همان نماد را نشان می‌دهد و زنده عوض می‌شود.\n",
    "- {lastprice} در قالب کانال مجاز نیست، چون قیمت داخل کانال به‌روز نمی‌شود.\n",
)
TPL_SIDES = ("LONG", "SHORT", "CH_LONG", "CH_SHORT")


def _tpl_title(side: str) -> str:
    return f"کانال {side[3:]}" if side.startswith("CH_") else side


def _tpl_back(side: str) -> str:
    return "a:chs" if side.startswith("CH_") else "a:tpl"


def page_tpl_view(side: str = "LONG", note: str = "") -> Page:
    side = side.upper()
    if side not in TPL_SIDES:
        return page_tpl()
    cur = db.get_template(side)
    body = f"<pre>{esc(cur)}</pre>" if cur.strip() else "فعلاً خالی است."
    helptxt = PLACEHOLDER_HELP_CH if side.startswith("CH_") else PLACEHOLDER_HELP
    text = f"{_note(note)}قالب {_tpl_title(side)}\n\n{body}\n\n{esc(helptxt)}"
    if len(tpl.plain_fallback(text)) > 4096:      # قالب بلند + راهنما از سقف پیام تلگرام بیشتر می‌شود
        text = f"{_note(note)}قالب {_tpl_title(side)}\n\n{body}"
    rows = [[btn("ثبت / ویرایش قالب", f"a:tple:{side}")]]
    if cur.strip():
        rows.append([btn("پیش‌نمایش", f"a:tplp:{side}")])
    rows.append(back_row(_tpl_back(side)))
    return text, kb(*rows)


def page_tpl_preview(side: str = "LONG", *_a) -> Page:
    side = side.upper()
    if side not in TPL_SIDES:
        return page_tpl()
    cur = db.get_template(side)
    if not cur.strip():
        return page_tpl_view(side, f"{NO} اول قالب را ثبت کنید.")
    try:
        body = tpl.render_signal(tpl.sample_signal(side.replace("CH_", "")), cur, config.BOT_USERNAME, blk.pick)
        if side.startswith("CH_"):
            body = tpl.append_tag(body, db.get("ch_sig_tag"))
    except Exception:
        log.exception("preview failed")
        return page_tpl_view(side, f"{NO} پیش‌نمایش ساخته نشد.")
    if len(tpl.plain_fallback(body)) > 4000:
        return page_tpl_view(
            side, f"{NO} متن نهایی این قالب از سقف ۴۰۹۶ کاراکتر تلگرام بیشتر است و ارسال نمی‌شود؛ قالب یا بلوک‌ها را کوتاه‌تر کنید."
        )
    return f"پیش‌نمایش قالب {_tpl_title(side)} (با داده‌ی نمونه):\n\n{body}", kb(back_row(f"a:tplv:{side}"))


# ---------------- بلوک‌ها ----------------
def _bt(n: str) -> str:
    return blk.block_type_of(n)


def page_block_home(n: str = "1", note: str = "") -> Page:
    bt = _bt(n)
    items = db.list_blocks(bt)
    active = sum(1 for b in items if b.is_active)
    text = (
        f"{_note(note)}مدیریت {blk.label(bt)}\n\n"
        f"تعداد متن‌ها: {len(items)} (فعال: {active})\n"
        "در هر سیگنال از متن‌های فعال این بلوک، یکی به‌صورت تصادفی انتخاب می‌شود."
    )
    num = blk.block_number(bt)
    return text, kb(
        [btn("افزودن متن جدید", f"a:blka:{num}")],
        [btn("ویرایش متن‌ها", f"a:blkl:{num}:e"), btn("حذف متن", f"a:blkl:{num}:d")],
        [btn("مشاهده همه", f"a:blkv:{num}")],
        back_row("a:tpl"),
    )


def _preview(s: str, n: int = 32) -> str:
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def page_block_list(n: str = "1", mode: str = "e") -> Page:
    bt = _bt(n)
    num = blk.block_number(bt)
    mode = "d" if mode == "d" else "e"
    items = db.list_blocks(bt)
    title = "حذف متن" if mode == "d" else "ویرایش متن‌ها"
    if not items:
        return f"{title} - {blk.label(bt)}\n\nهنوز متنی ثبت نشده است.", kb(back_row(f"a:blk:{num}"))
    shown = items[:40]
    rows = [
        [btn(f"{OK if b.is_active else NO} {i}) {_preview(b.content)}", f"a:blki:{b.id}:{mode}")]
        for i, b in enumerate(shown, 1)
    ]
    text = f"{title} - {blk.label(bt)}\n\nیکی از متن‌ها را انتخاب کنید:"
    if len(items) > len(shown):
        text += f"\n(فقط {len(shown)} مورد اول نمایش داده شد)"
    return text, kb(*rows, back_row(f"a:blk:{num}"))


def page_block_item(bid: str = "0", mode: str = "e", note: str = "") -> Page:
    try:
        b = db.get_block(int(bid))
    except ValueError:
        b = None
    if not b:
        return page_block_home("1", f"{NO} این متن پیدا نشد.")
    num = blk.block_number(b.block_type)
    mode = "d" if mode == "d" else "e"
    body = f"<pre>{esc(b.content)}</pre>"
    if mode == "d":
        text = f"{_note(note)}آیا از حذف این متن از {blk.label(b.block_type)} اطمینان دارید؟\n\n{body}"
        return text, kb(
            [btn(f"{OK} بله، حذف کنید", f"a:blkxd:{b.id}")],
            back_row(f"a:blkl:{num}:d"),
        )
    text = f"{_note(note)}{blk.label(b.block_type)}\nوضعیت: {on_off(b.is_active)}\n\n{body}"
    return text, kb(
        [btn("ویرایش متن", f"a:blke:{b.id}")],
        [btn("غیرفعال کردن" if b.is_active else "فعال کردن", f"a:blkt:{b.id}")],
        back_row(f"a:blkl:{num}:e"),
    )


def page_block_view_all(n: str = "1", *_a) -> Page:
    bt = _bt(n)
    num = blk.block_number(bt)
    items = db.list_blocks(bt)
    if not items:
        return f"{blk.label(bt)}\n\nهنوز متنی ثبت نشده است.", kb(back_row(f"a:blk:{num}"))
    parts, total = [], 0
    for i, b in enumerate(items, 1):
        chunk_txt = f"{i}) {OK if b.is_active else NO}\n<pre>{esc(b.content)}</pre>"
        if total + len(chunk_txt) > 3500:
            parts.append("...")
            break
        parts.append(chunk_txt)
        total += len(chunk_txt)
    return f"همه‌ی متن‌های {blk.label(bt)}:\n\n" + "\n".join(parts), kb(back_row(f"a:blk:{num}"))


# ---------------- کانال‌های عضویت اجباری ----------------
def page_channels(note: str = "", *_a) -> Page:
    chs = db.list_channels()
    lines = [f"کانال‌های عضویت اجباری ({len(chs)} از {config.MAX_CHANNELS})", ""]
    if not chs:
        lines.append("هنوز کانالی اضافه نشده است.")
    for c in chs:
        lines.append(f"{OK if c.is_active else NO} {esc(c.channel_title or c.channel_id)}")
    rows = [[btn(f"{OK if c.is_active else NO} {c.channel_title or c.channel_id}", f"a:chi:{c.id}")] for c in chs]
    if len(chs) < config.MAX_CHANNELS:
        rows.append([btn("افزودن کانال", "a:cha")])
    rows.append(back_row("a:home"))
    return _note(note) + "\n".join(lines), kb(*rows)


def page_channel(cid: str = "0", note: str = "") -> Page:
    try:
        c = db.get_channel(int(cid))
    except ValueError:
        c = None
    if not c:
        return page_channels(f"{NO} این کانال پیدا نشد.")
    link = force_subscribe.channel_link(c) or "ندارد"
    text = (
        f"{_note(note)}کانال: {esc(c.channel_title or '-')}\n\n"
        f"آیدی: <code>{esc(c.channel_id)}</code>\n"
        f"لینک: {esc(link)}\n"
        f"وضعیت: {on_off(c.is_active)}"
    )
    return text, kb(
        [btn("ویرایش کانال", f"a:chem:{c.id}")],
        [btn("غیرفعال کردن" if c.is_active else "فعال کردن", f"a:cht:{c.id}")],
        [btn("حذف کانال", f"a:chx:{c.id}")],
        back_row("a:ch"),
    )


def page_channel_edit(cid: str = "0", *_a) -> Page:
    try:
        c = db.get_channel(int(cid))
    except ValueError:
        c = None
    if not c:
        return page_channels(f"{NO} این کانال پیدا نشد.")
    return f"ویرایش کانال {esc(c.channel_title or c.channel_id)}\n\nکدام بخش را می‌خواهید تغییر دهید؟", kb(
        [btn("تغییر آیدی / یوزرنیم", f"a:chef:{c.id}:id")],
        [btn("تغییر عنوان", f"a:chef:{c.id}:title")],
        [btn("تغییر لینک", f"a:chef:{c.id}:link")],
        back_row(f"a:chi:{c.id}"),
    )


def page_channel_delete(cid: str = "0", *_a) -> Page:
    try:
        c = db.get_channel(int(cid))
    except ValueError:
        c = None
    if not c:
        return page_channels(f"{NO} این کانال پیدا نشد.")
    return f"آیا از حذف کامل کانال «{esc(c.channel_title or c.channel_id)}» اطمینان دارید؟", kb(
        [btn(f"{OK} بله، حذف کنید", f"a:chxd:{c.id}")],
        back_row(f"a:chi:{c.id}"),
    )


# ---------------- قیمت زنده ----------------
def page_live(note: str = "", *_a) -> Page:
    on = db.get_bool("live_on")
    text = (
        f"{_note(note)}قیمت زنده‌ی سیگنال\n\n"
        f"وضعیت: {on_off(on)}\n"
        f"فاصله‌ی به‌روزرسانی قیمت: {esc(_val('live_interval'))}\n"
        f"مدت به‌روزرسانی هر سیگنال: {esc(_val('live_duration'))}\n\n"
        "هرجا در قالب {lastprice} قرار دهید، آخرین قیمت معامله‌ی همان نماد با همین فاصله روی پیام سیگنال عوض می‌شود.\n"
        "حداقل فاصله ۱ ثانیه است. تلگرام سرعت ویرایش پیام‌ها را محدود می‌کند؛ با زیاد شدن تعداد کاربرها، "
        "به‌روزرسانی ممکن است کمی کندتر از فاصله‌ی تنظیم‌شده شود."
    )
    return text, kb(
        [btn(f"قیمت زنده: {on_off(on)}", "a:livet")],
        [btn(f"فاصله‌ی به‌روزرسانی: {_val('live_interval')}", "a:pr:live_interval")],
        [btn(f"مدت به‌روزرسانی: {_val('live_duration')}", "a:pr:live_duration")],
        back_row("a:set"),
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


def page_sf(note: str = "", *_a) -> Page:
    cmap = engine.category_map()
    if not engine.symbols:
        text = (
            f"{_note(note)}فیلتر نمادها\n\n"
            "هنوز لیست نمادها از صرافی دریافت نشده است. چند لحظه‌ی دیگر «به‌روزرسانی لیست» را بزنید."
        )
        return text, kb([btn("به‌روزرسانی لیست از صرافی", "a:sfr")], back_row("a:set"))
    total, allowed = len(engine.symbols), len(engine.allowed_symbols())
    lines = [
        "فیلتر نمادها", "",
        f"کل نمادهای فیوچرز: {total}",
        f"نمادهای فعال برای سیگنال: {allowed}", "",
        "فقط نمادهایی سیگنال می‌گیرند که خودشان تیک داشته باشند و همه‌ی دسته‌های آن‌ها فعال باشد. "
        "دسته‌های بازار سنتی (Stock / TradFi / Forex) به‌صورت پیش‌فرض غیرفعال‌اند. "
        "روی هر دسته بزنید تا نمادهای آن را مشاهده کنید:",
    ]
    cat_btns = []
    for n in _sorted_cats(cmap):
        pairs = cmap[n]
        ticked = sum(1 for p in pairs if db.sym_enabled(p))
        label = f"{OK if db.cat_enabled(n) else NO} {_cat_label(n)} ({ticked}/{len(pairs)})"
        cat_btns.append(btn(label, f"a:sfc:{_h(n)}:0"))
    rows = chunk(cat_btns, 2)
    rows += [
        [btn("فعال‌سازی همه‌ی دسته‌ها", "a:sfa:1"), btn("غیرفعال‌سازی همه‌ی دسته‌ها", "a:sfa:0")],
        [btn("به‌روزرسانی لیست از صرافی", "a:sfr"), btn("بازنشانی به پیش‌فرض", "a:sfd")],
        back_row("a:set"),
    ]
    return _note(note) + "\n".join(lines), kb(*rows)


def page_sf_cat(h: str = "", page: str = "0", note: str = "") -> Page:
    cmap = engine.category_map()
    name = _find_cat(h, cmap)
    if name is None:
        return page_sf(f"{NO} این دسته پیدا نشد (لیست نمادها تغییر کرده است).")
    pairs = cmap[name]
    size = config.SF_PAGE_SIZE
    pages = max(1, (len(pairs) + size - 1) // size)
    try:
        pg = min(max(0, int(page)), pages - 1)
    except ValueError:
        pg = 0
    ticked = sum(1 for p in pairs if db.sym_enabled(p))
    cat_on = db.cat_enabled(name)
    text = (
        f"{_note(note)}دسته: {esc(_cat_label(name))}\n"
        f"وضعیت دسته: {on_off(cat_on)}\n"
        f"تعداد نمادها: {len(pairs)} | تیک‌دار: {ticked}\n"
        f"صفحه {pg + 1} از {pages}\n\n"
        "اگر دسته غیرفعال باشد، هیچ‌یک از نمادهای آن سیگنال نمی‌گیرد. "
        "تیک هر نماد برای همه‌ی دسته‌های آن یکسان است."
    )
    sym_btns = [
        btn(f"{OK if db.sym_enabled(p) else NO} {engine.bases.get(p, p)}", f"a:sfs:{h}:{pg}:{_h(p)}")
        for p in pairs[pg * size:(pg + 1) * size]
    ]
    nav = []
    if pg > 0:
        nav.append(btn("قبلی", f"a:sfc:{h}:{pg - 1}"))
    if pg < pages - 1:
        nav.append(btn("بعدی", f"a:sfc:{h}:{pg + 1}"))
    rows = [
        [btn(f"دسته‌بندی: {on_off(cat_on)}", f"a:sfct:{h}:{pg}")],
        [btn("انتخاب همه (تیک‌دار)", f"a:sfca:{h}:1:{pg}"), btn("حذف همه‌ی تیک‌ها", f"a:sfca:{h}:0:{pg}")],
        *chunk(sym_btns, 3),
    ]
    if nav:
        rows.append(nav)
    rows.append(back_row("a:sf"))
    return text, kb(*rows)


def page_sf_reset(*_a) -> Page:
    return (
        "آیا از بازگرداندن فیلتر نمادها به حالت پیش‌فرض اطمینان دارید؟\n"
        "همه‌ی نمادها تیک‌دار می‌شوند و فقط دسته‌های بازار سنتی (Stock / TradFi / Forex) غیرفعال می‌مانند.",
        kb([btn(f"{OK} بله، بازنشانی کنید", "a:sfdx")], back_row("a:sf")),
    )


# ---------------- جدول صفحه‌ها ----------------
PAGES: Dict[str, Callable[..., Page]] = {
    "home": page_home,
    "set": page_settings,
    "tf": page_tf,
    "eng": page_eng,
    "engp": page_engine_params,
    "rsk": page_risk,
    "snd": page_send,
    "live": page_live,
    "sf": page_sf,
    "sfc": page_sf_cat,
    "sfd": page_sf_reset,
    "tpl": page_tpl,
    "tplv": page_tpl_view,
    "tplp": page_tpl_preview,
    "blk": page_block_home,
    "blkl": page_block_list,
    "blki": page_block_item,
    "blkv": page_block_view_all,
    "ch": page_channels,
    "chi": page_channel,
    "chem": page_channel_edit,
    "chx": page_channel_delete,
    "chs": page_chs,
    "pre": page_pre,
    "rl": page_rl,
    "sub": page_sub,
    "subd": page_sub_discount,
    "subtp": page_sub_tpl,
    "subtv": page_sub_tpl_preview,
    "subq": page_sub_requests,
    "subr": page_sub_request,
    "aik": page_ai_keys,
    "aimm": page_ai_models,
    "aimx": page_ai_model_delete,
    "aiks": page_ai_slot,
    "aikx": page_ai_slot_delete,
}


PAGES.update(admin_extra.PAGES)


def render_data(data: str) -> Page:
    """callback_data مثل 'a:engp:rsi' -> (متن، کیبورد)"""
    parts = data.split(":")[1:]
    fn = PAGES.get(parts[0] if parts else "home", page_home)
    try:
        return fn(*parts[1:])
    except TypeError:
        return page_home()


# ============================================================================
# دستورها
# ============================================================================
async def send_home(ctx: Ctx) -> None:
    ctx.user_data.pop("state", None)
    text, markup = page_home()
    await ctx.send(text, markup)


async def _open(ctx: Ctx, data: str) -> None:
    ctx.user_data.pop("state", None)
    text, markup = render_data(data)
    await ctx.show(text, markup)


async def cmd_settings(ctx: Ctx) -> None:
    await _open(ctx, "a:set")


async def cmd_channels(ctx: Ctx) -> None:
    await _open(ctx, "a:ch")


# ============================================================================
# کال‌بک‌ها
# ============================================================================
def _set_state(ctx: Ctx, kind: str, back: str, prompt: str, **extra) -> None:
    mid = ctx.q.message.message_id if ctx.q and ctx.q.message else None
    ctx.user_data["state"] = {"kind": kind, "back": back, "prompt": prompt, "mid": mid, **extra}


async def _prompt(ctx: Ctx, kind: str, back: str, prompt: str, **extra) -> None:
    """صفحه دریافت ورودی از ادمین (همیشه دکمه برگشت دارد)."""
    _set_state(ctx, kind, back, prompt, **extra)
    m = await ctx.show(prompt, kb(back_row(back)))
    if m is not None and ctx.user_data.get("state"):
        ctx.user_data["state"]["mid"] = m.message_id        # نتیجه‌ی ورودی همین پیام را ویرایش می‌کند


def _to_int(s: str, default: int = 0) -> int:
    return int(s) if s and s.lstrip("-").isdigit() else default


async def handle_callback(ctx: Ctx, parts: List[str]) -> None:
    """parts = اجزای callback_data بعد از 'a'."""
    act = parts[0] if parts else "home"
    args = parts[1:]
    ctx.user_data.pop("state", None)  # هر ناوبری، ورودی در انتظار را لغو می‌کند

    # ---------- ناوبری ساده ----------
    if act == "noop":
        await ctx.answer()
        return

    # منوی پنل در یک پیام جدید (دکمه‌ی زیر سیگنال و اعلان‌ها)
    if act == "hm":
        await ctx.answer()
        await send_home(ctx)
        return

    # ---------- بخش‌های منتقل‌شده به پنل مدیریت وب‌اپ ----------
    if act in {"bkp", "rst", "rstok", "supr", "supp", "rl", "aik"} or act.startswith(("ph", "sub")):
        await ctx.answer()
        from webapp.activation import admin_panel_url

        await ctx.show(
            "این بخش به پنل مدیریت وب‌اپ منتقل شده است.\n"
            "پرداخت‌ها، پشتیبانی، بکاپ، امنیت، کلیدهای API و تنظیمات اشتراک را از آن‌جا مدیریت کنید.",
            kb(
                [btn("ورود به پنل مدیریت وب‌اپ", url=admin_panel_url(), style="primary")],
                back_row("a:home"),
            ),
        )
        return

    # ---------- ارسال سیگنال فوری ----------
    if act == "sig":
        await ctx.answer()
        from telegram_handler import admin_signal
        await admin_signal(ctx)
        return

    # ---------- آنالیز با AI ----------
    if act in ("ana", "ana2"):
        await ctx.answer()
        await analyst.start(ctx, new_message=(act == "ana2"))
        return
    if act in ("anam", "anam2"):
        await ctx.answer()
        await analyst.start_multi(ctx, new_message=(act == "anam2"))
        return

    # ---------- خاموش / روشن کل ربات ----------
    if act == "bot":
        new = db.toggle("bot_on")
        await ctx.answer(f"ربات {'روشن' if new else 'خاموش'} شد.")
        await ctx.show(*page_home())
        return

    # ---------- روشن / خاموش کردن سیگنال ادمین ----------
    if act == "adminsig":
        new = db.toggle("admin_sig_on")
        await ctx.answer(f"سیگنال ادمین (کانال) {'روشن' if new else 'خاموش'} شد.")
        await ctx.show(*page_home())
        return

    if act in PAGES:
        await ctx.answer()
        text, markup = render_data("a:" + ":".join(parts))
        await ctx.show(text, markup)
        return

    # ---------- تایم‌فریم ----------
    if act in ("tfe", "tff"):
        opts = config.ENTRY_TF_OPTIONS if act == "tfe" else config.FINAL_TF_OPTIONS
        if args and args[0] in opts:
            db.set_e("tf_entry" if act == "tfe" else "tf_final", args[0], _view_side())
        await ctx.answer()
        await ctx.show(*page_tf())
        return
    if act == "tft":
        side = _view_side()
        which = args[0] if args else "e"
        key, other = ("tf_entry_on", "tf_final_on") if which == "e" else ("tf_final_on", "tf_entry_on")
        if db.get_e_bool(key, side) and not db.get_e_bool(other, side):
            await ctx.answer(f"{NO} حداقل یکی از تایم‌فریم‌ها باید فعال بماند.", alert=True)
            return
        db.toggle_e(key, side)
        await ctx.answer()
        await ctx.show(*page_tf())
        return

    # ---------- موتورها ----------
    if act == "engm":
        mode = args[0] if args else "global"
        if mode == "separate":
            db.ensure_side_profiles()
            db.set("eng_mode", "separate")
            note = f"{OK} حالت جداگانه فعال شد؛ هر جهت از روی تنظیمات فعلی پروفایل مستقل خودش را ساخت."
        else:
            db.set("eng_mode", "global")
            note = f"{OK} حالت مشترک فعال شد؛ حالا یک پروفایل برای هر دو جهت استفاده می‌شود."
        await ctx.answer()
        if len(args) > 1 and args[1] == "tf":
            await ctx.show(*page_tf(note))
        else:
            await ctx.show(*page_eng(note))
        return
    if act == "engs":
        side = (args[0] if args else "LONG").upper()
        if side in ("LONG", "SHORT"):
            db.set("eng_view", side)
        ret = args[1:] or ["eng"]
        await ctx.answer()
        fn = PAGES.get(ret[0], page_eng)
        try:
            await ctx.show(*fn(*ret[1:]))
        except TypeError:
            await ctx.show(*page_eng())
        return
    if act == "engt":
        eid = args[0] if args else ""
        meta = config.ENGINES.get(eid)
        side = _view_side()
        back_params = len(args) > 1 and args[1] == "p"
        if meta:
            key = meta[1]
            if eid in ("st", "ema") and db.get_e_bool(key, side):
                other = config.ENGINES["ema" if eid == "st" else "st"][1]
                if not db.get_e_bool(other, side):
                    await ctx.answer(
                        f"{NO} حداقل یکی از Supertrend یا EMA باید روشن بماند، چون جهت سیگنال از آن‌ها می‌آید.",
                        alert=True,
                    )
                    return
            db.toggle_e(key, side)
        await ctx.answer()
        await ctx.show(*(page_engine_params(eid) if back_params and meta else page_eng()))
        return
    if act == "cdt":
        pid = args[0] if args else ""
        if pid in config.CANDLE_PATTERNS:
            db.toggle_e(f"cdp_{pid}", _view_side())
        await ctx.answer()
        await ctx.show(*page_candles())
        return
    if act == "engx":
        await ctx.answer()
        try:
            await ctx.bot.send_document(
                chat_id=ctx.uid,
                document=engine_io.export_bytes(),
                filename="aban_trade_engines.json",
                caption=(
                    f"تنظیمات موتورها ({config.BRAND}). فایل را ویرایش کنید و با دکمه‌ی Import دوباره ارسال کنید.\n"
                    f"حالت فعلی: {'جداگانه (LONG و SHORT)' if db.eng_mode() == 'separate' else 'مشترک'}"
                ),
                reply_markup=kb(back_row("a:eng")),
            )
        except TelegramError as e:
            log.warning("export send failed: %s", e)
            await ctx.show(*page_eng(f"{NO} ارسال فایل ممکن نشد، دوباره امتحان کنید."))
        return
    if act == "engi":
        await ctx.answer()
        prompt = (
            "فایل JSON تنظیمات موتورها (همان فایل Export که ویرایش کرده‌اید) را همین‌جا ارسال کنید.\n\n"
            "- فایل را به‌صورت Document ارسال کنید (یا متن JSON را مستقیم paste کنید).\n"
            "- اول کل فایل بررسی می‌شود؛ اگر حتی یک مقدار نامعتبر باشد هیچ تغییری اعمال نمی‌شود.\n"
            "- حالت مشترک / جداگانه هم از روی فایل تعیین می‌شود."
        )
        await _prompt(ctx, "eng_import", "a:eng", prompt)
        return

    # ---------- هوش مصنوعی ----------
    if act == "aiont":
        new = db.toggle("ai_on")
        await ctx.answer()
        await ctx.show(*page_ai(f"{OK} سوییچ اصلی هوش مصنوعی {'روشن' if new else 'خاموش'} شد."))
        return
    if act == "aif":
        fid = args[0] if args else ""
        if fid in config.AI_FEATURES:
            db.toggle_e(config.AI_FEATURES[fid][0], _view_side())
        await ctx.answer()
        await ctx.show(*page_ai())
        return
    if act == "aim":
        idx = _to_int(args[0] if args else "", -1)
        models = db.ai_models()
        if 0 <= idx < len(models):
            db.set_e("ai_model", models[idx], _view_side())
        await ctx.answer()
        await ctx.show(*page_ai())
        return
    if act == "aimc":
        await ctx.answer()
        await _prompt(
            ctx, "ai_model", "a:engp:ai",
            "اسم کامل مدل را ارسال کنید (بدون فاصله).\nمثال: @cf/meta/llama-3.1-8b-instruct",
            side=_view_side(),
        )
        return
    if act == "aima":
        await ctx.answer()
        await _prompt(
            ctx, "ai_model_add", "a:aimm",
            "اسم کامل مدل را ارسال کنید (بدون فاصله).\nمثال: @cf/meta/llama-3.1-8b-instruct",
        )
        return
    if act == "aimxd":
        idx = _to_int(args[0] if args else "", -1)
        models = db.ai_models()
        await ctx.answer()
        if not 0 <= idx < len(models):
            await ctx.show(*page_ai_models(f"{NO} این مدل پیدا نشد."))
            return
        if len(models) <= 1:
            await ctx.show(*page_ai_models(f"{NO} حداقل یک مدل باید در لیست باقی بماند."))
            return
        removed = models.pop(idx)
        db.set_ai_models(models)
        affected = db.user_ids_with_models([removed])
        for u in affected:
            db.user(u).set("ai_model", models[0])
        sent = 0
        if affected:
            try:
                sent, _failed = await broadcaster.mass_send(
                    ctx.bot, affected,
                    {"text": f"مدل هوش مصنوعی انتخابی شما ({removed}) دیگر در لیست مدل‌های مجاز نیست؛ مدل شما به {models[0]} تغییر کرد. "
                             "برای انتخاب مدل دیگر به «تنظیمات > مدل هوش مصنوعی» بروید."},
                )
            except Exception:
                log.exception("model change notify failed")
        note = f"{OK} مدل حذف شد."
        if affected:
            note += f" مدل {len(affected)} کاربر تغییر کرد و به {sent} نفر اطلاع داده شد."
        await ctx.show(*page_ai_models(note))
        return
    if act == "aisep":
        db.toggle("ai_sep")
        await ctx.answer()
        await ctx.show(*page_ai())
        return
    if act == "aiurl":
        await ctx.answer()
        cur = (db.get("ai_url") or config.AI_WORKER_URL).strip()
        await _prompt(
            ctx, "ai_url", "a:aik",
            "آدرس سرویس هوش مصنوعی (Cloudflare Worker) را ارسال کنید.\n"
            "اگر آدرس شامل {model} باشد، اسم مدل جای آن قرار می‌گیرد؛ وگرنه مدل داخل بدنه‌ی درخواست فرستاده می‌شود.\n"
            "برای برگشت به آدرس فایل .env فقط یک خط تیره (-) ارسال کنید.\n\n"
            f"آدرس فعلی: {esc(cur) if cur else 'تنظیم نشده است'}",
        )
        return
    if act == "aires":
        ai.resume()
        await ctx.answer()
        await ctx.show(*page_ai_keys(f"{OK} هوش مصنوعی دوباره فعال شد و خطاهای موقت کلیدها پاک شد."))
        return
    if act in ("aikc", "aikn"):
        n = _to_int(args[0] if args else "")
        await ctx.answer()
        if not 1 <= n <= config.AI_MAX_KEYS:
            await ctx.show(*page_ai_keys(f"{NO} این اسلات وجود ندارد."))
            return
        if act == "aikc":
            await _prompt(
                ctx, "ai_key", f"a:aiks:{n}",
                f"کلید API اسلات {n} را همین‌جا ارسال کنید. برای امنیت، پس از ثبت، پیام حاوی کلید را خودتان از گفتگو حذف کنید.", slot=n,
            )
        else:
            if db.ai_key(n) is None:
                await ctx.show(*page_ai_slot(str(n), f"{NO} اول کلید را ثبت کنید."))
                return
            await _prompt(ctx, "ai_name", f"a:aiks:{n}", f"اسم جدید کلید اسلات {n} را ارسال کنید (حداکثر ۲۰ کاراکتر):", slot=n)
        return
    if act == "aika":
        n = _to_int(args[0] if args else "")
        await ctx.answer()
        if db.ai_key(n) is None:
            await ctx.show(*page_ai_slot(str(n), f"{NO} اول کلید را ثبت کنید."))
            return
        await _prompt(ctx, "ai_acc", f"a:aiks:{n}", _acc_prompt(n), slot=n)
        return
    if act == "aikt":
        n = _to_int(args[0] if args else "")
        k = db.ai_key(n)
        await ctx.answer()
        if k is not None:
            db.ai_key_set_enabled(n, not k.enabled)
        await ctx.show(*page_ai_slot(str(n)))
        return
    if act == "aikv":
        n = _to_int(args[0] if args else "")
        await ctx.answer()
        await ctx.show("در حال تست اتصال...", kb(back_row(f"a:aiks:{n}")))
        ok, msg = await ai.test_key(n)
        await ctx.show(*page_ai_slot(str(n), f"{OK if ok else NO} {esc(msg)}"))
        return
    if act == "aikxd":
        n = _to_int(args[0] if args else "")
        await ctx.answer()
        db.ai_key_delete(n)
        await ctx.show(*page_ai_keys(f"{OK} کلید اسلات {n} حذف شد."))
        return

    # ---------- قیمت زنده ----------
    if act == "livet":
        db.toggle("live_on")
        await ctx.answer()
        await ctx.show(*page_live())
        return

    # ---------- هشدار قبل از سیگنال ----------
    if act == "pret":
        new = db.toggle("pre_alert_on")
        await ctx.answer()
        await ctx.show(*page_pre(f"{OK} هشدار قبل از سیگنال {'فعال' if new else 'غیرفعال'} شد."))
        return

    # ---------- کانال سیگنال ----------
    if act == "chse":
        await ctx.answer()
        prompt = (
            "آیدی عددی یا یوزرنیم کانالی که سیگنال‌ها در آن منتشر می‌شود را ارسال کنید.\n"
            "مثال: @mychannel یا -1001234567890\n\n"
            "کانال می‌تواند عمومی یا خصوصی باشد، اما ربات باید در آن ادمین (با دسترسی ارسال پیام) باشد."
        )
        await _prompt(ctx, "chs_set", "a:chs", prompt)
        return
    if act == "chst":
        await ctx.answer()
        await _prompt(
            ctx, "chs_tag", "a:chs",
            "متنی که آخر هر پست سیگنال کانال نوشته می‌شود را ارسال کنید (مثلاً @mychannel یا لینک کانال).\n"
            "برای خالی کردن آن یک خط تیره (-) ارسال کنید.\n\n"
            f"مقدار فعلی: {esc(db.get('ch_sig_tag')) or '-'}",
        )
        return
    if act == "chsx":
        await ctx.answer()
        for k in ("ch_sig_id", "ch_sig_title", "ch_sig_tag"):
            db.set(k, "")
        await ctx.show(*page_chs(f"{OK} کانال سیگنال حذف شد؛ تا تعیین کانال جدید سیگنال ادمین ارسال نمی‌شود."))
        return

    # ---------- اشتراک ----------
    if act == "subt":
        new = db.toggle("sub_on")
        await ctx.answer()
        await ctx.show(*page_sub(f"{OK} خرید اشتراک {'فعال' if new else 'غیرفعال'} شد."))
        return
    # ---------- تغییر حالت خرید اشتراک ----------
    if act == "submode":
        new_mode = args[0] if args else "off"
        db.set("sub_mode", new_mode)
        await ctx.answer(f"حالت خرید اشتراک به {new_mode} تغییر کرد.")
        await ctx.show(*page_sub())
        return

    # ---------- آدرس BSC و کلید API ----------
    if act == "subbw":
        await ctx.answer()
        await _prompt(ctx, "sub_bsc_wallet", "a:sub", "آدرس ولت BSC را وارد کنید (شروع با 0x):")
        return
    if act == "subapi":
        await ctx.answer()
        await _prompt(ctx, "sub_bsc_api", "a:sub", "کلید API سایت BscScan را وارد کنید:")
        return        
    if act == "subdt":
        await ctx.answer()
        if subscription.discount_active():
            db.set("sub_disc_on", "0")
            note = f"{OK} تخفیف خاموش شد."
        elif db.get_float("sub_amount") <= 0:
            note = f"{NO} اول مبلغ اشتراک را تعیین کنید."
        else:
            db.set("sub_disc_start", str(time.time()))
            db.set("sub_disc_on", "1")
            note = f"{OK} تخفیف روشن شد و شمارش دوره از همین لحظه شروع شد."
        await ctx.show(*page_sub_discount(note))
        return
    if act == "subw":
        await ctx.answer()
        await _prompt(
            ctx, "sub_wallet", "a:sub",
            "آدرس کیف پول را ارسال کنید.\n\n"
            f"آدرس فعلی: {esc(db.get('sub_wallet')) or 'تنظیم نشده است'}",
        )
        return
    if act == "subte":
        await ctx.answer()
        await _prompt(
            ctx, "sub_tpl", "a:subtp",
            f"قالب متن خرید اشتراک را همین‌جا ارسال کنید (کل متن در یک پیام).\n\n{esc(SUB_TPL_HELP)}",
        )
        return
    if act == "subtd":
        await ctx.answer()
        db.set("sub_template", config.DEFAULT_SUB_TEMPLATE)
        await ctx.show(*page_sub_tpl(f"{OK} قالب به حالت پیش‌فرض برگشت."))
        return
    if act == "subm":
        req = db.get_sub_request(_to_int(args[0] if args else ""))
        if req is not None and getattr(req, "service_type", "bot") == "webapp":
            await ctx.answer(f"{NO} این درخواست مربوط به اشتراک تحلیل ساختاری است؛ از پنل مدیریت وب‌اپ بررسی کنید.", alert=True)
            return
        if req is None or not await subscription.send_request_media(ctx.bot, ctx.uid, req, f"a:subr:{req.id}"):
            await ctx.answer(f"{NO} تصویر یا فایلی برای این درخواست نیست.", alert=True)
        else:
            await ctx.answer()
        return
    if act == "subok":
        # دکمه‌ی قدیمی: تأیید مستقیم دیگر ممکن نیست؛ اول باید اطلاعات تراکنش ثبت و با مبلغ اشتراک مقایسه شود
        rid = _to_int(args[0] if args else "")
        await ctx.answer()
        await ctx.show(*page_sub_request(str(rid), f"{WARN} برای تأیید، اول اطلاعات تراکنش (هش، آدرس فرستنده، مبلغ) را ثبت کنید."))
        return
    if act == "subno":
        rid = _to_int(args[0] if args else "")
        req = db.get_sub_request(rid)
        await ctx.answer()
        if req is not None and getattr(req, "service_type", "bot") == "webapp":
            await ctx.show(*page_sub_requests(f"{NO} این درخواست مربوط به اشتراک تحلیل ساختاری است؛ آن را از پنل مدیریت وب‌اپ بررسی کنید."))
            return
        if req is None or req.status != "pending":
            await ctx.show(*page_sub_requests(f"{NO} این درخواست باز نیست."))
            return
        await _prompt(
            ctx, "sub_reject", f"a:subr:{rid}",
            f"دلیل رد درخواست {rid} را ارسال کنید. همین متن برای کاربر ارسال می‌شود:", rid=rid,
        )
        return
    # ---------- فیلتر نمادها ----------
    if act in ("sfct", "sfca", "sfs"):
        h = args[0] if args else ""
        cmap = engine.category_map()
        name = _find_cat(h, cmap)
        await ctx.answer()
        if name is None:
            await ctx.show(*page_sf(f"{NO} این دسته پیدا نشد (لیست نمادها تغییر کرده است)."))
            return
        pairs = cmap[name]
        if act == "sfct":
            db.set_cats([name], not db.cat_enabled(name))
            pg = args[1] if len(args) > 1 else "0"
        elif act == "sfca":
            db.set_syms(pairs, (args[1] if len(args) > 1 else "1") == "1")
            pg = args[2] if len(args) > 2 else "0"
        else:
            pg = args[1] if len(args) > 1 else "0"
            target = next((p for p in pairs if _h(p) == (args[2] if len(args) > 2 else "")), None)
            if target is not None:
                db.set_syms([target], not db.sym_enabled(target))
        await ctx.show(*page_sf_cat(h, pg))
        return
    if act == "sfa":
        on = (args[0] if args else "1") == "1"
        await ctx.answer()
        db.set_cats(list(engine.category_map().keys()), on)
        await ctx.show(*page_sf(f"{OK} همه‌ی دسته‌ها {'فعال' if on else 'غیرفعال'} شدند."))
        return
    if act == "sfr":
        await ctx.answer()
        await ctx.show("در حال گرفتن لیست نمادها از صرافی...", kb(back_row("a:sf")))
        try:
            await engine.refresh_markets()
            note = f"{OK} لیست نمادها به‌روزرسانی شد."
        except Exception:
            log.exception("refresh markets failed")
            note = f"{NO} به‌روزرسانی لیست نشد، چند لحظه‌ی دیگر دوباره امتحان کنید."
        await ctx.show(*page_sf(note))
        return
    if act == "sfdx":
        await ctx.answer()
        db.reset_filters()
        await ctx.show(*page_sf(f"{OK} فیلتر نمادها به حالت پیش‌فرض برگشت."))
        return

    # ---------- ارسال ----------
    if act == "sndt":
        db.toggle("dedupe_on")
        await ctx.answer()
        await ctx.show(*page_send())
        return

    # ---------- پرامپت پارامتر ----------
    if act == "pr":
        key = args[0] if args else ""
        spec = config.PARAMS.get(key)
        if not spec:
            await ctx.answer()
            return
        await ctx.answer()
        side = _side_for(key)
        lo = int(spec.min) if float(spec.min).is_integer() else spec.min
        hi = int(spec.max) if float(spec.max).is_integer() else spec.max
        kind_txt = "عدد صحیح" if spec.kind == "int" else "عدد (می‌تواند اعشاری باشد)"
        prompt = (
            f"{esc(spec.label)}{_side_title(side)}\n\n"
            f"مقدار فعلی: {esc(_val(key, side))}\n"
            f"بازه‌ی مجاز: {lo} تا {hi}\n\n"
            f"مقدار جدید را به‌صورت {kind_txt} همین‌جا ارسال کنید:"
        )
        await _prompt(ctx, "param", config.param_back(key), prompt, key=key, side=side)
        return

    # ---------- قالب ----------
    if act == "tple":
        side = (args[0] if args else "LONG").upper()
        if side not in TPL_SIDES:
            await ctx.answer()
            return
        await ctx.answer()
        helptxt = PLACEHOLDER_HELP_CH if side.startswith("CH_") else PLACEHOLDER_HELP
        prompt = (
            f"قالب {_tpl_title(side)} را همین‌جا ارسال کنید (کل متن قالب در یک پیام).\n\n"
            f"{esc(helptxt)}"
        )
        await _prompt(ctx, "tpl", f"a:tplv:{side}", prompt, side=side)
        return

    # ---------- بلوک‌ها ----------
    if act == "blka":
        n = args[0] if args else "1"
        bt = blk.block_type_of(n)
        await ctx.answer()
        await _prompt(
            ctx, "blk_add", f"a:blk:{blk.block_number(bt)}",
            f"متن جدید {blk.label(bt)} را همین‌جا ارسال کنید:\n\n{esc(fmt.HELP)}", bt=bt,
        )
        return
    if act == "blke":
        b = db.get_block(int(args[0])) if args and args[0].isdigit() else None
        await ctx.answer()
        if not b:
            await ctx.show(*page_block_home("1", f"{NO} این متن پیدا نشد."))
            return
        await _prompt(
            ctx, "blk_edit", f"a:blki:{b.id}:e",
            f"متن فعلی:\n<pre>{esc(b.content)}</pre>\nمتن جدید را همین‌جا ارسال کنید:\n\n{esc(fmt.HELP)}", bid=b.id,
        )
        return
    if act == "blkt":
        if args and args[0].isdigit():
            db.toggle_block(int(args[0]))
        await ctx.answer()
        await ctx.show(*page_block_item(args[0] if args else "0", "e"))
        return
    if act == "blkxd":
        b = db.get_block(int(args[0])) if args and args[0].isdigit() else None
        await ctx.answer()
        if not b:
            await ctx.show(*page_block_home("1", f"{NO} این متن پیدا نشد."))
            return
        db.delete_block(b.id)
        await ctx.show(*page_block_list(blk.block_number(b.block_type), "d"))
        return

    # ---------- کانال‌های عضویت اجباری ----------
    if act == "cha":
        await ctx.answer()
        if len(db.list_channels()) >= config.MAX_CHANNELS:
            await ctx.show(*page_channels(f"{NO} حداکثر {config.MAX_CHANNELS} کانال می‌توانید داشته باشید."))
            return
        prompt = (
            "آیدی عددی یا یوزرنیم کانال را ارسال کنید.\n"
            "مثال: @mychannel یا -1001234567890\n\n"
            "توجه: ربات باید در کانال ادمین باشد."
        )
        await _prompt(ctx, "ch_add", "a:ch", prompt)
        return
    if act == "cht":
        if args and args[0].isdigit():
            db.toggle_channel(int(args[0]))
            force_subscribe.clear_cache()
        await ctx.answer()
        await ctx.show(*page_channel(args[0] if args else "0"))
        return
    if act == "chxd":
        await ctx.answer()
        if args and args[0].isdigit():
            db.delete_channel(int(args[0]))
            force_subscribe.clear_cache()
        await ctx.show(*page_channels(f"{OK} کانال حذف شد."))
        return
    if act == "chef":
        cid = args[0] if args else "0"
        field = args[1] if len(args) > 1 else "id"
        c = db.get_channel(int(cid)) if cid.isdigit() else None
        await ctx.answer()
        if not c or field not in ("id", "title", "link"):
            await ctx.show(*page_channels(f"{NO} این کانال پیدا نشد."))
            return
        prompts = {
            "id": "آیدی عددی یا یوزرنیم جدید کانال را ارسال کنید (مثل @mychannel یا -1001234567890):",
            "title": "عنوان جدید کانال را ارسال کنید (همین عنوان روی دکمه‌ها نمایش داده می‌شود):",
            "link": "لینک جدید کانال را ارسال کنید (مثل https://t.me/mychannel):",
        }
        await _prompt(ctx, "ch_edit", f"a:chem:{c.id}", prompts[field], cid=c.id, field=field)
        return

    if await admin_extra.handle_callback(ctx, act, args):
        return

    await ctx.answer()
    await ctx.show(*page_home())


# ============================================================================
# دریافت ورودی از ادمین (حالت‌های ورودی)
# ============================================================================
def parse_channel_ref(text: str):
    s = (text or "").strip()
    s = re.sub(r"^https?://(?:www\.)?(?:t|telegram)\.me/", "", s, flags=re.I).strip("/ ")
    if re.fullmatch(r"-?\d{5,20}", s):
        return int(s) if s.startswith("-") else int("-100" + s)
    s = s.lstrip("@")
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{3,31}", s):
        return "@" + s
    return None


async def resolve_channel(bot, ref) -> Tuple[Optional[tuple], Optional[str]]:
    """((chat_id, title, link, warning, is_admin, username), None) یا (None, خطا)."""
    try:
        chat = await bot.get_chat(ref)
    except TelegramError:
        return None, "کانال پیدا نشد. مطمئن شوید آیدی درست است و ربات در کانال عضو/ادمین است."
    if chat.type == "private":
        return None, "این آیدی مربوط به یک کاربر است، نه کانال."
    title = chat.title or chat.username or str(chat.id)
    username = chat.username or ""
    link = f"https://t.me/{chat.username}" if chat.username else getattr(chat, "invite_link", None)
    is_admin = False
    can_post = True
    try:
        me = await bot.get_chat_member(chat.id, bot.id)
        is_admin = me.status in ("administrator", "creator")
        if me.status == "administrator" and getattr(me, "can_post_messages", True) is False:
            can_post = False
    except TelegramError:
        pass
    if not link and is_admin:
        try:
            link = await bot.export_chat_invite_link(chat.id)
        except TelegramError:
            link = None
    warn = []
    if not is_admin:
        warn.append("ربات هنوز در این کانال ادمین نیست؛ تا ادمین نشود، عضویت کاربرها بررسی نمی‌شود.")
    elif not can_post:
        warn.append("ربات دسترسی ارسال پیام در این کانال را ندارد.")
    if not link:
        warn.append("لینک عضویت پیدا نشد؛ از «ویرایش کانال > تغییر لینک» لینک را وارد کنید.")
    return (chat.id, title, link, " ".join(warn), is_admin and can_post, username), None


Result = Tuple[bool, object]  # (موفق؟، (متن، کیبورد) یا متن خطا)


def _after_param(back: str, spec, note: str) -> Page:
    """بعد از ذخیره‌ی پارامتر به صفحه‌ی مبدأ برمی‌گردیم."""
    pages = {
        "a:rsk": page_risk, "a:snd": page_send, "a:live": page_live, "a:rl": page_rl, "a:subd": page_sub_discount,
        "a:pre": page_pre, "a:sub": page_sub, "a:aik": page_ai_keys, "a:ka": admin_extra.page_ka,
    }
    fn = pages.get(back)
    if fn is not None:
        return fn(note)
    return page_engine_params(spec.group, note)


async def _t_param(ctx: Ctx, st: dict, raw: str) -> Result:
    key = st["key"]
    side = st.get("side")
    spec = config.PARAMS[key]
    val = config.parse_number(spec, raw)
    if val is None:
        return False, "فقط عدد معتبر ارسال کنید." if spec.kind == "float" else "فقط عدد صحیح ارسال کنید."
    if key == "sub_amount" and abs(val * 100 - round(val * 100)) > 1e-6:
        return False, "مبلغ حداکثر ۲ رقم اعشار می‌تواند داشته باشد."
    err = config.validate_param(key, val, lambda k: db.get_e_float(k, side))
    if err:
        return False, err
    db.set_e(key, str(int(val)) if spec.kind == "int" else config.fmt_number(float(val)), side)
    return True, _after_param(st["back"], spec, f"{OK} «{esc(spec.label)}» ذخیره شد.")


async def _t_tpl(ctx: Ctx, st: dict, raw: str) -> Result:
    text = message_text_with_ticks(ctx.update.effective_message).strip()
    side = st["side"]
    ok, msg = tpl.validate_template(text, channel=side.startswith("CH_"))
    if not ok:
        return False, msg
    db.set_template(side, text)
    return True, page_tpl_view(side, f"{OK} قالب {_tpl_title(side)} ذخیره شد.")


async def _t_blk_add(ctx: Ctx, st: dict, raw: str) -> Result:
    text = message_text_with_ticks(ctx.update.effective_message).strip()
    if not text:
        return False, "متن خالی است."
    if len(text) > 1000:
        return False, "متن بسیار طولانی است (حداکثر ۱۰۰۰ کاراکتر)."
    db.add_block(st["bt"], text)
    return True, page_block_home(blk.block_number(st["bt"]), f"{OK} متن جدید اضافه شد.")


async def _t_blk_edit(ctx: Ctx, st: dict, raw: str) -> Result:
    text = message_text_with_ticks(ctx.update.effective_message).strip()
    if not text:
        return False, "متن خالی است."
    if len(text) > 1000:
        return False, "متن بسیار طولانی است (حداکثر ۱۰۰۰ کاراکتر)."
    if not db.get_block(st["bid"]):
        return False, "این متن دیگر وجود ندارد."
    db.update_block(st["bid"], text)
    return True, page_block_item(str(st["bid"]), "e", f"{OK} متن ویرایش شد.")


async def _t_ch_add(ctx: Ctx, st: dict, raw: str) -> Result:
    ref = parse_channel_ref(raw)
    if ref is None:
        return False, "آیدی یا یوزرنیم معتبر نیست. مثال: @mychannel یا -1001234567890"
    if len(db.list_channels()) >= config.MAX_CHANNELS:
        return False, f"حداکثر {config.MAX_CHANNELS} کانال مجاز است."
    info, err = await resolve_channel(ctx.bot, ref)
    if err:
        return False, err
    chat_id, title, link, warn = info[:4]
    if db.channel_exists(str(chat_id)):
        return False, "این کانال قبلاً اضافه شده است."
    cid = db.add_channel(str(chat_id), title, link)
    if cid is None:
        return False, f"حداکثر {config.MAX_CHANNELS} کانال مجاز است."
    force_subscribe.clear_cache()
    note = f"{OK} کانال اضافه شد." + (f"\n{NO} {esc(warn)}" if warn else "")
    return True, page_channel(str(cid), note)


async def _t_ch_edit(ctx: Ctx, st: dict, raw: str) -> Result:
    cid, field = st["cid"], st["field"]
    ch = db.get_channel(cid)
    if not ch:
        return False, "این کانال دیگر وجود ندارد."
    raw = raw.strip()
    if field == "title":
        if not raw or len(raw) > 64:
            return False, "عنوان باید بین ۱ تا ۶۴ کاراکتر باشد."
        db.update_channel(cid, channel_title=raw)
        note = f"{OK} عنوان تغییر کرد."
    elif field == "link":
        if not re.fullmatch(r"https?://\S+", raw):
            return False, "لینک معتبر نیست. مثال: https://t.me/mychannel"
        db.update_channel(cid, invite_link=raw)
        note = f"{OK} لینک تغییر کرد."
    else:
        ref = parse_channel_ref(raw)
        if ref is None:
            return False, "آیدی یا یوزرنیم معتبر نیست. مثال: @mychannel یا -1001234567890"
        info, err = await resolve_channel(ctx.bot, ref)
        if err:
            return False, err
        chat_id, title, link, warn = info[:4]
        if db.channel_exists(str(chat_id), exclude_id=cid):
            return False, "این کانال قبلاً در فهرست وجود دارد."
        db.update_channel(cid, channel_id=str(chat_id), channel_title=title, invite_link=link)
        note = f"{OK} اطلاعات کانال به‌روزرسانی شد." + (f"\n{NO} {esc(warn)}" if warn else "")
    force_subscribe.clear_cache()
    return True, page_channel(str(cid), note)


async def _t_chs_set(ctx: Ctx, st: dict, raw: str) -> Result:
    ref = parse_channel_ref(raw)
    if ref is None:
        return False, "آیدی یا یوزرنیم معتبر نیست. مثال: @mychannel یا -1001234567890"
    info, err = await resolve_channel(ctx.bot, ref)
    if err:
        return False, err
    chat_id, title, link, warn, can_post, username = info
    if not can_post:
        return False, "ربات باید در این کانال ادمین (با دسترسی ارسال پیام) باشد. اول ربات را ادمین کنید و دوباره ارسال کنید."
    db.set("ch_sig_id", str(chat_id))
    db.set("ch_sig_title", title)
    db.set("ch_sig_tag", f"@{username}" if username else (link or ""))
    note = f"{OK} کانال سیگنال تعیین شد. آیدی کانال از این به بعد آخر هر پست نوشته می‌شود."
    return True, page_chs(note)


async def _t_chs_tag(ctx: Ctx, st: dict, raw: str) -> Result:
    v = raw.strip()
    if v == "-":
        v = ""
    if len(v) > 100 or "\n" in v:
        return False, "متن باید یک خط و حداکثر ۱۰۰ کاراکتر باشد."
    db.set("ch_sig_tag", v)
    return True, page_chs(f"{OK} ذخیره شد.")


async def _t_sub_wallet(ctx: Ctx, st: dict, raw: str) -> Result:
    v = raw.strip()
    if not 10 <= len(v) <= 120 or re.search(r"\s", v):
        return False, "آدرس کیف پول باید بین ۱۰ تا ۱۲۰ کاراکتر و بدون فاصله باشد."
    db.set("sub_wallet", v)
    return True, page_sub(f"{OK} آدرس کیف پول ذخیره شد.")


async def _t_sub_tpl(ctx: Ctx, st: dict, raw: str) -> Result:
    text = message_text_with_ticks(ctx.update.effective_message).strip()
    ok, msg = tpl.validate_sub_template(text)
    if not ok:
        return False, msg
    db.set("sub_template", text)
    return True, page_sub_tpl(f"{OK} قالب ذخیره شد.")


async def _t_sub_reject(ctx: Ctx, st: dict, raw: str) -> Result:
    reason = raw.strip()
    if not 3 <= len(reason) <= 300:
        return False, "دلیل باید بین ۳ تا ۳۰۰ کاراکتر باشد."
    rid = st["rid"]
    req = db.get_sub_request(rid)
    if req is not None and getattr(req, "service_type", "bot") == "webapp":
        return False, "این درخواست مربوط به اشتراک تحلیل ساختاری است؛ آن را از پنل مدیریت وب‌اپ بررسی کنید."
    if req is None or not db.decide_sub_request(rid, "rejected", reason):
        return False, "این درخواست دیگر باز نیست."
    pay = payments.by_request(rid)
    if pay is not None:
        payments.set_status(pay["id"], payments.REJECTED, (payments.PENDING, payments.SUSPENDED), reason)
    await subscription.notify_rejected(ctx.bot, req.user_id, reason)
    return True, page_sub_requests(f"{OK} درخواست {rid} رد شد و دلیلش برای کاربر ارسال شد.")


ACCOUNT_ID_RE = re.compile(r"[A-Za-z0-9_-]{8,64}")


def _acc_prompt(slot: int) -> str:
    cur = db.ai_key(slot)
    now = f"\nمقدار فعلی: <code>{esc(cur.account_id)}</code>" if cur and cur.account_id else ""
    return (
        f"Account ID اسلات {slot} را ارسال کنید (مثال: 36df9219-4bf1-5e16-394d-42cb162cb041).\n"
        "آدرس API این کلید از همین Account ID ساخته می‌شود.\n"
        f"برای استفاده از آدرس پیش‌فرض (Fallback) فقط یک خط تیره (-) ارسال کنید.{now}"
    )


async def _t_ai_key(ctx: Ctx, st: dict, raw: str) -> Result:
    key = raw.strip()
    if not 8 <= len(key) <= 400 or re.search(r"\s", key):
        return False, "کلید باید بین ۸ تا ۴۰۰ کاراکتر و بدون فاصله باشد."
    slot = st["slot"]
    db.ai_key_save(slot, key)
    k = db.ai_key(slot)
    if k is not None and not k.account_id:
        # کلید ذخیره شد؛ بلافاصله Account ID همین کلید پرسیده می‌شود
        prompt = _acc_prompt(slot)
        ctx.user_data["state"] = {
            "kind": "ai_acc", "back": f"a:aiks:{slot}", "prompt": prompt, "mid": st.get("mid"), "slot": slot,
        }
        st["keep"] = True
        return True, (f"{OK} کلید ذخیره شد.\n\n{prompt}", kb(back_row(f"a:aiks:{slot}")))
    return True, page_ai_slot(str(slot), f"{OK} کلید ذخیره شد. برای مطمئن شدن، «تست اتصال» را بزنید.")


async def _t_ai_acc(ctx: Ctx, st: dict, raw: str) -> Result:
    v = raw.strip()
    slot = st["slot"]
    if db.ai_key(slot) is None:
        return False, "اول کلید را ثبت کنید."
    if v == "-":
        db.ai_key_set_account(slot, "")
        return True, page_ai_slot(str(slot), f"{OK} Account ID پاک شد؛ این کلید از آدرس پیش‌فرض استفاده می‌کند.")
    if not ACCOUNT_ID_RE.fullmatch(v):
        return False, "Account ID باید ۸ تا ۶۴ کاراکتر و فقط شامل حروف انگلیسی، عدد و خط تیره باشد."
    db.ai_key_set_account(slot, v)
    return True, page_ai_slot(str(slot), f"{OK} Account ID ذخیره شد. برای مطمئن شدن، «تست اتصال» را بزنید.")


async def _t_ai_name(ctx: Ctx, st: dict, raw: str) -> Result:
    name = " ".join(raw.split())
    if not 1 <= len(name) <= 20:
        return False, "اسم باید بین ۱ تا ۲۰ کاراکتر باشد."
    db.ai_key_rename(st["slot"], name)
    return True, page_ai_slot(str(st["slot"]), f"{OK} اسم تغییر کرد.")


async def _t_ai_url(ctx: Ctx, st: dict, raw: str) -> Result:
    v = raw.strip()
    if v == "-":
        db.set("ai_url", "")
        return True, page_ai_keys(f"{OK} آدرس به مقدار فایل .env برگشت.")
    if not re.fullmatch(r"https?://\S{4,480}", v):
        return False, "آدرس معتبر نیست. باید با http:// یا https:// شروع شود."
    db.set("ai_url", v)
    return True, page_ai_keys(f"{OK} آدرس سرویس ذخیره شد.")


async def _t_ai_model_add(ctx: Ctx, st: dict, raw: str) -> Result:
    v = raw.strip()
    if not 3 <= len(v) <= 100 or re.search(r"\s", v):
        return False, "اسم مدل باید بین ۳ تا ۱۰۰ کاراکتر و بدون فاصله باشد."
    models = db.ai_models()
    if v in models:
        return False, "این مدل قبلاً در لیست هست."
    if len(models) >= config.AI_MAX_MODELS:
        return False, f"حداکثر {config.AI_MAX_MODELS} مدل مجاز است."
    models.append(v)
    db.set_ai_models(models)
    return True, page_ai_models(f"{OK} مدل اضافه شد.")


async def _t_ai_model(ctx: Ctx, st: dict, raw: str) -> Result:
    v = raw.strip()
    if not 3 <= len(v) <= 100 or re.search(r"\s", v):
        return False, "اسم مدل باید بین ۳ تا ۱۰۰ کاراکتر و بدون فاصله باشد."
    db.set_e("ai_model", v, st.get("side"))
    return True, page_ai(f"{OK} مدل ذخیره شد.")


async def _t_eng_import(ctx: Ctx, st: dict, raw: str) -> Result:
    text = (ctx.update.effective_message.text or "").strip()
    if not text.startswith("{"):
        return False, "فایل JSON را به‌صورت Document ارسال کنید (یا متن JSON را که با { شروع می‌شود paste کنید)."
    ok, message = engine_io.import_settings(text.encode("utf-8"))
    if not ok:
        return False, message
    return True, page_eng(f"{OK} تنظیمات وارد شد.\n{esc(message)}")


TEXT_HANDLERS = {
    "param": _t_param,
    "tpl": _t_tpl,
    "blk_add": _t_blk_add,
    "blk_edit": _t_blk_edit,
    "ch_add": _t_ch_add,
    "ch_edit": _t_ch_edit,
    "chs_set": _t_chs_set,
    "chs_tag": _t_chs_tag,
    "sub_wallet": _t_sub_wallet,
    "sub_tpl": _t_sub_tpl,
    "sub_reject": _t_sub_reject,
    "ai_key": _t_ai_key,
    "ai_acc": _t_ai_acc,
    "ai_name": _t_ai_name,
    "ai_url": _t_ai_url,
    "ai_model": _t_ai_model,
    "ai_model_add": _t_ai_model_add,
    "eng_import": _t_eng_import,
}
TEXT_HANDLERS.update(admin_extra.TEXT_HANDLERS)
# حالت‌هایی که علاوه بر متن، تصویر / فایل هم می‌پذیرند
MEDIA_KINDS = {"eng_import", "db_restore"}

async def _respond(ctx: Ctx, st: dict, msg, ok: bool, payload) -> None:
    """نتیجه‌ی پردازش ورودی ادمین: همیشه پیام جدید ارسال می‌شود."""
    if ok:
        if not st.get("keep"):
            ctx.user_data.pop("state", None)
        text, markup = payload
        m = await ctx.show_input(st.get("mid"), text, markup)
        if st.get("keep") and m is not None and ctx.user_data.get("state"):
            ctx.user_data["state"]["mid"] = m.message_id
        return
    text = f"{NO} {esc(payload)}\n\n{st['prompt']}"
    await ctx.show_input(st.get("mid"), text, kb(back_row(st["back"])))


async def handle_text(ctx: Ctx) -> bool:
    """متن ادمین را اگر در حالت ورودی باشد پردازش می‌کند. True یعنی پردازش شد."""
    st = ctx.user_data.get("state")
    if not st:
        return False
    msg0 = ctx.update.effective_message
    if st.get("kind") in analyst.KINDS and msg0 is not None and msg0.text:
        await analyst.run(ctx, msg0.text)
        return True
    handler = TEXT_HANDLERS.get(st.get("kind"))
    msg = ctx.update.effective_message
    if handler is None or msg is None or msg.text is None:
        ctx.user_data.pop("state", None)
        return False
    try:
        ok, payload = await handler(ctx, st, msg.text)
    except Exception:
        log.exception("admin text handler failed")
        ok, payload = False, "یک مشکل پیش آمد، دوباره امتحان کنید."
    await _respond(ctx, st, msg, ok, payload)
    return True


async def handle_media(ctx: Ctx) -> bool:
    """تصویر / فایل ادمین در حالت Import. True یعنی پردازش شد."""
    st = ctx.user_data.get("state")
    msg = ctx.update.effective_message
    if not st or msg is None or st.get("kind") not in MEDIA_KINDS:
        return False
    kind = st["kind"]
    ok, payload = False, "این نوع پیام در این بخش پذیرفته نمی‌شود."
    try:
        if kind == "db_restore":
            doc = msg.document
            if doc is None:
                ok, payload = False, "فایل JSON بکاپ را به‌صورت Document ارسال کنید."
            elif doc.file_size and doc.file_size > 40 * 1024 * 1024:
                ok, payload = False, "فایل بسیار بزرگ است."
            else:
                try:
                    f = await ctx.bot.get_file(doc.file_id)
                    data = bytes(await f.download_as_bytearray())
                except TelegramError:
                    ok, payload = False, "دریافت فایل ممکن نشد، دوباره ارسال کنید."
                else:
                    ok, payload = admin_extra.handle_restore_file(ctx, data)
        else:  # eng_import
            doc = msg.document
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
                    done, message = engine_io.import_settings(data)
                    if done:
                        ok, payload = True, page_eng(f"{OK} تنظیمات وارد شد.\n{esc(message)}")
                    else:
                        ok, payload = False, message
    except Exception:
        log.exception("admin media handler failed")
        ok, payload = False, "یک مشکل پیش آمد، دوباره امتحان کنید."
    await _respond(ctx, st, msg, ok, payload)
    return True


# ============================================================================
# هندلرهای مربوط به BSC (باید بعد از تعریف توابع ثبت شوند)
# ============================================================================
async def _t_sub_bsc_wallet(ctx: Ctx, st: dict, raw: str) -> Result:
    v = raw.strip()
    if not v.startswith("0x") or len(v) != 42:
        return False, "آدرس BSC معتبر نیست. باید ۴۲ کاراکتر و با 0x شروع شود."
    db.set("bsc_wallet", v)
    return True, page_sub(f"{OK} آدرس ولت BSC ذخیره شد.")


async def _t_sub_bsc_api(ctx: Ctx, st: dict, raw: str) -> Result:
    v = raw.strip()
    if len(v) < 10:
        return False, "کلید API معتبر نیست."
    db.set("bscscan_api_key", v)
    return True, page_sub(f"{OK} کلید API (NodeReal) ذخیره شد.")


# ثبت در TEXT_HANDLERS — این خطوط باید در انتهای فایل باشند (بعد از تعریف توابع)
TEXT_HANDLERS["sub_bsc_wallet"] = _t_sub_bsc_wallet
TEXT_HANDLERS["sub_bsc_api"] = _t_sub_bsc_api