"""S9 (#411) — activation gate + server-side completeness.

Decision 2026-07-03: the person triggers (sign-on request), the SU
assigns. A professional cannot be activated — and an unactivated
professional gets a no-access blob — until the profile is COMPLETE:

  1. >=1 ACTIVE affiliation (CareUnit + Role — guaranteed per row by the
     Affiliation schema, but the person must hold at least one).
  2. At least one granted phase that the held role(s) can actually use:
     granted UserPhase ∩ (union of affiliation roles' permitted_phases
     ∪ {planning}) must be non-empty — an affiliation whose role can
     never pass the S5 session intersection is not "useful".
  3. Every researcher affiliation carries >=1 research_project_guid
     (ResDB #400) — a researcher with no project can read nothing under
     the #422 consent join, so activating one is a configuration error.

Completeness is enforced HERE (server-side), not in the UI; the guided
SU form merely renders the same `missing` list this module computes.
"""
from src.models.affiliation import Affiliation
from src.models.role import Role
from src.models.user_phase import UserPhase
from src.services.affiliation_service import ORTHOGONAL_PHASES


# Machine-readable missing-element codes (stable API surface for the UI).
MISSING_AFFILIATION = 'no_active_affiliation'
MISSING_USEFUL_PHASE = 'no_useful_phase_grant'
MISSING_RESEARCH_PROJECTS = 'researcher_affiliation_missing_projects'


def is_activated(user) -> bool:
    """The blob-side gate. SU admins administer regardless of status;
    patients are outside S9 (their capture lives in ips, D1 #404)."""
    if user.is_su_admin or user.user_type != 'professional':
        return True
    return getattr(user, 'status', 'active') == 'active'


def completeness(session, user) -> dict:
    """Compute the guided-form checklist for one professional.

    Returns::

        {
          "status": "pending" | "active" | "suspended",
          "complete": bool,           # may the profile be activated?
          "missing": [<codes>],       # empty when complete
          "affiliations_n": int,
          "granted_phases": [...],
          "useful_phases": [...],     # granted ∩ role-permitted ∪ planning
        }
    """
    affs = session.query(Affiliation).filter_by(
        person_guid=user.guid, status='active').all()

    granted = {up.phase for up in session.query(UserPhase).filter_by(
        user_guid=user.guid).all()}

    permitted = set(ORTHOGONAL_PHASES)
    missing = []
    for aff in affs:
        role = session.query(Role).filter_by(guid=aff.role_guid).first()
        if role is not None:
            permitted |= set(role.permitted_phases or [])
            if role.code == 'researcher' and not (aff.research_project_guids or []):
                if MISSING_RESEARCH_PROJECTS not in missing:
                    missing.append(MISSING_RESEARCH_PROJECTS)

    useful = sorted(granted & permitted)

    if not affs:
        missing.insert(0, MISSING_AFFILIATION)
    if not useful:
        missing.append(MISSING_USEFUL_PHASE)

    return {
        'status': getattr(user, 'status', 'active'),
        'complete': not missing,
        'missing': missing,
        'affiliations_n': len(affs),
        'granted_phases': sorted(granted),
        'useful_phases': useful,
    }
