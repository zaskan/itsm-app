"""
Integration tests for the ITSM Streamable HTTP MCP mount (``/mcp/``).

``StreamableHTTPSessionManager.run()`` may only be started once per FastMCP instance,
so we use a single ``TestClient`` context per test function (or one combined flow).

Run: ``pytest tests/`` from the project root.
"""

from __future__ import annotations

import importlib
import json
import os

import pytest
from starlette.testclient import TestClient

JSON_HEADERS = {"Content-Type": "application/json", "Accept": "application/json"}
PROTOCOL_VERSION = "2024-11-05"


def _rpc(method: str, params: dict | None, req_id: int | str | None) -> dict:
    msg: dict = {"jsonrpc": "2.0", "method": method}
    if req_id is not None:
        msg["id"] = req_id
    if params is not None:
        msg["params"] = params
    return msg


def test_mcp_jsonrpc_initialize_tools_list_and_call() -> None:
    """Single lifespan: initialize → initialized → tools/list → tools/call list_incidents."""
    import app.main as main_mod

    importlib.reload(main_mod)

    with TestClient(main_mod.app) as client:
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
            headers=JSON_HEADERS,
        )
        assert r.status_code == 200
        data = r.json()
        assert data["result"]["serverInfo"]["name"] == "ITSM Demo"

        client.post(
            "/mcp/",
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            headers=JSON_HEADERS,
        )

        r = client.post("/mcp/", json=_rpc("tools/list", {}, 2), headers=JSON_HEADERS)
        assert r.status_code == 200
        tools = r.json()["result"]["tools"]
        names = {t["name"] for t in tools}
        assert "create_incident" in names
        assert "create_kb_article" in names
        assert "rag_search_kb" in names
        assert len(names) >= 20

        r = client.post(
            "/mcp/",
            json=_rpc("tools/call", {"name": "list_incidents", "arguments": {}}, 3),
            headers=JSON_HEADERS,
        )
        assert r.status_code == 200
        text = r.json()["result"]["content"][0]["text"]
        assert isinstance(json.loads(text), list)

        r = client.get("/.well-known/oauth-protected-resource/mcp")
        assert r.status_code == 200
        meta = r.json()
        assert "resource" in meta and meta["authorization_servers"]


def test_mcp_post_returns_401_when_mcp_token_required(
    monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """Separate process not needed: reload app with MCP_TOKEN; new FastMCP allows new lifespan."""
    default_db = os.environ["ITSM_DATABASE"]
    db_path = tmp_path_factory.mktemp("mcpauth") / "auth.db"
    import app.main as main_mod

    try:
        monkeypatch.setenv("ITSM_DATABASE", str(db_path))
        monkeypatch.setenv("MCP_TOKEN", "secret-test-token")
        importlib.reload(main_mod)

        body = _rpc(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "pytest", "version": "0"},
            },
            1,
        )
        with TestClient(main_mod.app) as c:
            r = c.post("/mcp/", json=body, headers=JSON_HEADERS)
            assert r.status_code == 401
            assert "error" in r.json()
    finally:
        monkeypatch.delenv("MCP_TOKEN", raising=False)
        os.environ["ITSM_DATABASE"] = default_db
        importlib.reload(main_mod)
