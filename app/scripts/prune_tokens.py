#!/usr/bin/env python3
"""Prune expired rows from revoked_tokens (#704).

Every logout inserts a row. Nothing removed one until this script existed:
`prune_expired_tokens` was written, correct, and called by nobody, which the
#704 dead-end triage found. `validate_token` queries this table on every
request on every service — §11 forbids caching the access blob, so each one
revalidates — so it sits on the platform's hot path and had been growing
since March.

A row only does work until the token's own `exp`. After that `decode_token`
raises TokenExpiredError before the revocation check is reached, so the row
cannot change any answer. Deleting at expiry is exact, not approximate.

    python3 scripts/prune_tokens.py --dry-run    # count only, deletes nothing
    python3 scripts/prune_tokens.py              # delete and report

Intended to run from cron, alongside request.pdhc's consent reconciler.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from src.db import init_db, get_session
import src.models  # noqa: F401
from src.models.revoked_token import RevokedToken
from src.services.jwt_service import (
    count_expired_tokens, prune_expired_tokens,
)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--dry-run', action='store_true',
                    help='report what would be removed, change nothing')
    args = ap.parse_args()

    database_url = os.environ.get('DATABASE_URL')
    if not database_url:
        print('ERROR: DATABASE_URL must be set.')
        sys.exit(1)

    init_db(database_url)
    session = get_session()
    try:
        total = session.query(RevokedToken).count()
        expired = count_expired_tokens(session)
        rows = 'row' if total == 1 else 'rows'

        if args.dry_run:
            print(f'revoked_tokens: {total} {rows}, {expired} expired '
                  f'({total - expired} still doing work). Nothing changed.')
            return

        removed = prune_expired_tokens(session)
        session.commit()
        print(f'revoked_tokens: {total} {rows} before, {removed} pruned, '
              f'{total - removed} remain.')
    except Exception as e:
        session.rollback()
        print(f'ERROR: {e}')
        sys.exit(1)
    finally:
        session.close()


if __name__ == '__main__':
    main()
