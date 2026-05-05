"""Outbound webhooks: migration from legacy key and multi-destination delivery."""

from __future__ import annotations

import importlib
import sqlite3
from unittest.mock import MagicMock

from starlette.testclient import TestClient

import app.db as db_mod
import app.main as main_mod
import app.services.webhooks as wh_mod


def test_legacy_app_settings_webhook_url_migrates_to_table(tmp_path, monkeypatch) -> None:
    """Old ``webhook_url`` in app_settings becomes one ``outbound_webhooks`` row and key is removed."""
    db_path = tmp_path / "legacy.db"
    monkeypatch.setenv("ITSM_DATABASE", str(db_path))
    if getattr(db_mod._local, "conn", None):
        db_mod._local.conn.close()
        delattr(db_mod._local, "conn")

    try:
        conn = sqlite3.connect(str(db_path))
        conn.executescript(db_mod._SCHEMA_SQL)
        conn.execute(
            "INSERT INTO app_settings (key, value) VALUES ('webhook_url', 'https://legacy.example/hook')",
        )
        conn.commit()
        conn.close()

        db_mod.init_db()

        hooks = wh_mod.list_webhooks()
        assert len(hooks) == 1
        assert hooks[0]["url"] == "https://legacy.example/hook"
        assert hooks[0]["enabled"] == 1

        with db_mod.cursor() as cur:
            cur.execute("SELECT value FROM app_settings WHERE key = 'webhook_url'")
            assert cur.fetchone() is None
    finally:
        if getattr(db_mod._local, "conn", None):
            db_mod._local.conn.close()
            delattr(db_mod._local, "conn")


def test_incident_notifies_all_enabled_webhook_urls(monkeypatch) -> None:
    """Creating an incident POSTs the same payload to every enabled webhook URL."""
    importlib.reload(main_mod)

    posted_urls: list[str] = []

    class _FakeAsyncClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        async def __aenter__(self) -> _FakeAsyncClient:
            return self

        async def __aexit__(self, *args: object) -> None:
            pass

        async def post(self, url: str, json: object | None = None, **kwargs: object) -> MagicMock:
            posted_urls.append(url)
            r = MagicMock()
            r.raise_for_status = MagicMock()
            return r

    monkeypatch.setattr(wh_mod.httpx, "AsyncClient", lambda **kw: _FakeAsyncClient())

    with TestClient(main_mod.app) as client:
        r = client.get("/api/v1/settings/webhooks", auth=("admin", "admin"))
        assert r.status_code == 200
        for h in r.json():
            d = client.delete(
                f"/api/v1/settings/webhooks/{h['id']}",
                auth=("admin", "admin"),
            )
            assert d.status_code == 204

        a = client.post(
            "/api/v1/settings/webhooks",
            json={"url": "https://alpha.example/hook", "label": "A"},
            auth=("admin", "admin"),
        )
        b = client.post(
            "/api/v1/settings/webhooks",
            json={"url": "https://bravo.example/hook", "label": "B"},
            auth=("admin", "admin"),
        )
        assert a.status_code == 201
        assert b.status_code == 201

        inc = client.post(
            "/api/v1/incidents",
            json={"title": "Webhook fan-out", "description": ""},
            auth=("admin", "admin"),
        )
        assert inc.status_code == 201

    assert sorted(posted_urls) == [
        "https://alpha.example/hook",
        "https://bravo.example/hook",
    ]


def test_list_webhooks_requires_auth() -> None:
    importlib.reload(main_mod)
    with TestClient(main_mod.app) as client:
        r = client.get("/api/v1/settings/webhooks")
        assert r.status_code == 401
