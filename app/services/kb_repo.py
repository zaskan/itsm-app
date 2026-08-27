"""Import knowledge base articles from a markdown repository."""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import yaml

from app import db
from app.services import kb_embeddings as kb_emb
from app.services import settings as settings_svc

logger = logging.getLogger(__name__)

KEY_KB_REPO_URL = "kb_repo_url"
KEY_KB_REPO_ROOT = "kb_repo_root"
KEY_KB_REPO_SUBPATH = "kb_repo_subpath"
KEY_KB_REPO_IGNORE_SSL = "kb_repo_ignore_ssl"
KEY_KB_REPO_LAST_SYNC = "kb_repo_last_sync"

ENV_KB_REPO_URL = "ITSM_KB_REPO_URL"
ENV_KB_REPO = "ITSM_KB_REPO"
ENV_KB_REPO_PATH = "ITSM_KB_REPO_PATH"
ENV_KB_REPO_CACHE = "ITSM_KB_REPO_CACHE"
ENV_KB_REPO_IGNORE_SSL = "ITSM_KB_REPO_IGNORE_SSL"

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


@dataclass(frozen=True)
class ParsedMarkdown:
    title: str
    body: str


def skip_kb_repo_sync() -> bool:
    return os.environ.get("ITSM_SKIP_KB_REPO_SYNC", "").strip().lower() in ("1", "true", "yes")


def get_kb_repo_url() -> str:
    return settings_svc.get_setting(KEY_KB_REPO_URL, "").strip()


def get_kb_repo_root() -> str:
    return settings_svc.get_setting(KEY_KB_REPO_ROOT, "").strip()


def get_kb_repo_subpath() -> str:
    return settings_svc.get_setting(KEY_KB_REPO_SUBPATH, "").strip()


def _truthy(value: str) -> bool:
    return value.strip().lower() in ("1", "true", "yes")


def get_kb_repo_ignore_ssl() -> bool:
    return _truthy(settings_svc.get_setting(KEY_KB_REPO_IGNORE_SSL, ""))


def get_kb_repo_last_sync() -> str:
    return settings_svc.get_setting(KEY_KB_REPO_LAST_SYNC, "").strip()


def set_kb_repo_config(
    *,
    url: str | None = None,
    root: str | None = None,
    subpath: str | None = None,
    ignore_ssl: bool | None = None,
) -> None:
    if url is not None:
        settings_svc.set_setting(KEY_KB_REPO_URL, url.strip())
    if root is not None:
        settings_svc.set_setting(KEY_KB_REPO_ROOT, root.strip())
    if subpath is not None:
        settings_svc.set_setting(KEY_KB_REPO_SUBPATH, subpath.strip())
    if ignore_ssl is not None:
        settings_svc.set_setting(KEY_KB_REPO_IGNORE_SSL, "1" if ignore_ssl else "")


def apply_env_defaults() -> None:
    """Seed KB repo settings from install-time env when not yet configured."""
    env_url = os.environ.get(ENV_KB_REPO_URL, "").strip()
    if env_url and not get_kb_repo_url():
        settings_svc.set_setting(KEY_KB_REPO_URL, env_url)
    env_root = os.environ.get(ENV_KB_REPO, "").strip()
    if env_root and not get_kb_repo_root() and not get_kb_repo_url():
        settings_svc.set_setting(KEY_KB_REPO_ROOT, env_root)
    env_sub = os.environ.get(ENV_KB_REPO_PATH, "").strip()
    if env_sub and not get_kb_repo_subpath():
        settings_svc.set_setting(KEY_KB_REPO_SUBPATH, env_sub)
    if _truthy(os.environ.get(ENV_KB_REPO_IGNORE_SSL, "")) and not get_kb_repo_ignore_ssl():
        settings_svc.set_setting(KEY_KB_REPO_IGNORE_SSL, "1")


def is_kb_repo_configured() -> bool:
    return bool(get_kb_repo_url() or get_kb_repo_root())


def _git_checkout_path() -> Path:
    override = os.environ.get(ENV_KB_REPO_CACHE, "").strip()
    if override:
        return Path(override)
    return db.db_path().parent / "kb-repo-git"


def _git_ssl_args() -> list[str]:
    if get_kb_repo_ignore_ssl():
        return ["-c", "http.sslVerify=false"]
    return []


def _run_git(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *_git_ssl_args(), *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )


def _sync_git_repository(url: str) -> Path:
    """Clone or refresh a shallow checkout of the configured Git repository."""
    checkout = _git_checkout_path()
    checkout.parent.mkdir(parents=True, exist_ok=True)
    repo_url = url.strip()
    if not repo_url:
        raise ValueError("KB repository URL is empty")

    if (checkout / ".git").is_dir():
        result = _run_git("remote", "set-url", "origin", repo_url, cwd=checkout)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "git remote set-url failed")
        result = _run_git("fetch", "--depth", "1", "origin", cwd=checkout)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "git fetch failed")
        result = _run_git("checkout", "-f", "FETCH_HEAD", cwd=checkout)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "git checkout failed")
        return checkout

    if checkout.exists():
        shutil.rmtree(checkout)
    result = _run_git("clone", "--depth", "1", repo_url, str(checkout))
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "git clone failed")
    return checkout


def resolve_scan_source() -> tuple[Path | None, Literal["git", "local"]]:
    """Resolve the directory to scan without fetching from Git."""
    url = get_kb_repo_url()
    subpath = get_kb_repo_subpath()
    if url:
        return resolve_scan_root(str(_git_checkout_path()), subpath), "git"
    root = get_kb_repo_root()
    return resolve_scan_root(root, subpath), "local"


def kb_repo_settings_dict() -> dict[str, Any]:
    url = get_kb_repo_url()
    root = get_kb_repo_root()
    subpath = get_kb_repo_subpath()
    scan_root, source = resolve_scan_source()
    return {
        "repo_url": url,
        "repo_root": root,
        "repo_subpath": subpath,
        "ignore_ssl": get_kb_repo_ignore_ssl(),
        "resolved_path": str(scan_root) if scan_root else "",
        "source": source,
        "configured": is_kb_repo_configured(),
        "path_exists": bool(scan_root and scan_root.is_dir()),
        "last_sync": get_kb_repo_last_sync(),
    }


def resolve_scan_root(repo_root: str, subpath: str = "") -> Path | None:
    root = repo_root.strip()
    if not root:
        return None
    base = Path(root)
    if subpath.strip():
        base = base / subpath.strip().strip("/")
    return base


def parse_markdown_file(path: Path) -> ParsedMarkdown:
    text = path.read_text(encoding="utf-8")
    title: str | None = None
    body = text

    match = _FRONTMATTER_RE.match(text)
    if match:
        try:
            meta = yaml.safe_load(match.group(1)) or {}
            if isinstance(meta, dict) and meta.get("title"):
                title = str(meta["title"]).strip()
        except yaml.YAMLError:
            pass
        body = text[match.end() :]

    body = body.lstrip("\n")
    if not title:
        for line in body.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                title = stripped.lstrip("#").strip()
                break
    if not title:
        title = path.stem.replace("-", " ").replace("_", " ").title()
    return ParsedMarkdown(title=title, body=body)


def _iter_markdown_files(scan_root: Path) -> list[Path]:
    return sorted(p for p in scan_root.rglob("*.md") if p.is_file())


def _relative_source_path(scan_root: Path, file_path: Path) -> str:
    return file_path.relative_to(scan_root).as_posix()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sync_from_repo(*, force: bool = False) -> dict[str, Any]:
    """Import or update KB articles from configured markdown repo."""
    del force  # reserved for future incremental sync options
    url = get_kb_repo_url()
    subpath = get_kb_repo_subpath()
    stats: dict[str, Any] = {
        "created": 0,
        "updated": 0,
        "deleted": 0,
        "skipped": 0,
        "errors": [],
    }
    if url:
        try:
            base = _sync_git_repository(url)
        except (OSError, RuntimeError, ValueError) as exc:
            stats["errors"].append(str(exc))
            return stats
        scan_root = resolve_scan_root(str(base), subpath)
    else:
        root = get_kb_repo_root()
        scan_root = resolve_scan_root(root, subpath)
        if not scan_root:
            stats["errors"].append("KB repository root is not configured")
            return stats
    if not scan_root:
        stats["errors"].append("KB repository root is not configured")
        return stats
    if not scan_root.is_dir():
        stats["errors"].append(f"KB repository path does not exist: {scan_root}")
        return stats

    files = _iter_markdown_files(scan_root)
    seen_paths: set[str] = set()
    now = _now()

    for file_path in files:
        rel = _relative_source_path(scan_root, file_path)
        seen_paths.add(rel)
        try:
            parsed = parse_markdown_file(file_path)
        except OSError as exc:
            stats["errors"].append(f"{rel}: {exc}")
            continue

        with db.cursor() as cur:
            cur.execute(
                "SELECT id FROM kb_articles WHERE source_path = ?",
                (rel,),
            )
            row = cur.fetchone()
            if row:
                aid = int(row[0])
                cur.execute(
                    """
                    UPDATE kb_articles
                    SET title = ?, description = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (parsed.title, parsed.body, now, aid),
                )
                stats["updated"] += 1
            else:
                cur.execute(
                    """
                    INSERT INTO kb_articles (title, description, source_path, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (parsed.title, parsed.body, rel, now, now),
                )
                aid = int(cur.lastrowid)
                stats["created"] += 1

        art = {"id": aid, "title": parsed.title, "description": parsed.body}
        kb_emb.upsert_article_embedding(art["id"], art["title"], art["description"])

    with db.cursor() as cur:
        if seen_paths:
            placeholders = ",".join("?" for _ in seen_paths)
            cur.execute(
                f"""
                SELECT id, source_path FROM kb_articles
                WHERE source_path IS NOT NULL AND source_path NOT IN ({placeholders})
                """,
                tuple(seen_paths),
            )
        else:
            cur.execute(
                "SELECT id, source_path FROM kb_articles WHERE source_path IS NOT NULL"
            )
        stale = cur.fetchall()
        for row in stale:
            cur.execute("DELETE FROM kb_articles WHERE id = ?", (row[0],))
            stats["deleted"] += 1

    settings_svc.set_setting(KEY_KB_REPO_LAST_SYNC, now)
    logger.info("KB repository sync complete: %s", stats)
    return stats


def sync_if_configured() -> dict[str, Any] | None:
    if skip_kb_repo_sync():
        return None
    if not is_kb_repo_configured():
        return None
    return sync_from_repo()
