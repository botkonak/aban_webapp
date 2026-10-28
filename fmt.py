"""
فرمت‌بندی متن تلگرام (آبان ترید).

ادمین هر جا که متنی ویرایش می‌کند (قالب‌ها، بلوک‌ها، پیام گروهی، ...) می‌تواند از این فرمت‌ها استفاده کند:

    **متن**        Bold
    __متن__        Italic
    ___متن___      Underline
    ~~متن~~        Strikethrough
    ||متن||        Spoiler
    `متن`          Inline Code / Monospace (قابل کپی)
    ```متن```      Code Block   (با زبان:  ```python  خط‌بعد کد ```)
    > متن          Blockquote (هر خط جدا با > شروع می‌شود)
    >> متن         Expandable Blockquote
    [متن](لینک)    لینک

علاوه بر تایپ مستقیم، اگر ادمین متن را با خود فرمت‌بندی تلگرام (Bold، Italic، ...) بفرستد هم
به همین نشانه‌ها تبدیل می‌شود (entities_to_markers) و موقع ارسال دوباره به HTML تلگرام برمی‌گردد.
"""
from __future__ import annotations

import html
import re
from typing import Iterable, List, Optional, Tuple

# نشانه‌های داخلی برای Blockquote هنگام تبدیل entity ها (کاراکترهای Private Use)
_QS, _QX, _QE = "\ue000", "\ue002", "\ue001"

_INLINE_MARKERS = {
    "bold": "**",
    "italic": "__",
    "underline": "___",
    "strikethrough": "~~",
    "spoiler": "||",
    "code": "`",
}
_SUPPORTED = set(_INLINE_MARKERS) | {"pre", "blockquote", "expandable_blockquote", "text_link"}


# ----------------------------------------------------------------------------
# Entity -> نشانه‌ها
# ----------------------------------------------------------------------------
def _unit(raw: bytes, i: int) -> str:
    return raw[i * 2:i * 2 + 2].decode("utf-16-le", errors="ignore")


def entities_to_markers(text: Optional[str], entities: Optional[Iterable]) -> str:
    """متن پیام + entity های تلگرام (آفست UTF-16) را به متن دارای نشانه‌ها تبدیل می‌کند."""
    text = text or ""
    ents = [e for e in (entities or []) if getattr(e, "type", None) in _SUPPORTED and e.length > 0]
    if not text or not ents:
        return text
    raw = text.encode("utf-16-le")
    total = len(raw) // 2
    events: List[Tuple[int, int, int, int, str]] = []   # (pos, kind, tiebreak, idx, marker)
    for idx, e in enumerate(ents):
        start, end = max(0, int(e.offset)), min(total, int(e.offset) + int(e.length))
        if end <= start:
            continue
        length = end - start
        t = e.type
        if t in _INLINE_MARKERS:
            # نشانه‌های درون‌خطی از خط بعد عبور نمی‌کنند و نباید به فاصله بچسبند: هر خط جدا و بدون فاصله‌ی کناری علامت می‌خورد
            marker = _INLINE_MARKERS[t]
            seg_start = start
            for i in range(start, end + 1):
                if i < end and raw[i * 2:i * 2 + 2] != b"\n\x00":
                    continue
                a, b = seg_start, i
                while a < b and _unit(raw, a).isspace():
                    a += 1
                while b > a and _unit(raw, b - 1).isspace():
                    b -= 1
                if b > a:
                    events.append((a, 1, -(b - a), idx, marker))
                    events.append((b, 0, b - a, -idx, marker))
                seg_start = i + 1
            continue
        if t == "pre":
            lang = (getattr(e, "language", None) or "").strip()
            opener, closer = "```" + lang + "\n", "```"
        elif t == "blockquote":
            opener, closer = _QS, _QE
        elif t == "expandable_blockquote":
            opener, closer = _QX, _QE
        else:  # text_link
            url = getattr(e, "url", None) or ""
            if not url:
                continue
            opener, closer = "[", f"]({url})"
        events.append((start, 1, -length, idx, opener))
        events.append((end, 0, length, -idx, closer))
    events.sort(key=lambda x: (x[0], x[1], x[2], x[3]))
    out: List[str] = []
    cur = 0
    for pos, _kind, _tie, _idx, marker in events:
        if pos > cur:
            out.append(raw[cur * 2:pos * 2].decode("utf-16-le", errors="ignore"))
            cur = pos
        out.append(marker)
    if cur < total:
        out.append(raw[cur * 2:].decode("utf-16-le", errors="ignore"))
    res = "".join(out)

    def quote(prefix: str):
        def fn(m: "re.Match[str]") -> str:
            return "\n".join(prefix + ln for ln in m.group(1).split("\n"))
        return fn

    res = re.sub(_QS + r"(.*?)" + _QE, quote("> "), res, flags=re.S)
    res = re.sub(_QX + r"(.*?)" + _QE, quote(">> "), res, flags=re.S)
    return res.replace(_QS, "").replace(_QX, "").replace(_QE, "")


# ----------------------------------------------------------------------------
# نشانه‌ها -> HTML تلگرام
# ----------------------------------------------------------------------------
_TOK_RE = re.compile("\x00(\\d+)\x00")
_A_OPEN_RE = re.compile(r"<a\s[^>]*>")
_PRE_RE = re.compile(r"```(?:([A-Za-z0-9_+#.\-]*)\n)?(.*?)```", re.S)
_CODE_RE = re.compile(r"`([^`\n]+)`")
_Q_RE = re.compile(r"^(&gt;&gt;|&gt;)[ \t]?(.*)$")
_LINK_RE = re.compile(r"\[([^\]\n]+)\]\(((?:https?|tg)://[^\s)]+)\)")
_INLINE = (
    (re.compile(r"\*\*(?=\S)([^\n]+?)(?<=\S)\*\*"), "b"),
    (re.compile(r"___(?=\S)([^\n]+?)(?<=\S)___"), "u"),
    (re.compile(r"__(?=\S)([^\n]+?)(?<=\S)__"), "i"),
    (re.compile(r"~~(?=\S)([^\n]+?)(?<=\S)~~"), "s"),
    (re.compile(r"\|\|(?=\S)([^\n]+?)(?<=\S)\|\|"), "tg-spoiler"),
)
_TAG_RE = re.compile(r"<(/?)(b|i|u|s|tg-spoiler|a|code|pre|blockquote)(?:\s[^>]*)?>")


def _balanced(s: str) -> bool:
    stack: List[str] = []
    for m in _TAG_RE.finditer(s):
        closing, name = m.group(1) == "/", m.group(2)
        if not closing:
            if name == "blockquote" and "blockquote" in stack:
                return False
            stack.append(name)
        else:
            if not stack or stack.pop() != name:
                return False
    return not stack


def apply(text: str, full: bool = True) -> str:
    """
    متن «escape شده‌ی HTML» (که نشانه‌ها توش مانده) را به HTML تلگرام تبدیل می‌کند.
    full=False فقط Monospace و Code Block را تبدیل می‌کند (حالت اضطراری وقتی تگ‌ها نامتوازن شده‌اند).
    """
    if not text:
        return text
    stash: List[str] = []

    def tok(fragment: str) -> str:
        stash.append(fragment)
        return f"\x00{len(stash) - 1}\x00"

    s = _A_OPEN_RE.sub(lambda m: tok(m.group(0)), text)
    s = s.replace("</a>", tok("</a>"))

    def pre(m: "re.Match[str]") -> str:
        lang, body = (m.group(1) or "").strip(), m.group(2)
        if body.startswith("\n"):
            body = body[1:]
        if body.endswith("\n"):
            body = body[:-1]
        if lang:
            return tok(f'<pre><code class="language-{lang}">{body}</code></pre>')
        return tok(f"<pre>{body}</pre>")

    s = _PRE_RE.sub(pre, s)

    def code(m: "re.Match[str]") -> str:
        inner = m.group(1)
        if "<" in inner or "\x00" in inner:      # داخل Monospace تگ مجاز نیست
            return m.group(0)
        return tok(f"<code>{inner}</code>")

    s = _CODE_RE.sub(code, s)

    if full:
        s = _quotes(s)
        s = _LINK_RE.sub(
            lambda m: tok(f'<a href="{m.group(2).replace(chr(34), "&quot;")}">') + m.group(1) + tok("</a>"), s
        )
        for rx, tag in _INLINE:
            s = rx.sub(lambda m, t=tag: f"<{t}>{m.group(1)}</{t}>", s)

    while _TOK_RE.search(s):
        s = _TOK_RE.sub(lambda m: stash[int(m.group(1))], s)
    if full and not _balanced(s):
        return apply(text, full=False)
    return s


def _quotes(s: str) -> str:
    lines = s.split("\n")
    out: List[str] = []
    i = 0
    while i < len(lines):
        m = _Q_RE.match(lines[i])
        if not m:
            out.append(lines[i])
            i += 1
            continue
        expandable = m.group(1) == "&gt;&gt;"
        buf = [m.group(2)]
        i += 1
        while i < len(lines):
            m2 = _Q_RE.match(lines[i])
            if not m2 or (m2.group(1) == "&gt;&gt;") != expandable:
                break
            buf.append(m2.group(2))
            i += 1
        if not "".join(buf).strip():
            continue                      # Quote خالی نمایش داده نمی‌شود
        tag = "blockquote expandable" if expandable else "blockquote"
        out.append(f"<{tag}>" + "\n".join(buf) + "</blockquote>")
    return "\n".join(out)


def to_html(marker_text: str) -> str:
    """متن خام ادمین (با نشانه‌ها) -> HTML امن تلگرام."""
    return apply(html.escape(marker_text or "", quote=False))


def strip_markers(marker_text: str) -> str:
    """متن ساده (بدون نشانه‌ها و بدون تگ) برای مواقعی که HTML پذیرفته نشد."""
    return html.unescape(re.sub(r"<[^>]+>", "", to_html(marker_text)))


HELP = (
    "فرمت‌های تلگرام (هر جا که متن را ویرایش می‌کنید):\n"
    "**متن** Bold | __متن__ Italic | ___متن___ Underline | ~~متن~~ Strikethrough | ||متن|| Spoiler\n"
    "`متن` Monospace (قابل کپی) | ```متن``` Code Block | > متن Quote | >> متن Quote بازشونده | [متن](لینک)\n"
    "می‌توانید فرمت‌بندی خود تلگرام (Bold، Italic، ...) را نیز روی متن اعمال کنید."
)
