"""Pytest configuration: set env before ``app`` is imported by test modules."""

from __future__ import annotations

import atexit
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# Isolated SQLite file for the whole test session (conftest loads before test modules).
_fd, _TEST_DB_PATH = tempfile.mkstemp(suffix=".db", prefix="itsm_mcp_test_")
os.close(_fd)
atexit.register(lambda: os.path.exists(_TEST_DB_PATH) and os.unlink(_TEST_DB_PATH))

TEST_SESSION_DB_PATH = _TEST_DB_PATH

os.environ["ITSM_DATABASE"] = _TEST_DB_PATH
os.environ["SESSION_SECRET"] = "pytest-session-secret"
os.environ["ITSM_BOOTSTRAP_ADMIN_USER"] = "admin"
os.environ["ITSM_BOOTSTRAP_ADMIN_PASSWORD"] = "admin"
os.environ.pop("MCP_TOKEN", None)
