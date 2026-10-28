"""
ارسال امن پیام (HTML/ساده/عکس/فایل) با «صف پیام‌ها» (Message Queue).

هیچ پیامی مستقیم به تلگرام فرستاده نمی‌شود؛ همه‌ی ارسال‌ها (سیگنال، اعلان، پیام گروهی، ...) به‌صورت یک Job در یک صف
اولویت‌دار سراسری قرار می‌گیرند و یک حلقه‌ی پس‌زمینه (Worker) آن‌ها را با سرعت کنترل‌شده (config.BROADCAST_PER_SECOND در ثانیه) می‌فرستد.
توابع ارسال (send_html / send_plain / send_content / send_photo_html / mass_send) همان امضا و همان خروجی قبلی را دارند
(شناسه‌ی پیام یا None)؛ فقط به‌جای ارسال مستقیم، نتیجه‌ی Job را از صف منتظر می‌مانند. پس بقیه‌ی کد بدون تغییر کار می‌کند.

اولویت‌ها:
  PRIO_HIGH   (0)  سیگنال‌ها و اعلان‌های ادمین (هر پیام به ADMIN_UID به‌طور پیش‌فرض)
  PRIO_NORMAL (1)  پیام‌های تکی (اشتراک، پشتیبانی، ...)
  PRIO_LOW    (2)  ارسال گروهی (mass_send)
مدیریت خطا:
  RetryAfter (Flood Wait)  کل صف به‌اندازه‌ی زمان خواسته‌شده متوقف می‌شود، همان پیام سرجایش برمی‌گردد و بعد از پایان ادامه می‌یابد.
  Forbidden / BadRequest   پیام دائماً ناموفق است؛ از صف حذف می‌شود و Worker سراغ پیام بعدی می‌رود.
  NetworkError             تا ۳ بار تلاش دوباره.
  اگر Job بیشتر از config.QUEUE_MAX_WAIT ثانیه در صف بماند لغو می‌شود (پیام کهنه ارسال نمی‌شود).
"""
from __future__ import annotations

import asyncio
import io
import itertools
import logging
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set, Tuple

from telegram.constants import ParseMode
from telegram.error import BadRequest, Forbidden, NetworkError, RetryAfter, TelegramError

import config
import fmt
import templates
from ui import NO_PREVIEW, is_flooded, menu_markup_for, retry_seconds, set_flood

log = logging.getLogger("broadcast")

Progress = Callable[[int, int, int], Awaitable[None]]

CAPTION_LIMIT = 1024
TEXT_LIMIT = 3800          # سقف تلگرام ۴۰۹۶ است؛ حاشیه برای موجودیت‌های HTML و ایموجی در نظر گرفته می‌شود

PRIO_HIGH = 0
PRIO_NORMAL = 1
PRIO_LOW = 2

MAX_INFLIGHT = 60          # حداکثر ارسال هم‌زمان (سرعت میانگین را BROADCAST_PER_SECOND تعیین می‌کند)


# ============================================================================
# صف
# ============================================================================
@dataclass
class _Job:
    bot: Any
    chat_id: Any
    kind: str                       # text | photo | document
    payload: Any                    # متن (text) یا فایل (photo / document: file_id یا bytes)
    caption: Optional[str]
    markup: Any
    html: bool
    future: "asyncio.Future[Optional[int]]"
    plain: bool = False             # بعد از خطای پارس HTML، نسخه‌ی ساده ارسال می‌شود
    net_tries: int = 0


_queue: Optional["asyncio.PriorityQueue"] = None
_worker: Optional["asyncio.Task"] = None
_tasks: Set["asyncio.Task"] = set()
_seq = itertools.count()
_pause_until = 0.0                  # تا این زمان (monotonic) ارسال متوقف است (Flood Wait)
_stats: Dict[str, int] = {"sent": 0, "dropped": 0, "retries": 0, "timeouts": 0}


def start_queue() -> None:
    """Worker صف را راه‌اندازی می‌کند (اگر قبلاً راه‌اندازی شده باشد کاری نمی‌کند)."""
    global _queue, _worker
    loop = asyncio.get_running_loop()
    if _queue is None:
        _queue = asyncio.PriorityQueue()
    if _worker is None or _worker.done():
        _worker = loop.create_task(_worker_loop(), name="msg-queue")


async def stop_queue() -> None:
    """توقف Worker و آزاد کردن همه‌ی درخواست‌های منتظر (خروجی None)."""
    global _worker
    w, _worker = _worker, None
    if w is not None:
        w.cancel()
        await asyncio.gather(w, return_exceptions=True)
    running = list(_tasks)
    for t in running:
        t.cancel()
    if running:
        await asyncio.gather(*running, return_exceptions=True)
    q = _queue
    while q is not None and not q.empty():
        try:
            _p, _s, job = q.get_nowait()
        except asyncio.QueueEmpty:
            break
        if not job.future.done():
            job.future.set_result(None)


def queue_size() -> int:
    return _queue.qsize() if _queue is not None else 0


def queue_stats() -> Dict[str, int]:
    return dict(_stats, queued=queue_size(), paused_for=int(max(0.0, _pause_until - time.monotonic())))


async def _deliver(job: _Job) -> Tuple[str, Any]:
    """
    یک بار ارسال. خروجی: (وضعیت، مقدار)
      ok -> شناسه‌ی پیام | retry -> ثانیه‌های انتظار | again -> فوراً دوباره | wait -> بعد از مقدار ثانیه دوباره | drop
    """
    mode = None if job.plain else (ParseMode.HTML if job.html else None)
    try:
        if job.kind == "photo":
            cap = templates.plain_fallback(job.caption) if (job.plain and job.caption) else job.caption
            m = await job.bot.send_photo(
                chat_id=job.chat_id, photo=job.payload, caption=cap, reply_markup=job.markup, parse_mode=mode,
            )
        elif job.kind == "document":
            cap = templates.plain_fallback(job.caption) if (job.plain and job.caption) else job.caption
            m = await job.bot.send_document(
                chat_id=job.chat_id, document=job.payload, caption=cap, reply_markup=job.markup, parse_mode=mode,
            )
        else:
            text = templates.plain_fallback(job.payload) if job.plain else job.payload
            m = await job.bot.send_message(
                chat_id=job.chat_id, text=text, reply_markup=job.markup, parse_mode=mode,
                link_preview_options=NO_PREVIEW,
            )
        return "ok", m.message_id
    except RetryAfter as e:
        return "retry", retry_seconds(e)
    except BadRequest as e:
        low = str(e).lower()
        if job.html and not job.plain and ("parse" in low or "entit" in low):
            job.plain = True
            return "again", None
        log.debug("send rejected for %s: %s", job.chat_id, e)
        return "drop", None
    except Forbidden:
        return "drop", None
    except NetworkError:
        job.net_tries += 1
        if job.net_tries >= 3:
            return "drop", None
        return "wait", 1.0
    except TelegramError as e:
        log.debug("send failed for %s: %s", job.chat_id, e)
        return "drop", None


async def _run(prio: int, seq: int, job: _Job, sem: "asyncio.Semaphore") -> None:
    """ارسال یک Job و ثبت نتیجه‌اش؛ در Flood Wait همان Job با همان جایگاه به صف برمی‌گردد."""
    global _pause_until
    try:
        if job.future.done():
            return
        result: Optional[int] = None
        requeue = False
        for _ in range(4):
            status, val = await _deliver(job)
            if status == "ok":
                result = int(val)
                _stats["sent"] += 1
                break
            if status == "retry":
                sec = float(val)
                _pause_until = max(_pause_until, time.monotonic() + sec + 0.5)
                if sec > 30:
                    set_flood(sec)          # بقیه‌ی بخش‌های ربات هم تا پایان محدودیت پیام تولید نمی‌کنند
                _stats["retries"] += 1
                requeue = True
                break
            if status == "again":
                continue
            if status == "wait":
                await asyncio.sleep(float(val))
                continue
            break                           # drop
        if requeue:
            if not job.future.done() and _queue is not None:
                _queue.put_nowait((prio, seq, job))     # همان seq: جلوتر از پیام‌های بعدیِ هم‌اولویت می‌ماند
            return
        if result is None:
            _stats["dropped"] += 1
        if not job.future.done():
            job.future.set_result(result)
    except asyncio.CancelledError:
        if not job.future.done():
            job.future.set_result(None)
        raise
    except Exception:
        log.exception("queue job failed for %s", job.chat_id)
        if not job.future.done():
            job.future.set_result(None)
    finally:
        sem.release()


async def _worker_loop() -> None:
    sem = asyncio.Semaphore(MAX_INFLIGHT)
    next_slot = 0.0
    while True:
        job: Optional[_Job] = None
        try:
            prio, seq, job = await _queue.get()
            if job.future.done():           # لغو شده (Timeout) یا صاحبش رفته است
                continue
            while True:                     # Flood Wait: کل صف منتظر می‌ماند
                wait = _pause_until - time.monotonic()
                if wait <= 0 or job.future.done():
                    break
                await asyncio.sleep(min(wait, 2.0))
            if job.future.done():
                continue
            per = max(1, config.BROADCAST_PER_SECOND)
            now = time.monotonic()
            slot = max(now, next_slot)
            next_slot = slot + 1.0 / per
            if slot > now:
                await asyncio.sleep(slot - now)
            await sem.acquire()
            t = asyncio.get_running_loop().create_task(_run(prio, seq, job, sem))
            _tasks.add(t)
            t.add_done_callback(_tasks.discard)
            job = None                      # از این لحظه _run مسئول نتیجه است
        except asyncio.CancelledError:
            if job is not None and not job.future.done():
                job.future.set_result(None)
            raise
        except Exception:
            log.exception("queue worker error")
            if job is not None and not job.future.done():
                job.future.set_result(None)
            await asyncio.sleep(0.5)


def split_text(text: str, limit: int = TEXT_LIMIT) -> List[str]:
    """تقسیم متن طولانی به تکه‌های قابل ارسال، با پرهیز از شکستن تگ/موجودیت HTML."""
    if not text or len(text) <= limit:
        return [text] if text else []
    parts: List[str] = []
    rest = text
    while len(rest) > limit:
        cut = rest.rfind("\n", limit // 2, limit)
        if cut < 0:
            cut = rest.rfind(" ", limit // 2, limit)
        if cut < 0:
            cut = limit
        lt, gt = rest.rfind("<", 0, cut), rest.rfind(">", 0, cut)
        if lt > gt:
            cut = lt
        amp, semi = rest.rfind("&", 0, cut), rest.rfind(";", 0, cut)
        if amp > semi and cut - amp < 12:
            cut = amp
        cut = max(1, cut)
        parts.append(rest[:cut].strip())
        rest = rest[cut:].lstrip()
    if rest:
        parts.append(rest)
    return [p for p in parts if p]


def _prio(chat_id: Any, priority: Optional[int]) -> int:
    if priority is not None:
        return priority
    return PRIO_HIGH if chat_id == config.ADMIN_UID else PRIO_NORMAL


async def _submit(
    bot, chat_id: Any, kind: str, payload: Any, caption: Optional[str], markup, html: bool,
    priority: Optional[int], wait: Optional[float],
) -> Optional[int]:
    """Job را در صف می‌گذارد و نتیجه (شناسه‌ی پیام یا None) را منتظر می‌ماند."""
    if kind == "text" and isinstance(payload, str) and len(payload) > TEXT_LIMIT:
        parts = split_text(payload)
        last: Optional[int] = None
        for idx, part in enumerate(parts):
            last = await _submit(
                bot, chat_id, "text", part, None,
                markup if idx == len(parts) - 1 else None, html, priority, wait,
            )
        return last
    prio = _prio(chat_id, priority)
    if prio >= PRIO_LOW and is_flooded():
        return None
    start_queue()
    if prio >= PRIO_LOW and _queue.qsize() >= config.QUEUE_MAX_SIZE:
        log.warning("message queue is full; low priority message dropped")
        return None
    fut = asyncio.get_running_loop().create_future()
    job = _Job(
        bot=bot, chat_id=chat_id, kind=kind, payload=payload, caption=caption, markup=markup, html=html, future=fut,
    )
    _queue.put_nowait((prio, next(_seq), job))
    try:
        return await asyncio.wait_for(fut, timeout=wait or config.QUEUE_MAX_WAIT)
    except asyncio.TimeoutError:
        _stats["timeouts"] += 1
        log.warning("queued message to %s expired after %ss", chat_id, wait or config.QUEUE_MAX_WAIT)
        return None


# ============================================================================
# توابع ارسال (همان امضای قبلی + پارامتر اختیاری priority / wait)
# ============================================================================
async def send_message(
    bot, uid: int, text: str, html: bool, markup=None, priority: Optional[int] = None, wait: Optional[float] = None,
) -> Optional[int]:
    """ارسال یک پیام؛ شناسه‌ی پیام را برمی‌گرداند (None یعنی نشد)."""
    return await _submit(bot, uid, "text", text, None, markup, html, priority, wait)


async def send_html(
    bot, uid: int, text: str, markup=None, priority: Optional[int] = None, wait: Optional[float] = None,
) -> Optional[int]:
    return await send_message(bot, uid, text, html=True, markup=markup, priority=priority, wait=wait)


async def send_plain(
    bot, uid: int, text: str, markup=None, priority: Optional[int] = None, wait: Optional[float] = None,
) -> Optional[int]:
    return await send_message(bot, uid, text, html=False, markup=markup, priority=priority, wait=wait)


async def _send_part(
    bot, uid: int, kind: str, payload: str, caption: Optional[str], markup, html: bool = False,
    priority: Optional[int] = None,
) -> Optional[int]:
    """kind: photo | document | text. html=True: متن/کپشن HTML تلگرام هست (اگر پذیرفته نشد، نسخه‌ی ساده ارسال می‌شود)."""
    if kind == "text":
        return await _submit(bot, uid, "text", payload, None, markup, html, priority, None)
    return await _submit(bot, uid, kind, payload, caption, markup, html, priority, None)


async def send_content(
    bot, uid: int, content: Dict[str, Optional[str]], markup=None, html: bool = False,
    priority: Optional[int] = None,
) -> List[int]:
    """
    ارسال محتوای ترکیبی: {"text":..., "photo": file_id, "document": file_id}
      فقط متن | فقط عکس | فقط فایل | عکس+متن | فایل+متن | ترکیب هر سه
    متن اگر تا ۱۰۲۴ کاراکتر باشد به‌عنوان کپشن (عکس، وگرنه فایل) می‌رود؛ وگرنه جدا ارسال می‌شود.
    دکمه (markup) فقط روی آخرین پیام قرار می‌گیرد. خروجی: شناسه‌ی پیام‌های ارسال‌شده (خالی یعنی نشد).
    html=True: content["text"] متنی با نشانه‌های فرمت (**، __، `، >، ...) هست و به HTML تلگرام تبدیل می‌شود.
    """
    text = (content.get("text") or "").strip()
    if html and text:
        text = fmt.to_html(text)
    photo, doc = content.get("photo"), content.get("document")
    cap_ok = bool(text) and len(text) <= CAPTION_LIMIT
    parts: List[Tuple[str, str, Optional[str]]] = []
    caption_used = False
    if photo:
        cap = text if cap_ok else None
        caption_used = cap is not None
        parts.append(("photo", photo, cap))
    if doc:
        cap = text if (cap_ok and not caption_used) else None
        caption_used = caption_used or cap is not None
        parts.append(("document", doc, cap))
    if text and not caption_used:
        parts.append(("text", text, None))
    ids: List[int] = []
    for i, (kind, payload, cap) in enumerate(parts):
        rm = markup if i == len(parts) - 1 else None
        mid = await _send_part(bot, uid, kind, payload, cap, rm, html=html, priority=priority)
        if mid:
            ids.append(mid)
    return ids


async def mass_send(
    bot,
    ids: List[int],
    content: Dict[str, Optional[str]],
    progress: Optional[Progress] = None,
    html: bool = False,
) -> Tuple[int, int]:
    """
    ارسال پیام (متن/عکس/فایل) به لیست کاربران با دکمه‌ی منو؛ همه با اولویت پایین در صف قرار می‌گیرند
    (سیگنال‌ها و اعلان‌های ادمین همیشه جلوتر می‌روند). خروجی: (موفق، ناموفق).
    """
    total = len(ids)
    window = max(50, config.BROADCAST_PER_SECOND * 4)
    sent = failed = 0
    last_report = 0.0
    for i in range(0, total, window):
        if is_flooded():
            failed += total - i
            break
        res = await asyncio.gather(
            *(
                send_content(bot, uid, content, menu_markup_for(uid), html=html, priority=PRIO_LOW)
                for uid in ids[i:i + window]
            ),
            return_exceptions=True,
        )
        for r in res:
            if isinstance(r, list) and r:
                sent += 1
            else:
                failed += 1
        done = min(i + window, total)
        now = time.monotonic()
        if progress is not None and done < total and now - last_report >= 2.0:
            last_report = now
            try:
                await progress(done, total, sent)
            except Exception:  # pragma: no cover
                log.debug("progress callback failed", exc_info=True)
    return sent, failed


async def send_photo_html(
    bot, uid: Any, photo: Any, caption: str, markup=None, priority: Optional[int] = PRIO_HIGH,
    wait: Optional[float] = None,
) -> Optional[int]:
    """ارسال عکس (BytesIO / bytes / file_id) با کپشن HTML؛ پیش‌فرض اولویت بالا (سیگنال)."""
    data = photo.getvalue() if isinstance(photo, io.BytesIO) else photo     # bytes برای ارسال مجدد بعد از Flood Wait امن است
    if caption and len(templates.plain_fallback(caption)) > CAPTION_LIMIT:
        # کپشن بیش از ۱۰۲۴ کاراکتر را تلگرام رد می‌کند: عکس بدون کپشن و متن جدا ارسال می‌شود
        if await _submit(bot, uid, "photo", data, None, None, True, priority, wait) is None:
            return None
        return await _submit(bot, uid, "text", caption, None, markup, True, priority, wait)
    return await _submit(bot, uid, "photo", data, caption, markup, True, priority, wait)
