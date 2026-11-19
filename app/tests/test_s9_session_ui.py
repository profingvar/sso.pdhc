"""The SU admin page is signed in by SESSION, not Bearer (2026-11-19).

Its S9 panel (employments, affiliations, activation) and the role registry
called Bearer-only /api routes, and nothing put a Bearer in the page -- so in a
browser Load came back 401 and no affiliation could be assigned, while every
API test (all Bearer) stayed green. These tests go through a session.
"""
import re

import pytest

from tests.test_s9_signon import s9  # noqa: F401  (fixture)


def _session(client, s9):
    with client.session_transaction() as s:
        s['token'] = s9['su_token']
    html = client.get('/admin').get_data(as_text=True)
    m = re.search(r"'X-CSRFToken': '([^']+)'", html)
    assert m, "the page must hand its fetches a CSRF token"
    return {'X-CSRFToken': m.group(1)}


def _aff(client, s9, h, role='doctor', **kw):
    return client.post(f"/api/admin/users/{s9['pending_guid']}/affiliations", headers=h,
                       json={'care_unit_guid': s9['org_guid'],
                             'role_guid': s9['role_guids'][role], **kw})


def test_session_signed_su_can_load_and_assign(app, s9):
    c = app.test_client()
    h = _session(c, s9)
    assert c.get(f"/api/admin/users/{s9['pending_guid']}/affiliations", headers=h).status_code == 200
    assert c.get('/api/registry/roles', headers=h).status_code == 200
    r = _aff(c, s9, h)
    assert r.status_code == 201, r.get_data(as_text=True)


def test_session_without_the_csrf_header_is_refused(app, s9):
    """A cross-site page can make the browser send the cookie, not the token."""
    c = app.test_client()
    _session(c, s9)
    assert c.get(f"/api/admin/users/{s9['pending_guid']}/affiliations").status_code == 401
    assert _aff(c, s9, {'X-CSRFToken': 'forged'}).status_code == 401


def test_a_non_su_session_is_still_forbidden(app, s9):
    c = app.test_client()
    with c.session_transaction() as s:
        s['token'] = s9['su_token']
    h = {'X-CSRFToken': re.search(r"'X-CSRFToken': '([^']+)'",
                                  c.get('/admin').get_data(as_text=True)).group(1)}
    with c.session_transaction() as s:
        s['token'] = s9['pro_token']
    assert _aff(c, s9, h).status_code == 403


# ---- change the role of an affiliation in place ----------------------------

def _patch(c, s9, h, aff, role, **kw):
    return c.patch(f"/api/admin/users/{s9['pending_guid']}/affiliations/{aff}", headers=h,
                   json={'role_guid': s9['role_guids'][role], **kw})


def test_change_role_keeps_the_affiliation(app, s9):
    c = app.test_client()
    h = _session(c, s9)
    aff = _aff(c, s9, h).get_json()['affiliation_guid']
    r = _patch(c, s9, h, aff, 'nurse')
    assert r.status_code == 200, r.get_data(as_text=True)
    affs = c.get(f"/api/admin/users/{s9['pending_guid']}/affiliations", headers=h).get_json()['affiliations']
    assert [(a['affiliation_guid'], a['role']) for a in affs] == [(aff, 'nurse')]


def test_change_role_validates_like_create(app, s9):
    c = app.test_client()
    h = _session(c, s9)
    aff = _aff(c, s9, h).get_json()['affiliation_guid']
    assert _patch(c, s9, h, aff, 'researcher').status_code == 400            # no project
    r = _patch(c, s9, h, aff, 'researcher', research_project_guids=[s9['proj_guid']])
    assert r.status_code == 200, r.get_data(as_text=True)
    assert _patch(c, s9, h, aff, 'nurse', research_project_guids=[s9['proj_guid']]).status_code == 400
    r = _patch(c, s9, h, aff, 'nurse')                                       # projects dropped
    assert r.status_code == 200
    other = _aff(c, s9, h, role='doctor').get_json()['affiliation_guid']
    assert _patch(c, s9, h, other, 'nurse').status_code == 409               # would duplicate
    assert c.patch(f"/api/admin/users/{s9['pending_guid']}/affiliations/nope", headers=h,
                   json={'role_guid': s9['role_guids']['nurse']}).status_code == 404


def test_change_role_is_su_only(app, s9):
    c = app.test_client()
    h = _session(c, s9)
    aff = _aff(c, s9, h).get_json()['affiliation_guid']
    r = c.patch(f"/api/admin/users/{s9['pending_guid']}/affiliations/{aff}",
                headers={'Authorization': f"Bearer {s9['pro_token']}"},
                json={'role_guid': s9['role_guids']['nurse']})
    assert r.status_code == 403


# ---- the person's own professional role (doctor / nurse / other) -----------

def test_su_can_set_the_professional_role(app, s9):
    c = app.test_client()
    h = _session(c, s9)
    r = c.post('/admin/set-professional-role',
               data={'user_guid': s9['pro_guid'], 'professional_role': 'doctor',
                     'csrf_token': h['X-CSRFToken']})
    assert r.status_code == 302
    from src.db import get_session
    from src.models.user import User
    from src.models.professional import Professional
    with app.app_context():
        s = get_session()
        u = s.query(User).filter_by(guid=s9['pro_guid']).one()
        assert s.query(Professional).filter_by(user_id=u.id).one().professional_role == 'doctor'
        s.close()
    r = c.post('/admin/set-professional-role',
               data={'user_guid': s9['pro_guid'], 'professional_role': 'king',
                     'csrf_token': h['X-CSRFToken']})
    assert r.status_code == 302   # refused with a flash, value unchanged
    with app.app_context():
        s = get_session()
        u = s.query(User).filter_by(guid=s9['pro_guid']).one()
        assert s.query(Professional).filter_by(user_id=u.id).one().professional_role == 'doctor'
        s.close()
