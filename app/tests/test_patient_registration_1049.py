"""#1049 (contract s7.1) — patient self-registration that creates the patient
in eps and links the account in one go, with one answer for every outcome.

Through the real routes with auth on: `/register-patient` (HTML) and
`/api/patient/register` (JSON) for the registration, `/login` and the passkey
ceremony for the first login, and the blob read back through
`/api/auth/me/service` with service credentials. eps is faked at the HTTP
boundary only (`eps_client._http_post`, tests/eps_register_fake.py; the GET
fake of #1040 stays in place for the link paths).

Personnummer: synthetic (wrong check digit, #405) except TOLVAN, Skatteverket's
published test number 19121212-1212, which has a VALID check digit and belongs
to nobody — the one number that can show the synthetic-only refusal without
using a number that could be a real person's.
"""
import time
from urllib.parse import parse_qs, quote, urlparse

import pytest

from src.db import get_session
from src.services import personnummer as pnr_rule
from src.services.jwt_service import issue_token
from src.services.patient_enrolment import normalise_mobile
from tests.eps_register_fake import FakeEpsRegister
from tests.test_patient_app_1040 import (  # noqa: F401  (fixtures)
    CALLBACK, IPS_1, SECRET, SoftAuthenticator, _blob, _enrol_passkey,
    _session_token, _token_from, events, world,
)

UNIFORM = ('Om uppgifterna kunde registreras kan du logga in nu. '
           'Har du redan ett konto loggar du in med det.')
TOLVAN = '191212121212'
NEW_PNR = '198502021235'
PW = 'nyttlosen1'


@pytest.fixture
def eps(world, monkeypatch):
    fake = FakeEpsRegister()
    monkeypatch.setattr('src.services.eps_client._http_post', fake)
    return fake


def test_the_numbers_used_here_are_what_they_claim():
    """Premise: if these flipped, the tests below would argue the wrong way."""
    assert pnr_rule.classify(TOLVAN)[0] == pnr_rule.REAL_FORMAT
    for n in (NEW_PNR, '199001011234', '8505051234', '850505-1234',
              '198603031234', '198604041234', '199003031234'):
        assert pnr_rule.classify(n)[0] == pnr_rule.SYNTHETIC


def _form(world, **over):
    data = {'given_name': 'Anna', 'family_name': 'Andersson',
            'personnummer': NEW_PNR, 'email': 'anna@test.com',
            'mobile': '070-123 45 67', 'organisation_guid': world['unit_a'],
            'password': PW}
    data.update(over)
    return data


def _html(world, **over):
    return world['app'].test_client().post('/register-patient', data=_form(world, **over))


def _api(world, **over):
    return world['app'].test_client().post('/api/patient/register', json=_form(world, **over))


def _users(world):
    from src.models.user import User
    with world['app'].app_context():
        s = get_session()
        n = s.query(User).filter_by(user_type='patient').count()
        s.close()
        return n


def _token_for(world, email):
    from src.models.user import User
    with world['app'].app_context():
        s = get_session()
        u = s.query(User).filter_by(email=email).one()
        guid = u.guid
        s.close()
    return issue_token(guid, SECRET, 1)


def _events(world, name):
    return [e for e in world['events'] if e['event'] == name]


# =============================================================================
# 1. The four outcomes
# =============================================================================

def test_new_registrant_is_created_and_linked_in_one_go(world, eps):
    r = _html(world)
    assert r.status_code == 200 and UNIFORM in r.get_data(as_text=True)
    (call,) = eps.calls
    assert call['url'] == 'http://eps.test/api/v1/patients/self-register'
    assert call['headers']['Authorization'] == 'ApiKey eps-test-key'
    assert call['json'] == {'personnummer': NEW_PNR, 'given_name': 'Anna',
                            'family_name': 'Andersson',
                            'care_unit_guid': world['unit_a'],
                            'email': 'anna@test.com', 'mobile': '+46701234567'}
    blob = _blob(world['app'], _token_for(world, 'anna@test.com'))
    link = blob['patient_link'] or {}
    assert link.get('ips_patient_guid') == eps.created[NEW_PNR]
    assert link.get('linked_by') == 'self-registration'
    assert link.get('care_unit_guid') == world['unit_a']
    assert blob.get('display_name') == 'Anna Andersson'
    (ev,) = _events(world, 'patient_self_linked')
    assert ev['detail']['ips_patient_guid'] == eps.created[NEW_PNR]


def test_eps_409_creates_the_account_unlinked(world, eps):
    eps.existing.add(NEW_PNR)
    r = _html(world)
    assert r.status_code == 200 and UNIFORM in r.get_data(as_text=True)
    blob = _blob(world['app'], _token_for(world, 'anna@test.com'))
    assert blob['patient_link'] is None, "never auto-linked to an existing patient"
    assert len(_events(world, 'patient_register_unlinked')) == 1
    # The s1.2 link-code path is open to this account.
    r = world['app'].test_client().post(
        '/api/patient/link-code',
        headers={'Authorization': 'Bearer ' + _token_for(world, 'anna@test.com')})
    assert r.status_code == 201


@pytest.mark.parametrize('dup', ['email', 'personnummer'])
def test_a_duplicate_creates_nothing_asks_nobody_and_looks_the_same(world, eps, dup):
    new = _html(world).get_data()
    before = _users(world)
    over = ({'email': 'pat@test.com', 'personnummer': '198603031234'} if dup == 'email'
            else {'email': 'other@test.com', 'personnummer': '199001011234'})
    r = _html(world, **over)
    assert r.status_code == 200 and r.get_data() == new, "same page, byte for byte"
    assert _users(world) == before and len(eps.calls) == 1
    (ev,) = _events(world, 'patient_register_duplicate')
    assert ev['detail']['which'] == dup


def test_the_json_door_gives_one_answer_for_all_three(world, eps):
    linked = _api(world)
    eps.existing.add('198604041234')
    unlinked = _api(world, email='b@x.test', personnummer='198604041234')
    duplicate = _api(world, email='c@x.test', personnummer=NEW_PNR)
    answers = {(r.status_code, r.get_data()) for r in (linked, unlinked, duplicate)}
    assert len(answers) == 1
    assert linked.status_code == 202
    assert linked.get_json() == {'status': 'received', 'message': UNIFORM}


@pytest.mark.parametrize('mode', ['down', 'timeout', 500, 401, 'foreign409',
                                  'foreign404', 'garbage201', 'header_mismatch'])
def test_eps_unreachable_or_odd_is_a_form_error_and_nothing_is_created(world, eps, mode):
    eps.mode = mode
    before = _users(world)
    r = _html(world)
    html = r.get_data(as_text=True)
    assert 'Försök igen om en stund.' in html and UNIFORM not in html
    assert _users(world) == before
    # Another number: a misbehaving eps may still have created the first one,
    # and a second attempt with it would rightly get eps's 409.
    r = _api(world, personnummer='198603031235')
    assert r.status_code == 503 and r.get_json()['error'] == 'eps_unavailable'
    assert _users(world) == before


@pytest.mark.parametrize('missing', ['EPS_BASE_URL', 'EPS_API_KEY'])
def test_eps_unconfigured_is_a_form_error_and_nothing_is_asked(world, eps, missing):
    world['app'].config[missing] = ''
    r = _api(world)
    assert r.status_code == 503 and r.get_json()['error'] == 'eps_not_configured'
    assert r.get_json()['message'] == 'Försök igen om en stund.'
    # Unconfigured is answered before the duplicate check, so it is the same
    # answer for an address that has an account.
    assert _api(world, email='pat@test.com').get_json().get('error') == 'eps_not_configured'
    assert eps.calls == [] and _users(world) == 1


def test_a_unit_eps_does_not_know_is_a_form_error(world, eps):
    eps.units = {world['unit_b']}
    r = _api(world)
    assert r.status_code == 422 and r.get_json()['error'] == 'unit_not_available'
    assert _users(world) == 1


def test_an_account_that_cannot_be_committed_is_audited_and_not_half_made(world, eps):
    """eps names a guid that is already linked to another account: the link
    step refuses, so the account must not survive without it."""
    eps.guid_to_return = IPS_1
    first = world['app'].test_client()
    assert _html(world).status_code == 200                       # takes IPS_1
    before = _users(world)
    r = first.post('/register-patient', data=_form(
        world, email='second@test.com', personnummer='198604041234'))
    assert 'Försök igen om en stund.' in r.get_data(as_text=True)
    assert _users(world) == before
    (ev,) = _events(world, 'patient_register_not_committed')
    assert ev['detail']['eps_patient_guid'] == IPS_1


# =============================================================================
# 2. The personnummer rule
# =============================================================================

def test_a_valid_check_digit_is_refused_in_the_pilot(world, eps):
    r = _html(world, personnummer=TOLVAN)
    assert 'Piloten tar bara emot testpersonnummer.' in r.get_data(as_text=True)
    assert eps.calls == [] and _users(world) == 1


@pytest.mark.parametrize('value', ['', None])
def test_the_pilot_rule_is_on_when_the_setting_is_blank_or_absent(world, eps, value):
    if value is None:
        world['app'].config.pop('PATIENT_REGISTRATION_SYNTHETIC_ONLY', None)
    else:
        world['app'].config['PATIENT_REGISTRATION_SYNTHETIC_ONLY'] = value
    r = _api(world, personnummer=TOLVAN)
    assert r.status_code == 400 and eps.calls == []


def test_the_operator_can_switch_the_pilot_rule_off(world, eps):
    world['app'].config['PATIENT_REGISTRATION_SYNTHETIC_ONLY'] = 'false'
    assert _api(world, personnummer=TOLVAN).status_code == 202
    assert eps.calls[0]['json']['personnummer'] == TOLVAN


def test_an_invite_cannot_bring_a_valid_number_either(world, eps):
    from tests.test_patient_app_1040 import _accept, _invite_token
    token, _ = _invite_token(world)
    r = _accept(world['app'].test_client(), token, pnr=TOLVAN)
    assert r.status_code == 400
    assert 'Piloten tar bara emot testpersonnummer.' in r.get_data(as_text=True)


@pytest.mark.parametrize('form', ['8505051234', '850505-1234', '19850505-1234'])
def test_ten_or_twelve_digits_are_stored_as_twelve(world, eps, form):
    assert _api(world, personnummer=form).status_code == 202
    assert eps.calls[0]['json']['personnummer'] == '198505051234'


@pytest.mark.parametrize('bad', ['19850231' + '1234', '1985050512345', '850505123',
                                 '19850505ABCD', ''])
def test_a_bad_shape_or_impossible_date_is_a_form_error(world, eps, bad):
    r = _api(world, personnummer=bad)
    assert r.status_code == 400 and 'personnummer' in str(r.get_json()['messages'])
    assert eps.calls == []


# =============================================================================
# 3. The other fields
# =============================================================================

@pytest.mark.parametrize('raw,want', [
    ('0701234567', '+46701234567'), ('070-123 45 67', '+46701234567'),
    ('+46 70 123 45 67', '+46701234567'), ('0046701234567', '+46701234567'),
    ('', None), ('  ', None),
])
def test_mobile_is_normalised(raw, want):
    assert normalise_mobile(raw) == want


@pytest.mark.parametrize('raw', ['081234567', '+4581234567', '07012', 'abc'])
def test_mobile_that_is_not_a_swedish_mobile_is_refused(world, eps, raw):
    with pytest.raises(ValueError):
        normalise_mobile(raw)
    assert _api(world, mobile=raw).status_code == 400 and eps.calls == []


def test_mobile_is_optional_and_not_sent_when_absent(world, eps):
    assert _api(world, mobile='').status_code == 202
    assert 'mobile' not in eps.calls[0]['json']


@pytest.mark.parametrize('field', ['given_name', 'family_name', 'email', 'password'])
def test_required_fields(world, eps, field):
    r = _api(world, **{field: ''})
    assert r.status_code == 400 and eps.calls == []


def test_configured_units_narrow_the_list(world, eps):
    world['app'].config['PATIENT_REGISTRATION_UNITS'] = world['unit_b'].upper()
    page = world['app'].test_client().get('/register-patient').get_data(as_text=True)
    assert world['unit_b'] in page and world['unit_a'] not in page
    r = _api(world)                                   # unit_a
    assert r.status_code == 404 and r.get_json()['message'] == 'Vårdenheten finns inte.'
    assert _api(world, organisation_guid=world['unit_b']).status_code == 202


def test_the_page_is_swedish(world):
    page = world['app'].test_client().get('/register-patient').get_data(as_text=True)
    for text in ('Registrera dig som patient', 'Förnamn', 'Efternamn',
                 'Mobilnummer (frivilligt)', 'Din vårdenhet', 'Lösenord'):
        assert text in page


# =============================================================================
# 4. ?next= and timing
# =============================================================================

def test_an_allowed_next_reaches_the_login_link(world, eps):
    c = world['app'].test_client()
    page = c.get('/register-patient?next=' + quote(CALLBACK)).get_data(as_text=True)
    assert f'name="next" value="{CALLBACK}"' in page
    r = c.post('/register-patient', data={**_form(world), 'next': CALLBACK})
    assert f'href="/login?next={quote(CALLBACK, safe="")}"' in r.get_data(as_text=True)


@pytest.mark.parametrize('evil', ['https://evil.example/cb', '//evil.example', 'javascript:x'])
def test_a_next_that_is_not_allowed_is_ignored(world, eps, evil):
    c = world['app'].test_client()
    page = c.get('/register-patient?next=' + quote(evil)).get_data(as_text=True)
    assert 'name="next"' not in page and 'evil' not in page
    r = c.post('/register-patient', data={**_form(world), 'next': evil})
    html = r.get_data(as_text=True)
    assert 'href="/login"' in html and 'evil' not in html


def test_every_uniform_answer_takes_the_minimum_time(world, eps):
    world['app'].config['PATIENT_REGISTRATION_MIN_SECONDS'] = 0.4
    took = []
    for over in ({}, {'email': 'x@y.test'}):            # new, then duplicate
        t = time.monotonic()
        assert _api(world, **over).status_code == 202
        took.append(time.monotonic() - t)
    assert min(took) >= 0.4, took


# =============================================================================
# 5. First login: the second factor before any service token
# =============================================================================

def test_register_then_first_login_enrols_a_passkey_before_the_service(world, eps):
    app, auth = world['app'], SoftAuthenticator()
    c = app.test_client()
    c.get('/register-patient?next=' + quote(CALLBACK))
    c.post('/register-patient', data={**_form(world), 'next': CALLBACK})
    r = c.post('/login', data={'email': 'anna@test.com', 'password': PW,
                               'next': CALLBACK, 'state': 's1'})
    assert r.status_code == 302 and r.headers['Location'].endswith('/second-factor')
    assert 'token=' not in r.headers['Location']
    # Asking for the service again without enrolling still does not get there.
    again = c.get('/login?next=' + quote(CALLBACK))
    assert not again.headers['Location'].startswith(CALLBACK)
    done = _enrol_passkey(c, auth)
    assert done.status_code == 201
    dest = done.get_json()['redirect']
    assert dest.startswith(CALLBACK)
    blob = _blob(app, _token_from(dest))
    assert blob['second_factor']['verified'] is True
    assert (blob['patient_link'] or {}).get('care_unit_guid') == world['unit_a']


def test_the_json_login_never_hands_a_patient_token_to_a_service(world, eps):
    r = world['app'].test_client().post('/api/auth/login', json={
        'email': 'pat@test.com', 'password': 'patpass1234', 'next': CALLBACK,
        'state': 'st'})
    assert r.status_code == 302
    loc = urlparse(r.headers['Location'])
    assert loc.path == '/login' and 'token' not in parse_qs(loc.query)
    assert parse_qs(loc.query) == {'next': [CALLBACK], 'state': ['st']}
    assert len(_events(world, 'login_patient_sent_to_second_factor')) == 1


def test_the_json_login_still_hands_a_professional_token_to_a_service(world):
    r = world['app'].test_client().post('/api/auth/login', json={
        'email': 'a@test.com', 'password': 'propass1234', 'next': CALLBACK})
    assert r.status_code == 302 and r.headers['Location'].startswith(CALLBACK + '?token=')


# =============================================================================
# 6. The unit on links made by care
# =============================================================================

def test_an_invited_account_carries_the_invites_unit(world):
    from tests.test_patient_app_1040 import _accept, _invite_token
    token, _ = _invite_token(world)
    c = world['app'].test_client()
    assert _accept(c, token).status_code == 302
    blob = _blob(world['app'], _session_token(c))
    assert blob['patient_link'].get('care_unit_guid') == world['unit_a']
    assert blob.get('display_name', 'ABSENT') is None


def test_a_link_code_carries_the_unit_the_patient_registered_at(world):
    from tests.test_patient_app_1040 import _code, _redeem
    r = _redeem(world, world['pro_a_h'], _code(world))
    assert r.status_code == 200
    assert r.get_json()['patient_link'].get('care_unit_guid') == world['unit_a']
    blob = _blob(world['app'], world['pat_h']['Authorization'][7:])
    assert blob['patient_link'].get('care_unit_guid') == world['unit_a']
