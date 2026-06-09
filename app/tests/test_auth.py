"""Phase 4 tests — authentication API: login, me, me/service, logout, change-password."""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from src.app import create_app
from src.db import get_session
from src.services.auth_service import hash_password
from src.services.jwt_service import issue_token, decode_token
from src.models.user import User
from src.models.patient import Patient
from src.models.professional import Professional
from src.models.organisation import Organisation
from src.models.user_organisation import UserOrganisation
from src.models.group import Group
from src.models.membership import Membership
from src.models.user_phase import UserPhase


SECRET = 'test-secret-key-not-for-production'


@pytest.fixture
def app():
    from src.middleware.rate_limit import reset_rate_limits
    reset_rate_limits()
    app = create_app({
        'TESTING': True,
        'SECRET_KEY': SECRET,
        'WTF_CSRF_ENABLED': False,
        'SESSION_EXPIRY_HOURS': 1,
        'ALLOWED_ORIGINS': ['http://localhost:9000'],
        'ALLOWED_CALLBACK_URLS': ['http://localhost:9000/callback'],
        'SERVICE_CREDENTIALS': {'test-client': 'test-secret'},
    })
    yield app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def seed_data(app):
    """Seed test users: SU admin professional, regular professional, patient."""
    with app.app_context():
        session = get_session()

        # Organisation
        org = Organisation(guid=str(uuid.uuid4()), name='Test Hospital')
        session.add(org)
        session.flush()

        # SU admin (professional)
        su_user = User(
            guid=str(uuid.uuid4()),
            email='admin@test.com',
            password_hash=hash_password('adminpass1'),
            user_type='professional',
            is_su_admin=True,
        )
        session.add(su_user)
        session.flush()
        su_prof = Professional(
            guid=str(uuid.uuid4()), user_id=su_user.id,
            professional_role='doctor', first_name='Admin', last_name='User',
        )
        session.add(su_prof)

        # Regular professional
        pro_user = User(
            guid=str(uuid.uuid4()),
            email='pro@test.com',
            password_hash=hash_password('propass12'),
            user_type='professional',
            is_su_admin=False,
        )
        session.add(pro_user)
        session.flush()
        pro_prof = Professional(
            guid=str(uuid.uuid4()), user_id=pro_user.id,
            professional_role='nurse', first_name='Pro', last_name='User',
        )
        session.add(pro_prof)

        # Link professional to org
        uo = UserOrganisation(user_guid=pro_user.guid, organisation_guid=org.guid)
        session.add(uo)

        # Group + membership. After #57 this no longer implies any phase
        # access — groups are purely organisational/category metadata.
        group = Group(guid=str(uuid.uuid4()), name='Planning A', category='planning')
        session.add(group)
        session.flush()
        mem = Membership(
            guid=str(uuid.uuid4()), user_guid=pro_user.guid,
            group_guid=group.guid, status='approved', is_admin=False,
        )
        session.add(mem)

        # #57: phase access comes only from UserPhase. Grant the pro the
        # `planning` phase directly so the professional-flow assertions
        # keep their meaning.
        session.add(UserPhase(user_guid=pro_user.guid, phase='planning'))

        # Patient
        pat_user = User(
            guid=str(uuid.uuid4()),
            email='patient@test.com',
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

        session.commit()

        data = {
            'su_user': su_user,
            'pro_user': pro_user,
            'pat_user': pat_user,
            'org': org,
            'group': group,
        }
        # Detach from session to avoid lazy load issues
        result = {
            'su_guid': su_user.guid,
            'su_email': su_user.email,
            'pro_guid': pro_user.guid,
            'pro_email': pro_user.email,
            'pat_guid': pat_user.guid,
            'pat_email': pat_user.email,
            'org_guid': org.guid,
            'group_guid': group.guid,
        }
        session.close()
        return result


def _login(client, email, password):
    """Helper: login and return token."""
    resp = client.post('/api/auth/login', json={'email': email, 'password': password})
    return resp


def _auth_header(token):
    return {'Authorization': f'Bearer {token}'}


# --- Login ---

class TestLogin:
    def test_login_success(self, client, seed_data):
        resp = _login(client, 'pro@test.com', 'propass12')
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'token' in data
        assert data['user_guid'] == seed_data['pro_guid']

    def test_login_wrong_password(self, client, seed_data):
        resp = _login(client, 'pro@test.com', 'wrongpass')
        assert resp.status_code == 401

    def test_login_nonexistent_email(self, client, seed_data):
        resp = _login(client, 'nobody@test.com', 'anything')
        assert resp.status_code == 401

    def test_login_missing_fields(self, client, seed_data):
        resp = client.post('/api/auth/login', json={'email': 'pro@test.com'})
        assert resp.status_code == 400

    def test_login_sso_handshake_redirect(self, client, seed_data):
        resp = client.post('/api/auth/login', json={
            'email': 'pro@test.com',
            'password': 'propass12',
            'next': 'http://localhost:9000/callback',
            'state': 'abc123',
        })
        assert resp.status_code == 302
        location = resp.headers['Location']
        assert 'token=' in location
        assert 'state=abc123' in location

    def test_login_sso_reject_unallowed_callback(self, client, seed_data):
        resp = client.post('/api/auth/login', json={
            'email': 'pro@test.com',
            'password': 'propass12',
            'next': 'http://evil.com/callback',
        })
        assert resp.status_code == 400
        data = resp.get_json()
        assert data['error'] == 'invalid_redirect'

    def test_login_sso_handshake_fail_redirect(self, client, seed_data):
        resp = client.post('/api/auth/login', json={
            'email': 'pro@test.com',
            'password': 'wrongpass',
            'next': 'http://localhost:9000/callback',
        })
        assert resp.status_code == 302
        location = resp.headers['Location']
        assert 'error=authentication_failed' in location


# --- /me ---

class TestMe:
    def test_me_professional(self, client, seed_data):
        login_resp = _login(client, 'pro@test.com', 'propass12')
        token = login_resp.get_json()['token']

        resp = client.get('/api/auth/me', headers=_auth_header(token))
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['user_guid'] == seed_data['pro_guid']
        assert data['user_type'] == 'professional'
        assert data['is_su_admin'] is False
        assert 'professional_guid' in data
        assert data['professional_role'] == 'nurse'
        assert seed_data['org_guid'] in data['organization_ids']
        assert len(data['groups']) == 1
        assert data['groups'][0]['category'] == 'planning'
        # #57: effective_phases comes only from UserPhase, not groups.
        # The seed adds a direct `planning` grant, hence the assertion.
        assert 'planning' in data['effective_phases']

    def test_me_effective_phases_ignores_group_category(self, client, seed_data, app):
        """#57: a user who is only in a planning-category group but has
        NO UserPhase row must have an empty `effective_phases`. (Field
        renamed from `group_type` → `category` in #60.)
        """
        # Make a brand new professional with a group membership but no UserPhase.
        with app.app_context():
            session = get_session()
            other = User(
                guid=str(uuid.uuid4()),
                email='phaseless@test.com',
                password_hash=hash_password('phaselesspw1'),
                user_type='professional',
                is_su_admin=False,
            )
            session.add(other)
            session.flush()
            session.add(Professional(
                guid=str(uuid.uuid4()), user_id=other.id,
                professional_role='nurse', first_name='Phase', last_name='Less',
            ))
            # Reuse the seeded planning-typed group.
            session.add(Membership(
                guid=str(uuid.uuid4()), user_guid=other.guid,
                group_guid=seed_data['group_guid'],
                status='approved', is_admin=False,
            ))
            session.commit()
            other_guid = other.guid
            session.close()

        login_resp = _login(client, 'phaseless@test.com', 'phaselesspw1')
        token = login_resp.get_json()['token']
        resp = client.get('/api/auth/me', headers=_auth_header(token))
        data = resp.get_json()
        assert data['user_guid'] == other_guid
        assert len(data['groups']) == 1
        assert data['groups'][0]['category'] == 'planning'
        # Phase access is orthogonal to group membership (#57).
        assert data['effective_phases'] == []

    def test_me_patient(self, client, seed_data):
        login_resp = _login(client, 'patient@test.com', 'patpass12')
        token = login_resp.get_json()['token']

        resp = client.get('/api/auth/me', headers=_auth_header(token))
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['user_type'] == 'patient'
        assert 'patient_guid' in data
        assert data['in_registry'] is True
        assert 'INCA' in data['registries']
        assert data['fhir_resource_type'] == 'Patient'

    def test_me_su_admin(self, client, seed_data):
        login_resp = _login(client, 'admin@test.com', 'adminpass1')
        token = login_resp.get_json()['token']

        resp = client.get('/api/auth/me', headers=_auth_header(token))
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['is_su_admin'] is True

    def test_me_no_token(self, client, seed_data):
        resp = client.get('/api/auth/me')
        assert resp.status_code == 401


# --- Caregiver roll-up in access blob (#188) ---

class TestOrganizationCaregivers:
    """Ticket #188. Every professional access blob carries an
    ``organization_caregivers`` map: each organisation guid the user
    belongs to maps onto its caregiver guid (the organisation's
    ``parent_caregiver_guid`` if set, else the org's own guid)."""

    def test_self_rollup_when_parent_caregiver_null(
        self, client, seed_data,
    ):
        login_resp = _login(client, 'pro@test.com', 'propass12')
        token = login_resp.get_json()['token']

        resp = client.get('/api/auth/me', headers=_auth_header(token))
        data = resp.get_json()
        assert 'organization_caregivers' in data
        org_guid = seed_data['org_guid']
        # Seeded Test Hospital has no parent_caregiver_guid → it IS the
        # caregiver, so it maps to itself.
        assert data['organization_caregivers'][org_guid] == org_guid

    def test_map_resolves_to_parent_caregiver(
        self, client, seed_data, app,
    ):
        """A clinic with parent_caregiver_guid → its caregiver guid."""
        # Wire a fresh caregiver + clinic, and put the regular pro in the
        # clinic so we can observe the roll-up.
        with app.app_context():
            session = get_session()
            caregiver = Organisation(
                guid=str(uuid.uuid4()), name='Region North',
            )
            session.add(caregiver)
            session.flush()
            clinic = Organisation(
                guid=str(uuid.uuid4()), name='Hospital North',
                parent_caregiver_guid=caregiver.guid,
            )
            session.add(clinic)
            session.flush()
            session.add(UserOrganisation(
                user_guid=seed_data['pro_guid'],
                organisation_guid=clinic.guid,
            ))
            session.commit()
            caregiver_guid = caregiver.guid
            clinic_guid = clinic.guid
            session.close()

        login_resp = _login(client, 'pro@test.com', 'propass12')
        token = login_resp.get_json()['token']
        resp = client.get('/api/auth/me', headers=_auth_header(token))
        data = resp.get_json()
        assert clinic_guid in data['organization_caregivers']
        assert data['organization_caregivers'][clinic_guid] == caregiver_guid
        # And every organization_ids entry has a matching caregivers entry.
        assert set(data['organization_caregivers'].keys()) == \
            set(data['organization_ids'])

    def test_patient_blob_does_not_include_caregivers(
        self, client, seed_data,
    ):
        """Patients have no organization_ids; the caregivers map is a
        professional-side concept only."""
        login_resp = _login(client, 'patient@test.com', 'patpass12')
        token = login_resp.get_json()['token']
        resp = client.get('/api/auth/me', headers=_auth_header(token))
        data = resp.get_json()
        assert 'organization_caregivers' not in data

    def test_existing_blob_fields_unchanged(self, client, seed_data):
        """Backwards compatibility: organization_ids stays as-is."""
        login_resp = _login(client, 'pro@test.com', 'propass12')
        token = login_resp.get_json()['token']
        resp = client.get('/api/auth/me', headers=_auth_header(token))
        data = resp.get_json()
        assert seed_data['org_guid'] in data['organization_ids']
        assert data['organisation_warning'] is False


# --- Session id (#191) ---

class TestSessionId:
    """The ``sid`` JWT claim plus its ``session_id`` projection in the
    access blob — Lag (2022:913) chain-of-custody correlation."""

    def test_issue_token_embeds_sid_claim(self):
        """``issue_token`` puts a ``sid`` UUID in the payload."""
        token = issue_token('user-guid-abc', SECRET, expiry_hours=1)
        payload = decode_token(token, SECRET)
        assert 'sid' in payload
        # uuid4 → 36-char canonical
        assert isinstance(payload['sid'], str)
        assert len(payload['sid']) == 36

    def test_two_issued_tokens_have_different_sids(self):
        """Independent logins must NOT share a session id."""
        t1 = issue_token('user-guid', SECRET, expiry_hours=1)
        t2 = issue_token('user-guid', SECRET, expiry_hours=1)
        assert decode_token(t1, SECRET)['sid'] != decode_token(t2, SECRET)['sid']

    def test_me_returns_session_id(self, client, seed_data):
        login_resp = _login(client, 'pro@test.com', 'propass12')
        token = login_resp.get_json()['token']
        resp = client.get('/api/auth/me', headers=_auth_header(token))
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'session_id' in data
        assert data['session_id']  # non-empty string

    def test_consecutive_me_calls_same_token_same_session_id(self, client, seed_data):
        """Two /me calls with the same Bearer → same session_id."""
        login_resp = _login(client, 'pro@test.com', 'propass12')
        token = login_resp.get_json()['token']
        first = client.get('/api/auth/me', headers=_auth_header(token)).get_json()
        second = client.get('/api/auth/me', headers=_auth_header(token)).get_json()
        assert first['session_id'] == second['session_id']

    def test_relogin_issues_fresh_session_id(self, client, seed_data):
        """A new login (new token) yields a fresh session_id."""
        first_login = _login(client, 'pro@test.com', 'propass12').get_json()
        t1 = first_login['token']
        sid_1 = client.get('/api/auth/me', headers=_auth_header(t1)).get_json()['session_id']

        # Logout first, then relogin so we don't keep two parallel sessions.
        client.post('/api/auth/logout', headers=_auth_header(t1))

        second_login = _login(client, 'pro@test.com', 'propass12').get_json()
        t2 = second_login['token']
        sid_2 = client.get('/api/auth/me', headers=_auth_header(t2)).get_json()['session_id']
        assert sid_1
        assert sid_2
        assert sid_1 != sid_2

    def test_me_service_returns_same_session_id_as_me(self, client, seed_data):
        """Switching from /me to /me/service with the same token must
        not produce a new session_id — the token is what carries it."""
        login_resp = _login(client, 'pro@test.com', 'propass12')
        token = login_resp.get_json()['token']
        me_sid = client.get(
            '/api/auth/me', headers=_auth_header(token)
        ).get_json()['session_id']
        svc_sid = client.get('/api/auth/me/service', headers={
            **_auth_header(token),
            'X-SSO-Client-Id': 'test-client',
            'X-SSO-Client-Secret': 'test-secret',
        }).get_json()['session_id']
        assert me_sid == svc_sid

    def test_session_id_is_present_for_su_admin(self, client, seed_data):
        login_resp = _login(client, 'admin@test.com', 'adminpass1')
        token = login_resp.get_json()['token']
        data = client.get('/api/auth/me', headers=_auth_header(token)).get_json()
        assert data['session_id']


# --- /me/service ---

class TestMeService:
    def test_me_service_valid_credentials(self, client, seed_data):
        login_resp = _login(client, 'pro@test.com', 'propass12')
        token = login_resp.get_json()['token']

        resp = client.get('/api/auth/me/service', headers={
            **_auth_header(token),
            'X-SSO-Client-Id': 'test-client',
            'X-SSO-Client-Secret': 'test-secret',
        })
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['user_guid'] == seed_data['pro_guid']

    def test_me_service_invalid_credentials(self, client, seed_data):
        login_resp = _login(client, 'pro@test.com', 'propass12')
        token = login_resp.get_json()['token']

        resp = client.get('/api/auth/me/service', headers={
            **_auth_header(token),
            'X-SSO-Client-Id': 'bad-client',
            'X-SSO-Client-Secret': 'bad-secret',
        })
        assert resp.status_code == 403

    def test_me_service_missing_credentials(self, client, seed_data):
        login_resp = _login(client, 'pro@test.com', 'propass12')
        token = login_resp.get_json()['token']

        resp = client.get('/api/auth/me/service', headers=_auth_header(token))
        assert resp.status_code == 403


# --- Logout ---

class TestLogout:
    def test_logout_revokes_token(self, client, seed_data):
        login_resp = _login(client, 'pro@test.com', 'propass12')
        token = login_resp.get_json()['token']

        # Logout
        resp = client.post('/api/auth/logout', headers=_auth_header(token))
        assert resp.status_code == 200

        # Token should now be revoked — /me should fail
        resp = client.get('/api/auth/me', headers=_auth_header(token))
        assert resp.status_code == 401

    def test_logout_without_auth(self, client, seed_data):
        resp = client.post('/api/auth/logout')
        assert resp.status_code == 401


# --- Change Password ---

class TestChangePassword:
    def test_change_password_success(self, client, seed_data):
        login_resp = _login(client, 'pro@test.com', 'propass12')
        token = login_resp.get_json()['token']

        resp = client.post('/api/auth/change-password', headers=_auth_header(token), json={
            'current_password': 'propass12',
            'new_password': 'newpass123',
        })
        assert resp.status_code == 200

        # Can login with new password
        resp2 = _login(client, 'pro@test.com', 'newpass123')
        assert resp2.status_code == 200

    def test_change_password_wrong_current(self, client, seed_data):
        login_resp = _login(client, 'admin@test.com', 'adminpass1')
        token = login_resp.get_json()['token']

        resp = client.post('/api/auth/change-password', headers=_auth_header(token), json={
            'current_password': 'wrongpass',
            'new_password': 'newpass123',
        })
        assert resp.status_code == 401

    def test_change_password_too_short(self, client, seed_data):
        login_resp = _login(client, 'admin@test.com', 'adminpass1')
        token = login_resp.get_json()['token']

        resp = client.post('/api/auth/change-password', headers=_auth_header(token), json={
            'current_password': 'adminpass1',
            'new_password': 'short',
        })
        assert resp.status_code == 400

    def test_change_password_missing_fields(self, client, seed_data):
        login_resp = _login(client, 'admin@test.com', 'adminpass1')
        token = login_resp.get_json()['token']

        resp = client.post('/api/auth/change-password', headers=_auth_header(token), json={
            'current_password': 'adminpass1',
        })
        assert resp.status_code == 400
