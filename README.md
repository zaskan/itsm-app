# ITSM demo — lightweight ticketing

Single-process FastAPI app with SQLite: incidents (comments, severity, close), outbound webhooks, Knowledge Base, REST (`/api/v1`), OpenAPI (`/docs`), and MCP Streamable HTTP at `/mcp`.

## Run locally

```bash
cd /path/to/itsm-app
pip install -r requirements.txt
export SESSION_SECRET="dev-secret"
export MCP_TOKEN=""                        # optional; if set, requires header on /mcp
export ITSM_CONFIG="$PWD/users.yaml"       # default if omitted
export ITSM_DATABASE="$PWD/data/itsm.db"   # default under ./data
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

- UI: `http://127.0.0.1:8000/incidents` — sign in (`admin` / `admin` or `ansible` / `ansible` per `users.yaml`).
- API: HTTP Basic with the same credentials, e.g. `curl -u admin:admin http://127.0.0.1:8000/api/v1/incidents`.

## Webhook payload

When **Webhook URL** is set (UI or `PUT /api/v1/settings/webhook`), each incident change triggers `POST` with JSON:

- `event`: `incident.created`, `incident.comment_added`, `incident.severity_changed`, `incident.closed`
- `timestamp`: ISO8601 UTC
- `actor`: username
- `incident`: snapshot including `comments` where applicable

Failures are logged only; the database change is not rolled back.

## MCP (Streamable HTTP)

- Endpoint: **`/mcp`** (mounted ASGI; use Streamable HTTP client against your Route/URL + `/mcp`).
- If **`MCP_TOKEN`** is set, send either header **`X-ITSM-MCP-Token: <token>`** or **`Authorization: Bearer <token>`**.

Tools: `list_incidents`, `get_incident`, `create_incident`, `add_comment`, `update_severity`, `close_incident`, `list_kb_articles`, `search_kb`, `get_kb_article`. Resources: `kb://catalog`, `kb://article/{article_id}`.

## Container image

```bash
docker build -t itsm-app:latest .
```

## Kubernetes / OpenShift

Apply manifests (adjust **Secret** strings and **image** in `deployment.yaml` if using a registry):

```bash
kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/configmap.yaml
kubectl apply -f k8s/secret.yaml
kubectl apply -f k8s/deployment.yaml
kubectl apply -f k8s/service.yaml
```

OpenShift (HTTP(S) edge route to the same Service; serves UI, API, and `/mcp`):

```bash
oc apply -f k8s/namespace.yaml
oc apply -f k8s/configmap.yaml
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
