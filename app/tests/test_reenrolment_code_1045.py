"""#1045 (internal security review) — after an SU resets a patient's second
factor, the password alone must not be enough to enrol a new one.

Operator decision "engångskod i display": the reset issues a one-time code,
shown once to the SU, who hands it to the patient after checking who they
are. Every test goes through the real routes: the SU reset over the API (or
the SU page), a password login at /login, the code page, and the passkey /
TOTP enrolment routes.
"""
import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from src.db import get_db, get_session
from src.models.second_factor_reenrolment import SecondFactorReenrolment
from src.services import second_factor as sf
from tests.test_patient_app_1040 import (  # noqa: F401  (fixtures)
    CALLBACK, PW, SECRET, SoftAuthenticator, _enrol_passkey, _password_login,
    _session_token, events, world,
)

CODE_RE = re.compile(r'^[A-HJ-NP-TV-Z2-9]{5}-[A-HJ-NP-TV-Z2-9]{5}$')
REFUSED = 'Koden kunde inte godtas'


def _reset(world, headers=None):
    api = world['app'].test_client()
    r = api.post(f"/api/admin/users/{world['pat']}/second-factor/reset",
                 headers=headers or world['su_h'])
    assert r.status_code == 200, r.get_json()
    time.sleep(1.1)        # iat is whole seconds; the revocation epoch must pass it
    return r


def _enrolled_then_reset(world):
    c = world['app'].test_client()
    _password_login(c)
    _enrol_passkey(c, SoftAuthenticator())
    time.sleep(1.1)
    return _reset(world)


def _type(client, code):
    return client.post('/login/reenrol-code', data={'code': code})


def _row(world):
    with world['app'].app_context():
        s = get_session()
        row = s.query(SecondFactorReenrolment).filter_by(user_guid=world['pat']).first()
        out = None if row is None else {c.name: getattr(row, c.name)
                                        for c in row.__table__.columns}
        s.close()
    return out


def _wrong(code):
    """A well-formed code that is not `code`."""
    first = 'A' if code[0] != 'A' else 'B'
    return first + code[1:]


# --- the reset ---------------------------------------------------------------

def test_reset_returns_the_code_once_and_stores_only_a_keyed_hash(world, caplog):
    caplog.set_level(logging.DEBUG)
    r = _enrolled_then_reset(world)
    body = r.get_json()
    code = body['reenrolment_code']
    assert CODE_RE.match(code)
    assert body['removed'] == 1
    assert body['reenrolment_code_expires_at']
    assert body['message'] == ('Visas bara en gång. Lämna koden till patienten '
                               'efter att du har kontrollerat vem det är.')
    assert r.headers['Cache-Control'] == 'no-store'
    row = _row(world)
    stored = json.dumps(row, default=str)
    assert code not in stored and code.replace('-', '') not in stored
    assert len(row['code_hash']) == 64
    # 72 h
    exp = row['expires_at'].replace(tzinfo=timezone.utc)
    assert timedelta(hours=71, minutes=59) < exp - datetime.now(timezone.utc) <= timedelta(hours=72)
    # Nothing else ever returns it.
    st = world['app'].test_client().get('/api/auth/second-factor', headers=world['su_h'])
    assert code not in st.get_data(as_text=True)


def test_the_code_is_never_in_the_audit_or_the_log(world, caplog):
    caplog.set_level(logging.DEBUG)
    code = _enrolled_then_reset(world).get_json()['reenrolment_code']
    c = world['app'].test_client()
    _password_login(c)
    _type(c, _wrong(code))
    _type(c, code)
    events = json.dumps(world['events'], default=str)
    for form in (code, code.replace('-', ''), _wrong(code), _wrong(code).replace('-', '')):
        assert form not in events
        assert form not in caplog.text
    names = [e['event'] for e in world['events']]
    assert 'second_factor_reset' in names
    assert 'second_factor_reenrol_code_refused' in names
    assert 'second_factor_reenrol_code_used' in names


# --- fail closed without the code --------------------------------------------

def test_password_alone_after_a_reset_gets_no_token_and_cannot_enrol(world):
    _enrolled_then_reset(world)
    app = world['app']
    c = app.test_client()
    r = _password_login(c)
    assert r.headers['Location'].endswith('/login/reenrol-code')
    assert _session_token(c) is None
    page = c.get('/login/reenrol-code').get_data(as_text=True)
    assert 'Kontakta din vårdenhet' in page
    # no session -> the enrolment routes have nobody to enrol
    assert c.post('/second-factor/passkey/options').status_code == 401
    # the handshake mints nothing for a service
    r = _password_login(app.test_client(), next_url=CALLBACK)
    assert 'token=' not in r.headers['Location']
    assert r.headers['Location'].endswith('/login/reenrol-code')
    # the JSON login mints nothing either
    r = app.test_client().post('/api/auth/login', json={'email': 'pat@test.com', 'password': PW})
    assert r.status_code == 403 and r.get_json()['error'] == 'reenrolment_code_required'


def test_a_password_session_token_without_the_code_cannot_enrol(world):
    """Belt and braces: even holding a token for the account (however got),
    a session that did not type the code may not add a factor."""
    _enrolled_then_reset(world)
    from src.services.jwt_service import issue_token
    c = world['app'].test_client()
    with c.session_transaction() as sess:
        sess['token'] = issue_token(world['pat'], SECRET, 1)
    r = c.post('/second-factor/passkey/options')
    assert r.status_code == 403 and r.get_json()['error'] == 'second_factor_required'
    assert c.post('/second-factor/totp/start').status_code == 302     # refused
    assert _row(world)['completed_at'] is None


# --- the code ----------------------------------------------------------------

def test_the_right_code_lets_this_session_enrol_and_lifts_the_gate(world):
    code = _enrolled_then_reset(world).get_json()['reenrolment_code']
    c = world['app'].test_client()
    _password_login(c)
    r = _type(c, code.lower().replace('-', ' '))          # forgiving of case/spacing
    assert r.status_code == 302 and r.headers['Location'].endswith('/second-factor')
    assert _session_token(c) is not None
    r = _enrol_passkey(c, SoftAuthenticator())
    assert r.status_code == 201
    assert _row(world)['completed_at'] is not None
    assert any(e['event'] == 'second_factor_reenrolment_complete' for e in world['events'])
    # Enrolled again: the next password login is the ordinary step-up.
    r = _password_login(world['app'].test_client())
    assert r.headers['Location'].endswith('/login/second-factor')


def test_the_code_works_once(world):
    code = _enrolled_then_reset(world).get_json()['reenrolment_code']
    c1 = world['app'].test_client()
    _password_login(c1)
    assert _type(c1, code).status_code == 302
    # Another browser with the password, before c1 has enrolled: still gated,
    # and the same code is now refused.
    c2 = world['app'].test_client()
    assert _password_login(c2).headers['Location'].endswith('/login/reenrol-code')
    r = _type(c2, code)
    assert r.status_code == 400 and REFUSED in r.get_data(as_text=True)
    assert _session_token(c2) is None
    assert c2.post('/second-factor/passkey/options').status_code == 401
    assert world['events'][-1]['event'] == 'second_factor_reenrol_code_refused'
    assert world['events'][-1]['detail'] == {'reason': 'used'}


def test_a_wrong_code_is_refused_and_counted(world):
    code = _enrolled_then_reset(world).get_json()['reenrolment_code']
    c = world['app'].test_client()
    _password_login(c)
    r = _type(c, _wrong(code))
    assert r.status_code == 400 and REFUSED in r.get_data(as_text=True)
    assert _session_token(c) is None
    assert _row(world)['failed_attempts'] == 1
    r = _type(c, 'nonsense')
    assert r.status_code == 400 and REFUSED in r.get_data(as_text=True)
    assert _row(world)['failed_attempts'] == 2


def test_five_wrong_codes_void_it(world):
    code = _enrolled_then_reset(world).get_json()['reenrolment_code']
    c = world['app'].test_client()
    _password_login(c)
    for _ in range(sf.REENROL_MAX_FAILURES):
        assert _type(c, _wrong(code)).status_code == 400
    assert _row(world)['voided_at'] is not None
    r = _type(c, code)
    assert r.status_code == 400 and REFUSED in r.get_data(as_text=True)
    assert _session_token(c) is None
    assert sum(e['event'] == 'second_factor_reenrol_code_locked'
               for e in world['events']) == 1
    assert world['events'][-1]['detail'] == {'reason': 'void'}


def test_concurrent_wrong_codes_still_void_it(world, monkeypatch):
    """Four failures committed by other workers while this one is checked:
    counted in the database, so this fifth one voids the code."""
    code = _enrolled_then_reset(world).get_json()['reenrolment_code']
    c = world['app'].test_client()
    _password_login(c)
    real = sf._reenrol_hash

    def hash_while_others_fail(*a, **kw):
        get_db().execute(text('UPDATE second_factor_reenrolments '
                              'SET failed_attempts = COALESCE(failed_attempts, 0) + 4'))
        return real(*a, **kw)

    monkeypatch.setattr(sf, '_reenrol_hash', hash_while_others_fail)
    _type(c, _wrong(code))
    monkeypatch.setattr(sf, '_reenrol_hash', real)
    assert _row(world)['voided_at'] is not None
    assert _type(c, code).status_code == 400


def test_an_expired_code_is_refused(world):
    code = _enrolled_then_reset(world).get_json()['reenrolment_code']
    with world['app'].app_context():
        s = get_session()
        s.query(SecondFactorReenrolment).update(
            {SecondFactorReenrolment.expires_at: datetime.now(timezone.utc) - timedelta(seconds=1)})
        s.commit()
        s.close()
    c = world['app'].test_client()
    _password_login(c)
    r = _type(c, code)
    assert r.status_code == 400 and REFUSED in r.get_data(as_text=True)
    assert _session_token(c) is None
    assert any(e['event'] == 'second_factor_reenrol_code_refused'
               and e['detail'].get('reason') == 'expired' for e in world['events'])


def test_a_new_reset_replaces_the_earlier_code_and_its_grant(world):
    old = _enrolled_then_reset(world).get_json()['reenrolment_code']
    c = world['app'].test_client()
    _password_login(c)
    assert _type(c, old).status_code == 302              # c holds a grant for the OLD reset
    new = _reset(world).get_json()['reenrolment_code']
    assert new != old
    # The old session is logged out by the reset, and its grant is dead too.
    with c.session_transaction() as sess:
        from src.services.jwt_service import issue_token
        sess['token'] = issue_token(world['pat'], SECRET, 1)
    assert c.post('/second-factor/passkey/options').status_code == 403
    c2 = world['app'].test_client()
    _password_login(c2)
    assert _type(c2, old).status_code == 400
    assert _type(c2, new).status_code == 302
    assert _enrol_passkey(c2, SoftAuthenticator()).status_code == 201


def test_the_su_page_shows_the_code_once_and_not_in_a_flash(world):
    app = world['app']
    _password_login(c := app.test_client())
    _enrol_passkey(c, SoftAuthenticator())
    time.sleep(1.1)
    su = app.test_client()
    su.post('/login', data={'email': 'su@test.com', 'password': 'propass1234'})
    admin = su.get('/admin').get_data(as_text=True)
    assert '/admin/second-factor-reset' in admin
    r = su.post('/admin/second-factor-reset', data={'user_guid': world['pat']})
    assert r.status_code == 200
    assert r.headers['Cache-Control'] == 'no-store'
    page = r.get_data(as_text=True)
    code = re.search(r'<code>([A-Z0-9]{5}-[A-Z0-9]{5})</code>', page).group(1)
    assert 'Visas bara en gång.' in page
    with su.session_transaction() as sess:
        assert code not in json.dumps(dict(sess), default=str)
    assert code not in su.get('/admin').get_data(as_text=True)
    time.sleep(1.1)
    p = app.test_client()
    _password_login(p)
    assert _type(p, code).status_code == 302
    # #1055: professionals have a (optional) second factor now, and the same
    # page resets it; see test_professional_second_factor_1055.py.
    r = su.post('/admin/second-factor-reset', data={'user_guid': world['pro_a']})
    assert r.status_code == 200
