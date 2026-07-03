"""Internal API — service-to-service endpoints. Require X-Service-Key."""
from flask import Blueprint, jsonify

from src.db import get_db
from src.middleware.auth_middleware import require_service_key

internal_bp = Blueprint('internal', __name__)


@internal_bp.route('/internal/organisations/<org_guid>/push-config', methods=['GET'])
@require_service_key
def get_org_push_config(org_guid):
    """Return push config for a provider organisation (auto-provisioning)."""
    session = get_db()
    from src.models.organisation import Organisation

    org = session.query(Organisation).filter_by(guid=org_guid).first()
    if org is None:
        return jsonify({'error': 'not_found'}), 404

    return jsonify({
        'organisation_guid': org.guid,
        'name': org.name,
        'push_endpoint_url': org.push_endpoint_url,
        'push_secret': org.push_secret,
    }), 200
