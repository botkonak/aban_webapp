"""مرکز داده‌ی بازار وب‌اپ: چند صرافی، اولویت، کش کندل و آمار نمادها.

قواعد:
  - نمادهای هر صرافی جدا نگه داشته می‌شوند و هیچ داده‌ای بین صرافی‌ها ترکیب نمی‌شود.
  - برای هر نماد، صرافی با اولویت بالاتر (عدد کمتر) که آن نماد را دارد منبع تحلیل است.
  - صرافی غیرفعال در پردازش شرکت نمی‌کند؛ صرافی پنهان فقط در لیست کاربر دیده نمی‌شود.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import aiohttp
import numpy as np

import config
from structural_engine import MarketCandles
from webapp.repo import repo

log = logging.getLogger("webapp.market")

TF_MINUTES = {
    "5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240,
    "6h": 360, "12h": 720, "1d": 1440, "1w": 10080,
}
TF_SECONDS = {k: v * 60 for k, v in TF_MINUTES.items()}
TF_FA = {
    "5m": "۵ دقیقه", "15m": "۱۵ دقیقه", "30m": "۳۰ دقیقه", "1h": "۱ ساعت",
    "4h": "۴ ساعت", "6h": "۶ ساعت", "12h": "۱۲ ساعت", "1d": "۱ روز", "1w": "۱ هفته",
}
TF_LABEL = {
    "5m": "5 دقیقه", "15m": "15 دقیقه", "30m": "30 دقیقه", "1h": "1 ساعت",
    "4h": "4 ساعت", "6h": "6 ساعت", "12h": "12 ساعت", "1d": "1 روز", "1w": "1 هفته",
}
SCANNER_TFS = ("1d", "5m", "15m", "30m", "1h", "4h", "6h", "12h", "1w")
SYMBOL_TFS = ("1d", "5m", "15m", "30m", "1h", "4h", "6h", "12h", "1w")

# بازه‌های مستقیم هر صرافی (بقیه از بازه‌ی پایه ساخته می‌شوند)
DIRECT_TFS = {
    "binance": {"5m", "15m", "30m", "1h", "4h", "1d", "1w"},
    "toobit": {"5m", "15m", "30m", "1h", "4h", "1d", "1w"},
    "ourbit": {"5m", "15m", "30m", "1h", "4h", "1d"},
}
AGG_SOURCE = {
    "6h": ("1h", 6),
    "12h": ("4h", 3),
    "1w": ("1d", 7),
}
SYMBOL_QUOTE = "USDT"


def norm_symbol(value: str) -> str:
    return "".join(ch for ch in (value or "").upper() if ch.isalnum())


@dataclass(frozen=True)
class SymbolRef:
    display: str
    base: str
    quote: str
    exchanges: Dict[str, str] = field(default_factory=dict)   # slug -> pair id
    primary: str = ""
    primary_pair: str = ""

    def exchange_name(self) -> str:
        row = repo.exchange(self.primary)
        return (row or {}).get("name") or self.primary


class ExchangeClient:
    slug = ""
    name = ""

    async def close(self) -> None:
        return None

    async def symbols(self) -> List[Dict[str, Any]]:
        raise NotImplementedError

    async def ohlcv(self, pair_id: str, tf: str, limit: int) -> List[List[float]]:
        raise NotImplementedError

    async def tickers(self) -> Dict[str, Dict[str, float]]:
        return {}

    def supported(self, tf: str) -> bool:
        return tf in DIRECT_TFS.get(self.slug, set(TF_MINUTES))


class CcxtClient(ExchangeClient):
    def __init__(self, row: Dict[str, Any]):
        self.slug = str(row.get("slug") or "")
        self.name = str(row.get("name") or self.slug)
        self.ccxt_id = str(row.get("ccxt_id") or self.slug)
        self.api_url = str(row.get("api_url") or "")
        self.ex = None
        self._lock = asyncio.Lock()

    async def _ensure(self):
        if self.ex is not None:
            return self.ex
        async with self._lock:
            if self.ex is not None:
                return self.ex
            import ccxt.async_support as ccxt_async

            cls = getattr(ccxt_async, self.ccxt_id, None)
            if cls is None:
                raise RuntimeError(f"صرافی {self.ccxt_id} در ccxt موجود نیست")
            options: Dict[str, Any] = {"defaultType": "swap"}
            params: Dict[str, Any] = {
                "enableRateLimit": True,
                "timeout": 20000,
                "options": options,
            }
            if self.api_url and "api" in self.api_url:
                params["urls"] = {"api": {"public": self.api_url.rstrip("/")}}
            self.ex = cls(params)
            return self.ex

    async def close(self) -> None:
        ex = self.ex
        self.ex = None
        if ex is not None:
            try:
                await ex.close()
            except Exception:
                pass

    async def symbols(self) -> List[Dict[str, Any]]:
        ex = await self._ensure()
        markets = await ex.load_markets()
        out: List[Dict[str, Any]] = []
        for sym, m in (markets or {}).items():
            if not isinstance(m, dict):
                continue
            if not m.get("swap") or m.get("linear") is False:
                continue
            if (m.get("settle") or m.get("quote")) != SYMBOL_QUOTE:
                continue
            if m.get("active") is False:
                continue
            info = m.get("info") if isinstance(m.get("info"), dict) else {}
            status = info.get("status")
            if status and str(status).upper() not in ("TRADING", "OPEN", "ENABLED"):
                continue
            base = (m.get("base") or sym.split("/")[0]).upper()
            display = norm_symbol(base + SYMBOL_QUOTE)
            if not display:
                continue
            out.append({"display": display, "base": base, "quote": SYMBOL_QUOTE, "pair_id": sym})
        return out

    async def ohlcv(self, pair_id: str, tf: str, limit: int) -> List[List[float]]:
        ex = await self._ensure()
        rows = await ex.fetch_ohlcv(pair_id, tf, None, max(50, int(limit)))
        return [list(r) for r in (rows or []) if isinstance(r, (list, tuple)) and len(r) >= 6]

    async def tickers(self) -> Dict[str, Dict[str, float]]:
        ex = await self._ensure()
        try:
            data = await ex.fetch_tickers()
        except Exception as exc:
            log.debug("%s fetch_tickers failed: %s", self.slug, exc)
            return {}
        out: Dict[str, Dict[str, float]] = {}
        for pair, t in (data or {}).items():
            if not isinstance(t, dict):
                continue
            last = t.get("last") or t.get("close") or t.get("mark")
            if last is None:
                continue
            out[pair] = {
                "last": float(last or 0),
                "pct": float(t.get("percentage") or 0),
                "quote_volume": float(t.get("quoteVolume") or 0),
            }
        return out


class OurbitClient(ExchangeClient):
    """صرافی Ourbit با API سازگار با Binance (بدون ccxt)."""

    slug = "ourbit"

    def __init__(self, row: Dict[str, Any]):
        self.name = str(row.get("name") or "Ourbit")
        self.base_url = str(row.get("api_url") or "https://api.ourbit.com/api/v3").rstrip("/")
        self._session: Optional[aiohttp.ClientSession] = None
        self._lock = asyncio.Lock()
        self._map_lock = asyncio.Lock()
        self._id_map: Dict[str, str] = {}

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            async with self._lock:
                if self._session is None or self._session.closed:
                    timeout = aiohttp.ClientTimeout(total=20)
                    self._session = aiohttp.ClientSession(
                        timeout=timeout,
                        headers={"User-Agent": "AbanTrade/1.0", "Accept": "application/json"},
                    )
        return self._session

    async def close(self) -> None:
        session, self._session = self._session, None
        if session is not None and not session.closed:
            await session.close()

    async def _json(self, path: str, params: Optional[Dict[str, Any]] = None):
        session = await self._get_session()
        async with session.get(self.base_url + path, params=params) as resp:
            text = await resp.text()
            if resp.status >= 400:
                raise RuntimeError(f"Ourbit HTTP {resp.status}: {text[:160]}")
            try:
                return await resp.json(content_type=None)
            except Exception:
                import json
                return json.loads(text)

    async def symbols(self) -> List[Dict[str, Any]]:
        data = await self._json("/exchangeInfo")
        out: List[Dict[str, Any]] = []
        async with self._map_lock:
            self._id_map = {}
        for s in (data or {}).get("symbols") or []:
            if not isinstance(s, dict):
                continue
            if str(s.get("status") or "TRADING").upper() not in ("TRADING", "ENABLED", "ONLINE", "1"):
                continue
            if str(s.get("quoteAsset") or "").upper() != SYMBOL_QUOTE:
                continue
            symbol_id = str(s.get("symbol") or "").upper()
            base = str(s.get("baseAsset") or "").upper()
            display = norm_symbol(symbol_id)
            if not base or not display:
                continue
            out.append({"display": display, "base": base, "quote": SYMBOL_QUOTE, "pair_id": symbol_id})
            self._id_map[display] = symbol_id
        return out

    async def ohlcv(self, pair_id: str, tf: str, limit: int) -> List[List[float]]:
        interval = {"1h": "60m"}.get(tf, tf)
        if interval not in ("5m", "15m", "30m", "60m", "4h", "1d"):
            raise ValueError(f"Ourbit interval {tf} requires aggregation")
        data = await self._json(
            "/klines",
            {"symbol": pair_id, "interval": interval, "limit": max(50, min(int(limit), 1000))},
        )
        rows: List[List[float]] = []
        for r in data or []:
            if not isinstance(r, (list, tuple)) or len(r) < 6:
                continue
            try:
                rows.append([
                    float(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5]),
                ])
            except (TypeError, ValueError):
                continue
        return rows

    @staticmethod
    def _pct(value: Any) -> float:
        num = float(value or 0)
        return num * 100 if abs(num) <= 1 else num

    async def ticker(self, pair_id: str) -> Dict[str, float]:
        data = await self._json("/ticker/24hr", {"symbol": pair_id})
        if not isinstance(data, dict):
            return {}
        try:
            return {
                "last": float(data.get("lastPrice") or 0),
                "pct": self._pct(data.get("priceChangePercent")),
                "quote_volume": float(data.get("quoteVolume") or 0),
            }
        except (TypeError, ValueError):
            return {}

    async def tickers(self) -> Dict[str, Dict[str, float]]:
        data = await self._json("/ticker/24hr")
        items = data.values() if isinstance(data, dict) else (data or [])
        out: Dict[str, Dict[str, float]] = {}
        for t in items:
            if not isinstance(t, dict):
                continue
            symbol = str(t.get("symbol") or "").upper()
            if not symbol:
                continue
            try:
                out[symbol] = {
                    "last": float(t.get("lastPrice") or 0),
                    "pct": self._pct(t.get("priceChangePercent")),
                    "quote_volume": float(t.get("quoteVolume") or 0),
                }
            except (TypeError, ValueError):
                continue
        return out


class MarketHub:
    def __init__(self, data_repo=repo):
        self.repo = data_repo
        self._clients: Dict[str, Tuple[Tuple[str, str], ExchangeClient]] = {}
        self._symbols: Dict[str, Tuple[List[Dict[str, Any]], float]] = {}
        self._tickers: Dict[str, Tuple[Dict[str, Dict[str, float]], float]] = {}
        self._candles: Dict[Tuple[str, str, str], Tuple[MarketCandles, float]] = {}
        self._locks: Dict[Tuple[str, str, str], asyncio.Lock] = {}
        self._universe_cache: Dict[Tuple[str, ...], Tuple[Dict[str, SymbolRef], float]] = {}
        self._fng: Dict[str, Any] = {"value": 50, "label": "خنثی", "at": 0.0}
        self._fng_history: List[Dict[str, Any]] = []
        self._fng_history_at = 0.0
        self.last_error = ""

    # ------------------------------------------------------------------
    def clear_cache(self) -> None:
        self._symbols.clear()
        self._tickers.clear()
        self._candles.clear()
        self._universe_cache.clear()

    async def sync(self) -> Dict[str, Any]:
        """لیست نمادهای همه‌ی صرافی‌های فعال را از نو دریافت می‌کند."""
        self.clear_cache()
        result: Dict[str, Any] = {"counts": {}, "errors": {}}
        for row in self.repo.exchange_rows(active_only=True):
            slug = str(row.get("slug") or "")
            try:
                client = self._client(row)
                items = await client.symbols()
                self._symbols[slug] = (items, time.time())
                result["counts"][slug] = len(items)
            except Exception as exc:
                result["errors"][slug] = str(exc)
                log.warning("sync %s failed: %s", slug, exc)
        self._universe_cache.clear()
        return result

    async def close(self) -> None:
        for _, client in list(self._clients.values()):
            await client.close()
        self._clients.clear()

    def _client(self, row: Dict[str, Any]) -> ExchangeClient:
        slug = str(row.get("slug") or "")
        key = (str(row.get("api_url") or ""), str(row.get("ccxt_id") or ""))
        hit = self._clients.get(slug)
        if hit and hit[0] == key:
            return hit[1]
        if hit:
            asyncio.create_task(hit[1].close())
        client: ExchangeClient
        if slug == "ourbit" or not str(row.get("ccxt_id") or "").strip():
            client = OurbitClient(row)
        else:
            client = CcxtClient(row)
        self._clients[slug] = (key, client)
        return client

    def selected_exchanges(self, slugs: Optional[Sequence[str]] = None, all_flag: bool = True) -> List[Dict[str, Any]]:
        active = self.repo.exchange_rows(active_only=True)
        if all_flag or not slugs:
            return [r for r in active if int(r.get("is_visible") or 0) == 1]
        wanted = {str(s).lower() for s in slugs}
        return [r for r in active if str(r.get("slug") or "").lower() in wanted]

    async def _exchange_symbols(self, row: Dict[str, Any]) -> List[Dict[str, Any]]:
        slug = str(row.get("slug") or "")
        hit = self._symbols.get(slug)
        if hit and time.time() - hit[1] < 3600:
            return hit[0]
        try:
            client = self._client(row)
            items = await client.symbols()
            self._symbols[slug] = (items, time.time())
            return items
        except Exception as exc:
            self.last_error = f"{slug}: {exc}"
            log.warning("symbols %s failed: %s", slug, exc)
            return hit[0] if hit else []

    async def universe(
        self, slugs: Optional[Sequence[str]] = None, all_flag: bool = True,
    ) -> Dict[str, SymbolRef]:
        rows = self.selected_exchanges(slugs, all_flag)
        cache_key = tuple(str(r.get("slug")) for r in rows)
        hit = self._universe_cache.get(cache_key)
        if hit and time.time() - hit[1] < 300:
            return hit[0]
        result: Dict[str, SymbolRef] = {}
        per_exchange = await asyncio.gather(*(self._exchange_symbols(r) for r in rows), return_exceptions=True)
        for row, items in zip(rows, per_exchange):
            if isinstance(items, Exception) or not items:
                continue
            slug = str(row.get("slug"))
            for it in items:
                display = it["display"]
                if display in result:
                    result[display].exchanges[slug] = it["pair_id"]
                    continue
                result[display] = SymbolRef(
                    display=display, base=it["base"], quote=it["quote"],
                    exchanges={slug: it["pair_id"]}, primary=slug, primary_pair=it["pair_id"],
                )
        if result:
            self._universe_cache[cache_key] = (result, time.time())
        return result
    async def resolve(self, symbol: str, slugs: Optional[Sequence[str]] = None, all_flag: bool = True) -> Optional[SymbolRef]:
        display = norm_symbol(symbol)
        if not display:
            return None
        uni = await self.universe(slugs, all_flag)
        return uni.get(display)

    async def search(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        q = norm_symbol(query)
        uni = await self.universe(None, True)
        if not q:
            items = sorted(uni.values(), key=lambda r: r.display)
            return [{"symbol": r.display, "exchange": r.exchange_name()} for r in items[:limit]]
        exact = [r for r in uni.values() if r.display == q]
        starts = [r for r in uni.values() if r.display.startswith(q) and r.display != q]
        contains = [r for r in uni.values() if q in r.display and not r.display.startswith(q)]
        out = (exact + sorted(starts, key=lambda r: len(r.display)) + sorted(contains, key=lambda r: len(r.display)))[:limit]
        return [{"symbol": r.display, "exchange": r.exchange_name()} for r in out]

    async def symbols_list(self, slugs: Optional[Sequence[str]] = None, all_flag: bool = True) -> List[Dict[str, Any]]:
        uni = await self.universe(slugs, all_flag)
        return [{"symbol": r.display, "base": r.base, "exchange": r.exchange_name(), "exchanges": list(r.exchanges)} for r in sorted(uni.values(), key=lambda x: x.display)]

    # ------------------------------------------------------------------
    async def _ticker_map(self, row: Dict[str, Any]) -> Dict[str, Dict[str, float]]:
        slug = str(row.get("slug") or "")
        hit = self._tickers.get(slug)
        if hit and time.time() - hit[1] < 60:
            return hit[0]
        try:
            client = self._client(row)
            data = await client.tickers()
            self._tickers[slug] = (data, time.time())
            return data
        except Exception as exc:
            log.debug("tickers %s failed: %s", slug, exc)
            return hit[0] if hit else {}

    async def ticker(self, ref: SymbolRef) -> Dict[str, float]:
        row = self.repo.exchange(ref.primary)
        if not row:
            return {"last": 0, "pct": 0, "quote_volume": 0}
        client = self._client(row)
        single = getattr(client, "ticker", None)
        if single is not None:
            try:
                data = await asyncio.wait_for(single(ref.primary_pair), timeout=10)
                if data:
                    return data
            except Exception as exc:
                log.debug("ticker %s/%s failed: %s", ref.primary, ref.primary_pair, exc)
        data = await self._ticker_map(row)
        return data.get(ref.primary_pair) or {"last": 0, "pct": 0, "quote_volume": 0}

    # ------------------------------------------------------------------
    @staticmethod
    def _clean_rows(rows: Iterable[Iterable[float]], tf: str) -> MarketCandles:
        arr = np.asarray([list(r) for r in rows], dtype=float)
        if arr.ndim != 2 or arr.shape[0] < 2 or arr.shape[1] < 5:
            raise ValueError("invalid candles")
        arr = arr[np.argsort(arr[:, 0])]
        if arr.shape[1] < 6:
            arr = np.column_stack([arr, np.zeros(len(arr))])
        now_ms = time.time() * 1000
        tf_ms = TF_MINUTES[tf] * 60_000
        if arr[-1, 0] + tf_ms > now_ms:
            arr = arr[:-1]
        if len(arr) < 2:
            raise ValueError("no closed candles")
        return MarketCandles(
            t=arr[:, 0].copy(), o=arr[:, 1].copy(), h=arr[:, 2].copy(),
            l=arr[:, 3].copy(), c=arr[:, 4].copy(), v=arr[:, 5].copy(),
        )

    @staticmethod
    def _aggregate(rows: List[List[float]], target_tf: str) -> List[List[float]]:
        source_tf, factor = AGG_SOURCE[target_tf]
        tgt_ms = TF_MINUTES[target_tf] * 60_000
        if target_tf == "1w":
            monday_offset = 3 * 86400_000

            def bucket(ts: float) -> float:
                return ((int(ts) + monday_offset) // tgt_ms) * tgt_ms - monday_offset
        else:
            def bucket(ts: float) -> float:
                return (int(ts) // tgt_ms) * tgt_ms
        groups: Dict[int, List[List[float]]] = {}
        for r in rows:
            groups.setdefault(int(bucket(r[0])), []).append(r)
        out: List[List[float]] = []
        now_ms = time.time() * 1000
        for start in sorted(groups):
            chunk = groups[start]
            if len(chunk) < factor or start + tgt_ms > now_ms:
                continue
            chunk.sort(key=lambda r: r[0])
            out.append([
                float(start), float(chunk[0][1]), float(max(r[2] for r in chunk)),
                float(min(r[3] for r in chunk)), float(chunk[-1][4]), float(sum(r[5] for r in chunk)),
            ])
        return out

    async def candles(self, ref: SymbolRef, tf: str, limit: int = 400) -> Optional[MarketCandles]:
        tf = tf if tf in TF_MINUTES else "1h"
        limit = max(60, min(int(limit), 1000))
        row = self.repo.exchange(ref.primary)
        if not row:
            return None
        pair = ref.primary_pair
        key = (ref.primary, pair, tf)
        now = time.time()
        hit = self._candles.get(key)
        if hit and now < hit[1]:
            return hit[0]
        lock = self._locks.get(key)
        if lock is None:
            lock = self._locks[key] = asyncio.Lock()
        async with lock:
            hit = self._candles.get(key)
            if hit and time.time() < hit[1]:
                return hit[0]
            client = self._client(row)
            try:
                if client.supported(tf):
                    rows = await client.ohlcv(pair, tf, limit)
                    tf_used = tf
                else:
                    source_tf, factor = AGG_SOURCE.get(tf, ("1h", max(1, TF_MINUTES[tf] // 60)))
                    rows = await client.ohlcv(pair, source_tf, min(1000, int(limit) * factor + factor))
                    rows = self._aggregate(rows, tf)
                    tf_used = tf
                candles = self._clean_rows(rows, tf_used)
                if len(candles) > limit:
                    candles = MarketCandles(
                        t=candles.t[-limit:], o=candles.o[-limit:], h=candles.h[-limit:],
                        l=candles.l[-limit:], c=candles.c[-limit:], v=candles.v[-limit:],
                        symbol=ref.display, exchange=ref.primary, timeframe=tf,
                    )
                else:
                    candles = MarketCandles(
                        t=candles.t, o=candles.o, h=candles.h, l=candles.l, c=candles.c, v=candles.v,
                        symbol=ref.display, exchange=ref.primary, timeframe=tf,
                    )
            except Exception as exc:
                self.last_error = f"{ref.primary} {ref.display} {tf}: {exc}"
                log.warning("candles %s %s %s failed: %s", ref.primary, ref.display, tf, exc)
                if hit:
                    return hit[0]
                return None
            tf_ms = TF_MINUTES[tf] * 60_000
            last_open = int(candles.t[-1])
            expires = (last_open + 2 * tf_ms) / 1000.0 + 2.0
            if expires <= time.time():
                expires = time.time() + 30
            self._candles[key] = (candles, expires)
            if len(self._candles) > 4000:
                for k in list(self._candles)[:1000]:
                    self._candles.pop(k, None)
            return candles

    # ------------------------------------------------------------------
    async def header(self, ref: SymbolRef) -> Dict[str, Any]:
        tick = await self.ticker(ref)
        daily = await self.candles(ref, "1d", 110)
        price = float(tick.get("last") or 0)
        changes = {"d7": 0.0, "d30": 0.0, "d90": 0.0}
        low = high = price
        if daily is not None and len(daily):
            closes = daily.c
            price = price or float(closes[-1])
            if len(closes) > 7:
                changes["d7"] = (price / float(closes[-8]) - 1) * 100
            if len(closes) > 30:
                changes["d30"] = (price / float(closes[-31]) - 1) * 100
            if len(closes) > 90:
                changes["d90"] = (price / float(closes[-91]) - 1) * 100
            window = slice(max(0, len(daily) - 90), len(daily))
            low = float(np.min(daily.l[window]))
            high = float(np.max(daily.h[window]))
        return {
            "symbol": ref.display,
            "base": ref.base,
            "exchange": ref.exchange_name(),
            "exchange_slug": ref.primary,
            "price": price,
            "change_pct": float(tick.get("pct") or 0),
            "change_7d": changes["d7"],
            "change_30d": changes["d30"],
            "change_90d": changes["d90"],
            "low_90d": low,
            "high_90d": high,
            "timeframes": [{"key": k, "label": TF_LABEL[k]} for k in SYMBOL_TFS],
        }

    async def fear_greed(self) -> Dict[str, Any]:
        if time.time() - float(self._fng.get("at") or 0) < 3600:
            return {k: v for k, v in self._fng.items() if k != "at"}
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=12)) as session:
                async with session.get(config.WEBAPP_FEAR_GREED_URL) as resp:
                    if resp.status == 200:
                        data = await resp.json(content_type=None)
                        item = ((data or {}).get("data") or [{}])[0]
                        value = int(float(item.get("value") or 50))
                        label_en = str(item.get("value_classification") or "")
                        labels = {
                            "Extreme Fear": "ترس شدید", "Fear": "ترس", "Neutral": "خنثی",
                            "Greed": "طمع", "Extreme Greed": "طمع شدید",
                        }
                        self._fng = {"value": max(0, min(100, value)), "label": labels.get(label_en, "خنثی"), "at": time.time()}
        except Exception as exc:
            log.debug("fear&greed failed: %s", exc)
        return {k: v for k, v in self._fng.items() if k != "at"}

    async def fear_greed_history(self, limit: int = 30) -> List[Dict[str, Any]]:
        """تاریخچه‌ی شاخص ترس و طمع برای نمودار داشبورد (صعودی بر اساس زمان)."""
        import re

        limit = max(2, min(int(limit or 30), 90))
        now = time.time()
        if self._fng_history and now - self._fng_history_at < 3600:
            return list(self._fng_history)
        url = re.sub(r"limit=\d+", f"limit={limit}", config.WEBAPP_FEAR_GREED_URL)
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=12)) as session:
                async with session.get(url) as resp:
                    if resp.status == 200:
                        data = await resp.json(content_type=None)
                        items = (data or {}).get("data") or []
                        out: List[Dict[str, Any]] = []
                        for item in reversed(items):
                            try:
                                out.append({
                                    "time": int(float(item.get("timestamp") or 0)),
                                    "value": max(0, min(100, int(float(item.get("value") or 50)))),
                                })
                            except (TypeError, ValueError):
                                continue
                        if out:
                            self._fng_history = out
                            self._fng_history_at = now
        except Exception as exc:
            log.debug("fear&greed history failed: %s", exc)
        return list(self._fng_history)


market = MarketHub()
