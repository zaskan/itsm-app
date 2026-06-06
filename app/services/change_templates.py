"""Standard change templates (admin-managed)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app import db
from app.services import custom_fields as cf_svc
from app.services import task_templates as tt_svc


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_task_ids(raw: str | list) -> list[int]:
    if isinstance(raw, list):
        return [int(x) for x in raw]
    try:
        val = json.loads(raw or "[]")
        return [int(x) for x in val] if isinstance(val, list) else []
    except (json.JSONDecodeError, TypeError, ValueError):
        return []


def _row_out(row: dict) -> dict[str, Any]:
    d = dict(row)
    d["task_template_ids"] = _parse_task_ids(d.get("task_template_ids", "[]"))
    d["custom_fields"] = cf_svc.parse_json_values(d.get("custom_fields"))
    d["field_definitions"] = cf_svc.list_definitions("change_template", d["id"])
    d["task_templates"] = [
        tt_svc.get_task_template(tid) for tid in d["task_template_ids"]
    ]
    d["task_templates"] = [t for t in d["task_templates"] if t is not None]
    return d


def _validate_task_ids(task_ids: list[int]) -> None:
    for tid in task_ids:
        if not tt_svc.get_task_template(tid):
            raise ValueError(f"Task template {tid} not found")


def list_change_templates() -> list[dict[str, Any]]:
    with db.cursor() as cur:
        cur.execute("SELECT * FROM standard_change_templates ORDER BY name ASC")
        return [_row_out(dict(r)) for r in cur.fetchall()]


def get_change_template(template_id: int) -> dict[str, Any] | None:
    with db.cursor() as cur:
        cur.execute("SELECT * FROM standard_change_templates WHERE id = ?", (template_id,))
        row = cur.fetchone()
        return _row_out(dict(row)) if row else None


def create_change_template(
    *,
    name: str,
    description: str = "",
    change_type: str = "standard",
    task_template_ids: list[int] | None = None,
) -> dict[str, Any]:
    if change_type not in ("standard", "normal"):
        raise ValueError("change_type must be standard or normal")
    tids = task_template_ids or []
    _validate_task_ids(tids)
    now = _utc_now_iso()
    with db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO standard_change_templates
            (name, description, change_type, task_template_ids, custom_fields, created_at, updated_at)
            VALUES (?, ?, ?, ?, '{}', ?, ?)
            """,
            (name.strip(), description, change_type, json.dumps(tids), now, now),
        )
        cid = cur.lastrowid
    tpl = get_change_template(cid)
    assert tpl is not None
    return tpl


def update_change_template(
    template_id: int,
    *,
    name: str | None = None,
    description: str | None = None,
    change_type: str | None = None,
    task_template_ids: list[int] | None = None,
) -> dict[str, Any] | None:
    existing = get_change_template(template_id)
    if not existing:
        return None
    if change_type is not None and change_type not in ("standard", "normal"):
        raise ValueError("change_type must be standard or normal")
    tids = task_template_ids if task_template_ids is not None else existing["task_template_ids"]
    _validate_task_ids(tids)
    now = _utc_now_iso()
    with db.cursor() as cur:
        cur.execute(
            """
            UPDATE standard_change_templates SET
                name = ?, description = ?, change_type = ?,
                task_template_ids = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                name.strip() if name is not None else existing["name"],
                description if description is not None else existing["description"],
                change_type if change_type is not None else existing["change_type"],
                json.dumps(tids),
                now,
                template_id,
            ),
        )
    return get_change_template(template_id)


def delete_change_template(template_id: int) -> bool:
    with db.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM request_templates WHERE change_template_id = ?",
            (template_id,),
        )
        if cur.fetchone()[0] > 0:
            raise ValueError("Change template is referenced by request templates")
        cf_svc.delete_definitions_for_scope("change_template", template_id)
        cur.execute("DELETE FROM standard_change_templates WHERE id = ?", (template_id,))
        return cur.rowcount > 0
