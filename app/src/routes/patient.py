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
    "Patient self-registration". So there are TWO anonymous enrolment paths,
    this one being the better protected of the two because it at least carries
    a rate limit. #904 asked whether self-enrolment was intended; the UI
    answers it.

    PERSONNUMMER IS DELIBERATELY NOT CHECKSUM-VALIDATED. Do not "harden" this
    by adding a Luhn check. The platform requires synthetic personnummer to
    carry an INVALID checksum (#405 confinement), so a checksum check would
    reject every synthetic patient the platform creates on purpose — including
    this module's own tests, whose `199001011234` and `200101011234` both fail
    Luhn by design. The 12-digit shape check is the whole of the intended
    validation.
    """
    session = get_db()

    data = request.get_json() if request.is_json else request.form.to_dict()

    email = data.get('email', '').strip().lower()
    password = data.get('password', '')
    personnummer = data.get('personnummer', '').strip()
    organisation_guid = data.get('organisation_guid', '').strip()

    # Validation
    errors = []
    if not email:
        errors.append('email is required')
    if not password or len(password) < 8:
        errors.append('password must be at least 8 characters')
    if not personnummer or not re.match(r'^\d{12}$', personnummer):
        errors.append('personnummer must be exactly 12 digits')
    if not organisation_guid:
        errors.append('organisation_guid is required')

    if errors:
        return jsonify({"error": "validation_error", "messages": errors}), 400

    # Check organisation exists
    from src.models.organisation import Organisation
    org = session.query(Organisation).filter_by(guid=organisation_guid).first()
    if org is None:
        return jsonify({"error": "not_found", "message": "Organisation not found"}), 404

    # Check duplicates
    from src.models.user import User
    from src.models.patient import Patient

    if session.query(User).filter_by(email=email).first():
        return jsonify({"error": "conflict", "message": "Email already registered"}), 409

    if session.query(Patient).filter_by(personnummer=personnummer).first():
        return jsonify({"error": "conflict", "message": "Personnummer already registered"}), 409

    # Create user + patient
    user = User(
        email=email,
        password_hash=hash_password(password),
        user_type='patient',
        is_su_admin=False,
    )
    session.add(user)
    session.flush()  # get user.id

    patient = Patient(
        user_id=user.id,
        personnummer=personnummer,
        organisation_guid=organisation_guid,
        in_registry=False,
        registries=[],
    )
    session.add(patient)
    session.flush()  # get patient.guid

    audit('patient_register', user_guid=user.guid,
          detail={'email': email, 'personnummer': personnummer[:4] + '****'},
          ip=request.remote_addr)

    return jsonify({
        "resourceType": Patient.FHIR_RESOURCE_TYPE,
        "user_guid": user.guid,
        "patient_guid": patient.guid,
        "email": email,
        "personnummer": personnummer,
        "organisation_guid": organisation_guid,
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
