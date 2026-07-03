"""Reform S1 (ticket #397) — CareOrganisation / CareUnit views + validation.

The 2-level PDL hierarchy lives on the single self-referential organisations
table (parent_caregiver_guid, #187). These tests cover the model accessors,
the query helpers, name resolution, and the 2-level integrity guard.
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db import Base
from src.models import (  # noqa: F401 — register models for create_all
    User, Patient, Professional, Organisation, UserOrganisation,
    Group, Membership, GroupProposal, LeaderRequest, AccessRequest,
    Invite, RevokedToken,
)
from src.models.user_phase import UserPhase  # noqa: F401
from src.services import care_hierarchy as ch


@pytest.fixture
def session():
    engine = create_engine('sqlite://', echo=False)
    Base.metadata.create_all(bind=engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def hierarchy(session):
    """A caregiver with two units, plus an external partner.

    Returns the created rows keyed by short name.
    """
    caregiver = Organisation(name='Region Uppsala')          # vårdgivare
    session.add(caregiver)
    session.flush()  # assign guid

    unit_a = Organisation(name='UAS Kardiologi',
                          parent_caregiver_guid=caregiver.guid)
    unit_b = Organisation(name='UAS Endokrin',
                          parent_caregiver_guid=caregiver.guid)
    partner = Organisation(name='Acme Vendor', is_external=True)
    session.add_all([unit_a, unit_b, partner])
    session.commit()
    return {'caregiver': caregiver, 'unit_a': unit_a, 'unit_b': unit_b,
            'partner': partner}


# --- model accessors -------------------------------------------------------

class TestAccessors:
    def test_caregiver_is_care_organisation(self, hierarchy):
        c = hierarchy['caregiver']
        assert c.is_care_organisation is True
        assert c.is_care_unit is False
        assert c.care_organisation_guid == c.guid

    def test_unit_is_care_unit(self, hierarchy):
        u = hierarchy['unit_a']
        assert u.is_care_unit is True
        assert u.is_care_organisation is False
        assert u.care_organisation_guid == hierarchy['caregiver'].guid

    def test_external_is_neither(self, hierarchy):
        p = hierarchy['partner']
        assert p.is_care_organisation is False
        assert p.is_care_unit is False
        assert p.care_organisation_guid is None

    def test_projections(self, hierarchy):
        cu = hierarchy['unit_a'].care_unit_dict()
        assert cu['care_unit_guid'] == hierarchy['unit_a'].guid
        assert cu['care_unit_name'] == 'UAS Kardiologi'
        assert cu['care_organisation_guid'] == hierarchy['caregiver'].guid
        co = hierarchy['caregiver'].care_organisation_dict()
        assert co['care_organisation_name'] == 'Region Uppsala'


# --- query helpers ---------------------------------------------------------

class TestQueries:
    def test_list_care_organisations(self, session, hierarchy):
        orgs = ch.list_care_organisations(session)
        names = {o.name for o in orgs}
        assert names == {'Region Uppsala'}      # partner + units excluded

    def test_list_care_units(self, session, hierarchy):
        units = ch.list_care_units(session)
        names = {u.name for u in units}
        assert names == {'UAS Kardiologi', 'UAS Endokrin'}

    def test_care_units_for_organisation(self, session, hierarchy):
        units = ch.care_units_for_organisation(
            session, hierarchy['caregiver'].guid)
        assert len(units) == 2

    def test_resolve_and_names(self, session, hierarchy):
        u = hierarchy['unit_a']
        assert ch.resolve_care_organisation_guid(session, u.guid) == \
            hierarchy['caregiver'].guid
        assert ch.care_unit_name(session, u.guid) == 'UAS Kardiologi'
        assert ch.care_organisation_name(
            session, hierarchy['caregiver'].guid) == 'Region Uppsala'

    def test_resolve_unknown_guid_none(self, session, hierarchy):
        assert ch.resolve_care_organisation_guid(session, 'nope') is None


# --- validation ------------------------------------------------------------

class TestValidation:
    def test_valid_caregiver(self, session, hierarchy):
        ch.validate_care_hierarchy(session, hierarchy['caregiver'])  # no raise

    def test_valid_unit(self, session, hierarchy):
        ch.validate_care_hierarchy(session, hierarchy['unit_a'])  # no raise

    def test_self_parent_rejected(self, session, hierarchy):
        u = hierarchy['unit_a']
        u.parent_caregiver_guid = u.guid
        with pytest.raises(ch.HierarchyError, match='its own parent'):
            ch.validate_care_hierarchy(session, u)

    def test_nonexistent_parent_rejected(self, session, hierarchy):
        u = hierarchy['unit_a']
        u.parent_caregiver_guid = 'does-not-exist'
        with pytest.raises(ch.HierarchyError, match='does not exist'):
            ch.validate_care_hierarchy(session, u)

    def test_external_parent_rejected(self, session, hierarchy):
        u = hierarchy['unit_a']
        u.parent_caregiver_guid = hierarchy['partner'].guid
        with pytest.raises(ch.HierarchyError, match='external partner'):
            ch.validate_care_hierarchy(session, u)

    def test_unit_under_unit_rejected(self, session, hierarchy):
        # Point unit_b at unit_a (which is itself a unit) -> 3-level, illegal.
        b = hierarchy['unit_b']
        b.parent_caregiver_guid = hierarchy['unit_a'].guid
        with pytest.raises(ch.HierarchyError, match='2-level only'):
            ch.validate_care_hierarchy(session, b)

    def test_scan_violations_clean(self, session, hierarchy):
        assert ch.scan_violations(session) == []

    def test_scan_violations_detects(self, session, hierarchy):
        bad = hierarchy['unit_b']
        bad.parent_caregiver_guid = hierarchy['unit_a'].guid
        session.commit()
        problems = ch.scan_violations(session)
        assert len(problems) == 1
        assert problems[0]['name'] == 'UAS Endokrin'
        assert '2-level only' in problems[0]['problem']
