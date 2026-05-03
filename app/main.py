"""ASGI entrypoint: FastAPI UI/API + mounted MCP Streamable HTTP."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

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
