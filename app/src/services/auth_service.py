"""Auth service — login, password verification, access blob assembly."""
import bcrypt

from src.db import get_db
from src.models.user import User
from src.models.patient import Patient
from src.models.professional import Professional
from src.models.membership import Membership
from src.models.user_organisation import UserOrganisation


def verify_password(plain_password, password_hash):
    """Verify a plain password against a bcrypt hash."""
    return bcrypt.checkpw(
        plain_password.encode('utf-8'),
        password_hash.encode('utf-8'),
    )


def hash_password(plain_password):
    """Hash a password with bcrypt."""
    return bcrypt.hashpw(
        plain_password.encode('utf-8'),
        bcrypt.gensalt(),
    ).decode('utf-8')


def authenticate_user(email, password, session):
    """Authenticate by email + password. Returns User or None."""
    user = session.query(User).filter_by(email=email).first()
    if user is None:
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user


def build_access_blob(user, session, session_id=None):
    """Build the access blob for /api/auth/me response.

    Returns dict with:
    - user_guid, email, user_type, is_su_admin, session_id
    - Patient: patient_guid, organisation_guid, in_registry, registries
    - Professional: professional_guid, professional_role, organization_ids, groups, effective_phases

    ``session_id`` is the ``sid`` claim from the JWT carrying the
    request — stable across every /me / /me/service call validated
    against the same token (ticket #191). Consumers forward it as
    ``X-Operator-Session-Id`` on onward calls so downstream audit logs
    can correlate "all reads under this operator session" for the
    Lag (2022:913) chain-of-custody requirement.
    """
    blob = {
        'user_guid': user.guid,
        'email': user.email,
        'user_type': user.user_type,
        'is_su_admin': user.is_su_admin,
        # Ticket #43: so every page load can detect a pending SU-triggered reset
        # (sibling services honour this via /me/service and can redirect too).
        'must_change_password': bool(getattr(user, 'force_change_on_next_login', False)),
        # Ticket #191: stable per-session correlation id. None for legacy
        # tokens issued before #191 landed (no `sid` claim).
        'session_id': session_id,
    }

    if user.user_type == 'patient':
        patient = session.query(Patient).filter_by(user_id=user.id).first()
        if patient:
            blob['patient_guid'] = patient.guid
            blob['organisation_guid'] = patient.organisation_guid
            blob['in_registry'] = patient.in_registry
            blob['registries'] = patient.registries or []
            blob['fhir_resource_type'] = Patient.FHIR_RESOURCE_TYPE

    elif user.user_type == 'professional':
        professional = session.query(Professional).filter_by(user_id=user.id).first()
        if professional:
            blob['professional_guid'] = professional.guid
            blob['professional_role'] = professional.professional_role
            blob['fhir_resource_type'] = Professional.FHIR_RESOURCE_TYPE

        # Organisation IDs (many-to-many)
        user_orgs = session.query(UserOrganisation).filter_by(user_guid=user.guid).all()
        blob['organization_ids'] = [uo.organisation_guid for uo in user_orgs]
        blob['organisation_warning'] = len(user_orgs) == 0

        # Groups and phases are independent criteria (#57).
        #
        # `groups` reports the user's approved group memberships for
        # services that want to filter by group identity. `category` is
        # a free-form label on each group entry — it does NOT grant phase
        # access. (Field renamed from `group_type` in #60.)
        #
        # `effective_phases` comes exclusively from the `UserPhase` table
        # (SU grants, #46). A user who is an approved member of an
        # "analysis"-category group does NOT get the `analysis` phase for
        # free; SU must grant it explicitly.
        memberships = session.query(Membership).filter_by(
            user_guid=user.guid, status='approved'
        ).all()

        groups = []
        for m in memberships:
            from src.models.group import Group
            group = session.query(Group).filter_by(guid=m.group_guid).first()
            if group:
                groups.append({
                    'group_guid': group.guid,
                    'group_name': group.name,
                    'category': group.category,  # free-form label only (#60)
                    'status': m.status,
                    'is_admin': m.is_admin,
                })

        # Direct UserPhase grants are the sole source of phase access.
        # Wrapped in try/except so a missing user_phases table (e.g.
        # migration skipped on a stale environment) degrades gracefully to
        # "no phases" — never 500s the access blob. After #57 the fallback
        # means the user has NO phases, not group-derived phases.
        effective_phases = set()
        try:
            from src.models.user_phase import UserPhase
            direct_phases = session.query(UserPhase).filter_by(
                user_guid=user.guid).all()
            for up in direct_phases:
                effective_phases.add(up.phase)
        except Exception:
            pass

        blob['groups'] = groups
        blob['effective_phases'] = sorted(effective_phases)

    return blob
