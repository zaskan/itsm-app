"""Service requests (REQ) and requested items (RITM)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app import db
from app.services import custom_fields as cf_svc
from app.services import request_templates as rtpl_svc
from app.services import workflow_events as we_svc


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_json(raw: str | dict | list, default: Any) -> Any:
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw or "")
    except json.JSONDecodeError:
        return default


def _next_request_public_id(cur) -> str:
    year = datetime.now(timezone.utc).year
    prefix = f"REQ-{year}-"
    cur.execute(
        "SELECT public_id FROM service_requests WHERE public_id LIKE ? ORDER BY public_id DESC LIMIT 1",
        (f"{prefix}%",),
    )
    row = cur.fetchone()
    if row:
        try:
            seq = int(str(row[0]).split("-")[-1]) + 1
        except ValueError:
            seq = 1
    else:
        seq = 1
    return f"{prefix}{seq:03d}"


def _get_request_row(cur, ident: str | int) -> dict | None:
    if isinstance(ident, int):
        cur.execute("SELECT * FROM service_requests WHERE id = ?", (ident,))
    elif isinstance(ident, str) and ident.isdigit():
        cur.execute("SELECT * FROM service_requests WHERE id = ?", (int(ident),))
    else:
        cur.execute("SELECT * FROM service_requests WHERE public_id = ?", (str(ident),))
    row = cur.fetchone()
    return dict(row) if row else None


def _get_ritm_row(cur, ident: str | int) -> dict | None:
    if isinstance(ident, int):
        cur.execute("SELECT * FROM requested_items WHERE id = ?", (ident,))
    elif isinstance(ident, str) and ident.isdigit():
        cur.execute("SELECT * FROM requested_items WHERE id = ?", (int(ident),))
    else:
        cur.execute("SELECT * FROM requested_items WHERE public_id = ?", (str(ident),))
    row = cur.fetchone()
    return dict(row) if row else None


def _ritm_out(cur, row: dict) -> dict[str, Any]:
    d = dict(row)
    d["specifications"] = _parse_json(d.get("specifications", "{}"), {})
    d["packages"] = _parse_json(d.get("packages", "[]"), [])
    cur.execute(
        "SELECT public_id, status FROM change_requests WHERE ritm_id = ?",
        (d["id"],),
    )
    chg = cur.fetchone()
    if chg:
        d["change_public_id"] = chg[0]
        d["change_status"] = chg[1]
    else:
        d["change_public_id"] = None
        d["change_status"] = None
    tpl_id = d.get("request_template_id")
    if tpl_id:
        tpl = rtpl_svc.get_request_template(tpl_id)
        d["request_template_name"] = tpl["name"] if tpl else None
    else:
        d["request_template_name"] = None
    d["catalog_item_name"] = d["request_template_name"]
    d["catalog_item_id"] = tpl_id
    return d


OPEN_REQUEST_STATUSES = ("draft", "submitted", "in_progress")


def list_requests(
    *,
    status: str | None = None,
    open_only: bool = False,
    q: str | None = None,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if open_only:
        placeholders = ",".join("?" * len(OPEN_REQUEST_STATUSES))
        clauses.append(f"r.status IN ({placeholders})")
        params.extend(OPEN_REQUEST_STATUSES)
    elif status:
        clauses.append("r.status = ?")
        params.append(status)
    if q:
        like = f"%{q}%"
        clauses.append("(r.public_id LIKE ? OR r.name LIKE ? OR r.description LIKE ?)")
        params.extend([like, like, like])
    where = " AND ".join(clauses) if clauses else "1=1"
    sql = f"""
        SELECT r.*, u.username AS requester_username
        FROM service_requests r
        JOIN users u ON u.id = r.requester_user_id
        WHERE {where}
        ORDER BY r.created_at DESC
    """
    with db.cursor() as cur:
        cur.execute(sql, params)
        rows = [dict(r) for r in cur.fetchall()]
        for row in rows:
            cur.execute(
                """
                SELECT ri.request_template_id, rt.name AS request_template_name
                FROM requested_items ri
                LEFT JOIN request_templates rt ON rt.id = ri.request_template_id
                WHERE ri.request_id = ? AND ri.request_template_id IS NOT NULL
                ORDER BY ri.id ASC
                LIMIT 1
                """,
                (row["id"],),
            )
            tpl = cur.fetchone()
            if tpl:
                row["request_template_id"] = tpl["request_template_id"]
                row["request_template_name"] = tpl["request_template_name"]
            else:
                row["request_template_id"] = None
                row["request_template_name"] = None
        return rows


def create_request(
    *,
    requester_user_id: int,
    name: str,
    description: str,
    request_template_id: int | str | None = None,
    specifications: dict | None = None,
) -> dict[str, Any]:
    name = name.strip()
    description = description.strip()
    if not name:
        raise ValueError("Name is required")
    if not description:
        raise ValueError("Description is required")
    tpl_id: int | None = None
    if request_template_id is not None and request_template_id != "":
        tpl = rtpl_svc.resolve_request_template(request_template_id)
        if not tpl:
            raise ValueError("Request template not found")
        tpl_id = tpl["id"]
    now = _utc_now_iso()
    with db.cursor() as cur:
        public_id = _next_request_public_id(cur)
        cur.execute(
            """
            INSERT INTO service_requests
            (public_id, requester_user_id, name, description, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, 'draft', ?, ?)
            """,
            (public_id, requester_user_id, name, description, now, now),
        )
        rid = cur.lastrowid
        we_svc.log_workflow_event(
            cur,
            record_type="request",
            record_id=rid,
            event_type="created",
            actor_user_id=requester_user_id,
            payload={"public_id": public_id},
        )
    if tpl_id:
        add_ritm_to_request(
            rid,
            request_template_id=tpl_id,
            specifications=specifications,
            actor_user_id=requester_user_id,
        )
    else:
        add_ritm_to_request(
            rid,
            item_type=name,
            specifications={"description": description},
            actor_user_id=requester_user_id,
        )
    detail = get_request_detail(rid)
    assert detail is not None
    return detail


def add_ritm_to_request(
    request_ref: str | int,
    *,
    request_template_id: int | str | None = None,
    catalog_item_id: int | str | None = None,
    item_type: str = "",
    specifications: dict | None = None,
    packages: list | None = None,
    application: str = "",
    actor_user_id: int,
) -> dict[str, Any]:
    raw_tpl = request_template_id if request_template_id is not None else catalog_item_id
    tpl_id: int | None = None
    if raw_tpl is not None and raw_tpl != "":
        tpl = rtpl_svc.resolve_request_template(raw_tpl)
        if not tpl:
            raise ValueError("Request template not found")
        tpl_id = tpl["id"]
    with db.cursor() as cur:
        req = _get_request_row(cur, request_ref)
        if not req:
            raise ValueError("Request not found")
        if req["status"] != "draft":
            raise ValueError("Can only add RITMs to draft requests")
        specs = specifications or {}
        itype = item_type
        if tpl_id:
            tpl = rtpl_svc.get_request_template(tpl_id)
            if not tpl:
                raise ValueError("Request template not found")
            defs = cf_svc.list_definitions("request_template", tpl_id)
            if not specs:
                specs = rtpl_svc.get_default_field_values(tpl_id)
            specs = cf_svc.validate_values(defs, specs)
            if not itype:
                itype = tpl["name"]
        now = _utc_now_iso()
        cur.execute(
            """
            INSERT INTO requested_items
            (public_id, request_id, request_template_id, item_type, specifications, packages,
             application, status, created_at, updated_at)
            VALUES ('TEMP', ?, ?, ?, ?, '[]', '', 'draft', ?, ?)
            """,
            (
                req["id"],
                tpl_id,
                itype,
                json.dumps(specs),
                now,
                now,
            ),
        )
        ritm_id = cur.lastrowid
        public_id = f"RITM-{ritm_id}"
        cur.execute(
            "UPDATE requested_items SET public_id = ? WHERE id = ?",
            (public_id, ritm_id),
        )
        we_svc.log_workflow_event(
            cur,
            record_type="request",
            record_id=req["id"],
            event_type="ritm_added",
            actor_user_id=actor_user_id,
            payload={"ritm_public_id": public_id},
        )
        cur.execute("SELECT * FROM requested_items WHERE id = ?", (ritm_id,))
        return _ritm_out(cur, dict(cur.fetchone()))


def is_simple_request(detail: dict[str, Any]) -> bool:
    """True when no RITM is tied to a request template (ad-hoc / non-catalog workflow)."""
    ritms = detail.get("ritms") or []
    return all(not r.get("request_template_id") for r in ritms)


def _validate_kb_article_id(cur, kb_id: int | None) -> None:
    if kb_id is None:
        return
    cur.execute("SELECT id FROM kb_articles WHERE id = ?", (kb_id,))
    if not cur.fetchone():
        raise ValueError(f"KB article {kb_id} not found")


def _require_simple_open(detail: dict[str, Any]) -> None:
    if not is_simple_request(detail):
        raise ValueError("Only non-template requests support this action")
    if detail["status"] != "draft":
        raise ValueError("Request is not open for updates")


def _list_request_comments(cur, request_id: int) -> list[dict[str, Any]]:
    cur.execute(
        """
        SELECT c.*, u.username AS author_username
        FROM request_comments c
        JOIN users u ON u.id = c.author_user_id
        WHERE c.request_id = ?
        ORDER BY c.created_at ASC
        """,
        (request_id,),
    )
    return [dict(r) for r in cur.fetchall()]


def add_request_comment(
    request_ref: str | int,
    body: str,
    actor_user_id: int,
) -> dict[str, Any]:
    body = body.strip()
    if not body:
        raise ValueError("Comment body is required")
    detail = get_request_detail(request_ref)
    if not detail:
        raise ValueError("Request not found")
    _require_simple_open(detail)
    now = _utc_now_iso()
    with db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO request_comments (request_id, author_user_id, body, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (detail["id"], actor_user_id, body, now),
        )
        cur.execute(
            "UPDATE service_requests SET updated_at = ? WHERE id = ?",
            (now, detail["id"]),
        )
        we_svc.log_workflow_event(
            cur,
            record_type="request",
            record_id=detail["id"],
            event_type="comment_added",
            actor_user_id=actor_user_id,
            payload={"comment_preview": body[:200]},
        )
    result = get_request_detail(request_ref)
    assert result is not None
    return result


def set_request_resolution_kb(
    request_ref: str | int,
    kb_article_id: int | None,
    actor_user_id: int,
) -> dict[str, Any]:
    detail = get_request_detail(request_ref)
    if not detail:
        raise ValueError("Request not found")
    _require_simple_open(detail)
    now = _utc_now_iso()
    with db.cursor() as cur:
        _validate_kb_article_id(cur, kb_article_id)
        cur.execute(
            """
            UPDATE service_requests
            SET resolution_kb_article_id = ?, updated_at = ?
            WHERE id = ?
            """,
            (kb_article_id, now, detail["id"]),
        )
        we_svc.log_workflow_event(
            cur,
            record_type="request",
            record_id=detail["id"],
            event_type="kb_assigned",
            actor_user_id=actor_user_id,
            payload={"resolution_kb_article_id": kb_article_id},
        )
    result = get_request_detail(request_ref)
    assert result is not None
    return result


def close_request(
    request_ref: str | int,
    actor_user_id: int,
    *,
    resolution_kb_article_id: int | None = None,
) -> dict[str, Any]:
    detail = get_request_detail(request_ref)
    if not detail:
        raise ValueError("Request not found")
    if not is_simple_request(detail):
        raise ValueError("Only non-template requests can be closed this way")
    if detail["status"] in ("closed", "cancelled"):
        raise ValueError("Request already closed or cancelled")
    if detail["status"] != "draft":
        raise ValueError("Use the normal workflow to complete template-based requests")
    now = _utc_now_iso()
    kb_id = resolution_kb_article_id
    if kb_id is None:
        kb_id = detail.get("resolution_kb_article_id")
    with db.cursor() as cur:
        _validate_kb_article_id(cur, kb_id)
        cur.execute(
            """
            UPDATE service_requests
            SET status = 'closed', closed_at = ?, updated_at = ?,
                resolution_kb_article_id = ?
            WHERE id = ?
            """,
            (now, now, kb_id, detail["id"]),
        )
        cur.execute(
            "UPDATE requested_items SET status = 'closed', updated_at = ? WHERE request_id = ?",
            (now, detail["id"]),
        )
        payload: dict[str, Any] = {}
        if kb_id is not None:
            payload["resolution_kb_article_id"] = kb_id
        we_svc.log_workflow_event(
            cur,
            record_type="request",
            record_id=detail["id"],
            event_type="closed",
            actor_user_id=actor_user_id,
            payload=payload,
        )
    result = get_request_detail(request_ref)
    assert result is not None
    return result


def get_request_detail(request_ref: str | int) -> dict[str, Any] | None:
    with db.cursor() as cur:
        req = _get_request_row(cur, request_ref)
        if not req:
            return None
        cur.execute(
            "SELECT username FROM users WHERE id = ?",
            (req["requester_user_id"],),
        )
        urow = cur.fetchone()
        cur.execute(
            "SELECT * FROM requested_items WHERE request_id = ? ORDER BY id ASC",
            (req["id"],),
        )
        ritms = [_ritm_out(cur, dict(r)) for r in cur.fetchall()]
        events = we_svc.list_events_for_record("request", req["id"])
        comments = _list_request_comments(cur, req["id"])
        kb_article = None
        kb_id = req.get("resolution_kb_article_id")
        if kb_id:
            cur.execute("SELECT id, title FROM kb_articles WHERE id = ?", (kb_id,))
            kb_row = cur.fetchone()
            if kb_row:
                kb_article = {"id": kb_row[0], "title": kb_row[1]}
        return {
            **dict(req),
            "requester_username": urow[0] if urow else "",
            "ritms": ritms,
            "events": events,
            "comments": comments,
            "resolution_kb_article": kb_article,
            "is_simple_request": is_simple_request({"ritms": ritms}),
        }


def patch_request(
    request_ref: str | int,
    *,
    name: str | None = None,
    description: str | None = None,
) -> dict[str, Any] | None:
    with db.cursor() as cur:
        req = _get_request_row(cur, request_ref)
        if not req:
            return None
        if req["status"] != "draft":
            raise ValueError("Can only edit draft requests")
        now = _utc_now_iso()
        cur.execute(
            """
            UPDATE service_requests SET
                name = COALESCE(?, name),
                description = COALESCE(?, description),
                updated_at = ?
            WHERE id = ?
            """,
            (name.strip() if name is not None else None, description.strip() if description is not None else None, now, req["id"]),
        )
    return get_request_detail(request_ref)


def cancel_request(request_ref: str | int, actor_user_id: int) -> dict[str, Any]:
    with db.cursor() as cur:
        req = _get_request_row(cur, request_ref)
        if not req:
            raise ValueError("Request not found")
        if req["status"] in ("closed", "cancelled"):
            raise ValueError("Request already closed or cancelled")
        now = _utc_now_iso()
        cur.execute(
            "UPDATE service_requests SET status = 'cancelled', updated_at = ? WHERE id = ?",
            (now, req["id"]),
        )
        we_svc.log_workflow_event(
            cur,
            record_type="request",
            record_id=req["id"],
            event_type="cancelled",
            actor_user_id=actor_user_id,
        )
    detail = get_request_detail(request_ref)
    assert detail is not None
    return detail


def delete_request(request_ref: str | int) -> bool:
    with db.cursor() as cur:
        req = _get_request_row(cur, request_ref)
        if not req:
            return False
        if req["status"] != "draft":
            raise ValueError("Can only delete draft requests")
        cur.execute("DELETE FROM service_requests WHERE id = ?", (req["id"],))
        return cur.rowcount > 0


def list_ritms_for_request(request_ref: str | int) -> list[dict[str, Any]]:
    detail = get_request_detail(request_ref)
    if not detail:
        return []
    return detail.get("ritms", [])


def get_ritm_detail(ritm_ref: str | int) -> dict[str, Any] | None:
    with db.cursor() as cur:
        ritm = _get_ritm_row(cur, ritm_ref)
        if not ritm:
            return None
        return _ritm_out(cur, ritm)


def patch_ritm(
    ritm_ref: str | int,
    *,
    item_type: str | None = None,
    specifications: dict | None = None,
    packages: list | None = None,
    application: str | None = None,
) -> dict[str, Any] | None:
    with db.cursor() as cur:
        ritm = _get_ritm_row(cur, ritm_ref)
        if not ritm:
            return None
        if ritm["status"] != "draft":
            raise ValueError("Can only edit draft RITMs")
        cur.execute("SELECT status FROM service_requests WHERE id = ?", (ritm["request_id"],))
        req = cur.fetchone()
        if not req or req[0] != "draft":
            raise ValueError("Parent request is not draft")
        specs = _parse_json(ritm["specifications"], {})
        pkgs = _parse_json(ritm["packages"], [])
        if specifications is not None and ritm.get("request_template_id"):
            defs = cf_svc.list_definitions("request_template", ritm["request_template_id"])
            specs = cf_svc.validate_values(defs, specifications)
        elif specifications is not None:
            specs = specifications
        now = _utc_now_iso()
        cur.execute(
            """
            UPDATE requested_items SET
                item_type = COALESCE(?, item_type),
                specifications = ?, packages = ?,
                application = COALESCE(?, application),
                updated_at = ?
            WHERE id = ?
            """,
            (
                item_type,
                json.dumps(specs),
                json.dumps(packages if packages is not None else pkgs),
                application,
                now,
                ritm["id"],
            ),
        )
    return get_ritm_detail(ritm_ref)


def mark_request_status(request_id: int, status: str) -> None:
    now = _utc_now_iso()
    with db.cursor() as cur:
        extra = ""
        params: list[Any] = [status, now, request_id]
        if status == "submitted":
            extra = ", submitted_at = ?"
            params = [status, now, now, request_id]
        cur.execute(
            f"UPDATE service_requests SET status = ?, updated_at = ?{extra} WHERE id = ?",
            params,
        )


def mark_ritm_status(ritm_id: int, status: str) -> None:
    now = _utc_now_iso()
    with db.cursor() as cur:
        cur.execute(
            "UPDATE requested_items SET status = ?, updated_at = ? WHERE id = ?",
            (status, now, ritm_id),
        )


def list_ritms_by_request_id(request_id: int) -> list[dict[str, Any]]:
    with db.cursor() as cur:
        cur.execute(
            "SELECT * FROM requested_items WHERE request_id = ? ORDER BY id ASC",
            (request_id,),
        )
        return [_ritm_out(cur, dict(r)) for r in cur.fetchall()]


REQUEST_STATE_LABELS: dict[str, str] = {
    "draft": "Draft",
    "submitted": "Submitted",
    "in_progress": "In Progress",
    "fulfilled": "Fulfilled",
    "closed": "Closed",
    "cancelled": "Cancelled",
}


def _request_activity_lines(event: dict[str, Any]) -> list[str]:
    from app.services.record_ui import default_event_lines

    et = event.get("event_type") or ""
    payload = event.get("payload") or {}
    if et == "created":
        name = payload.get("name")
        return [f"Short description: {name}"] if name else ["Request created"]
    if et == "ritm_added":
        ritm = payload.get("ritm_public_id") or payload.get("public_id")
        return [f"Requested item added: {ritm}"] if ritm else ["Requested item added"]
    if et == "comment_added":
        preview = (payload.get("comment_preview") or "").strip()
        return [preview] if preview else ["Comment added"]
    if et == "submitted":
        return ["State: Submitted was Draft"]
    if et == "closed":
        return ["State: Closed"]
    if et == "cancelled":
        return ["State: Cancelled"]
    if et == "fulfilled":
        return ["State: Fulfilled"]
    if et == "kb_assigned":
        title = payload.get("kb_title")
        return [f"Knowledge article assigned: {title}"] if title else ["Knowledge article assigned"]
    return default_event_lines(event)


def present_request(detail: dict[str, Any]) -> dict[str, Any]:
    from app.services.record_ui import build_activities, format_sn_datetime, status_label

    events = detail.get("events", [])
    status = detail.get("status", "")
    return {
        **detail,
        "display_number": detail.get("public_id", ""),
        "state_label": REQUEST_STATE_LABELS.get(status, status_label(status)),
        "opened_display": format_sn_datetime(detail.get("created_at")),
        "submitted_display": format_sn_datetime(detail.get("submitted_at")),
        "closed_display": format_sn_datetime(detail.get("closed_at")),
        "activities": build_activities(events, _request_activity_lines),
    }

