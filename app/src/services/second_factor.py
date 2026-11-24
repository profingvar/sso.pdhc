"""Second factor for patient accounts (#1040): passkey first, TOTP as reserve.

The cryptography is not ours. Passkeys go through `webauthn` (py_webauthn),
TOTP through `pyotp`; this module holds only the policy around them:

  * what "enrolled" means: at least one passkey, or a CONFIRMED TOTP secret;
  * what "verified" means: THIS token was issued after a second factor was
    presented. It is a claim in the JWT (`sfm` method, `sfa` when), set only by
    `issue_token(..., second_factor=...)`. It says nothing about the account
    in general: a second login with the password alone gets a token whose
    `verified` is false, even though the account is enrolled;
  * TOTP replay and lockout (below).

Passkeys need to know which site they belong to. `WEBAUTHN_RP_ID` and
`WEBAUTHN_ORIGIN` are read from config and there is NO fallback: deriving them
from the request's Host header would let whoever controls that header choose
the site a credential is verified against. Unset means passkeys answer
`passkey_not_configured` by name and TOTP still works.
"""
import hmac
import json
import secrets
import time
from datetime import datetime, timedelta, timezone

import pyotp
from sqlalchemy import func
from sqlalchemy.orm import object_session
from webauthn import (
    generate_authentication_options, generate_registration_options,
    options_to_json, verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
from webauthn.helpers.exceptions import (
    InvalidAuthenticationResponse, InvalidRegistrationResponse,
)
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria, PublicKeyCredentialDescriptor,
    ResidentKeyRequirement, UserVerificationRequirement,
)

from src.models.second_factor import TotpSecret, WebAuthnCredential
from src.models.second_factor_reenrolment import SecondFactorReenrolment
from src.services import secret_store

METHOD_PASSKEY = 'passkey'
METHOD_TOTP = 'totp'

TOTP_STEP_SECONDS = 30
#: A code is accepted for the current step and one either side (clock drift).
TOTP_WINDOW = 1
TOTP_MAX_FAILURES = 5
TOTP_LOCK = timedelta(minutes=15)
TOTP_ISSUER = 'PDHC'
_ENC_PURPOSE = 'totp'


class SecondFactorError(Exception):
    """A refusal with a stable code the route can name to the user."""

    def __init__(self, code, message, status=400):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def _utcnow():
    return datetime.now(timezone.utc)


def _aware(dt):
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# --- state ------------------------------------------------------------------

def passkeys(session, user_guid):
    return session.query(WebAuthnCredential).filter_by(user_guid=user_guid).all()


def confirmed_totp(session, user_guid):
    row = session.query(TotpSecret).filter_by(user_guid=user_guid).first()
    return row if row is not None and row.confirmed_at is not None else None


def enrolled_methods(session, user_guid):
    """['passkey', 'totp'] in order of preference; [] when not enrolled."""
    out = []
    if passkeys(session, user_guid):
        out.append(METHOD_PASSKEY)
    if confirmed_totp(session, user_guid) is not None:
        out.append(METHOD_TOTP)
    return out


def is_enrolled(session, user_guid):
    return bool(enrolled_methods(session, user_guid))


def claim_from_payload(token_payload):
    """The second-factor claim of a decoded token, or None.

    Returns {'method', 'verified_at' (epoch seconds)}: the shape
    `issue_token(second_factor=...)` takes, so a claim can be carried from the
    session token onto a token minted for a service."""
    payload = token_payload or {}
    method, at = payload.get('sfm'), payload.get('sfa')
    if method not in (METHOD_PASSKEY, METHOD_TOTP) or not isinstance(at, int):
        return None
    return {'method': method, 'verified_at': at}


def second_factor_for_blob(session, user, token_payload):
    """The `second_factor` object of the access blob (patient accounts).

    `verified` is about the token that carried this request. It additionally
    requires the account to still be enrolled, so a factor reset by an SU
    cannot leave a token claiming a factor that no longer exists."""
    enrolled = is_enrolled(session, user.guid)
    claim = claim_from_payload(token_payload)
    verified = bool(enrolled and claim)
    return {
        'enrolled': enrolled,
        'verified': verified,
        'method': claim['method'] if verified else None,
        'verified_at': (datetime.fromtimestamp(claim['verified_at'], tz=timezone.utc)
                        .isoformat() if verified else None),
    }


def new_claim(method):
    return {'method': method, 'verified_at': int(time.time())}


# --- passkeys (WebAuthn) ----------------------------------------------------

def rp_settings(config):
    """(rp_id, origin, rp_name), or SecondFactorError when not configured."""
    rp_id = (config.get('WEBAUTHN_RP_ID') or '').strip()
    origin = (config.get('WEBAUTHN_ORIGIN') or '').strip().rstrip('/')
    if not rp_id or not origin:
        raise SecondFactorError(
            'passkey_not_configured',
            'Passkeys are not configured on this server '
            '(WEBAUTHN_RP_ID / WEBAUTHN_ORIGIN).', 503)
    return rp_id, origin, (config.get('WEBAUTHN_RP_NAME') or 'PDHC')


def passkeys_configured(config):
    try:
        rp_settings(config)
        return True
    except SecondFactorError:
        return False


def _descriptors(creds):
    return [PublicKeyCredentialDescriptor(id=base64url_to_bytes(c.credential_id))
            for c in creds]


def registration_options(session, user, config):
    """(options as a JSON-able dict, challenge as base64url)."""
    rp_id, _origin, rp_name = rp_settings(config)
    options = generate_registration_options(
        rp_id=rp_id, rp_name=rp_name,
        user_id=user.guid.encode(), user_name=user.email,
        user_display_name=user.email,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.PREFERRED,
            user_verification=UserVerificationRequirement.REQUIRED),
        exclude_credentials=_descriptors(passkeys(session, user.guid)),
    )
    return json.loads(options_to_json(options)), bytes_to_base64url(options.challenge)


def finish_registration(session, user, credential, challenge_b64, config, name=None):
    """Verify the browser's answer and store the passkey. Returns the row."""
    rp_id, origin, _ = rp_settings(config)
    if not challenge_b64:
        raise SecondFactorError('no_challenge', 'No passkey registration in progress.')
    try:
        verified = verify_registration_response(
            credential=credential,
            expected_challenge=base64url_to_bytes(challenge_b64),
            expected_rp_id=rp_id, expected_origin=origin,
            require_user_verification=True)
    except (InvalidRegistrationResponse, ValueError, KeyError, TypeError) as e:
        raise SecondFactorError('passkey_invalid', f'Passkey could not be verified: {e}')
    cred_id = bytes_to_base64url(verified.credential_id)
    if session.query(WebAuthnCredential).filter_by(credential_id=cred_id).first():
        raise SecondFactorError('passkey_exists', 'This passkey is already registered.', 409)
    transports = None
    if isinstance(credential, dict):
        transports = (credential.get('response') or {}).get('transports')
    row = WebAuthnCredential(
        user_guid=user.guid, credential_id=cred_id,
        public_key=bytes_to_base64url(verified.credential_public_key),
        sign_count=verified.sign_count, transports=transports,
        name=(name or '').strip()[:100] or None)
    session.add(row)
    session.flush()
    return row


def authentication_options(session, user, config):
    rp_id, _origin, _ = rp_settings(config)
    creds = passkeys(session, user.guid)
    if not creds:
        raise SecondFactorError('no_passkey', 'No passkey is registered for this account.')
    options = generate_authentication_options(
        rp_id=rp_id, allow_credentials=_descriptors(creds),
        user_verification=UserVerificationRequirement.REQUIRED)
    return json.loads(options_to_json(options)), bytes_to_base64url(options.challenge)


def finish_authentication(session, user, credential, challenge_b64, config):
    """Verify a passkey assertion for THIS user. Returns the credential row."""
    rp_id, origin, _ = rp_settings(config)
    if not challenge_b64:
        raise SecondFactorError('no_challenge', 'No passkey login in progress.')
    cred_id = credential.get('id') if isinstance(credential, dict) else None
    # The credential is looked up for THIS user only: a valid passkey that
    # belongs to another account must not complete this one's login.
    row = session.query(WebAuthnCredential).filter_by(
        user_guid=user.guid, credential_id=cred_id or '').first()
    if row is None:
        raise SecondFactorError('passkey_invalid', 'Unknown passkey for this account.', 401)
    try:
        verified = verify_authentication_response(
            credential=credential,
            expected_challenge=base64url_to_bytes(challenge_b64),
            expected_rp_id=rp_id, expected_origin=origin,
            credential_public_key=base64url_to_bytes(row.public_key),
            credential_current_sign_count=row.sign_count,
            require_user_verification=True)
    except (InvalidAuthenticationResponse, ValueError, KeyError, TypeError) as e:
        raise SecondFactorError('passkey_invalid', f'Passkey could not be verified: {e}', 401)
    row.sign_count = verified.new_sign_count
    row.last_used_at = _utcnow()
    return row


# --- TOTP -------------------------------------------------------------------

def begin_totp(session, user, secret_key):
    """Create (or replace) an UNCONFIRMED secret. Returns (base32, otpauth uri).

    Refused when a confirmed secret exists: replacing a working factor is a
    removal followed by a new enrolment, and removal has its own rules."""
    row = session.query(TotpSecret).filter_by(user_guid=user.guid).first()
    if row is not None and row.confirmed_at is not None:
        raise SecondFactorError('totp_exists', 'An authenticator app is already enrolled.', 409)
    secret = pyotp.random_base32()
    enc = secret_store.encrypt(secret_key, _ENC_PURPOSE, secret)
    if row is None:
        row = TotpSecret(user_guid=user.guid, secret_enc=enc)
        session.add(row)
    else:
        row.secret_enc = enc
        row.failed_attempts, row.locked_until, row.last_used_step = 0, None, None
    session.flush()
    uri = pyotp.TOTP(secret).provisioning_uri(name=user.email, issuer_name=TOTP_ISSUER)
    return secret, uri


def _check_code(row, code, secret_key, now=None):
    """Accept or refuse one code against one row, with lockout and replay
    guard. Mutates the row (caller's transaction). Raises SecondFactorError."""
    now_dt = _utcnow()
    locked = _aware(row.locked_until)
    if locked is not None and locked > now_dt:
        raise SecondFactorError('totp_locked',
                                'Too many wrong codes. Try again in 15 minutes.', 429)
    code = ''.join(ch for ch in (code or '') if ch.isdigit())
    secret = secret_store.decrypt(secret_key, _ENC_PURPOSE, row.secret_enc)
    totp = pyotp.TOTP(secret)
    current = int((now if now is not None else time.time()) // TOTP_STEP_SECONDS)
    matched = None
    for step in range(current - TOTP_WINDOW, current + TOTP_WINDOW + 1):
        if hmac.compare_digest(totp.at(step * TOTP_STEP_SECONDS), code):
            matched = step
            break
    if matched is None or (row.last_used_step is not None
                           and matched <= row.last_used_step):
        # #1045: counted IN THE DATABASE, then re-read. `row.failed_attempts
        # + 1` in Python overwrote failures other workers committed meanwhile,
        # so parallel wrong codes never reached the lock.
        s = object_session(row)
        if s is not None and row.id is not None:
            s.query(TotpSecret).filter_by(id=row.id).update(
                {TotpSecret.failed_attempts:
                 func.coalesce(TotpSecret.failed_attempts, 0) + 1},
                synchronize_session=False)
            s.flush()
            s.refresh(row, attribute_names=['failed_attempts'])
        else:
            row.failed_attempts = (row.failed_attempts or 0) + 1
        if row.failed_attempts >= TOTP_MAX_FAILURES:
            row.locked_until = now_dt + TOTP_LOCK
            row.failed_attempts = 0
        raise SecondFactorError('totp_invalid', 'Wrong or already used code.', 401)
    row.last_used_step = matched
    row.failed_attempts = 0
    row.locked_until = None


def confirm_totp(session, user, code, secret_key, now=None):
    """First valid code turns the pending secret into an enrolled factor."""
    row = session.query(TotpSecret).filter_by(user_guid=user.guid).first()
    if row is None or row.confirmed_at is not None:
        raise SecondFactorError('totp_not_started', 'No authenticator enrolment in progress.')
    _check_code(row, code, secret_key, now=now)
    row.confirmed_at = _utcnow()
    return row


def verify_totp(session, user, code, secret_key, now=None):
    """Verify a login code against the user's CONFIRMED secret."""
    row = confirmed_totp(session, user.guid)
    if row is None:
        raise SecondFactorError('totp_not_enrolled', 'No authenticator app is enrolled.', 401)
    _check_code(row, code, secret_key, now=now)
    return row


# --- removal / reset --------------------------------------------------------

def remove_factor(session, user, kind, guid=None):
    """Remove one factor. The LAST one cannot be removed by the user: an
    account must not fall back to password-only by its own hand (an SU reset
    is the way out of a lost device)."""
    methods = enrolled_methods(session, user.guid)
    if kind == METHOD_TOTP:
        row = confirmed_totp(session, user.guid)
        if row is None:
            raise SecondFactorError('not_found', 'No authenticator app is enrolled.', 404)
        if methods == [METHOD_TOTP]:
            raise SecondFactorError('last_factor', 'The last second factor cannot be removed.', 409)
        session.delete(row)
    elif kind == METHOD_PASSKEY:
        row = session.query(WebAuthnCredential).filter_by(
            user_guid=user.guid, guid=guid or '').first()
        if row is None:
            raise SecondFactorError('not_found', 'Passkey not found.', 404)
        if METHOD_TOTP not in methods and len(passkeys(session, user.guid)) == 1:
            raise SecondFactorError('last_factor', 'The last second factor cannot be removed.', 409)
        session.delete(row)
    else:
        raise SecondFactorError('invalid_request', 'kind must be passkey or totp.')
    session.flush()


def reset_all(session, user_guid):
    """SU only: drop every factor of a user. Returns how many were removed."""
    n = session.query(WebAuthnCredential).filter_by(
        user_guid=user_guid).delete(synchronize_session='fetch')
    n += session.query(TotpSecret).filter_by(
        user_guid=user_guid).delete(synchronize_session='fetch')
    return n


# --- re-enrolment after an SU reset (#1045) ---------------------------------
#
# Operator decision 2026-11-24, "engångskod i display". Before this, a reset
# account enrolled a new factor with its password alone, so whoever held the
# password between the reset and the real patient's next login could take
# the account over. Now the reset issues a one-time code, shown ONCE to the
# SU, who hands it to the patient after checking who they are. Until a new
# factor is enrolled the account is GATED:
#
#   * a password login gets no token at all, only the code page
#     (routes/second_factor_ui.py, `begin_patient_login`);
#   * the code is checked against THIS account, single use, 5 wrong entries
#     void it, it expires after REENROL_CODE_TTL; unknown, wrong, expired,
#     used and void all give the same refusal;
#   * the right code lets THE SESSION THAT TYPED IT enrol (a session grant
#     bound to this row's guid); any other session still meets the gate;
#   * enrolling a factor completes the row and lifts the gate.
#
# The code is 10 characters from the link-code alphabet (no 0/O, 1/I/L, U),
# shown as XXXXX-XXXXX: about 49 bits, read aloud in two groups. With five
# tries per code a guess succeeds with probability 5/30^10. Only a keyed hash
# of "<user_guid>:<code>" is stored (secret_store.keyed_hash).
#
# 72 hours: the code is given in person or by telephone, and the patient may
# not be at their phone or computer that day; 72 h spans a weekend (Friday
# afternoon to Monday) without a second call to the care unit. Longer would
# leave a code written on paper valid for no gain; the guessing bound comes
# from the five tries, not from the expiry.

REENROL_CODE_ALPHABET = 'ABCDEFGHJKMNPQRSTVWXYZ23456789'
REENROL_CODE_LENGTH = 10
REENROL_CODE_TTL = timedelta(hours=72)
REENROL_MAX_FAILURES = 5
#: How long a session that typed the right code may take to enrol.
REENROL_GRANT_SECONDS = 1800
_REENROL_PURPOSE = 'second-factor-reenrolment'


def _reenrol_hash(secret_key, user_guid, code):
    return secret_store.keyed_hash(secret_key, _REENROL_PURPOSE, f'{user_guid}:{code}')


def normalise_reenrol_code(value):
    return ''.join(ch for ch in (value or '').upper() if ch.isalnum())


def format_reenrol_code(code):
    half = REENROL_CODE_LENGTH // 2
    return f'{code[:half]}-{code[half:]}'


def pending_reenrolment(session, user_guid):
    """The open re-enrolment row (reset, factor not yet re-enrolled), or None."""
    return session.query(SecondFactorReenrolment).filter_by(
        user_guid=user_guid, completed_at=None).first()


def reenrolment_required(session, user_guid):
    return pending_reenrolment(session, user_guid) is not None


def issue_reenrolment_code(session, user_guid, secret_key, issued_by=None):
    """Called by the SU reset. Replaces any earlier row (and so any earlier
    code, and any session that used one). Returns (formatted code, row). The
    code is in the return value and nowhere else."""
    session.query(SecondFactorReenrolment).filter_by(
        user_guid=user_guid).delete(synchronize_session='fetch')
    session.flush()
    code = ''.join(secrets.choice(REENROL_CODE_ALPHABET)
                   for _ in range(REENROL_CODE_LENGTH))
    row = SecondFactorReenrolment(
        user_guid=user_guid, code_hash=_reenrol_hash(secret_key, user_guid, code),
        issued_by_user_guid=issued_by, expires_at=_utcnow() + REENROL_CODE_TTL,
        failed_attempts=0)
    session.add(row)
    session.flush()
    return format_reenrol_code(code), row


class ReenrolmentRefused(Exception):
    """One refusal for every reason. `reason` is for the audit log only and
    must not reach the person at the keyboard (no oracle)."""

    def __init__(self, reason, locked_now=False):
        super().__init__(reason)
        self.reason, self.locked_now = reason, locked_now


def redeem_reenrolment_code(session, user_guid, code, secret_key):
    """Check a typed code for THIS account. Returns the row (now used) or
    raises ReenrolmentRefused. A wrong code is counted IN THE DATABASE and
    COMMITTED here, because the caller's request ends in a refusal."""
    row = pending_reenrolment(session, user_guid)
    if row is None:
        raise ReenrolmentRefused('no_code')
    now = _utcnow()
    if row.used_at is not None:
        raise ReenrolmentRefused('used')
    if row.voided_at is not None or (row.failed_attempts or 0) >= REENROL_MAX_FAILURES:
        raise ReenrolmentRefused('void')
    if _aware(row.expires_at) <= now:
        raise ReenrolmentRefused('expired')
    typed = normalise_reenrol_code(code)
    if len(typed) == REENROL_CODE_LENGTH and hmac.compare_digest(
            _reenrol_hash(secret_key, user_guid, typed), row.code_hash):
        # Single use, also under a race: only the request whose UPDATE finds
        # used_at still NULL wins.
        won = session.query(SecondFactorReenrolment).filter_by(
            id=row.id, used_at=None, voided_at=None).update(
            {SecondFactorReenrolment.used_at: now}, synchronize_session=False)
        if won != 1:
            raise ReenrolmentRefused('used')
        session.flush()
        session.refresh(row)
        return row
    # Wrong: +1 in the database (not row.failed_attempts + 1 in Python, which
    # loses failures other workers counted meanwhile), then re-read.
    session.query(SecondFactorReenrolment).filter_by(id=row.id).update(
        {SecondFactorReenrolment.failed_attempts:
         func.coalesce(SecondFactorReenrolment.failed_attempts, 0) + 1},
        synchronize_session=False)
    session.flush()
    session.refresh(row)
    locked_now = False
    if (row.failed_attempts or 0) >= REENROL_MAX_FAILURES and row.voided_at is None:
        row.voided_at = now
        locked_now = True
    session.commit()
    raise ReenrolmentRefused('wrong', locked_now=locked_now)


REENROL_SHOWN_ONCE_TEXT = ('Visas bara en gång. Lämna koden till patienten efter att du '
                           'har kontrollerat vem det är.')


def su_reset(session, target, actor_guid, secret_key):
    """The whole SU reset, shared by the JSON route and the SU page: remove
    every factor, end every session, issue the re-enrolment code.
    Returns (removed, formatted code, row). The caller audits WITHOUT the code."""
    removed = reset_all(session, target.guid)
    target.token_revocation_epoch = _utcnow()
    code, row = issue_reenrolment_code(session, target.guid, secret_key,
                                       issued_by=actor_guid)
    return removed, code, row


def complete_reenrolment(session, user_guid):
    """A new factor was enrolled: lift the gate. Returns True if one was open."""
    row = pending_reenrolment(session, user_guid)
    if row is None:
        return False
    row.completed_at = _utcnow()
    session.flush()
    return True
