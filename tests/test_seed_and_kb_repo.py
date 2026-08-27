"""Default content seeding and KB markdown repository import."""

from __future__ import annotations

import importlib
import os
import subprocess
from pathlib import Path

import pytest

from app import db
import app.main as main_mod
from app.services import kb as kb_svc
from app.services import kb_repo as kb_repo_svc
from app.services import seed_content as seed_content_svc
from app.services import settings as settings_svc
from starlette.testclient import TestClient


@pytest.fixture
def fresh_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db_path = tmp_path / "fresh.db"
    monkeypatch.setenv("ITSM_DATABASE", str(db_path))
    monkeypatch.delenv("ITSM_SKIP_DEFAULT_SEED", raising=False)
    monkeypatch.delenv("ITSM_SKIP_KB_REPO_SYNC", raising=False)
    monkeypatch.delenv("ITSM_KB_REPO", raising=False)
    monkeypatch.delenv("ITSM_KB_REPO_URL", raising=False)
    monkeypatch.delenv("ITSM_KB_REPO_PATH", raising=False)
    db.reset_connections()
    return db_path


def test_seed_default_content_on_fresh_db(fresh_db: Path) -> None:
    importlib.reload(main_mod)
    importlib.reload(seed_content_svc)

    with TestClient(main_mod.app):
        assert seed_content_svc.is_seeded()
        from app.services import asset_types as at_svc
        from app.services import change_templates as ctpl_svc
        from app.services import incidents as inc_svc
        from app.services import request_templates as rtpl_svc
        from app.services import task_templates as ttpl_svc
        from app.services import users_admin as usr_svc

        assert len(at_svc.list_types()) == 2
        assert len(inc_svc.list_incidents()) == 0
        assert len(ttpl_svc.list_task_templates()) == 10
        assert len(ctpl_svc.list_change_templates()) == 3
        assert len(rtpl_svc.list_request_templates()) == 3
        usernames = {u["username"] for u in usr_svc.list_users()}
        assert "admin" in usernames
        assert "aiops" in usernames


def test_kb_repo_sync_imports_markdown(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "kb"
    articles = repo / "docs"
    articles.mkdir(parents=True)
    (articles / "hello.md").write_text(
        "---\ntitle: Hello World\n---\n\nBody text here.\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("ITSM_DATABASE", str(tmp_path / "kb.db"))
    monkeypatch.setenv("ITSM_SKIP_DEFAULT_SEED", "1")
    monkeypatch.delenv("ITSM_SKIP_KB_REPO_SYNC", raising=False)
    db.reset_connections()
    importlib.reload(main_mod)

    kb_repo_svc.set_kb_repo_config(root=str(repo), subpath="docs")
    stats = kb_repo_svc.sync_from_repo()
    assert stats["created"] == 1
    assert stats["errors"] == []

    rows = kb_svc.list_articles()
    assert len(rows) == 1
    assert rows[0]["title"] == "Hello World"
    assert rows[0]["description"].startswith("Body text")
    assert rows[0]["source_path"] == "hello.md"


def test_kb_repo_sync_updates_and_removes_stale(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "kb"
    repo.mkdir()
    path = repo / "one.md"
    path.write_text("---\ntitle: One\n---\n\nFirst.\n", encoding="utf-8")

    monkeypatch.setenv("ITSM_DATABASE", str(tmp_path / "kb2.db"))
    monkeypatch.setenv("ITSM_SKIP_DEFAULT_SEED", "1")
    db.reset_connections()
    importlib.reload(main_mod)

    kb_repo_svc.set_kb_repo_config(root=str(repo))
    kb_repo_svc.sync_from_repo()

    path.write_text("---\ntitle: One Updated\n---\n\nSecond.\n", encoding="utf-8")
    stats = kb_repo_svc.sync_from_repo()
    assert stats["updated"] == 1

    path.unlink()
    stats = kb_repo_svc.sync_from_repo()
    assert stats["deleted"] == 1
    assert kb_svc.list_articles() == []


def test_manual_kb_article_not_removed_on_sync(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "kb"
    repo.mkdir()

    monkeypatch.setenv("ITSM_DATABASE", str(tmp_path / "kb3.db"))
    monkeypatch.setenv("ITSM_SKIP_DEFAULT_SEED", "1")
    db.reset_connections()
    importlib.reload(main_mod)

    kb_svc.create_article("Manual", "Created via API")
    kb_repo_svc.set_kb_repo_config(root=str(repo))
    kb_repo_svc.sync_from_repo()

    rows = kb_svc.list_articles()
    assert len(rows) == 1
    assert rows[0]["title"] == "Manual"
    assert rows[0].get("source_path") in (None, "")


def test_update_article_detaches_from_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "kb"
    repo.mkdir()
    (repo / "doc.md").write_text("---\ntitle: Repo\n---\n\nOriginal.\n", encoding="utf-8")

    monkeypatch.setenv("ITSM_DATABASE", str(tmp_path / "kb4.db"))
    monkeypatch.setenv("ITSM_SKIP_DEFAULT_SEED", "1")
    db.reset_connections()
    importlib.reload(main_mod)

    kb_repo_svc.set_kb_repo_config(root=str(repo))
    kb_repo_svc.sync_from_repo()
    art = kb_svc.list_articles()[0]

    kb_svc.update_article(art["id"], "Edited", "New body")
    updated = kb_svc.get_article(art["id"])
    assert updated is not None
    assert updated["title"] == "Edited"
    assert updated["description"] == "New body"
    assert updated.get("source_path") in (None, "")


def test_kb_repo_sync_from_url_overrides_root_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkout = tmp_path / "checkout"
    articles = checkout / "docs"
    articles.mkdir(parents=True)
    (articles / "from-url.md").write_text(
        "---\ntitle: From URL\n---\n\nGit-sourced article.\n",
        encoding="utf-8",
    )

    ignored = tmp_path / "ignored"
    ignored.mkdir()
    (ignored / "local.md").write_text("---\ntitle: Local\n---\n\n", encoding="utf-8")

    monkeypatch.setenv("ITSM_DATABASE", str(tmp_path / "url.db"))
    monkeypatch.setenv("ITSM_SKIP_DEFAULT_SEED", "1")
    db.reset_connections()
    importlib.reload(main_mod)

    monkeypatch.setattr(kb_repo_svc, "_sync_git_repository", lambda _url: checkout)
    kb_repo_svc.set_kb_repo_config(
        url="https://example.com/kb.git",
        root=str(ignored),
        subpath="docs",
    )
    stats = kb_repo_svc.sync_from_repo()
    assert stats["created"] == 1
    assert stats["errors"] == []

    rows = kb_svc.list_articles()
    assert len(rows) == 1
    assert rows[0]["title"] == "From URL"


def test_git_sync_honors_ignore_ssl_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(kb_repo_svc.subprocess, "run", fake_run)
    kb_repo_svc.set_kb_repo_config(ignore_ssl=True)

    kb_repo_svc._run_git("version")

    assert captured == [["git", "-c", "http.sslVerify=false", "version"]]

    captured.clear()
    kb_repo_svc.set_kb_repo_config(ignore_ssl=False)
    kb_repo_svc._run_git("version")

    assert captured == [["git", "version"]]


def test_kb_repo_settings_api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "kb"
    articles = repo / "a"
    articles.mkdir(parents=True)
    (articles / "x.md").write_text("# Title\n\nContent\n", encoding="utf-8")

    monkeypatch.setenv("ITSM_DATABASE", str(tmp_path / "api.db"))
    monkeypatch.setenv("ITSM_SKIP_DEFAULT_SEED", "1")
    db.reset_connections()
    importlib.reload(main_mod)

    with TestClient(main_mod.app) as client:
        r = client.get("/api/v1/settings/kb-repo", auth=("admin", "admin"))
        assert r.status_code == 200

        r = client.patch(
            "/api/v1/settings/kb-repo",
            auth=("admin", "admin"),
            json={
                "repo_url": "https://example.com/kb.git",
                "repo_root": str(repo),
                "repo_subpath": "a",
                "ignore_ssl": True,
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["configured"] is True
        assert body["source"] == "git"
        assert body["repo_url"] == "https://example.com/kb.git"
        assert body["ignore_ssl"] is True

        monkeypatch.setattr(kb_repo_svc, "_sync_git_repository", lambda _url: repo)
        r = client.post("/api/v1/settings/kb-repo/sync", auth=("admin", "admin"))
        assert r.status_code == 200
        assert r.json()["created"] == 1

        r = client.post(
            "/api/v1/kb/articles",
            auth=("admin", "admin"),
            json={"title": "API article", "description": "Still works"},
        )
        assert r.status_code == 201


def test_parse_markdown_file_uses_heading_when_no_frontmatter(tmp_path: Path) -> None:
    path = tmp_path / "note.md"
    path.write_text("# My Heading\n\nParagraph.\n", encoding="utf-8")
    parsed = kb_repo_svc.parse_markdown_file(path)
    assert parsed.title == "My Heading"
    assert "Paragraph" in parsed.body


def test_kb_public_id_and_snippet() -> None:
    assert kb_svc.public_id(28) == "KB0000028"
    snippet = kb_svc.plain_snippet("## Hello\n\nAvoid clicking unknown links.")
    assert "Avoid clicking" in snippet
    assert "#" not in snippet


def test_kb_search_and_article_pages() -> None:
    importlib.reload(main_mod)
    with TestClient(main_mod.app) as client:
        client.post("/login", data={"username": "admin", "password": "admin"})
        created = client.post(
            "/api/v1/kb/articles",
            auth=("admin", "admin"),
            json={
                "title": "What are phishing scams",
                "description": "Avoid clicking unknown links in email.",
            },
        )
        assert created.status_code == 201
        aid = created.json()["id"]

        listing = client.get("/kb")
        assert listing.status_code == 200
        assert "Knowledge Search" in listing.text
        assert "kb-result" in listing.text
        assert f"/kb/{aid}" in listing.text

        detail = client.get(f"/kb/{aid}")
        assert detail.status_code == 200
        assert kb_svc.public_id(aid) in detail.text
        assert "nx-record-list-header" in detail.text
        assert "View: Self Service" in detail.text
        assert "Most Useful" in detail.text
        assert "What are phishing scams" in detail.text
        assert "kb-article-title" in detail.text
