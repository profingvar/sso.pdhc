"""#1045 (internal security review) — TOTP wrong-code counter must not lose
a failure counted by a concurrent request.

`_check_code` read `failed_attempts` into Python, added one and left the sum
to be committed at teardown. A failure another worker committed between the
read and that commit was overwritten, so parallel wrong codes did not reach
TOTP_MAX_FAILURES and the 15-minute lock never engaged. The test injects
four concurrent failures (raw SQL, so this request's row object stays stale,
as another process's write would) while the code is being checked.
"""
from sqlalchemy import text

from src.db import get_db, get_session
from src.models.second_factor import TotpSecret
from tests.test_patient_app_1040 import (  # noqa: F401  (fixtures)
    _enrol_totp, _fresh_code, _password_login, _session_token, events, world,
)


def test_concurrent_wrong_codes_still_lock_the_factor(world, monkeypatch):
    app = world['app']
    c = app.test_client()
    _password_login(c)
    secret = _enrol_totp(c)

    from src.services import secret_store
    real = secret_store.decrypt

    def decrypt_while_others_fail(*a, **kw):
        get_db().execute(text(
            'UPDATE totp_secrets SET failed_attempts = COALESCE(failed_attempts, 0) + 4'))
        return real(*a, **kw)

    c2 = app.test_client()
    _password_login(c2)
    monkeypatch.setattr(secret_store, 'decrypt', decrypt_while_others_fail)
    c2.post('/login/second-factor/totp', data={'code': '000000'})
    monkeypatch.setattr(secret_store, 'decrypt', real)

    with app.app_context():
        s = get_session()
        row = s.query(TotpSecret).one()
        locked = row.locked_until
        s.close()
    assert locked is not None                    # 4 concurrent + this one = 5
    # and the right code is now refused
    r = c2.post('/login/second-factor/totp', data={'code': _fresh_code(secret)})
    assert r.headers['Location'].endswith('/login/second-factor')
    assert _session_token(c2) is None
