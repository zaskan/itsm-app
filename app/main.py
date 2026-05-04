"""ASGI entrypoint: FastAPI UI/API + mounted MCP Streamable HTTP."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app import db
from app.mcp_setup import build_mcp, mcp_mount_app
from app.routes import api_v1, ui

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

_session_secret = os.environ.get("SESSION_SECRET", "change-me-in-production")

_mcp = build_mcp()
_mcp_http = mcp_mount_app(_mcp)


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    async with _mcp.session_manager.run():
        logger.info("Database initialized; MCP session manager running.")
        yield


app = FastAPI(
    title="ITSM Demo API",
    description="Lightweight incident ticketing demo with REST, webhooks, and MCP.",
    lifespan=lifespan,
)


@app.exception_handler(StarletteHTTPException)
async def oauth_safe_http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """MCP clients (e.g. Cursor) validate OAuth-shaped JSON on error responses; FastAPI's default
    404 body only has ``detail``, which trips those parsers. Keep normal handling for other codes.
    """
    if exc.status_code == 404:
        desc = exc.detail if isinstance(exc.detail, str) else "Not Found"
        return JSONResponse(
            status_code=404,
            content={"error": "not_found", "error_description": desc},
        )
    return await http_exception_handler(request, exc)


# MCP authorization discovery (RFC 9728 + MCP spec): clients request
# ``GET /.well-known/oauth-protected-resource/mcp`` *before* the root metadata URL.
# Returning 404 here makes Cursor report "Not Found" during streamable HTTP / SSE setup.


def _public_base_url(request: Request) -> str:
    """HTTPS behind OpenShift / ingress: trust ``X-Forwarded-Proto`` and ``Host``."""
    xf = request.headers.get("x-forwarded-proto")
    scheme = (xf or request.url.scheme or "https").split(",")[0].strip()
    host = request.headers.get("host") or request.url.netloc or "localhost"
    return f"{scheme}://{host}".rstrip("/")


def _authorization_server_metadata_document(request: Request) -> dict[str, Any]:
    """RFC 8414-style document; issuer matches ``authorization_servers`` in protected-resource metadata."""
    base = _public_base_url(request)
    root = base + "/"
    return {
        "issuer": root,
        "authorization_endpoint": f"{base}/oauth/authorize",
        "token_endpoint": f"{base}/oauth/token",
        "registration_endpoint": f"{base}/oauth/register",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token", "client_credentials"],
        "code_challenge_methods_supported": ["S256"],
        "scopes_supported": ["openid", "mcp"],
        "token_endpoint_auth_methods_supported": ["none", "client_secret_post"],
    }


def _protected_resource_metadata_document(request: Request) -> dict[str, Any]:
    """RFC 9728; ``resource`` matches the MCP HTTP endpoint (same as Cursor ``url`` in ``mcp.json``)."""
    base = _public_base_url(request)
    return {
        "resource": f"{base}/mcp/",
        "authorization_servers": [base + "/"],
        "scopes_supported": ["mcp"],
        "bearer_methods_supported": ["header"],
    }


def _json_or_head(request: Request, data: dict[str, Any]) -> Response:
    if request.method == "HEAD":
        return Response(status_code=200, media_type="application/json")
    return JSONResponse(data)


@app.api_route("/.well-known/oauth-protected-resource/mcp", methods=["GET", "HEAD"], include_in_schema=False)
def oauth_protected_resource_metadata_for_mcp(request: Request) -> Response:
    """First probe in MCP discovery sequence (path-aligned with ``/mcp`` server)."""
    return _json_or_head(request, _protected_resource_metadata_document(request))


@app.api_route("/.well-known/oauth-protected-resource", methods=["GET", "HEAD"], include_in_schema=False)
def oauth_protected_resource_metadata_root(request: Request) -> Response:
    """Fallback discovery URL from RFC 9728."""
    return _json_or_head(request, _protected_resource_metadata_document(request))


@app.api_route("/.well-known/oauth-authorization-server", methods=["GET", "HEAD"], include_in_schema=False)
def oauth_authorization_server_metadata(request: Request) -> Response:
    return _json_or_head(request, _authorization_server_metadata_document(request))


@app.api_route("/.well-known/openid-configuration", methods=["GET", "HEAD"], include_in_schema=False)
def openid_configuration_metadata(request: Request) -> Response:
    """OIDC discovery — reuse same core fields as AS metadata for interoperability."""
    return _json_or_head(request, _authorization_server_metadata_document(request))


@app.get("/oauth/authorize", include_in_schema=False)
def oauth_authorize_stub(request: Request) -> Response:
    """OAuth browser flow is not implemented; real auth for MCP is optional ``MCP_TOKEN`` header."""
    msg = "This ITSM deployment does not use browser OAuth. Set ITSM_MCP_TOKEN and X-ITSM-MCP-Token in Cursor."
    accept = request.headers.get("accept") or ""
    if "application/json" in accept:
        return JSONResponse(
            status_code=400,
            content={"error": "unsupported_response_type", "error_description": msg},
        )
    return HTMLResponse(f"<html><body><p>{msg}</p></body></html>", status_code=200)


@app.post("/oauth/token", include_in_schema=False)
def oauth_token_stub() -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content={
            "error": "unsupported_grant_type",
            "error_description": "Use MCP with X-ITSM-MCP-Token or Authorization: Bearer matching MCP_TOKEN.",
        },
    )


@app.post("/oauth/register", include_in_schema=False)
def oauth_register_stub() -> JSONResponse:
    return JSONResponse(
        status_code=501,
        content={"error": "not_implemented", "error_description": "Dynamic client registration is not enabled."},
    )


app.add_middleware(SessionMiddleware, secret_key=_session_secret, session_cookie="itsm_session")

app.include_router(ui.router)
app.include_router(api_v1.router)

static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

app.mount("/mcp", _mcp_http)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}
