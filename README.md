# ITSM demo — lightweight ticketing

Single-process FastAPI app with SQLite: **incidents** (comments, severity changes, optional inventory link, optional **KB article as resolution when closing**), **Knowledge Base**, **asset types** and **inventory**, **custom app title** (Settings), **users and RBAC**, **global outbound webhooks**, REST (`/api/v1`), OpenAPI (`/docs`), and MCP Streamable HTTP at `/mcp`.

## Features

| Area | Description |
|------|-------------|
| Incidents | Create, filter, comment, change severity, link/unlink inventory asset, close. |
| Resolution on close | Optionally choose a **Knowledge Base article** when closing a ticket (UI, API, MCP). Stored as `resolution_kb_article_id`; webhooks include `resolution_kb_article` in the snapshot. |
| SLA (closed tickets) | Resolution time vs targets by severity: critical 1h, high 4h, medium 1d, low 2d. Duration uses the **created** audit event as open time (actual filing time), not only `incidents.created_at`, which may be midnight UTC for the chosen calendar day from the form. |
| Knowledge Base | Articles for documentation and linking from closed incidents. |
| Inventory | Hostname, IP, group, asset type; both roles may manage. |
| Asset types | Catalog for inventory classification; **any authenticated user** may create, edit, or delete types (same as inventory). |
| Settings | Admin: application title (`app_settings`). |
| Webhooks | Single global URL; **GET** readable by any authenticated user, **PUT** admin-only. |
| Users | Admin CRUD; cannot remove/demote the last administrator (guards in UI and API). |
| MCP | Tools for incidents, KB, asset types, inventory; optional bearer token (no per-user RBAC inside MCP—mirror REST credentials when auditing matters). |

## Roles

| Area | Admin | User |
|------|-------|------|
| Incidents, KB, Inventory, Asset types | Full | Full |
| Settings (app title) | Yes | — |
| Webhook URL (read) | Yes | Yes |
| Webhook URL (write) | Yes | — |
| Users CRUD | Yes | — |

## Environment variables

| Variable | Purpose |
|----------|---------|
| `SESSION_SECRET` | Secret for signed browser sessions (required in production). |
| `ITSM_DATABASE` | SQLite path (default: `./data/itsm.db`). |
| `ITSM_BOOTSTRAP_ADMIN_USER` / `ITSM_BOOTSTRAP_ADMIN_PASSWORD` | First admin when the DB has zero users. |
| `ITSM_BOOTSTRAP_ADMIN` | Alternative: `username:password` single string. |
| `MCP_TOKEN` | If set, MCP endpoint requires `X-ITSM-MCP-Token` or `Authorization: Bearer`. |

## Run locally (Python)

```bash
cd /path/to/itsm-app
pip install -r requirements.txt
export SESSION_SECRET="dev-secret"
export ITSM_DATABASE="$PWD/data/itsm.db"
export ITSM_BOOTSTRAP_ADMIN_USER=admin
export ITSM_BOOTSTRAP_ADMIN_PASSWORD=admin
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

- UI: `http://127.0.0.1:8000/login`
- API: HTTP Basic with the same credentials as UI users, e.g. `curl -u admin:admin http://127.0.0.1:8000/api/v1/incidents`

## Run with Podman

Build and run the same image as the `Dockerfile` (database under `/data` in the container):

```bash
cd /path/to/itsm-app
podman build -t itsm-app:latest .

podman run --rm -p 8000:8000 \
  -e SESSION_SECRET="your-long-secret" \
  -e ITSM_BOOTSTRAP_ADMIN_USER=admin \
  -e ITSM_BOOTSTRAP_ADMIN_PASSWORD=admin \
  -e ITSM_DATABASE=/data/itsm.db \
  -v itsm-data:/data \
  itsm-app:latest
```

Open `http://127.0.0.1:8000`. The named volume keeps SQLite across container restarts.

To push to a registry (for OpenShift or other clusters), tag and `podman push` to your repository, then set the image in `k8s/deployment.yaml` or `oc set image`.

## OpenShift installation

Prerequisites: `oc login` to your cluster, permission to create projects/resources.

1. **Project**

   ```bash
   oc new-project itsm-app
   ```

   Or: `oc apply -f k8s/namespace.yaml` then `oc project itsm-app`.

2. **Secrets**

   Edit [k8s/secret.yaml](k8s/secret.yaml): `session-secret`, `mcp-token`, and optionally `bootstrap-admin-user` / `bootstrap-admin-password` (used only when the database file has **no users** — typical first pod start with `emptyDir`).

   ```bash
   oc apply -f k8s/secret.yaml
   ```

3. **Build image in-cluster** (binary build from your workstation):

   ```bash
   cd /path/to/itsm-app
   oc new-build --name=itsm-app --binary --strategy=docker -l app=itsm-app
   oc start-build itsm-app --from-dir=. --follow
   ```

   Confirm the ImageStream name/tag (`oc get is -n itsm-app`). If it does not match [k8s/deployment.yaml](k8s/deployment.yaml) (`image-registry.openshift-image-registry.svc:5000/itsm-app/itsm-app:latest`), patch the Deployment image or use `oc tag` so the Deployment pulls your build.

4. **Deploy**

   ```bash
   oc apply -f k8s/deployment.yaml
   oc apply -f k8s/service.yaml
   oc apply -f k8s/route.yaml
   ```

5. **Verify**

   ```bash
   oc get pods -n itsm-app
   oc logs -n itsm-app -l app=itsm-app --tail=80
   oc get route -n itsm-app
   ```

**Notes**

- The sample Deployment uses **emptyDir** for `/data`; SQLite is ephemeral across node moves unless you add a PersistentVolumeClaim.
- Bootstrap env vars apply only when `users` count is zero; after that, manage users in the UI or API.

## Plain Kubernetes

Same manifests minus Route; use [k8s/ingress.example.yaml](k8s/ingress.example.yaml) or your ingress controller. Apply `namespace`, `secret`, `deployment`, `service`.

## REST API (`/api/v1`)

All routes require **HTTP Basic** authentication unless noted. **Admin** means `role == admin`.

| Method | Path | Access |
|--------|------|--------|
| GET | `/settings/app` | Authenticated |
| PUT | `/settings/app` | Admin |
| GET | `/settings/webhook` | Authenticated |
| PUT | `/settings/webhook` | Admin |
| GET | `/incidents` | Authenticated |
| POST | `/incidents` | Authenticated |
| GET | `/incidents/{incident_ref}` | Authenticated |
| PATCH | `/incidents/{incident_ref}` | Authenticated |
| POST | `/incidents/{incident_ref}/comments` | Authenticated |
| POST | `/incidents/{incident_ref}/close` | Authenticated — optional JSON body `{ "kb_article_id": <int> \| null }` to link a KB article as resolution |
| GET | `/kb/articles` | Authenticated |
| POST | `/kb/articles` | Authenticated |
| GET | `/kb/articles/{article_id}` | Authenticated |
| PATCH | `/kb/articles/{article_id}` | Authenticated |
| DELETE | `/kb/articles/{article_id}` | Authenticated |
| GET | `/users` | Admin |
| POST | `/users` | Admin |
| PATCH | `/users/{user_id}` | Admin |
| DELETE | `/users/{user_id}` | Admin |
| GET | `/asset-types` | Authenticated |
| POST | `/asset-types` | Authenticated |
| PATCH | `/asset-types/{type_id}` | Authenticated |
| DELETE | `/asset-types/{type_id}` | Authenticated |
| GET | `/inventory` | Authenticated |
| POST | `/inventory` | Authenticated |
| GET | `/inventory/{item_id}` | Authenticated |
| PATCH | `/inventory/{item_id}` | Authenticated |
| DELETE | `/inventory/{item_id}` | Authenticated |

Full schemas and try-it-out: **`/docs`**.

## Webhook payload

When a webhook URL is configured, incident changes trigger `POST` with JSON including `event` (e.g. `incident.closed`), `timestamp`, `actor`, and `incident` (snapshot with `linked_asset`, `resolution_kb_article`, `comments` when applicable).

## MCP (Streamable HTTP)

- **URL:** `{base URL}/mcp`
- **Auth:** If `MCP_TOKEN` is set, send **`X-ITSM-MCP-Token: <token>`** or **`Authorization: Bearer <token>`**.

### Tools

| Tool | Description |
|------|-------------|
| `list_incidents` | Optional `status`, `severity` filters. |
| `get_incident` | Full detail by `incident_ref`. |
| `create_incident` | Optional `inventory_asset_id`. |
| `add_comment` | Comment on open incident. |
| `update_severity` | Severity change. |
| `close_incident` | Optional `kb_article_id` for resolution KB link. |
| `list_kb_articles` | Optional query string. |
| `search_kb` | Substring search in KB. |
| `get_kb_article` | By id. |
| `list_asset_types` | — |
| `create_asset_type` | — |
| `update_asset_type` | — |
| `delete_asset_type` | — |
| `list_inventory` | Optional search substring. |
| `get_inventory_item` | By id. |
| `create_inventory_item` | — |
| `update_inventory_item` | — |
| `delete_inventory_item` | — |

### Resources

| URI | Content |
|-----|---------|
| `kb://catalog` | JSON list of KB articles. |
| `kb://article/{article_id}` | Single article JSON. |
| `inventory://catalog` | JSON list of inventory rows. |
| `inventory://item/{item_id}` | Single inventory row JSON. |

## Health

`GET /healthz` — returns `{"status":"ok"}` (liveness/readiness).
