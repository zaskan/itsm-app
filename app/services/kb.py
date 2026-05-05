"""Knowledge base article storage."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app import db
from app.services import kb_embeddings as kb_emb


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def list_articles(*, q: str | None = None) -> list[dict[str, Any]]:
    with db.cursor() as cur:
        if q:
            like = f"%{q}%"
            cur.execute(
                """
                SELECT * FROM kb_articles
                WHERE title LIKE ? OR description LIKE ?
                ORDER BY updated_at DESC
                """,
                (like, like),
            )
        else:
            cur.execute("SELECT * FROM kb_articles ORDER BY updated_at DESC")
        return [dict(r) for r in cur.fetchall()]


def get_article(aid: int) -> dict[str, Any] | None:
    with db.cursor() as cur:
        cur.execute("SELECT * FROM kb_articles WHERE id = ?", (aid,))
        row = cur.fetchone()
    return dict(row) if row else None


def create_article(title: str, description: str) -> dict[str, Any]:
    now = _now()
    with db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO kb_articles (title, description, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            (title, description, now, now),
        )
        iid = cur.lastrowid
        cur.execute("SELECT * FROM kb_articles WHERE id = ?", (iid,))
        row = dict(cur.fetchone())
    kb_emb.upsert_article_embedding(row["id"], row["title"], row["description"])
    return row


def update_article(aid: int, title: str | None, description: str | None) -> dict[str, Any] | None:
    art = get_article(aid)
    if not art:
        return None
    new_title = title if title is not None else art["title"]
    new_desc = description if description is not None else art["description"]
    now = _now()
    with db.cursor() as cur:
        cur.execute(
            """
            UPDATE kb_articles SET title = ?, description = ?, updated_at = ? WHERE id = ?
            """,
            (new_title, new_desc, now, aid),
        )
        cur.execute("SELECT * FROM kb_articles WHERE id = ?", (aid,))
        row = dict(cur.fetchone())
    kb_emb.upsert_article_embedding(row["id"], row["title"], row["description"])
    return row


def delete_article(aid: int) -> bool:
    with db.cursor() as cur:
        cur.execute("DELETE FROM kb_articles WHERE id = ?", (aid,))
        return cur.rowcount > 0


def search_articles(q: str, limit: int = 50) -> list[dict[str, Any]]:
    like = f"%{q}%"
    with db.cursor() as cur:
        cur.execute(
            """
            SELECT * FROM kb_articles
            WHERE title LIKE ? OR description LIKE ?
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (like, like, limit),
        )
        return [dict(r) for r in cur.fetchall()]
