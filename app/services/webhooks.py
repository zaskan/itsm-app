"""Outbound webhook notifications."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import BackgroundTasks

from app import db

logger = logging.getLogger(__name__)


def list_webhooks() -> list[dict[str, Any]]:
    with db.cursor() as cur:
        cur.execute(
            """
            SELECT id, url, label, enabled, created_at
            FROM outbound_webhooks
            ORDER BY id ASC
            """
        )
        rows = cur.fetchall()
    return [dict(row) for row in rows]


def get_webhook(webhook_id: int) -> dict[str, Any] | None:
    with db.cursor() as cur:
        cur.execute(
            """
            SELECT id, url, label, enabled, created_at
            FROM outbound_webhooks WHERE id = ?
            """,
            (webhook_id,),
        )
        row = cur.fetchone()
    return dict(row) if row else None


def create_webhook(url: str, label: str = "", enabled: bool = True) -> dict[str, Any]:
    url = url.strip()
    if not url:
        raise ValueError("URL is required")
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    with db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO outbound_webhooks (url, label, enabled, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (url, label.strip(), 1 if enabled else 0, now),
        )
        wid = cur.lastrowid
        cur.execute(
            """
            SELECT id, url, label, enabled, created_at
            FROM outbound_webhooks WHERE id = ?
            """,
            (wid,),
        )
        row = cur.fetchone()
    assert row is not None
    return dict(row)


def update_webhook(
    webhook_id: int,
    *,
    url: str | None = None,
    label: str | None = None,
    enabled: bool | None = None,
) -> dict[str, Any] | None:
    existing = get_webhook(webhook_id)
    if not existing:
        return None
    new_url = existing["url"] if url is None else url.strip()
    if not new_url:
        raise ValueError("URL is required")
    new_label = existing["label"] if label is None else label.strip()
    new_enabled = existing["enabled"] if enabled is None else (1 if enabled else 0)
    with db.cursor() as cur:
        cur.execute(
            """
            UPDATE outbound_webhooks
            SET url = ?, label = ?, enabled = ?
            WHERE id = ?
            """,
            (new_url, new_label, new_enabled, webhook_id),
        )
    return get_webhook(webhook_id)


def delete_webhook(webhook_id: int) -> bool:
    with db.cursor() as cur:
        cur.execute("DELETE FROM outbound_webhooks WHERE id = ?", (webhook_id,))
        return cur.rowcount > 0


def _enabled_urls() -> list[tuple[int, str]]:
    with db.cursor() as cur:
        cur.execute(
            "SELECT id, url FROM outbound_webhooks WHERE enabled = 1 ORDER BY id ASC"
        )
        rows = cur.fetchall()
    return [(int(row[0]), str(row[1]).strip()) for row in rows if row[1] and str(row[1]).strip()]


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


async def _post_one(client: httpx.AsyncClient, url: str, payload: dict[str, Any]) -> None:
    try:
        r = await client.post(url, json=payload)
        r.raise_for_status()
    except Exception:
        logger.exception("Webhook delivery failed for url=%s", url)


async def dispatch_webhook(event: str, actor_username: str, incident_snapshot: dict[str, Any]) -> None:
    targets = _enabled_urls()
    if not targets:
        return
    payload = {
        "event": event,
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "actor": actor_username,
        "incident": incident_snapshot,
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        await asyncio.gather(
            *(_post_one(client, url, payload) for _wid, url in targets),
            return_exceptions=False,
        )
