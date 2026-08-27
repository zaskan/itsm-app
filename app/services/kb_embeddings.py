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

_TRUNCATION_SUFFIX = "…"
# ~2.3 chars/token is typical for technical English; 1200 chars ≈ 480 tokens (under 512).
_DEFAULT_MAX_INPUT_CHARS = 1200
_CONTEXT_RETRY_SHRINK = 0.75
_MAX_CONTEXT_RETRIES = 4


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
    return f"{_base_url()}"


def _max_input_chars() -> int:
    raw = os.environ.get("ITSM_EMBEDDING_MAX_INPUT_CHARS", str(_DEFAULT_MAX_INPUT_CHARS)).strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return _DEFAULT_MAX_INPUT_CHARS


def truncate_embedding_input(text: str, *, max_chars: int | None = None) -> str:
    """Trim text to stay within the embedding model context window (char-based estimate)."""
    limit = max_chars if max_chars is not None else _max_input_chars()
    text = text.strip()
    if len(text) <= limit:
        return text
    if limit <= len(_TRUNCATION_SUFFIX):
        return text[:limit]
    return text[: limit - len(_TRUNCATION_SUFFIX)] + _TRUNCATION_SUFFIX


def _is_context_window_error(response: httpx.Response) -> bool:
    if response.status_code != 400:
        return False
    body = response.text.lower()
    return (
        "contextwindowexceeded" in body
        or "maximum context length" in body
        or "context length" in body
    )


def fetch_embedding(text: str) -> list[float]:
    headers = {"Content-Type": "application/json"}
    key = _api_key()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    original = text.strip()
    char_limit = min(len(original), _max_input_chars())
    input_text = truncate_embedding_input(original, max_chars=char_limit)
    if len(input_text) < len(original):
        logger.info(
            "Truncated embedding input from %s to %s chars (limit %s)",
            len(original),
            len(input_text),
            _max_input_chars(),
        )

    with httpx.Client(timeout=60.0) as client:
        for attempt in range(_MAX_CONTEXT_RETRIES):
            payload = {
                "model": _model(),
                "input": input_text,
                "encoding_format": "float",
            }
            r = client.post(_embeddings_url(), headers=headers, json=payload)
            if r.is_error:
                if _is_context_window_error(r) and attempt < _MAX_CONTEXT_RETRIES - 1:
                    char_limit = max(50, int(char_limit * _CONTEXT_RETRY_SHRINK))
                    input_text = truncate_embedding_input(original, max_chars=char_limit)
                    logger.info(
                        "Embedding context window exceeded; retrying with %s chars (attempt %s)",
                        len(input_text),
                        attempt + 2,
                    )
                    continue
                logger.warning(
                    "Embeddings API error %s %s: %s",
                    r.status_code,
                    r.request.url,
                    r.text[:500],
                )
            r.raise_for_status()
            data = r.json()
            break
    emb = data["data"][0]["embedding"]
    return [float(x) for x in emb]


def article_index_text(title: str, description: str) -> str:
    """Build index text; keep full title and truncate description to fit model limits."""
    prefix = f"Title: {title}\n\n"
    limit = _max_input_chars()
    if len(prefix) >= limit:
        return truncate_embedding_input(prefix, max_chars=limit)
    room = limit - len(prefix)
    desc = description.strip()
    if len(desc) <= room:
        return prefix + desc
    return prefix + truncate_embedding_input(desc, max_chars=room)


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
