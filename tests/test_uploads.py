import asyncio
import unittest
import xml.etree.ElementTree as ET
from io import BytesIO
from unittest.mock import patch

from fastapi import HTTPException, UploadFile
from PIL import Image
from starlette.datastructures import Headers

from webapp import uploads


def raster(fmt="PNG", frames=1):
    stream = BytesIO()
    images = [Image.new("RGB", (12, 10), (index * 40, 80, 120)) for index in range(frames)]
    options = {"save_all": True, "append_images": images[1:]} if frames > 1 else {}
    images[0].save(stream, format=fmt, **options)
    return stream.getvalue()


class UploadValidationTests(unittest.TestCase):
    def validate(self, data, filename="image.png", mime="image/png", **kwargs):
        options = {"allowed_extensions": uploads.IMAGE_FORMATS, "max_bytes": 5 * 1024 * 1024}
        options.update(kwargs)
        return uploads.validate_image(data, filename, mime, **options)

    def rejected(self, *args, **kwargs):
        with self.assertRaises(HTTPException) as raised:
            self.validate(*args, **kwargs)
        self.assertEqual(raised.exception.status_code, 400)

    def test_supported_rasters_roundtrip(self):
        for ext, (fmt, mime) in uploads.IMAGE_FORMATS.items():
            if fmt == "SVG":
                continue
            with self.subTest(ext=ext):
                data = raster(fmt)
                self.assertEqual(self.validate(data, "image" + ext, mime), (ext, mime, data))

    def test_mime_and_decoded_format_agree_with_extension(self):
        data = raster()
        for mime in ("", "text/html", "application/octet-stream", "image/jpeg"):
            with self.subTest(mime=mime):
                self.rejected(data, mime=mime)
        self.rejected(data, "image.jpg", "image/jpeg")
        self.rejected(data, "image.svg", "image/svg+xml")
        self.rejected(data, "image.bmp", "image/bmp")
        self.assertEqual(self.validate(data, "IMAGE.PNG", "IMAGE/PNG; charset=binary")[0], ".png")

    def test_empty_truncated_and_signature_only_rejected(self):
        self.rejected(b"")
        for ext, fmt, mime, signature in (
            ("png", "PNG", "image/png", b"\x89PNG\r\n\x1a\n"),
            ("jpg", "JPEG", "image/jpeg", b"\xff\xd8\xff"),
            ("gif", "GIF", "image/gif", b"GIF8"),
            ("webp", "WEBP", "image/webp", b"RIFF"),
        ):
            with self.subTest(ext=ext):
                self.rejected(signature, "image." + ext, mime)
                self.rejected(raster(fmt)[:20], "image." + ext, mime)
        self.rejected(b"RIFF\x10\x00\x00\x00WAVEfmt ", "image.webp", "image/webp")
        self.rejected(raster("JPEG")[:-20], "image.jpg", "image/jpeg")
        self.rejected(raster()[:-10])

    def test_bytes_pixels_and_animation_budgets(self):
        data = raster()
        self.assertEqual(self.validate(data, max_bytes=len(data))[2], data)
        self.rejected(data, max_bytes=len(data) - 1)
        with patch.object(uploads, "MAX_PIXELS", 119):
            self.rejected(data)
        animation = raster("GIF", 3)
        self.assertEqual(self.validate(animation, "animation.gif", "image/gif")[2], animation)
        with patch.object(uploads, "MAX_FRAMES", 2):
            self.rejected(animation, "animation.gif", "image/gif")
        with patch.object(uploads, "MAX_TOTAL_PIXELS", 250):
            self.rejected(animation, "animation.gif", "image/gif")

    def test_async_reader_bounds_read_and_restricts_formats(self):
        data = raster()
        upload = UploadFile(BytesIO(data), filename="image.png", headers=Headers({"content-type": "image/png"}))
        result = asyncio.run(uploads.read_image(upload, max_bytes=len(data)))
        self.assertEqual(result[2], data)
        upload = UploadFile(BytesIO(b"x" * 1024), filename="image.png", headers=Headers({"content-type": "image/png"}))
        with self.assertRaises(HTTPException):
            asyncio.run(uploads.read_image(upload, max_bytes=100))
        self.assertEqual(upload.file.tell(), 101)
        self.rejected(b'<svg xmlns="http://www.w3.org/2000/svg"/>', "image.svg", "image/svg+xml",
                      allowed_extensions=uploads.RASTER_EXTENSIONS)

    def test_safe_svg_geometry_gradients_and_internal_references(self):
        text = '''<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 100 100">
          <defs><linearGradient id="paint"><stop offset="0" stop-color="#fff"/><stop offset="1" stop-color="#000"/></linearGradient>
          <path id="outline" d="M0 0L10 10"/></defs>
          <rect width="100" height="100" fill="url('#paint')"/>
          <use xlink:href="#outline"/>
          <text x="10" y="20" style="fill:red;font-size:12px">آبان</text>
        </svg>'''
        for encoding in ("utf-8", "utf-16"):
            with self.subTest(encoding=encoding):
                data = self.validate(text.encode(encoding), "image.svg", "image/svg+xml")[2]
                root = ET.fromstring(data)
                self.assertEqual(root.tag, "{http://www.w3.org/2000/svg}svg")
                self.assertIn("آبان", "".join(root.itertext()))
                self.assertIn(b"url(#paint)", data)
                self.assertEqual(self.validate(data, "image.svg", "image/svg+xml")[2], data)

    def test_active_svg_and_external_resources_rejected_in_all_encodings(self):
        bodies = [
            '<script>alert(1)</script>',
            '<g onload="alert(1)"/>',
            '<foreignObject><div>html</div></foreignObject>',
            '<use href="https://example.invalid/remote.svg#x"/>',
            '<use href="java&#x73;cript:alert(1)"/>',
            '<image href="data:image/svg+xml,bad"/>',
            '<rect fill="url(https://example.invalid/a)"/>',
            '<rect style="fill:u\\72l(https://example.invalid/a)"/>',
            '<rect style="fill:/**/url(https://example.invalid/a)"/>',
            '<style>@import "https://example.invalid/a";</style>',
            '<animate attributeName="href" values="javascript:alert(1)"/>',
            '<g xmlns="http://www.w3.org/1999/xhtml"/>',
            '<g xml:base="https://example.invalid/"/>',
        ]
        for body in bodies:
            for encoding in ("utf-8", "utf-16"):
                with self.subTest(body=body, encoding=encoding):
                    data = ('<svg xmlns="http://www.w3.org/2000/svg">' + body + '</svg>').encode(encoding)
                    self.rejected(data, "image.svg", "image/svg+xml")

    def test_svg_entities_cycles_complexity_and_invalid_xml_rejected(self):
        payloads = [
            '<!DOCTYPE svg [<!ENTITY a "expanded">]><svg>&a;</svg>',
            '<!DOCTYPE svg SYSTEM "file:///nonexistent"><svg/>',
            '<svg><defs><g id="a"><use href="#a"/></g></defs><use href="#a"/></svg>',
            '<svg><g id="a"/><g id="a"/></svg>',
            '<svg><use href="#missing"/></svg>',
            '<svg>' + '<g>' * 66 + '</g>' * 66 + '</svg>',
            '<svg>' + '<path/>' * 10001 + '</svg>',
            '<?xml version="1.0" encoding="unknown-codec"?><svg/>',
            '<svg', '<html/>', '',
        ]
        chain = '<g id="n0"><path d="M0 0L1 1"/></g>'
        for index in range(1, 15):
            chain += f'<g id="n{index}"><use href="#n{index - 1}"/><use href="#n{index - 1}"/></g>'
        payloads.append('<svg><defs>' + chain + '</defs><use href="#n14"/></svg>')
        for text in payloads:
            with self.subTest(text=text[:120]):
                self.rejected(text.encode(), "image.svg", "image/svg+xml")
        for text in payloads[:2]:
            self.rejected(text.encode("utf-16"), "image.svg", "image/svg+xml")


if __name__ == "__main__":
    unittest.main()
