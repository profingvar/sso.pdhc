"""Affiliation queries, backfill, and blob assembly.

Reform S3 (#399) + S5 (#401) + S6 (#402). One module for:
  - backfill_affiliations_from_user_organisations (S3)
  - resolve_session_phases (S5, Option C runtime intersection)
  - build_affiliations_for_blob (S6)
"""
from src.models.affiliation import Affiliation
from src.models.organisation import Organisation
from src.models.professional import Professional
from src.models.role import Role, LEGACY_ROLE_MAP, seed_roles
from src.models.user import User
from src.models.user_organisation import UserOrganisation
from src.models.user_phase import PHASE_NAMES
from src.services import care_hierarchy as ch


# Phases that are NOT gated by role — they pass the S5 intersection if granted.
# The Plan (planning) phase touches no patient data (v3 spec §3.7).
ORTHOGONAL_PHASES = frozenset({'planning'})


# --- queries ---------------------------------------------------------------

def active_affiliations(session, person_guid):
    return session.query(Affiliation).filter_by(
        person_guid=person_guid, status='active').all()


def get_affiliation(session, affiliation_guid):
    return session.query(Affiliation).filter_by(guid=affiliation_guid).first()


# --- S5: Option C runtime phase intersection -------------------------------

def resolve_session_phases(granted_phases, role):
    """session_phases = granted ∩ (role.permitted_phases ∪ orthogonal).

    granted_phases: iterable of phase names the person was granted (UserPhase).
    role: the active affiliation's Role (or None -> only orthogonal phases pass).
    Returns a sorted list.
    """
    permitted = set(ORTHOGONAL_PHASES)
    if role is not None:
        permitted |= set(role.permitted_phases or [])
    return sorted(p for p in set(granted_phases) if p in permitted)


# --- S6: blob assembly -----------------------------------------------------

def build_affiliations_for_blob(session, person_guid):
    """Return the affiliations[] list for the access blob (S6).

    Each entry: affiliation_guid, care_unit_guid/name, care_organisation_guid/
    name, role, role_guid, is_admin, research_project_guids.
    """
    out = []
    for aff in active_affiliations(session, person_guid):
        unit = session.query(Organisation).filter_by(
            guid=aff.care_unit_guid).first()
        role = session.query(Role).filter_by(guid=aff.role_guid).first()
        care_org_guid = unit.care_organisation_guid if unit else None
        out.append({
            'affiliation_guid': aff.guid,
            'care_unit_guid': aff.care_unit_guid,
            'care_unit_name': unit.name if unit else None,
            'care_organisation_guid': care_org_guid,
            'care_organisation_name': ch.care_organisation_name(
                session, care_org_guid) if care_org_guid else None,
            'role': role.code if role else None,
            'role_guid': aff.role_guid,
            'is_admin': aff.is_admin,
            'research_project_guids': aff.research_project_guids or [],
        })
    return out


# --- S3: backfill ----------------------------------------------------------

def backfill_affiliations_from_user_organisations(session, dry_run=False):
    """Create an Affiliation for every UserOrganisation that lacks one.

    Role seeded from the person's legacy Professional.professional_role via
    LEGACY_ROLE_MAP (decision 2026-07-03). A user with no Professional row (or
    an unmapped legacy role) defaults to 'other_care' and is flagged.

    Returns a summary dict.
    """
    roles = seed_roles(session)  # ensure the registry exists; {code: Role}

    created = 0
    skipped = 0
    flagged = []  # rows that fell back to a default role

    for uo in session.query(UserOrganisation).all():
        # Skip if an affiliation for this (person, unit) already exists in ANY
        # role — the backfill is idempotent.
        existing = session.query(Affiliation).filter_by(
            person_guid=uo.user_guid,
            care_unit_guid=uo.organisation_guid,
        ).first()
        if existing:
            skipped += 1
            continue

        # Resolve the legacy role.
        user = session.query(User).filter_by(guid=uo.user_guid).first()
        legacy = None
        if user is not None:
            prof = session.query(Professional).filter_by(
                user_id=user.id).first()
            legacy = prof.professional_role if prof else None
        role_code = LEGACY_ROLE_MAP.get(legacy, 'other_care')
        if legacy not in LEGACY_ROLE_MAP:
            flagged.append({
                'person_guid': uo.user_guid,
                'care_unit_guid': uo.organisation_guid,
                'legacy_role': legacy,
                'assigned': role_code,
            })
        role = roles[role_code]

        if not dry_run:
            session.add(Affiliation(
                person_guid=uo.user_guid,
                care_unit_guid=uo.organisation_guid,
                role_guid=role.guid,
                status='active',
            ))
        created += 1

    if not dry_run:
        session.commit()

    return {
        'created': created,
        'skipped_existing': skipped,
        'flagged_default_role': flagged,
        'dry_run': dry_run,
    }
