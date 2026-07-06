#!/usr/bin/env python3
"""Read-only hygiene scan of the CareOrganisation / CareUnit hierarchy.

Reform S1 (ticket #397). The 2-level PDL hierarchy lives on the single
self-referential organisations table (parent_caregiver_guid, #187). This
script does NOT mutate anything — it reports any internal org that violates
the 2-level rule so the operator (SU) can fix it:
  - a care unit pointing at a non-existent parent
  - a care unit whose parent is an external partner
  - a care unit whose parent is itself a care unit (>2 levels)
  - a care unit that is its own parent

Run:  python app/scripts/verify_care_hierarchy.py
Exit code 0 = clean, 1 = violations found.

Because the hierarchy already exists in prod (#189 backfilled
parent_caregiver_guid), this is a verification step, not a data migration —
S1 formalises the existing shape rather than restructuring it.
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.db import init_db, get_session          # noqa: E402
from src.services import care_hierarchy as ch     # noqa: E402


def main():
    database_url = os.environ.get('DATABASE_URL', '')
    if not database_url:
        print('ERROR: DATABASE_URL not set. Run inside the app container '
              '(docker-compose run --rm app ...).', file=sys.stderr)
        return 1
    init_db(database_url)
    session = get_session()
    orgs = ch.list_care_organisations(session)
    units = ch.list_care_units(session)
    print(f"Care organisations (vårdgivare): {len(orgs)}")
    for o in orgs:
        n = len(ch.care_units_for_organisation(session, o.guid))
        print(f"  {o.name}  ({o.guid})  — {n} care unit(s)")
    print(f"Care units (vårdenheter): {len(units)}")

    problems = ch.scan_violations(session)
    if not problems:
        print("\nOK — hierarchy is clean (every care unit resolves to a "
              "valid top-level care organisation).")
        return 0
    print(f"\nVIOLATIONS ({len(problems)}):")
    for p in problems:
        print(f"  {p['name']}  ({p['guid']})  — {p['problem']}")
    print("\nFix each (SU) so it points at a valid top-level care "
          "organisation, then re-run.")
    return 1


if __name__ == '__main__':
    sys.exit(main())
