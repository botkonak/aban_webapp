"""
مدیریت دیتابیس آبان ترید (Aban Trade).

- با SQLAlchemy کار می‌کند و فقط از config.DATABASE_URL استفاده می‌شود:
      sqlite:///data/bot.db                          -> SQLite (پیش‌فرض: WAL + کش حافظه)
      postgresql://user:pass@host:5432/dbname        -> PostgreSQL (Supabase / Neon / Render / ...)
  با عوض کردن همین متغیر محیطی، بدون تغییر در کد، دیتابیس عوض می‌شود.
- ساختار جدول‌ها و کوئری‌ها برای هر دو دیتابیس یکسان است (کوئری‌ها با ? نوشته می‌شوند و خودکار به پارامتر نام‌دار تبدیل می‌شوند).
- داده‌های پرخوان (تنظیمات، قالب‌ها، کاربران تأییدشده، ...) در حافظه کش می‌شوند.
"""
from __future__ import annotations

import json
import logging
import os
import random
import re
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterator, List, Optional, Sequence, Set, Tuple

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import make_url

import config
from models import AIKey, Block, Channel, SubRequest, User

log = logging.getLogger("db")

# ----------------------------------------------------------------------------
# ساختار جدول‌ها (توکن‌ها بسته به نوع دیتابیس عوض می‌شوند)
#   {ID}  کلید خودکار     {BIG} عدد بزرگ (آیدی تلگرام > ۳۲ بیت)     {DBL} اعشار دقیق
#   {TS}  زمان با مقدار پیش‌فرض (UTC)     {TSN} زمان بدون مقدار پیش‌فرض
# ----------------------------------------------------------------------------
SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id {BIG} PRIMARY KEY,
    phone_number TEXT,
    is_verified INTEGER DEFAULT 0,
    joined_at {TS},
    pre_alert INTEGER DEFAULT 1,
    sub_until {DBL} DEFAULT 0,
    sub_notice {DBL} DEFAULT 0,
    first_name TEXT DEFAULT '',
    last_name TEXT DEFAULT '',
    username TEXT DEFAULT '',
    webapp_sub_until {DBL} DEFAULT 0
);
CREATE TABLE IF NOT EXISTS force_channels (
    id {ID},
    channel_id TEXT,
    channel_title TEXT,
    is_active INTEGER DEFAULT 1,
    sort_order INTEGER,
    invite_link TEXT
);
CREATE TABLE IF NOT EXISTS blocks (
    id {ID},
    block_type TEXT,
    content TEXT,
    is_active INTEGER DEFAULT 1,
    created_at {TS}
);
CREATE TABLE IF NOT EXISTS templates (
    id {ID},
    template_type TEXT,
    content TEXT,
    is_active INTEGER DEFAULT 1,
    updated_at {TS}
);
CREATE TABLE IF NOT EXISTS signal_history (
    id {ID},
    symbol TEXT,
    side TEXT,
    user_id {BIG} DEFAULT 0,
    created_at {TS}
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS symbol_filter (
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (kind, name)
);
CREATE TABLE IF NOT EXISTS blocked_users (
    user_id {BIG} PRIMARY KEY,
    "until" {DBL} NOT NULL,
    reason TEXT,
    created_at {TS}
);
CREATE TABLE IF NOT EXISTS sub_requests (
    id {ID},
    user_id {BIG} NOT NULL,
    text TEXT,
    photo_id TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    reason TEXT,
    created_at {TS},
    decided_at {TSN},
    service_type TEXT NOT NULL DEFAULT 'bot'
);
CREATE TABLE IF NOT EXISTS ai_keys (
    slot INTEGER PRIMARY KEY,
    name TEXT,
    api_key TEXT NOT NULL,
    account_id TEXT DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1,
    err_until {DBL} NOT NULL DEFAULT 0,
    last_error TEXT,
    ok_count INTEGER NOT NULL DEFAULT 0,
    fail_count INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS support_tickets (
    id {ID},
    user_id {BIG} NOT NULL,
    username TEXT,
    first_name TEXT,
    status TEXT NOT NULL DEFAULT 'open',
    created_at {TS},
    closed_at {TSN},
    ai_state TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS support_messages (
    id {ID},
    ticket_id {BIG} NOT NULL REFERENCES support_tickets(id),
    sender TEXT NOT NULL DEFAULT 'user',
    sender_id {BIG},
    message_type TEXT NOT NULL DEFAULT 'text',
    text TEXT,
    file_id TEXT,
    created_at {TS}
);
CREATE TABLE IF NOT EXISTS strategies (
    id {ID},
    strategy_key TEXT NOT NULL UNIQUE,
    strategy_name TEXT NOT NULL,
    description TEXT,
    long_settings_json TEXT,
    short_settings_json TEXT,
    is_active INTEGER DEFAULT 0,
    is_builtin INTEGER DEFAULT 0,
    created_at {TS}
);
CREATE TABLE IF NOT EXISTS signal_performance (
    id {ID},
    signal_id {BIG},
    user_id {BIG} DEFAULT 0,
    pair TEXT,
    side TEXT,
    entry {DBL},
    sl {DBL},
    tp1 {DBL},
    tp2 {DBL},
    tp3 {DBL},
    status TEXT NOT NULL DEFAULT 'active',
    best_tp INTEGER DEFAULT 0,
    result_pct {DBL} DEFAULT 0,
    max_price {DBL},
    min_price {DBL},
    first_tp_at {DBL},
    created_at {TS},
    closed_at {TSN}
);
CREATE TABLE IF NOT EXISTS engine_suggestions (
    id {ID},
    user_id {BIG} DEFAULT 0,
    analysis_text TEXT,
    suggestions_json TEXT,
    reasoning_json TEXT,
    old_json TEXT,
    rejected_json TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at {TS},
    applied_at {TSN}
);
CREATE TABLE IF NOT EXISTS analysis_requests (
    id {ID},
    user_id {BIG},
    symbol TEXT,
    result_text TEXT,
    kind TEXT DEFAULT 'single',
    created_at {TS}
);
CREATE TABLE IF NOT EXISTS price_channels (
    id {ID},
    channel_id TEXT NOT NULL,
    channel_title TEXT,
    symbols TEXT,
    interval_hours {DBL} DEFAULT 1,
    template TEXT,
    enabled INTEGER DEFAULT 1,
    last_sent_at {DBL} DEFAULT 0
);
CREATE TABLE IF NOT EXISTS bsc_payments (
    id {ID},
    user_id {BIG} NOT NULL,
    tx_hash TEXT UNIQUE,            -- هش تراکنش (یکتا برای جلوگیری از تقلب)
    amount {DBL} NOT NULL,          -- مبلغ تایید شده
    created_at {TS}
);
CREATE TABLE IF NOT EXISTS payments_history (
    id {ID},
    user_id {BIG} NOT NULL,
    amount {DBL} DEFAULT 0,
    expected_amount {DBL} DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending',
    method TEXT NOT NULL DEFAULT 'manual',
    service_type TEXT NOT NULL DEFAULT 'bot',
    tx_hash TEXT UNIQUE,
    sender_address TEXT,
    receiver_address TEXT,
    created_at {TS},
    admin_note TEXT,
    request_id {BIG},
    network TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS payment_networks (
    id {ID},
    plan TEXT NOT NULL,
    network_key TEXT NOT NULL,
    asset TEXT NOT NULL,
    network TEXT NOT NULL,
    network_label TEXT DEFAULT '',
    chain TEXT NOT NULL,
    kind TEXT NOT NULL,
    contract TEXT DEFAULT '',
    decimals INTEGER DEFAULT 18,
    wallet TEXT DEFAULT '',
    amount {DBL} DEFAULT 0,
    enabled INTEGER NOT NULL DEFAULT 0,
    sort_order INTEGER DEFAULT 0,
    created_at {TS},
    UNIQUE (plan, network_key)
);
CREATE TABLE IF NOT EXISTS payment_cards (
    id {ID},
    plan TEXT NOT NULL,
    holder_name TEXT DEFAULT '',
    card_number TEXT DEFAULT '',
    masked TEXT DEFAULT '',
    shaba TEXT DEFAULT '',
    bank_code TEXT DEFAULT '',
    bank_name TEXT DEFAULT '',
    note TEXT DEFAULT '',
    is_active INTEGER NOT NULL DEFAULT 0,
    sort_order INTEGER DEFAULT 0,
    created_at {TS}
);
CREATE TABLE IF NOT EXISTS payment_wallets (
    id {ID},
    plan TEXT NOT NULL,
    network TEXT DEFAULT '',
    chain TEXT DEFAULT '',
    asset TEXT DEFAULT '',
    network_label TEXT DEFAULT '',
    address TEXT DEFAULT '',
    amount {DBL} DEFAULT 0,
    note TEXT DEFAULT '',
    is_active INTEGER NOT NULL DEFAULT 0,
    sort_order INTEGER DEFAULT 0,
    created_at {TS}
);
CREATE TABLE IF NOT EXISTS free_sub_claims (
    id {ID},
    user_id {BIG} NOT NULL,
    plan TEXT NOT NULL,
    starts_at {DBL} DEFAULT 0,
    ends_at {DBL} DEFAULT 0,
    prev_sub_until {DBL} DEFAULT 0,
    features TEXT DEFAULT '',
    strategies TEXT DEFAULT '',
    categories TEXT DEFAULT '',
    restored INTEGER NOT NULL DEFAULT 0,
    warned INTEGER NOT NULL DEFAULT 0,
    created_at {TS},
    UNIQUE (user_id, plan)
);
CREATE TABLE IF NOT EXISTS backup_history (
    id {ID},
    filename TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'full',
    tables_json TEXT DEFAULT '',
    size {BIG} DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'ready',
    created_by {BIG} DEFAULT 0,
    created_at {TS}
);
CREATE TABLE IF NOT EXISTS security_events (
    id {ID},
    user_id {BIG} DEFAULT 0,
    kind TEXT NOT NULL,
    detail TEXT DEFAULT '',
    ip TEXT DEFAULT '',
    severity TEXT NOT NULL DEFAULT 'info',
    resolved INTEGER NOT NULL DEFAULT 0,
    created_at {TS}
);
CREATE TABLE IF NOT EXISTS ip_blocks (
    id {ID},
    ip TEXT NOT NULL UNIQUE,
    reason TEXT DEFAULT '',
    blocked_until {DBL} NOT NULL DEFAULT 0,
    created_at {TS}
);
CREATE TABLE IF NOT EXISTS admin_audit_log (
    id {ID},
    admin_id {BIG} NOT NULL DEFAULT 0,
    action TEXT NOT NULL,
    target_type TEXT DEFAULT '',
    target_id TEXT DEFAULT '',
    details TEXT DEFAULT '',
    ip TEXT DEFAULT '',
    created_at {TS}
);
CREATE TABLE IF NOT EXISTS pages (
    id {ID},
    title TEXT NOT NULL,
    slug TEXT NOT NULL UNIQUE,
    content TEXT DEFAULT '',
    status TEXT NOT NULL DEFAULT 'draft',
    is_system INTEGER NOT NULL DEFAULT 0,
    has_sidebar INTEGER NOT NULL DEFAULT 0,
    content_width TEXT DEFAULT 'normal',
    meta_title TEXT DEFAULT '',
    meta_description TEXT DEFAULT '',
    canonical_url TEXT DEFAULT '',
    robots TEXT DEFAULT '',
    og_title TEXT DEFAULT '',
    og_description TEXT DEFAULT '',
    og_image TEXT DEFAULT '',
    created_at {TS},
    updated_at {TS}
);
CREATE TABLE IF NOT EXISTS categories (
    id {ID},
    name TEXT NOT NULL,
    slug TEXT NOT NULL UNIQUE,
    created_at {TS}
);
CREATE TABLE IF NOT EXISTS posts (
    id {ID},
    title TEXT NOT NULL,
    slug TEXT NOT NULL UNIQUE,
    summary TEXT DEFAULT '',
    content TEXT DEFAULT '',
    thumbnail TEXT DEFAULT '',
    category_id {BIG} DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'draft',
    published_at {TSN},
    views INTEGER NOT NULL DEFAULT 0,
    reading_time INTEGER NOT NULL DEFAULT 0,
    has_sidebar INTEGER NOT NULL DEFAULT 1,
    content_width TEXT DEFAULT 'normal',
    meta_title TEXT DEFAULT '',
    meta_description TEXT DEFAULT '',
    canonical_url TEXT DEFAULT '',
    robots TEXT DEFAULT '',
    og_title TEXT DEFAULT '',
    og_description TEXT DEFAULT '',
    og_image TEXT DEFAULT '',
    created_at {TS},
    updated_at {TS}
);
CREATE TABLE IF NOT EXISTS menus (
    id {ID},
    name TEXT NOT NULL,
    location TEXT NOT NULL UNIQUE,
    created_at {TS}
);
CREATE TABLE IF NOT EXISTS menu_items (
    id {ID},
    menu_id {BIG} NOT NULL,
    title TEXT NOT NULL,
    type TEXT NOT NULL DEFAULT 'custom_link',
    reference_id {BIG} DEFAULT 0,
    url TEXT DEFAULT '',
    parent_id {BIG} DEFAULT 0,
    sort_order INTEGER NOT NULL DEFAULT 0,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at {TS}
);
CREATE TABLE IF NOT EXISTS media (
    id {ID},
    original_name TEXT DEFAULT '',
    stored_name TEXT DEFAULT '',
    path TEXT NOT NULL,
    mime_type TEXT DEFAULT '',
    size {BIG} DEFAULT 0,
    alt_text TEXT DEFAULT '',
    title TEXT DEFAULT '',
    created_at {TS}
);
CREATE TABLE IF NOT EXISTS registration_fields (
    id {ID},
    field_key TEXT NOT NULL UNIQUE,
    label TEXT NOT NULL,
    field_type TEXT NOT NULL DEFAULT 'text',
    is_required INTEGER NOT NULL DEFAULT 0,
    is_active INTEGER NOT NULL DEFAULT 0,
    sort_order INTEGER NOT NULL DEFAULT 0,
    validation_regex TEXT DEFAULT '',
    created_at {TS}
);
CREATE TABLE IF NOT EXISTS user_profiles (
    user_id {BIG} NOT NULL,
    field_key TEXT NOT NULL,
    value TEXT DEFAULT '',
    PRIMARY KEY (user_id, field_key)
);
CREATE TABLE IF NOT EXISTS otp_codes (
    id {ID},
    phone TEXT NOT NULL,
    code_hash TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    expires_at {DBL} NOT NULL DEFAULT 0,
    created_at {TS},
    used INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS message_templates (
    id {ID},
    template_type TEXT NOT NULL,
    template_text TEXT,
    updated_at {TS}
);
CREATE TABLE IF NOT EXISTS user_settings (
    user_id {BIG} NOT NULL,
    key TEXT NOT NULL,
    value TEXT,
    PRIMARY KEY (user_id, key)
);
CREATE TABLE IF NOT EXISTS user_symbol_filter (
    user_id {BIG} NOT NULL,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (user_id, kind, name)
);
CREATE TABLE IF NOT EXISTS structural_strategies (
    id {ID},
    strategy_key TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL,
    title TEXT NOT NULL,
    strategy_type TEXT NOT NULL,
    signal_type TEXT NOT NULL,
    text_color TEXT DEFAULT 'green',
    button_color TEXT DEFAULT 'success',
    logic_key TEXT NOT NULL,
    logic_params TEXT DEFAULT '',
    description TEXT DEFAULT '',
    guide_image TEXT DEFAULT '',
    guide_width INTEGER DEFAULT 0,
    guide_height INTEGER DEFAULT 0,
    sort_order INTEGER DEFAULT 0,
    is_active INTEGER DEFAULT 1,
    created_at {TS}
);
CREATE TABLE IF NOT EXISTS strategy_guides (
    id {ID},
    strategy_key TEXT NOT NULL UNIQUE,
    title TEXT DEFAULT '',
    description TEXT DEFAULT '',
    image_path TEXT DEFAULT '',
    image_width INTEGER DEFAULT 0,
    image_height INTEGER DEFAULT 0,
    updated_at {TS}
);
CREATE TABLE IF NOT EXISTS webapp_users (
    user_id {BIG} PRIMARY KEY,
    password_hash TEXT DEFAULT '',
    password_lookup TEXT DEFAULT '',
    access_enabled INTEGER DEFAULT 1,
    password_issued_at {DBL} DEFAULT 0,
    last_login_at {DBL} DEFAULT 0,
    username TEXT DEFAULT '',
    email TEXT DEFAULT '',
    phone TEXT DEFAULT '',
    first_name TEXT DEFAULT '',
    last_name TEXT DEFAULT '',
    updated_at {TS},
    created_at {TS}
);
CREATE TABLE IF NOT EXISTS webapp_user_settings (
    user_id {BIG} NOT NULL,
    key TEXT NOT NULL,
    value TEXT,
    PRIMARY KEY (user_id, key)
);
CREATE TABLE IF NOT EXISTS webapp_favorites (
    user_id {BIG} NOT NULL,
    kind TEXT NOT NULL,
    value TEXT NOT NULL,
    created_at {TS},
    PRIMARY KEY (user_id, kind, value)
);
CREATE TABLE IF NOT EXISTS structural_signals (
    id {ID},
    symbol TEXT NOT NULL,
    exchange TEXT NOT NULL DEFAULT '',
    timeframe TEXT NOT NULL,
    strategy_key TEXT NOT NULL,
    strategy_title TEXT DEFAULT '',
    strategy_type TEXT DEFAULT '',
    signal_type TEXT NOT NULL,
    direction TEXT NOT NULL DEFAULT 'LONG',
    price {DBL} DEFAULT 0,
    candle_ts {DBL} DEFAULT 0,
    detected_at {TS},
    details TEXT DEFAULT '',
    is_active INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS search_history (
    id {ID},
    user_id {BIG} NOT NULL,
    filters TEXT DEFAULT '',
    strategies TEXT DEFAULT '',
    timeframe TEXT DEFAULT '',
    depth INTEGER DEFAULT 0,
    symbols_mode TEXT DEFAULT '',
    result_count INTEGER DEFAULT 0,
    created_at {TS}
);
CREATE TABLE IF NOT EXISTS webapp_messages (
    id {ID},
    admin_id {BIG} DEFAULT 0,
    target_type TEXT DEFAULT '',
    filter_json TEXT DEFAULT '',
    text TEXT DEFAULT '',
    sent INTEGER DEFAULT 0,
    failed INTEGER DEFAULT 0,
    created_at {TS}
);
CREATE TABLE IF NOT EXISTS exchanges (
    id {ID},
    name TEXT NOT NULL,
    slug TEXT NOT NULL UNIQUE,
    ccxt_id TEXT DEFAULT '',
    api_url TEXT DEFAULT '',
    priority INTEGER NOT NULL DEFAULT 100,
    is_active INTEGER NOT NULL DEFAULT 1,
    is_visible INTEGER NOT NULL DEFAULT 1,
    created_at {TS}
);
CREATE TABLE IF NOT EXISTS symbol_assets (
    id {ID},
    symbol TEXT NOT NULL,
    base_asset TEXT NOT NULL,
    quote_asset TEXT NOT NULL DEFAULT 'USDT',
    exchange TEXT DEFAULT '',
    created_at {TS},
    UNIQUE(symbol, exchange)
);
CREATE TABLE IF NOT EXISTS asset_logos (
    id {ID},
    base_asset TEXT NOT NULL UNIQUE,
    logo_url TEXT DEFAULT '',
    updated_at {TS}
);
CREATE INDEX IF NOT EXISTS idx_signal_history_symbol ON signal_history(symbol, created_at);
CREATE INDEX IF NOT EXISTS idx_blocks_type ON blocks(block_type);
CREATE INDEX IF NOT EXISTS idx_sub_requests_status ON sub_requests(status, id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_templates_type ON templates(template_type);
CREATE INDEX IF NOT EXISTS idx_support_tickets_user ON support_tickets(user_id, status);
CREATE INDEX IF NOT EXISTS idx_support_messages_ticket ON support_messages(ticket_id, id);
CREATE INDEX IF NOT EXISTS idx_signal_perf_status ON signal_performance(status, id);
CREATE INDEX IF NOT EXISTS idx_analysis_user ON analysis_requests(user_id, created_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_message_templates_type ON message_templates(template_type);
CREATE INDEX IF NOT EXISTS idx_payments_user ON payments_history(user_id, id);
CREATE INDEX IF NOT EXISTS idx_payments_status ON payments_history(status, id);
CREATE INDEX IF NOT EXISTS idx_payments_request ON payments_history(request_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_struct_sig_unique
    ON structural_signals(symbol, exchange, timeframe, strategy_key, candle_ts);
CREATE INDEX IF NOT EXISTS idx_struct_sig_symbol ON structural_signals(symbol, timeframe, candle_ts);
CREATE INDEX IF NOT EXISTS idx_struct_sig_recent ON structural_signals(detected_at, id);
CREATE INDEX IF NOT EXISTS idx_search_history_user ON search_history(user_id, id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_webapp_password_lookup ON webapp_users(password_lookup) WHERE password_lookup <> '';
CREATE INDEX IF NOT EXISTS idx_webapp_fav_user ON webapp_favorites(user_id, kind);
CREATE INDEX IF NOT EXISTS idx_webapp_msg_created ON webapp_messages(created_at);
CREATE INDEX IF NOT EXISTS idx_pay_net_plan ON payment_networks(plan, enabled, sort_order);
CREATE INDEX IF NOT EXISTS idx_pay_card_plan ON payment_cards(plan, is_active, sort_order);
CREATE INDEX IF NOT EXISTS idx_pay_wallet_plan ON payment_wallets(plan, is_active, sort_order);
CREATE INDEX IF NOT EXISTS idx_free_claims_end ON free_sub_claims(ends_at, restored);
CREATE INDEX IF NOT EXISTS idx_backup_created ON backup_history(created_at, id);
CREATE INDEX IF NOT EXISTS idx_sec_events_created ON security_events(created_at, id);
CREATE INDEX IF NOT EXISTS idx_pages_slug ON pages(slug);
CREATE INDEX IF NOT EXISTS idx_posts_status ON posts(status, published_at);
CREATE INDEX IF NOT EXISTS idx_posts_category ON posts(category_id, status);
CREATE INDEX IF NOT EXISTS idx_menu_items_menu ON menu_items(menu_id, parent_id, sort_order);
CREATE INDEX IF NOT EXISTS idx_media_created ON media(created_at, id);
CREATE INDEX IF NOT EXISTS idx_reg_fields_order ON registration_fields(sort_order, id);
CREATE INDEX IF NOT EXISTS idx_otp_phone ON otp_codes(phone, created_at);
CREATE INDEX IF NOT EXISTS idx_ip_blocks_ip ON ip_blocks(ip);
CREATE INDEX IF NOT EXISTS idx_audit_created ON admin_audit_log(created_at, id);
"""

# ترتیب جدول‌ها برای Backup / Restore (جدول‌های وابسته بعد از جدول مرجع)
BACKUP_TABLES: Tuple[str, ...] = (
    "settings", "users", "force_channels", "blocks", "templates", "signal_history", "symbol_filter",
    "blocked_users", "sub_requests", "ai_keys", "support_tickets", "support_messages", "strategies",
    "signal_performance", "engine_suggestions", "analysis_requests", "price_channels", "message_templates",
    "user_settings", "user_symbol_filter", "bsc_payments", "payments_history",
    "structural_strategies", "strategy_guides", "webapp_users", "webapp_user_settings", "webapp_favorites",
    "structural_signals", "search_history", "webapp_messages", "exchanges",
    "symbol_assets", "asset_logos",
    "payment_networks", "payment_cards", "payment_wallets", "free_sub_claims", "backup_history", "security_events",
    "pages", "posts", "categories", "menus", "menu_items", "media",
    "registration_fields", "user_profiles", "ip_blocks", "admin_audit_log", "otp_codes",
)
# جدول‌هایی که کلید خودکار id دارند (برای تنظیم دوباره‌ی Sequence در PostgreSQL بعد از Restore)
SERIAL_TABLES: Tuple[str, ...] = tuple(
    t for t in BACKUP_TABLES
    if t not in (
        "settings", "users", "symbol_filter", "blocked_users", "ai_keys", "user_settings", "user_symbol_filter",
        "webapp_users", "webapp_user_settings", "webapp_favorites", "user_profiles",
    )
)

# قالب‌های LONG/SHORT ربات و قالب‌های کانال (CH_LONG / CH_SHORT)
TEMPLATE_TYPES = ("LONG", "SHORT", "CH_LONG", "CH_SHORT")
MSG_TEMPLATE_TYPES = ("price", "analysis")


def _norm_digits(s: str) -> str:
    return (s or "").translate(
        str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
    )


def ts_str(v: Any) -> str:
    """زمان دیتابیس (رشته‌ی SQLite یا datetime در PostgreSQL) -> 'YYYY-MM-DD HH:MM:SS'."""
    if v is None:
        return ""
    if isinstance(v, datetime):
        if v.tzinfo is not None:
            v = v.astimezone(timezone.utc).replace(tzinfo=None)
        return v.strftime("%Y-%m-%d %H:%M:%S")
    return str(v)[:19]


def utc_cutoff(*, hours: float = 0, days: float = 0) -> str:
    """رشته‌ی زمان UTC به فاصله‌ی مشخص قبل از اکنون (برای مقایسه با ستون‌های زمان)."""
    t = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=hours, days=days)
    return t.strftime("%Y-%m-%d %H:%M:%S")


def normalize_url(raw: str) -> str:
    """آدرس دیتابیس را برای SQLAlchemy آماده می‌کند."""
    url = (raw or "").strip() or "sqlite:///data/bot.db"
    if url.startswith("postgres://"):
        url = "postgresql+psycopg2://" + url[len("postgres://"):]
    elif url.startswith("postgresql://"):
        url = "postgresql+psycopg2://" + url[len("postgresql://"):]
    if url.startswith("sqlite:///") and not url.startswith("sqlite:////") and ":memory:" not in url:
        rel = url[len("sqlite:///"):]
        if not (os.path.isabs(rel) or re.match(r"^[A-Za-z]:[\\/]", rel)):
            url = "sqlite:///" + str(config.BASE_DIR / rel)
    if url.startswith("postgresql"):
        u = make_url(url)
        query = {k: v for k, v in u.query.items() if k not in ("pgbouncer", "connection_limit", "schema")}
        url = u.set(query=query).render_as_string(hide_password=False)
    return url


def _bind(sql: str, params: Any):
    """? -> :p0 :p1 ... (یا پارامترهای نام‌دار)"""
    if isinstance(params, dict):
        return text(sql), params
    params = tuple(params or ())
    out: List[str] = []
    n = 0
    for ch in sql:
        if ch == "?":
            out.append(f":p{n}")
            n += 1
        else:
            out.append(ch)
    return text("".join(out)), {f"p{i}": v for i, v in enumerate(params)}


class _Result:
    __slots__ = ("rows", "rowcount", "lastrowid")

    def __init__(self, rows: List[Dict[str, Any]], rowcount: int, lastrowid: Optional[int]):
        self.rows, self.rowcount, self.lastrowid = rows, rowcount, lastrowid

    def fetchone(self) -> Optional[Dict[str, Any]]:
        return self.rows[0] if self.rows else None

    def fetchall(self) -> List[Dict[str, Any]]:
        return list(self.rows)

    def __iter__(self) -> Iterator[Dict[str, Any]]:
        return iter(self.rows)


class _Exec:
    """اجرای کوئری: بدون conn هر دستور تراکنش مستقل (Autocommit)، با conn داخل یک تراکنش."""

    def __init__(self, engine, pg: bool, conn=None):
        self._engine, self._pg, self._conn = engine, pg, conn

    @staticmethod
    def _run(conn, sql: str, params: Any = (), many: bool = False) -> _Result:
        if many:
            plist = [_bind(sql, p)[1] for p in params]
            if not plist:
                return _Result([], 0, None)
            res = conn.execute(_bind(sql, ())[0], plist)
        else:
            stmt, bind = _bind(sql, params)
            res = conn.execute(stmt, bind)
        rows = [dict(r) for r in res.mappings().all()] if res.returns_rows else []
        try:
            lid = res.lastrowid
        except Exception:
            lid = None
        rc = res.rowcount if res.rowcount is not None else -1
        return _Result(rows, rc, lid)

    def execute(self, sql: str, params: Any = ()) -> _Result:
        if self._conn is not None:
            return self._run(self._conn, sql, params)
        with self._engine.begin() as conn:
            return self._run(conn, sql, params)

    def executemany(self, sql: str, seq: Sequence[Any]) -> _Result:
        if self._conn is not None:
            return self._run(self._conn, sql, seq, many=True)
        with self._engine.begin() as conn:
            return self._run(conn, sql, seq, many=True)

    def insert_id(self, sql: str, params: Any = ()) -> int:
        """INSERT را اجرا می‌کند و id ردیف جدید را برمی‌گرداند (جدول باید ستون id داشته باشد)."""
        if self._pg:
            r = self.execute(sql + " RETURNING id", params)
            return int(r.rows[0]["id"]) if r.rows else 0
        return int(self.execute(sql, params).lastrowid or 0)

    @contextmanager
    def transaction(self) -> Iterator["_Exec"]:
        if self._conn is not None:
            yield self
            return
        with self._engine.begin() as conn:
            yield _Exec(self._engine, self._pg, conn)


class Database:
    def __init__(self, url: str):
        self.raw_url = url
        self._lock = threading.RLock()
        self.engine = None
        self.conn: Optional[_Exec] = None
        self.dialect = "sqlite"
        self.NOW = "CURRENT_TIMESTAMP"

        self._settings: Dict[str, str] = {}
        self._settings_version = 0
        self._verified: Set[int] = set()
        self._channels: Optional[List[Channel]] = None
        self._templates: Dict[str, str] = {}
        self._msg_templates: Dict[str, str] = {}
        self._pre_off: Set[int] = set()
        self._blocks_active: Dict[str, List[str]] = {}
        self._cat_state: Dict[str, bool] = {}
        self._sym_state: Dict[str, bool] = {}
        self._sub_until: Dict[int, float] = {}
        self._blocked: Dict[int, Tuple[float, str]] = {}
        self._ai_keys: Dict[int, AIKey] = {}
        self._ucfg: Dict[int, Dict[str, str]] = {}                       # تنظیمات کاربران (بارگذاری تنبل)
        self._ufilt: Dict[int, Tuple[Dict[str, bool], Dict[str, bool]]] = {}   # فیلتر نمادهای کاربران
        self._models_cache: Optional[Tuple[str, List[str]]] = None

    # ------------------------------------------------------------------
    # راه‌اندازی
    # ------------------------------------------------------------------
    @property
    def is_pg(self) -> bool:
        return self.dialect == "postgresql"

    def _schema_statements(self) -> List[str]:
        pg = self.is_pg
        tokens = {
            "{ID}": "BIGSERIAL PRIMARY KEY" if pg else "INTEGER PRIMARY KEY",
            "{BIG}": "BIGINT" if pg else "INTEGER",
            "{DBL}": "DOUBLE PRECISION",
            "{TS}": "TIMESTAMP DEFAULT (NOW() AT TIME ZONE 'UTC')" if pg else "TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
            "{TSN}": "TIMESTAMP",
        }
        sql = SCHEMA
        for k, v in tokens.items():
            sql = sql.replace(k, v)
        return [s.strip() for s in sql.split(";") if s.strip()]

    def init(self) -> None:
        url = normalize_url(self.raw_url)
        self.dialect = "postgresql" if url.startswith("postgresql") else "sqlite"
        if self.is_pg:
            self.NOW = "(NOW() AT TIME ZONE 'UTC')"
            self.engine = create_engine(
                url, pool_pre_ping=True, pool_size=5, max_overflow=5, pool_recycle=900,
            )
        else:
            path = make_url(url).database
            if path and path != ":memory:":
                os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            self.engine = create_engine(
                url, pool_pre_ping=True,
                connect_args={"check_same_thread": False, "timeout": 30},
            )

            @event.listens_for(self.engine, "connect")
            def _sqlite_pragmas(dbapi_conn, _rec):  # فقط SQLite
                cur = dbapi_conn.cursor()
                try:
                    cur.execute("PRAGMA journal_mode=WAL")
                    cur.execute("PRAGMA synchronous=NORMAL")
                    cur.execute("PRAGMA temp_store=MEMORY")
                    cur.execute("PRAGMA cache_size=-16000")
                    cur.execute("PRAGMA foreign_keys=OFF")
                finally:
                    cur.close()

        self.conn = _Exec(self.engine, self.is_pg)
        with self._lock:
            with self.conn.transaction() as tx:
                for stmt in self._schema_statements():
                    tx.execute(stmt)
            self._migrate_columns()
            self._migrate_payments()
            fresh = int(self.conn.execute("SELECT COUNT(*) AS n FROM settings").fetchone()["n"]) == 0
            # تنظیمات پیش‌فرض (فقط کلیدهای جدید اضافه می‌شوند)
            self.conn.executemany(
                "INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO NOTHING",
                list(config.DEFAULT_SETTINGS.items()),
            )
            # قالب پیش‌فرض قدیمی اشتراک <code> داشت که escape می‌شد و خام نمایش داده می‌شد
            new_sub = config.DEFAULT_SETTINGS["sub_template"]
            self.conn.execute(
                "UPDATE settings SET value = ? WHERE key = ? AND value = ?",
                (new_sub, "sub_template", new_sub.replace("`{wallet}`", "<code>{wallet}</code>")),
            )
            self._migrate_defaults(fresh)
            self._seed_templates(fresh)
            # پاکسازی تاریخچه قدیمی و بلاک‌های تمام‌شده
            self.conn.execute("DELETE FROM signal_history WHERE created_at < ?", (utc_cutoff(days=30),))
            self.conn.execute('DELETE FROM blocked_users WHERE "until" <= ?', (time.time(),))
        self._load_caches()
        log.info("database ready (%s)", self.dialect)

    # ---------------- مهاجرت ----------------
    def table_columns(self, table: str) -> List[str]:
        try:
            return [c["name"] for c in inspect(self.engine).get_columns(table)]
        except Exception:
            return []

    def _add_column(self, table: str, column: str, ddl: str) -> None:
        if column not in self.table_columns(table):
            self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")

    def _migrate_columns(self) -> None:
        self._add_column("force_channels", "invite_link", "TEXT")
        self._add_column("users", "pre_alert", "INTEGER DEFAULT 1")
        self._add_column("users", "sub_until", "DOUBLE PRECISION DEFAULT 0")
        self._add_column("users", "sub_notice", "DOUBLE PRECISION DEFAULT 0")
        self._add_column("users", "first_name", "TEXT DEFAULT ''")
        self._add_column("users", "last_name", "TEXT DEFAULT ''")
        self._add_column("users", "username", "TEXT DEFAULT ''")
        self._add_column("users", "webapp_sub_until", "DOUBLE PRECISION DEFAULT 0")
        self._add_column("payments_history", "service_type", "TEXT NOT NULL DEFAULT 'bot'")
        self._add_column("payments_history", "network", "TEXT DEFAULT ''")
        self._add_column("sub_requests", "service_type", "TEXT NOT NULL DEFAULT 'bot'")
        # دوره‌ی رایگان وب‌اپ می‌تواند بر مبنای دسته‌بندی انتخاب شود؛ انتخاب خام دسته‌ها اینجا نگه داشته می‌شود.
        self._add_column("free_sub_claims", "categories", "TEXT DEFAULT ''")
        self._add_column("webapp_users", "password_lookup", "TEXT DEFAULT ''")
        self._add_column("webapp_users", "username", "TEXT DEFAULT ''")
        self._add_column("webapp_users", "email", "TEXT DEFAULT ''")
        self._add_column("webapp_users", "phone", "TEXT DEFAULT ''")
        self._add_column("webapp_users", "first_name", "TEXT DEFAULT ''")
        self._add_column("webapp_users", "last_name", "TEXT DEFAULT ''")
        self._add_column("webapp_users", "updated_at", "TIMESTAMP")
        self._add_column("security_events", "severity", "TEXT NOT NULL DEFAULT 'info'")
        self._add_column("security_events", "resolved", "INTEGER NOT NULL DEFAULT 0")
        self._add_column("ai_keys", "account_id", "TEXT DEFAULT ''")
        self._add_column("signal_performance", "first_tp_at", "DOUBLE PRECISION")
        # سیگنال‌ها / عملکرد / پیشنهادات مشاور: هر رکورد متعلق به یک کاربر است (0 = ادمین / کانال)
        self._add_column("signal_history", "user_id", "BIGINT DEFAULT 0")
        self._add_column("signal_performance", "user_id", "BIGINT DEFAULT 0")
        self._add_column("engine_suggestions", "user_id", "BIGINT DEFAULT 0")
        self._add_column("analysis_requests", "kind", "TEXT DEFAULT 'single'")
        self._add_column("support_tickets", "ai_state", "TEXT DEFAULT ''")
        for stmt in (
            "CREATE INDEX IF NOT EXISTS idx_signal_history_user ON signal_history(user_id, symbol, created_at)",
            "CREATE INDEX IF NOT EXISTS idx_signal_perf_user ON signal_performance(user_id, status, id)",
            "CREATE INDEX IF NOT EXISTS idx_engine_sugg_user ON engine_suggestions(user_id, id)",
            "CREATE INDEX IF NOT EXISTS idx_payments_service ON payments_history(service_type, status, id)",
        ):
            self.conn.execute(stmt)
        # کاربران قدیمی که فقط رمز داشتند، نام کاربری قابل حدس و یکتا می‌گیرند
        self.conn.execute(
            "UPDATE webapp_users SET username = 'user_' || CAST(user_id AS TEXT) "
            "WHERE (username IS NULL OR TRIM(username) = '') AND password_hash <> ''"
        )
        try:
            self.conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_webapp_username "
                "ON webapp_users(LOWER(username)) WHERE username <> ''"
            )
        except Exception:
            log.warning("unique username index creation skipped", exc_info=True)
        # «مخاطب ارسال» فقط برای مدیر معنا داشت و از تنظیمات کاربران حذف شد
        self.conn.execute("DELETE FROM webapp_user_settings WHERE key = 'notify_target'")

    def _migrate_payments(self) -> None:
        """تراکنش‌های قدیمی جدول bsc_payments یک بار به تاریخچه‌ی پرداخت‌ها منتقل می‌شوند (تکراری‌ها نادیده گرفته می‌شوند)."""
        try:
            self.conn.execute(
                "INSERT INTO payments_history(user_id, amount, expected_amount, status, method, tx_hash, created_at, admin_note) "
                "SELECT b.user_id, b.amount, b.amount, 'approved', 'bsc', LOWER(b.tx_hash), b.created_at, "
                "'منتقل‌شده از سیستم قدیمی' FROM bsc_payments b "
                "WHERE b.tx_hash IS NOT NULL AND NOT EXISTS "
                "(SELECT 1 FROM payments_history p WHERE p.tx_hash = LOWER(b.tx_hash))"
            )
        except Exception:
            log.warning("legacy bsc payments migration skipped", exc_info=True)

    def _migrate_defaults(self, fresh: bool) -> None:
        """تغییر پیش‌فرض موتورها (مثل Squeeze قبل از انفجار) فقط یک بار روی دیتابیس‌های قدیمی اعمال می‌شود."""
        row = self.conn.execute("SELECT value FROM settings WHERE key = ?", ("defaults_ver",)).fetchone()
        cur = int(float((row or {}).get("value") or 0))
        if not fresh:
            for ver in sorted(v for v in config.DEFAULTS_MIGRATION if v > cur):
                for k, v in config.DEFAULTS_MIGRATION[ver].items():
                    self.conn.execute(
                        "INSERT INTO settings(key, value) VALUES(?, ?) "
                        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                        (k, v),
                    )
                    for side in ("LONG", "SHORT"):
                        self.conn.execute("UPDATE settings SET value = ? WHERE key = ?", (v, f"{k}@{side}"))
        self.conn.execute(
            "UPDATE settings SET value = ? WHERE key = ?", (str(config.DEFAULTS_VERSION), "defaults_ver")
        )

    def _seed_templates(self, fresh: bool) -> None:
        """قالب‌های پیش‌فرض سیگنال (ربات/کانال) و قالب‌های قیمت/آنالیز؛ قالب‌های خالی قدیمی فقط یک بار پر می‌شوند."""
        for t in TEMPLATE_TYPES:
            self.conn.execute(
                "INSERT INTO templates(template_type, content, is_active) VALUES(?, ?, 1) "
                "ON CONFLICT(template_type) DO NOTHING",
                (t, config.DEFAULT_SIGNAL_TEMPLATES[t]),
            )
        ver_row = self.conn.execute("SELECT value FROM settings WHERE key = ?", ("tpl_defaults_ver",)).fetchone()
        if not fresh and int(float((ver_row or {}).get("value") or 0)) < 1:
            for t in TEMPLATE_TYPES:
                self.conn.execute(
                    "UPDATE templates SET content = ? "
                    "WHERE template_type = ? AND (content IS NULL OR TRIM(content) = '')",
                    (config.DEFAULT_SIGNAL_TEMPLATES[t], t),
                )
        self.conn.execute("UPDATE settings SET value = ? WHERE key = ?", ("1", "tpl_defaults_ver"))
        for t in MSG_TEMPLATE_TYPES:
            self.conn.execute(
                "INSERT INTO message_templates(template_type, template_text) VALUES(?, ?) "
                "ON CONFLICT(template_type) DO NOTHING",
                (t, config.DEFAULT_MSG_TEMPLATES[t]),
            )

    def _load_caches(self) -> None:
        with self._lock:
            self._settings = {r["key"]: r["value"] for r in self.conn.execute("SELECT key, value FROM settings")}
            self._verified = {
                int(r["user_id"]) for r in self.conn.execute("SELECT user_id FROM users WHERE is_verified = 1")
            }
            self._templates = {
                r["template_type"]: (r["content"] or "")
                for r in self.conn.execute("SELECT template_type, content FROM templates WHERE is_active = 1")
            }
            self._msg_templates = {
                r["template_type"]: (r["template_text"] or "")
                for r in self.conn.execute("SELECT template_type, template_text FROM message_templates")
            }
            self._pre_off = {
                int(r["user_id"]) for r in self.conn.execute("SELECT user_id FROM users WHERE pre_alert = 0")
            }
            self._channels = None
            self._rebuild_blocks()
            self._cat_state, self._sym_state = {}, {}
            for r in self.conn.execute("SELECT kind, name, enabled FROM symbol_filter"):
                (self._cat_state if r["kind"] == "cat" else self._sym_state)[r["name"]] = bool(r["enabled"])
            self._sub_until = {
                int(r["user_id"]): float(r["sub_until"])
                for r in self.conn.execute("SELECT user_id, sub_until FROM users WHERE sub_until > 0")
            }
            self._blocked = {
                int(r["user_id"]): (float(r["until"]), r["reason"] or "")
                for r in self.conn.execute('SELECT user_id, "until", reason FROM blocked_users')
            }
            self._ai_keys = {
                int(r["slot"]): self._ai_key(r) for r in self.conn.execute("SELECT * FROM ai_keys")
            }
            self._ucfg, self._ufilt = {}, {}

    def reload_caches(self) -> None:
        """بعد از Restore یا تغییر مستقیم دیتابیس: کش‌ها دوباره ساخته می‌شوند و کش تحلیل باطل می‌شود."""
        self._load_caches()
        self._settings_version += 1

    def close(self) -> None:
        with self._lock:
            if self.engine is not None:
                try:
                    self.engine.dispose()
                finally:
                    self.engine = None
                    self.conn = None

    # ------------------------------------------------------------------
    # کوئری عمومی (برای ماژول‌های دیگر)
    # ------------------------------------------------------------------
    def q(self, sql: str, params: Any = ()) -> List[Dict[str, Any]]:
        """SELECT: لیست دیکشنری."""
        return self.conn.execute(sql, params).rows

    def q1(self, sql: str, params: Any = ()) -> Optional[Dict[str, Any]]:
        rows = self.conn.execute(sql, params).rows
        return rows[0] if rows else None

    def x(self, sql: str, params: Any = ()) -> _Result:
        """INSERT / UPDATE / DELETE: تعداد ردیف‌های تأثیرگرفته در .rowcount"""
        return self.conn.execute(sql, params)

    def insert_id(self, sql: str, params: Any = ()) -> int:
        return self.conn.insert_id(sql, params)

    def transaction(self):
        return self.conn.transaction()

    def reset_sequences(self, tables: Sequence[str] = SERIAL_TABLES) -> None:
        """بعد از Restore با idهای مشخص، Sequence جدول‌ها در PostgreSQL به آخرین id می‌رسد."""
        if not self.is_pg:
            return
        for t in tables:
            self.conn.execute(
                f"SELECT setval(pg_get_serial_sequence('{t}', 'id'), COALESCE((SELECT MAX(id) FROM {t}), 1), "
                f"(SELECT MAX(id) FROM {t}) IS NOT NULL)"
            )

    # ------------------------------------------------------------------
    # تنظیمات
    # ------------------------------------------------------------------
    @property
    def settings_version(self) -> int:
        return self._settings_version

    def get(self, key: str) -> str:
        v = self._settings.get(key)
        if v is None:
            v = config.DEFAULT_SETTINGS.get(key, "")
        return v

    def get_int(self, key: str) -> int:
        try:
            return int(float(self.get(key)))
        except (TypeError, ValueError):
            return int(float(config.DEFAULT_SETTINGS.get(key, "0") or 0))

    def get_float(self, key: str) -> float:
        try:
            return float(self.get(key))
        except (TypeError, ValueError):
            return float(config.DEFAULT_SETTINGS.get(key, "0") or 0)

    def get_bool(self, key: str) -> bool:
        return self.get(key) in ("1", "true", "True")

    def set(self, key: str, value: str) -> None:
        value = str(value)
        with self._lock:
            self.conn.execute(
                "INSERT INTO settings(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
            self._settings[key] = value
            # فقط تنظیماتی که روی تحلیل اثر دارند کش تحلیل را باطل می‌کنند
            if key.split("@", 1)[0] in config.ANALYSIS_KEYS:
                self._settings_version += 1

    def toggle(self, key: str) -> bool:
        new = not self.get_bool(key)
        self.set(key, "1" if new else "0")
        return new

    def set_many(self, items: Dict[str, str]) -> None:
        """چند تنظیم با هم و در یک تراکنش (برای اعمال استراتژی / پیشنهادات)."""
        items = {k: str(v) for k, v in items.items()}
        if not items:
            return
        with self._lock:
            with self.conn.transaction() as tx:
                tx.executemany(
                    "INSERT INTO settings(key, value) VALUES(?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    list(items.items()),
                )
            self._settings.update(items)
            if any(k.split("@", 1)[0] in config.ANALYSIS_KEYS for k in items):
                self._settings_version += 1

    # ------------------------------------------------------------------
    # تنظیمات موتورها (حالت مشترک / جداگانه برای LONG و SHORT)
    # ------------------------------------------------------------------
    def eng_mode(self) -> str:
        return "separate" if self.get("eng_mode") == "separate" else "global"

    def _ek(self, key: str, side: Optional[str]) -> str:
        """کلید واقعی ذخیره‌سازی: در حالت جداگانه برای هر جهت یک کلید با پسوند @LONG / @SHORT."""
        if side and (config.is_engine_key(key) or key in config.USER_TF_KEYS) and self.eng_mode() == "separate":
            # تنظیمات AI اگر «مشترک» انتخاب شده باشد حتی تو حالت جداگانه از نسخه‌ی مشترک خوانده/نوشته می‌شوند
            if key in config.AI_KEYS and not self.get_bool("ai_sep"):
                return key
            return f"{key}@{side.upper()}"
        return key

    def get_e(self, key: str, side: Optional[str] = None) -> str:
        v = self._settings.get(self._ek(key, side))
        return v if v is not None else self.get(key)

    def get_e_int(self, key: str, side: Optional[str] = None) -> int:
        try:
            return int(float(self.get_e(key, side)))
        except (TypeError, ValueError):
            return int(float(config.DEFAULT_SETTINGS.get(key, "0") or 0))

    def get_e_float(self, key: str, side: Optional[str] = None) -> float:
        try:
            return float(self.get_e(key, side))
        except (TypeError, ValueError):
            return float(config.DEFAULT_SETTINGS.get(key, "0") or 0)

    def get_e_bool(self, key: str, side: Optional[str] = None) -> bool:
        return self.get_e(key, side) in ("1", "true", "True")

    def set_e(self, key: str, value: str, side: Optional[str] = None) -> None:
        self.set(self._ek(key, side), value)

    def set_many_e(self, items: Sequence[Tuple[str, str, Optional[str]]]) -> None:
        """(کلید، مقدار، جهت) چندتایی؛ کلید واقعی بسته به حالت مشترک/جداگانه تعیین می‌شود."""
        self.set_many({self._ek(k, side): v for k, v, side in items})

    def toggle_e(self, key: str, side: Optional[str] = None) -> bool:
        new = not self.get_e_bool(key, side)
        self.set_e(key, "1" if new else "0", side)
        return new

    def get_profile(self, key: str, side: Optional[str]) -> str:
        """مقدار کلید در پروفایل یک جهت (مستقل از حالت فعلی)؛ اگر نسخه‌ی جداگانه نبود مقدار مشترک."""
        if side:
            v = self._settings.get(f"{key}@{side.upper()}")
            if v is not None:
                return v
        return self.get(key)

    def ensure_side_profiles(self) -> None:
        """موقع رفتن به حالت جداگانه: نسخه‌ی LONG/SHORT هر کلیدی که ندارد از روی مقدار مشترک ساخته می‌شود."""
        for key in sorted(config.ENGINE_KEYS | config.USER_TF_KEYS):
            for side in ("LONG", "SHORT"):
                sk = f"{key}@{side}"
                if sk not in self._settings:
                    self.set(sk, self.get(key))

    # ------------------------------------------------------------------
    # کاربران
    # ------------------------------------------------------------------
    @staticmethod
    def _user(r: Dict[str, Any]) -> User:
        return User(
            user_id=int(r["user_id"]),
            phone_number=r["phone_number"],
            is_verified=bool(r["is_verified"]),
            joined_at=ts_str(r["joined_at"]),
            pre_alert=bool(r["pre_alert"]) if r["pre_alert"] is not None else True,
            sub_until=float(r["sub_until"] or 0.0),
        )

    def ensure_user(self, uid: int) -> None:
        with self._lock:
            self.conn.execute("INSERT INTO users(user_id) VALUES(?) ON CONFLICT DO NOTHING", (uid,))

    def touch_user(self, uid: int, first_name: str = "", last_name: str = "", username: str = "") -> None:
        """ثبت/به‌روزرسانی مشخصات نمایشی تلگرام برای پنل مدیریت وب‌اپ."""
        if not uid:
            return
        with self._lock:
            with self.conn.transaction() as tx:
                tx.execute("INSERT INTO users(user_id) VALUES(?) ON CONFLICT DO NOTHING", (uid,))
                if first_name or last_name or username:
                    tx.execute(
                        "UPDATE users SET first_name = ?, last_name = ?, username = ? WHERE user_id = ?",
                        ((first_name or "")[:128], (last_name or "")[:128], (username or "")[:64], uid),
                    )

    def webapp_sub_until(self, uid: int) -> float:
        with self._lock:
            r = self.conn.execute("SELECT webapp_sub_until FROM users WHERE user_id = ?", (uid,)).fetchone()
        return float((r or {}).get("webapp_sub_until") or 0.0)

    def has_active_webapp_sub(self, uid: int) -> bool:
        return self.webapp_sub_until(uid) > time.time()

    def grant_webapp_sub(self, uid: int, days: int) -> float:
        """افزودن روز به اشتراک وب‌اپ از «همین حالا» یا از پایان اشتراک فعلی."""
        with self._lock:
            now = time.time()
            cur = self.webapp_sub_until(uid)
            until = (cur if cur > now else now) + max(1, int(days)) * 86400.0
            with self.conn.transaction() as tx:
                tx.execute("INSERT INTO users(user_id) VALUES(?) ON CONFLICT DO NOTHING", (uid,))
                tx.execute("UPDATE users SET webapp_sub_until = ? WHERE user_id = ?", (until, uid))
        return until

    def reduce_webapp_sub(self, uid: int, days: int) -> Optional[float]:
        with self._lock:
            cur = self.webapp_sub_until(uid)
            if cur <= 0:
                return None
            until = max(0.0, cur - max(1, int(days)) * 86400.0)
            with self.conn.transaction() as tx:
                tx.execute("UPDATE users SET webapp_sub_until = ? WHERE user_id = ?", (until, uid))
        return until

    def remove_webapp_sub(self, uid: int) -> bool:
        with self._lock:
            cur = self.webapp_sub_until(uid)
            with self.conn.transaction() as tx:
                tx.execute("UPDATE users SET webapp_sub_until = 0 WHERE user_id = ?", (uid,))
        return cur > 0

    def set_webapp_sub_until(self, uid: int, until: float) -> None:
        """زمان پایان اشتراک وب‌اپ را مستقیم تنظیم می‌کند (برای بازگردانی وضعیت پس از دوره‌ی رایگان)."""
        until = max(0.0, float(until))
        with self._lock:
            self.conn.execute("INSERT INTO users(user_id) VALUES(?) ON CONFLICT DO NOTHING", (uid,))
            self.conn.execute(
                "UPDATE users SET webapp_sub_until = ? WHERE user_id = ?", (until, uid)
            )

    def get_user(self, uid: int) -> Optional[User]:
        with self._lock:
            r = self.conn.execute("SELECT * FROM users WHERE user_id = ?", (uid,)).fetchone()
        return self._user(r) if r else None

    def is_verified(self, uid: int) -> bool:
        return uid in self._verified

    def verify_user(self, uid: int, phone: str) -> bool:
        """شماره فقط یک بار ثبت می‌شود. اگر قبلاً تأیید شده باشد False برمی‌گرداند."""
        with self._lock:
            if uid in self._verified:
                return False
            with self.conn.transaction() as tx:
                tx.execute("INSERT INTO users(user_id) VALUES(?) ON CONFLICT DO NOTHING", (uid,))
                tx.execute("UPDATE users SET phone_number = ?, is_verified = 1 WHERE user_id = ?", (phone, uid))
            self._verified.add(uid)
            return True

    def delete_user(self, uid: int) -> bool:
        with self._lock:
            with self.conn.transaction() as tx:
                cur = tx.execute("DELETE FROM users WHERE user_id = ?", (uid,))
                tx.execute("DELETE FROM sub_requests WHERE user_id = ?", (uid,))
                tx.execute("DELETE FROM user_settings WHERE user_id = ?", (uid,))
                tx.execute("DELETE FROM user_symbol_filter WHERE user_id = ?", (uid,))
                tx.execute("DELETE FROM signal_performance WHERE user_id = ?", (uid,))
                tx.execute("DELETE FROM engine_suggestions WHERE user_id = ?", (uid,))
                tx.execute("DELETE FROM signal_history WHERE user_id = ?", (uid,))
                tx.execute("DELETE FROM webapp_users WHERE user_id = ?", (uid,))
                tx.execute("DELETE FROM webapp_user_settings WHERE user_id = ?", (uid,))
                tx.execute("DELETE FROM webapp_favorites WHERE user_id = ?", (uid,))
                tx.execute("DELETE FROM search_history WHERE user_id = ?", (uid,))
            self._ucfg.pop(uid, None)
            self._ufilt.pop(uid, None)
            self._verified.discard(uid)
            self._pre_off.discard(uid)
            self._sub_until.pop(uid, None)
            return cur.rowcount > 0

    def count_users(self) -> Tuple[int, int]:
        with self._lock:
            r = self.conn.execute(
                "SELECT COUNT(*) AS t, COALESCE(SUM(is_verified), 0) AS v FROM users"
            ).fetchone()
        return int(r["t"]), int(r["v"])

    def count_vip(self) -> int:
        with self._lock:
            r = self.conn.execute(
                "SELECT COUNT(*) AS n FROM users WHERE sub_until > ?", (time.time(),)
            ).fetchone()
        return int(r["n"])

    def list_users(self, offset: int, limit: int) -> List[User]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM users ORDER BY joined_at DESC, user_id DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return [self._user(r) for r in rows]

    def search_users(self, query: str, limit: int = 10) -> List[User]:
        q = _norm_digits((query or "").strip()).lstrip("+")
        if not q:
            return []
        phone_q = q[1:] if q.startswith("0") and len(q) > 1 else q

        def like(s: str) -> str:
            return "%" + s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"

        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM users WHERE CAST(user_id AS TEXT) LIKE ? ESCAPE '\\' "
                "OR phone_number LIKE ? ESCAPE '\\' ORDER BY joined_at DESC LIMIT ?",
                (like(q), like(phone_q), limit),
            ).fetchall()
        return [self._user(r) for r in rows]

    def all_verified_ids(self) -> List[int]:
        return list(self._verified)

    def user_ids(self, verified_only: bool = False) -> List[int]:
        with self._lock:
            q = "SELECT user_id FROM users" + (" WHERE is_verified = 1" if verified_only else "")
            return [int(r["user_id"]) for r in self.conn.execute(q + " ORDER BY user_id")]

    def user_ids_by(self, kind: str) -> List[int]:
        """گروه‌های هدف پیام: all همه | ver ثبت‌نام کامل | vip اشتراک فعال | nosub بدون اشتراک فعال."""
        now = time.time()
        with self._lock:
            if kind == "ver":
                rows = self.conn.execute("SELECT user_id FROM users WHERE is_verified = 1 ORDER BY user_id")
            elif kind == "vip":
                rows = self.conn.execute(
                    "SELECT user_id FROM users WHERE sub_until > ? ORDER BY user_id", (now,)
                )
            elif kind == "nosub":
                rows = self.conn.execute(
                    "SELECT user_id FROM users WHERE sub_until <= ? ORDER BY user_id", (now,)
                )
            else:
                rows = self.conn.execute("SELECT user_id FROM users ORDER BY user_id")
            return [int(r["user_id"]) for r in rows]

    # ---------------- هشدار قبل از سیگنال (انتخاب کاربر) ----------------
    def set_pre_alert(self, uid: int, on: bool) -> None:
        with self._lock:
            with self.conn.transaction() as tx:
                tx.execute("INSERT INTO users(user_id) VALUES(?) ON CONFLICT DO NOTHING", (uid,))
                tx.execute("UPDATE users SET pre_alert = ? WHERE user_id = ?", (1 if on else 0, uid))
            if on:
                self._pre_off.discard(uid)
            else:
                self._pre_off.add(uid)

    def pre_alert_enabled(self, uid: int) -> bool:
        return uid not in self._pre_off

    def pre_alert_ids(self) -> List[int]:
        with self._lock:
            return [
                int(r["user_id"])
                for r in self.conn.execute(
                    "SELECT user_id FROM users WHERE is_verified = 1 AND COALESCE(pre_alert, 1) = 1"
                )
            ]

    # ---------------- اشتراک ----------------
    def sub_until(self, uid: int) -> float:
        return self._sub_until.get(uid, 0.0)

    def has_active_sub(self, uid: int) -> bool:
        return self._sub_until.get(uid, 0.0) > time.time()

    def grant_sub(self, uid: int, days: int) -> float:
        """اشتراک را `days` روز تمدید می‌کند (اگر هنوز فعال است از روز پایانش ادامه می‌دهد). خروجی: زمان پایان."""
        now = time.time()
        with self._lock:
            base = max(now, self._sub_until.get(uid, 0.0))
            until = base + max(1, int(days)) * 86400.0
            with self.conn.transaction() as tx:
                tx.execute("INSERT INTO users(user_id) VALUES(?) ON CONFLICT DO NOTHING", (uid,))
                tx.execute("UPDATE users SET sub_until = ?, sub_notice = 0 WHERE user_id = ?", (until, uid))
            self._sub_until[uid] = until
            return until

    def set_sub_until(self, uid: int, until: float) -> None:
        """زمان پایان اشتراک ربات را مستقیم تنظیم می‌کند (برای بازگردانی وضعیت پس از دوره‌ی رایگان)."""
        now = time.time()
        until = max(0.0, float(until))
        with self._lock:
            self.conn.execute("INSERT INTO users(user_id) VALUES(?) ON CONFLICT DO NOTHING", (uid,))
            self.conn.execute(
                "UPDATE users SET sub_until = ?, sub_notice = 0 WHERE user_id = ?", (until, uid)
            )
            if until > now:
                self._sub_until[uid] = until
            else:
                self._sub_until.pop(uid, None)

    def reduce_sub(self, uid: int, days: int) -> Optional[float]:
        """از اشتراک فعال `days` روز کم می‌کند. خروجی: زمان پایان جدید، یا None اگر اشتراک فعال نداشت.
        اگر نتیجه از اکنون گذشته باشد اشتراک به پایان می‌رسد (بدون پیام پایان اشتراک)."""
        now = time.time()
        with self._lock:
            cur = self._sub_until.get(uid, 0.0)
            if cur <= now:
                return None
            until = cur - max(1, int(days)) * 86400.0
            if until <= now:
                until = now
                notice = until
            else:
                notice = 0.0
            self.conn.execute(
                "UPDATE users SET sub_until = ?, sub_notice = ? WHERE user_id = ?", (until, notice, uid)
            )
            self._sub_until[uid] = until
            return until

    def remove_sub(self, uid: int) -> bool:
        """اشتراک کاربر از بین می‌رود (خود کاربر حذف نمی‌شود)."""
        with self._lock:
            had = self._sub_until.get(uid, 0.0) > 0
            self.conn.execute("UPDATE users SET sub_until = 0, sub_notice = 0 WHERE user_id = ?", (uid,))
            self._sub_until.pop(uid, None)
            return had

    def extend_active_subs(self, days: int) -> List[int]:
        """به همه‌ی کاربرهای دارای اشتراک فعال `days` روز اضافه می‌شود. خروجی: آیدی کاربرهای تغییرکرده."""
        now = time.time()
        add = max(1, int(days)) * 86400.0
        with self._lock:
            ids = [
                int(r["user_id"])
                for r in self.conn.execute("SELECT user_id FROM users WHERE sub_until > ?", (now,))
            ]
            if not ids:
                return []
            self.conn.execute(
                "UPDATE users SET sub_until = sub_until + ?, sub_notice = 0 WHERE sub_until > ?", (add, now)
            )
            for uid in ids:
                self._sub_until[uid] = self._sub_until.get(uid, now) + add
            return ids

    def expired_unnotified(self) -> List[Tuple[int, float]]:
        """کاربرانی که اشتراک آن‌ها به پایان رسیده و هنوز پیام پایان اشتراک دریافت نکرده‌اند."""
        with self._lock:
            rows = self.conn.execute(
                "SELECT user_id, sub_until FROM users "
                "WHERE sub_until > 0 AND sub_until <= ? AND sub_notice != sub_until",
                (time.time(),),
            ).fetchall()
        return [(int(r["user_id"]), float(r["sub_until"])) for r in rows]

    def mark_sub_notified(self, uid: int, until: float) -> None:
        with self._lock:
            self.conn.execute("UPDATE users SET sub_notice = ? WHERE user_id = ?", (until, uid))

    # ---------------- درخواست‌های خرید اشتراک ----------------
    @staticmethod
    def _sub_req(r: Dict[str, Any]) -> SubRequest:
        return SubRequest(
            id=int(r["id"]),
            user_id=int(r["user_id"]),
            text=r["text"] or "",
            photo_id=r["photo_id"] or "",
            status=r["status"] or "pending",
            reason=r["reason"] or "",
            created_at=ts_str(r["created_at"]),
            service_type=r.get("service_type") or "bot",
        )

    def add_sub_request(self, uid: int, text_: str, photo_id: str, service_type: str = "bot") -> int:
        with self._lock:
            return self.conn.insert_id(
                "INSERT INTO sub_requests(user_id, text, photo_id, status, service_type) VALUES(?, ?, ?, 'pending', ?)",
                (uid, text_, photo_id, service_type if service_type in ("bot", "webapp") else "bot"),
            )

    def pending_request_of(self, uid: int, service_type: Optional[str] = "bot") -> Optional[SubRequest]:
        with self._lock:
            if service_type:
                r = self.conn.execute(
                    "SELECT * FROM sub_requests WHERE user_id = ? AND status = 'pending' AND service_type = ? "
                    "ORDER BY id DESC LIMIT 1",
                    (uid, service_type),
                ).fetchone()
            else:
                r = self.conn.execute(
                    "SELECT * FROM sub_requests WHERE user_id = ? AND status = 'pending' ORDER BY id DESC LIMIT 1",
                    (uid,),
                ).fetchone()
        return self._sub_req(r) if r else None

    def list_sub_requests(self, status: str = "pending", limit: int = 40, service_type: Optional[str] = None) -> List[SubRequest]:
        with self._lock:
            if service_type:
                rows = self.conn.execute(
                    "SELECT * FROM sub_requests WHERE status = ? AND service_type = ? ORDER BY id LIMIT ?",
                    (status, service_type, limit),
                ).fetchall()
            else:
                rows = self.conn.execute(
                    "SELECT * FROM sub_requests WHERE status = ? ORDER BY id LIMIT ?", (status, limit)
                ).fetchall()
        return [self._sub_req(r) for r in rows]

    def get_sub_request(self, rid: int) -> Optional[SubRequest]:
        with self._lock:
            r = self.conn.execute("SELECT * FROM sub_requests WHERE id = ?", (rid,)).fetchone()
        return self._sub_req(r) if r else None

    def count_pending_requests(self, service_type: Optional[str] = None) -> int:
        with self._lock:
            if service_type:
                r = self.conn.execute(
                    "SELECT COUNT(*) AS n FROM sub_requests WHERE status = 'pending' AND service_type = ?",
                    (service_type,),
                ).fetchone()
            else:
                r = self.conn.execute("SELECT COUNT(*) AS n FROM sub_requests WHERE status = 'pending'").fetchone()
        return int(r["n"])

    def decide_sub_request(self, rid: int, status: str, reason: str = "") -> bool:
        """فقط درخواست باز تصمیم‌گیری می‌شود؛ اگر قبلاً بررسی شده باشد False."""
        with self._lock:
            cur = self.conn.execute(
                f"UPDATE sub_requests SET status = ?, reason = ?, decided_at = {self.NOW} "
                "WHERE id = ? AND status = 'pending'",
                (status, reason, rid),
            )
            return cur.rowcount > 0

    # ---------------- بلاک موقت (ضد اسپم) ----------------
    def block_user(self, uid: int, until: float, reason: str = "spam") -> None:
        with self._lock:
            self.conn.execute(
                'INSERT INTO blocked_users(user_id, "until", reason) VALUES(?, ?, ?) '
                'ON CONFLICT(user_id) DO UPDATE SET "until" = excluded."until", reason = excluded.reason',
                (uid, until, reason),
            )
            self._blocked[uid] = (until, reason)

    def unblock_user(self, uid: int) -> bool:
        with self._lock:
            cur = self.conn.execute("DELETE FROM blocked_users WHERE user_id = ?", (uid,))
            existed = self._blocked.pop(uid, None) is not None
            return existed or cur.rowcount > 0

    def block_remaining(self, uid: int) -> float:
        """ثانیه‌های باقی‌مانده‌ی مسدودسازی (0 یعنی مسدود نیست)."""
        item = self._blocked.get(uid)
        if not item:
            return 0.0
        rem = item[0] - time.time()
        if rem <= 0:
            self.unblock_user(uid)
            return 0.0
        return rem

    def list_blocked(self) -> List[Tuple[int, float, str]]:
        now = time.time()
        out: List[Tuple[int, float, str]] = []
        for uid, (until, reason) in list(self._blocked.items()):
            if until <= now:
                self.unblock_user(uid)
                continue
            out.append((uid, until - now, reason))
        out.sort(key=lambda x: -x[1])
        return out

    def clear_blocks(self) -> int:
        with self._lock:
            n = len(self._blocked)
            self.conn.execute("DELETE FROM blocked_users")
            self._blocked.clear()
            return n

    # ------------------------------------------------------------------
    # کلیدهای API هوش مصنوعی (حداکثر ۲۰ اسلات، هر کدوم با Account ID مخصوص خودش)
    # ------------------------------------------------------------------
    @staticmethod
    def _ai_key(r: Dict[str, Any]) -> AIKey:
        return AIKey(
            slot=int(r["slot"]),
            name=r["name"] or "",
            api_key=r["api_key"] or "",
            enabled=bool(r["enabled"]),
            err_until=float(r["err_until"] or 0.0),
            last_error=r["last_error"] or "",
            ok_count=int(r["ok_count"] or 0),
            fail_count=int(r["fail_count"] or 0),
            account_id=(r.get("account_id") or "").strip(),
        )

    def ai_keys(self) -> List[AIKey]:
        return [self._ai_keys[s] for s in sorted(self._ai_keys)]

    def ai_key(self, slot: int) -> Optional[AIKey]:
        return self._ai_keys.get(slot)

    def ai_key_save(
        self, slot: int, api_key: str, name: Optional[str] = None, account_id: Optional[str] = None,
    ) -> bool:
        """ذخیره/ویرایش کلید یک اسلات (با ذخیره، خطا و فعال‌بودن ریست می‌شوند؛ Account ID قبلی حفظ می‌شود)."""
        if not 1 <= slot <= config.AI_MAX_KEYS:
            return False
        with self._lock:
            old = self._ai_keys.get(slot)
            nm = name if name is not None else (old.name if old and old.name else f"کلید {slot}")
            acc = account_id if account_id is not None else (old.account_id if old else "")
            self.conn.execute(
                "INSERT INTO ai_keys(slot, name, api_key, account_id, enabled, err_until, last_error) "
                "VALUES(?, ?, ?, ?, 1, 0, '') "
                "ON CONFLICT(slot) DO UPDATE SET name = excluded.name, api_key = excluded.api_key, "
                "account_id = excluded.account_id, enabled = 1, err_until = 0, last_error = ''",
                (slot, nm, api_key, acc),
            )
            r = self.conn.execute("SELECT * FROM ai_keys WHERE slot = ?", (slot,)).fetchone()
            self._ai_keys[slot] = self._ai_key(r)
        return True

    def ai_key_set_account(self, slot: int, account_id: str) -> bool:
        with self._lock:
            k = self._ai_keys.get(slot)
            if not k:
                return False
            self.conn.execute("UPDATE ai_keys SET account_id = ? WHERE slot = ?", (account_id, slot))
            k.account_id = account_id
        return True

    def ai_key_rename(self, slot: int, name: str) -> bool:
        with self._lock:
            if slot not in self._ai_keys:
                return False
            self.conn.execute("UPDATE ai_keys SET name = ? WHERE slot = ?", (name, slot))
            self._ai_keys[slot].name = name
        return True

    def ai_key_set_enabled(self, slot: int, enabled: bool) -> bool:
        with self._lock:
            k = self._ai_keys.get(slot)
            if not k:
                return False
            if enabled:
                self.conn.execute(
                    "UPDATE ai_keys SET enabled = 1, err_until = 0, last_error = '' WHERE slot = ?", (slot,)
                )
                k.enabled, k.err_until, k.last_error = True, 0.0, ""
            else:
                self.conn.execute("UPDATE ai_keys SET enabled = 0 WHERE slot = ?", (slot,))
                k.enabled = False
        return True

    def ai_key_delete(self, slot: int) -> bool:
        with self._lock:
            cur = self.conn.execute("DELETE FROM ai_keys WHERE slot = ?", (slot,))
            self._ai_keys.pop(slot, None)
            return cur.rowcount > 0

    def ai_key_mark_error(self, slot: int, until: float, msg: str, disable: bool = False) -> Optional[AIKey]:
        """کلید خطادار تا زمان `until` کنار گذاشته می‌شود (disable=True برای کلید نامعتبر: غیرفعال دائمی).
        کلید به‌روزشده (شامل account_id) را برمی‌گرداند."""
        with self._lock:
            k = self._ai_keys.get(slot)
            if not k:
                return None
            msg = (msg or "")[:200]
            self.conn.execute(
                "UPDATE ai_keys SET err_until = ?, last_error = ?, fail_count = fail_count + 1, "
                "enabled = CASE WHEN ? = 1 THEN 0 ELSE enabled END WHERE slot = ?",
                (until, msg, 1 if disable else 0, slot),
            )
            k.err_until, k.last_error, k.fail_count = until, msg, k.fail_count + 1
            if disable:
                k.enabled = False
            return k

    def ai_key_mark_ok(self, slot: int) -> Optional[AIKey]:
        with self._lock:
            k = self._ai_keys.get(slot)
            if not k:
                return None
            self.conn.execute(
                "UPDATE ai_keys SET ok_count = ok_count + 1, err_until = 0, last_error = '' WHERE slot = ?",
                (slot,),
            )
            k.ok_count += 1
            k.err_until, k.last_error = 0.0, ""
            return k

    def ai_key_clear_errors(self) -> None:
        """همه‌ی خطاهای موقت پاک می‌شوند (فعال‌سازی مجدد دستی AI بعد از خاموشی)."""
        with self._lock:
            self.conn.execute("UPDATE ai_keys SET err_until = 0")
            for k in self._ai_keys.values():
                k.err_until = 0.0

    # ------------------------------------------------------------------
    # کانال‌ها
    # ------------------------------------------------------------------
    @staticmethod
    def _channel(r: Dict[str, Any]) -> Channel:
        return Channel(
            id=int(r["id"]),
            channel_id=str(r["channel_id"]),
            channel_title=r["channel_title"] or "",
            is_active=bool(r["is_active"]),
            sort_order=r["sort_order"] or 0,
            invite_link=r["invite_link"],
        )

    def list_channels(self) -> List[Channel]:
        if self._channels is None:
            with self._lock:
                rows = self.conn.execute(
                    "SELECT * FROM force_channels ORDER BY sort_order, id"
                ).fetchall()
                self._channels = [self._channel(r) for r in rows]
        return list(self._channels)

    def active_channels(self) -> List[Channel]:
        return [c for c in self.list_channels() if c.is_active]

    def get_channel(self, cid: int) -> Optional[Channel]:
        for c in self.list_channels():
            if c.id == cid:
                return c
        return None

    def channel_exists(self, channel_id: str, exclude_id: Optional[int] = None) -> bool:
        for c in self.list_channels():
            if c.channel_id == str(channel_id) and c.id != exclude_id:
                return True
        return False

    def add_channel(self, channel_id: str, title: str, invite_link: Optional[str]) -> Optional[int]:
        with self._lock:
            n = self.conn.execute("SELECT COUNT(*) AS n FROM force_channels").fetchone()["n"]
            if int(n) >= config.MAX_CHANNELS:
                return None
            mx = self.conn.execute("SELECT COALESCE(MAX(sort_order), 0) AS m FROM force_channels").fetchone()["m"]
            cid = self.conn.insert_id(
                "INSERT INTO force_channels(channel_id, channel_title, is_active, sort_order, invite_link) "
                "VALUES(?, ?, 1, ?, ?)",
                (str(channel_id), title, int(mx) + 1, invite_link),
            )
            self._channels = None
            return cid

    def update_channel(self, cid: int, **fields) -> None:
        allowed = {"channel_id", "channel_title", "is_active", "invite_link"}
        sets, vals = [], []
        for k, v in fields.items():
            if k in allowed:
                sets.append(f"{k} = ?")
                vals.append(v)
        if not sets:
            return
        vals.append(cid)
        with self._lock:
            self.conn.execute(f"UPDATE force_channels SET {', '.join(sets)} WHERE id = ?", vals)
            self._channels = None

    def toggle_channel(self, cid: int) -> Optional[bool]:
        ch = self.get_channel(cid)
        if not ch:
            return None
        new = not ch.is_active
        self.update_channel(cid, is_active=1 if new else 0)
        return new

    def delete_channel(self, cid: int) -> bool:
        with self._lock:
            cur = self.conn.execute("DELETE FROM force_channels WHERE id = ?", (cid,))
            self._channels = None
            return cur.rowcount > 0

    # ------------------------------------------------------------------
    # بلوک‌ها
    # ------------------------------------------------------------------
    @staticmethod
    def _block(r: Dict[str, Any]) -> Block:
        return Block(
            id=int(r["id"]),
            block_type=r["block_type"],
            content=r["content"] or "",
            is_active=bool(r["is_active"]),
            created_at=ts_str(r["created_at"]),
        )

    def _rebuild_blocks(self) -> None:
        d: Dict[str, List[str]] = {t: [] for t in config.BLOCK_TYPES}
        for r in self.conn.execute(
            "SELECT block_type, content FROM blocks WHERE is_active = 1 ORDER BY id"
        ):
            if (r["content"] or "").strip():
                d.setdefault(r["block_type"], []).append(r["content"])
        self._blocks_active = d

    def add_block(self, block_type: str, content: str) -> int:
        with self._lock:
            bid = self.conn.insert_id(
                "INSERT INTO blocks(block_type, content, is_active) VALUES(?, ?, 1)",
                (block_type, content),
            )
            self._rebuild_blocks()
            return bid

    def list_blocks(self, block_type: str) -> List[Block]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM blocks WHERE block_type = ? ORDER BY id", (block_type,)
            ).fetchall()
        return [self._block(r) for r in rows]

    def get_block(self, bid: int) -> Optional[Block]:
        with self._lock:
            r = self.conn.execute("SELECT * FROM blocks WHERE id = ?", (bid,)).fetchone()
        return self._block(r) if r else None

    def update_block(self, bid: int, content: str) -> None:
        with self._lock:
            self.conn.execute("UPDATE blocks SET content = ? WHERE id = ?", (content, bid))
            self._rebuild_blocks()

    def toggle_block(self, bid: int) -> Optional[bool]:
        b = self.get_block(bid)
        if not b:
            return None
        new = not b.is_active
        with self._lock:
            self.conn.execute("UPDATE blocks SET is_active = ? WHERE id = ?", (1 if new else 0, bid))
            self._rebuild_blocks()
        return new

    def delete_block(self, bid: int) -> bool:
        with self._lock:
            cur = self.conn.execute("DELETE FROM blocks WHERE id = ?", (bid,))
            self._rebuild_blocks()
            return cur.rowcount > 0

    def pick_block(self, block_type: str) -> str:
        items = self._blocks_active.get(block_type) or []
        return random.choice(items) if items else ""

    # ------------------------------------------------------------------
    # قالب‌ها
    # ------------------------------------------------------------------
    def get_template(self, side: str) -> str:
        return self._templates.get(side.upper(), "")

    def set_template(self, side: str, content: str) -> None:
        side = side.upper()
        with self._lock:
            # UPSERT: اگر ردیف قالب (مثلاً بعد از بازیابی بکاپ) وجود نداشت، ویرایش ادمین گم نشود
            self.conn.execute(
                f"INSERT INTO templates(template_type, content, is_active, updated_at) VALUES(?, ?, 1, {self.NOW}) "
                "ON CONFLICT(template_type) DO UPDATE SET content = excluded.content, is_active = 1, "
                "updated_at = excluded.updated_at",
                (side, content),
            )
            self._templates[side] = content

    # ---------------- قالب پیام قیمت / آنالیز ----------------
    def get_msg_template(self, kind: str) -> str:
        txt = self._msg_templates.get(kind) or ""
        return txt if txt.strip() else config.DEFAULT_MSG_TEMPLATES.get(kind, "")

    def set_msg_template(self, kind: str, content: str) -> None:
        self.x(
            "INSERT INTO message_templates(template_type, template_text) VALUES(?, ?) "
            f"ON CONFLICT(template_type) DO UPDATE SET template_text = excluded.template_text, updated_at = {self.NOW}",
            (kind, content),
        )
        self._msg_templates[kind] = content

    def reset_msg_template(self, kind: str) -> None:
        self.set_msg_template(kind, config.DEFAULT_MSG_TEMPLATES[kind])

    # ------------------------------------------------------------------
    # فیلتر نمادها (دسته‌بندی + نماد تکی)
    # ------------------------------------------------------------------
    def cat_enabled(self, name: str) -> bool:
        """دسته‌های بازار سنتی (stock/TradFi/...) تا وقتی ادمین فعالشان نکرده غیرفعالند."""
        v = self._cat_state.get(name)
        return (not config.is_traditional_category(name)) if v is None else v

    def sym_enabled(self, pair: str) -> bool:
        return self._sym_state.get(pair, True)

    def set_cats(self, names, enabled: bool) -> None:
        names = list(names)
        with self._lock:
            with self.conn.transaction() as tx:
                tx.executemany(
                    "INSERT INTO symbol_filter(kind, name, enabled) VALUES('cat', ?, ?) "
                    "ON CONFLICT(kind, name) DO UPDATE SET enabled = excluded.enabled",
                    [(n, 1 if enabled else 0) for n in names],
                )
            for n in names:
                self._cat_state[n] = enabled

    def set_syms(self, pairs, enabled: bool) -> None:
        """نماد پیش‌فرض فعال است؛ فقط غیرفعال‌ها در دیتابیس ذخیره می‌شوند."""
        pairs = list(pairs)
        with self._lock:
            with self.conn.transaction() as tx:
                if enabled:
                    tx.executemany(
                        "DELETE FROM symbol_filter WHERE kind = 'sym' AND name = ?", [(p,) for p in pairs]
                    )
                else:
                    tx.executemany(
                        "INSERT INTO symbol_filter(kind, name, enabled) VALUES('sym', ?, 0) "
                        "ON CONFLICT(kind, name) DO UPDATE SET enabled = 0",
                        [(p,) for p in pairs],
                    )
            for p in pairs:
                if enabled:
                    self._sym_state.pop(p, None)
                else:
                    self._sym_state[p] = False

    def reset_filters(self) -> None:
        with self._lock:
            self.conn.execute("DELETE FROM symbol_filter")
            self._cat_state, self._sym_state = {}, {}

    # ------------------------------------------------------------------
    # تاریخچه سیگنال‌ها
    # ------------------------------------------------------------------
    def record_signal(self, symbol: str, side: str, uid: int = 0) -> int:
        """سیگنال صادرشده را ثبت می‌کند و شناسه‌ی آن را برمی‌گرداند (uid = 0: ادمین / کانال)."""
        with self._lock:
            return self.conn.insert_id(
                "INSERT INTO signal_history(symbol, side, user_id) VALUES(?, ?, ?)", (symbol, side, int(uid))
            )

    def recent_symbols(self, hours: int = 24, uid: int = 0) -> Set[str]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT DISTINCT symbol FROM signal_history WHERE created_at >= ? AND COALESCE(user_id, 0) = ?",
                (utc_cutoff(hours=int(hours)), int(uid)),
            ).fetchall()
        return {r["symbol"] for r in rows}

    # ------------------------------------------------------------------
    # تنظیمات مستقل هر کاربر (per-user)
    # ------------------------------------------------------------------
    def scope(self, uid: int):
        """منبع تنظیمات: uid = 0 -> تنظیمات ادمین (global)؛ در غیر این صورت تنظیمات مستقل همان کاربر."""
        return self if not uid else UserView(self, int(uid))

    def user(self, uid: int) -> "UserView":
        return UserView(self, int(uid))

    def _ucfg_get(self, uid: int) -> Dict[str, str]:
        cfg = self._ucfg.get(uid)
        if cfg is None:
            with self._lock:
                cfg = self._ucfg.get(uid)
                if cfg is None:
                    rows = self.conn.execute(
                        "SELECT key, value FROM user_settings WHERE user_id = ?", (uid,)
                    ).fetchall()
                    cfg = {r["key"]: (r["value"] if r["value"] is not None else "") for r in rows}
                    if len(self._ucfg) > 20000:
                        for k in list(self._ucfg)[:10000]:
                            self._ucfg.pop(k, None)
                    self._ucfg[uid] = cfg
        return cfg

    def u_set_many(self, uid: int, items: Dict[str, str]) -> None:
        items = {k: str(v) for k, v in items.items()}
        if not items:
            return
        with self._lock:
            cfg = self._ucfg_get(uid)
            with self.conn.transaction() as tx:
                tx.executemany(
                    "INSERT INTO user_settings(user_id, key, value) VALUES(?, ?, ?) "
                    "ON CONFLICT(user_id, key) DO UPDATE SET value = excluded.value",
                    [(uid, k, v) for k, v in items.items()],
                )
            cfg.update(items)

    def ensure_user_profile(self, uid: int) -> None:
        """اولین ورود به تنظیمات: پروفایل پیش‌فرض کاربر از پیش‌فرض‌های سیستم (نه تنظیمات ادمین) ساخته می‌شود."""
        cfg = self._ucfg_get(uid)
        if cfg.get("_init") == "1":
            return
        items = {k: config.DEFAULT_SETTINGS.get(k, "") for k in config.USER_KEYS if k not in cfg and k != "ai_model"}
        if "ai_model" not in cfg:
            items["ai_model"] = self.ai_models()[0]
        items["_init"] = "1"
        self.u_set_many(uid, items)

    # ---------------- مدل‌های مجاز AI ----------------
    def ai_models(self) -> List[str]:
        raw = self.get("ai_models")
        c = self._models_cache
        if c is not None and c[0] == raw:
            return list(c[1])
        models: List[str] = []
        try:
            v = json.loads(raw)
            if isinstance(v, list):
                for x in v:
                    s = str(x).strip() if isinstance(x, str) else ""
                    if s and s not in models:
                        models.append(s)
        except ValueError:
            pass
        if not models:
            models = list(config.AI_MODELS)
        self._models_cache = (raw, models)
        return list(models)

    def set_ai_models(self, models: List[str]) -> None:
        self.set("ai_models", json.dumps(list(models), ensure_ascii=False))

    def user_ids_with_models(self, models: Sequence[str]) -> List[int]:
        models = list(models)
        if not models:
            return []
        ph = ",".join("?" * len(models))
        with self._lock:
            rows = self.conn.execute(
                f"SELECT user_id FROM user_settings WHERE key = 'ai_model' AND value IN ({ph})", tuple(models)
            ).fetchall()
        return [int(r["user_id"]) for r in rows]

    # ---------------- فیلتر نمادهای کاربر ----------------
    def _ufilt_get(self, uid: int) -> Tuple[Dict[str, bool], Dict[str, bool]]:
        f = self._ufilt.get(uid)
        if f is None:
            with self._lock:
                f = self._ufilt.get(uid)
                if f is None:
                    cats: Dict[str, bool] = {}
                    syms: Dict[str, bool] = {}
                    rows = self.conn.execute(
                        "SELECT kind, name, enabled FROM user_symbol_filter WHERE user_id = ?", (uid,)
                    ).fetchall()
                    for r in rows:
                        (cats if r["kind"] == "cat" else syms)[r["name"]] = bool(r["enabled"])
                    if len(self._ufilt) > 20000:
                        for k in list(self._ufilt)[:10000]:
                            self._ufilt.pop(k, None)
                    f = self._ufilt[uid] = (cats, syms)
        return f

    def u_cat_enabled(self, uid: int, name: str) -> bool:
        v = self._ufilt_get(uid)[0].get(name)
        return (not config.is_traditional_category(name)) if v is None else v

    def u_sym_enabled(self, uid: int, pair: str) -> bool:
        return self._ufilt_get(uid)[1].get(pair, True)

    def u_set_cats(self, uid: int, names, enabled: bool) -> None:
        names = list(names)
        with self._lock:
            cats, _ = self._ufilt_get(uid)
            with self.conn.transaction() as tx:
                tx.executemany(
                    "INSERT INTO user_symbol_filter(user_id, kind, name, enabled) VALUES(?, 'cat', ?, ?) "
                    "ON CONFLICT(user_id, kind, name) DO UPDATE SET enabled = excluded.enabled",
                    [(uid, n, 1 if enabled else 0) for n in names],
                )
            for n in names:
                cats[n] = enabled

    def u_set_syms(self, uid: int, pairs, enabled: bool) -> None:
        pairs = list(pairs)
        with self._lock:
            _, syms = self._ufilt_get(uid)
            with self.conn.transaction() as tx:
                if enabled:
                    tx.executemany(
                        "DELETE FROM user_symbol_filter WHERE user_id = ? AND kind = 'sym' AND name = ?",
                        [(uid, p) for p in pairs],
                    )
                else:
                    tx.executemany(
                        "INSERT INTO user_symbol_filter(user_id, kind, name, enabled) VALUES(?, 'sym', ?, 0) "
                        "ON CONFLICT(user_id, kind, name) DO UPDATE SET enabled = 0",
                        [(uid, p) for p in pairs],
                    )
            for p in pairs:
                if enabled:
                    syms.pop(p, None)
                else:
                    syms[p] = False

    def u_reset_filters(self, uid: int) -> None:
        with self._lock:
            self.conn.execute("DELETE FROM user_symbol_filter WHERE user_id = ?", (uid,))
            self._ufilt[uid] = ({}, {})


class UserView:
    """
    نمای تنظیمات مستقل یک کاربر؛ همان API تنظیمات موتورها در Database را دارد تا همه‌ی توابع تحلیل / ساخت سیگنال
    / مشاور / Import و Export بتوانند هم با تنظیمات ادمین (db) و هم با تنظیمات یک کاربر کار کنند.

      - مقدارهای ذخیره‌نشده از پیش‌فرض‌های سیستم (config.DEFAULT_SETTINGS) خوانده می‌شوند، نه از تنظیمات ادمین.
      - تنظیمات AI (به‌جز مدل) همیشه از تنظیمات ادمین خوانده می‌شوند و کاربر نمی‌تواند آن‌ها را عوض کند.
      - مدل AI کاربر باید داخل لیست مدل‌های مجاز ادمین باشد؛ وگرنه اولین مدل لیست استفاده می‌شود.
    """

    __slots__ = ("d", "uid")

    def __init__(self, d: Database, uid: int):
        self.d, self.uid = d, int(uid)

    # ---------------- پایه ----------------
    def _cfg(self) -> Dict[str, str]:
        return self.d._ucfg_get(self.uid)

    def _raw(self, key: str) -> str:
        v = self._cfg().get(key)
        return v if v is not None else config.DEFAULT_SETTINGS.get(key, "")

    def ai_model(self) -> str:
        allowed = self.d.ai_models()
        v = (self._cfg().get("ai_model") or "").strip()
        return v if v in allowed else allowed[0]

    def fix_model(self) -> Optional[str]:
        """اگر مدل ذخیره‌شده‌ی کاربر دیگر در لیست مجاز نیست به اولین مدل برمی‌گردد و مدل قبلی را برمی‌گرداند."""
        v = (self._cfg().get("ai_model") or "").strip()
        if v and v not in self.d.ai_models():
            self.d.u_set_many(self.uid, {"ai_model": self.d.ai_models()[0]})
            return v
        return None

    def get(self, key: str) -> str:
        if key == "ai_model":
            return self.ai_model()
        if key in config.USER_KEYS:
            return self._raw(key)
        return self.d.get(key)

    def get_int(self, key: str) -> int:
        try:
            return int(float(self.get(key)))
        except (TypeError, ValueError):
            return int(float(config.DEFAULT_SETTINGS.get(key, "0") or 0))

    def get_float(self, key: str) -> float:
        try:
            return float(self.get(key))
        except (TypeError, ValueError):
            return float(config.DEFAULT_SETTINGS.get(key, "0") or 0)

    def get_bool(self, key: str) -> bool:
        return self.get(key) in ("1", "true", "True")

    def set(self, key: str, value) -> None:
        if key in config.USER_KEYS:
            self.d.u_set_many(self.uid, {key: str(value)})

    def toggle(self, key: str) -> bool:
        new = not self.get_bool(key)
        self.set(key, "1" if new else "0")
        return new

    # ---------------- موتورها (مشترک / جداگانه) ----------------
    def eng_mode(self) -> str:
        return "separate" if self._raw("eng_mode") == "separate" else "global"

    def _ek(self, key: str, side: Optional[str]) -> str:
        if key == "ai_model":
            return key
        if side and self.eng_mode() == "separate" and (config.is_engine_key(key) or key in config.USER_TF_KEYS):
            return f"{key}@{side.upper()}"
        return key

    def get_e(self, key: str, side: Optional[str] = None) -> str:
        if key == "ai_model":
            return self.ai_model()
        if key in config.AI_KEYS:
            return self.d.get_e(key, side)
        v = self._cfg().get(self._ek(key, side))
        return v if v is not None else self._raw(key)

    def get_e_int(self, key: str, side: Optional[str] = None) -> int:
        try:
            return int(float(self.get_e(key, side)))
        except (TypeError, ValueError):
            return int(float(config.DEFAULT_SETTINGS.get(key, "0") or 0))

    def get_e_float(self, key: str, side: Optional[str] = None) -> float:
        try:
            return float(self.get_e(key, side))
        except (TypeError, ValueError):
            return float(config.DEFAULT_SETTINGS.get(key, "0") or 0)

    def get_e_bool(self, key: str, side: Optional[str] = None) -> bool:
        return self.get_e(key, side) in ("1", "true", "True")

    def set_e(self, key: str, value, side: Optional[str] = None) -> None:
        if key in config.USER_KEYS:
            self.d.u_set_many(self.uid, {self._ek(key, side): str(value)})

    def set_many_e(self, items: Sequence[Tuple[str, str, Optional[str]]]) -> None:
        self.d.u_set_many(
            self.uid, {self._ek(k, side): str(v) for k, v, side in items if k in config.USER_KEYS}
        )

    def toggle_e(self, key: str, side: Optional[str] = None) -> bool:
        new = not self.get_e_bool(key, side)
        self.set_e(key, "1" if new else "0", side)
        return new

    def get_profile(self, key: str, side: Optional[str]) -> str:
        if side:
            v = self._cfg().get(f"{key}@{side.upper()}")
            if v is not None:
                return v
        return self.get(key)

    def ensure_side_profiles(self) -> None:
        cfg = self._cfg()
        new: Dict[str, str] = {}
        for key in sorted((config.USER_ENGINE_KEYS - {"ai_model"}) | config.USER_TF_KEYS):
            for side in ("LONG", "SHORT"):
                sk = f"{key}@{side}"
                if sk not in cfg:
                    new[sk] = self._raw(key)
        self.d.u_set_many(self.uid, new)

    # ---------------- فیلتر نمادها ----------------
    def cat_enabled(self, name: str) -> bool:
        return self.d.u_cat_enabled(self.uid, name)

    def sym_enabled(self, pair: str) -> bool:
        return self.d.u_sym_enabled(self.uid, pair)

    def set_cats(self, names, enabled: bool) -> None:
        self.d.u_set_cats(self.uid, names, enabled)

    def set_syms(self, pairs, enabled: bool) -> None:
        self.d.u_set_syms(self.uid, pairs, enabled)

    def reset_filters(self) -> None:
        self.d.u_reset_filters(self.uid)


db = Database(config.DATABASE_URL)
