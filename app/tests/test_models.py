"""Phase 2 tests — model structure, GUID generation, enums, relationships."""
import uuid

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker

from src.db import Base
# Import all models to register them
from src.models import (
    User, Patient, Professional, Organisation, UserOrganisation,
    Group, Membership, GroupProposal, LeaderRequest, AccessRequest,
    Invite, RevokedToken,
)
from src.models.user_phase import UserPhase  # noqa: F401  # register for create_all


@pytest.fixture(scope='module')
def db_session():
    """Create an in-memory SQLite database for testing model structure."""
    engine = create_engine('sqlite://', echo=False)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()
    Base.metadata.drop_all(bind=engine)


class TestAllTablesCreated:
    """Verify all expected tables exist after create_all."""

    EXPECTED_TABLES = [
        'users', 'patients', 'professionals', 'organisations',
        'user_organisations', 'groups', 'memberships', 'group_proposals',
        'leader_requests', 'access_requests', 'invites', 'revoked_tokens',
        'user_phases',  # #46: direct phase grants
        'organisation_audit',  # ticket #96: audit log for org/partner changes
    ]

    def test_all_tables_exist(self, db_session):
        engine = db_session.get_bind()
        inspector = inspect(engine)
        tables = inspector.get_table_names()
        for table in self.EXPECTED_TABLES:
            assert table in tables, f"Table '{table}' missing"

    def test_table_count(self, db_session):
        engine = db_session.get_bind()
        inspector = inspect(engine)
        tables = inspector.get_table_names()
        assert len(tables) == len(self.EXPECTED_TABLES)


class TestUserModel:
    """User model tests."""

    def test_create_user(self, db_session):
        user = User(
            email='test@example.com',
            password_hash='fakehash',
            user_type='professional',
        )
        db_session.add(user)
        db_session.commit()
        assert user.id is not None
        assert user.guid is not None
        assert len(user.guid) == 36  # UUID format
        assert user.is_su_admin is False

    def test_guid_is_valid_uuid(self, db_session):
        user = db_session.query(User).filter_by(email='test@example.com').first()
        parsed = uuid.UUID(user.guid)
        assert str(parsed) == user.guid

    def test_user_type_enum(self, db_session):
        user = User(
            email='patient@example.com',
            password_hash='fakehash',
            user_type='patient',
        )
        db_session.add(user)
        db_session.commit()
        assert user.user_type == 'patient'


class TestPatientModel:
    """Patient model tests."""

    def test_create_patient(self, db_session):
        user = db_session.query(User).filter_by(email='patient@example.com').first()
        patient = Patient(
            user_id=user.id,
            personnummer='199001011234',
        )
        db_session.add(patient)
        db_session.commit()
        assert patient.guid is not None
        assert patient.fhir_resource_type == 'Patient'
        assert patient.FHIR_RESOURCE_TYPE == 'Patient'
        assert patient.in_registry is False

    def test_personnummer_length(self, db_session):
        patient = db_session.query(Patient).first()
        assert len(patient.personnummer) == 12


class TestProfessionalModel:
    """Professional model tests."""

    def test_create_professional(self, db_session):
        user = db_session.query(User).filter_by(email='test@example.com').first()
        prof = Professional(
            user_id=user.id,
            professional_role='doctor',
            first_name='Anna',
            last_name='Svensson',
        )
        db_session.add(prof)
        db_session.commit()
        assert prof.guid is not None
        assert prof.fhir_resource_type == 'Practitioner'
        assert prof.FHIR_RESOURCE_TYPE == 'Practitioner'
        assert prof.professional_role == 'doctor'


class TestOrganisationModel:
    """Organisation model tests."""

    def test_create_organisation(self, db_session):
        org = Organisation(name='Test Hospital')
        db_session.add(org)
        db_session.commit()
        assert org.guid is not None
        assert org.fhir_resource_type == 'Organization'
        assert org.FHIR_RESOURCE_TYPE == 'Organization'

    def test_parent_caregiver_guid_defaults_to_null(self, db_session):
        """#187. New orgs default to caregiver (NULL parent)."""
        org = Organisation(name='Caregiver A')
        db_session.add(org)
        db_session.commit()
        assert org.parent_caregiver_guid is None

    def test_clinic_points_at_caregiver(self, db_session):
        """#187. A vårdenhet (clinic) carries its caregiver's guid in
        parent_caregiver_guid; round-trip persists."""
        caregiver = Organisation(name='Caregiver B')
        db_session.add(caregiver)
        db_session.commit()
        clinic = Organisation(
            name='Clinic of B',
            parent_caregiver_guid=caregiver.guid,
        )
        db_session.add(clinic)
        db_session.commit()
        # Reload to prove the value persisted.
        fetched = (
            db_session.query(Organisation).filter_by(guid=clinic.guid).one()
        )
        assert fetched.parent_caregiver_guid == caregiver.guid

    def test_admin_dict_exposes_parent_caregiver_guid(self, db_session):
        caregiver = Organisation(name='Caregiver C')
        db_session.add(caregiver)
        db_session.commit()
        clinic = Organisation(
            name='Clinic of C',
            parent_caregiver_guid=caregiver.guid,
        )
        db_session.add(clinic)
        db_session.commit()
        d = clinic.admin_dict()
        assert 'parent_caregiver_guid' in d
        assert d['parent_caregiver_guid'] == caregiver.guid
        assert caregiver.admin_dict()['parent_caregiver_guid'] is None


class TestUserOrganisationModel:
    """User-Organisation junction tests."""

    def test_create_link(self, db_session):
        user = db_session.query(User).filter_by(email='test@example.com').first()
        org = db_session.query(Organisation).first()
        link = UserOrganisation(
            user_guid=user.guid,
            organisation_guid=org.guid,
        )
        db_session.add(link)
        db_session.commit()
        assert link.id is not None

    def test_user_has_organisations(self, db_session):
        user = db_session.query(User).filter_by(email='test@example.com').first()
        assert len(user.organisations) >= 1


class TestGroupModel:
    """Group model tests."""

    def test_create_group(self, db_session):
        group = Group(name='Planning Team A', category='planning')
        db_session.add(group)
        db_session.commit()
        assert group.guid is not None
        assert group.fhir_resource_type == 'Group'
        assert group.category == 'planning'

    def test_category_is_free_form(self, db_session):
        """#60: category is now a plain varchar — SU can invent new labels."""
        group = Group(name='Bespoke Team', category='governance-board')
        db_session.add(group)
        db_session.commit()
        assert group.category == 'governance-board'


class TestMembershipModel:
    """Membership model tests."""

    def test_create_membership(self, db_session):
        user = db_session.query(User).filter_by(email='test@example.com').first()
        group = db_session.query(Group).first()
        mem = Membership(
            user_guid=user.guid,
            group_guid=group.guid,
        )
        db_session.add(mem)
        db_session.commit()
        assert mem.guid is not None
        assert mem.status == 'pending'
        assert mem.is_admin is False

    def test_approve_membership(self, db_session):
        mem = db_session.query(Membership).first()
        mem.status = 'approved'
        db_session.commit()
        assert mem.status == 'approved'


class TestGroupProposalModel:
    def test_create_proposal(self, db_session):
        user = db_session.query(User).first()
        prop = GroupProposal(
            proposed_name='New Analysis Group',
            category='analysis',
            requested_by_guid=user.guid,
        )
        db_session.add(prop)
        db_session.commit()
        assert prop.status == 'pending'
        assert prop.guid is not None


class TestLeaderRequestModel:
    def test_create_leader_request(self, db_session):
        user = db_session.query(User).first()
        group = db_session.query(Group).first()
        lr = LeaderRequest(
            user_guid=user.guid,
            group_guid=group.guid,
        )
        db_session.add(lr)
        db_session.commit()
        assert lr.status == 'pending'
        assert lr.guid is not None


class TestAccessRequestModel:
    def test_create_access_request(self, db_session):
        org = db_session.query(Organisation).first()
        leader = db_session.query(User).first()
        ar = AccessRequest(
            email='newpro@example.com',
            password_hash='fakehash',
            first_name='Erik',
            last_name='Johansson',
            professional_role='nurse',
            organisation_guid=org.guid,
            requested_phases=['planning', 'request'],
            chosen_leader_guid=leader.guid,
        )
        db_session.add(ar)
        db_session.commit()
        assert ar.status == 'pending'
        assert ar.guid is not None


class TestInviteModel:
    def test_create_invite(self, db_session):
        from datetime import datetime, timedelta, timezone
        group = db_session.query(Group).first()
        user = db_session.query(User).first()
        inv = Invite(
            group_guid=group.guid,
            token='test-invite-token-123',
            expires_at=datetime.now(timezone.utc) + timedelta(days=7),
            created_by_guid=user.guid,
        )
        db_session.add(inv)
        db_session.commit()
        assert inv.guid is not None


class TestRevokedTokenModel:
    def test_create_revoked_token(self, db_session):
        from datetime import datetime, timedelta, timezone
        rt = RevokedToken(
            token_guid=str(uuid.uuid4()),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
        )
        db_session.add(rt)
        db_session.commit()
        assert rt.id is not None


class TestFHIRAnnotations:
    """Verify all FHIR-mapped models carry correct resource type."""

    def test_patient_fhir_type(self):
        assert Patient.FHIR_RESOURCE_TYPE == 'Patient'

    def test_professional_fhir_type(self):
        assert Professional.FHIR_RESOURCE_TYPE == 'Practitioner'

    def test_organisation_fhir_type(self):
        assert Organisation.FHIR_RESOURCE_TYPE == 'Organization'

    def test_group_fhir_type(self):
        assert Group.FHIR_RESOURCE_TYPE == 'Group'


class TestGUIDUniqueness:
    """Verify multiple records get distinct GUIDs."""

    def test_users_have_unique_guids(self, db_session):
        users = db_session.query(User).all()
        guids = [u.guid for u in users]
        assert len(guids) == len(set(guids))
