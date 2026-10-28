"""لایه‌ی دسترسی داده‌ی وب‌اپ؛ همه‌ی کوئری‌های وب‌اپ در همین ماژول متمرکز است."""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import config
import plans
from database import Database, _norm_digits, db, utc_cutoff
from webapp.auth import generate_password, hash_password, verify_password
from webapp.jalali import jalali_dt, jalali_from_db, parse_db_ts

log = logging.getLogger("webapp.repo")

DEFAULT_EXCHANGES = (
    {"name": "Binance", "slug": "binance", "ccxt_id": "binance", "api_url": "https://fapi.binance.com", "priority": 1},
    {"name": "Toobit", "slug": "toobit", "ccxt_id": "toobit", "api_url": "https://api.toobit.com", "priority": 2},
    {"name": "Ourbit", "slug": "ourbit", "ccxt_id": "", "api_url": "https://api.ourbit.com/api/v3", "priority": 3},
)

SETTING_KEYS = (
    "notify_on", "notify_template",
    "scan_timeframe", "scan_depth", "scan_mode", "scan_symbols_mode",
    "scan_strategy_keys", "scan_symbol", "scan_exchanges", "exchanges_all",
    "last_notified_id", "favorite_symbols",
)


def _json_loads(raw: str, default):
    try:
        value = json.loads(raw or "")
        return value if isinstance(value, type(default)) else default
    except (TypeError, ValueError):
        return default


def _password_lookup(password: str) -> str:
    """کلید جستجوی قطعی رمز؛ رمز خام ذخیره نمی‌شود."""
    return hmac.new(
        config.WEBAPP_SECRET.encode("utf-8"),
        ("lookup:" + (password or "")).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


class Repo:
    def __init__(self, database: Optional[Database] = None):
        self.db = database or db

    # ------------------------------------------------------------------
    # راه‌اندازی
    # ------------------------------------------------------------------
    def ensure_defaults(self) -> None:
        self.ensure_exchanges()
        self.sync_catalog()

    def ensure_exchanges(self) -> None:
        if int(self.db.q1("SELECT COUNT(*) AS n FROM exchanges")["n"] or 0) > 0:
            return
        for e in DEFAULT_EXCHANGES:
            self.db.x(
                "INSERT INTO exchanges(name, slug, ccxt_id, api_url, priority, is_active, is_visible) "
                "VALUES(?, ?, ?, ?, ?, 1, 1)",
                (e["name"], e["slug"], e["ccxt_id"], e["api_url"], e["priority"]),
            )

    def sync_catalog(self) -> Dict[str, int]:
        """فایل list.txt را می‌خواند و کاتالوگ را بدون از دست دادن راهنما/فعال‌بودن به‌روز می‌کند."""
        import structural_catalog as catalog

        items = catalog.parse_list_file()
        with self.db.transaction() as tx:
            for order, it in enumerate(items):
                tx.execute(
                    "INSERT INTO structural_strategies(strategy_key, category, title, strategy_type, signal_type, "
                    "text_color, button_color, logic_key, logic_params, description, sort_order, is_active) "
                    "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1) "
                    "ON CONFLICT(strategy_key) DO UPDATE SET category = excluded.category, title = excluded.title, "
                    "strategy_type = excluded.strategy_type, signal_type = excluded.signal_type, "
                    "text_color = excluded.text_color, button_color = excluded.button_color, "
                    "logic_key = excluded.logic_key, logic_params = excluded.logic_params, "
                    "description = excluded.description, sort_order = excluded.sort_order",
                    (
                        it["strategy_key"], it["category"], it["title"], it["strategy_type"], it["signal_type"],
                        "green" if it["signal_type"] == "حمایت" else "red",
                        "success" if it["signal_type"] == "حمایت" else "danger",
                        it["logic_key"], it.get("logic_params", ""), it.get("description", ""), order,
                    ),
                )
        return catalog.catalog_stats(items)

    # ------------------------------------------------------------------
    # استراتژی‌ها و راهنما
    # ------------------------------------------------------------------
    def strategy_rows(self, active_only: bool = True) -> List[Dict[str, Any]]:
        where = " WHERE is_active = 1" if active_only else ""
        return self.db.q(
            "SELECT * FROM structural_strategies" + where + " ORDER BY sort_order, id"
        )

    def strategy(self, key: str) -> Optional[Dict[str, Any]]:
        return self.db.q1("SELECT * FROM structural_strategies WHERE strategy_key = ?", (key,))

    def strategy_groups(self, active_only: bool = True) -> List[Dict[str, Any]]:
        groups: Dict[str, List[Dict[str, Any]]] = {}
        for row in self.strategy_rows(active_only):
            groups.setdefault(row["category"], []).append({
                "key": row["strategy_key"],
                "title": row["title"],
                "category": row["category"],
                "strategy_type": row["strategy_type"],
                "signal_type": row["signal_type"],
                "text_color": row["text_color"],
                "button_color": row["button_color"],
                "description": row["description"] or "",
                "active": bool(row["is_active"]),
                "guide_image": row["guide_image"] or "",
                "guide_width": int(row["guide_width"] or 0),
                "guide_height": int(row["guide_height"] or 0),
            })
        out = []
        for name, items in groups.items():
            out.append({"category": name, "count": len(items), "strategies": items})
        out.sort(key=lambda g: (-g["count"], g["category"]))
        return out

    def guides(self) -> Dict[str, Dict[str, Any]]:
        out: Dict[str, Dict[str, Any]] = {}
        for row in self.db.q("SELECT * FROM strategy_guides"):
            out[row["strategy_key"]] = dict(row)
        return out

    def set_strategy_active(self, key: str, active: bool) -> bool:
        cur = self.db.x(
            "UPDATE structural_strategies SET is_active = ? WHERE strategy_key = ?",
            (1 if active else 0, key),
        )
        return cur.rowcount > 0

    def set_guide(
        self, key: str, title: str = "", description: str = "",
        image_path: Optional[str] = None, width: int = 0, height: int = 0,
    ) -> bool:
        if not self.strategy(key):
            return False
        current = self.db.q1("SELECT * FROM strategy_guides WHERE strategy_key = ?", (key,))
        if current:
            self.db.x(
                "UPDATE strategy_guides SET title = ?, description = ?, image_path = ?, image_width = ?, "
                "image_height = ?, updated_at = CURRENT_TIMESTAMP WHERE strategy_key = ?",
                (
                    title, description,
                    (image_path if image_path is not None else (current["image_path"] or "")),
                    width, height, key,
                ),
            )
        else:
            self.db.x(
                "INSERT INTO strategy_guides(strategy_key, title, description, image_path, image_width, image_height) "
                "VALUES(?, ?, ?, ?, ?, ?)",
                (key, title, description, image_path or "", width, height),
            )
        if image_path is not None:
            self.db.x(
                "UPDATE structural_strategies SET guide_image = ?, guide_width = ?, guide_height = ? "
                "WHERE strategy_key = ?",
                (image_path, width, height, key),
            )
        return True

    # ------------------------------------------------------------------
    # کاربران وب‌اپ
    # ------------------------------------------------------------------
    def webapp_user(self, uid: int) -> Optional[Dict[str, Any]]:
        return self.db.q1("SELECT * FROM webapp_users WHERE user_id = ?", (int(uid),))

    def ensure_webapp_user(self, uid: int) -> None:
        self.db.x(
            "INSERT INTO webapp_users(user_id, access_enabled) VALUES(?, 1) ON CONFLICT(user_id) DO NOTHING",
            (int(uid),),
        )

    def set_password(self, uid: int, password: Optional[str] = None) -> str:
        password = password or generate_password(10)
        self.ensure_webapp_user(uid)
        self.db.x(
            "UPDATE webapp_users SET password_hash = ?, password_lookup = ?, password_issued_at = ? "
            "WHERE user_id = ?",
            (hash_password(password), _password_lookup(password), time.time(), int(uid)),
        )
        self.ensure_username(int(uid))
        return password

    def find_password(self, password: str) -> Optional[Dict[str, Any]]:
        # ورود فقط با رمز، تنها وقتی مجاز است که همین رمز روی یک حساب فعال باشد؛
        # اگر چند حساب رمز یکسان داشته باشند انتخاب خودکار خطرناک است.
        rows = self.db.q(
            "SELECT * FROM webapp_users WHERE password_lookup = ? AND access_enabled = 1",
            (_password_lookup(password),),
        )
        return rows[0] if len(rows) == 1 else None

    def check_password(self, uid: int, password: str) -> bool:
        row = self.webapp_user(uid)
        return bool(row and int(row.get("access_enabled") or 0) and verify_password(password, row.get("password_hash") or ""))

    def find_by_username(self, username: str) -> Optional[Dict[str, Any]]:
        name = str(username or "").strip()
        if not name:
            return None
        return self.db.q1(
            "SELECT * FROM webapp_users WHERE LOWER(username) = LOWER(?) LIMIT 1", (name,)
        )

    def username_taken(self, username: str, exclude_uid: int = 0) -> bool:
        row = self.db.q1(
            "SELECT user_id FROM webapp_users WHERE LOWER(username) = LOWER(?) AND user_id != ? LIMIT 1",
            (str(username or "").strip(), int(exclude_uid)),
        )
        return row is not None

    def ensure_username(self, uid: int) -> str:
        """نام کاربری یکتا برای کاربر (بدون تغییر نام کاربری موجود)."""
        self.ensure_webapp_user(uid)
        row = self.webapp_user(uid)
        current = str((row or {}).get("username") or "").strip()
        if current:
            return current
        base = f"user_{int(uid)}" if int(uid) > 0 else f"web_{abs(int(uid))}"
        name, n = base, 1
        while self.username_taken(name, int(uid)):
            n += 1
            name = f"{base}_{n}"
        self.db.x("UPDATE webapp_users SET username = ? WHERE user_id = ?", (name, int(uid)))
        return name

    def set_username(self, uid: int, username: str) -> bool:
        name = str(username or "").strip()
        if not name or self.username_taken(name, int(uid)):
            return False
        self.ensure_webapp_user(uid)
        self.db.x("UPDATE webapp_users SET username = ? WHERE user_id = ?", (name, int(uid)))
        return True

    def set_access(self, uid: int, enabled: bool) -> bool:
        self.ensure_webapp_user(uid)
        self.db.x("UPDATE webapp_users SET access_enabled = ? WHERE user_id = ?", (1 if enabled else 0, int(uid)))
        return True

    def mark_login(self, uid: int) -> None:
        self.ensure_webapp_user(uid)
        self.db.x("UPDATE webapp_users SET last_login_at = ? WHERE user_id = ?", (time.time(), int(uid)))

    def has_password(self, uid: int) -> bool:
        row = self.webapp_user(uid)
        return bool(row and row.get("password_hash"))

    def all_webapp_ids(self) -> List[int]:
        return [int(r["user_id"]) for r in self.db.q(
            "SELECT user_id FROM webapp_users WHERE password_hash <> '' ORDER BY user_id"
        )]

    def user_rows(
        self, query: str = "", sub_filter: str = "all", access_filter: str = "all",
        offset: int = 0, limit: int = 25,
    ) -> Tuple[List[Dict[str, Any]], int]:
        q = _norm_digits((query or "").strip()).lstrip("+")
        where, params = [], []
        if q:
            likes = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            where.append(
                "(CAST(u.user_id AS TEXT) LIKE ? ESCAPE '\\' OR COALESCE(u.phone_number,'') LIKE ? ESCAPE '\\' "
                "OR LOWER(COALESCE(u.first_name,'')) LIKE ? ESCAPE '\\' OR LOWER(COALESCE(u.last_name,'')) LIKE ? ESCAPE '\\' "
                "OR LOWER(COALESCE(u.username,'')) LIKE ? ESCAPE '\\')"
            )
            params += [likes, likes, likes.lower(), likes.lower(), likes.lower()]
        now = time.time()
        if sub_filter == "vip":
            where.append("u.sub_until > ?")
            params.append(now)
        elif sub_filter == "expired":
            where.append("u.sub_until > 0 AND u.sub_until <= ?")
            params.append(now)
        elif sub_filter == "none":
            where.append("COALESCE(u.sub_until,0) <= 0")
        elif sub_filter == "web_vip":
            where.append("u.webapp_sub_until > ?")
            params.append(now)
        elif sub_filter == "web_expired":
            where.append("u.webapp_sub_until > 0 AND u.webapp_sub_until <= ?")
            params.append(now)
        if access_filter == "on":
            where.append("COALESCE(w.access_enabled,0) = 1 AND COALESCE(w.password_hash,'') <> ''")
        elif access_filter == "off":
            where.append("(COALESCE(w.password_hash,'') = '' OR COALESCE(w.access_enabled,0) = 0)")
        cond = (" WHERE " + " AND ".join(where)) if where else ""
        base = " FROM users u LEFT JOIN webapp_users w ON w.user_id = u.user_id" + cond
        total = int(self.db.q1("SELECT COUNT(*) AS n" + base, params)["n"] or 0)
        rows = self.db.q(
            "SELECT u.*, w.password_hash, w.access_enabled, w.password_issued_at, w.last_login_at"
            + base + " ORDER BY u.joined_at DESC, u.user_id DESC LIMIT ? OFFSET ?",
            params + [int(limit), int(offset)],
        )
        return [self.user_public(r, full=True) for r in rows], total

    def user_public(self, row: Dict[str, Any], full: bool = False) -> Dict[str, Any]:
        now = time.time()
        sub_until = float(row.get("sub_until") or 0)
        web_until = float(row.get("webapp_sub_until") or 0)
        phone = row.get("phone_number")
        name = " ".join(x for x in (row.get("first_name") or "", row.get("last_name") or "") if x).strip()
        out = {
            "user_id": int(row["user_id"]),
            "name": name or (phone or "کاربر آبان"),
            "first_name": row.get("first_name") or "",
            "last_name": row.get("last_name") or "",
            "username": row.get("username") or "",
            "phone": phone or "",
            "joined_at": jalali_from_db(row.get("joined_at")),
            "verified": bool(row.get("is_verified")),
            "bot_sub": {
                "active": sub_until > now, "until": sub_until,
                "until_jalali": jalali_dt(sub_until) if sub_until else "-",
                "days_left": max(0, int((sub_until - now + 86399) // 86400)) if sub_until > now else 0,
            },
            "webapp_sub": {
                "active": web_until > now, "until": web_until,
                "until_jalali": jalali_dt(web_until) if web_until else "-",
                "days_left": max(0, int((web_until - now + 86399) // 86400)) if web_until > now else 0,
            },
            "access_enabled": bool(row.get("access_enabled") or 0),
            "has_password": bool(row.get("password_hash")),
            "last_login": jalali_dt(float(row["last_login_at"])) if row.get("last_login_at") else "-",
        }
        if full:
            out["password_issued_at"] = jalali_dt(float(row["password_issued_at"])) if row.get("password_issued_at") else "-"
        return out

    def user_detail(self, uid: int) -> Optional[Dict[str, Any]]:
        row = self.db.q1(
            "SELECT u.*, w.password_hash, w.access_enabled, w.password_issued_at, w.last_login_at "
            "FROM users u LEFT JOIN webapp_users w ON w.user_id = u.user_id WHERE u.user_id = ?",
            (int(uid),),
        )
        if not row:
            return None
        out = self.user_public(row, full=True)
        payments = self.db.q(
            "SELECT * FROM payments_history WHERE user_id = ? ORDER BY id DESC LIMIT 100", (int(uid),)
        )
        out["payments"] = [self.payment_public(p) for p in payments]
        out["searches"] = [self.search_public(s) for s in self.db.q(
            "SELECT * FROM search_history WHERE user_id = ? ORDER BY id DESC LIMIT 30", (int(uid),)
        )]
        out["favorites"] = self.db.q(
            "SELECT kind, value FROM webapp_favorites WHERE user_id = ? ORDER BY kind, value", (int(uid),)
        )
        return out

    def delete_user(self, uid: int) -> bool:
        self.db.x("DELETE FROM webapp_user_settings WHERE user_id = ?", (int(uid),))
        self.db.x("DELETE FROM webapp_favorites WHERE user_id = ?", (int(uid),))
        self.db.x("DELETE FROM search_history WHERE user_id = ?", (int(uid),))
        self.db.x("DELETE FROM webapp_users WHERE user_id = ?", (int(uid),))
        return self.db.delete_user(int(uid))

    # ------------------------------------------------------------------
    # تنظیمات کاربر
    # ------------------------------------------------------------------
    def user_settings(self, uid: int) -> Dict[str, str]:
        rows = self.db.q("SELECT key, value FROM webapp_user_settings WHERE user_id = ?", (int(uid),))
        values = {r["key"]: (r["value"] if r["value"] is not None else "") for r in rows}
        values.setdefault("notify_on", "0")
        values.setdefault("notify_template", config.DEFAULT_SETTINGS.get("web_notify_template", ""))
        values.setdefault("scan_timeframe", config.DEFAULT_SETTINGS.get("web_default_timeframe", "1h"))
        values.setdefault("scan_depth", "10")
        values.setdefault("scan_mode", "any")
        values.setdefault("scan_symbols_mode", "all")
        values.setdefault("scan_strategy_keys", "[]")
        values.setdefault("scan_symbol", "")
        values.setdefault("scan_exchanges", "[]")
        values.setdefault("exchanges_all", "1")
        return values

    def save_user_settings(self, uid: int, values: Dict[str, Any]) -> Dict[str, str]:
        items: Dict[str, str] = {}
        for key in SETTING_KEYS:
            if key not in values:
                continue
            value = values[key]
            if key in ("scan_strategy_keys", "scan_exchanges"):
                value = json.dumps(value if isinstance(value, list) else [], ensure_ascii=False)
            elif isinstance(value, bool):
                value = "1" if value else "0"
            items[key] = str(value)
        if items:
            with self.db.transaction() as tx:
                tx.executemany(
                    "INSERT INTO webapp_user_settings(user_id, key, value) VALUES(?, ?, ?) "
                    "ON CONFLICT(user_id, key) DO UPDATE SET value = excluded.value",
                    [(int(uid), k, v) for k, v in items.items()],
                )
        return self.user_settings(uid)

    def scan_config(self, uid: int) -> Dict[str, Any]:
        s = self.user_settings(uid)
        return {
            "timeframe": s.get("scan_timeframe") or "1h",
            "depth": int(s.get("scan_depth") or 10),
            "mode": s.get("scan_mode") or "any",
            "symbols_mode": s.get("scan_symbols_mode") or "all",
            "symbol": s.get("scan_symbol") or "",
            "strategy_keys": _json_loads(s.get("scan_strategy_keys", "[]"), []),
            "exchanges": _json_loads(s.get("scan_exchanges", "[]"), []),
            "exchanges_all": s.get("exchanges_all", "1") == "1",
        }

    # ------------------------------------------------------------------
    # علاقه‌مندی‌ها
    # ------------------------------------------------------------------
    def favorites(self, uid: int) -> Dict[str, List[str]]:
        out: Dict[str, List[str]] = {"strategy": [], "symbol": [], "signal": []}
        for r in self.db.q("SELECT kind, value FROM webapp_favorites WHERE user_id = ? ORDER BY created_at DESC", (int(uid),)):
            out.setdefault(r["kind"], []).append(r["value"])
        return out

    def toggle_favorite(self, uid: int, kind: str, value: str) -> bool:
        kind = kind if kind in ("strategy", "symbol", "signal") else "symbol"
        value = (value or "").strip()
        if not value:
            return False
        existing = self.db.q1(
            "SELECT 1 AS x FROM webapp_favorites WHERE user_id = ? AND kind = ? AND value = ?",
            (int(uid), kind, value),
        )
        if existing:
            self.db.x(
                "DELETE FROM webapp_favorites WHERE user_id = ? AND kind = ? AND value = ?",
                (int(uid), kind, value),
            )
            return False
        self.db.x(
            "INSERT INTO webapp_favorites(user_id, kind, value) VALUES(?, ?, ?) ON CONFLICT DO NOTHING",
            (int(uid), kind, value),
        )
        return True

    # ------------------------------------------------------------------
    # تاریخچه جستجو
    # ------------------------------------------------------------------
    def add_search(self, uid: int, payload: Dict[str, Any], count: int) -> int:
        pid = self.db.insert_id(
            "INSERT INTO search_history(user_id, filters, strategies, timeframe, depth, symbols_mode, result_count) "
            "VALUES(?, ?, ?, ?, ?, ?, ?)",
            (
                int(uid),
                json.dumps(payload, ensure_ascii=False),
                json.dumps(payload.get("strategy_keys") or [], ensure_ascii=False),
                payload.get("timeframe") or "",
                int(payload.get("depth") or 0),
                payload.get("symbols_mode") or "",
                int(count),
            ),
        )
        rows = self.db.q(
            "SELECT id FROM search_history WHERE user_id = ? ORDER BY id DESC LIMIT ? OFFSET ?",
            (int(uid), config.WEBAPP_SCAN_HISTORY_LIMIT, config.WEBAPP_SCAN_HISTORY_LIMIT),
        )
        if rows:
            self.db.x(
                "DELETE FROM search_history WHERE id IN (" + ",".join("?" * len(rows)) + ")",
                tuple(int(r["id"]) for r in rows),
            )
        return pid

    def search_public(self, row: Dict[str, Any]) -> Dict[str, Any]:
        filters = _json_loads(row.get("filters") or "{}", {})
        strategies = _json_loads(row.get("strategies") or "[]", [])
        return {
            "id": int(row["id"]),
            "created_at": jalali_from_db(row.get("created_at")),
            "timeframe": row.get("timeframe") or "",
            "depth": int(row.get("depth") or 0),
            "symbols_mode": row.get("symbols_mode") or "",
            "result_count": int(row.get("result_count") or 0),
            "strategy_count": len(strategies),
            "strategy_keys": strategies,
            "filters": filters,
        }

    def search_rows(self, uid: int, offset: int = 0, limit: int = 10) -> Tuple[List[Dict[str, Any]], int]:
        total = int(self.db.q1(
            "SELECT COUNT(*) AS n FROM search_history WHERE user_id = ?", (int(uid),)
        )["n"] or 0)
        rows = self.db.q(
            "SELECT * FROM search_history WHERE user_id = ? ORDER BY id DESC LIMIT ? OFFSET ?",
            (int(uid), int(limit), int(offset)),
        )
        return [self.search_public(r) for r in rows], total

    def get_search(self, uid: int, sid: int) -> Optional[Dict[str, Any]]:
        row = self.db.q1("SELECT * FROM search_history WHERE id = ? AND user_id = ?", (int(sid), int(uid)))
        return self.search_public(row) if row else None

    def delete_search(self, uid: int, sid: int) -> bool:
        return self.db.x(
            "DELETE FROM search_history WHERE id = ? AND user_id = ?", (int(sid), int(uid))
        ).rowcount > 0

    def clear_searches(self, uid: int) -> int:
        return self.db.x("DELETE FROM search_history WHERE user_id = ?", (int(uid),)).rowcount

    def count_searches_24h(self) -> int:
        return int(self.db.q1(
            "SELECT COUNT(*) AS n FROM search_history WHERE created_at >= ?", (utc_cutoff(hours=24),)
        )["n"] or 0)

    def user_count_searches_24h(self, uid: int) -> int:
        return int(self.db.q1(
            "SELECT COUNT(*) AS n FROM search_history WHERE user_id = ? AND created_at >= ?",
            (int(uid), utc_cutoff(hours=24)),
        )["n"] or 0)

    # ------------------------------------------------------------------
    # سیگنال‌ها
    # ------------------------------------------------------------------
    def save_signals(self, signals: Sequence[Dict[str, Any]]) -> List[int]:
        ids: List[int] = []
        if not signals:
            return ids
        with self.db.transaction() as tx:
            for s in signals:
                try:
                    tx.execute(
                        "INSERT INTO structural_signals(symbol, exchange, timeframe, strategy_key, strategy_title, "
                        "strategy_type, signal_type, direction, price, candle_ts, details, is_active) "
                        "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1) ON CONFLICT DO NOTHING",
                        (
                            s.get("symbol", ""), s.get("exchange", ""), s.get("timeframe", ""),
                            s.get("strategy_key", ""), s.get("strategy_title") or s.get("strategy") or "",
                            s.get("strategy_type", ""),
                            s.get("signal_type", ""), s.get("direction", "LONG"), float(s.get("price") or 0),
                            float(s.get("candle_ts") or 0), s.get("details", ""),
                        ),
                    )
                except Exception:
                    log.exception(
                        "signal insert rejected: %s %s %s %s",
                        s.get("symbol", ""), s.get("exchange", ""),
                        s.get("timeframe", ""), s.get("strategy_key", ""),
                    )
                    continue
                row = tx.execute(
                    "SELECT id FROM structural_signals WHERE symbol = ? AND exchange = ? AND timeframe = ? "
                    "AND strategy_key = ? AND candle_ts = ?",
                    (
                        s.get("symbol", ""), s.get("exchange", ""), s.get("timeframe", ""),
                        s.get("strategy_key", ""), float(s.get("candle_ts") or 0),
                    ),
                ).fetchone()
                if row:
                    ids.append(int(row["id"]))
        return ids

    def signal_rows(
        self, symbol: str = "", exchange: str = "", timeframe: str = "", limit: int = 100,
        offset: int = 0,
    ) -> Tuple[List[Dict[str, Any]], int]:
        where, params = ["is_active = 1"], []
        if symbol:
            where.append("symbol = ?")
            params.append(symbol)
        if exchange:
            where.append("exchange = ?")
            params.append(exchange)
        if timeframe:
            where.append("timeframe = ?")
            params.append(timeframe)
        cond = " WHERE " + " AND ".join(where)
        total = int(self.db.q1("SELECT COUNT(*) AS n FROM structural_signals" + cond, params)["n"] or 0)
        rows = self.db.q(
            "SELECT * FROM structural_signals" + cond + " ORDER BY candle_ts DESC, id DESC LIMIT ? OFFSET ?",
            params + [int(limit), int(offset)],
        )
        return [self.signal_public(r) for r in rows], total

    def signal_public(self, row: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "id": int(row["id"]),
            "symbol": row.get("symbol") or "",
            "exchange": row.get("exchange") or "",
            "timeframe": row.get("timeframe") or "",
            "strategy_key": row.get("strategy_key") or "",
            "strategy": row.get("strategy_title") or "",
            "strategy_type": row.get("strategy_type") or "",
            "signal_type": row.get("signal_type") or "",
            "direction": row.get("direction") or "LONG",
            "price": float(row.get("price") or 0),
            "candle_ts": float(row.get("candle_ts") or 0),
            "date": jalali_dt(float(row.get("candle_ts") or 0)) if row.get("candle_ts") else "-",
            "detected_at": jalali_from_db(row.get("detected_at")),
            "details": row.get("details") or "",
        }

    def latest_signals(self, limit: int = 12) -> List[Dict[str, Any]]:
        rows = self.db.q(
            "SELECT * FROM structural_signals WHERE is_active = 1 ORDER BY candle_ts DESC, id DESC LIMIT ?",
            (int(limit),),
        )
        return [self.signal_public(r) for r in rows]

    def signals_after(self, last_id: int, limit: int = 200) -> List[Dict[str, Any]]:
        rows = self.db.q(
            "SELECT * FROM structural_signals WHERE id > ? AND is_active = 1 ORDER BY id LIMIT ?",
            (int(last_id), int(limit)),
        )
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # صرافی‌ها
    # ------------------------------------------------------------------
    def exchange_rows(self, visible_only: bool = False, active_only: bool = False) -> List[Dict[str, Any]]:
        where, params = [], []
        if visible_only:
            where.append("is_visible = 1")
        if active_only:
            where.append("is_active = 1")
        cond = (" WHERE " + " AND ".join(where)) if where else ""
        rows = self.db.q("SELECT * FROM exchanges" + cond + " ORDER BY priority, id", params)
        return [dict(r) for r in rows]

    def exchange(self, slug: str) -> Optional[Dict[str, Any]]:
        return self.db.q1("SELECT * FROM exchanges WHERE slug = ?", (slug,))

    def save_exchange(self, data: Dict[str, Any], eid: Optional[int] = None) -> Optional[int]:
        name = str(data.get("name") or "").strip()
        slug = str(data.get("slug") or "").strip().lower()
        if not name or not slug:
            return None
        fields = (
            name, slug, str(data.get("ccxt_id") or "").strip(), str(data.get("api_url") or "").strip(),
            int(data.get("priority") or 100), 1 if data.get("is_active", True) else 0,
            1 if data.get("is_visible", True) else 0,
        )
        if eid:
            self.db.x(
                "UPDATE exchanges SET name=?, slug=?, ccxt_id=?, api_url=?, priority=?, is_active=?, is_visible=? "
                "WHERE id=?",
                fields + (int(eid),),
            )
            return int(eid)
        existing = self.exchange(slug)
        if existing:
            return None
        return self.db.insert_id(
            "INSERT INTO exchanges(name, slug, ccxt_id, api_url, priority, is_active, is_visible) "
            "VALUES(?, ?, ?, ?, ?, ?, ?)",
            fields,
        )

    def set_exchange_flag(self, eid: int, field: str, value: bool) -> bool:
        if field not in ("is_active", "is_visible"):
            return False
        return self.db.x(f"UPDATE exchanges SET {field} = ? WHERE id = ?", (1 if value else 0, int(eid))).rowcount > 0

    def delete_exchange(self, eid: int) -> bool:
        return self.db.x("DELETE FROM exchanges WHERE id = ?", (int(eid),)).rowcount > 0

    # ------------------------------------------------------------------
    # پرداخت‌ها (وب‌اپ)
    # ------------------------------------------------------------------
    def payment_public(self, p: Dict[str, Any]) -> Dict[str, Any]:
        status = p.get("status") or "pending"
        status_fa = {"approved": "تأیید شده", "pending": "در انتظار", "suspended": "معلق", "rejected": "رد شده"}
        method = p.get("method") or "manual"
        service = p.get("service_type") or "bot"
        method_fa = {"bsc": "BSC (خودکار)", "auto": "خودکار (بلاک‌چین)", "manual": "دستی (رسید)"}.get(
            method, method
        )
        return {
            "id": int(p["id"]),
            "user_id": int(p["user_id"]),
            "amount": float(p.get("amount") or 0),
            "expected_amount": float(p.get("expected_amount") or 0),
            "status": status,
            "status_fa": status_fa.get(status, status),
            "method": method,
            "method_fa": method_fa,
            "service_type": service,
            "service_fa": "تحلیل ساختاری" if service == "webapp" else "ربات",
            "network": p.get("network") or "",
            "asset": str((plans.NETWORK_BY_KEY.get(str(p.get("network") or "")) or {}).get("asset") or ""),
            "tx_hash": p.get("tx_hash") or "",
            "sender": p.get("sender_address") or "",
            "receiver": p.get("receiver_address") or "",
            "note": p.get("admin_note") or "",
            "request_id": int(p.get("request_id") or 0),
            "created_at": jalali_from_db(p.get("created_at")),
        }

    def payment_rows(
        self, service: str = "webapp", status: str = "all", offset: int = 0, limit: int = 20,
    ) -> Tuple[List[Dict[str, Any]], int]:
        where, params = [], []
        if service and service != "all":
            where.append("service_type = ?")
            params.append(service)
        if status and status != "all":
            where.append("status = ?")
            params.append(status)
        cond = (" WHERE " + " AND ".join(where)) if where else ""
        total = int(self.db.q1("SELECT COUNT(*) AS n FROM payments_history" + cond, params)["n"] or 0)
        rows = self.db.q(
            "SELECT * FROM payments_history" + cond + " ORDER BY id DESC LIMIT ? OFFSET ?",
            params + [int(limit), int(offset)],
        )
        return [self.payment_public(r) for r in rows], total

    def payment(self, pid: int) -> Optional[Dict[str, Any]]:
        row = self.db.q1("SELECT * FROM payments_history WHERE id = ?", (int(pid),))
        return self.payment_public(row) if row else None

    # ------------------------------------------------------------------
    # آمار
    # ------------------------------------------------------------------
    def stats(self) -> Dict[str, Any]:
        now = time.time()
        total_users = int(self.db.q1("SELECT COUNT(*) AS n FROM users")["n"] or 0)
        web_users = int(self.db.q1("SELECT COUNT(*) AS n FROM webapp_users")["n"] or 0)
        web_active = int(self.db.q1(
            "SELECT COUNT(*) AS n FROM users WHERE webapp_sub_until > ?", (now,)
        )["n"] or 0)
        bot_active = int(self.db.q1(
            "SELECT COUNT(*) AS n FROM users WHERE sub_until > ?", (now,)
        )["n"] or 0)
        revenue = self.db.q1(
            "SELECT COALESCE(SUM(amount), 0) AS s FROM payments_history WHERE status = 'approved' AND service_type = 'webapp'"
        )
        revenue_bot = self.db.q1(
            "SELECT COALESCE(SUM(amount), 0) AS s FROM payments_history WHERE status = 'approved' AND service_type = 'bot'"
        )
        pending = int(self.db.q1(
            "SELECT COUNT(*) AS n FROM payments_history WHERE status = 'pending'"
        )["n"] or 0)
        return {
            "total_users": total_users,
            "web_users": web_users,
            "web_active": web_active,
            "bot_active": bot_active,
            "revenue": float(revenue["s"] or 0),
            "revenue_bot": float(revenue_bot["s"] or 0),
            "pending_payments": pending,
            "searches_24h": self.count_searches_24h(),
            "strategies": int(self.db.q1("SELECT COUNT(*) AS n FROM structural_strategies WHERE is_active = 1")["n"] or 0),
            "categories": int(self.db.q1("SELECT COUNT(DISTINCT category) AS n FROM structural_strategies")["n"] or 0),
        }

    # ------------------------------------------------------------------
    # پیام‌ها
    # ------------------------------------------------------------------
    def log_message(self, admin_id: int, target_type: str, filter_json: str, text: str, sent: int, failed: int) -> int:
        return self.db.insert_id(
            "INSERT INTO webapp_messages(admin_id, target_type, filter_json, text, sent, failed) "
            "VALUES(?, ?, ?, ?, ?, ?)",
            (int(admin_id), target_type, filter_json, text, int(sent), int(failed)),
        )

    def message_rows(self, limit: int = 30) -> List[Dict[str, Any]]:
        return self.db.q(
            "SELECT * FROM webapp_messages ORDER BY id DESC LIMIT ?", (int(limit),)
        )

    def users_for_message(self, target: str, sub_filter: str = "all", joined_days: int = 0) -> List[int]:
        where, params = ["COALESCE(w.password_hash,'') <> ''"], []
        if sub_filter == "active":
            where.append("u.webapp_sub_until > ?")
            params.append(time.time())
        elif sub_filter == "expired":
            where.append("u.webapp_sub_until > 0 AND u.webapp_sub_until <= ?")
            params.append(time.time())
        elif sub_filter == "none":
            where.append("COALESCE(u.webapp_sub_until,0) <= 0")
        if joined_days > 0:
            where.append("u.joined_at >= ?")
            params.append(utc_cutoff(days=joined_days))
        cond = " WHERE " + " AND ".join(where)
        rows = self.db.q(
            "SELECT u.user_id FROM users u LEFT JOIN webapp_users w ON w.user_id = u.user_id" + cond
            + " ORDER BY u.user_id",
            params,
        )
        ids = [int(r["user_id"]) for r in rows]
        if target == "admin" and config.ADMIN_UID not in ids:
            ids.append(config.ADMIN_UID)
        return ids


repo = Repo()
