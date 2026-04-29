"""External Partners routes — operates on the Organisation table with
``is_external=True`` (per ticket #96).

Three groups of endpoints:

  Public  (no auth)         GET    /api/public/partner/<guid>
  Internal (service-key)    POST   /api/internal/partner/<guid>/validate
  Admin   (SU bearer/sess)  GET    /api/admin/partners
                            POST   /api/admin/partners
                            GET    /api/admin/partners/<guid>
                            PATCH  /api/admin/partners/<guid>
                            POST   /api/admin/partners/<guid>/rotate
                            POST   /api/admin/partners/<guid>/suspend
                            POST   /api/admin/partners/<guid>/reactivate
                            POST   /api/admin/partners/<guid>/revoke
                            GET    /api/admin/partners/<guid>/audit
                            GET    /api/admin/partners/_meta/catalogue

URL surface keeps "partners" naming because that's the SU-facing concept
(an "external partner" is the registered third-party caller). The
underlying row is just an Organisation with ``is_external=True``, so
``Contract.signer.party.reference = Organisation/<guid>`` works
uniformly across internal payers and external providers — the FHIR
canonical referencing.

Credentials: SU-side admin endpoints accept Bearer token (operator
scripting) OR Flask session cookie (in-page UI). Mirrors
``_require_su_json`` in routes/frontend.py.
"""
import secrets
from datetime import datetime, timezone
from functools import wraps

from flask import Blueprint, request, jsonify, g, session, current_app

from src.db import get_db
from src.middleware.auth_middleware import require_service_key
from src.models.organisation import Organisation, OrganisationAudit
from src.services.auth_service import hash_password, verify_password
from src.services.audit_log import audit
from src.services.jwt_service import (
    validate_token, TokenExpiredError, TokenInvalidError, TokenRevokedError,
)


partners_bp = Blueprint('partners', __name__)


# ---------------------------------------------------------------------------
# Auth helper — Bearer token OR session cookie, then must be SU
# ---------------------------------------------------------------------------

def _resolve_su_user():
    from src.models.user import User
    db = get_db()
    secret = current_app.config.get('SECRET_KEY', '')

    auth_header = request.headers.get('Authorization', '')
    if auth_header.startswith('Bearer '):
        token = auth_header[7:]
        try:
            payload = validate_token(token, secret, db)
            user = db.query(User).filter_by(guid=payload['sub']).first()
            if user is not None:
                return user
        except (TokenExpiredError, TokenInvalidError, TokenRevokedError):
            pass

    sess_token = session.get('token')
    if sess_token:
        try:
            payload = validate_token(sess_token, secret, db)
            user = db.query(User).filter_by(guid=payload['sub']).first()
            if user is not None:
                return user
        except (TokenExpiredError, TokenInvalidError, TokenRevokedError):
            session.pop('token', None)

    return None


def _require_su(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        user = _resolve_su_user()
        if user is None:
            return jsonify(error='authentication_required',
                           message='Valid Bearer token or SU session required'), 401
        if not user.is_su_admin:
            return jsonify(error='forbidden',
                           message='SU admin access required'), 403
        g.current_user = user
        return f(*args, **kwargs)
    return decorated


# ---------------------------------------------------------------------------
# Catalogues — closed sets of permitted scopes and services
# ---------------------------------------------------------------------------

SCOPE_CATALOGUE = [
    'fhir.observation.read',
    'fhir.observation.write',
    'fhir.patient.read',
    'fhir.patient.write',
    'fhir.condition.read',
    'fhir.encounter.read',
    'fhir.questionnaireresponse.read',
    'fhir.questionnaireresponse.write',
    'cohort.read',
    'cohort.export',
]

KNOWN_SERVICES = [
    'cdr.pdhc',
    'gateway.pdhc',
    'dashboard.pdhc',
    'plan.pdhc',
    'contract.pdhc',
    'request.pdhc',
    'rosetta.pdhc',
    'ips.pdhc',
    '1177.pdhc',
    'cgm.pdhc',
    'forms.pdhc',
    'provider1.pdhc',
]


def _generate_secret() -> str:
    return secrets.token_urlsafe(32)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _record_audit(db_session, org: Organisation, event: str,
                  before: dict | None, after: dict | None,
                  actor_user_guid: str | None) -> None:
    db_session.add(OrganisationAudit(
        organisation_guid=org.guid,
        actor_user_guid=actor_user_guid,
        event=event,
        before_json=before,
        after_json=after,
        at=_now(),
    ))


def _validate_payload(data: dict, *, creating: bool) -> tuple[dict | None, str | None]:
    """Validate the SU's form / API submission. Returns (cleaned, None) or
    (None, error). The cleaned dict uses Organisation column names, so the
    caller can pass it straight into the model — except 'display_name'
    which we accept as a synonym for 'name' for UX continuity."""
    if not isinstance(data, dict):
        return None, "request body must be a JSON object"

    cleaned = {}

    if creating:
        for label, key in (('display name', 'display_name'),
                           ('country_code', 'country_code'),
                           ('contact_email', 'contact_email'),
                           ('auth_kind', 'auth_kind')):
            if not data.get(key):
                return None, f"missing required field: {label}"

    if 'display_name' in data:
        cleaned['name'] = data['display_name']

    for f in ('org_number', 'description', 'contact_name',
              'contact_email', 'contact_phone'):
        if f in data:
            v = data[f]
            if v is not None and not isinstance(v, str):
                return None, f"{f} must be a string"
            cleaned[f] = v

    if 'country_code' in data:
        cc = data['country_code']
        if not isinstance(cc, str) or len(cc) != 2:
            return None, "country_code must be a 2-letter ISO code"
        cleaned['country_code'] = cc.upper()

    if 'auth_kind' in data:
        ak = data['auth_kind']
        if ak not in ('oauth_client', 'api_key', 'none'):
            return None, "auth_kind must be one of oauth_client, api_key, none"
        cleaned['auth_kind'] = ak

    for list_field in ('allowed_scopes', 'allowed_services', 'allowed_org_guids'):
        if list_field in data:
            v = data[list_field]
            if v is None and list_field == 'allowed_org_guids':
                cleaned[list_field] = None
                continue
            if not isinstance(v, list) or any(not isinstance(x, str) for x in v):
                return None, f"{list_field} must be a list of strings"
            cleaned[list_field] = v

    if cleaned.get('allowed_scopes'):
        bad = [s for s in cleaned['allowed_scopes'] if s not in SCOPE_CATALOGUE]
        if bad:
            return None, f"unknown scope(s): {bad}; see SCOPE_CATALOGUE"

    if cleaned.get('allowed_services'):
        bad = [s for s in cleaned['allowed_services'] if s not in KNOWN_SERVICES]
        if bad:
            return None, f"unknown service(s): {bad}"

    if 'expires_at' in data:
        v = data['expires_at']
        if v is None:
            cleaned['expires_at'] = None
        else:
            try:
                cleaned['expires_at'] = datetime.fromisoformat(v.replace('Z', '+00:00'))
            except (ValueError, AttributeError):
                return None, "expires_at must be ISO-8601 or null"

    return cleaned, None


def _admin_dict_with_partner_alias(org: Organisation) -> dict:
    """admin_dict() but with ``partner_guid`` and ``display_name`` aliases
    so the existing SU-page JS keeps working without rewrite."""
    d = org.admin_dict()
    d['partner_guid'] = d['guid']
    d['display_name'] = d['name']
    return d


def _public_dict_with_partner_alias(org: Organisation) -> dict:
    d = org.public_dict()
    d['partner_guid'] = d['guid']
    d['display_name'] = d['name']
    return d


# ---------------------------------------------------------------------------
# Public — partner lookup
# ---------------------------------------------------------------------------

@partners_bp.route('/api/public/partner/<guid>', methods=['GET'])
def public_partner(guid: str):
    db = get_db()
    org = db.query(Organisation).filter_by(guid=guid, is_external=True).first()
    if org is None:
        return jsonify({'error': 'not_found'}), 404
    return jsonify(_public_dict_with_partner_alias(org)), 200


# ---------------------------------------------------------------------------
# Internal — service-to-service partner credential validation
# ---------------------------------------------------------------------------

@partners_bp.route('/api/internal/partner/<guid>/validate', methods=['POST'])
@require_service_key
def validate_partner(guid: str):
    body = request.get_json(silent=True) or {}
    secret = body.get('secret', '')
    if not secret:
        return jsonify({'error': 'missing_secret'}), 400

    db = get_db()
    org = db.query(Organisation).filter_by(guid=guid, is_external=True).first()
    if org is None:
        return jsonify({'error': 'not_found'}), 404

    if org.status != 'active':
        return jsonify({'error': 'forbidden', 'reason': org.status or 'unknown'}), 403

    if org.expires_at and org.expires_at < _now():
        return jsonify({'error': 'forbidden', 'reason': 'expired'}), 403

    stored = org.client_secret_hash if org.auth_kind == 'oauth_client' else org.api_key_hash
    if not stored or not verify_password(secret, stored):
        return jsonify({'error': 'unauthorized'}), 401

    blob = {
        'user_type': 'partner',
        'partner_guid': org.guid,
        'organisation_guid': org.guid,  # FHIR-correct alias
        'display_name': org.name,
        'allowed_scopes': org.allowed_scopes or [],
        'allowed_services': org.allowed_services or [],
        'allowed_org_guids': org.allowed_org_guids,
        'is_su_admin': False,
        'organization_ids': org.allowed_org_guids or [],
    }
    return jsonify({'access_blob': blob}), 200


# ---------------------------------------------------------------------------
# Admin — SU CRUD
# ---------------------------------------------------------------------------

@partners_bp.route('/api/admin/partners', methods=['GET'])
@_require_su
def admin_list_partners():
    db = get_db()
    rows = (
        db.query(Organisation)
        .filter_by(is_external=True)
        .order_by(Organisation.created_at.desc())
        .all()
    )
    return jsonify({'partners': [_admin_dict_with_partner_alias(r) for r in rows]}), 200


@partners_bp.route('/api/admin/partners', methods=['POST'])
@_require_su
def admin_create_partner():
    cleaned, err = _validate_payload(request.get_json(silent=True) or {}, creating=True)
    if err:
        return jsonify({'error': 'invalid', 'message': err}), 400

    db = get_db()

    # Uniqueness: organisations.name has a unique constraint. Surface a
    # friendly 409 instead of a SQL integrity error.
    if db.query(Organisation).filter_by(name=cleaned['name']).first():
        return jsonify({
            'error': 'conflict',
            'message': f"an organisation named '{cleaned['name']}' already exists",
        }), 409

    org = Organisation(
        name=cleaned['name'],
        is_external=True,
        country_code=cleaned['country_code'],
        org_number=cleaned.get('org_number'),
        description=cleaned.get('description'),
        contact_name=cleaned.get('contact_name'),
        contact_email=cleaned['contact_email'],
        contact_phone=cleaned.get('contact_phone'),
        auth_kind=cleaned['auth_kind'],
        allowed_scopes=cleaned.get('allowed_scopes', []),
        allowed_services=cleaned.get('allowed_services', []),
        allowed_org_guids=cleaned.get('allowed_org_guids'),
        expires_at=cleaned.get('expires_at'),
        status='active',
        created_by_user_guid=g.current_user.guid,
    )

    cleartext = None
    if org.auth_kind == 'api_key':
        cleartext = _generate_secret()
        org.api_key_hash = hash_password(cleartext)
        org.last_rotated_at = _now()
    elif org.auth_kind == 'oauth_client':
        org.client_id = secrets.token_urlsafe(16)
        cleartext = _generate_secret()
        org.client_secret_hash = hash_password(cleartext)
        org.last_rotated_at = _now()

    db.add(org)
    db.flush()
    _record_audit(db, org, 'created', None, org.admin_dict(), g.current_user.guid)
    db.commit()

    audit('partner.created', user_guid=g.current_user.guid,
          detail={'organisation_guid': org.guid, 'name': org.name})

    response = _admin_dict_with_partner_alias(org)
    if cleartext:
        response['secret_cleartext'] = cleartext
        response['secret_warning'] = (
            "This secret will not be shown again. Copy it now and deliver "
            "it to the partner via a secure channel."
        )
    return jsonify(response), 201


@partners_bp.route('/api/admin/partners/<guid>', methods=['GET'])
@_require_su
def admin_get_partner(guid: str):
    db = get_db()
    org = db.query(Organisation).filter_by(guid=guid, is_external=True).first()
    if org is None:
        return jsonify({'error': 'not_found'}), 404
    return jsonify(_admin_dict_with_partner_alias(org)), 200


@partners_bp.route('/api/admin/partners/<guid>', methods=['PATCH'])
@_require_su
def admin_edit_partner(guid: str):
    cleaned, err = _validate_payload(request.get_json(silent=True) or {}, creating=False)
    if err:
        return jsonify({'error': 'invalid', 'message': err}), 400

    immutable_attempted = [k for k in ('country_code', 'org_number', 'auth_kind')
                          if k in cleaned]
    if immutable_attempted:
        return jsonify({
            'error': 'invalid',
            'message': f"cannot change {immutable_attempted} after creation; "
                       f"revoke and re-register",
        }), 400

    db = get_db()
    org = db.query(Organisation).filter_by(guid=guid, is_external=True).first()
    if org is None:
        return jsonify({'error': 'not_found'}), 404

    before = org.admin_dict()
    for k, v in cleaned.items():
        setattr(org, k, v)
    org.updated_at = _now()
    _record_audit(db, org, 'edited', before, org.admin_dict(), g.current_user.guid)
    db.commit()
    audit('partner.edited', user_guid=g.current_user.guid,
          detail={'organisation_guid': org.guid})
    return jsonify(_admin_dict_with_partner_alias(org)), 200


@partners_bp.route('/api/admin/partners/<guid>/rotate', methods=['POST'])
@_require_su
def admin_rotate_credential(guid: str):
    db = get_db()
    org = db.query(Organisation).filter_by(guid=guid, is_external=True).first()
    if org is None:
        return jsonify({'error': 'not_found'}), 404
    if org.auth_kind == 'none':
        return jsonify({'error': 'invalid', 'message': 'partner has no credential'}), 400

    before = org.admin_dict()
    cleartext = _generate_secret()
    if org.auth_kind == 'api_key':
        org.api_key_hash = hash_password(cleartext)
    else:
        org.client_secret_hash = hash_password(cleartext)
    org.last_rotated_at = _now()
    org.updated_at = org.last_rotated_at
    _record_audit(db, org, 'rotated', before, org.admin_dict(), g.current_user.guid)
    db.commit()
    audit('partner.rotated', user_guid=g.current_user.guid,
          detail={'organisation_guid': org.guid})

    return jsonify({
        **_admin_dict_with_partner_alias(org),
        'secret_cleartext': cleartext,
        'secret_warning': (
            "Old credential is now invalid. New secret will not be shown "
            "again — copy it now and deliver to the partner securely."
        ),
    }), 200


@partners_bp.route('/api/admin/partners/<guid>/suspend', methods=['POST'])
@_require_su
def admin_suspend(guid: str):
    db = get_db()
    org = db.query(Organisation).filter_by(guid=guid, is_external=True).first()
    if org is None:
        return jsonify({'error': 'not_found'}), 404
    if org.status == 'revoked':
        return jsonify({'error': 'invalid', 'message': 'already revoked'}), 400
    before = org.admin_dict()
    org.status = 'suspended'
    org.updated_at = _now()
    _record_audit(db, org, 'suspended', before, org.admin_dict(), g.current_user.guid)
    db.commit()
    audit('partner.suspended', user_guid=g.current_user.guid,
          detail={'organisation_guid': org.guid})
    return jsonify(_admin_dict_with_partner_alias(org)), 200


@partners_bp.route('/api/admin/partners/<guid>/reactivate', methods=['POST'])
@_require_su
def admin_reactivate(guid: str):
    db = get_db()
    org = db.query(Organisation).filter_by(guid=guid, is_external=True).first()
    if org is None:
        return jsonify({'error': 'not_found'}), 404
    if org.status != 'suspended':
        return jsonify({'error': 'invalid', 'message': f'not suspended (status={org.status})'}), 400
    before = org.admin_dict()
    org.status = 'active'
    org.updated_at = _now()
    _record_audit(db, org, 'reactivated', before, org.admin_dict(), g.current_user.guid)
    db.commit()
    audit('partner.reactivated', user_guid=g.current_user.guid,
          detail={'organisation_guid': org.guid})
    return jsonify(_admin_dict_with_partner_alias(org)), 200


@partners_bp.route('/api/admin/partners/<guid>/revoke', methods=['POST'])
@_require_su
def admin_revoke(guid: str):
    body = request.get_json(silent=True) or {}
    reason = body.get('reason') or ''
    db = get_db()
    org = db.query(Organisation).filter_by(guid=guid, is_external=True).first()
    if org is None:
        return jsonify({'error': 'not_found'}), 404
    if org.status == 'revoked':
        return jsonify({'error': 'invalid', 'message': 'already revoked'}), 400
    before = org.admin_dict()
    org.status = 'revoked'
    org.revoked_at = _now()
    org.revoke_reason = reason
    org.updated_at = org.revoked_at
    _record_audit(db, org, 'revoked', before, org.admin_dict(), g.current_user.guid)
    db.commit()
    audit('partner.revoked', user_guid=g.current_user.guid,
          detail={'organisation_guid': org.guid, 'reason': reason})
    return jsonify(_admin_dict_with_partner_alias(org)), 200


@partners_bp.route('/api/admin/partners/<guid>/audit', methods=['GET'])
@_require_su
def admin_partner_audit(guid: str):
    db = get_db()
    org = db.query(Organisation).filter_by(guid=guid, is_external=True).first()
    if org is None:
        return jsonify({'error': 'not_found'}), 404
    entries = (
        db.query(OrganisationAudit)
        .filter_by(organisation_guid=guid)
        .order_by(OrganisationAudit.at.desc())
        .limit(200)
        .all()
    )
    return jsonify({'audit': [e.to_dict() for e in entries]}), 200


@partners_bp.route('/api/admin/partners/_meta/catalogue', methods=['GET'])
@_require_su
def admin_catalogue():
    return jsonify({
        'scopes': SCOPE_CATALOGUE,
        'services': KNOWN_SERVICES,
    }), 200
