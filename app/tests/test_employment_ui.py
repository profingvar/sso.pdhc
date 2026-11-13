"""SU admin UI for the main rule (operator 2026-11-13): employment is set in
the user table and only vårdgivare are offered; affiliations are shown as
unit · role; the old memberships are labelled legacy; the S9 unit picker is
no longer a list of every organisation."""
import re

import pytest

from src.db import get_session
from src.models.affiliation import Affiliation
from src.models.employment import Employment
from src.models.organisation import Organisation
from src.models.role import seed_roles
from src.models.user_organisation import UserOrganisation
from tests.test_frontend import (app, client, _make_su, _make_professional,  # noqa: F401
                                 _login_session)


@pytest.fixture
def world(app):
    su_guid, su_token = _make_su(app)
    pro_guid, pro_token = _make_professional(app)
    with app.app_context():
        s = get_session()
        roles = seed_roles(s)
        a = Organisation(name='Caregiver A'); b = Organisation(name='Caregiver B')
        ext = Organisation(name='External X', is_external=True)
        s.add_all([a, b, ext]); s.flush()
        a1 = Organisation(name='Unit A1', parent_caregiver_guid=a.guid)
        s.add(a1); s.flush()
        w = dict(su=su_guid, su_token=su_token, pro=pro_guid, pro_token=pro_token,
                 a=a.guid, b=b.guid, ext=ext.guid, a1=a1.guid,
                 doctor=roles['doctor'].guid)
        s.commit(); s.close()
    return w


def _employ(app, w, caregiver):
    with app.app_context():
        s = get_session()
        e = Employment(person_guid=w['pro'], caregiver_guid=caregiver)
        s.add(e); s.commit()
        guid = e.guid; s.close()
        return guid


def _affiliate(app, w, unit):
    with app.app_context():
        s = get_session()
        s.add(Affiliation(person_guid=w['pro'], care_unit_guid=unit,
                          role_guid=w['doctor'], status='active'))
        s.commit(); s.close()


def _member(app, w, org):
    with app.app_context():
        s = get_session()
        s.add(UserOrganisation(user_guid=w['pro'], organisation_guid=org))
        s.commit(); s.close()


def _employments(app, w):
    with app.app_context():
        s = get_session()
        out = sorted(e.caregiver_guid for e in s.query(Employment).filter_by(person_guid=w['pro']))
        s.close()
        return out


def _page(client, w):
    _login_session(client, None, w['su_token'])
    r = client.get('/admin')
    assert r.status_code == 200
    return r.data.decode()


def _caregiver_select(html, user_guid):
    """The '+ Anställning' select of ONE user's row."""
    m = re.search(r'name="user_guid" value="' + user_guid +
                  r'">\s*<select name="caregiver_guid".*?</select>', html, re.S)
    assert m, 'no employment picker for this user'
    return m.group(0)


def test_the_employment_picker_offers_ONLY_vardgivare(client, app, world):
    sel = _caregiver_select(_page(client, world), world['pro'])
    assert world['a'] in sel and world['b'] in sel
    assert world['a1'] not in sel, 'a vårdenhet is not an employer'
    assert world['ext'] not in sel, 'an external organisation is not an employer'


def test_the_picker_omits_a_vardgivare_already_employing_the_person(client, app, world):
    _employ(app, world, world['a'])
    sel = _caregiver_select(_page(client, world), world['pro'])
    assert world['a'] not in sel and world['b'] in sel


def test_the_row_shows_employment_and_affiliation_unit_and_role(client, app, world):
    _employ(app, world, world['a']); _affiliate(app, world, world['a1'])
    html = _page(client, world)
    assert 'Anställd av' in html and 'Affilierad till' in html
    assert re.search(r'class="aff-chip"[^>]*>\s*Unit A1 · ', html)


def test_a_membership_is_labelled_legacy_and_says_it_grants_nothing(client, app, world):
    _employ(app, world, world['a']); _affiliate(app, world, world['a1'])
    _member(app, world, world['b'])
    html = _page(client, world)
    assert 'Äldre medlemskap (1)' in html and 'ger ingen patientbehörighet' in html


def test_a_membership_WITHOUT_affiliations_says_it_is_the_fallback(client, app, world):
    """caller_org_ids falls back to organization_ids when there is no
    affiliation, so "grants nothing" would be false here."""
    _member(app, world, world['b'])
    html = _page(client, world)
    assert 'används som reserv' in html
    assert 'ger ingen patientbehörighet' not in html


def test_the_old_add_org_control_is_gone(client, app, world):
    html = _page(client, world)
    assert '+ Org' not in html and 'action="/admin/assign-org"' not in html


def test_the_s9_unit_picker_is_not_server_filled_with_every_organisation(client, app, world):
    html = _page(client, world)
    m = re.search(r'<select id="s9-unit"[^>]*>(.*?)</select>', html, re.S)
    assert m and '<option' not in m.group(1)


def test_form_assigns_an_employment_and_audits(client, app, world, monkeypatch):
    events = []
    monkeypatch.setattr('src.routes.frontend.audit', lambda ev, **kw: events.append(ev))
    _login_session(client, None, world['su_token'])
    r = client.post('/admin/assign-employment',
                    data={'user_guid': world['pro'], 'caregiver_guid': world['b']})
    assert r.status_code == 302
    assert _employments(app, world) == [world['b']]
    assert events == ['employment_create']


def test_form_refuses_a_UNIT_as_employer(client, app, world):
    _login_session(client, None, world['su_token'])
    r = client.post('/admin/assign-employment',
                    data={'user_guid': world['pro'], 'caregiver_guid': world['a1']},
                    follow_redirects=True)
    assert 'is a vårdenhet, not a vårdgivare' in r.data.decode()
    assert _employments(app, world) == []


def test_form_refuses_removal_while_an_affiliation_depends_on_it(client, app, world):
    eg = _employ(app, world, world['a']); _affiliate(app, world, world['a1'])
    _login_session(client, None, world['su_token'])
    r = client.post('/admin/remove-employment',
                    data={'user_guid': world['pro'], 'employment_guid': eg},
                    follow_redirects=True)
    assert 'remove the affiliations under this vårdgivare first (1 affiliation(s))' in r.data.decode()
    assert _employments(app, world) == [world['a']]


def test_form_removes_an_employment_with_no_dependants(client, app, world):
    eg = _employ(app, world, world['b'])
    _login_session(client, None, world['su_token'])
    r = client.post('/admin/remove-employment',
                    data={'user_guid': world['pro'], 'employment_guid': eg})
    assert r.status_code == 302 and _employments(app, world) == []


def test_a_non_su_cannot_use_the_employment_forms(client, app, world):
    _login_session(client, None, world['pro_token'])
    client.post('/admin/assign-employment',
                data={'user_guid': world['pro'], 'caregiver_guid': world['b']})
    assert _employments(app, world) == []
