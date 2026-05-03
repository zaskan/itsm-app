"""SLA resolution targets and labels for closed incidents."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


# Seconds from severity at creation to allowed resolution (close).
SLA_TARGET_SECONDS: dict[str, int] = {
    "critical": 3600,  # 1 hour
    "high": 4 * 3600,  # 4 hours
    "medium": 24 * 3600,  # 1 day
    "low": 48 * 3600,  # 2 days
}


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts or not isinstance(ts, str):
        return None
    s = ts.strip().replace("Z", "+00:00")
    try:
        d = datetime.fromisoformat(s)
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d


def sla_target_label(severity: str) -> str:
    sec = SLA_TARGET_SECONDS.get(severity, SLA_TARGET_SECONDS["medium"])
    if sec >= 86400:
        days = sec // 86400
        return f"{days} day{'s' if days != 1 else ''}"
    if sec >= 3600:
        h = sec // 3600
        return f"{h} hour{'s' if h != 1 else ''}"
    m = sec // 60
    return f"{m} minutes"


def format_duration(seconds: float) -> str:
    if seconds < 0:
        seconds = 0
    sec = int(seconds)
    if sec >= 86400:
        d, sec = divmod(sec, 86400)
        parts = [f"{d}d"]
        if sec >= 3600:
            parts.append(f"{sec // 3600}h")
        elif sec >= 60:
            parts.append(f"{sec // 60}m")
        else:
            parts.append(f"{sec}s")
        return " ".join(parts)
    if sec >= 3600:
        h, rem = divmod(sec, 3600)
        m = rem // 60
        return f"{h} h {m} min" if m else f"{h} h"
    if sec >= 60:
        return f"{sec // 60} min"
    return f"{sec} s"


def resolution_sla_fields(
    *,
    status: str,
    severity: str,
    opened_at: str | None,
    closed_at: str | None,
) -> dict[str, Any]:
    """Compute SLA summary for API/UI; omit numeric fields when unknown.

    Use the real moment the ticket was opened for ``opened_at`` (e.g. the audit
    ``created`` event). ``incidents.created_at`` alone may be midnight UTC on a
    chosen calendar day from the UI form, which would skew resolution duration.
    """
    target_sec = SLA_TARGET_SECONDS.get(severity, SLA_TARGET_SECONDS["medium"])
    out: dict[str, Any] = {
        "sla_target_seconds": target_sec,
        "sla_target_label": sla_target_label(severity),
        "resolution_seconds": None,
        "resolution_duration_label": None,
        "sla_compliant": None,
    }
    if status != "closed":
        return out

    t0 = _parse_iso(opened_at)
    t1 = _parse_iso(closed_at)
    if not t0 or not t1:
        return out

    delta = (t1 - t0).total_seconds()
    out["resolution_seconds"] = delta
    out["resolution_duration_label"] = format_duration(delta)
    out["sla_compliant"] = delta <= float(target_sec)
    return out
