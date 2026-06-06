"""Admin data purge API."""

from __future__ import annotations

import importlib

import app.main as main_mod
from app.services import data_purge as data_purge_svc
from app.services import settings as settings_svc
from starlette.testclient import TestClient


def test_purge_data_wrong_confirm_leaves_data() -> None:
    importlib.reload(main_mod)

    with TestClient(main_mod.app) as client:
        client.put(
            "/api/v1/settings/app",
            json={"app_title": "Custom Title"},
            auth=("admin", "admin"),
        )
        client.post(
            "/api/v1/users",
            json={"username": "demo", "password": "demo", "role": "user"},
            auth=("admin", "admin"),
        )
        client.post(
            "/api/v1/incidents",
            json={"title": "Test incident", "severity": "low"},
            auth=("admin", "admin"),
        )
        client.post(
            "/api/v1/settings/webhooks",
            json={"url": "https://example.com/hook", "label": "test"},
            auth=("admin", "admin"),
        )

        r = client.post(
            "/api/v1/settings/purge-data",
            json={"confirm": "wrong"},
            auth=("admin", "admin"),
        )
        assert r.status_code == 400

        assert settings_svc.get_app_title() == "Custom Title"
        users = client.get("/api/v1/users", auth=("admin", "admin")).json()
        assert len(users) == 2
        incidents = client.get("/api/v1/incidents", auth=("admin", "admin")).json()
        assert len(incidents) == 1
        webhooks = client.get("/api/v1/settings/webhooks", auth=("admin", "admin")).json()
        assert len(webhooks) == 1


def test_purge_data_success_keeps_admin_only() -> None:
    importlib.reload(main_mod)

    with TestClient(main_mod.app) as client:
        client.put(
            "/api/v1/settings/app",
            json={"app_title": "Custom Title"},
            auth=("admin", "admin"),
        )
        client.post(
            "/api/v1/users",
            json={"username": "demo", "password": "demo", "role": "user"},
            auth=("admin", "admin"),
        )
        client.post(
            "/api/v1/incidents",
            json={"title": "Test incident", "severity": "low"},
            auth=("admin", "admin"),
        )
        client.post(
            "/api/v1/settings/webhooks",
            json={"url": "https://example.com/hook", "label": "test"},
            auth=("admin", "admin"),
        )

        r = client.post(
            "/api/v1/settings/purge-data",
            json={"confirm": data_purge_svc.PURGE_CONFIRM_PHRASE},
            auth=("admin", "admin"),
        )
        assert r.status_code == 200
        data = r.json()
        assert "deleted" in data
        assert data["deleted"].get("incidents", 0) >= 1
        assert data["deleted"].get("non_admin_users", 0) >= 1

        assert settings_svc.get_app_title() == settings_svc.DEFAULT_APP_TITLE
        users = client.get("/api/v1/users", auth=("admin", "admin")).json()
        assert len(users) == 1
        assert users[0]["username"] == "admin"
        assert users[0]["role"] == "admin"
        assert client.get("/api/v1/incidents", auth=("admin", "admin")).json() == []
        assert client.get("/api/v1/settings/webhooks", auth=("admin", "admin")).json() == []
