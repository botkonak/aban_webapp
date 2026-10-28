"""Chart settings routes with real SQLite storage and signed-session auth."""
import importlib
import json
import os
import unittest
from contextlib import ExitStack
from itertools import product
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event

# Never read or create persisted application secrets when running this file alone.
with patch.dict(os.environ, {
    "WEBAPP_SECRET": "chart-settings-test-secret",
    "WEBHOOK_SECRET": "chart-settings-test-webhook-secret",
}):
    import config
    import database
    from webapp import admin_extra
    from webapp.auth import COOKIE_NAME, hash_password, session_token


DEFAULTS = {
    "theme": "dark",
    "showRealtimePrice": True,
    "showVolumePanel": True,
    "disableSymbolSearch": True,
    "enableDrawingTools": True,
}
MUTABLE = ("theme", "showRealtimePrice", "showVolumePanel")
ADMIN_URL = "/api/admin/chart-settings"
USER_URL = "/api/chart-settings"


class ChartSettingsTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        root = Path(self.stack.enter_context(TemporaryDirectory(prefix="aban-chart-tests-")))
        self.stack.enter_context(patch.object(config, "WEBAPP_SECRET", "chart-settings-test-secret"))
        self.stack.enter_context(patch.object(config, "ADMIN_UID", 9001))
        self.stack.enter_context(patch.object(config, "WEBAPP_UPLOAD_DIR", str(root / "uploads")))

        # Importing the app creates its guide directory, so redirect uploads first.
        # Do not create the full app or run its lifespan/background services.
        app_module = importlib.import_module("webapp.app")
        self.db = database.Database("sqlite:///" + (root / "chart.db").as_posix())
        self.db.init()
        self.stack.callback(self.db.close)
        self.stack.enter_context(patch.object(admin_extra, "db", self.db))
        self.stack.enter_context(patch.object(app_module, "db", self.db))
        self.stack.enter_context(patch.object(app_module.repo, "db", self.db))
        self.db.x(
            "INSERT INTO users(user_id, sub_until, webapp_sub_until) VALUES(?, ?, ?)",
            (1001, 123, 0),
        )
        self.db.x(
            "INSERT INTO webapp_users(user_id, access_enabled, password_hash) VALUES(?, 1, ?)",
            (1001, hash_password("chart-test-password")),
        )
        self.db.set("nodereal_api_key", "private-test-value")
        self.db.set("web_sub_mode", "manual")
        self.db.set("web_sub_amount", "23")

        app = FastAPI()
        admin_extra.register(
            app,
            require_admin=app_module._require_admin,
            require_user=app_module._require_user,
            payment_admin=lambda row: row,
            client_ip=lambda request: "127.0.0.1",
            upload_root=root / "uploads",
        )
        self.client = self.stack.enter_context(TestClient(app))
        self.admin = {"Authorization": "Bearer " + session_token(config.ADMIN_UID, "admin")}
        self.user = {"Authorization": "Bearer " + session_token(1001, "user")}
        self.payload = {"theme": "light", "showRealtimePrice": False, "showVolumePanel": False}

    def settings_rows(self):
        return {row["key"]: row["value"] for row in self.db.q("SELECT key, value FROM settings")}

    def assert_gets(self, expected):
        for path, headers in ((ADMIN_URL, self.admin), (USER_URL, self.user), (USER_URL, self.admin)):
            with self.subTest(path=path, role="admin" if headers == self.admin else "user"):
                response = self.client.get(path, headers=headers)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json(), expected)
                self.assertEqual(set(response.json()), set(DEFAULTS))

    def test_defaults_are_seeded_as_full_json_and_returned_directly(self):
        self.assertEqual(json.loads(config.DEFAULT_SETTINGS["chart_settings"]), DEFAULTS)
        row = self.db.q1("SELECT value FROM settings WHERE key = 'chart_settings'")
        self.assertEqual(json.loads(row["value"]), DEFAULTS)
        self.assert_gets(DEFAULTS)

    def test_missing_setting_uses_defaults_without_writing(self):
        self.db.x("DELETE FROM settings WHERE key = 'chart_settings'")
        self.db.reload_caches()
        self.assert_gets(DEFAULTS)
        self.assertIsNone(self.db.q1("SELECT value FROM settings WHERE key = 'chart_settings'"))

    def test_routes_require_authentication_and_admin_writes_require_role(self):
        before = self.settings_rows()
        for headers in ({}, {"Authorization": "Bearer invalid-session"}):
            for path in (ADMIN_URL, USER_URL):
                with self.subTest(path=path, headers=headers):
                    self.assertEqual(self.client.get(path, headers=headers).status_code, 401)
            self.assertEqual(self.client.put(ADMIN_URL, headers=headers, json=self.payload).status_code, 401)
        self.assertEqual(self.client.get(ADMIN_URL, headers=self.user).status_code, 403)
        self.assertEqual(self.client.put(ADMIN_URL, headers=self.user, json=self.payload).status_code, 403)
        self.assertEqual(self.settings_rows(), before)

    def test_real_session_checks_and_cookie_authentication(self):
        impostor = {"Authorization": "Bearer " + session_token(1001, "admin")}
        self.assertEqual(self.client.get(ADMIN_URL, headers=impostor).status_code, 401)
        self.db.x("UPDATE webapp_users SET access_enabled = 0 WHERE user_id = 1001")
        self.assertEqual(self.client.get(USER_URL, headers=self.user).status_code, 401)
        self.db.x("UPDATE webapp_users SET access_enabled = 1 WHERE user_id = 1001")
        self.client.cookies.set(COOKIE_NAME, session_token(1001, "user"))
        self.assertEqual(self.client.get(USER_URL).json(), DEFAULTS)
        self.assertEqual(self.client.get(ADMIN_URL).status_code, 403)

    def test_all_mutable_combinations_persist_full_object(self):
        before = self.settings_rows()
        users = self.db.q("SELECT * FROM users")
        accounts = self.db.q("SELECT * FROM webapp_users")
        analysis_version = self.db.settings_version
        for theme, realtime, volume in product(("dark", "light"), (False, True), (False, True)):
            payload = {"theme": theme, "showRealtimePrice": realtime, "showVolumePanel": volume}
            expected = {**DEFAULTS, **payload}
            with self.subTest(payload=payload):
                response = self.client.put(ADMIN_URL, headers=self.admin, json=payload)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json(), expected)
                row = self.db.q1("SELECT value FROM settings WHERE key = 'chart_settings'")
                self.assertEqual(json.loads(row["value"]), expected)
                self.assert_gets(expected)
        after = self.settings_rows()
        before.pop("chart_settings")
        after.pop("chart_settings")
        self.assertEqual(after, before)
        self.assertEqual(self.db.q("SELECT * FROM users"), users)
        self.assertEqual(self.db.q("SELECT * FROM webapp_users"), accounts)
        self.assertEqual(self.db.q("SELECT * FROM sub_requests"), [])
        self.assertEqual(self.db.q("SELECT * FROM payments_history"), [])
        self.assertEqual(self.db.settings_version, analysis_version)

    def test_persistence_survives_database_reopen(self):
        expected = {**DEFAULTS, **self.payload}
        self.assertEqual(self.client.put(ADMIN_URL, headers=self.admin, json=self.payload).status_code, 200)
        self.db.close()
        reopened = database.Database(self.db.raw_url)
        reopened.init()
        self.stack.callback(reopened.close)
        with patch.object(admin_extra, "db", reopened), patch.object(admin_extra.repo, "db", reopened):
            self.assertEqual(json.loads(reopened.get("chart_settings")), expected)
            self.assert_gets(expected)

    def test_invalid_input_is_422_without_any_sql_writes(self):
        # A non-default baseline catches accidental resets on rejected requests.
        self.assertEqual(self.client.put(ADMIN_URL, headers=self.admin, json=self.payload).status_code, 200)
        before = self.settings_rows()
        invalid = [{}, [], "dark", {"theme": "light"}]
        for theme in ("auto", "Dark", " light ", "", True, 1, None, [], {}):
            invalid.append({**self.payload, "theme": theme})
        for key in ("showRealtimePrice", "showVolumePanel"):
            for value in ("true", "false", "0", "1", 0, 1, 0.0, 1.0, None, [], {}):
                invalid.append({**self.payload, key: value})
        for key in ("disableSymbolSearch", "enableDrawingTools", "showIndicators", "unknown"):
            for value in (True, False):
                invalid.append({**self.payload, key: value})
        for key in MUTABLE:
            invalid.append({name: value for name, value in self.payload.items() if name != key})

        writes = []

        def record_writes(conn, cursor, statement, parameters, context, executemany):
            if statement.lstrip().split()[0].upper() in {"INSERT", "UPDATE", "DELETE", "REPLACE"}:
                writes.append(statement)

        event.listen(self.db.engine, "before_cursor_execute", record_writes)
        try:
            for payload in invalid:
                with self.subTest(payload=payload):
                    response = self.client.put(ADMIN_URL, headers=self.admin, json=payload)
                    self.assertEqual(response.status_code, 422, response.text)
                    self.assertEqual(self.settings_rows(), before)
            for raw in ("{invalid", "null"):
                response = self.client.put(
                    ADMIN_URL, headers={**self.admin, "Content-Type": "application/json"}, content=raw,
                )
                self.assertEqual(response.status_code, 422, response.text)
            self.assertEqual(writes, [])
            self.assertEqual(self.settings_rows(), before)
        finally:
            event.remove(self.db.engine, "before_cursor_execute", record_writes)

    def test_malformed_legacy_storage_falls_back_without_rewriting(self):
        malformed = ["", "{invalid", "null", "[]", "42", "true", '"dark"']
        for key, value in (("theme", "auto"), ("showRealtimePrice", "false"), ("showVolumePanel", 0)):
            malformed.append(json.dumps({**self.payload, key: value}))
        for raw in malformed:
            with self.subTest(raw=raw):
                self.db.set("chart_settings", raw)
                self.assert_gets(DEFAULTS)
                self.assertEqual(self.db.get("chart_settings"), raw)
                self.assertEqual(self.db.q1("SELECT value FROM settings WHERE key = 'chart_settings'")["value"], raw)

    def test_partial_legacy_storage_fills_missing_fields(self):
        for stored in ({}, {"theme": "light"}, {"showRealtimePrice": False}, {"showVolumePanel": False}):
            with self.subTest(stored=stored):
                self.db.set("chart_settings", json.dumps(stored))
                self.assert_gets({**DEFAULTS, **stored})

    def test_get_locks_are_true_and_legacy_extras_never_leak(self):
        for locked_value in (False, 0, "false", None, [], {}):
            stored = {
                **self.payload,
                "disableSymbolSearch": locked_value,
                "enableDrawingTools": locked_value,
                "api_key": "must-not-leak",
                "showIndicators": False,
                "web_sub_amount": "secret-extra",
            }
            with self.subTest(locked_value=locked_value):
                raw = json.dumps(stored)
                self.db.set("chart_settings", raw)
                self.assert_gets({**DEFAULTS, **self.payload})
                self.assertEqual(self.db.get("chart_settings"), raw)
        response = self.client.put(ADMIN_URL, headers=self.admin, json=self.payload)
        self.assertEqual(response.json(), {**DEFAULTS, **self.payload})
        self.assertEqual(json.loads(self.db.get("chart_settings")), response.json())


if __name__ == "__main__":
    unittest.main()
