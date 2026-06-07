"""SQLite database access and schema."""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from werkzeug.security import generate_password_hash

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "itsm.db"

# Applied on every new thread-local connection (sync routes run in a worker thread).
_SCHEMA_SQL = """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'user'
            );

            CREATE TABLE IF NOT EXISTS asset_types (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                description TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS inventory_assets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                asset_type_id INTEGER REFERENCES asset_types(id),
                name TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                parent_asset_id INTEGER REFERENCES inventory_assets(id) ON DELETE SET NULL,
                external_inventory INTEGER NOT NULL DEFAULT 0,
                hostname TEXT NOT NULL DEFAULT '',
                ip_address TEXT NOT NULL DEFAULT '',
                assigned_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                custom_fields TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_assets_parent ON inventory_assets(parent_asset_id);
            CREATE INDEX IF NOT EXISTS idx_assets_external ON inventory_assets(external_inventory);

            CREATE TABLE IF NOT EXISTS kb_articles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS kb_article_embeddings (
                article_id INTEGER PRIMARY KEY,
                embedding TEXT NOT NULL,
                model TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (article_id) REFERENCES kb_articles(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS incidents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                public_id TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                severity TEXT NOT NULL CHECK (severity IN ('low','medium','high','critical')),
                status TEXT NOT NULL CHECK (status IN ('open','closed')),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                closed_at TEXT,
                inventory_asset_id INTEGER REFERENCES inventory_assets(id),
                resolution_kb_article_id INTEGER REFERENCES kb_articles(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS comments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                incident_id INTEGER NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
                author_user_id INTEGER NOT NULL REFERENCES users(id),
                body TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS incident_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                incident_id INTEGER NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
                event_type TEXT NOT NULL,
                payload TEXT NOT NULL DEFAULT '{}',
                actor_user_id INTEGER NOT NULL REFERENCES users(id),
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS outbound_webhooks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT NOT NULL,
                label TEXT NOT NULL DEFAULT '',
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_incidents_status ON incidents(status);
            CREATE INDEX IF NOT EXISTS idx_incidents_severity ON incidents(severity);
            CREATE INDEX IF NOT EXISTS idx_comments_incident ON comments(incident_id);
            CREATE INDEX IF NOT EXISTS idx_events_incident ON incident_events(incident_id);
            CREATE INDEX IF NOT EXISTS idx_inventory_type ON inventory_assets(asset_type_id);

            CREATE TABLE IF NOT EXISTS custom_field_definitions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scope_type TEXT NOT NULL,
                scope_id INTEGER NOT NULL,
                field_key TEXT NOT NULL,
                label TEXT NOT NULL,
                field_type TEXT NOT NULL
                    CHECK (field_type IN ('text','textarea','number','boolean','select','date')),
                required INTEGER NOT NULL DEFAULT 0,
                options TEXT NOT NULL DEFAULT '[]',
                sort_order INTEGER NOT NULL DEFAULT 0,
                UNIQUE(scope_type, scope_id, field_key)
            );

            CREATE TABLE IF NOT EXISTS standard_task_templates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                assigned_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                kb_article_id INTEGER REFERENCES kb_articles(id) ON DELETE SET NULL,
                custom_fields TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS standard_change_templates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                description TEXT NOT NULL DEFAULT '',
                change_type TEXT NOT NULL DEFAULT 'standard'
                    CHECK (change_type IN ('standard','normal')),
                task_template_ids TEXT NOT NULL DEFAULT '[]',
                custom_fields TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS request_templates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                description TEXT NOT NULL DEFAULT '',
                change_template_id INTEGER REFERENCES standard_change_templates(id) ON DELETE SET NULL,
                require_standard_change INTEGER NOT NULL DEFAULT 1,
                packages TEXT NOT NULL DEFAULT '[]',
                application TEXT NOT NULL DEFAULT '',
                ci_class TEXT NOT NULL DEFAULT 'server',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS service_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                public_id TEXT NOT NULL UNIQUE,
                requester_user_id INTEGER NOT NULL REFERENCES users(id),
                name TEXT NOT NULL DEFAULT '',
                description TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'draft'
                    CHECK (status IN ('draft','submitted','in_progress','fulfilled','closed','cancelled')),
                resolution_kb_article_id INTEGER REFERENCES kb_articles(id) ON DELETE SET NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                submitted_at TEXT,
                closed_at TEXT
            );

            CREATE TABLE IF NOT EXISTS request_comments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id INTEGER NOT NULL REFERENCES service_requests(id) ON DELETE CASCADE,
                author_user_id INTEGER NOT NULL REFERENCES users(id),
                body TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_request_comments_request ON request_comments(request_id);

            CREATE TABLE IF NOT EXISTS requested_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                public_id TEXT NOT NULL UNIQUE,
                request_id INTEGER NOT NULL REFERENCES service_requests(id) ON DELETE CASCADE,
                request_template_id INTEGER REFERENCES request_templates(id),
                item_type TEXT NOT NULL DEFAULT '',
                specifications TEXT NOT NULL DEFAULT '{}',
                packages TEXT NOT NULL DEFAULT '[]',
                application TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'draft'
                    CHECK (status IN ('draft','submitted','in_progress','fulfilled','closed')),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS change_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                public_id TEXT NOT NULL UNIQUE,
                ritm_id INTEGER REFERENCES requested_items(id),
                change_template_id INTEGER REFERENCES standard_change_templates(id) ON DELETE SET NULL,
                change_type TEXT NOT NULL CHECK (change_type IN ('standard','normal')),
                risk_assessment TEXT NOT NULL DEFAULT '',
                implementation_plan TEXT NOT NULL DEFAULT '',
                test_plan TEXT NOT NULL DEFAULT '',
                backout_plan TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'draft'
                    CHECK (status IN ('draft','pending_approval','approved','implementing','completed','cancelled')),
                approved_at TEXT,
                approved_by_user_id INTEGER REFERENCES users(id),
                custom_fields TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS change_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                public_id TEXT NOT NULL UNIQUE,
                change_id INTEGER REFERENCES change_requests(id) ON DELETE CASCADE,
                sequence_order INTEGER NOT NULL,
                title TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                assigned_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                kb_article_id INTEGER REFERENCES kb_articles(id) ON DELETE SET NULL,
                status TEXT NOT NULL DEFAULT 'blocked'
                    CHECK (status IN ('blocked','pending','in_progress','completed')),
                completed_at TEXT,
                completion_comment TEXT NOT NULL DEFAULT '',
                custom_fields TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS workflow_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                record_type TEXT NOT NULL,
                record_id INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                payload TEXT NOT NULL DEFAULT '{}',
                actor_user_id INTEGER NOT NULL REFERENCES users(id),
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_requests_status ON service_requests(status);
            CREATE INDEX IF NOT EXISTS idx_ritms_request ON requested_items(request_id);
            CREATE INDEX IF NOT EXISTS idx_ritms_status ON requested_items(status);
            CREATE INDEX IF NOT EXISTS idx_changes_status ON change_requests(status);
            CREATE INDEX IF NOT EXISTS idx_changes_ritm ON change_requests(ritm_id);
            CREATE INDEX IF NOT EXISTS idx_ctasks_change ON change_tasks(change_id);
            CREATE INDEX IF NOT EXISTS idx_workflow_events_record ON workflow_events(record_type, record_id);
            CREATE INDEX IF NOT EXISTS idx_custom_fields_scope ON custom_field_definitions(scope_type, scope_id);
            CREATE INDEX IF NOT EXISTS idx_ctasks_status ON change_tasks(status);
"""

_local = threading.local()


def db_path() -> Path:
    return Path(os.environ.get("ITSM_DATABASE", str(DEFAULT_DB_PATH)))


def get_connection() -> sqlite3.Connection:
    """Thread-local SQLite connection."""
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not getattr(_local, "conn", None):
        _local.conn = sqlite3.connect(str(path), check_same_thread=False)
        _local.conn.row_factory = sqlite3.Row
        _local.conn.execute("PRAGMA foreign_keys = ON")
        _local.conn.executescript(_SCHEMA_SQL)
        _local.conn.commit()
    return _local.conn


@contextmanager
def cursor() -> Iterator[sqlite3.Cursor]:
    conn = get_connection()
    cur = conn.cursor()
    try:
        yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()


def init_db() -> None:
    with cursor() as cur:
        cur.execute("SELECT 1")

    _migrate_legacy_schema()
    _bootstrap_env_admin()
    from app.services import settings as settings_svc

    settings_svc.seed_defaults()


def _table_columns(cur: sqlite3.Cursor, table: str) -> set[str]:
    cur.execute(f'PRAGMA table_info("{table}")')
    return {row[1] for row in cur.fetchall()}


def _migrate_legacy_schema() -> None:
    """Alter older DB files to match current schema."""
    import json

    with cursor() as cur:
        cols = _table_columns(cur, "incidents")
        if cols and "inventory_asset_id" not in cols:
            cur.execute(
                """
                ALTER TABLE incidents ADD COLUMN inventory_asset_id INTEGER
                REFERENCES inventory_assets(id)
                """
            )
        cols = _table_columns(cur, "incidents")
        if cols and "resolution_kb_article_id" not in cols:
            cur.execute(
                """
                ALTER TABLE incidents ADD COLUMN resolution_kb_article_id INTEGER
                REFERENCES kb_articles(id) ON DELETE SET NULL
                """
            )

        inv_cols = _table_columns(cur, "inventory_assets")
        if inv_cols:
            if "custom_fields" not in inv_cols:
                cur.execute(
                    "ALTER TABLE inventory_assets ADD COLUMN custom_fields TEXT NOT NULL DEFAULT '{}'"
                )
            _migrate_inventory_assets_modern(cur)

        chg_cols = _table_columns(cur, "change_requests")
        if chg_cols:
            if "change_template_id" not in chg_cols:
                cur.execute(
                    """
                    ALTER TABLE change_requests ADD COLUMN change_template_id INTEGER
                    REFERENCES standard_change_templates(id) ON DELETE SET NULL
                    """
                )
            if "custom_fields" not in chg_cols:
                cur.execute(
                    "ALTER TABLE change_requests ADD COLUMN custom_fields TEXT NOT NULL DEFAULT '{}'"
                )

        ctask_cols = _table_columns(cur, "change_tasks")
        if ctask_cols and "custom_fields" not in ctask_cols:
            cur.execute(
                "ALTER TABLE change_tasks ADD COLUMN custom_fields TEXT NOT NULL DEFAULT '{}'"
            )

        ritm_cols = _table_columns(cur, "requested_items")
        if ritm_cols and "catalog_item_id" in ritm_cols and "request_template_id" not in ritm_cols:
            cur.execute(
                """
                ALTER TABLE requested_items ADD COLUMN request_template_id INTEGER
                REFERENCES request_templates(id)
                """
            )
            cur.execute(
                """
                UPDATE requested_items SET request_template_id = catalog_item_id
                WHERE catalog_item_id IS NOT NULL
                """
            )

        # Recreate change_requests if ritm_id is still NOT NULL (legacy schema)
        if chg_cols and "ritm_id" in chg_cols:
            cur.execute("PRAGMA table_info(change_requests)")
            ritm_info = [r for r in cur.fetchall() if r[1] == "ritm_id"]
            if ritm_info and ritm_info[0][3] == 1:
                _recreate_change_requests_nullable_ritm(cur)

        _migrate_service_catalog_to_templates(cur, json)
        _migrate_service_requests_modern(cur)
        _migrate_service_request_resolution(cur)
        _migrate_assigned_user_fields(cur)
        _migrate_change_tasks_standalone(cur)

        cur.execute("DROP TABLE IF EXISTS ci_relationships")
        cur.execute("DROP TABLE IF EXISTS configuration_items")

        cur.execute("SELECT COUNT(*) FROM outbound_webhooks")
        if cur.fetchone()[0] == 0:
            cur.execute(
                "SELECT value FROM app_settings WHERE key = ?",
                ("webhook_url",),
            )
            legacy = cur.fetchone()
            if legacy and legacy[0] and str(legacy[0]).strip():
                now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
                cur.execute(
                    """
                    INSERT INTO outbound_webhooks (url, label, enabled, created_at)
                    VALUES (?, '', 1, ?)
                    """,
                    (str(legacy[0]).strip(), now),
                )
                cur.execute("DELETE FROM app_settings WHERE key = ?", ("webhook_url",))


def _recreate_change_requests_nullable_ritm(cur: sqlite3.Cursor) -> None:
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS change_requests_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            public_id TEXT NOT NULL UNIQUE,
            ritm_id INTEGER REFERENCES requested_items(id),
            change_template_id INTEGER REFERENCES standard_change_templates(id) ON DELETE SET NULL,
            change_type TEXT NOT NULL CHECK (change_type IN ('standard','normal')),
            risk_assessment TEXT NOT NULL DEFAULT '',
            implementation_plan TEXT NOT NULL DEFAULT '',
            test_plan TEXT NOT NULL DEFAULT '',
            backout_plan TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'draft'
                CHECK (status IN ('draft','pending_approval','approved','implementing','completed','cancelled')),
            approved_at TEXT,
            approved_by_user_id INTEGER REFERENCES users(id),
            custom_fields TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    cols = _table_columns(cur, "change_requests")
    ct_col = ", change_template_id" if "change_template_id" in cols else ""
    cf_col = ", custom_fields" if "custom_fields" in cols else ", '{}'"
    ct_sel = ", change_template_id" if "change_template_id" in cols else ", NULL"
    cf_sel = ", custom_fields" if "custom_fields" in cols else ", '{}'"
    cur.execute(
        f"""
        INSERT INTO change_requests_new
        (id, public_id, ritm_id, change_template_id, change_type, risk_assessment,
         implementation_plan, test_plan, backout_plan, status, approved_at,
         approved_by_user_id, custom_fields, created_at, updated_at)
        SELECT id, public_id, ritm_id{ct_sel}, change_type, risk_assessment,
               implementation_plan, test_plan, backout_plan, status, approved_at,
               approved_by_user_id{cf_sel}, created_at, updated_at
        FROM change_requests
        """
    )
    cur.execute("DROP TABLE change_requests")
    cur.execute("ALTER TABLE change_requests_new RENAME TO change_requests")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_changes_status ON change_requests(status)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_changes_ritm ON change_requests(ritm_id)")


def _migrate_inventory_assets_modern(cur: sqlite3.Cursor) -> None:
    inv_cols = _table_columns(cur, "inventory_assets")
    if not inv_cols:
        return
    if "name" not in inv_cols:
        cur.execute("ALTER TABLE inventory_assets ADD COLUMN name TEXT NOT NULL DEFAULT ''")
    if "description" not in inv_cols:
        cur.execute(
            "ALTER TABLE inventory_assets ADD COLUMN description TEXT NOT NULL DEFAULT ''"
        )
    if "parent_asset_id" not in inv_cols:
        cur.execute(
            """
            ALTER TABLE inventory_assets ADD COLUMN parent_asset_id INTEGER
            REFERENCES inventory_assets(id) ON DELETE SET NULL
            """
        )
    if "external_inventory" not in inv_cols:
        cur.execute(
            "ALTER TABLE inventory_assets ADD COLUMN external_inventory INTEGER NOT NULL DEFAULT 0"
        )
    if "hostname" in inv_cols:
        cur.execute(
            "UPDATE inventory_assets SET name = hostname WHERE name IS NULL OR name = ''"
        )
    cur.execute("PRAGMA table_info(inventory_assets)")
    type_notnull = True
    for row in cur.fetchall():
        if row[1] == "asset_type_id":
            type_notnull = bool(row[3])
            break
    if not type_notnull and "name" in _table_columns(cur, "inventory_assets"):
        cur.execute("CREATE INDEX IF NOT EXISTS idx_assets_parent ON inventory_assets(parent_asset_id)")
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_assets_external ON inventory_assets(external_inventory)"
        )
        return
    cur.execute("PRAGMA foreign_keys = OFF")
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS inventory_assets_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            asset_type_id INTEGER REFERENCES asset_types(id),
            name TEXT NOT NULL DEFAULT '',
            description TEXT NOT NULL DEFAULT '',
            parent_asset_id INTEGER REFERENCES inventory_assets(id) ON DELETE SET NULL,
            external_inventory INTEGER NOT NULL DEFAULT 0,
            hostname TEXT NOT NULL DEFAULT '',
            ip_address TEXT NOT NULL DEFAULT '',
            assigned_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            custom_fields TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    has_name = "name" in inv_cols
    has_desc = "description" in inv_cols
    has_parent = "parent_asset_id" in inv_cols
    has_ext = "external_inventory" in inv_cols
    name_expr = "name" if has_name else "hostname"
    desc_expr = "description" if has_desc else "''"
    parent_expr = "parent_asset_id" if has_parent else "NULL"
    ext_expr = "external_inventory" if has_ext else "0"
    cur.execute(
        f"""
        INSERT INTO inventory_assets_new
        (id, asset_type_id, name, description, parent_asset_id, external_inventory,
         hostname, ip_address, assigned_user_id, custom_fields, created_at, updated_at)
        SELECT id, asset_type_id, {name_expr}, {desc_expr}, {parent_expr}, {ext_expr},
               hostname, ip_address, NULL, custom_fields, created_at, updated_at
        FROM inventory_assets
        """
    )
    cur.execute("DROP TABLE inventory_assets")
    cur.execute("ALTER TABLE inventory_assets_new RENAME TO inventory_assets")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_assets_parent ON inventory_assets(parent_asset_id)")
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_assets_external ON inventory_assets(external_inventory)"
    )
    cur.execute("PRAGMA foreign_keys = ON")


def _migrate_service_requests_modern(cur: sqlite3.Cursor) -> None:
    req_cols = _table_columns(cur, "service_requests")
    if not req_cols:
        return
    if "name" not in req_cols:
        cur.execute("ALTER TABLE service_requests ADD COLUMN name TEXT NOT NULL DEFAULT ''")
    if "description" not in req_cols:
        cur.execute(
            "ALTER TABLE service_requests ADD COLUMN description TEXT NOT NULL DEFAULT ''"
        )
    if "cost_center" in req_cols:
        cur.execute(
            """
            UPDATE service_requests
            SET name = cost_center
            WHERE (name IS NULL OR name = '') AND cost_center != ''
            """
        )
    if "business_justification" in req_cols:
        cur.execute(
            """
            UPDATE service_requests
            SET description = business_justification
            WHERE (description IS NULL OR description = '') AND business_justification != ''
            """
        )


def _migrate_service_request_resolution(cur: sqlite3.Cursor) -> None:
    req_cols = _table_columns(cur, "service_requests")
    if not req_cols:
        return
    if "resolution_kb_article_id" not in req_cols:
        cur.execute(
            """
            ALTER TABLE service_requests ADD COLUMN resolution_kb_article_id INTEGER
            REFERENCES kb_articles(id) ON DELETE SET NULL
            """
        )
    if "closed_at" not in req_cols:
        cur.execute("ALTER TABLE service_requests ADD COLUMN closed_at TEXT")
    cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='request_comments'"
    )
    if not cur.fetchone():
        cur.execute(
            """
            CREATE TABLE request_comments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id INTEGER NOT NULL REFERENCES service_requests(id) ON DELETE CASCADE,
                author_user_id INTEGER NOT NULL REFERENCES users(id),
                body TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_request_comments_request ON request_comments(request_id)"
        )


def _migrate_assigned_user_fields(cur: sqlite3.Cursor) -> None:
    for table in ("inventory_assets", "standard_task_templates", "change_tasks"):
        cols = _table_columns(cur, table)
        if cols and "assigned_user_id" not in cols:
            cur.execute(
                f"""
                ALTER TABLE {table} ADD COLUMN assigned_user_id INTEGER
                REFERENCES users(id) ON DELETE SET NULL
                """
            )


def _migrate_change_tasks_standalone(cur: sqlite3.Cursor) -> None:
    ctask_cols = _table_columns(cur, "change_tasks")
    if not ctask_cols:
        return
    if "completion_comment" not in ctask_cols:
        cur.execute(
            "ALTER TABLE change_tasks ADD COLUMN completion_comment TEXT NOT NULL DEFAULT ''"
        )
    cur.execute("PRAGMA table_info(change_tasks)")
    change_id_notnull = True
    for row in cur.fetchall():
        if row[1] == "change_id":
            change_id_notnull = bool(row[3])
            break
    if not change_id_notnull and "completion_comment" in _table_columns(cur, "change_tasks"):
        return
    cur.execute("PRAGMA foreign_keys = OFF")
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS change_tasks_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            public_id TEXT NOT NULL UNIQUE,
            change_id INTEGER REFERENCES change_requests(id) ON DELETE CASCADE,
            sequence_order INTEGER NOT NULL,
            title TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            assigned_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            kb_article_id INTEGER REFERENCES kb_articles(id) ON DELETE SET NULL,
            status TEXT NOT NULL DEFAULT 'blocked'
                CHECK (status IN ('blocked','pending','in_progress','completed')),
            completed_at TEXT,
            completion_comment TEXT NOT NULL DEFAULT '',
            custom_fields TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    has_comment = "completion_comment" in ctask_cols
    has_user = "assigned_user_id" in ctask_cols
    comment_expr = "completion_comment" if has_comment else "''"
    user_expr = "assigned_user_id" if has_user else "NULL"
    cur.execute(
        f"""
        INSERT INTO change_tasks_new
        (id, public_id, change_id, sequence_order, title, description, assigned_user_id,
         kb_article_id, status, completed_at, completion_comment, custom_fields,
         created_at, updated_at)
        SELECT id, public_id, change_id, sequence_order, title, description, {user_expr},
               kb_article_id, status, completed_at, {comment_expr}, custom_fields,
               created_at, updated_at
        FROM change_tasks
        """
    )
    cur.execute("DROP TABLE change_tasks")
    cur.execute("ALTER TABLE change_tasks_new RENAME TO change_tasks")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_ctasks_change ON change_tasks(change_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_ctasks_status ON change_tasks(status)")
    cur.execute("PRAGMA foreign_keys = ON")


def _migrate_service_catalog_to_templates(cur: sqlite3.Cursor, json_mod) -> None:
    cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='service_catalog_items'"
    )
    if not cur.fetchone():
        return
    cur.execute("SELECT COUNT(*) FROM request_templates")
    if cur.fetchone()[0] > 0:
        return
    cur.execute("SELECT * FROM service_catalog_items")
    catalog_rows = [dict(r) for r in cur.fetchall()]
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    id_map: dict[int, int] = {}

    for row in catalog_rows:
        try:
            ctask_tpls = json_mod.loads(row.get("ctask_templates") or "[]")
        except json_mod.JSONDecodeError:
            ctask_tpls = []
        task_ids: list[int] = []
        for tpl in sorted(ctask_tpls, key=lambda t: t.get("sequence", 0)):
            tpl_name = f"{row['name']} — {tpl.get('title', 'Task')}"
            cur.execute(
                """
                INSERT INTO standard_task_templates
                (name, title, description, assigned_user_id, kb_article_id, custom_fields, created_at, updated_at)
                VALUES (?, ?, ?, NULL, ?, '{}', ?, ?)
                """,
                (
                    tpl_name,
                    tpl.get("title", "Task"),
                    tpl.get("description", ""),
                    tpl.get("kb_article_id"),
                    now,
                    now,
                ),
            )
            task_ids.append(cur.lastrowid)

        chg_name = f"{row['name']} — Change"
        cur.execute(
            """
            INSERT INTO standard_change_templates
            (name, description, change_type, task_template_ids, custom_fields, created_at, updated_at)
            VALUES (?, ?, ?, ?, '{}', ?, ?)
            """,
            (
                chg_name,
                row.get("description", ""),
                row.get("default_change_type", "standard"),
                json_mod.dumps(task_ids),
                now,
                now,
            ),
        )
        chg_tpl_id = cur.lastrowid

        cur.execute(
            """
            INSERT INTO request_templates
            (name, description, change_template_id, require_standard_change,
             packages, application, ci_class, created_at, updated_at)
            VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?)
            """,
            (
                row["name"],
                row.get("description", ""),
                chg_tpl_id,
                row.get("packages", "[]"),
                row.get("application", ""),
                row.get("ci_class", "server"),
                row.get("created_at", now),
                row.get("updated_at", now),
            ),
        )
        new_id = cur.lastrowid
        id_map[row["id"]] = new_id

        try:
            specs = json_mod.loads(row.get("default_specs") or "{}")
        except json_mod.JSONDecodeError:
            specs = {}
        if isinstance(specs, dict):
            for idx, (key, val) in enumerate(specs.items()):
                ftype = "number" if isinstance(val, (int, float)) else "text"
                cur.execute(
                    """
                    INSERT OR IGNORE INTO custom_field_definitions
                    (scope_type, scope_id, field_key, label, field_type, required, options, sort_order)
                    VALUES ('request_template', ?, ?, ?, ?, 0, '[]', ?)
                    """,
                    (new_id, key, key.replace("_", " ").title(), ftype, idx),
                )

    if id_map:
        for old_id, new_id in id_map.items():
            cur.execute(
                """
                UPDATE requested_items SET request_template_id = ?
                WHERE request_template_id IS NULL AND catalog_item_id = ?
                """,
                (new_id, old_id),
            )


def _bootstrap_env_admin() -> None:
    """Create first admin from env when database has no users."""
    user = os.environ.get("ITSM_BOOTSTRAP_ADMIN_USER", "").strip()
    password = os.environ.get("ITSM_BOOTSTRAP_ADMIN_PASSWORD", "")
    legacy = os.environ.get("ITSM_BOOTSTRAP_ADMIN", "").strip()
    if legacy and ":" in legacy:
        parts = legacy.split(":", 1)
        if len(parts) == 2:
            user = parts[0].strip()
            password = parts[1]

    if not user or password is None or password == "":
        return

    with cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM users")
        if cur.fetchone()[0] > 0:
            return
        h = generate_password_hash(password)
        cur.execute(
            """
            INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)
            """,
            (user, h, "admin"),
        )


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return dict(row)
