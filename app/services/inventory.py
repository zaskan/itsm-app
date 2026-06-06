"""Inventory assets (all authenticated users)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app import db
from app.services import custom_fields as cf_svc


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_custom_fields(raw: str | None) -> dict[str, Any]:
    return cf_svc.parse_json_values(raw)


def _row_out(row: dict) -> dict[str, Any]:
    d = dict(row)
    d["custom_fields"] = _parse_custom_fields(d.get("custom_fields"))
    d["external_inventory"] = bool(d.get("external_inventory", 0))
    return d


def _base_select() -> str:
    return """
        SELECT i.*, t.name AS asset_type_name, p.name AS parent_name,
               u.username AS assigned_username,
               (SELECT COUNT(*) FROM inventory_assets c WHERE c.parent_asset_id = i.id) AS children_count
        FROM inventory_assets i
        LEFT JOIN asset_types t ON t.id = i.asset_type_id
        LEFT JOIN inventory_assets p ON p.id = i.parent_asset_id
        LEFT JOIN users u ON u.id = i.assigned_user_id
    """


def _would_create_cycle(cur, item_id: int | None, new_parent_id: int | None) -> bool:
    if new_parent_id is None:
        return False
    if item_id is not None and new_parent_id == item_id:
        return True
    walk: int | None = new_parent_id
    seen: set[int] = set()
    while walk is not None:
        if item_id is not None and walk == item_id:
            return True
        if walk in seen:
            break
        seen.add(walk)
        cur.execute("SELECT parent_asset_id FROM inventory_assets WHERE id = ?", (walk,))
        row = cur.fetchone()
        walk = row[0] if row and row[0] is not None else None
    return False


def list_inventory(
    q: str | None = None,
    *,
    external_only: bool = False,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if q:
        like = f"%{q}%"
        clauses.append("(i.name LIKE ? OR i.description LIKE ?)")
        params.extend([like, like])
    if external_only:
        clauses.append("i.external_inventory = 1")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = f"{_base_select()} {where} ORDER BY i.name COLLATE NOCASE"
    with db.cursor() as cur:
        cur.execute(sql, params)
        return [_row_out(dict(r)) for r in cur.fetchall()]


def get_item(iid: int) -> dict[str, Any] | None:
    with db.cursor() as cur:
        cur.execute(f"{_base_select()} WHERE i.id = ?", (iid,))
        row = cur.fetchone()
    return _row_out(dict(row)) if row else None


def list_children(parent_id: int) -> list[dict[str, Any]]:
    with db.cursor() as cur:
        cur.execute(
            f"{_base_select()} WHERE i.parent_asset_id = ? ORDER BY i.name COLLATE NOCASE",
            (parent_id,),
        )
        return [_row_out(dict(r)) for r in cur.fetchall()]


def create_item(
    name: str,
    description: str,
    *,
    asset_type_id: int | None = None,
    parent_asset_id: int | None = None,
    assigned_user_id: int | None = None,
    external_inventory: bool = False,
    custom_fields: dict | None = None,
) -> dict[str, Any]:
    name = name.strip()
    description = description.strip()
    if not name:
        raise ValueError("Name is required")
    if not description:
        raise ValueError("Description is required")
    validated: dict[str, Any] = {}
    if asset_type_id is not None:
        defs = cf_svc.list_definitions("asset_type", asset_type_id)
        validated = cf_svc.validate_values(defs, custom_fields)
    now = _now()
    with db.cursor() as cur:
        if parent_asset_id is not None:
            if _would_create_cycle(cur, None, parent_asset_id):
                raise ValueError("Invalid parent asset (cycle)")
            cur.execute("SELECT id FROM inventory_assets WHERE id = ?", (parent_asset_id,))
            if not cur.fetchone():
                raise ValueError("Parent asset not found")
        if asset_type_id is not None:
            cur.execute("SELECT id FROM asset_types WHERE id = ?", (asset_type_id,))
            if not cur.fetchone():
                raise ValueError("Asset type not found")
        if assigned_user_id is not None:
            cur.execute("SELECT id FROM users WHERE id = ?", (assigned_user_id,))
            if not cur.fetchone():
                raise ValueError("Assigned user not found")
        cur.execute(
            """
            INSERT INTO inventory_assets
            (asset_type_id, name, description, parent_asset_id, external_inventory,
             hostname, ip_address, assigned_user_id, custom_fields, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, '', '', ?, ?, ?, ?)
            """,
            (
                asset_type_id,
                name,
                description,
                parent_asset_id,
                1 if external_inventory else 0,
                assigned_user_id,
                cf_svc.dump_json_values(validated),
                now,
                now,
            ),
        )
        iid = cur.lastrowid
    return get_item(iid)  # type: ignore[return-value]


def update_item(
    iid: int,
    *,
    name: str | None = None,
    description: str | None = None,
    asset_type_id: int | None = None,
    parent_asset_id: int | None = None,
    clear_parent: bool = False,
    assigned_user_id: int | None = None,
    clear_assigned_user: bool = False,
    external_inventory: bool | None = None,
    custom_fields: dict | None = None,
) -> dict[str, Any] | None:
    item = get_item(iid)
    if not item:
        return None
    new_name = name.strip() if name is not None else item["name"]
    new_desc = description.strip() if description is not None else item["description"]
    if not new_name:
        raise ValueError("Name is required")
    if not new_desc:
        raise ValueError("Description is required")
    new_tid = asset_type_id if asset_type_id is not None else item.get("asset_type_id")
    if clear_parent:
        new_parent = None
    elif parent_asset_id is not None:
        new_parent = parent_asset_id
    else:
        new_parent = item.get("parent_asset_id")
    new_ext = (
        external_inventory
        if external_inventory is not None
        else item.get("external_inventory", False)
    )
    if clear_assigned_user:
        new_user = None
    elif assigned_user_id is not None:
        new_user = assigned_user_id
    else:
        new_user = item.get("assigned_user_id")
    new_cf = item["custom_fields"]
    if custom_fields is not None and new_tid is not None:
        defs = cf_svc.list_definitions("asset_type", new_tid)
        new_cf = cf_svc.validate_values(defs, custom_fields, partial=True)
    now = _now()
    with db.cursor() as cur:
        if new_parent is not None:
            if _would_create_cycle(cur, iid, new_parent):
                raise ValueError("Invalid parent asset (cycle)")
            cur.execute("SELECT id FROM inventory_assets WHERE id = ?", (new_parent,))
            if not cur.fetchone():
                raise ValueError("Parent asset not found")
        if new_tid is not None:
            cur.execute("SELECT id FROM asset_types WHERE id = ?", (new_tid,))
            if not cur.fetchone():
                raise ValueError("Asset type not found")
        if new_user is not None:
            cur.execute("SELECT id FROM users WHERE id = ?", (new_user,))
            if not cur.fetchone():
                raise ValueError("Assigned user not found")
        cur.execute(
            """
            UPDATE inventory_assets SET
                asset_type_id = ?, name = ?, description = ?,
                parent_asset_id = ?, external_inventory = ?,
                assigned_user_id = ?, custom_fields = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                new_tid,
                new_name,
                new_desc,
                new_parent,
                1 if new_ext else 0,
                new_user,
                cf_svc.dump_json_values(new_cf),
                now,
                iid,
            ),
        )
    return get_item(iid)


def delete_item(iid: int) -> bool:
    with db.cursor() as cur:
        cur.execute("DELETE FROM inventory_assets WHERE id = ?", (iid,))
        return cur.rowcount > 0
