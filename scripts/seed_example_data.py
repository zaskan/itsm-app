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
    ("Inventory sync mismatch", "Asset tag scanned twice; CMDB reconciliation.", "low"),
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

    # 60 inventory rows — 3 hosts per type (cycles through types)
    groups = ["corp", "branch", "dmz", "lab", "prod"]
    idx = 0
    inv_ids: list[int] = []
    for _ in range(60):
        tid = type_ids[idx % len(type_ids)]
        idx += 1
        host_num = (idx // len(type_ids)) + (idx % 7)
        hostname = f"host-{tid}-{host_num:02d}"
        ip = f"10.{(tid % 200) + 1}.{(idx % 200) + 1}.{((idx * 3) % 200) + 1}"
        grp = groups[idx % len(groups)]
        row = inv_svc.create_item(tid, hostname, ip, grp)
        inv_ids.append(row["id"])

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

    print(
        "Done: 20 asset types, 60 inventory items, 10 KB articles, 40 incidents "
        f"(database: {os.environ.get('ITSM_DATABASE', './data/itsm.db')})."
    )


if __name__ == "__main__":
    main()
