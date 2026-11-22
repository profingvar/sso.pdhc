#!/usr/bin/env python3
"""Clear a password-login lock from the server (#1040).

For the case the API cannot cover: the only SU admin is locked out and holds
no valid session. The lock ends by itself after 15 minutes; this is for when
that is too long.

    python scripts/unlock_login.py <email>
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()


def main():
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    from src.db import init_db, db_session_scope
    import src.models  # noqa: F401
    from src.models.user import User
    from src.services import login_lockout
    init_db(os.environ['DATABASE_URL'])
    with db_session_scope() as s:
        user = s.query(User).filter_by(email=sys.argv[1].strip()).first()
        if user is None:
            print('No such user.')
            sys.exit(1)
        was = login_lockout.unlock(s, user.guid)
    print('Unlocked.' if was else 'Was not locked; counter cleared.')


if __name__ == '__main__':
    main()
