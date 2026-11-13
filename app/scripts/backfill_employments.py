#!/usr/bin/env python3
"""Backfill Employments for the main rule (operator 2026-11-13).

A professional is EMPLOYED by vårdgivare and may only be AFFILIATED to units
under them. Existing people have affiliations but no employments, so without
this every new affiliation would be refused (409) and the S9 checklist would
say "no employment". Rules: employment_service.backfill_employments.

DRY RUN BY DEFAULT. Inside the app container:

    python scripts/backfill_employments.py            # report only
    python scripts/backfill_employments.py --apply    # write

create_all() creates the new `employments` table first (it only adds missing
tables). Idempotent: running --apply twice creates nothing the second time.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.db import init_db, create_all_tables, get_session   # noqa: E402
from src.models import Employment  # noqa: E402,F401 — register for create_all
from src.services.audit_log import audit  # noqa: E402
from src.services.employment_service import backfill_employments  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true', help='Write (default: dry run).')
    args = ap.parse_args()

    database_url = os.environ.get('DATABASE_URL', '')
    if not database_url:
        print('ERROR: DATABASE_URL not set. Run inside the app container.', file=sys.stderr)
        return 1
    init_db(database_url)
    create_all_tables()
    session = get_session()
    try:
        s = backfill_employments(session, dry_run=not args.apply)
    finally:
        session.close()

    tag = 'DRY-RUN' if s['dry_run'] else 'DONE'
    print(f"[{tag}] employment backfill")
    print(f"  {'would create' if s['dry_run'] else 'created'}: {len(s['created'])}")
    for c in s['created']:
        print(f"    person={c['person_guid'][:8]} employer={c['caregiver_name']} (from {c['from']})")
    print(f"  skipped existing: {s['skipped_existing']}")
    print(f"  unit memberships, not used: {len(s['unit_memberships_not_used'])}")
    print(f"  flagged: {len(s['flagged'])}")
    for f in s['flagged']:
        print(f"    person={f['person_guid'][:8]} org={f['org_guid'][:8]} {f['why']}")
    if not s['dry_run']:
        audit('employment_backfill', detail={'created': len(s['created']),
                                              'skipped_existing': s['skipped_existing']})
    return 0


if __name__ == '__main__':
    sys.exit(main())
