"""قالب‌های آبان ترید (LONG/SHORT، قیمت، آنالیز): اعتبارسنجی، جایگزینی Placeholder ها و ساخت متن نهایی (HTML)."""
from __future__ import annotations

import html
import re
from typing import Dict, List, Optional, Tuple

import config
import fmt
from models import Signal

PLACEHOLDER_RE = re.compile(r"\{([A-Za-z0-9_]+)\}")
# نشانه‌های فرمت (Quote، Bold، Italic، ...) و فاصله به‌تنهایی «متن» حساب نمی‌شوند؛ خطی که فقط بلوک خالی + نشانه دارد حذف می‌شود
_MARK_ONLY_RE = re.compile(r"[\s>*_~|`]+")
TICK_RE = re.compile(r"`([^`\n]+)`")
BLOCK_NAMES = set(config.BLOCK_TYPES)

# جایگزین موقت {lastprice}؛ برای به‌روزرسانی زنده‌ی قیمت داخل متن آماده‌ی سیگنال
LIVE_TOKEN = "⁣LIVEPRICE⁣"


def _esc(s: str) -> str:
    return html.escape(s, quote=False)


def validate_template(text: str, channel: bool = False) -> Tuple[bool, str]:
    """قالب معتبر: حتماً tp1/tp2/tp3 داشته باشد و Placeholder ناشناخته نداشته باشد.
    channel=True: قالب کانال؛ {lastprice} مجاز نیست (قیمت داخل کانال زنده نمی‌شود)."""
    t = (text or "").strip()
    if not t:
        return False, "قالب خالی است."
    if len(t) > 3500:
        return False, "قالب بسیار طولانی است (حداکثر ۳۵۰۰ کاراکتر)."
    names = [m.group(1).lower() for m in PLACEHOLDER_RE.finditer(t)]
    missing = [n for n in config.REQUIRED_PLACEHOLDERS if n not in names]
    if missing:
        return False, "این Placeholder ها حتماً باید در قالب باشند: " + " ".join("{%s}" % m for m in missing)
    allowed = config.CHANNEL_PLACEHOLDERS if channel else config.ALLOWED_PLACEHOLDERS
    unknown = sorted({n for n in names if n not in allowed})
    if unknown:
        if channel and unknown == ["lastprice"]:
            return False, "در قالب کانال نمی‌شود از {lastprice} استفاده کرد، چون قیمت داخل کانال به‌روز نمی‌شود."
        return False, "Placeholder ناشناخته: " + " ".join("{%s}" % u for u in unknown)
    return True, ""


def validate_sub_template(text: str) -> Tuple[bool, str]:
    """قالب خرید اشتراک: هر دو پترن {wallet} و {amount} الزامی است."""
    t = (text or "").strip()
    if not t:
        return False, "قالب خالی است."
    if len(t) > 3000:
        return False, "قالب بسیار طولانی است (حداکثر ۳۰۰۰ کاراکتر)."
    names = [m.group(1).lower() for m in PLACEHOLDER_RE.finditer(t)]
    missing = [n for n in config.SUB_REQUIRED_PLACEHOLDERS if n not in names]
    if missing:
        return False, "این Placeholder ها حتماً باید در قالب باشند: " + " ".join("{%s}" % m for m in missing)
    unknown = sorted({n for n in names if n not in config.SUB_PLACEHOLDERS})
    if unknown:
        return False, "Placeholder ناشناخته: " + " ".join("{%s}" % u for u in unknown)
    return True, ""


def fmt_price(x: float, decimals: int) -> str:
    s = f"{x:.{max(0, decimals)}f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s or "0"


def _calc_rr(sig: Signal) -> str:
    """ریسک به ریوارد کل سیگنال بر اساس آخرین TP (TP3)."""
    risk = abs(sig.entry - sig.sl)
    if risk <= 0:
        return "1"
    reward = abs(sig.tp3 - sig.entry)
    rr = reward / risk
    rr_rounded = round(rr, 1)
    if rr_rounded < 0.1:
        rr_rounded = 0.1
    if rr_rounded == int(rr_rounded):
        return str(int(rr_rounded))
    return f"{rr_rounded:.1f}"


def signal_values(sig: Signal) -> Dict[str, str]:
    d = sig.decimals
    return {
        "symbol": sig.symbol,
        "entry": fmt_price(sig.entry, d),
        "lastprice": fmt_price(sig.entry, d),
        "side": sig.side,
        "sl": fmt_price(sig.sl, d),
        "tp1": fmt_price(sig.tp1, d),
        "tp2": fmt_price(sig.tp2, d),
        "tp3": fmt_price(sig.tp3, d),
        "risk": config.fmt_number(round(sig.risk, 1)),
        "leverage": str(int(sig.leverage)),
        "rr": _calc_rr(sig),
        "aiinfo": (sig.ai_info or "").strip(),
    }


def _bot_link(bot_username: str) -> str:
    u = re.sub(r"[^A-Za-z0-9_]", "", bot_username or "")
    return f'<a href="https://t.me/{u}">@{u}</a>'


def render(
    template: str,
    values: Dict[str, str],
    blocks: Dict[str, str],
    bot_username: str,
) -> str:
    """
    - Placeholder هایی که در قالب نباشند اصلاً نمایش داده نمی‌شوند.
    - بک‌تیک‌ها به Monospace تبدیل می‌شوند (تلگرام: <code>).
    - {botid} لینک کلیک‌پذیر است.
    - خطی که فقط شامل بلوک خالی باشد حذف می‌شود.
    - خطی که {aiinfo} دارد و توضیح AI خالی است (AI خاموش است یا توضیحی نداده است) کامل حذف می‌شود.
    """
    template = (template or "").replace("\r\n", "\n").replace("\r", "\n")
    out_lines: List[str] = []
    removed_any = False

    for line in template.split("\n"):
        parts = PLACEHOLDER_RE.split(line)
        rendered: List[str] = []
        has_block = False
        block_text_total = ""
        literal_nonblank = False
        drop_line = False

        for i, piece in enumerate(parts):
            if i % 2 == 0:
                if _MARK_ONLY_RE.sub("", piece):
                    literal_nonblank = True
                rendered.append(_esc(piece))
                continue
            name = piece.lower()
            if name in BLOCK_NAMES:
                has_block = True
                txt = blocks.get(name, "") or ""
                block_text_total += txt.strip()
                rendered.append(_esc(txt))
            elif name == "botid":
                literal_nonblank = True
                rendered.append(_bot_link(bot_username))
            elif name == "aiinfo":
                txt = (values.get("aiinfo") or "").strip()
                if not txt:
                    drop_line = True
                else:
                    literal_nonblank = True
                    rendered.append(_esc(txt))
            elif name in values:
                literal_nonblank = True
                rendered.append(_esc(values[name]))
            else:
                # ناشناخته: همان‌طور که هست نمایش داده شود
                literal_nonblank = True
                rendered.append(_esc("{%s}" % piece))

        if drop_line:
            removed_any = True
            continue
        if has_block and not block_text_total and not literal_nonblank:
            removed_any = True
            continue
        out_lines.append("".join(rendered))

    text = "\n".join(out_lines)
    if removed_any:
        text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip("\n")

    # همه‌ی فرمت‌های تلگرام (Bold، Italic، Underline، Strikethrough، Spoiler، Monospace، Code Block، Quote، لینک)
    return fmt.apply(text)


def render_sub(template: str, wallet: str, amount: float, final: Optional[float] = None) -> str:
    """
    متن خرید اشتراک (HTML) با جایگزینی {wallet} و {amount}.
    final: مبلغ با تخفیف؛ اگر داده شود قیمت اصلی خط‌خورده و بعدش قیمت با تخفیف نمایش داده می‌شود.
    """
    shown = fmt_price(amount, 2)
    if final is not None:
        shown = f"~~{shown}~~ {fmt_price(final, 2)}"
    return render(template, {"wallet": wallet, "amount": shown}, {}, "")


def strip_lastprice(template: str) -> str:
    """
    قالب ربات را برای کانال آماده می‌کند (وقتی قالب کانال خالی است): قیمت داخل کانال به‌روز نمی‌شود؛ پس
    خطی که فقط {lastprice} دارد حذف می‌شود و اگر خط الگوهای دیگری هم داشت فقط خود {lastprice} برداشته می‌شود.
    """
    out: List[str] = []
    for line in (template or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        names = [m.group(1).lower() for m in PLACEHOLDER_RE.finditer(line)]
        if "lastprice" in names:
            if all(n == "lastprice" for n in names):
                continue
            line = re.sub(r"\{lastprice\}", "", line, flags=re.I).rstrip()
        out.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip("\n")


def append_tag(text: str, tag: str) -> str:
    """آیدی کانال را در آخرین خط پست می‌گذارد (اگر کانالی تعیین شده باشد)."""
    tag = (tag or "").strip()
    if not tag:
        return text
    return f"{text}\n{_esc(tag)}"


def plain_fallback(rendered_html: str) -> str:
    """نسخه متن ساده (اگر تلگرام HTML را نپذیرفت)."""
    return html.unescape(re.sub(r"<[^>]+>", "", rendered_html))


def render_signal(sig: Signal, template: str, bot_username: str, pick_block, live: bool = False) -> str:
    """live=True: به‌جای {lastprice} توکن LIVE_TOKEN گذاشته می‌شود تا بعداً با قیمت لحظه‌ای عوض شود."""
    blocks = {t: pick_block(t) for t in config.BLOCK_TYPES}
    values = signal_values(sig)
    if live:
        values["lastprice"] = LIVE_TOKEN
    return render(template, values, blocks, bot_username)


def sample_signal(side: str) -> Signal:
    info = "واگرایی مثبت RSI + فشردگی باندها." if side.upper() == "LONG" else "واگرایی منفی RSI + شکست حمایت."
    if side.upper() == "LONG":
        return Signal(
            symbol="BTC", pair="BTC/USDT:USDT", side="LONG", entry=64250.5,
            sl=63120.3, tp1=64805.2, tp2=65400.9, tp3=65980.4,
            risk=2.5, leverage=12, decimals=1, score=80, ai_info=info,
        )
    return Signal(
        symbol="BTC", pair="BTC/USDT:USDT", side="SHORT", entry=64250.5,
        sl=65380.7, tp1=63695.8, tp2=63100.1, tp3=62520.6,
        risk=2.5, leverage=12, decimals=1, score=80, ai_info=info,
    )


# ============================================================================
# قالب‌های پیام قیمت و آنالیز: {متغیر} با مقدار پویا؛ اگر مقدار موجود نبود --- نمایش داده می‌شود
# ============================================================================
_SEP_RE = re.compile(r"\s*[━─═\-_=]{5,}\s*")


def validate_msg_template(text: str, allowed) -> Tuple[bool, str]:
    t = (text or "").strip()
    if not t:
        return False, "قالب خالی است."
    if len(t) > 3800:
        return False, "قالب بسیار طولانی است (حداکثر ۳۸۰۰ کاراکتر)."
    names = {m.group(1).lower() for m in PLACEHOLDER_RE.finditer(t)}
    unknown = sorted(n for n in names if n not in allowed)
    if unknown:
        return False, "Placeholder ناشناخته: " + " ".join("{%s}" % u for u in unknown)
    return True, ""


def prune_template(text: str, excluded, always=frozenset({"date", "time"})) -> str:
    """
    خطوط / آیتم‌هایی که فقط شامل متغیرهای حذف‌شده (excluded) هستند را برمی‌دارد.
      - آیتم = یک خط + خطوط تو‌رفته‌ی بعدش (مثل «🥇 طلای ۱۸ عیار» با خطوط قیمتش)
      - بخش = عنوان بین دو خط جداکننده (━━━) + آیتم‌های زیرش؛ اگر همه‌ی آیتم‌های یک بخش حذف شوند کل بخش حذف می‌شود
      - پاراگراف آخر (پاورقی) که متغیر قابل‌انتخاب ندارد همیشه می‌ماند
    """
    excluded = {str(x).lower() for x in excluded}
    always = {str(x).lower() for x in always}
    lines = (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    n = len(lines)
    phs = [{m.lower() for m in PLACEHOLDER_RE.findall(ln)} for ln in lines]
    sel = [{p for p in ph if p not in always} for ph in phs]
    keep = [True] * n

    i = 0
    while i < n:                                   # ۱) حذف آیتم‌ها / خطوط
        if not lines[i].strip() or lines[i][0] in " \t":
            i += 1
            continue
        j = i + 1
        while j < n and lines[j].strip() and lines[j][0] in " \t":
            j += 1
        all_sel: set = set()
        for k in range(i, j):
            all_sel |= sel[k]
        if all_sel and all_sel <= excluded:
            for k in range(i, j):
                keep[k] = False
        else:
            for k in range(i, j):
                if sel[k] and sel[k] <= excluded:
                    keep[k] = False
        i = j

    headers = []                                   # ۲) بخش‌ها
    for t in range(n):
        ln = lines[t]
        if not ln.strip() or _SEP_RE.fullmatch(ln) or phs[t]:
            continue
        if t + 1 < n and _SEP_RE.fullmatch(lines[t + 1]):
            start = t - 1 if t > 0 and _SEP_RE.fullmatch(lines[t - 1]) else t
            headers.append((start, t, t + 1))
    footer = n
    last_blank = max((k for k in range(n) if not lines[k].strip()), default=-1)
    if last_blank >= 0 and last_blank + 1 < n and not any(sel[k] for k in range(last_blank + 1, n)):
        footer = last_blank + 1
    for idx, (start, _title, hend) in enumerate(headers):
        body_start = hend + 1
        body_end = headers[idx + 1][0] if idx + 1 < len(headers) else footer
        body_end = max(body_end, body_start)
        orig = any(sel[k] for k in range(body_start, body_end))
        left = any(sel[k] and keep[k] for k in range(body_start, body_end))
        if orig and not left:
            for k in range(start, body_end):
                keep[k] = False

    header_end = {h[2] for h in headers}
    lead_sep = {h[0] for h in headers if h[0] != h[1]}
    out: list = []
    last_idx = -1
    for k in range(n):                             # ۳) جمع‌کردن خطوط خالی و جداکننده‌های اضافی
        if not keep[k]:
            continue
        if not lines[k].strip():
            if not out or not out[-1].strip() or last_idx in header_end:
                continue
        elif k in lead_sep:
            prev = next((x for x in reversed(out) if x.strip()), None)
            if prev is not None and _SEP_RE.fullmatch(prev):
                continue
        out.append(lines[k])
        last_idx = k
    return "\n".join(out).strip("\n")


def drop_paragraphs_with(text: str, names) -> str:
    """پاراگراف‌هایی (جداشده با خط خالی) که یکی از متغیرهای names را دارند حذف می‌شوند."""
    names = {str(x).lower() for x in names}
    paras = re.split(r"\n\s*\n", (text or "").replace("\r\n", "\n"))
    kept = [p for p in paras if not ({m.lower() for m in PLACEHOLDER_RE.findall(p)} & names)]
    return "\n\n".join(kept)


def render_template(
    template_text: str,
    context: Dict[str, object],
    excluded=(),
    drop_if_missing=(),
) -> str:
    """
    جایگزینی متغیرهای پویا در قالب پیام (قیمت / آنالیز) و تبدیل به HTML تلگرام.
      - متغیری که مقدارش موجود نباشد (یا خالی باشد) به‌جای آن --- می‌آید.
      - excluded: متغیرهایی که نباید نمایش داده شوند (خط/آیتم/بخش مربوط حذف می‌شود).
      - drop_if_missing: اگر مقدار این متغیرها موجود نباشد کل پاراگراف حاوی آن‌ها حذف می‌شود (مثل {ai_analysis}).
    """
    text = (template_text or "").replace("\r\n", "\n").replace("\r", "\n")
    if excluded:
        text = prune_template(text, excluded)
    missing = [v for v in drop_if_missing if not str(context.get(v) or "").strip()]
    if missing:
        text = drop_paragraphs_with(text, missing)
    parts = PLACEHOLDER_RE.split(text)
    out: List[str] = []
    for i, piece in enumerate(parts):
        if i % 2 == 0:
            out.append(_esc(piece))
            continue
        val = context.get(piece.lower())
        val = "" if val is None else str(val)
        out.append(_esc(val) if val.strip() else "---")
    return fmt.apply("".join(out).strip("\n"))
