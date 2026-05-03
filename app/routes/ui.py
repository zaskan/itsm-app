"""Server-rendered HTML UI."""

from __future__ import annotations

import os
from datetime import date, datetime, timezone
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Form, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app import db
from app.auth_deps import get_session_user, login_redirect, verify_password
from app.services import incidents as inc_svc
from app.services import kb as kb_svc
from app.services import webhooks as wh_svc

DIR = os.path.join(os.path.dirname(__file__), "..", "templates")
templates = Jinja2Templates(directory=os.path.normpath(DIR))
router = APIRouter(tags=["ui"])


def _ctx(request: Request, user: dict, **extra: Any) -> dict[str, Any]:
    return {"request": request, "user": user, **extra}


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request) -> HTMLResponse:
    if get_session_user(request):
        return RedirectResponse("/incidents", status_code=302)
    return templates.TemplateResponse(request, "login.html", {})


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
    return templates.TemplateResponse(
        request,
        "incidents.html",
        _ctx(
            request,
            user,
            incidents=rows,
            q=q or "",
            status_filter=status_filter or "",
            severity_filter=severity or "",
            date_from=date_from or "",
            date_to=date_to or "",
            today=today,
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
    snap = inc_svc.create_incident(
        title=title.strip(),
        description=description.strip(),
        severity=severity,
        actor_user_id=me["id"],
        created_at=created_at,
    )
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
    return templates.TemplateResponse(
        request,
        "incident_detail_fragment.html",
        _ctx(request, user, incident=d),
    )


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
    return RedirectResponse(f"/incidents", status_code=303)


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
    return RedirectResponse(f"/incidents", status_code=303)


@router.post("/incidents/{incident_ref}/close")
def incident_close(
    request: Request,
    background_tasks: BackgroundTasks,
    incident_ref: str,
) -> RedirectResponse:
    me = get_session_user(request)
    if not me:
        raise login_redirect()
    try:
        snap = inc_svc.close_incident(incident_ref, me["id"])
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
        _ctx(request, user, articles=articles, q=q or ""),
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
    user = get_session_user(request)
    if not user:
        raise login_redirect()
    url = wh_svc.get_webhook_url()
    return templates.TemplateResponse(
        request,
        "webhook.html",
        _ctx(request, user, webhook_url=url),
    )


@router.post("/webhook-config")
def webhook_save(
    request: Request,
    webhook_url: str = Form(""),
) -> RedirectResponse:
    me = get_session_user(request)
    if not me:
        raise login_redirect()
    wh_svc.set_webhook_url(webhook_url)
    return RedirectResponse("/webhook-config", status_code=303)


@router.get("/users", response_class=HTMLResponse)
def users_page(request: Request) -> HTMLResponse:
    user = get_session_user(request)
    if not user:
        raise login_redirect()
    with db.cursor() as cur:
        cur.execute("SELECT id, username, role FROM users ORDER BY username")
        users = [dict(r) for r in cur.fetchall()]
    return templates.TemplateResponse(
        request,
        "users.html",
        _ctx(request, user, users=users),
    )
