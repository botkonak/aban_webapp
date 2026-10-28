"""مدیریت محتوای سایت (صفحات، آموزش، منو، رسانه) و سئو، همراه با رندر عمومی Jinja2."""
from __future__ import annotations

import html as html_lib
import json
import logging
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import quote

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from jinja2 import Environment, FileSystemLoader, select_autoescape
from pydantic import BaseModel

import config
from database import db
from webapp.jalali import DATE_TOKENS, format_jalali, format_date_filter, format_date_short, parse_db_ts
from webapp.uploads import IMAGE_FORMATS, read_image

try:
    import bleach

    _ALLOWED_TAGS = [
        "p", "br", "hr", "strong", "b", "em", "i", "u", "s", "del", "ins", "mark", "small",
        "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "pre", "code",
        "ul", "ol", "li", "dl", "dt", "dd",
        "a", "img", "figure", "figcaption",
        "table", "thead", "tbody", "tfoot", "tr", "th", "td", "colgroup", "col",
        "div", "span", "section", "article", "header", "footer", "aside",
        "iframe", "video", "audio", "source",
    ]
    _GLOBAL_ATTRS = ("class", "id", "title", "dir", "lang", "style")
    _DATA_UNSAFE = re.compile(r"(?i)(?:javascript|vbscript)\s*:")

    def _global_attr(tag: str, name: str, value: str) -> bool:
        name = str(name or "").lower()
        if name in _GLOBAL_ATTRS:
            return True
        raw = str(value or "")
        if name.startswith("data-") and len(name) <= 60:
            return len(raw) <= 2000 and _DATA_UNSAFE.search(raw) is None
        return False

    _ALLOWED_ATTRS = {
        "*": _global_attr,
        "a": ["href", "title", "target", "rel"],
        "img": ["src", "alt", "title", "width", "height", "loading"],
        "iframe": ["src", "width", "height", "frameborder", "allow", "allowfullscreen", "title",
                   "loading", "referrerpolicy"],
        "video": ["src", "controls", "width", "height", "poster", "playsinline"],
        "audio": ["src", "controls"],
        "source": ["src", "type"],
        "table": ["border", "cellpadding", "cellspacing"],
        "th": ["colspan", "rowspan", "scope"],
        "td": ["colspan", "rowspan"],
        "col": ["span"],
        "colgroup": ["span"],
    }
    # پروتکل‌های مجاز برای لینک/تصویر/مدیا. «data» عمداً مجاز نیست: توالیب مثل
    # data:image/svg+xml یا data:text/html می‌تواند اسکریپت داخل href/src قاچاق کند.
    _ALLOWED_PROTOCOLS = ["http", "https", "mailto"]
except ImportError:  # pragma: no cover
    bleach = None
    _ALLOWED_TAGS = []
    _ALLOWED_ATTRS = {}
    _ALLOWED_PROTOCOLS = []

_CSS_UNSAFE = re.compile(r"(?i)(url\s*\(|expression\s*\(|javascript|vbscript|behaviou?r|-moz-binding|@|<|>|\\\\)")
_CSS_PROPERTIES = {
    "color", "background-color", "text-align", "text-decoration", "text-transform",
    "font-size", "font-family", "font-weight", "font-style", "line-height", "letter-spacing",
    "width", "height", "max-width", "min-width", "max-height", "min-height", "aspect-ratio",
    "margin", "margin-top", "margin-bottom", "margin-left", "margin-right",
    "padding", "padding-top", "padding-bottom", "padding-left", "padding-right",
    "border", "border-top", "border-bottom", "border-left", "border-right",
    "border-collapse", "border-color", "border-style", "border-width", "border-radius",
    "vertical-align", "white-space", "direction", "list-style", "list-style-type",
    "page-break-before", "page-break-after", "break-before", "break-after", "object-fit",
    # «display» لازَمِ است که شکستِ صفحه‌ی CKEditor (span با display:none) پس از
    # پاک‌سازی همچنان به ویرایشگر برگردد.
    "display", "visibility", "opacity", "float", "clear", "overflow", "background",
}

log = logging.getLogger("webapp.content")

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
PUBLIC_WIDTHS = ("narrow", "normal", "wide", "full")
WIDTH_PX = {"narrow": 720, "normal": 960, "wide": 1200, "full": 0}
STATUSES = ("draft", "published")
ITEM_TYPES = ("page", "post", "category", "custom_link")
FIELD_TYPES = ("text", "email", "password", "tel", "number")
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
MAX_MEDIA_BYTES = 5 * 1024 * 1024


def env() -> Environment:
    e = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(("html", "xml")),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    e.filters["format_date"] = format_date_filter
    e.filters["format_date_short"] = format_date_short
    e.globals["width_px"] = lambda w: WIDTH_PX.get(w, WIDTH_PX["normal"])
    return e


_env: Optional[Environment] = None


def _tpl() -> Environment:
    global _env
    if _env is None:
        _env = env()
    return _env


def render(template: str, **ctx: Any) -> str:
    return _tpl().get_template(template).render(**ctx)


# ----------------------------------------------------------------------------
# ابزارهای عمومی
# ----------------------------------------------------------------------------
def setting(key: str, default: str = "") -> str:
    value = db.get(key)
    return value if value not in (None, "") else str(config.DEFAULT_SETTINGS.get(key, default))


def _clean_css(raw: str) -> str:
    """استایل inline را به فهرست مجاز محدود می‌کند؛ در صورت بی‌خطر نبودن خالی برمی‌گردد."""
    css = html_lib.unescape(str(raw or ""))
    if len(css) > 600 or _CSS_UNSAFE.search(css) or any(x in css for x in ("\\", "/*", "*/")):
        return ""
    parts: list[str] = []
    for decl in css.split(";"):
        prop, sep, value = decl.partition(":")
        prop, value = prop.strip().lower(), value.strip()
        if not sep or prop not in _CSS_PROPERTIES or not value or len(value) > 120:
            continue
        if any(ch in value for ch in ("\"", "'", "<", ">")):
            continue
        parts.append(f"{prop}:{value}")
    return ";".join(parts[:24])


class _InlineCSSSanitizer:
    def sanitize_css(self, style: str) -> str:
        return _clean_css(style)


def sanitize_html(raw: str) -> str:
    text = str(raw or "")
    if bleach is None:
        return html_lib.escape(text)
    return bleach.clean(
        text,
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRS,
        protocols=_ALLOWED_PROTOCOLS,
        css_sanitizer=_InlineCSSSanitizer(),
        strip=True,
        strip_comments=True,
    )


def plain_text(raw: str, limit: int = 160) -> str:
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", str(raw or ""))
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html_lib.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[: max(0, int(limit))]


def _slug(value: str, fallback: str = "item") -> str:
    slug = str(value or "").strip().lower()
    slug = re.sub(r"[^a-z0-9-]+", "-", slug).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)
    return slug or f"{fallback}-{int(time.time())}"


def _unique_slug(table: str, value: str, fallback: str, exclude_id: int = 0) -> str:
    base = _slug(value, fallback)
    slug = base
    n = 1
    while db.q1(f"SELECT id FROM {table} WHERE slug = ? AND id != ?", (slug, int(exclude_id))):
        n += 1
        slug = f"{base}-{n}"
    return slug


def _width(value: Optional[str], *, kind: str = "page") -> str:
    value = (value or "").strip().lower()
    if value in PUBLIC_WIDTHS:
        return value
    default = setting("default_content_width", "normal")
    return default if default in PUBLIC_WIDTHS else "normal"


def _sidebar(value: Any, *, kind: str = "page") -> int:
    if value is None or value == "":
        default = setting("sidebar_posts" if kind == "post" else "sidebar_pages", "0")
        return 1 if default == "1" else 0
    return 1 if bool(value) else 0


def _bounded_int(value: Any, low: int, high: int, default: int) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(n, high))


def _published(value: str) -> str:
    return "published" if str(value or "").strip().lower() == "published" else "draft"


def _robots(value: Optional[str]) -> str:
    value = str(value or "").strip().lower()
    if value:
        return value[:120]
    return setting("seo_robots_default", "index,follow") or "index,follow"


def canonical_base() -> str:
    base = setting("seo_canonical_base", "") or config.WEBAPP_URL or ""
    if not base:
        host = config.WEBAPP_PUBLIC_HOST or ""
        base = "https://" + host if host else ""
    return base.rstrip("/")


def public_url(path: str) -> str:
    path = "/" + str(path or "").lstrip("/")
    base = canonical_base()
    return base + path if base else path


def _og_image(path: str) -> str:
    path = str(path or "").strip()
    if not path:
        return ""
    if path.startswith(("http://", "https://")):
        return path
    return public_url(path)


def _meta(
    *, title: str, description: str = "", canonical: str = "", robots: str = "",
    og_title: str = "", og_description: str = "", og_image: str = "", extra: str = "",
) -> Dict[str, Any]:
    return {
        "title": title,
        "description": description,
        "canonical": canonical or public_url("/"),
        "robots": _robots(robots),
        "og_title": og_title or title,
        "og_description": og_description or description,
        "og_image": _og_image(og_image or setting("seo_og_image", "")),
        "extra": extra,
    }


def meta_tags(meta: Dict[str, Any]) -> str:
    e = html_lib.escape
    parts = [
        f'<meta name="description" content="{e(meta.get("description") or "")}">',
        f'<meta name="robots" content="{e(meta.get("robots") or "index,follow")}">',
        f'<link rel="canonical" href="{e(meta.get("canonical") or "/")}">',
        f'<meta property="og:title" content="{e(meta.get("og_title") or meta.get("title") or "")}">',
        f'<meta property="og:description" content="{e(meta.get("og_description") or "")}">',
        f'<meta property="og:type" content="website">',
        f'<meta property="og:url" content="{e(meta.get("canonical") or "/")}">',
        f'<meta name="twitter:card" content="summary_large_image">',
    ]
    if meta.get("og_image"):
        parts.append(f'<meta property="og:image" content="{e(meta["og_image"])}">')
        parts.append(f'<meta name="twitter:image" content="{e(meta["og_image"])}">')
    verify = setting("google_site_verification", "")
    if verify:
        parts.append(f'<meta name="google-site-verification" content="{e(verify)}">')
    if meta.get("extra"):
        parts.append(str(meta["extra"]))
    return "\n  ".join(parts)


def site_meta(path: str = "/") -> Dict[str, Any]:
    title = setting("seo_meta_title", "") or setting("site_title", config.WEBAPP_TITLE)
    description = setting("seo_meta_description", "") or setting("site_slogan", "")
    return _meta(
        title=title,
        description=description,
        canonical=public_url(path),
        robots=setting("seo_robots_default", "index,follow"),
        og_title=title,
        og_description=description,
        og_image=setting("seo_og_image", ""),
    )


def inject_spa_head(document: str, meta: Dict[str, Any]) -> str:
    doc = str(document or "")
    tags = meta_tags(meta)
    doc = re.sub(r"(?is)<title>.*?</title>", "<title>" + html_lib.escape(meta.get("title") or "") + "</title>", doc, count=1)
    if "</head>" in doc:
        doc = doc.replace("</head>", "  " + tags + "\n</head>", 1)
    return doc


# ----------------------------------------------------------------------------
# ردیف‌ها و دسترسی عمومی
# ----------------------------------------------------------------------------
def page_row(row: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(row)
    out["is_system"] = bool(out.get("is_system"))
    out["has_sidebar"] = bool(out.get("has_sidebar"))
    out["created_at_fa"] = format_jalali(out.get("created_at"))
    out["updated_at_fa"] = format_jalali(out.get("updated_at") or out.get("created_at"))
    return out


def post_row(row: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(row)
    out["has_sidebar"] = bool(out.get("has_sidebar"))
    out["published_at_fa"] = format_jalali(out.get("published_at")) if out.get("published_at") else "-"
    out["created_at_fa"] = format_jalali(out.get("created_at"))
    out["updated_at_fa"] = format_jalali(out.get("updated_at") or out.get("created_at"))
    out["category_name"] = out.get("category_name") or ""
    return out


def get_page(slug: str, *, published_only: bool = True) -> Optional[Dict[str, Any]]:
    cond = " AND status = 'published'" if published_only else ""
    return db.q1("SELECT * FROM pages WHERE slug = ?" + cond, (str(slug or "").strip().lower(),))


def get_post(slug: str, *, published_only: bool = True, increment: bool = False) -> Optional[Dict[str, Any]]:
    cond = " AND p.status = 'published'" if published_only else ""
    row = db.q1(
        "SELECT p.*, c.name AS category_name, c.slug AS category_slug FROM posts p "
        "LEFT JOIN categories c ON c.id = p.category_id WHERE p.slug = ?" + cond,
        (str(slug or "").strip().lower(),),
    )
    if row and increment and published_only:
        db.x("UPDATE posts SET views = COALESCE(views, 0) + 1 WHERE id = ?", (int(row["id"]),))
        row["views"] = int(row.get("views") or 0) + 1
    return row


def categories(*, with_counts: bool = False) -> List[Dict[str, Any]]:
    if with_counts:
        return db.q(
            "SELECT c.*, COUNT(p.id) AS posts FROM categories c LEFT JOIN posts p "
            "ON p.category_id = c.id AND p.status = 'published' GROUP BY c.id, c.name, c.slug ORDER BY c.name"
        )
    return db.q("SELECT * FROM categories ORDER BY name")


def _safe_menu_url(raw: str) -> str:
    """فقط آدرس‌های http/https/mailto/tel و مسیر داخلی سایت مجازند."""
    url = str(raw or "").strip()
    if not url:
        return ""
    low = url.lower()
    if low.startswith(("http://", "https://", "mailto:", "tel:")):
        return url
    if url.startswith("/") and not url.startswith("//") and "\\" not in url:
        return url
    return ""


def _menu_refs() -> Tuple[Dict[int, Dict[str, Any]], Dict[int, Dict[str, Any]], Dict[int, Dict[str, Any]]]:
    pages_ = {int(r["id"]): r for r in db.q("SELECT id, title, slug, status FROM pages")}
    posts_ = {int(r["id"]): r for r in db.q("SELECT id, title, slug, status FROM posts")}
    cats_ = {int(r["id"]): r for r in db.q("SELECT id, name, slug FROM categories")}
    return pages_, posts_, cats_


def menu_tree(location: str, *, active_only: bool = True, published_only: bool = True) -> List[Dict[str, Any]]:
    menu = db.q1("SELECT * FROM menus WHERE location = ?", (location,))
    if menu is None:
        return []
    items = [
        dict(r) for r in db.q(
            "SELECT * FROM menu_items WHERE menu_id = ? ORDER BY sort_order, id",
            (int(menu["id"]),),
        )
    ]
    pages_, posts_, cats_ = _menu_refs()
    nodes: Dict[int, Dict[str, Any]] = {}
    for item in items:
        if active_only and not int(item.get("is_active") or 0):
            continue
        typ, ref = item.get("type"), int(item.get("reference_id") or 0)
        url, resolved = "", True
        if typ == "page":
            page = pages_.get(ref)
            resolved = page is not None and (not published_only or page.get("status") == "published")
            url = f"/p/{page['slug']}" if page else ""
            item["title"] = item.get("title") or (page or {}).get("title", "")
        elif typ == "post":
            post = posts_.get(ref)
            resolved = post is not None and (not published_only or post.get("status") == "published")
            url = f"/education/{post['slug']}" if post else ""
            item["title"] = item.get("title") or (post or {}).get("title", "")
        elif typ == "category":
            cat = cats_.get(ref)
            resolved = cat is not None
            url = f"/category/{cat['slug']}" if cat else ""
            item["title"] = item.get("title") or (cat or {}).get("name", "")
        else:
            url = _safe_menu_url(item.get("url") or "")
            resolved = bool(url)
        if not resolved:
            continue
        item["href"] = url
        item["children"] = []
        item["_parent"] = int(item.get("parent_id") or 0)
        nodes[int(item["id"])] = item
    roots: List[Dict[str, Any]] = []
    for node in nodes.values():
        parent = nodes.get(node["_parent"])
        if parent is not None and parent is not node:
            parent["children"].append(node)
        else:
            roots.append(node)
    return roots


def _admin_menu(menu_id: int) -> Dict[str, Any]:
    menu = db.q1("SELECT * FROM menus WHERE id = ?", (int(menu_id),))
    if menu is None:
        raise HTTPException(404, "منو پیدا نشد.")
    pages_, posts_, cats_ = _menu_refs()
    rows = [dict(r) for r in db.q(
        "SELECT * FROM menu_items WHERE menu_id = ? ORDER BY sort_order, id", (int(menu_id),)
    )]
    for row in rows:
        row["is_active"] = bool(row.get("is_active"))
        if row["type"] == "page":
            row["resolved_title"] = (pages_.get(int(row.get("reference_id") or 0)) or {}).get("title", "")
            row["href"] = f"/p/{(pages_.get(int(row.get('reference_id') or 0)) or {}).get('slug', '')}"
        elif row["type"] == "post":
            row["resolved_title"] = (posts_.get(int(row.get("reference_id") or 0)) or {}).get("title", "")
            row["href"] = f"/education/{(posts_.get(int(row.get('reference_id') or 0)) or {}).get('slug', '')}"
        elif row["type"] == "category":
            row["resolved_title"] = (cats_.get(int(row.get("reference_id") or 0)) or {}).get("name", "")
            row["href"] = f"/category/{(cats_.get(int(row.get('reference_id') or 0)) or {}).get('slug', '')}"
        else:
            row["resolved_title"] = row.get("url") or ""
            row["href"] = row.get("url") or ""
    return {**dict(menu), "items": rows}


def _sidebar_context() -> Dict[str, Any]:
    return {
        "categories": categories(with_counts=True),
        "pages": db.q("SELECT title, slug FROM pages WHERE status = 'published' ORDER BY title LIMIT 8"),
        "recent": [
            post_row(r) for r in db.q(
                "SELECT * FROM posts WHERE status = 'published' ORDER BY COALESCE(published_at, created_at) DESC LIMIT 6"
            )
        ],
    }


def _site_context() -> Dict[str, Any]:
    brand = {
        "name": setting("site_name", "آبان ترید"),
        "slogan": setting("site_slogan", ""),
        "title": setting("site_title", config.WEBAPP_TITLE),
        "logo": setting("site_logo", ""),
    }
    return {
        "brand": brand,
        "maintenance": {"on": db.get_bool("maint_on"), "text": setting("maint_text", "")},
        "header_menu": menu_tree("header"),
        "footer_menu": menu_tree("footer"),
        "sidebar_on": setting("sidebar_enabled", "0") == "1",
        "sidebar_position": setting("sidebar_position", "right") if setting("sidebar_position", "right") in ("right", "left") else "right",
        "sidebar_ctx": _sidebar_context(),
        "bot_url": (f"https://t.me/{config.BOT_USERNAME.lstrip('@')}" if config.BOT_USERNAME else ""),
        "year": format_jalali(time.time(), "jalali_numeric")[:4],
    }


def _render_public(template: str, *, title: str, meta: Dict[str, Any], content_width: str, sidebar: bool, **ctx: Any):
    site = _site_context()
    show_sidebar = bool(site["sidebar_on"] and sidebar)
    return render(
        template,
        site=site,
        brand=site["brand"],
        sidebar_ctx=site["sidebar_ctx"],
        meta=meta,
        meta_html=meta_tags(meta),
        content_width=_width(content_width),
        width_px=WIDTH_PX.get(_width(content_width), 960),
        show_sidebar=show_sidebar,
        **ctx,
    )


def page_meta(page: Dict[str, Any]) -> Dict[str, Any]:
    title = (page.get("meta_title") or page.get("title") or "").strip()
    description = (page.get("meta_description") or plain_text(page.get("content") or "", 160)).strip()
    return _meta(
        title=(title + " | " + setting("site_name", "")) if title else site_meta()["title"],
        description=description,
        canonical=(page.get("canonical_url") or public_url(f"/p/{page['slug']}")).strip(),
        robots=page.get("robots") or "",
        og_title=page.get("og_title") or title,
        og_description=page.get("og_description") or description,
        og_image=page.get("og_image") or "",
    )


def post_meta(post: Dict[str, Any]) -> Dict[str, Any]:
    title = (post.get("meta_title") or post.get("title") or "").strip()
    description = (post.get("meta_description") or post.get("summary") or plain_text(post.get("content") or "", 160)).strip()
    return _meta(
        title=(title + " | " + setting("site_name", "")) if title else site_meta()["title"],
        description=description,
        canonical=(post.get("canonical_url") or public_url(f"/education/{post['slug']}")).strip(),
        robots=post.get("robots") or "",
        og_title=post.get("og_title") or title,
        og_description=post.get("og_description") or description,
        og_image=post.get("og_image") or post.get("thumbnail") or "",
        extra='<meta property="og:type" content="article">',
    )


# ----------------------------------------------------------------------------
# رندر صفحات عمومی
# ----------------------------------------------------------------------------
def render_page(slug: str) -> Optional[str]:
    page = get_page(slug)
    if page is None:
        return None
    page = page_row(page)
    meta = page_meta(page)
    return _render_public(
        "page.html", title=page["title"], meta=meta,
        content_width=page.get("content_width") or "",
        sidebar=bool(page.get("has_sidebar")), page=page,
    )


def render_post(slug: str) -> Optional[str]:
    post = get_post(slug, increment=True)
    if post is None:
        return None
    post = post_row(post)
    meta = post_meta(post)
    return _render_public(
        "post.html", title=post["title"], meta=meta,
        content_width=post.get("content_width") or "",
        sidebar=bool(post.get("has_sidebar")), post=post,
    )


def render_education(category_slug: str = "", page: int = 1) -> str:
    page = max(1, int(page or 1))
    size = 9
    where, params = ["p.status = 'published'"], []
    category = None
    if category_slug:
        category = db.q1("SELECT * FROM categories WHERE slug = ?", (category_slug,))
        if category is None:
            raise HTTPException(404, "دسته‌بندی پیدا نشد.")
        where.append("p.category_id = ?")
        params.append(int(category["id"]))
    total = int(db.q1("SELECT COUNT(*) AS n FROM posts p WHERE " + " AND ".join(where), params)["n"] or 0)
    rows = db.q(
        "SELECT p.*, c.name AS category_name, c.slug AS category_slug FROM posts p "
        "LEFT JOIN categories c ON c.id = p.category_id WHERE " + " AND ".join(where) +
        " ORDER BY COALESCE(p.published_at, p.created_at) DESC LIMIT ? OFFSET ?",
        params + [size, (page - 1) * size],
    )
    posts = [post_row(r) for r in rows]
    title = (category["name"] if category else "آموزش") + " | " + setting("site_name", "")
    meta = _meta(
        title=title,
        description=setting("seo_meta_description", "") or setting("site_slogan", ""),
        canonical=public_url((f"/category/{category['slug']}" if category else "/education") + (f"?page={page}" if page > 1 else "")),
        robots=setting("seo_robots_default", "index,follow"),
    )
    pages_total = max(1, (total + size - 1) // size)
    return _render_public(
        "education.html", title=category["name"] if category else "آموزش", meta=meta,
        content_width=setting("default_content_width", "normal"),
        sidebar=setting("sidebar_posts", "1") == "1",
        posts=posts, category=category, page=page, pages_total=pages_total, total=total,
    )


def render_sitemap() -> str:
    base = canonical_base()
    rows: List[Tuple[str, str]] = [("/", datetime.now(timezone.utc).strftime("%Y-%m-%d"))]
    for page in db.q("SELECT slug, updated_at, created_at, robots FROM pages WHERE status = 'published'"):
        if "noindex" in _robots(page.get("robots")):
            continue
        stamp = page.get("updated_at") or page.get("created_at")
        rows.append((f"/p/{page['slug']}", _iso_date(stamp)))
    for post in db.q(
        "SELECT slug, updated_at, created_at, published_at, robots FROM posts WHERE status = 'published'"
    ):
        if "noindex" in _robots(post.get("robots")):
            continue
        rows.append((f"/education/{post['slug']}", _iso_date(post.get("updated_at") or post.get("published_at") or post.get("created_at"))))
    rows.append(("/education", datetime.now(timezone.utc).strftime("%Y-%m-%d")))
    body = ['<?xml version="1.0" encoding="UTF-8"?>', '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for path, stamp in rows:
        body.append("  <url>")
        body.append(f"    <loc>{html_lib.escape(base + path)}</loc>")
        if stamp:
            body.append(f"    <lastmod>{stamp}</lastmod>")
        body.append("  </url>")
    body.append("</urlset>")
    return "\n".join(body)


def _iso_date(value: Any) -> str:
    ts = parse_db_ts(value)
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d") if ts else ""


def robots_text() -> str:
    custom = setting("robots_txt", "").strip()
    if custom:
        return custom
    base = canonical_base()
    lines = [
        "User-agent: *",
        "Allow: /",
        "Disallow: /admin",
        "Disallow: /api/",
        "Disallow: /login",
        "Disallow: /register",
        "Disallow: /uploads/",
    ]
    if base:
        lines.append(f"Sitemap: {base}/sitemap.xml")
    return "\n".join(lines) + "\n"


# ----------------------------------------------------------------------------
# Seed پیش‌فرض محتوا
# ----------------------------------------------------------------------------
SYSTEM_PAGES = (
    ("حریم خصوصی", "privacy", "content_privacy"),
    ("تماس با ما", "contact", "content_contact"),
    ("درباره ما", "about", "content_about"),
)


def ensure_defaults() -> None:
    for name, slug, key in SYSTEM_PAGES:
        db.x(
            "INSERT INTO pages(title, slug, content, status, is_system, has_sidebar, content_width) "
            "VALUES(?, ?, ?, 'published', 1, 0, 'normal') ON CONFLICT(slug) DO NOTHING",
            (name, slug, str(config.DEFAULT_SETTINGS.get(key, "") or f"<p>{name}</p>")),
        )
    if int(db.q1("SELECT COUNT(*) AS n FROM categories")["n"] or 0) == 0:
        for name, slug in (("آموزش مقدماتی", "basics"), ("تحلیل ساختاری", "structural")):
            db.x("INSERT INTO categories(name, slug) VALUES(?, ?) ON CONFLICT(slug) DO NOTHING", (name, slug))
    for loc, name in (("header", "منوی اصلی"), ("footer", "منوی پابرگ")):
        db.x("INSERT INTO menus(name, location) VALUES(?, ?) ON CONFLICT(location) DO NOTHING", (name, loc))
    header = db.q1("SELECT id FROM menus WHERE location = 'header'")
    if header and int(db.q1("SELECT COUNT(*) AS n FROM menu_items WHERE menu_id = ?", (int(header["id"]),))["n"] or 0) == 0:
        home = db.q1("SELECT id FROM pages WHERE slug = 'about'")
        contact = db.q1("SELECT id FROM pages WHERE slug = 'contact'")
        rows = [("داشبورد", "custom_link", 0, "/", 0)]
        if home:
            rows.append(("درباره ما", "page", int(home["id"]), "", 1))
        rows.append(("آموزش", "custom_link", 0, "/education", 2))
        if contact:
            rows.append(("تماس با ما", "page", int(contact["id"]), "", 3))
        for title, typ, ref, url, order in rows:
            db.x(
                "INSERT INTO menu_items(menu_id, title, type, reference_id, url, sort_order, is_active) "
                "VALUES(?, ?, ?, ?, ?, ?, 1)",
                (int(header["id"]), title, typ, ref, url, order),
            )
    footer = db.q1("SELECT id FROM menus WHERE location = 'footer'")
    if footer and int(db.q1("SELECT COUNT(*) AS n FROM menu_items WHERE menu_id = ?", (int(footer["id"]),))["n"] or 0) == 0:
        for order, (title, slug) in enumerate((("حریم خصوصی", "privacy"), ("تماس با ما", "contact"), ("درباره ما", "about"))):
            page = db.q1("SELECT id FROM pages WHERE slug = ?", (slug,))
            if page:
                db.x(
                    "INSERT INTO menu_items(menu_id, title, type, reference_id, sort_order, is_active) "
                    "VALUES(?, ?, 'page', ?, ?, 1)",
                    (int(footer["id"]), title, int(page["id"]), order),
                )
    # فیلدهای ثبت‌نام: username/password همیشه فعال و الزامی
    defaults = (
        ("username", "نام کاربری", "text", 1, 1, 10),
        ("password", "رمز عبور", "password", 1, 1, 20),
        ("email", "ایمیل", "email", 0, 0, 30),
        ("phone", "شماره موبایل", "tel", 0, 0, 40),
        ("first_name", "نام", "text", 0, 0, 50),
        ("last_name", "نام خانوادگی", "text", 0, 0, 60),
    )
    for key, label, ftype, required, active, order in defaults:
        db.x(
            "INSERT INTO registration_fields(field_key, label, field_type, is_required, is_active, sort_order) "
            "VALUES(?, ?, ?, ?, ?, ?) ON CONFLICT(field_key) DO NOTHING",
            (key, label, ftype, required, active, order),
        )
    db.x("UPDATE registration_fields SET is_active = 1, is_required = 1 WHERE field_key IN ('username', 'password')")


# ----------------------------------------------------------------------------
# مدل‌های ورودی
# ----------------------------------------------------------------------------
class PageBody(BaseModel):
    title: str = ""
    slug: str = ""
    content: str = ""
    status: str = "draft"
    has_sidebar: Optional[bool] = None
    content_width: Optional[str] = ""
    meta_title: str = ""
    meta_description: str = ""
    canonical_url: str = ""
    robots: str = ""
    og_title: str = ""
    og_description: str = ""
    og_image: str = ""


class PostBody(PageBody):
    summary: str = ""
    thumbnail: str = ""
    category_id: int = 0
    published_at: Optional[str] = None
    reading_time: int = 0


class CategoryBody(BaseModel):
    name: str = ""
    slug: str = ""


class MenuBody(BaseModel):
    name: str = ""
    location: str = "header"


class MenuItemBody(BaseModel):
    menu_id: int
    title: str = ""
    type: str = "custom_link"
    reference_id: int = 0
    url: str = ""
    parent_id: int = 0
    is_active: bool = True


class ReorderItem(BaseModel):
    id: int
    parent_id: int = 0
    sort_order: int = 0


class ReorderBody(BaseModel):
    items: List[ReorderItem] = []


class AppearanceBody(BaseModel):
    default_content_width: Optional[str] = None
    sidebar_enabled: Optional[bool] = None
    sidebar_position: Optional[str] = None
    sidebar_pages: Optional[bool] = None
    sidebar_posts: Optional[bool] = None
    date_format: Optional[str] = None
    date_format_custom: Optional[str] = None


class SeoBody(BaseModel):
    seo_meta_title: Optional[str] = None
    seo_meta_description: Optional[str] = None
    seo_og_image: Optional[str] = None
    seo_robots_default: Optional[str] = None
    seo_canonical_base: Optional[str] = None
    google_site_verification: Optional[str] = None
    robots_txt: Optional[str] = None
    tg_bot_username: Optional[str] = None
    tg_bot_name_guide: Optional[str] = None
    tg_bot_about_guide: Optional[str] = None
    tg_bot_description_guide: Optional[str] = None
    tg_share_app_name: Optional[str] = None


class MediaMetaBody(BaseModel):
    alt_text: Optional[str] = None
    title: Optional[str] = None


# ----------------------------------------------------------------------------
# ثبت مسیرها
# ----------------------------------------------------------------------------
def register(
    app: FastAPI,
    *,
    require_admin: Callable[[Request], Dict[str, Any]],
    upload_root: Path,
    web_dir: Path,
) -> None:
    media_root = Path(upload_root) / "media"
    media_root.mkdir(parents=True, exist_ok=True)
    _require_admin = require_admin

    # ---------------------------- صفحات ثابت ----------------------------
    @app.get("/api/admin/pages")
    async def admin_pages(request: Request, q: str = ""):
        _require_admin(request)
        where, params = "", []
        if q.strip():
            where = " WHERE LOWER(title) LIKE ? OR LOWER(slug) LIKE ?"
            like = "%" + q.strip().lower() + "%"
            params = [like, like]
        rows = [page_row(r) for r in db.q("SELECT * FROM pages" + where + " ORDER BY is_system DESC, id DESC", params)]
        return {"items": rows, "total": len(rows), "widths": list(PUBLIC_WIDTHS)}

    @app.get("/api/admin/pages/{pid}")
    async def admin_page(pid: int, request: Request):
        _require_admin(request)
        row = db.q1("SELECT * FROM pages WHERE id = ?", (int(pid),))
        if row is None:
            raise HTTPException(404, "صفحه پیدا نشد.")
        return {"item": page_row(row)}

    def _page_values(body: PageBody, current: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        current = current or {}
        title = (body.title or current.get("title") or "").strip()[:200]
        if not title:
            raise HTTPException(400, "عنوان صفحه را وارد کنید.")
        slug = (body.slug or current.get("slug") or "").strip().lower()
        if not slug:
            slug = _unique_slug("pages", title, "page", int(current.get("id") or 0))
        if not SLUG_RE.match(slug):
            raise HTTPException(400, "نامک صفحه فقط می‌تواند شامل حروف کوچک انگلیسی، عدد و خط تیره باشد.")
        slug = _unique_slug("pages", slug, "page", int(current.get("id") or 0))
        values = {
            "title": title,
            "slug": slug,
            "content": sanitize_html(body.content if body.content is not None else current.get("content") or ""),
            "status": _published(body.status),
            "has_sidebar": _sidebar(body.has_sidebar if body.has_sidebar is not None else current.get("has_sidebar"), kind="page"),
            "content_width": _width(body.content_width if body.content_width is not None else current.get("content_width"), kind="page"),
            "meta_title": (body.meta_title or "").strip()[:200],
            "meta_description": (body.meta_description or "").strip()[:400],
            "canonical_url": (body.canonical_url or "").strip()[:400],
            "robots": (body.robots or "").strip()[:120],
            "og_title": (body.og_title or "").strip()[:200],
            "og_description": (body.og_description or "").strip()[:400],
            "og_image": (body.og_image or "").strip()[:400],
        }
        return values

    @app.post("/api/admin/pages")
    async def admin_page_create(body: PageBody, request: Request):
        _require_admin(request)
        values = _page_values(body)
        pid = db.insert_id(
            "INSERT INTO pages(title, slug, content, status, is_system, has_sidebar, content_width, meta_title, "
            "meta_description, canonical_url, robots, og_title, og_description, og_image, updated_at) "
            "VALUES(?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)",
            (
                values["title"], values["slug"], values["content"], values["status"], values["has_sidebar"],
                values["content_width"], values["meta_title"], values["meta_description"], values["canonical_url"],
                values["robots"], values["og_title"], values["og_description"], values["og_image"],
            ),
        )
        return {"ok": True, "id": pid, "message": "صفحه ساخته شد.", "item": page_row(db.q1("SELECT * FROM pages WHERE id = ?", (pid,)))}

    @app.put("/api/admin/pages/{pid}")
    async def admin_page_update(pid: int, body: PageBody, request: Request):
        _require_admin(request)
        current = db.q1("SELECT * FROM pages WHERE id = ?", (int(pid),))
        if current is None:
            raise HTTPException(404, "صفحه پیدا نشد.")
        values = _page_values(body, current)
        db.x(
            "UPDATE pages SET title = ?, slug = ?, content = ?, status = ?, has_sidebar = ?, content_width = ?, "
            "meta_title = ?, meta_description = ?, canonical_url = ?, robots = ?, og_title = ?, og_description = ?, "
            "og_image = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (
                values["title"], values["slug"], values["content"], values["status"], values["has_sidebar"],
                values["content_width"], values["meta_title"], values["meta_description"], values["canonical_url"],
                values["robots"], values["og_title"], values["og_description"], values["og_image"], int(pid),
            ),
        )
        return {"ok": True, "message": "صفحه ذخیره شد.", "item": page_row(db.q1("SELECT * FROM pages WHERE id = ?", (int(pid),)))}

    @app.delete("/api/admin/pages/{pid}")
    async def admin_page_delete(pid: int, request: Request):
        _require_admin(request)
        row = db.q1("SELECT * FROM pages WHERE id = ?", (int(pid),))
        if row is None:
            raise HTTPException(404, "صفحه پیدا نشد.")
        if int(row.get("is_system") or 0):
            raise HTTPException(400, "صفحات سیستمی قابل حذف نیستند.")
        db.x("DELETE FROM pages WHERE id = ?", (int(pid),))
        return {"ok": True, "message": "صفحه حذف شد."}

    # ---------------------------- آموزش (پست‌ها) ----------------------------
    @app.get("/api/admin/categories")
    async def admin_categories(request: Request):
        _require_admin(request)
        return {"items": categories(with_counts=True)}

    def _category_values(body: CategoryBody, current: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        current = current or {}
        name = (body.name or current.get("name") or "").strip()[:120]
        if not name:
            raise HTTPException(400, "نام دسته‌بندی را وارد کنید.")
        slug = _unique_slug("categories", body.slug or current.get("slug") or name, "cat", int(current.get("id") or 0))
        return {"name": name, "slug": slug}

    @app.post("/api/admin/categories")
    async def admin_category_create(body: CategoryBody, request: Request):
        _require_admin(request)
        values = _category_values(body)
        cid = db.insert_id("INSERT INTO categories(name, slug) VALUES(?, ?)", (values["name"], values["slug"]))
        return {"ok": True, "id": cid, "message": "دسته‌بندی ساخته شد."}

    @app.put("/api/admin/categories/{cid}")
    async def admin_category_update(cid: int, body: CategoryBody, request: Request):
        _require_admin(request)
        current = db.q1("SELECT * FROM categories WHERE id = ?", (int(cid),))
        if current is None:
            raise HTTPException(404, "دسته‌بندی پیدا نشد.")
        values = _category_values(body, current)
        db.x("UPDATE categories SET name = ?, slug = ? WHERE id = ?", (values["name"], values["slug"], int(cid)))
        return {"ok": True, "message": "دسته‌بندی ذخیره شد."}

    @app.delete("/api/admin/categories/{cid}")
    async def admin_category_delete(cid: int, request: Request):
        _require_admin(request)
        db.x("UPDATE posts SET category_id = 0 WHERE category_id = ?", (int(cid),))
        db.x("DELETE FROM categories WHERE id = ?", (int(cid),))
        return {"ok": True, "message": "دسته‌بندی حذف شد."}

    @app.get("/api/admin/posts")
    async def admin_posts(request: Request, q: str = "", status: str = "all", category: int = 0, page: int = 1, page_size: int = 20):
        _require_admin(request)
        page = max(1, int(page))
        page_size = max(5, min(int(page_size), 100))
        where, params = [], []
        if q.strip():
            where.append("(LOWER(p.title) LIKE ? OR LOWER(p.slug) LIKE ?)")
            like = "%" + q.strip().lower() + "%"
            params += [like, like]
        if status in STATUSES:
            where.append("p.status = ?")
            params.append(status)
        if int(category or 0):
            where.append("p.category_id = ?")
            params.append(int(category))
        cond = (" WHERE " + " AND ".join(where)) if where else ""
        total = int(db.q1("SELECT COUNT(*) AS n FROM posts p" + cond, params)["n"] or 0)
        rows = db.q(
            "SELECT p.*, c.name AS category_name FROM posts p LEFT JOIN categories c ON c.id = p.category_id" + cond +
            " ORDER BY COALESCE(p.published_at, p.created_at) DESC, p.id DESC LIMIT ? OFFSET ?",
            params + [page_size, (page - 1) * page_size],
        )
        return {"items": [post_row(r) for r in rows], "total": total, "page": page, "page_size": page_size,
                "categories": categories(with_counts=True), "widths": list(PUBLIC_WIDTHS)}

    @app.get("/api/admin/posts/{pid}")
    async def admin_post(pid: int, request: Request):
        _require_admin(request)
        row = get_post_by_id(int(pid))
        if row is None:
            raise HTTPException(404, "مقاله پیدا نشد.")
        return {"item": post_row(row)}

    def _post_values(body: PostBody, current: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        current = current or {}
        title = (body.title or current.get("title") or "").strip()[:200]
        if not title:
            raise HTTPException(400, "عنوان مقاله را وارد کنید.")
        slug = _unique_slug("posts", body.slug or current.get("slug") or title, "post", int(current.get("id") or 0))
        category_id = int(body.category_id if body.category_id is not None else current.get("category_id") or 0)
        if category_id and not db.q1("SELECT id FROM categories WHERE id = ?", (category_id,)):
            category_id = 0
        published_at = body.published_at if body.published_at is not None else current.get("published_at")
        stamp = _parse_publish(published_at)
        content = sanitize_html(body.content if body.content is not None else current.get("content") or "")
        reading = int(body.reading_time or current.get("reading_time") or 0)
        if reading <= 0:
            words = len(plain_text(content, 100000).split())
            reading = max(1, (words + 199) // 200)
        return {
            "title": title,
            "slug": slug,
            "summary": sanitize_html(body.summary if body.summary is not None else current.get("summary") or "")[:300],
            "content": content,
            "thumbnail": (body.thumbnail or "").strip()[:400],
            "category_id": category_id,
            "status": _published(body.status),
            "published_at": stamp,
            "reading_time": max(1, min(reading, 999)),
            "has_sidebar": _sidebar(body.has_sidebar if body.has_sidebar is not None else current.get("has_sidebar"), kind="post"),
            "content_width": _width(body.content_width if body.content_width is not None else current.get("content_width"), kind="post"),
            "meta_title": (body.meta_title or "").strip()[:200],
            "meta_description": (body.meta_description or "").strip()[:400],
            "canonical_url": (body.canonical_url or "").strip()[:400],
            "robots": (body.robots or "").strip()[:120],
            "og_title": (body.og_title or "").strip()[:200],
            "og_description": (body.og_description or "").strip()[:400],
            "og_image": (body.og_image or "").strip()[:400],
        }

    @app.post("/api/admin/posts")
    async def admin_post_create(body: PostBody, request: Request):
        _require_admin(request)
        values = _post_values(body)
        if values["status"] == "published" and not values["published_at"]:
            values["published_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        pid = db.insert_id(
            "INSERT INTO posts(title, slug, summary, content, thumbnail, category_id, status, published_at, "
            "reading_time, has_sidebar, content_width, meta_title, meta_description, canonical_url, robots, "
            "og_title, og_description, og_image, updated_at) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)",
            (
                values["title"], values["slug"], values["summary"], values["content"], values["thumbnail"],
                values["category_id"], values["status"], values["published_at"], values["reading_time"],
                values["has_sidebar"], values["content_width"], values["meta_title"], values["meta_description"],
                values["canonical_url"], values["robots"], values["og_title"], values["og_description"], values["og_image"],
            ),
        )
        return {"ok": True, "id": pid, "message": "مقاله ساخته شد.", "item": post_row(get_post_by_id(pid))}

    @app.put("/api/admin/posts/{pid}")
    async def admin_post_update(pid: int, body: PostBody, request: Request):
        _require_admin(request)
        current = get_post_by_id(int(pid))
        if current is None:
            raise HTTPException(404, "مقاله پیدا نشد.")
        values = _post_values(body, current)
        if values["status"] == "published" and not values["published_at"]:
            values["published_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        db.x(
            "UPDATE posts SET title = ?, slug = ?, summary = ?, content = ?, thumbnail = ?, category_id = ?, "
            "status = ?, published_at = ?, reading_time = ?, has_sidebar = ?, content_width = ?, meta_title = ?, "
            "meta_description = ?, canonical_url = ?, robots = ?, og_title = ?, og_description = ?, og_image = ?, "
            "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (
                values["title"], values["slug"], values["summary"], values["content"], values["thumbnail"],
                values["category_id"], values["status"], values["published_at"], values["reading_time"],
                values["has_sidebar"], values["content_width"], values["meta_title"], values["meta_description"],
                values["canonical_url"], values["robots"], values["og_title"], values["og_description"],
                values["og_image"], int(pid),
            ),
        )
        return {"ok": True, "message": "مقاله ذخیره شد.", "item": post_row(get_post_by_id(int(pid)))}

    @app.delete("/api/admin/posts/{pid}")
    async def admin_post_delete(pid: int, request: Request):
        _require_admin(request)
        db.x("DELETE FROM posts WHERE id = ?", (int(pid),))
        return {"ok": True, "message": "مقاله حذف شد."}

    # ---------------------------- منو ----------------------------
    @app.get("/api/admin/menus")
    async def admin_menus(request: Request):
        _require_admin(request)
        menus = db.q("SELECT * FROM menus ORDER BY id")
        return {"items": menus, "locations": [{"key": "header", "label": "هدر"}, {"key": "footer", "label": "پابرگ"}]}

    @app.get("/api/admin/menus/{mid}")
    async def admin_menu(mid: int, request: Request):
        _require_admin(request)
        return {"item": _admin_menu(int(mid))}

    @app.post("/api/admin/menus")
    async def admin_menu_create(body: MenuBody, request: Request):
        _require_admin(request)
        name = (body.name or "").strip()[:120] or "منوی جدید"
        location = body.location if body.location in ("header", "footer") else "header"
        if db.q1("SELECT id FROM menus WHERE location = ?", (location,)):
            raise HTTPException(409, "برای این مکان قبلاً منو ساخته شده است.")
        mid = db.insert_id("INSERT INTO menus(name, location) VALUES(?, ?)", (name, location))
        return {"ok": True, "id": mid, "message": "منو ساخته شد."}

    @app.put("/api/admin/menus/{mid}")
    async def admin_menu_update(mid: int, body: MenuBody, request: Request):
        _require_admin(request)
        current = db.q1("SELECT * FROM menus WHERE id = ?", (int(mid),))
        if current is None:
            raise HTTPException(404, "منو پیدا نشد.")
        name = (body.name or current.get("name") or "").strip()[:120]
        location = body.location if body.location in ("header", "footer") else current.get("location")
        if db.q1("SELECT id FROM menus WHERE location = ? AND id != ?", (location, int(mid))):
            raise HTTPException(409, "برای این مکان منوی دیگری وجود دارد.")
        db.x("UPDATE menus SET name = ?, location = ? WHERE id = ?", (name, location, int(mid)))
        return {"ok": True, "message": "منو ذخیره شد."}

    @app.delete("/api/admin/menus/{mid}")
    async def admin_menu_delete(mid: int, request: Request):
        _require_admin(request)
        db.x("DELETE FROM menu_items WHERE menu_id = ?", (int(mid),))
        db.x("DELETE FROM menus WHERE id = ?", (int(mid),))
        return {"ok": True, "message": "منو حذف شد."}

    def _menu_cycle(menu_id: int, item_id: int, parent_id: int) -> bool:
        """آیا قرار دادن item زیر parent باعث حلقه می‌شود؟"""
        seen = set()
        cur = int(parent_id or 0)
        while cur and cur not in seen:
            if cur == int(item_id):
                return True
            seen.add(cur)
            row = db.q1("SELECT parent_id FROM menu_items WHERE id = ? AND menu_id = ?", (cur, int(menu_id)))
            if row is None:
                return False
            cur = int(row.get("parent_id") or 0)
        return False

    def _menu_item_values(body: MenuItemBody, current: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        current = current or {}
        typ = body.type if body.type in ITEM_TYPES else current.get("type") or "custom_link"
        ref = int(body.reference_id or current.get("reference_id") or 0)
        url = (body.url or current.get("url") or "").strip()[:500]
        if typ == "page" and not db.q1("SELECT id FROM pages WHERE id = ?", (ref,)):
            raise HTTPException(400, "صفحه انتخاب‌شده معتبر نیست.")
        if typ == "post" and not db.q1("SELECT id FROM posts WHERE id = ?", (ref,)):
            raise HTTPException(400, "مقاله انتخاب‌شده معتبر نیست.")
        if typ == "category" and not db.q1("SELECT id FROM categories WHERE id = ?", (ref,)):
            raise HTTPException(400, "دسته‌بندی انتخاب‌شده معتبر نیست.")
        if typ == "custom_link":
            if not url:
                raise HTTPException(400, "آدرس لینک را وارد کنید.")
            url = _safe_menu_url(url)
            if not url:
                raise HTTPException(400, "آدرس لینک باید با http، https، mailto، tel یا / شروع شود.")
        parent = int(body.parent_id or 0)
        if parent and not db.q1("SELECT id FROM menu_items WHERE id = ? AND menu_id = ?", (parent, int(body.menu_id))):
            parent = 0
        return {
            "menu_id": int(body.menu_id),
            "title": (body.title or current.get("title") or "").strip()[:120],
            "type": typ,
            "reference_id": ref if typ != "custom_link" else 0,
            "url": url if typ == "custom_link" else "",
            "parent_id": parent,
            "is_active": 1 if body.is_active else 0,
            "sort_order": int(current.get("sort_order") or 0) if current else int(db.q1(
                "SELECT COALESCE(MAX(sort_order), -1) + 1 AS n FROM menu_items WHERE menu_id = ?", (int(body.menu_id),)
            )["n"] or 0),
        }

    @app.get("/api/admin/menu-items")
    async def admin_menu_items(request: Request, menu_id: int = 0):
        _require_admin(request)
        if menu_id:
            return {"items": _admin_menu(int(menu_id))["items"]}
        return {"items": [dict(r) for r in db.q("SELECT * FROM menu_items ORDER BY menu_id, sort_order, id")]}

    @app.post("/api/admin/menu-items")
    async def admin_menu_item_create(body: MenuItemBody, request: Request):
        _require_admin(request)
        if not db.q1("SELECT id FROM menus WHERE id = ?", (int(body.menu_id),)):
            raise HTTPException(404, "منو پیدا نشد.")
        values = _menu_item_values(body)
        iid = db.insert_id(
            "INSERT INTO menu_items(menu_id, title, type, reference_id, url, parent_id, sort_order, is_active) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
            (values["menu_id"], values["title"], values["type"], values["reference_id"], values["url"],
             values["parent_id"], values["sort_order"], values["is_active"]),
        )
        return {"ok": True, "id": iid, "message": "آیتم منو اضافه شد."}

    @app.put("/api/admin/menu-items/{iid}")
    async def admin_menu_item_update(iid: int, body: MenuItemBody, request: Request):
        _require_admin(request)
        current = db.q1("SELECT * FROM menu_items WHERE id = ?", (int(iid),))
        if current is None:
            raise HTTPException(404, "آیتم منو پیدا نشد.")
        values = _menu_item_values(body, current)
        if values["parent_id"] and _menu_cycle(values["menu_id"], int(iid), values["parent_id"]):
            values["parent_id"] = 0
        db.x(
            "UPDATE menu_items SET title = ?, type = ?, reference_id = ?, url = ?, parent_id = ?, is_active = ? "
            "WHERE id = ?",
            (values["title"], values["type"], values["reference_id"], values["url"], values["parent_id"],
             values["is_active"], int(iid)),
        )
        return {"ok": True, "message": "آیتم منو ذخیره شد."}

    @app.delete("/api/admin/menu-items/{iid}")
    async def admin_menu_item_delete(iid: int, request: Request):
        _require_admin(request)
        db.x("UPDATE menu_items SET parent_id = 0 WHERE parent_id = ?", (int(iid),))
        db.x("DELETE FROM menu_items WHERE id = ?", (int(iid),))
        return {"ok": True, "message": "آیتم منو حذف شد."}

    @app.post("/api/admin/menus/{mid}/reorder")
    async def admin_menu_reorder(mid: int, body: ReorderBody, request: Request):
        _require_admin(request)
        menu = db.q1("SELECT id FROM menus WHERE id = ?", (int(mid),))
        if menu is None:
            raise HTTPException(404, "منو پیدا نشد.")
        valid = {int(r["id"]) for r in db.q("SELECT id FROM menu_items WHERE menu_id = ?", (int(mid),))}
        proposed: Dict[int, Tuple[int, int]] = {}
        for index, item in enumerate(body.items):
            iid = int(item.id)
            if iid not in valid:
                continue
            parent = int(item.parent_id or 0)
            if parent not in valid or parent == iid:
                parent = 0
            proposed[iid] = (parent, int(item.sort_order if item.sort_order else index))
        for iid in list(proposed):
            seen = set()
            cur = proposed[iid][0]
            while cur:
                if cur == iid or cur in seen:
                    proposed[iid] = (0, proposed[iid][1])
                    break
                seen.add(cur)
                cur = proposed.get(cur, (0, 0))[0]
        with db.transaction() as tx:
            for iid, (parent, order) in proposed.items():
                tx.execute(
                    "UPDATE menu_items SET parent_id = ?, sort_order = ? WHERE id = ? AND menu_id = ?",
                    (parent, order, iid, int(mid)),
                )
        return {"ok": True, "message": "ترتیب منو ذخیره شد."}

    # ---------------------------- رسانه ----------------------------
    def _media_row(row: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if row is None:
            return None
        out = dict(row)
        out["size"] = int(float(out.get("size") or 0))
        out["created_at_fa"] = format_jalali(out.get("created_at"))
        return out

    @app.get("/api/admin/media")
    async def admin_media(request: Request, q: str = "", page: int = 1, page_size: int = 24):
        _require_admin(request)
        page, page_size = max(1, int(page)), max(6, min(int(page_size), 100))
        where, params = "", []
        if q.strip():
            like = "%" + q.strip().lower() + "%"
            where = " WHERE LOWER(original_name) LIKE ? OR LOWER(alt_text) LIKE ? OR LOWER(title) LIKE ?"
            params = [like, like, like]
        total = int(db.q1("SELECT COUNT(*) AS n FROM media" + where, params)["n"] or 0)
        rows = db.q("SELECT * FROM media" + where + " ORDER BY id DESC LIMIT ? OFFSET ?",
                    params + [page_size, (page - 1) * page_size])
        return {"items": [_media_row(r) for r in rows], "total": total, "page": page, "page_size": page_size}

    @app.post("/api/admin/media/upload")
    async def admin_media_upload(
        request: Request,
        file: UploadFile = File(...),
        alt_text: str = Form(""),
        title: str = Form(""),
    ):
        _require_admin(request)
        if not file.filename:
            raise HTTPException(400, "فایلی انتخاب نشده است.")
        ext, mime, data = await read_image(file, allowed_extensions=IMAGE_FORMATS, max_bytes=MAX_MEDIA_BYTES)
        now = datetime.now(timezone.utc)
        folder = media_root / f"{now.year:04d}" / f"{now.month:02d}"
        folder.mkdir(parents=True, exist_ok=True)
        stored = uuid.uuid4().hex + ext
        try:
            (folder / stored).write_bytes(data)
        except OSError as exc:
            raise HTTPException(500, f"ذخیره‌ی فایل انجام نشد: {exc}") from None
        rel = f"/uploads/media/{now.year:04d}/{now.month:02d}/{stored}"
        mid = db.insert_id(
            "INSERT INTO media(original_name, stored_name, path, mime_type, size, alt_text, title) "
            "VALUES(?, ?, ?, ?, ?, ?, ?)",
            (Path(file.filename).name[:250], stored, rel, mime, len(data), alt_text.strip()[:200], title.strip()[:200]),
        )
        return {"ok": True, "id": mid, "message": "فایل بارگذاری شد.", "item": _media_row(db.q1("SELECT * FROM media WHERE id = ?", (mid,)))}

    @app.put("/api/admin/media/{mid}")
    async def admin_media_update(mid: int, body: MediaMetaBody, request: Request):
        _require_admin(request)
        row = db.q1("SELECT * FROM media WHERE id = ?", (int(mid),))
        if row is None:
            raise HTTPException(404, "رسانه پیدا نشد.")
        db.x(
            "UPDATE media SET alt_text = ?, title = ? WHERE id = ?",
            ((body.alt_text if body.alt_text is not None else row.get("alt_text") or "")[:200],
             (body.title if body.title is not None else row.get("title") or "")[:200], int(mid)),
        )
        return {"ok": True, "message": "اطلاعات رسانه ذخیره شد."}

    @app.delete("/api/admin/media/{mid}")
    async def admin_media_delete(mid: int, request: Request):
        _require_admin(request)
        row = db.q1("SELECT * FROM media WHERE id = ?", (int(mid),))
        if row is None:
            raise HTTPException(404, "رسانه پیدا نشد.")
        rel = str(row.get("path") or "")
        try:
            if rel.startswith("/uploads/media/"):
                target = media_root / Path(rel).name
                parent = (media_root / rel.split("/uploads/media/")[-1]).parent
                possible = parent / Path(rel).name
                for candidate in (possible, target):
                    try:
                        if candidate.exists() and media_root.resolve() in candidate.resolve().parents:
                            candidate.unlink()
                            break
                    except OSError:
                        pass
        except Exception:
            log.warning("media file delete failed: %s", rel, exc_info=True)
        db.x("DELETE FROM media WHERE id = ?", (int(mid),))
        return {"ok": True, "message": "رسانه حذف شد."}

    # ---------------------------- ظاهر ----------------------------
    @app.get("/api/admin/appearance")
    async def admin_appearance(request: Request):
        _require_admin(request)
        return {
            "settings": {
                "default_content_width": setting("default_content_width", "normal"),
                "sidebar_enabled": db.get_bool("sidebar_enabled"),
                "sidebar_position": setting("sidebar_position", "right"),
                "sidebar_pages": db.get_bool("sidebar_pages"),
                "sidebar_posts": db.get_bool("sidebar_posts"),
                "date_format": setting("date_format", "jalali_long"),
                "date_format_custom": setting("date_format_custom", "{weekday} {day} {month} {year}"),
            },
            "widths": [
                {"key": "narrow", "label": "باریک (۷۲۰ پیکسل)"},
                {"key": "normal", "label": "معمولی (۹۶۰ پیکسل)"},
                {"key": "wide", "label": "پهن (۱۲۰۰ پیکسل)"},
                {"key": "full", "label": "تمام عرض"},
            ],
            "date_formats": [
                {"key": "jalali_long", "label": "سه‌شنبه ۵ آبان ۱۴۰۵"},
                {"key": "jalali_medium", "label": "۵ آبان ۱۴۰۵"},
                {"key": "jalali_short", "label": "۱۴۰۵/۰۸/۰۵"},
                {"key": "jalali_numeric", "label": "۱۴۰۵-۰۸-۰۵"},
                {"key": "jalali_with_time", "label": "سه‌شنبه ۵ آبان ۱۴۰۵ ساعت ۱۴:۳۰"},
                {"key": "custom", "label": "الگوی دلخواه"},
            ],
            "tokens": ["{" + token + "}" for token in DATE_TOKENS],
        }

    @app.put("/api/admin/appearance")
    async def admin_appearance_save(body: AppearanceBody, request: Request):
        _require_admin(request)
        updates: Dict[str, str] = {}
        if body.default_content_width is not None:
            updates["default_content_width"] = body.default_content_width if body.default_content_width in PUBLIC_WIDTHS else "normal"
        if body.sidebar_enabled is not None:
            updates["sidebar_enabled"] = "1" if body.sidebar_enabled else "0"
        if body.sidebar_position is not None:
            updates["sidebar_position"] = body.sidebar_position if body.sidebar_position in ("right", "left") else "right"
        if body.sidebar_pages is not None:
            updates["sidebar_pages"] = "1" if body.sidebar_pages else "0"
        if body.sidebar_posts is not None:
            updates["sidebar_posts"] = "1" if body.sidebar_posts else "0"
        if body.date_format is not None:
            allowed = ("jalali_long", "jalali_medium", "jalali_short", "jalali_numeric", "jalali_with_time", "custom")
            updates["date_format"] = body.date_format if body.date_format in allowed else "jalali_long"
        if body.date_format_custom is not None:
            updates["date_format_custom"] = body.date_format_custom.strip()[:180] or "{weekday} {day} {month} {year}"
        if updates:
            db.set_many(updates)
        return {"ok": True, "message": "تنظیمات ظاهر ذخیره شد."}

    @app.get("/api/admin/appearance/preview")
    async def admin_appearance_preview(request: Request, format: str = "", custom: str = ""):
        _require_admin(request)
        now = time.time()
        if format == "custom":
            text = format_jalali(now, "custom", custom or "{weekday} {day} {month} {year}")
        else:
            text = format_jalali(now, format or setting("date_format", "jalali_long"))
        return {"preview": text}

    # ---------------------------- سئو ----------------------------
    @app.get("/api/admin/seo")
    async def admin_seo(request: Request):
        _require_admin(request)
        return {
            "settings": {
                "seo_meta_title": setting("seo_meta_title", ""),
                "seo_meta_description": setting("seo_meta_description", ""),
                "seo_og_image": setting("seo_og_image", ""),
                "seo_robots_default": setting("seo_robots_default", "index,follow"),
                "seo_canonical_base": setting("seo_canonical_base", ""),
                "google_site_verification": setting("google_site_verification", ""),
                "robots_txt": setting("robots_txt", ""),
                "tg_bot_username": setting("tg_bot_username", ""),
                "tg_bot_name_guide": setting("tg_bot_name_guide", ""),
                "tg_bot_about_guide": setting("tg_bot_about_guide", ""),
                "tg_bot_description_guide": setting("tg_bot_description_guide", ""),
                "tg_share_app_name": setting("tg_share_app_name", ""),
            },
            "defaults": {
                "robots_default": "index,follow",
                "robots_txt": robots_text(),
                "site": canonical_base(),
                "bot_username": (setting("tg_bot_username", "") or config.BOT_USERNAME or "").strip().lstrip("@"),
            },
        }

    @app.put("/api/admin/seo")
    async def admin_seo_save(body: SeoBody, request: Request):
        _require_admin(request)
        updates: Dict[str, str] = {}
        limits = {
            "seo_meta_title": 200, "seo_meta_description": 400, "seo_og_image": 400,
            "seo_robots_default": 80, "seo_canonical_base": 300, "google_site_verification": 200,
            "robots_txt": 20000, "tg_bot_username": 64, "tg_bot_name_guide": 2000, "tg_bot_about_guide": 2000,
            "tg_bot_description_guide": 4000, "tg_share_app_name": 40,
        }
        for key, limit in limits.items():
            value = getattr(body, key)
            if value is None:
                continue
            value = str(value).strip()
            if key == "seo_canonical_base" and value and not value.startswith(("http://", "https://")):
                raise HTTPException(400, "آدرس کانونیکال باید با http یا https شروع شود.")
            if key == "seo_robots_default" and value and value not in ("index,follow", "noindex,follow", "index,nofollow", "noindex,nofollow"):
                raise HTTPException(400, "مقدار robots معتبر نیست.")
            updates[key] = value[:limit]
        if updates:
            db.set_many(updates)
        return {"ok": True, "message": "تنظیمات سئو ذخیره شد."}

    @app.get("/api/admin/seo/share-link")
    async def admin_seo_share_link(request: Request, param: str = ""):
        _require_admin(request)
        bot = (setting("tg_bot_username", "") or config.BOT_USERNAME or "").strip().lstrip("@")
        if not bot:
            raise HTTPException(400, "نام کاربری ربات ثبت نشده است؛ ابتدا آن را در تنظیمات سئو وارد کنید.")
        app_name = setting("tg_share_app_name", "").strip()
        if app_name:
            link = f"https://t.me/{bot}/{quote(app_name, safe='')}"
        else:
            link = f"https://t.me/{bot}"
        if param.strip():
            link += "?startapp=" + quote(param.strip()[:64], safe="")
        return {"link": link}

    # ---------------------------- APIهای عمومی ----------------------------
    @app.get("/api/pages/{slug}")
    async def api_public_page(slug: str):
        row = get_page(slug)
        if row is None:
            raise HTTPException(404, "صفحه پیدا نشد.")
        data = page_row(row)
        data["meta"] = page_meta(data)
        return {"item": data}

    @app.get("/api/posts")
    async def api_public_posts(request: Request, category: str = "", page: int = 1, page_size: int = 9):
        rows = render_education_data(category, page, page_size)
        return rows

    @app.get("/api/posts/{slug}")
    async def api_public_post(slug: str):
        row = get_post(slug, increment=True)
        if row is None:
            raise HTTPException(404, "مقاله پیدا نشد.")
        data = post_row(row)
        data["meta"] = post_meta(data)
        data["category"] = {"name": data.get("category_name") or "", "slug": data.get("category_slug") or ""}
        return {"item": data}

    @app.get("/api/categories")
    async def api_public_categories():
        return {"items": categories(with_counts=True)}

    @app.get("/api/menus/{location}")
    async def api_public_menu(location: str):
        if location not in ("header", "footer"):
            raise HTTPException(404, "منو پیدا نشد.")
        return {"items": menu_tree(location)}

    # ---------------------------- رندر عمومی ----------------------------
    @app.get("/robots.txt", response_class=PlainTextResponse)
    async def robots_file():
        return PlainTextResponse(robots_text(), media_type="text/plain; charset=utf-8")

    @app.get("/sitemap.xml")
    async def sitemap_file():
        return Response(render_sitemap(), media_type="application/xml; charset=utf-8")

    @app.get("/p/{slug}", response_class=HTMLResponse)
    async def public_page(slug: str):
        document = render_page(slug)
        if document is None:
            raise HTTPException(404, "صفحه پیدا نشد.")
        return HTMLResponse(document)

    @app.get("/education", response_class=HTMLResponse)
    async def public_education(request: Request, page: int = 1):
        return HTMLResponse(render_education("", page))

    @app.get("/education/{slug}", response_class=HTMLResponse)
    async def public_post(slug: str):
        document = render_post(slug)
        if document is None:
            raise HTTPException(404, "مقاله پیدا نشد.")
        return HTMLResponse(document)

    @app.get("/category/{slug}", response_class=HTMLResponse)
    async def public_category(slug: str, request: Request, page: int = 1):
        if not db.q1("SELECT id FROM categories WHERE slug = ?", (slug,)):
            raise HTTPException(404, "دسته‌بندی پیدا نشد.")
        return HTMLResponse(render_education(slug, page))

    @app.get("/api/public/appearance")
    async def public_appearance():
        return {
            "content_width": setting("default_content_width", "normal"),
            "sidebar_enabled": db.get_bool("sidebar_enabled"),
            "sidebar_position": setting("sidebar_position", "right"),
            "date_format": setting("date_format", "jalali_long"),
            "date_format_custom": setting("date_format_custom", ""),
        }


def get_post_by_id(pid: int) -> Optional[Dict[str, Any]]:
    return db.q1(
        "SELECT p.*, c.name AS category_name, c.slug AS category_slug FROM posts p "
        "LEFT JOIN categories c ON c.id = p.category_id WHERE p.id = ?",
        (int(pid),),
    )


def render_education_data(category_slug: str, page: int = 1, page_size: int = 9) -> Dict[str, Any]:
    page = max(1, int(page or 1))
    size = max(1, min(int(page_size or 9), 50))
    where, params = ["p.status = 'published'"], []
    if category_slug:
        category = db.q1("SELECT * FROM categories WHERE slug = ?", (category_slug,))
        if category is None:
            raise HTTPException(404, "دسته‌بندی پیدا نشد.")
        where.append("p.category_id = ?")
        params.append(int(category["id"]))
    total = int(db.q1("SELECT COUNT(*) AS n FROM posts p WHERE " + " AND ".join(where), params)["n"] or 0)
    rows = db.q(
        "SELECT p.*, c.name AS category_name, c.slug AS category_slug FROM posts p "
        "LEFT JOIN categories c ON c.id = p.category_id WHERE " + " AND ".join(where) +
        " ORDER BY COALESCE(p.published_at, p.created_at) DESC LIMIT ? OFFSET ?",
        params + [size, (page - 1) * size],
    )
    return {
        "items": [post_row(r) for r in rows],
        "total": total,
        "page": page,
        "page_size": size,
        "pages": max(1, (total + size - 1) // size),
    }


def _parse_publish(value: Any) -> Optional[str]:
    """تاریخ انتشار: پذیرش ISO/رشته‌ی دیتابیس/epoch و ذخیره به UTC."""
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    text = str(value).strip().replace("T", " ")
    text = text.replace("Z", "").split("+")[0].strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(text[:19], fmt)
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
    return None
