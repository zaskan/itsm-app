#!/usr/bin/env python3
"""
Populate the ITSM platform with realistic fake data for demos and manual testing.

By default, targets the itsm-app instance on the **current OpenShift cluster** via
REST API (``oc login`` required). Credentials come from ``ITSM_API_USER`` /
``ITSM_API_PASSWORD`` or the ``itsm-secrets`` bootstrap admin keys.

Usage (OpenShift — default):
  oc login …
  oc project itsm-app
  python scripts/populate_fake_data.py

Usage (local SQLite — direct service layer):
  export ITSM_DATABASE="$PWD/data/itsm.db"
  python scripts/populate_fake_data.py --local

Options:
  --minimal       Smaller dataset
  --no-workflow   Skip service requests and template chain
  --no-submit     Draft requests only
  --base-url URL  Override API base (default: Route host from ``oc get route``)
  --namespace NS  OpenShift namespace (default: current project or itsm-app)
  --user / --password   API credentials (default: secret bootstrap-admin-*)
  --secure        Verify TLS certificates (default: skip, for private Route CAs)
  --insecure      Skip TLS verification (default)
"""

from __future__ import annotations

import argparse
import os
import random
import sys
from typing import Any, Protocol

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(SCRIPT_DIR, ".."))
sys.path.insert(0, SCRIPT_DIR)

from itsm_populate_client import (  # noqa: E402
    PopulateError,
    connect_openshift_backend,
)

DEMO_USERS: list[tuple[str, str, str]] = [
    ("alice", "demo", "user"),
    ("bob", "demo", "user"),
    ("carol", "demo", "user"),
    ("dana", "demo", "admin"),
]

ASSET_TYPES: list[tuple[str, str]] = [
    ("Laptop", "End-user mobile workstations"),
    ("Desktop PC", "Office fixed workstations"),
    ("VM – Linux", "Virtual machines running Linux"),
    ("Physical Server", "Bare-metal application servers"),
    ("Network Switch", "Access and distribution switches"),
    ("Database Cluster", "Managed database tiers"),
    ("Kubernetes Node", "Worker or control plane nodes"),
    ("Load Balancer", "ADC / reverse-proxy appliances"),
    ("Printer / MFP", "Office multifunction devices"),
    ("Monitoring Probe", "Synthetic or SNMP collectors"),
]

KB_ARTICLES: list[tuple[str, str]] = [
    ("VPN connectivity checklist", "Verify split tunnel, DNS suffixes, and MFA prompt order."),
    ("Password reset — AD", "Use self-service portal; escalate to admin after two failures."),
    ("New hire laptop setup", "Join domain, install baseline, enable disk encryption, enroll MDM."),
    ("Incident severity guidelines", "Critical: outage; High: major degradation; Medium: workaround exists."),
    ("Change window policy", "Production changes Thu 18:00–22:00 unless emergency CAB."),
    ("Backup restore drill", "Quarterly test: restore random VM and verify app login."),
    ("Phishing response", "Isolate host, revoke sessions, scan, re-image if persistence suspected."),
    ("SSL certificate renewal", "Track 30/14/7 days; update ingress and app trust stores."),
    ("Disk space runbook", "Find large logs, rotate, expand LVM if approved."),
    ("Vendor escalation tree", "L1 helpdesk → L2 ops → vendor TAM with case priority mapping."),
    ("Linux VM provisioning", "Select datastore, apply CPU/RAM/disk template, attach network, register DNS."),
    ("Docker baseline install", "Install docker.io, enable service, verify with docker ps."),
    ("Application deploy checklist", "Pull image, apply config, run health check, register monitoring."),
    ("Smoke test procedure", "Connectivity ping, app login, verify metrics and alert rules."),
    ("On-call handoff template", "Open incidents, pending changes, known issues, vendor tickets."),
]

INCIDENTS: list[tuple[str, str, str]] = [
    ("Email sync stuck on mobile", "Mailbox shows spinner; restart profile then re-add account.", "medium"),
    ("VPN drops every ~15 minutes", "Stable on wired; suspect Wi‑Fi power save on VPN adapter.", "high"),
    ("Printer offline after patch Tuesday", "Spooler service restart fixes until driver update.", "low"),
    ("SharePoint page 503", "Front-end pool unhealthy; IIS reset scheduled.", "critical"),
    ("SAP dialog response slow", "DB wait events elevated; DBA investigating blocking sessions.", "high"),
    ("Teams screen share black", "GPU driver rollback requested for affected laptops.", "medium"),
    ("Wi‑Fi auth failures in wing B", "RADIUS timeout to NPS; network team failover secondary.", "critical"),
    ("Backup job failed — SQL", "Dedupe volume full; retention trim in progress.", "high"),
    ("LDAP bind errors", "Replica lag; readonly DC promoted.", "critical"),
    ("DNS resolution flaky", "Conditional forwarder misconfigured for partner zone.", "high"),
    ("Jenkins pipeline timeout", "Artifact repo latency spike overnight.", "medium"),
    ("Guest Wi‑Fi captive portal down", "Radius secret rotated; portal pods restarted.", "high"),
    ("Disk SMART warning", "Replace SSD scheduled during maintenance.", "medium"),
    ("Cluster node NotReady", "Kubelet cert rotated; node cordoned for drain.", "critical"),
    ("Malware alert — isolated host", "EDR containment; forensic image captured.", "high"),
    ("DHCP exhaustion", "Short lease on guest VLAN; scope expanded.", "medium"),
    ("ERP batch job failed", "Calendar period closed; rerun next window.", "medium"),
    ("Emergency change approved", "Hotfix for payment API timeout.", "critical"),
    ("Service restart during lunch", "Quick mitigation for memory leak pending patch.", "medium"),
    ("Inventory sync mismatch", "Asset tag scanned twice; asset reconciliation.", "low"),
    ("Certificate expiry warning — mail", "Renewal ticket with messaging team.", "medium"),
    ("Latency to SaaS CRM", "Traceroute clean; provider status page shows incident.", "medium"),
    ("Power outage — branch", "UPS exhausted; generator refuel in progress.", "critical"),
    ("Database lock escalation", "Long transaction killed; app vendor engaged.", "critical"),
    ("Voice quality MOS drop", "Codec mismatch after firmware push; rollback planned.", "high"),
]

COMMENT_SNIPPETS: list[str] = [
    "Checked logs — no errors on our side. Escalating to vendor.",
    "User confirmed issue resolved after cache clear.",
    "Scheduled maintenance window communicated to stakeholders.",
    "Linked related KB article for self-service steps.",
    "Waiting on user to confirm VPN reconnect.",
    "Applied workaround; permanent fix tracked in CHG backlog.",
]

REQUEST_SAMPLES: list[tuple[str, str, bool]] = [
    ("Contractor laptop", "MacBook Pro 14 for 3-month engagement starting next Monday.", False),
    ("VPN access for partner", "Temporary VPN profile for external auditor.", False),
    ("Dev sandbox VM", "Linux VM for sales-app feature branch testing.", True),
    ("Database read replica", "Read-only replica for reporting dashboard.", True),
    ("Printer for floor 3", "Replace aging MFP near reception.", False),
    ("SSL cert renewal", "Renew wildcard cert before expiry next week.", False),
]

WORKFLOW_KB: list[tuple[str, str]] = [
    ("Provision Linux VM on hypervisor", "Select datastore, apply CPU/RAM/disk template, attach network, register DNS."),
    ("Install Python and Docker packages", "Use automation: install python3, docker; enable and start docker."),
    ("Deploy Sales-App v2.0", "Pull container image, apply config map, run health check on /healthz."),
    ("Smoke tests and monitoring onboarding", "Run connectivity ping, app login test, verify metrics dashboard."),
]

LINUX_VM_CATALOG = {
    "name": "New Linux Virtual Machine",
    "description": "Standard Linux VM with packages and optional application deployment.",
    "default_change_type": "standard",
    "default_specs": {"vcpu": 2, "ram_gb": 4, "storage_gb": 50, "hostname": "srv-sales-01", "ip": "10.0.1.50"},
}
CHANGE_TEMPLATE_NAME = "Linux VM — Standard Change"


class PopulateBackend(Protocol):
    def list_users(self) -> list[dict[str, Any]]: ...
    def create_user(self, username: str, password: str, role: str) -> dict[str, Any]: ...
    def list_asset_types(self) -> list[dict[str, Any]]: ...
    def create_asset_type(self, name: str, description: str) -> dict[str, Any]: ...
    def create_asset(self, **kwargs: Any) -> dict[str, Any]: ...
    def create_kb_article(self, title: str, description: str) -> dict[str, Any]: ...
    def create_incident(self, **kwargs: Any) -> dict[str, Any]: ...
    def add_comment(self, incident_ref: str, body: str) -> dict[str, Any]: ...
    def close_incident(self, incident_ref: str, *, kb_article_id: int | None = None) -> dict[str, Any]: ...
    def list_request_templates(self) -> list[dict[str, Any]]: ...
    def create_task_template(self, **kwargs: Any) -> dict[str, Any]: ...
    def create_change_template(self, **kwargs: Any) -> dict[str, Any]: ...
    def create_request_template(self, **kwargs: Any) -> dict[str, Any]: ...
    def create_request_template_field(self, template_id: int, **kwargs: Any) -> dict[str, Any]: ...
    def create_request(self, **kwargs: Any) -> dict[str, Any]: ...
    def submit_request(self, request_ref: str) -> dict[str, Any]: ...
    def list_change_templates(self) -> list[dict[str, Any]]: ...
    def create_change(self, *, change_template_id: int) -> dict[str, Any]: ...
    def get_change(self, change_ref: str) -> dict[str, Any]: ...
    def start_task(self, change_ref: str, task_ref: str) -> dict[str, Any]: ...
    def complete_task(self, change_ref: str, task_ref: str) -> dict[str, Any]: ...


class LocalPopulateBackend:
    """Populate via in-process service layer (local SQLite only)."""

    def __init__(self) -> None:
        from app import db
        from app.services import asset_types as at_svc
        from app.services import change_templates as ctpl_svc
        from app.services import changes as chg_svc
        from app.services import custom_fields as cf_svc
        from app.services import incidents as inc_svc
        from app.services import inventory as inv_svc
        from app.services import kb as kb_svc
        from app.services import request_templates as rtpl_svc
        from app.services import service_requests as req_svc
        from app.services import task_templates as ttpl_svc
        from app.services import users_admin as usr_svc
        from app.services import workflow as wf_svc

        db.init_db()
        self._at = at_svc
        self._ctpl = ctpl_svc
        self._chg = chg_svc
        self._cf = cf_svc
        self._inc = inc_svc
        self._inv = inv_svc
        self._kb = kb_svc
        self._rtpl = rtpl_svc
        self._req = req_svc
        self._ttpl = ttpl_svc
        self._usr = usr_svc
        self._wf = wf_svc
        self._actor_id = 1

    def list_users(self) -> list[dict[str, Any]]:
        return self._usr.list_users()

    def create_user(self, username: str, password: str, role: str) -> dict[str, Any]:
        return self._usr.create_user(username, password, role)

    def list_asset_types(self) -> list[dict[str, Any]]:
        return self._at.list_types()

    def create_asset_type(self, name: str, description: str) -> dict[str, Any]:
        return self._at.create_type(name, description)

    def create_asset(self, **kwargs: Any) -> dict[str, Any]:
        name = kwargs.pop("name")
        description = kwargs.pop("description")
        return self._inv.create_item(name, description, **kwargs)

    def create_kb_article(self, title: str, description: str) -> dict[str, Any]:
        return self._kb.create_article(title, description)

    def create_incident(self, **kwargs: Any) -> dict[str, Any]:
        return self._inc.create_incident(
            title=kwargs["title"],
            description=kwargs["description"],
            severity=kwargs["severity"],
            actor_user_id=self._actor_id,
            created_at=None,
            inventory_asset_id=kwargs.get("inventory_asset_id"),
        )

    def add_comment(self, incident_ref: str, body: str) -> dict[str, Any]:
        return self._inc.add_comment(incident_ref, body, self._actor_id)

    def close_incident(self, incident_ref: str, *, kb_article_id: int | None = None) -> dict[str, Any]:
        return self._inc.close_incident(
            incident_ref, self._actor_id, resolution_kb_article_id=kb_article_id
        )

    def list_request_templates(self) -> list[dict[str, Any]]:
        return self._rtpl.list_request_templates()

    def create_task_template(self, **kwargs: Any) -> dict[str, Any]:
        return self._ttpl.create_task_template(**kwargs)

    def create_change_template(self, **kwargs: Any) -> dict[str, Any]:
        return self._ctpl.create_change_template(**kwargs)

    def create_request_template(self, **kwargs: Any) -> dict[str, Any]:
        return self._rtpl.create_request_template(**kwargs)

    def create_request_template_field(self, template_id: int, **kwargs: Any) -> dict[str, Any]:
        return self._cf.create_definition(scope_type="request_template", scope_id=template_id, **kwargs)

    def create_request(self, **kwargs: Any) -> dict[str, Any]:
        return self._req.create_request(requester_user_id=self._actor_id, **kwargs)

    def submit_request(self, request_ref: str) -> dict[str, Any]:
        return self._wf.submit_request(request_ref, self._actor_id)

    def list_change_templates(self) -> list[dict[str, Any]]:
        return self._ctpl.list_change_templates()

    def create_change(self, *, change_template_id: int) -> dict[str, Any]:
        chg = self._chg.create_change_from_template(
            change_template_id=change_template_id,
            actor_user_id=self._actor_id,
        )
        if chg.get("change_type") == "standard":
            chg = self._chg.auto_approve_change(chg["id"], self._actor_id)
        else:
            chg = self._chg.set_change_pending_approval(chg["id"], self._actor_id)
        detail = self._chg.get_change_detail(chg["public_id"])
        assert detail is not None
        return detail

    def get_change(self, change_ref: str) -> dict[str, Any]:
        detail = self._chg.get_change_detail(change_ref)
        if not detail:
            raise ValueError("Change not found")
        return detail

    def start_task(self, change_ref: str, task_ref: str) -> dict[str, Any]:
        return self._chg.start_ctask(change_ref, task_ref, self._actor_id)

    def complete_task(self, change_ref: str, task_ref: str) -> dict[str, Any]:
        return self._wf.on_ctask_completed(change_ref, task_ref, self._actor_id)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Populate ITSM with fake demo data.")
    parser.add_argument(
        "--local",
        action="store_true",
        help="Use local SQLite via service layer instead of OpenShift REST API.",
    )
    parser.add_argument("--base-url", default="", help="ITSM API base URL (e.g. https://itsm-app.apps.example.com)")
    parser.add_argument("--namespace", default="", help="OpenShift namespace (default: oc project or itsm-app)")
    parser.add_argument("--user", default="", help="API username (HTTP Basic)")
    parser.add_argument("--password", default="", help="API password (HTTP Basic)")
    tls = parser.add_mutually_exclusive_group()
    tls.add_argument(
        "--secure",
        action="store_true",
        help="Verify TLS certificates (default skips verify for OpenShift Routes)",
    )
    tls.add_argument(
        "--insecure",
        action="store_true",
        help="Skip TLS certificate verification (default)",
    )
    parser.add_argument("--minimal", action="store_true", help="Smaller dataset")
    parser.add_argument("--no-workflow", action="store_true", help="Skip requests/changes/templates")
    parser.add_argument("--no-submit", action="store_true", help="Draft requests only")
    return parser.parse_args()


def ensure_demo_users(backend: PopulateBackend) -> list[dict[str, Any]]:
    users = backend.list_users()
    if not users:
        if isinstance(backend, LocalPopulateBackend):
            admin_user = os.environ.get("ITSM_BOOTSTRAP_ADMIN_USER", "admin").strip()
            admin_pass = os.environ.get("ITSM_BOOTSTRAP_ADMIN_PASSWORD", "admin")
            if not admin_user:
                print("Error: no users in database.", file=sys.stderr)
                sys.exit(1)
            backend.create_user(admin_user, admin_pass, "admin")
            users = backend.list_users()
        else:
            print(
                "Error: remote instance has no users. Log in to the app once or set bootstrap secrets.",
                file=sys.stderr,
            )
            sys.exit(1)

    existing = {u["username"] for u in users}
    for username, password, role in DEMO_USERS:
        if username not in existing:
            try:
                backend.create_user(username, password, role)
            except (PopulateError, Exception):
                pass
    return backend.list_users()


def seed_asset_types(backend: PopulateBackend, count: int) -> list[int]:
    existing = {t["name"] for t in backend.list_asset_types()}
    type_ids: list[int] = []
    for name, desc in ASSET_TYPES[:count]:
        n = name
        suffix = 0
        while n in existing:
            suffix += 1
            n = f"{name} ({suffix})"
        existing.add(n)
        row = backend.create_asset_type(n, desc)
        type_ids.append(row["id"])
    return type_ids


def seed_assets(
    backend: PopulateBackend,
    type_ids: list[int],
    user_ids: list[int],
    count: int,
) -> list[int]:
    inv_ids: list[int] = []
    for i in range(count):
        tid = type_ids[i % len(type_ids)]
        host_num = (i // max(len(type_ids), 1)) + (i % 7) + 1
        row = backend.create_asset(
            name=f"asset-{tid}-{host_num:02d}",
            description=f"Demo asset #{i + 1} for type id {tid}",
            asset_type_id=tid,
            assigned_user_id=user_ids[i % len(user_ids)] if user_ids else None,
            external_inventory=(i % 4 == 0),
        )
        inv_ids.append(row["id"])

    if len(type_ids) >= 2:
        server = backend.create_asset(
            name="PhysicalHost-Node03",
            description="Bare-metal server hosting nested application assets",
            asset_type_id=type_ids[min(3, len(type_ids) - 1)],
            assigned_user_id=user_ids[0] if user_ids else None,
            external_inventory=True,
        )
        backend.create_asset(
            name="Sales-App v2.0",
            description="Sales application deployed on PhysicalHost-Node03",
            asset_type_id=type_ids[min(5, len(type_ids) - 1)],
            parent_asset_id=server["id"],
            assigned_user_id=user_ids[1 % len(user_ids)] if user_ids else None,
        )
    return inv_ids


def seed_kb_articles(backend: PopulateBackend, count: int) -> list[int]:
    return [backend.create_kb_article(title, body)["id"] for title, body in KB_ARTICLES[:count]]


def seed_incidents(
    backend: PopulateBackend,
    inv_ids: list[int],
    count: int,
    rng: random.Random,
) -> list[dict[str, Any]]:
    created: list[dict[str, Any]] = []
    severities = ["low", "medium", "high", "critical"]
    for i in range(count):
        title, desc, sev = INCIDENTS[i % len(INCIDENTS)]
        title = f"{title} (#{i + 1})"
        if i >= len(INCIDENTS):
            sev = severities[i % 4]
        asset_id = inv_ids[i % len(inv_ids)] if inv_ids and rng.random() > 0.3 else None
        snap = backend.create_incident(
            title=title,
            description=desc,
            severity=sev,
            inventory_asset_id=asset_id,
        )
        created.append(snap)
        if rng.random() > 0.45:
            try:
                backend.add_comment(snap["public_id"], COMMENT_SNIPPETS[i % len(COMMENT_SNIPPETS)])
            except (PopulateError, Exception):
                pass
    return created


def close_some_incidents(
    backend: PopulateBackend,
    incidents: list[dict[str, Any]],
    kb_ids: list[int],
    count: int,
    rng: random.Random,
) -> int:
    closed = 0
    open_incidents = [i for i in incidents if i.get("status") == "open"]
    rng.shuffle(open_incidents)
    for inc in open_incidents[:count]:
        kb = kb_ids[rng.randrange(len(kb_ids))] if kb_ids and rng.random() > 0.4 else None
        try:
            backend.close_incident(inc["public_id"], kb_article_id=kb)
            closed += 1
        except (PopulateError, Exception):
            pass
    return closed


def ensure_workflow_templates(
    backend: PopulateBackend,
    kb_ids: list[int],
    assign_user_id: int,
) -> tuple[int | None, int | None]:
    """Return (request_template_id, change_template_id)."""
    for tpl in backend.list_request_templates():
        if tpl["name"] == LINUX_VM_CATALOG["name"]:
            return tpl["id"], tpl.get("change_template_id")

    for ctpl in backend.list_change_templates():
        if ctpl["name"] == CHANGE_TEMPLATE_NAME:
            return None, ctpl["id"]

    task_tpl_ids: list[int] = []
    for i, (title, body) in enumerate(WORKFLOW_KB):
        tt = backend.create_task_template(
            name=f"Linux VM — {title[:40]}",
            title=title,
            description=body[:120],
            assigned_user_id=assign_user_id,
            kb_article_id=kb_ids[i] if i < len(kb_ids) else None,
        )
        task_tpl_ids.append(tt["id"])

    chg_tpl = backend.create_change_template(
        name=CHANGE_TEMPLATE_NAME,
        description=LINUX_VM_CATALOG["description"],
        change_type=LINUX_VM_CATALOG["default_change_type"],
        task_template_ids=task_tpl_ids,
    )
    req_tpl = backend.create_request_template(
        name=LINUX_VM_CATALOG["name"],
        description=LINUX_VM_CATALOG["description"],
        change_template_id=chg_tpl["id"],
        require_standard_change=True,
    )
    for key, val in LINUX_VM_CATALOG["default_specs"].items():
        ftype = "number" if isinstance(val, (int, float)) else "text"
        backend.create_request_template_field(
            req_tpl["id"],
            field_key=key,
            label=key.replace("_", " ").title(),
            field_type=ftype,
        )
    return req_tpl["id"], chg_tpl["id"]


def seed_service_requests(
    backend: PopulateBackend,
    template_id: int | None,
    count: int,
    submit_count: int,
) -> tuple[int, int, list[str]]:
    """Return (created, submitted, change public_ids from submitted requests)."""
    created = 0
    submitted = 0
    change_refs: list[str] = []
    template_drafts: list[str] = []
    other_drafts: list[str] = []

    samples = list(REQUEST_SAMPLES)
    if template_id:
        with_tpl = [s for s in REQUEST_SAMPLES if s[2]]
        without_tpl = [s for s in REQUEST_SAMPLES if not s[2]]
        samples = (with_tpl + without_tpl)[:count]
    else:
        samples = samples[:count]

    for i, (name, desc, use_template) in enumerate(samples):
        tpl_id = template_id if use_template and template_id else None
        specs = LINUX_VM_CATALOG["default_specs"] if tpl_id else None
        try:
            detail = backend.create_request(
                name=f"{name} (demo {i + 1})",
                description=desc,
                request_template_id=tpl_id,
                specifications=specs,
            )
            created += 1
            if detail.get("status") == "draft":
                ref = detail["public_id"]
                if tpl_id:
                    template_drafts.append(ref)
                else:
                    other_drafts.append(ref)
        except (PopulateError, Exception):
            pass

    to_submit = template_drafts[:submit_count]
    if len(to_submit) < submit_count:
        to_submit.extend(other_drafts[: submit_count - len(to_submit)])

    for ref in to_submit:
        try:
            result = backend.submit_request(ref)
            submitted += 1
            for chg in result.get("changes_created") or []:
                pid = chg.get("public_id")
                if pid:
                    change_refs.append(pid)
        except (PopulateError, Exception):
            pass
    return created, submitted, change_refs


def seed_standalone_changes(
    backend: PopulateBackend,
    change_template_id: int | None,
    count: int,
) -> list[str]:
    if not change_template_id or count <= 0:
        return []
    refs: list[str] = []
    for i in range(count):
        try:
            chg = backend.create_change(change_template_id=change_template_id)
            refs.append(chg["public_id"])
        except (PopulateError, Exception):
            pass
    return refs


def progress_change_tasks(
    backend: PopulateBackend,
    change_refs: list[str],
) -> dict[str, int]:
    """Start and complete tasks on changes to populate the Tasks view."""
    stats = {
        "changes_with_tasks": 0,
        "tasks_pending": 0,
        "tasks_in_progress": 0,
        "tasks_completed": 0,
    }
    unique_refs = list(dict.fromkeys(change_refs))

    for idx, change_ref in enumerate(unique_refs):
        try:
            detail = backend.get_change(change_ref)
        except (PopulateError, Exception):
            continue
        tasks = sorted(detail.get("tasks") or [], key=lambda t: t.get("sequence_order", 0))
        if not tasks:
            continue
        stats["changes_with_tasks"] += 1

        mode = idx % 4  # all done, half done, last in progress, all pending

        for ti, task in enumerate(tasks):
            task_ref = task["public_id"]
            try:
                if mode == 0:
                    backend.start_task(change_ref, task_ref)
                    backend.complete_task(change_ref, task_ref)
                    stats["tasks_completed"] += 1
                elif mode == 1 and ti < len(tasks) // 2:
                    backend.start_task(change_ref, task_ref)
                    backend.complete_task(change_ref, task_ref)
                    stats["tasks_completed"] += 1
                elif mode == 2:
                    if ti < len(tasks) - 1:
                        backend.start_task(change_ref, task_ref)
                        backend.complete_task(change_ref, task_ref)
                        stats["tasks_completed"] += 1
                    elif ti == len(tasks) - 1:
                        backend.start_task(change_ref, task_ref)
                        stats["tasks_in_progress"] += 1
                    else:
                        stats["tasks_pending"] += 1
                else:
                    stats["tasks_pending"] += 1
            except (PopulateError, Exception):
                stats["tasks_pending"] += 1

    return stats


def run_populate(backend: PopulateBackend, args: argparse.Namespace) -> dict[str, Any]:
    rng = random.Random(42)
    users = ensure_demo_users(backend)
    user_ids = [u["id"] for u in users]

    n_types = 5 if args.minimal else len(ASSET_TYPES)
    n_assets = 12 if args.minimal else 40
    n_kb = 6 if args.minimal else len(KB_ARTICLES)
    n_incidents = 10 if args.minimal else 25
    n_close = 3 if args.minimal else 8
    n_requests = 3 if args.minimal else 6
    n_submit = 0 if args.no_submit else (2 if args.minimal else 4)
    n_standalone_changes = 0 if args.no_submit else (1 if args.minimal else 2)

    type_ids = seed_asset_types(backend, n_types)
    inv_ids = seed_assets(backend, type_ids, user_ids, n_assets)
    kb_ids = seed_kb_articles(backend, n_kb)
    incidents = seed_incidents(backend, inv_ids, n_incidents, rng)
    closed = close_some_incidents(backend, incidents, kb_ids, n_close, rng)

    req_created = 0
    req_submitted = 0
    request_template_id: int | None = None
    change_template_id: int | None = None
    task_stats: dict[str, int] = {}
    if not args.no_workflow:
        workflow_kb_start = max(0, len(kb_ids) - len(WORKFLOW_KB))
        workflow_kb_ids = kb_ids[workflow_kb_start:] or kb_ids
        request_template_id, change_template_id = ensure_workflow_templates(
            backend, workflow_kb_ids, user_ids[0]
        )
        req_created, req_submitted, change_refs = seed_service_requests(
            backend, request_template_id, n_requests, n_submit
        )
        change_refs.extend(
            seed_standalone_changes(backend, change_template_id, n_standalone_changes)
        )
        if not args.no_submit and change_refs:
            task_stats = progress_change_tasks(backend, change_refs)

    return {
        "users": users,
        "type_ids": type_ids,
        "inv_ids": inv_ids,
        "kb_ids": kb_ids,
        "incidents": incidents,
        "closed": closed,
        "request_template_id": request_template_id,
        "change_template_id": change_template_id,
        "req_created": req_created,
        "req_submitted": req_submitted,
        "task_stats": task_stats,
    }


def main() -> None:
    args = parse_args()

    if args.local:
        backend: PopulateBackend = LocalPopulateBackend()
        target = os.environ.get("ITSM_DATABASE", "./data/itsm.db")
        print(f"Populating local database: {target}")
        stats = run_populate(backend, args)
        print("Fake data population complete.")
        print(f"  Database: {target}")
    else:
        ns = args.namespace.strip() or None
        if args.secure:
            tls_verify: bool | None = True
        elif args.insecure:
            tls_verify = False
        else:
            tls_verify = None  # resolve_tls_verify default (off)
        try:
            with connect_openshift_backend(
                base_url=args.base_url.strip() or None,
                namespace=ns,
                username=args.user.strip() or None,
                password=args.password or None,
                verify=tls_verify,
            ) as backend:
                stats = run_populate(backend, args)
        except PopulateError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
        print("Fake data population complete (remote API).")

    users = stats["users"]
    print(f"  Users: {len(users)} ({', '.join(u['username'] for u in users)})")
    print(f"  Asset types: {len(stats['type_ids'])}")
    print(f"  Assets: {len(stats['inv_ids'])} (+ nested demo assets if created)")
    print(f"  KB articles: {len(stats['kb_ids'])}")
    print(f"  Incidents: {len(stats['incidents'])} ({stats['closed']} closed)")
    if not args.no_workflow:
        tid = stats["request_template_id"]
        print(f"  Request template: {'yes' if tid else 'skipped (already exists)'}")
        print(
            f"  Service requests: {stats['req_created']} created, "
            f"{stats['req_submitted']} submitted into changes"
        )
        ts = stats.get("task_stats") or {}
        if ts:
            print(
                f"  Change tasks: {ts.get('changes_with_tasks', 0)} changes with tasks — "
                f"{ts.get('tasks_completed', 0)} completed, "
                f"{ts.get('tasks_in_progress', 0)} in progress, "
                f"{ts.get('tasks_pending', 0)} pending"
            )
    if any(u["username"] in {"alice", "bob", "carol", "dana"} for u in users):
        print("  Demo logins (if newly created): alice/demo, bob/demo, carol/demo, dana/demo")


if __name__ == "__main__":
    main()
