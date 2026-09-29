# sso.pdhc — Progress Tracking

Follows deployment plan numbering from readme.md. Each completed step includes tests deployed and their results.

---

## Phase 1 — Project Foundation

**1.a** — DONE. Directory structure created, venv initialised (Python 3.14.3), requirements.txt with all dependencies installed, .gitignore created.
**1.b** — DONE. `.env.example` created with all documented variables. `.env` created with generated SECRET_KEY. Not committed.
**1.c** — DONE. `Dockerfile` (python:3.12-slim + gunicorn) and `docker-compose.yml` (PostgreSQL 16 on 9003, app on 9000, health-checks) created.
**1.d** — DONE. `start.sh` at repo root (kills ports, checks Docker, starts DB + app, Ctrl+C cleanup). `safe_restart.sh` for server-side restarts. Both executable.
**1.e** — DONE. 29/29 tests passed. Results: `results/2026-03-18T08-05-13Z_results/phase1_tests.txt`

Tests deployed:
- TestVenvAndDependencies (9 tests): all imports verified (Flask, SQLAlchemy, JWT, bcrypt, dotenv, Flask-WTF, Flask-Cors, fhir.resources, psycopg2) — all PASSED
- TestAppFactory (3 tests): create_app, testing mode, health endpoint — all PASSED
- TestProjectStructure (13 tests): all directories, packages, config files verified — all PASSED
- TestStartScript (4 tests): start.sh + safe_restart.sh exist and are executable — all PASSED

## Phase 2 — Database Design

**2.a** — DONE. `initial_sql_design.txt` written with all 12 tables, enums, indexes, FHIR annotations.
**2.b** — DONE. 12 SQLAlchemy models implemented. ARRAY columns replaced with JSON for cross-DB compatibility. FHIR_RESOURCE_TYPE class attribute on Patient, Professional, Organisation, Group. Enums enforced for user_type, professional_role, group_type, membership_status, access_request_status, proposal_status.
**2.c** — DONE. `scripts/init_db.py` — drops and recreates all tables from model metadata.
**2.d** — DONE. `scripts/create_su.py` — bootstrap SU from .env, idempotent, bcrypt password hashing.
**2.e** — DONE. 24/24 tests passed. Results: `results/2026-03-18T08-14-27Z_results/phase2_tests.txt`

Tests deployed:
- TestAllTablesCreated (2 tests): all 12 tables exist, correct count — PASSED
- TestUserModel (3 tests): create, GUID valid UUID, user_type enum — PASSED
- TestPatientModel (2 tests): create with FHIR type, personnummer 12 chars — PASSED
- TestProfessionalModel (1 test): create with role enum + FHIR type — PASSED
- TestOrganisationModel (1 test): create with FHIR type — PASSED
- TestUserOrganisationModel (2 tests): junction create, user.organisations populated — PASSED
- TestGroupModel (1 test): create with group_type enum + FHIR type — PASSED
- TestMembershipModel (2 tests): create pending, approve changes status — PASSED
- TestGroupProposalModel (1 test): create pending — PASSED
- TestLeaderRequestModel (1 test): create pending — PASSED
- TestAccessRequestModel (1 test): create with all fields — PASSED
- TestInviteModel (1 test): create with expiry — PASSED
- TestRevokedTokenModel (1 test): create with expiry — PASSED
- TestFHIRAnnotations (4 tests): Patient/Practitioner/Organization/Group types — PASSED
- TestGUIDUniqueness (1 test): all user GUIDs unique — PASSED

## Phase 3 — Core Application

**3.a** — DONE. Flask app factory with config, DB init (SQLite for test, PostgreSQL for prod), teardown, middleware registration.
**3.b** — DONE. Config loads .env, validates required vars, TestConfig for testing. Service credentials parsed from SSO_CLIENT_ID_*/SSO_CLIENT_SECRET_* pairs.
**3.c** — DONE. DB session management: request-scoped via Flask g, commit on success, rollback on error. Context manager for scripts.
**3.d** — DONE. JWT service: issue (HS256, UUID jti), decode, validate (with revocation check), revoke, prune expired.
**3.e** — DONE. Auth middleware: @require_auth, @require_su, @require_professional, @require_patient, @require_group_admin. All GUID-based.
**3.f** — DONE. CSRF via Flask-WTF. API routes (/api/*) exempt (Bearer token auth).
**3.g** — DONE. CORS via Flask-Cors. Origins from ALLOWED_ORIGINS config.
**3.h** — DONE. In-memory rate limiter. Per-IP, configurable max_requests/window.
**3.i** — DONE. File-based audit logging. JSON lines, daily rotation, 90-day retention.
**3.j** — DONE. GET /api/health — status, DB connectivity, uptime.
**3.k** — DONE. 23/23 tests passed. Full suite: 76/76. Results: `results/2026-03-18T08-19-28Z_results/phase3_all_tests.txt`

Tests deployed:
- TestAppFactory (3): creates app, testing config, secret key — PASSED
- TestConfigModule (2): test config values, service credentials parsing — PASSED
- TestJWTService (7): issue, decode, wrong secret, expired, revoked, not revoked, unique jti — PASSED
- TestAuthMiddleware (5): no token 401, invalid token 401, require_su 401, require_professional 401, require_patient 401 — PASSED
- TestCSRF (1): API routes exempt — PASSED
- TestCORS (1): headers present — PASSED
- TestRateLimiter (2): under threshold OK, over threshold 429 — PASSED
- TestAuditLog (1): writes JSON to file — PASSED
- TestHealthEndpoint (1): returns ok in test mode — PASSED

## Phase 4 — Authentication API

**4.a** — DONE. `src/services/auth_service.py` — verify_password (bcrypt), hash_password, authenticate_user (email+password→User), build_access_blob (composite profile for /me endpoint with groups, phases, org IDs for professionals; patient_guid, registries for patients).
**4.b** — DONE. `src/routes/auth.py` — auth blueprint at `/api/auth`. POST /login with SSO handshake (next+state→redirect with token), callback URL allowlist validation. Supports JSON and form data.
**4.c** — DONE. GET /me — returns access blob (professional or patient shape). GET /me/service — same plus service credential validation via X-SSO-Client-Id/X-SSO-Client-Secret headers.
**4.d** — DONE. POST /logout — revokes token (adds jti to revoked_tokens table with explicit commit). POST /change-password — verifies current password, min 8 chars for new password.
**4.e** — DONE. Blueprint registered in app factory. Rate limiting on login (20 req/min).
**4.f** — DONE. 20/20 tests passed. Full suite: 96/96. Results: `results/2026-03-18T09-15-00Z_results/phase4_all_tests.txt`

Bug fix: `_get_current_user()` cached `g.current_user` at app-context level. In pytest-flask (shared app context), this caused stale user cache across requests — logout revocation was invisible to subsequent /me. Fixed with per-request caching using `id(request._get_current_object())`.

Tests deployed:
- TestLogin (7 tests): success, wrong password, nonexistent email, missing fields, SSO handshake redirect, reject unallowed callback, SSO fail redirect — all PASSED
- TestMe (4 tests): professional blob (orgs, groups, phases), patient blob (registries, FHIR type), SU admin, no token 401 — all PASSED
- TestMeService (3 tests): valid credentials, invalid credentials, missing credentials — all PASSED
- TestLogout (2 tests): revokes token (re-auth returns 401), without auth 401 — all PASSED
- TestChangePassword (4 tests): success (can re-login), wrong current password, too short, missing fields — all PASSED

## Phase 5 — Patient API

**5.a** — DONE. `src/routes/patient.py` — POST /api/patient/register: self-enrolment creates user (type=patient) + patient row. Validates personnummer (12 digits regex), organisation_guid exists, email/personnummer uniqueness. Returns FHIR Patient-shaped response (201).
**5.b** — DONE. GET /api/patient/registry-status — authenticated patient: returns in_registry, registries list, personnummer, organisation_guid. @require_patient enforces access control. FHIR Patient resourceType on response.
**5.c** — DONE. 13/13 tests passed. Full suite: 109/109. Results: `results/2026-03-18T09-30-00Z_results/phase5_all_tests.txt`

Bug fix: rate limiter state (module-level dict) persisted across test files. Added `reset_rate_limits()` call in app fixtures for auth and patient tests.

Tests deployed:
- TestPatientRegister (9 tests): success (FHIR shape), invalid personnummer (too short, non-digits), duplicate email, duplicate personnummer, nonexistent org, password too short, missing fields, can login after register — all PASSED
- TestRegistryStatus (4 tests): returns correct data (in_registry, registries), professional forbidden (403), no auth (401), FHIR response shape — all PASSED

## Phase 6 — Professional & Group API

**6.a** — DONE. GET /api/groups — lists own approved groups with FHIR Group resourceType shape, is_admin flag, membership_status.
**6.b** — DONE. POST /api/groups/request-membership — creates pending membership. Duplicate check, group existence validation. Audit-logged.
**6.c** — DONE. POST /api/groups/request-admin — creates leader_request (pending). Duplicate pending check. Audit-logged.
**6.d** — DONE. GET /api/groups/admin/pending — group admin sees pending memberships for their groups. SU sees all groups. Shows user email, group name, timestamps.
**6.e** — DONE. POST /api/groups/admin/decide — approve/reject pending membership. Authority check (admin of the specific group or SU). Sets decided_by_guid. Audit-logged.
**6.f** — DONE. POST /api/groups/admin/invite — create time-limited invite token (UUID, configurable hours_valid). Authority check. Audit-logged.
**6.g** — DONE. POST /api/groups/join-by-invite — redeem invite token → pending membership. Expiry check (timezone-safe), duplicate membership check.
**6.h** — DONE. 22/22 tests passed. Full suite: 131/131. Results: `results/2026-03-18T09-50-00Z_results/phase6_all_tests.txt`

Bug fix: datetime comparison in join-by-invite — SQLite stores naive datetimes, so added tzinfo normalization before comparing with timezone-aware `datetime.now(timezone.utc)`.

Tests deployed:
- TestListGroups (3): approved groups with FHIR shape, empty for new pro, patient forbidden — PASSED
- TestRequestMembership (4): creates pending, duplicate rejected (409), nonexistent group (404), missing field — PASSED
- TestRequestAdmin (2): creates leader_request, duplicate pending rejected — PASSED
- TestAdminPending (2): admin sees pending, non-admin forbidden — PASSED
- TestAdminDecide (5): approve, reject, already decided (409), non-admin of group forbidden, SU can decide any — PASSED
- TestAdminInvite (2): create invite, non-admin forbidden — PASSED
- TestJoinByInvite (4): end-to-end flow, expired token (410), invalid token (404), duplicate membership (409) — PASSED

## Phase 7 — SU Admin API

**7.a** — DONE. GET /api/admin/users — lists all users with full membership detail, org IDs, professional/patient info. SU only.
**7.b** — DONE. POST /api/admin/promote-su — promotes professional to SU admin. Requires caller password confirmation. Rejects patients. Audit-logged.
**7.c** — DONE. DELETE /api/admin/users/<user_guid> — deletes user. Cascade null on decided_by refs. Cannot delete self. Audit-logged.
**7.d** — DONE. DELETE /api/admin/groups/<group_guid> — deletes group with cascade (memberships, invites). Audit-logged.
**7.e** — DONE. POST /api/admin/assign-group-admin — sets user as admin in group. Requires existing membership. Audit-logged.
**7.f** — DONE. GET/POST /api/admin/group-proposals — list pending; approve (creates group) or reject. Audit-logged.
**7.g** — DONE. GET/POST /api/admin/leader-requests — list pending; approve (sets is_admin on membership) or reject. Audit-logged.
**7.h** — DONE. GET/POST /api/admin/access-requests — list pending/endorsed; approve (creates user + professional + org link + phase memberships) or reject. Full lifecycle. Audit-logged.
**7.i** — DONE. GET/POST /api/admin/organisations — list with FHIR Organization shape; create with duplicate name check. Audit-logged.
**7.j** — DONE. GET /api/admin/export-users (CSV download); POST /api/admin/import-users (CSV upload, skip existing, temp password). Audit-logged.
**7.k** — DONE. GET/PUT /api/admin/oath-overview — read/write oath_overview.csv service registry. Audit-logged.
**7.l** — DONE. 31/31 tests passed. Full suite: 162/162. Results: `results/2026-03-18T10-15-00Z_results/phase7_all_tests.txt`

Tests deployed:
- TestListUsers (3): SU sees all users, includes memberships, non-SU forbidden — PASSED
- TestPromoteSU (4): success, wrong password, patient rejected, already SU — PASSED
- TestDeleteUser (3): success, cannot delete self, not found — PASSED
- TestDeleteGroup (2): success, not found — PASSED
- TestAssignGroupAdmin (2): success, not member — PASSED
- TestGroupProposals (3): list, approve creates group, reject — PASSED
- TestLeaderRequests (2): list, approve — PASSED
- TestAccessRequests (3): list, approve creates user, reject — PASSED
- TestOrganisations (3): list FHIR shape, create, duplicate rejected — PASSED
- TestExportImport (3): CSV export, import, skip existing — PASSED
- TestOathOverview (2): read empty, write and read back — PASSED
- TestNonSUForbidden (1): all 16 admin endpoints return 403 for non-SU — PASSED

## Phase 8 — Public / Catalog API

**8.a** — DONE. GET /api/public/organisations — organisation catalog (single source of truth), no auth required, rate limited (60/min).
**8.b** — DONE. GET /api/public/groups — read-only group catalog with type, no auth, rate limited.
**8.c** — DONE. GET /api/public/group-leaders — lists group admins + SU admins with names (for access request form), no auth.
**8.d** — DONE. POST /api/public/access-request — submit professional access request with full validation (email, password min 8, name, role, org, leader). Creates pending AccessRequest record. Rate limited (5/min). Audit-logged.
**8.e** — DONE. 12/12 tests passed. Full suite: 174/174. Results: `results/2026-03-18T10-30-00Z_results/phase8_all_tests.txt`

Tests deployed:
- TestPublicOrganisations (2): lists orgs without auth, has GUIDs — PASSED
- TestPublicGroups (2): lists groups without auth, has GUIDs — PASSED
- TestPublicGroupLeaders (2): includes SU + group admin, has names — PASSED
- TestAccessRequest (6): success (pending status), missing fields, invalid role, nonexistent org, duplicate email, short password — PASSED

## Phase 9 — Frontend (UI)

**9.a** — DONE. Base template (`base.html`) with PDHC Layout Standard adaptation: navbar (context-aware for auth state), flash messages, CSRF token injection, responsive grid, badge system, card components.
**9.b** — DONE. Login page with SSO handshake redirect handling (`next`+`state` params). Auto-redirect for existing sessions. Callback URL allowlist validation.
**9.c** — DONE. Dashboard: shows user profile (type, email, SU badge), professional view (name, role, orgs, groups table with status/admin badges), patient view (personnummer, registry status).
**9.d** — DONE. SU admin page: user table (with CSV export/import), promote SU (password confirmation), delete user, organisations (create), access requests (endorse/approve/reject), group proposals (approve/reject), leader requests (approve/reject), groups (delete), oath overview table.
**9.e** — DONE. Group admin page: pending membership requests table (approve/reject), invite link generator (group dropdown, configurable hours).
**9.f** — DONE. Onboarding pages: register patient (personnummer, org dropdown), join by invite, request group membership, suggest new group, request professional access (full form with role/org/phases/leader dropdowns), change password.
**9.g** — DONE. Docs page: allowlisted document download, path-traversal safe (404 on unknown files).
**9.h** — DONE. Landing page: service cards (login, register, request access), registered services table from `oath_overview.csv`.
**9.i** — DONE. 32/32 tests passed. Full suite: 206/206. Results: `results/2026-03-18T11-00-00Z_results/phase9_all_tests.txt`

Bug fix: `pytest-flask` plugin kept app context alive between test client requests, causing `g`-based session cache to persist — revoked tokens were invisible to subsequent requests. Removed `pytest-flask` from requirements.txt (not used by any test fixture).

Tests deployed:
- TestBaseTemplate (3): landing renders, login has CSRF token, navbar shows login when unauthenticated — PASSED
- TestLoginPage (4): renders, success redirects to dashboard, fail shows error, SSO handshake redirect with state — PASSED
- TestDashboard (4): requires login, professional view, patient view, SU shows admin link — PASSED
- TestSUAdminPage (4): requires SU (non-SU redirected), renders for SU, create organisation, decide access request — PASSED
- TestGroupAdminPage (3): requires group admin, renders for SU, renders for group admin — PASSED
- TestOnboardingPages (9): register patient page, register success, request access page, join requires login, request join renders, suggest group renders, suggest group creates proposal, change password renders, change password success — PASSED
- TestDocsPage (2): renders, path traversal blocked (404) — PASSED
- TestLandingPage (1): renders with service list section — PASSED
- TestLoginFlowEndToEnd (2): full login→dashboard→logout→blocked flow — PASSED

## Phase 10 — FHIR 5 Compliance

**10.a** — DONE. `GET /fhir/metadata` — CapabilityStatement resource (FHIR R5 v5.0.0). Describes all supported resources (Patient, Practitioner, Organization, Group), interactions (read, create, search-type), security (OAuth/JWT), and custom operations (login, me, logout). Content-Type `application/fhir+json`. Blueprint registered in app factory.
**10.b** — DONE. `src/services/fhir_validator.py` — validates FHIR resource payloads using `fhir.resources` R5 library. Supports Patient, Practitioner, Organization, Group, CapabilityStatement. Raises `FHIRValidationError` with details. `src/fhir/schemas.py` — helper functions to convert internal models to FHIR resource shapes (`patient_to_fhir`, `practitioner_to_fhir`, `organization_to_fhir`, `group_to_fhir`). All outputs validated against R5 models.
**10.c** — DONE. `oath_overview.csv` with correct schema: `service_name`, `service_url`, `api_health_url`, `capability_statement_url`, `endpoints_url`, `privilege_level`, `notes`.
**10.d** — DONE. 20/20 tests passed. Full suite: 226/226. Results: `results/2026-03-18T11-30-00Z_results/phase10_all_tests.txt`

Bug fix: `test_admin.py` fixture was deleting `oath_overview.csv` without restoring. Changed to backup/restore pattern so FHIR schema tests find the file.

Tests deployed:
- TestCapabilityStatement (7): endpoint 200, content-type fhir+json, resourceType correct, fhirVersion 5.0.0, has Patient/Practitioner/Organization/Group resources, security OAuth, validates as FHIR R5 — PASSED
- TestFHIRValidator (7): Patient/Practitioner/Organization/Group valid, missing resourceType raises ValueError, unsupported type raises ValueError, invalid Group (missing required fields) raises FHIRValidationError — PASSED
- TestFHIRSchemas (4): patient_to_fhir, practitioner_to_fhir, organization_to_fhir, group_to_fhir all validate against R5 models — PASSED
- TestOathOverviewSchema (2): CSV exists, has correct 7 columns — PASSED

## Phase 11 — Integration & Endpoint Testing

**11.a** — DONE. `tests/conftest.py` updated with comprehensive shared fixtures: `app` (test app with all config), `client`, `seeded_app` (SU admin, professional/group_admin, patient, org, group, with tokens for all roles). Rate limit reset in fixture.
**11.b** — DONE. `scripts/test_endpoints.sh` — bash script testing all API endpoints against the capability statement. Tests: health, FHIR metadata, auth (unauthenticated + authenticated if seed data available), public endpoints, patient endpoints, group endpoints, admin endpoints, frontend pages. Generates timestamped report in `results/`.
**11.c** — DONE. Full test suite: 226/226 passed. Results: `results/2026-03-18T11-45-00Z_results/phase11_all_tests.txt`. Endpoint test script ready for live testing against running instance.

## Phase 12 — Documentation

**12.a** — DONE. `app/docs/mkdocs.yml` — Material theme, Mermaid diagrams, search, syntax highlighting, full nav structure.
**12.b** — DONE. `app/docs/docs/architecture.md` — system context diagram, ER data model (all 12 tables), auth flow sequence, SSO handshake (H1–H4) sequence, access blob schema (patient + professional), decision tree flowchart, middleware stack, port allocation.
**12.c** — DONE. `app/docs/docs/api-reference.md` — all endpoints grouped by blueprint (health, FHIR, auth, patient, groups, admin, public). Each with method, path, auth requirement, request/response examples, error codes. 40+ endpoints documented.
**12.d** — DONE. `app/docs/docs/integration-guide.md` — service registration, SSO handshake implementation (H1–H4 with code), access blob usage, patient/professional authorization pseudocode, `map_action_to_phase()` reference, organisation as single source of truth, `oath_overview.csv` schema, error handling table, new service checklist.
**12.e** — DONE. `app/docs/docs/admin-manual.md` — SU admin operations (user management, CSV export/import, org management, group lifecycle, proposals, leader requests, access request workflow), group admin operations (pending requests, invite links), audit trail.
**12.f** — DONE. `app/docs/docs/deployment-guide.md` — local dev setup (start.sh, .env, DB init, verify), server deployment (Docker, production .env, safe_restart.sh), nginx reverse proxy config, DNS, first-run checklist, backup/restore, log rotation, API key management with rotation procedure, port allocation.
**12.g** — DONE. `app/docs/docs/user-guide.md` — professional workflow (login, dashboard, group membership, invites, admin requests, suggest group, change password), patient workflow (registration, login, dashboard, registry status), access request for new professionals, common tasks.
**12.h** — DONE. `mkdocs build` completed successfully. All 7 pages + index rendered to `app/docs/site/`. Output: index.html, architecture/, api-reference/, integration-guide/, admin-manual/, deployment-guide/, user-guide/, 404.html, sitemap.xml, search/. Full test suite re-run: 226/226 passed.

## Phase 13 — Hardening & Final Check

**13.a** — DONE. OWASP Top 10 review completed:

- **SQL Injection** — MITIGATED: all queries via SQLAlchemy ORM, no raw SQL
- **XSS** — MITIGATED: Jinja2 autoescaping enabled, no `|safe` on user input
- **Open Redirect** — MITIGATED: `next` URL validated against `ALLOWED_CALLBACK_URLS` allowlist
- **Path Traversal** — MITIGATED: docs download uses explicit allowlist of 7 filenames
- **CSRF** — MITIGATED: Flask-WTF on forms, API routes exempt (use Bearer tokens)
- **Rate Limiting** — MITIGATED: login (20/min), public (60/min), access-request (5/min)
- **Broken Auth** — MITIGATED: bcrypt hashing, JWT with jti + revocation table, configurable expiry
- **Security Misconfiguration** — FIXED: added security headers (X-Frame-Options: DENY, X-Content-Type-Options: nosniff, X-XSS-Protection, Referrer-Policy, Strict-Transport-Security), cookie security (SESSION_COOKIE_SECURE, HTTPONLY, SAMESITE=Lax), MAX_CONTENT_LENGTH (16 MB upload limit)

Hardening changes applied to `app/src/app.py`. All 226 tests still passing.

**13.b** — DONE. Final rule compliance check:

| Rule | Status | Verification |
|------|--------|-------------|
| R1 | OK | top_rules.md unchanged |
| R2 | OK | readme.md, progress.md, changed_files.md all present |
| R3 | OK | readme.md uses 1.a–13.b numbering |
| R4 | OK | progress.md tracks all phases with test results |
| R5 | OK | initial_sql_design.txt exists as reference |
| R7 | OK | Dockerfile + docker-compose.yml in app/ |
| R8 | OK | API key rules documented in readme.md |
| R9/R20 | OK | scripts/test_endpoints.sh created |
| R10 | OK | PostgreSQL on localhost:9003 |
| R11 | OK | Results stored in results/<timestamp>_results/ |
| R14 | OK | _obs_gateway_repo not touched |
| R15 | OK | FHIR R5: CapabilityStatement, fhir.resources v8 validator, resource shapes |
| R16 | OK | Ports 9000–9003, start.sh kills and manages all |
| R17 | OK | changed_files.md tracks all edited files |
| R18 | OK | All external refs use UUID4 GUIDs |
| R19 | OK | safe_restart.sh prepared |
| R21 | OK | App in app/ folder with venv, requirements.txt updated |
| R22 | OK | nginx config isolated, deployment guide warns about fragility |
| R23 | OK | .env fully prepared, create_su.py bootstraps SU from .env |

All required files present. 226/226 tests passing. Documentation built. All phases complete.

## 2026-04-29 — External Partners feature (plans/external_partners_plan.md)

Workflow for registering external organisations as authenticated callers
of PDHC, with stable per-partner GUIDs that contract.pdhc references in
FHIR Contract.signer.party.reference. Replaces the broken KEYAUTH_SERVICE_*
service-key UI (which always rendered "No keyed services configured"
because nothing in prod set those env pairs).

**What landed locally** (not yet deployed):

- New ORM models: `ExternalPartner` + `ExternalPartnerAudit` (plan §3).
- New blueprint at `src/routes/partners.py` with 11 endpoints (public
  lookup, internal validate, SU CRUD + rotate / suspend / reactivate /
  revoke / audit / catalogue).
- Admin UI integrated into `su_admin.html` (server-rendered + small JS):
  register form with checkbox-driven scope and service selection from a
  closed catalogue; partners table with status badges and per-row
  lifecycle actions; secret shown exactly once at create / rotate.
- 22 new tests in `tests/test_partners.py`. Full suite green (253 / 253).

**Test results — `pytest`:**

- `tests/test_partners.py` — 22 / 22 passed.
- `tests/test_models.py::TestAllTablesCreated` — updated EXPECTED_TABLES;
  passes.
- Whole repo suite — 253 / 253 passed in ~114 s.

**Open / next steps (per plan §8 rollout):**

1. **Deploy** — pack via `pack_deploy.sh`, operator transfers + extracts
   on miserver:/usr/local/www/sso.pdhc/, runs `python scripts/init_db.py`
   to materialise the two new tables, then `safe_restart.sh`. The
   migration is idempotent (Base.metadata.create_all) and additive — no
   data loss possible.
2. **Step 4** of the plan (extending each PDHC service's request loader
   to recognise `partner:<guid>` sources) is **not** yet done — needs a
   per-service code change in 13 services, gated on the operator
   approving partner #1 in production first. Out of scope for this
   commit.
3. **Step 5** (contract.pdhc reference validation + inline display) —
   not yet done. Self-contained next change in contract.pdhc; also
   gated on at least one production partner existing.
4. **Step 6** (delete the legacy `KEYAUTH_SERVICE_*` config block in
   `src/config.py` + the unused frontend routes that fed the old UI) —
   deferred. Operator should grep prod env on miserver for any
   `KEYAUTH_SERVICE_*=` first; if nothing, safe to remove.

## 2026-04-29 — Ticket #96 closed: external_partner folded into organisations

The morning's External Partners deploy created a separate
`external_partner` table. Per ticket #96 (raised by the operator
mid-afternoon: *"Externa organisations should be 'organisations'!
Otherwise they fall out of contract"*) this broke FHIR Contract
referencing — `Contract.signer.party.reference` is meant to point at
Organisation, not at a custom Partner type.

Refactor landed and deployed:

- `organisations` extended with `is_external` bool + 18 partner-
  specific columns (auth_kind, allowed_scopes, allowed_services,
  client_secret_hash, api_key_hash, status, expires_at,
  last_rotated_at, revoked_at, revoke_reason, country_code,
  org_number, description, contact_name/email/phone, updated_at,
  created_by_user_guid).
- `organisation_audit` (one table) replaces the per-population
  `external_partner_audit`.
- `external_partner` and `external_partner_audit` dropped — no
  production rows lost; only the smoke-test row from the morning's
  deploy was discarded.
- FHIR Contract reference convention now `Organisation/<guid>`
  uniformly. The previous `https://sso.pdhc.se/Partner/<guid>` shape
  is retired.
- API surface kept: `/api/admin/partners/*` endpoints still work,
  filter `Organisation by is_external=true`. JSON responses carry
  both `partner_guid` (legacy alias) and `guid` (canonical) so the
  SU admin page JS continues to work without rewrite.

**Ticket #96 closed** via POST /api/tickets/96/respond at 16:35 with
the full resolution text; status now `completed`.

**Test results — `pytest`:**

- `tests/test_partners.py` — 22 / 22 passed.
- `tests/test_models.py` — passes (EXPECTED_TABLES updated:
  `external_partner*` removed, `organisation_audit` added).
- Whole repo suite — 253 / 253 passed.

**Migration on miserver** (idempotent SQL applied via colima ssh →
docker exec sso_db psql): organisations got the new columns + FK,
organisation_audit created, obsolete tables dropped. gunicorn
restarted via /tmp/sso_restart.sh; /api/health 200 with
`database: connected`.

Pushed to `profingvar/formserviceFHIR` as commit `5065ccb`.

---

# Access-model reform (rollup #396) — progress

The v3-locked reform (2026-07-03, `Paper_sso/Roles for access to patient
data - v3 … DECISIONS LOCKED.docx`) moves SSO from the flat
`organization_ids` + `professional_role` blob to an
`affiliations[]` + zone/role/consent model. Legal correctness (PDL,
Lag 2022:913, GDPR, EHDS) is the governing priority. Numbering follows
the S/D/X/M ticket labels, not the older `1.a` scheme.

## Reconcile (prereq) — commit `13adeb4`

Before building the reform, 17 deployed-but-uncommitted SSO files (from
the #43/#44 era) were reconciled into git — verified sha256-identical
across local / prod-disk / running container, then committed. Git is now
authoritative for sso.pdhc (matters for the deploy runbook's pull path).

## S1–S8 — sso.pdhc (all DONE, committed + pushed, NOT yet deployed)

- **S1 (#397)** — commit `8074987`. Formalized CareOrganisation /
  CareUnit as views on the existing self-referential Organisation table
  (`parent_caregiver_guid`); added `care_hierarchy` service with a
  2-level guard + read-only hygiene scan script. Tests: `test_care_hierarchy.py` (17).
- **S2–S6 (#398–#402)** — commit `63ce3c9`. Role registry (7 seed roles,
  role→phase matrix), ResearchProject registry, Affiliation binding
  (person↔care_unit↔role, replaces UserOrganisation), Option-C runtime
  phase intersection (`granted ∩ (role.permitted ∪ {planning})`), and
  `build_access_blob` rewritten to emit `affiliations[]`,
  `active_affiliation_guid`, `session_phases` **alongside every legacy
  field (dual-emit)**. Backfill script + `test_affiliation_model.py` (30).
- **S7 (#403)** — commit `e935054`. Blob-contract test pinning the
  dual-emit shape; first SSO CI (`.github/workflows/test.yml`).
- **S8 (#410)** — commit `3c30cac`. SU-only registry management:
  `/api/registry` blueprint (Role + ResearchProject CRUD, SU-only writes,
  reads open to any professional; CareOrg/CareUnit read projections) +
  `validate_care_hierarchy` wired into the SU-only org create/edit path.
  `test_registry.py` (15). Affiliation assignment stays SU-only.
- **D2 (#405)** — commit `98d4e05`. Personnummer confinement guard:
  audit confirmed pnr lives only in ips PatientIndex.identifier_value +
  the sso patient-login anchor; the access blob emits it for no user
  type. Regression guard `test_blob_never_leaks_personnummer`.

**Test results:** whole sso suite **328 / 328 pass** (was 313 + 15 S8).
`EXPECTED_TABLES` updated for the 3 reform tables (roles,
research_projects, affiliations).

## Cross-repo reform work

- **D1 (#404)** — ips.pdhc commit `fccb302`. Added `ehds_opt_out`,
  `quality_registry_opt_out`, `consented_research_projects` (JSONB) to
  `PatientIndex` + `primary_care_unit_guids()` helper. Reconciled: the
  other two v3 consents already exist richer — `allow_sharing_in_care` →
  `PatientConsent` (#198), care-units → `PatientClinicAssignment`.
  Idempotent ALTER migration for prod. +3 tests (17/17).
- **X1 (#407)** — canonical access-log tuple + `purpose`/`access_basis`
  closed enums locked in `plans/pdhc_data_shapes.md §5` + wire-alias
  inventory. Per-service adoption rides M0.
- **X2 (#408)** — gateway.pdhc commit `29b0e13`. Reference implementation
  of universal `X-Operator-Session-Id` propagation: reusable
  `outbound_session_headers()` helper on all synchronous onward calls +
  operator-session capture at ingest / replay on the async cdr1 forwarder
  (chain-of-custody across the queue gap). `session_id` = JWT `sid`
  (#191), already live → **no deploy dependency**. +7 tests. #408 left
  open (cross-cutting); per-repo adoption tracked in continuation **#423**.
  **2026-07-08: #408 CLOSED.** Per-repo adoption (#423) deployed to all 7
  repos on miserver (cdr migration `e3f4a5b6c7d8` applied; rollback tags
  `<image>:pre_x2`). Prod e2e passed: one sid (`e2e-x2-408-20260708`)
  captured at gateway ingest → replayed on the real forwarder HTTP hop
  (visible in cdr `ingest_raw.headers_json`) → carried on both cdr
  `cambio_delivery_log` rows. Cambio external hop config-disabled; worker
  replay unit-proven. Side finds: contract prod git reconciled to origin
  (`ccc7f44`, incl. revert of #350's broken in-container 127.0.0.1 bind);
  cdr prod git-ified at `b66c392`; gateway→cdr dev service key → **#440**.
- **S9 (#411)** — **DONE + DEPLOYED 2026-07-09** (commit `82d0e1e`, migration
  `add_user_status.sql` applied, rollback `sso-app:pre_s9`). Guided
  professional sign-on: access-request approval now creates a PENDING
  person (User.status) with a NO-ACCESS blob (`activation_pending`);
  SU-only affiliation CRUD + server-side completeness
  (`activation_service.py`) gate activation (409 + missing list);
  guided panel in su_admin.html renders the server-computed checklist.
  +9 tests, full suite 323/323.
- **M0 (#409)** — consumer-migration tracker: 11 Wave-3 sub-tickets
  **#412–#422** created, ticked into `Paper_sso/repos_to_reform.md`.
  Consumer CODE all committed + green; **the 7-service consumer DEPLOY
  campaign (#428–#434) is now COMPLETE — see "Deploy status — M0 consumer
  campaign" below.** #409 umbrella + #422 (per-reader wiring) stay open.

## Deploy status — DEPLOYED 2026-07-06

S1–S8 went **live on the macmini 2026-07-06** (image `sha256:8f8587c6…`,
rollback target `sha256:9314e021…`). Followed the runbook: backups
(`~/backups/predeploy/sso.pdhc/…2026-07-05T14-26-24Z`), sha-verified prod
source matched baseline `13adeb4` (5/5 modified src files identical; only
stale test files differed), shipped reform src by tar (prod dir is NOT a
git checkout), built new image via `docker-compose` v1 (v2 plugin absent),
ran backfill from a throwaway `docker-compose run --rm app` container
**before** swapping (avoided the missing-table 500 window), then
`up -d app`. Backfill: **7 roles · 9 affiliations · 0 flagged**. Care
hierarchy: 7 vårdgivare · 1 vårdenhet · 0 violations. Post-swap: health
200, clean boot, `/api/registry/*` → 401 (wired), all 10 SSO-dependent
services 200.

Two `init_db()` bugs fixed mid-deploy (backfill + verify_care_hierarchy
scripts called `init_db()` bare; now source `DATABASE_URL` from env) —
commits pushed. ips D1 ALTER applied to `ips-db-1` (3 columns). ips **app**
redeploy DEFERRED — it predates D1 code (`fccb302`); harmless (old ORM
ignores the columns), rides the first D1-consumer work.

Prior status (kept for history): S1–S8 (`13adeb4..3c30cac`) green + pushed. Runbook: `docs/deploy_reform_S1-S8_runbook.md`. Critical
ordering: build image → run backfill from a throwaway `docker compose
run --rm app` container (creates the 3 tables + seeds roles + backfills
affiliations) **while old code still serves** → then swap. Reason:
`affiliation_service.active_affiliations` has no missing-table guard and
prod boot does not run `create_all`, so serving new code before the
tables exist would 500 every professional `/api/auth/me` and break auth
platform-wide. Deploying unblocks X2's per-repo adoption and all of M0.

## Deploy status — M0 consumer campaign DEPLOYED 2026-07-07

All **7 consumer-reform deploys live on the macmini 2026-07-07** (campaign
tickets #428–#434, worked one-by-one, low-blast-first). Every service
stayed healthy throughout; no data loss (SoT cdr1 7063, ips patients 146,
dashboard 7019 all intact); final platform sweep all services 200.

| # | Service | Ticket | Image | Method | Migration |
|---|---------|--------|-------|--------|-----------|
| 1 | rosetta | #428/#417 | `89765f4b` | new release, git app overlay | +1 benign catch-up `b7f2a1c3d901` (measurement_source_url) |
| 2 | plan | #429/#421 | `4b11d124` | `git ff-merge` origin/main | none |
| 3 | dashboard | #430 (#415 zone part) | `8bb94c8c` | release overlay (+pruned 2 dead dupes) | none |
| 4 | cdr1 | #431/#416 | `1f9a1b27` | surgical 2-file (SoT, 7063 rows) | none |
| 5 | request | #432/#419 | `1c2a364d` | `git ff-merge` full catch-up (app+worker) | none |
| 6 | ips | #433/#404 | `cf5b3271` | surgical 3-file (PII service) | none (D1 cols pre-ALTERed) |
| 7 | gateway | #434/#418 | `9ac562bc` | surgical + migration (ingest boundary) | `e9f0a1b2c3d4` operator_session_id |

**Every prod dir was behind git in a different way** (release-symlink /
flat dir / git-checkout / stale-HEAD partial deploy) — none matched the
ticket's naive "code only, no migration" assumption, so each got a full
divergence audit (container-vs-git manifest + novelty scan) before any
mutation. Notable:
- **rosetta** — prod predated the repo; catch-up applied one benign
  additive migration the container had never received.
- **request** — a `git ff-merge` also shipped merged-but-undeployed
  **FHIR R5 conformance** fixes (#377/#381); `/api/v1/metadata` now emits
  `fhirVersion 5.0.0`. Resolved the long-standing "request runs behind".
- **ips** — prod incoherently diverged; a blanket catch-up would have
  activated spärr patient-facing copy still **pending legal sign-off**, so
  deployed the surgical D1+#422 scope only.
- **gateway** — proved all 31 "modified" working-tree files were
  non-novel (matched real commits) before touching the ingest boundary;
  ran the migration from a throwaway container **before** swapping;
  forwarder verified against the new `operator_session_id` column.

Backups (rollback image tags + source tars + gateway full DB dump) in
`~/backups/predeploy/<service>/` on miserver.

## Known follow-ups

- **Reform tails still open:** **#422** per-reader consent wiring
  (rosetta/cdr2–6/analyse call ips `analysis-filter`); **#408/#423**
  per-repo X2 `X-Operator-Session-Id` adoption; **#415** dashboard
  roles-hack removal + EHDS/consent joins (only the zone-swap shipped).
- **Deploy-surfaced cleanups (2026-07-07, non-blocking):** **#435**
  dashboard `test_auth` SSO-revalidation mock; **#436** cdr1 hygiene
  (prune 3 dead analyse-split files + 17 pre-existing `test_fhir_read`
  fails); **#437** ips full git reconcile (**needs legal sign-off on
  spärr copy first**); **#438** gateway git-HEAD reconcile (stale label
  over a proven-non-novel partial deploy).
- Deferred: **D3 (#406)** spärr inre/yttre reconcile (blocked on ips-team
  confirmation); **S9 (#411)** guided sign-on UI.
- **#424** (gateway's 10 pre-existing broken tests) — **RESOLVED** during
  gateway deploy prep; suite now green, fixes rode along in #434.

## 2026-08-05 — SU admin-page audit + doc reform-sync (phase B)

Audit request: "Is the SU admin page correctly reflecting SSO capabilities?
Is the local manual/documentation up to date?" Findings (read-only, Rule 12/19):

Admin page (su_admin.html) — mostly faithful, but:
1. Orphaned **Service Key Management** JS (lines ~759–848) drives km-* IDs that
   no longer exist in the body → live TypeError on every load; the backend
   endpoints (/api/admin/service-keys[/…/generate]) + KEYED_SERVICES still exist.
   Restore-vs-remove decision → phase A.
2. **Care hierarchy (S8 #410)** not surfaced: create-org form is name-only, no
   parent/internal-external column, though the API supports parent_caregiver_guid
   + is_external and affiliations REQUIRE an internal care unit.
3. **Role/Research-Project registries** have no management UI (panel only
   consumes them as dropdowns).
- external-partners.md was orphaned from nav + download allowlist + admin card.

Phase B done (docs, local only — operator deploys):
- admin-manual.md: corrected the WRONG "Approved → memberships for all requested
  phases (auto-approved)" text to the S9 reality (pending user, zero access,
  SU assigns affiliation+phase then activates); added sections for Affiliations
  & Guided Activation (S9), Care Hierarchy (S8), Role/Research-Project registries,
  External Partners; fixed stale group-type enum → free-form category.
- SSO_Service_Functions_SV.md: added "Åtkomstmodell-reformen (S1–S9)" section;
  updated professional-attribute + SU-functions lists.
- mkdocs.yml: External Partners added to nav; `mkdocs build` regenerated site
  (exit 0; external-partners + admin-manual pages present).
- NOTE: this is LOCAL only. Operator must deploy the rebuilt docs to the server
  per Rule 12/19 (site/ + source .md). Also note progress "Known follow-ups"
  above lists S9 (#411) as deferred — it is in fact DONE + deployed (git log).

Phase A (code/UI: su_admin.html service-key card, care-hierarchy org fields,
registry admin section, ALLOWED_DOCS + docs card wiring) — NEXT.

## 2026-08-05 — SU admin-page reform-sync (phase A) DONE

Closed the 3 gaps + 1 broken remnant found in the phase-B audit:
1. **Service Key Management** — RESTORED the HTML card (matched to the orphaned
   km-* JS + live /api/admin/service-keys endpoints). No more load-time TypeError.
2. **Care hierarchy (S8 #410)** — org list now carries kind/parent; create-org
   form takes an optional parent vårdgivare (validated); NEW /admin/set-org-parent
   inline re-parent per internal org; table shows Vårdgivare/Vårdenhet/External.
3. **Registries** — NEW Roles + Research-Projects CRUD card (create/list/delete)
   against /api/registry/*, with permitted-phase checkboxes on roles.
4. external-partners.md wired into ALLOWED_DOCS + the docs card (nav done in B).

Validation: py_compile + jinja parse clean; test_admin (32) + registry/frontend/
care-hierarchy/affiliation (79) + full test_frontend (36, incl. 2 new hierarchy
tests) all green. LOCAL only — operator deploys su_admin.html + frontend.py per
Rule 12/19 (graceful restart of sso_app; note the sso safe_restart Colima-forward
gotcha in memory before restarting).

Deferred/notes: role EDIT (PUT) not surfaced in the registry card (create+delete
only); affiliation is_admin flag still not sent by the S9 form; org is_external
toggle stays API/Partners-panel only (create-org here is internal-only by design).

## Ticket #707 — `require_organisation` removed, and why it must not come back (2026-09-29)

(Committed as "#709" — the ticket was created afterwards and came back as
#707. See ~/T7_sidewinder/docs/wiring_triage_2026-09-29.md for the mapping.)

The #704 triage found a complete auth decorator in
`app/src/middleware/auth_middleware.py` — "a professional must hold at least
one organisation membership, SU admins bypass" — applied to no route. It was
written before 2026-03-24, deployed uncommitted, and swept into git by the
#365 reconciliation (13adeb4), so there is no ticket and no author's note
recording what it was for.

The triage asked whether a rule somebody intended was going unenforced.
**It is the other way round: applying it would have been a bug.**

The only routes it fits are the four `@require_professional` ones in
`routes/groups.py`, and every one of them is how a professional *obtains* a
membership — `GET /api/groups`, `request-membership`, `request-admin`,
`join-by-invite`. Gating those on already holding one is a permanent lockout
for every new professional. `tests/test_groups.py::TestRequestMembership`
already proves it: its "regular" professional holds no `UserOrganisation`
row and must get a 201.

The rule *is* enforced, just not at this door. The access blob carries
`organization_ids` and every consuming service scopes its rows to it
(Rule 24), so an org-less professional sees nothing anywhere. The SU console
shows the state directly — a red "No org" badge beside a "+ Org" control in
`templates/su_admin.html` — because the intended answer is an administrator
assigning an organisation, not a 403 at the user.

**Changed.** The decorator is gone; a comment block stands in its place
carrying the whole argument, so the next person to notice the gap reads the
reasoning instead of re-adding it. Four tests
(`TestOrgLessProfessionalKeepsTheRoute`) fail if it returns, including one
that asserts the attribute no longer exists.

333 tests pass.

**Not deployed.** This removes dead code and changes no behaviour, and
rebuilding `sso_app` is the highest-blast-radius restart on the platform —
every service validates every request against it. It rides along with the
next sso deploy. Prod disk is therefore one no-op behind git for
`auth_middleware.py`; that is git-ahead, not the prod-ahead divergence
pattern that silently reverts on a pull.

## #704 item 1 — `revoked_tokens` had a pruner and no caller (2026-09-29)

`POST /api/auth/logout` inserts a row into `revoked_tokens` on every logout.
`prune_expired_tokens` in `src/services/jwt_service.py` was written and
correct; nothing called it. The table had been growing since March.

It matters more than the row count suggests: `validate_token` queries this
table **on every request on every service**. CLAUDE.md §11 forbids caching
the access blob, so each service revalidates against sso.pdhc every time,
and each revalidation reads here. This is the platform's hot path.

**A row is dead the moment it expires.** It exists to make `validate_token`
reject a token *before* the token's own `exp`. Once `exp` has passed,
`decode_token` raises `TokenExpiredError` before the revocation check is
reached, so the row can no longer change any answer. No grace margin is
needed either: `expires_at` is written from the token's own `exp` and
compared against the same process's clock, so no database clock is involved
and there is nothing to skew. `test_a_pruned_row_cannot_un_revoke_a_live_token`
pins that argument.

**Added**
- `count_expired_tokens(session)` — read-only, so `--dry-run` is safe to
  point at production.
- `prune_expired_tokens` now returns the number deleted (and still does not
  commit — the caller owns the transaction, matching `revoke_token`).
- `app/scripts/prune_tokens.py` — the caller, in the same standalone-script
  idiom as `create_su.py` and `init_db.py`, since sso.pdhc has no Flask CLI.

Verified against a scratch sqlite database: 3 expired + 1 live →
`--dry-run` reported "4 rows, 3 expired (1 still doing work). Nothing
changed", the real run pruned 3 and kept the live one, and a second run was
a clean no-op.

### Still to do — operator steps, not done here

1. **Deploy.** This ships with the #707 `require_organisation` removal; both
   are waiting on one sso rebuild. Nothing prunes until the script is on the
   server.
2. **First run, dry.** Read the count before deleting anything — the table
   has never been pruned, so the number is the interesting part:
   ```
   docker exec sso_app python scripts/prune_tokens.py --dry-run
   ```
3. **Cron**, alongside request.pdhc's consent reconciler. Daily is ample;
   the rows are small and nothing depends on prompt removal:
   ```
   7 4 * * *  /opt/homebrew/bin/docker exec sso_app python scripts/prune_tokens.py >> ~/logs/prune_tokens.log 2>&1
   ```
   Not installed — a cron entry is a standing change to server state and is
   the operator's call (§15).
