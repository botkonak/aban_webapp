"""Asset logo manager and live-price routes against a real temporary database."""
import asyncio
import importlib
import os
import unittest
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

with patch.dict(os.environ, {
    "WEBAPP_SECRET": "symbol-assets-test-secret",
    "WEBHOOK_SECRET": "symbol-assets-test-webhook-secret",
}):
    import config
    import database
    from webapp import logos
    from webapp.auth import hash_password, session_token
    from webapp.market import SymbolRef

USER_ID = 1201
ADMIN_ID = 9101


class AssetTestBase(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        root = Path(self.stack.enter_context(TemporaryDirectory(prefix="aban-logo-tests-")))
        self.root = root
        self.stack.enter_context(patch.object(config, "WEBAPP_SECRET", "symbol-assets-test-secret"))
        self.stack.enter_context(patch.object(config, "ADMIN_UID", ADMIN_ID))
        self.db = database.Database("sqlite:///" + (root / "logos.db").as_posix())
        self.db.init()
        self.stack.callback(self.db.close)
        self.db.x(
            "INSERT INTO users(user_id, sub_until, webapp_sub_until) VALUES(?, ?, ?)",
            (USER_ID, 123, 0),
        )
        self.db.x(
            "INSERT INTO webapp_users(user_id, access_enabled, password_hash) VALUES(?, 1, ?)",
            (USER_ID, hash_password("logo-test-password")),
        )
        for module in (logos,):
            self.stack.enter_context(patch.object(module, "db", self.db))
        self.user = {"Authorization": "Bearer " + session_token(USER_ID, "user")}

    def add_signal(self, symbol):
        self.db.x(
            "INSERT INTO structural_signals(symbol, exchange, timeframe, strategy_key, signal_type, candle_ts, details, detected_at) "
            "VALUES(?, 'Binance', '1h', 'test-key', 'حمایت', 1, '', 1)",
            (symbol,),
        )


class LogoManagerTests(AssetTestBase):
    def test_base_normalization_aliases_and_urls(self):
        self.assertEqual(logos.normalize_base("xbt"), "BTC")
        self.assertEqual(logos.normalize_base(" bcc "), "BCH")
        self.assertEqual(logos.normalize_base("bchabc"), "BCH")
        self.assertEqual(logos.normalize_base("eth-usdt"), "ETHUSDT")
        self.assertEqual(logos.remote_url("xbt"), "https://essamamdani.github.io/open-crypto-icons/icons/colored/btc.svg")
        self.assertEqual(logos.endpoint_url("eth"), "/api/assets/ETH/logo")
        self.assertEqual(logos.endpoint_url("---"), "")
        self.assertEqual(logos.remote_url(""), "")

    def test_cached_base_inserts_once_and_honours_stored_value(self):
        self.assertEqual(
            logos.cached_base("btc"),
            "https://essamamdani.github.io/open-crypto-icons/icons/colored/btc.svg",
        )
        self.assertEqual(logos.cached_base("BTC"), logos.remote_url("BTC"))
        rows = self.db.q("SELECT base_asset, logo_url FROM asset_logos")
        self.assertEqual(len(rows), 1)
        self.db.x("UPDATE asset_logos SET logo_url = '' WHERE base_asset = 'BTC'")
        self.assertEqual(logos.cached_base("xbt"), "")
        self.assertEqual(logos.cached_base("---"), "")

    def test_remember_symbol_upserts_without_duplicates(self):
        logos.remember_symbol("BTCUSDT", "xbt", "usdt", "Toobit")
        logos.remember_symbol("BTCUSDT", "BTC", "USDT", "Toobit")
        rows = self.db.q("SELECT symbol, base_asset, quote_asset, exchange FROM symbol_assets")
        self.assertEqual(rows, [{"symbol": "BTCUSDT", "base_asset": "BTC", "quote_asset": "USDT", "exchange": "Toobit"}])
        logos.remember_symbol("ETHUSDT", "", "", "")
        row = self.db.q1("SELECT base_asset FROM symbol_assets WHERE symbol = 'ETHUSDT'")
        self.assertEqual(row["base_asset"], "ETHUSDT")

    def test_bases_combines_registry_and_signals_without_aliases(self):
        logos.remember_symbol("BTCUSDT", "BTC", "USDT", "Toobit")
        self.add_signal("SOLUSDT")
        self.add_signal("XBTUSDT")
        self.assertEqual(logos._bases(), ["BTC", "SOL"])

    def test_refresh_probes_missing_and_stale_rows(self):
        logos.remember_symbol("BTCUSDT", "BTC", "USDT", "Toobit")
        self.add_signal("ETHUSDT")

        class FakeSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

        probe = AsyncMock(side_effect=lambda session, base: logos.remote_url(base))
        self.stack.enter_context(patch.object(logos, "_probe", probe))
        self.stack.enter_context(patch.object(logos.aiohttp, "TCPConnector", lambda **kw: object()))
        self.stack.enter_context(patch.object(logos.aiohttp, "ClientSession", lambda **kw: FakeSession()))
        self.assertEqual(asyncio.run(logos.refresh()), 2)
        self.assertEqual(asyncio.run(logos.refresh()), 0)
        self.assertEqual(probe.await_count, 2)
        self.assertEqual(asyncio.run(logos.refresh(force=True)), 2)
        self.assertEqual(probe.await_count, 4)
        rows = {r["base_asset"]: r["logo_url"] for r in self.db.q("SELECT base_asset, logo_url FROM asset_logos")}
        self.assertEqual(rows["BTC"], logos.remote_url("BTC"))
        self.assertEqual(rows["ETH"], logos.remote_url("ETH"))

        probe.side_effect = lambda session, base: ""
        self.assertEqual(asyncio.run(logos.refresh(force=True)), 2)
        self.assertEqual(logos.cached_base("BTC"), "")


class SymbolRouteTests(AssetTestBase):
    def setUp(self):
        super().setUp()
        app_module = importlib.import_module("webapp.app")
        from webapp import security as web_security
        self.app_module = app_module
        self.stack.enter_context(patch.object(app_module, "db", self.db))
        self.stack.enter_context(patch.object(app_module.repo, "db", self.db))
        self.stack.enter_context(patch.object(web_security, "db", self.db))
        self.app = app_module.create_app(None)
        self.client = self.stack.enter_context(TestClient(self.app))
        self.ref = SymbolRef(display="BTCUSDT", base="BTC", quote="USDT", primary="toobit")

    def test_logo_route_redirects_aliases_and_falls_back(self):
        response = self.client.get("/api/assets/BTC/logo", follow_redirects=False)
        self.assertEqual(response.status_code, 307)
        self.assertEqual(response.headers["location"], logos.remote_url("BTC"))
        self.assertIn("max-age=604800", response.headers["cache-control"])

        response = self.client.get("/api/assets/xbt/logo", follow_redirects=False)
        self.assertEqual(response.status_code, 307)
        self.assertTrue(response.headers["location"].endswith("/btc.svg"))

        response = self.client.get("/api/assets/--/logo")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"].split(";")[0], "image/svg+xml")
        self.assertIn("max-age=86400", response.headers["cache-control"])
        self.assertIn(b"<svg", response.content[:300])

    def test_logo_route_uses_empty_cache_value_as_fallback(self):
        self.db.x("INSERT INTO asset_logos(base_asset, logo_url) VALUES('ETH', '')")
        response = self.client.get("/api/assets/ETH/logo", follow_redirects=False)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"<svg", response.content[:300])

    def test_header_adds_logo_fields_and_remembers_symbol(self):
        header = {"price": 100.0, "change_pct": 1.5}
        with patch.object(self.app_module.market, "resolve", AsyncMock(return_value=self.ref)), \
                patch.object(self.app_module.market, "header", AsyncMock(return_value=header)):
            response = self.client.get("/api/symbol/BTCUSDT/header", headers=self.user)
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["base_asset"], "BTC")
        self.assertEqual(body["quote_asset"], "USDT")
        self.assertEqual(body["logo_url"], "/api/assets/BTC/logo")
        self.assertTrue(any(item["key"] == "1d" for item in body["chart_timeframes"]))
        row = self.db.q1("SELECT base_asset, quote_asset FROM symbol_assets WHERE symbol = 'BTCUSDT'")
        self.assertEqual(row, {"base_asset": "BTC", "quote_asset": "USDT"})

    def test_price_requires_session_and_returns_live_values(self):
        self.assertEqual(self.client.get("/api/symbol/BTCUSDT/price").status_code, 401)
        tick = {"last": 80123.5, "pct": -2.25, "quote_volume": 987654.0}
        with patch.object(self.app_module.market, "resolve", AsyncMock(return_value=self.ref)), \
                patch.object(self.app_module.market, "ticker", AsyncMock(return_value=tick)):
            response = self.client.get("/api/symbol/BTCUSDT/price", headers=self.user)
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["symbol"], "BTCUSDT")
        self.assertEqual(body["price"], 80123.5)
        self.assertEqual(body["change_pct"], -2.25)
        self.assertEqual(body["quote_volume"], 987654.0)
        self.assertIsInstance(body["at"], int)

    def test_price_not_found_unavailable_and_timeout(self):
        with patch.object(self.app_module.market, "resolve", AsyncMock(return_value=None)):
            self.assertEqual(self.client.get("/api/symbol/NOPE/price", headers=self.user).status_code, 404)
        with patch.object(self.app_module.market, "resolve", AsyncMock(return_value=self.ref)), \
                patch.object(self.app_module.market, "ticker", AsyncMock(return_value={"last": 0})):
            self.assertEqual(self.client.get("/api/symbol/BTCUSDT/price", headers=self.user).status_code, 503)
        with patch.object(self.app_module.market, "resolve", AsyncMock(side_effect=asyncio.TimeoutError)):
            self.assertEqual(self.client.get("/api/symbol/BTCUSDT/price", headers=self.user).status_code, 504)


if __name__ == "__main__":
    unittest.main()
