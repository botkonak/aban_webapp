import importlib
import unittest
from contextlib import ExitStack
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image

import database
import payments
import plans
from webapp import admin_extra, content, security, security_ext, subscription_web

app_module = importlib.import_module("webapp.app")


def png_bytes():
    stream = BytesIO()
    Image.new("RGB", (16, 12), "green").save(stream, format="PNG")
    return stream.getvalue()


class UploadRouteTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        root = Path(self.stack.enter_context(TemporaryDirectory(prefix="aban-upload-tests-")))
        self.root = root / "uploads"
        self.db = database.Database("sqlite:///" + (root / "test.db").as_posix())
        self.db.init()
        self.stack.callback(self.db.engine.dispose)
        modules = (app_module, database, plans, payments, admin_extra, content, security, security_ext, subscription_web)
        for module in modules:
            self.stack.enter_context(patch.object(module, "db", self.db))
        self.stack.enter_context(patch.object(app_module.repo, "db", self.db))
        self.stack.enter_context(patch.object(app_module, "UPLOAD_ROOT", self.root))
        self.stack.enter_context(patch.object(app_module, "GUIDE_DIR", self.root / "guides"))
        self.stack.enter_context(patch.object(app_module, "_session_from_request", self.session))
        self.db.set("wsec_on", "0")
        self.db.x(
            "INSERT INTO structural_strategies(strategy_key, category, title, strategy_type, signal_type, logic_key) "
            "VALUES('probe', 'test', 'test', 'test', 'test', 'test')"
        )
        self.card = self.db.insert_id(
            "INSERT INTO payment_cards(plan, card_number, is_active) VALUES(?, ?, 1)",
            (plans.PLAN_WEBAPP, "0000000000000000"),
        )
        self.client = TestClient(app_module.create_app())
        self.stack.callback(self.client.close)
        self.admin = {"x-test-role": "admin"}
        self.user = {"x-test-role": "user"}

    @staticmethod
    def session(request):
        role = request.headers.get("x-test-role")
        return {"uid": 1001, "role": role} if role in ("admin", "user") else None

    def test_upload_routes_require_authentication_and_admin_role(self):
        endpoints = [
            ("/api/admin/media/upload", "file"),
            ("/api/admin/branding/logo", "image"),
            ("/api/admin/strategies/probe/guide", "image"),
            ("/api/admin/backups/upload", "file"),
            ("/api/subscription/request", "receipt"),
        ]
        for path, field in endpoints:
            with self.subTest(path=path):
                files = {field: ("image.png", png_bytes(), "image/png")}
                self.assertEqual(self.client.post(path, files=files).status_code, 401)
                if path.startswith("/api/admin/"):
                    self.assertEqual(self.client.post(path, files=files, headers=self.user).status_code, 403)
        self.assertEqual(list(self.root.rglob("*.png")), [])

    def test_full_middleware_stack_rejects_oversize_uploads(self):
        path = "/api/admin/branding/logo"
        declared = self.client.post(path, content=b"", headers={**self.admin, "content-length": "999999999"})
        self.assertEqual(declared.status_code, 413)
        self.assertEqual(declared.headers["cache-control"], "no-store")
        self.assertEqual(declared.headers["x-content-type-options"], "nosniff")
        denied = self.client.post(path, content=b"", headers={"content-length": "999999999"})
        self.assertEqual(denied.status_code, 401)
        streamed = self.client.post(
            path, files={"image": ("large.png", b"x" * (3 * 1024 * 1024 + 65537), "image/png")},
            headers={**self.admin, "content-length": "1"},
        )
        self.assertEqual(streamed.status_code, 413)
        self.assertEqual(streamed.json()["status"], 413)
        self.assertEqual([p for p in self.root.rglob("*") if p.is_file()], [])

    def test_media_upload_uses_validated_bytes_and_remains_public(self):
        data = png_bytes()
        response = self.client.post("/api/admin/media/upload", headers=self.admin,
                                    files={"file": ("valid.png", data, "image/png")})
        self.assertEqual(response.status_code, 200, response.text)
        item = response.json()["item"]
        self.assertEqual(item["mime_type"], "image/png")
        self.assertEqual(item["size"], len(data))
        public = self.client.get(item["path"])
        self.assertEqual(public.status_code, 200)
        self.assertEqual(public.content, data)
        self.assertEqual(public.headers["x-content-type-options"], "nosniff")
        self.assertIn("sandbox", public.headers["content-security-policy"])
        unsafe = self.client.post("/api/admin/media/upload", headers=self.admin,
                                  files={"file": ("unsafe.svg", b'<svg onload="bad()"/>', "image/svg+xml")})
        self.assertEqual(unsafe.status_code, 400)
        invalid_encoding = self.client.post(
            "/api/admin/media/upload", headers=self.admin,
            files={"file": ("encoding.svg", b'<?xml version="1.0" encoding="unknown-codec"?><svg/>', "image/svg+xml")},
        )
        self.assertEqual(invalid_encoding.status_code, 400)
        self.assertEqual(self.db.q1("SELECT COUNT(*) AS n FROM media")["n"], 1)
        safe = self.client.post("/api/admin/media/upload", headers=self.admin,
                                files={"file": ("safe.svg", b'<svg><rect width="10" height="10" fill="red"/></svg>', "image/svg+xml")})
        self.assertEqual(safe.status_code, 200, safe.text)
        self.assertIn(b"http://www.w3.org/2000/svg", self.client.get(safe.json()["item"]["path"]).content)

    def test_invalid_images_rejected_by_all_image_endpoints_without_storage(self):
        endpoints = [
            ("/api/admin/media/upload", "file", self.admin, {}),
            ("/api/admin/branding/logo", "image", self.admin, {}),
            ("/api/admin/strategies/probe/guide", "image", self.admin, {}),
            ("/api/subscription/request", "receipt", self.user, {"network_key": f"card:{self.card}"}),
        ]
        payloads = [("fake.webp", b"RIFF", "image/webp"), ("wrong.png", png_bytes(), "image/jpeg")]
        for path, field, headers, fields in endpoints:
            for payload in payloads:
                with self.subTest(path=path, filename=payload[0]):
                    response = self.client.post(path, headers=headers, data=fields, files={field: payload})
                    self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual([p for p in self.root.rglob("*") if p.is_file()], [])
        self.assertEqual(self.db.q1("SELECT COUNT(*) AS n FROM media")["n"], 0)
        self.assertEqual(self.db.q1("SELECT COUNT(*) AS n FROM sub_requests")["n"], 0)

    def test_logo_and_guide_names_do_not_collide(self):
        for path, field, result_key in (
            ("/api/admin/branding/logo", "image", "logo"),
            ("/api/admin/strategies/probe/guide", "image", "guide_image"),
        ):
            urls = []
            with self.subTest(path=path):
                for _ in range(2):
                    response = self.client.post(path, headers=self.admin, files={field: ("image.png", png_bytes(), "image/png")})
                    self.assertEqual(response.status_code, 200, response.text)
                    urls.append(response.json()[result_key])
                self.assertNotEqual(urls[0], urls[1])
                self.assertEqual(self.client.get(urls[0]).status_code, 404)
                self.assertEqual(self.client.get(urls[1]).content, png_bytes())

    def test_receipt_is_private_and_admin_can_review_it(self):
        response = self.client.post(
            "/api/subscription/request", headers=self.user,
            data={"network_key": f"card:{self.card}"},
            files={"receipt": ("receipt.png", png_bytes(), "image/png")},
        )
        self.assertEqual(response.status_code, 200, response.text)
        rid = response.json()["request_id"]
        row = self.db.get_sub_request(rid)
        public_path = row.photo_id.removeprefix("local:")
        self.assertTrue((self.root / public_path.removeprefix("/uploads/")).is_file())
        for headers in ({}, self.user, self.admin):
            for method in ("get", "head"):
                with self.subTest(role=headers, method=method):
                    self.assertNotEqual(getattr(self.client, method)(public_path, headers=headers).status_code, 200)
        private_path = f"/api/admin/requests/{rid}/media"
        self.assertEqual(self.client.get(private_path).status_code, 401)
        self.assertEqual(self.client.get(private_path, headers=self.user).status_code, 403)
        private = self.client.get(private_path, headers=self.admin)
        self.assertEqual(private.status_code, 200)
        self.assertEqual(private.content, png_bytes())
        self.assertEqual(private.headers["cache-control"], "private, no-store")
        self.assertEqual(private.headers["content-type"], "image/png")

    def test_receipt_cannot_be_accessed_via_traversal_or_unlisted_directories(self):
        (self.root / "receipts").mkdir()
        (self.root / "receipts" / "private.png").write_bytes(png_bytes())
        (self.root / "private.txt").write_text("private", encoding="utf-8")
        urls = (
            "/uploads/receipts/private.png", "/uploads/private.txt",
            "/uploads/media/../receipts/private.png", "/uploads/media/%2e%2e/receipts/private.png",
            "/uploads/media/%2e%2e%2freceipts%2fprivate.png", "/uploads/media/%2e%2e%5creceipts%5cprivate.png",
        )
        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 404)
        (self.root / "branding" / "public.png").write_bytes(png_bytes())
        rid = self.db.add_sub_request(1001, "test", "local:/uploads/receipts/../branding/public.png", service_type="webapp")
        self.assertEqual(self.client.get(f"/api/admin/requests/{rid}/media", headers=self.admin).status_code, 404)


if __name__ == "__main__":
    unittest.main()
