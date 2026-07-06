#!/usr/bin/env python3
"""Seed the Role registry and backfill Affiliations from UserOrganisation.

Reform S2/S3 (#398, #399). Deploy step after the new code is shipped:

  1. create_all() creates the three new tables (roles, research_projects,
     affiliations) — they don't exist yet on prod, and create_all only adds
     missing tables (it never alters existing ones), so this is safe.
  2. seed_roles() inserts the 7 seed roles (idempotent).
  3. backfill: one Affiliation per UserOrganisation, role seeded from the
     person's legacy Professional.professional_role (decision 2026-07-03).

Run a dry-run first, inspect the flagged rows (people with no Professional /
an unmapped legacy role default to 'other_care'), then run for real:

    python app/scripts/backfill_affiliations.py --dry-run
    python app/scripts/backfill_affiliations.py
"""
import sys
import os
import argparse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.db import init_db, create_all_tables, get_session   # noqa: E402
from src.models import (  # noqa: E402,F401 — register for create_all
    Role, ResearchProject, Affiliation,
)
from src.services.affiliation_service import (  # noqa: E402
    backfill_affiliations_from_user_organisations,
)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true',
                    help='Report what would be created without committing.')
    args = ap.parse_args()

    database_url = os.environ.get('DATABASE_URL', '')
    if not database_url:
        print('ERROR: DATABASE_URL not set in the environment. Run this inside '
              'the app container (docker-compose run --rm app ...) so the '
              'compose DATABASE_URL is present.', file=sys.stderr)
        return 1
    init_db(database_url)
    # Idempotent: creates the 3 new reform tables if missing, leaves the rest.
    create_all_tables()
    session = get_session()

    summary = backfill_affiliations_from_user_organisations(
        session, dry_run=args.dry_run)

    tag = 'DRY-RUN' if summary['dry_run'] else 'DONE'
    print(f"[{tag}] affiliation backfill")
    print(f"  created:          {summary['created']}")
    print(f"  skipped existing: {summary['skipped_existing']}")
    flagged = summary['flagged_default_role']
    print(f"  flagged (defaulted to 'other_care'): {len(flagged)}")
    for f in flagged:
        print(f"    person={f['person_guid']} unit={f['care_unit_guid']} "
              f"legacy={f['legacy_role']!r} -> {f['assigned']}")
    if flagged:
        print("  Review flagged rows: assign the correct role (SU) after "
              "backfill — these people had no Professional row or an "
              "unmapped legacy role.")
    return 0


if __name__ == '__main__':
    sys.exit(main())
