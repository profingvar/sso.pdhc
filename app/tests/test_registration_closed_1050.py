"""#1050 — a patient joins ONLY by invitation from their care unit.

Open self-registration is closed unless the operator sets
PATIENT_SELF_REGISTRATION=true. Both doors (HTML and JSON) refuse, create
nothing and never call eps; the links to the form are not shown.
"""
import uuid

import pytest

from src.app import create_app
from src.config import Config
from src.db import get_session
from src.middleware.rate_limit import reset_rate_limits
from src.models.organisation import Organisation
from src.models.user import User
from src.services import eps_client

SECRET = 'test-secret-key-not-for-production'


def _app(flag):
    reset_rate_limits()
    cfg = {'TESTING': True, 'SECRET_KEY': SECRET, 'WTF_CSRF_ENABLED': False,
           'EPS_BASE_URL': 'http://eps.test', 'EPS_API_KEY': 'eps-test-key'}
    if flag is not None:
        cfg['PATIENT_SELF_REGISTRATION'] = flag
    return create_app(cfg)


@pytest.fixture
def eps_calls(monkeypatch):
    calls = []
    monkeypatch.setattr(eps_client, '_http_post', lambda *a, **k: calls.append(a) or None)
    return calls


def _form(app):
    with app.app_context():
        s = get_session(); o = Organisation(name='Unit'); s.add(o); s.commit(); g = o.guid; s.close()
    return {'given_name': 'Test', 'family_name': 'Testsson', 'personnummer': '199001011234',
            'email': f'{uuid.uuid4().hex[:8]}@test.com', 'password': 'password123',
            'organisation_guid': g}


def _users(app):
    with app.app_context():
        s = get_session(); n = s.query(User).count(); s.close(); return n


def test_the_production_default_is_closed(monkeypatch):
    monkeypatch.delenv('PATIENT_SELF_REGISTRATION', raising=False)
    import importlib, src.config as c
    importlib.reload(c)
    assert c.Config.PATIENT_SELF_REGISTRATION == 'false'
    importlib.reload(c)


@pytest.mark.parametrize('flag', [None, '', 'false', '0', 'no'])
def test_both_doors_are_closed_and_create_nothing(flag, eps_calls, monkeypatch):
    monkeypatch.delenv('PATIENT_SELF_REGISTRATION', raising=False)
    app = _app(flag)
    if flag is None:
        app.config.pop('PATIENT_SELF_REGISTRATION', None)
    c = app.test_client(); before = _users(app); data = _form(app)
    page = c.get('/register-patient')
    assert page.status_code == 200 and 'Du behöver en inbjudan' in page.get_data(as_text=True)
    assert 'name="personnummer"' not in page.get_data(as_text=True)
    html = c.post('/register-patient', data=data)
    assert 'Du behöver en inbjudan' in html.get_data(as_text=True)
    api = c.post('/api/patient/register', json=data)
    assert api.status_code == 403 and api.get_json()['error'] == 'registration_closed'
    assert _users(app) == before
    assert eps_calls == []


def test_the_register_links_are_hidden_while_closed():
    app = _app('false'); c = app.test_client()
    for path in ('/', '/login'):
        assert 'href="/register-patient"' not in c.get(path).get_data(as_text=True), path


def test_control_open_door_still_offers_the_form_and_links():
    app = _app('true'); c = app.test_client()
    assert 'name="personnummer"' in c.get('/register-patient').get_data(as_text=True)
    assert 'href="/register-patient"' in c.get('/login').get_data(as_text=True)
