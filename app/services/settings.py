"""Application settings stored in app_settings (SQLite)."""

from __future__ import annotations

from app import db

KEY_APP_TITLE = "app_title"
DEFAULT_APP_TITLE = "ITSM Demo"


def get_setting(key: str, default: str = "") -> str:
    with db.cursor() as cur:
        cur.execute("SELECT value FROM app_settings WHERE key = ?", (key,))
        row = cur.fetchone()
    if not row:
        return default
    return row[0] or default


def set_setting(key: str, value: str) -> None:
    with db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO app_settings (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )


def get_app_title() -> str:
    return get_setting(KEY_APP_TITLE, DEFAULT_APP_TITLE)


def set_app_title(title: str) -> None:
    set_setting(KEY_APP_TITLE, title.strip() or DEFAULT_APP_TITLE)


def seed_defaults() -> None:
    """Ensure default keys exist."""
    with db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO app_settings (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO NOTHING
            """,
            (KEY_APP_TITLE, DEFAULT_APP_TITLE),
        )
    from app.services.branding import seed_branding_defaults

    seed_branding_defaults()
