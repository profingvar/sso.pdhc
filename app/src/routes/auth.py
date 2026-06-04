"""Authentication API routes — login, me, me/service, logout, change-password."""
from datetime import datetime, timezone
from urllib.parse import urlencode, urlparse

from flask import Blueprint, request, jsonify, redirect, g

from src.db import get_db
from src.services.jwt_service import issue_token, decode_token, revoke_token
from src.services.auth_service import authenticate_user, build_access_blob, verify_password, hash_password
from src.services.audit_log import audit
from src.middleware.auth_middleware import require_auth
from src.middleware.rate_limit import rate_limit

auth_bp = Blueprint('auth', __name__, url_prefix='/api/auth')


def _is_allowed_callback(url, app_config):
    """Check if a callback URL is in the allowlist."""
    allowed_urls = app_config.get('ALLOWED_CALLBACK_URLS', [])
    allowed_origins = app_config.get('ALLOWED_ORIGINS', [])

    # Exact URL match
    if url in allowed_urls:
        return True

    # Origin match
    parsed = urlparse(url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    if origin in allowed_origins:
        return True

    return False


@auth_bp.route('/login', methods=['POST'])
@rate_limit(max_requests=20, window_seconds=60)
def login():
    """POST /api/auth/login — email + password → JWT.

    Supports SSO handshake: if 'next' param provided, redirects with token.
    Auto-redirect: if user already has valid session and 'next' is provided,
    skip login and redirect immediately.
    """
    from flask import current_app
    session = get_db()
    config = current_app.config

    # Check for JSON body or form data
    if request.is_json:
        data = request.get_json()
    else:
        data = request.form.to_dict()

    email = data.get('email', '').strip()
    password = data.get('password', '')
    next_url = data.get('next', '') or request.args.get('next', '')
    state = data.get('state', '') or request.args.get('state', '')

    if not email or not password:
        audit('login_fail', detail={'reason': 'missing_credentials'}, ip=request.remote_addr)
        if next_url and _is_allowed_callback(next_url, config):
            params = {'error': 'invalid_request', 'error_description': 'Missing email or password'}
            if state:
                params['state'] = state
            return redirect(f"{next_url}?{urlencode(params)}")
        return jsonify({"error": "invalid_request", "message": "Email and password required"}), 400

    user = authenticate_user(email, password, session)
    if user is None:
        audit('login_fail', detail={'reason': 'invalid_credentials', 'email': email}, ip=request.remote_addr)
        if next_url and _is_allowed_callback(next_url, config):
            params = {'error': 'authentication_failed', 'error_description': 'Invalid email or password'}
            if state:
                params['state'] = state
            return redirect(f"{next_url}?{urlencode(params)}")
        return jsonify({"error": "authentication_failed", "message": "Invalid email or password"}), 401

    # Issue JWT
    secret_key = config.get('SECRET_KEY', '')
    expiry_hours = config.get('SESSION_EXPIRY_HOURS', 24)
    token = issue_token(user.guid, secret_key, expiry_hours=expiry_hours)

    audit('login_success', user_guid=user.guid, detail={'email': email}, ip=request.remote_addr)

    # SSO handshake redirect
    if next_url:
        if not _is_allowed_callback(next_url, config):
            return jsonify({"error": "invalid_redirect", "message": "Callback URL not in allowlist"}), 400
        params = {'token': token}
        if state:
            params['state'] = state
        return redirect(f"{next_url}?{urlencode(params)}")

    return jsonify({"token": token, "user_guid": user.guid}), 200


@auth_bp.route('/me', methods=['GET'])
@require_auth
def me():
    """GET /api/auth/me — Bearer token → access blob."""
    session = get_db()
    user = g.current_user
    sid = (g.token_payload or {}).get('sid')
    blob = build_access_blob(user, session, session_id=sid)
    return jsonify(blob), 200


@auth_bp.route('/me/service', methods=['GET'])
@require_auth
def me_service():
    """GET /api/auth/me/service — same as /me but requires service credentials."""
    from flask import current_app
    config = current_app.config

    client_id = request.headers.get('X-SSO-Client-Id', '')
    client_secret = request.headers.get('X-SSO-Client-Secret', '')

    service_creds = config.get('SERVICE_CREDENTIALS', {})
    if not client_id or service_creds.get(client_id) != client_secret:
        audit('service_auth_fail', detail={'client_id': client_id}, ip=request.remote_addr)
        return jsonify({"error": "invalid_service_credentials",
                        "message": "Valid X-SSO-Client-Id and X-SSO-Client-Secret required"}), 403

    session = get_db()
    user = g.current_user
    sid = (g.token_payload or {}).get('sid')
    blob = build_access_blob(user, session, session_id=sid)

    audit('service_auth_success', user_guid=user.guid,
          detail={'client_id': client_id}, ip=request.remote_addr)

    return jsonify(blob), 200


@auth_bp.route('/logout', methods=['POST'])
@require_auth
def logout():
    """POST /api/auth/logout — adds token GUID to revoked_tokens."""
    session = get_db()
    payload = g.token_payload

    token_guid = payload.get('jti')
    exp_timestamp = payload.get('exp')
    expires_at = datetime.fromtimestamp(exp_timestamp, tz=timezone.utc) if exp_timestamp else None

    if token_guid and expires_at:
        revoke_token(token_guid, expires_at, session)
        session.commit()

    audit('logout', user_guid=g.current_user.guid, ip=request.remote_addr)
    return jsonify({"message": "Logged out successfully"}), 200


@auth_bp.route('/change-password', methods=['POST'])
@require_auth
def change_password():
    """POST /api/auth/change-password — change own password."""
    session = get_db()
    user = g.current_user

    data = request.get_json() if request.is_json else request.form.to_dict()
    current_password = data.get('current_password', '')
    new_password = data.get('new_password', '')

    if not current_password or not new_password:
        return jsonify({"error": "invalid_request",
                        "message": "current_password and new_password required"}), 400

    if len(new_password) < 8:
        return jsonify({"error": "invalid_request",
                        "message": "New password must be at least 8 characters"}), 400

    if not verify_password(current_password, user.password_hash):
        audit('change_password_fail', user_guid=user.guid,
              detail={'reason': 'wrong_current_password'}, ip=request.remote_addr)
        return jsonify({"error": "authentication_failed",
                        "message": "Current password is incorrect"}), 401

    user.password_hash = hash_password(new_password)
    audit('change_password_success', user_guid=user.guid, ip=request.remote_addr)
    return jsonify({"message": "Password changed successfully"}), 200
