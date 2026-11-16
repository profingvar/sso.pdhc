"""#863 — the organisation controls that changed nothing are gone.

Approving an access request used to write a legacy membership, and two "+ Org"
controls (SU and group-admin page) wrote the same: a row that grants nothing,
since production emits no organization_ids and every consumer reads
affiliations. Operator decision A (2026-11-16): approval EMPLOYS the person by
the requested organisation's vårdgivare; the SU still affiliates and
activates in S9. The API twin answers 410 instead of silently succeeding.
"""
import uuid

import pytest

from src.db import get_session
from src.models.access_request import AccessRequest
from src.models.employment import Employment
from src.models.user import User
from src.models.user_organisation import UserOrganisation
from src.services.auth_service import hash_password
from tests.test_employment_ui import world  # noqa: F401  (fixture)
from tests.test_frontend import app, client, _login_session  # noqa: F401


def _request(app, w, org):
    with app.app_context():
        s = get_session()
        ar = AccessRequest(guid=str(uuid.uuid4()), email=f'{uuid.uuid4().hex[:8]}@test.com',
                           password_hash=hash_password('newpass12'), first_name='New',
                           last_name='Pro', professional_role='doctor', organisation_guid=org,
                           requested_phases=['request'], chosen_leader_guid=w['su'],
                           status='pending')
        s.add(ar); s.commit()
        out = (ar.guid, ar.email); s.close()
        return out


def _state(app, email):
    """(employer guids, legacy membership org guids, user status) for the created person."""
    with app.app_context():
        s = get_session()
        u = s.query(User).filter_by(email=email).one()
        emp = {e.caregiver_guid for e in s.query(Employment).filter_by(person_guid=u.guid)}
        mem = {m.organisation_guid for m in s.query(UserOrganisation).filter_by(user_guid=u.guid)}
        out = (emp, mem, u.status); s.close()
        return out


def _api_approve(client, w, ar_guid):
    return client.post('/api/admin/access-requests',
                       json={'access_request_guid': ar_guid, 'decision': 'approved'},
                       headers={'Authorization': f"Bearer {w['su_token']}"})


def _ui_approve(client, app, w, ar_guid):
    _login_session(client, app, w['su_token'])
    return client.post('/admin/decide-access-request',
                       data={'access_request_guid': ar_guid, 'decision': 'approved'})


# ------------------------------------------------------------ approval employs

@pytest.mark.parametrize('via', ['api', 'ui'])
def test_approval_EMPLOYS_by_the_vardgivare_and_writes_no_membership(client, app, world, via):
    ar, email = _request(app, world, world['a'])
    r = _api_approve(client, world, ar) if via == 'api' else _ui_approve(client, app, world, ar)
    assert r.status_code in (200, 302)
    assert _state(app, email) == ({world['a']}, set(), 'pending'), \
        "employed, no inert membership, PENDING on both paths (operator 2026-11-16)"


@pytest.mark.parametrize('via', ['api', 'ui'])
def test_a_request_naming_a_UNIT_employs_by_its_parent_vardgivare(client, app, world, via):
    ar, email = _request(app, world, world['a1'])
    _api_approve(client, world, ar) if via == 'api' else _ui_approve(client, app, world, ar)
    assert _state(app, email)[0] == {world['a']}


def test_an_EXTERNAL_organisation_approves_without_an_employment_and_says_why(client, app, world):
    ar, email = _request(app, world, world['ext'])
    r = _api_approve(client, world, ar)
    assert r.status_code == 200
    assert _state(app, email)[:2] == (set(), set())
    assert 'external' in r.get_json()['employment']


def test_the_api_response_names_the_employment(client, app, world):
    ar, _ = _request(app, world, world['a'])
    assert _api_approve(client, world, ar).get_json()['employment'] == 'employed by Caregiver A'


# ------------------------------------------------------------ the inert writers

def test_the_membership_API_refuses_with_410_and_writes_nothing(client, app, world):
    r = client.post(f"/api/admin/users/{world['pro']}/organisations",
                    json={'organisation_guid': world['a']},
                    headers={'Authorization': f"Bearer {world['su_token']}"})
    assert r.status_code == 410
    assert 'employments' in r.get_json()['message']
    with app.app_context():
        assert get_session().query(UserOrganisation).filter_by(user_guid=world['pro']).count() == 0


@pytest.mark.parametrize('path', ['/admin/assign-org', '/group-admin/assign-org'])
def test_the_form_routes_are_gone(client, app, world, path):
    _login_session(client, app, world['su_token'])
    assert client.post(path, data={'user_guid': world['pro'],
                                   'organisation_guid': world['a']}).status_code == 404


def test_the_group_admin_page_has_no_add_org_control(client, app, world):
    # The control lived INSIDE the member loop, so the page needs a group with
    # an approved member or the assertion below passes on the old template too.
    from src.models.group import Group
    from src.models.membership import Membership
    with app.app_context():
        s = get_session()
        grp = Group(name='Diabetes team', category='team'); s.add(grp); s.flush()
        s.add(Membership(user_guid=world['pro'], group_guid=grp.guid, status='approved'))
        s.commit(); s.close()
    _login_session(client, app, world['su_token'])
    html = client.get('/group-admin').get_data(as_text=True)
    assert 'Group Admin Panel' in html and 'pro@test.com' in html, "the member row must render"
    assert 'assign-org' not in html and '+ Org' not in html


def test_removing_an_old_membership_still_works(client, app, world):
    with app.app_context():
        s = get_session(); s.add(UserOrganisation(user_guid=world['pro'], organisation_guid=world['b']))
        s.commit(); s.close()
    r = client.delete(f"/api/admin/users/{world['pro']}/organisations/{world['b']}",
                      headers={'Authorization': f"Bearer {world['su_token']}"})
    assert r.status_code in (200, 204)
