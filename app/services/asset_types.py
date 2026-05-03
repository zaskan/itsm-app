"""Asset type catalog (admin-managed)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app import db


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def list_types() -> list[dict[str, Any]]:
    with db.cursor() as cur:
        cur.execute(
            "SELECT * FROM asset_types ORDER BY name COLLATE NOCASE"
        )
        return [dict(r) for r in cur.fetchall()]


def get_type(tid: int) -> dict[str, Any] | None:
    with db.cursor() as cur:
        cur.execute("SELECT * FROM asset_types WHERE id = ?", (tid,))
        row = cur.fetchone()
    return dict(row) if row else None


def create_type(name: str, description: str = "") -> dict[str, Any]:
    now = _now()
    with db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO asset_types (name, description, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            (name.strip(), description.strip(), now, now),
        )
        tid = cur.lastrowid
        cur.execute("SELECT * FROM asset_types WHERE id = ?", (tid,))
        return dict(cur.fetchone())


def update_type(tid: int, name: str | None, description: str | None) -> dict[str, Any] | None:
    t = get_type(tid)
    if not t:
        return None
    new_name = name.strip() if name is not None else t["name"]
    new_desc = description.strip() if description is not None else t["description"]
    now = _now()
    with db.cursor() as cur:
        cur.execute(
            """
            UPDATE asset_types SET name = ?, description = ?, updated_at = ? WHERE id = ?
            """,
            (new_name, new_desc, now, tid),
        )
        cur.execute("SELECT * FROM asset_types WHERE id = ?", (tid,))
        return dict(cur.fetchone())


def delete_type(tid: int) -> bool:
    with db.cursor() as cur:
        cur.execute("DELETE FROM asset_types WHERE id = ?", (tid,))
        return cur.rowcount > 0
