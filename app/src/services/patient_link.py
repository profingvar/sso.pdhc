"""Linking an SSO patient account to an eps/ips patient (#1040).

Two entrances, ONE link step (`_set_link`):

  1. invited by care   `create_invite` -> the patient opens the one-time link
                       -> `accept_invite` creates the account ALREADY linked;
  2. self-registered   the patient makes a one-time `create_link_code`, gives
                       it to the care unit, and a professional there calls
                       `redeem_link_code` with the code and the ips patient guid.

Who may link. A professional who is ACTIVATED and holds an active affiliation
to the care unit in question, or an SU admin. "The care unit in question" is
the unit named on the invite, or, for a link code, the unit the patient chose
when registering (`Patient.organisation_guid`).

The patient guid is CHECKED AGAINST EPS (`_eps_check`, services/eps_client.py)
at each of the three points where a guid enters: invite creation, link-code
redemption, SU relink. The guid must exist in eps and the patient must be
listed at the care unit the link is made for (for an SU relink the unit check
applies when a unit is named). It fails closed and by name; no link, invite or
history row is written on a refusal. The check runs only AFTER the caller has
passed the affiliation gate, so nobody outside the gate learns from it whether
a guid exists. A wrong guid shows one patient another patient's tasks, which
is also why a link is never editable by the unit: changing it is SU only.

Accepting an invite does not ask eps again: the guid was checked when the
invite was created, at most seven days earlier.

One account <-> one patient, in both directions, enforced by unique
constraints on `patient_links` as well as checked here.
"""
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from src.models.patient import Patient
from src.models.patient_invite_detail import PatientInviteDetail
from src.models.patient_link import (
    PatientInvite, PatientLink, PatientLinkCode, PatientLinkHistory,
)
from src.models.user import User
from flask import current_app

from src.services import eps_client, secret_store
from src.services.activation_service import is_activated
from src.services.affiliation_service import active_affiliations
from src.services.audit_log import audit
from src.services.care_hierarchy import enrollable_care_units

INVITE_TTL = timedelta(days=7)
LINK_CODE_TTL = timedelta(days=7)
#: No 0/O, 1/I/L, U: the code is read aloud or copied by hand.
LINK_CODE_ALPHABET = 'ABCDEFGHJKMNPQRSTVWXYZ23456789'
LINK_CODE_LENGTH = 8
#: #1050: wrong personnummer typed on one invite before it closes.
MAX_PNR_FAILURES = 5
NAME_MAX = 100


class LinkRefused(Exception):
    def __init__(self, status, code, message):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def _utcnow():
    return datetime.now(timezone.utc)


def _aware(dt):
    return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _iso(dt):
    dt = _aware(dt)
    return dt.isoformat() if dt else None


def normalise_guid(value, field='ips_patient_guid'):
    """Canonical lower-case GUID, or LinkRefused(400)."""
    try:
        return str(uuid.UUID(str(value or '').strip()))
    except (ValueError, AttributeError):
        raise LinkRefused(400, 'invalid_request', f'{field} must be a GUID')


def normalise_code(value):
    return ''.join(ch for ch in (value or '').upper() if ch.isalnum())


def format_code(code):
    return f'{code[:4]}-{code[4:]}'


# --- the blob ---------------------------------------------------------------

def current_link(session, user_guid):
    return session.query(PatientLink).filter_by(user_guid=user_guid).first()


def link_for_blob(session, user):
    """`patient_link` for the access blob: the object, or None until linked."""
    link = current_link(session, user.guid)
    if link is None:
        return None
    return {
        'ips_patient_guid': link.ips_patient_guid,
        'linked_at': _iso(link.linked_at),
        'linked_by': link.linked_by_user_guid,
        # #1049: the unit the patient chose at registration, the unit of the
        # invite, or (link code) the unit the patient registered at. Null for
        # an SU relink that named none.
        'care_unit_guid': link.care_unit_guid,
    }


# --- who may link -----------------------------------------------------------

def actor_units(session, actor):
    """Care-unit guids the actor may link patients for (active affiliations of
    an activated professional). Empty for everybody else, SU included: an SU's
    right does not come from a unit."""
    if actor.user_type != 'professional' or not is_activated(actor):
        return set()
    return {a.care_unit_guid for a in active_affiliations(session, actor.guid)}


def _may_act_for_unit(session, actor, care_unit_guid):
    if actor.is_su_admin:
        return True
    return bool(care_unit_guid) and care_unit_guid in actor_units(session, actor)


def _require_linker(actor):
    if actor.user_type != 'professional' and not actor.is_su_admin:
        raise LinkRefused(403, 'forbidden', 'Professional access required')
    if getattr(actor, 'force_change_on_next_login', False):
        raise LinkRefused(403, 'must_change_password',
                          'Change your password before linking patients')


# --- the eps check ----------------------------------------------------------

_EPS_ANSWERS = {
    eps_client.NOT_CONFIGURED: (503, 'The patient register (eps) is not configured on this server'),
    eps_client.UNAVAILABLE: (503, 'The patient register (eps) did not answer. Nothing was linked; try again'),
    eps_client.PATIENT_NOT_FOUND: (404, 'No patient with this guid in eps'),
    eps_client.PATIENT_NOT_AT_UNIT: (409, 'This patient is not listed at this care unit in eps'),
}


def _eps_check(actor, ips_patient_guid, care_unit_guid, where, ip=None):
    """Ask eps; turn a refusal into an audited LinkRefused. Call it only after
    the actor has passed the affiliation / SU gate for `care_unit_guid`."""
    try:
        outcome = eps_client.verify_patient(current_app.config, ips_patient_guid,
                                            care_unit_guid)
    except eps_client.EpsRefusal as e:
        audit('patient_link_refused', user_guid=actor.guid,
              detail={'reason': e.code, 'where': where, 'eps_detail': e.detail,
                      'ips_patient_guid': ips_patient_guid,
                      'care_unit_guid': care_unit_guid}, ip=ip)
        status, message = _EPS_ANSWERS[e.code]
        raise LinkRefused(status, e.code, message)
    if outcome == 'skipped':
        audit('patient_link_eps_check_skipped', user_guid=actor.guid,
              detail={'where': where, 'ips_patient_guid': ips_patient_guid}, ip=ip)


# --- the one link step ------------------------------------------------------

def _set_link(session, *, user, ips_patient_guid, actor_guid, method,
              care_unit_guid=None, reason=None, ip=None):
    """Create the link. Refuses if the account or the patient is linked."""
    if current_link(session, user.guid) is not None:
        raise LinkRefused(409, 'account_already_linked',
                          'This account is already linked to a patient')
    if session.query(PatientLink).filter_by(
            ips_patient_guid=ips_patient_guid).first() is not None:
        raise LinkRefused(409, 'patient_already_linked',
                          'This patient is already linked to an account')
    link = PatientLink(user_guid=user.guid, ips_patient_guid=ips_patient_guid,
                       care_unit_guid=care_unit_guid,
                       linked_by_user_guid=actor_guid, method=method,
                       linked_at=_utcnow())
    session.add(link)
    session.add(PatientLinkHistory(
        event='link', user_guid=user.guid, ips_patient_guid=ips_patient_guid,
        care_unit_guid=care_unit_guid, actor_user_guid=actor_guid,
        method=method, reason=reason))
    session.flush()
    audit('patient_link', user_guid=actor_guid,
          detail={'account_user_guid': user.guid,
                  'ips_patient_guid': ips_patient_guid,
                  'care_unit_guid': care_unit_guid, 'method': method}, ip=ip)
    return link


# --- entrance 1: invited by care --------------------------------------------

def _invite_names(given_name, family_name):
    """(given, family) both set, or (None, None). One without the other is
    a 400: the invite page shows the name, and half a name is a wrong one."""
    given = (given_name or '').strip() if isinstance(given_name, str) else ''
    family = (family_name or '').strip() if isinstance(family_name, str) else ''
    if bool(given) != bool(family):
        raise LinkRefused(400, 'invalid_request',
                          'given_name and family_name go together')
    if len(given) > NAME_MAX or len(family) > NAME_MAX:
        raise LinkRefused(400, 'invalid_request', f'names are at most {NAME_MAX} characters')
    return (given, family) if given else (None, None)


def invite_detail(session, invite):
    return session.query(PatientInviteDetail).filter_by(invite_guid=invite.guid).first()


def create_invite(session, actor, *, ips_patient_guid, care_unit_guid,
                  secret_key, given_name=None, family_name=None, ip=None):
    """Returns (invite row, token). The token is returned ONCE and never
    stored; only its keyed hash is.

    #1050: `given_name`/`family_name` (optional, together) are kept beside
    the invite in `patient_invite_details`, shown on the invite page and
    stored as the account's profile when the patient accepts."""
    _require_linker(actor)
    ips_patient_guid = normalise_guid(ips_patient_guid)
    given_name, family_name = _invite_names(given_name, family_name)
    care_unit_guid = (care_unit_guid or '').strip()
    if not care_unit_guid:
        units = actor_units(session, actor)
        if len(units) != 1:
            raise LinkRefused(400, 'invalid_request', 'care_unit_guid is required')
        care_unit_guid = next(iter(units))
    if not _may_act_for_unit(session, actor, care_unit_guid):
        audit('patient_invite_refused', user_guid=actor.guid,
              detail={'reason': 'not_affiliated', 'care_unit_guid': care_unit_guid}, ip=ip)
        raise LinkRefused(403, 'not_affiliated',
                          'You are not affiliated to this care unit')
    if care_unit_guid not in {o.guid for o in enrollable_care_units(session)}:
        raise LinkRefused(404, 'not_found', 'Care unit not found')
    if session.query(PatientLink).filter_by(
            ips_patient_guid=ips_patient_guid).first() is not None:
        raise LinkRefused(409, 'patient_already_linked',
                          'This patient is already linked to an account')
    _eps_check(actor, ips_patient_guid, care_unit_guid, 'invite', ip=ip)
    token = secrets.token_urlsafe(32)
    invite = PatientInvite(
        token_hash=secret_store.keyed_hash(secret_key, 'patient-invite', token),
        ips_patient_guid=ips_patient_guid, care_unit_guid=care_unit_guid,
        created_by_user_guid=actor.guid, expires_at=_utcnow() + INVITE_TTL)
    session.add(invite)
    session.flush()
    session.add(PatientInviteDetail(invite_guid=invite.guid, given_name=given_name,
                                    family_name=family_name, pnr_failures=0))
    session.flush()
    audit('patient_invite_create', user_guid=actor.guid,
          detail={'invite_guid': invite.guid, 'ips_patient_guid': ips_patient_guid,
                  'care_unit_guid': care_unit_guid}, ip=ip)
    return invite, token


def revoke_invite(session, actor, invite_guid, ip=None):
    _require_linker(actor)
    invite = session.query(PatientInvite).filter_by(guid=invite_guid or '').first()
    # Same answer for "does not exist" and "not yours to see".
    if invite is None or not _may_act_for_unit(session, actor, invite.care_unit_guid):
        raise LinkRefused(404, 'not_found', 'Invite not found')
    if invite.used_at is not None:
        raise LinkRefused(409, 'invite_used', 'Invite has already been used')
    if invite.revoked_at is None:
        invite.revoked_at = _utcnow()
        invite.revoked_by_user_guid = actor.guid
        audit('patient_invite_revoke', user_guid=actor.guid,
              detail={'invite_guid': invite.guid}, ip=ip)
    return invite


def find_valid_invite(session, token, secret_key):
    """The invite this token opens, or None (unknown, used, revoked, expired
    — one answer for all four, so the page tells a guesser nothing)."""
    if not token:
        return None
    invite = session.query(PatientInvite).filter_by(
        token_hash=secret_store.keyed_hash(secret_key, 'patient-invite', token)).first()
    if invite is None or invite.used_at is not None or invite.revoked_at is not None:
        return None
    if _aware(invite.expires_at) <= _utcnow():
        return None
    detail = invite_detail(session, invite)
    if detail is not None and (detail.pnr_failures or 0) >= MAX_PNR_FAILURES:
        return None
    return invite


PNR_MISMATCH_TEXT = ('Personnumret stämmer inte med inbjudan. Kontrollera '
                     'siffrorna och försök igen.')
TRY_LATER_TEXT = 'Det gick inte att kontrollera uppgifterna just nu. Försök igen om en stund.'


def check_invite_personnummer(session, invite, personnummer, ip=None):
    """#1050: the personnummer typed on the invite page must be the eps
    patient's. Raises EnrolmentRefused (a plain form error) otherwise.

    Order: shape and the pilot's synthetic rule first (no eps call for a
    number that could never be accepted), then eps. A mismatch is counted on
    the invite; at MAX_PNR_FAILURES the invite closes. Audited WITHOUT the
    number. Commits the counter itself, because the caller's request ends in
    an error and must not roll the count back."""
    from src.services.patient_enrolment import EnrolmentRefused, _check_personnummer
    errors = []
    normalised = _check_personnummer(personnummer, current_app.config, errors)
    if errors:
        raise EnrolmentRefused(400, 'validation_error', errors)
    try:
        match = eps_client.identifier_match(current_app.config,
                                            invite.ips_patient_guid, normalised)
    except eps_client.EpsRefusal as e:
        audit('patient_invite_pnr_check_failed',
              detail={'invite_guid': invite.guid, 'reason': e.code,
                      'eps_detail': e.detail}, ip=ip)
        if e.code == eps_client.INVALID_PERSONNUMMER:
            match = False
        else:
            raise EnrolmentRefused(503, e.code, [TRY_LATER_TEXT])
    if match:
        return normalised
    detail = invite_detail(session, invite)
    if detail is None:
        detail = PatientInviteDetail(invite_guid=invite.guid, pnr_failures=0)
        session.add(detail)
    detail.pnr_failures = (detail.pnr_failures or 0) + 1
    closed = detail.pnr_failures >= MAX_PNR_FAILURES
    session.commit()
    audit('patient_invite_pnr_mismatch',
          detail={'invite_guid': invite.guid, 'failures': detail.pnr_failures,
                  'closed': closed},
          ip=ip)
    if closed:
        raise LinkRefused(404, 'invite_not_found',
                          'This invite is not valid (used, expired or revoked)')
    raise EnrolmentRefused(400, 'personnummer_mismatch', [PNR_MISMATCH_TEXT])


def accept_invite(session, token, *, email, password, personnummer,
                  secret_key, ip=None):
    """Create the patient account already linked. Returns the new User.

    Account creation is `patient_enrolment.enrol_patient` — the same rules as
    self-registration, so there is still one implementation of them. It raises
    EnrolmentRefused (caller handles it); this raises LinkRefused.

    #1050: the personnummer must equal the eps patient's
    (`check_invite_personnummer`), and the name care gave on the invite
    becomes the account's `patient_profiles` row (the blob's display_name)."""
    from src.services.patient_enrolment import enrol_patient
    invite = find_valid_invite(session, token, secret_key)
    if invite is None:
        raise LinkRefused(404, 'invite_not_found',
                          'This invite is not valid (used, expired or revoked)')
    if session.query(PatientLink).filter_by(
            ips_patient_guid=invite.ips_patient_guid).first() is not None:
        raise LinkRefused(409, 'patient_already_linked',
                          'This patient is already linked to an account')
    personnummer = check_invite_personnummer(session, invite, personnummer, ip=ip)
    detail = invite_detail(session, invite)
    user, _patient = enrol_patient(
        session, email=email, password=password, personnummer=personnummer,
        organisation_guid=invite.care_unit_guid,
        given_name=detail.given_name if detail else None,
        family_name=detail.family_name if detail else None, ip=ip)
    _set_link(session, user=user, ips_patient_guid=invite.ips_patient_guid,
              actor_guid=invite.created_by_user_guid, method='invite',
              care_unit_guid=invite.care_unit_guid, ip=ip)
    invite.used_at = _utcnow()
    invite.used_by_user_guid = user.guid
    audit('patient_invite_accept', user_guid=user.guid,
          detail={'invite_guid': invite.guid}, ip=ip)
    return user


# --- entrance 2: self-registered, link code ---------------------------------

def create_link_code(session, user, secret_key, ip=None):
    """A fresh one-time code for a patient account that is not yet linked.
    Returns (formatted code, expires_at). Any earlier unused code is dropped:
    only the newest works."""
    if user.user_type != 'patient':
        raise LinkRefused(403, 'forbidden', 'Patient access required')
    if current_link(session, user.guid) is not None:
        raise LinkRefused(409, 'account_already_linked',
                          'This account is already linked to a patient')
    session.query(PatientLinkCode).filter_by(
        user_guid=user.guid, used_at=None).delete(synchronize_session='fetch')
    code = ''.join(secrets.choice(LINK_CODE_ALPHABET) for _ in range(LINK_CODE_LENGTH))
    row = PatientLinkCode(
        user_guid=user.guid,
        code_hash=secret_store.keyed_hash(secret_key, 'patient-link-code', code),
        expires_at=_utcnow() + LINK_CODE_TTL)
    session.add(row)
    session.flush()
    audit('patient_link_code_create', user_guid=user.guid,
          detail={'code_guid': row.guid}, ip=ip)
    return format_code(code), row.expires_at


def redeem_link_code(session, actor, *, code, ips_patient_guid, secret_key, ip=None):
    """Care enters the patient's code together with the ips patient guid.

    Unknown, used, expired, and "valid but for a patient at a unit you are not
    affiliated to" all answer the SAME 404, so a professional cannot use this
    to learn which codes exist outside their own unit."""
    _require_linker(actor)
    ips_patient_guid = normalise_guid(ips_patient_guid)
    code = normalise_code(code)

    def refuse(reason):
        audit('patient_link_refused', user_guid=actor.guid,
              detail={'reason': reason, 'ips_patient_guid': ips_patient_guid}, ip=ip)
        return LinkRefused(404, 'link_code_not_found',
                           'Link code not found (wrong, used or expired)')

    if len(code) != LINK_CODE_LENGTH:
        raise refuse('malformed')
    row = session.query(PatientLinkCode).filter_by(
        code_hash=secret_store.keyed_hash(secret_key, 'patient-link-code', code)).first()
    if row is None:
        raise refuse('unknown')
    if row.used_at is not None:
        raise refuse('used')
    if _aware(row.expires_at) <= _utcnow():
        raise refuse('expired')
    user = session.query(User).filter_by(guid=row.user_guid).first()
    if user is None or user.user_type != 'patient':
        raise refuse('no_account')
    patient = session.query(Patient).filter_by(user_id=user.id).first()
    unit = patient.organisation_guid if patient else None
    if not _may_act_for_unit(session, actor, unit):
        raise refuse('not_affiliated')
    # Only now: the caller holds a valid code AND may act for the unit, so
    # what eps says goes to somebody inside the gate. A refusal here does not
    # burn the code.
    _eps_check(actor, ips_patient_guid, unit, 'link_code', ip=ip)

    link = _set_link(session, user=user, ips_patient_guid=ips_patient_guid,
                     actor_guid=actor.guid, method='link_code',
                     care_unit_guid=unit, ip=ip)
    row.used_at = _utcnow()
    row.used_by_user_guid = actor.guid
    return link


# --- SU only: relink / unlink -----------------------------------------------

def su_relink(session, actor, *, user_guid, ips_patient_guid, reason,
              care_unit_guid=None, ip=None):
    """Point an account at another patient (or link one that has no link).

    The guid must exist in eps. When `care_unit_guid` is named, the patient
    must also be listed there, and the link records that unit."""
    if not actor.is_su_admin:
        raise LinkRefused(403, 'forbidden', 'SU admin access required')
    ips_patient_guid = normalise_guid(ips_patient_guid)
    reason = (reason or '').strip()
    if not reason:
        raise LinkRefused(400, 'invalid_request', 'reason is required')
    user = session.query(User).filter_by(guid=user_guid or '').first()
    if user is None or user.user_type != 'patient':
        raise LinkRefused(404, 'not_found', 'Patient account not found')
    other = session.query(PatientLink).filter_by(ips_patient_guid=ips_patient_guid).first()
    if other is not None and other.user_guid != user.guid:
        raise LinkRefused(409, 'patient_already_linked',
                          'This patient is already linked to another account')
    care_unit_guid = (care_unit_guid or '').strip() or None
    _eps_check(actor, ips_patient_guid, care_unit_guid, 'su_relink', ip=ip)
    link = current_link(session, user.guid)
    previous = link.ips_patient_guid if link else None
    if link is None:
        link = PatientLink(user_guid=user.guid, ips_patient_guid=ips_patient_guid,
                           care_unit_guid=care_unit_guid,
                           linked_by_user_guid=actor.guid, method='su_relink',
                           linked_at=_utcnow())
        session.add(link)
    else:
        if care_unit_guid:
            link.care_unit_guid = care_unit_guid
        link.ips_patient_guid = ips_patient_guid
        link.linked_by_user_guid = actor.guid
        link.linked_at = _utcnow()
        link.method = 'su_relink'
    session.add(PatientLinkHistory(
        event='relink', user_guid=user.guid, ips_patient_guid=ips_patient_guid,
        previous_ips_patient_guid=previous, care_unit_guid=link.care_unit_guid,
        actor_user_guid=actor.guid, method='su_relink', reason=reason[:500]))
    session.flush()
    audit('patient_relink', user_guid=actor.guid,
          detail={'account_user_guid': user.guid, 'from': previous,
                  'to': ips_patient_guid, 'reason': reason[:500]}, ip=ip)
    return link


def su_unlink(session, actor, *, user_guid, reason, ip=None):
    if not actor.is_su_admin:
        raise LinkRefused(403, 'forbidden', 'SU admin access required')
    reason = (reason or '').strip()
    if not reason:
        raise LinkRefused(400, 'invalid_request', 'reason is required')
    link = current_link(session, user_guid or '')
    if link is None:
        raise LinkRefused(404, 'not_found', 'No link for this account')
    previous = link.ips_patient_guid
    session.add(PatientLinkHistory(
        event='unlink', user_guid=link.user_guid, ips_patient_guid=None,
        previous_ips_patient_guid=previous, care_unit_guid=link.care_unit_guid,
        actor_user_guid=actor.guid, method='su_relink', reason=reason[:500]))
    session.delete(link)
    session.flush()
    audit('patient_unlink', user_guid=actor.guid,
          detail={'account_user_guid': user_guid, 'from': previous,
                  'reason': reason[:500]}, ip=ip)
