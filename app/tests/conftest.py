"""Pytest fixtures for sso.pdhc tests.

Provides fixtures for all roles: patient, professional, group_admin, su_admin.
Handles test DB lifecycle (in-memory SQLite via StaticPool).
"""
import uuid

import pytest

from src.app import create_app
from src.db import get_session
from src.services.auth_service import hash_password
from src.services.jwt_service import issue_token
from src.middleware.rate_limit import reset_rate_limits

SECRET = 'test-secret-key-not-for-production'


@pytest.fixture
def app():
    """Create test application with in-memory SQLite."""
    reset_rate_limits()
    application = create_app({
        # #1050: production default is CLOSED; these suites test the open door.
        'PATIENT_SELF_REGISTRATION': 'true',
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
    """Create test client."""
    return app.test_client()


@pytest.fixture
def seeded_app(app):
    """App with full seed data: SU admin, professional, patient, org, group.

    Returns dict with guids, emails, passwords, and tokens for each role.
    """
    from src.models.user import User
    from src.models.patient import Patient
    from src.models.professional import Professional
    from src.models.organisation import Organisation
    from src.models.user_organisation import UserOrganisation
    from src.models.group import Group
    from src.models.membership import Membership

    with app.app_context():
        s = get_session()

        # Organisation
        org = Organisation(name='Test Hospital')
        s.add(org)
        s.flush()

        # SU admin professional
        su_pw = 'supass1234'
        su = User(email='su@test.com', password_hash=hash_password(su_pw),
                  user_type='professional', is_su_admin=True)
        s.add(su)
        s.flush()
        su_prof = Professional(user_id=su.id, professional_role='doctor',
                               first_name='Super', last_name='Admin')
        s.add(su_prof)
        s.add(UserOrganisation(user_guid=su.guid, organisation_guid=org.guid))

        # Regular professional
        pro_pw = 'propass1234'
        pro = User(email='pro@test.com', password_hash=hash_password(pro_pw),
                   user_type='professional', is_su_admin=False)
        s.add(pro)
        s.flush()
        pro_prof = Professional(user_id=pro.id, professional_role='nurse',
                                first_name='Regular', last_name='Professional')
        s.add(pro_prof)
        s.add(UserOrganisation(user_guid=pro.guid, organisation_guid=org.guid))

        # Group + make pro a group admin
        grp = Group(name='Test Group', category='planning')
        s.add(grp)
        s.flush()
        mem = Membership(user_guid=pro.guid, group_guid=grp.guid,
                         status='approved', is_admin=True)
        s.add(mem)

        # Patient
        pat_pw = 'patpass1234'
        pat = User(email='pat@test.com', password_hash=hash_password(pat_pw),
                   user_type='patient', is_su_admin=False)
        s.add(pat)
        s.flush()
        patient = Patient(user_id=pat.id, personnummer='199001011234',
                          organisation_guid=org.guid, in_registry=True,
                          registries=['INCA'])
        s.add(patient)

        s.commit()

        su_token = issue_token(su.guid, SECRET, expiry_hours=1)
        pro_token = issue_token(pro.guid, SECRET, expiry_hours=1)
        pat_token = issue_token(pat.guid, SECRET, expiry_hours=1)

        data = {
            'su_guid': su.guid, 'su_email': su.email, 'su_pw': su_pw,
            'su_token': su_token,
            'pro_guid': pro.guid, 'pro_email': pro.email, 'pro_pw': pro_pw,
            'pro_token': pro_token,
            'pat_guid': pat.guid, 'pat_email': pat.email, 'pat_pw': pat_pw,
            'pat_token': pat_token,
            'org_guid': org.guid, 'org_name': org.name,
            'grp_guid': grp.guid, 'grp_name': grp.name,
        }
        s.close()
        return data
