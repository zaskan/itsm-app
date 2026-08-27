#!/usr/bin/env python3
"""
Re-apply the default ITSM catalog (asset types, AAP templates, aiops user).

Usage:
  export ITSM_DATABASE=/path/to/itsm.db
  python -c "from app import db; db.init_db()"
  python scripts/seed_example_data.py --force
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import db
from app.services import seed_content as seed_content_svc


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed default ITSM catalog content")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-run default content seed even if already seeded",
    )
    args = parser.parse_args()

    os.environ.pop("ITSM_SKIP_DEFAULT_SEED", None)
    db.init_db()

    stats = seed_content_svc.seed_default_content(force=args.force)
    if stats is None and not args.force:
        print("Default content already seeded (use --force to run again).")
    elif stats:
        print(f"Default content seeded: {stats}")

    print(f"Database: {os.environ.get('ITSM_DATABASE', './data/itsm.db')}")


if __name__ == "__main__":
    main()
