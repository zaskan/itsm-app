"""Server-wide branding: logotype, sidebar colors (app_settings + static uploads)."""

from __future__ import annotations

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

LOGO_MODES = ("builtin", "custom")
LogoMode = Literal["builtin", "custom"]

MODE_BUILTIN: LogoMode = "builtin"
MODE_CUSTOM: LogoMode = "custom"

DEFAULT_LOGO_MODE = MODE_BUILTIN
DEFAULT_SIDEBAR_BG = "#1a233a"
DEFAULT_SIDEBAR_TEXT = "#e6eefc"
BUILTIN_LOGO_URL = "/static/branding/default-logo.png"

MAX_LOGO_BYTES = 2 * 1024 * 1024
ALLOWED_TYPES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/svg+xml": ".svg",
    "image/webp": ".webp",
}

_HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")

STATIC_ROOT = Path(__file__).resolve().parent.parent / "static"
UPLOAD_SUBDIR = Path("uploads") / "branding"

PRESETS: dict[str, dict[str, str]] = {
    "navy": {"bg": "#1a233a", "text": "#e6eefc"},
    "slate": {"bg": "#1e293b", "text": "#e2e8f0"},
    "forest": {"bg": "#14532d", "text": "#ecfdf5"},
    "wine": {"bg": "#4c0519", "text": "#ffe4e6"},
    "bronze": {"bg": "#451a03", "text": "#fef3c7"},
    "light": {"bg": "#f1f5f9", "text": "#0f172a"},
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


def seed_branding_defaults() -> None:
    defaults = [
        (KEY_LOGO_MODE, DEFAULT_LOGO_MODE),
        (KEY_LOGO_CUSTOM_PATH, ""),
        (KEY_SIDEBAR_BG, DEFAULT_SIDEBAR_BG),
        (KEY_SIDEBAR_TEXT, DEFAULT_SIDEBAR_TEXT),
    ]
    with db.cursor() as cur:
        for key, val in defaults:
            cur.execute(
                """
                INSERT INTO app_settings (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO NOTHING
                """,
                (key, val),
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
    # path like /static/uploads/branding/foo.png
    prefix = "/static/"
    if not path.startswith(prefix):
        return
    rel = path[len(prefix) :]
    fs = STATIC_ROOT / rel
    try:
        if fs.is_file():
            fs.unlink()
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
    upload_dir = STATIC_ROOT / UPLOAD_SUBDIR
    upload_dir.mkdir(parents=True, exist_ok=True)
    fname = f"{uuid.uuid4().hex}{ext}"
    fs_path = upload_dir / fname
    fs_path.write_bytes(content)
    url_path = f"/static/{UPLOAD_SUBDIR.as_posix()}/{fname}"
    _set(KEY_LOGO_MODE, MODE_CUSTOM)
    _set(KEY_LOGO_CUSTOM_PATH, url_path)
    return branding_api_dict()


def template_branding_context() -> dict[str, Any]:
    """Extra keys for Jinja: CSS vars + logo URL (validated)."""
    b = get_branding()
    # Normalize hex for CSS (already validated in get_branding)
    return {
        "branding": b,
        "sidebar_style": (
            f"--sidebar-bg: {b['sidebar_background']}; "
            f"--sidebar-text: {b['sidebar_text']};"
        ),
    }
