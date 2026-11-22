"""Patient enrolment: ONE implementation for both doors (#910, #1049).

Two entry points, both here, both used by the JSON door
(`routes/patient.py::register`) AND the HTML door
(`routes/frontend.py::register_patient`):

  register_patient  self-registration (#1049, contract s7.1). Validates,
                    asks eps to create the patient, creates the account and,
                    when eps created the patient, the patient link — in one
                    transaction.
  enrol_patient     account creation only. Used by `accept_invite` (care
                    invited the patient; the eps guid is on the invite).

UNAUTHENTICATED BY DESIGN — self-enrolment is intended.

THE PERSONNUMMER RULE CHANGED WITH #1049. Until then it was "12 digits, no
checksum", because synthetic numbers carry a WRONG check digit on purpose
(#405) and a Luhn check that refuses invalid numbers would refuse every
synthetic patient. The pilot now goes further in the SAME direction:
`PATIENT_REGISTRATION_SYNTHETIC_ONLY` (default true) refuses a number whose
check digit is VALID — it may be a real person's. A synthetic number is still
never refused for its check digit. 10 or 12 digits and a real date are
required either way (services/personnummer.py).

NO ENUMERATION (s7.1). Every outcome that is not a plain form error gives the
registrant the SAME answer (`REGISTERED_TEXT`, HTTP 202 on the JSON door):
  email or personnummer already used    nothing created
  new, eps 201                          account + patient_link, linked
  new, eps 409 (patient exists in eps)  account UNLINKED (link-code path).
                                        Never auto-linked: anyone could take
                                        over an existing patient by typing a
                                        number.
The duplicate path also hashes the password and every uniform answer is held
to `PATIENT_REGISTRATION_MIN_SECONDS`, so the outcomes take comparable time.

Which organisation: `care_hierarchy.enrollable_care_units`, narrowed by
`PATIENT_REGISTRATION_UNITS` when set. Anything else answers the SAME "not
found", so the form cannot be used to learn which guids exist.
"""
import re
import time

from flask import current_app

from src.models.patient import Patient
from src.models.patient_profile import PatientProfile
from src.models.user import User
from src.services import eps_client
from src.services import personnummer as pnr_rule
from src.services.audit_log import audit
from src.services.auth_service import hash_password
from src.services.care_hierarchy import enrollable_care_units

REGISTERED_TEXT = ('Om uppgifterna kunde registreras kan du logga in nu. '
                   'Har du redan ett konto loggar du in med det.')
SYNTHETIC_ONLY_TEXT = 'Piloten tar bara emot testpersonnummer.'
TRY_LATER_TEXT = 'Försök igen om en stund.'
#: `linked_by` on a link the registration itself made (contract s7.1).
LINKED_BY_REGISTRATION = 'self-registration'
#: `patient_links.method` (String(16)).
METHOD_SELF_REGISTER = 'self_register'

_EMAIL = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
NAME_MAX = 100


class EnrolmentRefused(Exception):
    def __init__(self, status, code, messages):
        super().__init__('; '.join(messages))
        self.status, self.code, self.messages = status, code, list(messages)


# --- config -----------------------------------------------------------------

def _cfg():
    return current_app.config


def _truthy(value, default):
    if value is None or str(value).strip() == '':
        return default
    return str(value).strip().lower() in ('1', 'true', 'yes')


def synthetic_only(config):
    """Default TRUE: only the operator turns it off, and only when the
    platform is cleared to hold real patients."""
    return _truthy(config.get('PATIENT_REGISTRATION_SYNTHETIC_ONLY'), True)


def configured_units(config):
    raw = config.get('PATIENT_REGISTRATION_UNITS') or ''
    if isinstance(raw, (list, tuple, set)):
        raw = ','.join(raw)
    return {u.strip().lower() for u in str(raw).split(',') if u.strip()}


def min_seconds(config):
    try:
        return max(0.0, float(config.get('PATIENT_REGISTRATION_MIN_SECONDS', 1.0)))
    except (TypeError, ValueError):
        return 1.0


# --- the lists --------------------------------------------------------------

def enrollable_choices(session):
    """[(guid, name)] every unit a patient may belong to."""
    return [(o.guid, o.name) for o in enrollable_care_units(session)]


def registration_choices(session, config):
    """[(guid, name)] the units SELF-registration offers: the enrollable ones,
    narrowed by PATIENT_REGISTRATION_UNITS when set. A configured guid that is
    not enrollable is not offered (the setting narrows, never widens)."""
    choices = enrollable_choices(session)
    wanted = configured_units(config)
    if wanted:
        choices = [(g, n) for g, n in choices if g.lower() in wanted]
    return choices


# --- validation -------------------------------------------------------------

def normalise_mobile(value):
    """'' -> None. Swedish mobile -> '+467XXXXXXXX'. Anything else -> ValueError.

    Accepted: 07XXXXXXXX, +467XXXXXXXX, 00467XXXXXXXX, 467XXXXXXXX, with
    spaces, dashes or brackets anywhere."""
    v = re.sub(r'[\s\-().]', '', value or '')
    if not v:
        return None
    if v.startswith('+46'):
        rest = v[3:]
    elif v.startswith('0046'):
        rest = v[4:]
    elif v.startswith('46') and len(v) == 11:
        rest = v[2:]
    elif v.startswith('0'):
        rest = v[1:]
    else:
        raise ValueError('not a Swedish mobile number')
    if not re.fullmatch(r'7\d{8}', rest):
        raise ValueError('not a Swedish mobile number')
    return '+46' + rest


def _check_personnummer(value, config, errors):
    kind, n = pnr_rule.classify(value)
    if kind == pnr_rule.MALFORMED:
        errors.append('Ange ditt personnummer med 10 eller 12 siffror och ett '
                      'riktigt datum.')
        return None
    if kind == pnr_rule.REAL_FORMAT and synthetic_only(config):
        errors.append(SYNTHETIC_ONLY_TEXT)
        return None
    return n


def _validate_account(errors, *, email, password, personnummer, config):
    email = (email or '').strip().lower()
    if not email:
        errors.append('Ange din e-postadress.')
    elif not _EMAIL.match(email) or len(email) > 255:
        errors.append('Ange en giltig e-postadress.')
    if len(password or '') < 8:
        errors.append('Lösenordet måste ha minst 8 tecken.')
    return email, _check_personnummer(personnummer, config, errors)


def _duplicate(session, email, personnummer):
    """'email' | 'personnummer' | None."""
    if session.query(User).filter_by(email=email).first():
        return 'email'
    if session.query(Patient).filter_by(personnummer=personnummer).first():
        return 'personnummer'
    return None


def _create(session, *, email, password, personnummer, organisation_guid,
            given_name=None, family_name=None, ip=None):
    user = User(email=email, password_hash=hash_password(password),
                user_type='patient', is_su_admin=False)
    session.add(user)
    session.flush()
    patient = Patient(user_id=user.id, personnummer=personnummer,
                      organisation_guid=organisation_guid,
                      in_registry=False, registries=[])
    session.add(patient)
    if given_name and family_name:
        session.add(PatientProfile(user_guid=user.guid, given_name=given_name,
                                   family_name=family_name))
    session.flush()
    audit('patient_register', user_guid=user.guid,
          detail={'email': email, 'personnummer': pnr_rule.mask(personnummer),
                  'organisation_guid': organisation_guid},
          ip=ip)
    return user, patient


# --- account creation only (the invite path) --------------------------------

def enrol_patient(session, *, email, password, personnummer, organisation_guid, ip=None):
    """Create user (type=patient) + patient row. Raises EnrolmentRefused.

    Used where the eps patient is already known (an invite). The personnummer
    rule is the same as self-registration's."""
    config = _cfg()
    organisation_guid = (organisation_guid or '').strip()
    errors = []
    email, personnummer = _validate_account(
        errors, email=email, password=password, personnummer=personnummer,
        config=config)
    if not organisation_guid:
        errors.append('Välj din vårdenhet.')
    if errors:
        raise EnrolmentRefused(400, 'validation_error', errors)
    if organisation_guid not in {g for g, _ in enrollable_choices(session)}:
        raise EnrolmentRefused(404, 'not_found', ['Vårdenheten finns inte.'])
    dup = _duplicate(session, email, personnummer)
    if dup == 'email':
        raise EnrolmentRefused(409, 'conflict', ['E-postadressen är redan registrerad.'])
    if dup == 'personnummer':
        raise EnrolmentRefused(409, 'conflict', ['Personnumret är redan registrerat.'])
    return _create(session, email=email, password=password,
                   personnummer=personnummer, organisation_guid=organisation_guid,
                   ip=ip)


# --- self-registration (#1049) ----------------------------------------------

_EPS_REFUSALS = {
    eps_client.NOT_CONFIGURED: (503, 'eps_not_configured', TRY_LATER_TEXT),
    eps_client.UNAVAILABLE: (503, 'eps_unavailable', TRY_LATER_TEXT),
    eps_client.UNKNOWN_UNIT: (422, 'unit_not_available',
                              'Vårdenheten kan inte ta emot registreringar just nu. '
                              + TRY_LATER_TEXT),
}


def _hold(started, config):
    """Every uniform answer takes at least PATIENT_REGISTRATION_MIN_SECONDS."""
    left = min_seconds(config) - (time.monotonic() - started)
    if left > 0:
        time.sleep(left)


def register_patient(session, *, given_name, family_name, personnummer, email,
                     mobile, organisation_guid, password, ip=None):
    """Self-registration. Returns the outcome name for the caller's log
    ('duplicate' | 'linked' | 'unlinked'); the registrant must be shown the
    same thing for all three. Raises EnrolmentRefused for a form error.

    Commits on success: the eps patient already exists by then, so the
    account is not left to the request teardown."""
    started = time.monotonic()
    config = _cfg()
    errors = []
    given_name = (given_name or '').strip()
    family_name = (family_name or '').strip()
    organisation_guid = (organisation_guid or '').strip()
    if not given_name:
        errors.append('Ange ditt förnamn.')
    if not family_name:
        errors.append('Ange ditt efternamn.')
    if len(given_name) > NAME_MAX or len(family_name) > NAME_MAX:
        errors.append(f'Namnet får vara högst {NAME_MAX} tecken.')
    email, personnummer = _validate_account(
        errors, email=email, password=password, personnummer=personnummer,
        config=config)
    try:
        mobile = normalise_mobile(mobile)
    except ValueError:
        errors.append('Ange ett svenskt mobilnummer (07…), eller lämna fältet tomt.')
    if not organisation_guid:
        errors.append('Välj din vårdenhet.')
    if errors:
        raise EnrolmentRefused(400, 'validation_error', errors)
    if organisation_guid not in {g for g, _ in registration_choices(session, config)}:
        raise EnrolmentRefused(404, 'not_found', ['Vårdenheten finns inte.'])
    # Before the duplicate check: "not configured" is the same for everyone,
    # so it cannot tell a registrant anything about an account.
    if not (config.get('EPS_BASE_URL') or '').strip() or \
            not (config.get('EPS_API_KEY') or '').strip():
        audit('patient_register_refused', detail={'reason': eps_client.NOT_CONFIGURED},
              ip=ip)
        status, code, text = _EPS_REFUSALS[eps_client.NOT_CONFIGURED]
        raise EnrolmentRefused(status, code, [text])

    dup = _duplicate(session, email, personnummer)
    if dup:
        hash_password(password)          # the work a new account costs
        audit('patient_register_duplicate',
              detail={'which': dup, 'organisation_guid': organisation_guid}, ip=ip)
        _hold(started, config)
        return 'duplicate'

    try:
        outcome, eps_guid = eps_client.self_register(
            config, personnummer=personnummer, given_name=given_name,
            family_name=family_name, care_unit_guid=organisation_guid,
            email=email, mobile=mobile)
    except eps_client.EpsRefusal as e:
        audit('patient_register_refused',
              detail={'reason': e.code, 'eps_detail': e.detail,
                      'organisation_guid': organisation_guid}, ip=ip)
        status, code, text = _EPS_REFUSALS[e.code]
        raise EnrolmentRefused(status, code, [text])

    from src.services import patient_link as pl
    try:
        user, _patient = _create(
            session, email=email, password=password, personnummer=personnummer,
            organisation_guid=organisation_guid, given_name=given_name,
            family_name=family_name, ip=ip)
        if outcome == eps_client.CREATED:
            pl._set_link(session, user=user, ips_patient_guid=eps_guid,
                         actor_guid=LINKED_BY_REGISTRATION, method=METHOD_SELF_REGISTER,
                         care_unit_guid=organisation_guid, ip=ip)
        session.commit()
    except Exception as e:  # noqa: BLE001 — any failure here is the same
        session.rollback()
        # eps may now hold a patient with no account here. Harmless (the next
        # attempt gets eps's 409 and an unlinked account), but it is recorded.
        audit('patient_register_not_committed',
              detail={'eps_outcome': outcome, 'eps_patient_guid': eps_guid,
                      'error': type(e).__name__,
                      'organisation_guid': organisation_guid}, ip=ip)
        raise EnrolmentRefused(503, 'not_committed', [TRY_LATER_TEXT])

    if outcome == eps_client.CREATED:
        audit('patient_self_linked', user_guid=user.guid,
              detail={'ips_patient_guid': eps_guid,
                      'care_unit_guid': organisation_guid}, ip=ip)
        result = 'linked'
    else:
        audit('patient_register_unlinked', user_guid=user.guid,
              detail={'care_unit_guid': organisation_guid}, ip=ip)
        result = 'unlinked'
    _hold(started, config)
    return result
