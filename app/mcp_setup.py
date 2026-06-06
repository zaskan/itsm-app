"""FastMCP Streamable HTTP server for agent integrations."""

from __future__ import annotations

import json
import os
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from app.services import asset_types as at_svc
from app.services import change_templates as ctpl_svc
from app.services import changes as chg_svc
from app.services import custom_fields as cf_svc
from app.services import incidents as inc_svc
from app.services import inventory as inv_svc
from app.services import kb as kb_svc
from app.services import kb_embeddings as kb_emb_svc
from app.services import request_templates as rtpl_svc
from app.services import service_requests as req_svc
from app.services import task_templates as ttpl_svc
from app.services import tasks as task_svc
from app.services import workflow as wf_svc


def _mcp_transport_security() -> TransportSecuritySettings:
    """FastMCP defaults ``host=127.0.0.1``, which turns on DNS rebinding protection for localhost only.

    OpenShift/Ingress sends the public hostname in ``Host``; without disabling or allowlisting, the MCP
    transport returns **421 Invalid Host** after token auth passes.

    Set ``MCP_ALLOWED_HOSTS`` to a comma-separated list (e.g. ``app.example.com,app.example.com:443``)
    to enable protection in production. Omit it to disable (typical behind a trusted ingress).
    """
    raw = os.environ.get("MCP_ALLOWED_HOSTS", "").strip()
    if not raw:
        return TransportSecuritySettings(enable_dns_rebinding_protection=False)
    hosts = [h.strip() for h in raw.split(",") if h.strip()]
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=hosts,
        allowed_origins=[],
    )


def build_mcp() -> FastMCP:
    mcp = FastMCP(
        "ITSM Demo",
        instructions=(
            "Tools for ITSM incidents, service requests (REQ/RITM), changes (CHG/CTASK), tasks, "
            "request/change/task templates, knowledge base, asset types, and assets. "
            "Workflow: create request from template, submit_request to auto-create CHG/CTASK; "
            "complete CTASKs sequentially to fulfill RITM/REQ. "
            "For KB: prefer rag_search_kb for natural-language questions. "
            "MCP has no per-user auth; mirror REST credentials when auditing matters."
        ),
        stateless_http=True,
        json_response=True,
        streamable_http_path="/",
        transport_security=_mcp_transport_security(),
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

    @mcp.tool(
        name="create_incident",
        description="Create an incident (system user context — prefer REST with credentials for audit).",
    )
    def create_incident(
        title: str,
        description: str = "",
        severity: str = "medium",
        actor_user_id: int = 1,
        inventory_asset_id: int | None = None,
    ) -> str:
        try:
            snap = inc_svc.create_incident(
                title=title,
                description=description,
                severity=severity,
                actor_user_id=actor_user_id,
                created_at=None,
                inventory_asset_id=inventory_asset_id,
            )
        except ValueError as e:
            return json.dumps({"error": str(e)})
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

    @mcp.tool(
        name="close_incident",
        description="Close an incident; optionally link a KB article as the resolution record.",
    )
    def close_incident(
        incident_ref: str,
        actor_user_id: int = 1,
        kb_article_id: int | None = None,
    ) -> str:
        try:
            snap = inc_svc.close_incident(
                incident_ref,
                actor_user_id,
                resolution_kb_article_id=kb_article_id,
            )
        except ValueError as e:
            return json.dumps({"error": str(e)})
        return json.dumps(snap, indent=2)

    @mcp.tool(
        name="delete_incident",
        description="Permanently delete an incident by ref (id or public_id).",
    )
    def delete_incident(incident_ref: str) -> str:
        ok = inc_svc.delete_incident(incident_ref)
        return json.dumps({"deleted": ok})

    @mcp.tool(name="list_kb_articles", description="List knowledge base articles.")
    def list_kb_articles(query: str | None = None) -> str:
        rows = kb_svc.list_articles(q=query)
        return json.dumps(rows, indent=2)

    @mcp.tool(name="search_kb", description="Search KB articles by substring in title or description.")
    def search_kb(query: str, limit: int = 50) -> str:
        rows = kb_svc.search_articles(query, limit=min(limit, 200))
        return json.dumps(rows, indent=2)

    @mcp.tool(
        name="rag_search_kb",
        description="Semantic search over the knowledge base using embeddings (configure ITSM_EMBEDDING_* env). Returns top matching articles with similarity scores.",
    )
    def rag_search_kb(query: str, top_k: int = 5) -> str:
        out = kb_emb_svc.rag_search_kb(query, top_k=min(max(top_k, 1), 50))
        return json.dumps(out, indent=2)

    @mcp.tool(name="get_kb_article", description="Fetch one KB article by id.")
    def get_kb_article(article_id: int) -> str:
        art = kb_svc.get_article(article_id)
        if not art:
            return json.dumps({"error": "not_found"})
        return json.dumps(art, indent=2)

    @mcp.tool(name="create_kb_article", description="Create a knowledge base article (title and body text).")
    def create_kb_article(title: str, description: str = "") -> str:
        try:
            row = kb_svc.create_article(title, description)
        except Exception as e:
            return json.dumps({"error": str(e)})
        return json.dumps(row, indent=2)

    @mcp.tool(name="list_asset_types", description="List all asset type definitions.")
    def list_asset_types() -> str:
        return json.dumps(at_svc.list_types(), indent=2)

    @mcp.tool(name="create_asset_type", description="Create an asset type (name must be unique).")
    def create_asset_type(name: str, description: str = "") -> str:
        try:
            row = at_svc.create_type(name, description)
        except Exception as e:
            return json.dumps({"error": str(e)})
        return json.dumps(row, indent=2)

    @mcp.tool(name="update_asset_type", description="Update an asset type by id.")
    def update_asset_type(type_id: int, name: str | None = None, description: str | None = None) -> str:
        row = at_svc.update_type(type_id, name, description)
        if not row:
            return json.dumps({"error": "not_found"})
        return json.dumps(row, indent=2)

    @mcp.tool(name="delete_asset_type", description="Delete an asset type by id.")
    def delete_asset_type(type_id: int) -> str:
        try:
            ok = at_svc.delete_type(type_id)
        except Exception as e:
            return json.dumps({"error": str(e), "deleted": False})
        return json.dumps({"deleted": ok})

    @mcp.tool(name="list_assets", description="List assets; optional search and external_only filter.")
    def list_assets(query: str | None = None, external_only: bool = False) -> str:
        return json.dumps(inv_svc.list_inventory(q=query, external_only=external_only), indent=2)

    @mcp.tool(name="get_asset", description="Get one asset by id.")
    def get_asset(item_id: int) -> str:
        row = inv_svc.get_item(item_id)
        if not row:
            return json.dumps({"error": "not_found"})
        return json.dumps(row, indent=2)

    @mcp.tool(name="create_asset", description="Create an asset (name and description required).")
    def create_asset(
        name: str,
        description: str,
        asset_type_id: int | None = None,
        parent_asset_id: int | None = None,
        external_inventory: bool = False,
        custom_fields_json: str = "{}",
    ) -> str:
        try:
            cf = json.loads(custom_fields_json or "{}")
            row = inv_svc.create_item(
                name,
                description,
                asset_type_id=asset_type_id,
                parent_asset_id=parent_asset_id,
                external_inventory=external_inventory,
                custom_fields=cf,
            )
        except Exception as e:
            return json.dumps({"error": str(e)})
        return json.dumps(row, indent=2)

    @mcp.tool(name="update_asset", description="Update fields on an asset.")
    def update_asset(
        item_id: int,
        name: str | None = None,
        description: str | None = None,
        asset_type_id: int | None = None,
        parent_asset_id: int | None = None,
        external_inventory: bool | None = None,
        custom_fields_json: str | None = None,
    ) -> str:
        cf = json.loads(custom_fields_json) if custom_fields_json else None
        try:
            row = inv_svc.update_item(
                item_id,
                name=name,
                description=description,
                asset_type_id=asset_type_id,
                parent_asset_id=parent_asset_id,
                external_inventory=external_inventory,
                custom_fields=cf,
            )
        except Exception as e:
            return json.dumps({"error": str(e)})
        if not row:
            return json.dumps({"error": "not_found"})
        return json.dumps(row, indent=2)

    @mcp.tool(name="delete_asset", description="Delete an asset by id.")
    def delete_asset(item_id: int) -> str:
        try:
            ok = inv_svc.delete_item(item_id)
        except Exception as e:
            return json.dumps({"error": str(e), "deleted": False})
        return json.dumps({"deleted": ok})

    @mcp.tool(name="list_request_templates", description="List service request templates.")
    def list_request_templates() -> str:
        return json.dumps(rtpl_svc.list_request_templates(), indent=2)

    @mcp.tool(name="get_request_template", description="Get request template by id.")
    def get_request_template(template_id: int) -> str:
        item = rtpl_svc.get_request_template(template_id)
        if not item:
            return json.dumps({"error": "not_found"})
        return json.dumps(item, indent=2)

    @mcp.tool(name="create_request_template", description="Create request template (admin).")
    def create_request_template(
        name: str,
        description: str = "",
        change_template_id: int | None = None,
        require_standard_change: bool = True,
    ) -> str:
        try:
            item = rtpl_svc.create_request_template(
                name=name,
                description=description,
                change_template_id=change_template_id,
                require_standard_change=require_standard_change,
            )
        except ValueError as e:
            return json.dumps({"error": str(e)})
        return json.dumps(item, indent=2)

    @mcp.tool(name="list_change_templates", description="List standard change templates.")
    def list_change_templates_mcp() -> str:
        return json.dumps(ctpl_svc.list_change_templates(), indent=2)

    @mcp.tool(name="create_change_template", description="Create standard change template (admin).")
    def create_change_template_mcp(
        name: str,
        description: str = "",
        change_type: str = "standard",
        task_template_ids_json: str = "[]",
    ) -> str:
        try:
            tids = json.loads(task_template_ids_json or "[]")
            item = ctpl_svc.create_change_template(
                name=name, description=description, change_type=change_type, task_template_ids=tids
            )
        except (ValueError, json.JSONDecodeError) as e:
            return json.dumps({"error": str(e)})
        return json.dumps(item, indent=2)

    @mcp.tool(name="list_task_templates", description="List standard task templates.")
    def list_task_templates_mcp() -> str:
        return json.dumps(ttpl_svc.list_task_templates(), indent=2)

    @mcp.tool(name="create_task_template", description="Create standard task template (admin).")
    def create_task_template_mcp(
        name: str,
        title: str,
        description: str = "",
        assigned_user_id: int | None = None,
        kb_article_id: int | None = None,
    ) -> str:
        try:
            item = ttpl_svc.create_task_template(
                name=name,
                title=title,
                description=description,
                assigned_user_id=assigned_user_id,
                kb_article_id=kb_article_id,
            )
        except ValueError as e:
            return json.dumps({"error": str(e)})
        return json.dumps(item, indent=2)

    @mcp.tool(name="list_custom_fields", description="List custom field definitions for a scope.")
    def list_custom_fields(scope_type: str, scope_id: int) -> str:
        try:
            rows = cf_svc.list_definitions(scope_type, scope_id)
        except ValueError as e:
            return json.dumps({"error": str(e)})
        return json.dumps(rows, indent=2)

    @mcp.tool(name="create_custom_field", description="Create custom field definition (admin).")
    def create_custom_field(
        scope_type: str,
        scope_id: int,
        field_key: str,
        label: str,
        field_type: str = "text",
        required: bool = False,
        options_json: str = "[]",
    ) -> str:
        try:
            opts = json.loads(options_json or "[]")
            row = cf_svc.create_definition(
                scope_type=scope_type,
                scope_id=scope_id,
                field_key=field_key,
                label=label,
                field_type=field_type,
                required=required,
                options=opts,
            )
        except (ValueError, json.JSONDecodeError) as e:
            return json.dumps({"error": str(e)})
        return json.dumps(row, indent=2)

    @mcp.tool(name="list_requests", description="List service requests with optional status filter.")
    def list_requests(status: str | None = None) -> str:
        return json.dumps(req_svc.list_requests(status=status), indent=2)

    @mcp.tool(name="get_request", description="Get service request detail with RITMs.")
    def get_request(request_ref: str) -> str:
        d = req_svc.get_request_detail(request_ref)
        if not d:
            return json.dumps({"error": "not_found"})
        return json.dumps(d, indent=2)

    @mcp.tool(name="create_request", description="Create a draft service request; optional template id.")
    def create_request(
        actor_user_id: int = 1,
        name: str = "",
        description: str = "",
        request_template_id: int | None = None,
    ) -> str:
        try:
            snap = req_svc.create_request(
                requester_user_id=actor_user_id,
                name=name,
                description=description,
                request_template_id=request_template_id,
            )
        except ValueError as e:
            return json.dumps({"error": str(e)})
        return json.dumps(snap, indent=2)

    @mcp.tool(name="add_ritm", description="Add requested item to a draft request from template.")
    def add_ritm(
        request_ref: str,
        request_template_id: int | None = None,
        specifications_json: str = "{}",
        actor_user_id: int = 1,
    ) -> str:
        try:
            specs = json.loads(specifications_json or "{}")
            row = req_svc.add_ritm_to_request(
                request_ref,
                request_template_id=request_template_id,
                specifications=specs,
                actor_user_id=actor_user_id,
            )
        except (ValueError, json.JSONDecodeError) as e:
            return json.dumps({"error": str(e)})
        return json.dumps(row, indent=2)

    @mcp.tool(name="submit_request", description="Submit request; auto-creates CHG and CTASKs from catalog.")
    def submit_request(request_ref: str, actor_user_id: int = 1) -> str:
        try:
            snap = wf_svc.submit_request(request_ref, actor_user_id)
        except ValueError as e:
            return json.dumps({"error": str(e)})
        return json.dumps(snap, indent=2)

    @mcp.tool(name="cancel_request", description="Cancel a service request.")
    def cancel_request(request_ref: str, actor_user_id: int = 1) -> str:
        try:
            snap = req_svc.cancel_request(request_ref, actor_user_id)
        except ValueError as e:
            return json.dumps({"error": str(e)})
        return json.dumps(snap, indent=2)

    @mcp.tool(name="list_changes", description="List change requests.")
    def list_changes(status: str | None = None) -> str:
        return json.dumps(chg_svc.list_changes(status=status), indent=2)

    @mcp.tool(name="create_change", description="Create change from standard change template.")
    def create_change(change_template_id: int, actor_user_id: int = 1, custom_fields_json: str = "{}") -> str:
        try:
            cf = json.loads(custom_fields_json or "{}")
            snap = chg_svc.create_change_from_template(
                change_template_id=change_template_id,
                custom_fields=cf,
                actor_user_id=actor_user_id,
            )
            if snap.get("change_type") == "standard":
                snap = chg_svc.auto_approve_change(snap["id"], actor_user_id)
        except (ValueError, json.JSONDecodeError) as e:
            return json.dumps({"error": str(e)})
        return json.dumps(snap, indent=2)

    @mcp.tool(name="get_change", description="Get change detail with CTASKs.")
    def get_change(change_ref: str) -> str:
        d = chg_svc.get_change_detail(change_ref)
        if not d:
            return json.dumps({"error": "not_found"})
        return json.dumps(d, indent=2)

    @mcp.tool(name="list_tasks", description="List change tasks.")
    def list_tasks_mcp(status: str | None = None, open_only: bool = True) -> str:
        return json.dumps(task_svc.list_tasks(status=status, open_only=open_only), indent=2)

    @mcp.tool(name="get_task", description="Get task detail by ref.")
    def get_task(task_ref: str) -> str:
        d = task_svc.get_task_detail(task_ref)
        if not d:
            return json.dumps({"error": "not_found"})
        return json.dumps(d, indent=2)

    @mcp.tool(name="create_task", description="Create task, optionally linked to a change.")
    def create_task_mcp(
        title: str = "",
        change_ref: str | None = None,
        task_template_id: int | None = None,
        actor_user_id: int = 1,
    ) -> str:
        try:
            row = task_svc.create_task(
                change_ref=change_ref,
                title=title,
                task_template_id=task_template_id,
                actor_user_id=actor_user_id,
            )
        except ValueError as e:
            return json.dumps({"error": str(e)})
        return json.dumps(row, indent=2)

    @mcp.tool(name="approve_change", description="Approve a pending Normal change (admin).")
    def approve_change(change_ref: str, actor_user_id: int = 1) -> str:
        try:
            snap = chg_svc.approve_change(change_ref, actor_user_id)
        except ValueError as e:
            return json.dumps({"error": str(e)})
        return json.dumps(snap, indent=2)

    @mcp.tool(name="start_ctask", description="Start a pending change task.")
    def start_ctask(change_ref: str, ctask_ref: str, actor_user_id: int = 1) -> str:
        try:
            snap = chg_svc.start_ctask(change_ref, ctask_ref, actor_user_id)
        except ValueError as e:
            return json.dumps({"error": str(e)})
        return json.dumps(snap, indent=2)

    @mcp.tool(name="complete_ctask", description="Complete change task; fulfills RITM when all tasks done.")
    def complete_ctask(
        change_ref: str,
        ctask_ref: str,
        completion_comment: str = "",
        actor_user_id: int = 1,
    ) -> str:
        try:
            snap = wf_svc.on_ctask_completed(
                change_ref, ctask_ref, actor_user_id, completion_comment=completion_comment
            )
        except ValueError as e:
            return json.dumps({"error": str(e)})
        return json.dumps(snap, indent=2)

    @mcp.resource("request-templates://catalog")
    def request_templates_resource() -> str:
        return json.dumps(rtpl_svc.list_request_templates(), indent=2)

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

    @mcp.resource("assets://catalog")
    def assets_catalog() -> str:
        return json.dumps(inv_svc.list_inventory(), indent=2)

    @mcp.resource("assets://item/{item_id}")
    def assets_item_resource(item_id: int) -> str:
        row = inv_svc.get_item(item_id)
        if not row:
            return json.dumps({"error": "not_found"})
        return json.dumps(row, indent=2)

    return mcp


def _normalize_mcp_token(value: str) -> str:
    """Strip whitespace and one pair of surrounding quotes (common shell copy-paste mistake)."""
    s = value.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ('"', "'"):
        return s[1:-1].strip()
    return s


def asgi_with_optional_mcp_auth(inner: Any, token: str | None) -> Any:
    """Wrap MCP Starlette app with optional bearer / X-ITSM-MCP-Token check."""

    class MCPAuthASGI:
        __slots__ = ("app", "token")

        def __init__(self, app: Any, tok: str | None) -> None:
            self.app = app
            self.token = tok.strip() if tok else None

        async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
            if scope["type"] != "http":
                await self.app(scope, receive, send)
                return
            if self.token:
                raw = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
                header_tok = _normalize_mcp_token(raw.get("x-itsm-mcp-token", ""))
                auth = raw.get("authorization", "")
                bearer = ""
                if auth.lower().startswith("bearer "):
                    bearer = _normalize_mcp_token(auth[7:])
                if header_tok != self.token and bearer != self.token:
                    from starlette.responses import JSONResponse

                    resp = JSONResponse(
                        {
                            "error": "invalid_token",
                            "error_description": "Missing or wrong MCP token (X-ITSM-MCP-Token or Bearer).",
                        },
                        status_code=401,
                    )
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
