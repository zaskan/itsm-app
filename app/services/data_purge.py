"""Nuclear data purge — delete all application data except admin users."""

from __future__ import annotations

from app import db
from app.services import branding as branding_svc
from app.services import settings as settings_svc
from app.services import users_admin as usr_svc

PURGE_CONFIRM_PHRASE = "DELETE ALL DATA"

_PURGE_TABLES: tuple[str, ...] = (
    "workflow_events",
    "change_tasks",
    "change_requests",
    "requested_items",
    "service_requests",
    "comments",
    "incident_events",
    "incidents",
    "inventory_assets",
    "kb_article_embeddings",
    "kb_articles",
    "custom_field_definitions",
    "standard_task_templates",
    "request_templates",
    "standard_change_templates",
    "asset_types",
    "outbound_webhooks",
    "app_settings",
)


def purge_all_data(*, actor_admin_id: int) -> dict[str, int]:
    """Delete all data except admin user accounts; restore factory settings."""
    actor = usr_svc.get_user(actor_admin_id)
    if not actor or actor.get("role") != "admin":
        raise ValueError("Administrator access required")

    deleted: dict[str, int] = {}

    with db.cursor() as cur:
        cur.execute("PRAGMA foreign_keys = OFF")
        try:
            for table in _PURGE_TABLES:
                cur.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                    (table,),
                )
                if not cur.fetchone():
                    continue
                cur.execute(f"DELETE FROM {table}")  # noqa: S608
                deleted[table] = cur.rowcount

            cur.execute("DELETE FROM users WHERE role != ?", ("admin",))
            deleted["non_admin_users"] = cur.rowcount

            for table in _PURGE_TABLES:
                cur.execute("DELETE FROM sqlite_sequence WHERE name = ?", (table,))
        finally:
            cur.execute("PRAGMA foreign_keys = ON")

    branding_svc.clear_custom_uploads()
    settings_svc.seed_defaults()

    return deleted
