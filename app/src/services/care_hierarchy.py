"""CareOrganisation / CareUnit hierarchy queries + validation.

Reform S1 (ticket #397). The legal 2-level PDL hierarchy
(vårdgivare -> vårdenhet) lives on the single self-referential
`organisations` table via parent_caregiver_guid (#187). This module exposes
the CareOrganisation / CareUnit views, name resolution, and the 2-level
integrity guard that the reform's affiliation model (S3, #399) and access
blob (S6, #402) build on.

No physical table split (see the #96 FHIR-unification note on the model):
  CareOrganisation (data holder) = internal org, parent_caregiver_guid NULL
  CareUnit         (clinic)      = internal org, parent_caregiver_guid set
  external partner               = neither (is_external True)
"""
from src.models.organisation import Organisation


class HierarchyError(ValueError):
    """Raised when an Organisation violates the 2-level care hierarchy."""


# --- queries ---------------------------------------------------------------

def get_organisation(session, guid):
    return session.query(Organisation).filter_by(guid=guid).first()


def list_care_organisations(session):
    """All CareOrganisations (internal orgs at the top of the hierarchy)."""
    return session.query(Organisation).filter(
        Organisation.is_external.is_(False),
        Organisation.parent_caregiver_guid.is_(None),
    ).all()


def list_care_units(session):
    """All CareUnits (internal orgs that belong to a CareOrganisation)."""
    return session.query(Organisation).filter(
        Organisation.is_external.is_(False),
        Organisation.parent_caregiver_guid.isnot(None),
    ).all()


def care_units_for_organisation(session, care_organisation_guid):
    """Every CareUnit belonging to the given CareOrganisation."""
    return session.query(Organisation).filter(
        Organisation.is_external.is_(False),
        Organisation.parent_caregiver_guid == care_organisation_guid,
    ).all()


# --- resolution (used by S6 blob assembly) ---------------------------------

def resolve_care_organisation_guid(session, care_unit_guid):
    """The CareOrganisation guid a CareUnit belongs to (or the org's own guid
    if the guid is already a CareOrganisation). None if not found / external."""
    org = get_organisation(session, care_unit_guid)
    return org.care_organisation_guid if org else None


def care_unit_name(session, care_unit_guid):
    org = get_organisation(session, care_unit_guid)
    return org.name if org else None


def care_organisation_name(session, care_organisation_guid):
    org = get_organisation(session, care_organisation_guid)
    return org.name if org else None


# --- validation (2-level integrity guard) ----------------------------------

def validate_care_hierarchy(session, org):
    """Enforce the 2-level rule for an Organisation about to be saved.

    - External partners are exempt (not part of the care hierarchy).
    - A CareOrganisation (parent NULL) is always valid.
    - A CareUnit (parent set) must: not point at itself; point at an existing
      org; and that parent must itself be a top-level CareOrganisation — no
      unit-under-unit nesting and no external parent.

    Raises HierarchyError on violation. Call from the create/edit path
    (SU-only, S8) before commit.
    """
    if org.is_external or org.parent_caregiver_guid is None:
        return
    if org.parent_caregiver_guid == org.guid:
        raise HierarchyError('a care unit cannot be its own parent caregiver')
    parent = get_organisation(session, org.parent_caregiver_guid)
    if parent is None:
        raise HierarchyError(
            f'parent caregiver {org.parent_caregiver_guid} does not exist')
    if parent.is_external:
        raise HierarchyError(
            "a care unit's parent must be a care organisation, "
            "not an external partner")
    if parent.parent_caregiver_guid is not None:
        raise HierarchyError(
            'care hierarchy is 2-level only: a care unit\'s parent must be a '
            'top-level care organisation, not another care unit')


def scan_violations(session):
    """Read-only hygiene scan of the whole table. Returns a list of
    {guid, name, problem} for every org that violates the 2-level rule.
    Used by scripts/verify_care_hierarchy.py."""
    problems = []
    for org in session.query(Organisation).filter(
            Organisation.is_external.is_(False)).all():
        try:
            validate_care_hierarchy(session, org)
        except HierarchyError as e:
            problems.append({
                'guid': org.guid, 'name': org.name, 'problem': str(e),
            })
    return problems
