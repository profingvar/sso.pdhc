"""Phase 9 tests — frontend UI: all pages render, CSRF present, login flow,
dashboard data per role, admin pages restricted, group admin restricted."""
import uuid

import pytest

from src.app import create_app
from src.db import get_session
from src.services.auth_service import hash_password
from src.services.jwt_service import issue_token
from src.models.user import User
from src.models.patient import Patient
from src.models.professional import Professional
from src.models.organisation import Organisation
from src.models.user_organisation import UserOrganisation
from src.models.group import Group
from src.models.membership import Membership
from src.models.group_proposal import GroupProposal


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
        'ALLOWED_ORIGINS': ['http://localhost:9000'],
        'ALLOWED_CALLBACK_URLS': ['http://localhost:9000/callback'],
        'SERVICE_CREDENTIALS': {'test-client': 'test-secret'},
    })
    yield application


@pytest.fixture
def client(app):
    return app.test_client()


def _make_su(app):
    """Create SU admin user, return (user, token)."""
    with app.app_context():
        s = get_session()
        user = User(email='su@test.com', password_hash=hash_password('password123'),
                    user_type='professional', is_su_admin=True)
        s.add(user)
        s.flush()
        prof = Professional(user_id=user.id, professional_role='doctor',
                            first_name='Super', last_name='Admin')
        s.add(prof)
        s.flush()
        token = issue_token(user.guid, SECRET, expiry_hours=1)
        return user.guid, token


def _make_professional(app, email='pro@test.com'):
    """Create professional user, return (guid, token)."""
    with app.app_context():
        s = get_session()
        user = User(email=email, password_hash=hash_password('password123'),
                    user_type='professional', is_su_admin=False)
        s.add(user)
        s.flush()
        prof = Professional(user_id=user.id, professional_role='nurse',
                            first_name='Pro', last_name='User')
        s.add(prof)
        s.flush()
        token = issue_token(user.guid, SECRET, expiry_hours=1)
        return user.guid, token


def _make_patient(app, email='pat@test.com'):
    """Create patient user, return (guid, token)."""
    with app.app_context():
        s = get_session()
        org = Organisation(name='Test Hospital')
        s.add(org)
        s.flush()
        user = User(email=email, password_hash=hash_password('password123'),
                    user_type='patient', is_su_admin=False)
        s.add(user)
        s.flush()
        patient = Patient(user_id=user.id, personnummer='199901011234',
                          organisation_guid=org.guid)
        s.add(patient)
        s.flush()
        token = issue_token(user.guid, SECRET, expiry_hours=1)
        return user.guid, token


def _login_session(client, app, token):
    """Set session token for frontend routes."""
    with client.session_transaction() as sess:
        sess['token'] = token


# ============================================================
# 9.a — Base template rendering
# ============================================================

class TestBaseTemplate:
    """Base template: navbar, flash messages, CSRF token present."""

    def test_landing_page_renders(self, client, app):
        resp = client.get('/')
        assert resp.status_code == 200
        html = resp.data.decode()
        assert 'sso.pdhc' in html

    def test_login_page_has_csrf(self, client, app):
        resp = client.get('/login')
        assert resp.status_code == 200
        html = resp.data.decode()
        assert 'csrf_token' in html

    def test_navbar_shows_login_when_unauthenticated(self, client, app):
        resp = client.get('/')
        html = resp.data.decode()
        assert 'Login' in html
        assert 'Dashboard' not in html


# ============================================================
# 9.b — Login page + SSO handshake
# ============================================================

class TestLoginPage:
    """Login form, success redirect, SSO handshake."""

    def test_login_page_renders(self, client, app):
        resp = client.get('/login')
        assert resp.status_code == 200
        assert b'Login' in resp.data

    def test_login_success_redirects_to_dashboard(self, client, app):
        with app.app_context():
            s = get_session()
            s.add(User(email='test@x.com', password_hash=hash_password('password123'),
                       user_type='professional', is_su_admin=False))
            s.flush()

        resp = client.post('/login', data={'email': 'test@x.com', 'password': 'password123'},
                           follow_redirects=False)
        assert resp.status_code == 302
        assert '/dashboard' in resp.headers['Location']

    def test_login_fail_shows_error(self, client, app):
        resp = client.post('/login', data={'email': 'bad@x.com', 'password': 'wrong'},
                           follow_redirects=True)
        assert b'Invalid email or password' in resp.data

    def test_sso_handshake_redirect(self, client, app):
        with app.app_context():
            s = get_session()
            s.add(User(email='sso@x.com', password_hash=hash_password('password123'),
                       user_type='professional', is_su_admin=False))
            s.flush()

        resp = client.post('/login', data={
            'email': 'sso@x.com', 'password': 'password123',
            'next': 'http://localhost:9000/callback', 'state': 'abc123',
        }, follow_redirects=False)
        assert resp.status_code == 302
        loc = resp.headers['Location']
        assert 'http://localhost:9000/callback' in loc
        assert 'token=' in loc
        assert 'state=abc123' in loc

    def test_auto_redirect_existing_session(self, client, app):
        guid, token = _make_professional(app)
        _login_session(client, app, token)

        resp = client.get('/login?next=http://localhost:9000/callback&state=xyz',
                          follow_redirects=False)
        assert resp.status_code == 302
        assert 'http://localhost:9000/callback' in resp.headers['Location']
        assert 'token=' in resp.headers['Location']


# ============================================================
# 9.c — Dashboard
# ============================================================

class TestDashboard:
    """Dashboard shows correct data per role."""

    def test_dashboard_requires_login(self, client, app):
        resp = client.get('/dashboard', follow_redirects=False)
        assert resp.status_code == 302
        assert '/login' in resp.headers['Location']

    def test_dashboard_professional(self, client, app):
        guid, token = _make_professional(app)
        _login_session(client, app, token)

        resp = client.get('/dashboard')
        assert resp.status_code == 200
        html = resp.data.decode()
        assert 'professional' in html
        assert 'Pro User' in html

    def test_dashboard_patient(self, client, app):
        guid, token = _make_patient(app)
        _login_session(client, app, token)

        resp = client.get('/dashboard')
        assert resp.status_code == 200
        html = resp.data.decode()
        assert 'patient' in html
        assert '199901011234' in html

    def test_dashboard_su_shows_admin_link(self, client, app):
        guid, token = _make_su(app)
        _login_session(client, app, token)

        resp = client.get('/dashboard')
        assert resp.status_code == 200
        assert b'Admin' in resp.data


# ============================================================
# 9.d — SU Admin page
# ============================================================

class TestSUAdminPage:
    """SU admin panel access control and rendering."""

    def test_admin_page_requires_su(self, client, app):
        guid, token = _make_professional(app)
        _login_session(client, app, token)

        resp = client.get('/admin', follow_redirects=False)
        assert resp.status_code == 302
        assert '/dashboard' in resp.headers['Location']

    def test_admin_page_renders_for_su(self, client, app):
        guid, token = _make_su(app)
        _login_session(client, app, token)

        resp = client.get('/admin')
        assert resp.status_code == 200
        html = resp.data.decode()
        assert 'SU Admin Panel' in html
        assert 'su@test.com' in html

    def test_admin_create_organisation(self, client, app):
        guid, token = _make_su(app)
        _login_session(client, app, token)

        resp = client.post('/admin/create-organisation',
                           data={'name': 'New Org'},
                           follow_redirects=True)
        assert resp.status_code == 200
        assert b'New Org' in resp.data

    def test_admin_decide_access_request(self, client, app):
        guid, token = _make_su(app)
        _login_session(client, app, token)

        # Create org + access request
        with app.app_context():
            s = get_session()
            org = Organisation(name='Test Org')
            s.add(org)
            s.flush()

            from src.models.access_request import AccessRequest
            ar = AccessRequest(
                email='newpro@test.com', password_hash=hash_password('password123'),
                first_name='New', last_name='Pro', professional_role='doctor',
                organisation_guid=org.guid, requested_phases=['planning'],
                chosen_leader_guid=guid, status='pending',
            )
            s.add(ar)
            s.flush()
            ar_guid = ar.guid

        resp = client.post('/admin/decide-access-request',
                           data={'access_request_guid': ar_guid, 'decision': 'approved'},
                           follow_redirects=True)
        assert resp.status_code == 200
        assert b'approved' in resp.data


# ============================================================
# 9.e — Group Admin page
# ============================================================

class TestGroupAdminPage:
    """Group admin panel access control and functionality."""

    def test_group_admin_requires_admin(self, client, app):
        guid, token = _make_professional(app)
        _login_session(client, app, token)

        resp = client.get('/group-admin', follow_redirects=False)
        assert resp.status_code == 302
        assert '/dashboard' in resp.headers['Location']

    def test_group_admin_renders_for_su(self, client, app):
        guid, token = _make_su(app)
        _login_session(client, app, token)

        resp = client.get('/group-admin')
        assert resp.status_code == 200
        assert b'Group Admin Panel' in resp.data

    def test_group_admin_renders_for_group_admin(self, client, app):
        guid, token = _make_professional(app)
        _login_session(client, app, token)

        # Make user admin of a group
        with app.app_context():
            s = get_session()
            grp = Group(name='Test Group', group_type='planning')
            s.add(grp)
            s.flush()
            mem = Membership(user_guid=guid, group_guid=grp.guid,
                             status='approved', is_admin=True)
            s.add(mem)
            s.flush()

        resp = client.get('/group-admin')
        assert resp.status_code == 200
        assert b'Group Admin Panel' in resp.data


# ============================================================
# 9.f — Onboarding pages
# ============================================================

class TestOnboardingPages:
    """Register patient, request access, join, suggest group, change password."""

    def test_register_patient_page_renders(self, client, app):
        resp = client.get('/register-patient')
        assert resp.status_code == 200
        assert b'Patient Registration' in resp.data
        assert b'csrf_token' in resp.data

    def test_register_patient_success(self, client, app):
        with app.app_context():
            s = get_session()
            org = Organisation(name='Reg Org')
            s.add(org)
            s.flush()
            org_guid = org.guid

        resp = client.post('/register-patient', data={
            'email': 'newpat@test.com', 'password': 'password123',
            'personnummer': '200001011234', 'organisation_guid': org_guid,
        }, follow_redirects=False)
        assert resp.status_code == 302
        assert '/login' in resp.headers['Location']

    def test_request_access_page_renders(self, client, app):
        resp = client.get('/request-access')
        assert resp.status_code == 200
        assert b'Request Professional Access' in resp.data

    def test_join_requires_login(self, client, app):
        resp = client.get('/join', follow_redirects=False)
        assert resp.status_code == 302
        assert '/login' in resp.headers['Location']

    def test_request_join_page_renders(self, client, app):
        guid, token = _make_professional(app)
        _login_session(client, app, token)

        resp = client.get('/request-join')
        assert resp.status_code == 200
        assert b'Request Group Membership' in resp.data

    def test_suggest_group_page_renders(self, client, app):
        guid, token = _make_professional(app)
        _login_session(client, app, token)

        resp = client.get('/suggest-group')
        assert resp.status_code == 200
        assert b'Suggest New Group' in resp.data

    def test_suggest_group_creates_proposal(self, client, app):
        guid, token = _make_professional(app)
        _login_session(client, app, token)

        resp = client.post('/suggest-group', data={
            'proposed_name': 'New Research', 'group_type': 'analysis',
        }, follow_redirects=False)
        assert resp.status_code == 302
        assert '/dashboard' in resp.headers['Location']

        # Verify proposal created
        with app.app_context():
            s = get_session()
            p = s.query(GroupProposal).filter_by(proposed_name='New Research').first()
            assert p is not None
            assert p.status == 'pending'

    def test_change_password_page_renders(self, client, app):
        guid, token = _make_professional(app)
        _login_session(client, app, token)

        resp = client.get('/change-password')
        assert resp.status_code == 200
        assert b'Change Password' in resp.data

    def test_change_password_success(self, client, app):
        guid, token = _make_professional(app, email='chpw@test.com')
        _login_session(client, app, token)

        resp = client.post('/change-password', data={
            'current_password': 'password123', 'new_password': 'newpassword99',
        }, follow_redirects=False)
        assert resp.status_code == 302
        assert '/dashboard' in resp.headers['Location']


# ============================================================
# 9.g — Docs page
# ============================================================

class TestDocsPage:
    """Docs page renders, path traversal blocked."""

    def test_docs_page_renders(self, client, app):
        resp = client.get('/docs')
        assert resp.status_code == 200
        assert b'Documentation' in resp.data

    def test_docs_download_unknown_file_404(self, client, app):
        resp = client.get('/docs/download/../../etc/passwd')
        assert resp.status_code == 404

    def test_nonexistent_route_returns_404(self, client, app):
        """Ticket #58: the global exception handler must not coerce 404 → 500."""
        resp = client.get('/this-route-definitely-does-not-exist')
        assert resp.status_code == 404

    def test_wrong_method_returns_405(self, client, app):
        """Ticket #58: the global exception handler must not coerce 405 → 500."""
        # /api/auth/login accepts POST; GET must yield 405, not 500.
        resp = client.get('/api/auth/login')
        assert resp.status_code == 405


# ============================================================
# 9.h — Landing page
# ============================================================

class TestLandingPage:
    """Landing page with service list."""

    def test_landing_renders(self, client, app):
        resp = client.get('/')
        assert resp.status_code == 200
        html = resp.data.decode()
        assert 'sso.pdhc' in html
        # The landing page groups services into two sections after the
        # 2026-04 rewrite (ticket #59): SSO-authenticated core services
        # and non-SSO-authenticated provider/patient endpoints.
        assert 'Platform Services' in html
        assert 'Connected Services' in html


# ============================================================
# 9.i — Login flow end-to-end
# ============================================================

class TestLoginFlowEndToEnd:
    """Complete login → dashboard → logout flow."""

    def test_full_login_logout_flow(self, client, app):
        # Create user
        with app.app_context():
            s = get_session()
            s.add(User(email='flow@test.com', password_hash=hash_password('password123'),
                       user_type='professional', is_su_admin=False))
            prof = None
            s.flush()
            user = s.query(User).filter_by(email='flow@test.com').first()
            prof = Professional(user_id=user.id, professional_role='other',
                                first_name='Flow', last_name='Test')
            s.add(prof)
            s.flush()

        # Login
        resp = client.post('/login', data={'email': 'flow@test.com', 'password': 'password123'},
                           follow_redirects=False)
        assert resp.status_code == 302
        assert '/dashboard' in resp.headers['Location']

        # Access dashboard
        resp = client.get('/dashboard')
        assert resp.status_code == 200
        assert b'Flow Test' in resp.data

        # Navbar shows user email and Dashboard link
        assert b'flow@test.com' in resp.data
        assert b'Dashboard' in resp.data

        # Logout
        resp = client.post('/logout', follow_redirects=False)
        assert resp.status_code == 302
        assert '/login' in resp.headers['Location']

        # Dashboard now requires login
        resp = client.get('/dashboard', follow_redirects=False)
        assert resp.status_code == 302
        assert '/login' in resp.headers['Location']
