"""Auth middleware — decorators for route protection. All use GUID from token."""
import hmac as hmac_mod
from functools import wraps

from flask import current_app, request, jsonify, g

from src.db import get_db
from src.services.jwt_service import (
    validate_token, TokenExpiredError, TokenInvalidError, TokenRevokedError,
)


def _get_current_user():
    """Extract and validate Bearer token, load user into g.current_user.

    Caches per-request using the raw token string so that chained decorators
    (e.g. @require_auth + @require_su) don't double-validate within one request,
    but each new request (or different token) always re-validates.
    """
    auth_header = request.headers.get('Authorization', '')
    if not auth_header.startswith('Bearer '):
        return None

    token = auth_header[7:]

    # Per-request cache: skip re-validation if same token already validated
    if hasattr(g, '_auth_token') and g._auth_token == token and hasattr(g, 'current_user'):
        return g.current_user

    from flask import current_app
    secret_key = current_app.config.get('SECRET_KEY', '')
    session = get_db()

    try:
        payload = validate_token(token, secret_key, session)
    except (TokenExpiredError, TokenInvalidError, TokenRevokedError):
        return None

    from src.models.user import User
    user = session.query(User).filter_by(guid=payload['sub']).first()
    if user is None:
        return None

    g.current_user = user
    g.token_payload = payload
    g._auth_token = token
    return user


def require_auth(f):
    """Require valid Bearer token. Sets g.current_user."""
    @wraps(f)
    def decorated(*args, **kwargs):
        user = _get_current_user()
        if user is None:
            return jsonify({"error": "authentication_required", "message": "Valid Bearer token required"}), 401
        return f(*args, **kwargs)
    return decorated


def require_su(f):
    """Require SU admin."""
    @wraps(f)
    def decorated(*args, **kwargs):
        user = _get_current_user()
        if user is None:
            return jsonify({"error": "authentication_required", "message": "Valid Bearer token required"}), 401
        if not user.is_su_admin:
            return jsonify({"error": "forbidden", "message": "SU admin access required"}), 403
        return f(*args, **kwargs)
    return decorated


def require_professional(f):
    """Require user_type == professional."""
    @wraps(f)
    def decorated(*args, **kwargs):
        user = _get_current_user()
        if user is None:
            return jsonify({"error": "authentication_required", "message": "Valid Bearer token required"}), 401
        if user.user_type != 'professional':
            return jsonify({"error": "forbidden", "message": "Professional access required"}), 403
        return f(*args, **kwargs)
    return decorated


def require_patient(f):
    """Require user_type == patient."""
    @wraps(f)
    def decorated(*args, **kwargs):
        user = _get_current_user()
        if user is None:
            return jsonify({"error": "authentication_required", "message": "Valid Bearer token required"}), 401
        if user.user_type != 'patient':
            return jsonify({"error": "forbidden", "message": "Patient access required"}), 403
        return f(*args, **kwargs)
    return decorated


# ── require_organisation: removed 2026-09-29 (#709). Do not re-add. ──
#
# There was a `require_organisation` decorator here — "a professional must
# hold at least one organisation membership, SU admins bypass" — written
# before 2026-03-24, deployed uncommitted, and swept into git by the #365
# reconciliation. It was never applied to a route. The #704 triage flagged
# it as a rule somebody intended that was simply not being enforced.
#
# It is the other way round. The only routes it would fit are the four
# @require_professional ones in routes/groups.py, and every one of them is
# how a professional OBTAINS a membership: list-groups, request-membership,
# request-admin, join-by-invite. Gating those on already having one locks a
# new professional out of the only path to getting one, permanently.
# `tests/test_groups.py::TestRequestMembership` proves it — its "regular"
# professional holds no UserOrganisation row and must succeed.
#
# The rule is enforced, just not at this door: the access blob carries
# `organization_ids`, and every consuming service scopes its rows to it
# (Rule 24), so an org-less professional sees nothing anywhere. The SU admin
# console shows the state directly — a red "No org" badge with a "+ Org"
# control beside it (templates/su_admin.html) — because the answer is an
# administrator assigning an organisation, not a 403 at the user.
#
# If a future route genuinely needs "must already belong somewhere", write
# it there against organization_ids rather than reviving this.


def require_service_key(f):
    """Require X-Service-Key header for internal service-to-service calls.
    Uses constant-time comparison."""
    @wraps(f)
    def decorated(*args, **kwargs):
        key = current_app.config.get('INTERNAL_SERVICE_KEY')
        if not key:
            return jsonify({'error': 'unauthorized'}), 401
        provided = request.headers.get('X-Service-Key', '')
        if not provided or not hmac_mod.compare_digest(provided, key):
            return jsonify({'error': 'unauthorized'}), 401
        return f(*args, **kwargs)
    return decorated


def require_group_admin(f):
    """Require user is admin of at least one group."""
    @wraps(f)
    def decorated(*args, **kwargs):
        user = _get_current_user()
        if user is None:
            return jsonify({"error": "authentication_required", "message": "Valid Bearer token required"}), 401
        # SU admins bypass
        if user.is_su_admin:
            return f(*args, **kwargs)
        from src.models.membership import Membership
        session = get_db()
        admin_membership = session.query(Membership).filter_by(
            user_guid=user.guid, status='approved', is_admin=True
        ).first()
        if admin_membership is None:
            return jsonify({"error": "forbidden", "message": "Group admin access required"}), 403
        return f(*args, **kwargs)
    return decorated
