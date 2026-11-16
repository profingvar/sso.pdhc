"""The employment rule, in ONE place (operator 2026-11-13).

    a person is EMPLOYED by one or more vårdgivare, and may only be AFFILIATED
    to a care unit under one of them.

Pure lookups over the session; the admin routes, the access blob, the
completeness check and the backfill script all call these, so the rule cannot
be implemented twice and drift.
"""
from src.models.employment import Employment
from src.models.organisation import Organisation


def caregiver_of(session, org_guid):
    """The vårdgivare an organisation belongs to (a unit -> its parent; a
    vårdgivare -> itself); None for an unknown or external organisation."""
    org = session.query(Organisation).filter_by(guid=org_guid).first()
    return org.care_organisation_guid if org is not None else None


def employer_guids(session, person_guid):
    return {e.caregiver_guid for e in
            session.query(Employment).filter_by(person_guid=person_guid).all()}


def affiliation_outside_employment(session, person_guid, care_unit_guid):
    """None when the person may be affiliated to this unit, else the reason.

    The unit's vårdgivare must be one the person is employed by. A vårdgivare
    with no units is its own unit, so affiliating to it directly needs that
    same employment.
    """
    caregiver = caregiver_of(session, care_unit_guid)
    if caregiver is None:
        return "the care unit is unknown or not part of the care hierarchy"
    if caregiver not in employer_guids(session, person_guid):
        name = None
        org = session.query(Organisation).filter_by(guid=caregiver).first()
        if org is not None:
            name = org.name
        return (f"the person is not employed by this unit's vårdgivare "
                f"({name or caregiver}); add the employment first")
    return None


def employments_for_blob(session, person_guid):
    out = []
    for e in session.query(Employment).filter_by(person_guid=person_guid).all():
        org = session.query(Organisation).filter_by(guid=e.caregiver_guid).first()
        out.append({'caregiver_guid': e.caregiver_guid,
                    'caregiver_name': org.name if org else None})
    return sorted(out, key=lambda x: x['caregiver_name'] or '')


# --- the two writes, shared by the JSON API and the SU admin forms ----------

class EmploymentRefused(Exception):
    def __init__(self, status, error, message, **extra):
        super().__init__(message)
        self.status, self.error, self.message, self.extra = status, error, message, extra


def add_employment(session, person_guid, caregiver_guid, by_guid=None):
    """Employ a professional by a VÅRDGIVARE. Raises EmploymentRefused."""
    from src.models.user import User
    user = session.query(User).filter_by(guid=person_guid).first()
    if user is None:
        raise EmploymentRefused(404, 'not_found', 'User not found')
    if user.user_type != 'professional':
        raise EmploymentRefused(400, 'invalid_request', 'Employment applies to professionals only')
    org = session.query(Organisation).filter_by(guid=caregiver_guid).first() if caregiver_guid else None
    if org is None or org.is_external:
        raise EmploymentRefused(400, 'invalid_request', 'caregiver_guid must be an internal organisation')
    if org.parent_caregiver_guid:
        raise EmploymentRefused(400, 'invalid_request',
                                f"{org.name} is a vårdenhet, not a vårdgivare; a person is "
                                f"employed by the vårdgivare and AFFILIATED to its units")
    dup = session.query(Employment).filter_by(person_guid=person_guid,
                                              caregiver_guid=caregiver_guid).first()
    if dup is not None:
        raise EmploymentRefused(409, 'conflict', 'employment already exists',
                                employment_guid=dup.guid)
    emp = Employment(person_guid=person_guid, caregiver_guid=caregiver_guid,
                     created_by_guid=by_guid)
    session.add(emp)
    session.flush()
    return emp, org


def remove_employment(session, person_guid, employment_guid):
    """Refused while an ACTIVE affiliation depends on it. Returns the
    caregiver guid that was removed. Raises EmploymentRefused."""
    from src.models.affiliation import Affiliation
    emp = session.query(Employment).filter_by(guid=employment_guid,
                                              person_guid=person_guid).first()
    if emp is None:
        raise EmploymentRefused(404, 'not_found', 'Employment not found')
    dependent = [a.guid for a in session.query(Affiliation).filter_by(
                     person_guid=person_guid, status='active').all()
                 if caregiver_of(session, a.care_unit_guid) == emp.caregiver_guid]
    if dependent:
        raise EmploymentRefused(409, 'conflict',
                                'remove the affiliations under this vårdgivare first',
                                affiliation_guids=dependent)
    caregiver = emp.caregiver_guid
    session.delete(emp)
    return caregiver


# --- one-off backfill for the main rule (2026-11-13) ------------------------

def backfill_employments(session, dry_run=True, by_guid=None):
    """Create the employments the existing data already implies, so the
    strict rule does not lock anyone out on deploy.

    For every PROFESSIONAL, one employment per vårdgivare that is
      (a) the vårdgivare above any of their affiliations (all statuses, so a
          suspended affiliation can be reactivated), or
      (b) a vårdgivare they hold a legacy MEMBERSHIP of.
    A membership of a vårdenhet does NOT create one: membership was never the
    access basis next to an affiliation, and employment is a decision. Those
    are reported, as are references to external or missing organisations.
    Idempotent: an existing employment is skipped.
    """
    from src.models.affiliation import Affiliation
    from src.models.user import User
    from src.models.user_organisation import UserOrganisation

    orgs = {o.guid: o for o in session.query(Organisation).all()}
    out = {'dry_run': dry_run, 'created': [], 'skipped_existing': 0,
           'unit_memberships_not_used': [], 'flagged': []}
    for user in session.query(User).filter_by(user_type='professional').all():
        wanted = {}                                  # caregiver_guid -> reason
        for a in session.query(Affiliation).filter_by(person_guid=user.guid).all():
            cg = caregiver_of(session, a.care_unit_guid)
            org = orgs.get(cg)
            if org is None or org.is_external:
                out['flagged'].append({'person_guid': user.guid, 'org_guid': a.care_unit_guid,
                                       'why': 'affiliation to a missing or external organisation'})
                continue
            wanted.setdefault(cg, 'affiliation')
        for uo in session.query(UserOrganisation).filter_by(user_guid=user.guid).all():
            org = orgs.get(uo.organisation_guid)
            if org is None or org.is_external:
                out['flagged'].append({'person_guid': user.guid, 'org_guid': uo.organisation_guid,
                                       'why': 'membership of a missing or external organisation'})
            elif org.parent_caregiver_guid:
                out['unit_memberships_not_used'].append(
                    {'person_guid': user.guid, 'org_guid': org.guid})
            else:
                wanted.setdefault(org.guid, 'membership')
        have = employer_guids(session, user.guid)
        for cg, reason in sorted(wanted.items()):
            if cg in have:
                out['skipped_existing'] += 1
                continue
            out['created'].append({'person_guid': user.guid, 'caregiver_guid': cg,
                                   'caregiver_name': orgs[cg].name, 'from': reason})
            if not dry_run:
                session.add(Employment(person_guid=user.guid, caregiver_guid=cg,
                                       created_by_guid=by_guid))
    if dry_run:
        session.rollback()
    else:
        session.commit()
    return out


# --- approving an access request (#863, operator decision A, 2026-11-16) ----

def employ_for_access_request(session, person_guid, org_guid, by_guid=None):
    """Approving "I work at organisation X" employs the person by X's
    vårdgivare (X itself, or its parent if X is a vårdenhet).

    It used to create a legacy MEMBERSHIP, which grants nothing: production
    emits no organization_ids (SSO_EMIT_LEGACY_BLOB_FIELDS=false) and every
    request-phase consumer reads affiliations only. An employment grants no
    patient access either — it decides WHERE the person may be affiliated —
    so the SU still assigns the affiliation (unit + role) and activates in S9.

    Returns (employment_or_None, note). Never raises: an approval must not
    fail because the hint names an external or unknown organisation; the
    note says why nothing was created.
    """
    if not org_guid:
        return None, "the request named no organisation"
    caregiver = caregiver_of(session, org_guid)
    org = session.query(Organisation).filter_by(guid=caregiver).first() if caregiver else None
    if org is None or org.is_external:
        return None, "the requested organisation is unknown or external; no employment created"
    if caregiver in employer_guids(session, person_guid):
        return None, f"already employed by {org.name}"
    try:
        emp, org = add_employment(session, person_guid, caregiver, by_guid)
    except EmploymentRefused as e:
        return None, e.message
    return emp, f"employed by {org.name}"
