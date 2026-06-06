"""Audit log for workflow records (requests, changes, CIs)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app import db


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def log_workflow_event(
    cur,
    *,
    record_type: str,
    record_id: int,
    event_type: str,
    actor_user_id: int,
    payload: dict | None = None,
) -> None:
    cur.execute(
        """
        INSERT INTO workflow_events
        (record_type, record_id, event_type, payload, actor_user_id, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            record_type,
            record_id,
            event_type,
            json.dumps(payload or {}),
            actor_user_id,
            _utc_now_iso(),
        ),
    )


def list_events_for_record(record_type: str, record_id: int) -> list[dict[str, Any]]:
    with db.cursor() as cur:
        cur.execute(
            """
            SELECT e.*, u.username AS actor_username
            FROM workflow_events e
            JOIN users u ON u.id = e.actor_user_id
            WHERE e.record_type = ? AND e.record_id = ?
            ORDER BY e.created_at ASC
            """,
            (record_type, record_id),
        )
        rows = []
        for r in cur.fetchall():
            d = dict(r)
            try:
                d["payload"] = json.loads(d.get("payload") or "{}")
            except json.JSONDecodeError:
                d["payload"] = {}
            rows.append(d)
        return rows
