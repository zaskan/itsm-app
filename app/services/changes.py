"""Change requests (CHG) and change task instantiation."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app import db
from app.services import change_templates as ctpl_svc
from app.services import custom_fields as cf_svc
from app.services import request_templates as rtpl_svc
from app.services import task_templates as tt_svc
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


def _get_change_row(cur, ident: str | int) -> dict | None:
    if isinstance(ident, int):
        cur.execute("SELECT * FROM change_requests WHERE id = ?", (ident,))
    elif isinstance(ident, str) and ident.isdigit():
        cur.execute("SELECT * FROM change_requests WHERE id = ?", (int(ident),))
    else:
        cur.execute("SELECT * FROM change_requests WHERE public_id = ?", (str(ident),))
    row = cur.fetchone()
    return dict(row) if row else None


def _get_ctask_row(cur, ident: str | int) -> dict | None:
    if isinstance(ident, int):
        cur.execute("SELECT * FROM change_tasks WHERE id = ?", (ident,))
    elif isinstance(ident, str) and ident.isdigit():
        cur.execute("SELECT * FROM change_tasks WHERE id = ?", (int(ident),))
    else:
        cur.execute("SELECT * FROM change_tasks WHERE public_id = ?", (str(ident),))
    row = cur.fetchone()
    return dict(row) if row else None


def _build_plans(
    ritm: dict | None,
    request_template: dict | None,
    change_template: dict | None,
) -> tuple[str, str, str, str]:
    specs = _parse_json(ritm.get("specifications", "{}"), {}) if ritm else {}
    item_label = (
        ritm.get("item_type", "request item") if ritm else (change_template or {}).get("name", "change")
    )
    spec_summary = ", ".join(f"{k}={v}" for k, v in specs.items() if v) if specs else "see template fields"
    tpl_desc = (request_template or change_template or {}).get("description", "")
    risk = f"Standard provisioning of {item_label}."
    if spec_summary:
        risk = f"{risk} Specifications: {spec_summary}."
    impl = (
        f"1. Execute change tasks per linked templates.\n"
        f"2. Apply configuration from request specifications.\n"
        f"3. Run smoke tests and onboard to monitoring."
    )
    test = (
        "Verify connectivity, configuration values, and monitoring registration "
        "per linked KB articles and template custom fields."
    )
    backout = "Decommission changes and restore previous state if validation fails."
    if tpl_desc:
        risk = f"{risk} Template: {tpl_desc}"
    return risk, impl, test, backout


def _ctask_out(cur, row: dict) -> dict[str, Any]:
    d = dict(row)
    d["custom_fields"] = cf_svc.parse_json_values(d.get("custom_fields"))
    d.setdefault("completion_comment", "")
    if d.get("kb_article_id"):
        cur.execute("SELECT title FROM kb_articles WHERE id = ?", (d["kb_article_id"],))
        kb = cur.fetchone()
        d["kb_article_title"] = kb[0] if kb else None
    else:
        d["kb_article_title"] = None
    if d.get("assigned_user_id") and not d.get("assigned_username"):
        cur.execute("SELECT username FROM users WHERE id = ?", (d["assigned_user_id"],))
        u = cur.fetchone()
        d["assigned_username"] = u[0] if u else None
    elif not d.get("assigned_username"):
        d["assigned_username"] = None
    return d


def _instantiate_tasks_from_template(
    cur,
    change_id: int,
    change_template: dict[str, Any],
    now: str,
) -> None:
    for seq, tid in enumerate(change_template.get("task_template_ids", []), start=1):
        tpl = tt_svc.get_task_template(tid)
        if not tpl:
            continue
        cur.execute(
            """
            INSERT INTO change_tasks
            (public_id, change_id, sequence_order, title, description, assigned_user_id,
             kb_article_id, status, custom_fields, created_at, updated_at)
            VALUES ('TEMP', ?, ?, ?, ?, ?, ?, 'blocked', ?, ?, ?)
            """,
            (
                change_id,
                seq,
                tpl["title"],
                tpl["description"],
                tpl.get("assigned_user_id"),
                tpl["kb_article_id"],
                cf_svc.dump_json_values(tpl.get("custom_fields") or {}),
                now,
                now,
            ),
        )
        task_row_id = cur.lastrowid
        tpid = f"CTASK-{change_id}-{seq:02d}"
        cur.execute(
            "UPDATE change_tasks SET public_id = ? WHERE id = ?",
            (tpid, task_row_id),
        )


OPEN_CHANGE_STATUSES = ("draft", "pending_approval", "approved", "implementing")


def list_changes(
    *,
    status: str | None = None,
    open_only: bool = False,
    q: str | None = None,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if open_only:
        placeholders = ",".join("?" * len(OPEN_CHANGE_STATUSES))
        clauses.append(f"c.status IN ({placeholders})")
        params.extend(OPEN_CHANGE_STATUSES)
    elif status:
        clauses.append("c.status = ?")
        params.append(status)
    if q:
        like = f"%{q}%"
        clauses.append("(c.public_id LIKE ? OR r.public_id LIKE ? OR c.risk_assessment LIKE ?)")
        params.extend([like, like, like])
    where = " AND ".join(clauses) if clauses else "1=1"
    sql = f"""
        SELECT c.*, r.public_id AS ritm_public_id, r.item_type AS ritm_item_type,
               sr.public_id AS request_public_id
        FROM change_requests c
        LEFT JOIN requested_items r ON r.id = c.ritm_id
        LEFT JOIN service_requests sr ON sr.id = r.request_id
        WHERE {where}
        ORDER BY c.created_at DESC
    """
    with db.cursor() as cur:
        cur.execute(sql, params)
        rows = []
        for r in cur.fetchall():
            d = dict(r)
            d["custom_fields"] = cf_svc.parse_json_values(d.get("custom_fields"))
            rows.append(d)
        return rows


def create_change_from_ritm(
    ritm: dict[str, Any],
    *,
    request_template: dict[str, Any] | None,
    actor_user_id: int,
) -> dict[str, Any]:
    change_template = None
    if request_template and request_template.get("change_template_id"):
        change_template = ctpl_svc.get_change_template(request_template["change_template_id"])
    if request_template and request_template.get("require_standard_change"):
        if not change_template or change_template["change_type"] != "standard":
            raise ValueError("Request template requires a standard change")
    change_type = change_template["change_type"] if change_template else "standard"
    risk, impl, test, backout = _build_plans(ritm, request_template, change_template)
    chg_tpl_id = change_template["id"] if change_template else None
    now = _utc_now_iso()
    with db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO change_requests
            (public_id, ritm_id, change_template_id, change_type, risk_assessment,
             implementation_plan, test_plan, backout_plan, status, custom_fields, created_at, updated_at)
            VALUES ('TEMP', ?, ?, ?, ?, ?, ?, ?, 'draft', '{}', ?, ?)
            """,
            (ritm["id"], chg_tpl_id, change_type, risk, impl, test, backout, now, now),
        )
        cid = cur.lastrowid
        public_id = f"CHG-{cid}"
        cur.execute(
            "UPDATE change_requests SET public_id = ? WHERE id = ?",
            (public_id, cid),
        )
        if change_template:
            _instantiate_tasks_from_template(cur, cid, change_template, now)
        we_svc.log_workflow_event(
            cur,
            record_type="change",
            record_id=cid,
            event_type="created",
            actor_user_id=actor_user_id,
            payload={"public_id": public_id, "ritm_id": ritm["id"]},
        )
    detail = get_change_detail(cid)
    assert detail is not None
    return detail


def create_change_from_template(
    *,
    change_template_id: int,
    custom_fields: dict | None = None,
    actor_user_id: int,
) -> dict[str, Any]:
    change_template = ctpl_svc.get_change_template(change_template_id)
    if not change_template:
        raise ValueError("Change template not found")
    defs = cf_svc.list_definitions("change_template", change_template_id)
    validated = cf_svc.validate_values(defs, custom_fields)
    change_type = change_template["change_type"]
    risk, impl, test, backout = _build_plans(None, None, change_template)
    now = _utc_now_iso()
    with db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO change_requests
            (public_id, ritm_id, change_template_id, change_type, risk_assessment,
             implementation_plan, test_plan, backout_plan, status, custom_fields, created_at, updated_at)
            VALUES ('TEMP', NULL, ?, ?, ?, ?, ?, ?, 'draft', ?, ?, ?)
            """,
            (
                change_template_id,
                change_type,
                risk,
                impl,
                test,
                backout,
                cf_svc.dump_json_values(validated),
                now,
                now,
            ),
        )
        cid = cur.lastrowid
        public_id = f"CHG-{cid}"
        cur.execute(
            "UPDATE change_requests SET public_id = ? WHERE id = ?",
            (public_id, cid),
        )
        _instantiate_tasks_from_template(cur, cid, change_template, now)
        we_svc.log_workflow_event(
            cur,
            record_type="change",
            record_id=cid,
            event_type="created",
            actor_user_id=actor_user_id,
            payload={"public_id": public_id, "change_template_id": change_template_id},
        )
    detail = get_change_detail(cid)
    assert detail is not None
    return detail


def get_change_detail(change_ref: str | int) -> dict[str, Any] | None:
    with db.cursor() as cur:
        chg = _get_change_row(cur, change_ref)
        if not chg:
            return None
        chg["custom_fields"] = cf_svc.parse_json_values(chg.get("custom_fields"))
        ritm = None
        if chg.get("ritm_id"):
            cur.execute(
                """
                SELECT r.*, sr.public_id AS request_public_id
                FROM requested_items r
                JOIN service_requests sr ON sr.id = r.request_id
                WHERE r.id = ?
                """,
                (chg["ritm_id"],),
            )
            ritm_row = cur.fetchone()
            ritm = dict(ritm_row) if ritm_row else None
            if ritm:
                ritm["specifications"] = _parse_json(ritm.get("specifications", "{}"), {})
                ritm["packages"] = _parse_json(ritm.get("packages", "[]"), [])
        if chg.get("change_template_id"):
            chg["change_template"] = ctpl_svc.get_change_template(chg["change_template_id"])
        else:
            chg["change_template"] = None
        cur.execute(
            "SELECT * FROM change_tasks WHERE change_id = ? ORDER BY sequence_order ASC",
            (chg["id"],),
        )
        tasks = [_ctask_out(cur, dict(t)) for t in cur.fetchall()]
        if chg.get("approved_by_user_id"):
            cur.execute(
                "SELECT username FROM users WHERE id = ?",
                (chg["approved_by_user_id"],),
            )
            approver = cur.fetchone()
            chg["approved_by_username"] = approver[0] if approver else None
        else:
            chg["approved_by_username"] = None
        events = we_svc.list_events_for_record("change", chg["id"])
        return {**dict(chg), "ritm": ritm, "tasks": tasks, "events": events}


def patch_change(
    change_ref: str | int,
    *,
    risk_assessment: str | None = None,
    implementation_plan: str | None = None,
    test_plan: str | None = None,
    backout_plan: str | None = None,
) -> dict[str, Any] | None:
    with db.cursor() as cur:
        chg = _get_change_row(cur, change_ref)
        if not chg:
            return None
        if chg["status"] not in ("draft", "pending_approval"):
            raise ValueError("Can only edit plans before approval")
        now = _utc_now_iso()
        cur.execute(
            """
            UPDATE change_requests SET
                risk_assessment = COALESCE(?, risk_assessment),
                implementation_plan = COALESCE(?, implementation_plan),
                test_plan = COALESCE(?, test_plan),
                backout_plan = COALESCE(?, backout_plan),
                updated_at = ?
            WHERE id = ?
            """,
            (risk_assessment, implementation_plan, test_plan, backout_plan, now, chg["id"]),
        )
    return get_change_detail(change_ref)


def set_change_pending_approval(change_id: int, actor_user_id: int) -> dict[str, Any]:
    now = _utc_now_iso()
    with db.cursor() as cur:
        cur.execute(
            "UPDATE change_requests SET status = 'pending_approval', updated_at = ? WHERE id = ?",
            (now, change_id),
        )
        we_svc.log_workflow_event(
            cur,
            record_type="change",
            record_id=change_id,
            event_type="pending_approval",
            actor_user_id=actor_user_id,
        )
    detail = get_change_detail(change_id)
    assert detail is not None
    return detail


def approve_change(change_ref: str | int, actor_user_id: int) -> dict[str, Any]:
    with db.cursor() as cur:
        chg = _get_change_row(cur, change_ref)
        if not chg:
            raise ValueError("Change not found")
        if chg["status"] == "approved":
            return get_change_detail(change_ref)  # type: ignore
        if chg["status"] not in ("pending_approval", "draft"):
            raise ValueError("Change cannot be approved in current status")
        now = _utc_now_iso()
        cur.execute(
            """
            UPDATE change_requests SET status = 'approved', approved_at = ?,
                approved_by_user_id = ?, updated_at = ?
            WHERE id = ?
            """,
            (now, actor_user_id, now, chg["id"]),
        )
        cur.execute(
            """
            UPDATE change_tasks SET status = 'pending', updated_at = ?
            WHERE change_id = ? AND status = 'blocked'
            """,
            (now, chg["id"]),
        )
        cur.execute(
            "UPDATE change_requests SET status = 'implementing', updated_at = ? WHERE id = ?",
            (now, chg["id"]),
        )
        we_svc.log_workflow_event(
            cur,
            record_type="change",
            record_id=chg["id"],
            event_type="approved",
            actor_user_id=actor_user_id,
        )
    detail = get_change_detail(change_ref)
    assert detail is not None
    return detail


def auto_approve_change(change_id: int, actor_user_id: int) -> dict[str, Any]:
    return approve_change(change_id, actor_user_id)


def cancel_change(change_ref: str | int, actor_user_id: int) -> dict[str, Any]:
    with db.cursor() as cur:
        chg = _get_change_row(cur, change_ref)
        if not chg:
            raise ValueError("Change not found")
        if chg["status"] in ("completed", "cancelled"):
            raise ValueError("Change already completed or cancelled")
        now = _utc_now_iso()
        cur.execute(
            "UPDATE change_requests SET status = 'cancelled', updated_at = ? WHERE id = ?",
            (now, chg["id"]),
        )
        we_svc.log_workflow_event(
            cur,
            record_type="change",
            record_id=chg["id"],
            event_type="cancelled",
            actor_user_id=actor_user_id,
        )
    detail = get_change_detail(change_ref)
    assert detail is not None
    return detail


def start_ctask(change_ref: str | int, ctask_ref: str | int, actor_user_id: int) -> dict[str, Any]:
    with db.cursor() as cur:
        chg = _get_change_row(cur, change_ref)
        if not chg:
            raise ValueError("Change not found")
        if chg["status"] not in ("approved", "implementing"):
            raise ValueError("Change is not approved for implementation")
        task = _get_ctask_row(cur, ctask_ref)
        if not task or task["change_id"] != chg["id"]:
            raise ValueError("Task not found")
        if task["status"] not in ("pending",):
            raise ValueError("Task cannot be started")
        cur.execute(
            """
            SELECT COUNT(*) FROM change_tasks
            WHERE change_id = ? AND sequence_order < ? AND status != 'completed'
            """,
            (chg["id"], task["sequence_order"]),
        )
        if cur.fetchone()[0] > 0:
            raise ValueError("Complete prior tasks first")
        now = _utc_now_iso()
        cur.execute(
            "UPDATE change_tasks SET status = 'in_progress', updated_at = ? WHERE id = ?",
            (now, task["id"]),
        )
        we_svc.log_workflow_event(
            cur,
            record_type="change",
            record_id=chg["id"],
            event_type="ctask_started",
            actor_user_id=actor_user_id,
            payload={"ctask_public_id": task["public_id"]},
        )
    detail = get_change_detail(change_ref)
    assert detail is not None
    return detail


def complete_ctask(
    change_ref: str | int,
    ctask_ref: str | int,
    actor_user_id: int,
    completion_comment: str = "",
) -> dict[str, Any]:
    comment = completion_comment.strip()
    with db.cursor() as cur:
        chg = _get_change_row(cur, change_ref)
        if not chg:
            raise ValueError("Change not found")
        task = _get_ctask_row(cur, ctask_ref)
        if not task or task["change_id"] != chg["id"]:
            raise ValueError("Task not found")
        if task["status"] != "in_progress":
            raise ValueError("Task must be in progress to complete")
        now = _utc_now_iso()
        cur.execute(
            """
            UPDATE change_tasks
            SET status = 'completed', completed_at = ?, updated_at = ?, completion_comment = ?
            WHERE id = ?
            """,
            (now, now, comment, task["id"]),
        )
        we_svc.log_workflow_event(
            cur,
            record_type="change",
            record_id=chg["id"],
            event_type="ctask_completed",
            actor_user_id=actor_user_id,
            payload={"ctask_public_id": task["public_id"], "completion_comment": comment},
        )
    detail = get_change_detail(change_ref)
    assert detail is not None
    return detail


def all_tasks_completed(change_id: int) -> bool:
    with db.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM change_tasks WHERE change_id = ? AND status != 'completed'",
            (change_id,),
        )
        return cur.fetchone()[0] == 0


def mark_change_completed(change_id: int, actor_user_id: int) -> dict[str, Any]:
    now = _utc_now_iso()
    with db.cursor() as cur:
        cur.execute(
            "UPDATE change_requests SET status = 'completed', updated_at = ? WHERE id = ?",
            (now, change_id),
        )
        we_svc.log_workflow_event(
            cur,
            record_type="change",
            record_id=change_id,
            event_type="completed",
            actor_user_id=actor_user_id,
        )
    detail = get_change_detail(change_id)
    assert detail is not None
    return detail
