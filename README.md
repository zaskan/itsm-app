# ITSM demo — lightweight ticketing

Single-process FastAPI app with SQLite: incidents (comments, severity, close, optional inventory link), outbound webhooks, Knowledge Base, customizable app title (Settings), users (RBAC), asset types and inventory, REST (`/api/v1`), OpenAPI (`/docs`), and MCP Streamable HTTP at `/mcp`.

## Roles

| Area | Admin | User |
|------|-------|------|
| Incidents, KB, Inventory | Full | Full |
| Settings (app title) | Yes | — |
| Webhook URL (read) | Yes | Yes |
| Webhook URL (write) | Yes | — |
| Users CRUD | Yes | — |
| Asset types | Yes | — |

## First user (bootstrap)

When the database has **no** users, the app creates one **admin** from the environment (then you manage users in the UI or API):

- `ITSM_BOOTSTRAP_ADMIN_USER` and `ITSM_BOOTSTRAP_ADMIN_PASSWORD`, or
- `ITSM_BOOTSTRAP_ADMIN=username:password`

If the database already has users, these variables are ignored.

## Run locally

```bash
cd /path/to/itsm-app
pip install -r requirements.txt
export SESSION_SECRET="dev-secret"
export MCP_TOKEN=""                        # optional; if set, requires header on /mcp
export ITSM_DATABASE="$PWD/data/itsm.db"   # default under ./data
export ITSM_BOOTSTRAP_ADMIN_USER=admin
export ITSM_BOOTSTRAP_ADMIN_PASSWORD=admin
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

- UI: `http://127.0.0.1:8000/incidents` — sign in with the bootstrapped admin (or any account created by an admin).
- API: HTTP Basic with the same credentials, e.g. `curl -u admin:admin http://127.0.0.1:8000/api/v1/incidents`.

### Useful endpoints

- `GET/PUT /api/v1/settings/app` — app title (`GET`: any authenticated user; `PUT`: admin).
- `GET/PUT /api/v1/settings/webhook` — webhook URL (`GET`: any authenticated user; `PUT`: admin).
- `GET/POST/PATCH/DELETE /api/v1/users` — admin only (last admin cannot be demoted or removed in ways that leave zero admins).
- `GET /api/v1/asset-types` — any authenticated user; `POST/PATCH/DELETE` — admin.
- `GET/POST/PATCH/DELETE /api/v1/inventory` — any authenticated user.

## Webhook payload

When **Webhook URL** is set (admin UI or `PUT /api/v1/settings/webhook`), incident changes trigger `POST` with JSON:

- `event`: `incident.created`, `incident.comment_added`, `incident.severity_changed`, `incident.closed`, `incident.asset_linked`, …
- `timestamp`: ISO8601 UTC
- `actor`: username
- `incident`: snapshot including `linked_asset` when applicable

Failures are logged only; the database change is not rolled back.

## MCP (Streamable HTTP)

- Endpoint: **`/mcp`** (mounted ASGI; use Streamable HTTP client against your Route/URL + `/mcp`).
- If **`MCP_TOKEN`** is set, send either header **`X-ITSM-MCP-Token: <token>`** or **`Authorization: Bearer <token>`**.

Tools include incidents, KB, asset types, and inventory; resources include `kb://catalog`, `kb://article/{article_id}`, `inventory://catalog`, `inventory://item/{item_id}`.

## Container image

```bash
docker build -t itsm-app:latest .
```

## Kubernetes / OpenShift

Apply manifests (adjust **Secret** strings and **image** in `deployment.yaml` if using a registry):

```bash
kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/secret.yaml
kubectl apply -f k8s/deployment.yaml
kubectl apply -f k8s/service.yaml
```

`k8s/secret.yaml` includes optional `bootstrap-admin-user` and `bootstrap-admin-password` for the first start with an empty database (see `k8s/deployment.yaml` env).

OpenShift (HTTP(S) edge route to the same Service; serves UI, API, and `/mcp`):

```bash
oc apply -f k8s/namespace.yaml
oc apply -f k8s/secret.yaml
# edit k8s/deployment.yaml image to your registry image
oc apply -f k8s/deployment.yaml
oc apply -f k8s/service.yaml
oc apply -f k8s/route.yaml
```

Namespace used in manifests: **`itsm-app`**.

`Route` is OpenShift-specific. For plain Kubernetes, add an **Ingress** (or your platform’s equivalent) pointing to Service `itsm-app` port `8000`.

## Health

`GET /healthz` — liveness/readiness.
