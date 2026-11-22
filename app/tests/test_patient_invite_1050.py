"""#1050 — patients join the patient app ONLY by invitation from their unit.

The invite now carries the patient's name (from dashboard's "Bjud in
patient"), and on the invite page the patient must ENTER a personnummer that
equals the eps patient's. SSO asks eps (`POST /patients/<guid>/identifier-
match`, a yes/no) — eps holds the number, SSO never sees it until the
patient types it.

Real routes, auth on, the #1040 `world` (two units, professionals, an SU).
The only thing patched is the outbound HTTP to eps (`FakeEps` in
test_patient_app_1040: `_http_get` for the unit check, `_http_post` for the
identifier match). Personnummer are synthetic (wrong check digit, #405).
"""
from src.db import get_session
from tests.test_patient_app_1040 import (  # noqa: F401  (fixtures)
    IPS_1, IPS_2, SoftAuthenticator, _blob, _enrol_passkey, _session_token,
    events, world,
)

PNR = '199003031234'          # what FakeEps holds for IPS_1 / IPS_2
WRONG = '199003031241'        # another synthetic number (wrong check digit)


def _invite(world, headers=None, ips=IPS_1, **names):
    body = {'ips_patient_guid': ips, 'care_unit_guid': world['unit_a']}
    body.update(names)
    return world['app'].test_client().post('/api/patient-links/invites',
                                           headers=headers or world['pro_a_h'], json=body)


def _token(world, **kw):
    kw.setdefault('given_name', 'Synt')
    kw.setdefault('family_name', 'Inbjudensson')
    r = _invite(world, **kw)
    assert r.status_code == 201, r.get_json()
    return r.get_json()['invite_url'].rsplit('/', 1)[1], r.get_json()


def _accept(client, token, pnr=PNR, email='inv@test.com'):
    return client.post('/patient-invite/' + token, data={
        'email': email, 'password': 'newpass1234', 'personnummer': pnr})


def _n_patient_accounts(world):
    from src.models.user import User
    with world['app'].app_context():
        s = get_session()
        n = s.query(User).filter_by(user_type='patient').count()
        s.close()
    return n


def test_invite_carries_the_name_and_the_page_shows_it(world):
    token, body = _token(world)
    assert (body['given_name'], body['family_name']) == ('Synt', 'Inbjudensson')
    page = world['app'].test_client().get('/patient-invite/' + token)
    html = page.get_data(as_text=True)
    assert page.status_code == 200
    assert 'Inbjudan från Unit A' in html and 'Synt Inbjudensson' in html
    # the page never shows a number: the patient must type it
    assert PNR not in html and PNR[:8] not in html


def test_half_a_name_is_refused_and_no_name_is_allowed(world):
    r = _invite(world, given_name='Synt')
    assert r.status_code == 400 and r.get_json()['error'] == 'invalid_request'
    r = _invite(world, ips=IPS_2)                       # older API callers
    assert r.status_code == 201 and r.get_json()['given_name'] is None


def test_wrong_personnummer_is_a_form_error_counted_and_audited_without_it(world):
    app, eps, events = world['app'], world['eps'], world['events']
    token, body = _token(world)
    before = _n_patient_accounts(world)
    r = _accept(app.test_client(), token, pnr=WRONG)
    html = r.get_data(as_text=True)
    assert r.status_code == 400
    assert 'Personnumret stämmer inte med inbjudan' in html
    assert 'Synt Inbjudensson' in html                   # still the invite form
    assert _n_patient_accounts(world) == before
    # eps was asked with the 12 digits, about THIS invite's patient
    call = eps.match_calls[-1]
    assert call['url'].endswith(f'/api/v1/patients/{IPS_1}/identifier-match')
    assert call['json'] == {'personnummer': WRONG}
    assert call['headers']['Authorization'] == 'ApiKey eps-test-key'
    ev = [e for e in events if e['event'] == 'patient_invite_pnr_mismatch']
    assert len(ev) == 1 and ev[0]['detail']['failures'] == 1
    assert ev[0]['detail']['invite_guid'] == body['invite_guid']
    for e in events:
        assert WRONG not in str(e) and PNR not in str(e)


def test_five_wrong_numbers_close_the_invite(world):
    app = world['app']
    token, _ = _token(world)
    for i in range(4):
        assert _accept(app.test_client(), token, pnr=WRONG).status_code == 400
    r = _accept(app.test_client(), token, pnr=WRONG)
    assert r.status_code == 404 and 'Länken gäller inte' in r.get_data(as_text=True)
    # closed: even the right number no longer opens it, and the page says so
    assert app.test_client().get('/patient-invite/' + token).status_code == 404
    assert _accept(app.test_client(), token).status_code == 404
    closed = [e for e in world['events'] if e['event'] == 'patient_invite_pnr_mismatch']
    assert closed[-1]['detail'] == {'invite_guid': closed[-1]['detail']['invite_guid'],
                                    'failures': 5, 'closed': True}


def test_right_number_creates_the_linked_account_with_the_name(world):
    app = world['app']
    token, _ = _token(world)
    _accept(app.test_client(), token, pnr=WRONG)          # one miss first
    c = app.test_client()
    # the 10-digit spelling of the same number is the same number
    r = _accept(c, token, pnr='900303-1234')
    assert r.status_code == 302 and r.headers['Location'].endswith('/second-factor')
    blob = _blob(app, _session_token(c))
    assert blob['patient_link']['ips_patient_guid'] == IPS_1
    assert blob['patient_link']['care_unit_guid'] == world['unit_a']
    assert blob['display_name'] == 'Synt Inbjudensson'
    assert world['eps'].match_calls[-1]['json'] == {'personnummer': PNR}


def test_after_the_passkey_the_patient_goes_to_the_patient_app_login(world):
    app = world['app']
    token, _ = _token(world)
    c = app.test_client()
    assert _accept(c, token).status_code == 302
    r = _enrol_passkey(c, SoftAuthenticator())
    assert r.status_code == 201
    assert r.get_json()['redirect'] == 'https://patientapp.example.test/auth/login'
    assert _blob(app, _session_token(c))['second_factor']['verified'] is True


def test_eps_unavailable_is_try_later_and_not_counted(world):
    app, eps = world['app'], world['eps']
    token, body = _token(world)
    eps.mode = 'down'
    r = _accept(app.test_client(), token)
    assert r.status_code == 503 and 'Försök igen om en stund' in r.get_data(as_text=True)
    eps.mode = 'foreign404'                 # another vhost answering for eps
    assert _accept(app.test_client(), token).status_code == 503
    assert _n_patient_accounts(world) == 1  # the world's own patient only
    from src.models.patient_invite_detail import PatientInviteDetail
    with app.app_context():
        s = get_session()
        d = s.query(PatientInviteDetail).filter_by(invite_guid=body['invite_guid']).one()
        assert d.pnr_failures == 0
        s.close()
    eps.mode = 'ok'
    assert _accept(app.test_client(), token).status_code == 302


def test_a_real_format_number_is_refused_without_asking_eps(world):
    app, eps = world['app'], world['eps']
    token, _ = _token(world)
    n = len(eps.match_calls)
    r = _accept(app.test_client(), token, pnr='191212121212')   # VALID check digit
    assert r.status_code == 400
    assert 'Piloten tar bara emot testpersonnummer' in r.get_data(as_text=True)
    assert len(eps.match_calls) == n


def test_an_invite_without_a_name_still_needs_the_right_number(world):
    app = world['app']
    r = _invite(world, ips=IPS_2)
    token = r.get_json()['invite_url'].rsplit('/', 1)[1]
    assert _accept(app.test_client(), token, pnr=WRONG).status_code == 400
    c = app.test_client()
    assert _accept(c, token).status_code == 302
    assert _blob(app, _session_token(c)).get('display_name') is None

