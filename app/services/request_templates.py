"""Request templates (admin-managed, formerly service catalog)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app import db
from app.services import change_templates as ctpl_svc
from app.services import custom_fields as cf_svc


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _row_out(row: dict) -> dict[str, Any]:
    d = dict(row)
    d["require_standard_change"] = bool(d.get("require_standard_change", 1))
    d["field_definitions"] = cf_svc.list_definitions("request_template", d["id"])
    if d.get("change_template_id"):
        ct = ctpl_svc.get_change_template(d["change_template_id"])
        d["change_template_name"] = ct["name"] if ct else None
        d["change_template"] = ct
    else:
        d["change_template_name"] = None
        d["change_template"] = None
    return d


def list_request_templates() -> list[dict[str, Any]]:
    with db.cursor() as cur:
        cur.execute("SELECT * FROM request_templates ORDER BY name ASC")
        return [_row_out(dict(r)) for r in cur.fetchall()]


def get_request_template(template_id: int) -> dict[str, Any] | None:
    with db.cursor() as cur:
        cur.execute("SELECT * FROM request_templates WHERE id = ?", (template_id,))
        row = cur.fetchone()
        return _row_out(dict(row)) if row else None


def resolve_request_template(ref: str | int) -> dict[str, Any] | None:
    """Resolve by numeric id or by name with spaces replaced by hyphens.

    Example: name ``New Linux Virtual Machine`` → ref ``New-Linux-Virtual-Machine``.
    """
    if isinstance(ref, int) or (isinstance(ref, str) and ref.strip().isdigit()):
        return get_request_template(int(ref))
    slug = str(ref).strip()
    if not slug:
        return None
    with db.cursor() as cur:
        cur.execute(
            "SELECT * FROM request_templates WHERE REPLACE(name, ' ', '-') = ?",
            (slug,),
        )
        row = cur.fetchone()
        return _row_out(dict(row)) if row else None


def create_request_template(
    *,
    name: str,
    description: str,
    change_template_id: int | None = None,
    require_standard_change: bool = True,
) -> dict[str, Any]:
    name = name.strip()
    description = description.strip()
    if not name:
        raise ValueError("Name is required")
    if not description:
        raise ValueError("Description is required")
    if change_template_id is not None:
        ct = ctpl_svc.get_change_template(change_template_id)
        if not ct:
            raise ValueError("Change template not found")
        if require_standard_change and ct["change_type"] != "standard":
            raise ValueError("require_standard_change requires a standard change template")
    now = _utc_now_iso()
    with db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO request_templates
            (name, description, change_template_id, require_standard_change,
             packages, application, ci_class, created_at, updated_at)
            VALUES (?, ?, ?, ?, '[]', '', 'server', ?, ?)
            """,
            (
                name,
                description,
                change_template_id,
                1 if require_standard_change else 0,
                now,
                now,
            ),
        )
        tid = cur.lastrowid
    tpl = get_request_template(tid)
    assert tpl is not None
    return tpl


def update_request_template(
    template_id: int,
    *,
    name: str | None = None,
    description: str | None = None,
    change_template_id: int | None = None,
    require_standard_change: bool | None = None,
    clear_change_template: bool = False,
) -> dict[str, Any] | None:
    existing = get_request_template(template_id)
    if not existing:
        return None
    ct_id = None if clear_change_template else (
        change_template_id if change_template_id is not None else existing["change_template_id"]
    )
    req_std = (
        require_standard_change
        if require_standard_change is not None
        else existing["require_standard_change"]
    )
    if ct_id is not None:
        ct = ctpl_svc.get_change_template(ct_id)
        if not ct:
            raise ValueError("Change template not found")
        if req_std and ct["change_type"] != "standard":
            raise ValueError("require_standard_change requires a standard change template")
    new_name = name.strip() if name is not None else existing["name"]
    new_desc = description.strip() if description is not None else existing["description"]
    if not new_name:
        raise ValueError("Name is required")
    if not new_desc:
        raise ValueError("Description is required")
    now = _utc_now_iso()
    with db.cursor() as cur:
        cur.execute(
            """
            UPDATE request_templates SET
                name = ?, description = ?, change_template_id = ?,
                require_standard_change = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                new_name,
                new_desc,
                ct_id,
                1 if req_std else 0,
                now,
                template_id,
            ),
        )
    return get_request_template(template_id)


def delete_request_template(template_id: int) -> bool:
    with db.cursor() as cur:
        cf_svc.delete_definitions_for_scope("request_template", template_id)
        cur.execute("DELETE FROM request_templates WHERE id = ?", (template_id,))
        return cur.rowcount > 0


def get_default_field_values(template_id: int) -> dict[str, Any]:
    """Build default specifications dict from field definitions (empty values)."""
    defs = cf_svc.list_definitions("request_template", template_id)
    return {d["field_key"]: "" for d in defs}
