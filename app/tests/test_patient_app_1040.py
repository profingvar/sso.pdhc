"""#1040 — second factor for patient accounts, and the account <-> patient link.

Every test goes through the real routes with authentication on: a password
login at /login, the WebAuthn / TOTP ceremony, and the access blob read back
through /api/auth/me/service with service credentials — the path every
consuming service uses.

Passkeys are exercised with a SOFTWARE AUTHENTICATOR (below): a real P-256 key
producing real attestation objects and signed assertions, verified by
py_webauthn. Nothing in the verification is mocked, so a wrong origin, a wrong
challenge or a foreign key fails for the real reason.

`audit` is replaced by a recorder in every module that calls it: the SSO audit
log is a FILE, not a transaction, so a test must not leave events in it — and
the recorder is what lets a test assert that a link was audited.

Personnummer here are synthetic and deliberately fail the checksum (#405).
"""
import hashlib
import json
import os
import re
import struct
import time
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import cbor2
import pyotp
import pytest
import requests
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url

from src.app import create_app
from src.db import get_session
from src.middleware.rate_limit import reset_rate_limits
from src.services.auth_service import hash_password
from src.services.jwt_service import issue_token

SECRET = 'test-secret-key-not-for-production'
RP_ID = 'localhost'
ORIGIN = 'http://localhost:9000'
CALLBACK = 'http://localhost:9000/callback'
SVC = {'X-SSO-Client-Id': 'test-client', 'X-SSO-Client-Secret': 'test-secret'}
PW = 'patpass1234'


# --- a software authenticator ------------------------------------------------

class SoftAuthenticator:
    """A passkey in software: one P-256 credential, user-verifying."""

    def __init__(self, rp_id=RP_ID, origin=ORIGIN):
        self.rp_id, self.origin = rp_id, origin
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.cred_id = os.urandom(32)
        self.count = 0

    def _client_data(self, typ, challenge):
        return json.dumps({'type': typ, 'challenge': challenge,
                           'origin': self.origin, 'crossOrigin': False}).encode()

    def _auth_data(self, attested):
        self.count += 1
        flags = 0x01 | 0x04 | (0x40 if attested else 0)      # UP | UV | AT
        data = hashlib.sha256(self.rp_id.encode()).digest() + bytes([flags]) \
            + struct.pack('>I', self.count)
        if attested:
            nums = self.key.public_key().public_numbers()
            cose = cbor2.dumps({1: 2, 3: -7, -1: 1,
                                -2: nums.x.to_bytes(32, 'big'),
                                -3: nums.y.to_bytes(32, 'big')})
            data += bytes(16) + struct.pack('>H', len(self.cred_id)) + self.cred_id + cose
        return data

    def register(self, options):
        client = self._client_data('webauthn.create', options['challenge'])
        att = cbor2.dumps({'fmt': 'none', 'attStmt': {},
                           'authData': self._auth_data(True)})
        cid = bytes_to_base64url(self.cred_id)
        return {'id': cid, 'rawId': cid, 'type': 'public-key',
                'response': {'clientDataJSON': bytes_to_base64url(client),
                             'attestationObject': bytes_to_base64url(att),
                             'transports': ['internal']}}

    def assertion(self, options, challenge=None):
        client = self._client_data('webauthn.get', challenge or options['challenge'])
        auth = self._auth_data(False)
        sig = self.key.sign(auth + hashlib.sha256(client).digest(),
                            ec.ECDSA(hashes.SHA256()))
        cid = bytes_to_base64url(self.cred_id)
        return {'id': cid, 'rawId': cid, 'type': 'public-key',
                'response': {'clientDataJSON': bytes_to_base64url(client),
                             'authenticatorData': bytes_to_base64url(auth),
                             'signature': bytes_to_base64url(sig)}}


# --- a fake eps: the ONLY thing patched is the outbound HTTP call -------------

class _Resp:
    def __init__(self, status, body, headers=None):
        self.status_code, self._body, self.headers = status, body, headers or {}

    def json(self):
        if isinstance(self._body, str):
            raise ValueError('not json')
        return self._body


class FakeEps:
    """Stands where `eps_client._http_get` is. `patients` maps a patient guid
    to the clinics eps would list, in eps's own shape (models/clinic.py):
    each clinic has its OWN `guid` and the sso unit as `care_unit_guid`."""

    def __init__(self):
        self.patients, self.mode, self.calls = {}, 'ok', []
        self.canonical = {}     # identifier asked for -> platform guid eps names
        # #1050: the personnummer eps holds per patient guid (12 digits), for
        # POST /patients/<guid>/identifier-match (the invite page).
        self.pnrs, self.match_calls = {}, []

    def post(self, url, headers, json_body, timeout):
        """Where `eps_client._http_post` is, for identifier-match only."""
        assert url.endswith('/identifier-match'), url
        self.match_calls.append({'url': url, 'headers': dict(headers),
                                 'json': dict(json_body)})
        if self.mode == 'down':
            raise requests.ConnectionError('refused')
        if self.mode == 'foreign404':
            return _Resp(404, {'error': 'not_found', 'service': '1177.pdhc'})
        guid = url.rsplit('/patients/', 1)[1].split('/')[0]
        if guid not in self.patients:
            return _Resp(404, {'error': 'Patient not found'})
        return _Resp(200, {'match': self.pnrs.get(guid) == json_body.get('personnummer')})

    def list_at(self, patient, *units, active=True, clinic_guid=None):
        self.patients[patient] = [
            {'guid': clinic_guid or str(uuid.uuid4()), 'organisation_guid': u,
             'care_unit_guid': u, 'is_active': active, 'name': 'Clinic'}
            for u in units]

    def __call__(self, url, headers, timeout):
        self.calls.append({'url': url, 'headers': dict(headers)})
        if self.mode == 'down':
            raise requests.ConnectionError('refused')
        if self.mode == 'timeout':
            raise requests.Timeout('slow')
        if self.mode in (401, 403, 500, 502, 302):
            return _Resp(self.mode, {'resourceType': 'OperationOutcome'})
        if self.mode == 'foreign404':        # another vhost answering for eps
            return _Resp(404, {'error': 'not_found', 'service': '1177.pdhc'})
        if self.mode == 'html404':
            return _Resp(404, '<html>404</html>')
        if self.mode == 'garbage200':
            return _Resp(200, {'unexpected': True})
        guid = url.rsplit('/patients/', 1)[1].split('/')[0]
        if guid not in self.patients:
            return _Resp(404, {'error': 'Patient not found'})
        return _Resp(200, self.patients[guid],
                     {'X-PDHC-Patient-Guid': self.canonical.get(guid, guid)})


# --- fixtures ----------------------------------------------------------------

@pytest.fixture
def events(monkeypatch):
    """Record audit events instead of writing the audit FILE."""
    seen = []

    def record(event, user_guid=None, detail=None, ip=None):
        seen.append({'event': event, 'user_guid': user_guid, 'detail': detail or {}})

    for mod in ('src.services.patient_link', 'src.services.patient_enrolment',
                'src.routes.second_factor_ui', 'src.routes.patient_access',
                'src.routes.frontend', 'src.routes.auth'):
        monkeypatch.setattr(mod + '.audit', record)
    return seen


def _make_app(**extra):
    reset_rate_limits()
    cfg = {
        'TESTING': True, 'SECRET_KEY': SECRET, 'WTF_CSRF_ENABLED': False,
        'SESSION_EXPIRY_HOURS': 1,
        'ALLOWED_ORIGINS': ['http://localhost:9000'],
        'ALLOWED_CALLBACK_URLS': [CALLBACK],
        'SERVICE_CREDENTIALS': {'test-client': 'test-secret'},
        'WEBAUTHN_RP_ID': RP_ID, 'WEBAUTHN_ORIGIN': ORIGIN,
        'PATIENTAPP_URL': 'https://patientapp.example.test',
        'EPS_BASE_URL': 'http://eps.test', 'EPS_API_KEY': 'eps-test-key',
        'FLASK_ENV': 'production', 'EPS_LINK_CHECK_DISABLED': '',
    }
    cfg.update(extra)
    return create_app(cfg)


@pytest.fixture
def world(events, monkeypatch):
    """Two care units, a professional affiliated to each, an SU, a patient
    registered at unit A (not linked, no second factor). eps is the fake
    above and lists IPS_1 and IPS_2 at unit A."""
    eps = FakeEps()
    monkeypatch.setattr('src.services.eps_client._http_get', eps)
    monkeypatch.setattr('src.services.eps_client._http_post', eps.post)
    from src.models.affiliation import Affiliation
    from src.models.organisation import Organisation
    from src.models.patient import Patient
    from src.models.professional import Professional
    from src.models.role import seed_roles
    from src.models.user import User

    app = _make_app()
    with app.app_context():
        s = get_session()
        roles = seed_roles(s)
        unit_a, unit_b = Organisation(name='Unit A'), Organisation(name='Unit B')
        s.add_all([unit_a, unit_b])
        s.flush()

        def pro(email, unit=None, su=False, status='active'):
            u = User(email=email, password_hash=hash_password('propass1234'),
                     user_type='professional', is_su_admin=su, status=status)
            s.add(u)
            s.flush()
            s.add(Professional(user_id=u.id, professional_role='nurse',
                               first_name='P', last_name='P'))
            if unit is not None:
                s.add(Affiliation(person_guid=u.guid, care_unit_guid=unit.guid,
                                  role_guid=roles['nurse'].guid, status='active'))
            return u

        pro_a, pro_b = pro('a@test.com', unit_a), pro('b@test.com', unit_b)
        pending = pro('pending@test.com', unit_a, status='pending')
        su = pro('su@test.com', su=True)
        pat = User(email='pat@test.com', password_hash=hash_password(PW),
                   user_type='patient')
        s.add(pat)
        s.flush()
        s.add(Patient(user_id=pat.id, personnummer='199001011234',
                      organisation_guid=unit_a.guid))
        s.commit()
        w = {
            'app': app, 'unit_a': unit_a.guid, 'unit_b': unit_b.guid,
            'pat': pat.guid, 'pro_a': pro_a.guid, 'pro_b': pro_b.guid,
            'su': su.guid, 'events': events, 'eps': eps,
        }
        eps.list_at(IPS_1, unit_a.guid)
        eps.list_at(IPS_2, unit_a.guid)
        eps.pnrs.update({IPS_1: '199003031234', IPS_2: '199003031234'})
        for name, u in (('pro_a', pro_a), ('pro_b', pro_b), ('su', su),
                        ('pat', pat), ('pending', pending)):
            w[name + '_h'] = {'Authorization': 'Bearer ' + issue_token(u.guid, SECRET, 1)}
        s.close()
    return w


def _blob(app, token):
    r = app.test_client().get('/api/auth/me/service',
                              headers={'Authorization': 'Bearer ' + token, **SVC})
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def _password_login(client, email='pat@test.com', pw=PW, next_url=None):
    data = {'email': email, 'password': pw}
    if next_url:
        data.update(next=next_url, state='st1')
    return client.post('/login', data=data)


def _session_token(client):
    with client.session_transaction() as sess:
        return sess.get('token')


def _token_from(location):
    return parse_qs(urlparse(location).query)['token'][0]


def _enrol_passkey(client, auth):
    opts = client.post('/second-factor/passkey/options')
    assert opts.status_code == 200, opts.get_json()
    return client.post('/second-factor/passkey/verify',
                       json={'credential': auth.register(opts.get_json()), 'name': 'Test'})


def _enrol_totp(client):
    page = client.post('/second-factor/totp/start')
    assert page.status_code == 200
    secret = re.search(r'<code[^>]*>([A-Z2-7]{32})</code>', page.get_data(as_text=True)).group(1)
    r = client.post('/second-factor/totp/confirm',
                    data={'code': pyotp.TOTP(secret).now()})
    assert r.status_code == 302
    return secret


def _fresh_code(secret, steps_ahead=1):
    """A valid code that the replay guard has not seen (the next 30 s step)."""
    return pyotp.TOTP(secret).at(time.time() + 30 * steps_ahead)


IPS_1 = '11111111-1111-4111-8111-111111111111'
IPS_2 = '22222222-2222-4222-8222-222222222222'
IPS_3 = '33333333-3333-4333-8333-333333333333'


# =============================================================================
# 1. The blob (contract s1.1)
# =============================================================================

def test_patient_blob_carries_both_objects_before_anything_is_set_up(world):
    blob = _blob(world['app'], world['pat_h']['Authorization'][7:])
    assert blob['user_type'] == 'patient'
    assert 'patient_link' in blob and blob['patient_link'] is None
    assert blob.get('second_factor') == {'enrolled': False, 'verified': False,
                                     'method': None, 'verified_at': None}


def test_professional_blob_has_no_patient_link(world):
    # #1055: a professional's blob now carries `second_factor` too (record.pdhc
    # D4); `patient_link` stays patient-only.
    blob = _blob(world['app'], world['pro_a_h']['Authorization'][7:])
    assert 'patient_link' not in blob
    assert blob['second_factor'] == {'enrolled': False, 'verified': False,
                                     'method': None, 'verified_at': None}


# =============================================================================
# 2. Passkey: enrol, then log in with it
# =============================================================================

def test_passkey_enrol_then_login_gives_a_verified_token(world):
    app, auth = world['app'], SoftAuthenticator()
    c = app.test_client()
    r = _password_login(c)
    assert r.headers['Location'].endswith('/second-factor')
    assert _blob(app, _session_token(c))['second_factor']['verified'] is False
    assert _enrol_passkey(c, auth).status_code == 201
    # Enrolling proved possession: the session token now carries the claim.
    assert _blob(app, _session_token(c))['second_factor'] == {
        'enrolled': True, 'verified': True, 'method': 'passkey',
        'verified_at': _blob(app, _session_token(c))['second_factor']['verified_at']}

    # A new browser: password, then the passkey, then on to the service.
    c2 = app.test_client()
    r = _password_login(c2, next_url=CALLBACK)
    assert r.status_code == 302 and r.headers['Location'].endswith('/login/second-factor')
    assert _session_token(c2) is None           # no token for the password alone
    opts = c2.post('/login/second-factor/passkey/options').get_json()
    done = c2.post('/login/second-factor/passkey/verify', json=auth.assertion(opts))
    assert done.status_code == 200, done.get_json()
    target = done.get_json()['redirect']
    assert target.startswith(CALLBACK + '?') and 'state=st1' in target
    sf = _blob(app, _token_from(target))['second_factor']
    assert sf['enrolled'] is True and sf['verified'] is True and sf['method'] == 'passkey'
    assert datetime.fromisoformat(sf['verified_at']).tzinfo is not None
    assert any(e['event'] == 'login_success' and e['detail'].get('second_factor') == 'passkey'
               for e in world['events'])


def test_verified_is_about_the_token_not_the_account(world):
    """An enrolled account logging in with the password alone (JSON API) gets a
    token, and that token is NOT verified."""
    app = world['app']
    c = app.test_client()
    _password_login(c)
    assert _enrol_passkey(c, SoftAuthenticator()).status_code == 201
    r = app.test_client().post('/api/auth/login',
                               json={'email': 'pat@test.com', 'password': PW})
    sf = _blob(app, r.get_json()['token'])['second_factor']
    assert sf == {'enrolled': True, 'verified': False, 'method': None, 'verified_at': None}


def test_password_alone_never_reaches_the_service_callback(world):
    app = world['app']
    c = app.test_client()
    _password_login(c)
    assert _enrol_passkey(c, SoftAuthenticator()).status_code == 201
    c2 = app.test_client()
    r = _password_login(c2, next_url=CALLBACK)
    assert CALLBACK not in r.headers['Location']
    assert 'token=' not in r.headers['Location']
    # ...and asking again with `next` while only password-verified changes nothing.
    r = c2.get('/login?next=' + CALLBACK)
    assert 'token=' not in r.headers.get('Location', '')


def test_unenrolled_patient_is_sent_to_enrol_not_to_the_service(world):
    c = world['app'].test_client()
    r = _password_login(c, next_url=CALLBACK)
    assert r.headers['Location'].endswith('/second-factor')
    r = c.get('/login?next=' + CALLBACK)
    assert r.headers['Location'].endswith('/second-factor')
    # Enrolling resumes the journey to the service, now with a verified token.
    done = _enrol_passkey(c, SoftAuthenticator())
    target = done.get_json()['redirect']
    assert target.startswith(CALLBACK + '?')
    assert _blob(world['app'], _token_from(target))['second_factor']['verified'] is True


def test_logged_in_verified_patient_handshake_carries_the_claim(world):
    app = world['app']
    c = app.test_client()
    _password_login(c)
    _enrol_passkey(c, SoftAuthenticator())
    r = c.get('/login?next=' + CALLBACK + '&state=zz')
    assert r.status_code == 302 and r.headers['Location'].startswith(CALLBACK + '?')
    minted = _token_from(r.headers['Location'])
    assert minted != _session_token(c)           # a separate token for the service
    assert _blob(app, minted)['second_factor']['method'] == 'passkey'


def test_assertion_for_the_wrong_site_is_refused(world):
    app, auth = world['app'], SoftAuthenticator()
    c = app.test_client()
    _password_login(c)
    _enrol_passkey(c, auth)
    c2 = app.test_client()
    _password_login(c2)
    opts = c2.post('/login/second-factor/passkey/options').get_json()
    auth.origin = 'https://evil.example'
    r = c2.post('/login/second-factor/passkey/verify', json=auth.assertion(opts))
    assert r.status_code == 401 and r.get_json()['error'] == 'passkey_invalid'
    assert _session_token(c2) is None


def test_a_challenge_is_single_use(world):
    app, auth = world['app'], SoftAuthenticator()
    c = app.test_client()
    _password_login(c)
    _enrol_passkey(c, auth)
    c2 = app.test_client()
    _password_login(c2)
    opts = c2.post('/login/second-factor/passkey/options').get_json()
    auth.origin = 'https://evil.example'
    assert c2.post('/login/second-factor/passkey/verify',
                   json=auth.assertion(opts)).status_code == 401
    auth.origin = ORIGIN
    # The same challenge again, now correctly signed: it was consumed.
    r = c2.post('/login/second-factor/passkey/verify', json=auth.assertion(opts))
    assert r.status_code == 400 and r.get_json()['error'] == 'no_challenge'


def test_another_accounts_passkey_cannot_finish_this_login(world):
    """A valid passkey, validly signed, belonging to a DIFFERENT account."""
    from src.models.patient import Patient
    from src.models.user import User
    app = world['app']
    with app.app_context():
        s = get_session()
        other = User(email='other@test.com', password_hash=hash_password(PW),
                     user_type='patient')
        s.add(other)
        s.flush()
        s.add(Patient(user_id=other.id, personnummer='199002021234',
                      organisation_guid=world['unit_a']))
        s.commit()
        s.close()
    mine, theirs = SoftAuthenticator(), SoftAuthenticator()
    c = app.test_client()
    _password_login(c)
    _enrol_passkey(c, mine)
    co = app.test_client()
    _password_login(co, email='other@test.com')
    assert _enrol_passkey(co, theirs).status_code == 201

    c2 = app.test_client()
    _password_login(c2)                                    # pat's password
    opts = c2.post('/login/second-factor/passkey/options').get_json()
    r = c2.post('/login/second-factor/passkey/verify', json=theirs.assertion(opts))
    assert r.status_code == 401
    assert _session_token(c2) is None


def test_passkeys_unconfigured_are_refused_by_name(events):
    from src.models.user import User
    app = _make_app(WEBAUTHN_RP_ID='', WEBAUTHN_ORIGIN='')
    with app.app_context():
        s = get_session()
        s.add(User(email='p@test.com', password_hash=hash_password(PW), user_type='patient'))
        s.commit()
        s.close()
    c = app.test_client()
    _password_login(c, email='p@test.com')
    r = c.post('/second-factor/passkey/options')
    assert r.status_code == 503 and r.get_json()['error'] == 'passkey_not_configured'


# =============================================================================
# 3. TOTP (the reserve)
# =============================================================================

def test_totp_enrol_then_login(world):
    from src.models.second_factor import TotpSecret
    app = world['app']
    c = app.test_client()
    _password_login(c)
    secret = _enrol_totp(c)
    with app.app_context():
        s = get_session()
        row = s.query(TotpSecret).filter_by(user_guid=world['pat']).one()
        stored = row.secret_enc
        s.close()
    # Encrypted at rest: the base32 secret is not what is in the column.
    assert secret not in stored and stored.startswith('v1:')

    c2 = app.test_client()
    r = _password_login(c2, next_url=CALLBACK)
    assert r.headers['Location'].endswith('/login/second-factor')
    step = c2.get('/login/second-factor')
    assert step.status_code == 200 and b'Kod fr' in step.data
    r = c2.post('/login/second-factor/totp', data={'code': _fresh_code(secret)})
    assert r.status_code == 302 and r.headers['Location'].startswith(CALLBACK + '?')
    # The management page renders for the enrolled, verified patient.
    assert b'Kopplad' in c2.get('/second-factor').data
    # Without a pending login the step-up page sends you back to /login.
    assert app.test_client().get('/login/second-factor').headers['Location'].endswith('/login')
    sf = _blob(app, _token_from(r.headers['Location']))['second_factor']
    assert sf['verified'] is True and sf['method'] == 'totp'


def test_totp_code_cannot_be_used_twice(world):
    app = world['app']
    c = app.test_client()
    _password_login(c)
    secret = _enrol_totp(c)
    code = _fresh_code(secret)
    c2 = app.test_client()
    _password_login(c2)
    assert c2.post('/login/second-factor/totp', data={'code': code}
                   ).headers['Location'].endswith('/dashboard')
    c3 = app.test_client()
    _password_login(c3)
    r = c3.post('/login/second-factor/totp', data={'code': code})
    assert r.headers['Location'].endswith('/login/second-factor')
    assert _session_token(c3) is None


def test_totp_locks_after_five_wrong_codes(world):
    app = world['app']
    c = app.test_client()
    _password_login(c)
    secret = _enrol_totp(c)
    c2 = app.test_client()
    _password_login(c2)
    for _ in range(5):
        c2.post('/login/second-factor/totp', data={'code': '000000'})
    # Now even the RIGHT code is refused: the account's TOTP is locked.
    r = c2.post('/login/second-factor/totp', data={'code': _fresh_code(secret)})
    assert r.headers['Location'].endswith('/login/second-factor')
    assert _session_token(c2) is None
    assert any(e['event'] == 'second_factor_fail' and e['detail']['reason'] == 'totp_locked'
               for e in world['events'])


def test_unconfirmed_totp_is_not_a_second_factor(world):
    app = world['app']
    c = app.test_client()
    _password_login(c)
    assert c.post('/second-factor/totp/start').status_code == 200   # never confirmed
    assert _blob(app, _session_token(c))['second_factor']['enrolled'] is False
    r = _password_login(app.test_client())
    assert r.headers['Location'].endswith('/second-factor')         # still enrolment


# =============================================================================
# 4. Managing factors
# =============================================================================

def test_the_last_factor_cannot_be_removed(world):
    from src.models.second_factor import WebAuthnCredential
    app = world['app']
    c = app.test_client()
    _password_login(c)
    _enrol_passkey(c, SoftAuthenticator())
    with app.app_context():
        s = get_session()
        guid = s.query(WebAuthnCredential).filter_by(user_guid=world['pat']).one().guid
        s.close()
    c.post('/second-factor/remove', data={'kind': 'passkey', 'guid': guid})
    assert _blob(app, _session_token(c))['second_factor']['enrolled'] is True


def test_a_password_only_session_cannot_add_a_factor_to_an_enrolled_account(world):
    """Otherwise the password alone would be enough to attach one's own
    authenticator to somebody else's enrolled account."""
    app = world['app']
    c = app.test_client()
    _password_login(c)
    _enrol_passkey(c, SoftAuthenticator())
    # A session token minted WITHOUT the claim (what a password-only path gives).
    c2 = app.test_client()
    with c2.session_transaction() as sess:
        sess['token'] = issue_token(world['pat'], SECRET, 1)
    r = c2.post('/second-factor/passkey/options')
    assert r.status_code == 403 and r.get_json()['error'] == 'second_factor_required'
    r = c2.post('/second-factor/totp/start')
    assert r.status_code == 302                                     # refused, not the setup page


def test_su_reset_removes_factors_and_ends_sessions(world):
    app = world['app']
    c = app.test_client()
    _password_login(c)
    _enrol_passkey(c, SoftAuthenticator())
    old = _session_token(c)
    api = app.test_client()
    url = f"/api/admin/users/{world['pat']}/second-factor/reset"
    assert api.post(url, headers=world['pro_a_h']).status_code == 403
    assert api.post(url).status_code == 401
    time.sleep(1.1)                    # iat is whole seconds; the epoch must pass it
    r = api.post(url, headers=world['su_h'])
    assert r.status_code == 200 and r.get_json()['removed'] == 1
    assert api.get('/api/auth/me', headers={'Authorization': 'Bearer ' + old}).status_code == 401
    assert any(e['event'] == 'second_factor_reset' and e['user_guid'] == world['su']
               for e in world['events'])
    time.sleep(1.1)
    r = _password_login(app.test_client())
    # #1045: not straight to enrolment any more; the one-time code comes first.
    assert r.headers['Location'].endswith('/login/reenrol-code')


# =============================================================================
# 5. The handshake allowlist (found while here: the GET branch had none)
# =============================================================================

def test_logged_in_user_is_not_redirected_with_a_token_to_an_unlisted_url(world):
    c = world['app'].test_client()
    r = c.post('/login', data={'email': 'a@test.com', 'password': 'propass1234'})
    assert r.status_code == 302
    r = c.get('/login?next=https://evil.example/collect')
    assert 'evil.example' not in r.headers.get('Location', '')
    assert 'token=' not in r.headers.get('Location', '')
    # The listed callback still works.
    r = c.get('/login?next=' + CALLBACK)
    assert r.headers['Location'].startswith(CALLBACK + '?token=')


# =============================================================================
# 6. Entrance 1 — invited by care
# =============================================================================

def _invite(world, headers, unit=None, ips=IPS_1):
    return world['app'].test_client().post(
        '/api/patient-links/invites', headers=headers,
        json={'ips_patient_guid': ips, 'care_unit_guid': unit or world['unit_a']})


def test_who_may_invite(world):
    assert _invite(world, world['pro_a_h']).status_code == 201
    assert _invite(world, world['su_h'], ips=IPS_2).status_code == 201
    # Affiliated to ANOTHER unit:
    r = _invite(world, world['pro_b_h'])
    assert r.status_code == 403 and r.get_json()['error'] == 'not_affiliated'
    # Affiliated to the unit but not activated:
    assert _invite(world, world['pending_h']).status_code == 403
    assert _invite(world, world['pat_h']).status_code == 403
    assert _invite(world, {}).status_code == 401
    r = world['app'].test_client().post(
        '/api/patient-links/invites', headers=world['pro_a_h'],
        json={'ips_patient_guid': 'not-a-guid', 'care_unit_guid': world['unit_a']})
    assert r.status_code == 400


def _invite_token(world, headers=None, ips=IPS_1):
    r = _invite(world, headers or world['pro_a_h'], ips=ips)
    assert r.status_code == 201, r.get_json()
    return r.get_json()['invite_url'].rsplit('/', 1)[1], r.get_json()


def _accept(client, token, email='new@test.com', pnr='199003031234'):
    return client.post('/patient-invite/' + token,
                       data={'email': email, 'password': 'newpass1234',
                             'personnummer': pnr})


def test_invited_account_is_created_already_linked(world):
    app = world['app']
    token, body = _invite_token(world)
    c = app.test_client()
    assert c.get('/patient-invite/' + token).status_code == 200
    r = _accept(c, token)
    assert r.status_code == 302 and r.headers['Location'].endswith('/second-factor')
    blob = _blob(app, _session_token(c))
    assert blob['user_type'] == 'patient'
    assert blob.get('patient_link') is not None
    assert blob['patient_link']['ips_patient_guid'] == IPS_1
    assert blob['patient_link']['linked_by'] == world['pro_a']
    assert datetime.fromisoformat(blob['patient_link']['linked_at'])
    assert blob['organisation_guid'] == world['unit_a']
    assert blob['second_factor']['verified'] is False       # password only so far
    link = [e for e in world['events'] if e['event'] == 'patient_link']
    assert len(link) == 1 and link[0]['user_guid'] == world['pro_a']
    assert link[0]['detail']['ips_patient_guid'] == IPS_1
    assert link[0]['detail']['account_user_guid'] == blob['user_guid']
    # One-time: the same link no longer opens.
    assert app.test_client().get('/patient-invite/' + token).status_code == 404
    assert _accept(app.test_client(), token, 'again@test.com', '199004041234').status_code == 404


def test_expired_and_revoked_invites_do_not_open(world):
    from src.models.patient_link import PatientInvite
    app = world['app']
    token, body = _invite_token(world)
    with app.app_context():
        s = get_session()
        inv = s.query(PatientInvite).filter_by(guid=body['invite_guid']).one()
        inv.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        s.commit()
        s.close()
    assert _accept(app.test_client(), token).status_code == 404

    token2, body2 = _invite_token(world, ips=IPS_2)
    api = app.test_client()
    # Not the other unit's to revoke — and it does not learn the invite exists.
    assert api.delete('/api/patient-links/invites/' + body2['invite_guid'],
                      headers=world['pro_b_h']).status_code == 404
    assert api.delete('/api/patient-links/invites/' + body2['invite_guid'],
                      headers=world['pro_a_h']).status_code == 200
    assert _accept(app.test_client(), token2).status_code == 404


def test_secrets_are_hashed_at_rest(world):
    """Neither the invite token nor the link code is in the database."""
    from src.models.patient_link import PatientInvite, PatientLinkCode
    app = world['app']
    token, body = _invite_token(world)
    r = app.test_client().post('/api/patient/link-code', headers=world['pat_h'])
    code = r.get_json()['link_code']
    with app.app_context():
        s = get_session()
        inv = s.query(PatientInvite).filter_by(guid=body['invite_guid']).one()
        row = s.query(PatientLinkCode).filter_by(user_guid=world['pat']).one()
        assert token not in (inv.token_hash, ) and len(inv.token_hash) == 64
        raw = code.replace('-', '')
        assert raw not in row.code_hash and len(row.code_hash) == 64
        # Keyed: a plain sha256 of the code is NOT what is stored.
        assert row.code_hash != hashlib.sha256(raw.encode()).hexdigest()
        s.close()


# =============================================================================
# 7. Entrance 2 — self-registered, link code
# =============================================================================

def _code(world, headers=None):
    r = world['app'].test_client().post('/api/patient/link-code',
                                        headers=headers or world['pat_h'])
    assert r.status_code == 201, r.get_json()
    return r.get_json()['link_code']


def _redeem(world, headers, code, ips=IPS_1):
    return world['app'].test_client().post(
        '/api/patient-links/redeem', headers=headers,
        json={'link_code': code, 'ips_patient_guid': ips})


def test_link_code_redeemed_by_the_patients_care_unit(world):
    app = world['app']
    code = _code(world)
    assert re.fullmatch(r'[A-Z2-9]{4}-[A-Z2-9]{4}', code)
    r = _redeem(world, world['pro_a_h'], code.lower().replace('-', ' '))   # as typed
    assert r.status_code == 200, r.get_json()
    link = _blob(app, world['pat_h']['Authorization'][7:]).get('patient_link')
    assert link is not None
    assert link['ips_patient_guid'] == IPS_1 and link['linked_by'] == world['pro_a']
    ev = [e for e in world['events'] if e['event'] == 'patient_link']
    assert ev and ev[0]['user_guid'] == world['pro_a'] \
        and ev[0]['detail']['method'] == 'link_code'
    # One-time.
    assert _redeem(world, world['pro_a_h'], code, IPS_2).status_code == 404


def test_link_code_is_useless_to_another_unit_and_says_nothing(world):
    app = world['app']
    code = _code(world)
    wrong = _redeem(world, world['pro_b_h'], 'ZZZZ-ZZZZ')
    other = _redeem(world, world['pro_b_h'], code)
    assert other.status_code == 404
    # Same answer as a code that does not exist: no oracle.
    assert other.get_json() == wrong.get_json()
    assert _blob(app, world['pat_h']['Authorization'][7:])['patient_link'] is None
    assert _redeem(world, world['pat_h'], code).status_code == 403
    assert _redeem(world, world['pending_h'], code).status_code == 404
    assert _redeem(world, {}, code).status_code == 401
    # Still good for the right unit: the failed attempts did not burn it.
    assert _redeem(world, world['pro_a_h'], code).status_code == 200


def test_su_may_redeem_any_units_code(world):
    assert _redeem(world, world['su_h'], _code(world)).status_code == 200


def test_expired_link_code_and_superseded_code(world):
    from src.models.patient_link import PatientLinkCode
    app = world['app']
    first = _code(world)
    second = _code(world)
    assert _redeem(world, world['pro_a_h'], first).status_code == 404   # superseded
    with app.app_context():
        s = get_session()
        row = s.query(PatientLinkCode).filter_by(user_guid=world['pat']).one()
        row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        s.commit()
        s.close()
    assert _redeem(world, world['pro_a_h'], second).status_code == 404  # expired


def test_one_account_one_patient_both_ways(world):
    app = world['app']
    assert _redeem(world, world['pro_a_h'], _code(world)).status_code == 200
    # The linked account cannot ask for another code...
    r = app.test_client().post('/api/patient/link-code', headers=world['pat_h'])
    assert r.status_code == 409 and r.get_json()['error'] == 'account_already_linked'
    # ...and the same PATIENT cannot be given to a second account.
    token, _ = _invite_token(world, ips=IPS_2)
    c = app.test_client()
    assert _accept(c, token).status_code == 302
    r = _invite(world, world['pro_a_h'], ips=IPS_1)
    assert r.status_code == 409 and r.get_json()['error'] == 'patient_already_linked'


def test_relink_is_su_only_and_leaves_history(world):
    from src.models.patient_link import PatientLinkHistory
    app = world['app']
    assert _redeem(world, world['pro_a_h'], _code(world)).status_code == 200
    api = app.test_client()
    url = '/api/admin/patient-links/' + world['pat']
    body = {'ips_patient_guid': IPS_2, 'reason': 'wrong patient chosen'}
    assert api.put(url, headers=world['pro_a_h'], json=body).status_code == 403
    assert _blob(app, world['pat_h']['Authorization'][7:])['patient_link'][
        'ips_patient_guid'] == IPS_1
    assert api.put(url, headers=world['su_h'],
                   json={'ips_patient_guid': IPS_2}).status_code == 400   # reason required
    assert api.put(url, headers=world['su_h'], json=body).status_code == 200
    link = _blob(app, world['pat_h']['Authorization'][7:])['patient_link']
    assert link['ips_patient_guid'] == IPS_2 and link['linked_by'] == world['su']
    with app.app_context():
        s = get_session()
        hist = s.query(PatientLinkHistory).filter_by(user_guid=world['pat']).order_by(
            PatientLinkHistory.id).all()
        assert [(h.event, h.previous_ips_patient_guid, h.ips_patient_guid) for h in hist] == [
            ('link', None, IPS_1), ('relink', IPS_1, IPS_2)]
        s.close()
    assert any(e['event'] == 'patient_relink' and e['detail']['from'] == IPS_1
               and e['detail']['to'] == IPS_2 for e in world['events'])
    # Unlink: SU only, back to null.
    assert api.delete(url, headers=world['pro_a_h'], json={'reason': 'x'}).status_code == 403
    assert api.delete(url, headers=world['su_h'], json={'reason': 'left care'}).status_code == 200
    assert _blob(app, world['pat_h']['Authorization'][7:])['patient_link'] is None


def test_a_professional_who_must_change_password_cannot_link(world):
    from src.models.user import User
    app = world['app']
    code = _code(world)
    with app.app_context():
        s = get_session()
        s.query(User).filter_by(guid=world['pro_a']).one().force_change_on_next_login = True
        s.commit()
        s.close()
    r = _redeem(world, world['pro_a_h'], code)
    assert r.status_code == 403 and r.get_json()['error'] == 'must_change_password'
    assert _invite(world, world['pro_a_h']).status_code == 403


# =============================================================================
# 8. The browser forms (session, not Bearer)
# =============================================================================

def test_the_whole_self_registered_journey_in_the_browser(world, monkeypatch):
    """Register -> log in -> enrol passkey -> get a code -> care redeems it in
    the dashboard form -> the patient's next token is linked AND verified.

    #1049: eps already holds this patient (409), so registration creates the
    account UNLINKED and the link code is the way in, as before."""
    from tests.eps_register_fake import FakeEpsRegister
    fake = FakeEpsRegister()
    fake.existing.add('199005051234')
    monkeypatch.setattr('src.services.eps_client._http_post', fake)
    app = world['app']
    c = app.test_client()
    r = c.post('/register-patient', data={
        'given_name': 'Self', 'family_name': 'Reg',
        'email': 'self@test.com', 'password': PW, 'personnummer': '199005051234',
        'organisation_guid': world['unit_a']})
    assert r.status_code == 200 and len(fake.calls) == 1
    auth = SoftAuthenticator()
    _password_login(c, email='self@test.com')
    assert _enrol_passkey(c, auth).status_code == 201
    page = c.post('/patient-link/code')
    assert page.status_code == 200 and page.headers['Cache-Control'] == 'no-store'
    code = re.search(r'<code>([A-Z2-9]{4}-[A-Z2-9]{4})</code>',
                     page.get_data(as_text=True)).group(1)

    care = app.test_client()
    care.post('/login', data={'email': 'a@test.com', 'password': 'propass1234'})
    assert b'Patient gave you a link code' in care.get('/dashboard').data
    r = care.post('/patient-link/redeem', data={'link_code': code, 'ips_patient_guid': IPS_1})
    assert r.status_code == 302

    r = c.get('/login?next=' + CALLBACK)
    blob = _blob(app, _token_from(r.headers['Location']))
    assert blob['patient_link']['ips_patient_guid'] == IPS_1
    assert blob['second_factor']['verified'] is True
    assert b'https://patientapp.example.test' in c.get('/dashboard').data


def test_the_care_forms_refuse_a_patient_session(world):
    from src.models.patient_link import PatientInvite
    app = world['app']
    c = app.test_client()
    with c.session_transaction() as sess:
        sess['token'] = issue_token(world['pat'], SECRET, 1)
    r = c.post('/patient-link/invite', data={'ips_patient_guid': IPS_1,
                                             'care_unit_guid': world['unit_a']})
    assert r.status_code == 302
    with app.app_context():
        s = get_session()
        assert s.query(PatientInvite).count() == 0
        s.close()
    care = app.test_client()
    care.post('/login', data={'email': 'a@test.com', 'password': 'propass1234'})
    page = care.post('/patient-link/invite', data={'ips_patient_guid': IPS_1,
                                                   'care_unit_guid': world['unit_a']})
    assert page.status_code == 200 and b'/patient-invite/' in page.data


# =============================================================================
# 9. The eps check: the guid must exist and be listed at the unit
# =============================================================================
# Three points take a patient guid: invite creation, link-code redemption, SU
# relink. Each helper returns (response, "was anything written?").

def _n_invites(world):
    from src.models.patient_link import PatientInvite
    with world['app'].app_context():
        s = get_session()
        n = s.query(PatientInvite).count()
        s.close()
        return n


def _linked_guid(world):
    link = _blob(world['app'], world['pat_h']['Authorization'][7:])['patient_link']
    return link['ips_patient_guid'] if link else None


def _err(r):
    """(status, error code). `.get`, so a wrongly successful answer fails the
    comparison instead of raising."""
    return r.status_code, (r.get_json() or {}).get('error')


def _refusals(world, code):
    return [e for e in world['events']
            if e['event'] == 'patient_link_refused' and e['detail'].get('reason') == code]


def test_the_eps_call_is_the_documented_one(world):
    assert _invite(world, world['pro_a_h']).status_code == 201
    (call,) = world['eps'].calls
    assert call['url'] == f'http://eps.test/api/v1/patients/{IPS_1}/clinics'
    # eps reads ONLY the Authorization header.
    assert call['headers'].get('Authorization') == 'ApiKey eps-test-key'
    assert 'X-API-Key' not in call['headers']


# (fake-eps mode or setup, expected error code, expected status)
EPS_REFUSALS = [
    ('down', 'eps_unavailable', 503),
    ('timeout', 'eps_unavailable', 503),
    (401, 'eps_unavailable', 503),
    (403, 'eps_unavailable', 503),
    (500, 'eps_unavailable', 503),
    (302, 'eps_unavailable', 503),
    ('foreign404', 'eps_unavailable', 503),
    ('html404', 'eps_unavailable', 503),
    ('garbage200', 'eps_unavailable', 503),
    ('unknown', 'patient_not_found', 404),
    ('other_unit', 'patient_not_at_unit', 409),
]


def _arrange(world, how):
    eps = world['eps']
    if how == 'unknown':
        eps.patients.pop(IPS_1)
    elif how == 'other_unit':
        eps.list_at(IPS_1, world['unit_b'])
    else:
        eps.mode = how


@pytest.mark.parametrize('how,code,status', EPS_REFUSALS)
def test_invite_is_refused_and_nothing_is_written(world, how, code, status):
    _arrange(world, how)
    r = _invite(world, world['pro_a_h'])
    assert _err(r) == (status, code)
    assert _n_invites(world) == 0
    assert len(_refusals(world, code)) == 1
    assert _refusals(world, code)[0]['user_guid'] == world['pro_a']


@pytest.mark.parametrize('how,code,status', EPS_REFUSALS)
def test_redeem_is_refused_nothing_is_linked_and_the_code_survives(world, how, code, status):
    link_code = _code(world)
    _arrange(world, how)
    r = _redeem(world, world['pro_a_h'], link_code)
    assert _err(r) == (status, code)
    assert _linked_guid(world) is None
    assert len(_refusals(world, code)) == 1
    # eps answers properly again: the same code still works.
    world['eps'].mode = 'ok'
    world['eps'].list_at(IPS_1, world['unit_a'])
    assert _redeem(world, world['pro_a_h'], link_code).status_code == 200
    assert _linked_guid(world) == IPS_1


@pytest.mark.parametrize('how,code,status', EPS_REFUSALS)
def test_su_relink_is_refused_and_the_link_is_unchanged(world, how, code, status):
    from src.models.patient_link import PatientLinkHistory
    assert _redeem(world, world['pro_a_h'], _code(world), IPS_2).status_code == 200
    _arrange(world, how)
    r = world['app'].test_client().put(
        '/api/admin/patient-links/' + world['pat'], headers=world['su_h'],
        json={'ips_patient_guid': IPS_1, 'reason': 'correction',
              'care_unit_guid': world['unit_a']})
    assert _err(r) == (status, code)
    assert _linked_guid(world) == IPS_2
    with world['app'].app_context():
        s = get_session()
        assert s.query(PatientLinkHistory).filter_by(event='relink').count() == 0
        s.close()
    assert len(_refusals(world, code)) == 1


@pytest.mark.parametrize('missing', ['EPS_BASE_URL', 'EPS_API_KEY'])
def test_eps_unset_refuses_by_name_and_calls_nothing(world, missing):
    world['app'].config[missing] = ''
    link_code = _code(world)
    r = _invite(world, world['pro_a_h'])
    assert _err(r) == (503, 'eps_not_configured')
    r = _redeem(world, world['pro_a_h'], link_code)
    assert _err(r) == (503, 'eps_not_configured')
    r = world['app'].test_client().put(
        '/api/admin/patient-links/' + world['pat'], headers=world['su_h'],
        json={'ips_patient_guid': IPS_1, 'reason': 'x'})
    assert _err(r) == (503, 'eps_not_configured')
    assert _n_invites(world) == 0 and _linked_guid(world) is None
    assert world['eps'].calls == []            # no default URL was tried


def test_unit_is_matched_on_the_sso_organisation_guid_not_eps_clinic_guid(world):
    """eps's clinic row has its own `guid`. Even when that value equals the
    SSO unit guid, it is the wrong identifier space and must not match."""
    eps = world['eps']
    eps.list_at(IPS_1, world['unit_b'], clinic_guid=world['unit_a'])
    r = _invite(world, world['pro_a_h'])
    assert _err(r) == (409, 'patient_not_at_unit')


def test_an_inactive_clinic_does_not_count(world):
    world['eps'].list_at(IPS_1, world['unit_a'], active=False)
    r = _invite(world, world['pro_a_h'])
    assert _err(r) == (409, 'patient_not_at_unit')


def test_outside_the_gate_eps_is_never_asked(world):
    """Someone who may not link learns nothing from eps: the affiliation gate
    answers first, identically whether the guid exists or not."""
    link_code = _code(world)
    known = _invite(world, world['pro_b_h'], ips=IPS_1)
    unknown = _invite(world, world['pro_b_h'], ips=IPS_3)
    assert known.status_code == unknown.status_code == 403
    assert known.get_json() == unknown.get_json()
    a = _redeem(world, world['pro_b_h'], link_code, IPS_1)
    b = _redeem(world, world['pro_b_h'], link_code, IPS_3)
    c = _redeem(world, world['pro_a_h'], 'ZZZZ-ZZZZ', IPS_3)        # no valid code
    assert a.status_code == b.status_code == c.status_code == 404
    assert a.get_json() == b.get_json() == c.get_json()
    r = world['app'].test_client().put(
        '/api/admin/patient-links/' + world['pat'], headers=world['pro_a_h'],
        json={'ips_patient_guid': IPS_3, 'reason': 'x'})
    assert r.status_code == 403
    assert world['eps'].calls == []


def test_su_relink_without_a_unit_needs_existence_only(world):
    world['eps'].list_at(IPS_3, world['unit_b'])        # exists, elsewhere
    url = '/api/admin/patient-links/' + world['pat']
    api = world['app'].test_client()
    r = api.put(url, headers=world['su_h'],
                json={'ips_patient_guid': IPS_3, 'reason': 'x',
                      'care_unit_guid': world['unit_a']})
    assert _err(r) == (409, 'patient_not_at_unit')
    assert _linked_guid(world) is None
    r = api.put(url, headers=world['su_h'], json={'ips_patient_guid': IPS_3, 'reason': 'x'})
    assert r.status_code == 200 and _linked_guid(world) == IPS_3


def test_the_browser_forms_go_through_the_same_check(world):
    world['eps'].patients.pop(IPS_1)
    care = world['app'].test_client()
    care.post('/login', data={'email': 'a@test.com', 'password': 'propass1234'})
    r = care.post('/patient-link/invite', data={'ips_patient_guid': IPS_1,
                                                'care_unit_guid': world['unit_a']})
    assert r.status_code == 302 and _n_invites(world) == 0
    link_code = _code(world)
    care.post('/patient-link/redeem', data={'link_code': link_code, 'ips_patient_guid': IPS_1})
    assert _linked_guid(world) is None


def test_the_opt_out_cannot_be_on_outside_development(events):
    with pytest.raises(RuntimeError, match='EPS_LINK_CHECK_DISABLED'):
        _make_app(EPS_LINK_CHECK_DISABLED='true', FLASK_ENV='production')


def test_the_opt_out_is_ignored_at_run_time_outside_development(world):
    """Belt and braces: even if the flag reaches a running production config,
    the check still runs."""
    world['app'].config['EPS_LINK_CHECK_DISABLED'] = 'true'
    world['eps'].patients.pop(IPS_1)
    r = _invite(world, world['pro_a_h'])
    assert _err(r) == (404, 'patient_not_found')


def test_the_development_opt_out_skips_the_call_and_says_so(world):
    app = world['app']
    app.config.update(FLASK_ENV='development', EPS_LINK_CHECK_DISABLED='true',
                      EPS_BASE_URL='', EPS_API_KEY='')
    assert _invite(world, world['pro_a_h'], ips=IPS_3).status_code == 201
    assert world['eps'].calls == []
    assert any(e['event'] == 'patient_link_eps_check_skipped' for e in world['events'])


def test_an_identifier_that_is_not_the_platform_guid_is_refused(monkeypatch):
    """eps resolves a FHIR resource_id too and names the platform guid it
    found; sso must not store the resource_id as the patient link."""
    from src.services import eps_client
    eps = FakeEps()
    unit, resource_id, platform = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    eps.list_at(resource_id, unit); eps.canonical[resource_id] = platform
    eps.list_at(platform, unit)
    monkeypatch.setattr('src.services.eps_client._http_get', eps)
    cfg = {'EPS_BASE_URL': 'https://eps.example', 'EPS_API_KEY': 'k'}
    assert eps_client.verify_patient(cfg, platform, unit) == 'checked'
    with pytest.raises(eps_client.EpsRefusal) as e:
        eps_client.verify_patient(cfg, resource_id, unit)
    assert e.value.code == eps_client.PATIENT_NOT_FOUND
