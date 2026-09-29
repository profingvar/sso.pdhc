"""Phase 6 tests — Professional & Group API: groups, membership, admin, invites."""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from src.app import create_app
from src.db import get_session
from src.services.auth_service import hash_password
from src.services.jwt_service import issue_token
from src.models.user import User
from src.models.professional import Professional
from src.models.organisation import Organisation
from src.models.user_organisation import UserOrganisation
from src.models.group import Group
from src.models.membership import Membership
from src.models.invite import Invite


SECRET = 'test-secret-key-not-for-production'


@pytest.fixture
def app():
    from src.middleware.rate_limit import reset_rate_limits
    reset_rate_limits()
    application = create_app({
        'TESTING': True,
        'SECRET_KEY': SECRET,
        'WTF_CSRF_ENABLED': False,
        'SESSION_EXPIRY_HOURS': 1,
        'ALLOWED_ORIGINS': [],
        'ALLOWED_CALLBACK_URLS': [],
        'SERVICE_CREDENTIALS': {},
    })
    # Ensure clean state: drop and recreate tables
    with application.app_context():
        from src.db import drop_all_tables, create_all_tables
        drop_all_tables()
        create_all_tables()
    yield application


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def seed_data(app):
    """Seed: org, 2 groups, admin professional (admin of group A), regular professional, patient."""
    with app.app_context():
        session = get_session()

        org = Organisation(guid=str(uuid.uuid4()), name='Test Hospital')
        session.add(org)
        session.flush()

        # Group A (planning) and Group B (request)
        group_a = Group(guid=str(uuid.uuid4()), name='Planning Alpha', category='planning')
        group_b = Group(guid=str(uuid.uuid4()), name='Request Beta', category='request')
        session.add_all([group_a, group_b])
        session.flush()

        # Admin professional — admin of group A, approved
        admin_user = User(
            guid=str(uuid.uuid4()), email='admin@test.com',
            password_hash=hash_password('adminpass1'),
            user_type='professional', is_su_admin=False,
        )
        session.add(admin_user)
        session.flush()
        admin_prof = Professional(
            guid=str(uuid.uuid4()), user_id=admin_user.id,
            professional_role='doctor', first_name='Admin', last_name='Pro',
        )
        session.add(admin_prof)
        admin_mem = Membership(
            guid=str(uuid.uuid4()), user_guid=admin_user.guid,
            group_guid=group_a.guid, status='approved', is_admin=True,
        )
        session.add(admin_mem)

        # Regular professional — no memberships yet
        reg_user = User(
            guid=str(uuid.uuid4()), email='regular@test.com',
            password_hash=hash_password('regpass12'),
            user_type='professional', is_su_admin=False,
        )
        session.add(reg_user)
        session.flush()
        reg_prof = Professional(
            guid=str(uuid.uuid4()), user_id=reg_user.id,
            professional_role='nurse', first_name='Reg', last_name='Pro',
        )
        session.add(reg_prof)

        # SU admin
        su_user = User(
            guid=str(uuid.uuid4()), email='su@test.com',
            password_hash=hash_password('supass123'),
            user_type='professional', is_su_admin=True,
        )
        session.add(su_user)
        session.flush()
        su_prof = Professional(
            guid=str(uuid.uuid4()), user_id=su_user.id,
            professional_role='doctor', first_name='SU', last_name='Admin',
        )
        session.add(su_prof)

        # Patient (should be forbidden from group endpoints)
        from src.models.patient import Patient
        pat_user = User(
            guid=str(uuid.uuid4()), email='patient@test.com',
            password_hash=hash_password('patpass12'),
            user_type='patient', is_su_admin=False,
        )
        session.add(pat_user)
        session.flush()
        patient = Patient(
            guid=str(uuid.uuid4()), user_id=pat_user.id,
            personnummer='199001011234', organisation_guid=org.guid,
        )
        session.add(patient)

        session.commit()

        result = {
            'org_guid': org.guid,
            'group_a_guid': group_a.guid,
            'group_b_guid': group_b.guid,
            'admin_guid': admin_user.guid,
            'admin_email': admin_user.email,
            'reg_guid': reg_user.guid,
            'reg_email': reg_user.email,
            'su_guid': su_user.guid,
            'su_email': su_user.email,
            'pat_email': pat_user.email,
        }
        session.close()
        return result


def _auth_header(token):
    return {'Authorization': f'Bearer {token}'}


def _login(client, email, password):
    return client.post('/api/auth/login', json={'email': email, 'password': password})


def _get_token(client, email, password):
    return _login(client, email, password).get_json()['token']


# --- List Groups ---

class TestListGroups:
    def test_list_groups_returns_approved_only(self, client, seed_data):
        token = _get_token(client, 'admin@test.com', 'adminpass1')
        resp = client.get('/api/groups', headers=_auth_header(token))
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data) == 1
        assert data[0]['group_guid'] == seed_data['group_a_guid']
        assert data[0]['resourceType'] == 'Group'
        assert data[0]['is_admin'] is True

    def test_list_groups_empty_for_new_professional(self, client, seed_data):
        token = _get_token(client, 'regular@test.com', 'regpass12')
        resp = client.get('/api/groups', headers=_auth_header(token))
        assert resp.status_code == 200
        assert resp.get_json() == []

    def test_list_groups_patient_forbidden(self, client, seed_data):
        token = _get_token(client, 'patient@test.com', 'patpass12')
        resp = client.get('/api/groups', headers=_auth_header(token))
        assert resp.status_code == 403


# --- Request Membership ---

class TestRequestMembership:
    def test_request_membership_creates_pending(self, client, seed_data):
        token = _get_token(client, 'regular@test.com', 'regpass12')
        resp = client.post('/api/groups/request-membership',
                           headers=_auth_header(token),
                           json={'group_guid': seed_data['group_a_guid']})
        assert resp.status_code == 201
        data = resp.get_json()
        assert data['status'] == 'pending'
        assert data['group_guid'] == seed_data['group_a_guid']
        assert 'membership_guid' in data

    def test_request_membership_duplicate_rejected(self, client, seed_data):
        token = _get_token(client, 'regular@test.com', 'regpass12')
        client.post('/api/groups/request-membership',
                    headers=_auth_header(token),
                    json={'group_guid': seed_data['group_a_guid']})
        resp2 = client.post('/api/groups/request-membership',
                            headers=_auth_header(token),
                            json={'group_guid': seed_data['group_a_guid']})
        assert resp2.status_code == 409

    def test_request_membership_nonexistent_group(self, client, seed_data):
        token = _get_token(client, 'regular@test.com', 'regpass12')
        resp = client.post('/api/groups/request-membership',
                           headers=_auth_header(token),
                           json={'group_guid': str(uuid.uuid4())})
        assert resp.status_code == 404

    def test_request_membership_missing_group(self, client, seed_data):
        token = _get_token(client, 'regular@test.com', 'regpass12')
        resp = client.post('/api/groups/request-membership',
                           headers=_auth_header(token), json={})
        assert resp.status_code == 400


# --- Request Admin ---

class TestRequestAdmin:
    def test_request_admin_creates_leader_request(self, client, seed_data):
        token = _get_token(client, 'regular@test.com', 'regpass12')
        resp = client.post('/api/groups/request-admin',
                           headers=_auth_header(token),
                           json={'group_guid': seed_data['group_a_guid']})
        assert resp.status_code == 201
        data = resp.get_json()
        assert data['status'] == 'pending'
        assert 'leader_request_guid' in data

    def test_request_admin_duplicate_pending_rejected(self, client, seed_data):
        token = _get_token(client, 'regular@test.com', 'regpass12')
        client.post('/api/groups/request-admin',
                    headers=_auth_header(token),
                    json={'group_guid': seed_data['group_a_guid']})
        resp2 = client.post('/api/groups/request-admin',
                            headers=_auth_header(token),
                            json={'group_guid': seed_data['group_a_guid']})
        assert resp2.status_code == 409


# --- Admin Pending ---

class TestAdminPending:
    def test_admin_sees_pending_memberships(self, client, seed_data):
        # Regular user requests membership
        reg_token = _get_token(client, 'regular@test.com', 'regpass12')
        client.post('/api/groups/request-membership',
                    headers=_auth_header(reg_token),
                    json={'group_guid': seed_data['group_a_guid']})

        # Admin lists pending
        admin_token = _get_token(client, 'admin@test.com', 'adminpass1')
        resp = client.get('/api/groups/admin/pending', headers=_auth_header(admin_token))
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data) >= 1
        assert data[0]['status'] == 'pending'
        assert data[0]['user_guid'] == seed_data['reg_guid']

    def test_non_admin_cannot_see_pending(self, client, seed_data):
        """Regular professional without admin role cannot access pending."""
        token = _get_token(client, 'regular@test.com', 'regpass12')
        resp = client.get('/api/groups/admin/pending', headers=_auth_header(token))
        assert resp.status_code == 403


# --- Admin Decide ---

class TestAdminDecide:
    def test_approve_membership(self, client, seed_data):
        # Request membership
        reg_token = _get_token(client, 'regular@test.com', 'regpass12')
        req_resp = client.post('/api/groups/request-membership',
                               headers=_auth_header(reg_token),
                               json={'group_guid': seed_data['group_a_guid']})
        mem_guid = req_resp.get_json()['membership_guid']

        # Admin approves
        admin_token = _get_token(client, 'admin@test.com', 'adminpass1')
        resp = client.post('/api/groups/admin/decide',
                           headers=_auth_header(admin_token),
                           json={'membership_guid': mem_guid, 'decision': 'approved'})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['status'] == 'approved'
        assert data['decided_by'] == seed_data['admin_guid']

    def test_reject_membership(self, client, seed_data):
        reg_token = _get_token(client, 'regular@test.com', 'regpass12')
        req_resp = client.post('/api/groups/request-membership',
                               headers=_auth_header(reg_token),
                               json={'group_guid': seed_data['group_a_guid']})
        mem_guid = req_resp.get_json()['membership_guid']

        admin_token = _get_token(client, 'admin@test.com', 'adminpass1')
        resp = client.post('/api/groups/admin/decide',
                           headers=_auth_header(admin_token),
                           json={'membership_guid': mem_guid, 'decision': 'rejected'})
        assert resp.status_code == 200
        assert resp.get_json()['status'] == 'rejected'

    def test_cannot_decide_already_decided(self, client, seed_data):
        reg_token = _get_token(client, 'regular@test.com', 'regpass12')
        req_resp = client.post('/api/groups/request-membership',
                               headers=_auth_header(reg_token),
                               json={'group_guid': seed_data['group_a_guid']})
        mem_guid = req_resp.get_json()['membership_guid']

        admin_token = _get_token(client, 'admin@test.com', 'adminpass1')
        client.post('/api/groups/admin/decide',
                    headers=_auth_header(admin_token),
                    json={'membership_guid': mem_guid, 'decision': 'approved'})
        resp2 = client.post('/api/groups/admin/decide',
                            headers=_auth_header(admin_token),
                            json={'membership_guid': mem_guid, 'decision': 'rejected'})
        assert resp2.status_code == 409

    def test_non_admin_cannot_decide(self, client, seed_data):
        """Non-admin of the group cannot decide memberships."""
        # Admin requests membership in group B (where admin is NOT admin)
        admin_token = _get_token(client, 'admin@test.com', 'adminpass1')

        # Regular requests membership in group B
        reg_token = _get_token(client, 'regular@test.com', 'regpass12')
        req_resp = client.post('/api/groups/request-membership',
                               headers=_auth_header(reg_token),
                               json={'group_guid': seed_data['group_b_guid']})
        mem_guid = req_resp.get_json()['membership_guid']

        # Admin (admin of group A only) tries to decide group B membership
        resp = client.post('/api/groups/admin/decide',
                           headers=_auth_header(admin_token),
                           json={'membership_guid': mem_guid, 'decision': 'approved'})
        assert resp.status_code == 403

    def test_su_can_decide_any_group(self, client, seed_data):
        """SU admin can decide memberships in any group."""
        reg_token = _get_token(client, 'regular@test.com', 'regpass12')
        req_resp = client.post('/api/groups/request-membership',
                               headers=_auth_header(reg_token),
                               json={'group_guid': seed_data['group_b_guid']})
        mem_guid = req_resp.get_json()['membership_guid']

        su_token = _get_token(client, 'su@test.com', 'supass123')
        resp = client.post('/api/groups/admin/decide',
                           headers=_auth_header(su_token),
                           json={'membership_guid': mem_guid, 'decision': 'approved'})
        assert resp.status_code == 200


# --- Admin Invite ---

class TestAdminInvite:
    def test_create_invite(self, client, seed_data):
        admin_token = _get_token(client, 'admin@test.com', 'adminpass1')
        resp = client.post('/api/groups/admin/invite',
                           headers=_auth_header(admin_token),
                           json={'group_guid': seed_data['group_a_guid'], 'hours_valid': 24})
        assert resp.status_code == 201
        data = resp.get_json()
        assert 'token' in data
        assert data['group_guid'] == seed_data['group_a_guid']
        assert 'expires_at' in data

    def test_non_admin_cannot_create_invite(self, client, seed_data):
        reg_token = _get_token(client, 'regular@test.com', 'regpass12')
        resp = client.post('/api/groups/admin/invite',
                           headers=_auth_header(reg_token),
                           json={'group_guid': seed_data['group_a_guid']})
        assert resp.status_code == 403


# --- Join by Invite ---

class TestJoinByInvite:
    def test_join_by_invite_end_to_end(self, client, seed_data):
        """Full flow: admin creates invite → regular redeems → pending membership."""
        admin_token = _get_token(client, 'admin@test.com', 'adminpass1')
        invite_resp = client.post('/api/groups/admin/invite',
                                  headers=_auth_header(admin_token),
                                  json={'group_guid': seed_data['group_a_guid']})
        invite_token = invite_resp.get_json()['token']

        reg_token = _get_token(client, 'regular@test.com', 'regpass12')
        resp = client.post('/api/groups/join-by-invite',
                           headers=_auth_header(reg_token),
                           json={'token': invite_token})
        assert resp.status_code == 201
        data = resp.get_json()
        assert data['status'] == 'pending'
        assert data['group_guid'] == seed_data['group_a_guid']

    def test_join_by_invite_expired(self, app, client, seed_data):
        """Expired invite token returns 410."""
        # Create expired invite directly in DB
        admin_token = _get_token(client, 'admin@test.com', 'adminpass1')
        with app.app_context():
            session = get_session()
            expired_invite = Invite(
                group_guid=seed_data['group_a_guid'],
                token='expired-token-123',
                expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
                created_by_guid=seed_data['admin_guid'],
            )
            session.add(expired_invite)
            session.commit()
            session.close()

        reg_token = _get_token(client, 'regular@test.com', 'regpass12')
        resp = client.post('/api/groups/join-by-invite',
                           headers=_auth_header(reg_token),
                           json={'token': 'expired-token-123'})
        assert resp.status_code == 410

    def test_join_by_invite_invalid_token(self, client, seed_data):
        reg_token = _get_token(client, 'regular@test.com', 'regpass12')
        resp = client.post('/api/groups/join-by-invite',
                           headers=_auth_header(reg_token),
                           json={'token': 'nonexistent-token'})
        assert resp.status_code == 404

    def test_join_by_invite_duplicate_membership(self, client, seed_data):
        """Cannot join group already a member of."""
        admin_token = _get_token(client, 'admin@test.com', 'adminpass1')
        invite_resp = client.post('/api/groups/admin/invite',
                                  headers=_auth_header(admin_token),
                                  json={'group_guid': seed_data['group_a_guid']})
        invite_token = invite_resp.get_json()['token']

        reg_token = _get_token(client, 'regular@test.com', 'regpass12')
        client.post('/api/groups/join-by-invite',
                    headers=_auth_header(reg_token),
                    json={'token': invite_token})

        # Create second invite
        invite_resp2 = client.post('/api/groups/admin/invite',
                                   headers=_auth_header(admin_token),
                                   json={'group_guid': seed_data['group_a_guid']})
        invite_token2 = invite_resp2.get_json()['token']

        resp = client.post('/api/groups/join-by-invite',
                           headers=_auth_header(reg_token),
                           json={'token': invite_token2})
        assert resp.status_code == 409


# --- #709: the org-less professional must keep the path to an org ---

class TestOrgLessProfessionalKeepsTheRoute:
    """Pins the reason `require_organisation` was removed rather than applied.

    The #704 triage found a complete "professional must hold at least one
    organisation membership" decorator applied to no route, and asked whether
    an intended rule was going unenforced. It was not: the only routes it
    would fit are the four that a professional uses to GET a membership, and
    gating those on already having one is a permanent lockout.

    These assertions are the shape of that argument. If someone reintroduces
    the decorator on the group routes, they fail.
    """

    def _orgless_token(self, client, seed_data, app):
        from src.db import get_session
        from src.models.user_organisation import UserOrganisation
        with app.app_context():
            session = get_session()
            assert session.query(UserOrganisation).filter_by(
                user_guid=seed_data['reg_guid']).first() is None, \
                'the seeded regular professional is meant to hold no org'
            session.close()
        return _get_token(client, 'regular@test.com', 'regpass12')

    def test_they_can_see_the_groups_list(self, client, seed_data, app):
        token = self._orgless_token(client, seed_data, app)
        resp = client.get('/api/groups', headers=_auth_header(token))
        assert resp.status_code == 200

    def test_they_can_request_a_membership(self, client, seed_data, app):
        """The route out of having no organisation. A 403 here is the bug."""
        token = self._orgless_token(client, seed_data, app)
        resp = client.post('/api/groups/request-membership',
                           headers=_auth_header(token),
                           json={'group_guid': seed_data['group_a_guid']})
        assert resp.status_code == 201
        assert resp.get_json()['status'] == 'pending'

    def test_they_can_redeem_an_invite(self, client, seed_data, app):
        """Same argument: an invite is how an org reaches someone who has
        none. It may fail on the token, never on the missing membership."""
        token = self._orgless_token(client, seed_data, app)
        resp = client.post('/api/groups/join-by-invite',
                           headers=_auth_header(token),
                           json={'token': 'no-such-invite'})
        assert resp.status_code in (400, 404)
        body = resp.get_json() or {}
        assert body.get('error') != 'organisation_required'

    def test_the_decorator_is_gone(self):
        """Named so a grep for the removal lands on the reasoning."""
        from src.middleware import auth_middleware
        assert not hasattr(auth_middleware, 'require_organisation')
