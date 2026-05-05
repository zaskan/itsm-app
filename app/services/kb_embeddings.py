"""KB article embeddings via an OpenAI-compatible /v1/embeddings HTTP API (e.g. LlamaStack)."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

import httpx

from app import db

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _base_url() -> str:
    return os.environ.get("ITSM_EMBEDDING_BASE_URL", "").strip().rstrip("/")


def _api_key() -> str | None:
    k = os.environ.get("ITSM_EMBEDDING_API_KEY", "").strip()
    return k or None


def _model() -> str:
    return os.environ.get("ITSM_EMBEDDING_MODEL", "").strip()


def embeddings_configured() -> bool:
    return bool(_base_url() and _model())


def _embeddings_url() -> str:
    return f"{_base_url()}/v1/embeddings"


def fetch_embedding(text: str) -> list[float]:
    headers = {"Content-Type": "application/json"}
    key = _api_key()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    payload = {"model": _model(), "input": text}
    with httpx.Client(timeout=60.0) as client:
        r = client.post(_embeddings_url(), headers=headers, json=payload)
        r.raise_for_status()
        data = r.json()
    emb = data["data"][0]["embedding"]
    return [float(x) for x in emb]


def article_index_text(title: str, description: str) -> str:
    return f"Title: {title}\n\n{description}"


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def upsert_article_embedding(article_id: int, title: str, description: str) -> None:
    if not embeddings_configured():
        return
    text = article_index_text(title, description)
    try:
        vec = fetch_embedding(text)
    except Exception as e:
        logger.warning("KB embedding failed for article_id=%s: %s", article_id, e)
        return
    now = _now()
    model = _model()
    emb_json = json.dumps(vec)
    with db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO kb_article_embeddings (article_id, embedding, model, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(article_id) DO UPDATE SET
                embedding = excluded.embedding,
                model = excluded.model,
                updated_at = excluded.updated_at
            """,
            (article_id, emb_json, model, now),
        )


def rag_search_kb(query: str, top_k: int = 5) -> dict[str, Any]:
    if not embeddings_configured():
        return {
            "error": "rag_not_configured",
            "message": (
                "Set ITSM_EMBEDDING_BASE_URL and ITSM_EMBEDDING_MODEL (optional ITSM_EMBEDDING_API_KEY) "
                "to enable semantic KB search."
            ),
        }
    try:
        qvec = fetch_embedding(query.strip())
    except Exception as e:
        return {"error": "embedding_failed", "message": str(e)}

    from app.services import kb as kb_svc

    with db.cursor() as cur:
        cur.execute("SELECT article_id, embedding FROM kb_article_embeddings")
        rows = cur.fetchall()

    if not rows:
        return {"results": [], "message": "no_indexed_articles"}

    scored: list[tuple[float, int]] = []
    for row in rows:
        aid = int(row["article_id"])
        vec = json.loads(row["embedding"])
        scored.append((cosine_similarity(qvec, vec), aid))
    scored.sort(key=lambda t: -t[0])
    top = scored[: max(1, min(top_k, 50))]

    results: list[dict[str, Any]] = []
    for score, aid in top:
        art = kb_svc.get_article(aid)
        if not art:
            continue
        results.append(
            {
                "id": art["id"],
                "title": art["title"],
                "description": art["description"],
                "score": round(score, 6),
            }
        )
    return {"results": results}


def reindex_all_articles() -> dict[str, Any]:
    """Embed every KB article (for backfill)."""
    from app.services import kb as kb_svc

    if not embeddings_configured():
        return {
            "error": "rag_not_configured",
            "message": (
                "Set ITSM_EMBEDDING_BASE_URL and ITSM_EMBEDDING_MODEL (optional ITSM_EMBEDDING_API_KEY)."
            ),
        }
    rows = kb_svc.list_articles()
    ok = 0
    failed = 0
    for art in rows:
        try:
            text = article_index_text(art["title"], art["description"])
            vec = fetch_embedding(text)
        except Exception as e:
            logger.warning("KB embedding failed for article_id=%s during reindex: %s", art["id"], e)
            failed += 1
            continue
        now = _now()
        model = _model()
        emb_json = json.dumps(vec)
        with db.cursor() as cur:
            cur.execute(
                """
                INSERT INTO kb_article_embeddings (article_id, embedding, model, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(article_id) DO UPDATE SET
                    embedding = excluded.embedding,
                    model = excluded.model,
                    updated_at = excluded.updated_at
                """,
                (art["id"], emb_json, model, now),
            )
        ok += 1
    return {"indexed": ok, "failed": failed, "total_articles": len(rows)}
