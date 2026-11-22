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
    if care_unit_guid:
        wanted = str(care_unit_guid).strip().lower()
        for c in clinics:
            if not isinstance(c, dict) or c.get('is_active') is False:
                continue
            unit = c.get('care_unit_guid') or c.get('organisation_guid')
            if unit and str(unit).strip().lower() == wanted:
                return 'checked'
        raise EpsRefusal(PATIENT_NOT_AT_UNIT)
    return 'checked'
