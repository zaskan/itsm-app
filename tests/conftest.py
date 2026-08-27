"""Pytest configuration: set env before ``app`` is imported by test modules."""

from __future__ import annotations

import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# Isolated data dir so SQLite and branding uploads do not land in /tmp/uploads.
_TEST_DIR = tempfile.mkdtemp(prefix="itsm_mcp_test_")
_TEST_DB_PATH = str(Path(_TEST_DIR) / "itsm.db")
atexit.register(lambda: shutil.rmtree(_TEST_DIR, ignore_errors=True))

TEST_SESSION_DB_PATH = _TEST_DB_PATH

os.environ["ITSM_DATABASE"] = _TEST_DB_PATH
os.environ["SESSION_SECRET"] = "pytest-session-secret"
os.environ["ITSM_BOOTSTRAP_ADMIN_USER"] = "admin"
os.environ["ITSM_BOOTSTRAP_ADMIN_PASSWORD"] = "admin"
os.environ["ITSM_SEED_AIOPS_PASSWORD"] = "aiops"
os.environ["ITSM_SKIP_DEFAULT_SEED"] = "1"
os.environ["ITSM_SKIP_KB_REPO_SYNC"] = "1"
os.environ.pop("MCP_TOKEN", None)
os.environ.pop("ITSM_UPLOAD_DIR", None)
os.environ.pop("ITSM_KB_REPO", None)
os.environ.pop("ITSM_KB_REPO_PATH", None)
