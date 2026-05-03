"""SQLite database access and schema."""

from __future__ import annotations

import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from werkzeug.security import generate_password_hash

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "itsm.db"

# Applied on every new thread-local connection (sync routes run in a worker thread).
_SCHEMA_SQL = """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'user'
            );

            CREATE TABLE IF NOT EXISTS asset_types (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                description TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS inventory_assets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                asset_type_id INTEGER NOT NULL REFERENCES asset_types(id),
                hostname TEXT NOT NULL,
                ip_address TEXT NOT NULL DEFAULT '',
                group_name TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS incidents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                public_id TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                severity TEXT NOT NULL CHECK (severity IN ('low','medium','high','critical')),
                status TEXT NOT NULL CHECK (status IN ('open','closed')),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                closed_at TEXT,
                inventory_asset_id INTEGER REFERENCES inventory_assets(id)
            );

            CREATE TABLE IF NOT EXISTS comments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                incident_id INTEGER NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
                author_user_id INTEGER NOT NULL REFERENCES users(id),
                body TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS incident_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                incident_id INTEGER NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
                event_type TEXT NOT NULL,
                payload TEXT NOT NULL DEFAULT '{}',
                actor_user_id INTEGER NOT NULL REFERENCES users(id),
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS kb_articles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_incidents_status ON incidents(status);
            CREATE INDEX IF NOT EXISTS idx_incidents_severity ON incidents(severity);
            CREATE INDEX IF NOT EXISTS idx_comments_incident ON comments(incident_id);
            CREATE INDEX IF NOT EXISTS idx_events_incident ON incident_events(incident_id);
            CREATE INDEX IF NOT EXISTS idx_inventory_type ON inventory_assets(asset_type_id);
"""

_local = threading.local()


def db_path() -> Path:
    return Path(os.environ.get("ITSM_DATABASE", str(DEFAULT_DB_PATH)))


def get_connection() -> sqlite3.Connection:
    """Thread-local SQLite connection."""
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not getattr(_local, "conn", None):
        _local.conn = sqlite3.connect(str(path), check_same_thread=False)
        _local.conn.row_factory = sqlite3.Row
        _local.conn.execute("PRAGMA foreign_keys = ON")
        _local.conn.executescript(_SCHEMA_SQL)
        _local.conn.commit()
    return _local.conn


@contextmanager
def cursor() -> Iterator[sqlite3.Cursor]:
    conn = get_connection()
    cur = conn.cursor()
    try:
        yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()


def init_db() -> None:
    with cursor() as cur:
        cur.execute("SELECT 1")

    _migrate_legacy_schema()
    _bootstrap_env_admin()
    from app.services import settings as settings_svc

    settings_svc.seed_defaults()


def _table_columns(cur: sqlite3.Cursor, table: str) -> set[str]:
    cur.execute(f'PRAGMA table_info("{table}")')
    return {row[1] for row in cur.fetchall()}


def _migrate_legacy_schema() -> None:
    """Alter older DB files created before inventory_asset_id existed."""
    with cursor() as cur:
        cols = _table_columns(cur, "incidents")
        if cols and "inventory_asset_id" not in cols:
            cur.execute(
                """
                ALTER TABLE incidents ADD COLUMN inventory_asset_id INTEGER
                REFERENCES inventory_assets(id)
                """
            )


def _bootstrap_env_admin() -> None:
    """Create first admin from env when database has no users."""
    user = os.environ.get("ITSM_BOOTSTRAP_ADMIN_USER", "").strip()
    password = os.environ.get("ITSM_BOOTSTRAP_ADMIN_PASSWORD", "")
    legacy = os.environ.get("ITSM_BOOTSTRAP_ADMIN", "").strip()
    if legacy and ":" in legacy:
        parts = legacy.split(":", 1)
        if len(parts) == 2:
            user = parts[0].strip()
            password = parts[1]

    if not user or password is None or password == "":
        return

    with cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM users")
        if cur.fetchone()[0] > 0:
            return
        h = generate_password_hash(password)
        cur.execute(
            """
            INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)
            """,
            (user, h, "admin"),
        )


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return dict(row)
