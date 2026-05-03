"""Inventory assets (all authenticated users)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app import db


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def list_inventory(q: str | None = None) -> list[dict[str, Any]]:
    with db.cursor() as cur:
        if q:
            like = f"%{q}%"
            cur.execute(
                """
                SELECT i.*, t.name AS asset_type_name
                FROM inventory_assets i
                JOIN asset_types t ON t.id = i.asset_type_id
                WHERE i.hostname LIKE ? OR i.ip_address LIKE ? OR i.group_name LIKE ?
                ORDER BY i.hostname COLLATE NOCASE
                """,
                (like, like, like),
            )
        else:
            cur.execute(
                """
                SELECT i.*, t.name AS asset_type_name
                FROM inventory_assets i
                JOIN asset_types t ON t.id = i.asset_type_id
                ORDER BY i.hostname COLLATE NOCASE
                """
            )
        return [dict(r) for r in cur.fetchall()]


def get_item(iid: int) -> dict[str, Any] | None:
    with db.cursor() as cur:
        cur.execute(
            """
            SELECT i.*, t.name AS asset_type_name
            FROM inventory_assets i
            JOIN asset_types t ON t.id = i.asset_type_id
            WHERE i.id = ?
            """,
            (iid,),
        )
        row = cur.fetchone()
    return dict(row) if row else None


def create_item(
    asset_type_id: int,
    hostname: str,
    ip_address: str = "",
    group_name: str = "",
) -> dict[str, Any]:
    now = _now()
    with db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO inventory_assets
            (asset_type_id, hostname, ip_address, group_name, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (asset_type_id, hostname.strip(), ip_address.strip(), group_name.strip(), now, now),
        )
        iid = cur.lastrowid
    return get_item(iid)  # type: ignore


def update_item(
    iid: int,
    *,
    asset_type_id: int | None = None,
    hostname: str | None = None,
    ip_address: str | None = None,
    group_name: str | None = None,
) -> dict[str, Any] | None:
    item = get_item(iid)
    if not item:
        return None
    new_tid = asset_type_id if asset_type_id is not None else item["asset_type_id"]
    new_host = hostname.strip() if hostname is not None else item["hostname"]
    new_ip = ip_address.strip() if ip_address is not None else item["ip_address"]
    new_grp = group_name.strip() if group_name is not None else item["group_name"]
    now = _now()
    with db.cursor() as cur:
        cur.execute(
            """
            UPDATE inventory_assets SET
                asset_type_id = ?, hostname = ?, ip_address = ?, group_name = ?, updated_at = ?
            WHERE id = ?
            """,
            (new_tid, new_host, new_ip, new_grp, now, iid),
        )
    return get_item(iid)


def delete_item(iid: int) -> bool:
    with db.cursor() as cur:
        cur.execute("DELETE FROM inventory_assets WHERE id = ?", (iid,))
        return cur.rowcount > 0
