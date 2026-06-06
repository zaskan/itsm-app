"""HTTP client and OpenShift discovery for populate_fake_data.py."""

from __future__ import annotations

import base64
import json
import os
import subprocess
from typing import Any

import httpx


class PopulateError(RuntimeError):
    pass


def _run_oc(args: list[str], *, namespace: str | None = None) -> str:
    cmd = ["oc", *args]
    if namespace:
        cmd.extend(["-n", namespace])
    try:
        return subprocess.check_output(cmd, stderr=subprocess.PIPE, text=True).strip()
    except FileNotFoundError as e:
        raise PopulateError(
            "OpenShift CLI (oc) not found. Install oc, log in with `oc login`, "
            "or pass --base-url and --user/--password."
        ) from e
    except subprocess.CalledProcessError as e:
        detail = (e.stderr or e.stdout or "").strip()
        raise PopulateError(f"oc command failed: {' '.join(cmd)}\n{detail}") from e


def openshift_current_namespace(fallback: str = "itsm-app") -> str:
    return discover_openshift_namespace()


def openshift_route_host(
    namespace: str,
    route_name: str = "itsm-app",
) -> str:
    name = os.environ.get("ITSM_OPENSHIFT_ROUTE", route_name).strip() or route_name
    return _run_oc(
        ["get", "route", name, "-o", "jsonpath={.spec.host}"],
        namespace=namespace,
    )


def discover_openshift_namespace(route_name: str = "itsm-app") -> str:
    """Prefer explicit env, then current ``oc project``, then namespace ``itsm-app``."""
    env_ns = os.environ.get("ITSM_OPENSHIFT_NAMESPACE", "").strip()
    if env_ns:
        return env_ns
    candidates: list[str] = []
    try:
        current = _run_oc(["project", "-q"])
        if current:
            candidates.append(current)
    except PopulateError:
        pass
    if "itsm-app" not in candidates:
        candidates.append("itsm-app")
    for ns in candidates:
        try:
            host = openshift_route_host(ns, route_name)
            if host:
                return ns
        except PopulateError:
            continue
    return candidates[0] if candidates else "itsm-app"


def openshift_secret_value(namespace: str, secret: str, key: str) -> str:
    b64 = _run_oc(
        [
            "get",
            "secret",
            secret,
            "-o",
            f"jsonpath={{.data.{key}}}",
        ],
        namespace=namespace,
    )
    if not b64:
        return ""
    return base64.b64decode(b64).decode("utf-8")


def resolve_api_base_url(
    *,
    base_url: str | None = None,
    namespace: str | None = None,
    route_name: str = "itsm-app",
) -> str:
    explicit = (base_url or os.environ.get("ITSM_BASE_URL", "")).strip().rstrip("/")
    if explicit:
        return explicit

    ns = namespace or openshift_current_namespace()
    host = openshift_route_host(ns, route_name)
    if not host:
        raise PopulateError(
            f"No host on Route '{route_name}' in namespace '{ns}'. "
            "Set ITSM_BASE_URL or pass --base-url."
        )
    scheme = os.environ.get("ITSM_BASE_URL_SCHEME", "https").strip() or "https"
    return f"{scheme}://{host}"


def resolve_api_credentials(
    *,
    username: str | None = None,
    password: str | None = None,
    namespace: str | None = None,
    secret_name: str = "itsm-secrets",
) -> tuple[str, str]:
    user = (username or os.environ.get("ITSM_API_USER", "")).strip()
    pw = password if password is not None else os.environ.get("ITSM_API_PASSWORD", "")

    if user and pw:
        return user, pw

    ns = namespace or openshift_current_namespace()
    sec = os.environ.get("ITSM_OPENSHIFT_SECRET", secret_name).strip() or secret_name
    try:
        if not user:
            user = openshift_secret_value(ns, sec, "bootstrap-admin-user")
        if not pw:
            pw = openshift_secret_value(ns, sec, "bootstrap-admin-password")
    except PopulateError:
        pass

    if not user or not pw:
        raise PopulateError(
            "API credentials required. Set ITSM_API_USER and ITSM_API_PASSWORD, "
            f"or ensure secret '{sec}' exists in namespace '{ns}' with bootstrap-admin-* keys."
        )
    return user, pw


class RemotePopulateBackend:
    """Populate data through the running ITSM REST API (/api/v1)."""

    def __init__(self, base_url: str, username: str, password: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_root = f"{self.base_url}/api/v1"
        self._client = httpx.Client(
            auth=(username, password),
            timeout=httpx.Timeout(60.0, connect=15.0),
            headers={"Accept": "application/json"},
            follow_redirects=True,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> RemotePopulateBackend:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _url(self, path: str) -> str:
        if not path.startswith("/"):
            path = f"/{path}"
        return f"{self.api_root}{path}"

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        resp = self._client.request(method, self._url(path), **kwargs)
        if resp.status_code >= 400:
            detail = resp.text.strip()
            try:
                detail = json.dumps(resp.json())
            except Exception:
                pass
            raise PopulateError(f"{method} {path} → {resp.status_code}: {detail}")
        if resp.status_code == 204 or not resp.content:
            return None
        return resp.json()

    def verify(self) -> dict[str, Any]:
        return self._request("GET", "/settings/app")

    def list_users(self) -> list[dict[str, Any]]:
        return self._request("GET", "/users")

    def create_user(self, username: str, password: str, role: str) -> dict[str, Any]:
        return self._request(
            "POST",
            "/users",
            json={"username": username, "password": password, "role": role},
        )

    def list_asset_types(self) -> list[dict[str, Any]]:
        return self._request("GET", "/asset-types")

    def create_asset_type(self, name: str, description: str) -> dict[str, Any]:
        return self._request(
            "POST",
            "/asset-types",
            json={"name": name, "description": description},
        )

    def create_asset(
        self,
        *,
        name: str,
        description: str,
        asset_type_id: int | None = None,
        parent_asset_id: int | None = None,
        assigned_user_id: int | None = None,
        external_inventory: bool = False,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "name": name,
            "description": description,
            "external_inventory": external_inventory,
        }
        if asset_type_id is not None:
            body["asset_type_id"] = asset_type_id
        if parent_asset_id is not None:
            body["parent_asset_id"] = parent_asset_id
        if assigned_user_id is not None:
            body["assigned_user_id"] = assigned_user_id
        return self._request("POST", "/assets", json=body)

    def create_kb_article(self, title: str, description: str) -> dict[str, Any]:
        return self._request(
            "POST",
            "/kb/articles",
            json={"title": title, "description": description},
        )

    def create_incident(
        self,
        *,
        title: str,
        description: str,
        severity: str,
        inventory_asset_id: int | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "title": title,
            "description": description,
            "severity": severity,
        }
        if inventory_asset_id is not None:
            body["inventory_asset_id"] = inventory_asset_id
        return self._request("POST", "/incidents", json=body)

    def add_comment(self, incident_ref: str, body: str) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/incidents/{incident_ref}/comments",
            json={"body": body},
        )

    def close_incident(
        self,
        incident_ref: str,
        *,
        kb_article_id: int | None = None,
    ) -> dict[str, Any]:
        payload = {"kb_article_id": kb_article_id} if kb_article_id else {}
        return self._request(
            "POST",
            f"/incidents/{incident_ref}/close",
            json=payload,
        )

    def list_request_templates(self) -> list[dict[str, Any]]:
        return self._request("GET", "/request-templates")

    def create_task_template(
        self,
        *,
        name: str,
        title: str,
        description: str,
        assigned_user_id: int | None = None,
        kb_article_id: int | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "name": name,
            "title": title,
            "description": description,
        }
        if assigned_user_id is not None:
            body["assigned_user_id"] = assigned_user_id
        if kb_article_id is not None:
            body["kb_article_id"] = kb_article_id
        return self._request("POST", "/task-templates", json=body)

    def create_change_template(
        self,
        *,
        name: str,
        description: str,
        change_type: str,
        task_template_ids: list[int],
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/change-templates",
            json={
                "name": name,
                "description": description,
                "change_type": change_type,
                "task_template_ids": task_template_ids,
            },
        )

    def create_request_template(
        self,
        *,
        name: str,
        description: str,
        change_template_id: int,
        require_standard_change: bool = True,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/request-templates",
            json={
                "name": name,
                "description": description,
                "change_template_id": change_template_id,
                "require_standard_change": require_standard_change,
            },
        )

    def create_request_template_field(
        self,
        template_id: int,
        *,
        field_key: str,
        label: str,
        field_type: str,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/request-templates/{template_id}/fields",
            json={
                "field_key": field_key,
                "label": label,
                "field_type": field_type,
                "required": False,
                "options": [],
                "sort_order": 0,
            },
        )

    def create_request(
        self,
        *,
        name: str,
        description: str,
        request_template_id: int | None = None,
        specifications: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"name": name, "description": description}
        if request_template_id is not None:
            body["request_template_id"] = request_template_id
        if specifications:
            body["specifications"] = specifications
        return self._request("POST", "/requests", json=body)

    def submit_request(self, request_ref: str) -> dict[str, Any]:
        return self._request("POST", f"/requests/{request_ref}/submit")

    def list_change_templates(self) -> list[dict[str, Any]]:
        return self._request("GET", "/change-templates")

    def create_change(self, *, change_template_id: int) -> dict[str, Any]:
        return self._request(
            "POST",
            "/changes",
            json={"change_template_id": change_template_id, "custom_fields": {}},
        )

    def get_change(self, change_ref: str) -> dict[str, Any]:
        return self._request("GET", f"/changes/{change_ref}")

    def start_task(self, change_ref: str, task_ref: str) -> dict[str, Any]:
        return self._request("POST", f"/changes/{change_ref}/tasks/{task_ref}/start")

    def complete_task(self, change_ref: str, task_ref: str) -> dict[str, Any]:
        return self._request("POST", f"/changes/{change_ref}/tasks/{task_ref}/complete")


def connect_openshift_backend(
    *,
    base_url: str | None = None,
    namespace: str | None = None,
    username: str | None = None,
    password: str | None = None,
) -> RemotePopulateBackend:
    ns = namespace or openshift_current_namespace()
    url = resolve_api_base_url(base_url=base_url, namespace=ns)
    user, pw = resolve_api_credentials(username=username, password=password, namespace=ns)
    backend = RemotePopulateBackend(url, user, pw)
    try:
        app = backend.verify()
    except PopulateError as e:
        raise PopulateError(
            f"Cannot reach ITSM API at {url} (namespace {ns}): {e}"
        ) from e
    print(f"Connected to {url} (app title: {app.get('app_title', 'ITSM')})")
    return backend
