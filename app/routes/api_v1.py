"""REST API v1."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status

from app import schemas
from app.auth_deps import get_current_user_basic
from app.services import incidents as inc_svc
from app.services import kb as kb_svc
from app.services import webhooks as wh_svc

router = APIRouter(prefix="/api/v1", tags=["api-v1"])


@router.get("/incidents", response_model=list[schemas.IncidentOut])
def api_list_incidents(
    _user: Annotated[dict, Depends(get_current_user_basic)],
    q: str | None = None,
    status_filter: str | None = Query(None, alias="status"),
    severity: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[dict]:
    return inc_svc.list_incidents(
        q=q,
        status=status_filter,
        severity=severity,
        date_from=date_from,
        date_to=date_to,
    )


@router.post("/incidents", response_model=schemas.IncidentOut, status_code=status.HTTP_201_CREATED)
def api_create_incident(
    background_tasks: BackgroundTasks,
    user: Annotated[dict, Depends(get_current_user_basic)],
    body: schemas.IncidentCreate,
) -> dict:
    created_at = body.created_at.isoformat() if body.created_at else None
    try:
        snap = inc_svc.create_incident(
            title=body.title,
            description=body.description,
            severity=body.severity,
            actor_user_id=user["id"],
            created_at=created_at,
        )
    except Exception as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    wh_svc.schedule_incident_webhook(
        background_tasks,
        "created",
        user["username"],
        snap,
    )
    return {k: v for k, v in snap.items() if k != "comments"}


@router.get("/incidents/{incident_ref}", response_model=dict)
def api_get_incident(
    user: Annotated[dict, Depends(get_current_user_basic)],
    incident_ref: str,
) -> dict:
    del user
    d = inc_svc.get_incident_detail(incident_ref)
    if not d:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    return d


@router.patch("/incidents/{incident_ref}", response_model=schemas.IncidentOut)
def api_patch_incident(
    background_tasks: BackgroundTasks,
    user: Annotated[dict, Depends(get_current_user_basic)],
    incident_ref: str,
    body: schemas.IncidentPatch,
) -> dict:
    if body.severity is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No fields to update")
    try:
        snap_full = inc_svc.update_severity(
            incident_ref,
            body.severity,
            user["id"],
        )
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    snap = {k: v for k, v in snap_full.items() if k != "comments"}
    wh_svc.schedule_incident_webhook(
        background_tasks, "severity_changed", user["username"], snap_full
    )
    return snap


@router.post("/incidents/{incident_ref}/comments", response_model=dict)
def api_add_comment(
    background_tasks: BackgroundTasks,
    user: Annotated[dict, Depends(get_current_user_basic)],
    incident_ref: str,
    body: schemas.CommentCreate,
) -> dict:
    try:
        snap_full = inc_svc.add_comment(incident_ref, body.body, user["id"])
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    wh_svc.schedule_incident_webhook(
        background_tasks, "comment_added", user["username"], snap_full
    )
    return snap_full


@router.post("/incidents/{incident_ref}/close", response_model=dict)
def api_close(
    background_tasks: BackgroundTasks,
    user: Annotated[dict, Depends(get_current_user_basic)],
    incident_ref: str,
) -> dict:
    try:
        snap_full = inc_svc.close_incident(incident_ref, user["id"])
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    wh_svc.schedule_incident_webhook(
        background_tasks, "closed", user["username"], snap_full
    )
    return snap_full


@router.get("/kb/articles", response_model=list[schemas.KBArticleOut])
def api_kb_list(
    user: Annotated[dict, Depends(get_current_user_basic)],
    q: str | None = None,
) -> list[dict]:
    del user
    return kb_svc.list_articles(q=q)


@router.post("/kb/articles", response_model=schemas.KBArticleOut, status_code=status.HTTP_201_CREATED)
def api_kb_create(
    user: Annotated[dict, Depends(get_current_user_basic)],
    body: schemas.KBArticleCreate,
) -> dict:
    del user
    return kb_svc.create_article(body.title, body.description)


@router.get("/kb/articles/{article_id}", response_model=schemas.KBArticleOut)
def api_kb_get(
    user: Annotated[dict, Depends(get_current_user_basic)],
    article_id: int,
) -> dict:
    del user
    art = kb_svc.get_article(article_id)
    if not art:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    return art


@router.patch("/kb/articles/{article_id}", response_model=schemas.KBArticleOut)
def api_kb_patch(
    user: Annotated[dict, Depends(get_current_user_basic)],
    article_id: int,
    body: schemas.KBArticleUpdate,
) -> dict:
    del user
    art = kb_svc.update_article(article_id, body.title, body.description)
    if not art:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    return art


@router.delete("/kb/articles/{article_id}", status_code=status.HTTP_204_NO_CONTENT)
def api_kb_delete(
    user: Annotated[dict, Depends(get_current_user_basic)],
    article_id: int,
) -> None:
    del user
    if not kb_svc.delete_article(article_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")


@router.get("/settings/webhook", response_model=schemas.WebhookSettings)
def api_get_webhook(user: Annotated[dict, Depends(get_current_user_basic)]) -> dict:
    del user
    return {"webhook_url": wh_svc.get_webhook_url()}


@router.put("/settings/webhook", response_model=schemas.WebhookSettings)
def api_put_webhook(
    user: Annotated[dict, Depends(get_current_user_basic)],
    body: schemas.WebhookSettings,
) -> dict:
    del user
    wh_svc.set_webhook_url(body.webhook_url)
    return {"webhook_url": wh_svc.get_webhook_url()}


@router.get("/users", response_model=list[schemas.UserOut])
def api_list_users(user: Annotated[dict, Depends(get_current_user_basic)]) -> list[dict]:
    del user
    from app import db

    with db.cursor() as cur:
        cur.execute("SELECT id, username, role FROM users ORDER BY username")
        return [dict(r) for r in cur.fetchall()]
