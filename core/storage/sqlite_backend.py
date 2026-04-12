"""
SQLite backend for bot persistence.

Goal: replace JSON files with transactional storage while keeping existing public APIs
in modules like `user_storage.py` and `core.support.issue_binding_registry.py`.

The DB file is local (works on Windows dev and Linux prod).
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
import re


_DB_LOCK = threading.Lock()
_DB_INITIALIZED = False
_DB_THREAD_LOCAL = threading.local()
_ISSUE_KEY_RE = re.compile(r"^[A-Z][A-Z0-9]+-\d+$")


def _db_path() -> Path:
    p = (os.getenv("SQLITE_PATH") or "").strip()
    if p:
        return Path(p)
    return Path(__file__).resolve().parents[2] / "data" / "storage.sqlite3"


def _connect_new() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=5, isolation_level=None)  # autocommit; we use BEGIN explicitly
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    # Reduce lock errors and WAL spikes; tuneable via env.
    try:
        conn.execute(f"PRAGMA busy_timeout={int(os.getenv('SQLITE_BUSY_TIMEOUT_MS') or '5000')};")
    except Exception:
        pass
    try:
        conn.execute(f"PRAGMA wal_autocheckpoint={int(os.getenv('SQLITE_WAL_AUTOCHECKPOINT_PAGES') or '1000')};")
    except Exception:
        pass
    if (os.getenv("SQLITE_TEMP_STORE_MEMORY") or "").strip().lower() in ("1", "true", "yes", "on"):
        try:
            conn.execute("PRAGMA temp_store=MEMORY;")
        except Exception:
            pass
    # cache_size: negative means KB. Example: -65536 == 64MB.
    raw_cache = (os.getenv("SQLITE_CACHE_SIZE") or "").strip()
    if raw_cache:
        try:
            conn.execute(f"PRAGMA cache_size={int(raw_cache)};")
        except Exception:
            pass
    return conn


def _connect() -> sqlite3.Connection:
    """
    Thread-local persistent connection.
    This avoids connect/close overhead on every operation and plays well with asyncio.to_thread().
    """
    conn = getattr(_DB_THREAD_LOCAL, "conn", None)
    if conn is not None:
        try:
            if conn:
                return conn
        except Exception:
            pass
    conn = _connect_new()
    _DB_THREAD_LOCAL.conn = conn
    return conn


def init_db() -> None:
    global _DB_INITIALIZED
    if _DB_INITIALIZED:
        return
    with _DB_LOCK:
        if _DB_INITIALIZED:
            return
        # Use a fresh connection for migrations/schema creation.
        # Do not reuse thread-local connections here to avoid cross-thread issues.
        conn = _connect_new()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    telegram_id       INTEGER PRIMARY KEY,
                    full_name         TEXT,
                    login             TEXT,
                    email             TEXT,
                    phone             TEXT,
                    phone_norm        TEXT,
                    employee_id       TEXT,
                    department        TEXT,
                    department_wms    TEXT,
                    jira_username     TEXT,
                    position          TEXT,
                    phone_needs_verification INTEGER DEFAULT 0,
                    registered_at     REAL
                )
                """
            )
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_login ON users(lower(login))")
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email ON users(lower(email))")
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_employee_id ON users(employee_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_users_phone_norm ON users(phone_norm)")

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS max_links (
                    max_user_id  INTEGER PRIMARY KEY,
                    telegram_id  INTEGER NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE
                )
                """
            )

            # Persist MAX personal chat_id for reliable notifications after restart.
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS max_chat_ids (
                    max_user_id INTEGER PRIMARY KEY,
                    chat_id     TEXT NOT NULL,
                    updated_at  REAL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_max_chat_ids_chat_id ON max_chat_ids(chat_id)")

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS issue_bindings (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    issue_key       TEXT NOT NULL,
                    project_key     TEXT,
                    ticket_type_id  TEXT,
                    channel_id      TEXT NOT NULL,
                    channel_user_id INTEGER NOT NULL,
                    created_at      REAL,
                    UNIQUE (issue_key, channel_id, channel_user_id)
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_bindings_issue ON issue_bindings(issue_key)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_bindings_user ON issue_bindings(channel_user_id, channel_id)")

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS issue_notification_state (
                    issue_key          TEXT PRIMARY KEY,
                    last_status        TEXT,
                    last_comment_count INTEGER,
                    last_assignee      TEXT,
                    notified_comment_ids TEXT,
                    updated_at         REAL
                )
                """
            )

            # Lightweight migrations for existing DBs.
            try:
                ucols = {str(r["name"]) for r in conn.execute("PRAGMA table_info(users)").fetchall()}
                if "position" not in ucols:
                    conn.execute("ALTER TABLE users ADD COLUMN position TEXT")
            except Exception:
                pass
            try:
                cols = {str(r["name"]) for r in conn.execute("PRAGMA table_info(issue_notification_state)").fetchall()}
                if "last_assignee" not in cols:
                    conn.execute("ALTER TABLE issue_notification_state ADD COLUMN last_assignee TEXT")
                if "notified_comment_ids" not in cols:
                    conn.execute("ALTER TABLE issue_notification_state ADD COLUMN notified_comment_ids TEXT")
            except Exception:
                # Don't break startup if migration fails; worst case is fallback to non-persisted fields.
                pass

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS pending_password_requests (
                    issue_key          TEXT PRIMARY KEY,
                    user_id            INTEGER NOT NULL,
                    channel_id         TEXT NOT NULL,
                    last_comment_count INTEGER,
                    created_at         REAL
                )
                """
            )
        finally:
            conn.close()
        _DB_INITIALIZED = True


def sanitize_issue_bindings(*, save: bool = True) -> Dict[str, int]:
    """
    Sanitize issue bindings storage.
    - For SQLite: normalize issue_key to UPPER(TRIM), remove obviously invalid records.
    - For JSON: handled by core.support.issue_binding_registry.sanitize_registry().
    """
    init_db()
    if not save:
        # still run read-only validation but avoid writes
        pass
    conn = _connect()
    before = int(conn.execute("SELECT COUNT(*) AS c FROM issue_bindings").fetchone()["c"])
    removed = 0
    fixed = 0
    with _DB_LOCK:
        conn = _connect()
        try:
            conn.execute("BEGIN")
            # Normalize issue_key and channel_id
            cur = conn.execute("SELECT id, issue_key, channel_id, channel_user_id, created_at FROM issue_bindings")
            rows = cur.fetchall()
            for r in rows:
                rid = int(r["id"])
                issue_key = (str(r["issue_key"] or "").strip().upper())
                channel_id = (str(r["channel_id"] or "").strip())
                try:
                    channel_user_id = int(r["channel_user_id"])
                except Exception:
                    channel_user_id = -1

                if not issue_key or not channel_id or channel_user_id < 0 or not _ISSUE_KEY_RE.match(issue_key):
                    if save:
                        conn.execute("DELETE FROM issue_bindings WHERE id=?", (rid,))
                    removed += 1
                    continue

                created_at = r["created_at"]
                if not created_at:
                    created_at = float(time.time())
                    fixed += 1

                # Update if changed
                if issue_key != (r["issue_key"] or "") or channel_id != (r["channel_id"] or "") or created_at != r["created_at"]:
                    fixed += 1
                    if save:
                        conn.execute(
                            "UPDATE issue_bindings SET issue_key=?, channel_id=?, channel_user_id=?, created_at=? WHERE id=?",
                            (issue_key, channel_id, channel_user_id, float(created_at), rid),
                        )
            if save:
                conn.execute("COMMIT")
            else:
                conn.execute("ROLLBACK")
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            raise

    after = int(conn.execute("SELECT COUNT(*) AS c FROM issue_bindings").fetchone()["c"])
    return {"before": before, "after": after, "removed": removed, "fixed": fixed}


# ---------------------------------------------------------------------------
# MAX chat ids (max_user_id -> chat_id)
# ---------------------------------------------------------------------------


def upsert_max_chat_id(max_user_id: int, chat_id: str) -> None:
    init_db()
    cid = (chat_id or "").strip()
    if not cid or cid == "0":
        return
    with _DB_LOCK:
        conn = _connect()
        conn.execute(
                """
                INSERT INTO max_chat_ids(max_user_id, chat_id, updated_at)
                VALUES(?,?,?)
                ON CONFLICT(max_user_id) DO UPDATE SET
                    chat_id=excluded.chat_id,
                    updated_at=excluded.updated_at
                """,
                (int(max_user_id), cid, float(time.time())),
            )


def get_max_chat_id(max_user_id: int) -> Optional[str]:
    init_db()
    conn = _connect()
    row = conn.execute("SELECT chat_id FROM max_chat_ids WHERE max_user_id=?", (int(max_user_id),)).fetchone()
    return str(row["chat_id"]) if row and row["chat_id"] else None


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------


def upsert_user(telegram_id: int, profile: Dict[str, Any]) -> None:
    init_db()
    now = time.time()
    tid = int(telegram_id)
    with _DB_LOCK:
        conn = _connect()
        try:
            conn.execute("BEGIN")
            row = conn.execute("SELECT * FROM users WHERE telegram_id=?", (tid,)).fetchone()
            base = dict(row) if row else {}
            merged = dict(base)
            merged.update(profile)
            reg = merged.get("registered_at")
            registered_at = float(reg) if reg is not None else now
            conn.execute(
                """
                INSERT INTO users(
                    telegram_id, full_name, login, email, phone, phone_norm,
                    employee_id, department, department_wms, jira_username,
                    position,
                    phone_needs_verification, registered_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(telegram_id) DO UPDATE SET
                    full_name=excluded.full_name,
                    login=excluded.login,
                    email=excluded.email,
                    phone=excluded.phone,
                    phone_norm=excluded.phone_norm,
                    employee_id=excluded.employee_id,
                    department=excluded.department,
                    department_wms=excluded.department_wms,
                    jira_username=excluded.jira_username,
                    position=excluded.position,
                    phone_needs_verification=excluded.phone_needs_verification
                """,
                (
                    tid,
                    merged.get("full_name"),
                    merged.get("login"),
                    merged.get("email"),
                    merged.get("phone"),
                    merged.get("phone_norm"),
                    merged.get("employee_id"),
                    merged.get("department"),
                    merged.get("department_wms"),
                    merged.get("jira_username"),
                    merged.get("position"),
                    1 if merged.get("phone_needs_verification") else 0,
                    registered_at,
                ),
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise


def get_user(telegram_id: int) -> Optional[Dict[str, Any]]:
    init_db()
    conn = _connect()
    row = conn.execute("SELECT * FROM users WHERE telegram_id=?", (int(telegram_id),)).fetchone()
    return dict(row) if row else None


def delete_user(telegram_id: int) -> bool:
    init_db()
    with _DB_LOCK:
        conn = _connect()
        cur = conn.execute("DELETE FROM users WHERE telegram_id=?", (int(telegram_id),))
        return cur.rowcount > 0


def find_user_id_by_login(login: str) -> Optional[int]:
    init_db()
    login = (login or "").strip().lower()
    if not login:
        return None
    conn = _connect()
    row = conn.execute("SELECT telegram_id FROM users WHERE lower(login)=?", (login,)).fetchone()
    return int(row["telegram_id"]) if row else None


def find_user_id_by_email(email: str) -> Optional[int]:
    init_db()
    email = (email or "").strip().lower()
    if not email:
        return None
    conn = _connect()
    row = conn.execute("SELECT telegram_id FROM users WHERE lower(email)=?", (email,)).fetchone()
    return int(row["telegram_id"]) if row else None


def find_user_id_by_employee_id(employee_id: str) -> Optional[int]:
    init_db()
    eid = (employee_id or "").strip()
    if not eid:
        return None
    conn = _connect()
    row = conn.execute("SELECT telegram_id FROM users WHERE employee_id=?", (eid,)).fetchone()
    return int(row["telegram_id"]) if row else None


def find_user_id_by_phone_norm(phone_norm: str) -> Optional[int]:
    init_db()
    key = (phone_norm or "").strip()
    if not key:
        return None
    conn = _connect()
    row = conn.execute("SELECT telegram_id FROM users WHERE phone_norm=?", (key,)).fetchone()
    return int(row["telegram_id"]) if row else None


def list_users() -> List[Tuple[int, Dict[str, Any]]]:
    init_db()
    conn = _connect()
    rows = conn.execute("SELECT * FROM users").fetchall()
    return [(int(r["telegram_id"]), dict(r)) for r in rows]


def find_users_by_jira_username(jira_username: str) -> List[int]:
    init_db()
    ju = (jira_username or "").strip().lower()
    if not ju:
        return []
    conn = _connect()
    rows = conn.execute("SELECT telegram_id FROM users WHERE lower(jira_username)=?", (ju,)).fetchall()
    return [int(r["telegram_id"]) for r in rows]


# ---------------------------------------------------------------------------
# MAX links
# ---------------------------------------------------------------------------


def link_max_user(max_user_id: int, telegram_id: int) -> None:
    init_db()
    with _DB_LOCK:
        conn = _connect()
        conn.execute(
            "INSERT INTO max_links(max_user_id, telegram_id) VALUES(?,?) "
            "ON CONFLICT(max_user_id) DO UPDATE SET telegram_id=excluded.telegram_id",
            (int(max_user_id), int(telegram_id)),
        )


def resolve_telegram_id_from_max(max_user_id: int) -> Optional[int]:
    init_db()
    conn = _connect()
    row = conn.execute("SELECT telegram_id FROM max_links WHERE max_user_id=?", (int(max_user_id),)).fetchone()
    return int(row["telegram_id"]) if row else None


def list_max_user_ids_by_telegram(telegram_id: int) -> List[int]:
    init_db()
    conn = _connect()
    rows = conn.execute("SELECT max_user_id FROM max_links WHERE telegram_id=?", (int(telegram_id),)).fetchall()
    return [int(r["max_user_id"]) for r in rows]


# ---------------------------------------------------------------------------
# Issue bindings
# ---------------------------------------------------------------------------


def add_issue_binding(
    channel_id: str,
    channel_user_id: int,
    issue_key: str,
    project_key: str,
    ticket_type_id: str,
    created_at: Optional[float] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    init_db()
    key = (issue_key or "").strip().upper()
    if not key:
        return
    with _DB_LOCK:
        conn = _connect()
        conn.execute(
                """
                INSERT OR IGNORE INTO issue_bindings(
                    issue_key, project_key, ticket_type_id, channel_id, channel_user_id, created_at
                ) VALUES(?,?,?,?,?,?)
                """,
                (
                    key,
                    (project_key or "").strip(),
                    (ticket_type_id or "").strip(),
                    (channel_id or "").strip().lower(),
                    int(channel_user_id),
                    float(created_at or (extra or {}).get("created_at") or time.time()),
                ),
            )


def get_issue_bindings_by_user(channel_id: str, channel_user_id: int) -> List[Dict[str, Any]]:
    init_db()
    conn = _connect()
    rows = conn.execute(
        "SELECT * FROM issue_bindings WHERE channel_id=? AND channel_user_id=? ORDER BY created_at DESC",
        ((channel_id or "").strip().lower(), int(channel_user_id)),
    ).fetchall()
    return [dict(r) for r in rows]


def get_issue_bindings_by_issue(issue_key: str) -> List[Dict[str, Any]]:
    init_db()
    key = (issue_key or "").strip().upper()
    if not key:
        return []
    conn = _connect()
    rows = conn.execute("SELECT * FROM issue_bindings WHERE issue_key=?", (key,)).fetchall()
    return [dict(r) for r in rows]


def get_issue_user_ids_by_issue(issue_key: str) -> List[Tuple[str, int]]:
    return [(r["channel_id"], int(r["channel_user_id"])) for r in get_issue_bindings_by_issue(issue_key)]


def list_all_issue_keys() -> List[str]:
    init_db()
    conn = _connect()
    rows = conn.execute("SELECT DISTINCT issue_key FROM issue_bindings").fetchall()
    return sorted([str(r["issue_key"]).strip().upper() for r in rows if r["issue_key"]])


def list_all_issue_bindings() -> List[Dict[str, Any]]:
    init_db()
    conn = _connect()
    rows = conn.execute("SELECT * FROM issue_bindings").fetchall()
    return [dict(r) for r in rows]


def remove_issue_binding(issue_key: str, channel_id: str, channel_user_id: int) -> bool:
    init_db()
    key = (issue_key or "").strip().upper()
    with _DB_LOCK:
        conn = _connect()
        cur = conn.execute(
            "DELETE FROM issue_bindings WHERE issue_key=? AND channel_id=? AND channel_user_id=?",
            (key, (channel_id or "").strip().lower(), int(channel_user_id)),
        )
        return cur.rowcount > 0


def remove_issue_bindings_by_issue(issue_key: str) -> int:
    init_db()
    key = (issue_key or "").strip().upper()
    if not key:
        return 0
    with _DB_LOCK:
        conn = _connect()
        cur = conn.execute("DELETE FROM issue_bindings WHERE issue_key=?", (key,))
        return int(cur.rowcount or 0)


# ---------------------------------------------------------------------------
# Notification state
# ---------------------------------------------------------------------------


def get_notification_state(issue_key: str) -> Optional[Dict[str, Any]]:
    init_db()
    key = (issue_key or "").strip().upper()
    if not key:
        return None
    conn = _connect()
    row = conn.execute("SELECT * FROM issue_notification_state WHERE issue_key=?", (key,)).fetchone()
    return dict(row) if row else None


def upsert_notification_state(issue_key: str, last_status: Optional[str], last_comment_count: Optional[int]) -> None:
    init_db()
    key = (issue_key or "").strip().upper()
    if not key:
        return
    with _DB_LOCK:
        conn = _connect()
        conn.execute(
            """
            INSERT INTO issue_notification_state(issue_key, last_status, last_comment_count, updated_at)
            VALUES(?,?,?,?)
            ON CONFLICT(issue_key) DO UPDATE SET
                last_status=excluded.last_status,
                last_comment_count=excluded.last_comment_count,
                updated_at=excluded.updated_at
            """,
            (key, last_status, last_comment_count, float(time.time())),
        )


def upsert_notification_states_bulk(data: Dict[str, Dict[str, Any]]) -> None:
    """
    Быстрый bulk-upsert состояния уведомлений одним соединением/транзакцией.
    Используется фоновой задачей, чтобы не блокировать event-loop множеством open/close.
    """
    init_db()
    if not data:
        return
    now = float(time.time())
    rows: List[Tuple[str, Optional[str], Optional[int], Optional[str], Optional[str], float]] = []
    for issue_key, v in (data or {}).items():
        key = (issue_key or "").strip().upper()
        if not key or not isinstance(v, dict):
            continue
        last_assignee = (v.get("last_assignee") or None)
        if isinstance(last_assignee, str):
            last_assignee = last_assignee.strip().lower() or None
        notified = v.get("notified_comment_ids")
        notified_json: Optional[str] = None
        if isinstance(notified, list):
            try:
                import json as _json
                notified_json = _json.dumps(notified, ensure_ascii=False)
            except Exception:
                notified_json = None
        rows.append((key, v.get("last_status"), v.get("last_comment_count"), last_assignee, notified_json, now))
    if not rows:
        return
    with _DB_LOCK:
        conn = _connect()
        try:
            conn.execute("BEGIN")
            conn.executemany(
                """
                INSERT INTO issue_notification_state(issue_key, last_status, last_comment_count, last_assignee, notified_comment_ids, updated_at)
                VALUES(?,?,?,?,?,?)
                ON CONFLICT(issue_key) DO UPDATE SET
                    last_status=excluded.last_status,
                    last_comment_count=excluded.last_comment_count,
                    last_assignee=excluded.last_assignee,
                    notified_comment_ids=excluded.notified_comment_ids,
                    updated_at=excluded.updated_at
                """,
                rows,
            )
            conn.execute("COMMIT")
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            raise


def list_notification_states() -> Dict[str, Dict[str, Any]]:
    """
    Возвращает все строки issue_notification_state как dict[ISSUE_KEY] -> row_dict.
    Нужен, чтобы не делать N запросов из фоновых циклов (которые могут тормозить event-loop).
    """
    init_db()
    conn = _connect()
    rows = conn.execute("SELECT * FROM issue_notification_state").fetchall()
    out: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        key = (str(r["issue_key"]) if r["issue_key"] is not None else "").strip().upper()
        if key:
            row = dict(r)
            # Normalize notified_comment_ids JSON back to list for callers.
            raw = row.get("notified_comment_ids")
            if isinstance(raw, str) and raw.strip():
                try:
                    import json as _json
                    parsed = _json.loads(raw)
                    row["notified_comment_ids"] = parsed if isinstance(parsed, list) else []
                except Exception:
                    row["notified_comment_ids"] = []
            elif raw is None:
                row["notified_comment_ids"] = []
            out[key] = row
    return out


# ---------------------------------------------------------------------------
# Pending password requests
# ---------------------------------------------------------------------------


def add_pending_password(issue_key: str, user_id: int, channel_id: str) -> None:
    init_db()
    key = (issue_key or "").strip().upper()
    if not key:
        return
    with _DB_LOCK:
        conn = _connect()
        conn.execute(
            """
            INSERT INTO pending_password_requests(issue_key, user_id, channel_id, last_comment_count, created_at)
            VALUES(?,?,?,?,?)
            ON CONFLICT(issue_key) DO UPDATE SET
                user_id=excluded.user_id,
                channel_id=excluded.channel_id
            """,
            (key, int(user_id), (channel_id or "telegram").strip().lower(), None, float(time.time())),
        )


def list_pending_password() -> List[Tuple[str, int, str]]:
    init_db()
    conn = _connect()
    rows = conn.execute("SELECT issue_key, user_id, channel_id FROM pending_password_requests").fetchall()
    return [(str(r["issue_key"]), int(r["user_id"]), str(r["channel_id"])) for r in rows]


def get_pending_password_raw() -> Dict[str, Dict[str, Any]]:
    init_db()
    conn = _connect()
    rows = conn.execute("SELECT * FROM pending_password_requests").fetchall()
    out: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        out[str(r["issue_key"]).strip().upper()] = dict(r)
    return out


def set_pending_password_last_comment_count(issue_key: str, count: int) -> None:
    init_db()
    key = (issue_key or "").strip().upper()
    if not key:
        return
    with _DB_LOCK:
        conn = _connect()
        conn.execute(
            "UPDATE pending_password_requests SET last_comment_count=? WHERE issue_key=?",
            (int(count), key),
        )


def remove_pending_password(issue_key: str) -> None:
    init_db()
    key = (issue_key or "").strip().upper()
    if not key:
        return
    with _DB_LOCK:
        conn = _connect()
        conn.execute("DELETE FROM pending_password_requests WHERE issue_key=?", (key,))

