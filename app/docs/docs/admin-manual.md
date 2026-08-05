# Admin Manual

Operations guide for SU (Super User) administrators and group administrators.

## Roles Overview

Two orthogonal things are both loosely called "role" — keep them apart:

**Administrative roles** (who can operate the panel):

| Role | Scope | Key Actions |
|------|-------|-------------|
| **SU Admin** | System-wide | All admin operations, user management, org management, affiliations, registries, partners |
| **Group Admin** | Per-group | Membership approvals, invites for own groups |

**Professional (clinical) roles** are a separate concept introduced by the
access-model reform (S1–S9). They live in an SU-managed **role registry**
(`/api/registry/roles`, e.g. doctor, nurse, researcher, …) and are attached to
a person through an **Affiliation** (person + CareUnit + Role), not stored as a
free-text field. See **Affiliations & Guided Activation** and **Role &
Research-Project Registries** below. A person's actual data access is composed
downstream from three independent inputs: **phase grants**, **affiliations/role**,
and **organisation scope** — no single one implies another.

## SU Admin Operations

### Accessing the Admin Panel

1. Log in at `/login` with an SU admin account
2. Navigate to the dashboard — the **SU Admin** section is visible only to SU admins
3. Or go directly to `/su-admin`

### User Management

#### View All Users

The user table shows all registered users with:

- Email, user type (patient/professional), SU status
- Professional details: name, role (doctor/nurse/other)
- Group memberships and admin status
- Organisation affiliations

**API:** `GET /api/admin/users`

#### Promote to SU Admin

Elevates a professional to system-wide admin. Requires your password for confirmation.

1. Click **Promote** next to the target user
2. Enter your password
3. Confirm

**API:** `POST /api/admin/promote-su` with `{user_guid, password}`

!!! warning
    Only professionals can be SU admins. This action cannot be undone via the UI — demote by directly updating the database.

#### Delete User

Removes a user and their associated data. The system:

- Nullifies all `decided_by_guid` references (preserving decision history)
- Deletes all memberships
- Cascades to patient/professional records

**API:** `DELETE /api/admin/users/<user_guid>`

!!! warning
    You cannot delete yourself. User deletion is irreversible.

#### CSV Export

Downloads all users as a CSV file with columns: `user_guid`, `email`, `user_type`, `is_su_admin`, `first_name`, `last_name`, `professional_role`, `created_at`.

**API:** `GET /api/admin/export-users`

#### CSV Import

Upload a CSV file to bulk-create users. Requirements:

- CSV must have headers matching the export format
- Existing emails are skipped (not overwritten)
- New users get temporary password `changeme01`
- Users should change their password on first login

**API:** `POST /api/admin/import-users` (multipart form, `file` field)

---

### Forcing a Password Reset (#43)

Use when a user has forgotten their password, a credential has leaked, or compliance requires rotation.

1. Locate the user in the admin panel
2. Click **Reset password** (or POST to the endpoint below)
3. Optionally supply a temporary password; if omitted, the server generates one and returns it
4. Communicate the temporary password to the user out-of-band (phone, in-person)

**What happens next:**

- The server sets `force_change_on_next_login = True` on the user
- Their next authenticated request to any PDHC service causes `/api/auth/me/service` to return `must_change_password: true`
- Every downstream service (dashboard, plan, contract, request, rosetta, cdr, ips, gateway) is wired to redirect the user to `sso.pdhc.se/change-password` until they pick a new password
- Once the user successfully posts `POST /api/auth/change-password`, the flag clears automatically and they are unblocked on every service

**API:** `POST /api/admin/users/<user_guid>/reset-password` with optional `{temporary_password}`

!!! note "This does not kill existing tokens"
    The user's existing JWTs are not invalidated — they just hit a forced redirect on every call. If you also want to terminate every active session (e.g. after credential theft), call **Flush Sessions** immediately after.

### Flushing User Sessions (#44)

Use when you need to invalidate **every** active JWT for a user in one operation — typically after a credential compromise, lost device, or role change.

1. Locate the user in the admin panel
2. Click **Flush sessions**

**What happens:**

- The server sets `user.token_revocation_epoch = now()`
- Every existing JWT for this user (on every device, every service) starts returning **401** on the next `/me/service` call, because its `iat` timestamp is older than the new epoch
- Downstream services must treat that 401 as "session terminated" — they wipe their local session and bounce the user through SSO login again
- The user must re-login anywhere they were signed in

**API:** `POST /api/admin/users/<user_guid>/flush-sessions` (empty body)

!!! note "Why not use the token revocation list?"
    The per-JWT revocation list (`RevokedToken` / `jti`) works well when a single token is being revoked (e.g. the `/api/auth/logout` path), but it requires knowing every `jti` that is currently in circulation. `flush-sessions` is the right primitive when you simply want "all tokens issued before now are void."

### Managing Phase Grants (#46 + #57)

After #57, **direct `UserPhase` grants are the sole source of phase access**. Groups are orthogonal organisational/category metadata — an approved membership in a `planning`-typed group does **not** grant the `planning` phase. Every phase assignment goes through an explicit SU decision.

This separation lets each downstream service compose its own access policy from independent inputs (phase, group membership, org scope) rather than inheriting one conflated "group = phase" rule.

**View a user's direct grants:**

```
GET /api/admin/users/<user_guid>/phases
```

Response includes `direct_phases` and `effective_phases` (now identical — groups no longer contribute).

**Grant a phase:**

```
POST /api/admin/users/<user_guid>/phases
Body: {"phase": "analysis"}
```

The phase appears in `effective_phases` on the user's next blob call; no re-login required. Idempotent.

**Revoke a phase:**

```
DELETE /api/admin/users/<user_guid>/phases/<phase>
```

Removes the `UserPhase` row. Since #57 this is sufficient on its own — there is no second "group-derived" source that could keep the phase alive. Group cleanup, if desired, is a separate SU action.

!!! info "Access-request approval no longer auto-grants phases (#57)"
    Approving an access request creates the professional and their group membership(s) but **does not** create `UserPhase` rows — even if the request's `requested_phases` names `planning`/`request`/etc. The approval response lists `requested_phases_pending_su_grant`; SU must then call `POST /api/admin/users/<guid>/phases` for each phase the user should actually hold.

!!! tip "Migration from pre-#57 installations"
    A read-only report — `python scripts/phases_migration_report.py` — lists every user who previously had a phase implicitly via group membership but has no matching `UserPhase` row. SU should review the list and grant explicitly. Nothing is auto-backfilled.

---

### Affiliations & Guided Activation (S9, #411)

This is the reform's core person-onboarding flow: **the person requests
sign-on; the SU assigns.** A professional is created **pending** (zero access)
and cannot be **activated** until their profile is complete. All completeness
checks are computed **server-side** — the panel renders the same `missing` list
the API enforces, so hiding a field in the UI cannot bypass a check.

An **Affiliation** binds a person to a **CareUnit + Role** (with research
projects when the role is `researcher`):

- **Assign an affiliation:**
  `POST /api/admin/users/<guid>/affiliations`
  Body `{care_unit_guid, role_guid, research_project_guids?, is_admin?}`.
  Server validations: the user is a professional; `care_unit_guid` is an
  **internal** organisation; `role_guid` is in the role registry; a
  `researcher` role requires ≥1 known `research_project_guid` and any other
  role must carry none; `(person, unit, role)` is unique (409 on duplicate).
- **List a person's affiliations + completeness:**
  `GET /api/admin/users/<guid>/affiliations`
- **Remove an affiliation:**
  `DELETE /api/admin/users/<guid>/affiliations/<affiliation_guid>`
- **Completeness checklist:** `GET /api/admin/users/<guid>/completeness`.
  Typical `missing` codes: `no_active_affiliation`, `no_useful_phase_grant`
  (no phase the held role can use), `researcher_affiliation_missing_projects`.

**Activate / deactivate:**

- `POST /api/admin/users/<guid>/activate` — flips `status` to `active`. Returns
  **409 `incomplete_profile`** with the `missing` list while the profile is
  incomplete.
- `POST /api/admin/users/<guid>/deactivate` — sets a professional back to
  `pending` (access is revoked on the next blob build; pair with **Flush
  Sessions** for immediate effect). An SU admin cannot be deactivated.

!!! note "Phases and affiliations are both required, and independent"
    A complete profile needs **both** ≥1 affiliation **and** a phase grant the
    role can use. Assigning an affiliation does not grant a phase, and granting
    a phase does not create an affiliation — do both, then activate.

---

### Role & Research-Project Registries (S2/#398, #400)

Two SU-managed registries feed the affiliation form. Reads are open to any
authenticated professional; writes are **SU-only**.

**Roles** — the clinical role vocabulary (doctor, nurse, researcher, …):

- `GET /api/registry/roles` · `POST /api/registry/roles`
- `PUT /api/registry/roles/<guid>` · `DELETE /api/registry/roles/<guid>`
  (delete refuses if any affiliation still references the role).

**Research projects** — referenced by `researcher` affiliations:

- `GET /api/registry/research-projects` · `POST /api/registry/research-projects`
- `PUT` / `DELETE /api/registry/research-projects/<guid>`

!!! warning "Panel gap"
    The SU panel currently **consumes** these registries (the affiliation form's
    Role and Projects dropdowns) but has **no management UI** to create/rename/
    retire a role or research project — do that via the API above until a
    registry admin section is added.

---

### External Partners

Third-party callers licensed to talk to PDHC are managed in the **External
Partners** panel (this replaced the legacy `KEYAUTH_SERVICE_*` env registry).
The panel supports register / rotate-secret / suspend / reactivate / revoke,
with the cleartext secret shown **once** on register or rotate.

See the dedicated **[External Partners](external-partners.md)** document for the
full model, endpoints (`/api/admin/partners…`), auth kinds, and scopes.

---

### Organisation Management

Organisations are the **single source of truth** across all PDHC services.

#### View Organisations

Lists all registered organisations with GUID and creation date.

**API:** `GET /api/admin/organisations`

#### Create Organisation

Add a new organisation. Name must be unique.

**API:** `POST /api/admin/organisations` with `{name}` (optionally
`{parent_caregiver_guid}` — see Care Hierarchy below).

!!! note
    Downstream services pull organisation lists from `GET /api/public/organisations`. New organisations are immediately available system-wide.

#### Care Hierarchy — vårdgivare / CareUnit (S8, #410/#187)

Organisations form a **2-level PDL care hierarchy** on a single
self-referential table (`parent_caregiver_guid`):

- `parent_caregiver_guid = NULL` → the row **is** a caregiver (vårdgivare;
  the legal entity). Exposed as the **CareOrganisation** view.
- `parent_caregiver_guid = <guid>` → the row is a **CareUnit** under that
  caregiver.

An organisation also carries an `is_external` flag distinguishing internal PDHC
organisations from external partner orgs. **Affiliations may only target an
internal organisation** (`is_external = false`) — so an org must exist and be
internal before a professional can be affiliated to it.

- Set/relocate the parent on create or update:
  `POST` / `PUT /api/admin/organisations[/<guid>]` with `parent_caregiver_guid`.
  The 2-level hierarchy is validated server-side (`validate_care_hierarchy`);
  an invalid parent (self-reference, or a parent that is itself a CareUnit)
  returns `400 invalid_hierarchy`.
- Read the derived views: `GET /api/registry/care-organisations` (caregivers)
  and `GET /api/registry/care-units`.

!!! warning "Panel gap"
    The current SU panel's Create-Organisation form only sends `name`, and the
    org table does not show parent / internal-external. Until that is added,
    set `parent_caregiver_guid` and `is_external` via the API (`PUT
    /api/admin/organisations/<guid>`).

---

### Group Lifecycle

#### Group Proposals

Professionals can suggest new groups via the UI. Proposals appear in the admin panel.

**Workflow:**

1. Professional submits group proposal (name + type)
2. SU reviews proposal in admin panel
3. **Approve** — creates the group immediately
4. **Reject** — proposal archived

**API:** `GET /api/admin/group-proposals` and `POST /api/admin/group-proposals` with `{proposal_guid, decision}`

!!! note "`category` is a free-form label, not a phase (#60, #57)"
    A group's `category` is a **free-text organisational label** (since #60 —
    formerly a 4-value `group_type` enum). It has **no effect on access**.
    Do **not** reuse phase names (`planning`/`request`/`provider`/`analysis`)
    as categories — group membership does not confer phases (#57); the panel
    warns against this. Phases are granted per-user in the Person Registry.

#### Delete Group

Removes a group and all associated memberships and invites.

**API:** `DELETE /api/admin/groups/<group_guid>`

#### Assign Group Admin

Set any existing group member as an admin of that group.

**API:** `POST /api/admin/assign-group-admin` with `{user_guid, group_guid}`

---

### Leader Requests

Professionals can request to become group admins. These requests appear in the SU admin panel.

**Workflow:**

1. Professional requests admin role for a specific group
2. SU reviews in admin panel
3. **Approve** — sets `is_admin=True` on their membership
4. **Reject** — request archived

**API:** `GET /api/admin/leader-requests` and `POST /api/admin/leader-requests` with `{leader_request_guid, decision}`

---

### Access Request Workflow

New professionals request access through the public onboarding form. The workflow has three stages:

```
pending → endorsed → approved (creates account)
              ↘ rejected
```

1. **Pending** — new request submitted. SU can endorse or reject.
2. **Endorsed** — leader endorsement recorded. SU can approve or reject.
3. **Approved** — system creates a **pending** professional with **zero
   access**:
   - User account (`user_type = professional`, **`status = pending`**)
   - Professional record (with role, name)
   - Organisation link
   - **Nothing else.** No phase grants, no affiliations, no active access.

**API:** `GET /api/admin/access-requests` and `POST /api/admin/access-requests` with `{access_request_guid, decision}`

`decision` values: `endorsed`, `approved`, `rejected`

!!! warning "Approval does not grant access (#57, S9 #411)"
    Approving an access request **does not** auto-grant the requested phases,
    and **does not** activate the user. The request's `organisation_guid` and
    `professional_role` survive only as a **non-authoritative hint** the guided
    UI prefills from (`requested_phases_pending_su_grant` / `hint` in the
    response). After approval the SU must, in the **Activation & Affiliations**
    panel: (1) assign ≥1 **affiliation** (CareUnit + Role), (2) grant the phase(s)
    the role should hold, then (3) **activate** the user. Activation is blocked
    server-side until the profile is complete — see the next section.

---

### Service Registry (Oath Overview)

The `oath_overview.csv` tracks all services under SSO management.

**View:** `GET /api/admin/oath-overview`

**Update:** `PUT /api/admin/oath-overview` — send the full CSV as a JSON array of row objects.

Fields: `service_name`, `service_url`, `api_health_url`, `capability_statement_url`, `endpoints_url`, `privilege_level`, `notes`

---

## Group Admin Operations

### Accessing the Group Admin Panel

1. Log in with a professional account that has `is_admin=True` in at least one group
2. Navigate to `/group-admin`

### Pending Membership Requests

View all pending membership requests for groups you administer.

Each request shows: applicant email, group name, submission date.

**Actions:**

- **Approve** — member gains access to the group
- **Reject** — membership denied

**API:** `GET /api/groups/admin/pending` and `POST /api/groups/admin/decide` with `{membership_guid, decision}`

### Creating Invite Links

Generate time-limited invite tokens for a group:

1. Select the group
2. Set validity period (default 48 hours)
3. Share the generated token/link with the invitee

The invitee redeems the token at `/join` or via `POST /api/groups/join-by-invite`.

**API:** `POST /api/groups/admin/invite` with `{group_guid, hours_valid}`

!!! note
    Invite tokens create **pending** memberships. The group admin must still approve the membership after the invite is redeemed.

---

## Audit Trail

All admin actions are logged to structured audit files in `LOG_DIR`:

- Login attempts (success/fail)
- User creation, deletion, promotion
- Group creation, deletion
- Membership decisions
- Access request decisions
- Organisation changes
- Oath overview updates
- Forced password resets (#43) — actor, target, whether a temp password was supplied
- Session flushes (#44) — actor, target, new `token_revocation_epoch`
- Direct phase grants and revocations (#46)
- Affiliation create/delete, user activate/deactivate (S9, #411)
- External partner register / rotate / suspend / reactivate / revoke

Logs include: timestamp, action type, actor GUID, target GUID, IP address, and action-specific details.
