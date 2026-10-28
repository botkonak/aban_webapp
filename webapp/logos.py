"""مدیریت لوگوی دارایی‌های پایه؛ منبع خارجی یک‌بار بررسی و در دیتابیس کش می‌شود."""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Iterable, List

import aiohttp

from database import db, utc_cutoff

log = logging.getLogger("webapp.logos")

SOURCE_URL = "https://essamamdani.github.io/open-crypto-icons/icons/colored/{base}.svg"
ALIASES = {
    "XBT": "BTC",
    "BCC": "BCH",
    "BCHABC": "BCH",
    "BCHSV": "BSV",
    "MIOTA": "IOTA",
    "GEM": "GEM",
}
BASE_RE = re.compile(r"^[A-Z0-9]{1,20}$")


def normalize_base(value: str) -> str:
    base = re.sub(r"[^A-Za-z0-9]", "", str(value or "")).upper()
    return ALIASES.get(base, base)


def remote_url(base: str) -> str:
    base = normalize_base(base)
    if not BASE_RE.match(base):
        return ""
    return SOURCE_URL.format(base=base.lower())


def endpoint_url(base: str) -> str:
    base = normalize_base(base)
    return "/api/assets/" + base + "/logo" if base else ""


def remember_symbol(symbol: str, base: str, quote: str = "USDT", exchange: str = "") -> None:
    """نگاشت نماد به دارایی پایه را برای اجرای دوره‌ای ذخیره می‌کند."""
    base = normalize_base(base or symbol)
    if not base:
        return
    try:
        db.x(
            "INSERT INTO symbol_assets(symbol, base_asset, quote_asset, exchange) VALUES(?, ?, ?, ?) "
            "ON CONFLICT(symbol, exchange) DO UPDATE SET base_asset=excluded.base_asset, quote_asset=excluded.quote_asset",
            (str(symbol or "").upper(), base, str(quote or "USDT").upper(), str(exchange or "")),
        )
    except Exception:
        log.debug("remember symbol failed", exc_info=True)


def cached_base(base: str) -> str:
    """آدرس منبع کش‌شده برای دارایی پایه؛ این تابع هیچ رکوردی نمی‌سازد.

    endpoint عمومی نباید با ورودی دلخواه ردیف جدید در دیتابیس بسازد؛ پرکردن کش
    فقط از مسیرهای شناخته‌شده (هدر نماد و اجرای دوره‌ای) انجام می‌شود.
    """
    base = normalize_base(base)
    if not BASE_RE.match(base):
        return ""
    row = db.q1("SELECT logo_url FROM asset_logos WHERE base_asset = ?", (base,))
    return str(row.get("logo_url") or "") if row else ""


def _bases() -> List[str]:
    bases = set()
    for row in db.q("SELECT DISTINCT base_asset FROM symbol_assets"):
        base = normalize_base(row.get("base_asset") or "")
        if BASE_RE.match(base):
            bases.add(base)
    for row in db.q("SELECT DISTINCT symbol FROM structural_signals"):
        base = normalize_base(re.sub(r"(USDT|USDC|BUSD)$", "", str(row.get("symbol") or "")))
        if BASE_RE.match(base):
            bases.add(base)
    return sorted(bases)


async def _probe(session: aiohttp.ClientSession, base: str) -> str:
    url = remote_url(base)
    if not url:
        return ""
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status != 200:
                return ""
            body = await resp.content.read(2048)
            head = body.lstrip()[:64].lower()
            if head.startswith(b"<svg") or head.startswith(b"<?xml"):
                return url
    except Exception:
        log.debug("logo probe failed for %s", base)
    return ""


async def refresh(force: bool = False) -> int:
    """آدرس‌های بررسی‌نشده یا کهنه (بیش از ۷ روز) را دوباره اعتبارسنجی می‌کند."""
    bases = await asyncio.to_thread(_bases)
    if not bases:
        return 0
    rows = await asyncio.to_thread(db.q, "SELECT base_asset, logo_url, updated_at FROM asset_logos")
    known = {normalize_base(r.get("base_asset") or ""): r for r in rows}
    cutoff = utc_cutoff(days=7)
    pending = []
    for base in bases:
        row = known.get(base)
        if force or row is None or not row.get("logo_url") or str(row.get("updated_at") or "") < cutoff:
            pending.append(base)
    if not pending:
        return 0
    updated = 0
    connector = aiohttp.TCPConnector(limit=8, ttl_dns_cache=300)
    async with aiohttp.ClientSession(connector=connector) as session:
        for start in range(0, len(pending), 8):
            chunk = pending[start:start + 8]
            urls = await asyncio.gather(*(_probe(session, base) for base in chunk))
            for base, url in zip(chunk, urls):
                await asyncio.to_thread(
                    db.x,
                    "INSERT INTO asset_logos(base_asset, logo_url) VALUES(?, ?) "
                    "ON CONFLICT(base_asset) DO UPDATE SET logo_url=excluded.logo_url, updated_at=CURRENT_TIMESTAMP",
                    (base, url or ""),
                )
                updated += 1
    return updated


async def refresh_loop(interval: float = 6 * 3600) -> None:
    while True:
        try:
            count = await refresh()
            if count:
                log.info("asset logos refreshed: %s", count)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("asset logo refresh failed")
        await asyncio.sleep(interval)
