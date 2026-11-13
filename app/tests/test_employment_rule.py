"""Main rule (operator 2026-11-13): a professional is EMPLOYED by one or more
vårdgivare and may only be AFFILIATED to a care unit under one of them."""
import pytest

from src.db import get_session
from src.models.employment import Employment
from src.models.organisation import Organisation
from src.models.user import User
from src.services.auth_service import hash_password
from tests.test_s9_signon import _auth
from src.models.role import seed_roles


@pytest.fixture
def world(seeded_app, app):
    """Two vårdgivare: A (with unit A1) and B (with unit B1); one pending
    professional employed by A only."""
    with app.app_context():
        s = get_session()
        roles = seed_roles(s)
        a = Organisation(name='Caregiver A'); b = Organisation(name='Caregiver B')
        s.add_all([a, b]); s.flush()
        a1 = Organisation(name='Unit A1', parent_caregiver_guid=a.guid)
        b1 = Organisation(name='Unit B1', parent_caregiver_guid=b.guid)
        p = User(email='emp@test.com', password_hash=hash_password('emppass12345'),
                 user_type='professional', is_su_admin=False, status='pending')
        s.add_all([a1, b1, p]); s.flush()
        s.add(Employment(person_guid=p.guid, caregiver_guid=a.guid))
        d = dict(seeded_app, a=a.guid, b=b.guid, a1=a1.guid, b1=b1.guid, p=p.guid,
                 doctor=roles['doctor'].guid)
        s.commit(); s.close()
        return d


def _aff(client, w, unit):
    return client.post(f"/api/admin/users/{w['p']}/affiliations",
                       json={'care_unit_guid': unit, 'role_guid': w['doctor']},
                       headers=_auth(w['su_token']))


def test_a_unit_under_the_EMPLOYER_is_allowed(app, world):
    assert _aff(app.test_client(), world, world['a1']).status_code == 201


def test_a_unit_under_ANOTHER_vardgivare_is_refused(app, world):
    r = _aff(app.test_client(), world, world['b1'])
    assert r.status_code == 409 and r.get_json()['error'] == 'affiliation_outside_employment'
    assert 'Caregiver B' in r.get_json()['message']


def test_the_vardgivare_itself_counts_as_its_own_unit(app, world):
    c = app.test_client()
    assert _aff(c, world, world['a']).status_code == 201
    assert _aff(c, world, world['b']).status_code == 409


def test_adding_the_employment_then_allows_the_affiliation(app, world):
    c = app.test_client()
    su = _auth(world['su_token'])
    r = c.post(f"/api/admin/users/{world['p']}/employments", json={'caregiver_guid': world['b']}, headers=su)
    assert r.status_code == 201
    assert _aff(c, world, world['b1']).status_code == 201


def test_employment_must_be_a_VARDGIVARE_not_a_unit(app, world):
    r = app.test_client().post(f"/api/admin/users/{world['p']}/employments",
                               json={'caregiver_guid': world['a1']}, headers=_auth(world['su_token']))
    assert r.status_code == 400 and 'vårdenhet' in r.get_json()['message']


def test_duplicate_employment_409(app, world):
    r = app.test_client().post(f"/api/admin/users/{world['p']}/employments",
                               json={'caregiver_guid': world['a']}, headers=_auth(world['su_token']))
    assert r.status_code == 409


def test_an_employment_with_dependent_affiliations_cannot_be_removed(app, world):
    c = app.test_client(); su = _auth(world['su_token'])
    assert _aff(c, world, world['a1']).status_code == 201
    emp = c.get(f"/api/admin/users/{world['p']}/employments", headers=su).get_json()['employments'][0]
    r = c.delete(f"/api/admin/users/{world['p']}/employments/{emp['employment_guid']}", headers=su)
    assert r.status_code == 409 and r.get_json()['affiliation_guids']


def test_non_su_cannot_add_employment(app, world):
    r = app.test_client().post(f"/api/admin/users/{world['p']}/employments",
                               json={'caregiver_guid': world['b']}, headers=_auth(world['pro_token']))
    assert r.status_code == 403


def test_completeness_names_a_missing_employment(app, world):
    with app.app_context():
        s = get_session()
        q = User(email='noemp@test.com', password_hash=hash_password('noemppass123'),
                 user_type='professional', is_su_admin=False, status='pending')
        s.add(q); s.commit(); guid = q.guid; s.close()
    r = app.test_client().get(f"/api/admin/users/{guid}/completeness", headers=_auth(world['su_token']))
    assert 'no_employment' in r.get_json()['missing']


def test_the_blob_carries_the_employments(app, world):
    c = app.test_client(); su = _auth(world['su_token'])
    _aff(c, world, world['a1'])
    c.post(f"/api/admin/users/{world['p']}/phases", json={'phase': 'analysis'}, headers=su)
    assert c.post(f"/api/admin/users/{world['p']}/activate", headers=su).status_code == 200
    tok = c.post('/api/auth/login', json={'email': 'emp@test.com', 'password': 'emppass12345'}).get_json()
    tok = tok.get('token') or tok.get('access_token')
    blob = c.get('/api/auth/me', headers=_auth(tok)).get_json()
    assert blob['employments'] == [{'caregiver_guid': world['a'], 'caregiver_name': 'Caregiver A'}]
    assert [a['care_unit_guid'] for a in blob['affiliations']] == [world['a1']]
