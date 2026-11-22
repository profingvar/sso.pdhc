# API Reference

All endpoints are grouped by blueprint. Base URL: `https://sso.pdhc.se` (production) or `http://localhost:9000` (development).

## Authentication Headers

| Header | Usage |
|--------|-------|
| `Authorization: Bearer <JWT>` | Required for all authenticated endpoints |
| `X-SSO-Client-Id` | Service-to-service auth (with `/me/service`) |
| `X-SSO-Client-Secret` | Service-to-service auth (with `/me/service`) |
| `Content-Type: application/json` | Required for all POST/PUT with JSON body |

---

## Health

### `GET /api/health`

**Auth:** None

Returns service status and database connectivity.

**Response** `200 OK`:
```json
{
  "status": "healthy",
  "database": "connected",
  "version": "1.0.0"
}
```

---

## FHIR

### `GET /fhir/metadata`

**Auth:** None
**Content-Type:** `application/fhir+json`

Returns the FHIR R5 CapabilityStatement describing all supported resources and interactions.

**Response** `200 OK`:
```json
{
  "resourceType": "CapabilityStatement",
  "fhirVersion": "5.0.0",
  "status": "active",
  "kind": "instance",
  "rest": [
    {
      "mode": "server",
      "resource": [
        {"type": "Patient", "interaction": [{"code": "read"}, {"code": "create"}]},
        {"type": "Practitioner", "interaction": [{"code": "read"}, {"code": "create"}]},
        {"type": "Organization", "interaction": [{"code": "read"}, {"code": "create"}, {"code": "search-type"}]},
        {"type": "Group", "interaction": [{"code": "read"}, {"code": "create"}, {"code": "search-type"}]}
      ]
    }
  ]
}
```

---

## Auth (`/api/auth`)

### `POST /api/auth/login`

**Auth:** None
**Rate Limit:** 20 req/min per IP

Authenticate with email and password. Returns JWT token. Supports SSO handshake via `next` and `state` parameters.

**Request:**
```json
{
  "email": "user@example.com",
  "password": "secretpass",
  "next": "https://app.pdhc.se/callback",
  "state": "random-csrf-state"
}
```

**Response** `200 OK` (direct login):
```json
{
  "token": "eyJhbGciOiJIUzI1NiIs...",
  "user_guid": "a1b2c3d4-e5f6-..."
}
```

**Response** `302 Redirect` (SSO handshake):
Redirects to `{next}?token={JWT}&state={state}`

**Errors:**

| Code | Error | Description |
|------|-------|-------------|
| 400 | `invalid_request` | Missing email or password |
| 400 | `invalid_redirect` | `next` URL not in allowlist |
| 401 | `authentication_failed` | Wrong email or password — **and** an unknown email, **and** an account locked after five wrong passwords (#1040), even when the password is now right. The three are the same answer on purpose |

---

### `GET /api/auth/me`

**Auth:** Bearer token

Returns the access blob for the authenticated user.

**Response** `200 OK` (professional):
```json
{
  "user_guid": "...",
  "email": "doctor@hospital.se",
  "user_type": "professional",
  "is_su_admin": false,
  "must_change_password": false,
  "professional_guid": "...",
  "professional_role": "doctor",
  "fhir_resource_type": "Practitioner",
  "organization_ids": ["org-guid-1"],
  "groups": [
    {
      "group_guid": "...",
      "group_name": "Oncology Planning",
      "category": "planning",
      "status": "approved",
      "is_admin": false
    }
  ],
  "effective_phases": ["planning"]
}
```

**Response** `200 OK` (patient):
```json
{
  "user_guid": "...",
  "email": "patient@example.com",
  "user_type": "patient",
  "is_su_admin": false,
  "must_change_password": false,
  "patient_guid": "...",
  "organisation_guid": "...",
  "in_registry": true,
  "registries": ["INCA"],
  "fhir_resource_type": "Patient"
}
```

**Blob fields (both user types):**

| Field | Type | Added | Meaning |
|-------|------|-------|---------|
| `must_change_password` | bool | #43 | SU triggered a password reset. Callers MUST block normal actions and route the user to the `/change-password` page until this flips back to `false`. |
| `effective_phases` | list[str] | #46/#57 | Direct `UserPhase` grants **only**. Groups are orthogonal organisational/category metadata and do NOT contribute to phase access (#57). `'planning' in effective_phases` remains the canonical phase check — same field, narrower source. |

**Errors:**

| Code | Error | Description |
|------|-------|-------------|
| 401 | — | Missing/invalid/expired/revoked token, **or** token was issued before the user's `token_revocation_epoch` (#44 bulk session flush). Callers MUST treat this as "re-login required" and clear any cached blob. |

---

### `GET /api/auth/me/service`

**Auth:** Bearer token + `X-SSO-Client-Id` + `X-SSO-Client-Secret`

Same response shape as `/me` (including `must_change_password`, `effective_phases`, etc. — see the table above). Used by downstream services to validate the user's bearer on every protected request.

!!! warning "Per-request validation, no caching"
    Downstream services MUST call this endpoint on every protected request and trust only the returned blob for authorisation decisions. Caching the blob defeats `must_change_password` (#43) and the per-user session flush (#44). See `subservice-onboarding.md` F5/F6.

**Errors:**

| Code | Error | Description |
|------|-------|-------------|
| 401 | — | Missing/invalid/expired/revoked token, **or** token was issued before the user's `token_revocation_epoch` (#44). Services must treat 401 here as "session terminated" — wipe their local session and bounce the user back through the SSO login flow. |
| 403 | `invalid_service_credentials` | Missing or wrong client ID/secret |

---

### `POST /api/auth/logout`

**Auth:** Bearer token

Revokes the current JWT by adding its `jti` to the revoked tokens table.

**Response** `200 OK`:
```json
{
  "message": "Logged out successfully"
}
```

---

### `POST /api/auth/change-password`

**Auth:** Bearer token

Change the authenticated user's password.

**Request:**
```json
{
  "current_password": "oldpass123",
  "new_password": "newpass456"
}
```

**Response** `200 OK`:
```json
{
  "message": "Password changed successfully"
}
```

On success the server also clears `force_change_on_next_login` on the user, so the next `/me`/`/me/service` call will return `must_change_password: false` and downstream services will unblock the user automatically (#43). The `token_revocation_epoch` is **not** bumped — the existing JWT remains valid.

**Errors:**

| Code | Error | Description |
|------|-------|-------------|
| 400 | `invalid_request` | Missing fields or new password < 8 chars |
| 401 | `authentication_failed` | Current password incorrect |

---

## Patient (`/api/patient`)

### `POST /api/patient/register`

**Auth:** None
**Rate Limit:** 10 req/min per IP
**FHIR:** Patient resource shape in response

Self-registration for patients. The same rules as the `/register-patient`
form, which calls the same service (#910).

`organisation_guid` must be a **care unit** (vårdenhet), or a vårdgivare that
has no units (it is its own unit). An external partner, a vårdgivare that has
units, and an unknown guid all answer the same `404 Organisation not found`.
The personnummer is checked for 12 digits only — deliberately no checksum
(synthetic numbers carry an invalid one, #405).

**Request:**
```json
{
  "email": "patient@example.com",
  "password": "securepass",
  "personnummer": "199001011234",
  "organisation_guid": "org-guid-here"
}
```

**Response** `201 Created`:
```json
{
  "resourceType": "Patient",
  "user_guid": "...",
  "patient_guid": "...",
  "email": "patient@example.com",
  "personnummer": "199001011234",
  "organisation_guid": "...",
  "in_registry": false,
  "registries": []
}
```

**Errors:**

| Code | Error | Description |
|------|-------|-------------|
| 400 | `validation_error` | Missing/invalid fields |
| 404 | `not_found` | Organisation not found |
| 409 | `conflict` | Email or personnummer already registered |

---

### `GET /api/patient/registry-status`

**Auth:** Bearer token (patient only)

Returns the patient's registry enrolment status. Only own data is accessible.

**Response** `200 OK`:
```json
{
  "resourceType": "Patient",
  "patient_guid": "...",
  "user_guid": "...",
  "personnummer": "199001011234",
  "organisation_guid": "...",
  "in_registry": true,
  "registries": ["INCA"]
}
```

---

## Patient app: link and second factor (#1040)

Bearer token on every route. Errors use the standard shape
`{"error": "<code>", "message": "…"}`.

### `POST /api/patient/link-code`

Patient only. A one-time code for an account that is not yet linked. A new code
replaces any earlier unused one. The code is in this response and nowhere else
(a keyed hash is stored).

**Response** `201`:
```json
{"link_code": "ABCD-EFGH", "expires_at": "2026-11-29T09:00:00+00:00",
 "message": "Give this code to your care unit."}
```

| Code | Error | When |
|------|-------|------|
| 403 | `forbidden` | Not a patient account |
| 409 | `account_already_linked` | The account already has a patient link |

### `POST /api/patient-links/invites`

An activated professional with an active affiliation to `care_unit_guid`, or an
SU admin. `care_unit_guid` may be omitted when the caller has exactly one unit.

**Request**:
```json
{"ips_patient_guid": "<eps patient guid>", "care_unit_guid": "<care unit guid>"}
```

**Response** `201` — `invite_url` is shown ONCE; it opens for 7 days, once:
```json
{"invite_guid": "…", "ips_patient_guid": "…", "care_unit_guid": "…",
 "expires_at": "…", "invite_url": "https://sso.pdhc.se/patient-invite/<token>"}
```

| Code | Error | When |
|------|-------|------|
| 400 | `invalid_request` | `ips_patient_guid` is not a GUID, or `care_unit_guid` missing and ambiguous |
| 403 | `forbidden` / `not_affiliated` / `must_change_password` | Not a professional; not affiliated to that unit (or not activated); password change pending |
| 404 | `not_found` | Not a care unit a patient can belong to |
| 409 | `patient_already_linked` | That patient already has an account |
| 404 | `patient_not_found` | eps does not know this patient guid |
| 409 | `patient_not_at_unit` | The patient is not listed at `care_unit_guid` in eps |
| 503 | `eps_not_configured` / `eps_unavailable` | eps is not configured on this server / did not give a usable answer. Nothing was created |

The eps check runs only for a caller who passed the affiliation gate; see
*Architecture → The eps check*. The same four answers apply to
`/patient-links/redeem` and to the SU relink below.

### `DELETE /api/patient-links/invites/<invite_guid>`

Revokes an unused invite. Same caller rule; an invite of a unit you are not
affiliated to answers `404`, like one that does not exist. `409 invite_used`.

### `POST /api/patient-links/redeem`

Care enters the patient's link code together with the eps patient guid. The
unit is the one the patient registered at.

**Request**: `{"link_code": "ABCD-EFGH", "ips_patient_guid": "<guid>"}`
(the code is case-insensitive; dashes and spaces are ignored)

**Response** `200`:
```json
{"user_guid": "…", "method": "link_code",
 "patient_link": {"ips_patient_guid": "…", "linked_at": "…", "linked_by": "…"}}
```

| Code | Error | When |
|------|-------|------|
| 403 | `forbidden` / `must_change_password` | Not a professional or SU; password change pending |
| 404 | `link_code_not_found` | Unknown, used or expired code — **and** a valid code of a patient at a unit the caller is not affiliated to. One answer for all, by design |
| 409 | `patient_already_linked` / `account_already_linked` | One account ↔ one patient |
| 404 / 409 / 503 | `patient_not_found` / `patient_not_at_unit` / `eps_not_configured` / `eps_unavailable` | The eps check, against the unit the patient registered at. The code is NOT used up by these |

### `PUT /api/admin/patient-links/<user_guid>` · `DELETE …`

SU admin only. `PUT {"ips_patient_guid": "…", "reason": "…", "care_unit_guid": "…"}`
relinks (or links an unlinked account); `DELETE {"reason": "…"}` unlinks.
`reason` is required and recorded in `patient_link_history` and the audit log.
The patient guid must exist in eps. `care_unit_guid` is optional: when given,
the patient must also be listed at that unit in eps, and the link records it.

### `GET /api/auth/second-factor`

Own state: the blob's `second_factor` object plus `enrolled_methods`
(`["passkey", "totp"]`).

### `POST /api/admin/users/<user_guid>/second-factor/reset`

SU admin only. Removes every passkey and the TOTP secret of the user and ends
all their sessions. Response `200 {"user_guid": "…", "removed": <n>}`.

!!! warning
    After a reset the account enrols a new factor with its password alone.
    Verify who is asking before you reset.

### Browser routes (session cookie, CSRF)

The passkey ceremony needs the browser, so enrolment and the login step live on
pages, not in the JSON API: `/second-factor` (manage), `/login/second-factor`
(step-up after the password), `/patient-invite/<token>` (accept an invite),
`/patient-link/code` (patient), `/patient-link/invite` and
`/patient-link/redeem` (care, from the dashboard). See *Architecture → Patient
login*.

---

## Groups (`/api/groups`)

### `GET /api/groups`

**Auth:** Bearer token (professional only)
**FHIR:** Group resource shape

List the authenticated professional's approved group memberships.

**Response** `200 OK`:
```json
[
  {
    "resourceType": "Group",
    "group_guid": "...",
    "name": "Oncology Planning",
    "category": "planning",
    "is_admin": false,
    "membership_status": "approved"
  }
]
```

---

### `POST /api/groups/request-membership`

**Auth:** Bearer token (professional only)

Request to join a group. Creates a pending membership.

**Request:**
```json
{
  "group_guid": "target-group-guid"
}
```

**Response** `201 Created`:
```json
{
  "membership_guid": "...",
  "group_guid": "...",
  "status": "pending"
}
```

**Errors:** `404` group not found, `409` membership already exists

---

### `POST /api/groups/request-admin`

**Auth:** Bearer token (professional only)

Request group admin (leader) role. Creates a leader request for SU review.

**Request:**
```json
{
  "group_guid": "target-group-guid"
}
```

**Response** `201 Created`:
```json
{
  "leader_request_guid": "...",
  "group_guid": "...",
  "status": "pending"
}
```

---

### `GET /api/groups/admin/pending`

**Auth:** Bearer token (group admin or SU)

List pending membership requests for groups the caller administers. SU admins see all groups.

**Response** `200 OK`:
```json
[
  {
    "membership_guid": "...",
    "user_guid": "...",
    "user_email": "applicant@example.com",
    "group_guid": "...",
    "group_name": "Oncology Planning",
    "status": "pending",
    "created_at": "2026-03-18T10:00:00"
  }
]
```

---

### `POST /api/groups/admin/decide`

**Auth:** Bearer token (group admin or SU)

Approve or reject a pending membership request.

**Request:**
```json
{
  "membership_guid": "...",
  "decision": "approved"
}
```

`decision` must be `"approved"` or `"rejected"`.

**Response** `200 OK`:
```json
{
  "membership_guid": "...",
  "status": "approved",
  "decided_by": "admin-user-guid"
}
```

---

### `POST /api/groups/admin/invite`

**Auth:** Bearer token (group admin or SU)

Create a time-limited invite token for a group.

**Request:**
```json
{
  "group_guid": "...",
  "hours_valid": 48
}
```

**Response** `201 Created`:
```json
{
  "invite_guid": "...",
  "token": "uuid-invite-token",
  "group_guid": "...",
  "expires_at": "2026-03-20T10:00:00+00:00"
}
```

---

### `POST /api/groups/join-by-invite`

**Auth:** Bearer token (professional only)

Redeem an invite token to create a pending membership.

**Request:**
```json
{
  "token": "uuid-invite-token"
}
```

**Response** `201 Created`:
```json
{
  "membership_guid": "...",
  "group_guid": "...",
  "status": "pending"
}
```

**Errors:** `404` invalid token, `409` already member, `410` expired

---

## Admin (`/api/admin`)

All admin endpoints require **SU admin** role.

### `GET /api/admin/users`

List all users with full detail (professional memberships, patient data, organisations).

**Response** `200 OK`: Array of user objects (see architecture doc for full schema).

---

### `POST /api/admin/promote-su`

Promote a professional to SU admin. Requires caller's password confirmation.

**Request:**
```json
{
  "user_guid": "target-user-guid",
  "password": "callers-password"
}
```

**Errors:** `401` wrong password, `404` user not found, `400` not a professional, `409` already SU

---

### `DELETE /api/admin/users/<user_guid>`

Delete a user. Cascades: nullifies `decided_by_guid` references, deletes memberships.

**Response** `200 OK`:
```json
{
  "message": "User deleted",
  "user_guid": "..."
}
```

---

### `DELETE /api/admin/groups/<group_guid>`

Delete a group. Cascades: deletes memberships and invites.

---

### `POST /api/admin/assign-group-admin`

Set a user as admin of a group (user must already be a member).

**Request:**
```json
{
  "user_guid": "...",
  "group_guid": "..."
}
```

---

### `POST /api/admin/users/<user_guid>/reset-password` *(#43)*

Force a password reset for `<user_guid>`. Sets the user's password to a temporary value and flips `force_change_on_next_login = True`, so the next `/me`/`/me/service` call returns `must_change_password: true` and downstream services must redirect the user to the `/change-password` page. The flag clears automatically the next time the user successfully completes `POST /api/auth/change-password`.

**Request:**
```json
{
  "temporary_password": "TempPass!23"
}
```

If `temporary_password` is omitted the server generates one and returns it in the response so the SU can communicate it to the user out-of-band.

**Response** `200 OK`:
```json
{
  "user_guid": "...",
  "message": "Password reset; user must change on next login",
  "temporary_password": "TempPass!23"
}
```

!!! note "Existing tokens remain valid"
    This endpoint does **not** bump `token_revocation_epoch`. The user's existing JWTs stay valid — they just hit a forced redirect to `/change-password` on every request until they set a new password. Use `flush-sessions` below if you want to also invalidate every active token.

**Errors:** `404` user not found, `400` temporary password < 8 chars

---

### `GET /api/admin/login-locks` · `POST /api/admin/users/<user_guid>/unlock-login` *(#1040)*

SU admin only. Five consecutive wrong passwords lock password login for an
account for 15 minutes (both the web form and `POST /api/auth/login`; state in
the `login_lockouts` table).

- `GET /api/admin/login-locks` → `200 [{"user_guid": "…", "email": "…", "locked_until": "…"}]`, accounts locked right now.
- `POST /api/admin/users/<user_guid>/unlock-login` → `200 {"user_guid": "…", "was_locked": true}`. Ends the lock and clears the failure counter. `404` unknown user. Audited (`login_unlock`).

### `POST /api/admin/users/<user_guid>/flush-sessions` *(#44)*

Invalidate every active JWT for `<user_guid>` in one call. Sets `user.token_revocation_epoch = now()`; `/me/service` will subsequently return **401** for any token whose `iat` is older than the stored epoch. Downstream services must treat that 401 as "session terminated" — wipe local session, bounce the user through the SSO login flow.

**Request:** *(empty body)*

**Response** `200 OK`:
```json
{
  "user_guid": "...",
  "token_revocation_epoch": "2026-04-15T08:30:00+00:00",
  "message": "All active sessions invalidated"
}
```

Cheaper than adding N `jti` entries to the revocation list when a user has many active devices, and correct when the set of active `jti`s isn't known (e.g. after a password compromise).

**Errors:** `404` user not found

---

### `GET /api/admin/users/<user_guid>/phases` *(#46, clarified by #57)*

List the direct `UserPhase` grants for `<user_guid>`. After #57 these grants are the **sole** source of `effective_phases` in the access blob — group memberships are orthogonal metadata and do not contribute to phase access.

**Response** `200 OK`:
```json
{
  "user_guid": "...",
  "direct_phases": ["analysis"],
  "effective_phases": ["analysis"]
}
```

The `group_derived_phases` field was dropped in #57. For historical inspection of which phases a user would previously have held via group membership, run `scripts/phases_migration_report.py`.

---

### `POST /api/admin/users/<user_guid>/phases` *(#46)*

Grant a direct phase to a user without requiring group membership. Useful for analysts who need `analysis` access without joining every planning group.

**Request:**
```json
{
  "phase": "analysis"
}
```

`phase` must be one of `planning`, `request`, `provider`, `analysis`.

**Response** `201 Created`:
```json
{
  "user_guid": "...",
  "phase": "analysis",
  "granted_at": "2026-04-15T08:30:00+00:00",
  "effective_phases": ["analysis"]
}
```

Idempotent: granting an already-held phase returns `200 OK` with the existing record.

**Errors:** `400` invalid phase name, `404` user not found, `409` user is a patient (phases are professional-only)

---

### `DELETE /api/admin/users/<user_guid>/phases/<phase>` *(#46, clarified by #57)*

Revoke a direct `UserPhase` grant. Since #57 this is the only mechanism that removes `<phase>` from `effective_phases` — group memberships no longer contribute to the set, so there is no second source to also clear. Group cleanup, if desired, is a separate SU action.

**Response** `200 OK`:
```json
{
  "user_guid": "...",
  "phase": "analysis",
  "revoked": true,
  "effective_phases": []
}
```

**Errors:** `404` user has no direct grant for this phase

---

### `GET /api/admin/group-proposals`

List pending group proposals.

### `POST /api/admin/group-proposals`

Approve or reject a group proposal. Approve creates the group.

**Request:**
```json
{
  "proposal_guid": "...",
  "decision": "approved"
}
```

---

### `GET /api/admin/leader-requests`

List pending leader (group admin) requests.

### `POST /api/admin/leader-requests`

Approve or reject. Approve sets `is_admin=True` on the membership.

**Request:**
```json
{
  "leader_request_guid": "...",
  "decision": "approved"
}
```

---

### `GET /api/admin/access-requests`

List pending and endorsed access requests.

### `POST /api/admin/access-requests`

Endorse, approve, or reject. Approve creates user + professional + organisation link + phase memberships.

**Request:**
```json
{
  "access_request_guid": "...",
  "decision": "approved"
}
```

**Response** `200 OK`:
```json
{
  "access_request_guid": "...",
  "status": "approved",
  "user_guid": "newly-created-user-guid"
}
```

---

### `GET /api/admin/organisations`

List all organisations (FHIR Organization shape).

### `POST /api/admin/organisations`

Create a new organisation.

**Request:**
```json
{
  "name": "New Hospital"
}
```

**Response** `201 Created`:
```json
{
  "resourceType": "Organization",
  "organisation_guid": "...",
  "name": "New Hospital"
}
```

---

### `GET /api/admin/export-users`

Download all users as CSV. Response `Content-Type: text/csv`.

### `POST /api/admin/import-users`

Upload CSV to import users. Multipart form with `file` field. Duplicates (by email) are skipped. New users get temporary password `changeme01`.

**Response** `200 OK`:
```json
{
  "created": 5,
  "skipped": 2,
  "errors": []
}
```

---

### `GET /api/admin/oath-overview`

Read the service registry (`oath_overview.csv`).

### `PUT /api/admin/oath-overview`

Update the service registry. Send array of row objects.

**Request:**
```json
[
  {
    "service_name": "sso.pdhc",
    "service_url": "https://sso.pdhc.se",
    "api_health_url": "https://sso.pdhc.se/api/health",
    "capability_statement_url": "https://sso.pdhc.se/fhir/metadata",
    "endpoints_url": "",
    "privilege_level": "public",
    "notes": "Central SSO"
  }
]
```

---

## Public (`/api/public`)

No authentication required. All endpoints are rate-limited.

### `GET /api/public/organisations`

**Rate Limit:** 60 req/min per IP

Organisation catalog (single source of truth for all services).

**Response** `200 OK`:
```json
[
  {
    "organisation_guid": "...",
    "name": "Test Hospital"
  }
]
```

---

### `GET /api/public/groups`

**Rate Limit:** 60 req/min per IP

Read-only group catalog.

---

### `GET /api/public/group-leaders`

**Rate Limit:** 60 req/min per IP

List group leaders and SU admins (used by the access request form).

**Response** `200 OK`:
```json
[
  {
    "user_guid": "...",
    "first_name": "Anna",
    "last_name": "Svensson",
    "is_su_admin": true
  }
]
```

---

### `POST /api/public/access-request`

**Rate Limit:** 5 req/min per IP

Submit a professional access request.

**Request:**
```json
{
  "email": "new.doctor@hospital.se",
  "password": "securepass",
  "first_name": "Erik",
  "last_name": "Johansson",
  "professional_role": "doctor",
  "organisation_guid": "...",
  "requested_phases": ["planning", "analysis"],
  "chosen_leader_guid": "..."
}
```

`professional_role` must be one of: `doctor`, `nurse`, `other`.

**Response** `201 Created`:
```json
{
  "access_request_guid": "...",
  "status": "pending",
  "message": "Access request submitted. A leader will review your request."
}
```

**Errors:** `400` validation, `404` org/leader not found, `409` email taken

---

## Error Response Format

All error responses follow this structure:

```json
{
  "error": "error_code",
  "message": "Human-readable description"
}
```

Validation errors include a `messages` array:

```json
{
  "error": "validation_error",
  "messages": [
    "email is required",
    "password must be at least 8 characters"
  ]
}
```
