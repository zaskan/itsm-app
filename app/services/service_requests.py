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
        return [dict(r) for r in cur.fetchall()]


def create_request(
    *,
    requester_user_id: int,
    name: str,
    description: str,
    request_template_id: int | None = None,
    specifications: dict | None = None,
) -> dict[str, Any]:
    name = name.strip()
    description = description.strip()
    if not name:
        raise ValueError("Name is required")
    if not description:
        raise ValueError("Description is required")
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
    if request_template_id:
        add_ritm_to_request(
            rid,
            request_template_id=request_template_id,
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
    request_template_id: int | None = None,
    catalog_item_id: int | None = None,
    item_type: str = "",
    specifications: dict | None = None,
    packages: list | None = None,
    application: str = "",
    actor_user_id: int,
) -> dict[str, Any]:
    tpl_id = request_template_id or catalog_item_id
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
        return {
            **dict(req),
            "requester_username": urow[0] if urow else "",
            "ritms": ritms,
            "events": events,
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
