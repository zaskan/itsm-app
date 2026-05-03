"""REST API v1."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status

from app import schemas
from app.auth_deps import (
    get_current_user_basic,
    get_current_user_basic_admin,
)
from app.services import asset_types as at_svc
from app.services import incidents as inc_svc
from app.services import inventory as inv_svc
from app.services import kb as kb_svc
from app.services import settings as settings_svc
from app.services import users_admin as usr_svc
from app.services import webhooks as wh_svc

router = APIRouter(prefix="/api/v1", tags=["api-v1"])


@router.get("/settings/app", response_model=schemas.AppSettings)
def api_get_app_settings(
    _user: Annotated[dict, Depends(get_current_user_basic)],
) -> dict:
    return {"app_title": settings_svc.get_app_title()}


@router.put("/settings/app", response_model=schemas.AppSettings)
def api_put_app_settings(
    user: Annotated[dict, Depends(get_current_user_basic_admin)],
    body: schemas.AppSettings,
) -> dict:
    del user
    settings_svc.set_app_title(body.app_title)
    return {"app_title": settings_svc.get_app_title()}


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
            inventory_asset_id=body.inventory_asset_id,
        )
    except Exception as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    wh_svc.schedule_incident_webhook(
        background_tasks,
        "created",
        user["username"],
        snap,
    )
    row = {k: v for k, v in snap.items() if k not in ("comments", "linked_asset")}
    return row


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


@router.patch("/incidents/{incident_ref}", response_model=dict)
def api_patch_incident(
    background_tasks: BackgroundTasks,
    user: Annotated[dict, Depends(get_current_user_basic)],
    incident_ref: str,
    body: schemas.IncidentPatch,
) -> dict:
    patch = body.model_dump(exclude_unset=True)
    if not patch:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No fields to update")
    try:
        if "severity" in patch:
            inc_svc.update_severity(
                incident_ref,
                patch["severity"],
                user["id"],
            )
        if "inventory_asset_id" in patch:
            inc_svc.update_incident_links(
                incident_ref,
                inventory_asset_id=patch["inventory_asset_id"],
                actor_user_id=user["id"],
                clear_asset=patch["inventory_asset_id"] is None,
            )
        snap_full = inc_svc.get_incident_detail(incident_ref)
        if not snap_full:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e

    if "severity" in patch:
        wh_svc.schedule_incident_webhook(
            background_tasks,
            "severity_changed",
            user["username"],
            snap_full,
        )
    if "inventory_asset_id" in patch:
        wh_svc.schedule_incident_webhook(
            background_tasks,
            "asset_linked",
            user["username"],
            snap_full,
        )
    return snap_full


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
    user: Annotated[dict, Depends(get_current_user_basic_admin)],
    body: schemas.WebhookSettings,
) -> dict:
    del user
    wh_svc.set_webhook_url(body.webhook_url)
    return {"webhook_url": wh_svc.get_webhook_url()}


@router.get("/users", response_model=list[schemas.UserOut])
def api_list_users(user: Annotated[dict, Depends(get_current_user_basic_admin)]) -> list[dict]:
    del user
    return usr_svc.list_users()


@router.post("/users", response_model=schemas.UserOut, status_code=status.HTTP_201_CREATED)
def api_create_user(
    user: Annotated[dict, Depends(get_current_user_basic_admin)],
    body: schemas.UserCreate,
) -> dict:
    del user
    try:
        return usr_svc.create_user(body.username, body.password, body.role)
    except Exception as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e


@router.patch("/users/{user_id}", response_model=schemas.UserOut)
def api_update_user(
    admin: Annotated[dict, Depends(get_current_user_basic_admin)],
    user_id: int,
    body: schemas.UserUpdate,
) -> dict:
    if user_id == admin["id"] and body.role == "user":
        admins = usr_svc.count_admins()
        if admins <= 1:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "Cannot demote the only administrator",
            )
    try:
        u = usr_svc.update_user(
            user_id,
            username=body.username,
            role=body.role,
            password=body.password,
        )
    except Exception as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    if not u:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    return u


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def api_delete_user(
    admin: Annotated[dict, Depends(get_current_user_basic_admin)],
    user_id: int,
) -> None:
    if user_id == admin["id"]:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Delete your account using another administrator",
        )
    target = usr_svc.get_user(user_id)
    if not target:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    if target["role"] == "admin" and usr_svc.count_admins() <= 1:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Cannot delete the only administrator",
        )
    if not usr_svc.delete_user(user_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")


@router.get("/asset-types", response_model=list[schemas.AssetTypeOut])
def api_asset_types_list(user: Annotated[dict, Depends(get_current_user_basic)]) -> list[dict]:
    del user
    return at_svc.list_types()


@router.post("/asset-types", response_model=schemas.AssetTypeOut, status_code=status.HTTP_201_CREATED)
def api_asset_types_create(
    user: Annotated[dict, Depends(get_current_user_basic_admin)],
    body: schemas.AssetTypeCreate,
) -> dict:
    del user
    try:
        return at_svc.create_type(body.name, body.description)
    except Exception as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e


@router.patch("/asset-types/{type_id}", response_model=schemas.AssetTypeOut)
def api_asset_types_patch(
    user: Annotated[dict, Depends(get_current_user_basic_admin)],
    type_id: int,
    body: schemas.AssetTypeUpdate,
) -> dict:
    del user
    t = at_svc.update_type(type_id, body.name, body.description)
    if not t:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    return t


@router.delete("/asset-types/{type_id}", status_code=status.HTTP_204_NO_CONTENT)
def api_asset_types_delete(
    user: Annotated[dict, Depends(get_current_user_basic_admin)],
    type_id: int,
) -> None:
    del user
    try:
        if not at_svc.delete_type(type_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    except Exception as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e


@router.get("/inventory", response_model=list[schemas.InventoryOut])
def api_inventory_list(
    user: Annotated[dict, Depends(get_current_user_basic)],
    q: str | None = None,
) -> list[dict]:
    del user
    return inv_svc.list_inventory(q=q)


@router.post("/inventory", response_model=schemas.InventoryOut, status_code=status.HTTP_201_CREATED)
def api_inventory_create(
    user: Annotated[dict, Depends(get_current_user_basic)],
    body: schemas.InventoryCreate,
) -> dict:
    del user
    try:
        row = inv_svc.create_item(
            body.asset_type_id,
            body.hostname,
            body.ip_address,
            body.group_name,
        )
        if not row:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Create failed")
        return row
    except Exception as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e


@router.get("/inventory/{item_id}", response_model=schemas.InventoryOut)
def api_inventory_get(
    user: Annotated[dict, Depends(get_current_user_basic)],
    item_id: int,
) -> dict:
    del user
    row = inv_svc.get_item(item_id)
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    return row


@router.patch("/inventory/{item_id}", response_model=schemas.InventoryOut)
def api_inventory_patch(
    user: Annotated[dict, Depends(get_current_user_basic)],
    item_id: int,
    body: schemas.InventoryUpdate,
) -> dict:
    del user
    row = inv_svc.update_item(
        item_id,
        asset_type_id=body.asset_type_id,
        hostname=body.hostname,
        ip_address=body.ip_address,
        group_name=body.group_name,
    )
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    return row


@router.delete("/inventory/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def api_inventory_delete(
    user: Annotated[dict, Depends(get_current_user_basic)],
    item_id: int,
) -> None:
    del user
    try:
        if not inv_svc.delete_item(item_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    except HTTPException:
        raise
    except Exception as err:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Cannot delete: incident or other reference still uses this asset",
        ) from err
