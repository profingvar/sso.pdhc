"""Patient self-enrolment: ONE implementation for both routes (#910).

`routes/patient.py::register` (JSON) and `routes/frontend.py::register_patient`
(HTML) each carried their own copy, and the copies had drifted: the HTML one
had no rate limit until #904, wrote no audit entry and did not set the
registry fields. Both now call `enrol_patient`, so a rule changes in one place.

UNAUTHENTICATED BY DESIGN — self-enrolment is intended. PERSONNUMMER IS
DELIBERATELY NOT CHECKSUM-VALIDATED: synthetic personnummer carry an INVALID
checksum on purpose (#405), so a Luhn check would refuse every synthetic
patient. The 12-digit shape is the whole of the validation (#904).

Which organisation: only `care_hierarchy.enrollable_care_units` (operator
2026-11-16). Anything else — external, a vårdgivare that has units, or a guid
that does not exist — answers the SAME "not found", so an anonymous caller
cannot use the form to learn which guids exist beyond the list it is shown.
"""
import re

from src.models.patient import Patient
from src.models.user import User
from src.services.audit_log import audit
from src.services.auth_service import hash_password
from src.services.care_hierarchy import enrollable_care_units


class EnrolmentRefused(Exception):
    def __init__(self, status, code, messages):
        super().__init__('; '.join(messages))
        self.status, self.code, self.messages = status, code, list(messages)


def enrollable_choices(session):
    """[(guid, name)] for the form."""
    return [(o.guid, o.name) for o in enrollable_care_units(session)]


def enrol_patient(session, *, email, password, personnummer, organisation_guid, ip=None):
    """Create user (type=patient) + patient row. Raises EnrolmentRefused."""
    email = (email or '').strip().lower()
    password = password or ''
    personnummer = (personnummer or '').strip()
    organisation_guid = (organisation_guid or '').strip()

    errors = []
    if not email:
        errors.append('email is required')
    if len(password) < 8:
        errors.append('password must be at least 8 characters')
    if not re.match(r'^\d{12}$', personnummer):
        errors.append('personnummer must be exactly 12 digits')
    if not organisation_guid:
        errors.append('organisation_guid is required')
    if errors:
        raise EnrolmentRefused(400, 'validation_error', errors)

    if organisation_guid not in {g for g, _ in enrollable_choices(session)}:
        raise EnrolmentRefused(404, 'not_found', ['Organisation not found'])
    if session.query(User).filter_by(email=email).first():
        raise EnrolmentRefused(409, 'conflict', ['Email already registered'])
    if session.query(Patient).filter_by(personnummer=personnummer).first():
        raise EnrolmentRefused(409, 'conflict', ['Personnummer already registered'])

    user = User(email=email, password_hash=hash_password(password),
                user_type='patient', is_su_admin=False)
    session.add(user)
    session.flush()
    patient = Patient(user_id=user.id, personnummer=personnummer,
                      organisation_guid=organisation_guid,
                      in_registry=False, registries=[])
    session.add(patient)
    session.flush()

    audit('patient_register', user_guid=user.guid,
          detail={'email': email, 'personnummer': personnummer[:4] + '****',
                  'organisation_guid': organisation_guid},
          ip=ip)
    return user, patient
