"""User CRUD for administrators."""

from __future__ import annotations

from typing import Any

from werkzeug.security import generate_password_hash

from app import db


def list_users() -> list[dict[str, Any]]:
    with db.cursor() as cur:
        cur.execute("SELECT id, username, role FROM users ORDER BY username")
        return [dict(r) for r in cur.fetchall()]


def get_user(uid: int) -> dict[str, Any] | None:
    with db.cursor() as cur:
        cur.execute(
            "SELECT id, username, role FROM users WHERE id = ?",
            (uid,),
        )
        row = cur.fetchone()
    return dict(row) if row else None


def create_user(username: str, password: str, role: str) -> dict[str, Any]:
    h = generate_password_hash(password)
    with db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)
            """,
            (username.strip(), h, role),
        )
        uid = cur.lastrowid
        cur.execute("SELECT id, username, role FROM users WHERE id = ?", (uid,))
        return dict(cur.fetchone())


def update_user(
    uid: int,
    *,
    username: str | None = None,
    role: str | None = None,
    password: str | None = None,
) -> dict[str, Any] | None:
    u = get_user(uid)
    if not u:
        return None
    new_username = username.strip() if username is not None else u["username"]
    new_role = role if role is not None else u["role"]
    with db.cursor() as cur:
        if password:
            h = generate_password_hash(password)
            cur.execute(
                """
                UPDATE users SET username = ?, role = ?, password_hash = ? WHERE id = ?
                """,
                (new_username, new_role, h, uid),
            )
        else:
            cur.execute(
                "UPDATE users SET username = ?, role = ? WHERE id = ?",
                (new_username, new_role, uid),
            )
        cur.execute("SELECT id, username, role FROM users WHERE id = ?", (uid,))
        return dict(cur.fetchone())


def count_admins() -> int:
    with db.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM users WHERE role = ?", ("admin",))
        return int(cur.fetchone()[0])


def delete_user(uid: int) -> bool:
    with db.cursor() as cur:
        cur.execute("DELETE FROM users WHERE id = ?", (uid,))
        return cur.rowcount > 0
