"""Browser side of #1040: the second factor at login, enrolment, the patient
invite page, and the linking forms. Session-cookie pages, CSRF-protected (this
blueprint is NOT exempted in app.py).

The login rule for a PATIENT account, in one place:

  password OK, factor enrolled      -> NO token yet. The session holds only
                                       `sf_pending` (who, and where they were
                                       going). The token is issued by
                                       `_finish_login` after the factor.
  password OK, nothing enrolled     -> a token WITHOUT the second-factor claim
                                       (so the blob says verified: false) and
                                       a redirect to /second-factor to enrol.
                                       Enrolling proves possession, so the
                                       session token is then re-issued WITH
                                       the claim.
  handshake to a service (`next`)   -> only ever with a verified token. An
                                       unverified patient session is sent to
                                       enrol / step up first and resumes after.
  password OK, factor RESET by an   -> NO token. The session holds only
  SU and not yet re-enrolled           `sf_reenrol_pending`, and the page asks
  (#1045)                              for the one-time code the SU gave the
                                       patient. The right code gives the
                                       unenrolled token above plus a session
                                       grant (`sf_reenrol`) that alone lets
                                       THIS session enrol. Without the code:
                                       no token, no enrolment, no handshake.

PROFESSIONALS (#1055, record.pdhc stage 3, decision D4): the same factor,
the same models, the same pages, but OPTIONAL. Enrolment is never forced,
because every PDHC service relies on a professional's SSO login:

  password OK, nothing enrolled     -> exactly as before #1055: a token
  (and no SU reset pending)            without the claim, straight on to the
                                       dashboard or the service. Nothing here
                                       is called (`professional_needs_step`).
  password OK, factor enrolled      -> the patient row above: `sf_pending`,
                                       the factor, then the verified token.
  handshake to a service (`next`)   -> an enrolled professional's service
                                       token carries the session's claim; an
                                       unverified session steps up first.
  factor RESET by an SU             -> the one-time code page, as for a
                                       patient. The difference: for a
                                       professional the right code ENDS the
                                       reset (`complete_reenrolment`), so the
                                       account is back to "nothing enrolled"
                                       and logs in with the password; adding a
                                       new factor is offered, not required. A
                                       patient's reset ends only on enrolment.
"""
import time
from urllib.parse import urlencode

from flask import (
    Blueprint, current_app, flash, jsonify, redirect, render_template,
    request, session, url_for,
)

from src.db import get_db
from src.middleware.rate_limit import rate_limit
from src.services import patient_link as pl
from src.services import second_factor as sf
from src.services.audit_log import audit
from src.services.jwt_service import (
    TokenExpiredError, TokenInvalidError, TokenRevokedError, issue_token,
    validate_token,
)

sf_ui_bp = Blueprint('second_factor_ui', __name__, template_folder='../templates')

#: How long a password-verified login may wait for its second factor.
PENDING_MAX_AGE_SECONDS = 600


# --- helpers ----------------------------------------------------------------

def _cfg():
    return current_app.config


def _session_user():
    from src.routes.frontend import _get_session_user
    return _get_session_user()


def session_claim():
    """The second-factor claim of the SESSION token, or None."""
    token = session.get('token')
    if not token:
        return None
    try:
        payload = validate_token(token, _cfg().get('SECRET_KEY', ''), get_db())
    except (TokenExpiredError, TokenInvalidError, TokenRevokedError):
        return None
    return sf.claim_from_payload(payload)


def _issue(user, claim=None):
    return issue_token(user.guid, _cfg().get('SECRET_KEY', ''),
                       expiry_hours=_cfg().get('SESSION_EXPIRY_HOURS', 24),
                       second_factor=claim)


def callback_allowed(next_url):
    from src.routes.auth import _is_allowed_callback
    return bool(next_url) and _is_allowed_callback(next_url, _cfg())


def _destination(token, resume):
    """Where a completed login goes: the service callback (allow-listed only),
    else the SSO dashboard."""
    next_url = (resume or {}).get('next') or ''
    state = (resume or {}).get('state') or ''
    if next_url:
        if callback_allowed(next_url):
            params = {'token': token}
            if state:
                params['state'] = state
            return f"{next_url}?{urlencode(params)}"
        flash('Callback URL not in allowlist.', 'warning')
    return url_for('frontend.dashboard')


def _pending_user():
    """The user whose password was just accepted and who still owes a second
    factor, or None (no pending login, or it is too old)."""
    pending = session.get('sf_pending')
    if not pending:
        return None
    if time.time() - pending.get('at', 0) > PENDING_MAX_AGE_SECONDS:
        session.pop('sf_pending', None)
        return None
    from src.models.user import User
    return get_db().query(User).filter_by(guid=pending.get('u')).first()


def reenrol_grant_ok(user):
    """#1045: did THIS session type the right re-enrolment code for THIS
    user's CURRENT reset, recently? A new reset (new row guid) ends it."""
    grant = session.get('sf_reenrol') or {}
    if grant.get('u') != user.guid:
        return False
    if time.time() - grant.get('at', 0) > sf.REENROL_GRANT_SECONDS:
        return False
    row = sf.pending_reenrolment(get_db(), user.guid)
    return row is not None and row.guid == grant.get('r') and row.used_at is not None


def _reenrol_pending_user():
    """The user whose password was accepted and who now owes the one-time
    re-enrolment code, or None."""
    pending = session.get('sf_reenrol_pending')
    if not pending:
        return None
    if time.time() - pending.get('at', 0) > PENDING_MAX_AGE_SECONDS:
        session.pop('sf_reenrol_pending', None)
        return None
    from src.models.user import User
    return get_db().query(User).filter_by(guid=pending.get('u')).first()


def professional_needs_step(user):
    """#1055: does this PROFESSIONAL's login go through the second-factor
    pages? Only when a factor is enrolled, or an SU reset is waiting for its
    one-time code. A professional with neither logs in exactly as before."""
    if user is None or user.user_type == 'patient':
        return False
    db = get_db()
    return sf.is_enrolled(db, user.guid) or sf.reenrolment_required(db, user.guid)


def begin_patient_login(user, next_url, state, email=None):
    """Called by /login once a PATIENT's password is accepted, and (#1055)
    once a PROFESSIONAL's is accepted when `professional_needs_step`."""
    db = get_db()
    session.pop('token', None)
    session.pop('wa_auth', None)
    session.pop('sf_reenrol_pending', None)
    # A destination left over from an earlier, abandoned login must not win
    # over the one this login asked for.
    session.pop('sf_resume', None)
    if (not sf.is_enrolled(db, user.guid) and sf.reenrolment_required(db, user.guid)
            and not reenrol_grant_ok(user)):
        # #1045: reset by an SU. The password alone gets nothing here: no
        # token, no enrolment. The code page is the only way on.
        session.pop('sf_pending', None)
        session['sf_reenrol_pending'] = {'u': user.guid, 'next': next_url or '',
                                         'state': state or '', 'at': int(time.time())}
        audit('login_password_ok', user_guid=user.guid,
              detail={'email': email, 'second_factor': 'reenrolment_code_required'},
              ip=request.remote_addr)
        return redirect(url_for('second_factor_ui.reenrol_code'))
    if sf.is_enrolled(db, user.guid):
        session['sf_pending'] = {'u': user.guid, 'next': next_url or '',
                                 'state': state or '', 'at': int(time.time())}
        audit('login_password_ok', user_guid=user.guid,
              detail={'email': email, 'second_factor': 'pending'},
              ip=request.remote_addr)
        return redirect(url_for('second_factor_ui.login_step'))
    session.pop('sf_pending', None)
    session['token'] = _issue(user)
    session['sf_resume'] = {'next': next_url or '', 'state': state or ''}
    audit('login_success', user_guid=user.guid,
          detail={'email': email, 'second_factor': 'not_enrolled'},
          ip=request.remote_addr)
    return redirect(url_for('second_factor_ui.manage'))


def patient_handshake(user, next_url, state):
    """Called by /login when a logged-in PATIENT arrives with `next`, and
    (#1055) a logged-in PROFESSIONAL for whom `professional_needs_step`.

    Returns a redirect. A token is minted for the service only when the
    session token is second-factor verified; the claim is carried over, so the
    service's token says the same thing the session's does."""
    claim = session_claim()
    if claim is not None and sf.is_enrolled(get_db(), user.guid):
        return redirect(_destination(_issue(user, claim),
                                     {'next': next_url, 'state': state}))
    return begin_patient_login(user, next_url, state, email=user.email)


def _finish_login(user, method):
    """The second factor was presented: issue the verified token."""
    pending = session.pop('sf_pending', None) or {}
    resume = session.pop('sf_resume', None) or pending
    token = _issue(user, sf.new_claim(method))
    session['token'] = token
    audit('login_success', user_guid=user.guid,
          detail={'email': user.email, 'second_factor': method},
          ip=request.remote_addr)
    return _destination(token, resume)


def _upgrade_after_enrolment(user, method):
    """Enrolment proved possession of the factor: re-issue the session token
    with the claim. Returns where to go next."""
    # #1045: a new factor ends a reset's gate (the code was already used).
    if sf.complete_reenrolment(get_db(), user.guid):
        audit('second_factor_reenrolment_complete', user_guid=user.guid,
              detail={'method': method}, ip=request.remote_addr)
    session.pop('sf_reenrol', None)
    resume = session.pop('sf_resume', None)
    token = _issue(user, sf.new_claim(method))
    session['token'] = token
    if (resume or {}).get('next'):
        return _destination(token, resume)
    # #1050: an accepted invite goes on to the patient app's login (a plain
    # link from config, never from the request; no token rides on it: the
    # app's own login fetches one through /login?next=).
    if (resume or {}).get('after'):
        return resume['after']
    return url_for('second_factor_ui.manage')


def _may_change_factors(user):
    """Adding or removing a factor on an ENROLLED account needs a session that
    itself presented one. Not enrolled yet: the password session may enrol,
    EXCEPT after an SU reset (#1045): then only a session that typed the
    one-time re-enrolment code may."""
    db = get_db()
    if sf.is_enrolled(db, user.guid):
        return session_claim() is not None
    if sf.reenrolment_required(db, user.guid):
        return reenrol_grant_ok(user)
    return True


def _json_error(e):
    return jsonify({"error": e.code, "message": e.message}), e.status


def _patient_or_none():
    user = _session_user()
    return user if user is not None and user.user_type == 'patient' else None


def _account_or_none():
    """#1055: the factor pages serve patients AND professionals (SU included)."""
    return _session_user()


# --- login step-up ----------------------------------------------------------

@sf_ui_bp.route('/login/second-factor', methods=['GET'])
def login_step():
    user = _pending_user()
    if user is None:
        flash('Logga in igen.', 'warning')
        return redirect(url_for('frontend.login_page'))
    methods = sf.enrolled_methods(get_db(), user.guid)
    return render_template(
        'second_factor_login.html', methods=methods,
        is_patient=(user.user_type == 'patient'),
        passkey_ready=('passkey' in methods and sf.passkeys_configured(_cfg())))


@sf_ui_bp.route('/login/second-factor/passkey/options', methods=['POST'])
@rate_limit(max_requests=20, window_seconds=60)
def login_passkey_options():
    user = _pending_user()
    if user is None:
        return jsonify({"error": "no_pending_login", "message": "Logga in igen."}), 401
    try:
        options, challenge = sf.authentication_options(get_db(), user, _cfg())
    except sf.SecondFactorError as e:
        return _json_error(e)
    session['wa_auth'] = challenge
    return jsonify(options), 200


@sf_ui_bp.route('/login/second-factor/passkey/verify', methods=['POST'])
@rate_limit(max_requests=20, window_seconds=60)
def login_passkey_verify():
    user = _pending_user()
    if user is None:
        return jsonify({"error": "no_pending_login", "message": "Logga in igen."}), 401
    # One challenge, one attempt: popped before it is checked.
    challenge = session.pop('wa_auth', None)
    try:
        sf.finish_authentication(get_db(), user, request.get_json(silent=True) or {},
                                 challenge, _cfg())
    except sf.SecondFactorError as e:
        audit('second_factor_fail', user_guid=user.guid,
              detail={'method': 'passkey', 'reason': e.code}, ip=request.remote_addr)
        return _json_error(e)
    return jsonify({"redirect": _finish_login(user, sf.METHOD_PASSKEY)}), 200


@sf_ui_bp.route('/login/second-factor/totp', methods=['POST'])
@rate_limit(max_requests=20, window_seconds=60)
def login_totp():
    user = _pending_user()
    if user is None:
        flash('Logga in igen.', 'warning')
        return redirect(url_for('frontend.login_page'))
    try:
        sf.verify_totp(get_db(), user, request.form.get('code', ''),
                       _cfg().get('SECRET_KEY', ''))
    except sf.SecondFactorError as e:
        audit('second_factor_fail', user_guid=user.guid,
              detail={'method': 'totp', 'reason': e.code}, ip=request.remote_addr)
        flash('För många fel. Försök igen om 15 minuter.' if e.code == 'totp_locked'
              else 'Fel kod, eller en kod som redan har använts.', 'error')
        return redirect(url_for('second_factor_ui.login_step'))
    return redirect(_finish_login(user, sf.METHOD_TOTP))


# --- the one-time re-enrolment code after an SU reset (#1045) ----------------

#: One text for every refusal (unknown, wrong, expired, used, void): no oracle.
REENROL_REFUSED_TEXT = ('Koden kunde inte godtas. Kontakta din vårdenhet för att '
                        'få en ny kod.')


@sf_ui_bp.route('/login/reenrol-code', methods=['GET', 'POST'])
@rate_limit(max_requests=20, window_seconds=60)
def reenrol_code():
    user = _reenrol_pending_user()
    if user is None:
        flash('Logga in igen.', 'warning')
        return redirect(url_for('frontend.login_page'))
    if request.method == 'GET':
        return render_template('second_factor_reenrol.html',
                               is_patient=(user.user_type == 'patient'))
    db = get_db()
    try:
        row = sf.redeem_reenrolment_code(db, user.guid, request.form.get('code', ''),
                                         _cfg().get('SECRET_KEY', ''))
    except sf.ReenrolmentRefused as e:
        # Audited by reason; the typed value is never written anywhere.
        audit('second_factor_reenrol_code_refused', user_guid=user.guid,
              detail={'reason': e.reason}, ip=request.remote_addr)
        if e.locked_now:
            audit('second_factor_reenrol_code_locked', user_guid=user.guid,
                  detail={'failures': sf.REENROL_MAX_FAILURES}, ip=request.remote_addr)
        flash(REENROL_REFUSED_TEXT, 'error')
        return render_template('second_factor_reenrol.html',
                               is_patient=(user.user_type == 'patient')), 400
    pending = session.pop('sf_reenrol_pending', None) or {}
    session['sf_reenrol'] = {'u': user.guid, 'r': row.guid, 'at': int(time.time())}
    session['token'] = _issue(user)          # no claim: the factor is still to come
    audit('second_factor_reenrol_code_used', user_guid=user.guid,
          detail={'reenrolment_guid': row.guid}, ip=request.remote_addr)
    if user.user_type != 'patient':
        # #1055: a professional's factor is optional, so the code ends the
        # reset here and the account is back to "nothing enrolled" (password
        # login, as for any professional without a factor). Waiting for a new
        # enrolment, as for a patient, would shut a professional out of every
        # PDHC service once the 30-minute session grant lapsed.
        sf.complete_reenrolment(db, user.guid)
        session.pop('sf_reenrol', None)
        flash('Koden stämde. Du är inloggad. Lägg gärna till en ny passkey eller '
              'autentiseringsapp; den behövs för att signera.', 'success')
        return redirect(_destination(session['token'], pending)
                        if pending.get('next') else url_for('second_factor_ui.manage'))
    session['sf_resume'] = {'next': pending.get('next') or '',
                            'state': pending.get('state') or ''}
    flash('Koden stämde. Lägg nu till en ny passkey eller autentiseringsapp.', 'success')
    return redirect(url_for('second_factor_ui.manage'))


# --- enrolment / management -------------------------------------------------

def _manage_page(user, totp_setup=None):
    db = get_db()
    return render_template(
        'second_factor.html',
        passkeys=sf.passkeys(db, user.guid),
        totp_enrolled=sf.confirmed_totp(db, user.guid) is not None,
        enrolled=sf.is_enrolled(db, user.guid),
        verified=session_claim() is not None,
        passkey_ready=sf.passkeys_configured(_cfg()),
        totp_setup=totp_setup,
        resume_pending=bool((session.get('sf_resume') or {}).get('next')),
        is_patient=(user.user_type == 'patient'),
        linked=pl.current_link(db, user.guid) is not None,
        patientapp_url=_cfg().get('PATIENTAPP_URL') or '')


@sf_ui_bp.route('/second-factor', methods=['GET'])
def manage():
    user = _account_or_none()
    if user is None:
        return redirect(url_for('frontend.login_page'))
    return _manage_page(user)


@sf_ui_bp.route('/second-factor/passkey/options', methods=['POST'])
@rate_limit(max_requests=20, window_seconds=60)
def enrol_passkey_options():
    user = _account_or_none()
    if user is None:
        return jsonify({"error": "not_logged_in", "message": "Logga in igen."}), 401
    if not _may_change_factors(user):
        return jsonify({"error": "second_factor_required",
                        "message": "Logga in med din andra faktor först."}), 403
    try:
        options, challenge = sf.registration_options(get_db(), user, _cfg())
    except sf.SecondFactorError as e:
        return _json_error(e)
    session['wa_reg'] = challenge
    return jsonify(options), 200


@sf_ui_bp.route('/second-factor/passkey/verify', methods=['POST'])
@rate_limit(max_requests=20, window_seconds=60)
def enrol_passkey_verify():
    user = _account_or_none()
    if user is None:
        return jsonify({"error": "not_logged_in", "message": "Logga in igen."}), 401
    challenge = session.pop('wa_reg', None)
    if not _may_change_factors(user):
        return jsonify({"error": "second_factor_required",
                        "message": "Logga in med din andra faktor först."}), 403
    body = request.get_json(silent=True) or {}
    try:
        row = sf.finish_registration(get_db(), user, body.get('credential') or {},
                                     challenge, _cfg(), name=body.get('name'))
    except sf.SecondFactorError as e:
        return _json_error(e)
    audit('second_factor_enrol', user_guid=user.guid,
          detail={'method': 'passkey', 'credential_guid': row.guid},
          ip=request.remote_addr)
    return jsonify({"redirect": _upgrade_after_enrolment(user, sf.METHOD_PASSKEY)}), 201


@sf_ui_bp.route('/second-factor/totp/start', methods=['POST'])
@rate_limit(max_requests=20, window_seconds=60)
def enrol_totp_start():
    user = _account_or_none()
    if user is None:
        return redirect(url_for('frontend.login_page'))
    if not _may_change_factors(user):
        flash('Logga in med din andra faktor först.', 'error')
        return redirect(url_for('second_factor_ui.manage'))
    try:
        secret, uri = sf.begin_totp(get_db(), user, _cfg().get('SECRET_KEY', ''))
    except sf.SecondFactorError as e:
        flash(e.message, 'error')
        return redirect(url_for('second_factor_ui.manage'))
    resp = current_app.make_response(
        _manage_page(user, totp_setup={'secret': secret, 'uri': uri}))
    resp.headers['Cache-Control'] = 'no-store'
    return resp


@sf_ui_bp.route('/second-factor/totp/confirm', methods=['POST'])
@rate_limit(max_requests=20, window_seconds=60)
def enrol_totp_confirm():
    user = _account_or_none()
    if user is None:
        return redirect(url_for('frontend.login_page'))
    if not _may_change_factors(user):
        flash('Logga in med din andra faktor först.', 'error')
        return redirect(url_for('second_factor_ui.manage'))
    try:
        sf.confirm_totp(get_db(), user, request.form.get('code', ''),
                        _cfg().get('SECRET_KEY', ''))
    except sf.SecondFactorError as e:
        flash('Koden stämde inte. Börja om och försök igen.'
              if e.code == 'totp_invalid' else e.message, 'error')
        return redirect(url_for('second_factor_ui.manage'))
    audit('second_factor_enrol', user_guid=user.guid, detail={'method': 'totp'},
          ip=request.remote_addr)
    flash('Autentiseringsappen är kopplad.', 'success')
    return redirect(_upgrade_after_enrolment(user, sf.METHOD_TOTP))


@sf_ui_bp.route('/second-factor/remove', methods=['POST'])
def remove_factor():
    user = _account_or_none()
    if user is None:
        return redirect(url_for('frontend.login_page'))
    if session_claim() is None:
        flash('Logga in med din andra faktor först.', 'error')
        return redirect(url_for('second_factor_ui.manage'))
    kind = request.form.get('kind', '')
    try:
        sf.remove_factor(get_db(), user, kind, guid=request.form.get('guid'))
    except sf.SecondFactorError as e:
        flash('Den sista andra faktorn kan inte tas bort.'
              if e.code == 'last_factor' else e.message, 'error')
        return redirect(url_for('second_factor_ui.manage'))
    audit('second_factor_remove', user_guid=user.guid,
          detail={'method': kind, 'credential_guid': request.form.get('guid')},
          ip=request.remote_addr)
    flash('Borttagen.', 'success')
    return redirect(url_for('second_factor_ui.manage'))


# --- entrance 1: the invite page --------------------------------------------

@sf_ui_bp.route('/patient-invite/<token>', methods=['GET', 'POST'])
@rate_limit(max_requests=20, window_seconds=60)
def patient_invite(token):
    """The one-time link care sent. UNAUTHENTICATED by necessity (the account
    does not exist yet); the token is the credential. Unknown, used, expired
    and revoked all render the same page."""
    from src.services.patient_enrolment import EnrolmentRefused
    db = get_db()
    secret = _cfg().get('SECRET_KEY', '')
    invite = pl.find_valid_invite(db, token, secret)
    if invite is None:
        return render_template('patient_invite.html', invalid=True), 404
    from src.models.organisation import Organisation
    unit = db.query(Organisation).filter_by(guid=invite.care_unit_guid).first()
    unit_name = unit.name if unit else ''
    detail = pl.invite_detail(db, invite)
    patient_name = (f'{detail.given_name} {detail.family_name}'
                    if detail and detail.given_name else '')

    def page(status=200, invalid=False):
        return render_template('patient_invite.html', invalid=invalid,
                               unit_name=unit_name, patient_name=patient_name,
                               email=request.form.get('email', '') if not invalid else ''), status

    if request.method == 'POST':
        try:
            user = pl.accept_invite(
                db, token, email=request.form.get('email'),
                password=request.form.get('password'),
                personnummer=request.form.get('personnummer'),
                secret_key=secret, ip=request.remote_addr)
        except EnrolmentRefused as e:
            for m in e.messages:
                flash(m[0].upper() + m[1:] + ('' if m.endswith('.') else '.'), 'error')
            if e.code == 'conflict':
                flash('Har du redan ett konto? Logga in och ge din vårdenhet '
                      'en länkkod i stället.', 'warning')
            return page(e.status)
        except pl.LinkRefused as e:
            if e.code != 'invite_not_found':
                flash(e.message, 'error')
            return page(e.status, invalid=True)
        # Logged in with the password just chosen; no second factor yet, so
        # the token carries no claim and the next page is enrolment. After
        # it (#1050) the patient is sent to the patient app's login.
        session.pop('sf_pending', None)
        session['token'] = _issue(user)
        app_url = (_cfg().get('PATIENTAPP_URL') or '').strip().rstrip('/')
        session['sf_resume'] = {'next': '', 'state': '',
                                'after': f'{app_url}/auth/login' if app_url else ''}
        flash('Kontot är skapat och kopplat till din vårdenhet. '
              'Lägg nu till en passkey.', 'success')
        return redirect(url_for('second_factor_ui.manage'))

    return page()


# --- entrance 2 (patient side) and the care forms ---------------------------

@sf_ui_bp.route('/patient-link/code', methods=['POST'])
@rate_limit(max_requests=10, window_seconds=60)
def patient_link_code():
    user = _patient_or_none()
    if user is None:
        return redirect(url_for('frontend.login_page'))
    try:
        code, expires = pl.create_link_code(get_db(), user, _cfg().get('SECRET_KEY', ''),
                                            ip=request.remote_addr)
    except pl.LinkRefused as e:
        flash(e.message, 'error')
        return redirect(url_for('frontend.dashboard'))
    resp = current_app.make_response(render_template(
        'patient_link_code.html', code=code, expires_at=pl._aware(expires)))
    resp.headers['Cache-Control'] = 'no-store'
    return resp


def _care_actor():
    user = _session_user()
    if user is None:
        return None
    return user if (user.user_type == 'professional' or user.is_su_admin) else None


@sf_ui_bp.route('/patient-link/invite', methods=['POST'])
@rate_limit(max_requests=30, window_seconds=60)
def care_invite():
    actor = _care_actor()
    if actor is None:
        return redirect(url_for('frontend.login_page'))
    try:
        invite, token = pl.create_invite(
            get_db(), actor, ips_patient_guid=request.form.get('ips_patient_guid'),
            care_unit_guid=request.form.get('care_unit_guid'),
            secret_key=_cfg().get('SECRET_KEY', ''), ip=request.remote_addr)
    except pl.LinkRefused as e:
        flash(e.message, 'error')
        return redirect(url_for('frontend.dashboard'))
    from src.routes.patient_access import invite_url
    resp = current_app.make_response(render_template(
        'patient_invite_created.html', invite_url=invite_url(token),
        expires_at=pl._aware(invite.expires_at),
        ips_patient_guid=invite.ips_patient_guid))
    resp.headers['Cache-Control'] = 'no-store'
    return resp


@sf_ui_bp.route('/patient-link/redeem', methods=['POST'])
@rate_limit(max_requests=10, window_seconds=60)
def care_redeem():
    actor = _care_actor()
    if actor is None:
        return redirect(url_for('frontend.login_page'))
    try:
        pl.redeem_link_code(
            get_db(), actor, code=request.form.get('link_code'),
            ips_patient_guid=request.form.get('ips_patient_guid'),
            secret_key=_cfg().get('SECRET_KEY', ''), ip=request.remote_addr)
    except pl.LinkRefused as e:
        flash(e.message, 'error')
        return redirect(url_for('frontend.dashboard'))
    flash('Patient account linked.', 'success')
    return redirect(url_for('frontend.dashboard'))
