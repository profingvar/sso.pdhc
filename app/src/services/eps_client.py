"""The one question SSO asks eps.pdhc (formerly ips.pdhc), #1040:

    does this patient guid exist, and is the patient listed at this care unit?

Asked at every point a patient link is created or changed (services/
patient_link.py). Without it SSO took the professional's word for the guid,
and a wrong guid shows one patient another patient's tasks.

    GET {EPS_BASE_URL}/api/v1/patients/<guid>/clinics
    Authorization: ApiKey <EPS_API_KEY>

eps reads ONLY the Authorization header (its `require_auth`); `X-API-Key` is
ignored there. The route answers 200 with a list of clinics, or 404
`{"error": "Patient not found"}`.

IDENTIFIER SPACES — checked in eps's model, not assumed. Each clinic in the
answer has TWO guids:
    `guid`               eps's own clinic row id. NOT an SSO identifier.
    `organisation_guid`  "the sso organisation this clinic IS" (eps
                         models/clinic.py), repeated as `care_unit_guid`.
SSO's care-unit guid is an `organisations.guid`, so it is compared with
`care_unit_guid` / `organisation_guid` and never with `guid`.

FAIL CLOSED, BY NAME. Every outcome that is not a clean "yes" is a refusal
with its own code, and the three kinds are never read as each other:

    eps_not_configured   EPS_BASE_URL or EPS_API_KEY unset. No default URL:
                         unset reads as unset.
    eps_unavailable      no answer, 401/403 (our key is wrong), 3xx, 5xx, a 200
                         that is not a list, or a 404 that is not eps's own
                         "Patient not found". A refusal or an outage is never
                         "patient not found" and never "ok". The 404 rule
                         matters on this platform: a hostname nginx does not
                         know falls through to another service, which answers
                         404 for any path.
    patient_not_found    eps's own 404.
    patient_not_at_unit  the patient exists, no active clinic is that unit.

Local development only: EPS_LINK_CHECK_DISABLED=true skips the call. It is
honoured only when FLASK_ENV=development, and `create_app` refuses to start
with it set under any other FLASK_ENV — the same switch that already relaxes
the session cookie in this repo.
"""
import requests

TIMEOUT_SECONDS = 5
NOT_CONFIGURED = 'eps_not_configured'
UNAVAILABLE = 'eps_unavailable'
PATIENT_NOT_FOUND = 'patient_not_found'
PATIENT_NOT_AT_UNIT = 'patient_not_at_unit'
#: eps's own body for an unknown patient (ips.pdhc api/patient_routes.py).
_EPS_NOT_FOUND = 'patient not found'


class EpsRefusal(Exception):
    def __init__(self, code, detail=''):
        super().__init__(f'{code}: {detail}' if detail else code)
        self.code, self.detail = code, detail


def _truthy(value):
    return str(value or '').strip().lower() in ('1', 'true', 'yes')


def opt_out_requested(config):
    return _truthy(config.get('EPS_LINK_CHECK_DISABLED'))


def opt_out_allowed(config):
    """The dev opt-out exists only under FLASK_ENV=development."""
    return config.get('FLASK_ENV') == 'development'


def refuse_opt_out_outside_development(config):
    """Called by create_app. A production process must not start with the
    check switched off."""
    if opt_out_requested(config) and not opt_out_allowed(config):
        raise RuntimeError(
            'EPS_LINK_CHECK_DISABLED is set but FLASK_ENV is not "development". '
            'The eps check on patient links cannot be disabled in production.')


def _http_get(url, headers, timeout):
    """The ONLY outbound call. Tests patch this and nothing else."""
    return requests.get(url, headers=headers, timeout=timeout, allow_redirects=False)


def verify_patient(config, ips_patient_guid, care_unit_guid=None):
    """Return normally when the patient exists in eps and — if a care unit is
    named — is listed at it. Raise EpsRefusal otherwise.

    Returns 'checked', or 'skipped' under the development opt-out."""
    if opt_out_requested(config) and opt_out_allowed(config):
        return 'skipped'
    clinics = _fetch_clinics(config, ips_patient_guid)
    if care_unit_guid:
        wanted = str(care_unit_guid).strip().lower()
        if wanted in _active_units(clinics):
            return 'checked'
        raise EpsRefusal(PATIENT_NOT_AT_UNIT)
    return 'checked'


def listed_units(config, ips_patient_guid):
    """#1059: the care units (lower-case guids) the patient is actively listed
    at in eps. Raise EpsRefusal(NOT_CONFIGURED | UNAVAILABLE |
    PATIENT_NOT_FOUND). NO development opt-out: this decides who may read a
    patient's extract, and without eps there is no answer to that."""
    return _active_units(_fetch_clinics(config, ips_patient_guid))


def _active_units(clinics):
    units = set()
    for c in clinics:
        if not isinstance(c, dict) or c.get('is_active') is False:
            continue
        unit = c.get('care_unit_guid') or c.get('organisation_guid')
        if unit:
            units.add(str(unit).strip().lower())
    return units


def _fetch_clinics(config, ips_patient_guid):
    """GET the patient's clinics from eps; the list, or EpsRefusal."""
    base = (config.get('EPS_BASE_URL') or '').strip().rstrip('/')
    key = (config.get('EPS_API_KEY') or '').strip()
    if not base or not key:
        raise EpsRefusal(NOT_CONFIGURED, 'EPS_BASE_URL / EPS_API_KEY')
    url = f'{base}/api/v1/patients/{ips_patient_guid}/clinics'
    try:
        resp = _http_get(url, {'Authorization': f'ApiKey {key}',
                               'Accept': 'application/json'}, TIMEOUT_SECONDS)
    except requests.RequestException as e:
        raise EpsRefusal(UNAVAILABLE, type(e).__name__)

    def body():
        try:
            return resp.json()
        except ValueError:
            return None

    if resp.status_code == 404:
        data = body()
        if isinstance(data, dict) and str(data.get('error', '')).lower() == _EPS_NOT_FOUND:
            raise EpsRefusal(PATIENT_NOT_FOUND)
        raise EpsRefusal(UNAVAILABLE, 'HTTP 404 that is not eps\'s own')
    if resp.status_code != 200:
        raise EpsRefusal(UNAVAILABLE, f'HTTP {resp.status_code}')
    clinics = body()
    if not isinstance(clinics, list):
        raise EpsRefusal(UNAVAILABLE, 'unexpected body')
    # eps's route also resolves a FHIR resource_id, and names the platform
    # guid it resolved to in a header. What sso stores must BE the platform
    # guid (it is what orders and CDR rows carry), so anything else is refused
    # rather than stored. An eps too old to send the header is not refused.
    canonical = (getattr(resp, 'headers', None) or {}).get('X-PDHC-Patient-Guid')
    if canonical and str(canonical).strip().lower() != str(ips_patient_guid).strip().lower():
        raise EpsRefusal(PATIENT_NOT_FOUND, 'identifier is not the platform guid')
    return clinics


# --- #1049: self-registration creates the patient in eps --------------------
#
#     POST {EPS_BASE_URL}/api/v1/patients/self-register
#     Authorization: ApiKey <EPS_API_KEY>        (scope patients:self-register)
#     {"personnummer", "given_name", "family_name", "care_unit_guid",
#      "email"?, "mobile"?}
#
# eps answers (contract s7.2):
#     201 {"patient_guid"} + X-PDHC-Patient-Guid   created
#     409 {"error": "exists"}                       a patient with that number
#     422 {"error": "unknown_unit"}                 the unit is no active clinic
# Anything else, and a 409/422 whose body is not eps's own, is UNAVAILABLE:
# the 404 rule above applies to every status (another vhost can answer).
# NO development opt-out: without eps there is no patient guid to link.

CREATED = 'created'
EXISTS = 'exists'
UNKNOWN_UNIT = 'unknown_unit'


def _http_post(url, headers, json_body, timeout):
    """The ONLY outbound POST. Tests patch this and nothing else."""
    return requests.post(url, headers=headers, json=json_body, timeout=timeout,
                         allow_redirects=False)


def self_register(config, *, personnummer, given_name, family_name,
                  care_unit_guid, email=None, mobile=None):
    """Return (CREATED, platform patient guid) or (EXISTS, None).
    Raise EpsRefusal(NOT_CONFIGURED | UNAVAILABLE | UNKNOWN_UNIT)."""
    base = (config.get('EPS_BASE_URL') or '').strip().rstrip('/')
    key = (config.get('EPS_API_KEY') or '').strip()
    if not base or not key:
        raise EpsRefusal(NOT_CONFIGURED, 'EPS_BASE_URL / EPS_API_KEY')
    body = {'personnummer': personnummer, 'given_name': given_name,
            'family_name': family_name, 'care_unit_guid': care_unit_guid}
    if email:
        body['email'] = email
    if mobile:
        body['mobile'] = mobile
    try:
        resp = _http_post(f'{base}/api/v1/patients/self-register',
                          {'Authorization': f'ApiKey {key}',
                           'Accept': 'application/json'}, body, TIMEOUT_SECONDS)
    except requests.RequestException as e:
        raise EpsRefusal(UNAVAILABLE, type(e).__name__)
    try:
        data = resp.json()
    except ValueError:
        data = None
    error = str(data.get('error', '')).lower() if isinstance(data, dict) else ''
    if resp.status_code == 409 and error == 'exists':
        return EXISTS, None
    if resp.status_code == 422 and error == 'unknown_unit':
        raise EpsRefusal(UNKNOWN_UNIT)
    if resp.status_code != 201 or not isinstance(data, dict):
        raise EpsRefusal(UNAVAILABLE, f'HTTP {resp.status_code}')
    import uuid as _uuid
    try:
        guid = str(_uuid.UUID(str(data.get('patient_guid') or '')))
    except ValueError:
        raise EpsRefusal(UNAVAILABLE, '201 without a usable patient_guid')
    header = (getattr(resp, 'headers', None) or {}).get('X-PDHC-Patient-Guid')
    if header and str(header).strip().lower() != guid:
        raise EpsRefusal(UNAVAILABLE, '201 body and header name different guids')
    return CREATED, guid


# --- #1050: the invited patient's personnummer ------------------------------
#
#     POST {EPS_BASE_URL}/api/v1/patients/<guid>/identifier-match
#     Authorization: ApiKey <EPS_API_KEY>     (scope patients:identifier-match)
#     {"personnummer": "<12 digits>"}
#
# eps answers 200 {"match": true|false}, 404 {"error": "Patient not found"},
# 422 {"error": "invalid_personnummer"}. The stored number never leaves eps;
# SSO learns yes or no. Anything that is not one of those three, with eps's
# own body, is UNAVAILABLE (the 404 rule above: another vhost can answer).
# NO development opt-out: the check is the whole point of the invite page.

INVALID_PERSONNUMMER = 'invalid_personnummer'


def identifier_match(config, ips_patient_guid, personnummer):
    """True / False. Raise EpsRefusal(NOT_CONFIGURED | UNAVAILABLE |
    PATIENT_NOT_FOUND | INVALID_PERSONNUMMER)."""
    base = (config.get('EPS_BASE_URL') or '').strip().rstrip('/')
    key = (config.get('EPS_API_KEY') or '').strip()
    if not base or not key:
        raise EpsRefusal(NOT_CONFIGURED, 'EPS_BASE_URL / EPS_API_KEY')
    try:
        resp = _http_post(f'{base}/api/v1/patients/{ips_patient_guid}/identifier-match',
                          {'Authorization': f'ApiKey {key}',
                           'Accept': 'application/json'},
                          {'personnummer': personnummer}, TIMEOUT_SECONDS)
    except requests.RequestException as e:
        raise EpsRefusal(UNAVAILABLE, type(e).__name__)
    try:
        data = resp.json()
    except ValueError:
        data = None
    error = str(data.get('error', '')).lower() if isinstance(data, dict) else ''
    if resp.status_code == 404 and error == _EPS_NOT_FOUND:
        raise EpsRefusal(PATIENT_NOT_FOUND)
    if resp.status_code == 422 and error == INVALID_PERSONNUMMER:
        raise EpsRefusal(INVALID_PERSONNUMMER)
    if resp.status_code != 200 or not isinstance(data, dict) \
            or not isinstance(data.get('match'), bool):
        raise EpsRefusal(UNAVAILABLE, f'HTTP {resp.status_code}')
    return data['match']
