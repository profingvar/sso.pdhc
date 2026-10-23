"""#862 — logging out must end the user's sessions in the SERVICES too.

Reported from icd11.pdhc 2026-10-23: logging out of sso still landed on a
working service page. Not an icd11 bug.

The SSO handshake in `frontend.login_page` mints a SEPARATE token for the
service and does not store it:

    sso's session holds token A
    the service holds token B      (different jti, recorded nowhere)

and `logout` revoked `session['token']` — A. B stayed valid for
SESSION_EXPIRY_HOURS (live: 24), on whatever machine it was left open, across
all 16 services with a registered callback.

The fix uses machinery that already existed: `validate_token` has rejected any
token whose `iat` precedes `User.token_revocation_epoch` since #44's bulk
session flush. Logout now bumps that epoch, so it reaches tokens sso never
recorded.
"""
import time

import pytest

from src.db import get_session
from src.services.jwt_service import (
    TokenRevokedError, issue_token, validate_token,
)

SECRET = 'test-secret-key-not-for-production'


def _service_token(app, user_guid):
    """A token like the one the handshake hands a service: same user, its own
    jti, never stored by sso."""
    with app.app_context():
        return issue_token(user_guid, SECRET, expiry_hours=1)


def _still_valid(app, token):
    with app.app_context():
        try:
            validate_token(token, SECRET, get_session())
            return True
        except TokenRevokedError:
            return False


# =======================================================================
# the defect, reproduced
# =======================================================================

def test_logout_kills_the_SERVICE_token_not_just_its_own(app, client, seeded_app):
    """CONTROL: remove the epoch bump from logout_page and this fails, while
    the service token stays valid exactly as it did in production."""
    d = seeded_app
    service_token = _service_token(app, d['pro_guid'])
    assert _still_valid(app, service_token)
    # The clinician opens the service, works, and logs out LATER. The epoch is
    # floored to the second (so an immediate re-login works), which leaves a
    # sub-second hole; without this the test would be asserting against that
    # hole rather than against the defect.
    time.sleep(1.1)

    # Log in through the browser form, as a clinician does.
    r = client.post('/login', data={'email': d['pro_email'],
                                    'password': d['pro_pw']},
                    follow_redirects=False)
    assert r.status_code in (301, 302), r.status_code

    client.post('/logout', follow_redirects=False)

    assert not _still_valid(app, service_token), (
        "the service's token survived logout — this is #862"
    )


def test_a_token_issued_AFTER_logout_still_works(app, client, seeded_app):
    """The flush must not be a permanent lockout: logging back in has to work."""
    d = seeded_app
    client.post('/login', data={'email': d['pro_email'], 'password': d['pro_pw']})
    client.post('/logout')
    # No sleep. The epoch is floored to the second precisely so an immediate
    # re-login works; sleeping here would hide the bug this asserts against.
    fresh = _service_token(app, d['pro_guid'])
    assert _still_valid(app, fresh)


def test_logging_out_does_not_flush_ANOTHER_user(app, client, seeded_app):
    """A logout is not a platform-wide flush. CONTROL: bump the epoch for every
    user and this fails."""
    d = seeded_app
    other = _service_token(app, d['su_guid'])
    client.post('/login', data={'email': d['pro_email'], 'password': d['pro_pw']})
    client.post('/logout')
    assert _still_valid(app, other), "another user's session was flushed"


# =======================================================================
# the API path too
# =======================================================================

def test_the_API_logout_also_flushes(app, client, seeded_app):
    d = seeded_app
    service_token = _service_token(app, d['pro_guid'])
    time.sleep(1.1)          # see the note above: the token predates the logout
    r = client.post('/api/auth/logout',
                    headers={'Authorization': f"Bearer {d['pro_token']}"})
    assert r.status_code == 200, r.get_data(as_text=True)[:200]
    assert not _still_valid(app, service_token), (
        "the API logout revoked only its own jti"
    )


def test_the_per_jti_revoke_is_KEPT_as_well(app, client, seeded_app):
    """Belt and braces: the epoch is the broad flush, the jti row is the record
    that this particular token was deliberately ended."""
    from src.models.revoked_token import RevokedToken
    d = seeded_app
    client.post('/api/auth/logout',
                headers={'Authorization': f"Bearer {d['pro_token']}"})
    with app.app_context():
        n = get_session().query(RevokedToken).count()
    assert n >= 1, "logout stopped recording the revoked jti"
