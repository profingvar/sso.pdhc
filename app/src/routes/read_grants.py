"""External read grants: issue, list, revoke (SU) and validate (CDRs).

See src/models/read_grant.py for what a grant is. The CDR side is cdr.pdhc
`app/api/external_read.py`, which calls `POST /api/internal/read-grants/validate`
on EVERY request (no caching, so a revoke takes effect at once) with its own
SSO client pair -- the same credential it already uses for /api/auth/me/service.
"""
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from flask import Blueprint, current_app, g, jsonify, request

from src.db import get_db
from src.models.organisation import Organisation
from src.models.read_grant import KEY_PREFIX, KNOWN_CDRS, MAX_HOURS, PURPOSES, ReadGrant
from src.models.research_project import ResearchProject
from src.routes.partners import _require_su
from src.services.audit_log import audit

read_grants_bp = Blueprint('read_grants', __name__)


def _now():
    return datetime.now(timezone.utc)


def _hash(secret: str) -> str:
    # The secret is 256 random bits, so a fast hash is the right tool: there
    # is nothing to brute-force, and the CDRs validate on every request.
    return hashlib.sha256(secret.encode()).hexdigest()


def _aware(d):
    return d if d is None or d.tzinfo else d.replace(tzinfo=timezone.utc)


def _bad(msg, code=400, **extra):
    return jsonify({'error': 'invalid' if code == 400 else 'refused', 'message': msg, **extra}), code


# --------------------------------------------------------------- SU: issue

@read_grants_bp.route('/api/admin/partners/<partner_guid>/read-grants', methods=['POST'])
@_require_su
def issue(partner_guid):
    db = get_db()
    partner = db.query(Organisation).filter_by(guid=partner_guid, is_external=True).first()
    if partner is None:
        return _bad('no external partner with that guid', 404)
    if (partner.status or 'active') != 'active':
        return _bad(f'partner is {partner.status}', 409)

    body = request.get_json(silent=True) or {}
    orgs = body.get('org_guids')
    cdrs = body.get('cdrs')
    hours = body.get('hours', MAX_HOURS)
    purpose = (body.get('purpose') or '').strip() or None

    if not isinstance(orgs, list) or not orgs or not all(isinstance(o, str) for o in orgs):
        return _bad('org_guids: choose at least one organisation')
    orgs = sorted(set(orgs))
    known = {o.guid for o in db.query(Organisation)
             .filter(Organisation.guid.in_(orgs), Organisation.is_external.is_(False))}
    unknown = [o for o in orgs if o not in known]
    if unknown:
        return _bad('org_guids: not care organisations in SSO', unknown=unknown)
    if not isinstance(cdrs, list) or not cdrs:
        return _bad('cdrs: choose at least one CDR')
    cdrs = sorted(set(cdrs))
    if any(c not in KNOWN_CDRS for c in cdrs):
        return _bad(f'cdrs: each must be one of {", ".join(KNOWN_CDRS)}')
    if isinstance(hours, bool) or not isinstance(hours, int) or not 1 <= hours <= MAX_HOURS:
        return _bad(f'hours: a whole number from 1 to {MAX_HOURS}')
    if purpose and len(purpose) > 500:
        return _bad('purpose: at most 500 characters')
    code = body.get('purpose_code')
    if code not in PURPOSES:
        return _bad(f'purpose_code: one of {", ".join(PURPOSES)}')
    projects = body.get('research_project_guids') or []
    if not isinstance(projects, list) or not all(isinstance(x, str) for x in projects):
        return _bad('research_project_guids: a list of guids')
    projects = sorted(set(projects))
    if code == 'research':
        if not projects:
            return _bad('research_project_guids: research needs at least one project')
        found = {p.guid for p in db.query(ResearchProject).filter(ResearchProject.guid.in_(projects))}
        missing = [x for x in projects if x not in found]
        if missing:
            return _bad('research_project_guids: unknown research projects', unknown=missing)
    else:
        projects = []

    secret = secrets.token_urlsafe(32)
    now = _now()
    grant = ReadGrant(partner_guid=partner.guid, org_guids=orgs, cdrs=cdrs,
                      secret_hash=_hash(secret), purpose=purpose,
                      purpose_code=code, research_project_guids=projects,
                      created_by_user_guid=g.current_user.guid,
                      created_at=now, expires_at=now + timedelta(hours=hours))
    db.add(grant)
    db.commit()
    audit('read_grant.issued', user_guid=g.current_user.guid,
          detail={'grant_guid': grant.guid, 'partner_guid': partner.guid,
                  'org_guids': orgs, 'cdrs': cdrs, 'hours': hours,
                  'purpose_code': code, 'research_project_guids': projects},
          ip=request.remote_addr)
    return jsonify({**grant.to_dict(), 'partner_name': partner.name,
                    'key': f'{KEY_PREFIX}.{grant.guid}.{secret}',
                    'key_warning': 'This key is shown once. Send it to the partner '
                                   'over a secure channel.'}), 201


@read_grants_bp.route('/api/admin/partners/<partner_guid>/read-grants', methods=['GET'])
@_require_su
def list_for_partner(partner_guid):
    db = get_db()
    rows = (db.query(ReadGrant).filter_by(partner_guid=partner_guid)
            .order_by(ReadGrant.created_at.desc()).all())
    return jsonify({'items': [r.to_dict() for r in rows]})


@read_grants_bp.route('/api/admin/read-grants/<grant_guid>/revoke', methods=['POST'])
@_require_su
def revoke(grant_guid):
    db = get_db()
    grant = db.query(ReadGrant).filter_by(guid=grant_guid).first()
    if grant is None:
        return _bad('no such grant', 404)
    if grant.revoked_at is None:
        grant.revoked_at = _now()
        grant.revoked_by_user_guid = g.current_user.guid
        db.commit()
        audit('read_grant.revoked', user_guid=g.current_user.guid,
              detail={'grant_guid': grant.guid}, ip=request.remote_addr)
    return jsonify(grant.to_dict())


# --------------------------------------------------------------- CDR: validate

def _calling_service_ok() -> bool:
    creds = current_app.config.get('SERVICE_CREDENTIALS', {}) or {}
    cid = request.headers.get('X-SSO-Client-Id', '')
    sec = request.headers.get('X-SSO-Client-Secret', '')
    expected = creds.get(cid) if cid else None
    return bool(expected) and hmac.compare_digest(sec.encode(), expected.encode())


@read_grants_bp.route('/api/internal/read-grants/validate', methods=['POST'])
def validate():
    """Body {"key": "<rg.guid.secret>", "cdr": "cdr2"} -> the grant's scope.

    401 for anything that is not a working key (unknown grant and wrong
    secret answer the same), 403 with a reason for a real key that may not be
    used here and now."""
    if not _calling_service_ok():
        return jsonify({'error': 'invalid_service_credentials'}), 403
    body = request.get_json(silent=True) or {}
    key, cdr = body.get('key') or '', body.get('cdr') or ''
    parts = key.split('.', 2) if isinstance(key, str) else []
    if len(parts) != 3 or parts[0] != KEY_PREFIX:
        return jsonify({'error': 'invalid_key'}), 401
    _, guid, secret = parts
    db = get_db()
    grant = db.query(ReadGrant).filter_by(guid=guid).first()
    if grant is None or not hmac.compare_digest(_hash(secret), grant.secret_hash):
        return jsonify({'error': 'invalid_key'}), 401

    now = _now()
    created = _aware(grant.created_at)
    reason = None
    if grant.revoked_at:
        reason = 'revoked'
    elif _aware(grant.expires_at) <= now or now - created > timedelta(hours=MAX_HOURS):
        reason = 'expired'
    else:
        partner = db.query(Organisation).filter_by(guid=grant.partner_guid).first()
        if partner is None or (partner.status or 'active') != 'active':
            reason = 'partner_inactive'
        elif cdr not in (grant.cdrs or []):
            reason = 'cdr_not_granted'
    if reason:
        return jsonify({'error': 'forbidden', 'reason': reason}), 403

    grant.last_used_at = now
    db.commit()
    partner = db.query(Organisation).filter_by(guid=grant.partner_guid).first()
    return jsonify({'grant_guid': grant.guid, 'partner_guid': grant.partner_guid,
                    'partner_name': partner.name if partner else None,
                    'org_guids': list(grant.org_guids or []),
                    'cdrs': list(grant.cdrs or []),
                    'purpose_code': grant.purpose_code,
                    'research_project_guids': list(grant.research_project_guids or []),
                    'expires_at': _aware(grant.expires_at).isoformat()})
