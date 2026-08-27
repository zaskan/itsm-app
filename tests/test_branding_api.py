"""Admin branding API (Basic auth).

Uses a single ``TestClient`` session because the app lifespan starts MCP's
``StreamableHTTPSessionManager``, which only allows one startup per process.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest
from starlette.testclient import TestClient

import app.main as main_mod
from app.services import branding as branding_svc

# 1x1 transparent PNG
_PNG_1X1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
)


def test_upload_root_defaults_beside_sqlite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ITSM_DATABASE", str(tmp_path / "itsm.db"))
    monkeypatch.delenv("ITSM_UPLOAD_DIR", raising=False)
    assert branding_svc.upload_root() == tmp_path / "uploads"


def test_upload_root_honors_itsm_upload_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    custom = tmp_path / "logos"
    monkeypatch.setenv("ITSM_UPLOAD_DIR", str(custom))
    assert branding_svc.upload_root() == custom


def test_settings_branding_admin_get_upload_serve_and_delete() -> None:
    """Admin can read branding; custom logos persist under the data dir and DELETE returns 204."""
    importlib.reload(main_mod)
    importlib.reload(branding_svc)

    with TestClient(main_mod.app) as client:
        r = client.get("/api/v1/settings/branding", auth=("admin", "admin"))
        assert r.status_code == 200
        data = r.json()
        assert data["logo_mode"] == "builtin"
        assert data["logo_url"].startswith("/static/")
        assert data["sidebar_background"] == branding_svc.DEFAULT_SIDEBAR_BG
        assert data["sidebar_text"] == branding_svc.DEFAULT_SIDEBAR_TEXT
        assert "navy" in data["presets_supported"]
        assert "polaris" in data["presets_supported"]

        posted = client.post(
            "/api/v1/settings/branding/logo",
            auth=("admin", "admin"),
            files={"file": ("logo.png", _PNG_1X1, "image/png")},
        )
        assert posted.status_code == 200, posted.text
        body = posted.json()
        assert body["logo_mode"] == "custom"
        url = body["logo_url"]
        assert url.startswith("/static/uploads/branding/")
        stored = branding_svc.upload_root() / "branding" / Path(url).name
        assert stored.is_file()
        served = client.get(url)
        assert served.status_code == 200
        assert served.content == _PNG_1X1

        rdel = client.delete("/api/v1/settings/branding/logo", auth=("admin", "admin"))
        assert rdel.status_code == 204
        assert (rdel.content or b"") == b""
        assert not stored.exists()


def test_clear_app_title_via_ui_form_and_api() -> None:
    importlib.reload(main_mod)
    with TestClient(main_mod.app) as client:
        client.post(
            "/login",
            data={"username": "admin", "password": "admin"},
            follow_redirects=False,
        )
        client.put(
            "/api/v1/settings/app",
            json={"app_title": "Temporary"},
            auth=("admin", "admin"),
        )
        r = client.post(
            "/settings/application-title",
            data={"app_title": ""},
            follow_redirects=False,
        )
        assert r.status_code == 303, r.text
        assert client.get("/api/v1/settings/app", auth=("admin", "admin")).json()["app_title"] == ""
        r2 = client.put(
            "/api/v1/settings/app",
            json={"app_title": ""},
            auth=("admin", "admin"),
        )
        assert r2.status_code == 200, r2.text
        assert r2.json()["app_title"] == ""


def test_action_colors_polaris_and_custom_headers() -> None:
    assert branding_svc.action_colors(branding_svc.DEFAULT_SIDEBAR_BG) == (
        branding_svc.DEFAULT_PRIMARY,
        branding_svc.DEFAULT_PRIMARY_HOVER,
    )
    assert branding_svc.action_colors("#14532d")[0] == "#14532d"
    assert branding_svc.action_colors("#f1f5f9")[0] == branding_svc.DEFAULT_PRIMARY


def test_incidents_ui_renders_next_experience_chrome() -> None:
    importlib.reload(main_mod)
    with TestClient(main_mod.app) as client:
        client.post(
            "/login",
            data={"username": "admin", "password": "admin"},
            follow_redirects=False,
        )
        r = client.get("/incidents")
        assert r.status_code == 200
        assert 'data-unified-nav' in r.text
        assert 'id="nx-all-btn"' in r.text
        assert 'nx-rail' not in r.text
        assert 'aria-label="Workspace"' not in r.text
        assert branding_svc.DEFAULT_SIDEBAR_BG in r.text
        assert "/static/css/next-experience.css" in r.text
        css = client.get("/static/css/next-experience.css")
        assert css.status_code == 200
        assert b".nx-header" in css.content


def test_incident_detail_servicenow_layout() -> None:
    importlib.reload(main_mod)
    with TestClient(main_mod.app) as client:
        client.post(
            "/login",
            data={"username": "admin", "password": "admin"},
            follow_redirects=False,
        )
        created = client.post(
            "/api/v1/incidents",
            json={
                "title": "ATF: Test vista detalle",
                "description": "Ticket de prueba para layout estilo ServiceNow.",
                "severity": "low",
            },
            auth=("admin", "admin"),
        )
        assert created.status_code == 201, created.text
        public_id = created.json()["public_id"]
        r = client.get(f"/incidents/{public_id}")
        assert r.status_code == 200
        html = r.text
        assert "incident-detail-page" in html
        assert "nx-record-list-header" in html
        assert "View: Self Service" in html
        assert "Comments" in html and "Customer visible" in html
        assert "Activities:" in html
        assert "inc-activity-card" in html
        assert "Urgency" in html
        assert "Short description" in html
        assert "ATF: Test vista detalle" in html
        assert ".inc-form-panel" in client.get("/static/css/next-experience.css").text


def test_request_change_task_detail_servicenow_layout() -> None:
    importlib.reload(main_mod)
    with TestClient(main_mod.app) as client:
        client.post(
            "/login",
            data={"username": "admin", "password": "admin"},
            follow_redirects=False,
        )
        req = client.post(
            "/api/v1/requests",
            json={"name": "ATF: Request detail", "description": "Test request layout."},
            auth=("admin", "admin"),
        )
        assert req.status_code == 201, req.text
        req_id = req.json()["public_id"]
        r_req = client.get(f"/requests/{req_id}")
        assert r_req.status_code == 200
        assert "incident-detail-page" in r_req.text
        assert "nx-record-list-header" in r_req.text
        assert "Requested for" in r_req.text
        assert "Activities:" in r_req.text

        task = client.post(
            "/api/v1/tasks",
            json={"title": "ATF: Task detail", "description": "Test task layout."},
            auth=("admin", "admin"),
        )
        assert task.status_code == 201, task.text
        task_id = task.json()["public_id"]
        r_task = client.get(f"/tasks/{task_id}")
        assert r_task.status_code == 200
        assert "Assigned to" in r_task.text
        assert "inc-form-panel" in r_task.text
        assert "inc-activity-card" in r_task.text


def test_global_search_routes_by_record_prefix() -> None:
    importlib.reload(main_mod)
    with TestClient(main_mod.app) as client:
        client.post(
            "/login",
            data={"username": "admin", "password": "admin"},
            follow_redirects=False,
        )
        r = client.get("/search", params={"q": "REQ-2026-001"}, follow_redirects=False)
        assert r.status_code == 302
        assert r.headers["location"].startswith("/requests")
        r2 = client.get("/search", params={"q": "disk"}, follow_redirects=False)
        assert r2.status_code == 302
        assert r2.headers["location"].startswith("/incidents")


def test_legacy_navy_chrome_migrates_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app import db

    monkeypatch.setenv("ITSM_DATABASE", str(tmp_path / "legacy.db"))
    db.reset_connections()
    try:
        db.init_db()
        branding_svc.apply_preset("navy")
        with db.cursor() as cur:
            cur.execute(
                "DELETE FROM app_settings WHERE key = ?", (branding_svc.KEY_UI_SHELL,)
            )
        branding_svc.seed_branding_defaults()
        b = branding_svc.get_branding()
        assert b["sidebar_background"] == branding_svc.DEFAULT_SIDEBAR_BG
        assert b["sidebar_text"] == branding_svc.DEFAULT_SIDEBAR_TEXT
    finally:
        db.reset_connections()


def test_custom_chrome_is_not_migrated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app import db

    monkeypatch.setenv("ITSM_DATABASE", str(tmp_path / "custom.db"))
    db.reset_connections()
    try:
        db.init_db()
        branding_svc.apply_preset("forest")
        with db.cursor() as cur:
            cur.execute(
                "DELETE FROM app_settings WHERE key = ?", (branding_svc.KEY_UI_SHELL,)
            )
        branding_svc.seed_branding_defaults()
        b = branding_svc.get_branding()
        assert b["sidebar_background"] == "#14532d"
    finally:
        db.reset_connections()