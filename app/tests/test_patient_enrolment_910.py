"""#910 — one enrolment for both routes, offering only care units.

Two copies of patient self-enrolment had drifted (rate limit, audit, registry
fields), and the anonymous GET handed out every organisation's guid. Operator
2026-11-16: offer only where a patient can belong — care units, plus a
vårdgivare with no units (its own unit). External partners and a vårdgivare
that has units are neither shown nor accepted.
"""
import ast
import inspect
import textwrap
import uuid

import pytest

from src.db import get_session
from src.models.organisation import Organisation
from src.models.patient import Patient
from src.models.user import User
from src.routes import frontend, patient as patient_routes
from tests.test_frontend import app as _base_app, client  # noqa: F401
from tests.eps_register_fake import EPS_CFG, eps_register  # noqa: F401

PNR_INVALID_CHECKSUM = '199001011234'   # synthetic on purpose (#405)


@pytest.fixture
def app(_base_app, eps_register):
    """#1049: registration asks eps to create the patient; the fake eps
    answers 201, so a new registrant is created and linked."""
    _base_app.config.update(EPS_CFG)
    return _base_app


@pytest.fixture
def orgs(app):
    with app.app_context():
        s = get_session()
        a = Organisation(name='Caregiver With Units'); k = Organisation(name='Lone Caregiver')
        x = Organisation(name='External Partner', is_external=True)
        s.add_all([a, k, x]); s.flush()
        a1 = Organisation(name='Unit A1', parent_caregiver_guid=a.guid); s.add(a1); s.flush()
        out = {'a': a.guid, 'k': k.guid, 'x': x.guid, 'a1': a1.guid,
               'names': {'a': a.name, 'k': k.name, 'x': x.name, 'a1': a1.name}}
        s.commit(); s.close()
    return out


@pytest.fixture
def audited(monkeypatch):
    events = []
    for mod in ('src.services.patient_enrolment', 'src.services.patient_link'):
        monkeypatch.setattr(mod + '.audit',
                            lambda event, **kw: events.append((event, kw)))
    return events


def _form(org, email=None, pnr=PNR_INVALID_CHECKSUM, password='password123'):
    return {'given_name': 'Test', 'family_name': 'Testsson',
            'email': email or f'{uuid.uuid4().hex[:8]}@test.com', 'password': password,
            'personnummer': pnr, 'organisation_guid': org}


def _api(client, data):
    return client.post('/api/patient/register', json=data)


def _html(client, data):
    return client.post('/register-patient', data=data)


def _patients(app):
    with app.app_context():
        s = get_session()
        out = [(p.organisation_guid, p.in_registry, p.registries) for p in s.query(Patient).all()]
        s.close()
        return out


# --------------------------------------------------------------- the list

def test_the_form_offers_units_and_a_unitless_caregiver_ONLY(client, orgs):
    html = client.get('/register-patient').get_data(as_text=True)
    for k in ('a1', 'k'):
        assert orgs[k] in html and orgs['names'][k] in html
    for k in ('a', 'x'):
        assert orgs[k] not in html, f"{orgs['names'][k]}'s guid is published"
        assert orgs['names'][k] not in html


# --------------------------------------------------------------- acceptance, both paths

@pytest.mark.parametrize('post', [_api, _html], ids=['api', 'html'])
@pytest.mark.parametrize('target', ['a1', 'k'])
def test_a_care_unit_is_accepted(client, app, orgs, audited, post, target):
    r = post(client, _form(orgs[target]))
    assert r.status_code in (202, 200)
    assert _patients(app) == [(orgs[target], False, [])], "same registry fields on both paths"
    assert [e for e, _ in audited] == ['patient_register', 'patient_link',
                                       'patient_self_linked'], "both paths audit"


@pytest.mark.parametrize('post', [_api, _html], ids=['api', 'html'])
@pytest.mark.parametrize('target', ['a', 'x', 'unknown'])
def test_anything_else_is_refused_with_the_SAME_not_found(client, app, orgs, audited, post, target):
    guid = orgs.get(target) or str(uuid.uuid4())
    r = post(client, _form(guid))
    if post is _api:
        assert r.status_code == 404 and r.get_json() == {'error': 'not_found', 'message': 'Vårdenheten finns inte.'}
    else:
        assert r.status_code == 200 and 'Vårdenheten finns inte.' in r.get_data(as_text=True)
    assert _patients(app) == [] and audited == []
    with app.app_context():
        assert get_session().query(User).filter_by(user_type='patient').count() == 0


# --------------------------------------------------------------- one validation

@pytest.mark.parametrize('bad', [
    {'email': ''}, {'password': 'short'}, {'personnummer': '19900101123'},
    {'personnummer': '19900101123a'}, {'organisation_guid': ''},
])
def test_both_paths_refuse_the_same_input(client, app, orgs, bad):
    for post in (_api, _html):
        data = {**_form(orgs['a1']), **bad}
        if bad.get('email') == '':
            data['email'] = ''
        r = post(client, data)
        assert r.status_code in (400, 200)
        assert _patients(app) == [], f"{post.__name__} accepted {bad}"


def test_an_invalid_checksum_is_ACCEPTED_on_both_paths(client, app, orgs):
    assert _api(client, _form(orgs['a1'], pnr='199001011234')).status_code == 202
    assert _html(client, _form(orgs['k'], pnr='200101011234')).status_code == 200
    assert len(_patients(app)) == 2


# --------------------------------------------------------------- no second copy

@pytest.mark.parametrize('fn', [patient_routes.register, frontend.register_patient],
                         ids=['api', 'html'])
def test_neither_route_builds_a_patient_itself(fn):
    tree = ast.parse(textwrap.dedent(inspect.getsource(inspect.unwrap(fn))))
    called = {n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)
                and n.module == 'src.services.patient_enrolment' for a in n.names}
    assert 'register_patient' in imported, "#1049: both doors use the one service"
    assert not ({'User', 'Patient'} & called), "a second enrolment implementation is back"
