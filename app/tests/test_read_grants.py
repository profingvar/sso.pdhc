"""External read grants (2026-11-19): SU issues a key for at most 48 h, bound
to chosen patient organisations and chosen CDRs; a CDR validates it per request."""
from datetime import datetime, timedelta, timezone

import pytest

SVC = {'X-SSO-Client-Id': 'test-client', 'X-SSO-Client-Secret': 'test-secret'}


def _su(seeded_app):
    return {'Authorization': f"Bearer {seeded_app['su_token']}"}


@pytest.fixture
def partner(client, seeded_app):
    r = client.post('/api/admin/partners', headers={**_su(seeded_app), 'X-Service-Key': 'test-internal-key'},
                    json={'display_name': 'Forskargruppen AB', 'country_code': 'SE',
                          'contact_email': 'x@example.org', 'auth_kind': 'none',
                          'allowed_services': ['cdr.pdhc']})
    assert r.status_code == 201, r.get_data(as_text=True)
    return r.get_json()['partner_guid']


def _issue(client, seeded_app, partner, **kw):
    body = {'org_guids': [seeded_app['org_guid']], 'cdrs': ['cdr2'], 'hours': 48,
            'purpose_code': 'statistics', **kw}
    return client.post(f'/api/admin/partners/{partner}/read-grants', json=body,
                       headers=_su(seeded_app))


def _validate(client, key, cdr='cdr2', headers=SVC):
    return client.post('/api/internal/read-grants/validate', json={'key': key, 'cdr': cdr},
                       headers=headers)


def _set(app, guid, **cols):
    from src.db import get_session
    from src.models.read_grant import ReadGrant
    with app.app_context():
        s = get_session()
        g = s.query(ReadGrant).filter_by(guid=guid).one()
        for k, v in cols.items():
            setattr(g, k, v)
        s.commit()
        s.close()


def test_issue_returns_key_once_and_validate_returns_the_scope(client, seeded_app, partner):
    r = _issue(client, seeded_app, partner, purpose='studie X')
    assert r.status_code == 201, r.get_data(as_text=True)
    body = r.get_json()
    assert body['key'].startswith(f"rg.{body['guid']}.") and body['status'] == 'active'
    hours = (datetime.fromisoformat(body['expires_at']) - datetime.fromisoformat(body['created_at']))
    assert hours == timedelta(hours=48)
    v = _validate(client, body['key'])
    assert v.status_code == 200
    assert v.get_json()['org_guids'] == [seeded_app['org_guid']]
    assert v.get_json()['cdrs'] == ['cdr2'] and v.get_json()['partner_name'] == 'Forskargruppen AB'
    listed = client.get(f'/api/admin/partners/{partner}/read-grants', headers=_su(seeded_app)).get_json()
    assert 'key' not in listed['items'][0] and listed['items'][0]['last_used_at']


@pytest.mark.parametrize('kw,frag', [
    ({'hours': 49}, 'hours'), ({'hours': 0}, 'hours'), ({'hours': True}, 'hours'),
    ({'cdrs': []}, 'cdrs'), ({'cdrs': ['cdr_6']}, 'cdrs'),
    ({'org_guids': []}, 'org_guids'), ({'org_guids': ['nope']}, 'org_guids'),
    ({'purpose_code': None}, 'purpose_code'), ({'purpose_code': 'care'}, 'purpose_code'),
    ({'purpose_code': 'research'}, 'research_project_guids'),
    ({'purpose_code': 'research', 'research_project_guids': ['nope']}, 'research_project_guids'),
])
def test_issue_refuses_bad_input(client, seeded_app, partner, kw, frag):
    r = _issue(client, seeded_app, partner, **kw)
    assert r.status_code == 400 and frag in r.get_json()['message']


def test_issue_needs_su_and_an_external_partner(client, seeded_app, partner):
    r = client.post(f'/api/admin/partners/{partner}/read-grants',
                    json={'org_guids': [seeded_app['org_guid']], 'cdrs': ['cdr2']},
                    headers={'Authorization': f"Bearer {seeded_app['pro_token']}"})
    assert r.status_code == 403
    assert _issue(client, seeded_app, seeded_app['org_guid']).status_code == 404  # internal org


def test_validate_refuses_other_cdr_wrong_secret_and_bad_service(client, seeded_app, partner):
    key = _issue(client, seeded_app, partner).get_json()['key']
    r = _validate(client, key, cdr='cdr3')
    assert r.status_code == 403 and r.get_json()['reason'] == 'cdr_not_granted'
    assert _validate(client, key[:-2] + 'xx').status_code == 401
    assert _validate(client, 'rg.00000000-0000-0000-0000-000000000000.x').status_code == 401
    assert _validate(client, 'garbage').status_code == 401
    assert _validate(client, key, headers={'X-SSO-Client-Id': 'test-client',
                                           'X-SSO-Client-Secret': 'wrong'}).status_code == 403


def test_revoked_and_expired_grants_are_refused(client, seeded_app, partner, app):
    a = _issue(client, seeded_app, partner).get_json()
    assert client.post(f"/api/admin/read-grants/{a['guid']}/revoke",
                       headers=_su(seeded_app)).get_json()['status'] == 'revoked'
    assert _validate(client, a['key']).get_json()['reason'] == 'revoked'

    b = _issue(client, seeded_app, partner).get_json()
    _set(app, b['guid'], expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
    assert _validate(client, b['key']).get_json()['reason'] == 'expired'

    # A row stretched past 48 h by hand is still refused: the ceiling is
    # checked against created_at, not only the stored expiry.
    c = _issue(client, seeded_app, partner).get_json()
    _set(app, c['guid'], created_at=datetime.now(timezone.utc) - timedelta(hours=49),
         expires_at=datetime.now(timezone.utc) + timedelta(hours=10))
    assert _validate(client, c['key']).get_json()['reason'] == 'expired'


def test_suspended_partner_stops_its_grants(client, seeded_app, partner):
    key = _issue(client, seeded_app, partner).get_json()['key']
    client.post(f'/api/admin/partners/{partner}/suspend',
                headers={**_su(seeded_app), 'X-Service-Key': 'test-internal-key'}, json={})
    r = _validate(client, key)
    assert r.status_code == 403 and r.get_json()['reason'] == 'partner_inactive'


# ---- The SU admin PAGE is signed in by session cookie, not Bearer (2026-11-19).
# The first UI fetched its organisation list from /api/admin/organisations, a
# Bearer-only route, so in the browser the picker came back empty and no key
# could be issued -- while every API test above (all Bearer) stayed green.

def _session(client, seeded_app):
    with client.session_transaction() as s:
        s['token'] = seeded_app['su_token']


def test_admin_page_carries_the_organisations_for_the_picker(client, seeded_app):
    _session(client, seeded_app)
    html = client.get('/admin').get_data(as_text=True)
    assert 'const RG_ALL_ORGS = ' in html
    i = html.index('const RG_ALL_ORGS = ')
    assert seeded_app['org_guid'] in html[i:i + 20000]
    assert "fetch('/api/admin/organisations'" not in html


def test_a_session_signed_su_can_issue_without_a_bearer(client, seeded_app, partner):
    _session(client, seeded_app)
    r = client.post(f'/api/admin/partners/{partner}/read-grants',
                    json={'org_guids': [seeded_app['org_guid']], 'cdrs': ['cdr2'], 'hours': 1,
                          'purpose_code': 'statistics'})
    assert r.status_code == 201, r.get_data(as_text=True)
    assert r.get_json()['key'].startswith('rg.')


def _project(app):
    from src.db import get_session
    from src.models.research_project import ResearchProject
    with app.app_context():
        s = get_session(); p = ResearchProject(name='Diabetesstudien'); s.add(p); s.commit()
        g = p.guid; s.close()
    return g


def test_purpose_and_projects_travel_to_the_reader(client, seeded_app, partner, app):
    proj = _project(app)
    r = _issue(client, seeded_app, partner, cdrs=['ips', 'cdr2'], purpose_code='research',
               research_project_guids=[proj])
    assert r.status_code == 201, r.get_data(as_text=True)
    key = r.get_json()['key']
    v = _validate(client, key, cdr='ips').get_json()
    assert v['purpose_code'] == 'research' and v['research_project_guids'] == [proj]
    assert v['cdrs'] == ['cdr2', 'ips']


def test_non_research_purpose_drops_projects(client, seeded_app, partner, app):
    proj = _project(app)
    d = _issue(client, seeded_app, partner, research_project_guids=[proj]).get_json()
    assert d['purpose_code'] == 'statistics' and d['research_project_guids'] == []


def test_admin_page_offers_ips_and_the_purposes(client, seeded_app, app):
    proj = _project(app)
    _session(client, seeded_app)
    html = client.get('/admin').get_data(as_text=True)
    assert "const RG_CDRS = ['cdr1', 'cdr2', 'cdr3', 'ips'];" in html
    assert 'rg-purpose-code' in html and proj in html
