"""User CRUD for administrators."""

from __future__ import annotations

import hashlib
import secrets
from typing import Any

from werkzeug.security import generate_password_hash

from app import db


def _hash_mcp_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def generate_mcp_token() -> str:
    return secrets.token_urlsafe(32)


def list_users() -> list[dict[str, Any]]:
    with db.cursor() as cur:
        cur.execute(
            """
            SELECT id, username, role,
                   CASE WHEN mcp_token_hash IS NOT NULL THEN 1 ELSE 0 END AS has_mcp_token
            FROM users ORDER BY username
            """
        )
        rows = []
        for r in cur.fetchall():
            d = dict(r)
            d["has_mcp_token"] = bool(d.pop("has_mcp_token", 0))
            rows.append(d)
        return rows


def get_user(uid: int) -> dict[str, Any] | None:
    with db.cursor() as cur:
        cur.execute(
            """
            SELECT id, username, role,
                   CASE WHEN mcp_token_hash IS NOT NULL THEN 1 ELSE 0 END AS has_mcp_token
            FROM users WHERE id = ?
            """,
            (uid,),
        )
        row = cur.fetchone()
    if not row:
        return None
    d = dict(row)
    d["has_mcp_token"] = bool(d.pop("has_mcp_token", 0))
    return d


def get_user_by_mcp_token(raw: str) -> dict[str, Any] | None:
    if not raw:
        return None
    h = _hash_mcp_token(raw.strip())
    with db.cursor() as cur:
        cur.execute(
            "SELECT id, username, role FROM users WHERE mcp_token_hash = ?",
            (h,),
        )
        row = cur.fetchone()
    return dict(row) if row else None


def any_user_has_mcp_token() -> bool:
    with db.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM users WHERE mcp_token_hash IS NOT NULL LIMIT 1"
        )
        return cur.fetchone() is not None


def create_user(username: str, password: str, role: str) -> dict[str, Any]:
    h = generate_password_hash(password)
    token = generate_mcp_token()
    token_hash = _hash_mcp_token(token)
    with db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO users (username, password_hash, role, mcp_token_hash)
            VALUES (?, ?, ?, ?)
            """,
            (username.strip(), h, role, token_hash),
        )
        uid = cur.lastrowid
        cur.execute("SELECT id, username, role FROM users WHERE id = ?", (uid,))
        out = dict(cur.fetchone())
    out["mcp_token"] = token
    out["has_mcp_token"] = True
    return out


def regenerate_mcp_token(uid: int) -> dict[str, Any] | None:
    u = get_user(uid)
    if not u:
        return None
    token = generate_mcp_token()
    token_hash = _hash_mcp_token(token)
    with db.cursor() as cur:
        cur.execute(
            "UPDATE users SET mcp_token_hash = ? WHERE id = ?",
            (token_hash, uid),
        )
    out = dict(u)
    out["mcp_token"] = token
    out["has_mcp_token"] = True
    return out


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
    return get_user(uid)


def count_admins() -> int:
    with db.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM users WHERE role = ?", ("admin",))
        return int(cur.fetchone()[0])


def delete_user(uid: int) -> bool:
    with db.cursor() as cur:
        cur.execute("DELETE FROM users WHERE id = ?", (uid,))
        return cur.rowcount > 0
