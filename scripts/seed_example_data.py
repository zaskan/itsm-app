#!/usr/bin/env python3
"""
Populate the ITSM database with example data using the same service layer as MCP tools.

Equivalent to calling: create_asset_type, create_kb_article, create_inventory_item, create_incident
(actor_user_id=1, same as MCP defaults).

Usage:
  export ITSM_DATABASE=/path/to/itsm.db  # or rely on default
  export ITSM_BOOTSTRAP_ADMIN_USER=admin ITSM_BOOTSTRAP_ADMIN_PASSWORD=admin  # once, empty DB
  python -c "from app import db; db.init_db()"   # creates user id 1 if DB had no users
  python scripts/seed_example_data.py

Requires at least one user (incidents reference ``actor_user_id``); id ``1`` is used like MCP defaults.
"""

from __future__ import annotations

import os
import random
import sys

# Project root on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import db
from app.services import asset_types as at_svc
from app.services import incidents as inc_svc
from app.services import inventory as inv_svc
from app.services import kb as kb_svc
from app.services import change_templates as ctpl_svc
from app.services import custom_fields as cf_svc
from app.services import request_templates as rtpl_svc
from app.services import task_templates as ttpl_svc

ACTOR_ID = 1

ASSET_TYPE_SEEDS: list[tuple[str, str]] = [
    ("Laptop", "End-user mobile workstations"),
    ("Desktop PC", "Office fixed workstations"),
    ("VM – Linux", "Virtual machines running Linux"),
    ("VM – Windows", "Virtual machines running Windows"),
    ("Physical Server", "Bare-metal application servers"),
    ("Hypervisor Host", "ESXi / KVM / Hyper-V hosts"),
    ("Network Switch", "Access and distribution switches"),
    ("Router / Firewall", "Perimeter and core routing"),
    ("Wi‑Fi AP", "Wireless access points"),
    ("Storage Array", "SAN / NAS heads"),
    ("Backup Appliance", "Dedupe and backup targets"),
    ("Database Cluster", "Managed DB tiers"),
    ("Kubernetes Node", "Worker or control plane nodes"),
    ("Container Registry", "Image storage"),
    ("Load Balancer", "ADC / reverse-proxy appliances"),
    ("UPS / PDU", "Power infrastructure"),
    ("Printer / MFP", "Office multifunction devices"),
    ("VoIP Phone", "Desk phones and conference units"),
    ("IoT Gateway", "Shop floor or facility gateways"),
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
]

INCIDENT_TEMPLATES: list[tuple[str, str, str]] = [
    ("Email sync stuck on mobile", "Mailbox shows spinner; restart profile then re-add account.", "medium"),
    ("VPN drops every ~15 minutes", "Stable on wired; suspect Wi‑Fi power save on VPN adapter.", "high"),
    ("Printer offline after patch Tuesday", "Spooler service restart fixes until driver update.", "low"),
    ("SharePoint page 503", "Front-end pool unhealthy; IIS reset scheduled.", "critical"),
    ("SAP dialog response slow", "DB wait events elevated; DBA investigating blocking sessions.", "high"),
    ("Teams screen share black", "GPU driver rollback requested for affected laptops.", "medium"),
    ("Wi‑Fi auth failures in wing B", "RADIUS timeout to NPS; network team failover secondary.", "critical"),
    ("Backup job failed — SQL", "Dedupe volume full; retention trim in progress.", "high"),
    ("Antivirus false positive", "Quarantined build tool; exclusion requested via security.", "low"),
    ("LDAP bind errors", "Replica lag; readonly DC promoted.", "critical"),
    ("Certificate expiry warning — mail", "Renewal ticket with messaging team.", "medium"),
    ("Monitor flicker on dock", "USB-C firmware update pilot.", "low"),
    ("DNS resolution flaky", "Conditional forwarder misconfigured for partner zone.", "high"),
    ("Jenkins pipeline timeout", "Artifact repo latency spike overnight.", "medium"),
    ("Guest Wi‑Fi captive portal down", "Radius secret rotated; portal pods restarted.", "high"),
    ("File share permission denied", "AD group membership sync delay after HR import.", "low"),
    ("Latency to SaaS CRM", "Traceroute clean; provider status page shows incident.", "medium"),
    ("Voice quality MOS drop", "Codec mismatch after firmware push; rollback planned.", "high"),
    ("Disk SMART warning", "Replace SSD scheduled during maintenance.", "medium"),
    ("Cluster node NotReady", "Kubelet cert rotated; node cordoned for drain.", "critical"),
    ("Patch reboot loop", "Boot driver conflict; safe mode uninstall.", "high"),
    ("Inventory sync mismatch", "Asset tag scanned twice; asset reconciliation.", "low"),
    ("Power outage — branch", "UPS exhausted; generator refuel in progress.", "critical"),
    ("Database lock escalation", "Long transaction killed; app vendor engaged.", "critical"),
    ("Malware alert — isolated host", "EDR containment; forensic image captured.", "high"),
    ("DHCP exhaustion", "Short lease on guest VLAN; scope expanded.", "medium"),
    ("SSL inspection breaking site", "PAC exception added after security review.", "low"),
    ("Replication lag — DR site", "WAN saturation; QoS adjustment.", "high"),
    ("App crash after Java update", "JRE pinned to previous minor.", "medium"),
    ("Camera NVR offline", "PoE switch port flapping; cable replace.", "medium"),
    ("Badge reader intermittent", "Wiegand wiring corrosion in conduit.", "low"),
    ("ERP batch job failed", "Calendar period closed; rerun next window.", "medium"),
    ("Mailbox migration stalled", "Cross-forest move rescheduled off-hours.", "high"),
    ("SIEM parser errors", "Syslog format change from firewall upgrade.", "low"),
    ("Wi‑Fi survey requested", "Capacity planning for new open-plan area.", "low"),
    ("Satellite link degraded", "Weather; failover to LTE backup.", "high"),
    ("Container image pull 401", "Registry token expired in CI secret.", "medium"),
    ("Legacy app IE mode", "Enterprise mode list updated for finance URL.", "low"),
    ("Ticket duplicate merged", "Same root cause as INC linked in parent.", "low"),
    ("Emergency change approved", "Hotfix for payment API timeout.", "critical"),
    ("Service restart during lunch", "Quick mitigation for memory leak pending patch.", "medium"),
]

WORKFLOW_KB: list[tuple[str, str]] = [
    ("Provision Linux VM on hypervisor", "Steps: select datastore, apply CPU/RAM/disk template, attach network port group, register in DNS."),
    ("Install Python and Docker packages", "Use automation: apt/yum install python3.11, docker.io; enable and start docker; verify with python --version and docker ps."),
    ("Deploy Sales-App v2.0", "Pull container image, apply config map, run health check on /healthz, register in service mesh if applicable."),
    ("Smoke tests and monitoring onboarding", "Run connectivity ping, app login test, verify metrics in monitoring dashboard, create alert rules."),
]

LINUX_VM_CATALOG = {
    "name": "New Linux Virtual Machine",
    "description": "Standard Linux VM with packages and optional application deployment.",
    "default_change_type": "standard",
    "default_specs": {"vcpu": 2, "ram_gb": 4, "storage_gb": 50, "hostname": "srv-sales-01", "ip": "10.0.1.50"},
}


def main() -> None:
    db.init_db()

    with db.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM users")
        (n_users,) = cur.fetchone()
    if n_users == 0:
        print(
            "Error: no users in this database. Create one first, e.g.\n"
            "  ITSM_BOOTSTRAP_ADMIN_USER=admin ITSM_BOOTSTRAP_ADMIN_PASSWORD=admin python -c \"from app import db; db.init_db()\"",
            file=sys.stderr,
        )
        sys.exit(1)

    # 20 asset types (skip if name exists — make unique by suffix if re-run)
    existing = {t["name"] for t in at_svc.list_types()}
    type_ids: list[int] = []
    for name, desc in ASSET_TYPE_SEEDS:
        n = name
        suffix = 0
        while n in existing:
            suffix += 1
            n = f"{name} ({suffix})"
        existing.add(n)
        row = at_svc.create_type(n, desc)
        type_ids.append(row["id"])

    # 10 KB articles
    for title, body in KB_ARTICLES:
        kb_svc.create_article(title, body)

    # 60 assets — 3 per type (cycles through types)
    idx = 0
    inv_ids: list[int] = []
    for _ in range(60):
        tid = type_ids[idx % len(type_ids)]
        idx += 1
        host_num = (idx // len(type_ids)) + (idx % 7)
        name = f"host-{tid}-{host_num:02d}"
        desc = f"Example asset for type id {tid}"
        row = inv_svc.create_item(
            name,
            desc,
            asset_type_id=tid,
            external_inventory=(idx % 5 == 0),
        )
        inv_ids.append(row["id"])

    if len(inv_ids) >= 2:
        server = inv_svc.create_item(
            "PhysicalHost-Node03",
            "Bare-metal server hosting application workloads",
            asset_type_id=type_ids[4] if len(type_ids) > 4 else type_ids[0],
            external_inventory=True,
        )
        inv_svc.create_item(
            "Sales-App v2.0",
            "Sales application deployed on PhysicalHost-Node03",
            asset_type_id=type_ids[12] if len(type_ids) > 12 else type_ids[0],
            parent_asset_id=server["id"],
        )

    # 40 incidents — rotate severities and optionally link inventory
    rng = random.Random(42)
    severities = ["low", "medium", "high", "critical"]
    for i in range(40):
        title, desc, sev = INCIDENT_TEMPLATES[i % len(INCIDENT_TEMPLATES)]
        title = f"{title} (batch {i + 1})"
        if i >= len(INCIDENT_TEMPLATES):
            sev = severities[i % 4]
        asset_id = inv_ids[i % len(inv_ids)] if rng.random() > 0.35 else None
        inc_svc.create_incident(
            title=title,
            description=desc,
            severity=sev,
            actor_user_id=ACTOR_ID,
            created_at=None,
            inventory_asset_id=asset_id,
        )

    # Workflow: KB for CTASKs and request template chain
    kb_ids: list[int] = []
    for title, body in WORKFLOW_KB:
        art = kb_svc.create_article(title, body)
        kb_ids.append(art["id"])

    existing_catalog = {c["name"] for c in rtpl_svc.list_request_templates()}
    if LINUX_VM_CATALOG["name"] not in existing_catalog:
        task_tpl_ids: list[int] = []
        for i, t in enumerate(WORKFLOW_KB):
            tt = ttpl_svc.create_task_template(
                name=f"Linux VM — {t[0][:40]}",
                title=t[0],
                description=t[1][:80],
                assigned_user_id=ACTOR_ID,
                kb_article_id=kb_ids[i] if i < len(kb_ids) else None,
            )
            task_tpl_ids.append(tt["id"])
        chg_tpl = ctpl_svc.create_change_template(
            name="Linux VM — Standard Change",
            description=LINUX_VM_CATALOG["description"],
            change_type=LINUX_VM_CATALOG["default_change_type"],
            task_template_ids=task_tpl_ids,
        )
        req_tpl = rtpl_svc.create_request_template(
            name=LINUX_VM_CATALOG["name"],
            description=LINUX_VM_CATALOG["description"],
            change_template_id=chg_tpl["id"],
            require_standard_change=True,
        )
        for key, val in LINUX_VM_CATALOG["default_specs"].items():
            ftype = "number" if isinstance(val, (int, float)) else "text"
            cf_svc.create_definition(
                scope_type="request_template",
                scope_id=req_tpl["id"],
                field_key=key,
                label=key.replace("_", " ").title(),
                field_type=ftype,
            )

    print(
        "Done: 20 asset types, 60 assets, 10 KB articles, 40 incidents, "
        "workflow templates "
        f"(database: {os.environ.get('ITSM_DATABASE', './data/itsm.db')})."
    )


if __name__ == "__main__":
    main()
