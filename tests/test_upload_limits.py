import asyncio
import json
import unittest
from unittest.mock import patch

from fastapi import FastAPI, File, UploadFile
from starlette import formparsers
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse

from webapp.security import UploadLimitMiddleware


async def count_body(scope, receive, send):
    size = 0
    async for chunk in Request(scope, receive).stream():
        size += len(chunk)
    await JSONResponse({"size": size})(scope, receive, send)


class UploadLimitTests(unittest.IsolatedAsyncioTestCase):
    async def invoke(self, app, path="/api/admin/branding/logo", size=0, headers=(), chunks=None, method="POST"):
        scope = {
            "type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"},
            "http_version": "1.1", "method": method, "scheme": "http", "path": path,
            "raw_path": path.encode(), "root_path": "", "query_string": b"", "headers": list(headers),
            "client": ("127.0.0.1", 1234), "server": ("test", 80),
        }
        if chunks is None:
            chunks = (b"x" * min(65536, size - offset) for offset in range(0, size, 65536))
        chunks = iter(chunks)
        reads = 0
        sent = []

        async def receive():
            nonlocal reads
            reads += 1
            await asyncio.sleep(0)
            try:
                return {"type": "http.request", "body": next(chunks), "more_body": True}
            except StopIteration:
                return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            sent.append(message.copy())

        await app(scope, receive, send)
        starts = [message for message in sent if message["type"] == "http.response.start"]
        self.assertEqual(len(starts), 1)
        body = b"".join(message.get("body", b"") for message in sent if message["type"] == "http.response.body")
        return starts[0]["status"], json.loads(body), reads

    def middleware(self, app=count_body, auth=None):
        callback = auth or (lambda request: {"uid": 1, "role": "admin"})
        return UploadLimitMiddleware(app, callback, callback)

    async def test_all_limits_and_trailing_slash(self):
        caps = dict(UploadLimitMiddleware._FILE_LIMITS)
        caps["/api/admin/strategies/test/guide"] = 5 * 1024 * 1024
        app = self.middleware()
        for path, cap in caps.items():
            for suffix in ("", "/"):
                with self.subTest(path=path + suffix):
                    status, body, _ = await self.invoke(app, path + suffix, size=cap + 65536)
                    self.assertEqual(status, 200)
                    self.assertEqual(body["size"], cap + 65536)
                    status, body, _ = await self.invoke(app, path + suffix, size=cap + 65537)
                    self.assertEqual((status, body["status"]), (413, 413))

    async def test_missing_or_false_content_length_does_not_bypass_limit(self):
        for headers in ([], [(b"content-length", b"0")], [(b"content-length", b"1")]):
            with self.subTest(headers=headers):
                status, _, _ = await self.invoke(self.middleware(), size=3 * 1024 * 1024 + 65537, headers=headers)
                self.assertEqual(status, 413)

    async def test_content_length_and_auth_rejected_before_body_read(self):
        headers_to_test = [
            ([(b"content-length", b"999999999")], 413),
            ([(b"content-length", b"9" * 5000)], 413),
            ([(b"content-length", b"-1")], 400),
            ([(b"content-length", b"1"), (b"content-length", b"1")], 400),
            ([(b"content-length", b"invalid")], 400),
        ]
        for headers, expected in headers_to_test:
            status, _, reads = await self.invoke(self.middleware(), headers=headers)
            self.assertEqual((status, reads), (expected, 0))
        for expected in (401, 403):
            def denied(request):
                raise HTTPException(expected, "Denied")
            status, _, reads = await self.invoke(self.middleware(auth=denied), headers=[(b"content-length", b"invalid")])
            self.assertEqual((status, reads), (expected, 0))

    async def test_unrelated_routes_and_methods_unchanged(self):
        def unexpected(request):
            raise AssertionError("Unexpected auth")
        app = self.middleware(auth=unexpected)
        for path, method in (("/api/other", "POST"), ("/api/admin/branding/logo", "GET")):
            status, body, _ = await self.invoke(app, path, size=3, method=method, headers=[(b"content-length", b"invalid")])
            self.assertEqual((status, body["size"]), (200, 3))

    async def test_fastapi_closes_partial_multipart_files_on_limit(self):
        app = FastAPI()

        @app.post("/api/admin/branding/logo")
        async def upload(file: UploadFile = File(...)):
            return {"size": file.size}

        files = []
        original = formparsers.SpooledTemporaryFile

        def tracked(*args, **kwargs):
            file = original(*args, **kwargs)
            files.append(file)
            return file

        def multipart():
            yield b'--test\r\nContent-Disposition: form-data; name="file"; filename="test.png"\r\nContent-Type: image/png\r\n\r\n'
            for _ in range(52):
                yield b"x" * 65536
            yield b"\r\n--test--\r\n"

        with patch.object(formparsers, "SpooledTemporaryFile", tracked):
            result = await self.invoke(
                self.middleware(app), chunks=multipart(),
                headers=[(b"content-type", b"multipart/form-data; boundary=test"), (b"content-length", b"1")],
            )
        self.assertEqual(result[0], 413)
        self.assertEqual(len(files), 1)
        self.assertTrue(files[0].closed)
        self.assertTrue(files[0]._rolled)

    async def test_concurrent_requests_have_independent_limits(self):
        app = self.middleware()
        before = app.__dict__.copy()
        sizes = [20, 3 * 1024 * 1024 + 65537, 35]
        results = await asyncio.gather(*(self.invoke(app, size=size) for size in sizes))
        self.assertEqual([result[0] for result in results], [200, 413, 200])
        self.assertEqual(app.__dict__, before)


if __name__ == "__main__":
    unittest.main()
