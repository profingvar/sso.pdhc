"""#1054 (record.pdhc stage 2) — a professional's HSA-id and legitimation.

Set by an SU only (API and the SU page), audited, emitted in the access blob
through /api/auth/me/service — the path every consuming service uses — and
not touched by the legacy-field kill-switch. HSA-ids in the pilot are
synthetic (D8): the part after the dash must start with SYNT.
"""
import re

import pytest

from src.db import get_session
from src.models.affiliation import Affiliation
from src.models.professional import Professional
from src.models.professional_qualification import ProfessionalQualification
from src.models.user import User
from tests.test_s9_signon import s9  # noqa: F401  (fixture)

SVC = {'X-SSO-Client-Id': 'test-client', 'X-SSO-Client-Secret': 'test-secret'}
GOOD = 'SE5565594230-SYNT0001'
FIELDS = ('hsa_id', 'hsa_id_synthetic', 'legitimation', 'legitimation_verified')


@pytest.fixture
def s9p(s9, app):
    """s9, with a Professional row for the pending person (s9 leaves none)."""
    with app.app_context():
        s = get_session()
        u = s.query(User).filter_by(guid=s9['pending_guid']).one()
        s.add(Professional(user_id=u.id, professional_role='nurse',
                           first_name='Pen', last_name='Ding'))
        s.commit()
        s.close()
    return s9


@pytest.fixture
def events(monkeypatch):
    seen = []

    def record(event, user_guid=None, detail=None, ip=None):
        seen.append({'event': event, 'user_guid': user_guid, 'detail': detail or {}})

    monkeypatch.setattr('src.services.professional_qualification.audit', record)
    return seen


def _h(token):
    return {'Authorization': f'Bearer {token}'}


def _blob(app, token):
    r = app.test_client().get('/api/auth/me/service', headers={**_h(token), **SVC})
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def _put(app, token, user_guid, **body):
    return app.test_client().put(f'/api/admin/users/{user_guid}/qualification',
                                 headers=_h(token), json=body)


def _stored(app, user_guid):
    with app.app_context():
        s = get_session()
        u = s.query(User).filter_by(guid=user_guid).one()
        p = s.query(Professional).filter_by(user_id=u.id).one()
        q = s.query(ProfessionalQualification).filter_by(professional_guid=p.guid).first()
        out = (q.hsa_id, q.legitimation, q.legitimation_verified) if q else None
        s.close()
        return out


# --- the blob ----------------------------------------------------------------

def test_blob_has_the_four_fields_when_nothing_is_recorded(app, seeded_app):
    blob = _blob(app, seeded_app['pro_token'])
    assert {k: blob.get(k, 'ABSENT') for k in FIELDS} == {
        'hsa_id': None, 'hsa_id_synthetic': False,
        'legitimation': None, 'legitimation_verified': False}


def test_patient_blob_has_none_of_them(app, seeded_app):
    blob = _blob(app, seeded_app['pat_token'])
    assert not set(FIELDS) & set(blob)


def test_su_sets_them_and_the_blob_carries_them(app, seeded_app, events):
    r = _put(app, seeded_app['su_token'], seeded_app['pro_guid'],
             hsa_id=' se5565594230-synt0001 ', legitimation='lakare')
    assert r.status_code == 200, r.get_json()
    blob = _blob(app, seeded_app['pro_token'])
    assert {k: blob.get(k, 'ABSENT') for k in FIELDS} == {
        'hsa_id': GOOD, 'hsa_id_synthetic': True,
        'legitimation': 'lakare', 'legitimation_verified': False}
    assert blob['professional_role']             # the existing fields are intact
    ev = [e for e in events if e['event'] == 'set_professional_qualification']
    assert len(ev) == 1 and ev[0]['user_guid'] == seeded_app['su_guid']
    assert ev[0]['detail']['changes'] == {
        'hsa_id': {'from': None, 'to': GOOD},
        'legitimation': {'from': None, 'to': 'lakare'}}


def test_kill_switch_strips_legacy_fields_but_not_these(app, seeded_app, monkeypatch):
    _put(app, seeded_app['su_token'], seeded_app['pro_guid'], hsa_id=GOOD)
    monkeypatch.setenv('SSO_EMIT_LEGACY_BLOB_FIELDS', 'false')
    blob = _blob(app, seeded_app['pro_token'])
    assert 'professional_role' not in blob
    assert blob.get('hsa_id') == GOOD and 'legitimation_verified' in blob


def test_pending_professional_carries_them_too(app, s9p):
    _put(app, s9p['su_token'], s9p['pending_guid'], hsa_id=GOOD, legitimation='sjukskoterska')
    from src.services.jwt_service import issue_token
    token = issue_token(s9p['pending_guid'], 'test-secret-key-not-for-production', 1)
    blob = _blob(app, token)
    assert blob['activation_pending'] is True
    assert blob.get('hsa_id') == GOOD and blob.get('legitimation') == 'sjukskoterska'


def test_role_per_unit_is_in_the_blob(app, s9):
    """record.pdhc renders 'ansvarig med yrkesroll' from the affiliation role
    per unit; it was already there (S6). This pins it."""
    with app.app_context():
        s = get_session()
        s.add(Affiliation(person_guid=s9['pro_guid'], care_unit_guid=s9['org_guid'],
                          role_guid=s9['role_guids']['doctor'], status='active'))
        s.commit()
        s.close()
    blob = _blob(app, s9['pro_token'])
    assert [(a['care_unit_guid'], a['role']) for a in blob['affiliations']] == [
        (s9['org_guid'], 'doctor')]


def test_admin_user_list_shows_them(app, seeded_app):
    _put(app, seeded_app['su_token'], seeded_app['pro_guid'], hsa_id=GOOD)
    users = app.test_client().get('/api/admin/users', headers=_h(seeded_app['su_token'])).get_json()
    row = next(u for u in (users if isinstance(users, list) else users['users'])
               if u['user_guid'] == seeded_app['pro_guid'])
    assert row['hsa_id'] == GOOD


# --- who may set them --------------------------------------------------------

def test_a_non_su_cannot_set_them(app, seeded_app, events):
    for token in (seeded_app['pro_token'], seeded_app['pat_token']):
        r = _put(app, token, seeded_app['pro_guid'], hsa_id=GOOD, legitimation='lakare')
        assert r.status_code == 403
    assert _stored(app, seeded_app['pro_guid']) is None
    assert not events


def test_the_service_refuses_a_non_su_even_without_the_decorator(app, seeded_app):
    from flask import current_app
    from src.services import professional_qualification as pq
    with app.app_context():
        s = get_session()
        pro = s.query(User).filter_by(guid=seeded_app['pro_guid']).one()
        with pytest.raises(pq.QualificationRefused) as e:
            pq.set_qualification(s, pro, pro.guid, {'hsa_id': GOOD}, current_app.config)
        assert e.value.status == 403
        s.close()


def test_a_non_su_session_cannot_use_the_su_page_form(app, seeded_app):
    c = app.test_client()
    with c.session_transaction() as sess:
        sess['token'] = seeded_app['pro_token']
    c.post('/admin/set-professional-qualification',
           data={'user_guid': seeded_app['pro_guid'], 'hsa_id': GOOD, 'legitimation': 'lakare'})
    assert _stored(app, seeded_app['pro_guid']) is None


def test_the_su_page_form_sets_them(app, seeded_app, events):
    c = app.test_client()
    with c.session_transaction() as sess:
        sess['token'] = seeded_app['su_token']
    page = c.get('/admin').get_data(as_text=True)
    assert 'set-professional-qualification' in page and 'Sjuksköterska' in page
    r = c.post('/admin/set-professional-qualification',
               data={'user_guid': seeded_app['pro_guid'], 'hsa_id': GOOD,
                     'legitimation': 'sjukskoterska'})
    assert r.status_code == 302
    assert _stored(app, seeded_app['pro_guid']) == (GOOD, 'sjukskoterska', False)
    # a blank form clears both
    c.post('/admin/set-professional-qualification',
           data={'user_guid': seeded_app['pro_guid'], 'hsa_id': '', 'legitimation': ''})
    assert _stored(app, seeded_app['pro_guid']) == (None, None, False)


def test_verified_cannot_be_set_and_a_change_resets_it(app, seeded_app):
    _put(app, seeded_app['su_token'], seeded_app['pro_guid'], legitimation='lakare',
         legitimation_verified=True)
    assert _stored(app, seeded_app['pro_guid']) == (None, 'lakare', False)
    with app.app_context():                      # as if level B had verified it
        s = get_session()
        q = s.query(ProfessionalQualification).one()
        q.legitimation_verified = True
        s.commit()
        s.close()
    _put(app, seeded_app['su_token'], seeded_app['pro_guid'], legitimation='tandlakare')
    assert _stored(app, seeded_app['pro_guid']) == (None, 'tandlakare', False)


def test_unknown_user_is_404(app, seeded_app):
    assert _put(app, seeded_app['su_token'], seeded_app['pat_guid'], hsa_id=GOOD).status_code == 404
    assert _put(app, seeded_app['su_token'], 'nope', hsa_id=GOOD).status_code == 404
    assert _put(app, seeded_app['su_token'], seeded_app['pro_guid']).status_code == 400


# --- uniqueness --------------------------------------------------------------

def test_hsa_id_is_unique_when_set(app, s9p):
    assert _put(app, s9p['su_token'], s9p['pro_guid'], hsa_id=GOOD).status_code == 200
    r = _put(app, s9p['su_token'], s9p['pending_guid'], hsa_id=GOOD.lower())
    assert r.status_code == 409 and r.get_json()['error'] == 'hsa_id_taken'
    assert _stored(app, s9p['pending_guid']) is None
    # the holder may save it again; null is not unique
    assert _put(app, s9p['su_token'], s9p['pro_guid'], hsa_id=GOOD).status_code == 200
    assert _put(app, s9p['su_token'], s9p['pending_guid'], legitimation='lakare').status_code == 200
    assert _put(app, s9p['su_token'], s9p['su_guid'], legitimation='lakare').status_code == 200


def test_the_database_enforces_uniqueness_too(app, s9p):
    """Two SUs racing past the check: the unique index decides, as a 409."""
    from flask import current_app
    from src.services import professional_qualification as pq
    _put(app, s9p['su_token'], s9p['pro_guid'], hsa_id=GOOD)
    with app.app_context():
        s = get_session()
        su = s.query(User).filter_by(guid=s9p['su_guid']).one()
        real_query = s.query

        def blind_query(model, *a, **kw):        # the check sees no holder
            q = real_query(model, *a, **kw)
            if model is ProfessionalQualification:
                return q.filter(ProfessionalQualification.id < 0)
            return q
        s.query = blind_query
        try:
            pq.set_qualification(s, su, s9p['pending_guid'], {'hsa_id': GOOD},
                                 current_app.config)
            raised = None
        except Exception as exc:                 # noqa: BLE001 - asserted below
            raised = exc
        assert isinstance(raised, pq.QualificationRefused), repr(raised)
        assert raised.status == 409
        s.close()


def test_deleting_the_account_frees_the_hsa_id(app, s9p):
    _put(app, s9p['su_token'], s9p['pending_guid'], hsa_id=GOOD)
    r = app.test_client().delete(f"/api/admin/users/{s9p['pending_guid']}",
                                 headers=_h(s9p['su_token']))
    assert r.status_code == 200
    assert _put(app, s9p['su_token'], s9p['pro_guid'], hsa_id=GOOD).status_code == 200


# --- format ------------------------------------------------------------------

@pytest.mark.parametrize('bad', [
    'SE556559423-SYNT0001',        # 9-digit organisation number
    'SE55655942300-SYNT0001',      # 11 digits
    'DK5565594230-SYNT0001',       # not SE
    'SE5565594230SYNT0001',        # no dash
    'SE5565594230-',               # empty serial
    'SE5565594230-SYNT 001',       # space
    'SE5565594230-SYNT_001',       # underscore
    'SE5565594230-SYNT0001-2',     # second dash
    'SE5565594230-SYNT' + '0' * 15,  # 32 characters
])
def test_malformed_hsa_ids_are_refused(app, seeded_app, bad):
    r = _put(app, seeded_app['su_token'], seeded_app['pro_guid'], hsa_id=bad)
    assert r.status_code == 400 and r.get_json()['error'] == 'invalid_hsa_id'
    assert _stored(app, seeded_app['pro_guid']) is None


def test_31_characters_is_the_maximum(app, seeded_app):
    hsa = 'SE5565594230-SYNT' + '0' * 14
    assert len(hsa) == 31
    assert _put(app, seeded_app['su_token'], seeded_app['pro_guid'], hsa_id=hsa).status_code == 200


def test_a_real_looking_hsa_id_is_refused_in_the_pilot(app, seeded_app):
    r = _put(app, seeded_app['su_token'], seeded_app['pro_guid'], hsa_id='SE2321000016-1234')
    assert r.status_code == 400 and r.get_json()['error'] == 'hsa_id_not_synthetic'
    assert _stored(app, seeded_app['pro_guid']) is None


def test_with_the_pilot_rule_off_a_real_shape_is_accepted_and_not_marked(seeded_app, app):
    app.config['HSA_ID_SYNTHETIC_ONLY'] = 'false'
    r = _put(app, seeded_app['su_token'], seeded_app['pro_guid'], hsa_id='SE2321000016-1234')
    assert r.status_code == 200
    assert _blob(app, seeded_app['pro_token']).get('hsa_id_synthetic', 'ABSENT') is False


def test_the_prefix_is_configurable(seeded_app, app):
    app.config['HSA_ID_SYNTHETIC_PREFIX'] = 'TEST'
    assert _put(app, seeded_app['su_token'], seeded_app['pro_guid'], hsa_id=GOOD).status_code == 400
    assert _put(app, seeded_app['su_token'], seeded_app['pro_guid'],
                hsa_id='SE5565594230-TEST1').status_code == 200


def test_legitimation_must_be_one_of_socialstyrelsens_list(app, seeded_app):
    from src.services.professional_qualification import LEGITIMATIONSYRKEN
    assert len(LEGITIMATIONSYRKEN) == 22
    assert {'lakare', 'sjukskoterska', 'barnmorska', 'fysioterapeut'} <= set(LEGITIMATIONSYRKEN)
    for bad in ('doctor', 'undersköterska', 'lakare '.upper() + 'x'):
        r = _put(app, seeded_app['su_token'], seeded_app['pro_guid'], legitimation=bad)
        assert r.status_code == 400 and r.get_json()['error'] == 'invalid_legitimation'
    assert _put(app, seeded_app['su_token'], seeded_app['pro_guid'],
                legitimation='BARNMORSKA').status_code == 200
    assert _stored(app, seeded_app['pro_guid'])[1] == 'barnmorska'


def test_hsa_pattern_has_no_way_around_the_anchor():
    from src.services.professional_qualification import _HSA_ID
    assert _HSA_ID.match('SE5565594230-SYNT1')
    assert not _HSA_ID.match('xSE5565594230-SYNT1')
    assert not _HSA_ID.match('SE5565594230-SYNT1\n')
