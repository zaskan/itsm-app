"""Default application content seeded on first startup (everything except KB)."""

from __future__ import annotations

import logging
import os
from typing import Any, Literal

from app import db
from app.services import asset_types as at_svc
from app.services import change_templates as ctpl_svc
from app.services import custom_fields as cf_svc
from app.services import request_templates as rtpl_svc
from app.services import settings as settings_svc
from app.services import task_templates as ttpl_svc
from app.services import users_admin as usr_svc

logger = logging.getLogger(__name__)

KEY_DEFAULT_CONTENT_SEEDED = "default_content_seeded"

FieldType = Literal["text", "textarea", "number", "boolean", "select", "date"]


class _FieldSeed:
    __slots__ = ("field_key", "label", "field_type", "required")

    def __init__(
        self,
        field_key: str,
        label: str,
        field_type: FieldType = "text",
        *,
        required: bool = False,
    ) -> None:
        self.field_key = field_key
        self.label = label
        self.field_type = field_type
        self.required = required


class _AssetTypeSeed:
    __slots__ = ("name", "description", "fields")

    def __init__(self, name: str, description: str, fields: list[_FieldSeed]) -> None:
        self.name = name
        self.description = description
        self.fields = fields


class _TaskSeed:
    __slots__ = ("key", "name", "title", "description")

    def __init__(self, key: str, name: str, title: str, description: str) -> None:
        self.key = key
        self.name = name
        self.title = title
        self.description = description


class _ChangeSeed:
    __slots__ = ("name", "description", "change_type", "task_keys")

    def __init__(
        self,
        name: str,
        description: str,
        *,
        change_type: str = "standard",
        task_keys: list[str],
    ) -> None:
        self.name = name
        self.description = description
        self.change_type = change_type
        self.task_keys = task_keys


class _RequestSeed:
    __slots__ = (
        "name",
        "description",
        "change_template_name",
        "require_standard_change",
        "fields",
    )

    def __init__(
        self,
        name: str,
        description: str,
        change_template_name: str,
        *,
        require_standard_change: bool = False,
        fields: list[_FieldSeed] | None = None,
    ) -> None:
        self.name = name
        self.description = description
        self.change_template_name = change_template_name
        self.require_standard_change = require_standard_change
        self.fields = fields or []


class _UserSeed:
    __slots__ = ("username", "password", "role")

    def __init__(self, username: str, password: str, role: str = "user") -> None:
        self.username = username
        self.password = password
        self.role = role


ASSET_TYPE_SEEDS: list[_AssetTypeSeed] = [
    _AssetTypeSeed(
        "Virtual Machine",
        "KubeVirt-provisioned virtual machine",
        [
            _FieldSeed("hostname", "Hostname"),
            _FieldSeed("ip_address", "IP address"),
            _FieldSeed("ansible_host", "Ansible SSH host"),
            _FieldSeed("cpus", "CPUs", "number"),
            _FieldSeed("memory", "Memory GiB", "number"),
        ],
    ),
    _AssetTypeSeed(
        "Generic Application",
        "Application deployed on a provisioned VM (packages, clone path, and services from ITSM)",
        [
            _FieldSeed("hostname", "Hostname"),
            _FieldSeed("vm_hostname", "VM hostname"),
            _FieldSeed("ip_address", "IP address"),
            _FieldSeed("app_repo", "Application repository"),
            _FieldSeed("app_branch", "Git branch"),
            _FieldSeed("app_clone_path", "Repository clone path"),
            _FieldSeed("git_commit", "Deployed git commit"),
            _FieldSeed("route_url", "Route URL"),
            _FieldSeed("rpm_packages", "RPM packages"),
            _FieldSeed("enabled_services", "Service to enable"),
            _FieldSeed("exposed_port", "Port to expose", "number"),
        ],
    ),
]

TASK_TEMPLATE_SEEDS: list[_TaskSeed] = [
    _TaskSeed(
        "push_vm",
        "AAP — Push VM Manifest",
        "AAP — Push VM Manifest",
        "Push KubeVirt VM manifest to Gitea Infrastructure and capture git commit.",
    ),
    _TaskSeed(
        "sync_infra",
        "AAP — Sync Infrastructure VMs",
        "AAP — Sync Infrastructure VMs",
        "Sync Infrastructure VMs via Argo CD after manifest push.",
    ),
    _TaskSeed(
        "register_vm",
        "AAP — Register ITSM VM Asset",
        "AAP — Register ITSM VM Asset",
        "Register VM as ITSM asset and refresh AIOps Infrastructure inventory.",
    ),
    _TaskSeed(
        "install_packages",
        "AAP — Install application packages",
        "AAP — Install application packages",
        "Install RPM packages on the provisioned VM (from ITSM RPM packages field or playbook defaults).",
    ),
    _TaskSeed(
        "deploy_repo",
        "AAP — Deploy application repository",
        "AAP — Deploy application repository",
        "Clone Gitea application repository into the configured repository clone path.",
    ),
    _TaskSeed(
        "expose_app",
        "AAP — Expose application",
        "AAP — Expose application",
        "Inject application Service and Route into VM GitOps manifest and sync Argo CD.",
    ),
    _TaskSeed(
        "start_services",
        "AAP — Start application services",
        "AAP — Start application services",
        "Enable and start application services (from ITSM Service to enable field or playbook defaults).",
    ),
    _TaskSeed(
        "patch_cpu",
        "AAP — Patch VM manifest (CPU)",
        "AAP — Patch VM manifest (CPU)",
        "Patch KubeVirt VM manifest CPU cores in Gitea Infrastructure without rotating password.",
    ),
    _TaskSeed(
        "patch_memory",
        "AAP — Patch VM manifest (Memory)",
        "AAP — Patch VM manifest (Memory)",
        "Patch KubeVirt VM manifest memory in Gitea Infrastructure without rotating password.",
    ),
    _TaskSeed(
        "restart_vm",
        "AAP — Restart VM",
        "AAP — Restart VM",
        "Stop and start the VM to apply manifest resource changes.",
    ),
]

CHANGE_TEMPLATE_SEEDS: list[_ChangeSeed] = [
    _ChangeSeed(
        "Generic Application Stack — Standard Change",
        "Standard change for Generic Application stack (VM provision + application deploy).",
        task_keys=[
            "push_vm",
            "sync_infra",
            "register_vm",
            "install_packages",
            "deploy_repo",
            "expose_app",
            "start_services",
        ],
    ),
    _ChangeSeed(
        "Modify VM CPUs — Standard Change",
        "Standard change to resize VM CPU cores via GitOps manifest patch.",
        task_keys=["patch_cpu", "sync_infra", "restart_vm", "register_vm"],
    ),
    _ChangeSeed(
        "Modify VM Memory — Standard Change",
        "Standard change to resize VM memory via GitOps manifest patch.",
        task_keys=["patch_memory", "sync_infra", "restart_vm", "register_vm"],
    ),
]

REQUEST_TEMPLATE_SEEDS: list[_RequestSeed] = [
    _RequestSeed(
        "Generic Application Stack",
        "Request Generic Application stack (KubeVirt VM + application deployment).",
        "Generic Application Stack — Standard Change",
        fields=[
            _FieldSeed("vm_name", "VM name", required=True),
            _FieldSeed("cpus", "CPUs", "number", required=True),
            _FieldSeed("mem", "Memory GiB", "number", required=True),
            _FieldSeed("app_repo", "Gitea app repository", required=True),
            _FieldSeed("app_branch", "Git branch"),
            _FieldSeed("rpm_packages", "RPM packages"),
            _FieldSeed("app_clone_path", "Repository clone path"),
            _FieldSeed("enabled_services", "Service to enable"),
            _FieldSeed("exposed_port", "Port to expose", "number"),
        ],
    ),
    _RequestSeed(
        "Modify VM CPUs",
        "Request CPU resize for an existing KubeVirt VM.",
        "Modify VM CPUs — Standard Change",
        fields=[
            _FieldSeed("vm_name", "VM name", required=True),
            _FieldSeed("cpus", "CPUs", "number", required=True),
        ],
    ),
    _RequestSeed(
        "Modify VM Memory",
        "Request memory resize for an existing KubeVirt VM.",
        "Modify VM Memory — Standard Change",
        fields=[
            _FieldSeed("vm_name", "VM name", required=True),
            _FieldSeed("mem", "Memory GiB", "number", required=True),
        ],
    ),
]


def _demo_user_seeds() -> list[_UserSeed]:
    password = os.environ.get("ITSM_SEED_AIOPS_PASSWORD", "aiops")
    if not password:
        return []
    return [_UserSeed("aiops", password, "user")]


def skip_default_seed() -> bool:
    return os.environ.get("ITSM_SKIP_DEFAULT_SEED", "").strip().lower() in ("1", "true", "yes")


def is_seeded() -> bool:
    return settings_svc.get_setting(KEY_DEFAULT_CONTENT_SEEDED, "") == "1"


def _seed_demo_users(stats: dict[str, Any]) -> None:
    existing = {u["username"] for u in usr_svc.list_users()}
    for user in _demo_user_seeds():
        if user.username in existing:
            continue
        usr_svc.create_user(user.username, user.password, user.role)
        stats["users"] += 1
        existing.add(user.username)


def _seed_asset_types(stats: dict[str, Any]) -> None:
    existing = {t["name"] for t in at_svc.list_types()}
    for seed in ASSET_TYPE_SEEDS:
        if seed.name in existing:
            continue
        row = at_svc.create_type(seed.name, seed.description)
        stats["asset_types"] += 1
        existing.add(seed.name)
        for field in seed.fields:
            cf_svc.create_definition(
                scope_type="asset_type",
                scope_id=row["id"],
                field_key=field.field_key,
                label=field.label,
                field_type=field.field_type,
                required=field.required,
            )
            stats["custom_fields"] += 1


def _seed_task_templates(stats: dict[str, Any]) -> dict[str, int]:
    existing = {t["name"]: t["id"] for t in ttpl_svc.list_task_templates()}
    ids: dict[str, int] = {}
    for seed in TASK_TEMPLATE_SEEDS:
        if seed.name in existing:
            ids[seed.key] = existing[seed.name]
            continue
        row = ttpl_svc.create_task_template(
            name=seed.name,
            title=seed.title,
            description=seed.description,
            assigned_user_id=None,
            kb_article_id=None,
        )
        ids[seed.key] = row["id"]
        existing[seed.name] = row["id"]
        stats["task_templates"] += 1
    return ids


def _seed_change_templates(stats: dict[str, Any], task_ids: dict[str, int]) -> dict[str, int]:
    existing = {c["name"]: c["id"] for c in ctpl_svc.list_change_templates()}
    ids: dict[str, int] = {}
    for seed in CHANGE_TEMPLATE_SEEDS:
        if seed.name in existing:
            ids[seed.name] = existing[seed.name]
            continue
        task_tpl_ids = [task_ids[key] for key in seed.task_keys]
        row = ctpl_svc.create_change_template(
            name=seed.name,
            description=seed.description,
            change_type=seed.change_type,
            task_template_ids=task_tpl_ids,
        )
        ids[seed.name] = row["id"]
        existing[seed.name] = row["id"]
        stats["change_templates"] += 1
    return ids


def _seed_request_templates(stats: dict[str, Any], change_ids: dict[str, int]) -> None:
    existing = {c["name"] for c in rtpl_svc.list_request_templates()}
    for seed in REQUEST_TEMPLATE_SEEDS:
        if seed.name in existing:
            continue
        change_id = change_ids.get(seed.change_template_name)
        if change_id is None:
            logger.warning(
                "Skipping request template %r: change template %r not found",
                seed.name,
                seed.change_template_name,
            )
            continue
        row = rtpl_svc.create_request_template(
            name=seed.name,
            description=seed.description,
            change_template_id=change_id,
            require_standard_change=seed.require_standard_change,
        )
        stats["request_templates"] += 1
        for field in seed.fields:
            cf_svc.create_definition(
                scope_type="request_template",
                scope_id=row["id"],
                field_key=field.field_key,
                label=field.label,
                field_type=field.field_type,
                required=field.required,
            )
            stats["custom_fields"] += 1


def seed_default_content(*, force: bool = False) -> dict[str, Any] | None:
    """Populate default catalog content (no KB, incidents, or assets)."""
    if not force and skip_default_seed():
        return None
    if not force and is_seeded():
        return None

    with db.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM users")
        (n_users,) = cur.fetchone()
    if n_users == 0:
        logger.warning("Default content seed skipped: no users in database yet")
        return None

    stats: dict[str, Any] = {
        "users": 0,
        "asset_types": 0,
        "task_templates": 0,
        "change_templates": 0,
        "request_templates": 0,
        "custom_fields": 0,
    }

    _seed_demo_users(stats)
    _seed_asset_types(stats)
    task_ids = _seed_task_templates(stats)
    change_ids = _seed_change_templates(stats, task_ids)
    _seed_request_templates(stats, change_ids)

    settings_svc.set_setting(KEY_DEFAULT_CONTENT_SEEDED, "1")
    logger.info("Default application content seeded: %s", stats)
    return stats


def reset_seed_flag() -> None:
    settings_svc.set_setting(KEY_DEFAULT_CONTENT_SEEDED, "")


def seed_default_content_if_needed(*, force: bool = False) -> dict[str, Any] | None:
    return seed_default_content(force=force)
