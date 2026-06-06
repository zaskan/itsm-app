"""Workflow orchestration across REQ, RITM, CHG, and CTASK."""

from __future__ import annotations

from typing import Any

from app import db
from app.services import changes as chg_svc
from app.services import request_templates as rtpl_svc
from app.services import service_requests as req_svc
from app.services import workflow_events as we_svc


def submit_request(request_ref: str | int, actor_user_id: int) -> dict[str, Any]:
    return on_request_submitted(request_ref, actor_user_id)


def on_request_submitted(request_ref: str | int, actor_user_id: int) -> dict[str, Any]:
    detail = req_svc.get_request_detail(request_ref)
    if not detail:
        raise ValueError("Request not found")
    if detail["status"] != "draft":
        raise ValueError("Only draft requests can be submitted")
    if not detail.get("ritms"):
        raise ValueError("Add at least one requested item before submitting")

    changes_created: list[dict[str, Any]] = []
    for ritm in detail["ritms"]:
        request_template = None
        if ritm.get("request_template_id"):
            request_template = rtpl_svc.get_request_template(ritm["request_template_id"])
        req_svc.mark_ritm_status(ritm["id"], "submitted")
        chg = chg_svc.create_change_from_ritm(
            ritm, request_template=request_template, actor_user_id=actor_user_id
        )
        change_type = chg.get("change_type", "standard")
        if change_type == "standard":
            chg = chg_svc.auto_approve_change(chg["id"], actor_user_id)
        else:
            chg = chg_svc.set_change_pending_approval(chg["id"], actor_user_id)
        req_svc.mark_ritm_status(ritm["id"], "in_progress")
        changes_created.append(chg)

    req_svc.mark_request_status(detail["id"], "submitted")
    req_svc.mark_request_status(detail["id"], "in_progress")

    with db.cursor() as cur:
        we_svc.log_workflow_event(
            cur,
            record_type="request",
            record_id=detail["id"],
            event_type="submitted",
            actor_user_id=actor_user_id,
            payload={"changes": [c["public_id"] for c in changes_created]},
        )

    result = req_svc.get_request_detail(request_ref)
    assert result is not None
    result["changes_created"] = changes_created
    return result


def on_ctask_completed(
    change_ref: str | int,
    ctask_ref: str | int,
    actor_user_id: int,
    completion_comment: str = "",
) -> dict[str, Any]:
    chg = chg_svc.complete_ctask(change_ref, ctask_ref, actor_user_id, completion_comment)
    change_id = chg["id"]
    if not chg_svc.all_tasks_completed(change_id):
        return chg

    chg_svc.mark_change_completed(change_id, actor_user_id)
    ritm = chg.get("ritm")
    if not ritm:
        return chg

    req_svc.mark_ritm_status(ritm["id"], "fulfilled")

    request_id = ritm["request_id"]
    ritms = req_svc.list_ritms_by_request_id(request_id)
    if all(r["status"] in ("fulfilled", "closed") for r in ritms):
        req_svc.mark_request_status(request_id, "fulfilled")
        with db.cursor() as cur:
            we_svc.log_workflow_event(
                cur,
                record_type="request",
                record_id=request_id,
                event_type="fulfilled",
                actor_user_id=actor_user_id,
            )

    detail = chg_svc.get_change_detail(change_ref)
    assert detail is not None
    return detail
