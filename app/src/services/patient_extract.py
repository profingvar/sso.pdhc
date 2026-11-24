"""Everything sso holds about one patient, for record.pdhc's extract (#1059).

Contract: plans/record_pdhc_extract_contract.md (workspace repo),
`GET /api/v1/patient-extract/<patient_guid>`, schema `pdhc.patient-extract/1`.
`<patient_guid>` is the EPS patient guid — in sso that is
`patient_links.ips_patient_guid` / `patient_invites.ips_patient_guid`.

Gate (contract + D9)
--------------------
  - an SU admin, or
  - an ACTIVATED professional holding an active affiliation in the role
    `doctor` or `nurse` to a care unit where eps lists the patient
    (`eps_client.listed_units`, the same eps endpoint the invite check uses).
Everything outside that answers 404 `not_found`, the same as an unknown
patient, never an empty extract. eps unreachable / not configured is 503: the
gate cannot be decided, and it fails closed. There is no development opt-out.
A patient token is 403 (patients get their extract via patientapp at level B).
`must_change_password` is 403, as everywhere in sso.

What is in it, and what is never in it
--------------------------------------
Sections (only those with items): `account`, `second_factors`, `invites`,
`patient_link`, `reenrolments`. Types and dates only. NEVER: the password hash,
the TOTP secret, a passkey's credential id or public key, an invite token or
its hash, a link code or its hash, a re-enrolment code or its hash, and no
personnummer (the contract keeps that to eps's identity section).
`SECRET_COLUMNS` names every such column; the tests populate each one and
assert its value is nowhere in the response, and fail if a table read here
grows a secret-looking column that is not listed.

The account sections describe the account CURRENTLY linked to the patient.
An account that was linked earlier and then unlinked or relinked is shown only
as a guid in the link history: it may belong to another person (a wrong link
is exactly why relinks exist), so its email and factors are not given out.

sso keeps no version history of these rows, so every item has ONE version
(its current state, with every date the row holds); the link history is the
exception and is given event by event. `notes` says so.
"""
import uuid
from datetime import datetime, timezone

from src.models.patient import Patient
from src.models.patient_invite_detail import PatientInviteDetail
from src.models.patient_link import (
    PatientInvite, PatientLink, PatientLinkCode, PatientLinkHistory,
)
from src.models.patient_profile import PatientProfile
from src.models.professional import Professional
from src.models.organisation import Organisation
from src.models.login_lockout import LoginLockout
from src.models.role import Role
from src.models.second_factor import TotpSecret, WebAuthnCredential
from src.models.second_factor_reenrolment import SecondFactorReenrolment
from src.models.user import User
from src.services import eps_client
from src.services.activation_service import is_activated
from src.services.affiliation_service import active_affiliations
from src.services.audit_log import audit

SCHEMA = 'pdhc.patient-extract/1'
SERVICE = 'sso.pdhc'
#: Affiliation roles that may take out an extract (D9: läkare, sjuksköterska).
EXTRACT_ROLES = frozenset({'doctor', 'nurse'})

#: (table, column) that must never reach an extract. The tests read each one
#: back from the database and search the response for the value.
SECRET_COLUMNS = (
    ('users', 'password_hash'),
    ('patients', 'personnummer'),
    ('totp_secrets', 'secret_enc'),
    ('webauthn_credentials', 'credential_id'),
    ('webauthn_credentials', 'public_key'),
    ('patient_invites', 'token_hash'),
    ('patient_link_codes', 'code_hash'),
    ('second_factor_reenrolments', 'code_hash'),
)

#: Every table this module reads. The secret-column test walks these.
TABLES_READ = (
    'users', 'patients', 'patient_profiles', 'login_lockouts',
    'webauthn_credentials', 'totp_secrets', 'patient_invites',
    'patient_invite_details', 'patient_links', 'patient_link_history',
    'patient_link_codes', 'second_factor_reenrolments',
)


class ExtractRefused(Exception):
    def __init__(self, status, code, message, reason):
        super().__init__(message)
        self.status, self.code, self.message, self.reason = status, code, message, reason


NOT_FOUND = (404, 'not_found', 'Patient not found')


def _utcnow():
    return datetime.now(timezone.utc)


def _iso(dt):
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat().replace('+00:00', 'Z')


def _day(dt):
    return _iso(dt)[:10] if dt else '–'


# --- the gate ---------------------------------------------------------------

def _refuse(status_code_msg, reason):
    status, code, message = status_code_msg
    raise ExtractRefused(status, code, message, reason)


def check_access(session, actor, raw_guid, config):
    """Return (patient_guid, basis). Raise ExtractRefused otherwise."""
    if actor.user_type != 'professional' and not actor.is_su_admin:
        _refuse((403, 'forbidden', 'Professional access required'), 'not_professional')
    if getattr(actor, 'force_change_on_next_login', False):
        _refuse((403, 'must_change_password',
                 'Change your password before taking out an extract'),
                'must_change_password')
    try:
        patient_guid = str(uuid.UUID(str(raw_guid or '').strip()))
    except (ValueError, AttributeError):
        _refuse(NOT_FOUND, 'invalid_guid')
    if actor.is_su_admin:
        return patient_guid, 'su'
    if not is_activated(actor):
        _refuse(NOT_FOUND, 'not_activated')
    roles = {r.guid: r.code for r in session.query(Role).all()}
    units = {a.care_unit_guid.strip().lower()
             for a in active_affiliations(session, actor.guid)
             if roles.get(a.role_guid) in EXTRACT_ROLES}
    if not units:
        _refuse(NOT_FOUND, 'no_doctor_or_nurse_affiliation')
    try:
        listed = eps_client.listed_units(config, patient_guid)
    except eps_client.EpsRefusal as e:
        if e.code == eps_client.PATIENT_NOT_FOUND:
            _refuse(NOT_FOUND, 'eps_patient_not_found')
        _refuse((503, e.code, 'The patient register (eps) did not answer; '
                 'the extract cannot be checked now'), e.code)
    shared = units & listed
    if not shared:
        _refuse(NOT_FOUND, 'not_listed_at_actor_unit')
    return patient_guid, 'affiliation:' + ','.join(sorted(shared))


# --- building blocks --------------------------------------------------------

def _author(session, user_guid):
    if not user_guid:
        return None
    user = session.query(User).filter_by(guid=user_guid).first()
    if user is None:
        # e.g. 'self-registration', or an account since deleted
        return {'guid': user_guid, 'name': None, 'role': None, 'service': SERVICE}
    prof = session.query(Professional).filter_by(user_id=user.id).first()
    name = None
    if prof is not None:
        name = ' '.join(p for p in (prof.first_name, prof.last_name) if p) or None
    return {'guid': user.guid, 'name': name,
            'role': prof.professional_role if prof else user.user_type,
            'service': SERVICE}


def _unit_name(session, guid):
    if not guid:
        return None
    org = session.query(Organisation).filter_by(guid=guid).first()
    return org.name if org else None


def _item(resource_type, item_id, status, versions):
    return {'resource_type': resource_type, 'id': item_id,
            'current_status': status, 'versions': versions}


def _one_version(*, recorded_at, status, summary_sv, data, author=None,
                 event_time=None):
    return [{'version': 1, 'event_time': _iso(event_time) if event_time else None,
             'recorded_at': _iso(recorded_at), 'author': author,
             'status': status, 'summary_sv': summary_sv, 'data': data}]


def _section(key, title_sv, items):
    return {'key': key, 'title_sv': title_sv, 'count': len(items), 'items': items}


_STATUS_SV = {'active': 'aktivt', 'pending': 'väntar', 'suspended': 'avstängt'}


def _account_items(session, user):
    profile = session.query(PatientProfile).filter_by(user_guid=user.guid).first()
    legacy = session.query(Patient).filter_by(user_id=user.id).first()
    lock = session.query(LoginLockout).filter_by(user_guid=user.guid).first()
    data = {
        'email': user.email,
        'user_type': user.user_type,
        'status': user.status,
        'created_at': _iso(user.created_at),
        'updated_at': _iso(user.updated_at),
        'password_changed_at': _iso(user.password_changed_at),
        'must_change_password': bool(user.force_change_on_next_login),
        'given_name': profile.given_name if profile else None,
        'family_name': profile.family_name if profile else None,
        'registered_care_unit_guid': legacy.organisation_guid if legacy else None,
        'in_registry': bool(legacy.in_registry) if legacy else None,
        'registries': (legacy.registries or []) if legacy else None,
        'login_lockout': ({'failed_count': lock.failed_count,
                           'last_failed_at': _iso(lock.last_failed_at),
                           'locked_until': _iso(lock.locked_until)} if lock else None),
    }
    summary = (f'Patientkonto {user.email}, skapat {_day(user.created_at)}, '
               f'status {_STATUS_SV.get(user.status, user.status)}')
    return [_item('UserAccount', user.guid, user.status, _one_version(
        recorded_at=user.created_at, status=user.status, summary_sv=summary,
        data=data))]


def _second_factor_items(session, user):
    items = []
    for c in (session.query(WebAuthnCredential).filter_by(user_guid=user.guid)
              .order_by(WebAuthnCredential.created_at).all()):
        data = {'type': 'passkey', 'name': c.name,
                'created_at': _iso(c.created_at), 'last_used_at': _iso(c.last_used_at)}
        items.append(_item('SecondFactor', c.guid, 'enrolled', _one_version(
            recorded_at=c.created_at, status='enrolled', data=data,
            summary_sv=f'Passkey registrerad {_day(c.created_at)}, '
                       f'senast använd {_day(c.last_used_at)}')))
    t = session.query(TotpSecret).filter_by(user_guid=user.guid).first()
    if t is not None:
        status = 'enrolled' if t.confirmed_at else 'not_confirmed'
        data = {'type': 'totp', 'created_at': _iso(t.created_at),
                'confirmed_at': _iso(t.confirmed_at), 'locked_until': _iso(t.locked_until)}
        items.append(_item('SecondFactor', t.guid, status, _one_version(
            recorded_at=t.created_at, status=status, data=data,
            summary_sv=(f'Autentiseringsapp registrerad {_day(t.confirmed_at)}'
                        if t.confirmed_at else
                        f'Autentiseringsapp påbörjad {_day(t.created_at)}, ej bekräftad'))))
    return items


def _invite_status(invite, detail, now):
    from src.services.patient_link import MAX_PNR_FAILURES
    if invite.used_at is not None:
        return 'used'
    if invite.revoked_at is not None:
        return 'revoked'
    if detail is not None and (detail.pnr_failures or 0) >= MAX_PNR_FAILURES:
        return 'locked'
    exp = invite.expires_at if invite.expires_at.tzinfo else invite.expires_at.replace(tzinfo=timezone.utc)
    return 'expired' if exp <= now else 'open'


_INVITE_SV = {'used': 'använd', 'revoked': 'återkallad', 'locked': 'låst (fel personnummer)',
              'expired': 'utgången', 'open': 'öppen'}


def _invite_items(session, patient_guid):
    now = _utcnow()
    items = []
    for inv in (session.query(PatientInvite).filter_by(ips_patient_guid=patient_guid)
                .order_by(PatientInvite.created_at).all()):
        detail = session.query(PatientInviteDetail).filter_by(invite_guid=inv.guid).first()
        status = _invite_status(inv, detail, now)
        unit = _unit_name(session, inv.care_unit_guid)
        data = {
            'care_unit_guid': inv.care_unit_guid, 'care_unit_name': unit,
            'created_at': _iso(inv.created_at), 'expires_at': _iso(inv.expires_at),
            'used_at': _iso(inv.used_at), 'used_by_user_guid': inv.used_by_user_guid,
            'revoked_at': _iso(inv.revoked_at),
            'revoked_by': _author(session, inv.revoked_by_user_guid),
            'given_name': detail.given_name if detail else None,
            'family_name': detail.family_name if detail else None,
            'wrong_personnummer_attempts': detail.pnr_failures if detail else 0,
        }
        items.append(_item('PatientInvite', inv.guid, status, _one_version(
            recorded_at=inv.created_at, status=status, data=data,
            author=_author(session, inv.created_by_user_guid),
            summary_sv=f'Inbjudan från {unit or inv.care_unit_guid} '
                       f'{_day(inv.created_at)}, {_INVITE_SV[status]}')))
    return items


_EVENT_SV = {'link': 'kopplat', 'relink': 'omkopplat', 'unlink': 'kopplat bort'}


def _link_items(session, patient_guid, link):
    rows = (session.query(PatientLinkHistory)
            .filter((PatientLinkHistory.ips_patient_guid == patient_guid)
                    | (PatientLinkHistory.previous_ips_patient_guid == patient_guid))
            .order_by(PatientLinkHistory.at, PatientLinkHistory.id).all())
    items = []
    if link is not None or rows:
        versions = []
        for n, h in enumerate(rows, start=1):
            moved_away = (h.previous_ips_patient_guid == patient_guid
                          and h.ips_patient_guid != patient_guid)
            status = 'unlinked' if (h.event == 'unlink' or moved_away) else 'linked'
            versions.append({
                'version': n, 'event_time': _iso(h.at), 'recorded_at': _iso(h.at),
                'author': _author(session, h.actor_user_guid), 'status': status,
                'summary_sv': (f'Konto {h.user_guid[:8]}… {_EVENT_SV.get(h.event, h.event)} '
                               f'{_day(h.at)} ({h.method})'),
                'data': {'event': h.event, 'account_user_guid': h.user_guid,
                         'ips_patient_guid': h.ips_patient_guid,
                         'previous_ips_patient_guid': h.previous_ips_patient_guid,
                         'care_unit_guid': h.care_unit_guid, 'method': h.method,
                         'reason': h.reason},
            })
        if not versions and link is not None:
            # a link older than the history table: its own row is the record
            versions = _one_version(
                recorded_at=link.linked_at, event_time=link.linked_at,
                status='linked', author=_author(session, link.linked_by_user_guid),
                summary_sv=f'Konto kopplat {_day(link.linked_at)} ({link.method})',
                data={'event': 'link', 'account_user_guid': link.user_guid,
                      'care_unit_guid': link.care_unit_guid, 'method': link.method})
        items.append(_item('PatientLink', link.guid if link else None,
                           'linked' if link else 'unlinked', versions))
    if link is not None:
        for c in (session.query(PatientLinkCode).filter_by(user_guid=link.user_guid)
                  .order_by(PatientLinkCode.created_at).all()):
            status = 'used' if c.used_at else 'unused'
            items.append(_item('PatientLinkCode', c.guid, status, _one_version(
                recorded_at=c.created_at, status=status,
                summary_sv=f'Kopplingskod skapad {_day(c.created_at)}, '
                           f'{"använd " + _day(c.used_at) if c.used_at else "ej använd"}',
                data={'created_at': _iso(c.created_at), 'expires_at': _iso(c.expires_at),
                      'used_at': _iso(c.used_at), 'used_by_user_guid': c.used_by_user_guid})))
    return items


def _reenrolment_items(session, user):
    items = []
    for r in session.query(SecondFactorReenrolment).filter_by(user_guid=user.guid).all():
        status = ('completed' if r.completed_at else 'used' if r.used_at
                  else 'void' if r.voided_at else 'issued')
        items.append(_item('SecondFactorReenrolment', r.guid, status, _one_version(
            recorded_at=r.created_at, status=status,
            author=_author(session, r.issued_by_user_guid),
            summary_sv=f'Engångskod för ny inloggningsfaktor utfärdad {_day(r.created_at)}, '
                       f'{ {"completed": "ny faktor registrerad", "used": "använd", "void": "spärrad", "issued": "ej använd"}[status] }',
            data={'issued_at': _iso(r.created_at), 'expires_at': _iso(r.expires_at),
                  'used_at': _iso(r.used_at), 'completed_at': _iso(r.completed_at),
                  'voided_at': _iso(r.voided_at), 'wrong_attempts': r.failed_attempts})))
    return items


def build_extract(session, patient_guid):
    link = session.query(PatientLink).filter_by(ips_patient_guid=patient_guid).first()
    user = session.query(User).filter_by(guid=link.user_guid).first() if link else None
    sections = []
    if user is not None:
        sections.append(_section('account', 'Konto', _account_items(session, user)))
        sections.append(_section('second_factors', 'Inloggningsfaktorer',
                                 _second_factor_items(session, user)))
    sections.append(_section('invites', 'Inbjudningar', _invite_items(session, patient_guid)))
    sections.append(_section('patient_link', 'Koppling konto–patient',
                             _link_items(session, patient_guid, link)))
    if user is not None:
        sections.append(_section('reenrolments', 'Engångskoder efter återställning',
                                 _reenrolment_items(session, user)))
    sections = [s for s in sections if s['count']]
    notes = ['sso.pdhc keeps no version history of accounts, factors, invites or '
             'codes: each is given as one version, its current state with every '
             'date it holds. The link history is given event by event.',
             'Secrets are never included: password hash, TOTP secret, passkey '
             'credential id and key, invite and link-code tokens, re-enrolment '
             'codes. Personnummer is in eps\'s identity section only.']
    if user is None:
        notes.append('No sso account is linked to this patient.' if not sections
                     else 'No sso account is currently linked to this patient; an '
                          'account linked earlier is shown only by guid in the link history.')
    return {
        'schema': SCHEMA, 'service': SERVICE, 'patient_guid': patient_guid,
        'generated_at': _iso(_utcnow()), 'complete': True,
        'sections': sections, 'notes': notes,
    }


def extract(session, actor, raw_guid, config, ip=None):
    """Gate, build, audit. Returns the body; raises ExtractRefused (audited)."""
    try:
        patient_guid, basis = check_access(session, actor, raw_guid, config)
    except ExtractRefused as e:
        audit('patient_extract', user_guid=actor.guid,
              detail={'outcome': 'refused', 'reason': e.reason, 'status': e.status,
                      'patient_guid': str(raw_guid)[:64]}, ip=ip)
        raise
    body = build_extract(session, patient_guid)
    audit('patient_extract', user_guid=actor.guid,
          detail={'outcome': 'ok', 'patient_guid': patient_guid, 'basis': basis,
                  'sections': {s['key']: s['count'] for s in body['sections']}}, ip=ip)
    return body
