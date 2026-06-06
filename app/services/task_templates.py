"""Standard task templates (admin-managed)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app import db
from app.services import custom_fields as cf_svc


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _row_out(row: dict) -> dict[str, Any]:
    d = dict(row)
    d["custom_fields"] = cf_svc.parse_json_values(d.get("custom_fields"))
    if d.get("kb_article_id"):
        with db.cursor() as cur:
            cur.execute("SELECT title FROM kb_articles WHERE id = ?", (d["kb_article_id"],))
            kb = cur.fetchone()
            d["kb_article_title"] = kb[0] if kb else None
    else:
        d["kb_article_title"] = None
    uid = d.get("assigned_user_id")
    if uid:
        with db.cursor() as cur:
            cur.execute("SELECT username FROM users WHERE id = ?", (uid,))
            u = cur.fetchone()
            d["assigned_username"] = u[0] if u else None
    else:
        d["assigned_username"] = None
    d["field_definitions"] = cf_svc.list_definitions("task_template", d["id"])
    return d


def list_task_templates() -> list[dict[str, Any]]:
    with db.cursor() as cur:
        cur.execute(
            """
            SELECT t.*, u.username AS assigned_username
            FROM standard_task_templates t
            LEFT JOIN users u ON u.id = t.assigned_user_id
            ORDER BY t.name ASC
            """
        )
        return [_row_out(dict(r)) for r in cur.fetchall()]


def get_task_template(template_id: int) -> dict[str, Any] | None:
    with db.cursor() as cur:
        cur.execute("SELECT * FROM standard_task_templates WHERE id = ?", (template_id,))
        row = cur.fetchone()
        return _row_out(dict(row)) if row else None


def create_task_template(
    *,
    name: str,
    title: str,
    description: str = "",
    assigned_user_id: int | None = None,
    kb_article_id: int | None = None,
) -> dict[str, Any]:
    now = _utc_now_iso()
    with db.cursor() as cur:
        if kb_article_id is not None:
            cur.execute("SELECT id FROM kb_articles WHERE id = ?", (kb_article_id,))
            if not cur.fetchone():
                raise ValueError(f"KB article {kb_article_id} not found")
        if assigned_user_id is not None:
            cur.execute("SELECT id FROM users WHERE id = ?", (assigned_user_id,))
            if not cur.fetchone():
                raise ValueError("Assigned user not found")
        cur.execute(
            """
            INSERT INTO standard_task_templates
            (name, title, description, assigned_user_id, kb_article_id, custom_fields, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, '{}', ?, ?)
            """,
            (name.strip(), title.strip(), description, assigned_user_id, kb_article_id, now, now),
        )
        tid = cur.lastrowid
    tpl = get_task_template(tid)
    assert tpl is not None
    return tpl


def update_task_template(
    template_id: int,
    *,
    name: str | None = None,
    title: str | None = None,
    description: str | None = None,
    assigned_user_id: int | None = None,
    clear_assigned_user: bool = False,
    kb_article_id: int | None = None,
    clear_kb: bool = False,
) -> dict[str, Any] | None:
    existing = get_task_template(template_id)
    if not existing:
        return None
    now = _utc_now_iso()
    kid = None if clear_kb else (kb_article_id if kb_article_id is not None else existing["kb_article_id"])
    if clear_assigned_user:
        uid = None
    elif assigned_user_id is not None:
        uid = assigned_user_id
    else:
        uid = existing.get("assigned_user_id")
    with db.cursor() as cur:
        if kid is not None:
            cur.execute("SELECT id FROM kb_articles WHERE id = ?", (kid,))
            if not cur.fetchone():
                raise ValueError(f"KB article {kid} not found")
        if uid is not None:
            cur.execute("SELECT id FROM users WHERE id = ?", (uid,))
            if not cur.fetchone():
                raise ValueError("Assigned user not found")
        cur.execute(
            """
            UPDATE standard_task_templates SET
                name = ?, title = ?, description = ?, assigned_user_id = ?,
                kb_article_id = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                name.strip() if name is not None else existing["name"],
                title.strip() if title is not None else existing["title"],
                description if description is not None else existing["description"],
                uid,
                kid,
                now,
                template_id,
            ),
        )
    return get_task_template(template_id)


def delete_task_template(template_id: int) -> bool:
    with db.cursor() as cur:
        cf_svc.delete_definitions_for_scope("task_template", template_id)
        cur.execute("DELETE FROM standard_task_templates WHERE id = ?", (template_id,))
        return cur.rowcount > 0
