"""Load YAML user bootstrap configuration."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "users.yaml"


def get_config_path() -> Path:
    return Path(os.environ.get("ITSM_CONFIG", str(DEFAULT_CONFIG_PATH)))


def load_users_yaml() -> list[dict[str, Any]]:
    path = get_config_path()
    if not path.is_file():
        return []
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return list(data.get("users") or [])
