"""Incident CRUD and audit helpers."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app import db
from app.services.sla import resolution_sla_fields


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _get_incident_row(cur, ident: str | int) -> dict | None:
    if isinstance(ident, int):
        cur.execute("SELECT * FROM incidents WHERE id = ?", (ident,))
    elif isinstance(ident, str) and ident.isdigit():
        cur.execute("SELECT * FROM incidents WHERE id = ?", (int(ident),))
    else:
        cur.execute("SELECT * FROM incidents WHERE public_id = ?", (str(ident),))
    row = cur.fetchone()
    return dict(row) if row else None


def _asset_summary(cur, asset_id: int | None) -> dict[str, Any] | None:
    if asset_id is None:
        return None
    cur.execute(
        """
        SELECT i.id, i.hostname, i.ip_address, i.group_name, t.name AS asset_type_name
        FROM inventory_assets i
        JOIN asset_types t ON t.id = i.asset_type_id
        WHERE i.id = ?
        """,
        (asset_id,),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def _sla_opened_at(cur, incident_id: int, fallback: str | None) -> str | None:
    """Prefer audit `created` event time — matches actual filing time vs date-only row."""
    cur.execute(
        """
        SELECT created_at FROM incident_events
        WHERE incident_id = ? AND event_type = 'created'
        ORDER BY created_at ASC LIMIT 1
        """,
        (incident_id,),
    )
    row = cur.fetchone()
    return row[0] if row else fallback


def _kb_summary(cur, article_id: int | None) -> dict[str, Any] | None:
    if article_id is None:
        return None
    cur.execute(
        "SELECT id, title FROM kb_articles WHERE id = ?",
        (article_id,),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def incident_snapshot(cur, incident_id: int) -> dict[str, Any]:
    inc = _get_incident_row(cur, incident_id)
    if not inc:
        return {}
    comments = list_comments(cur, incident_id)
    aid = inc.get("inventory_asset_id")
    asset = _asset_summary(cur, aid) if aid is not None else None
    kid = inc.get("resolution_kb_article_id")
    resolution_kb = _kb_summary(cur, kid) if kid is not None else None
    out = {
        "id": inc["id"],
        "public_id": inc["public_id"],
        "title": inc["title"],
        "description": inc["description"],
        "severity": inc["severity"],
        "status": inc["status"],
        "created_at": inc["created_at"],
        "updated_at": inc["updated_at"],
        "closed_at": inc["closed_at"],
        "inventory_asset_id": aid,
        "resolution_kb_article_id": kid,
        "linked_asset": asset,
        "resolution_kb_article": resolution_kb,
        "comments": comments,
    }
    opened = _sla_opened_at(cur, incident_id, inc.get("created_at"))
    out.update(
        resolution_sla_fields(
            status=inc["status"],
            severity=inc["severity"],
            opened_at=opened,
            closed_at=inc.get("closed_at"),
        )
    )
    return out


def list_comments(cur, incident_id: int) -> list[dict[str, Any]]:
    cur.execute(
        """
        SELECT c.id, c.body, c.created_at, u.username AS author_username
        FROM comments c
        JOIN users u ON u.id = c.author_user_id
        WHERE c.incident_id = ?
        ORDER BY c.created_at ASC
        """,
        (incident_id,),
    )
    return [dict(r) for r in cur.fetchall()]


def log_event(
    cur,
    incident_id: int,
    event_type: str,
    actor_user_id: int,
    payload: dict[str, Any] | None = None,
) -> None:
    cur.execute(
        """
        INSERT INTO incident_events (incident_id, event_type, payload, actor_user_id, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            incident_id,
            event_type,
            json.dumps(payload or {}),
            actor_user_id,
            _utc_now_iso(),
        ),
    )


def _validate_asset_id(cur, asset_id: int | None) -> None:
    if asset_id is None:
        return
    cur.execute("SELECT id FROM inventory_assets WHERE id = ?", (asset_id,))
    if not cur.fetchone():
        raise ValueError("Unknown inventory asset")


def _validate_kb_article_id(cur, article_id: int | None) -> None:
    if article_id is None:
        return
    cur.execute("SELECT id FROM kb_articles WHERE id = ?", (article_id,))
    if not cur.fetchone():
        raise ValueError("Unknown knowledge base article")


def create_incident(
    *,
    title: str,
    description: str,
    severity: str,
    actor_user_id: int,
    created_at: str | None,
    inventory_asset_id: int | None = None,
) -> dict[str, Any]:
    ts = created_at or _utc_now_iso()
    now = _utc_now_iso()
    with db.cursor() as cur:
        _validate_asset_id(cur, inventory_asset_id)
        cur.execute(
            """
            INSERT INTO incidents (public_id, title, description, severity, status, created_at, updated_at, inventory_asset_id)
            VALUES ('TEMP', ?, ?, ?, 'open', ?, ?, ?)
            """,
            (title, description, severity, ts, now, inventory_asset_id),
        )
        iid = cur.lastrowid
        public_id = f"INC-{iid}"
        cur.execute(
            "UPDATE incidents SET public_id = ? WHERE id = ?",
            (public_id, iid),
        )
        log_event(
            cur,
            iid,
            "created",
            actor_user_id,
            {"title": title, "severity": severity, "inventory_asset_id": inventory_asset_id},
        )
        snap = incident_snapshot(cur, iid)
    return snap


def add_comment(incident_id: int, body: str, actor_user_id: int) -> dict[str, Any]:
    now = _utc_now_iso()
    with db.cursor() as cur:
        inc = _get_incident_row(cur, incident_id)
        if not inc or inc["status"] != "open":
            raise ValueError("Incident not found or closed")
        cur.execute(
            """
            INSERT INTO comments (incident_id, author_user_id, body, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (inc["id"], actor_user_id, body, now),
        )
        cur.execute(
            "UPDATE incidents SET updated_at = ? WHERE id = ?",
            (now, inc["id"]),
        )
        log_event(
            cur,
            inc["id"],
            "comment_added",
            actor_user_id,
            {"comment_preview": body[:200]},
        )
        snap = incident_snapshot(cur, inc["id"])
    return snap


def update_severity(incident_id: int, severity: str, actor_user_id: int) -> dict[str, Any]:
    now = _utc_now_iso()
    with db.cursor() as cur:
        inc = _get_incident_row(cur, incident_id)
        if not inc or inc["status"] != "open":
            raise ValueError("Incident not found or closed")
        old = inc["severity"]
        if old == severity:
            return incident_snapshot(cur, inc["id"])
        cur.execute(
            "UPDATE incidents SET severity = ?, updated_at = ? WHERE id = ?",
            (severity, now, inc["id"]),
        )
        log_event(
            cur,
            inc["id"],
            "severity_changed",
            actor_user_id,
            {"from": old, "to": severity},
        )
        snap = incident_snapshot(cur, inc["id"])
    return snap


def update_incident_links(
    incident_id: str | int,
    *,
    inventory_asset_id: int | None,
    actor_user_id: int,
    clear_asset: bool = False,
) -> dict[str, Any]:
    """Set or clear linked inventory asset on an open incident."""
    now = _utc_now_iso()
    with db.cursor() as cur:
        inc = _get_incident_row(cur, incident_id)
        if not inc or inc["status"] != "open":
            raise ValueError("Incident not found or closed")
        new_val = None if clear_asset else inventory_asset_id
        if new_val is not None:
            _validate_asset_id(cur, new_val)
        old = inc.get("inventory_asset_id")
        cur.execute(
            "UPDATE incidents SET inventory_asset_id = ?, updated_at = ? WHERE id = ?",
            (new_val, now, inc["id"]),
        )
        log_event(
            cur,
            inc["id"],
            "asset_linked",
            actor_user_id,
            {"from": old, "to": new_val},
        )
        return incident_snapshot(cur, inc["id"])


def close_incident(
    incident_ref: str | int,
    actor_user_id: int,
    *,
    resolution_kb_article_id: int | None = None,
) -> dict[str, Any]:
    now = _utc_now_iso()
    with db.cursor() as cur:
        inc = _get_incident_row(cur, incident_ref)
        if not inc:
            raise ValueError("Incident not found")
        if inc["status"] == "closed":
            return incident_snapshot(cur, inc["id"])
        _validate_kb_article_id(cur, resolution_kb_article_id)
        cur.execute(
            """
            UPDATE incidents SET status = 'closed', closed_at = ?, updated_at = ?,
                resolution_kb_article_id = ? WHERE id = ?
            """,
            (now, now, resolution_kb_article_id, inc["id"]),
        )
        payload: dict[str, Any] = {}
        if resolution_kb_article_id is not None:
            payload["resolution_kb_article_id"] = resolution_kb_article_id
        log_event(cur, inc["id"], "closed", actor_user_id, payload)
        snap = incident_snapshot(cur, inc["id"])
    return snap


def list_incidents(
    *,
    q: str | None = None,
    status: str | None = None,
    severity: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if q:
        clauses.append("(x.title LIKE ? OR x.description LIKE ? OR x.public_id LIKE ?)")
        like = f"%{q}%"
        params.extend([like, like, like])
    if status:
        clauses.append("x.status = ?")
        params.append(status)
    if severity:
        clauses.append("x.severity = ?")
        params.append(severity)
    if date_from:
        clauses.append("x.created_at >= ?")
        params.append(date_from)
    if date_to:
        clauses.append("x.created_at <= ?")
        params.append(date_to + "T23:59:59")
    where = " AND ".join(clauses) if clauses else "1=1"
    sql = f"""
        SELECT x.*, ia.hostname AS linked_hostname, at.name AS linked_asset_type_name,
            kb.title AS resolution_kb_title
        FROM incidents x
        LEFT JOIN inventory_assets ia ON ia.id = x.inventory_asset_id
        LEFT JOIN asset_types at ON at.id = ia.asset_type_id
        LEFT JOIN kb_articles kb ON kb.id = x.resolution_kb_article_id
        WHERE {where}
        ORDER BY x.created_at DESC
    """
    with db.cursor() as cur:
        cur.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]


def get_incident_detail(incident_id: str | int) -> dict[str, Any] | None:
    with db.cursor() as cur:
        inc = _get_incident_row(cur, incident_id)
        if not inc:
            return None
        iid = inc["id"]
        cur.execute(
            """
            SELECT e.id, e.event_type, e.payload, e.created_at, u.username AS actor_username
            FROM incident_events e
            JOIN users u ON u.id = e.actor_user_id
            WHERE e.incident_id = ?
            ORDER BY e.created_at ASC
            """,
            (iid,),
        )
        events = []
        for r in cur.fetchall():
            d = dict(r)
            try:
                d["payload"] = json.loads(d["payload"] or "{}")
            except json.JSONDecodeError:
                d["payload"] = {}
            events.append(d)
        comments = list_comments(cur, iid)
        aid = inc.get("inventory_asset_id")
        linked = _asset_summary(cur, aid) if aid is not None else None
        rid = inc.get("resolution_kb_article_id")
        resolution_kb = _kb_summary(cur, rid) if rid is not None else None
        out = {
            **dict(inc),
            "comments": comments,
            "events": events,
            "linked_asset": linked,
            "resolution_kb_article": resolution_kb,
        }
        opened_at = inc.get("created_at")
        for e in events:
            if e.get("event_type") == "created":
                opened_at = e["created_at"]
                break
        out.update(
            resolution_sla_fields(
                status=inc["status"],
                severity=inc["severity"],
                opened_at=opened_at,
                closed_at=inc.get("closed_at"),
            )
        )
        return out
