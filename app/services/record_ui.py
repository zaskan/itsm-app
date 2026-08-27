"""Shared presentation helpers for ServiceNow-style record detail views."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable


def format_sn_datetime(iso: str | None) -> str:
    if not iso:
        return ""
    try:
        normalized = iso.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return iso or ""


def status_label(status: str | None) -> str:
    if not status:
        return ""
    return status.replace("_", " ").title()


def build_activities(
    events: list[dict[str, Any]],
    line_fn: Callable[[dict[str, Any]], list[str]],
) -> list[dict[str, Any]]:
    activities: list[dict[str, Any]] = []
    for event in reversed(events):
        activities.append(
            {
                "actor_username": event.get("actor_username") or "System",
                "created_at": event.get("created_at"),
                "created_at_display": format_sn_datetime(event.get("created_at")),
                "lines": line_fn(event),
            }
        )
    return activities


def default_event_lines(event: dict[str, Any]) -> list[str]:
    et = (event.get("event_type") or "").replace("_", " ")
    payload = event.get("payload") or {}
    if payload:
        parts = [f"{k}: {v}" for k, v in payload.items() if v is not None and v != ""]
        if parts:
            return [et.title(), *parts[:3]]
    return [et.title()]
