"""Tests for MCP tokens, auth by id/username, template resolve, and get tools."""

from __future__ import annotations

import importlib
import json
import os

import pytest
from starlette.testclient import TestClient

from app.auth_deps import verify_password
from app.services import request_templates as rtpl_svc

JSON_HEADERS = {"Content-Type": "application/json", "Accept": "application/json"}
PROTOCOL_VERSION = "2024-11-05"
AUTH = ("admin", "admin")


def _rpc(method: str, params: dict | None, req_id: int | str | None) -> dict:
    msg: dict = {"jsonrpc": "2.0", "method": method}
    if req_id is not None:
        msg["id"] = req_id
    if params is not None:
        msg["params"] = params
    return msg


def _mcp_headers(token: str | None = None) -> dict[str, str]:
    h = dict(JSON_HEADERS)
    if token:
        h["X-ITSM-MCP-Token"] = token
    return h


def test_create_user_returns_mcp_token() -> None:
    import app.main as main_mod

    importlib.reload(main_mod)
    with TestClient(main_mod.app) as client:
        r = client.post(
            "/api/v1/users",
            auth=AUTH,
            json={"username": "mcpuser1", "password": "secret", "role": "user"},
        )
        assert r.status_code == 201
        body = r.json()
        assert body["username"] == "mcpuser1"
        assert body["has_mcp_token"] is True
        assert body["mcp_token"] and len(body["mcp_token"]) > 20

        r2 = client.post(
            f"/api/v1/users/{body['id']}/mcp-token/refresh",
            auth=AUTH,
        )
        assert r2.status_code == 200
        assert r2.json()["mcp_token"] != body["mcp_token"]


def test_login_and_api_accept_user_id() -> None:
    import app.main as main_mod

    importlib.reload(main_mod)
    admin = verify_password("admin", "admin")
    assert admin is not None
    by_id = verify_password(str(admin["id"]), "admin")
    assert by_id is not None
    assert by_id["id"] == admin["id"]

    with TestClient(main_mod.app) as client:
        r = client.get("/api/v1/users", auth=(str(admin["id"]), "admin"))
        assert r.status_code == 200
        assert isinstance(r.json(), list)


def test_resolve_request_template_by_name_slug() -> None:
    import app.main as main_mod

    importlib.reload(main_mod)
    with TestClient(main_mod.app) as client:
        assert client.get("/healthz").status_code == 200
        tpl = rtpl_svc.create_request_template(
            name="New Linux Virtual Machine",
            description="Provision a Linux VM",
            change_template_id=None,
            require_standard_change=False,
        )
        by_id = rtpl_svc.resolve_request_template(tpl["id"])
        by_slug = rtpl_svc.resolve_request_template("New-Linux-Virtual-Machine")
        assert by_id is not None and by_slug is not None
        assert by_id["id"] == by_slug["id"] == tpl["id"]
        assert rtpl_svc.resolve_request_template("no-such-template") is None

        r = client.get(
            "/api/v1/request-templates/New-Linux-Virtual-Machine",
            auth=AUTH,
        )
        assert r.status_code == 200
        assert r.json()["id"] == tpl["id"]


def test_mcp_get_request_and_get_change_and_user_token(
    monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> None:
    default_db = os.environ["ITSM_DATABASE"]
    db_path = tmp_path_factory.mktemp("mcpget") / "get.db"
    import app.main as main_mod

    try:
        monkeypatch.setenv("ITSM_DATABASE", str(db_path))
        monkeypatch.delenv("MCP_TOKEN", raising=False)
        importlib.reload(main_mod)

        with TestClient(main_mod.app) as client:
            created = client.post(
                "/api/v1/users",
                auth=AUTH,
                json={"username": "agent", "password": "agentpass", "role": "admin"},
            )
            assert created.status_code == 201, created.text
            token = created.json()["mcp_token"]
            headers = _mcp_headers(token)

            # wrong token → 401 once a user token exists
            r = client.post(
                "/mcp/",
                json=_rpc(
                    "initialize",
                    {
                        "protocolVersion": PROTOCOL_VERSION,
                        "capabilities": {},
                        "clientInfo": {"name": "pytest", "version": "0"},
                    },
                    1,
                ),
                headers=_mcp_headers("wrong"),
            )
            assert r.status_code == 401

            r = client.post(
                "/mcp/",
                json=_rpc(
                    "initialize",
                    {
                        "protocolVersion": PROTOCOL_VERSION,
                        "capabilities": {},
                        "clientInfo": {"name": "pytest", "version": "0"},
                    },
                    1,
                ),
                headers=headers,
            )
            assert r.status_code == 200
            client.post(
                "/mcp/",
                json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                headers=headers,
            )

            r = client.post(
                "/mcp/", json=_rpc("tools/list", {}, 2), headers=headers
            )
            names = {t["name"] for t in r.json()["result"]["tools"]}
            assert "get_request" in names
            assert "get_change" in names

            tr = client.post(
                "/api/v1/request-templates",
                auth=("agent", "agentpass"),
                json={
                    "name": "Simple Catalog Item",
                    "description": "A simple item",
                    "require_standard_change": False,
                },
            )
            assert tr.status_code == 201

            rr = client.post(
                "/api/v1/requests",
                auth=("agent", "agentpass"),
                json={
                    "name": "My REQ",
                    "description": "desc",
                    "request_template_id": "Simple-Catalog-Item",
                },
            )
            assert rr.status_code == 201, rr.text
            req = rr.json()
            assert req["public_id"].startswith("REQ-")

            r = client.post(
                "/mcp/",
                json=_rpc(
                    "tools/call",
                    {
                        "name": "get_request",
                        "arguments": {"request_ref": req["public_id"]},
                    },
                    3,
                ),
                headers=headers,
            )
            assert r.status_code == 200
            payload = json.loads(r.json()["result"]["content"][0]["text"])
            assert payload.get("public_id") == req["public_id"]
            assert "ritms" in payload

            r = client.post(
                "/mcp/",
                json=_rpc(
                    "tools/call",
                    {"name": "get_change", "arguments": {"change_ref": "CHG-99999"}},
                    4,
                ),
                headers=headers,
            )
            assert r.status_code == 200
            chg_payload = json.loads(r.json()["result"]["content"][0]["text"])
            assert chg_payload.get("error") == "not_found"
    finally:
        monkeypatch.delenv("MCP_TOKEN", raising=False)
        os.environ["ITSM_DATABASE"] = default_db
        importlib.reload(main_mod)


def test_mcp_actor_is_token_owner(
    monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """Mutating MCP tools must audit as the per-user token owner, not user 1."""
    default_db = os.environ["ITSM_DATABASE"]
    db_path = tmp_path_factory.mktemp("mcpactor") / "actor.db"
    import app.main as main_mod

    try:
        monkeypatch.setenv("ITSM_DATABASE", str(db_path))
        monkeypatch.delenv("MCP_TOKEN", raising=False)
        importlib.reload(main_mod)

        with TestClient(main_mod.app) as client:
            created = client.post(
                "/api/v1/users",
                auth=AUTH,
                json={"username": "tokactor", "password": "pass", "role": "admin"},
            )
            assert created.status_code == 201, created.text
            agent_id = created.json()["id"]
            assert agent_id != 1
            token = created.json()["mcp_token"]
            headers = _mcp_headers(token)

            r = client.post(
                "/mcp/",
                json=_rpc(
                    "initialize",
                    {
                        "protocolVersion": PROTOCOL_VERSION,
                        "capabilities": {},
                        "clientInfo": {"name": "pytest", "version": "0"},
                    },
                    1,
                ),
                headers=headers,
            )
            assert r.status_code == 200
            client.post(
                "/mcp/",
                json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                headers=headers,
            )

            r = client.post(
                "/mcp/", json=_rpc("tools/list", {}, 2), headers=headers
            )
            tools = {t["name"]: t for t in r.json()["result"]["tools"]}
            create_schema = tools["create_incident"]["inputSchema"]
            props = create_schema.get("properties") or {}
            assert "actor_user_id" not in props

            r = client.post(
                "/mcp/",
                json=_rpc(
                    "tools/call",
                    {
                        "name": "create_incident",
                        "arguments": {
                            "title": "Token actor incident",
                            "description": "should be owned by tokactor",
                            "severity": "low",
                        },
                    },
                    3,
                ),
                headers=headers,
            )
            assert r.status_code == 200
            snap = json.loads(r.json()["result"]["content"][0]["text"])
            assert "error" not in snap, snap
            public_id = snap["public_id"]

            detail = client.get(
                f"/api/v1/incidents/{public_id}",
                auth=("tokactor", "pass"),
            )
            assert detail.status_code == 200
            body = detail.json()
            events = body.get("events") or []
            assert events, body
            # created event actor must be token owner, not bootstrap admin (1)
            created_ev = next(e for e in events if e.get("event_type") == "created")
            assert created_ev["actor_username"] == "tokactor"

            r = client.post(
                "/mcp/",
                json=_rpc(
                    "tools/call",
                    {
                        "name": "create_request",
                        "arguments": {
                            "name": "From token",
                            "description": "requester should be tokactor",
                        },
                    },
                    4,
                ),
                headers=headers,
            )
            assert r.status_code == 200
            req_snap = json.loads(r.json()["result"]["content"][0]["text"])
            assert "error" not in req_snap, req_snap
            assert req_snap["requester_user_id"] == agent_id
            assert req_snap.get("requester_username") == "tokactor"
    finally:
        monkeypatch.delenv("MCP_TOKEN", raising=False)
        os.environ["ITSM_DATABASE"] = default_db
        importlib.reload(main_mod)
