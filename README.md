# ITSM demo — lightweight ticketing

Single-process FastAPI app with SQLite: **incidents** (comments, severity changes, optional inventory link, optional **KB article as resolution when closing**), **Knowledge Base**, **asset types** and **inventory**, **custom app title** (Settings), **users and RBAC**, **global outbound webhooks**, REST (`/api/v1`), OpenAPI (`/docs`), and MCP Streamable HTTP at `/mcp`.

## Features


| Area                 | Description                                                                                                                                                                                                                                                         |
| -------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Incidents            | Create, filter, comment, change severity, link/unlink inventory asset, close.                                                                                                                                                                                       |
| Resolution on close  | Optionally choose a **Knowledge Base article** when closing a ticket (UI, API, MCP). Stored as `resolution_kb_article_id`; webhooks include `resolution_kb_article` in the snapshot.                                                                                |
| SLA (closed tickets) | Resolution time vs targets by severity: critical 1h, high 4h, medium 1d, low 2d. Duration uses the **created** audit event as open time (actual filing time), not only `incidents.created_at`, which may be midnight UTC for the chosen calendar day from the form. |
| Knowledge Base       | Articles for documentation and linking from closed incidents. Optional **semantic search** via MCP `rag_search_kb` when `ITSM_EMBEDDING_*` is set (OpenAI-compatible `/v1/embeddings`); new and updated articles are indexed automatically. Run `scripts/reindex_kb_embeddings.py` once to backfill existing rows. |
| Inventory            | Hostname, IP, group, asset type; both roles may manage.                                                                                                                                                                                                             |
| Asset types          | Catalog for inventory classification; **any authenticated user** may create, edit, or delete types (same as inventory).                                                                                                                                             |
| Settings             | Admin: **branding** (title, built-in or custom logotype, sidebar colors with Navy/Slate/Forest/Wine/Bronze/Light presets) in `app_settings` and optional uploads under `app/static/uploads/branding/`. API: `GET`/`PATCH` `/api/v1/settings/branding`, `POST` `.../logo` (multipart), `DELETE` `.../logo` | `.../colors` | `.../branding` (204, no body on deletes). |
| Webhooks             | Multiple outbound URLs stored in `outbound_webhooks`; **GET** list readable by any authenticated user; **POST** / **PATCH** / **DELETE** admin-only (UI under Webhook config).                                                                                                                                                       |
| Users                | Admin CRUD; cannot remove/demote the last administrator (guards in UI and API).                                                                                                                                                                                     |
| MCP                  | Tools for incidents, KB (including `rag_search_kb` semantic search when embeddings are configured), asset types, inventory; optional bearer token (no per-user RBAC inside MCP—mirror REST credentials when auditing matters).                                                                                                                 |


## Roles


| Area                                  | Admin | User |
| ------------------------------------- | ----- | ---- |
| Incidents, KB, Inventory, Asset types | Full  | Full |
| Settings (title, branding)            | Yes   | —    |
| Webhooks (list)                       | Yes   | Yes  |
| Webhooks (create / update / delete)    | Yes   | —    |
| Users CRUD                            | Yes   | —    |


## Environment variables


| Variable                                                      | Purpose                                                                      |
| ------------------------------------------------------------- | ---------------------------------------------------------------------------- |
| `SESSION_SECRET`                                              | Secret for signed browser sessions (required in production).                 |
| `ITSM_DATABASE`                                               | SQLite path (default: `./data/itsm.db`).                                     |
| `ITSM_BOOTSTRAP_ADMIN_USER` / `ITSM_BOOTSTRAP_ADMIN_PASSWORD` | First admin when the DB has zero users.                                      |
| `ITSM_BOOTSTRAP_ADMIN`                                        | Alternative: `username:password` single string.                              |
| `MCP_TOKEN`                                                   | Optional shared secret (you define the value). If set, MCP requires `X-ITSM-MCP-Token` or `Authorization: Bearer`. See [MCP token](#mcp-token-create-and-configure). |
| `MCP_ALLOWED_HOSTS`                                           | Optional comma-separated `Host` values for MCP DNS rebinding protection. **Unset by default** so MCP works behind OpenShift/ingress with a public hostname. Set only if you need strict host allowlists. |
| `ITSM_EMBEDDING_BASE_URL`                                     | Origin of an OpenAI-compatible API (e.g. `https://llamastack.example.com`, no path); the app POSTs to `{BASE}/v1/embeddings`. Used for MCP `rag_search_kb` and automatic KB indexing on create/update. |
| `ITSM_EMBEDDING_MODEL`                                        | Embedding model id required when using RAG (with `ITSM_EMBEDDING_BASE_URL`). |
| `ITSM_EMBEDDING_API_KEY`                                      | Optional `Bearer` token for the embeddings API. |

### KB semantic search (RAG)

Set `ITSM_EMBEDDING_BASE_URL` and `ITSM_EMBEDDING_MODEL` (and `ITSM_EMBEDDING_API_KEY` if your gateway requires it). Embeddings are stored in SQLite (`kb_article_embeddings`) and updated when articles are created or edited; deleting an article removes its row via foreign-key cascade.

After enabling embeddings, **backfill** articles that already existed:

```bash
export ITSM_DATABASE="$PWD/data/itsm.db"
export ITSM_EMBEDDING_BASE_URL="https://your-llamastack-host"
export ITSM_EMBEDDING_MODEL="your-embedding-model"
python scripts/reindex_kb_embeddings.py
```

Use MCP tool **`rag_search_kb`** for natural-language queries; **`search_kb`** remains substring search on title and description.

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


| Method | Path                                 | Access                                                                                                    |
| ------ | ------------------------------------ | --------------------------------------------------------------------------------------------------------- |
| GET    | `/settings/app`                      | Authenticated                                                                                             |
| PUT    | `/settings/app`                      | Admin                                                                                                     |
| GET    | `/settings/webhooks`                 | Authenticated                                                                                             |
| POST   | `/settings/webhooks`                 | Admin                                                                                                     |
| PATCH  | `/settings/webhooks/{webhook_id}`    | Admin                                                                                                     |
| DELETE | `/settings/webhooks/{webhook_id}`    | Admin                                                                                                     |
| GET    | `/incidents`                         | Authenticated                                                                                             |
| POST   | `/incidents`                         | Authenticated                                                                                             |
| GET    | `/incidents/{incident_ref}`          | Authenticated                                                                                             |
| PATCH  | `/incidents/{incident_ref}`          | Authenticated                                                                                             |
| POST   | `/incidents/{incident_ref}/comments` | Authenticated                                                                                             |
| POST   | `/incidents/{incident_ref}/close`    | Authenticated — optional JSON body `{ "kb_article_id": <int> | null }` to link a KB article as resolution |
| GET    | `/kb/articles`                       | Authenticated                                                                                             |
| POST   | `/kb/articles`                       | Authenticated                                                                                             |
| GET    | `/kb/articles/{article_id}`          | Authenticated                                                                                             |
| PATCH  | `/kb/articles/{article_id}`          | Authenticated                                                                                             |
| DELETE | `/kb/articles/{article_id}`          | Authenticated                                                                                             |
| GET    | `/users`                             | Admin                                                                                                     |
| POST   | `/users`                             | Admin                                                                                                     |
| PATCH  | `/users/{user_id}`                   | Admin                                                                                                     |
| DELETE | `/users/{user_id}`                   | Admin                                                                                                     |
| GET    | `/asset-types`                       | Authenticated                                                                                             |
| POST   | `/asset-types`                       | Authenticated                                                                                             |
| PATCH  | `/asset-types/{type_id}`             | Authenticated                                                                                             |
| DELETE | `/asset-types/{type_id}`             | Authenticated                                                                                             |
| GET    | `/inventory`                         | Authenticated                                                                                             |
| POST   | `/inventory`                         | Authenticated                                                                                             |
| GET    | `/inventory/{item_id}`               | Authenticated                                                                                             |
| PATCH  | `/inventory/{item_id}`               | Authenticated                                                                                             |
| DELETE | `/inventory/{item_id}`               | Authenticated                                                                                             |


Full schemas and try-it-out: `**/docs`**.

## Webhook payload

When one or more webhook URLs are enabled, incident changes trigger a `POST` to each destination with JSON including `event` (e.g. `incident.closed`), `timestamp`, `actor`, and `incident` (snapshot with `linked_asset`, `resolution_kb_article`, `comments` when applicable).

## MCP (Streamable HTTP)

- **KB RAG:** With `ITSM_EMBEDDING_BASE_URL` and `ITSM_EMBEDDING_MODEL` set, tool **`rag_search_kb`** runs semantic retrieval over indexed articles. Prefer it over **`search_kb`** for paraphrased or conceptual questions.
- **URL:** `{base URL}/mcp/` (trailing slash avoids redirect issues with some HTTP clients.)
- **Auth:** If `MCP_TOKEN` is set, send **`X-ITSM-MCP-Token: <token>`** or **`Authorization: Bearer <token>`**. Wrong or missing token → **401** with OAuth-style JSON. A **404** usually means the URL or route is wrong, not the token.
- **OpenShift / ingress:** The MCP library’s DNS rebinding check defaults to localhost-only and causes **421 Misdirected Request / Invalid Host header** when a valid token reaches the app. This repo disables that unless **`MCP_ALLOWED_HOSTS`** is set (see env table).
- **Cursor / MCP authorization discovery:** The MCP spec requires **OAuth Protected Resource Metadata** (RFC 9728). Clients typically call **`/.well-known/oauth-protected-resource/mcp`** first (aligned with the **`/mcp`** mount). This app serves valid **200** metadata documents there and at **`/.well-known/oauth-authorization-server`**, plus stub **`/oauth/*`** endpoints so discovery does not end in **404 Not Found**. Real access control for this deployment is still **optional `MCP_TOKEN`** and the **`X-ITSM-MCP-Token`** header in Cursor.

### MCP token (create and configure)

The app does **not** issue tokens over HTTP. You choose a long random string and use it as the shared secret.

1. **Generate a value** (example):

   ```bash
   openssl rand -hex 32
   ```

2. **Local / Podman:** export it before starting the app:

   ```bash
   export MCP_TOKEN="<paste-the-generated-string>"
   ```

   Omit `MCP_TOKEN` entirely if you want MCP open without a header (development only).

3. **OpenShift / Kubernetes:** put the **same** string in the cluster secret as `mcp-token` (see [k8s/secret.yaml](k8s/secret.yaml)). The Deployment maps that key to env `MCP_TOKEN`. After changing the secret:

   ```bash
   oc apply -f k8s/secret.yaml   # or patch itsm-secrets
   oc rollout restart deployment/itsm-app -n itsm-app
   ```

4. **Read the token currently deployed** (to configure a client without rotation):

   ```bash
   oc get secret itsm-secrets -n itsm-app -o jsonpath='{.data.mcp-token}' | base64 -d; echo
   ```

5. **Cursor (and other MCP clients):** the token must be available in **the environment of the Cursor process** (not only in a terminal). Remote MCP entries cannot use `envFile`; `${env:ITSM_MCP_TOKEN}` is resolved when Cursor starts. Example `.cursor/mcp.json`:

   ```json
   "headers": {
     "X-ITSM-MCP-Token": "${env:ITSM_MCP_TOKEN}",
     "Authorization": "Bearer ${env:ITSM_MCP_TOKEN}"
   }
   ```

   **If tools never connect:** confirm the server accepts your token:  
   `curl -sS -H "Authorization: Bearer $(oc get secret itsm-secrets -n itsm-app -o jsonpath='{.data.mcp-token}' | base64 -d)" -H "Content-Type: application/json" -H "Accept: application/json" -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"t","version":"0"}}}' "https://<route>/mcp/"`  
   should return HTTP **200** JSON with `serverInfo`. If that works but Cursor does not, **`ITSM_MCP_TOKEN` is not set for the IDE** — e.g. launch Cursor from a shell where you exported it (`ITSM_MCP_TOKEN=... cursor /path/to/project`), or define it in your desktop session (systemd `environment.d`, GNOME session env, etc.), then **fully restart Cursor**.

6. **Quick check with curl (OpenShift):** do **not** add extra quotes around the token; the header value must be the raw secret only.

   ```bash
   TOKEN=$(oc get secret itsm-secrets -n itsm-app -o jsonpath='{.data.mcp-token}' | base64 -d)
   curl -sS -H "X-ITSM-MCP-Token: ${TOKEN}" "https://<route-host>/mcp/"
   ```

   A **401** `invalid_token` with a correct secret in the cluster usually means the shell sent quotes inside the header (e.g. `-H "X-ITSM-MCP-Token: \"${TOKEN}\""` is wrong). The app also accepts a single pair of surrounding quotes on the token for copy-paste mistakes.

### Tools


| Tool                    | Description                                      |
| ----------------------- | ------------------------------------------------ |
| `list_incidents`        | Optional `status`, `severity` filters.           |
| `get_incident`          | Full detail by `incident_ref`.                   |
| `create_incident`       | Optional `inventory_asset_id`.                   |
| `add_comment`           | Comment on open incident.                        |
| `update_severity`       | Severity change.                                 |
| `close_incident`        | Optional `kb_article_id` for resolution KB link. |
| `list_kb_articles`      | Optional query string.                           |
| `search_kb`             | Substring search in KB.                          |
| `get_kb_article`        | By id.                                           |
| `create_kb_article`     | Create article (`title`, `description`).         |
| `list_asset_types`      | —                                                |
| `create_asset_type`     | —                                                |
| `update_asset_type`     | —                                                |
| `delete_asset_type`     | —                                                |
| `list_inventory`        | Optional search substring.                       |
| `get_inventory_item`    | By id.                                           |
| `create_inventory_item` | —                                                |
| `update_inventory_item` | —                                                |
| `delete_inventory_item` | —                                                |


### Resources


| URI                          | Content                      |
| ---------------------------- | ---------------------------- |
| `kb://catalog`               | JSON list of KB articles.    |
| `kb://article/{article_id}`  | Single article JSON.         |
| `inventory://catalog`        | JSON list of inventory rows. |
| `inventory://item/{item_id}` | Single inventory row JSON.   |


## Testing (MCP)

Install dev dependencies and run pytest from the repo root:

```bash
pip install -r requirements-dev.txt
pytest tests/ -v
```

Tests use a temporary SQLite file (see `tests/conftest.py`) and exercise Streamable HTTP JSON-RPC (`initialize`, `tools/list`, `tools/call`) plus OAuth discovery metadata and optional `MCP_TOKEN` rejection.

## Health

`GET /healthz` — returns `{"status":"ok"}` (liveness/readiness).