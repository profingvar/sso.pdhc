"""#1055 (record.pdhc stage 3, decision D4): a second factor for PROFESSIONALS.

The #1040 mechanism, models and pages, made available to professionals, and
OPTIONAL for them: a professional without a factor logs in exactly as before,
because every PDHC service relies on that login. Once a factor is enrolled the
login asks for it, and the token (and the service's token) carries the claim.

Every test goes through the real routes with authentication on, using the
#1040 world (two units, professionals pro_a/pro_b, an SU, a patient) and its
software authenticator: nothing in the WebAuthn or TOTP verification is mocked.
"""
import re
import time

import pyotp

from tests.test_patient_app_1040 import (  # noqa: F401  (fixtures)
    CALLBACK, SoftAuthenticator, _blob, _enrol_passkey, _enrol_totp,
    _fresh_code, _session_token, _token_from, events, world,
)

PRO = 'a@test.com'
PRO_PW = 'propass1234'
UNSET = {'enrolled': False, 'verified': False, 'method': None, 'verified_at': None}


def _login(client, email=PRO, next_url=None):
    data = {'email': email, 'password': PRO_PW}
    if next_url:
        data.update(next=next_url, state='st1')
    return client.post('/login', data=data)


def _enrolled_pro(world, totp=False):
    """pro_a logged in (password only, no factor yet) and enrolled."""
    c = world['app'].test_client()
    r = _login(c)
    assert r.status_code == 302 and r.headers['Location'].endswith('/dashboard')
    auth = SoftAuthenticator()
    if totp:
        secret = _enrol_totp(c)
        return c, secret
    assert _enrol_passkey(c, auth).status_code == 201
    return c, auth


# --- 1. without a factor: exactly as before, plus the blob object -------------

def test_professional_without_factor_logs_in_as_before_and_blob_says_so(world):
    app = world['app']
    c = app.test_client()
    r = _login(c, next_url=CALLBACK)
    # Straight to the service with a token, no second-factor page.
    assert r.status_code == 302 and r.headers['Location'].startswith(CALLBACK + '?')
    blob = _blob(app, _token_from(r.headers['Location']))
    assert blob['user_type'] == 'professional'
    assert blob['second_factor'] == UNSET
    # The JSON handshake is unchanged too.
    r = app.test_client().post('/api/auth/login', json={
        'email': PRO, 'password': PRO_PW, 'next': CALLBACK})
    assert r.status_code == 302 and r.headers['Location'].startswith(CALLBACK + '?token=')


def test_unactivated_professional_blob_also_carries_second_factor(world):
    blob = _blob(world['app'], world['pending_h']['Authorization'][7:])
    assert blob.get('activation_pending') is True
    assert blob['second_factor'] == UNSET


# --- 2. enrolment from sso's own page -----------------------------------------

def test_professional_reaches_the_security_page_and_enrols_a_passkey(world):
    app = world['app']
    c = app.test_client()
    _login(c)
    assert '/second-factor' in c.get('/dashboard').get_data(as_text=True)
    page = c.get('/second-factor')
    assert page.status_code == 200
    text = page.get_data(as_text=True)
    assert 'Säkerhet / andra faktor' in text and 'patientappen' not in text.lower()
    assert _blob(app, _session_token(c))['second_factor']['verified'] is False
    assert _enrol_passkey(c, SoftAuthenticator()).status_code == 201
    sf = _blob(app, _session_token(c))['second_factor']
    assert sf['enrolled'] is True and sf['verified'] is True and sf['method'] == 'passkey'


# --- 3. with a factor: the login asks for it ----------------------------------

def test_enrolled_professional_must_present_passkey_and_token_is_verified(world):
    app = world['app']
    _c, auth = _enrolled_pro(world)
    c2 = app.test_client()
    r = _login(c2, next_url=CALLBACK)
    assert r.status_code == 302 and r.headers['Location'].endswith('/login/second-factor')
    assert _session_token(c2) is None            # nothing for the password alone
    assert 'Ditt konto har en andra faktor' in c2.get('/login/second-factor').get_data(as_text=True)
    opts = c2.post('/login/second-factor/passkey/options').get_json()
    done = c2.post('/login/second-factor/passkey/verify', json=auth.assertion(opts))
    assert done.status_code == 200, done.get_json()
    target = done.get_json()['redirect']
    assert target.startswith(CALLBACK + '?') and 'state=st1' in target
    sf = _blob(app, _token_from(target))['second_factor']
    assert sf['enrolled'] is True and sf['verified'] is True and sf['method'] == 'passkey'
    assert sf['verified_at']


def test_enrolled_professional_totp_login(world):
    app = world['app']
    _c, secret = _enrolled_pro(world, totp=True)
    c2 = app.test_client()
    assert _login(c2).headers['Location'].endswith('/login/second-factor')
    r = c2.post('/login/second-factor/totp', data={'code': _fresh_code(secret)})
    assert r.status_code == 302 and r.headers['Location'].endswith('/dashboard')
    sf = _blob(app, _session_token(c2))['second_factor']
    assert sf['verified'] is True and sf['method'] == 'totp'


def test_enrolled_professional_json_handshake_gets_no_token(world):
    app = world['app']
    _enrolled_pro(world)
    r = app.test_client().post('/api/auth/login', json={
        'email': PRO, 'password': PRO_PW, 'next': CALLBACK})
    assert r.status_code == 302
    assert r.headers['Location'].startswith('/login?') and 'token=' not in r.headers['Location']
    # Without `next`: a token, honestly NOT verified (same as a patient's).
    r = app.test_client().post('/api/auth/login', json={'email': PRO, 'password': PRO_PW})
    assert _blob(app, r.get_json()['token'])['second_factor'] == {
        'enrolled': True, 'verified': False, 'method': None, 'verified_at': None}


def test_logged_in_enrolled_professional_handshake_carries_the_claim(world):
    app = world['app']
    c, _auth = _enrolled_pro(world)
    r = c.get('/login?next=' + CALLBACK + '&state=zz')
    assert r.status_code == 302 and r.headers['Location'].startswith(CALLBACK + '?')
    minted = _token_from(r.headers['Location'])
    assert minted != _session_token(c)
    assert _blob(app, minted)['second_factor']['method'] == 'passkey'


# --- 4. SU login ---------------------------------------------------------------

def test_su_without_factor_logs_in_as_before_and_with_factor_steps_up(world):
    app = world['app']
    su = app.test_client()
    r = _login(su, email='su@test.com')
    assert r.headers['Location'].endswith('/dashboard')
    assert su.get('/admin').status_code == 200
    assert _blob(app, _session_token(su))['second_factor'] == UNSET
    _secret = _enrol_totp(su)
    su2 = app.test_client()
    assert _login(su2, email='su@test.com').headers['Location'].endswith('/login/second-factor')
    su2.post('/login/second-factor/totp', data={'code': _fresh_code(_secret)})
    assert su2.get('/admin').status_code == 200
    assert _blob(app, _session_token(su2))['second_factor']['method'] == 'totp'


# --- 5. SU reset of a professional's factor ------------------------------------

def test_su_reset_of_professional_code_lets_them_back_in_without_forcing_enrolment(world):
    app = world['app']
    _enrolled_pro(world)
    time.sleep(1.1)
    su = app.test_client()
    _login(su, email='su@test.com')
    r = su.post('/admin/second-factor-reset', data={'user_guid': world['pro_a']})
    assert r.status_code == 200
    page = r.get_data(as_text=True)
    code = re.search(r'<code>([A-Z0-9]{5}-[A-Z0-9]{5})</code>', page).group(1)
    assert 'Lämna koden till personen' in page
    time.sleep(1.1)
    # The password alone: no token (browser or JSON) until the code is typed.
    p = app.test_client()
    assert _login(p).headers['Location'].endswith('/login/reenrol-code')
    assert _session_token(p) is None
    j = app.test_client().post('/api/auth/login', json={'email': PRO, 'password': PRO_PW})
    assert j.status_code == 403 and j.get_json()['error'] == 'reenrolment_code_required'
    r = p.post('/login/reenrol-code', data={'code': code})
    assert r.status_code == 302 and r.headers['Location'].endswith('/second-factor')
    assert _blob(app, _session_token(p))['second_factor'] == UNSET
    # The code ENDED the reset: a later login is the plain professional login.
    p2 = app.test_client()
    r = _login(p2, next_url=CALLBACK)
    assert r.headers['Location'].startswith(CALLBACK + '?token=')


def test_su_reset_json_route_message_names_the_person_for_a_professional(world):
    _enrolled_pro(world)
    r = world['app'].test_client().post(
        f"/api/admin/users/{world['pro_a']}/second-factor/reset", headers=world['su_h'])
    assert r.status_code == 200
    assert 'personen' in r.get_json()['message']


# --- 6. patients unchanged ------------------------------------------------------

def test_patient_second_factor_pages_keep_their_text(world):
    c = world['app'].test_client()
    c.post('/login', data={'email': 'pat@test.com', 'password': 'patpass1234'})
    text = c.get('/second-factor').get_data(as_text=True)
    assert 'Du behöver lägga till en andra faktor innan du kan använda patientappen.' in text
    assert 'Säkerhet / andra faktor' not in text
    _enrol_passkey(c, SoftAuthenticator())
    c2 = world['app'].test_client()
    c2.post('/login', data={'email': 'pat@test.com', 'password': 'patpass1234'})
    assert 'Som patient loggar du också in med en andra faktor.' in \
        c2.get('/login/second-factor').get_data(as_text=True)
