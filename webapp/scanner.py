"""اجرای اسکن ساختاری روی چند صرافی/نماد و ساخت خروجی یکنواخت برای وب‌اپ."""
from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import config
from structural_engine import Detection, Strategy, scan
from webapp.jalali import jalali_dt
from webapp.market import MarketHub, SymbolRef, market, norm_symbol
from webapp.repo import repo

log = logging.getLogger("webapp.scanner")


@dataclass
class ScanRequest:
    timeframe: str = "1h"
    depth: int = 10
    mode: str = "any"                    # any | all
    symbols_mode: str = "all"            # all | favorites | single
    symbol: str = ""
    strategy_keys: List[str] = field(default_factory=list)
    exchanges: List[str] = field(default_factory=list)
    exchanges_all: bool = True
    user_id: int = 0


def _strategy(row: Dict[str, Any]) -> Strategy:
    return Strategy(
        key=row["strategy_key"],
        title=row["title"],
        category=row["category"],
        strategy_type=row["strategy_type"],
        signal_type=row["signal_type"],
        logic_key=row["logic_key"],
        logic_params=row.get("logic_params") or "",
        description=row.get("description") or "",
    )


OVERLAY_LIST_KEYS = ("lines", "rays", "levels", "zones", "points", "markers", "labels", "fib", "panels")


def _merge_overlays(overlays: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    out: Dict[str, Any] = {key: [] for key in OVERLAY_LIST_KEYS}
    indicators: List[str] = []
    for ov in overlays:
        if not isinstance(ov, dict):
            continue
        for key in OVERLAY_LIST_KEYS:
            values = ov.get(key) or []
            if isinstance(values, list):
                out[key].extend(values)
        for token in ov.get("indicators") or []:
            token = str(token)
            if token and token not in indicators:
                indicators.append(token)
    for key in OVERLAY_LIST_KEYS:
        seen, unique = set(), []
        for item in out[key]:
            marker = repr(item)
            if marker in seen:
                continue
            seen.add(marker)
            unique.append(item)
        out[key] = unique[:40]
    if indicators:
        out["indicators"] = indicators[:24]
    return {k: v for k, v in out.items() if v}


class Scanner:
    def __init__(self, hub: Optional[MarketHub] = None, data_repo=repo):
        self.hub = hub or market
        self.repo = data_repo
        self._cache: Dict[str, Tuple[float, List[Dict[str, Any]], List[Dict[str, Any]]]] = {}
        self._sem: Optional[asyncio.Semaphore] = None
        self._cache_lock: Optional[asyncio.Lock] = None

    def clear_cache(self) -> None:
        self._cache.clear()

    def _semaphore(self) -> asyncio.Semaphore:
        if self._sem is None:
            self._sem = asyncio.Semaphore(config.WEBAPP_SCAN_CONCURRENCY)
        return self._sem

    def _cache_key(self, req: ScanRequest, rows: Sequence[Dict[str, Any]]) -> str:
        payload = "|".join([
            req.timeframe, str(req.depth), req.mode, req.symbols_mode, norm_symbol(req.symbol),
            ",".join(sorted(req.strategy_keys)), ",".join(sorted(req.exchanges)), "1" if req.exchanges_all else "0",
            str(req.user_id),
        ])
        return hashlib.md5(payload.encode("utf-8")).hexdigest()

    def _strategies(self, keys: Sequence[str]) -> Tuple[List[Strategy], List[Dict[str, Any]]]:
        rows = self.repo.strategy_rows(active_only=True)
        wanted = {str(k) for k in keys or []}
        if wanted:
            rows = [r for r in rows if r["strategy_key"] in wanted]
        return [_strategy(r) for r in rows], rows

    async def _symbols(self, req: ScanRequest, universe: Dict[str, SymbolRef]) -> List[SymbolRef]:
        if req.symbols_mode == "single":
            ref = universe.get(norm_symbol(req.symbol))
            return [ref] if ref else []
        if req.symbols_mode == "favorites":
            favs = set(self.repo.favorites(req.user_id).get("symbol") or [])
            return [universe[s] for s in sorted(favs) if s in universe]
        return list(universe.values())

    @staticmethod
    def _result(
        ref: SymbolRef, candles, det: Detection, timeframe: str, row: Dict[str, Any],
        matches: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        idx = max(0, min(int(det.index), len(candles) - 1))
        candle_ts = float(candles.t[idx]) / 1000.0
        price = float(det.price or candles.c[idx])
        signal_type = det.strategy.signal_type
        direction = det.strategy.direction
        return {
            "symbol": ref.display,
            "exchange": ref.exchange_name(),
            "exchange_slug": ref.primary,
            "timeframe": timeframe,
            "strategy_key": det.strategy.key,
            "strategy": det.strategy.title,
            "strategy_type": det.strategy.strategy_type,
            "signal_type": signal_type,
            "direction": direction,
            "price": price,
            "candle_ts": candle_ts,
            "candle_ts_ms": int(candle_ts * 1000),
            "date": jalali_dt(candle_ts),
            "candle_index": idx,
            "overlay": det.overlay or {},
            "matches": matches or [],
            "score": round(max(0.0, 100.0 - len(candles) + idx + 1), 3),
            "details": (row or {}).get("description") or "",
        }

    async def scan(self, req: ScanRequest, persist: bool = True, use_cache: bool = True) -> List[Dict[str, Any]]:
        if req.timeframe not in ("5m", "15m", "30m", "1h", "4h", "6h", "12h", "1d", "1w"):
            req.timeframe = "1h"
        req.depth = max(0, min(int(req.depth or 0), 120))
        req.mode = "all" if req.mode == "all" else "any"
        strategies, rows = self._strategies(req.strategy_keys)
        if not strategies:
            return []
        by_key = {r["strategy_key"]: r for r in rows}
        ck = self._cache_key(req, rows)
        now = time.time()
        if use_cache:
            hit = self._cache.get(ck)
            if hit and now - hit[0] < 30:
                return [dict(x) for x in hit[1]]

        universe = await self.hub.universe(req.exchanges, req.exchanges_all)
        refs = await self._symbols(req, universe)
        if not refs:
            return []
        sem = self._semaphore()

        async def one(ref: SymbolRef) -> List[Dict[str, Any]]:
            async with sem:
                candles = await self.hub.candles(ref, req.timeframe, config.WEBAPP_CANDLE_LIMIT)
                if candles is None or len(candles) < 40:
                    return []
                try:
                    dets = scan(candles, strategies, req.depth)
                except Exception:
                    log.exception("scan %s failed", ref.display)
                    return []
                if not dets:
                    return []
                out: List[Dict[str, Any]] = []
                if req.mode == "all":
                    grouped: Dict[str, Dict[str, List[Detection]]] = {"LONG": {}, "SHORT": {}}
                    for d in dets:
                        grouped[d.strategy.direction].setdefault(d.strategy.key, []).append(d)
                    for direction, found in grouped.items():
                        if not found or any(k not in found for k in by_key):
                            continue
                        picks = [max(found[k], key=lambda d: d.index) for k in by_key]
                        latest = max(picks, key=lambda d: d.index)
                        combined_key = "custom_" + hashlib.md5(
                            ("|".join(sorted(by_key)) + "|" + req.timeframe).encode("utf-8")
                        ).hexdigest()[:12]
                        combined = Strategy(
                            key=combined_key,
                            title=f"ترکیب {len(by_key)} استراتژی",
                            category="استراتژی سفارشی",
                            strategy_type="همه با هم",
                            signal_type="حمایت" if direction == "LONG" else "مقاومت",
                            logic_key="custom",
                        )
                        det = Detection(strategy=combined, index=latest.index, price=latest.price,
                                        overlay=_merge_overlays(d.overlay for d in picks))
                        matches = [{
                            "key": d.strategy.key, "title": d.strategy.title,
                            "signal_type": d.strategy.signal_type, "direction": d.strategy.direction,
                            "candle_ts": float(candles.t[d.index]) / 1000.0,
                            "price": float(d.price),
                        } for d in picks]
                        out.append(self._result(ref, candles, det, req.timeframe, {}, matches))
                else:
                    seen = set()
                    for d in dets:
                        sig = (d.strategy.key, int(d.index))
                        if sig in seen:
                            continue
                        seen.add(sig)
                        out.append(self._result(ref, candles, d, req.timeframe, by_key.get(d.strategy.key, {})))
                return out

        chunks = await asyncio.gather(*(one(r) for r in refs), return_exceptions=True)
        results: List[Dict[str, Any]] = []
        for chunk in chunks:
            if isinstance(chunk, Exception) or not chunk:
                continue
            results.extend(chunk)
        results.sort(key=lambda x: (-float(x["candle_ts"]), x["symbol"], x["strategy"]))
        if persist and results:
            ids = self.repo.save_signals(results)
            for r, sid in zip(results, ids):
                r["id"] = sid
        for r in results:
            r.setdefault("id", 0)
        if use_cache and len(self._cache) < 500:
            self._cache[ck] = (now, [dict(r) for r in results], [])
        return results

    async def scan_symbol(
        self, symbol: str, timeframe: str = "1h", depth: int = 40,
        strategy_keys: Optional[Sequence[str]] = None, user_id: int = 0,
        exchanges: Optional[Sequence[str]] = None, exchanges_all: bool = True,
    ) -> List[Dict[str, Any]]:
        req = ScanRequest(
            timeframe=timeframe, depth=depth, mode="any", symbols_mode="single", symbol=symbol,
            strategy_keys=list(strategy_keys or []), exchanges=list(exchanges or []),
            exchanges_all=exchanges_all, user_id=user_id,
        )
        return await self.scan(req, persist=True, use_cache=True)

    async def overview(self, timeframe: str = "1h", limit: int = 12) -> List[Dict[str, Any]]:
        """آخرین سیگنال‌های ثبت‌شده برای داشبورد (بدون اسکن سنگین)."""
        rows = self.repo.latest_signals(limit)
        out = []
        for r in rows:
            if timeframe and r["timeframe"] != timeframe:
                continue
            out.append(r)
        return out[:limit]


scanner = Scanner()
