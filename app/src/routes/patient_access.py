"""Patient-app API (#1040): linking an account to a patient, and second-factor
status / reset. Bearer-authenticated JSON, CSRF-exempt like the other API
blueprints. The browser ceremonies (passkey, TOTP enrolment, login step-up)
are in routes/second_factor_ui.py: they need the session cookie.

  POST   /api/patient/link-code                       patient: one-time code
  POST   /api/patient-links/invites                   care: invite a patient
  DELETE /api/patient-links/invites/<invite_guid>     care: revoke an invite
  POST   /api/patient-links/redeem                    care: code + ips guid
  PUT    /api/admin/patient-links/<user_guid>         SU: relink
  DELETE /api/admin/patient-links/<user_guid>         SU: unlink
  GET    /api/auth/second-factor                      own status
  POST   /api/admin/users/<user_guid>/second-factor/reset   SU: lost device
"""
from datetime import datetime, timezone

from flask import Blueprint, current_app, g, jsonify, request

from src.db import get_db
from src.middleware.auth_middleware import require_auth, require_patient, require_su
from src.middleware.rate_limit import rate_limit
from src.services import patient_link as pl
from src.services import second_factor as sf
from src.services.audit_log import audit

patient_access_bp = Blueprint('patient_access', __name__, url_prefix='/api')


def _data():
    data = request.get_json(silent=True) if request.is_json else request.form.to_dict()
    return data or {}


def _refused(e):
    return jsonify({"error": e.code, "message": e.message}), e.status


def _secret():
    return current_app.config.get('SECRET_KEY', '')


def _link_json(link):
    return {
        'user_guid': link.user_guid,
        'patient_link': {
            'ips_patient_guid': link.ips_patient_guid,
            'linked_at': pl._iso(link.linked_at),
            'linked_by': link.linked_by_user_guid,
        },
        'method': link.method,
    }


def invite_url(token):
    return request.host_url.rstrip('/') + '/patient-invite/' + token


# --- patient ----------------------------------------------------------------

@patient_access_bp.route('/patient/link-code', methods=['POST'])
@rate_limit(max_requests=10, window_seconds=60)
@require_auth
@require_patient
def create_link_code():
    """A self-registered patient asks for the code to give to their care unit.
    The code is in this response and nowhere else."""
    try:
        code, expires = pl.create_link_code(get_db(), g.current_user, _secret(),
                                            ip=request.remote_addr)
    except pl.LinkRefused as e:
        return _refused(e)
    return jsonify({"link_code": code, "expires_at": pl._iso(expires),
                    "message": "Give this code to your care unit."}), 201


# --- care -------------------------------------------------------------------

@patient_access_bp.route('/patient-links/invites', methods=['POST'])
@rate_limit(max_requests=30, window_seconds=60)
@require_auth
def create_invite():
    data = _data()
    try:
        invite, token = pl.create_invite(
            get_db(), g.current_user,
            ips_patient_guid=data.get('ips_patient_guid'),
            care_unit_guid=data.get('care_unit_guid'),
            secret_key=_secret(), ip=request.remote_addr)
    except pl.LinkRefused as e:
        return _refused(e)
    return jsonify({
        "invite_guid": invite.guid,
        "ips_patient_guid": invite.ips_patient_guid,
        "care_unit_guid": invite.care_unit_guid,
        "expires_at": pl._iso(invite.expires_at),
        # Shown ONCE. Only a keyed hash of the token is stored.
        "invite_url": invite_url(token),
    }), 201


@patient_access_bp.route('/patient-links/invites/<invite_guid>', methods=['DELETE'])
@require_auth
def revoke_invite(invite_guid):
    try:
        invite = pl.revoke_invite(get_db(), g.current_user, invite_guid,
                                  ip=request.remote_addr)
    except pl.LinkRefused as e:
        return _refused(e)
    return jsonify({"invite_guid": invite.guid, "status": "revoked"}), 200


@patient_access_bp.route('/patient-links/redeem', methods=['POST'])
@rate_limit(max_requests=10, window_seconds=60)
@require_auth
def redeem_link_code():
    data = _data()
    try:
        link = pl.redeem_link_code(
            get_db(), g.current_user, code=data.get('link_code'),
            ips_patient_guid=data.get('ips_patient_guid'),
            secret_key=_secret(), ip=request.remote_addr)
    except pl.LinkRefused as e:
        return _refused(e)
    return jsonify(_link_json(link)), 200


# --- SU ---------------------------------------------------------------------

@patient_access_bp.route('/admin/patient-links/<user_guid>', methods=['PUT'])
@require_auth
@require_su
def su_relink(user_guid):
    data = _data()
    try:
        link = pl.su_relink(get_db(), g.current_user, user_guid=user_guid,
                            ips_patient_guid=data.get('ips_patient_guid'),
                            reason=data.get('reason'),
                            care_unit_guid=data.get('care_unit_guid'),
                            ip=request.remote_addr)
    except pl.LinkRefused as e:
        return _refused(e)
    return jsonify(_link_json(link)), 200


@patient_access_bp.route('/admin/patient-links/<user_guid>', methods=['DELETE'])
@require_auth
@require_su
def su_unlink(user_guid):
    try:
        pl.su_unlink(get_db(), g.current_user, user_guid=user_guid,
                     reason=_data().get('reason'), ip=request.remote_addr)
    except pl.LinkRefused as e:
        return _refused(e)
    return jsonify({"user_guid": user_guid, "patient_link": None}), 200


# --- second factor ----------------------------------------------------------

@patient_access_bp.route('/auth/second-factor', methods=['GET'])
@require_auth
def second_factor_status():
    """Own second-factor state. `verified` is about the token sent with THIS
    request — the same object the access blob carries."""
    session = get_db()
    user = g.current_user
    body = sf.second_factor_for_blob(session, user, g.token_payload)
    body['enrolled_methods'] = sf.enrolled_methods(session, user.guid)
    return jsonify(body), 200


@patient_access_bp.route('/admin/users/<user_guid>/second-factor/reset', methods=['POST'])
@require_auth
@require_su
def su_reset_second_factor(user_guid):
    """Lost device. Removes every passkey and the TOTP secret and ends all the
    user's sessions; at the next login the account enrols a new factor with
    its password alone. So the SU must have verified who is asking: this is
    the one place where a second factor can be replaced without presenting it.
    """
    session = get_db()
    from src.models.user import User
    target = session.query(User).filter_by(guid=user_guid).first()
    if target is None:
        return jsonify({"error": "not_found", "message": "User not found"}), 404
    removed = sf.reset_all(session, user_guid)
    target.token_revocation_epoch = datetime.now(timezone.utc)
    audit('second_factor_reset', user_guid=g.current_user.guid,
          detail={'target_guid': user_guid, 'removed': removed},
          ip=request.remote_addr)
    return jsonify({"user_guid": user_guid, "removed": removed}), 200
