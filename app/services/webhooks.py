"""Outbound webhook notifications."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import BackgroundTasks

from app import db

logger = logging.getLogger(__name__)


def get_webhook_url() -> str:
    with db.cursor() as cur:
        cur.execute(
            "SELECT value FROM app_settings WHERE key = ?",
            ("webhook_url",),
        )
        row = cur.fetchone()
    if not row:
        return ""
    return row[0] or ""


def set_webhook_url(url: str) -> None:
    with db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO app_settings (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            ("webhook_url", url.strip()),
        )


def event_name(internal: str) -> str:
    return {
        "created": "incident.created",
        "comment_added": "incident.comment_added",
        "severity_changed": "incident.severity_changed",
        "closed": "incident.closed",
    }.get(internal, f"incident.{internal}")


def schedule_incident_webhook(
    background_tasks: BackgroundTasks,
    internal_event: str,
    actor_username: str,
    snapshot: dict[str, Any],
) -> None:
    """Register async webhook delivery after HTTP response."""
    ev = event_name(internal_event)
    background_tasks.add_task(dispatch_webhook, ev, actor_username, snapshot)


async def dispatch_webhook(event: str, actor_username: str, incident_snapshot: dict[str, Any]) -> None:
    url = get_webhook_url()
    if not url:
        return
    payload = {
        "event": event,
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "actor": actor_username,
        "incident": incident_snapshot,
    }
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(url, json=payload)
            r.raise_for_status()
    except Exception:
        logger.exception("Webhook delivery failed for event=%s url=%s", event, url)
