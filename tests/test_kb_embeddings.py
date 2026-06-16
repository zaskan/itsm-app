"""Tests for KB embedding helpers and rag_search_kb."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from app import db
from app.services import kb as kb_svc
from app.services import kb_embeddings as kb_emb


def test_cosine_similarity_identical() -> None:
    v = [0.6, 0.8, 0.0]
    assert abs(kb_emb.cosine_similarity(v, v) - 1.0) < 1e-9


def test_cosine_similarity_orthogonal() -> None:
    assert kb_emb.cosine_similarity([1.0, 0.0], [0.0, 1.0]) == 0.0


def test_cosine_similarity_mismatched_length() -> None:
    assert kb_emb.cosine_similarity([1.0], [1.0, 0.0]) == 0.0


def test_embeddings_configured_requires_both(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ITSM_EMBEDDING_BASE_URL", raising=False)
    monkeypatch.delenv("ITSM_EMBEDDING_MODEL", raising=False)
    assert kb_emb.embeddings_configured() is False
    monkeypatch.setenv("ITSM_EMBEDDING_BASE_URL", "http://api.example")
    assert kb_emb.embeddings_configured() is False
    monkeypatch.setenv("ITSM_EMBEDDING_MODEL", "emb-small")
    assert kb_emb.embeddings_configured() is True


def test_rag_search_kb_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ITSM_EMBEDDING_BASE_URL", raising=False)
    monkeypatch.delenv("ITSM_EMBEDDING_MODEL", raising=False)
    out = kb_emb.rag_search_kb("hello")
    assert out.get("error") == "rag_not_configured"


def test_rag_search_kb_ranked_results(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ITSM_EMBEDDING_BASE_URL", "http://embeddings.example")
    monkeypatch.setenv("ITSM_EMBEDDING_MODEL", "test-model")

    a = kb_svc.create_article("VPN setup", "Corporate VPN instructions")
    b = kb_svc.create_article("Printer toner", "Replace toner cartridge")

    now = "2026-01-01T00:00:00Z"
    with db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO kb_article_embeddings (article_id, embedding, model, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            (a["id"], json.dumps([1.0, 0.0]), "test-model", now),
        )
        cur.execute(
            """
            INSERT INTO kb_article_embeddings (article_id, embedding, model, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            (b["id"], json.dumps([0.0, 1.0]), "test-model", now),
        )

    with patch.object(kb_emb, "fetch_embedding", return_value=[1.0, 0.0]):
        out = kb_emb.rag_search_kb("how do I connect remotely", top_k=5)

    assert "error" not in out
    results = out["results"]
    assert len(results) >= 1
    assert results[0]["id"] == a["id"]
    assert results[0]["title"] == "VPN setup"
    assert results[0]["score"] > results[-1]["score"] if len(results) > 1 else True


def test_rag_search_kb_empty_index(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ITSM_EMBEDDING_BASE_URL", "http://embeddings.example")
    monkeypatch.setenv("ITSM_EMBEDDING_MODEL", "test-model")

    with db.cursor() as cur:
        cur.execute("DELETE FROM kb_article_embeddings")

    with patch.object(kb_emb, "fetch_embedding", return_value=[1.0, 0.0]):
        out = kb_emb.rag_search_kb("anything")

    assert out.get("message") == "no_indexed_articles"
    assert out.get("results") == []


def test_fetch_embedding_openai_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ITSM_EMBEDDING_BASE_URL", "http://api.example")
    monkeypatch.setenv("ITSM_EMBEDDING_MODEL", "m")

    fake_response = {
        "object": "list",
        "data": [{"object": "embedding", "embedding": [0.1, 0.2, 0.3], "index": 0}],
        "model": "m",
        "usage": {"prompt_tokens": 2, "total_tokens": 2},
    }

    class FakeResp:
        is_error = False

        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict:
            return fake_response

    class FakeClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def post(self, url: str, headers: dict, json: dict) -> FakeResp:
            assert url == "http://api.example/v1/embeddings"
            assert json["model"] == "m"
            assert json["input"] == "hello"
            assert json["encoding_format"] == "float"
            return FakeResp()

    with patch("app.services.kb_embeddings.httpx.Client", FakeClient):
        vec = kb_emb.fetch_embedding("hello")
    assert vec == [0.1, 0.2, 0.3]


def test_reindex_all_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ITSM_EMBEDDING_BASE_URL", raising=False)
    monkeypatch.delenv("ITSM_EMBEDDING_MODEL", raising=False)
    summary = kb_emb.reindex_all_articles()
    assert summary.get("error") == "rag_not_configured"


def test_truncate_embedding_input_short_text() -> None:
    assert kb_emb.truncate_embedding_input("hello", max_chars=100) == "hello"


def test_truncate_embedding_input_long_text() -> None:
    text = "a" * 200
    out = kb_emb.truncate_embedding_input(text, max_chars=50)
    assert len(out) == 50
    assert out.endswith("…")


def test_article_index_text_truncates_description(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ITSM_EMBEDDING_MAX_INPUT_CHARS", "40")
    out = kb_emb.article_index_text("Short title", "x" * 100)
    assert out.startswith("Title: Short title\n\n")
    assert len(out) <= 40
    assert out.endswith("…")


def test_fetch_embedding_retries_on_context_window(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ITSM_EMBEDDING_BASE_URL", "http://api.example")
    monkeypatch.setenv("ITSM_EMBEDDING_MODEL", "m")
    monkeypatch.setenv("ITSM_EMBEDDING_MAX_INPUT_CHARS", "200")

    calls: list[int] = []

    class FakeResp:
        def __init__(self, status: int, body: str) -> None:
            self.status_code = status
            self.text = body
            self.is_error = status >= 400
            self.request = type("R", (), {"url": "http://api.example/v1/embeddings"})()

        def raise_for_status(self) -> None:
            if self.is_error:
                raise httpx.HTTPStatusError("err", request=None, response=self)

        def json(self) -> dict:
            return {
                "data": [{"embedding": [0.1, 0.2]}],
            }

    class FakeClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def post(self, url: str, headers: dict, json: dict) -> FakeResp:
            calls.append(len(json["input"]))
            if len(calls) == 1:
                return FakeResp(400, "ContextWindowExceededError maximum context length is 512")
            return FakeResp(200, "ok")

    import httpx

    with patch("app.services.kb_embeddings.httpx.Client", FakeClient):
        vec = kb_emb.fetch_embedding("a" * 300)

    assert len(calls) == 2
    assert calls[0] == 200
    assert calls[1] < calls[0]
    assert vec == [0.1, 0.2]
