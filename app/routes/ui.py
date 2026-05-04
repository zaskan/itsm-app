"""Server-rendered HTML UI."""

from __future__ import annotations

import os
from datetime import date, datetime, timezone
from typing import Any

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app import db
from app.auth_deps import (
    get_session_user,
    login_redirect,
    require_admin_session,
    verify_password,
)
from app.services import asset_types as at_svc
from app.services import incidents as inc_svc
from app.services import inventory as inv_svc
from app.services import kb as kb_svc
from app.services import branding as branding_svc
from app.services import settings as settings_svc
from app.services import users_admin as usr_svc
from app.services import webhooks as wh_svc

DIR = os.path.join(os.path.dirname(__file__), "..", "templates")
templates = Jinja2Templates(directory=os.path.normpath(DIR))
router = APIRouter(tags=["ui"])


def _page(request: Request, user: dict, **extra: Any) -> dict[str, Any]:
    ctx = {
        "request": request,
        "user": user,
        "app_title": settings_svc.get_app_title(),
        "is_admin": user.get("role") == "admin",
        **branding_svc.template_branding_context(),
        **extra,
    }
    return ctx


def _login_ctx(request: Request) -> dict[str, Any]:
    return {
        "request": request,
        "app_title": settings_svc.get_app_title(),
        "is_admin": False,
        **branding_svc.template_branding_context(),
    }


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request) -> HTMLResponse:
    if get_session_user(request):
        return RedirectResponse("/incidents", status_code=302)
    return templates.TemplateResponse(request, "login.html", _login_ctx(request))


@router.post("/login")
def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
) -> RedirectResponse:
    user = verify_password(username, password)
    if not user:
        return RedirectResponse("/login?error=1", status_code=303)
    request.session["user_id"] = user["id"]
    return RedirectResponse("/incidents", status_code=303)


@router.get("/logout")
def logout(request: Request) -> RedirectResponse:
    request.session.clear()
    return RedirectResponse("/login", status_code=302)


@router.get("/", response_class=HTMLResponse)
def root() -> RedirectResponse:
    return RedirectResponse("/incidents", status_code=302)


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request) -> HTMLResponse:
    me = require_admin_session(request)
    b = branding_svc.get_branding()
    return templates.TemplateResponse(
        request,
        "settings.html",
        _page(
            request,
            me,
            current_title=b["app_title"],
            branding_presets=branding_svc.PRESETS,
        ),
    )


@router.post("/settings/application-title")
def settings_save_title(
    request: Request,
    app_title: str = Form(...),
) -> RedirectResponse:
    require_admin_session(request)
    settings_svc.set_app_title(app_title)
    return RedirectResponse("/settings", status_code=303)


@router.post("/settings/branding/logo")
async def settings_branding_logo(
    request: Request,
    logo_mode: str = Form("builtin"),
    file: UploadFile | None = File(default=None),
) -> RedirectResponse:
    require_admin_session(request)
    try:
        lm = logo_mode.strip().lower()
        if lm == branding_svc.MODE_BUILTIN:
            branding_svc.set_logo_builtin()
        elif file is not None and (file.filename or "").strip():
            content = await file.read()
            branding_svc.save_uploaded_logo(content, file.content_type or "")
        elif lm == branding_svc.MODE_CUSTOM:
            branding_svc.patch_branding(logo_mode="custom")
        else:
            branding_svc.set_logo_builtin()
    except ValueError:
        pass
    return RedirectResponse("/settings", status_code=303)


@router.post("/settings/branding/colors")
def settings_branding_colors(
    request: Request,
    sidebar_background: str = Form(...),
    sidebar_text: str = Form(...),
) -> RedirectResponse:
    require_admin_session(request)
    try:
        branding_svc.patch_branding(sidebar_background=sidebar_background, sidebar_text=sidebar_text)
    except ValueError:
        pass
    return RedirectResponse("/settings", status_code=303)


@router.post("/settings/branding/preset")
def settings_branding_preset(
    request: Request,
    preset: str = Form(...),
) -> RedirectResponse:
    require_admin_session(request)
    try:
        branding_svc.apply_preset(preset)
    except ValueError:
        pass
    return RedirectResponse("/settings", status_code=303)


@router.post("/settings/branding/reset-title-logo")
def settings_branding_reset_title_logo(request: Request) -> RedirectResponse:
    require_admin_session(request)
    branding_svc.reset_title_logo()
    return RedirectResponse("/settings", status_code=303)


@router.post("/settings/branding/reset-colors")
def settings_branding_reset_colors(request: Request) -> RedirectResponse:
    require_admin_session(request)
    branding_svc.reset_sidebar_colors()
    return RedirectResponse("/settings", status_code=303)


@router.get("/incidents", response_class=HTMLResponse)
def incidents_page(
    request: Request,
    q: str | None = None,
    status_filter: str | None = Query(None, alias="status"),
    severity: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> HTMLResponse:
    user = get_session_user(request)
    if not user:
        raise login_redirect()
    rows = inc_svc.list_incidents(
        q=q,
        status=status_filter,
        severity=severity,
        date_from=date_from,
        date_to=date_to,
    )
    today = date.today().isoformat()
    inventory_rows = inv_svc.list_inventory()
    return templates.TemplateResponse(
        request,
        "incidents.html",
        _page(
            request,
            user,
            incidents=rows,
            q=q or "",
            status_filter=status_filter or "",
            severity_filter=severity or "",
            date_from=date_from or "",
            date_to=date_to or "",
            today=today,
            inventory_items=inventory_rows,
        ),
    )


@router.post("/incidents/new")
def incidents_new(
    request: Request,
    background_tasks: BackgroundTasks,
    title: str = Form(...),
    description: str = Form(""),
    severity: str = Form("medium"),
    incident_date: str = Form(...),
    inventory_asset_id: str = Form(""),
) -> RedirectResponse:
    me = get_session_user(request)
    if not me:
        raise login_redirect()
    created_at = None
    if incident_date:
        try:
            d = date.fromisoformat(incident_date)
            created_at = datetime(
                d.year,
                d.month,
                d.day,
                tzinfo=timezone.utc,
            ).isoformat().replace("+00:00", "Z")
        except ValueError:
            pass
    aid: int | None = None
    raw = inventory_asset_id.strip()
    if raw == "":
        pass
    elif raw.isdigit():
        aid = int(raw)
    else:
        raise HTTPException(400, "Invalid inventory asset selection")
    try:
        snap = inc_svc.create_incident(
            title=title.strip(),
            description=description.strip(),
            severity=severity,
            actor_user_id=me["id"],
            created_at=created_at,
            inventory_asset_id=aid,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    wh_svc.schedule_incident_webhook(
        background_tasks, "created", me["username"], snap
    )
    return RedirectResponse("/incidents", status_code=303)


@router.get("/incidents/{incident_ref}", response_class=HTMLResponse)
def incident_detail_fragment(
    request: Request,
    incident_ref: str,
) -> HTMLResponse:
    user = get_session_user(request)
    if not user:
        raise login_redirect()
    d = inc_svc.get_incident_detail(incident_ref)
    if not d:
        raise HTTPException(404, "Not found")
    inventory_rows = inv_svc.list_inventory()
    kb_articles = kb_svc.list_articles()
    return templates.TemplateResponse(
        request,
        "incident_detail_fragment.html",
        _page(
            request,
            user,
            incident=d,
            inventory_items=inventory_rows,
            kb_articles=kb_articles,
        ),
    )


@router.post("/incidents/{incident_ref}/asset")
def incident_asset(
    request: Request,
    background_tasks: BackgroundTasks,
    incident_ref: str,
    inventory_asset_id: str = Form(""),
) -> RedirectResponse:
    me = get_session_user(request)
    if not me:
        raise login_redirect()
    aid: int | None = None
    clear_asset = False
    raw = inventory_asset_id.strip()
    if raw == "":
        clear_asset = True
    elif raw.isdigit():
        aid = int(raw)
    else:
        raise HTTPException(400, "Invalid inventory asset selection")
    try:
        snap = inc_svc.update_incident_links(
            incident_ref,
            inventory_asset_id=aid,
            actor_user_id=me["id"],
            clear_asset=clear_asset,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    wh_svc.schedule_incident_webhook(
        background_tasks, "asset_linked", me["username"], snap
    )
    return RedirectResponse("/incidents", status_code=303)


@router.post("/incidents/{incident_ref}/comment")
def incident_comment(
    request: Request,
    background_tasks: BackgroundTasks,
    incident_ref: str,
    body: str = Form(...),
) -> RedirectResponse:
    me = get_session_user(request)
    if not me:
        raise login_redirect()
    try:
        snap = inc_svc.add_comment(incident_ref, body.strip(), me["id"])
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    wh_svc.schedule_incident_webhook(
        background_tasks, "comment_added", me["username"], snap
    )
    return RedirectResponse("/incidents", status_code=303)


@router.post("/incidents/{incident_ref}/severity")
def incident_severity(
    request: Request,
    background_tasks: BackgroundTasks,
    incident_ref: str,
    severity: str = Form(...),
) -> RedirectResponse:
    me = get_session_user(request)
    if not me:
        raise login_redirect()
    try:
        snap = inc_svc.update_severity(incident_ref, severity, me["id"])
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    wh_svc.schedule_incident_webhook(
        background_tasks, "severity_changed", me["username"], snap
    )
    return RedirectResponse("/incidents", status_code=303)


@router.post("/incidents/{incident_ref}/close")
def incident_close(
    request: Request,
    background_tasks: BackgroundTasks,
    incident_ref: str,
    kb_article_id: str = Form(""),
) -> RedirectResponse:
    me = get_session_user(request)
    if not me:
        raise login_redirect()
    kid: int | None = None
    raw = kb_article_id.strip()
    if raw.isdigit():
        kid = int(raw)
    elif raw != "":
        raise HTTPException(400, "Invalid KB article selection")
    try:
        snap = inc_svc.close_incident(
            incident_ref,
            me["id"],
            resolution_kb_article_id=kid,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    wh_svc.schedule_incident_webhook(
        background_tasks, "closed", me["username"], snap
    )
    return RedirectResponse("/incidents", status_code=303)


@router.get("/kb", response_class=HTMLResponse)
def kb_page(request: Request, q: str | None = None) -> HTMLResponse:
    user = get_session_user(request)
    if not user:
        raise login_redirect()
    articles = kb_svc.list_articles(q=q)
    return templates.TemplateResponse(
        request,
        "kb.html",
        _page(request, user, articles=articles, q=q or ""),
    )


@router.post("/kb/new")
def kb_new(
    request: Request,
    title: str = Form(...),
    description: str = Form(""),
) -> RedirectResponse:
    me = get_session_user(request)
    if not me:
        raise login_redirect()
    kb_svc.create_article(title.strip(), description.strip())
    return RedirectResponse("/kb", status_code=303)


@router.post("/kb/{article_id}/edit")
def kb_edit(
    request: Request,
    article_id: int,
    title: str = Form(...),
    description: str = Form(""),
) -> RedirectResponse:
    me = get_session_user(request)
    if not me:
        raise login_redirect()
    kb_svc.update_article(article_id, title.strip(), description.strip())
    return RedirectResponse("/kb", status_code=303)


@router.post("/kb/{article_id}/delete")
def kb_delete(request: Request, article_id: int) -> RedirectResponse:
    me = get_session_user(request)
    if not me:
        raise login_redirect()
    kb_svc.delete_article(article_id)
    return RedirectResponse("/kb", status_code=303)


@router.get("/webhook-config", response_class=HTMLResponse)
def webhook_page(request: Request) -> HTMLResponse:
    user = require_admin_session(request)
    url = wh_svc.get_webhook_url()
    return templates.TemplateResponse(
        request,
        "webhook.html",
        _page(request, user, webhook_url=url),
    )


@router.post("/webhook-config")
def webhook_save(
    request: Request,
    webhook_url: str = Form(""),
) -> RedirectResponse:
    require_admin_session(request)
    wh_svc.set_webhook_url(webhook_url)
    return RedirectResponse("/webhook-config", status_code=303)


@router.get("/users", response_class=HTMLResponse)
def users_page(request: Request) -> HTMLResponse:
    user = require_admin_session(request)
    users = usr_svc.list_users()
    return templates.TemplateResponse(
        request,
        "users.html",
        _page(request, user, users=users),
    )


@router.post("/users/new")
def users_new(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    role: str = Form("user"),
) -> RedirectResponse:
    require_admin_session(request)
    try:
        usr_svc.create_user(username, password, role)
    except Exception:
        return RedirectResponse("/users?error=1", status_code=303)
    return RedirectResponse("/users", status_code=303)


@router.post("/users/{user_id}/edit")
def users_edit(
    request: Request,
    user_id: int,
    username: str = Form(...),
    role: str = Form(...),
    password: str = Form(""),
) -> RedirectResponse:
    admin = require_admin_session(request)
    if user_id == admin["id"] and role == "user":
        if usr_svc.count_admins() <= 1:
            return RedirectResponse("/users?error=demote", status_code=303)
    try:
        usr_svc.update_user(
            user_id,
            username=username,
            role=role,
            password=password if password.strip() else None,
        )
    except Exception:
        return RedirectResponse("/users?error=update", status_code=303)
    return RedirectResponse("/users", status_code=303)


@router.post("/users/{user_id}/delete")
def users_delete(request: Request, user_id: int) -> RedirectResponse:
    admin = require_admin_session(request)
    if user_id == admin["id"]:
        return RedirectResponse("/users?error=self", status_code=303)
    target = usr_svc.get_user(user_id)
    if not target:
        return RedirectResponse("/users", status_code=303)
    if target["role"] == "admin" and usr_svc.count_admins() <= 1:
        return RedirectResponse("/users?error=lastadmin", status_code=303)
    usr_svc.delete_user(user_id)
    return RedirectResponse("/users", status_code=303)


@router.get("/asset-types", response_class=HTMLResponse)
def asset_types_page(request: Request) -> HTMLResponse:
    user = get_session_user(request)
    if not user:
        raise login_redirect()
    rows = at_svc.list_types()
    return templates.TemplateResponse(
        request,
        "asset_types.html",
        _page(request, user, types=rows),
    )


@router.post("/asset-types/new")
def asset_types_new(
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
) -> RedirectResponse:
    if not get_session_user(request):
        raise login_redirect()
    try:
        at_svc.create_type(name, description)
    except Exception:
        return RedirectResponse("/asset-types?error=1", status_code=303)
    return RedirectResponse("/asset-types", status_code=303)


@router.post("/asset-types/{type_id}/edit")
def asset_types_edit(
    request: Request,
    type_id: int,
    name: str = Form(...),
    description: str = Form(""),
) -> RedirectResponse:
    if not get_session_user(request):
        raise login_redirect()
    at_svc.update_type(type_id, name, description)
    return RedirectResponse("/asset-types", status_code=303)


@router.post("/asset-types/{type_id}/delete")
def asset_types_delete(request: Request, type_id: int) -> RedirectResponse:
    if not get_session_user(request):
        raise login_redirect()
    try:
        at_svc.delete_type(type_id)
    except Exception:
        return RedirectResponse("/asset-types?error=fk", status_code=303)
    return RedirectResponse("/asset-types", status_code=303)


@router.get("/inventory", response_class=HTMLResponse)
def inventory_page(request: Request, q: str | None = None) -> HTMLResponse:
    user = get_session_user(request)
    if not user:
        raise login_redirect()
    rows = inv_svc.list_inventory(q=q)
    types = at_svc.list_types()
    return templates.TemplateResponse(
        request,
        "inventory.html",
        _page(request, user, items=rows, asset_types=types, q=q or ""),
    )


@router.post("/inventory/new")
def inventory_new(
    request: Request,
    asset_type_id: int = Form(...),
    hostname: str = Form(...),
    ip_address: str = Form(""),
    group_name: str = Form(""),
) -> RedirectResponse:
    me = get_session_user(request)
    if not me:
        raise login_redirect()
    try:
        inv_svc.create_item(asset_type_id, hostname, ip_address, group_name)
    except Exception:
        return RedirectResponse("/inventory?error=1", status_code=303)
    return RedirectResponse("/inventory", status_code=303)


@router.post("/inventory/{item_id}/edit")
def inventory_edit(
    request: Request,
    item_id: int,
    asset_type_id: int = Form(...),
    hostname: str = Form(...),
    ip_address: str = Form(""),
    group_name: str = Form(""),
) -> RedirectResponse:
    me = get_session_user(request)
    if not me:
        raise login_redirect()
    inv_svc.update_item(
        item_id,
        asset_type_id=asset_type_id,
        hostname=hostname,
        ip_address=ip_address,
        group_name=group_name,
    )
    return RedirectResponse("/inventory", status_code=303)


@router.post("/inventory/{item_id}/delete")
def inventory_delete(request: Request, item_id: int) -> RedirectResponse:
    me = get_session_user(request)
    if not me:
        raise login_redirect()
    try:
        inv_svc.delete_item(item_id)
    except Exception:
        return RedirectResponse("/inventory?error=fk", status_code=303)
    return RedirectResponse("/inventory", status_code=303)
