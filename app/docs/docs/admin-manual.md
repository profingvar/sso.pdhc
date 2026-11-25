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

#### The Person Registry

Reorganised in #784 (B4/B5). It was ten columns wide, which meant horizontal
scrolling on any realistic screen and no way to read one person as one thing.

It is now **six columns**, with the identity fields packed into a single
**Person** cell — GUID, email, user type, name and role together, because they
describe one person and were never useful apart:

| column | holds |
|---|---|
| Person | GUID, email, user type, name, professional role |
| Organisation | the affiliated care unit, **by name** |
| Phases | granted phases |
| Groups | memberships and group-admin status |
| Status | SU, activation, forced-password-change |
| Actions | Promote SU / Reset pw / Flush / Del |

**The organisation is shown by name, which it never was before #784.** The
page resolved it with `{% set org_name = o.name %}` inside a nested `{% for %}`
loop, and Jinja2 scopes that assignment to the loop iteration — so the variable
was always empty on the way out and the cell *always* fell back to the first
eight characters of the GUID. Every administrator who ever read this table saw
a hex fragment and had no way to know a name was intended.

The route now passes an explicit `org_names` mapping, which also replaced an
O(users × organisations) scan with a lookup. An affiliation whose organisation
GUID resolves to nothing reads **"(unknown org abcd1234…)"** rather than a bare
fragment — a dangling reference is a thing to fix, so it says so.

Each action button carries a **tooltip stating what it does and what it does
not do** (#784 B6), because "Flush" and "Reset pw" are both irreversible from
the user's point of view and neither name says how far it reaches.

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

Downloads every person as CSV. Use the **Export CSV** button on the admin page.

**Two routes, and the difference matters:**

| route | authenticates with | use from |
|---|---|---|
| `GET /admin/export-users` | the **session cookie** | a browser (this is what the button uses) |
| `GET /api/admin/export-users` | `Authorization: Bearer <token>` | a script or another service |

Before #784 only the API route existed, and the admin page linked straight to
it. `require_auth` accepts **only** a bearer header, so a browser following
that link sent its cookie, no token, and opened a new tab containing
`{"error": "authentication_required"}`. Export appeared broken for every human
who clicked it while working perfectly for every script.

It was an asymmetry rather than a mystery: **import** already had a
session-authenticated twin; export never got one. The API route was
deliberately **not** loosened to accept a cookie — a test pins that it still
refuses one, so "fixing" this by weakening the API would fail the suite. The
columns come from a single `USER_CSV_COLUMNS` contract shared by the exporter,
the importer and the template below, so the three cannot drift apart.

#### CSV Import

Upload a CSV to bulk-create people. The file input reads **"CSV file of people
to add:"**, and a **Show template** panel on the page prints the exact header
row expected (`GET /admin/users-csv-template`), generated from the same
`USER_CSV_COLUMNS` contract as the export — so the template cannot describe a
format the importer does not accept.

- Headers must match that template
- Existing emails are **skipped**, never overwritten
- Every imported account is created with the password **`changeme01`**
- **The account must change it at first login.** Not "should" — the
  `force_change_on_next_login` flag is set and `/dashboard` redirects to the
  change-password page until it is done.

That last point was a real defect found while writing this section, not a
feature. The documentation has to state the shared password in order to be
usable, and that is only safe to write down if the account cannot keep it. The
gate enforcing it already existed; import simply never set the flag, so every
imported account kept a known shared password indefinitely. Fixed in #784, with
a test that walks an imported user through `/dashboard` and asserts the
redirect fires — asserting the column alone would have proved nothing about the
gate.

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

### Unlocking a Locked-Out User (#1040)

Five wrong passwords in a row lock **password login** for that account for 15
minutes, for every user type and at both login doors (the web form and
`POST /api/auth/login`). The user is not told: they see "Invalid email or
password" even with the right password. So a user who says "my password
stopped working" may simply be locked.

| task | how |
|---|---|
| See who is locked right now | `GET /api/admin/login-locks` → `[{user_guid, email, locked_until}]` |
| Unlock now | `POST /api/admin/users/<user_guid>/unlock-login` → `{user_guid, was_locked}`. Clears the counter too. Audited as `login_unlock` |
| Do nothing | The lock ends by itself at `locked_until` |

- The lock is **time-based only**. It never becomes permanent and never grows;
  attempts made while locked are not counted and do not extend it.
- It cannot be switched off per user. Somebody who knows an email address can
  re-lock that account every 15 minutes with five wrong passwords — including
  an SU admin's. If that happens, the audit log shows it (`login_locked`,
  `login_refused_locked`, with the address), and the address can be blocked at
  the reverse proxy.
- **If the only SU admin is locked out** and holds no open session: wait 15
  minutes, or the operator runs `python scripts/unlock_login.py <email>` in
  the app container.
- This is not the authenticator-app lock on a patient's second step (five
  wrong codes, 15 minutes), which is separate and has no unlock; a passkey
  still works during it.

There is no SU panel for this yet; use the API with your Bearer token.

---

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

### Patient app: links and second factor (#1040)

**Linking is done by care, not by the SU.** A professional affiliated to the
patient's care unit invites the patient or redeems the patient's link code,
from their own Dashboard. As SU you can do the same for any care unit.

What only an SU can do:

| task | how | note |
|---|---|---|
| Change which patient an account is linked to | `PUT /api/admin/patient-links/<user_guid>` with `ips_patient_guid`, `reason` and, preferably, `care_unit_guid` | The guid must exist in eps. Name the care unit and eps must also list the patient there; leave it out and only existence is checked — so name it |
| Remove a link | `DELETE /api/admin/patient-links/<user_guid>` with `reason` | The patient app then answers `not_linked` |
| Reset a lost second factor | **Återställ 2FA** on the user's row in the SU page, or `POST /api/admin/users/<user_guid>/second-factor/reset` | Removes all passkeys and the authenticator secret, logs the user out everywhere, and shows a **one-time code once** (#1045). *Visas bara en gång. Lämna koden till patienten efter att du har kontrollerat vem det är.* Give it in person or by telephone after checking who it is. The patient types it after their password, then adds a new factor; without it the account cannot log in or add one. Valid 72 h, once; five wrong entries void it; lost or void code → reset again (that replaces it) |
| Reset a professional's lost second factor (#1055) | the same button (on professional rows too) or the same API call | Same reset, same code, text *"Lämna koden till personen…"*. The professional types the code after the password and is then logged in WITHOUT a factor (as before they enrolled); adding a new one is optional. Until the code is typed the account cannot log in |

**When linking is refused.** Every link is checked against eps.pdhc first.

| the user sees | meaning | what to do |
|---|---|---|
| `patient_not_found` | eps has no patient with that guid | The guid is wrong. Copy it again from eps |
| `patient_not_at_unit` | The patient exists but is not listed at that care unit in eps | Wrong patient, wrong unit, or the listing is missing in eps. Fix the listing in eps if the patient really belongs there |
| `eps_unavailable` | eps did not answer, or refused SSO's key | Nothing was linked. Try again; if it persists, the operator checks eps and `EPS_API_KEY` |
| `eps_not_configured` | `EPS_BASE_URL` / `EPS_API_KEY` are not set on SSO | Operator: see the Deployment Guide |

There is no SU panel for these yet; use the API with your Bearer token. Every
link, relink and unlink is kept in the `patient_link_history` table as well as
in the audit log.

### Patients join by invitation only (#1050)

Operator decision 2026-11-22: a patient joins the patient app **only by an
invitation from their care unit**, made in dashboard.pdhc's **Bjud in
patient**. That page registers the patient in eps at the chosen unit and calls
`POST /api/patient-links/invites` with the professional's own bearer, sending
the patient's name. On `/patient-invite/<token>` the patient must type a
personnummer that eps confirms belongs to that patient
(`POST /api/v1/patients/<guid>/identifier-match`, scope
`patients:identifier-match` on SSO's eps key, operator script
`plans/patientapp_deploy/ops/pa7_invite_scope.sh`). Five wrong numbers close
the invite. The name becomes the account's `patient_profiles` row
(`display_name`).

| audit event | meaning |
|---|---|
| `patient_invite_pnr_mismatch` | a wrong personnummer on an invite (`failures`, `closed`); never the number |
| `patient_invite_pnr_check_failed` | eps could not be asked (`reason`); nothing created, nothing counted |

Self-registration is **closed** by default (`PATIENT_SELF_REGISTRATION`, #1050
step 1): `/register-patient` and `POST /api/patient/register` answer 403
`registration_closed`. The rest of this section describes what happens if the
operator turns it on again.

#### Patient self-registration (#1049), when turned on

A patient could register on their own (`/register-patient`). SSO asks eps to create the patient at the
chosen unit; when eps does, the account is linked at once
(`linked_by = self-registration`, the chosen unit). When eps already knew the
personnummer, the account is created **unlinked** and care links it with the
patient's link code as before — SSO never links to an existing eps patient on
the registrant's word.

| audit event | meaning |
|---|---|
| `patient_self_linked` | new account, eps created the patient, linked |
| `patient_register_unlinked` | new account, eps already had the patient; waits for a link code |
| `patient_register_duplicate` | email or personnummer already had an account; nothing created (`which` says which) |
| `patient_register_refused` | eps not configured / unreachable / unit unknown to eps; nothing created |
| `patient_register_not_committed` | eps created a patient but SSO could not save the account; rolled back. The eps patient (guid in the event) has no account; a new attempt gets an unlinked account |

The registrant sees the same page for the first three, by design: the page
must not reveal who already has an account. To answer "did my registration
work?", look up the email in the audit log, not by asking the patient to
register again.

Which units are offered is `PATIENT_REGISTRATION_UNITS` (operator, `.env`).
In the pilot only test personnummer are accepted
(`PATIENT_REGISTRATION_SYNTHETIC_ONLY=true`).

---

### HSA-id and legitimation (#1054, record.pdhc)

record.pdhc shows who is responsible for a journal entry **with their
professional role**, and decides who may sign. It reads two things from SSO:
the role per care unit (the affiliation, unchanged) and, new, the person's
**HSA-id** and **legitimation**.

**Set them** on the professional's row in the SU page (the small form under
the role: HSA-id field, legitimation list, **Spara**), or with
`PUT /api/admin/users/<user_guid>/qualification`. Only an SU can. A blank
field clears the value. Every change is audited with old and new value.

- **HSA-id**: `SE` + the 10-digit organisation number + `-` + 1–18 letters or
  digits, at most 31 characters, e.g. `SE5565594230-SYNT0001`. Stored
  upper-case. Two people cannot share one.
- **Pilot rule (D8): synthetic HSA-ids only.** The part after the dash must
  start with `SYNT` (`HSA_ID_SYNTHETIC_PREFIX`). A real-looking HSA-id such as
  `SE2321000016-1234` is refused ("The pilot accepts synthetic HSA-ids only").
  The operator turns this off (`HSA_ID_SYNTHETIC_ONLY=false`) only at level B,
  when real HSA-ids are allowed.
- **Legitimation**: one of Socialstyrelsen's 22 legitimationsyrken (läkare,
  sjuksköterska, barnmorska, fysioterapeut, …), or none — an undersköterska,
  for example, has none. It is shown as **ej verifierad**: the pilot has no
  lookup against Socialstyrelsen's register, so `legitimation_verified` is
  always false, and a change of legitimation keeps it false.

#### Svenska: HSA-id och legitimation

record.pdhc visar vem som är ansvarig för en journalanteckning **med
yrkesroll** och avgör vem som får signera. Från SSO läser den rollen per
vårdenhet (affilieringen, som förut) och nu även personens **HSA-id** och
**legitimationsyrke**.

**Så sätter du dem:** på den professionelles rad i SU-sidan (det lilla
formuläret under rollen: fältet HSA-id, listan legitimation, **Spara**), eller
med `PUT /api/admin/users/<user_guid>/qualification`. Bara en SU kan. Ett tomt
fält tar bort värdet. Varje ändring loggas med gammalt och nytt värde.

- **HSA-id**: `SE` + vårdgivarens tiosiffriga organisationsnummer + `-` +
  1–18 bokstäver eller siffror, högst 31 tecken, t.ex.
  `SE5565594230-SYNT0001`. Två personer kan inte ha samma.
- **I piloten bara syntetiska HSA-id (D8).** Delen efter bindestrecket måste
  börja med `SYNT`. Ett HSA-id som ser riktigt ut nekas. Riktiga HSA-id först
  på nivå B (operatören sätter då `HSA_ID_SYNTHETIC_ONLY=false`).
- **Legitimation**: ett av Socialstyrelsens 22 legitimationsyrken, eller
  ingen (en undersköterska har till exempel ingen legitimation). Visas som
  **ej verifierad**: piloten slår inte upp legitimationen i Socialstyrelsens
  register.

### Patient extract (#1059, record.pdhc)

`GET /api/v1/patient-extract/<eps patient guid>` gives everything SSO holds
about a patient — the linked account, its login factors (type and dates),
invites, the link and its history, one-time codes — for record.pdhc's PDF
extract. **Never** a password hash, an authenticator secret, a passkey key,
an invite or link code, a re-enrolment code, or a personnummer. Allowed: SU,
and doctors and nurses affiliated to a care unit where eps lists the patient;
everyone else gets 404 as if the patient did not exist. Every call is in the
audit log as `patient_extract`, refusals too. Details: API Reference.

**Svenska.** Utdraget ger allt SSO har om patienten: kopplat konto,
inloggningsfaktorer (typ och datum), inbjudningar, kopplingen och dess
historik, engångskoder. Aldrig lösenordshash, hemligheter, nycklar, koder
eller personnummer. Får tas ut av SU och av läkare och sjuksköterskor som är
affilierade till en vårdenhet där patienten är listad i eps; alla andra får
404. Varje anrop loggas som `patient_extract`, även nekade.

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

##### How the panel displays it (#784 C1)

The organisation table is **grouped, not flat**. Each vårdgivare heads a
section with its vårdenheter nested beneath it, and external partners sit in
their own section at the end. Every row is classified server-side into one of
three kinds:

| kind | condition |
|---|---|
| `care_organisation` | vårdgivare — no parent, not external |
| `care_unit` | vårdenhet — has a `parent_caregiver_guid` |
| `external` | `is_external = true` |

**The section headings state what the level MEANS**, not just what it is called:
a vårdenhet is the **spärrgräns** — the boundary a patient's block applies
across. That is a legal fact under PDL rather than a naming convention, and an
administrator moving a unit between caregivers is changing which records a
spärr hides. Leaving the hierarchy labelled without saying so invited exactly
that mistake.

**A unit whose parent GUID resolves to nothing is surfaced, not hidden.**
Grouping strictly by parent would have made such a row vanish from the table
altogether — present in the database, invisible in the UI, and therefore
unfixable. It is shown so it can be corrected.

The Create-Organisation form now sends `parent_caregiver_guid` alongside
`name`, and each row carries a **re-parent** control
(`POST /admin/set-org-parent`). Re-parenting an external partner is refused:
*"External partners are not part of the care hierarchy."*

!!! note "Still API-only"
    `is_external` cannot be set from the panel. An external organisation is
    created through the External Partner subsystem, or by
    `PUT /api/admin/organisations/<guid>`.

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

> **The Service Registry PANE was removed from the UI in #784 (D2)**, together
> with the Documentation pane (D1). The data and the API below are unchanged —
> only the admin-page panels that displayed them are gone, because neither
> showed an administrator anything they could act on. The landing page's
> Platform Services table and Connected Services cards went in the same pass
> (A3) along with the health prober that fed them.

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
- Login lockout (#1040): `login_locked` (the fifth wrong password; account,
  `locked_until`), `login_refused_locked` (an attempt during the lock, right
  password or not), `login_unlock` (SU, target, whether it was locked)
- HSA-id and legitimation (#1054): `set_professional_qualification` (SU,
  target, from/to of each changed field)
- Patient extract (#1059): `patient_extract` on every call to
  `GET /api/v1/patient-extract/<guid>`: who, which patient, `outcome`
  `ok`/`refused`, the reason of a refusal, and for `ok` the basis and the
  count per section. Unauthenticated calls are logged with no user
- Patient app (#1040): `patient_invite_create` / `_revoke` / `_accept` / `_refused`,
  `patient_link_code_create`, `patient_link` (who, which account, which patient,
  method), `patient_link_refused` (with the real reason, which the caller is not
  told), `patient_relink`, `patient_unlink`, `second_factor_enrol` / `_remove` /
  `_fail` / `_reset`, and `login_password_ok` (password accepted, second factor
  still owed) before `login_success` (which names the method)

Logs include: timestamp, action type, actor GUID, target GUID, IP address, and action-specific details.
