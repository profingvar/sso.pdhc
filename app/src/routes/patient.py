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
    """POST /api/patient/register — self-enrolment.

    Creates user (type=patient) + patient row.
    Requires: email, password, personnummer (12 digits), organisation_guid.
    Returns FHIR Patient-shaped response.

    UNAUTHENTICATED BY DESIGN, and that is now stated rather than implied
    (#904). This docstring used to promise "Dev-mode only flag checked via
    config", and no such flag existed anywhere in `app/src` — `config` was
    assigned on the next line and never read. That is the #804 shape: a
    docstring advertising a capability that is not there, which is worse than
    silence because it stops the next reader looking.

    Self-enrolment IS intended production behaviour. Measured 2026-11-02:
    `frontend.register_patient` serves `register_patient.html` at
    `/register-patient` with no decorator at all and a docstring reading
    "Patient self-registration". #904 asked whether self-enrolment was
    intended; the UI answers it. Both paths now call
    `services/patient_enrolment.enrol_patient` (#910), so they cannot drift,
    and only care units are accepted.

    PERSONNUMMER IS DELIBERATELY NOT CHECKSUM-VALIDATED. Do not "harden" this
    by adding a Luhn check. The platform requires synthetic personnummer to
    carry an INVALID checksum (#405 confinement), so a checksum check would
    reject every synthetic patient the platform creates on purpose — including
    this module's own tests, whose `199001011234` and `200101011234` both fail
    Luhn by design. The 12-digit shape check is the whole of the intended
    validation.
    """
    from src.services.patient_enrolment import EnrolmentRefused, enrol_patient
    data = request.get_json(silent=True) if request.is_json else request.form.to_dict()
    data = data or {}
    try:
        user, patient = enrol_patient(
            get_db(), email=data.get('email'), password=data.get('password'),
            personnummer=data.get('personnummer'),
            organisation_guid=data.get('organisation_guid'), ip=request.remote_addr)
    except EnrolmentRefused as e:
        if e.code == 'validation_error':
            return jsonify({"error": e.code, "messages": e.messages}), e.status
        return jsonify({"error": e.code, "message": e.messages[0]}), e.status

    from src.models.patient import Patient
    return jsonify({
        "resourceType": Patient.FHIR_RESOURCE_TYPE,
        "user_guid": user.guid,
        "patient_guid": patient.guid,
        "email": user.email,
        "personnummer": patient.personnummer,
        "organisation_guid": patient.organisation_guid,
        "in_registry": False,
        "registries": [],
    }), 201


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
