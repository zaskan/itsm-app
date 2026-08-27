"""Change tasks (CTASK) — list and create across changes."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app import db
from app.services import changes as chg_svc
from app.services import custom_fields as cf_svc
from app.services import task_templates as tt_svc
from app.services import workflow as wf_svc
from app.services import workflow_events as we_svc


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _get_ctask_row(cur, ident: str | int) -> dict | None:
    if isinstance(ident, int):
        cur.execute("SELECT * FROM change_tasks WHERE id = ?", (ident,))
    elif isinstance(ident, str) and ident.isdigit():
        cur.execute("SELECT * FROM change_tasks WHERE id = ?", (int(ident),))
    else:
        cur.execute("SELECT * FROM change_tasks WHERE public_id = ?", (str(ident),))
    row = cur.fetchone()
    return dict(row) if row else None


def _task_out(cur, row: dict) -> dict[str, Any]:
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
    if d.get("change_id") and not d.get("change_public_id"):
        cur.execute("SELECT public_id, status FROM change_requests WHERE id = ?", (d["change_id"],))
        chg = cur.fetchone()
        if chg:
            d["change_public_id"] = chg[0]
            d["change_status"] = chg[1]
        else:
            d["change_public_id"] = None
            d["change_status"] = None
    elif not d.get("change_id"):
        d["change_public_id"] = None
        d["change_status"] = None
    return d


OPEN_STATUSES = ("blocked", "pending", "in_progress")


def list_tasks(
    *,
    status: str | None = None,
    open_only: bool = False,
    q: str | None = None,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if open_only:
        placeholders = ",".join("?" * len(OPEN_STATUSES))
        clauses.append(f"t.status IN ({placeholders})")
        params.extend(OPEN_STATUSES)
    elif status:
        clauses.append("t.status = ?")
        params.append(status)
    if q:
        like = f"%{q}%"
        clauses.append("(t.public_id LIKE ? OR t.title LIKE ? OR COALESCE(c.public_id, '') LIKE ?)")
        params.extend([like, like, like])
    where = " AND ".join(clauses) if clauses else "1=1"
    sql = f"""
        SELECT t.*, c.public_id AS change_public_id, c.status AS change_status,
               u.username AS assigned_username
        FROM change_tasks t
        LEFT JOIN change_requests c ON c.id = t.change_id
        LEFT JOIN users u ON u.id = t.assigned_user_id
        WHERE {where}
        ORDER BY t.updated_at DESC, t.sequence_order ASC
    """
    with db.cursor() as cur:
        cur.execute(sql, params)
        return [_task_out(cur, dict(r)) for r in cur.fetchall()]


def get_task_detail(task_ref: str | int) -> dict[str, Any] | None:
    with db.cursor() as cur:
        task = _get_ctask_row(cur, task_ref)
        if not task:
            return None
        out = _task_out(cur, task)
        events = we_svc.list_events_for_record("task", out["id"])
        if out.get("change_id"):
            change_events = we_svc.list_events_for_record("change", out["change_id"])
            pid = out["public_id"]
            for event in change_events:
                payload = event.get("payload") or {}
                if payload.get("ctask_public_id") == pid:
                    events.append(event)
            events.sort(key=lambda e: e.get("created_at") or "")
        out["events"] = events
        return out


def _resolve_template_fields(
    *,
    title: str,
    description: str,
    assigned_user_id: int | None,
    kb_article_id: int | None,
    task_template_id: int | None,
    custom_fields: dict | None,
) -> tuple[str, str, int | None, int | None, dict]:
    tpl_title = title.strip()
    tpl_desc = description
    tpl_user = assigned_user_id
    tpl_kb = kb_article_id
    field_values = custom_fields or {}

    if task_template_id is not None:
        tpl = tt_svc.get_task_template(task_template_id)
        if not tpl:
            raise ValueError("Task template not found")
        if not tpl_title:
            tpl_title = tpl["title"]
        if not tpl_desc:
            tpl_desc = tpl["description"]
        if tpl_user is None:
            tpl_user = tpl.get("assigned_user_id")
        if tpl_kb is None:
            tpl_kb = tpl["kb_article_id"]
        defs = cf_svc.list_definitions("task_template", task_template_id)
        field_values = cf_svc.validate_values(defs, field_values)

    if not tpl_title:
        raise ValueError("title is required")
    return tpl_title, tpl_desc, tpl_user, tpl_kb, field_values


def _validate_task_refs(cur, tpl_user: int | None, tpl_kb: int | None) -> None:
    if tpl_kb is not None:
        cur.execute("SELECT id FROM kb_articles WHERE id = ?", (tpl_kb,))
        if not cur.fetchone():
            raise ValueError(f"KB article {tpl_kb} not found")
    if tpl_user is not None:
        cur.execute("SELECT id FROM users WHERE id = ?", (tpl_user,))
        if not cur.fetchone():
            raise ValueError("Assigned user not found")


def create_task(
    *,
    change_ref: str | int | None = None,
    title: str,
    description: str = "",
    assigned_user_id: int | None = None,
    kb_article_id: int | None = None,
    task_template_id: int | None = None,
    custom_fields: dict | None = None,
    actor_user_id: int,
) -> dict[str, Any]:
    standalone = change_ref is None or (isinstance(change_ref, str) and not str(change_ref).strip())
    tpl_title, tpl_desc, tpl_user, tpl_kb, field_values = _resolve_template_fields(
        title=title,
        description=description,
        assigned_user_id=assigned_user_id,
        kb_article_id=kb_article_id,
        task_template_id=task_template_id,
        custom_fields=custom_fields,
    )

    with db.cursor() as cur:
        _validate_task_refs(cur, tpl_user, tpl_kb)
        now = _utc_now_iso()

        if standalone:
            cur.execute(
                """
                INSERT INTO change_tasks
                (public_id, change_id, sequence_order, title, description, assigned_user_id,
                 kb_article_id, status, custom_fields, created_at, updated_at)
                VALUES ('TEMP', NULL, 1, ?, ?, ?, ?, 'pending', ?, ?, ?)
                """,
                (
                    tpl_title,
                    tpl_desc,
                    tpl_user,
                    tpl_kb,
                    cf_svc.dump_json_values(field_values),
                    now,
                    now,
                ),
            )
            tid = cur.lastrowid
            tpid = f"CTASK-{tid}"
            cur.execute(
                "UPDATE change_tasks SET public_id = ? WHERE id = ?",
                (tpid, tid),
            )
            we_svc.log_workflow_event(
                cur,
                record_type="task",
                record_id=tid,
                event_type="created",
                actor_user_id=actor_user_id,
                payload={"ctask_public_id": tpid},
            )
            cur.execute("SELECT * FROM change_tasks WHERE id = ?", (tid,))
            return _task_out(cur, dict(cur.fetchone()))

        chg = chg_svc._get_change_row(cur, change_ref)  # noqa: SLF001
        if not chg:
            raise ValueError("Change not found")
        if chg["status"] in ("completed", "cancelled"):
            raise ValueError("Cannot add tasks to completed or cancelled changes")

        cur.execute(
            "SELECT COALESCE(MAX(sequence_order), 0) FROM change_tasks WHERE change_id = ?",
            (chg["id"],),
        )
        seq = cur.fetchone()[0] + 1
        initial_status = "pending" if chg["status"] in ("approved", "implementing") else "blocked"
        cur.execute(
            """
            INSERT INTO change_tasks
            (public_id, change_id, sequence_order, title, description, assigned_user_id,
             kb_article_id, status, custom_fields, created_at, updated_at)
            VALUES ('TEMP', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                chg["id"],
                seq,
                tpl_title,
                tpl_desc,
                tpl_user,
                tpl_kb,
                initial_status,
                cf_svc.dump_json_values(field_values),
                now,
                now,
            ),
        )
        tid = cur.lastrowid
        tpid = f"CTASK-{chg['id']}-{seq:02d}"
        cur.execute(
            "UPDATE change_tasks SET public_id = ? WHERE id = ?",
            (tpid, tid),
        )
        we_svc.log_workflow_event(
            cur,
            record_type="change",
            record_id=chg["id"],
            event_type="task_created",
            actor_user_id=actor_user_id,
            payload={"ctask_public_id": tpid},
        )
        cur.execute("SELECT * FROM change_tasks WHERE id = ?", (tid,))
        return _task_out(cur, dict(cur.fetchone()))


def _change_ref_for_task(task: dict) -> str:
    with db.cursor() as cur:
        cur.execute("SELECT public_id FROM change_requests WHERE id = ?", (task["change_id"],))
        chg = cur.fetchone()
        if not chg:
            raise ValueError("Change not found")
        return chg[0]


def start_task(task_ref: str | int, actor_user_id: int) -> dict[str, Any]:
    with db.cursor() as cur:
        task = _get_ctask_row(cur, task_ref)
        if not task:
            raise ValueError("Task not found")

    if task["change_id"] is not None:
        change_ref = _change_ref_for_task(task)
        chg_svc.start_ctask(change_ref, task_ref, actor_user_id)
        detail = get_task_detail(task_ref)
        assert detail is not None
        return detail

    if task["status"] != "pending":
        raise ValueError("Task must be pending to start")
    now = _utc_now_iso()
    with db.cursor() as cur:
        cur.execute(
            "UPDATE change_tasks SET status = 'in_progress', updated_at = ? WHERE id = ?",
            (now, task["id"]),
        )
        we_svc.log_workflow_event(
            cur,
            record_type="task",
            record_id=task["id"],
            event_type="started",
            actor_user_id=actor_user_id,
            payload={"ctask_public_id": task["public_id"]},
        )
        cur.execute("SELECT * FROM change_tasks WHERE id = ?", (task["id"],))
        return _task_out(cur, dict(cur.fetchone()))


def complete_task(
    task_ref: str | int,
    actor_user_id: int,
    completion_comment: str = "",
) -> dict[str, Any]:
    with db.cursor() as cur:
        task = _get_ctask_row(cur, task_ref)
        if not task:
            raise ValueError("Task not found")
        if task["status"] != "in_progress":
            raise ValueError("Task must be in progress to complete")

    comment = completion_comment.strip()
    if task["change_id"] is not None:
        change_ref = _change_ref_for_task(task)
        wf_svc.on_ctask_completed(
            change_ref, task_ref, actor_user_id, completion_comment=comment
        )
        detail = get_task_detail(task_ref)
        assert detail is not None
        return detail

    now = _utc_now_iso()
    with db.cursor() as cur:
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
            record_type="task",
            record_id=task["id"],
            event_type="completed",
            actor_user_id=actor_user_id,
            payload={"ctask_public_id": task["public_id"], "completion_comment": comment},
        )
        cur.execute("SELECT * FROM change_tasks WHERE id = ?", (task["id"],))
        return _task_out(cur, dict(cur.fetchone()))


TASK_STATE_LABELS: dict[str, str] = {
    "blocked": "Blocked",
    "pending": "Pending",
    "in_progress": "Work in Progress",
    "completed": "Closed Complete",
}


def _task_activity_lines(event: dict[str, Any]) -> list[str]:
    from app.services.record_ui import default_event_lines

    et = event.get("event_type") or ""
    payload = event.get("payload") or {}
    if et == "created":
        tid = payload.get("ctask_public_id")
        return [f"Task created: {tid}"] if tid else ["Task created"]
    if et == "started":
        return ["State: Work in Progress was Pending"]
    if et == "completed":
        comment = (payload.get("completion_comment") or "").strip()
        lines = ["State: Closed Complete"]
        if comment:
            lines.append(comment)
        return lines
    if et == "ctask_started":
        return ["State: Work in Progress was Pending"]
    if et == "ctask_completed":
        comment = (payload.get("completion_comment") or "").strip()
        lines = ["State: Closed Complete"]
        if comment:
            lines.append(comment)
        return lines
    if et == "task_created":
        tid = payload.get("ctask_public_id")
        return [f"Task added: {tid}"] if tid else ["Task added"]
    return default_event_lines(event)


def present_task(detail: dict[str, Any]) -> dict[str, Any]:
    from app.services.record_ui import build_activities, format_sn_datetime, status_label

    events = detail.get("events", [])
    status = detail.get("status", "")
    return {
        **detail,
        "display_number": detail.get("public_id", ""),
        "state_label": TASK_STATE_LABELS.get(status, status_label(status)),
        "opened_display": format_sn_datetime(detail.get("created_at")),
        "completed_display": format_sn_datetime(detail.get("completed_at")),
        "activities": build_activities(events, _task_activity_lines),
    }
