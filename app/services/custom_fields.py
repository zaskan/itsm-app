"""Reusable custom field definitions and value validation."""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, Literal

from app import db

ScopeType = Literal["asset_type", "request_template", "change_template", "task_template"]
FieldType = Literal["text", "textarea", "number", "boolean", "select", "date"]

VALID_SCOPE_TYPES = frozenset({"asset_type", "request_template", "change_template", "task_template"})
VALID_FIELD_TYPES = frozenset({"text", "textarea", "number", "boolean", "select", "date"})
_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def _parse_options(raw: str | list) -> list[str]:
    if isinstance(raw, list):
        return [str(x) for x in raw]
    try:
        val = json.loads(raw or "[]")
        return [str(x) for x in val] if isinstance(val, list) else []
    except json.JSONDecodeError:
        return []


def _row_out(row: dict) -> dict[str, Any]:
    d = dict(row)
    d["required"] = bool(d.get("required", 0))
    d["options"] = _parse_options(d.get("options", "[]"))
    return d


def list_definitions(scope_type: str, scope_id: int) -> list[dict[str, Any]]:
    if scope_type not in VALID_SCOPE_TYPES:
        raise ValueError(f"Invalid scope_type: {scope_type}")
    with db.cursor() as cur:
        cur.execute(
            """
            SELECT * FROM custom_field_definitions
            WHERE scope_type = ? AND scope_id = ?
            ORDER BY sort_order ASC, id ASC
            """,
            (scope_type, scope_id),
        )
        return [_row_out(dict(r)) for r in cur.fetchall()]


def get_definition(field_id: int) -> dict[str, Any] | None:
    with db.cursor() as cur:
        cur.execute("SELECT * FROM custom_field_definitions WHERE id = ?", (field_id,))
        row = cur.fetchone()
        return _row_out(dict(row)) if row else None


def create_definition(
    *,
    scope_type: str,
    scope_id: int,
    field_key: str,
    label: str,
    field_type: str,
    required: bool = False,
    options: list[str] | None = None,
    sort_order: int = 0,
) -> dict[str, Any]:
    _validate_definition_input(scope_type, field_key, label, field_type, options)
    with db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO custom_field_definitions
            (scope_type, scope_id, field_key, label, field_type, required, options, sort_order)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                scope_type,
                scope_id,
                field_key.strip(),
                label.strip(),
                field_type,
                1 if required else 0,
                json.dumps(options or []),
                sort_order,
            ),
        )
        fid = cur.lastrowid
    row = get_definition(fid)
    assert row is not None
    return row


def update_definition(
    field_id: int,
    *,
    field_key: str | None = None,
    label: str | None = None,
    field_type: str | None = None,
    required: bool | None = None,
    options: list[str] | None = None,
    sort_order: int | None = None,
) -> dict[str, Any] | None:
    existing = get_definition(field_id)
    if not existing:
        return None
    new_key = field_key.strip() if field_key is not None else existing["field_key"]
    new_label = label.strip() if label is not None else existing["label"]
    new_type = field_type if field_type is not None else existing["field_type"]
    new_opts = options if options is not None else existing["options"]
    new_req = required if required is not None else existing["required"]
    new_sort = sort_order if sort_order is not None else existing["sort_order"]
    _validate_definition_input(existing["scope_type"], new_key, new_label, new_type, new_opts)
    with db.cursor() as cur:
        cur.execute(
            """
            UPDATE custom_field_definitions SET
                field_key = ?, label = ?, field_type = ?, required = ?,
                options = ?, sort_order = ?
            WHERE id = ?
            """,
            (
                new_key,
                new_label,
                new_type,
                1 if new_req else 0,
                json.dumps(new_opts),
                new_sort,
                field_id,
            ),
        )
    return get_definition(field_id)


def delete_definition(field_id: int) -> bool:
    with db.cursor() as cur:
        cur.execute("DELETE FROM custom_field_definitions WHERE id = ?", (field_id,))
        return cur.rowcount > 0


def delete_definitions_for_scope(scope_type: str, scope_id: int) -> None:
    with db.cursor() as cur:
        cur.execute(
            "DELETE FROM custom_field_definitions WHERE scope_type = ? AND scope_id = ?",
            (scope_type, scope_id),
        )


def _validate_definition_input(
    scope_type: str,
    field_key: str,
    label: str,
    field_type: str,
    options: list[str] | None,
) -> None:
    if scope_type not in VALID_SCOPE_TYPES:
        raise ValueError(f"Invalid scope_type: {scope_type}")
    if not _KEY_RE.match(field_key.strip()):
        raise ValueError("field_key must be lowercase alphanumeric with underscores")
    if not label.strip():
        raise ValueError("label is required")
    if field_type not in VALID_FIELD_TYPES:
        raise ValueError(f"Invalid field_type: {field_type}")
    if field_type == "select" and not options:
        raise ValueError("select fields require at least one option")


def validate_values(
    definitions: list[dict[str, Any]],
    values: dict[str, Any] | None,
    *,
    partial: bool = False,
) -> dict[str, Any]:
    """Validate and coerce custom field values against definitions."""
    raw = values or {}
    result: dict[str, Any] = {}
    for d in definitions:
        key = d["field_key"]
        if key not in raw:
            if d["required"] and not partial:
                raise ValueError(f"Required field missing: {d['label']} ({key})")
            continue
        val = raw[key]
        if val is None or val == "":
            if d["required"] and not partial:
                raise ValueError(f"Required field empty: {d['label']} ({key})")
            continue
        result[key] = _coerce_value(d, val)
    return result


def _coerce_value(defn: dict[str, Any], val: Any) -> Any:
    ft = defn["field_type"]
    if ft == "boolean":
        if isinstance(val, bool):
            return val
        if isinstance(val, str):
            return val.lower() in ("1", "true", "yes", "on")
        return bool(val)
    if ft == "number":
        try:
            return float(val) if "." in str(val) else int(val)
        except (TypeError, ValueError) as e:
            raise ValueError(f"{defn['label']} must be a number") from e
    if ft == "select":
        s = str(val)
        if s not in defn.get("options", []):
            raise ValueError(f"{defn['label']}: invalid option '{s}'")
        return s
    if ft == "date":
        s = str(val).strip()
        try:
            datetime.strptime(s, "%Y-%m-%d")
        except ValueError as e:
            raise ValueError(f"{defn['label']} must be YYYY-MM-DD") from e
        return s
    return str(val)


def parse_json_values(raw: str | dict | None) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        val = json.loads(raw or "{}")
        return val if isinstance(val, dict) else {}
    except json.JSONDecodeError:
        return {}


def dump_json_values(values: dict[str, Any]) -> str:
    return json.dumps(values or {})
