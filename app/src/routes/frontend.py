"""Frontend routes — serves HTML templates for the web UI.

Uses Flask session (cookie) to store JWT token for authenticated views.
All form actions call the existing service layer directly (not HTTP API).
"""
import csv
import io
import os
import re

from flask import (
    Blueprint, render_template, request, redirect, url_for,
    flash, session, g, current_app, send_from_directory, abort,
    jsonify,
)

from src.db import get_db
from src.services.jwt_service import issue_token, validate_token, revoke_token
from src.services.jwt_service import TokenExpiredError, TokenInvalidError, TokenRevokedError
from src.services.auth_service import authenticate_user, hash_password, verify_password
from src.services.audit_log import audit

frontend_bp = Blueprint('frontend', __name__, template_folder='../templates')


# ---------------------------------------------------------------------------
# Context processor: inject current_user + is_group_admin into all templates
# ---------------------------------------------------------------------------

@frontend_bp.before_app_request
def _force_password_change_gate():
    """Ticket #43: block protected frontend paths until pending SU-reset is
    resolved.

    Allows: /login, /logout, /change-password, /static/*, /api/*, /fhir/*,
    /docs/*. Everything else redirects to /change-password when the logged-in
    user has force_change_on_next_login=True.
    """
    from flask import request as _req
    path = _req.path
    # Never touch API / FHIR / static / asset / auth paths.
    if (path.startswith('/api/') or path.startswith('/fhir/')
            or path.startswith('/static/') or path.startswith('/docs/')
            or path in ('/change-password', '/login', '/logout', '/favicon.ico')):
        return None
    try:
        user = _get_session_user()
    except Exception:
        return None
    if user is not None and getattr(user, 'force_change_on_next_login', False):
        flash('An administrator reset your password. Please choose a new one.', 'warning')
        return redirect(url_for('frontend.change_password'))
    return None


@frontend_bp.app_context_processor
def inject_user():
    """Make current_user and is_group_admin available in all templates.

    Only performs DB lookups for frontend routes (not /api/) to avoid
    interfering with API request DB sessions.
    """
    from flask import request as current_request
    try:
        path = current_request.path
    except RuntimeError:
        return dict(current_user=None, is_group_admin=False)

    # Skip DB lookups for API routes
    if path.startswith('/api/') or path.startswith('/fhir/'):
        return dict(current_user=None, is_group_admin=False)

    try:
        user = _get_session_user()
    except Exception:
        return dict(current_user=None, is_group_admin=False)
    is_ga = False
    if user and user.user_type == 'professional':
        try:
            from src.models.membership import Membership
            db = get_db()
            is_ga = db.query(Membership).filter_by(
                user_guid=user.guid, status='approved', is_admin=True
            ).first() is not None
        except Exception:
            pass
    if user and user.is_su_admin:
        is_ga = True
    return dict(current_user=user, is_group_admin=is_ga)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_session_user():
    """Load user from JWT stored in Flask session. Returns User or None."""
    token = session.get('token')
    if not token:
        return None
    secret = current_app.config.get('SECRET_KEY', '')
    db = get_db()
    try:
        payload = validate_token(token, secret, db)
    except (TokenExpiredError, TokenInvalidError, TokenRevokedError):
        session.pop('token', None)
        return None
    from src.models.user import User
    return db.query(User).filter_by(guid=payload['sub']).first()


def _require_login(f):
    """Decorator: redirect to /login if not authenticated."""
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if _get_session_user() is None:
            flash('Please log in to continue.', 'warning')
            return redirect(url_for('frontend.login_page'))
        return f(*args, **kwargs)
    return decorated


def _require_su_login(f):
    """Decorator: require SU admin."""
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        user = _get_session_user()
        if user is None:
            flash('Please log in to continue.', 'warning')
            return redirect(url_for('frontend.login_page'))
        if not user.is_su_admin:
            flash('SU admin access required.', 'error')
            return redirect(url_for('frontend.dashboard'))
        return f(*args, **kwargs)
    return decorated


def _require_group_admin_login(f):
    """Decorator: require group admin or SU."""
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        user = _get_session_user()
        if user is None:
            flash('Please log in to continue.', 'warning')
            return redirect(url_for('frontend.login_page'))
        if user.is_su_admin:
            return f(*args, **kwargs)
        from src.models.membership import Membership
        db = get_db()
        admin_m = db.query(Membership).filter_by(
            user_guid=user.guid, status='approved', is_admin=True
        ).first()
        if admin_m is None:
            flash('Group admin access required.', 'error')
            return redirect(url_for('frontend.dashboard'))
        return f(*args, **kwargs)
    return decorated


# ---------------------------------------------------------------------------
# 9.h — Landing page / service list
# ---------------------------------------------------------------------------

@frontend_bp.route('/')
def landing():
    """Landing page with service list from oath_overview.csv."""
    services = []
    oath_path = os.path.join(current_app.root_path, '..', 'oath_overview.csv')
    if os.path.exists(oath_path):
        with open(oath_path, 'r') as f:
            services = list(csv.DictReader(f))
    return render_template('landing.html', services=services)


# ---------------------------------------------------------------------------
# 9.b — Login / Logout
# ---------------------------------------------------------------------------

@frontend_bp.route('/login', methods=['GET', 'POST'])
def login_page():
    """Login page with SSO handshake support."""
    next_url = request.args.get('next', '')
    state = request.args.get('state', '')

    # Auto-redirect: if already logged in and next is provided
    if next_url and _get_session_user() is not None:
        from urllib.parse import urlencode
        secret = current_app.config.get('SECRET_KEY', '')
        expiry_hours = current_app.config.get('SESSION_EXPIRY_HOURS', 24)
        user = _get_session_user()
        token = issue_token(user.guid, secret, expiry_hours=expiry_hours)
        params = {'token': token}
        if state:
            params['state'] = state
        return redirect(f"{next_url}?{urlencode(params)}")

    if _get_session_user() is not None and not next_url:
        return redirect(url_for('frontend.dashboard'))

    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        next_url = request.form.get('next', '') or next_url
        state = request.form.get('state', '') or state

        db = get_db()
        user = authenticate_user(email, password, db)
        if user is None:
            audit('login_fail', detail={'reason': 'invalid_credentials', 'email': email},
                  ip=request.remote_addr)
            flash('Invalid email or password.', 'error')
            return render_template('login.html', next_url=next_url, state=state)

        secret = current_app.config.get('SECRET_KEY', '')
        expiry_hours = current_app.config.get('SESSION_EXPIRY_HOURS', 24)
        token = issue_token(user.guid, secret, expiry_hours=expiry_hours)
        session['token'] = token

        audit('login_success', user_guid=user.guid, detail={'email': email},
              ip=request.remote_addr)

        # SSO handshake redirect
        if next_url:
            allowed_urls = current_app.config.get('ALLOWED_CALLBACK_URLS', [])
            allowed_origins = current_app.config.get('ALLOWED_ORIGINS', [])
            from urllib.parse import urlparse, urlencode
            parsed = urlparse(next_url)
            origin = f"{parsed.scheme}://{parsed.netloc}"
            if next_url in allowed_urls or origin in allowed_origins:
                params = {'token': token}
                if state:
                    params['state'] = state
                return redirect(f"{next_url}?{urlencode(params)}")
            else:
                flash('Callback URL not in allowlist.', 'warning')

        return redirect(url_for('frontend.dashboard'))

    return render_template('login.html', next_url=next_url, state=state)


@frontend_bp.route('/logout', methods=['POST'])
def logout_page():
    """Logout — revoke token and clear session."""
    token = session.get('token')
    if token:
        secret = current_app.config.get('SECRET_KEY', '')
        db = get_db()
        try:
            payload = validate_token(token, secret, db)
            from datetime import datetime, timezone
            exp = payload.get('exp')
            expires_at = datetime.fromtimestamp(exp, tz=timezone.utc) if exp else None
            if payload.get('jti') and expires_at:
                revoke_token(payload['jti'], expires_at, db)
                db.commit()
            audit('logout', user_guid=payload.get('sub'), ip=request.remote_addr)
        except (TokenExpiredError, TokenInvalidError, TokenRevokedError):
            pass
    session.pop('token', None)
    flash('Logged out successfully.', 'success')
    return redirect(url_for('frontend.login_page'))


# ---------------------------------------------------------------------------
# 9.c — Dashboard
# ---------------------------------------------------------------------------

@frontend_bp.route('/dashboard')
@_require_login
def dashboard():
    """Dashboard — shows user profile, groups, registry status."""
    user = _get_session_user()
    db = get_db()

    professional = None
    patient = None
    groups = []
    org_names = []

    if user.user_type == 'professional':
        from src.models.professional import Professional
        professional = db.query(Professional).filter_by(user_id=user.id).first()

        from src.models.membership import Membership
        from src.models.group import Group
        memberships = db.query(Membership).filter_by(user_guid=user.guid).all()
        for m in memberships:
            grp = db.query(Group).filter_by(guid=m.group_guid).first()
            if grp:
                groups.append({
                    'group_name': grp.name,
                    'category': grp.category,
                    'status': m.status,
                    'is_admin': m.is_admin,
                })

        from src.models.user_organisation import UserOrganisation
        from src.models.organisation import Organisation
        user_orgs = db.query(UserOrganisation).filter_by(user_guid=user.guid).all()
        for uo in user_orgs:
            org = db.query(Organisation).filter_by(guid=uo.organisation_guid).first()
            if org:
                org_names.append(org.name)

    elif user.user_type == 'patient':
        from src.models.patient import Patient
        patient = db.query(Patient).filter_by(user_id=user.id).first()

    return render_template('dashboard.html',
                           professional=professional, patient=patient,
                           groups=groups, org_names=org_names)


# ---------------------------------------------------------------------------
# 9.d — SU Admin page
# ---------------------------------------------------------------------------

@frontend_bp.route('/admin')
@_require_su_login
def admin_page():
    """SU admin panel — users, orgs, requests, groups, oath overview."""
    db = get_db()

    from src.models.user import User
    from src.models.professional import Professional
    from src.models.patient import Patient
    from src.models.organisation import Organisation
    from src.models.access_request import AccessRequest
    from src.models.group_proposal import GroupProposal
    from src.models.leader_request import LeaderRequest
    from src.models.group import Group

    from src.models.user_organisation import UserOrganisation
    from src.models.membership import Membership
    # Ticket #46: direct phase grants. Wrapped in try/except because legacy
    # envs may not have the user_phases table yet — admin page must still
    # render; phases just show as empty.
    try:
        from src.models.user_phase import UserPhase, PHASE_NAMES
        _user_phase_available = True
    except Exception:
        UserPhase = None
        PHASE_NAMES = ('planning', 'request', 'provider', 'analysis')
        _user_phase_available = False

    # Users
    all_users = db.query(User).all()
    users = []
    for u in all_users:
        entry = {
            'user_guid': u.guid, 'email': u.email, 'user_type': u.user_type,
            'is_su_admin': u.is_su_admin,
            'created_at': u.created_at.isoformat() if u.created_at else '',
            'first_name': '', 'last_name': '', 'professional_role': '',
            'personnummer': '', 'organization_ids': [],
            'direct_phases': [],       # list[str]
            'group_phases': [],        # list[{'phase': str, 'group_name': str}]
        }
        if u.user_type == 'professional':
            prof = db.query(Professional).filter_by(user_id=u.id).first()
            if prof:
                entry['first_name'] = prof.first_name or ''
                entry['last_name'] = prof.last_name or ''
                entry['professional_role'] = prof.professional_role or ''
            # Load org assignments
            user_orgs = db.query(UserOrganisation).filter_by(user_guid=u.guid).all()
            entry['organization_ids'] = [uo.organisation_guid for uo in user_orgs]

            # Ticket #46: direct phase grants
            if _user_phase_available:
                try:
                    direct = db.query(UserPhase).filter_by(user_guid=u.guid).all()
                    entry['direct_phases'] = sorted({up.phase for up in direct})
                except Exception:
                    entry['direct_phases'] = []

            # Group-derived phases (legacy display-only after #57; `category`
            # is free-form after #60 so only legacy phase-shaped labels match).
            memberships = db.query(Membership).filter_by(
                user_guid=u.guid, status='approved').all()
            for m in memberships:
                grp = db.query(Group).filter_by(guid=m.group_guid).first()
                if grp and grp.category in PHASE_NAMES:
                    entry['group_phases'].append({
                        'phase': grp.category,
                        'group_name': grp.name,
                    })
        elif u.user_type == 'patient':
            pat = db.query(Patient).filter_by(user_id=u.id).first()
            if pat:
                entry['personnummer'] = pat.personnummer or ''
        users.append(entry)

    # Organisations — with dependent counts for the ticket #45 Delete modal.
    orgs = db.query(Organisation).all()
    organisations = []
    for o in orgs:
        organisations.append({
            'organisation_guid': o.guid,
            'name': o.name,
            'patient_count': db.query(Patient).filter_by(organisation_guid=o.guid).count(),
            'user_assignment_count': db.query(UserOrganisation).filter_by(
                organisation_guid=o.guid).count(),
            'access_request_count': db.query(AccessRequest).filter_by(
                organisation_guid=o.guid).count(),
        })

    # Access requests
    ar_list = db.query(AccessRequest).filter(
        AccessRequest.status.in_(['pending', 'endorsed'])
    ).all()
    access_requests = [{
        'access_request_guid': ar.guid, 'email': ar.email,
        'first_name': ar.first_name, 'last_name': ar.last_name,
        'professional_role': ar.professional_role, 'status': ar.status,
        # #57: surface for SU review — advisory only, not auto-granted.
        'requested_phases': list(ar.requested_phases or []),
    } for ar in ar_list]

    # Group proposals
    gp_list = db.query(GroupProposal).filter_by(status='pending').all()
    group_proposals = [{
        'proposal_guid': p.guid, 'proposed_name': p.proposed_name,
        'category': p.category,
    } for p in gp_list]

    # Leader requests
    lr_list = db.query(LeaderRequest).filter_by(status='pending').all()
    leader_requests = [{
        'leader_request_guid': lr.guid,
        'user_guid': lr.user_guid, 'group_guid': lr.group_guid,
    } for lr in lr_list]

    # Groups
    all_groups = db.query(Group).all()
    groups_list = [{'guid': g.guid, 'name': g.name, 'category': g.category} for g in all_groups]

    # Oath overview
    oath_overview = []
    oath_path = os.path.join(current_app.root_path, '..', 'oath_overview.csv')
    if os.path.exists(oath_path):
        with open(oath_path, 'r') as f:
            oath_overview = list(csv.DictReader(f))

    return render_template('su_admin.html',
                           users=users, organisations=organisations,
                           access_requests=access_requests,
                           group_proposals=group_proposals,
                           leader_requests=leader_requests,
                           groups=groups_list, oath_overview=oath_overview,
                           phase_names=list(PHASE_NAMES))


# --- SU Admin form actions ---

@frontend_bp.route('/admin/promote-su', methods=['POST'])
@_require_su_login
def admin_promote_su():
    user = _get_session_user()
    db = get_db()
    target_guid = request.form.get('user_guid', '').strip()
    password = request.form.get('password', '')

    if not verify_password(password, user.password_hash):
        flash('Password confirmation failed.', 'error')
        return redirect(url_for('frontend.admin_page'))

    from src.models.user import User
    target = db.query(User).filter_by(guid=target_guid).first()
    if target is None:
        flash('User not found.', 'error')
    elif target.user_type != 'professional':
        flash('Only professionals can be promoted to SU.', 'error')
    elif target.is_su_admin:
        flash('User is already SU admin.', 'warning')
    else:
        target.is_su_admin = True
        audit('promote_su', user_guid=user.guid,
              detail={'target_guid': target_guid}, ip=request.remote_addr)
        flash(f'{target.email} promoted to SU admin.', 'success')

    return redirect(url_for('frontend.admin_page'))


@frontend_bp.route('/admin/delete-user', methods=['POST'])
@_require_su_login
def admin_delete_user():
    user = _get_session_user()
    db = get_db()
    target_guid = request.form.get('user_guid', '').strip()

    from src.models.user import User
    target = db.query(User).filter_by(guid=target_guid).first()
    if target is None:
        flash('User not found.', 'error')
    elif target.guid == user.guid:
        flash('Cannot delete yourself.', 'error')
    else:
        from src.models.membership import Membership
        from src.models.group_proposal import GroupProposal
        from src.models.leader_request import LeaderRequest
        from src.models.access_request import AccessRequest

        db.query(Membership).filter_by(decided_by_guid=target_guid).update(
            {'decided_by_guid': None}, synchronize_session='fetch')
        db.query(GroupProposal).filter_by(decided_by_guid=target_guid).update(
            {'decided_by_guid': None}, synchronize_session='fetch')
        db.query(LeaderRequest).filter_by(decided_by_guid=target_guid).update(
            {'decided_by_guid': None}, synchronize_session='fetch')
        db.query(AccessRequest).filter_by(decided_by_guid=target_guid).update(
            {'decided_by_guid': None}, synchronize_session='fetch')
        db.query(Membership).filter_by(user_guid=target_guid).delete(synchronize_session='fetch')
        db.delete(target)

        audit('delete_user', user_guid=user.guid,
              detail={'deleted_guid': target_guid, 'email': target.email},
              ip=request.remote_addr)
        flash(f'User {target.email} deleted.', 'success')

    return redirect(url_for('frontend.admin_page'))


@frontend_bp.route('/admin/reset-password', methods=['POST'])
@_require_su_login
def admin_reset_password():
    """SU-triggered password reset. Generates temp password, flashes it once.

    Ticket #43: temp password is shown in a success flash so the SU can copy
    it and hand it to the user out-of-band. force_change_on_next_login is set
    so the user must change it on next login.
    """
    from datetime import datetime, timezone
    from src.routes.admin import _generate_temp_password

    user = _get_session_user()
    db = get_db()
    target_guid = request.form.get('user_guid', '').strip()

    from src.models.user import User
    target = db.query(User).filter_by(guid=target_guid).first()
    if target is None:
        flash('User not found.', 'error')
        return redirect(url_for('frontend.admin_page'))

    temp_password = _generate_temp_password()
    target.password_hash = hash_password(temp_password)
    target.force_change_on_next_login = True
    target.password_changed_at = datetime.now(timezone.utc)

    audit('reset_password', user_guid=user.guid,
          detail={'target_guid': target_guid, 'email': target.email,
                  'generated': True},
          ip=request.remote_addr)

    # Plaintext password shown once in a flash — not persisted. The SU must
    # copy it now; refreshing the page will lose it.
    flash(f'Temp password for {target.email}: {temp_password} '
          f'(copy now — not stored; user must change on next login)', 'success')
    return redirect(url_for('frontend.admin_page'))


@frontend_bp.route('/admin/delete-organisation', methods=['POST'])
@_require_su_login
def admin_delete_org():
    """Ticket #45: SU deletes an organisation.

    Hard-blocks on patient references (medical-data integrity). UI sends
    the expected dependent counts in hidden inputs so the confirm already
    listed them — we re-check on the server anyway (never trust the form).
    """
    user = _get_session_user()
    db = get_db()
    org_guid = request.form.get('organisation_guid', '').strip()

    from src.models.organisation import Organisation
    from src.models.patient import Patient
    from src.models.user_organisation import UserOrganisation
    from src.models.access_request import AccessRequest

    org = db.query(Organisation).filter_by(guid=org_guid).first()
    if org is None:
        flash('Organisation not found.', 'error')
        return redirect(url_for('frontend.admin_page'))

    patient_count = db.query(Patient).filter_by(organisation_guid=org_guid).count()
    if patient_count > 0:
        flash(f'Cannot delete "{org.name}" — {patient_count} patient(s) still '
              f'reference this organisation. Reassign or remove them first.',
              'error')
        return redirect(url_for('frontend.admin_page'))

    org_name = org.name
    uo_deleted = db.query(UserOrganisation).filter_by(
        organisation_guid=org_guid).delete(synchronize_session='fetch')
    ar_deleted = db.query(AccessRequest).filter_by(
        organisation_guid=org_guid).delete(synchronize_session='fetch')
    db.delete(org)

    audit('delete_organisation', user_guid=user.guid,
          detail={'org_guid': org_guid, 'name': org_name,
                  'user_assignments_removed': uo_deleted,
                  'access_requests_removed': ar_deleted},
          ip=request.remote_addr)

    flash(f'Organisation "{org_name}" deleted '
          f'({uo_deleted} user assignment(s), {ar_deleted} access request(s) removed).',
          'success')
    return redirect(url_for('frontend.admin_page'))


@frontend_bp.route('/admin/grant-phase', methods=['POST'])
@_require_su_login
def admin_grant_phase():
    """Ticket #46: SU grants a direct phase to a user (independent of groups)."""
    user = _get_session_user()
    db = get_db()
    target_guid = request.form.get('user_guid', '').strip()
    phase = request.form.get('phase', '').strip()

    from src.models.user import User
    from src.models.user_phase import UserPhase, PHASE_NAMES

    if phase not in PHASE_NAMES:
        flash(f'Phase must be one of {", ".join(PHASE_NAMES)}.', 'error')
        return redirect(url_for('frontend.admin_page'))

    target = db.query(User).filter_by(guid=target_guid).first()
    if target is None:
        flash('User not found.', 'error')
        return redirect(url_for('frontend.admin_page'))

    existing = db.query(UserPhase).filter_by(
        user_guid=target_guid, phase=phase).first()
    if existing:
        flash(f'{target.email} already has direct phase "{phase}".', 'warning')
        return redirect(url_for('frontend.admin_page'))

    up = UserPhase(user_guid=target_guid, phase=phase,
                   granted_by_guid=user.guid)
    db.add(up)

    audit('grant_user_phase', user_guid=user.guid,
          detail={'target_guid': target_guid, 'phase': phase},
          ip=request.remote_addr)

    flash(f'Granted direct phase "{phase}" to {target.email}.', 'success')
    return redirect(url_for('frontend.admin_page'))


@frontend_bp.route('/admin/revoke-phase', methods=['POST'])
@_require_su_login
def admin_revoke_phase():
    """Ticket #46: SU revokes a direct phase grant.

    Leaves implicit (group-derived) access intact. A warning flash surfaces
    when the user still has the phase via a group membership, so the SU
    understands the revoke didn't actually cut access.
    """
    user = _get_session_user()
    db = get_db()
    target_guid = request.form.get('user_guid', '').strip()
    phase = request.form.get('phase', '').strip()

    from src.models.user import User
    from src.models.user_phase import UserPhase, PHASE_NAMES
    from src.models.membership import Membership
    from src.models.group import Group

    if phase not in PHASE_NAMES:
        flash(f'Phase must be one of {", ".join(PHASE_NAMES)}.', 'error')
        return redirect(url_for('frontend.admin_page'))

    target = db.query(User).filter_by(guid=target_guid).first()
    up = db.query(UserPhase).filter_by(
        user_guid=target_guid, phase=phase).first()
    if up is None:
        flash(f'No direct grant of phase "{phase}" to remove.', 'error')
        return redirect(url_for('frontend.admin_page'))

    db.delete(up)

    # Diagnostic-only after #57: group membership no longer grants phases,
    # but if the user is in a group whose (free-form) category happens to
    # match the phase name, surface that in the audit log and the UI.
    still_implicit = False
    for m in db.query(Membership).filter_by(
            user_guid=target_guid, status='approved').all():
        grp = db.query(Group).filter_by(guid=m.group_guid).first()
        if grp and grp.category == phase:
            still_implicit = True
            break

    audit('revoke_user_phase', user_guid=user.guid,
          detail={'target_guid': target_guid, 'phase': phase,
                  'still_implicit_via_group': still_implicit},
          ip=request.remote_addr)

    if still_implicit:
        flash(f'Direct "{phase}" revoked from {target.email if target else target_guid}, '
              f'but they still have it via a group membership.',
              'warning')
    else:
        flash(f'Revoked phase "{phase}" from {target.email if target else target_guid}.',
              'success')
    return redirect(url_for('frontend.admin_page'))


@frontend_bp.route('/admin/flush-sessions', methods=['POST'])
@_require_su_login
def admin_flush_sessions():
    """Ticket #44: bulk session flush by bumping token_revocation_epoch.

    Immediately invalidates every outstanding JWT for the target user — next
    request from any existing session will be rejected and they'll have to
    log in again. SU cannot flush themselves (avoids locking ourselves out
    mid-session).
    """
    from datetime import datetime, timezone

    user = _get_session_user()
    db = get_db()
    target_guid = request.form.get('user_guid', '').strip()

    from src.models.user import User
    target = db.query(User).filter_by(guid=target_guid).first()
    if target is None:
        flash('User not found.', 'error')
        return redirect(url_for('frontend.admin_page'))
    if target.guid == user.guid:
        flash('Refusing to flush your own sessions (that would log you out).', 'error')
        return redirect(url_for('frontend.admin_page'))

    new_epoch = datetime.now(timezone.utc)
    target.token_revocation_epoch = new_epoch

    audit('flush_sessions', user_guid=user.guid,
          detail={'target_guid': target_guid, 'email': target.email,
                  'epoch': new_epoch.isoformat()},
          ip=request.remote_addr)

    flash(f'All sessions flushed for {target.email}. '
          f'They will need to log in again.', 'success')
    return redirect(url_for('frontend.admin_page'))


@frontend_bp.route('/admin/create-organisation', methods=['POST'])
@_require_su_login
def admin_create_org():
    user = _get_session_user()
    db = get_db()
    name = request.form.get('name', '').strip()

    if not name:
        flash('Organisation name required.', 'error')
        return redirect(url_for('frontend.admin_page'))

    from src.models.organisation import Organisation
    if db.query(Organisation).filter_by(name=name).first():
        flash('Organisation name already exists.', 'error')
        return redirect(url_for('frontend.admin_page'))

    org = Organisation(name=name)
    db.add(org)
    db.flush()
    audit('create_organisation', user_guid=user.guid,
          detail={'org_guid': org.guid, 'name': name}, ip=request.remote_addr)
    flash(f'Organisation "{name}" created.', 'success')
    return redirect(url_for('frontend.admin_page'))


@frontend_bp.route('/admin/assign-org', methods=['POST'])
@_require_su_login
def admin_assign_org():
    user = _get_session_user()
    db = get_db()
    user_guid = request.form.get('user_guid', '').strip()
    org_guid = request.form.get('organisation_guid', '').strip()

    if not user_guid or not org_guid:
        flash('User and organisation required.', 'error')
        return redirect(url_for('frontend.admin_page'))

    from src.models.user import User
    from src.models.organisation import Organisation
    from src.models.user_organisation import UserOrganisation

    target = db.query(User).filter_by(guid=user_guid).first()
    org = db.query(Organisation).filter_by(guid=org_guid).first()
    if not target or not org:
        flash('User or organisation not found.', 'error')
        return redirect(url_for('frontend.admin_page'))

    if target.user_type != 'professional':
        flash('Only professionals can be assigned to organisations.', 'error')
        return redirect(url_for('frontend.admin_page'))

    existing = db.query(UserOrganisation).filter_by(
        user_guid=user_guid, organisation_guid=org_guid
    ).first()
    if existing:
        flash('User already assigned to this organisation.', 'error')
        return redirect(url_for('frontend.admin_page'))

    uo = UserOrganisation(user_guid=user_guid, organisation_guid=org_guid)
    db.add(uo)
    audit('assign_user_organisation', user_guid=user.guid,
          detail={'target_guid': user_guid, 'org_guid': org_guid, 'org_name': org.name},
          ip=request.remote_addr)
    flash(f'Assigned {target.email} to {org.name}.', 'success')
    return redirect(url_for('frontend.admin_page'))


@frontend_bp.route('/admin/remove-org', methods=['POST'])
@_require_su_login
def admin_remove_org():
    user = _get_session_user()
    db = get_db()
    user_guid = request.form.get('user_guid', '').strip()
    org_guid = request.form.get('organisation_guid', '').strip()

    if not user_guid or not org_guid:
        flash('User and organisation required.', 'error')
        return redirect(url_for('frontend.admin_page'))

    from src.models.user_organisation import UserOrganisation

    uo = db.query(UserOrganisation).filter_by(
        user_guid=user_guid, organisation_guid=org_guid
    ).first()
    if not uo:
        flash('Assignment not found.', 'error')
        return redirect(url_for('frontend.admin_page'))

    db.delete(uo)
    audit('remove_user_organisation', user_guid=user.guid,
          detail={'target_guid': user_guid, 'org_guid': org_guid},
          ip=request.remote_addr)
    flash('Organisation assignment removed.', 'success')
    return redirect(url_for('frontend.admin_page'))


@frontend_bp.route('/admin/create-group', methods=['POST'])
@_require_su_login
def admin_create_group():
    user = _get_session_user()
    db = get_db()
    name = request.form.get('name', '').strip()
    # #60: category is a free-form varchar — accept legacy `group_type`
    # param name as a fallback so older clients/templates still work while
    # the form rollouts propagate.
    category = (request.form.get('category') or
                request.form.get('group_type') or '').strip()

    if not name or not category:
        flash('Group name and category required.', 'error')
        return redirect(url_for('frontend.admin_page'))

    from src.models.group import Group
    if db.query(Group).filter_by(name=name).first():
        flash('Group name already exists.', 'error')
        return redirect(url_for('frontend.admin_page'))

    grp = Group(name=name, category=category)
    db.add(grp)
    db.flush()
    audit('create_group', user_guid=user.guid,
          detail={'group_guid': grp.guid, 'name': name, 'category': category},
          ip=request.remote_addr)
    flash(f'Group "{name}" ({category}) created.', 'success')
    return redirect(url_for('frontend.admin_page'))


@frontend_bp.route('/admin/decide-access-request', methods=['POST'])
@_require_su_login
def admin_decide_access_request():
    user = _get_session_user()
    db = get_db()
    ar_guid = request.form.get('access_request_guid', '').strip()
    decision = request.form.get('decision', '').strip().lower()

    if decision not in ('endorsed', 'approved', 'rejected'):
        flash('Invalid decision.', 'error')
        return redirect(url_for('frontend.admin_page'))

    from src.models.access_request import AccessRequest
    ar = db.query(AccessRequest).filter_by(guid=ar_guid).first()
    if ar is None:
        flash('Access request not found.', 'error')
        return redirect(url_for('frontend.admin_page'))

    ar.status = decision
    ar.decided_by_guid = user.guid

    if decision == 'approved':
        # #57: groups and phases are independent criteria. Approval
        # creates the user + professional + organisation link. The SU
        # grants phases and group memberships separately afterwards —
        # `ar.requested_phases` is now advisory metadata only.
        from src.models.user import User
        from src.models.professional import Professional
        from src.models.user_organisation import UserOrganisation

        new_user = User(email=ar.email, password_hash=ar.password_hash,
                        user_type='professional', is_su_admin=False)
        db.add(new_user)
        db.flush()
        prof = Professional(user_id=new_user.id, professional_role=ar.professional_role,
                            first_name=ar.first_name, last_name=ar.last_name)
        db.add(prof)
        uo = UserOrganisation(user_guid=new_user.guid, organisation_guid=ar.organisation_guid)
        db.add(uo)
        db.flush()

    audit('access_request_decide', user_guid=user.guid,
          detail={'access_request_guid': ar_guid, 'decision': decision,
                  # #57: surfaced for audit; SU must grant these explicitly.
                  'requested_phases_pending_su_grant':
                      list(ar.requested_phases or [])},
          ip=request.remote_addr)
    if decision == 'approved' and (ar.requested_phases or []):
        phases = ', '.join(ar.requested_phases)
        flash(
            f'Access request approved. User requested phases [{phases}] — '
            'grant explicitly via the Users table when ready.',
            'success',
        )
    else:
        flash(f'Access request {decision}.', 'success')
    return redirect(url_for('frontend.admin_page'))


@frontend_bp.route('/admin/decide-group-proposal', methods=['POST'])
@_require_su_login
def admin_decide_group_proposal():
    user = _get_session_user()
    db = get_db()
    proposal_guid = request.form.get('proposal_guid', '').strip()
    decision = request.form.get('decision', '').strip().lower()

    from src.models.group_proposal import GroupProposal
    proposal = db.query(GroupProposal).filter_by(guid=proposal_guid).first()
    if proposal is None:
        flash('Proposal not found.', 'error')
        return redirect(url_for('frontend.admin_page'))

    proposal.status = decision
    proposal.decided_by_guid = user.guid
    if decision == 'approved':
        from src.models.group import Group
        grp = Group(name=proposal.proposed_name, category=proposal.category)
        db.add(grp)
        db.flush()

    audit('group_proposal_decide', user_guid=user.guid,
          detail={'proposal_guid': proposal_guid, 'decision': decision},
          ip=request.remote_addr)
    flash(f'Group proposal {decision}.', 'success')
    return redirect(url_for('frontend.admin_page'))


@frontend_bp.route('/admin/decide-leader-request', methods=['POST'])
@_require_su_login
def admin_decide_leader_request():
    user = _get_session_user()
    db = get_db()
    lr_guid = request.form.get('leader_request_guid', '').strip()
    decision = request.form.get('decision', '').strip().lower()

    from src.models.leader_request import LeaderRequest
    lr = db.query(LeaderRequest).filter_by(guid=lr_guid).first()
    if lr is None:
        flash('Leader request not found.', 'error')
        return redirect(url_for('frontend.admin_page'))

    lr.status = decision
    lr.decided_by_guid = user.guid
    if decision == 'approved':
        from src.models.membership import Membership
        mem = db.query(Membership).filter_by(
            user_guid=lr.user_guid, group_guid=lr.group_guid
        ).first()
        if mem:
            mem.is_admin = True

    audit('leader_request_decide', user_guid=user.guid,
          detail={'leader_request_guid': lr_guid, 'decision': decision},
          ip=request.remote_addr)
    flash(f'Leader request {decision}.', 'success')
    return redirect(url_for('frontend.admin_page'))


@frontend_bp.route('/admin/delete-group', methods=['POST'])
@_require_su_login
def admin_delete_group():
    user = _get_session_user()
    db = get_db()
    group_guid = request.form.get('group_guid', '').strip()

    from src.models.group import Group
    from src.models.membership import Membership
    from src.models.invite import Invite

    grp = db.query(Group).filter_by(guid=group_guid).first()
    if grp is None:
        flash('Group not found.', 'error')
        return redirect(url_for('frontend.admin_page'))

    db.query(Membership).filter_by(group_guid=group_guid).delete(synchronize_session='fetch')
    db.query(Invite).filter_by(group_guid=group_guid).delete(synchronize_session='fetch')
    db.delete(grp)

    audit('delete_group', user_guid=user.guid,
          detail={'group_guid': group_guid, 'group_name': grp.name},
          ip=request.remote_addr)
    flash(f'Group "{grp.name}" deleted.', 'success')
    return redirect(url_for('frontend.admin_page'))


@frontend_bp.route('/admin/import-users', methods=['POST'])
@_require_su_login
def admin_import_users():
    user = _get_session_user()
    db = get_db()

    if 'file' not in request.files:
        flash('CSV file required.', 'error')
        return redirect(url_for('frontend.admin_page'))

    file = request.files['file']
    content = file.read().decode('utf-8')
    reader = csv.DictReader(io.StringIO(content))

    from src.models.user import User
    from src.models.professional import Professional

    created = 0
    skipped = 0
    for row in reader:
        email = row.get('email', '').strip()
        if not email:
            continue
        if db.query(User).filter_by(email=email).first():
            skipped += 1
            continue
        user_type = row.get('user_type', 'professional')
        new_user = User(email=email, password_hash=hash_password('changeme01'),
                        user_type=user_type,
                        is_su_admin=row.get('is_su_admin', '').lower() == 'true')
        db.add(new_user)
        db.flush()
        if user_type == 'professional':
            prof = Professional(user_id=new_user.id,
                                professional_role=row.get('professional_role', 'other'),
                                first_name=row.get('first_name', ''),
                                last_name=row.get('last_name', ''))
            db.add(prof)
        created += 1

    audit('import_users', user_guid=user.guid,
          detail={'created': created, 'skipped': skipped}, ip=request.remote_addr)
    flash(f'Imported {created} users, skipped {skipped} existing.', 'success')
    return redirect(url_for('frontend.admin_page'))


# ---------------------------------------------------------------------------
# 9.e — Group Admin page
# ---------------------------------------------------------------------------

@frontend_bp.route('/group-admin')
@_require_group_admin_login
def group_admin_page():
    """Group admin panel — pending requests, invite generation."""
    user = _get_session_user()
    db = get_db()

    from src.models.membership import Membership
    from src.models.group import Group
    from src.models.user import User

    # Get admin groups
    if user.is_su_admin:
        all_groups = db.query(Group).all()
        admin_group_guids = [g.guid for g in all_groups]
    else:
        admin_ms = db.query(Membership).filter_by(
            user_guid=user.guid, status='approved', is_admin=True
        ).all()
        admin_group_guids = [m.group_guid for m in admin_ms]

    # Pending memberships
    pending = []
    if admin_group_guids:
        pending_ms = db.query(Membership).filter(
            Membership.group_guid.in_(admin_group_guids),
            Membership.status == 'pending'
        ).all()
        for m in pending_ms:
            grp = db.query(Group).filter_by(guid=m.group_guid).first()
            req_user = db.query(User).filter_by(guid=m.user_guid).first()
            pending.append({
                'membership_guid': m.guid,
                'user_email': req_user.email if req_user else 'unknown',
                'group_name': grp.name if grp else 'unknown',
                'created_at': m.created_at.isoformat() if m.created_at else '',
            })

    # Admin groups for invite dropdown
    admin_groups = []
    for guid in admin_group_guids:
        grp = db.query(Group).filter_by(guid=guid).first()
        if grp:
            admin_groups.append({'group_guid': grp.guid, 'group_name': grp.name})

    # Ticket #47: per-group member rosters with orgs + phases so SU can edit
    # all three criteria from one place. Non-SU group admins see the same
    # roster but read-only (the template hides the edit controls).
    from src.models.user_organisation import UserOrganisation
    from src.models.organisation import Organisation
    try:
        from src.models.user_phase import UserPhase, PHASE_NAMES
        _up_ok = True
    except Exception:
        UserPhase = None
        PHASE_NAMES = ('planning', 'request', 'provider', 'analysis')
        _up_ok = False

    # Global org list for the "+ Org" dropdown.
    all_orgs = db.query(Organisation).all()
    all_organisations = [{'organisation_guid': o.guid, 'name': o.name}
                         for o in all_orgs]
    org_name_by_guid = {o.guid: o.name for o in all_orgs}

    member_groups = []
    for guid in admin_group_guids:
        grp = db.query(Group).filter_by(guid=guid).first()
        if not grp:
            continue
        approved_ms = db.query(Membership).filter_by(
            group_guid=guid, status='approved').all()
        members = []
        for m in approved_ms:
            mu = db.query(User).filter_by(guid=m.user_guid).first()
            if not mu:
                continue
            entry = {
                'user_guid': mu.guid,
                'email': mu.email,
                'is_group_admin': m.is_admin,
                'organization_ids': [],
                'org_labels': [],   # list[{guid,name}] for render
                'direct_phases': [],
                'group_phases': [],
            }
            user_orgs = db.query(UserOrganisation).filter_by(
                user_guid=mu.guid).all()
            entry['organization_ids'] = [uo.organisation_guid for uo in user_orgs]
            entry['org_labels'] = [
                {'guid': uo.organisation_guid,
                 'name': org_name_by_guid.get(uo.organisation_guid, uo.organisation_guid[:8])}
                for uo in user_orgs
            ]
            if _up_ok:
                try:
                    direct = db.query(UserPhase).filter_by(
                        user_guid=mu.guid).all()
                    entry['direct_phases'] = sorted({up.phase for up in direct})
                except Exception:
                    entry['direct_phases'] = []
            mem_other = db.query(Membership).filter_by(
                user_guid=mu.guid, status='approved').all()
            for mm in mem_other:
                og = db.query(Group).filter_by(guid=mm.group_guid).first()
                if og and og.category in PHASE_NAMES:
                    entry['group_phases'].append({
                        'phase': og.category,
                        'group_name': og.name,
                    })
            members.append(entry)
        member_groups.append({
            'group_guid': grp.guid,
            'group_name': grp.name,
            'category': grp.category,
            'members': members,
        })

    return render_template('group_admin.html', pending=pending,
                           admin_groups=admin_groups, invite_result=None,
                           member_groups=member_groups,
                           all_organisations=all_organisations,
                           phase_names=list(PHASE_NAMES))


@frontend_bp.route('/group-admin/decide', methods=['POST'])
@_require_group_admin_login
def group_admin_decide():
    user = _get_session_user()
    db = get_db()
    membership_guid = request.form.get('membership_guid', '').strip()
    decision = request.form.get('decision', '').strip().lower()

    from src.models.membership import Membership
    mem = db.query(Membership).filter_by(guid=membership_guid).first()
    if mem is None:
        flash('Membership not found.', 'error')
    elif mem.status != 'pending':
        flash(f'Already decided ({mem.status}).', 'warning')
    else:
        # Verify admin authority
        if not user.is_su_admin:
            admin_m = db.query(Membership).filter_by(
                user_guid=user.guid, group_guid=mem.group_guid,
                status='approved', is_admin=True
            ).first()
            if admin_m is None:
                flash('Not admin of this group.', 'error')
                return redirect(url_for('frontend.group_admin_page'))

        mem.status = decision
        mem.decided_by_guid = user.guid
        audit('membership_decide', user_guid=user.guid,
              detail={'membership_guid': membership_guid, 'decision': decision},
              ip=request.remote_addr)
        flash(f'Membership {decision}.', 'success')

    return redirect(url_for('frontend.group_admin_page'))


@frontend_bp.route('/group-admin/invite', methods=['POST'])
@_require_group_admin_login
def group_admin_invite():
    import uuid
    from datetime import datetime, timedelta, timezone

    user = _get_session_user()
    db = get_db()
    group_guid = request.form.get('group_guid', '').strip()
    hours_valid = int(request.form.get('hours_valid', 48))

    from src.models.group import Group
    from src.models.invite import Invite

    grp = db.query(Group).filter_by(guid=group_guid).first()
    if grp is None:
        flash('Group not found.', 'error')
        return redirect(url_for('frontend.group_admin_page'))

    invite_token = str(uuid.uuid4())
    invite = Invite(
        group_guid=group_guid, token=invite_token,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=hours_valid),
        created_by_guid=user.guid,
    )
    db.add(invite)
    db.flush()

    audit('invite_created', user_guid=user.guid,
          detail={'group_guid': group_guid, 'hours_valid': hours_valid},
          ip=request.remote_addr)

    flash(f'Invite created for "{grp.name}". Token: {invite_token} (expires in {hours_valid}h)', 'success')
    return redirect(url_for('frontend.group_admin_page'))


# --- Ticket #47: org + phase editing from /group-admin (SU-only) -------------
# Mirrors the /admin/{assign,remove}-org + /admin/{grant,revoke}-phase logic
# but redirects back to /group-admin so SUs can manage all three access
# criteria (groups / orgs / phases) without leaving this page.

@frontend_bp.route('/group-admin/assign-org', methods=['POST'])
@_require_su_login
def group_admin_assign_org():
    user = _get_session_user()
    db = get_db()
    user_guid = request.form.get('user_guid', '').strip()
    org_guid = request.form.get('organisation_guid', '').strip()

    from src.models.user import User
    from src.models.organisation import Organisation
    from src.models.user_organisation import UserOrganisation

    target = db.query(User).filter_by(guid=user_guid).first()
    org = db.query(Organisation).filter_by(guid=org_guid).first()
    if not target or not org:
        flash('User or organisation not found.', 'error')
        return redirect(url_for('frontend.group_admin_page'))
    if target.user_type != 'professional':
        flash('Only professionals can be assigned to organisations.', 'error')
        return redirect(url_for('frontend.group_admin_page'))

    existing = db.query(UserOrganisation).filter_by(
        user_guid=user_guid, organisation_guid=org_guid).first()
    if existing:
        flash(f'{target.email} already assigned to {org.name}.', 'warning')
        return redirect(url_for('frontend.group_admin_page'))

    db.add(UserOrganisation(user_guid=user_guid, organisation_guid=org_guid))
    audit('assign_user_organisation', user_guid=user.guid,
          detail={'target_guid': user_guid, 'org_guid': org_guid,
                  'org_name': org.name, 'from': 'group-admin'},
          ip=request.remote_addr)
    flash(f'Assigned {target.email} to {org.name}.', 'success')
    return redirect(url_for('frontend.group_admin_page'))


@frontend_bp.route('/group-admin/remove-org', methods=['POST'])
@_require_su_login
def group_admin_remove_org():
    user = _get_session_user()
    db = get_db()
    user_guid = request.form.get('user_guid', '').strip()
    org_guid = request.form.get('organisation_guid', '').strip()

    from src.models.user_organisation import UserOrganisation
    uo = db.query(UserOrganisation).filter_by(
        user_guid=user_guid, organisation_guid=org_guid).first()
    if not uo:
        flash('Assignment not found.', 'error')
        return redirect(url_for('frontend.group_admin_page'))

    db.delete(uo)
    audit('remove_user_organisation', user_guid=user.guid,
          detail={'target_guid': user_guid, 'org_guid': org_guid,
                  'from': 'group-admin'},
          ip=request.remote_addr)
    flash('Organisation assignment removed.', 'success')
    return redirect(url_for('frontend.group_admin_page'))


@frontend_bp.route('/group-admin/grant-phase', methods=['POST'])
@_require_su_login
def group_admin_grant_phase():
    user = _get_session_user()
    db = get_db()
    target_guid = request.form.get('user_guid', '').strip()
    phase = request.form.get('phase', '').strip()

    from src.models.user import User
    from src.models.user_phase import UserPhase, PHASE_NAMES

    if phase not in PHASE_NAMES:
        flash(f'Phase must be one of {", ".join(PHASE_NAMES)}.', 'error')
        return redirect(url_for('frontend.group_admin_page'))

    target = db.query(User).filter_by(guid=target_guid).first()
    if target is None:
        flash('User not found.', 'error')
        return redirect(url_for('frontend.group_admin_page'))

    if db.query(UserPhase).filter_by(user_guid=target_guid, phase=phase).first():
        flash(f'{target.email} already has direct phase "{phase}".', 'warning')
        return redirect(url_for('frontend.group_admin_page'))

    db.add(UserPhase(user_guid=target_guid, phase=phase,
                     granted_by_guid=user.guid))
    audit('grant_user_phase', user_guid=user.guid,
          detail={'target_guid': target_guid, 'phase': phase,
                  'from': 'group-admin'},
          ip=request.remote_addr)
    flash(f'Granted direct phase "{phase}" to {target.email}.', 'success')
    return redirect(url_for('frontend.group_admin_page'))


@frontend_bp.route('/group-admin/revoke-phase', methods=['POST'])
@_require_su_login
def group_admin_revoke_phase():
    user = _get_session_user()
    db = get_db()
    target_guid = request.form.get('user_guid', '').strip()
    phase = request.form.get('phase', '').strip()

    from src.models.user import User
    from src.models.user_phase import UserPhase, PHASE_NAMES
    from src.models.membership import Membership
    from src.models.group import Group

    if phase not in PHASE_NAMES:
        flash(f'Phase must be one of {", ".join(PHASE_NAMES)}.', 'error')
        return redirect(url_for('frontend.group_admin_page'))

    target = db.query(User).filter_by(guid=target_guid).first()
    up = db.query(UserPhase).filter_by(
        user_guid=target_guid, phase=phase).first()
    if up is None:
        flash(f'No direct grant of phase "{phase}" to remove.', 'error')
        return redirect(url_for('frontend.group_admin_page'))

    db.delete(up)

    still_implicit = False
    for m in db.query(Membership).filter_by(
            user_guid=target_guid, status='approved').all():
        grp = db.query(Group).filter_by(guid=m.group_guid).first()
        if grp and grp.category == phase:
            still_implicit = True
            break

    audit('revoke_user_phase', user_guid=user.guid,
          detail={'target_guid': target_guid, 'phase': phase,
                  'still_implicit_via_group': still_implicit,
                  'from': 'group-admin'},
          ip=request.remote_addr)

    if still_implicit:
        flash(f'Direct "{phase}" revoked from {target.email if target else target_guid}, '
              f'but they still have it via a group membership.', 'warning')
    else:
        flash(f'Revoked phase "{phase}" from '
              f'{target.email if target else target_guid}.', 'success')
    return redirect(url_for('frontend.group_admin_page'))


# ---------------------------------------------------------------------------
# 9.f — Onboarding pages
# ---------------------------------------------------------------------------

@frontend_bp.route('/register-patient', methods=['GET', 'POST'])
def register_patient():
    """Patient self-registration."""
    db = get_db()
    from src.models.organisation import Organisation
    orgs = db.query(Organisation).all()
    organisations = [{'organisation_guid': o.guid, 'name': o.name} for o in orgs]

    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        personnummer = request.form.get('personnummer', '').strip()
        org_guid = request.form.get('organisation_guid', '').strip()

        errors = []
        if not email:
            errors.append('Email is required.')
        if not password or len(password) < 8:
            errors.append('Password must be at least 8 characters.')
        if not re.match(r'^\d{12}$', personnummer):
            errors.append('Personnummer must be exactly 12 digits.')
        if not org_guid:
            errors.append('Organisation is required.')

        if errors:
            for e in errors:
                flash(e, 'error')
            return render_template('register_patient.html', organisations=organisations)

        org = db.query(Organisation).filter_by(guid=org_guid).first()
        if org is None:
            flash('Organisation not found.', 'error')
            return render_template('register_patient.html', organisations=organisations)

        from src.models.user import User
        if db.query(User).filter_by(email=email).first():
            flash('Email already registered.', 'error')
            return render_template('register_patient.html', organisations=organisations)

        from src.models.patient import Patient
        if db.query(Patient).filter_by(personnummer=personnummer).first():
            flash('Personnummer already registered.', 'error')
            return render_template('register_patient.html', organisations=organisations)

        new_user = User(email=email, password_hash=hash_password(password),
                        user_type='patient', is_su_admin=False)
        db.add(new_user)
        db.flush()
        patient = Patient(user_id=new_user.id, personnummer=personnummer,
                          organisation_guid=org_guid)
        db.add(patient)
        db.flush()

        flash('Registration successful! You can now log in.', 'success')
        return redirect(url_for('frontend.login_page'))

    return render_template('register_patient.html', organisations=organisations)


@frontend_bp.route('/join', methods=['GET', 'POST'])
@_require_login
def join_by_invite():
    """Join group by invite token."""
    if request.method == 'POST':
        user = _get_session_user()
        db = get_db()
        token_val = request.form.get('token', '').strip()

        from src.models.invite import Invite
        from src.models.membership import Membership
        from datetime import datetime, timezone

        invite = db.query(Invite).filter_by(token=token_val).first()
        if invite is None:
            flash('Invalid invite token.', 'error')
            return render_template('join.html')

        expires = invite.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if expires < datetime.now(timezone.utc):
            flash('Invite token has expired.', 'error')
            return render_template('join.html')

        existing = db.query(Membership).filter_by(
            user_guid=user.guid, group_guid=invite.group_guid
        ).first()
        if existing:
            flash(f'Membership already exists (status: {existing.status}).', 'warning')
            return render_template('join.html')

        mem = Membership(user_guid=user.guid, group_guid=invite.group_guid,
                         status='pending', is_admin=False)
        db.add(mem)
        db.flush()

        audit('join_by_invite', user_guid=user.guid,
              detail={'group_guid': invite.group_guid}, ip=request.remote_addr)
        flash('Membership request created (pending approval).', 'success')
        return redirect(url_for('frontend.dashboard'))

    return render_template('join.html')


@frontend_bp.route('/request-join', methods=['GET', 'POST'])
@_require_login
def request_join():
    """Request membership in a group."""
    db = get_db()
    from src.models.group import Group
    all_groups = db.query(Group).all()
    available_groups = [{'group_guid': g.guid, 'name': g.name, 'category': g.category}
                        for g in all_groups]

    if request.method == 'POST':
        user = _get_session_user()
        group_guid = request.form.get('group_guid', '').strip()

        grp = db.query(Group).filter_by(guid=group_guid).first()
        if grp is None:
            flash('Group not found.', 'error')
            return render_template('request_join.html', available_groups=available_groups)

        from src.models.membership import Membership
        existing = db.query(Membership).filter_by(
            user_guid=user.guid, group_guid=group_guid
        ).first()
        if existing:
            flash(f'Membership already exists (status: {existing.status}).', 'warning')
            return render_template('request_join.html', available_groups=available_groups)

        mem = Membership(user_guid=user.guid, group_guid=group_guid,
                         status='pending', is_admin=False)
        db.add(mem)
        db.flush()

        audit('membership_request', user_guid=user.guid,
              detail={'group_guid': group_guid, 'group_name': grp.name},
              ip=request.remote_addr)
        flash('Membership request submitted (pending approval).', 'success')
        return redirect(url_for('frontend.dashboard'))

    return render_template('request_join.html', available_groups=available_groups)


@frontend_bp.route('/suggest-group', methods=['GET', 'POST'])
@_require_login
def suggest_group():
    """Suggest a new group (creates proposal)."""
    if request.method == 'POST':
        user = _get_session_user()
        db = get_db()
        proposed_name = request.form.get('proposed_name', '').strip()
        # #60: free-form category; accept legacy `group_type` form field
        # as a fallback while templates roll forward.
        category = (request.form.get('category') or
                    request.form.get('group_type') or '').strip()

        if not proposed_name or not category:
            flash('Group name and category required.', 'error')
            return render_template('suggest_group.html')

        from src.models.group_proposal import GroupProposal
        proposal = GroupProposal(
            proposed_name=proposed_name, category=category,
            requested_by_guid=user.guid, status='pending',
        )
        db.add(proposal)
        db.flush()

        audit('group_proposal_submit', user_guid=user.guid,
              detail={'proposed_name': proposed_name, 'category': category},
              ip=request.remote_addr)
        flash('Group proposal submitted for admin review.', 'success')
        return redirect(url_for('frontend.dashboard'))

    return render_template('suggest_group.html')


@frontend_bp.route('/request-access', methods=['GET', 'POST'])
def request_access():
    """Public professional access request."""
    db = get_db()
    from src.models.organisation import Organisation
    from src.models.user import User
    from src.models.professional import Professional
    from src.models.membership import Membership

    orgs = db.query(Organisation).all()
    organisations = [{'organisation_guid': o.guid, 'name': o.name} for o in orgs]

    # Leaders: group admins + SU admins
    leaders_set = set()
    admin_ms = db.query(Membership).filter_by(status='approved', is_admin=True).all()
    for m in admin_ms:
        leaders_set.add(m.user_guid)
    su_users = db.query(User).filter_by(is_su_admin=True).all()
    for u in su_users:
        leaders_set.add(u.guid)

    leaders = []
    for guid in leaders_set:
        u = db.query(User).filter_by(guid=guid).first()
        if u and u.user_type == 'professional':
            prof = db.query(Professional).filter_by(user_id=u.id).first()
            leaders.append({
                'user_guid': u.guid,
                'first_name': prof.first_name if prof else '',
                'last_name': prof.last_name if prof else '',
                'is_su_admin': u.is_su_admin,
            })

    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        first_name = request.form.get('first_name', '').strip()
        last_name = request.form.get('last_name', '').strip()
        professional_role = request.form.get('professional_role', '').strip()
        org_guid = request.form.get('organisation_guid', '').strip()
        requested_phases = request.form.getlist('requested_phases')
        chosen_leader_guid = request.form.get('chosen_leader_guid', '').strip()

        errors = []
        if not email:
            errors.append('Email is required.')
        if not password or len(password) < 8:
            errors.append('Password must be at least 8 characters.')
        if not first_name:
            errors.append('First name is required.')
        if not last_name:
            errors.append('Last name is required.')
        if professional_role not in ('doctor', 'nurse', 'other'):
            errors.append('Valid professional role required.')
        if not org_guid:
            errors.append('Organisation is required.')
        if not chosen_leader_guid:
            errors.append('Leader selection is required.')

        if errors:
            for e in errors:
                flash(e, 'error')
            return render_template('request_access.html',
                                   organisations=organisations, leaders=leaders)

        # Check org + leader exist
        if not db.query(Organisation).filter_by(guid=org_guid).first():
            flash('Organisation not found.', 'error')
            return render_template('request_access.html',
                                   organisations=organisations, leaders=leaders)
        if not db.query(User).filter_by(guid=chosen_leader_guid).first():
            flash('Leader not found.', 'error')
            return render_template('request_access.html',
                                   organisations=organisations, leaders=leaders)

        if db.query(User).filter_by(email=email).first():
            flash('Email already registered.', 'error')
            return render_template('request_access.html',
                                   organisations=organisations, leaders=leaders)

        from src.models.access_request import AccessRequest
        ar = AccessRequest(
            email=email, password_hash=hash_password(password),
            first_name=first_name, last_name=last_name,
            professional_role=professional_role, organisation_guid=org_guid,
            requested_phases=requested_phases, chosen_leader_guid=chosen_leader_guid,
            status='pending',
        )
        db.add(ar)
        db.flush()

        audit('access_request_submit', detail={'email': email, 'organisation_guid': org_guid},
              ip=request.remote_addr)
        flash('Access request submitted. A leader will review your request.', 'success')
        return redirect(url_for('frontend.login_page'))

    return render_template('request_access.html',
                           organisations=organisations, leaders=leaders)


@frontend_bp.route('/change-password', methods=['GET', 'POST'])
@_require_login
def change_password():
    """Change password page."""
    if request.method == 'POST':
        user = _get_session_user()
        db = get_db()
        current_pw = request.form.get('current_password', '')
        new_pw = request.form.get('new_password', '')

        if not current_pw or not new_pw:
            flash('Both fields are required.', 'error')
            return render_template('change_password.html')

        if len(new_pw) < 8:
            flash('New password must be at least 8 characters.', 'error')
            return render_template('change_password.html')

        if not verify_password(current_pw, user.password_hash):
            flash('Current password is incorrect.', 'error')
            return render_template('change_password.html')

        from datetime import datetime, timezone
        user.password_hash = hash_password(new_pw)
        # Ticket #43: clear SU-set force-change flag on self-service change.
        user.force_change_on_next_login = False
        user.password_changed_at = datetime.now(timezone.utc)
        audit('change_password_success', user_guid=user.guid, ip=request.remote_addr)
        flash('Password changed successfully.', 'success')
        return redirect(url_for('frontend.dashboard'))

    return render_template('change_password.html')


# ---------------------------------------------------------------------------
# 9.g — Docs page (static allowlisted document download)
# ---------------------------------------------------------------------------

# Allowlisted document filenames (path-traversal safe)
ALLOWED_DOCS = {
    'api-reference.md': 'API Reference',
    'architecture.md': 'Architecture Overview',
    'integration-guide.md': 'Integration Guide',
    'subservice-onboarding.md': 'Subservice Onboarding & Acceptance',
    'admin-manual.md': 'Admin Manual',
    'deployment-guide.md': 'Deployment Guide',
    'pre-deployment-checklist.md': 'Pre-Deployment Checklist',
    'user-guide.md': 'User Guide',
}


@frontend_bp.route('/docs')
def docs_page():
    """Docs page — list available documents."""
    docs_dir = os.path.join(current_app.root_path, '..', 'docs', 'docs')
    documents = []
    for filename, name in ALLOWED_DOCS.items():
        filepath = os.path.join(docs_dir, filename)
        if os.path.exists(filepath):
            documents.append({'filename': filename, 'name': name})
    return render_template('docs.html', documents=documents)


@frontend_bp.route('/docs/download/<filename>')
def docs_download(filename):
    """Download a specific document (allowlisted, path-traversal safe)."""
    if filename not in ALLOWED_DOCS:
        abort(404)
    docs_dir = os.path.join(current_app.root_path, '..', 'docs', 'docs')
    return send_from_directory(docs_dir, filename, as_attachment=True)


# --------------------------------------------------------------------------
# Service key management — JSON endpoints called by su_admin.html JS
# --------------------------------------------------------------------------

def _require_su_json(f):
    """Like _require_su_login but returns JSON 401/403 instead of redirect."""
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        user = _get_session_user()
        if user is None:
            return jsonify(error='authentication_required'), 401
        if not user.is_su_admin:
            return jsonify(error='forbidden', message='SU admin access required'), 403
        return f(*args, **kwargs)
    return decorated


def _get_keyed_service(service_name):
    """Look up a keyed service by name. Returns (url, key) or (None, None)."""
    services = current_app.config.get('KEYED_SERVICES', {})
    svc = services.get(service_name)
    if not svc:
        return None, None
    return svc['url'], svc['key']


@frontend_bp.route('/api/admin/service-keys', methods=['GET'])
@_require_su_json
def list_keyed_services():
    """List configured keyed services."""
    services = current_app.config.get('KEYED_SERVICES', {})
    return jsonify(services=list(services.keys())), 200


@frontend_bp.route('/api/admin/service-keys/<service_name>/users', methods=['GET'])
@_require_su_json
def service_key_users(service_name):
    """Proxy: list users+keys on a keyed service."""
    import requests as _requests

    base_url, svc_key = _get_keyed_service(service_name)
    if not base_url:
        return jsonify(error='unknown_service',
                       message=f'Service "{service_name}" not configured'), 404

    try:
        r = _requests.get(
            f'{base_url}/keyauth/api/users',
            headers={'X-Internal-Service-Key': svc_key},
            timeout=10,
        )
        return jsonify(r.json()), r.status_code
    except _requests.exceptions.ConnectionError:
        return jsonify(error='connection_error',
                       message=f'Cannot reach {service_name}'), 502
    except _requests.exceptions.Timeout:
        return jsonify(error='timeout',
                       message=f'{service_name} did not respond in time'), 504


@frontend_bp.route('/api/admin/service-keys/<service_name>/generate', methods=['POST'])
@_require_su_json
def service_key_generate(service_name):
    """Proxy: generate a key on a keyed service."""
    import requests as _requests

    base_url, svc_key = _get_keyed_service(service_name)
    if not base_url:
        return jsonify(error='unknown_service',
                       message=f'Service "{service_name}" not configured'), 404

    user = _get_session_user()
    body = request.get_json(silent=True) or {}

    try:
        r = _requests.post(
            f'{base_url}/keyauth/api/generate-key',
            headers={
                'X-Internal-Service-Key': svc_key,
                'Content-Type': 'application/json',
            },
            json=body,
            timeout=10,
        )
        result = r.json()

        if r.status_code == 201:
            audit('service_key_generated', user_guid=user.guid,
                  detail={
                      'service': service_name,
                      'target_username': body.get('username', ''),
                      'label': body.get('label', ''),
                  }, ip=request.remote_addr)

        return jsonify(result), r.status_code
    except _requests.exceptions.ConnectionError:
        return jsonify(error='connection_error',
                       message=f'Cannot reach {service_name}'), 502
    except _requests.exceptions.Timeout:
        return jsonify(error='timeout',
                       message=f'{service_name} did not respond in time'), 504
