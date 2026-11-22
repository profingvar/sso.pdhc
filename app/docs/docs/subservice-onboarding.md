# Subservice Onboarding & Acceptance Specification

This document defines the requirements, procedures, and acceptance criteria a downstream service must satisfy to be registered and accepted under the sso.pdhc SSO umbrella.

---

## 1. Overview

Every service in the PDHC ecosystem authenticates users through the central SSO at `sso.pdhc.se`. A subservice does **not** manage its own user accounts, passwords, or sessions. Instead, it delegates authentication to the SSO and receives an **access blob** describing the user's identity, roles, group memberships, and effective phases.

This document is the single reference a subservice team follows from initial request through to production go-live.

---

## 2. Prerequisites

Before requesting SSO integration, the subservice team must have:

| # | Requirement | Evidence |
|---|-------------|----------|
| 2.1 | A running application with at least one protected route | URL or demo |
| 2.2 | HTTPS enabled (self-signed OK for staging; valid cert for production) | Certificate |
| 2.3 | A dedicated callback endpoint (e.g. `/callback` or `/auth/sso/callback`) | Endpoint URL |
| 2.4 | A health check endpoint returning JSON `{"status": "ok"}` | Endpoint URL |
| 2.5 | A FHIR R5 CapabilityStatement at `/fhir/metadata` (if the service exposes FHIR resources) | Endpoint URL |
| 2.6 | Secure credential storage — no secrets in source code, environment-based config | Confirmation |
| 2.7 | Designated technical contact and service owner | Name + email |

---

## 3. Registration Procedure

### 3.1 Request

The subservice owner submits the following to the SSO SU administrator:

1. **Service name** — human-readable identifier (e.g. `formservice`, `analytics-dashboard`)
2. **Service base URL** — production URL (e.g. `https://forms.pdhc.se`)
3. **Callback URL(s)** — exact URLs the SSO will redirect to after login (e.g. `https://forms.pdhc.se/callback`)
4. **Origin(s)** — for CORS allowlisting (e.g. `https://forms.pdhc.se`)
5. **Health check URL** — e.g. `https://forms.pdhc.se/api/health`
6. **CapabilityStatement URL** — e.g. `https://forms.pdhc.se/fhir/metadata` (if applicable)
7. **Privilege level** — `public`, `authenticated`, or `admin`
8. **Required phases** — which group types (planning, request, provider, analysis) the service needs access to
9. **Technical contact** — name and email

### 3.2 Approval

The SU administrator reviews the request and, upon approval:

1. Generates a unique credential pair: `SSO_CLIENT_ID_<SERVICE>` and `SSO_CLIENT_SECRET_<SERVICE>`
2. Adds the callback URL to `ALLOWED_CALLBACK_URLS` in `.env`
3. Adds the origin to `ALLOWED_ORIGINS` in `.env`
4. Registers the service in `oath_overview.csv` via `PUT /api/admin/oath-overview`
5. Restarts the SSO service
6. Sends the credentials to the subservice owner via a secure channel (never email)

### 3.3 Credential Delivery

Credentials are delivered securely and must be:

- Stored in the subservice's `.env` or secret manager — never in source code
- Rotated every 90 days (see Section 9)
- Revoked immediately if compromised

---

## 4. Technical Integration Requirements

### 4.1 SSO Handshake (mandatory)

The subservice must implement the four-step SSO handshake:

**H1 — Redirect to SSO.** When a user hits a protected route without a valid session:

```
https://sso.pdhc.se/login?next=https://yourservice.pdhc.se/callback&state=RANDOM_STATE
```

- `next` must be an exact URL from the registered callback list
- `state` must be a cryptographically random string stored in the user's server-side session

**H2 — User authenticates.** The SSO handles login. If the user already has a valid SSO session, they are auto-redirected without seeing the login form.

**H3 — SSO redirects back.** On success:

```
https://yourservice.pdhc.se/callback?token=JWT_TOKEN&state=RANDOM_STATE
```

On failure:

```
https://yourservice.pdhc.se/callback?error=authentication_failed&error_description=...&state=RANDOM_STATE
```

**H4 — Validate token.** At the callback endpoint:

1. Verify `state` matches the value stored in session (CSRF protection)
2. Extract the `token` query parameter
3. Call `GET /api/auth/me/service` with the token and service credentials
4. Use the returned access blob for authorization

```python
import requests

def sso_callback(token, state):
    if state != session.get('sso_state'):
        abort(403, "CSRF state mismatch")

    resp = requests.get(
        "https://sso.pdhc.se/api/auth/me/service",
        headers={
            "Authorization": f"Bearer {token}",
            "X-SSO-Client-Id": os.environ["SSO_CLIENT_ID"],
            "X-SSO-Client-Secret": os.environ["SSO_CLIENT_SECRET"],
        },
        timeout=10,
    )

    if resp.status_code != 200:
        abort(401, "Token validation failed")

    access_blob = resp.json()
    session['user'] = access_blob
    return redirect(url_for('dashboard'))
```

### 4.2 Access Blob Usage (mandatory)

All authorization decisions must be based on the access blob. The subservice must **never**:

- Maintain its own user/password database
- **Cache the access blob across requests** — re-validate every protected request by calling `GET /api/auth/me/service`. Caching defeats #43 (forced password reset), #44 (bulk session flush), and #46 (direct phase changes), all of which are expected to take effect *immediately* for downstream services.
- Modify or extend the access blob fields

Key fields to use:

| Field | Type | Purpose |
|-------|------|---------|
| `user_guid` | UUID | Unique user identifier — use as foreign key for all user references |
| `user_type` | `patient` or `professional` | Top-level role |
| `is_su_admin` | boolean | Superuser bypass |
| `must_change_password` | boolean | **#43.** When `true`, the subservice MUST block all protected actions and redirect the user to `${SSO_BASE_URL}/change-password` — see 4.8 below |
| `effective_phases` | list of strings | Authorized phase names. Sourced **only** from direct `UserPhase` grants (#46 + #57) — group membership does NOT contribute. The field name and check (`phase in effective_phases`) are unchanged; only the source narrowed |
| `groups` | list of objects | Group memberships with `group_guid`, `group_name`, `category`, `status`, `is_admin`. After #57 these are orthogonal organisational/category metadata — use for org-admin checks or category-based UI grouping, **never** as a phase-access signal. `category` is a free-form string after #60 (was the `group_type` enum before) |
| `organization_ids` | list of UUIDs | Organisations the user belongs to |
| `patient_guid` | UUID | Patient record GUID (patient users only) |
| `professional_guid` | UUID | Professional record GUID (professional users only) |
| `in_registry` | boolean | Whether the patient is enrolled in a registry (patient users only) |
| `patient_link` | object or `null` | **#1040**, patient users only. `{ips_patient_guid, linked_at, linked_by}` once care has linked the account to an eps patient; `null` before. The patient to serve is `ips_patient_guid`, not `patient_guid` |
| `second_factor` | object | **#1040**, patient users only. `{enrolled, verified, method, verified_at}`. `verified` is about THIS token. A service serving patient data MUST refuse when it is not `true` — a delegated control, like `must_change_password`. See the Integration Guide |

### 4.3 Phase-Based Authorization (mandatory if service is phase-gated)

Map each service action to the SSO phase it requires:

```python
ACTION_PHASE_MAP = {
    "view_treatment_plan": "planning",
    "submit_data_request": "request",
    "view_provider_network": "provider",
    "run_analysis": "analysis",
}

def check_phase_access(blob, action):
    required_phase = ACTION_PHASE_MAP.get(action)
    if required_phase is None:
        return True  # action not phase-gated
    return required_phase in blob.get('effective_phases', [])
```

### 4.4 GUID-Based References (mandatory)

Per Rule 18, all user and entity references must use GUIDs, not integer IDs:

- Store `user_guid` from the access blob as the foreign key in your data
- Never expose or rely on internal integer IDs in API responses or URLs
- Use `group_guid`, `organisation_guid`, etc. for all cross-service references

### 4.5 Organisation Data (mandatory)

The SSO is the single source of truth for organisations. Subservices must:

- Fetch organisation data from `GET /api/public/organisations`
- Never maintain a separate organisation registry
- Sync periodically or on-demand (endpoint is public, rate-limited)

### 4.6 Health Endpoint (mandatory)

The subservice must expose a health endpoint that returns:

```json
{"status": "ok"}
```

This URL is registered in `oath_overview.csv` and may be polled by the SSO admin dashboard.

### 4.7 Forced Password Reset Handling (#43, mandatory)

Downstream services must honour the `must_change_password` flag in the access blob:

```python
if blob.get("must_change_password"):
    if request.is_json or request.path.startswith("/api/"):
        # API/FHIR route — return 403 (or a FHIR OperationOutcome) with the change-password URL
        return jsonify({
            "error": "password_change_required",
            "change_password_url": f"{SSO_BASE_URL}/change-password",
        }), 403
    # HTML route — redirect
    return redirect(f"{SSO_BASE_URL}/change-password")
```

Once the user completes the password change, the flag clears on the next `/me/service` call and the user is unblocked automatically — no subservice-side cleanup needed.

### 4.8 Session-Flush 401 Handling (#44, mandatory)

When an SU flushes a user's sessions, subsequent `/me/service` calls return **401** for JWTs older than the new `token_revocation_epoch`. Treat this 401 as "session terminated":

```python
resp = requests.get(f"{SSO_BASE_URL}/api/auth/me/service", ...)
if resp.status_code == 401:
    session.clear()                          # wipe local session cache
    return redirect(url_for("auth.login"))   # bounce back through SSO
```

Do **not** distinguish expired-token, revoked-token, and epoch-flush cases — they all warrant the same "re-login" handling from the subservice's point of view.

### 4.9 FHIR R5 Compliance (mandatory if exposing FHIR resources)

If the subservice manages FHIR resources:

- Expose a CapabilityStatement at `GET /fhir/metadata`
- Use FHIR R5 resource shapes (validated with `fhir.resources` v8+)
- Return `Content-Type: application/fhir+json` for FHIR endpoints
- Reference the SSO's Patient, Practitioner, Organization, Group resources by GUID

---

## 5. Security Requirements

| # | Requirement | Detail |
|---|-------------|--------|
| 5.1 | HTTPS in production | All traffic encrypted; valid SSL certificate |
| 5.2 | Credential storage | SSO client ID/secret in `.env` or secret manager, never in code |
| 5.3 | State parameter | Random, server-side, validated on callback (CSRF protection) |
| 5.4 | Token handling | JWT stored server-side in session; never in localStorage or URL fragments |
| 5.5 | Session expiry | Honour token expiry; redirect to SSO login on 401 |
| 5.6 | No token forwarding | Never pass JWT to client-side JavaScript or third parties |
| 5.7 | Error handling | Log SSO errors; never expose internal details to users |
| 5.8 | Audit logging | Log authentication events (login, logout, access denied) with timestamps |
| 5.9 | Input validation | Validate all user input at system boundary (OWASP Top 10) |
| 5.10 | Security headers | Set `X-Frame-Options`, `X-Content-Type-Options`, `Referrer-Policy` |
| 5.11 | Reverse proxy safety | Must not interfere with other services on the same server (Rule 22) |

---

## 6. Error Handling Requirements

The subservice must handle these SSO failure scenarios:

| Scenario | SSO Response | Required Action |
|----------|-------------|-----------------|
| Token expired | `401` from `/me/service` | Clear session, redirect to SSO login |
| Token revoked (`jti` list) | `401` from `/me/service` | Clear session, redirect to SSO login |
| User session flushed (#44) | `401` from `/me/service` (token `iat` < `token_revocation_epoch`) | Clear session, redirect to SSO login — same handling as expired/revoked |
| User must change password (#43) | `200` from `/me/service` with `must_change_password: true` | Block protected action; redirect (HTML) or 403 with change-password URL (API/FHIR) to `${SSO_BASE_URL}/change-password` |
| Invalid service credentials | `403` from `/me/service` | Log error, return HTTP 500 to user, alert ops |
| SSO unreachable | Connection timeout/error | Return HTTP 503, retry with exponential backoff (max 3 retries) |
| User lacks required phase | `effective_phases` missing needed phase | Return HTTP 403 with user-friendly message |
| Invalid state parameter | State mismatch at callback | Return HTTP 403, log possible CSRF attempt |
| SSO returns error on redirect | `error` query parameter at callback | Display error to user, log details |

---

## 7. Acceptance Testing

Before a subservice is approved for production, it must pass the following acceptance tests. The SSO administrator and the subservice team conduct these jointly.

### 7.1 Functional Tests

| # | Test | Pass Criteria |
|---|------|---------------|
| F1 | Health check | `GET /api/health` returns `200` with `{"status": "ok"}` |
| F2 | Unauthenticated redirect | Accessing a protected route redirects to `sso.pdhc.se/login` with correct `next` and `state` |
| F3 | Login flow | User can log in via SSO and is redirected back with a valid session |
| F4 | Access blob consumption | Service correctly reads `user_type`, `effective_phases`, `groups`, `organization_ids` |
| F5 | Patient authorization | Patient can only access own data; `in_registry` check works if applicable |
| F6 | Professional phase check | Professional without required phase gets 403 |
| F7 | SU admin bypass | SU admin can access all resources |
| F8 | Group admin check | Group admin actions only available to users with `is_admin: true` for the group |
| F9 | Organisation data | Service fetches organisations from SSO, not local store |
| F10 | Logout | Logging out clears session; user must re-authenticate to access protected routes |
| F11 | Token expiry | Expired token triggers redirect to SSO login (not an error page) |
| F12 | FHIR metadata | `GET /fhir/metadata` returns valid CapabilityStatement (if applicable) |
| F13 | Forced password reset (#43) | After SU resets the user's password, the service blocks protected actions and routes the user to `${SSO_BASE_URL}/change-password` until the user picks a new password. No subservice-side flag; derived from `/me/service` blob |
| F14 | Session flush (#44) | After SU flushes the user's sessions, the next protected request returns 401 from `/me/service`; the service wipes local session and redirects to SSO login |
| F15 | No blob caching | Service calls `/me/service` on every protected request; changing `must_change_password` or `effective_phases` at the SSO takes effect on the very next request without a restart |

### 7.2 Security Tests

| # | Test | Pass Criteria |
|---|------|---------------|
| S1 | HTTPS enforcement | HTTP requests redirect to HTTPS (production) |
| S2 | State validation | Tampered `state` parameter is rejected with 403 |
| S3 | Invalid token | Forged or tampered JWT is rejected |
| S4 | Credential exposure | SSO client secret not visible in HTML, JS, logs, or error messages |
| S5 | Security headers | Response includes `X-Frame-Options`, `X-Content-Type-Options` |
| S6 | Session fixation | Session ID regenerated after SSO callback |
| S7 | CORS | Only registered origins accepted |

### 7.3 Resilience Tests

| # | Test | Pass Criteria |
|---|------|---------------|
| R1 | SSO unreachable | Service returns 503 (not 500) and retries with backoff |
| R2 | Slow SSO response | Service has a timeout (max 10s) and does not hang indefinitely |
| R3 | Concurrent users | Service handles at least 10 concurrent SSO-authenticated sessions |

---

## 8. Onboarding Checklist

The subservice team and SSO administrator sign off on each item before production registration.

### Phase A — Preparation

- [ ] A.1 Service owner and technical contact identified
- [ ] A.2 Service name, base URL, callback URL, origin documented
- [ ] A.3 Health endpoint operational
- [ ] A.4 HTTPS configured (staging or production certificate)
- [ ] A.5 FHIR CapabilityStatement available (if applicable)

### Phase B — Registration

- [ ] B.1 SSO credentials received via secure channel
- [ ] B.2 Credentials stored in `.env` / secret manager (not in code)
- [ ] B.3 Callback URL added to SSO `ALLOWED_CALLBACK_URLS`
- [ ] B.4 Origin added to SSO `ALLOWED_ORIGINS`
- [ ] B.5 Service added to `oath_overview.csv`

### Phase C — Integration

- [ ] C.1 SSO handshake implemented (H1–H4)
- [ ] C.2 Access blob consumed for all authorization decisions
- [ ] C.3 Phase-based authorization implemented (if applicable)
- [ ] C.4 GUID-based references used throughout (no integer IDs externally)
- [ ] C.5 Organisation data sourced from SSO `/api/public/organisations`
- [ ] C.6 Error handling implemented for all scenarios in Section 6
- [ ] C.7 `must_change_password` honoured per request (#43)
- [ ] C.8 `401` from `/me/service` wipes local session and redirects to SSO login (#44)
- [ ] C.9 Access blob is NOT cached — `/me/service` is called on every protected request

### Phase D — Acceptance Testing

- [ ] D.1 All functional tests passed (F1–F12)
- [ ] D.2 All security tests passed (S1–S7)
- [ ] D.3 All resilience tests passed (R1–R3)
- [ ] D.4 Test results documented and stored in `results/`

### Phase E — Go-Live

- [ ] E.1 Production SSL certificate active
- [ ] E.2 Reverse proxy configured without interference to other services
- [ ] E.3 Audit logging enabled
- [ ] E.4 Credential rotation schedule agreed (90-day cycle)
- [ ] E.5 Sign-off by SSO administrator
- [ ] E.6 Sign-off by subservice owner

---

## 9. Credential Rotation

Service credentials must be rotated every 90 days.

### Rotation Procedure

1. SSO administrator generates new `SSO_CLIENT_ID` / `SSO_CLIENT_SECRET` pair
2. Both old and new credentials are temporarily active (grace period: 7 days)
3. Subservice team updates their `.env` with new credentials and restarts
4. Subservice team confirms new credentials work via `/api/auth/me/service`
5. SSO administrator removes old credentials after confirmation
6. `KEY_CREATED_AT` updated in SSO `.env`

### Emergency Revocation

If credentials are compromised:

1. SSO administrator immediately removes the credentials from `.env`
2. SSO service restarted
3. Subservice is notified and receives new credentials via secure channel
4. Subservice updates and restarts
5. Incident documented

---

## 10. Deregistration

To remove a subservice from the SSO:

1. SSO administrator removes service credentials from `.env`
2. Callback URL removed from `ALLOWED_CALLBACK_URLS`
3. Origin removed from `ALLOWED_ORIGINS`
4. Service removed from `oath_overview.csv`
5. SSO service restarted
6. Subservice team notified

---

## 11. Contact

| Role | Contact |
|------|---------|
| SSO Administrator | SU admin account holder |
| Integration support | Technical contact listed in `oath_overview.csv` |
| Incident reporting | SSO administrator (immediate) |
