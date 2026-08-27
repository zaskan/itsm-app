"""Knowledge base article storage."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from app import db
from app.services import kb_embeddings as kb_emb

SortKey = Literal["newest", "alpha"]
SourceFilter = Literal["all", "repo", "manual"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def public_id(aid: int) -> str:
    """Return a ServiceNow-style knowledge number (KB0000001)."""
    return f"KB{int(aid):07d}"


def plain_snippet(description: str, limit: int = 220) -> str:
    """Return a short plain-text preview of markdown body."""
    text = re.sub(r"```.*?```", " ", description or "", flags=re.S)
    text = re.sub(r"[#>*_`~\-]+", " ", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0]
    return (cut or text[:limit]) + "…"


def relative_time(iso: str) -> str:
    """Return a compact relative timestamp (5m ago, 4y ago)."""
    raw = (iso or "").strip()
    if not raw:
        return ""
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return iso
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - dt
    seconds = max(int(delta.total_seconds()), 0)
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    if seconds < 86400:
        return f"{seconds // 3600}h ago"
    days = seconds // 86400
    if days < 30:
        return f"{days}d ago"
    if days < 365:
        months = max(days // 30, 1)
        return f"{months}mo ago"
    years = max(days // 365, 1)
    return f"{years}y ago"


def category_label(article: dict[str, Any]) -> str:
    """Return a breadcrumb-style category for search results."""
    path = (article.get("source_path") or "").strip()
    if not path:
        return "IT | Knowledge Base"
    stem = Path(path).stem.replace("-", " ").replace("_", " ").strip()
    if stem:
        return f"IT | {stem.title()}"
    return "IT | Knowledge"


def present_article(article: dict[str, Any]) -> dict[str, Any]:
    """Add display fields used by the Next Experience KB templates."""
    out = dict(article)
    out["public_id"] = public_id(int(article["id"]))
    out["snippet"] = plain_snippet(str(article.get("description") or ""))
    out["relative_updated"] = relative_time(str(article.get("updated_at") or ""))
    out["category"] = category_label(article)
    return out


def list_articles(
    *,
    q: str | None = None,
    sort: str = "newest",
    source: str = "all",
) -> list[dict[str, Any]]:
    order = "title COLLATE NOCASE ASC" if sort == "alpha" else "updated_at DESC"
    source_sql = ""
    if source == "repo":
        source_sql = " AND source_path IS NOT NULL AND source_path != ''"
    elif source == "manual":
        source_sql = " AND (source_path IS NULL OR source_path = '')"
    with db.cursor() as cur:
        if q:
            like = f"%{q}%"
            cur.execute(
                f"""
                SELECT * FROM kb_articles
                WHERE (title LIKE ? OR description LIKE ?){source_sql}
                ORDER BY {order}
                """,
                (like, like),
            )
        else:
            where = "1=1" + source_sql
            cur.execute(
                f"SELECT * FROM kb_articles WHERE {where} ORDER BY {order}",
            )
        return [dict(r) for r in cur.fetchall()]


def related_articles(exclude_id: int, limit: int = 5) -> list[dict[str, Any]]:
    """Return other recent articles for the article-page sidebar."""
    with db.cursor() as cur:
        cur.execute(
            """
            SELECT * FROM kb_articles
            WHERE id != ?
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (exclude_id, limit),
        )
        return [present_article(dict(r)) for r in cur.fetchall()]


def get_article(aid: int) -> dict[str, Any] | None:
    with db.cursor() as cur:
        cur.execute("SELECT * FROM kb_articles WHERE id = ?", (aid,))
        row = cur.fetchone()
    return dict(row) if row else None


def create_article(title: str, description: str, *, source_path: str | None = None) -> dict[str, Any]:
    now = _now()
    with db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO kb_articles (title, description, source_path, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (title, description, source_path, now, now),
        )
        iid = cur.lastrowid
        cur.execute("SELECT * FROM kb_articles WHERE id = ?", (iid,))
        row = dict(cur.fetchone())
    kb_emb.upsert_article_embedding(row["id"], row["title"], row["description"])
    return row


def update_article(
    aid: int,
    title: str | None,
    description: str | None,
    *,
    detach_from_repo: bool = True,
) -> dict[str, Any] | None:
    art = get_article(aid)
    if not art:
        return None
    new_title = title if title is not None else art["title"]
    new_desc = description if description is not None else art["description"]
    now = _now()
    with db.cursor() as cur:
        if detach_from_repo and art.get("source_path"):
            cur.execute(
                """
                UPDATE kb_articles
                SET title = ?, description = ?, source_path = NULL, updated_at = ?
                WHERE id = ?
                """,
                (new_title, new_desc, now, aid),
            )
        else:
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
