"""Reform S2/S3/S4/S5/S6 — Role registry, Affiliation, phase intersection, blob.

Self-contained-engine tests (matches test_care_hierarchy / test_models).
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db import Base
from src.models import (  # noqa: F401 — register for create_all
    User, Patient, Professional, Organisation, UserOrganisation,
    Group, Membership, GroupProposal, LeaderRequest, AccessRequest,
    Invite, RevokedToken, Role, ResearchProject, Affiliation,
)
from src.models.user_phase import UserPhase  # noqa: F401
from src.models.role import seed_roles, SEED_ROLES
from src.services import affiliation_service as afs
from src.services.auth_service import hash_password


@pytest.fixture
def session():
    engine = create_engine('sqlite://', echo=False)
    Base.metadata.create_all(bind=engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()
    Base.metadata.drop_all(bind=engine)


# --- S2: Role registry -----------------------------------------------------

class TestRoleRegistry:
    def test_seed_creates_seven(self, session):
        roles = seed_roles(session)
        assert len(roles) == len(SEED_ROLES) == 7
        assert set(roles) == {c for c, *_ in SEED_ROLES}

    def test_seed_idempotent(self, session):
        seed_roles(session)
        seed_roles(session)
        assert session.query(Role).count() == 7

    def test_doctor_permits_analysis(self, session):
        roles = seed_roles(session)
        assert 'analysis' in roles['doctor'].permitted_phases
        assert 'analysis' in roles['nurse'].permitted_phases
        assert 'analysis' not in roles['other_care'].permitted_phases

    def test_bad_phase_rejected(self, session):
        r = Role(code='x', display_name='X', zone='care',
                 permitted_phases=['request', 'nonsense'])
        with pytest.raises(ValueError, match='unknown phase'):
            r.validate_permitted_phases()


# --- S5: Option C phase intersection ---------------------------------------

class TestSessionPhases:
    def test_doctor_keeps_permitted(self, session):
        roles = seed_roles(session)
        got = afs.resolve_session_phases(
            {'planning', 'request', 'analysis'}, roles['doctor'])
        assert got == ['analysis', 'planning', 'request']

    def test_other_care_strips_analysis(self, session):
        roles = seed_roles(session)
        got = afs.resolve_session_phases(
            {'request', 'analysis'}, roles['other_care'])
        assert got == ['request']              # analysis not permitted

    def test_planning_is_orthogonal(self, session):
        roles = seed_roles(session)
        # researcher permits only analysis, but planning passes if granted.
        got = afs.resolve_session_phases(
            {'planning', 'analysis'}, roles['researcher'])
        assert got == ['analysis', 'planning']

    def test_no_role_only_orthogonal(self, session):
        got = afs.resolve_session_phases({'planning', 'analysis'}, None)
        assert got == ['planning']


# --- S3: backfill from UserOrganisation ------------------------------------

def _make_person(session, email, legacy_role):
    u = User(email=email, password_hash=hash_password('pw'),
             user_type='professional')
    session.add(u)
    session.flush()
    if legacy_role is not None:
        session.add(Professional(user_id=u.id, professional_role=legacy_role))
    session.flush()
    return u


class TestBackfill:
    def _hierarchy(self, session):
        caregiver = Organisation(name='Region X')
        session.add(caregiver)
        session.flush()
        unit = Organisation(name='Clinic A',
                            parent_caregiver_guid=caregiver.guid)
        session.add(unit)
        session.commit()
        return caregiver, unit

    def test_backfill_creates_affiliation_with_legacy_role(self, session):
        _, unit = self._hierarchy(session)
        u = _make_person(session, 'doc@x.se', 'doctor')
        session.add(UserOrganisation(user_guid=u.guid,
                                     organisation_guid=unit.guid))
        session.commit()

        summary = afs.backfill_affiliations_from_user_organisations(session)
        assert summary['created'] == 1
        aff = session.query(Affiliation).filter_by(person_guid=u.guid).one()
        assert aff.care_unit_guid == unit.guid
        role = session.query(Role).filter_by(guid=aff.role_guid).one()
        assert role.code == 'doctor'
        assert aff.status == 'active'

    def test_backfill_idempotent(self, session):
        _, unit = self._hierarchy(session)
        u = _make_person(session, 'n@x.se', 'nurse')
        session.add(UserOrganisation(user_guid=u.guid,
                                     organisation_guid=unit.guid))
        session.commit()
        afs.backfill_affiliations_from_user_organisations(session)
        s2 = afs.backfill_affiliations_from_user_organisations(session)
        assert s2['created'] == 0 and s2['skipped_existing'] == 1
        assert session.query(Affiliation).count() == 1

    def test_no_professional_defaults_and_flags(self, session):
        _, unit = self._hierarchy(session)
        u = _make_person(session, 'noprof@x.se', None)  # no Professional row
        session.add(UserOrganisation(user_guid=u.guid,
                                     organisation_guid=unit.guid))
        session.commit()
        summary = afs.backfill_affiliations_from_user_organisations(session)
        assert summary['created'] == 1
        assert len(summary['flagged_default_role']) == 1
        aff = session.query(Affiliation).filter_by(person_guid=u.guid).one()
        role = session.query(Role).filter_by(guid=aff.role_guid).one()
        assert role.code == 'other_care'


# --- S6: blob assembly -----------------------------------------------------

class TestBlobAssembly:
    def test_build_affiliations_for_blob(self, session):
        caregiver = Organisation(name='Region Y')
        session.add(caregiver); session.flush()
        unit = Organisation(name='Clinic B',
                            parent_caregiver_guid=caregiver.guid)
        session.add(unit); session.flush()
        roles = seed_roles(session)
        u = _make_person(session, 'r@y.se', 'doctor')
        session.add(Affiliation(person_guid=u.guid, care_unit_guid=unit.guid,
                                role_guid=roles['researcher'].guid,
                                research_project_guids=['proj-1'],
                                status='active'))
        session.commit()

        blob_affs = afs.build_affiliations_for_blob(session, u.guid)
        assert len(blob_affs) == 1
        a = blob_affs[0]
        assert a['care_unit_guid'] == unit.guid
        assert a['care_unit_name'] == 'Clinic B'
        assert a['care_organisation_guid'] == caregiver.guid
        assert a['care_organisation_name'] == 'Region Y'
        assert a['role'] == 'researcher'
        assert a['research_project_guids'] == ['proj-1']

    def test_suspended_affiliation_excluded(self, session):
        caregiver = Organisation(name='Region Z')
        session.add(caregiver); session.flush()
        unit = Organisation(name='Clinic C',
                            parent_caregiver_guid=caregiver.guid)
        session.add(unit); session.flush()
        roles = seed_roles(session)
        u = _make_person(session, 's@z.se', 'nurse')
        session.add(Affiliation(person_guid=u.guid, care_unit_guid=unit.guid,
                                role_guid=roles['nurse'].guid,
                                status='suspended'))
        session.commit()
        assert afs.build_affiliations_for_blob(session, u.guid) == []
