"""REST API v1."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Body, Depends, File, HTTPException, Query, UploadFile, status

from app import schemas
from app.auth_deps import (
    get_current_user_basic,
    get_current_user_basic_admin,
)
from app.services import asset_types as at_svc
from app.services import change_templates as ctpl_svc
from app.services import changes as chg_svc
from app.services import custom_fields as cf_svc
from app.services import incidents as inc_svc
from app.services import inventory as inv_svc
from app.services import kb as kb_svc
from app.services import branding as branding_svc
from app.services import data_purge as data_purge_svc
from app.services import request_templates as rtpl_svc
from app.services import service_requests as req_svc
from app.services import settings as settings_svc
from app.services import task_templates as ttpl_svc
from app.services import tasks as task_svc
from app.services import users_admin as usr_svc
from app.services import webhooks as wh_svc
from app.services import workflow as wf_svc
from starlette.responses import Response

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


@router.get("/settings/branding", response_model=schemas.BrandingOut)
def api_get_branding(
    _user: Annotated[dict, Depends(get_current_user_basic_admin)],
) -> dict:
    return branding_svc.branding_api_dict()


@router.patch("/settings/branding", response_model=schemas.BrandingOut)
def api_patch_branding(
    user: Annotated[dict, Depends(get_current_user_basic_admin)],
    body: schemas.BrandingPatch,
) -> dict:
    del user
    try:
        return branding_svc.patch_branding(
            app_title=body.app_title,
            logo_mode=body.logo_mode,
            sidebar_background=body.sidebar_background,
            sidebar_text=body.sidebar_text,
            preset=body.preset,
        )
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e


@router.post("/settings/branding/logo", response_model=schemas.BrandingOut)
async def api_post_branding_logo(
    user: Annotated[dict, Depends(get_current_user_basic_admin)],
    file: UploadFile = File(...),
) -> dict:
    del user
    content = await file.read()
    try:
        return branding_svc.save_uploaded_logo(content, file.content_type or "")
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e


@router.delete("/settings/branding/logo")
def api_delete_branding_logo(
    user: Annotated[dict, Depends(get_current_user_basic_admin)],
) -> Response:
    del user
    branding_svc.set_logo_builtin()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/settings/branding/colors")
def api_delete_branding_colors(
    user: Annotated[dict, Depends(get_current_user_basic_admin)],
) -> Response:
    del user
    branding_svc.reset_sidebar_colors()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/settings/branding")
def api_delete_branding_reset_all(
    user: Annotated[dict, Depends(get_current_user_basic_admin)],
) -> Response:
    del user
    branding_svc.reset_all_branding()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/settings/purge-data", response_model=schemas.PurgeDataOut)
def api_purge_data(
    user: Annotated[dict, Depends(get_current_user_basic_admin)],
    body: schemas.PurgeDataBody,
) -> dict:
    if body.confirm != data_purge_svc.PURGE_CONFIRM_PHRASE:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f'Confirmation phrase must be exactly "{data_purge_svc.PURGE_CONFIRM_PHRASE}"',
        )
    try:
        deleted = data_purge_svc.purge_all_data(actor_admin_id=user["id"])
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    return {"deleted": deleted}


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
    body: schemas.IncidentClose | None = Body(None),
) -> dict:
    kid = body.kb_article_id if body else None
    try:
        snap_full = inc_svc.close_incident(
            incident_ref,
            user["id"],
            resolution_kb_article_id=kid,
        )
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    wh_svc.schedule_incident_webhook(
        background_tasks, "closed", user["username"], snap_full
    )
    return snap_full


@router.delete("/incidents/{incident_ref}", status_code=status.HTTP_204_NO_CONTENT)
def api_delete_incident(
    background_tasks: BackgroundTasks,
    user: Annotated[dict, Depends(get_current_user_basic)],
    incident_ref: str,
) -> Response:
    snap = inc_svc.get_incident_detail(incident_ref)
    if not inc_svc.delete_incident(incident_ref):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    if snap:
        wh_svc.schedule_incident_webhook(
            background_tasks, "deleted", user["username"], snap
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


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
) -> Response:
    del user
    if not kb_svc.delete_article(article_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/settings/webhooks", response_model=list[schemas.WebhookOut])
def api_list_webhooks(user: Annotated[dict, Depends(get_current_user_basic)]) -> list[dict]:
    del user
    return wh_svc.list_webhooks()


@router.post(
    "/settings/webhooks",
    response_model=schemas.WebhookOut,
    status_code=status.HTTP_201_CREATED,
)
def api_create_webhook(
    user: Annotated[dict, Depends(get_current_user_basic_admin)],
    body: schemas.WebhookCreate,
) -> dict:
    del user
    try:
        return wh_svc.create_webhook(body.url, label=body.label, enabled=body.enabled)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e


@router.patch("/settings/webhooks/{webhook_id}", response_model=schemas.WebhookOut)
def api_patch_webhook(
    user: Annotated[dict, Depends(get_current_user_basic_admin)],
    webhook_id: int,
    body: schemas.WebhookPatch,
) -> dict:
    del user
    patch = body.model_dump(exclude_unset=True)
    if not patch:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No fields to update")
    try:
        row = wh_svc.update_webhook(
            webhook_id,
            url=patch.get("url") if "url" in patch else None,
            label=patch.get("label") if "label" in patch else None,
            enabled=patch.get("enabled") if "enabled" in patch else None,
        )
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    return row


@router.delete("/settings/webhooks/{webhook_id}", status_code=status.HTTP_204_NO_CONTENT)
def api_delete_webhook(
    user: Annotated[dict, Depends(get_current_user_basic_admin)],
    webhook_id: int,
) -> Response:
    del user
    if not wh_svc.delete_webhook(webhook_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


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


@router.post(
    "/users/{user_id}/mcp-token/refresh",
    response_model=schemas.UserMcpTokenOut,
)
def api_refresh_user_mcp_token(
    user: Annotated[dict, Depends(get_current_user_basic_admin)],
    user_id: int,
) -> dict:
    del user
    out = usr_svc.regenerate_mcp_token(user_id)
    if not out:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    return out


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
) -> Response:
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
    return Response(status_code=status.HTTP_204_NO_CONTENT)


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
) -> Response:
    del user
    try:
        if not at_svc.delete_type(type_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    except Exception as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/asset-types/{type_id}/fields", response_model=list[schemas.CustomFieldDefinitionOut])
def api_asset_type_fields_list(
    user: Annotated[dict, Depends(get_current_user_basic)],
    type_id: int,
) -> list[dict]:
    del user
    if not at_svc.get_type(type_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    return cf_svc.list_definitions("asset_type", type_id)


@router.post(
    "/asset-types/{type_id}/fields",
    response_model=schemas.CustomFieldDefinitionOut,
    status_code=status.HTTP_201_CREATED,
)
def api_asset_type_fields_create(
    user: Annotated[dict, Depends(get_current_user_basic_admin)],
    type_id: int,
    body: schemas.CustomFieldDefinitionCreate,
) -> dict:
    del user
    if not at_svc.get_type(type_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    try:
        return cf_svc.create_definition(
            scope_type="asset_type",
            scope_id=type_id,
            field_key=body.field_key,
            label=body.label,
            field_type=body.field_type,
            required=body.required,
            options=body.options,
            sort_order=body.sort_order,
        )
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e


@router.patch("/asset-types/{type_id}/fields/{field_id}", response_model=schemas.CustomFieldDefinitionOut)
def api_asset_type_fields_patch(
    user: Annotated[dict, Depends(get_current_user_basic_admin)],
    type_id: int,
    field_id: int,
    body: schemas.CustomFieldDefinitionUpdate,
) -> dict:
    del user
    existing = cf_svc.get_definition(field_id)
    if not existing or existing["scope_type"] != "asset_type" or existing["scope_id"] != type_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    try:
        row = cf_svc.update_definition(
            field_id,
            field_key=body.field_key,
            label=body.label,
            field_type=body.field_type,
            required=body.required,
            options=body.options,
            sort_order=body.sort_order,
        )
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    assert row is not None
    return row


@router.delete("/asset-types/{type_id}/fields/{field_id}", status_code=status.HTTP_204_NO_CONTENT)
def api_asset_type_fields_delete(
    user: Annotated[dict, Depends(get_current_user_basic_admin)],
    type_id: int,
    field_id: int,
) -> Response:
    del user
    existing = cf_svc.get_definition(field_id)
    if not existing or existing["scope_type"] != "asset_type" or existing["scope_id"] != type_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    cf_svc.delete_definition(field_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/assets", response_model=list[schemas.InventoryOut])
def api_assets_list(
    user: Annotated[dict, Depends(get_current_user_basic)],
    q: str | None = None,
    external_only: bool = False,
) -> list[dict]:
    del user
    return inv_svc.list_inventory(q=q, external_only=external_only)


@router.post("/assets", response_model=schemas.InventoryOut, status_code=status.HTTP_201_CREATED)
def api_assets_create(
    user: Annotated[dict, Depends(get_current_user_basic)],
    body: schemas.InventoryCreate,
) -> dict:
    del user
    try:
        row = inv_svc.create_item(
            body.name,
            body.description,
            asset_type_id=body.asset_type_id,
            parent_asset_id=body.parent_asset_id,
            assigned_user_id=body.assigned_user_id,
            external_inventory=body.external_inventory,
            custom_fields=body.custom_fields,
        )
        if not row:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Create failed")
        return row
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e


@router.get("/assets/{item_id}", response_model=schemas.InventoryOut)
def api_assets_get(
    user: Annotated[dict, Depends(get_current_user_basic)],
    item_id: int,
) -> dict:
    del user
    row = inv_svc.get_item(item_id)
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    return row


@router.patch("/assets/{item_id}", response_model=schemas.InventoryOut)
def api_assets_patch(
    user: Annotated[dict, Depends(get_current_user_basic)],
    item_id: int,
    body: schemas.InventoryUpdate,
) -> dict:
    del user
    try:
        row = inv_svc.update_item(
            item_id,
            name=body.name,
            description=body.description,
            asset_type_id=body.asset_type_id,
            parent_asset_id=body.parent_asset_id,
            clear_parent=body.clear_parent,
            assigned_user_id=body.assigned_user_id,
            clear_assigned_user=body.clear_assigned_user,
            external_inventory=body.external_inventory,
            custom_fields=body.custom_fields,
        )
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    return row


@router.delete("/assets/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def api_assets_delete(
    user: Annotated[dict, Depends(get_current_user_basic)],
    item_id: int,
) -> Response:
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
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Request templates (admin) ---


@router.get("/request-templates", response_model=list[schemas.RequestTemplateOut])
def api_request_templates_list(
    user: Annotated[dict, Depends(get_current_user_basic)],
) -> list[dict]:
    del user
    return rtpl_svc.list_request_templates()


@router.post("/request-templates", response_model=schemas.RequestTemplateOut, status_code=status.HTTP_201_CREATED)
def api_request_templates_create(
    user: Annotated[dict, Depends(get_current_user_basic_admin)],
    body: schemas.RequestTemplateCreate,
) -> dict:
    del user
    try:
        return rtpl_svc.create_request_template(
            name=body.name,
            description=body.description,
            change_template_id=body.change_template_id,
            require_standard_change=body.require_standard_change,
        )
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e


@router.get("/request-templates/{template_ref}", response_model=schemas.RequestTemplateOut)
def api_request_templates_get(
    user: Annotated[dict, Depends(get_current_user_basic)],
    template_ref: str,
) -> dict:
    del user
    item = rtpl_svc.resolve_request_template(template_ref)
    if not item:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    return item


@router.patch("/request-templates/{template_ref}", response_model=schemas.RequestTemplateOut)
def api_request_templates_patch(
    user: Annotated[dict, Depends(get_current_user_basic_admin)],
    template_ref: str,
    body: schemas.RequestTemplateUpdate,
) -> dict:
    del user
    existing = rtpl_svc.resolve_request_template(template_ref)
    if not existing:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    try:
        item = rtpl_svc.update_request_template(
            existing["id"],
            name=body.name,
            description=body.description,
            change_template_id=body.change_template_id,
            require_standard_change=body.require_standard_change,
            clear_change_template=body.clear_change_template,
        )
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    if not item:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    return item


@router.delete("/request-templates/{template_ref}", status_code=status.HTTP_204_NO_CONTENT)
def api_request_templates_delete(
    user: Annotated[dict, Depends(get_current_user_basic_admin)],
    template_ref: str,
) -> Response:
    del user
    existing = rtpl_svc.resolve_request_template(template_ref)
    if not existing:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    if not rtpl_svc.delete_request_template(existing["id"]):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/request-templates/{template_id}/fields", response_model=list[schemas.CustomFieldDefinitionOut])
def api_request_template_fields_list(
    user: Annotated[dict, Depends(get_current_user_basic)],
    template_id: int,
) -> list[dict]:
    del user
    if not rtpl_svc.get_request_template(template_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    return cf_svc.list_definitions("request_template", template_id)


@router.post(
    "/request-templates/{template_id}/fields",
    response_model=schemas.CustomFieldDefinitionOut,
    status_code=status.HTTP_201_CREATED,
)
def api_request_template_fields_create(
    user: Annotated[dict, Depends(get_current_user_basic_admin)],
    template_id: int,
    body: schemas.CustomFieldDefinitionCreate,
) -> dict:
    del user
    if not rtpl_svc.get_request_template(template_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    try:
        return cf_svc.create_definition(
            scope_type="request_template",
            scope_id=template_id,
            field_key=body.field_key,
            label=body.label,
            field_type=body.field_type,
            required=body.required,
            options=body.options,
            sort_order=body.sort_order,
        )
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e


@router.patch(
    "/request-templates/{template_id}/fields/{field_id}",
    response_model=schemas.CustomFieldDefinitionOut,
)
def api_request_template_fields_patch(
    user: Annotated[dict, Depends(get_current_user_basic_admin)],
    template_id: int,
    field_id: int,
    body: schemas.CustomFieldDefinitionUpdate,
) -> dict:
    del user
    existing = cf_svc.get_definition(field_id)
    if not existing or existing["scope_type"] != "request_template" or existing["scope_id"] != template_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    try:
        row = cf_svc.update_definition(
            field_id,
            field_key=body.field_key,
            label=body.label,
            field_type=body.field_type,
            required=body.required,
            options=body.options,
            sort_order=body.sort_order,
        )
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    assert row is not None
    return row


@router.delete("/request-templates/{template_id}/fields/{field_id}", status_code=status.HTTP_204_NO_CONTENT)
def api_request_template_fields_delete(
    user: Annotated[dict, Depends(get_current_user_basic_admin)],
    template_id: int,
    field_id: int,
) -> Response:
    del user
    existing = cf_svc.get_definition(field_id)
    if not existing or existing["scope_type"] != "request_template" or existing["scope_id"] != template_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    cf_svc.delete_definition(field_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Change templates (admin) ---


@router.get("/change-templates", response_model=list[schemas.ChangeTemplateOut])
def api_change_templates_list(
    user: Annotated[dict, Depends(get_current_user_basic)],
) -> list[dict]:
    del user
    return ctpl_svc.list_change_templates()


@router.post("/change-templates", response_model=schemas.ChangeTemplateOut, status_code=status.HTTP_201_CREATED)
def api_change_templates_create(
    user: Annotated[dict, Depends(get_current_user_basic_admin)],
    body: schemas.ChangeTemplateCreate,
) -> dict:
    del user
    try:
        return ctpl_svc.create_change_template(
            name=body.name,
            description=body.description,
            change_type=body.change_type,
            task_template_ids=body.task_template_ids,
        )
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e


@router.get("/change-templates/{template_id}", response_model=schemas.ChangeTemplateOut)
def api_change_templates_get(
    user: Annotated[dict, Depends(get_current_user_basic)],
    template_id: int,
) -> dict:
    del user
    item = ctpl_svc.get_change_template(template_id)
    if not item:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    return item


@router.patch("/change-templates/{template_id}", response_model=schemas.ChangeTemplateOut)
def api_change_templates_patch(
    user: Annotated[dict, Depends(get_current_user_basic_admin)],
    template_id: int,
    body: schemas.ChangeTemplateUpdate,
) -> dict:
    del user
    try:
        item = ctpl_svc.update_change_template(
            template_id,
            name=body.name,
            description=body.description,
            change_type=body.change_type,
            task_template_ids=body.task_template_ids,
        )
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    if not item:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    return item


@router.delete("/change-templates/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
def api_change_templates_delete(
    user: Annotated[dict, Depends(get_current_user_basic_admin)],
    template_id: int,
) -> Response:
    del user
    try:
        if not ctpl_svc.delete_change_template(template_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/change-templates/{template_id}/fields", response_model=list[schemas.CustomFieldDefinitionOut])
def api_change_template_fields_list(
    user: Annotated[dict, Depends(get_current_user_basic)],
    template_id: int,
) -> list[dict]:
    del user
    if not ctpl_svc.get_change_template(template_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    return cf_svc.list_definitions("change_template", template_id)


@router.post(
    "/change-templates/{template_id}/fields",
    response_model=schemas.CustomFieldDefinitionOut,
    status_code=status.HTTP_201_CREATED,
)
def api_change_template_fields_create(
    user: Annotated[dict, Depends(get_current_user_basic_admin)],
    template_id: int,
    body: schemas.CustomFieldDefinitionCreate,
) -> dict:
    del user
    if not ctpl_svc.get_change_template(template_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    try:
        return cf_svc.create_definition(
            scope_type="change_template",
            scope_id=template_id,
            field_key=body.field_key,
            label=body.label,
            field_type=body.field_type,
            required=body.required,
            options=body.options,
            sort_order=body.sort_order,
        )
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e


@router.patch(
    "/change-templates/{template_id}/fields/{field_id}",
    response_model=schemas.CustomFieldDefinitionOut,
)
def api_change_template_fields_patch(
    user: Annotated[dict, Depends(get_current_user_basic_admin)],
    template_id: int,
    field_id: int,
    body: schemas.CustomFieldDefinitionUpdate,
) -> dict:
    del user
    existing = cf_svc.get_definition(field_id)
    if not existing or existing["scope_type"] != "change_template" or existing["scope_id"] != template_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    try:
        row = cf_svc.update_definition(
            field_id,
            field_key=body.field_key,
            label=body.label,
            field_type=body.field_type,
            required=body.required,
            options=body.options,
            sort_order=body.sort_order,
        )
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    assert row is not None
    return row


@router.delete("/change-templates/{template_id}/fields/{field_id}", status_code=status.HTTP_204_NO_CONTENT)
def api_change_template_fields_delete(
    user: Annotated[dict, Depends(get_current_user_basic_admin)],
    template_id: int,
    field_id: int,
) -> Response:
    del user
    existing = cf_svc.get_definition(field_id)
    if not existing or existing["scope_type"] != "change_template" or existing["scope_id"] != template_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    cf_svc.delete_definition(field_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Task templates (admin) ---


@router.get("/task-templates", response_model=list[schemas.TaskTemplateOut])
def api_task_templates_list(
    user: Annotated[dict, Depends(get_current_user_basic)],
) -> list[dict]:
    del user
    return ttpl_svc.list_task_templates()


@router.post("/task-templates", response_model=schemas.TaskTemplateOut, status_code=status.HTTP_201_CREATED)
def api_task_templates_create(
    user: Annotated[dict, Depends(get_current_user_basic_admin)],
    body: schemas.TaskTemplateCreate,
) -> dict:
    del user
    try:
        return ttpl_svc.create_task_template(
            name=body.name,
            title=body.title,
            description=body.description,
            assigned_user_id=body.assigned_user_id,
            kb_article_id=body.kb_article_id,
        )
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e


@router.get("/task-templates/{template_id}", response_model=schemas.TaskTemplateOut)
def api_task_templates_get(
    user: Annotated[dict, Depends(get_current_user_basic)],
    template_id: int,
) -> dict:
    del user
    item = ttpl_svc.get_task_template(template_id)
    if not item:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    return item


@router.patch("/task-templates/{template_id}", response_model=schemas.TaskTemplateOut)
def api_task_templates_patch(
    user: Annotated[dict, Depends(get_current_user_basic_admin)],
    template_id: int,
    body: schemas.TaskTemplateUpdate,
) -> dict:
    del user
    try:
        item = ttpl_svc.update_task_template(
            template_id,
            name=body.name,
            title=body.title,
            description=body.description,
            assigned_user_id=body.assigned_user_id,
            clear_assigned_user=body.clear_assigned_user,
            kb_article_id=body.kb_article_id,
            clear_kb=body.clear_kb,
        )
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    if not item:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    return item


@router.delete("/task-templates/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
def api_task_templates_delete(
    user: Annotated[dict, Depends(get_current_user_basic_admin)],
    template_id: int,
) -> Response:
    del user
    if not ttpl_svc.delete_task_template(template_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/task-templates/{template_id}/fields", response_model=list[schemas.CustomFieldDefinitionOut])
def api_task_template_fields_list(
    user: Annotated[dict, Depends(get_current_user_basic)],
    template_id: int,
) -> list[dict]:
    del user
    if not ttpl_svc.get_task_template(template_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    return cf_svc.list_definitions("task_template", template_id)


@router.post(
    "/task-templates/{template_id}/fields",
    response_model=schemas.CustomFieldDefinitionOut,
    status_code=status.HTTP_201_CREATED,
)
def api_task_template_fields_create(
    user: Annotated[dict, Depends(get_current_user_basic_admin)],
    template_id: int,
    body: schemas.CustomFieldDefinitionCreate,
) -> dict:
    del user
    if not ttpl_svc.get_task_template(template_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    try:
        return cf_svc.create_definition(
            scope_type="task_template",
            scope_id=template_id,
            field_key=body.field_key,
            label=body.label,
            field_type=body.field_type,
            required=body.required,
            options=body.options,
            sort_order=body.sort_order,
        )
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e


@router.patch(
    "/task-templates/{template_id}/fields/{field_id}",
    response_model=schemas.CustomFieldDefinitionOut,
)
def api_task_template_fields_patch(
    user: Annotated[dict, Depends(get_current_user_basic_admin)],
    template_id: int,
    field_id: int,
    body: schemas.CustomFieldDefinitionUpdate,
) -> dict:
    del user
    existing = cf_svc.get_definition(field_id)
    if not existing or existing["scope_type"] != "task_template" or existing["scope_id"] != template_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    try:
        row = cf_svc.update_definition(
            field_id,
            field_key=body.field_key,
            label=body.label,
            field_type=body.field_type,
            required=body.required,
            options=body.options,
            sort_order=body.sort_order,
        )
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    assert row is not None
    return row


@router.delete("/task-templates/{template_id}/fields/{field_id}", status_code=status.HTTP_204_NO_CONTENT)
def api_task_template_fields_delete(
    user: Annotated[dict, Depends(get_current_user_basic_admin)],
    template_id: int,
    field_id: int,
) -> Response:
    del user
    existing = cf_svc.get_definition(field_id)
    if not existing or existing["scope_type"] != "task_template" or existing["scope_id"] != template_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    cf_svc.delete_definition(field_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Service requests ---


@router.get("/requests", response_model=list[schemas.RequestOut])
def api_requests_list(
    user: Annotated[dict, Depends(get_current_user_basic)],
    status: str | None = None,
    open_only: bool = False,
    q: str | None = None,
) -> list[dict]:
    del user
    return req_svc.list_requests(status=status, open_only=open_only, q=q)


@router.post("/requests", response_model=dict, status_code=status.HTTP_201_CREATED)
def api_requests_create(
    background_tasks: BackgroundTasks,
    user: Annotated[dict, Depends(get_current_user_basic)],
    body: schemas.RequestCreate,
) -> dict:
    try:
        snap = req_svc.create_request(
            requester_user_id=user["id"],
            name=body.name,
            description=body.description,
            request_template_id=body.request_template_id,
            specifications=body.specifications,
        )
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    wh_svc.schedule_workflow_webhook(background_tasks, "request", "created", user["username"], snap)
    return snap


@router.get("/requests/{request_ref}")
def api_request_get(
    user: Annotated[dict, Depends(get_current_user_basic)],
    request_ref: str,
) -> dict:
    del user
    d = req_svc.get_request_detail(request_ref)
    if not d:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    return d


@router.patch("/requests/{request_ref}")
def api_request_patch(
    user: Annotated[dict, Depends(get_current_user_basic)],
    request_ref: str,
    body: schemas.RequestPatch,
) -> dict:
    del user
    try:
        d = req_svc.patch_request(
            request_ref,
            name=body.name,
            description=body.description,
        )
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    if not d:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    return d


@router.delete("/requests/{request_ref}", status_code=status.HTTP_204_NO_CONTENT)
def api_request_delete(
    user: Annotated[dict, Depends(get_current_user_basic)],
    request_ref: str,
) -> Response:
    del user
    try:
        if not req_svc.delete_request(request_ref):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/requests/{request_ref}/submit")
def api_request_submit(
    background_tasks: BackgroundTasks,
    user: Annotated[dict, Depends(get_current_user_basic)],
    request_ref: str,
) -> dict:
    try:
        snap = wf_svc.submit_request(request_ref, user["id"])
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    wh_svc.schedule_workflow_webhook(background_tasks, "request", "submitted", user["username"], snap)
    for chg in snap.get("changes_created", []):
        wh_svc.schedule_workflow_webhook(
            background_tasks, "change", "created", user["username"], chg
        )
        if chg.get("status") == "implementing":
            wh_svc.schedule_workflow_webhook(
                background_tasks, "change", "approved", user["username"], chg
            )
        elif chg.get("status") == "pending_approval":
            wh_svc.schedule_workflow_webhook(
                background_tasks, "change", "pending_approval", user["username"], chg
            )
    return snap


@router.post("/requests/{request_ref}/cancel")
def api_request_cancel(
    background_tasks: BackgroundTasks,
    user: Annotated[dict, Depends(get_current_user_basic)],
    request_ref: str,
) -> dict:
    try:
        snap = req_svc.cancel_request(request_ref, user["id"])
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    wh_svc.schedule_workflow_webhook(background_tasks, "request", "cancelled", user["username"], snap)
    return snap


@router.post("/requests/{request_ref}/comments")
def api_request_comment(
    background_tasks: BackgroundTasks,
    user: Annotated[dict, Depends(get_current_user_basic)],
    request_ref: str,
    body: schemas.RequestCommentBody,
) -> dict:
    try:
        snap = req_svc.add_request_comment(request_ref, body.body, user["id"])
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    wh_svc.schedule_workflow_webhook(
        background_tasks, "request", "comment_added", user["username"], snap
    )
    return snap


@router.post("/requests/{request_ref}/resolution-kb")
def api_request_resolution_kb(
    background_tasks: BackgroundTasks,
    user: Annotated[dict, Depends(get_current_user_basic)],
    request_ref: str,
    body: schemas.RequestKbBody,
) -> dict:
    try:
        snap = req_svc.set_request_resolution_kb(request_ref, body.kb_article_id, user["id"])
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    wh_svc.schedule_workflow_webhook(
        background_tasks, "request", "kb_assigned", user["username"], snap
    )
    return snap


@router.post("/requests/{request_ref}/close")
def api_request_close(
    background_tasks: BackgroundTasks,
    user: Annotated[dict, Depends(get_current_user_basic)],
    request_ref: str,
    body: schemas.RequestCloseBody | None = None,
) -> dict:
    kid = body.kb_article_id if body else None
    try:
        snap = req_svc.close_request(
            request_ref, user["id"], resolution_kb_article_id=kid
        )
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    wh_svc.schedule_workflow_webhook(
        background_tasks, "request", "closed", user["username"], snap
    )
    return snap


@router.get("/requests/{request_ref}/ritms", response_model=list[schemas.RitmOut])
def api_request_ritms_list(
    user: Annotated[dict, Depends(get_current_user_basic)],
    request_ref: str,
) -> list[dict]:
    del user
    if not req_svc.get_request_detail(request_ref):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    return req_svc.list_ritms_for_request(request_ref)


@router.post("/requests/{request_ref}/ritms", response_model=schemas.RitmOut, status_code=status.HTTP_201_CREATED)
def api_request_ritm_create(
    user: Annotated[dict, Depends(get_current_user_basic)],
    request_ref: str,
    body: schemas.RitmCreate,
) -> dict:
    try:
        return req_svc.add_ritm_to_request(
            request_ref,
            request_template_id=body.request_template_id or body.catalog_item_id,
            item_type=body.item_type,
            specifications=body.specifications,
            actor_user_id=user["id"],
        )
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e


@router.get("/ritms/{ritm_ref}", response_model=schemas.RitmOut)
def api_ritm_get(
    user: Annotated[dict, Depends(get_current_user_basic)],
    ritm_ref: str,
) -> dict:
    del user
    d = req_svc.get_ritm_detail(ritm_ref)
    if not d:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    return d


@router.patch("/ritms/{ritm_ref}", response_model=schemas.RitmOut)
def api_ritm_patch(
    user: Annotated[dict, Depends(get_current_user_basic)],
    ritm_ref: str,
    body: schemas.RitmPatch,
) -> dict:
    del user
    try:
        d = req_svc.patch_ritm(
            ritm_ref,
            item_type=body.item_type,
            specifications=body.specifications,
        )
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    if not d:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    return d


# --- Changes ---


@router.get("/changes", response_model=list[schemas.ChangeOut])
def api_changes_list(
    user: Annotated[dict, Depends(get_current_user_basic)],
    status: str | None = None,
    open_only: bool = False,
    q: str | None = None,
) -> list[dict]:
    del user
    return chg_svc.list_changes(status=status, open_only=open_only, q=q)


@router.post("/changes", status_code=status.HTTP_201_CREATED)
def api_changes_create(
    background_tasks: BackgroundTasks,
    user: Annotated[dict, Depends(get_current_user_basic)],
    body: schemas.ChangeCreate,
) -> dict:
    try:
        snap = chg_svc.create_change_from_template(
            change_template_id=body.change_template_id,
            custom_fields=body.custom_fields,
            actor_user_id=user["id"],
        )
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    wh_svc.schedule_workflow_webhook(background_tasks, "change", "created", user["username"], snap)
    if snap.get("change_type") == "standard":
        snap = chg_svc.auto_approve_change(snap["id"], user["id"])
        wh_svc.schedule_workflow_webhook(background_tasks, "change", "approved", user["username"], snap)
    else:
        snap = chg_svc.set_change_pending_approval(snap["id"], user["id"])
        wh_svc.schedule_workflow_webhook(
            background_tasks, "change", "pending_approval", user["username"], snap
        )
    return snap


@router.get("/changes/{change_ref}")
def api_change_get(
    user: Annotated[dict, Depends(get_current_user_basic)],
    change_ref: str,
) -> dict:
    del user
    d = chg_svc.get_change_detail(change_ref)
    if not d:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    return d


@router.patch("/changes/{change_ref}")
def api_change_patch(
    user: Annotated[dict, Depends(get_current_user_basic)],
    change_ref: str,
    body: schemas.ChangePatch,
) -> dict:
    del user
    try:
        d = chg_svc.patch_change(
            change_ref,
            risk_assessment=body.risk_assessment,
            implementation_plan=body.implementation_plan,
            test_plan=body.test_plan,
            backout_plan=body.backout_plan,
        )
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    if not d:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    return d


@router.post("/changes/{change_ref}/approve")
def api_change_approve(
    background_tasks: BackgroundTasks,
    user: Annotated[dict, Depends(get_current_user_basic_admin)],
    change_ref: str,
) -> dict:
    try:
        snap = chg_svc.approve_change(change_ref, user["id"])
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    wh_svc.schedule_workflow_webhook(background_tasks, "change", "approved", user["username"], snap)
    return snap


@router.post("/changes/{change_ref}/cancel")
def api_change_cancel(
    background_tasks: BackgroundTasks,
    user: Annotated[dict, Depends(get_current_user_basic)],
    change_ref: str,
) -> dict:
    try:
        snap = chg_svc.cancel_change(change_ref, user["id"])
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    wh_svc.schedule_workflow_webhook(background_tasks, "change", "cancelled", user["username"], snap)
    return snap


@router.post("/changes/{change_ref}/tasks/{ctask_ref}/start")
def api_ctask_start(
    user: Annotated[dict, Depends(get_current_user_basic)],
    change_ref: str,
    ctask_ref: str,
) -> dict:
    try:
        return chg_svc.start_ctask(change_ref, ctask_ref, user["id"])
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e


@router.post("/changes/{change_ref}/tasks/{ctask_ref}/complete")
def api_ctask_complete(
    background_tasks: BackgroundTasks,
    user: Annotated[dict, Depends(get_current_user_basic)],
    change_ref: str,
    ctask_ref: str,
    body: schemas.TaskCompleteBody | None = None,
) -> dict:
    comment = body.comment if body else ""
    try:
        snap = wf_svc.on_ctask_completed(
            change_ref, ctask_ref, user["id"], completion_comment=comment
        )
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    wh_svc.schedule_workflow_webhook(
        background_tasks, "change", "ctask_completed", user["username"], snap
    )
    if snap.get("status") == "completed":
        wh_svc.schedule_workflow_webhook(
            background_tasks, "change", "completed", user["username"], snap
        )
    return snap


# --- Tasks ---


@router.get("/tasks", response_model=list[schemas.CtaskOut])
def api_tasks_list(
    user: Annotated[dict, Depends(get_current_user_basic)],
    status: str | None = None,
    open_only: bool = False,
    q: str | None = None,
) -> list[dict]:
    del user
    return task_svc.list_tasks(status=status, open_only=open_only, q=q)


@router.get("/tasks/{task_ref}", response_model=schemas.CtaskOut)
def api_task_get(
    user: Annotated[dict, Depends(get_current_user_basic)],
    task_ref: str,
) -> dict:
    del user
    d = task_svc.get_task_detail(task_ref)
    if not d:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    return d


@router.post("/tasks", response_model=schemas.CtaskOut, status_code=status.HTTP_201_CREATED)
def api_tasks_create(
    user: Annotated[dict, Depends(get_current_user_basic)],
    body: schemas.TaskCreate,
) -> dict:
    try:
        return task_svc.create_task(
            change_ref=body.change_ref,
            title=body.title,
            description=body.description,
            assigned_user_id=body.assigned_user_id,
            kb_article_id=body.kb_article_id,
            task_template_id=body.task_template_id,
            custom_fields=body.custom_fields,
            actor_user_id=user["id"],
        )
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e


@router.post("/tasks/{task_ref}/start", response_model=schemas.CtaskOut)
def api_task_start(
    user: Annotated[dict, Depends(get_current_user_basic)],
    task_ref: str,
) -> dict:
    try:
        return task_svc.start_task(task_ref, user["id"])
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e


@router.post("/tasks/{task_ref}/complete", response_model=schemas.CtaskOut)
def api_task_complete(
    user: Annotated[dict, Depends(get_current_user_basic)],
    task_ref: str,
    body: schemas.TaskCompleteBody | None = None,
) -> dict:
    comment = body.comment if body else ""
    try:
        return task_svc.complete_task(task_ref, user["id"], completion_comment=comment)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
