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
from app.markdown_render import render_markdown

DIR = os.path.join(os.path.dirname(__file__), "..", "templates")
templates = Jinja2Templates(directory=os.path.normpath(DIR))
templates.env.filters["markdown"] = render_markdown
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
def settings_page(
    request: Request,
    purged: str | None = None,
    error: str | None = None,
) -> HTMLResponse:
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
            purged=purged == "1",
            purge_error=error or "",
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


@router.post("/settings/purge-data")
def settings_purge_data(
    request: Request,
    confirm: str = Form(""),
) -> RedirectResponse:
    me = require_admin_session(request)
    if confirm != data_purge_svc.PURGE_CONFIRM_PHRASE:
        return RedirectResponse("/settings?error=purge_confirm", status_code=303)
    try:
        data_purge_svc.purge_all_data(actor_admin_id=me["id"])
    except ValueError as e:
        from urllib.parse import quote

        return RedirectResponse(f"/settings?error={quote(str(e))}", status_code=303)
    return RedirectResponse("/settings?purged=1", status_code=303)


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


@router.post("/incidents/{incident_ref}/delete")
def incident_delete(
    request: Request,
    background_tasks: BackgroundTasks,
    incident_ref: str,
) -> RedirectResponse:
    me = get_session_user(request)
    if not me:
        raise login_redirect()
    snap = inc_svc.get_incident_detail(incident_ref)
    if not inc_svc.delete_incident(incident_ref):
        raise HTTPException(404, "Not found")
    if snap:
        wh_svc.schedule_incident_webhook(
            background_tasks, "deleted", me["username"], snap
        )
    return RedirectResponse("/incidents", status_code=303)


@router.get("/kb", response_class=HTMLResponse)
def kb_page(
    request: Request,
    q: str | None = None,
    open: int | None = Query(None, alias="open"),
) -> HTMLResponse:
    user = get_session_user(request)
    if not user:
        raise login_redirect()
    articles = kb_svc.list_articles(q=q)
    return templates.TemplateResponse(
        request,
        "kb.html",
        _page(request, user, articles=articles, q=q or "", open_article_id=open),
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
    q: str = Form(""),
) -> RedirectResponse:
    me = get_session_user(request)
    if not me:
        raise login_redirect()
    kb_svc.update_article(article_id, title.strip(), description.strip())
    params = f"open={article_id}"
    if q.strip():
        from urllib.parse import quote

        params = f"q={quote(q.strip())}&{params}"
    return RedirectResponse(f"/kb?{params}", status_code=303)


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
    hooks = wh_svc.list_webhooks()
    return templates.TemplateResponse(
        request,
        "webhook.html",
        _page(request, user, webhooks=hooks),
    )


@router.post("/webhook-config/add")
def webhook_add(
    request: Request,
    url: str = Form(...),
    label: str = Form(""),
    enabled: str | None = Form(None),
) -> RedirectResponse:
    require_admin_session(request)
    try:
        wh_svc.create_webhook(url, label=label, enabled=enabled == "on")
    except ValueError:
        pass
    return RedirectResponse("/webhook-config", status_code=303)


@router.post("/webhook-config/{webhook_id}/toggle")
def webhook_toggle(request: Request, webhook_id: int) -> RedirectResponse:
    require_admin_session(request)
    row = wh_svc.get_webhook(webhook_id)
    if row:
        wh_svc.update_webhook(webhook_id, enabled=not bool(row["enabled"]))
    return RedirectResponse("/webhook-config", status_code=303)


@router.post("/webhook-config/{webhook_id}/delete")
def webhook_delete(request: Request, webhook_id: int) -> RedirectResponse:
    require_admin_session(request)
    wh_svc.delete_webhook(webhook_id)
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
    user = require_admin_session(request)
    rows = at_svc.list_types()
    types_with_fields = []
    for t in rows:
        d = dict(t)
        d["field_definitions"] = cf_svc.list_definitions("asset_type", t["id"])
        types_with_fields.append(d)
    return templates.TemplateResponse(
        request,
        "asset_types.html",
        _page(request, user, types=types_with_fields),
    )


@router.post("/asset-types/new")
def asset_types_new(
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
) -> RedirectResponse:
    require_admin_session(request)
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
    require_admin_session(request)
    at_svc.update_type(type_id, name, description)
    return RedirectResponse("/asset-types", status_code=303)


@router.post("/asset-types/{type_id}/delete")
def asset_types_delete(request: Request, type_id: int) -> RedirectResponse:
    require_admin_session(request)
    try:
        at_svc.delete_type(type_id)
    except Exception:
        return RedirectResponse("/asset-types?error=fk", status_code=303)
    return RedirectResponse("/asset-types", status_code=303)


@router.post("/asset-types/{type_id}/fields/new")
def asset_type_field_new(
    request: Request,
    type_id: int,
    field_key: str = Form(...),
    label: str = Form(...),
    field_type: str = Form("text"),
    required: str = Form(""),
    options: str = Form(""),
) -> RedirectResponse:
    require_admin_session(request)
    opts = [o.strip() for o in options.split(",") if o.strip()]
    try:
        cf_svc.create_definition(
            scope_type="asset_type",
            scope_id=type_id,
            field_key=field_key,
            label=label,
            field_type=field_type,
            required=required == "1",
            options=opts,
        )
    except ValueError:
        return RedirectResponse("/asset-types?error=field", status_code=303)
    return RedirectResponse("/asset-types", status_code=303)


@router.post("/asset-types/{type_id}/fields/{field_id}/delete")
def asset_type_field_delete(request: Request, type_id: int, field_id: int) -> RedirectResponse:
    require_admin_session(request)
    cf_svc.delete_definition(field_id)
    return RedirectResponse("/asset-types", status_code=303)


@router.get("/assets", response_class=HTMLResponse)
def assets_page(
    request: Request,
    q: str | None = None,
    external_only: str | None = None,
) -> HTMLResponse:
    user = get_session_user(request)
    if not user:
        raise login_redirect()
    ext = external_only == "1"
    rows = inv_svc.list_inventory(q=q, external_only=ext)
    types = at_svc.list_types()
    type_fields = {t["id"]: cf_svc.list_definitions("asset_type", t["id"]) for t in types}
    all_assets = inv_svc.list_inventory()
    users = usr_svc.list_users()
    return templates.TemplateResponse(
        request,
        "assets.html",
        _page(
            request,
            user,
            items=rows,
            asset_types=types,
            type_fields=type_fields,
            all_assets=all_assets,
            users=users,
            q=q or "",
            external_only=ext,
        ),
    )


@router.get("/inventory")
def inventory_redirect() -> RedirectResponse:
    return RedirectResponse("/assets", status_code=303)


@router.post("/assets/new")
async def assets_new(request: Request) -> RedirectResponse:
    me = get_session_user(request)
    if not me:
        raise login_redirect()
    form = await request.form()
    custom = {k[3:]: v for k, v in form.items() if k.startswith("cf_")}
    asset_type_raw = form.get("asset_type_id", "")
    parent_raw = form.get("parent_asset_id", "")
    user_raw = form.get("assigned_user_id", "")
    try:
        inv_svc.create_item(
            str(form.get("name", "")),
            str(form.get("description", "")),
            asset_type_id=int(asset_type_raw) if str(asset_type_raw).isdigit() else None,
            parent_asset_id=int(parent_raw) if str(parent_raw).isdigit() else None,
            assigned_user_id=int(user_raw) if str(user_raw).isdigit() else None,
            external_inventory=form.get("external_inventory") == "1",
            custom_fields=custom or None,
        )
    except (ValueError, Exception):
        return RedirectResponse("/assets?error=1", status_code=303)
    return RedirectResponse("/assets", status_code=303)


@router.post("/assets/{item_id}/edit")
async def assets_edit(request: Request, item_id: int) -> RedirectResponse:
    me = get_session_user(request)
    if not me:
        raise login_redirect()
    form = await request.form()
    custom = {k[3:]: v for k, v in form.items() if k.startswith("cf_")}
    asset_type_raw = form.get("asset_type_id", "")
    parent_raw = form.get("parent_asset_id", "")
    user_raw = form.get("assigned_user_id", "")
    try:
        inv_svc.update_item(
            item_id,
            name=str(form.get("name", "")),
            description=str(form.get("description", "")),
            asset_type_id=int(asset_type_raw) if str(asset_type_raw).isdigit() else None,
            parent_asset_id=int(parent_raw) if str(parent_raw).isdigit() else None,
            clear_parent=not str(parent_raw).isdigit(),
            assigned_user_id=int(user_raw) if str(user_raw).isdigit() else None,
            clear_assigned_user=not str(user_raw).isdigit(),
            external_inventory=form.get("external_inventory") == "1",
            custom_fields=custom if custom else None,
        )
    except ValueError:
        return RedirectResponse("/assets?error=1", status_code=303)
    return RedirectResponse("/assets", status_code=303)


@router.post("/assets/{item_id}/delete")
def assets_delete(request: Request, item_id: int) -> RedirectResponse:
    me = get_session_user(request)
    if not me:
        raise login_redirect()
    try:
        inv_svc.delete_item(item_id)
    except Exception:
        return RedirectResponse("/assets?error=fk", status_code=303)
    return RedirectResponse("/assets", status_code=303)


@router.get("/requests", response_class=HTMLResponse)
def requests_page(
    request: Request,
    q: str | None = None,
    status: str | None = None,
    open_only: str | None = None,
) -> HTMLResponse:
    user = get_session_user(request)
    if not user:
        raise login_redirect()
    is_open = open_only == "1" or (open_only is None and status is None)
    rows = req_svc.list_requests(
        status=status if not is_open else None,
        open_only=is_open and status is None,
        q=q,
    )
    templates_list = rtpl_svc.list_request_templates()
    template_fields = {
        str(t["id"]): cf_svc.list_definitions("request_template", t["id"])
        for t in templates_list
    }
    template_meta = {
        str(t["id"]): {"name": t["name"], "description": t["description"]}
        for t in templates_list
    }
    return templates.TemplateResponse(
        request,
        "requests.html",
        _page(
            request,
            user,
            requests=rows,
            q=q or "",
            status_filter=status or "",
            open_only=is_open,
            request_templates=templates_list,
            template_fields=template_fields,
            template_meta=template_meta,
        ),
    )


@router.post("/requests/new")
async def requests_new(
    request: Request,
    background_tasks: BackgroundTasks,
    name: str = Form(...),
    description: str = Form(...),
    request_template_id: str = Form(""),
) -> RedirectResponse:
    me = get_session_user(request)
    if not me:
        raise login_redirect()
    tpl_id = int(request_template_id) if request_template_id.isdigit() else None
    form = await request.form()
    custom = {k[3:]: v for k, v in form.items() if k.startswith("cf_")}
    try:
        snap = req_svc.create_request(
            requester_user_id=me["id"],
            name=name.strip(),
            description=description.strip(),
            request_template_id=tpl_id,
            specifications=custom if tpl_id else None,
        )
        if tpl_id:
            snap = wf_svc.submit_request(snap["public_id"], me["id"])
            wh_svc.schedule_workflow_webhook(
                background_tasks, "request", "submitted", me["username"], snap
            )
            for chg in snap.get("changes_created", []):
                wh_svc.schedule_workflow_webhook(
                    background_tasks, "change", "created", me["username"], chg
                )
                if chg.get("status") == "implementing":
                    wh_svc.schedule_workflow_webhook(
                        background_tasks, "change", "approved", me["username"], chg
                    )
                elif chg.get("status") == "pending_approval":
                    wh_svc.schedule_workflow_webhook(
                        background_tasks, "change", "pending_approval", me["username"], chg
                    )
    except ValueError as e:
        from urllib.parse import quote

        return RedirectResponse(f"/requests?error={quote(str(e))}", status_code=303)
    return RedirectResponse("/requests", status_code=303)


@router.get("/requests/{request_ref}", response_class=HTMLResponse)
def request_detail_fragment(request: Request, request_ref: str) -> HTMLResponse:
    user = get_session_user(request)
    if not user:
        raise login_redirect()
    detail = req_svc.get_request_detail(request_ref)
    if not detail:
        raise HTTPException(404, "Not found")
    catalog = rtpl_svc.list_request_templates()
    template_fields = {
        str(t["id"]): cf_svc.list_definitions("request_template", t["id"])
        for t in catalog
    }
    kb_articles = kb_svc.list_articles()
    return templates.TemplateResponse(
        request,
        "request_detail_fragment.html",
        _page(
            request,
            user,
            service_request=detail,
            request_templates=catalog,
            template_fields=template_fields,
            kb_articles=kb_articles,
        ),
    )


@router.post("/requests/{request_ref}/ritm")
async def request_add_ritm(
    request: Request,
    request_ref: str,
    catalog_item_id: str = Form(""),
    item_type: str = Form(""),
) -> RedirectResponse:
    me = get_session_user(request)
    if not me:
        raise login_redirect()
    cid: int | None = int(catalog_item_id) if catalog_item_id.isdigit() else None
    form = await request.form()
    custom = {k[3:]: v for k, v in form.items() if k.startswith("cf_")}
    try:
        req_svc.add_ritm_to_request(
            request_ref,
            request_template_id=cid,
            item_type=item_type.strip(),
            specifications=custom if cid else None,
            actor_user_id=me["id"],
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return RedirectResponse("/requests", status_code=303)


@router.post("/requests/{request_ref}/submit")
def request_submit(
    request: Request,
    background_tasks: BackgroundTasks,
    request_ref: str,
) -> RedirectResponse:
    me = get_session_user(request)
    if not me:
        raise login_redirect()
    try:
        snap = wf_svc.submit_request(request_ref, me["id"])
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    wh_svc.schedule_workflow_webhook(
        background_tasks, "request", "submitted", me["username"], snap
    )
    return RedirectResponse("/requests", status_code=303)


@router.post("/requests/{request_ref}/cancel")
def request_cancel(
    request: Request,
    background_tasks: BackgroundTasks,
    request_ref: str,
) -> RedirectResponse:
    me = get_session_user(request)
    if not me:
        raise login_redirect()
    try:
        snap = req_svc.cancel_request(request_ref, me["id"])
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    wh_svc.schedule_workflow_webhook(
        background_tasks, "request", "cancelled", me["username"], snap
    )
    return RedirectResponse("/requests", status_code=303)


@router.post("/requests/{request_ref}/comment")
def request_comment_ui(
    request: Request,
    background_tasks: BackgroundTasks,
    request_ref: str,
    body: str = Form(...),
) -> RedirectResponse:
    me = get_session_user(request)
    if not me:
        raise login_redirect()
    try:
        snap = req_svc.add_request_comment(request_ref, body.strip(), me["id"])
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    wh_svc.schedule_workflow_webhook(
        background_tasks, "request", "comment_added", me["username"], snap
    )
    return RedirectResponse("/requests", status_code=303)


@router.post("/requests/{request_ref}/resolution-kb")
def request_resolution_kb_ui(
    request: Request,
    background_tasks: BackgroundTasks,
    request_ref: str,
    kb_article_id: str = Form(""),
) -> RedirectResponse:
    me = get_session_user(request)
    if not me:
        raise login_redirect()
    raw = kb_article_id.strip()
    kid = int(raw) if raw.isdigit() else None
    try:
        snap = req_svc.set_request_resolution_kb(request_ref, kid, me["id"])
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    wh_svc.schedule_workflow_webhook(
        background_tasks, "request", "kb_assigned", me["username"], snap
    )
    return RedirectResponse("/requests", status_code=303)


@router.post("/requests/{request_ref}/close")
def request_close_ui(
    request: Request,
    background_tasks: BackgroundTasks,
    request_ref: str,
    kb_article_id: str = Form(""),
) -> RedirectResponse:
    me = get_session_user(request)
    if not me:
        raise login_redirect()
    raw = kb_article_id.strip()
    kid = int(raw) if raw.isdigit() else None
    try:
        snap = req_svc.close_request(
            request_ref, me["id"], resolution_kb_article_id=kid
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    wh_svc.schedule_workflow_webhook(
        background_tasks, "request", "closed", me["username"], snap
    )
    return RedirectResponse("/requests", status_code=303)


@router.get("/changes", response_class=HTMLResponse)
def changes_page(
    request: Request,
    status: str | None = None,
    open_only: str | None = None,
) -> HTMLResponse:
    user = get_session_user(request)
    if not user:
        raise login_redirect()
    if status:
        rows = chg_svc.list_changes(status=status, open_only=False)
        is_open = False
    else:
        is_open = open_only != "0" if open_only is not None else True
        rows = chg_svc.list_changes(status=None, open_only=is_open)
    change_templates_list = ctpl_svc.list_change_templates()
    template_fields = {
        str(t["id"]): cf_svc.list_definitions("change_template", t["id"])
        for t in change_templates_list
    }
    return templates.TemplateResponse(
        request,
        "changes.html",
        _page(
            request,
            user,
            changes=rows,
            status_filter=status or "",
            open_only=is_open,
            change_templates=change_templates_list,
            template_fields=template_fields,
        ),
    )


@router.get("/changes/{change_ref}", response_class=HTMLResponse)
def change_detail_fragment(request: Request, change_ref: str) -> HTMLResponse:
    user = get_session_user(request)
    if not user:
        raise login_redirect()
    detail = chg_svc.get_change_detail(change_ref)
    if not detail:
        raise HTTPException(404, "Not found")
    return templates.TemplateResponse(
        request,
        "change_detail_fragment.html",
        _page(request, user, change=detail),
    )


@router.post("/changes/{change_ref}/approve")
def change_approve(
    request: Request,
    background_tasks: BackgroundTasks,
    change_ref: str,
) -> RedirectResponse:
    me = require_admin_session(request)
    try:
        snap = chg_svc.approve_change(change_ref, me["id"])
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    wh_svc.schedule_workflow_webhook(
        background_tasks, "change", "approved", me["username"], snap
    )
    return RedirectResponse("/changes", status_code=303)


@router.post("/changes/{change_ref}/tasks/{ctask_ref}/start")
def ctask_start_ui(request: Request, change_ref: str, ctask_ref: str) -> RedirectResponse:
    me = get_session_user(request)
    if not me:
        raise login_redirect()
    try:
        chg_svc.start_ctask(change_ref, ctask_ref, me["id"])
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return RedirectResponse("/changes", status_code=303)


@router.post("/changes/{change_ref}/tasks/{ctask_ref}/complete")
def ctask_complete_ui(
    request: Request,
    background_tasks: BackgroundTasks,
    change_ref: str,
    ctask_ref: str,
    completion_comment: str = Form(""),
) -> RedirectResponse:
    me = get_session_user(request)
    if not me:
        raise login_redirect()
    try:
        snap = wf_svc.on_ctask_completed(
            change_ref, ctask_ref, me["id"], completion_comment=completion_comment
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    wh_svc.schedule_workflow_webhook(
        background_tasks, "change", "ctask_completed", me["username"], snap
    )
    return RedirectResponse("/changes", status_code=303)


@router.post("/tasks/{task_ref}/start")
def task_start_ui(request: Request, task_ref: str) -> RedirectResponse:
    me = get_session_user(request)
    if not me:
        raise login_redirect()
    try:
        task_svc.start_task(task_ref, me["id"])
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return RedirectResponse("/tasks", status_code=303)


@router.post("/tasks/{task_ref}/complete")
def task_complete_ui(
    request: Request,
    task_ref: str,
    completion_comment: str = Form(""),
) -> RedirectResponse:
    me = get_session_user(request)
    if not me:
        raise login_redirect()
    try:
        task_svc.complete_task(task_ref, me["id"], completion_comment=completion_comment)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return RedirectResponse("/tasks", status_code=303)


@router.post("/changes/new")
async def changes_new_ui(
    request: Request,
    background_tasks: BackgroundTasks,
    change_template_id: int = Form(...),
) -> RedirectResponse:
    me = get_session_user(request)
    if not me:
        raise login_redirect()
    form = await request.form()
    custom = {k[3:]: v for k, v in form.items() if k.startswith("cf_")}
    try:
        snap = chg_svc.create_change_from_template(
            change_template_id=change_template_id,
            custom_fields=custom,
            actor_user_id=me["id"],
        )
        if snap.get("change_type") == "standard":
            snap = chg_svc.auto_approve_change(snap["id"], me["id"])
            wh_svc.schedule_workflow_webhook(
                background_tasks, "change", "approved", me["username"], snap
            )
        else:
            snap = chg_svc.set_change_pending_approval(snap["id"], me["id"])
            wh_svc.schedule_workflow_webhook(
                background_tasks, "change", "pending_approval", me["username"], snap
            )
        wh_svc.schedule_workflow_webhook(
            background_tasks, "change", "created", me["username"], snap
        )
    except ValueError as e:
        from urllib.parse import quote

        return RedirectResponse(f"/changes?error={quote(str(e))}", status_code=303)
    return RedirectResponse("/changes", status_code=303)


@router.get("/tasks", response_class=HTMLResponse)
def tasks_page(
    request: Request,
    status: str | None = None,
    open_only: str | None = None,
    q: str | None = None,
) -> HTMLResponse:
    user = get_session_user(request)
    if not user:
        raise login_redirect()
    if status:
        rows = task_svc.list_tasks(status=status, open_only=False, q=q)
        is_open = False
    else:
        is_open = open_only != "0" if open_only is not None else True
        rows = task_svc.list_tasks(status=None, open_only=is_open, q=q)
    changes = chg_svc.list_changes(open_only=True)
    task_templates_list = ttpl_svc.list_task_templates()
    users = usr_svc.list_users()
    return templates.TemplateResponse(
        request,
        "tasks.html",
        _page(
            request,
            user,
            tasks=rows,
            status_filter=status or "",
            open_only=is_open,
            q=q or "",
            changes=changes,
            task_templates=task_templates_list,
            users=users,
        ),
    )


@router.get("/tasks/{task_ref}", response_class=HTMLResponse)
def task_detail_fragment(request: Request, task_ref: str) -> HTMLResponse:
    user = get_session_user(request)
    if not user:
        raise login_redirect()
    detail = task_svc.get_task_detail(task_ref)
    if not detail:
        raise HTTPException(404, "Not found")
    return templates.TemplateResponse(
        request,
        "task_detail_fragment.html",
        _page(request, user, task=detail),
    )


@router.post("/tasks/new")
def tasks_new_ui(
    request: Request,
    change_ref: str = Form(""),
    title: str = Form(""),
    task_template_id: str = Form(""),
    description: str = Form(""),
    assigned_user_id: str = Form(""),
    kb_article_id: str = Form(""),
) -> RedirectResponse:
    me = get_session_user(request)
    if not me:
        raise login_redirect()
    tpl_id = int(task_template_id) if task_template_id.isdigit() else None
    kid = int(kb_article_id) if kb_article_id.isdigit() else None
    uid = int(assigned_user_id) if assigned_user_id.isdigit() else None
    cref = change_ref.strip() or None
    try:
        task_svc.create_task(
            change_ref=cref,
            title=title.strip(),
            description=description.strip(),
            assigned_user_id=uid,
            kb_article_id=kid,
            task_template_id=tpl_id,
            actor_user_id=me["id"],
        )
    except ValueError:
        return RedirectResponse("/tasks?error=1", status_code=303)
    return RedirectResponse("/tasks", status_code=303)


@router.get("/request-templates", response_class=HTMLResponse)
def request_templates_page(request: Request) -> HTMLResponse:
    user = require_admin_session(request)
    items = rtpl_svc.list_request_templates()
    change_tpls = ctpl_svc.list_change_templates()
    return templates.TemplateResponse(
        request,
        "request_templates.html",
        _page(request, user, templates=items, change_templates=change_tpls),
    )


@router.post("/request-templates/new")
def request_templates_new(
    request: Request,
    name: str = Form(...),
    description: str = Form(...),
    change_template_id: str = Form(""),
    require_standard_change: str = Form("1"),
) -> RedirectResponse:
    require_admin_session(request)
    ct_id = int(change_template_id) if change_template_id.isdigit() else None
    try:
        rtpl_svc.create_request_template(
            name=name.strip(),
            description=description.strip(),
            change_template_id=ct_id,
            require_standard_change=require_standard_change == "1",
        )
    except ValueError:
        return RedirectResponse("/request-templates?error=1", status_code=303)
    return RedirectResponse("/request-templates", status_code=303)


@router.post("/request-templates/{template_id}/delete")
def request_templates_delete(request: Request, template_id: int) -> RedirectResponse:
    require_admin_session(request)
    rtpl_svc.delete_request_template(template_id)
    return RedirectResponse("/request-templates", status_code=303)


@router.post("/request-templates/{template_id}/fields/new")
def request_template_field_new(
    request: Request,
    template_id: int,
    field_key: str = Form(...),
    label: str = Form(...),
    field_type: str = Form("text"),
    required: str = Form(""),
    options: str = Form(""),
) -> RedirectResponse:
    require_admin_session(request)
    opts = [o.strip() for o in options.split(",") if o.strip()]
    try:
        cf_svc.create_definition(
            scope_type="request_template",
            scope_id=template_id,
            field_key=field_key,
            label=label,
            field_type=field_type,
            required=required == "1",
            options=opts,
        )
    except ValueError:
        return RedirectResponse("/request-templates?error=field", status_code=303)
    return RedirectResponse("/request-templates", status_code=303)


@router.post("/request-templates/{template_id}/fields/{field_id}/delete")
def request_template_field_delete(
    request: Request, template_id: int, field_id: int
) -> RedirectResponse:
    require_admin_session(request)
    cf_svc.delete_definition(field_id)
    return RedirectResponse("/request-templates", status_code=303)


@router.get("/change-templates", response_class=HTMLResponse)
def change_templates_page(request: Request) -> HTMLResponse:
    user = require_admin_session(request)
    items = ctpl_svc.list_change_templates()
    task_tpls = ttpl_svc.list_task_templates()
    return templates.TemplateResponse(
        request,
        "change_templates.html",
        _page(request, user, templates=items, task_templates=task_tpls),
    )


@router.post("/change-templates/new")
def change_templates_new(
    request: Request,
    name: str = Form(...),
    description: str = Form(...),
    change_type: str = Form("standard"),
    task_template_ids: list[int] = Form([]),
) -> RedirectResponse:
    require_admin_session(request)
    try:
        ctpl_svc.create_change_template(
            name=name.strip(),
            description=description.strip(),
            change_type=change_type,
            task_template_ids=task_template_ids,
        )
    except ValueError:
        return RedirectResponse("/change-templates?error=1", status_code=303)
    return RedirectResponse("/change-templates", status_code=303)


@router.post("/change-templates/{template_id}/delete")
def change_templates_delete(request: Request, template_id: int) -> RedirectResponse:
    require_admin_session(request)
    try:
        ctpl_svc.delete_change_template(template_id)
    except ValueError:
        return RedirectResponse("/change-templates?error=ref", status_code=303)
    return RedirectResponse("/change-templates", status_code=303)


@router.post("/change-templates/{template_id}/fields/new")
def change_template_field_new(
    request: Request,
    template_id: int,
    field_key: str = Form(...),
    label: str = Form(...),
    field_type: str = Form("text"),
    required: str = Form(""),
    options: str = Form(""),
) -> RedirectResponse:
    require_admin_session(request)
    opts = [o.strip() for o in options.split(",") if o.strip()]
    try:
        cf_svc.create_definition(
            scope_type="change_template",
            scope_id=template_id,
            field_key=field_key,
            label=label,
            field_type=field_type,
            required=required == "1",
            options=opts,
        )
    except ValueError:
        return RedirectResponse("/change-templates?error=field", status_code=303)
    return RedirectResponse("/change-templates", status_code=303)


@router.post("/change-templates/{template_id}/fields/{field_id}/delete")
def change_template_field_delete(
    request: Request, template_id: int, field_id: int
) -> RedirectResponse:
    require_admin_session(request)
    cf_svc.delete_definition(field_id)
    return RedirectResponse("/change-templates", status_code=303)


@router.get("/task-templates", response_class=HTMLResponse)
def task_templates_page(request: Request) -> HTMLResponse:
    user = require_admin_session(request)
    items = ttpl_svc.list_task_templates()
    articles = kb_svc.list_articles()
    users = usr_svc.list_users()
    return templates.TemplateResponse(
        request,
        "task_templates.html",
        _page(request, user, templates=items, kb_articles=articles, users=users),
    )


@router.post("/task-templates/new")
def task_templates_new(
    request: Request,
    name: str = Form(...),
    title: str = Form(...),
    description: str = Form(""),
    assigned_user_id: str = Form(""),
    kb_article_id: str = Form(""),
) -> RedirectResponse:
    require_admin_session(request)
    kid = int(kb_article_id) if kb_article_id.isdigit() else None
    uid = int(assigned_user_id) if assigned_user_id.isdigit() else None
    try:
        ttpl_svc.create_task_template(
            name=name.strip(),
            title=title.strip(),
            description=description.strip(),
            assigned_user_id=uid,
            kb_article_id=kid,
        )
    except ValueError:
        return RedirectResponse("/task-templates?error=1", status_code=303)
    return RedirectResponse("/task-templates", status_code=303)


@router.post("/task-templates/{template_id}/delete")
def task_templates_delete(request: Request, template_id: int) -> RedirectResponse:
    require_admin_session(request)
    ttpl_svc.delete_task_template(template_id)
    return RedirectResponse("/task-templates", status_code=303)


@router.post("/task-templates/{template_id}/fields/new")
def task_template_field_new(
    request: Request,
    template_id: int,
    field_key: str = Form(...),
    label: str = Form(...),
    field_type: str = Form("text"),
    required: str = Form(""),
    options: str = Form(""),
) -> RedirectResponse:
    require_admin_session(request)
    opts = [o.strip() for o in options.split(",") if o.strip()]
    try:
        cf_svc.create_definition(
            scope_type="task_template",
            scope_id=template_id,
            field_key=field_key,
            label=label,
            field_type=field_type,
            required=required == "1",
            options=opts,
        )
    except ValueError:
        return RedirectResponse("/task-templates?error=field", status_code=303)
    return RedirectResponse("/task-templates", status_code=303)


@router.post("/task-templates/{template_id}/fields/{field_id}/delete")
def task_template_field_delete(
    request: Request, template_id: int, field_id: int
) -> RedirectResponse:
    require_admin_session(request)
    cf_svc.delete_definition(field_id)
    return RedirectResponse("/task-templates", status_code=303)


@router.get("/service-catalog")
def service_catalog_redirect() -> RedirectResponse:
    return RedirectResponse("/request-templates", status_code=303)
