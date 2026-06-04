# Integration Guide

How to connect a downstream service to the sso.pdhc SSO.

## Overview

Downstream services authenticate users through the SSO service. The flow is:

1. User arrives at your service without a valid session
2. Your service redirects to SSO for login (SSO handshake)
3. SSO authenticates and redirects back with a JWT
4. Your service validates the JWT via `/api/auth/me/service`
5. Your service uses the **access blob** to make authorization decisions

## Step 1: Register Your Service

Before integrating, register your service with the SSO administrator:

1. Request service credentials (`X-SSO-Client-Id` / `X-SSO-Client-Secret`)
2. Provide your callback URL(s) to be added to `ALLOWED_CALLBACK_URLS`
3. Provide your origin(s) to be added to `ALLOWED_ORIGINS`
4. Your service will be added to `oath_overview.csv`

The SSO admin configures these in the `.env` file:

```bash
SSO_CLIENT_ID_YOURSERVICE=your-client-id
SSO_CLIENT_SECRET_YOURSERVICE=your-client-secret
ALLOWED_CALLBACK_URLS=...,https://yourservice.pdhc.se/callback
ALLOWED_ORIGINS=...,https://yourservice.pdhc.se
```

## Step 2: Implement the SSO Handshake

### H1 — Redirect to SSO

When a user hits a protected route without a valid session, redirect them:

```
https://sso.pdhc.se/login?next=https://yourservice.pdhc.se/callback&state=RANDOM_STRING
```

- `next` — your callback URL (must be in allowlist)
- `state` — random CSRF token you generate and store in session

### H2 — User Authenticates

The SSO login page handles authentication. If the user already has a valid SSO session, they are auto-redirected without seeing the login form.

### H3 — SSO Redirects Back

On success, SSO redirects to:

```
https://yourservice.pdhc.se/callback?token=JWT_TOKEN&state=RANDOM_STRING
```

On failure:

```
https://yourservice.pdhc.se/callback?error=authentication_failed&error_description=...&state=RANDOM_STRING
```

### H4 — Validate Token

At your callback endpoint:

1. Verify `state` matches what you stored
2. Extract the `token` parameter
3. Call `/api/auth/me/service` to validate and get the access blob

```python
import requests

def sso_callback(token, state):
    # Verify state matches session
    if state != session.get('sso_state'):
        abort(403, "CSRF state mismatch")

    # Validate token with SSO
    resp = requests.get(
        "https://sso.pdhc.se/api/auth/me/service",
        headers={
            "Authorization": f"Bearer {token}",
            "X-SSO-Client-Id": "your-client-id",
            "X-SSO-Client-Secret": "your-client-secret",
        },
    )

    if resp.status_code != 200:
        abort(401, "Token validation failed")

    access_blob = resp.json()
    # Store in session and proceed
    session['user'] = access_blob
```

## Step 3: Use the Access Blob

The access blob contains everything needed for authorization decisions.

### Patient Flow

```python
def authorize_patient_action(blob, resource_owner_guid, requires_registry=False):
    """Authorize a patient action."""
    if blob['user_type'] != 'patient':
        return False, "Not a patient"

    # Ownership check
    if blob['patient_guid'] != resource_owner_guid:
        return False, "Not the resource owner"

    # Registry check
    if requires_registry and not blob.get('in_registry', False):
        return False, "Not enrolled in registry"

    return True, "Authorized"
```

### Professional Flow

```python
def authorize_professional_action(blob, required_phase=None, require_admin=False, group_guid=None):
    """Authorize a professional action."""
    if blob['user_type'] != 'professional':
        return False, "Not a professional"

    # #43: forced password reset — short-circuit before any other check.
    # Callers should render/return a redirect to ${SSO_BASE_URL}/change-password
    # when this hits.
    if blob.get('must_change_password'):
        return False, "Password change required"

    # SU admins bypass all other checks.
    if blob.get('is_su_admin'):
        return True, "SU admin"

    # Phase check — after #57 `effective_phases` is sourced only from
    # direct UserPhase grants (#46). Groups are orthogonal category metadata
    # and do NOT contribute. Your service is free to compose its own policy
    # from `effective_phases` + `groups` + `organization_ids` independently.
    if required_phase and required_phase not in blob.get('effective_phases', []):
        return False, f"No access to phase: {required_phase}"

    # Group admin check
    if require_admin and group_guid:
        for group in blob.get('groups', []):
            if group['group_guid'] == group_guid and group['is_admin']:
                return True, "Group admin"
        return False, "Not group admin"

    return True, "Authorized"
```

### `map_action_to_phase()` Reference

Map your service's actions to SSO group types (phases):

```python
ACTION_PHASE_MAP = {
    "view_treatment_plan": "planning",
    "create_treatment_plan": "planning",
    "submit_data_request": "request",
    "view_provider_network": "provider",
    "run_analysis": "analysis",
    "view_analysis_results": "analysis",
}

def map_action_to_phase(action):
    """Map a service action to its required SSO phase."""
    return ACTION_PHASE_MAP.get(action)
```

## Operator Session Correlation (`session_id` + `X-Operator-Session-Id`)

**Source: ticket #191 (SSO Phase 3 — session_id claim).**

Every JWT issued at login carries a stable per-session UUID in the
`sid` claim. The access blob projects this as `session_id`:

```json
{
  "user_guid": "…",
  "is_su_admin": false,
  "session_id": "9e8c7d61-…-…-…",
  …
}
```

Properties:

- Same value on every `/api/auth/me` and `/api/auth/me/service` call
  validated against the same token.
- Fresh value on each new login (new token).
- `null` only for legacy tokens issued before #191 — services should
  treat that as "no correlation available", not an error.

**Convention for consumers.** When a service authenticated by the
SSO blob makes an *onward* HTTPS call to another PDHC service that
records read audits (e.g. `cdr_6`, `gateway`, `ips`), forward the
session id as a header:

```http
GET /api/v1/Observation?patient=…
Authorization: ApiKey …                # or Bearer if applicable
X-Operator-Session-Id: 9e8c7d61-…-…    # <- blob['session_id']
```

The downstream service writes that value into its audit log row. A
PDL Ch 4 §3 *kontroller* query can then ask "what did this operator
session touch?" across services without joining on user_guid +
narrow time window.

**Do not:**

- Cache the session id beyond the blob's normal lifetime (it changes
  on relogin).
- Forward the JWT itself as `X-Operator-Session-Id`. It's a separate,
  smaller, non-secret identifier — safe to log; the JWT is not.
- Treat absence of the header as an integrity violation. Legacy
  callers may omit it; record `session_id=NULL` and proceed.

## Organisation as Single Source of Truth

The SSO service maintains the canonical list of organisations. All services should use:

```
GET https://sso.pdhc.se/api/public/organisations
```

This is a public, rate-limited endpoint. Use it to populate dropdowns, validate organisation references, and sync your local organisation data.

Do **not** maintain a separate organisation registry. Always defer to the SSO.

## Service Registry (`oath_overview.csv`)

The `oath_overview.csv` file tracks all services under the SSO umbrella:

| Field | Description |
|-------|-------------|
| `service_name` | Human-readable service name |
| `service_url` | Base URL of the service |
| `api_health_url` | Health check endpoint |
| `capability_statement_url` | FHIR CapabilityStatement URL |
| `endpoints_url` | Endpoint documentation URL |
| `privilege_level` | `public`, `authenticated`, `admin` |
| `notes` | Free-text notes |

SU admins manage this via `GET/PUT /api/admin/oath-overview`.

## Error Handling

Handle these SSO error scenarios in your service:

| Scenario | SSO Response | Your Action |
|----------|-------------|-------------|
| Token expired | `401` from `/me/service` | Redirect to SSO login |
| Token revoked (`jti`) | `401` from `/me/service` | Redirect to SSO login |
| Session flushed (#44) | `401` from `/me/service` (token `iat` < `token_revocation_epoch`) | Clear local session, redirect to SSO login — same handling as expired/revoked |
| Must change password (#43) | `200` with `must_change_password: true` | Block the protected action; HTML routes redirect to `${SSO_BASE_URL}/change-password`, API/FHIR routes return 403 with the URL in the response body |
| Invalid service creds | `403` from `/me/service` | Log error, return 500 |
| SSO unreachable | Connection error | Return 503, retry with backoff |
| User lacks phase | `effective_phases` missing phase | Return 403 to user |

### Troubleshooting

- **User is stuck in a redirect loop to `/change-password`.** Confirm the service is not caching the blob across requests — caching makes `must_change_password` stick permanently. Call `/me/service` on every protected request (Rule 11).
- **After an admin session flush, user still reaches protected pages.** Same root cause — blob caching. `/me/service` must be re-called per request so a fresh 401 can fire.
- **SU granted a direct phase but the user still sees 403.** Confirm your service reads `blob['effective_phases']`, not `blob['groups']`. After #57 phase access lives only in `effective_phases`; group membership by itself never grants a phase.
- **User was approved into a planning-typed group but has no `planning` access.** Expected under #57. Groups are organisational/category metadata — phase access is granted separately by SU via `POST /api/admin/users/<guid>/phases`.

## Adding a New Service: Checklist

1. Request service credentials from SU admin
2. Get callback URL added to SSO allowlist
3. Implement SSO handshake (H1–H4)
4. Implement token validation via `/api/auth/me/service`
5. Map your actions to phases using `map_action_to_phase()`
6. Use the access blob for all authorization decisions
7. Use `/api/public/organisations` for organisation data
8. Register in `oath_overview.csv` via SU admin
9. Test: login flow, token validation, authorization, token expiry handling
