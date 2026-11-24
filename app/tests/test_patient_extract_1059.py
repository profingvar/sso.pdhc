"""#1059 (record.pdhc stage 6b) — GET /api/v1/patient-extract/<patient_guid>.

Real route, authentication on, eps faked only at `eps_client._http_get` (the
FakeEps of the #1040 suite). The audit is a FILE, so it is replaced by a
recorder; the recorder is also how refusals are shown to be audited.

The patient: Skatteverket's test person 19121212-1212 (synthetic), linked as
eps patient IPS_1 at unit A. Every secret-bearing column sso has is given a
known value, and the test reads each one back from the database and looks
for it in the response.
"""
import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from src.db import Base, get_session
from src.services import patient_extract as px
from src.services.auth_service import hash_password
from src.services.jwt_service import issue_token
from tests.test_patient_app_1040 import (  # noqa: F401  (fixtures)
    IPS_1, IPS_2, IPS_3, SECRET, events, world,
)

TOLVAN = '191212121212'


def _now():
    return datetime.now(timezone.utc)


@pytest.fixture
def xw(world, monkeypatch):
    """world + a doctor and an 'other_care' professional at unit A, and a
    patient account linked to IPS_1 with every kind of row sso keeps."""
    seen = world['events']

    def record(event, user_guid=None, detail=None, ip=None):
        seen.append({'event': event, 'user_guid': user_guid, 'detail': detail or {}})
    monkeypatch.setattr('src.services.patient_extract.audit', record)

    from src.models.affiliation import Affiliation
    from src.models.login_lockout import LoginLockout
    from src.models.patient import Patient
    from src.models.patient_invite_detail import PatientInviteDetail
    from src.models.patient_link import (
        PatientInvite, PatientLink, PatientLinkCode, PatientLinkHistory,
    )
    from src.models.patient_profile import PatientProfile
    from src.models.professional import Professional
    from src.models.role import Role
    from src.models.second_factor import TotpSecret, WebAuthnCredential
    from src.models.second_factor_reenrolment import SecondFactorReenrolment
    from src.models.user import User

    app = world['app']
    with app.app_context():
        s = get_session()
        roles = {r.code: r for r in s.query(Role).all()}

        def pro(email, role, unit=world['unit_a']):
            u = User(email=email, password_hash=hash_password('propass1234'),
                     user_type='professional', status='active')
            s.add(u)
            s.flush()
            s.add(Professional(user_id=u.id, professional_role='other',
                               first_name='X', last_name=email))
            s.add(Affiliation(person_guid=u.guid, care_unit_guid=unit,
                              role_guid=roles[role].guid, status='active'))
            return u

        doc = pro('doc@test.com', 'doctor')
        other = pro('other@test.com', 'other_care')
        researcher = pro('res@test.com', 'researcher')

        acct = User(email='tolvan@test.com', password_hash=hash_password('tolvanpass12'),
                    user_type='patient')
        s.add(acct)
        s.flush()
        s.add(Patient(user_id=acct.id, personnummer=TOLVAN, organisation_guid=world['unit_a']))
        s.add(PatientProfile(user_guid=acct.guid, given_name='Tolvan', family_name='Tolvansson'))
        s.add(LoginLockout(user_guid=acct.guid, failed_count=2, last_failed_at=_now()))
        s.add(PatientLink(user_guid=acct.guid, ips_patient_guid=IPS_1,
                          care_unit_guid=world['unit_a'], linked_by_user_guid=world['pro_a'],
                          method='invite'))
        s.add(PatientLinkHistory(event='link', user_guid=acct.guid, ips_patient_guid=IPS_1,
                                 care_unit_guid=world['unit_a'],
                                 actor_user_guid=world['pro_a'], method='invite'))
        s.add(WebAuthnCredential(user_guid=acct.guid, credential_id='CRED-ID-' + uuid.uuid4().hex,
                                 public_key='PUBKEY-' + uuid.uuid4().hex, name='Telefon',
                                 last_used_at=_now()))
        s.add(TotpSecret(user_guid=acct.guid, secret_enc='TOTPENC-' + uuid.uuid4().hex,
                         confirmed_at=_now()))
        s.add(SecondFactorReenrolment(user_guid=acct.guid, code_hash='R' * 63 + 'H',
                                      issued_by_user_guid=world['su'],
                                      expires_at=_now() + timedelta(hours=72)))
        s.add(PatientLinkCode(user_guid=acct.guid, code_hash='L' * 63 + 'C',
                              expires_at=_now() + timedelta(days=7)))
        used = PatientInvite(token_hash='U' * 63 + 'T', ips_patient_guid=IPS_1,
                             care_unit_guid=world['unit_a'], created_by_user_guid=world['pro_a'],
                             expires_at=_now() + timedelta(days=7), used_at=_now(),
                             used_by_user_guid=acct.guid)
        revoked = PatientInvite(token_hash='V' * 63 + 'T', ips_patient_guid=IPS_1,
                                care_unit_guid=world['unit_a'],
                                created_by_user_guid=world['pro_a'],
                                expires_at=_now() + timedelta(days=7), revoked_at=_now(),
                                revoked_by_user_guid=world['pro_a'])
        other_patient = PatientInvite(token_hash='W' * 63 + 'T', ips_patient_guid=IPS_2,
                                      care_unit_guid=world['unit_a'],
                                      created_by_user_guid=world['pro_a'],
                                      expires_at=_now() + timedelta(days=7))
        s.add_all([used, revoked, other_patient])
        s.flush()
        s.add(PatientInviteDetail(invite_guid=used.guid, given_name='Tolvan',
                                  family_name='Tolvansson', pnr_failures=1))
        s.commit()
        for name, u in (('doc', doc), ('other', other), ('researcher', researcher),
                        ('acct', acct)):
            world[name] = u.guid
            world[name + '_h'] = {'Authorization': 'Bearer ' + issue_token(u.guid, SECRET, 1)}
        s.close()
    return world


def _get(w, headers, guid=IPS_1):
    return w['app'].test_client().get(f'/api/v1/patient-extract/{guid}', headers=headers)


def _extracts(w):
    return [e for e in w['events'] if e['event'] == 'patient_extract']


def _section(body, key):
    return next(s for s in body['sections'] if s['key'] == key)


# --- the shape ---------------------------------------------------------------

def test_a_nurse_at_the_patients_unit_gets_the_extract(xw):
    r = _get(xw, xw['pro_a_h'])
    assert r.status_code == 200, r.get_json()
    assert r.headers['Cache-Control'] == 'no-store'
    b = r.get_json()
    assert b['schema'] == 'pdhc.patient-extract/1' and b['service'] == 'sso.pdhc'
    assert b['patient_guid'] == IPS_1 and b['complete'] is True and b['notes']
    assert [s['key'] for s in b['sections']] == [
        'account', 'second_factors', 'invites', 'patient_link', 'reenrolments']
    for s in b['sections']:
        assert s['count'] == len(s['items']) and s['title_sv']
        for it in s['items']:
            assert it['versions'] and it['versions'][0]['version'] == 1
            assert all(v['summary_sv'] for v in it['versions'])
    acct = _section(b, 'account')['items'][0]
    data = acct['versions'][0]['data']
    assert acct['id'] == xw['acct'] and data['email'] == 'tolvan@test.com'
    assert data['given_name'] == 'Tolvan' and data['status'] == 'active'
    assert data['login_lockout']['failed_count'] == 2
    sf = _section(b, 'second_factors')['items']
    assert sorted(i['versions'][0]['data']['type'] for i in sf) == ['passkey', 'totp']
    assert {i['current_status'] for i in _section(b, 'invites')['items']} == {'used', 'revoked'}
    link = _section(b, 'patient_link')['items']
    assert [i['resource_type'] for i in link] == ['PatientLink', 'PatientLinkCode']
    assert link[0]['versions'][0]['author']['guid'] == xw['pro_a']
    re_ = _section(b, 'reenrolments')['items'][0]
    assert re_['current_status'] == 'issued' and re_['versions'][0]['data']['issued_at']


def test_counts_match_the_database(xw):
    from src.models.patient_link import PatientInvite
    from src.models.second_factor import TotpSecret, WebAuthnCredential
    b = _get(xw, xw['pro_a_h']).get_json()
    with xw['app'].app_context():
        s = get_session()
        n_inv = s.query(PatientInvite).filter_by(ips_patient_guid=IPS_1).count()
        n_sf = (s.query(WebAuthnCredential).filter_by(user_guid=xw['acct']).count()
                + s.query(TotpSecret).filter_by(user_guid=xw['acct']).count())
        s.close()
    assert _section(b, 'invites')['count'] == n_inv == 2
    assert _section(b, 'second_factors')['count'] == n_sf == 2


def test_an_unlinked_patient_with_nothing_in_sso_is_an_empty_extract(xw):
    xw['eps'].list_at(IPS_3, xw['unit_a'])
    b = _get(xw, xw['pro_a_h'], IPS_3).get_json()
    assert b['sections'] == [] and b['complete'] is True


def test_su_needs_no_affiliation_and_no_eps(xw):
    xw['eps'].mode = 'down'
    r = _get(xw, xw['su_h'])
    assert r.status_code == 200 and r.get_json()['sections']
    assert _extracts(xw)[-1]['detail']['basis'] == 'su'


# --- no secrets --------------------------------------------------------------

def _secret_looking(name):
    return any(w in name for w in ('hash', 'secret', 'token', 'credential',
                                   'public_key', 'personnummer', 'code', 'key'))


def test_every_secret_looking_column_read_here_is_declared():
    """A column added later to a table this module reads, with a name that
    looks secret, must be put in SECRET_COLUMNS (or be shown harmless here)."""
    import src.models  # noqa: F401
    declared = set(px.SECRET_COLUMNS)
    # a date (the #44 session flush), not a value that opens anything
    harmless = {('users', 'token_revocation_epoch')}
    for table in px.TABLES_READ:
        for col in Base.metadata.tables[table].columns:
            if _secret_looking(col.name) and (table, col.name) not in declared | harmless:
                pytest.fail(f'{table}.{col.name} looks secret and is not in SECRET_COLUMNS')
    assert ('users', 'password_hash') in declared


def test_no_secret_value_is_in_the_response(xw):
    from sqlalchemy import text
    body = _get(xw, xw['pro_a_h']).get_data(as_text=True)
    assert 'tolvan@test.com' in body                 # the control: data IS there
    checked = 0
    with xw['app'].app_context():
        s = get_session()
        for table, column in px.SECRET_COLUMNS:
            values = [v for (v,) in s.execute(text(f'SELECT {column} FROM {table}')) if v]
            assert values, f'no {table}.{column} populated: the test would prove nothing'
            for v in values:
                checked += 1
                assert str(v) not in body, f'{table}.{column} leaked'
                assert json.dumps(str(v))[1:-1] not in body, f'{table}.{column} leaked (escaped)'
        s.close()
    assert checked >= len(px.SECRET_COLUMNS)
    assert '19121212' not in body and '1212-1212' not in body


# --- the gate ----------------------------------------------------------------

def _refused(xw, headers, status, reason, guid=IPS_1):
    r = _get(xw, headers, guid)
    assert r.status_code == status, r.get_json()
    assert 'sections' not in (r.get_json() or {})
    evs = _extracts(xw)
    assert evs, 'the refusal was not audited'
    ev = evs[-1]
    assert ev['detail']['outcome'] == 'refused' and ev['detail']['reason'] == reason
    return r


def test_a_doctor_at_the_unit_is_allowed(xw):
    assert _get(xw, xw['doc_h']).status_code == 200


def test_a_professional_at_another_unit_gets_404(xw):
    _refused(xw, xw['pro_b_h'], 404, 'not_listed_at_actor_unit')


def test_other_roles_at_the_unit_get_404(xw):
    _refused(xw, xw['other_h'], 404, 'no_doctor_or_nurse_affiliation')
    _refused(xw, xw['researcher_h'], 404, 'no_doctor_or_nurse_affiliation')


def test_an_unknown_patient_is_the_same_404(xw):
    a = _refused(xw, xw['pro_a_h'], 404, 'eps_patient_not_found',
                 guid='44444444-4444-4444-8444-444444444444').get_json()
    b = _refused(xw, xw['pro_b_h'], 404, 'not_listed_at_actor_unit').get_json()
    assert a == b


def test_a_malformed_guid_is_404(xw):
    _refused(xw, xw['pro_a_h'], 404, 'invalid_guid', guid='not-a-guid')


def test_a_pending_professional_gets_404(xw):
    _refused(xw, xw['pending_h'], 404, 'not_activated')


def test_a_patient_token_is_403(xw):
    _refused(xw, xw['acct_h'], 403, 'not_professional')


def test_must_change_password_is_403(xw):
    from src.models.user import User
    with xw['app'].app_context():
        s = get_session()
        s.query(User).filter_by(guid=xw['pro_a']).one().force_change_on_next_login = True
        s.commit()
        s.close()
    _refused(xw, xw['pro_a_h'], 403, 'must_change_password')


def test_no_bearer_is_401_and_audited(xw):
    _refused(xw, {}, 401, 'no_valid_bearer')
    _refused(xw, {'Authorization': 'Bearer nonsense'}, 401, 'no_valid_bearer')
    assert _extracts(xw)[-1]['user_guid'] is None


def test_the_su_page_session_is_not_a_door(xw):
    """require_auth would accept the session + CSRF header; this route takes
    the bearer only."""
    import re
    c = xw['app'].test_client()
    with c.session_transaction() as sess:
        sess['token'] = xw['su_h']['Authorization'][7:]
    m = re.search(r"'X-CSRFToken': '([^']+)'", c.get('/admin').get_data(as_text=True))
    assert m, 'the SU page must hand out a CSRF token, or this test proves nothing'
    h = {'X-CSRFToken': m.group(1)}
    # the control: the same session + token IS accepted by a require_auth route
    assert c.get(f"/api/admin/users/{xw['pro_a']}/affiliations", headers=h).status_code == 200
    r = c.get(f'/api/v1/patient-extract/{IPS_1}', headers=h)
    assert r.status_code == 401
    assert _extracts(xw)[-1]['detail']['reason'] == 'no_valid_bearer'


@pytest.mark.parametrize('mode', ['down', 'timeout', 500, 'foreign404', 'garbage200'])
def test_eps_that_cannot_answer_fails_closed(xw, mode):
    xw['eps'].mode = mode
    r = _get(xw, xw['pro_a_h'])
    assert r.status_code == 503 and 'sections' not in r.get_json()


def test_eps_unconfigured_fails_closed_even_with_the_dev_opt_out(xw):
    xw['app'].config.update(FLASK_ENV='development', EPS_LINK_CHECK_DISABLED='true')
    before = len(xw['eps'].calls)
    _refused(xw, xw['pro_b_h'], 404, 'not_listed_at_actor_unit')   # eps still asked
    assert len(xw['eps'].calls) == before + 1
    xw['app'].config.update(EPS_BASE_URL='')
    r = _get(xw, xw['pro_a_h'])
    assert r.status_code == 503 and r.get_json()['error'] == 'eps_not_configured'


def test_an_inactive_listing_does_not_count(xw):
    xw['eps'].list_at(IPS_1, xw['unit_a'], active=False)
    _refused(xw, xw['pro_a_h'], 404, 'not_listed_at_actor_unit')


def test_a_suspended_affiliation_does_not_count(xw):
    from src.models.affiliation import Affiliation
    with xw['app'].app_context():
        s = get_session()
        for a in s.query(Affiliation).filter_by(person_guid=xw['pro_a']).all():
            a.status = 'suspended'
        s.commit()
        s.close()
    _refused(xw, xw['pro_a_h'], 404, 'no_doctor_or_nurse_affiliation')


def test_success_is_audited_with_who_whom_and_what(xw):
    _get(xw, xw['pro_a_h'])
    ev = _extracts(xw)[-1]
    assert ev['user_guid'] == xw['pro_a']
    assert ev['detail']['outcome'] == 'ok' and ev['detail']['patient_guid'] == IPS_1
    assert ev['detail']['basis'] == 'affiliation:' + xw['unit_a'].lower()
    assert ev['detail']['sections']['invites'] == 2


# --- whose data --------------------------------------------------------------

def test_a_relinked_account_is_not_given_out_under_the_old_patient(xw):
    """SU relinks the account from IPS_1 to IPS_2: IPS_1's extract keeps the
    link history (account by guid only) and loses the account's email and
    factors, which now belong to IPS_2's extract."""
    xw['eps'].list_at(IPS_2, xw['unit_a'])
    r = xw['app'].test_client().put(
        f"/api/admin/patient-links/{xw['acct']}", headers=xw['su_h'],
        json={'ips_patient_guid': IPS_2, 'reason': 'wrong patient'})
    assert r.status_code == 200, r.get_json()
    b = _get(xw, xw['pro_a_h']).get_json()
    text = json.dumps(b)
    assert 'tolvan@test.com' not in text
    keys = [s['key'] for s in b['sections']]
    assert 'account' not in keys and 'second_factors' not in keys
    hist = _section(b, 'patient_link')['items'][0]
    assert hist['current_status'] == 'unlinked'
    assert [v['data']['event'] for v in hist['versions']] == ['link', 'relink']
    b2 = _get(xw, xw['pro_a_h'], IPS_2).get_json()
    assert _section(b2, 'account')['items'][0]['id'] == xw['acct']
