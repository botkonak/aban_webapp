from __future__ import annotations

import re
import warnings
import xml.etree.ElementTree as ET
from io import BytesIO
from pathlib import Path

from fastapi import HTTPException, UploadFile
from PIL import Image, UnidentifiedImageError
from starlette.concurrency import run_in_threadpool

IMAGE_FORMATS = {
    ".jpg": ("JPEG", "image/jpeg"),
    ".jpeg": ("JPEG", "image/jpeg"),
    ".png": ("PNG", "image/png"),
    ".webp": ("WEBP", "image/webp"),
    ".gif": ("GIF", "image/gif"),
    ".svg": ("SVG", "image/svg+xml"),
}
RASTER_EXTENSIONS = frozenset(IMAGE_FORMATS) - {".svg"}
MAX_PIXELS = 20_000_000
MAX_TOTAL_PIXELS = 60_000_000
MAX_FRAMES = 100
_SVG_NS = "http://www.w3.org/2000/svg"
_XLINK_NS = "http://www.w3.org/1999/xlink"
_SVG_ELEMENTS = {
    "svg", "g", "defs", "path", "rect", "circle", "ellipse", "line", "polyline", "polygon",
    "text", "tspan", "textPath", "title", "desc", "linearGradient", "radialGradient", "stop",
    "clipPath", "mask", "pattern", "marker", "symbol", "use",
}
_SVG_STYLES = {
    "fill", "fill-opacity", "fill-rule", "stroke", "stroke-width", "stroke-opacity",
    "stroke-linecap", "stroke-linejoin", "stroke-miterlimit", "stroke-dasharray", "stroke-dashoffset",
    "opacity", "color", "stop-color", "stop-opacity", "clip-path", "clip-rule", "mask",
    "marker-start", "marker-mid", "marker-end", "font-family", "font-size", "font-weight",
    "font-style", "text-anchor", "dominant-baseline", "letter-spacing", "word-spacing",
    "display", "visibility", "vector-effect", "paint-order",
}
_SVG_ATTRIBUTES = _SVG_STYLES | {
    "id", "class", "version", "width", "height", "viewBox", "preserveAspectRatio", "transform",
    "x", "y", "x1", "y1", "x2", "y2", "dx", "dy", "cx", "cy", "r", "rx", "ry", "d", "points",
    "pathLength", "gradientUnits", "gradientTransform", "spreadMethod", "offset", "fx", "fy", "fr",
    "clipPathUnits", "maskUnits", "maskContentUnits", "patternUnits", "patternContentUnits",
    "patternTransform", "markerWidth", "markerHeight", "markerUnits", "orient", "refX", "refY",
    "textLength", "lengthAdjust", "rotate", "startOffset", "method", "spacing",
}
_FRAGMENT = re.compile(r"#[A-Za-z_][A-Za-z0-9_.:-]*\Z")
_LOCAL_URL = re.compile(r"url\(\s*(['\"]?)(#[A-Za-z_][A-Za-z0-9_.:-]*)\1\s*\)\Z", re.I)


class _SVGTreeBuilder(ET.TreeBuilder):
    def doctype(self, name, pubid, system):
        # Reject declarations before Expat can expand internal entities, including UTF-16 input.
        raise ValueError("SVG document types are not allowed")


def _svg_value(value: str, refs: list[str]) -> str:
    local = _LOCAL_URL.fullmatch(value.strip())
    if local:
        refs.append(local.group(2)[1:])
        return "url(" + local.group(2) + ")"
    if any(c in value for c in ("\\", "@", "<", ">", ";", "'", '"')) or re.search(
        r"url\s*\(|expression\s*\(|/\*|\*/", value, re.I
    ):
        raise ValueError("Unsafe SVG attribute")
    return value


def _sanitize_svg(data: bytes) -> bytes:
    root = ET.fromstring(data, parser=ET.XMLParser(target=_SVGTreeBuilder()))
    if root.tag not in ("svg", "{" + _SVG_NS + "}svg"):
        raise ValueError("Not an SVG document")
    ids: dict[str, ET.Element] = {}
    references: dict[ET.Element, list[str]] = {}
    stack = [(root, 0)]
    while stack:
        node, depth = stack.pop()
        if depth > 64 or len(references) >= 10_000:
            raise ValueError("SVG is too complex")
        name = node.tag
        if name.startswith("{" + _SVG_NS + "}"):
            name = name[len(_SVG_NS) + 2:]
        if name not in _SVG_ELEMENTS:
            raise ValueError("Unsupported SVG element")
        node.tag = "{" + _SVG_NS + "}" + name
        refs: list[str] = []
        references[node] = refs
        attrs: dict[str, str] = {}
        for attr, value in node.attrib.items():
            if attr in ("href", "{" + _XLINK_NS + "}href"):
                if not _FRAGMENT.fullmatch(value):
                    raise ValueError("Only internal SVG references are allowed")
                refs.append(value[1:])
                attrs[attr] = value
            elif attr == "style":
                styles = []
                for declaration in value.split(";"):
                    if not declaration.strip():
                        continue
                    prop, sep, val = declaration.partition(":")
                    prop = prop.strip().lower()
                    if not sep or prop not in _SVG_STYLES:
                        raise ValueError("Unsupported SVG style")
                    styles.append(prop + ":" + _svg_value(val.strip(), refs))
                attrs[attr] = ";".join(styles)
            elif attr == "{" + "http://www.w3.org/XML/1998/namespace" + "}space" and value in ("default", "preserve"):
                attrs[attr] = value
            elif attr in _SVG_ATTRIBUTES:
                attrs[attr] = _svg_value(value, refs)
            else:
                raise ValueError("Unsupported SVG attribute")
        ident = attrs.get("id")
        if ident is not None:
            if not _FRAGMENT.fullmatch("#" + ident) or ident in ids:
                raise ValueError("Invalid SVG identifier")
            ids[ident] = node
        node.attrib.clear()
        node.attrib.update(attrs)
        stack.extend((child, depth + 1) for child in node)

    costs: dict[ET.Element, tuple[int, int]] = {}
    visiting: set[ET.Element] = set()

    def cost(node: ET.Element, depth: int = 0) -> tuple[int, int]:
        if node in visiting or depth > 64:
            raise ValueError("Cyclic SVG reference")
        if node in costs:
            return costs[node]
        visiting.add(node)
        count, height = 1, 1
        targets = list(node)
        for ref in references[node]:
            if ref not in ids:
                raise ValueError("Missing SVG reference")
            targets.append(ids[ref])
        for child in targets:
            child_count, child_height = cost(child, depth + 1)
            count += child_count
            height = max(height, child_height + 1)
            if count > 10_000 or height > 64:
                raise ValueError("SVG reference expansion is too large")
        visiting.remove(node)
        costs[node] = (count, height)
        return costs[node]

    cost(root)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def validate_image(data: bytes, filename: str, content_type: str, *, allowed_extensions, max_bytes: int):
    ext = Path(filename or "").suffix.lower()
    if ext not in allowed_extensions or ext not in IMAGE_FORMATS:
        raise HTTPException(400, "فرمت تصویر مجاز نیست.")
    expected_format, mime = IMAGE_FORMATS[ext]
    if (content_type or "").split(";", 1)[0].strip().lower() != mime:
        raise HTTPException(400, "نوع محتوای فایل با پسوند تصویر هم‌خوانی ندارد.")
    if not data or len(data) > max_bytes:
        raise HTTPException(400, "فایل تصویر خالی است یا از حجم مجاز بیشتر است.")
    try:
        if ext == ".svg":
            data = _sanitize_svg(data)
            if len(data) > max_bytes:
                raise ValueError("Sanitized SVG exceeds size limit")
        else:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(BytesIO(data), formats=[expected_format]) as image:
                    if image.width * image.height > MAX_PIXELS:
                        raise ValueError("Image dimensions exceed limit")
                    image.verify()
                with Image.open(BytesIO(data), formats=[expected_format]) as image:
                    total_pixels = 0
                    for index in range(MAX_FRAMES + 1):
                        try:
                            image.seek(index)
                        except EOFError:
                            break
                        pixels = image.width * image.height
                        total_pixels += pixels
                        if index >= MAX_FRAMES or pixels > MAX_PIXELS or total_pixels > MAX_TOTAL_PIXELS:
                            raise ValueError("Decoded image exceeds limit")
                        image.load()
    except (ValueError, LookupError, OSError, SyntaxError, UnidentifiedImageError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise HTTPException(400, "تصویر خراب، ناامن یا بیش از حد بزرگ است؛ فایل معتبر دیگری انتخاب کنید.") from None
    return ext, mime, data


async def read_image(upload: UploadFile, *, allowed_extensions=RASTER_EXTENSIONS, max_bytes: int):
    data = await upload.read(max_bytes + 1)
    return await run_in_threadpool(
        validate_image, data, upload.filename, upload.content_type,
        allowed_extensions=allowed_extensions, max_bytes=max_bytes,
    )
