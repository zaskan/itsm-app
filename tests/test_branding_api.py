"""Admin branding API (Basic auth).

Uses a single ``TestClient`` session because the app lifespan starts MCP's
``StreamableHTTPSessionManager``, which only allows one startup per process.
"""

from __future__ import annotations

import importlib

import app.main as main_mod
from starlette.testclient import TestClient


def test_settings_branding_admin_get_and_delete_logo_204() -> None:
    """Bootstrap admin can read branding; DELETE logo returns 204 with empty body."""
    importlib.reload(main_mod)

    with TestClient(main_mod.app) as client:
        r = client.get("/api/v1/settings/branding", auth=("admin", "admin"))
        assert r.status_code == 200
        data = r.json()
        assert data["logo_mode"] == "builtin"
        assert data["logo_url"].startswith("/static/")
        assert data["sidebar_background"].startswith("#")
        assert data["sidebar_text"].startswith("#")
        assert "navy" in data["presets_supported"]

        rdel = client.delete("/api/v1/settings/branding/logo", auth=("admin", "admin"))
        assert rdel.status_code == 204
        assert (rdel.content or b"") == b""
