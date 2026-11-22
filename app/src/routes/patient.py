"""Patient API routes — register, registry-status."""
import re

from flask import Blueprint, request, jsonify, g

from src.db import get_db
from src.services.auth_service import hash_password
from src.services.audit_log import audit
from src.middleware.auth_middleware import require_auth, require_patient
from src.middleware.rate_limit import rate_limit

patient_bp = Blueprint('patient', __name__, url_prefix='/api/patient')


@patient_bp.route('/register', methods=['POST'])
@rate_limit(max_requests=10, window_seconds=60)
def register():
    """POST /api/patient/register — self-registration (#1049, contract s7.1).

    Body: given_name, family_name, personnummer (10 or 12 digits, real date),
    email (the login), mobile (optional, Swedish), organisation_guid (a care
    unit from the registration list), password.

    202 {"status": "received", "message": <REGISTERED_TEXT>} for EVERY
    non-error outcome (already registered / created and linked / created
    unlinked), so the answer says nothing about who has an account. Form
    errors: 400 validation_error {messages}, 404 not_found (unit),
    422 unit_not_available, 503 eps_not_configured | eps_unavailable |
    not_committed. All rules are in services/patient_enrolment.py; this route
    reads no config itself.

    UNAUTHENTICATED BY DESIGN (#904): self-enrolment is intended production
    behaviour, rate-limited like the HTML door. The personnummer check digit
    is never a reason to refuse a SYNTHETIC number (#405); under the pilot's
    synthetic-only rule it is a VALID one that is refused, in the service.
    """
    from src.services.patient_enrolment import (REGISTERED_TEXT, EnrolmentRefused,
                                                register_patient)
    data = request.get_json(silent=True) if request.is_json else request.form.to_dict()
    data = data or {}
    try:
        register_patient(
            get_db(), given_name=data.get('given_name'),
            family_name=data.get('family_name'),
            personnummer=data.get('personnummer'), email=data.get('email'),
            mobile=data.get('mobile'),
            organisation_guid=data.get('organisation_guid'),
            password=data.get('password'), ip=request.remote_addr)
    except EnrolmentRefused as e:
        if e.code == 'validation_error':
            return jsonify({"error": e.code, "messages": e.messages}), e.status
        return jsonify({"error": e.code, "message": e.messages[0]}), e.status
    # #1049: ONE answer for "already registered", "registered and linked" and
    # "registered, not linked" — no guid, no echo of what was sent.
    return jsonify({"status": "received", "message": REGISTERED_TEXT}), 202


@patient_bp.route('/registry-status', methods=['GET'])
@require_auth
@require_patient
def registry_status():
    """GET /api/patient/registry-status — own registry status.

    Returns in_registry flag and list of registries from IPS.
    Only own data (enforced by require_patient + patient_guid match).
    """
    session = get_db()
    user = g.current_user

    from src.models.patient import Patient
    patient = session.query(Patient).filter_by(user_id=user.id).first()
    if patient is None:
        return jsonify({"error": "not_found", "message": "Patient record not found"}), 404

    return jsonify({
        "resourceType": Patient.FHIR_RESOURCE_TYPE,
        "patient_guid": patient.guid,
        "user_guid": user.guid,
        "personnummer": patient.personnummer,
        "organisation_guid": patient.organisation_guid,
        "in_registry": patient.in_registry,
        "registries": patient.registries or [],
    }), 200
