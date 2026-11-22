"""Phase 5 tests — Patient API: register, registry-status."""
import uuid

import pytest

from src.app import create_app
from src.db import get_session
from src.services.auth_service import hash_password
from src.services.jwt_service import issue_token
from src.models.user import User
from src.models.patient import Patient
from src.models.organisation import Organisation
from tests.eps_register_fake import EPS_CFG, eps_register  # noqa: F401


SECRET = 'test-secret-key-not-for-production'


@pytest.fixture
def app():
    from src.middleware.rate_limit import reset_rate_limits
    reset_rate_limits()
    app = create_app({
        # #1050: production default is CLOSED; these suites test the open door.
        'PATIENT_SELF_REGISTRATION': 'true',
        'TESTING': True,
        'SECRET_KEY': SECRET,
        'WTF_CSRF_ENABLED': False,
        'SESSION_EXPIRY_HOURS': 1,
        'ALLOWED_ORIGINS': [],
        'ALLOWED_CALLBACK_URLS': [],
        'SERVICE_CREDENTIALS': {},
    })
    yield app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def seed_data(app):
    """Seed: one organisation, one existing patient, one professional."""
    with app.app_context():
        session = get_session()

        org = Organisation(guid=str(uuid.uuid4()), name='Test Hospital')
        session.add(org)
        session.flush()

        # Existing patient user
        pat_user = User(
            guid=str(uuid.uuid4()),
            email='existing@test.com',
            password_hash=hash_password('patpass12'),
            user_type='patient',
            is_su_admin=False,
        )
        session.add(pat_user)
        session.flush()

        patient = Patient(
            guid=str(uuid.uuid4()), user_id=pat_user.id,
            personnummer='199001011234', organisation_guid=org.guid,
            in_registry=True, registries=['INCA', 'SRQ'],
        )
        session.add(patient)

        # Professional user (should not access patient endpoints)
        from src.models.professional import Professional
        pro_user = User(
            guid=str(uuid.uuid4()),
            email='pro@test.com',
            password_hash=hash_password('propass12'),
            user_type='professional',
            is_su_admin=False,
        )
        session.add(pro_user)
        session.flush()
        pro = Professional(
            guid=str(uuid.uuid4()), user_id=pro_user.id,
            professional_role='doctor', first_name='Pro', last_name='User',
        )
        session.add(pro)

        session.commit()

        result = {
            'org_guid': org.guid,
            'pat_guid': pat_user.guid,
            'pat_email': pat_user.email,
            'patient_guid': patient.guid,
            'personnummer': patient.personnummer,
            'pro_guid': pro_user.guid,
            'pro_email': pro_user.email,
        }
        session.close()
        return result


def _auth_header(token):
    return {'Authorization': f'Bearer {token}'}


def _login(client, email, password):
    resp = client.post('/api/auth/login', json={'email': email, 'password': password})
    return resp


# --- Register ---

UNIFORM = {'status': 'received',
           'message': 'Om uppgifterna kunde registreras kan du logga in nu. '
                      'Har du redan ett konto loggar du in med det.'}


def _reg(client, **fields):
    body = {'given_name': 'Test', 'family_name': 'Testsson', 'password': 'newpass12'}
    body.update(fields)
    return client.post('/api/patient/register', json=body)


def _n_patients(app):
    with app.app_context():
        s = get_session()
        n = s.query(Patient).count()
        s.close()
        return n


class TestPatientRegister:
    """#1049: the JSON door answers ONE uniform 202 for every non-error
    outcome; it no longer echoes the patient or says "conflict"."""

    @pytest.fixture(autouse=True)
    def _eps(self, app, eps_register):
        app.config.update(EPS_CFG)

    def test_register_success(self, client, app, seed_data):
        resp = _reg(client, email='newpatient@test.com', personnummer='200101011234',
                    organisation_guid=seed_data['org_guid'])
        assert resp.status_code == 202
        assert resp.get_json() == UNIFORM
        with app.app_context():
            s = get_session()
            u = s.query(User).filter_by(email='newpatient@test.com').one()
            p = s.query(Patient).filter_by(user_id=u.id).one()
            assert (u.user_type, p.organisation_guid, p.in_registry) == \
                ('patient', seed_data['org_guid'], False)
            s.close()

    def test_register_invalid_personnummer_too_short(self, client, seed_data):
        resp = _reg(client, email='new2@test.com', personnummer='19900101',
                    organisation_guid=seed_data['org_guid'])
        assert resp.status_code == 400
        data = resp.get_json()
        assert 'personnummer' in str(data['messages'])

    def test_register_invalid_personnummer_non_digits(self, client, seed_data):
        resp = _reg(client, email='new3@test.com', personnummer='19900101ABCD',
                    organisation_guid=seed_data['org_guid'])
        assert resp.status_code == 400

    def test_register_duplicate_email(self, client, app, seed_data):
        resp = _reg(client, email='existing@test.com', personnummer='200201011234',
                    organisation_guid=seed_data['org_guid'])
        assert resp.status_code == 202 and resp.get_json() == UNIFORM
        assert _n_patients(app) == 1

    def test_register_duplicate_personnummer(self, client, app, seed_data):
        resp = _reg(client, email='unique@test.com', personnummer='199001011234',
                    organisation_guid=seed_data['org_guid'])
        assert resp.status_code == 202 and resp.get_json() == UNIFORM
        assert _n_patients(app) == 1

    def test_register_nonexistent_organisation(self, client, seed_data):
        resp = _reg(client, email='new4@test.com', personnummer='200301011234',
                    organisation_guid=str(uuid.uuid4()))
        assert resp.status_code == 404

    def test_register_password_too_short(self, client, seed_data):
        resp = _reg(client, email='new5@test.com', password='short',
                    personnummer='200401011234', organisation_guid=seed_data['org_guid'])
        assert resp.status_code == 400

    def test_register_missing_fields(self, client, seed_data):
        resp = client.post('/api/patient/register', json={
            'email': 'new6@test.com',
        })
        assert resp.status_code == 400
        data = resp.get_json()
        assert len(data['messages']) >= 2  # password, personnummer, org

    def test_register_can_login_after(self, client, seed_data):
        """After registration, new patient can log in."""
        _reg(client, email='logintest@test.com', password='loginpass1',
             personnummer='200501011234', organisation_guid=seed_data['org_guid'])
        resp = _login(client, 'logintest@test.com', 'loginpass1')
        assert resp.status_code == 200
        assert 'token' in resp.get_json()


# --- Registry Status ---

class TestRegistryStatus:
    def test_registry_status_returns_data(self, client, seed_data):
        login_resp = _login(client, 'existing@test.com', 'patpass12')
        token = login_resp.get_json()['token']

        resp = client.get('/api/patient/registry-status', headers=_auth_header(token))
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['resourceType'] == 'Patient'
        assert data['patient_guid'] == seed_data['patient_guid']
        assert data['in_registry'] is True
        assert 'INCA' in data['registries']
        assert 'SRQ' in data['registries']
        assert data['personnummer'] == seed_data['personnummer']

    def test_registry_status_professional_forbidden(self, client, seed_data):
        """Professional cannot access patient endpoint."""
        login_resp = _login(client, 'pro@test.com', 'propass12')
        token = login_resp.get_json()['token']

        resp = client.get('/api/patient/registry-status', headers=_auth_header(token))
        assert resp.status_code == 403

    def test_registry_status_no_auth(self, client, seed_data):
        resp = client.get('/api/patient/registry-status')
        assert resp.status_code == 401

    def test_registry_status_fhir_shape(self, client, seed_data):
        """Response has FHIR-compliant resourceType field."""
        login_resp = _login(client, 'existing@test.com', 'patpass12')
        token = login_resp.get_json()['token']

        resp = client.get('/api/patient/registry-status', headers=_auth_header(token))
        data = resp.get_json()
        assert data['resourceType'] == 'Patient'
        assert 'organisation_guid' in data
