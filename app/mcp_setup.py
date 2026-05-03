"""FastMCP Streamable HTTP server for agent integrations."""

from __future__ import annotations

import json
import os
from typing import Any

from mcp.server.fastmcp import FastMCP

from app.services import incidents as inc_svc
from app.services import kb as kb_svc


def build_mcp() -> FastMCP:
    mcp = FastMCP(
        "ITSM Demo",
        instructions="Tools for ITSM incidents and knowledge base (demo).",
        stateless_http=True,
        json_response=True,
        streamable_http_path="/",
    )

    @mcp.tool(name="list_incidents", description="List incidents with optional status and severity filters.")
    def list_incidents(status: str | None = None, severity: str | None = None) -> str:
        rows = inc_svc.list_incidents(
            q=None,
            status=status,
            severity=severity,
            date_from=None,
            date_to=None,
        )
        return json.dumps(rows, indent=2)

    @mcp.tool(name="get_incident", description="Get full incident detail including comments and audit events.")
    def get_incident(incident_ref: str) -> str:
        d = inc_svc.get_incident_detail(incident_ref)
        if not d:
            return json.dumps({"error": "not_found"})
        return json.dumps(d, indent=2)

    @mcp.tool(name="create_incident", description="Create an incident (system user context — prefer REST with credentials for audit).")
    def create_incident(
        title: str,
        description: str = "",
        severity: str = "medium",
        actor_user_id: int = 1,
    ) -> str:
        snap = inc_svc.create_incident(
            title=title,
            description=description,
            severity=severity,
            actor_user_id=actor_user_id,
            created_at=None,
        )
        return json.dumps(snap, indent=2)

    @mcp.tool(name="add_comment", description="Add a comment to an open incident.")
    def add_comment(incident_ref: str, body: str, actor_user_id: int = 1) -> str:
        try:
            snap = inc_svc.add_comment(incident_ref, body, actor_user_id)
        except ValueError as e:
            return json.dumps({"error": str(e)})
        return json.dumps(snap, indent=2)

    @mcp.tool(name="update_severity", description="Change severity on an open incident.")
    def update_severity(incident_ref: str, severity: str, actor_user_id: int = 1) -> str:
        try:
            snap = inc_svc.update_severity(incident_ref, severity, actor_user_id)
        except ValueError as e:
            return json.dumps({"error": str(e)})
        return json.dumps(snap, indent=2)

    @mcp.tool(name="close_incident", description="Close an incident.")
    def close_incident(incident_ref: str, actor_user_id: int = 1) -> str:
        try:
            snap = inc_svc.close_incident(incident_ref, actor_user_id)
        except ValueError as e:
            return json.dumps({"error": str(e)})
        return json.dumps(snap, indent=2)

    @mcp.tool(name="list_kb_articles", description="List knowledge base articles.")
    def list_kb_articles(query: str | None = None) -> str:
        rows = kb_svc.list_articles(q=query)
        return json.dumps(rows, indent=2)

    @mcp.tool(name="search_kb", description="Search KB articles by substring in title or description.")
    def search_kb(query: str, limit: int = 50) -> str:
        rows = kb_svc.search_articles(query, limit=min(limit, 200))
        return json.dumps(rows, indent=2)

    @mcp.tool(name="get_kb_article", description="Fetch one KB article by id.")
    def get_kb_article(article_id: int) -> str:
        art = kb_svc.get_article(article_id)
        if not art:
            return json.dumps({"error": "not_found"})
        return json.dumps(art, indent=2)

    @mcp.resource("kb://catalog")
    def kb_catalog() -> str:
        rows = kb_svc.list_articles()
        return json.dumps(rows, indent=2)

    @mcp.resource("kb://article/{article_id}")
    def kb_article_resource(article_id: int) -> str:
        art = kb_svc.get_article(article_id)
        if not art:
            return json.dumps({"error": "not_found"})
        return json.dumps(art, indent=2)

    return mcp


def asgi_with_optional_mcp_auth(inner: Any, token: str | None) -> Any:
    """Wrap MCP Starlette app with optional bearer / X-ITSM-MCP-Token check."""

    class MCPAuthASGI:
        __slots__ = ("app", "token")

        def __init__(self, app: Any, tok: str | None) -> None:
            self.app = app
            self.token = tok

        async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
            if scope["type"] != "http":
                await self.app(scope, receive, send)
                return
            if self.token:
                raw = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
                header_tok = raw.get("x-itsm-mcp-token", "")
                auth = raw.get("authorization", "")
                bearer = ""
                if auth.lower().startswith("bearer "):
                    bearer = auth[7:].strip()
                if header_tok != self.token and bearer != self.token:
                    from starlette.responses import JSONResponse

                    resp = JSONResponse({"detail": "Unauthorized"}, status_code=401)
                    await resp(scope, receive, send)
                    return
            await self.app(scope, receive, send)

    if token:
        return MCPAuthASGI(inner, token)
    return inner


def mcp_mount_app(mcp: FastMCP) -> Any:
    starlette_app = mcp.streamable_http_app()
    tok = os.environ.get("MCP_TOKEN") or None
    return asgi_with_optional_mcp_auth(starlette_app, tok)
