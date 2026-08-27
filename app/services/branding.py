"""Server-wide branding: logotype and Next Experience chrome colors."""

from __future__ import annotations

import os
import re
import uuid
from pathlib import Path
from typing import Any, Literal

from app import db
from app.services import settings as settings_svc

KEY_LOGO_MODE = "branding_logo_mode"
KEY_LOGO_CUSTOM_PATH = "branding_logo_custom_path"
KEY_SIDEBAR_BG = "branding_sidebar_bg"
KEY_SIDEBAR_TEXT = "branding_sidebar_text"
KEY_UI_SHELL = "branding_ui_shell"

LOGO_MODES = ("builtin", "custom")
LogoMode = Literal["builtin", "custom"]

MODE_BUILTIN: LogoMode = "builtin"
MODE_CUSTOM: LogoMode = "custom"

UI_SHELL_NEXT = "next-experience"

DEFAULT_LOGO_MODE = MODE_BUILTIN

# Next Experience chrome defaults
DEFAULT_SIDEBAR_BG = "#002d41"
DEFAULT_SIDEBAR_TEXT = "#ffffff"
DEFAULT_LIST_HEADER_BG = "#e5edef"
DEFAULT_BTN_BG = "#ffffff"
DEFAULT_BTN_BORDER = "#b1c3c9"
DEFAULT_FORM_BORDER = "#68838b"
DEFAULT_PRIMARY = "#006f8e"
DEFAULT_PRIMARY_HOVER = "#005a72"
CANVAS_BG = "#ffffff"
BUILTIN_LOGO_URL = "/static/branding/default-logo.png"

_LEGACY_DEFAULT_BG = "#1a233a"
_LEGACY_DEFAULT_TEXT = "#e6eefc"
_LEGACY_POLARIS_BG = "#1d1e4b"
_LEGACY_POLARIS_TEXT = "#ffffff"
_LEGACY_BLUE_GRAY_BG = "#527181"

MAX_LOGO_BYTES = 2 * 1024 * 1024
ALLOWED_TYPES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/svg+xml": ".svg",
    "image/webp": ".webp",
}

_HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")

UPLOAD_URL_PREFIX = "/static/uploads"
BRANDING_SUBDIR = "branding"


def upload_root() -> Path:
    """Return the directory served at ``/static/uploads``.

    Defaults to ``<sqlite-parent>/uploads`` so a single data volume keeps the
    database and custom logos. Override with ``ITSM_UPLOAD_DIR``.
    """
    raw = os.environ.get("ITSM_UPLOAD_DIR", "").strip()
    if raw:
        return Path(raw)
    return db.db_path().parent / "uploads"


def _branding_upload_dir() -> Path:
    return upload_root() / BRANDING_SUBDIR


def _fs_path_from_public_url(url: str) -> Path | None:
    """Map ``/static/uploads/...`` to a path under ``upload_root()``."""
    prefix = UPLOAD_URL_PREFIX + "/"
    if not url.startswith(prefix):
        return None
    rel = url[len(prefix) :]
    parts = Path(rel).parts
    if not rel or ".." in parts:
        return None
    root = upload_root().resolve()
    candidate = (root / rel).resolve()
    if not candidate.is_relative_to(root):
        return None
    return candidate


PRESETS: dict[str, dict[str, str]] = {
    "polaris": {
        "bg": DEFAULT_SIDEBAR_BG,
        "text": DEFAULT_SIDEBAR_TEXT,
        "label": "Default",
    },
    "navy": {"bg": "#1a233a", "text": "#e6eefc", "label": "Navy"},
    "slate": {"bg": "#1e293b", "text": "#e2e8f0", "label": "Slate"},
    "forest": {"bg": "#14532d", "text": "#ecfdf5", "label": "Forest"},
    "wine": {"bg": "#4c0519", "text": "#ffe4e6", "label": "Wine"},
    "bronze": {"bg": "#451a03", "text": "#fef3c7", "label": "Bronze"},
    "light": {"bg": "#f1f5f9", "text": "#0f172a", "label": "Light"},
}


def _get(key: str, default: str = "") -> str:
    return settings_svc.get_setting(key, default)


def _set(key: str, value: str) -> None:
    settings_svc.set_setting(key, value)


def validate_hex(color: str) -> str:
    c = color.strip()
    if not _HEX_RE.match(c):
        raise ValueError("Color must be a #RRGGBB hex value")
    return c.lower()


def _hex_luminance(color: str) -> float:
    r, g, b = (int(color[i : i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def action_colors(header_bg: str) -> tuple[str, str]:
    """Return primary / hover hex for buttons and links.

    Default Next Experience chrome keeps Polaris indigo actions. Custom dark
    header colors tint actions to match; light headers keep indigo.
    """
    bg = header_bg.lower()
    if bg == DEFAULT_SIDEBAR_BG:
        return DEFAULT_PRIMARY, DEFAULT_PRIMARY_HOVER
    if _hex_luminance(bg) < 160:
        return bg, bg
    return DEFAULT_PRIMARY, DEFAULT_PRIMARY_HOVER


def _migrate_legacy_chrome_colors(cur) -> None:
    cur.execute("SELECT value FROM app_settings WHERE key = ?", (KEY_SIDEBAR_BG,))
    bg_row = cur.fetchone()
    cur.execute("SELECT value FROM app_settings WHERE key = ?", (KEY_SIDEBAR_TEXT,))
    text_row = cur.fetchone()
    bg = (bg_row["value"] if bg_row else "").strip().lower()
    text = (text_row["value"] if text_row else "").strip().lower()
    if bg == _LEGACY_DEFAULT_BG and text == _LEGACY_DEFAULT_TEXT:
        cur.execute(
            "UPDATE app_settings SET value = ? WHERE key = ?",
            (DEFAULT_SIDEBAR_BG, KEY_SIDEBAR_BG),
        )
        cur.execute(
            "UPDATE app_settings SET value = ? WHERE key = ?",
            (DEFAULT_SIDEBAR_TEXT, KEY_SIDEBAR_TEXT),
        )
    elif bg == _LEGACY_POLARIS_BG and text == _LEGACY_POLARIS_TEXT:
        cur.execute(
            "UPDATE app_settings SET value = ? WHERE key = ?",
            (DEFAULT_SIDEBAR_BG, KEY_SIDEBAR_BG),
        )
    elif bg == _LEGACY_BLUE_GRAY_BG and text == DEFAULT_SIDEBAR_TEXT:
        cur.execute(
            "UPDATE app_settings SET value = ? WHERE key = ?",
            (DEFAULT_SIDEBAR_BG, KEY_SIDEBAR_BG),
        )


def seed_branding_defaults() -> None:
    defaults = [
        (KEY_LOGO_MODE, DEFAULT_LOGO_MODE),
        (KEY_LOGO_CUSTOM_PATH, ""),
        (KEY_SIDEBAR_BG, DEFAULT_SIDEBAR_BG),
        (KEY_SIDEBAR_TEXT, DEFAULT_SIDEBAR_TEXT),
    ]
    with db.cursor() as cur:
        cur.execute("SELECT 1 FROM app_settings WHERE key = ?", (KEY_UI_SHELL,))
        migrate_legacy_chrome = cur.fetchone() is None
        for key, val in defaults:
            cur.execute(
                """
                INSERT INTO app_settings (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO NOTHING
                """,
                (key, val),
            )
        _migrate_legacy_chrome_colors(cur)
        if migrate_legacy_chrome:
            cur.execute(
                "INSERT INTO app_settings (key, value) VALUES (?, ?)",
                (KEY_UI_SHELL, UI_SHELL_NEXT),
            )


def _resolved_logo_url() -> str:
    mode = _get(KEY_LOGO_MODE, DEFAULT_LOGO_MODE)
    if mode == MODE_CUSTOM:
        path = _get(KEY_LOGO_CUSTOM_PATH, "").strip()
        if path:
            return path
    return BUILTIN_LOGO_URL


def _safe_hex(key: str, default: str) -> str:
    raw = _get(key, default)
    try:
        return validate_hex(raw)
    except ValueError:
        return validate_hex(default)


def get_branding() -> dict[str, Any]:
    """Payload for API and templates."""
    return {
        "app_title": settings_svc.get_app_title(),
        "logo_mode": _get(KEY_LOGO_MODE, DEFAULT_LOGO_MODE),
        "logo_url": _resolved_logo_url(),
        "sidebar_background": _safe_hex(KEY_SIDEBAR_BG, DEFAULT_SIDEBAR_BG),
        "sidebar_text": _safe_hex(KEY_SIDEBAR_TEXT, DEFAULT_SIDEBAR_TEXT),
    }


def _delete_custom_file_if_any() -> None:
    path = _get(KEY_LOGO_CUSTOM_PATH, "").strip()
    if not path:
        return
    fs = _fs_path_from_public_url(path)
    if fs is None:
        return
    try:
        if fs.is_file():
            fs.unlink()
    except OSError:
        pass


def clear_custom_uploads() -> None:
    """Remove all files in the branding upload directory."""
    upload_dir = _branding_upload_dir()
    if not upload_dir.is_dir():
        return
    for path in upload_dir.iterdir():
        try:
            if path.is_file():
                path.unlink()
        except OSError:
            pass


def set_logo_builtin() -> None:
    _delete_custom_file_if_any()
    _set(KEY_LOGO_MODE, MODE_BUILTIN)
    _set(KEY_LOGO_CUSTOM_PATH, "")


def apply_preset(name: str) -> None:
    key = name.strip().lower()
    if key not in PRESETS:
        raise ValueError(f"Unknown preset: {name}")
    p = PRESETS[key]
    _set(KEY_SIDEBAR_BG, validate_hex(p["bg"]))
    _set(KEY_SIDEBAR_TEXT, validate_hex(p["text"]))


def reset_sidebar_colors() -> None:
    _set(KEY_SIDEBAR_BG, DEFAULT_SIDEBAR_BG)
    _set(KEY_SIDEBAR_TEXT, DEFAULT_SIDEBAR_TEXT)


def reset_title_logo() -> None:
    settings_svc.set_app_title(settings_svc.DEFAULT_APP_TITLE)
    set_logo_builtin()


def reset_all_branding() -> None:
    reset_title_logo()
    reset_sidebar_colors()


def branding_api_dict() -> dict[str, Any]:
    """GET /settings/branding payload including preset names."""
    out = get_branding()
    out["presets_supported"] = sorted(PRESETS.keys())
    return out


def patch_branding(
    *,
    app_title: str | None = None,
    logo_mode: str | None = None,
    sidebar_background: str | None = None,
    sidebar_text: str | None = None,
    preset: str | None = None,
) -> dict[str, Any]:
    if preset is not None:
        apply_preset(preset)
    else:
        if sidebar_background is not None:
            _set(KEY_SIDEBAR_BG, validate_hex(sidebar_background))
        if sidebar_text is not None:
            _set(KEY_SIDEBAR_TEXT, validate_hex(sidebar_text))
    if app_title is not None:
        settings_svc.set_app_title(app_title)
    if logo_mode is not None:
        lm = logo_mode.strip().lower()
        if lm not in LOGO_MODES:
            raise ValueError("logo_mode must be builtin or custom")
        if lm == MODE_BUILTIN:
            set_logo_builtin()
        else:
            _set(KEY_LOGO_MODE, MODE_CUSTOM)
    return branding_api_dict()


def save_uploaded_logo(content: bytes, content_type: str) -> dict[str, Any]:
    ct = (content_type or "").split(";")[0].strip().lower()
    if ct not in ALLOWED_TYPES:
        raise ValueError("Unsupported image type (use PNG, JPEG, SVG, or WebP)")
    if len(content) > MAX_LOGO_BYTES:
        raise ValueError("File too large (max 2 MB)")
    ext = ALLOWED_TYPES[ct]
    _delete_custom_file_if_any()
    upload_dir = _branding_upload_dir()
    upload_dir.mkdir(parents=True, exist_ok=True)
    fname = f"{uuid.uuid4().hex}{ext}"
    fs_path = upload_dir / fname
    fs_path.write_bytes(content)
    url_path = f"{UPLOAD_URL_PREFIX}/{BRANDING_SUBDIR}/{fname}"
    _set(KEY_LOGO_MODE, MODE_CUSTOM)
    _set(KEY_LOGO_CUSTOM_PATH, url_path)
    return branding_api_dict()


def template_branding_context() -> dict[str, Any]:
    """Extra keys for Jinja: CSS vars + logo URL (validated)."""
    b = get_branding()
    primary, primary_hover = action_colors(b["sidebar_background"])
    return {
        "branding": b,
        "sidebar_style": (
            f"--sidebar-bg: {b['sidebar_background']}; "
            f"--sidebar-text: {b['sidebar_text']}; "
            f"--now-primary: {primary}; "
            f"--now-primary-hover: {primary_hover}; "
            f"--now-canvas: {CANVAS_BG}; "
            f"--nx-list-header-bg: {DEFAULT_LIST_HEADER_BG}; "
            f"--now-btn-bg: {DEFAULT_BTN_BG}; "
            f"--now-btn-border: {DEFAULT_BTN_BORDER}; "
            f"--nx-form-border: {DEFAULT_FORM_BORDER};"
        ),
    }
