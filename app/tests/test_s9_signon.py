"""S9 (#411) — guided professional sign-on + SU affiliation assignment.

DoD coverage:
  (a) non-SU affiliation POST -> 403
  (b) activation blocked while the profile is incomplete (server-side)
  (c) a completed profile yields the correct affiliations[] in the blob
Plus: a pending professional gets a NO-ACCESS blob; approval of an
access request creates a PENDING user; researcher affiliations require
registered research projects.
"""
import pytest

from src.db import get_session
from src.models.role import seed_roles
from src.models.user import User
from src.models.user_phase import UserPhase
from src.models.research_project import ResearchProject


def _auth(token):
    return {'Authorization': f'Bearer {token}'}


@pytest.fixture
def s9(seeded_app, app):
    """seeded_app + role registry + one research project + a PENDING
    professional (as if just approved from an access request)."""
    from src.services.auth_service import hash_password
    with app.app_context():
        s = get_session()
        roles = seed_roles(s)
        proj = ResearchProject(name='S9 test project', ethics_ref='EPM-1')
        s.add(proj)
        pend = User(email='pending@test.com',
                    password_hash=hash_password('pendpass1234'),
                    user_type='professional', is_su_admin=False,
                    status='pending')
        s.add(pend)
        s.flush()
        data = dict(seeded_app)
        data['pending_guid'] = pend.guid
        data['proj_guid'] = proj.guid
        data['role_guids'] = {code: r.guid for code, r in roles.items()}
        s.commit()
        s.close()
        return data


# --- (a) SU-only enforcement ------------------------------------------------

def test_non_su_affiliation_post_403(app, s9):
    client = app.test_client()
    resp = client.post(f"/api/admin/users/{s9['pending_guid']}/affiliations",
                       json={'care_unit_guid': s9['org_guid'],
                             'role_guid': s9['role_guids']['doctor']},
                       headers=_auth(s9['pro_token']))
    assert resp.status_code == 403


def test_non_su_activate_403(app, s9):
    client = app.test_client()
    resp = client.post(f"/api/admin/users/{s9['pending_guid']}/activate",
                       headers=_auth(s9['pro_token']))
    assert resp.status_code == 403


# --- (b) activation gate is server-enforced ---------------------------------

def test_activate_blocked_without_affiliation(app, s9):
    client = app.test_client()
    resp = client.post(f"/api/admin/users/{s9['pending_guid']}/activate",
                       headers=_auth(s9['su_token']))
    assert resp.status_code == 409
    body = resp.get_json()
    assert 'no_active_affiliation' in body['missing']
    assert 'no_useful_phase_grant' in body['missing']


def test_activate_blocked_without_useful_phase(app, s9):
    client = app.test_client()
    r = client.post(f"/api/admin/users/{s9['pending_guid']}/affiliations",
                    json={'care_unit_guid': s9['org_guid'],
                          'role_guid': s9['role_guids']['doctor']},
                    headers=_auth(s9['su_token']))
    assert r.status_code == 201
    resp = client.post(f"/api/admin/users/{s9['pending_guid']}/activate",
                       headers=_auth(s9['su_token']))
    assert resp.status_code == 409
    assert resp.get_json()['missing'] == ['no_useful_phase_grant']


def test_researcher_affiliation_requires_registered_projects(app, s9):
    client = app.test_client()
    # no projects -> 400
    r = client.post(f"/api/admin/users/{s9['pending_guid']}/affiliations",
                    json={'care_unit_guid': s9['org_guid'],
                          'role_guid': s9['role_guids']['researcher']},
                    headers=_auth(s9['su_token']))
    assert r.status_code == 400
    # unknown project -> 400
    r = client.post(f"/api/admin/users/{s9['pending_guid']}/affiliations",
                    json={'care_unit_guid': s9['org_guid'],
                          'role_guid': s9['role_guids']['researcher'],
                          'research_project_guids': ['nope']},
                    headers=_auth(s9['su_token']))
    assert r.status_code == 400
    # registered project -> 201
    r = client.post(f"/api/admin/users/{s9['pending_guid']}/affiliations",
                    json={'care_unit_guid': s9['org_guid'],
                          'role_guid': s9['role_guids']['researcher'],
                          'research_project_guids': [s9['proj_guid']]},
                    headers=_auth(s9['su_token']))
    assert r.status_code == 201


def test_duplicate_affiliation_409(app, s9):
    client = app.test_client()
    body = {'care_unit_guid': s9['org_guid'],
            'role_guid': s9['role_guids']['nurse']}
    assert client.post(f"/api/admin/users/{s9['pending_guid']}/affiliations",
                       json=body, headers=_auth(s9['su_token'])).status_code == 201
    assert client.post(f"/api/admin/users/{s9['pending_guid']}/affiliations",
                       json=body, headers=_auth(s9['su_token'])).status_code == 409


# --- pending = zero access ---------------------------------------------------

def test_pending_professional_gets_no_access_blob(app, s9):
    client = app.test_client()
    # login as the pending professional
    r = client.post('/api/auth/login',
                    json={'email': 'pending@test.com',
                          'password': 'pendpass1234'})
    assert r.status_code == 200
    token = r.get_json().get('token') or r.get_json().get('access_token')
    me = client.get('/api/auth/me', headers=_auth(token))
    assert me.status_code == 200
    blob = me.get_json()
    assert blob['activation_pending'] is True
    assert blob['effective_phases'] == []
    assert blob['session_phases'] == []
    assert blob['affiliations'] == []
    assert blob['organization_ids'] == []


# --- (c) completed profile -> correct blob -----------------------------------

def test_full_guided_flow_yields_correct_blob(app, s9):
    client = app.test_client()
    su = _auth(s9['su_token'])
    guid = s9['pending_guid']

    # SU assigns a doctor affiliation …
    r = client.post(f"/api/admin/users/{guid}/affiliations",
                    json={'care_unit_guid': s9['org_guid'],
                          'role_guid': s9['role_guids']['doctor']},
                    headers=su)
    assert r.status_code == 201
    # … grants a phase the role can use …
    r = client.post(f"/api/admin/users/{guid}/phases",
                    json={'phase': 'analysis'}, headers=su)
    assert r.status_code in (200, 201)
    # … completeness now reports complete …
    r = client.get(f"/api/admin/users/{guid}/completeness", headers=su)
    assert r.status_code == 200 and r.get_json()['complete'] is True
    # … and activation succeeds.
    r = client.post(f"/api/admin/users/{guid}/activate", headers=su)
    assert r.status_code == 200

    # The person's blob now carries the assigned affiliation + phases.
    r = client.post('/api/auth/login',
                    json={'email': 'pending@test.com',
                          'password': 'pendpass1234'})
    token = r.get_json().get('token') or r.get_json().get('access_token')
    blob = client.get('/api/auth/me', headers=_auth(token)).get_json()
    assert blob['activation_pending'] is False
    affs = blob['affiliations']
    assert len(affs) == 1
    assert affs[0]['care_unit_guid'] == s9['org_guid']
    assert affs[0]['role'] == 'doctor'
    assert blob['active_affiliation_guid'] == affs[0]['affiliation_guid']
    assert blob['session_phases'] == ['analysis']

    # Deactivation revokes on the next blob build.
    r = client.post(f"/api/admin/users/{guid}/deactivate", headers=su)
    assert r.status_code == 200
    blob = client.get('/api/auth/me', headers=_auth(token)).get_json()
    assert blob['activation_pending'] is True
    assert blob['session_phases'] == []


# --- approve flow creates PENDING --------------------------------------------

def test_access_request_approval_creates_pending_user(app, s9):
    from src.models.access_request import AccessRequest
    from src.services.auth_service import hash_password
    with app.app_context():
        s = get_session()
        ar = AccessRequest(
            email='newpro@test.com', password_hash=hash_password('newpass1234'),
            first_name='New', last_name='Pro', professional_role='doctor',
            organisation_guid=s9['org_guid'],
            requested_phases=['analysis'], chosen_leader_guid=s9['su_guid'],
        )
        s.add(ar)
        s.commit()
        ar_guid = ar.guid
        s.close()

    client = app.test_client()
    r = client.post('/api/admin/access-requests',
                    json={'access_request_guid': ar_guid,
                          'decision': 'approved'},
                    headers=_auth(s9['su_token']))
    assert r.status_code == 200
    body = r.get_json()
    assert body['activation_pending'] is True
    assert body['hint']['care_unit_guid'] == s9['org_guid']

    with app.app_context():
        s = get_session()
        u = s.query(User).filter_by(guid=body['user_guid']).first()
        assert u.status == 'pending'
        s.close()
