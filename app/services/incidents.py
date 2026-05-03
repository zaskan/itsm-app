"""Incident CRUD and audit helpers."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app import db


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


def incident_snapshot(cur, incident_id: int) -> dict[str, Any]:
    inc = _get_incident_row(cur, incident_id)
    if not inc:
        return {}
    comments = list_comments(cur, incident_id)
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
        "comments": comments,
    }
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


def create_incident(
    *,
    title: str,
    description: str,
    severity: str,
    actor_user_id: int,
    created_at: str | None,
) -> dict[str, Any]:
    ts = created_at or _utc_now_iso()
    now = _utc_now_iso()
    with db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO incidents (public_id, title, description, severity, status, created_at, updated_at)
            VALUES ('TEMP', ?, ?, ?, 'open', ?, ?)
            """,
            (title, description, severity, ts, now),
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
            {"title": title, "severity": severity},
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


def close_incident(incident_id: int, actor_user_id: int) -> dict[str, Any]:
    now = _utc_now_iso()
    with db.cursor() as cur:
        inc = _get_incident_row(cur, incident_id)
        if not inc:
            raise ValueError("Incident not found")
        if inc["status"] == "closed":
            return incident_snapshot(cur, inc["id"])
        cur.execute(
            """
            UPDATE incidents SET status = 'closed', closed_at = ?, updated_at = ? WHERE id = ?
            """,
            (now, now, inc["id"]),
        )
        log_event(cur, inc["id"], "closed", actor_user_id, {})
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
        clauses.append("(title LIKE ? OR description LIKE ? OR public_id LIKE ?)")
        like = f"%{q}%"
        params.extend([like, like, like])
    if status:
        clauses.append("status = ?")
        params.append(status)
    if severity:
        clauses.append("severity = ?")
        params.append(severity)
    if date_from:
        clauses.append("created_at >= ?")
        params.append(date_from)
    if date_to:
        clauses.append("created_at <= ?")
        params.append(date_to + "T23:59:59")
    where = " AND ".join(clauses) if clauses else "1=1"
    sql = f"SELECT * FROM incidents WHERE {where} ORDER BY created_at DESC"
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
            d["payload"] = json.loads(d["payload"] or "{}")
            events.append(d)
        comments = list_comments(cur, iid)
        return {
            **dict(inc),
            "comments": comments,
            "events": events,
        }
