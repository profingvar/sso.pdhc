"""Temporary lockout of PASSWORD login after repeated wrong passwords (#1040).

Called from `auth_service.authenticate_user`, the one function both doors go
through (the HTML form at /login and POST /api/auth/login), so the two cannot
differ.

The rule:
  * MAX_FAILURES consecutive wrong passwords lock password login for that
    account for LOCK_DURATION. A correct password is refused while locked.
  * A success resets the counter. A failure older than LOCK_DURATION no longer
    counts, so five mistakes spread over a year do not add up.
  * TIME-BASED ONLY. The lock always ends by itself; nothing escalates and
    nothing is permanent. Attempts made WHILE locked neither extend the lock
    nor count, so hammering a locked account does not keep it locked.

What that does not prevent, stated: someone who knows an email address can
re-lock the account with five wrong passwords every fifteen minutes. That is
inherent in any lockout keyed on the account. It applies to an SU admin too;
the ways out are another SU (`POST /api/admin/users/<guid>/unlock-login`), an
SU's own still-valid session, or `scripts/unlock_login.py` on the server.

No enumeration: `authenticate_user` answers None for an unknown email, a wrong
password and a locked account alike, and does the bcrypt work in all three.
Unknown emails get no row here, so the table cannot be filled by guessing.

This is separate from the TOTP lock in services/second_factor.py, which
guards the second step and is unchanged.
"""
from datetime import datetime, timedelta, timezone

from src.models.login_lockout import LoginLockout

MAX_FAILURES = 5
LOCK_DURATION = timedelta(minutes=15)


def _now():
    """The clock. Tests replace this; nothing sleeps."""
    return datetime.now(timezone.utc)


def _aware(dt):
    return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _row(session, user_guid):
    return session.query(LoginLockout).filter_by(user_guid=user_guid).first()


def locked_until(session, user_guid):
    """When the lock ends, or None if the account is not locked now."""
    row = _row(session, user_guid)
    until = _aware(row.locked_until) if row else None
    return until if until is not None and until > _now() else None


def record_failure(session, user_guid):
    """Count one wrong password. Returns the new lock end if this failure
    locked the account, else None."""
    now = _now()
    row = _row(session, user_guid)
    if row is None:
        row = LoginLockout(user_guid=user_guid, failed_count=0)
        session.add(row)
    last = _aware(row.last_failed_at)
    if last is None or now - last > LOCK_DURATION:
        row.failed_count = 0
    row.failed_count = (row.failed_count or 0) + 1
    row.last_failed_at = now
    row.updated_at = now
    if row.failed_count >= MAX_FAILURES:
        row.locked_until = now + LOCK_DURATION
        row.failed_count = 0
        session.flush()
        return row.locked_until
    session.flush()
    return None


def record_success(session, user_guid):
    row = _row(session, user_guid)
    if row is not None and (row.failed_count or row.locked_until is not None):
        row.failed_count = 0
        row.locked_until = None
        row.updated_at = _now()


def unlock(session, user_guid):
    """Clear the lock and the counter. True if there was a live lock."""
    row = _row(session, user_guid)
    if row is None:
        return False
    was_locked = locked_until(session, user_guid) is not None
    row.failed_count = 0
    row.locked_until = None
    row.updated_at = _now()
    return was_locked


def currently_locked(session):
    """[(user_guid, locked_until)] for every account locked right now."""
    now = _now()
    out = []
    for row in session.query(LoginLockout).filter(
            LoginLockout.locked_until.isnot(None)).all():
        until = _aware(row.locked_until)
        if until > now:
            out.append((row.user_guid, until))
    return out
