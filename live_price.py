"""
قیمت زنده‌ی سیگنال (آبان ترید): جایگزین {lastprice} در پیام‌های ارسال‌شده، هر چند ثانیه با آخرین last price همان نماد
ویرایش می‌شود. فاصله‌ی به‌روزرسانی، مدت و روشن/خاموش بودن از تنظیمات ربات خوانده می‌شود.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from telegram.constants import ParseMode
from telegram.error import BadRequest, Forbidden, NetworkError, RetryAfter, TelegramError

import config
import templates
from database import db
from signal_engine import engine
from ui import NO_PREVIEW, is_flooded, retry_seconds, set_flood, signal_markup_for

log = logging.getLogger("live")

MAX_ACTIVE_SIGNALS = 5000


@dataclass
class LiveSignal:
    pair: str
    decimals: int
    base_text: str                       # متن سیگنال با LIVE_TOKEN به‌جای {lastprice}
    expires: float
    last_str: str                        # آخرین قیمتی که روی پیام‌ها نشان داده شده است
    recipients: List[Tuple[int, int]] = field(default_factory=list)   # (chat_id, message_id)


class LiveManager:
    def __init__(self) -> None:
        self.items: Dict[int, LiveSignal] = {}
        self._seq = 0
        self._flood_until = 0.0
        self._retry_after = 0.0

    # ------------------------------------------------------------------
    def register(
        self,
        pair: str,
        decimals: int,
        base_text: str,
        first_price: float,
        recipients: List[Tuple[int, int]],
    ) -> bool:
        """سیگنال ارسال‌شده را برای به‌روزرسانی قیمت زنده ثبت می‌کند."""
        if not recipients or templates.LIVE_TOKEN not in base_text or not db.get_bool("live_on"):
            return False
        now = time.time()
        for k in [k for k, v in self.items.items() if v.expires <= now]:
            self.items.pop(k, None)
        while len(self.items) >= MAX_ACTIVE_SIGNALS:
            self.items.pop(min(self.items), None)
        self._seq += 1
        self.items[self._seq] = LiveSignal(
            pair=pair,
            decimals=decimals,
            base_text=base_text,
            expires=now + max(1, db.get_int("live_duration")) * 60,
            last_str=templates.fmt_price(first_price, decimals),
            recipients=list(recipients),
        )
        return True

    # ------------------------------------------------------------------
    async def _edit(self, bot, chat_id: int, message_id: int, text: str) -> str:
        """ok | gone (دیگر نباید ویرایش شود) | retry (محدودیت تلگرام) | skip"""
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=text,
                parse_mode=ParseMode.HTML,
                # بدون reply_markup دکمه‌های پیام با ویرایش پاک می‌شوند؛ پس هر بار دوباره گذاشته می‌شوند
                reply_markup=signal_markup_for(chat_id),
                link_preview_options=NO_PREVIEW,
            )
            return "ok"
        except RetryAfter as e:
            self._retry_after = retry_seconds(e)
            if self._retry_after > 30:
                set_flood(self._retry_after)
            return "retry"
        except BadRequest as e:
            low = str(e).lower()
            if "not modified" in low:
                return "ok"
            return "gone"  # پیام پاک شده است، قابل ویرایش نیست یا متن نامعتبر است
        except Forbidden:
            return "gone"
        except NetworkError:
            return "skip"
        except TelegramError:
            return "skip"

    async def _run_jobs(self, bot, jobs: List[Tuple[LiveSignal, Tuple[int, int], str]]) -> None:
        """ویرایش پیام‌ها با سقف سرعت (در هر ثانیه حداکثر LIVE_EDITS_PER_SECOND ویرایش برای همه‌ی سیگنال‌ها)."""
        per = config.LIVE_EDITS_PER_SECOND
        gone: Dict[int, Tuple[LiveSignal, List[Tuple[int, int]]]] = {}
        for i in range(0, len(jobs), per):
            t0 = time.monotonic()
            chunk = jobs[i:i + per]
            res = await asyncio.gather(*(self._edit(bot, rcp[0], rcp[1], text) for _sig, rcp, text in chunk))
            for (sig, rcp, _t), r in zip(chunk, res):
                if r == "gone":
                    gone.setdefault(id(sig), (sig, []))[1].append(rcp)
            if "retry" in res:
                self._flood_until = time.monotonic() + max(1.0, self._retry_after) + 0.5
                break
            if i + per < len(jobs):
                await asyncio.sleep(max(0.0, 1.0 - (time.monotonic() - t0)))
        for sig, lst in gone.values():
            sig.recipients = [r for r in sig.recipients if r not in lst]

    async def tick(self, bot) -> None:
        if not self.items or not db.get_bool("live_on"):
            return
        if time.monotonic() < self._flood_until or is_flooded():
            return
        now = time.time()
        for k in [k for k, v in self.items.items() if v.expires <= now or not v.recipients]:
            self.items.pop(k, None)
        live = list(self.items.values())
        if not live:
            return
        # قیمت هر نماد فقط یک بار گرفته می‌شود (چند کاربر می‌توانند سیگنال یک نماد را داشته باشند)
        pairs = sorted({s.pair for s in live})
        prices: Dict[str, float] = {}
        try:
            ticks = await get_tickers(pairs)
            prices = {p: v[0] for p, v in ticks.items()}
        except Exception:
            log.debug("live tickers failed", exc_info=True)
        for p in pairs:
            if p not in prices:
                try:
                    px = await engine.last_price(p)
                except Exception:
                    px = None
                if isinstance(px, float):
                    prices[p] = px
        jobs: List[Tuple[LiveSignal, Tuple[int, int], str]] = []
        for sig in live:
            px = prices.get(sig.pair)
            if px is None:
                continue
            new = templates.fmt_price(px, sig.decimals)
            if new == sig.last_str:
                continue  # قیمت عوض نشده است؛ ویرایش لازم نیست
            sig.last_str = new
            text = sig.base_text.replace(templates.LIVE_TOKEN, new)
            jobs.extend((sig, rcp, text) for rcp in list(sig.recipients))
        if jobs:
            await self._run_jobs(bot, jobs)

    async def run(self, bot) -> None:
        await asyncio.sleep(5)
        while True:
            t0 = time.monotonic()
            try:
                await self.tick(bot)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("live tick failed")
            interval = max(1, db.get_int("live_interval"))
            await asyncio.sleep(max(0.25, interval - (time.monotonic() - t0)))


live = LiveManager()


# ============================================================================
# توابع قیمت برای ماژول‌های دیگر (رصد عملکرد، ارسال قیمت به کانال، آنالیز)
# ============================================================================
async def get_last_price(pair: str) -> Optional[float]:
    """آخرین قیمت معامله‌ی یک نماد (همان منبع قیمت سیگنال‌ها)."""
    return await engine.last_price(pair)


def _pct24(t: dict) -> Optional[float]:
    pct = t.get("percentage")
    try:
        if pct is not None:
            return float(pct)
        last, op = t.get("last"), t.get("open")
        if last is not None and op:
            return (float(last) - float(op)) / float(op) * 100.0
    except (TypeError, ValueError, ZeroDivisionError):
        pass
    return None


async def get_tickers(pairs: List[str]) -> Dict[str, Tuple[float, Optional[float]]]:
    """نماد -> (آخرین قیمت، درصد تغییر ۲۴ ساعته یا None). نمادی که قیمتش گرفته نشود در خروجی نیست."""
    out: Dict[str, Tuple[float, Optional[float]]] = {}
    pairs = list(dict.fromkeys(pairs))
    if not pairs or engine.ex is None:
        return out
    tickers: dict = {}
    try:
        tickers = await engine.ex.fetch_tickers(pairs) or {}
    except Exception as e:
        log.debug("fetch_tickers failed, falling back to single requests: %s", e)
    for p in pairs:
        t = tickers.get(p) or {}
        try:
            if t.get("last") is None:
                t = await engine.ex.fetch_ticker(p) or {}
            last = t.get("last")
            if last is not None and float(last) > 0:
                out[p] = (float(last), _pct24(t))
        except Exception as e:
            log.debug("ticker %s failed: %s", p, e)
    return out
