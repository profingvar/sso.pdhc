# sso.pdhc — SSO/OAuth Service Deployment Plan

## Overview

Central Flask-based SSO/OAuth server with PostgreSQL. Issues JWT tokens, manages users (patients & professionals), groups, memberships, organisations. Delivers an "access blob" to downstream FHIR-compliant microservices. FHIR 5 compliant from day one (models, API responses, validation). Docker-contained. Ports 9000–9003, DB on localhost:9003. Target domain: `sso.pdhc.se`.

**Reference documents**: `SSO_Service_Functions_SV.md` is a condensed extraction from the old prototype — use as guidance, not as master truth. `top_rules.md` is authoritative for project rules.

## Folder Structure

```
claude1/                        ← repo root (keep clean)
├── top_rules.md                ← project rules (DO NOT MODIFY)
├── CLAUDE.md                   ← claude instructions (references top_rules.md)
├── SSO_Service_Functions_SV.md ← service specification (Swedish, reference only)
├── readme.md                   ← this file (deployment plan)
├── progress.md                 ← progress tracking per step
├── newtask.txt                 ← debugging focus (created when needed)
├── changed_files.md            ← tracks all edited files with full path
├── open_questions.md           ← CTO review decisions (resolved)
├── initial_sql_design.txt      ← DB reference design (created in step 2.a)
├── .gitignore
├── start.sh                    ← single entry point (Rule 16)
├── results/                    ← test results (ISO-8601 UTC timestamped)
└── app/                        ← application root (self-contained)
    ├── venv/
    ├── requirements.txt
    ├── .env                    ← secrets, never committed
    ├── .env.example            ← committed template
    ├── Dockerfile
    ├── docker-compose.yml
    ├── safe_restart.sh         ← server-side restart script
    ├── src/
    │   ├── __init__.py
    │   ├── app.py              ← Flask factory
    │   ├── config.py
    │   ├── db.py               ← SQLAlchemy + connection helpers
    │   ├── models/
    │   │   ├── __init__.py
    │   │   ├── user.py
    │   │   ├── patient.py          ← includes organisation_guid FK
    │   │   ├── professional.py     ← includes professional_role enum
    │   │   ├── organisation.py
    │   │   ├── user_organisation.py ← many-to-many junction
    │   │   ├── group.py
    │   │   ├── membership.py
    │   │   ├── group_proposal.py
    │   │   ├── leader_request.py
    │   │   ├── access_request.py
    │   │   ├── invite.py
    │   │   └── revoked_token.py
    │   ├── routes/
    │   │   ├── __init__.py
    │   │   ├── auth.py
    │   │   ├── patient.py
    │   │   ├── professional.py
    │   │   ├── groups.py
    │   │   ├── admin.py
    │   │   ├── public.py
    │   │   └── docs.py
    │   ├── services/
    │   │   ├── __init__.py
    │   │   ├── jwt_service.py
    │   │   ├── auth_service.py
    │   │   ├── fhir_validator.py
    │   │   └── audit_log.py        ← file-based structured audit logging
    │   ├── fhir/
    │   │   ├── __init__.py
    │   │   ├── capability_statement.py
    │   │   └── schemas.py
    │   ├── middleware/
    │   │   ├── __init__.py
    │   │   ├── auth_middleware.py
    │   │   ├── csrf.py             ← CSRF protection (active from Phase 3)
    │   │   ├── cors.py             ← CORS for cross-origin SSO
    │   │   └── rate_limit.py
    │   └── templates/
    │       ├── base.html
    │       ├── login.html
    │       ├── dashboard.html
    │       ├── su_admin.html
    │       ├── group_admin.html
    │       ├── join.html
    │       ├── request_join.html
    │       ├── register_patient.html
    │       ├── suggest_group.html
    │       ├── request_access.html
    │       ├── change_password.html
    │       └── docs.html
    ├── tests/
    │   ├── conftest.py
    │   ├── test_models.py
    │   ├── test_auth.py
    │   ├── test_patient.py
    │   ├── test_professional.py
    │   ├── test_groups.py
    │   ├── test_admin.py
    │   ├── test_public.py
    │   └── test_fhir.py
    ├── docs/                       ← MkDocs source (Material theme)
    │   ├── mkdocs.yml              ← MkDocs config
    │   └── docs/
    │       ├── index.md            ← Landing / overview
    │       ├── architecture.md     ← Data model, auth flow, SSO handshake sequence
    │       ├── api-reference.md    ← All endpoints, request/response, error codes
    │       ├── integration-guide.md ← Downstream services: token, blob, decision tree
    │       ├── admin-manual.md     ← SU admin operations guide
    │       ├── deployment-guide.md ← Macmini setup, Docker, nginx, DNS, first-run
    │       ├── user-guide.md       ← Professional/patient UI walkthrough
    │       └── assets/
    │           └── diagrams/       ← Architecture diagrams, flow charts
    └── scripts/
        ├── init_db.py
        ├── create_su.py
        └── test_endpoints.sh
```

---

## API Key Rules

All API keys and secrets are stored in `.env` (never committed).

- **Storage**: `.env` file, loaded via `python-dotenv`. On server: environment variables injected by systemd/docker.
- **Rotation**: Keys are rotated by updating `.env` and restarting the service. A `KEY_CREATED_AT` timestamp is stored per key.
- **Expiry**: `SESSION_EXPIRY_HOURS` controls JWT lifetime (default 24h). Service client secrets do not auto-expire but should be rotated every 90 days.
- **Revocation**: JWT revocation is handled by token expiry. For immediate revocation, a `revoked_tokens` table (GUID + exp) is checked on every `/api/auth/me` call. Entries are pruned after expiry.
- **Bootstrap SU**: On first deployment, `scripts/create_su.py` creates the superuser from `.env` values (`BOOTSTRAP_SU_EMAIL`, `BOOTSTRAP_SU_PASSWORD`). Must be run once after DB init.
- **Service credentials**: `SSO_CLIENT_ID_<NAME>` / `SSO_CLIENT_SECRET_<NAME>` pairs in `.env`. Rotated per client.

---

## Deployment Plan

**Testing policy (Rule 4):** Each phase includes its own tests. Tests must pass before advancing to the next phase. Results are recorded in `progress.md`. Phase 11 is reserved for the integration endpoint test script only.

**FHIR policy (Rule 15):** FHIR 5 compliance is built in from Phase 2 onward — models carry FHIR resource type annotations, API responses use FHIR-shaped payloads where applicable. Phase 10 adds only the CapabilityStatement and the formal validator.

---

### Phase 1 — Project Foundation

**1.a** Create project directory structure under `app/`, initialise virtual environment, create `requirements.txt` with all dependencies (Flask, SQLAlchemy, psycopg2-binary, python-dotenv, PyJWT, bcrypt, flask-wtf, flask-cors, fhir.resources, gunicorn, pytest, mkdocs-material). Create `.gitignore` (exclude `.env`, `venv/`, `__pycache__/`, `*.pyc`, `.DS_Store`, `results/`, `site/`).

**1.b** Create `.env.example` with all required variables documented. Create actual `.env` (not committed). Variables include: `SECRET_KEY`, `DATABASE_URL`, `SESSION_EXPIRY_HOURS`, `BOOTSTRAP_SU_EMAIL`, `BOOTSTRAP_SU_PASSWORD`, `ALLOWED_ORIGINS`, `ALLOWED_CALLBACK_URLS`, `SSO_CLIENT_ID_*`, `SSO_CLIENT_SECRET_*`, `KEY_CREATED_AT`, `FLASK_ENV`, `LOG_DIR`.

**1.c** Create `Dockerfile` and `docker-compose.yml` under `app/`. Service ports: app on 9000, DB on 9003. Docker network internal. Health-check endpoints included.

**1.d** Create `./start.sh` at repo root (Rule 16): kills ports 9000–9003, checks Docker is running, activates venv, starts DB container, starts app; Ctrl+C gracefully shuts down all and deactivates venv. Create `app/safe_restart.sh` for server-side restarts.

**1.e** Tests: verify venv activates, requirements install, Docker compose starts DB, `start.sh` lifecycle works.

---

### Phase 2 — Database Design

**2.a** Write `initial_sql_design.txt`: reference SQL schema for all tables (users, patients, professionals, organisations, user_organisations, groups, memberships, group_proposals, leader_requests, access_requests, invites, revoked_tokens). FHIR 5 aligned identifiers. Each table that maps to a FHIR resource includes a `fhir_resource_type` annotation.

**2.b** Implement SQLAlchemy models in `src/models/`. All external references use GUID (UUID4). Internal PK is integer. Models:
- `user` — email, password_hash (bcrypt), user_type (patient|professional), is_su_admin, guid
- `patient` — personnummer (12 digits), organisation_guid FK, guid. FHIR: Patient
- `professional` — professional_role enum (doctor|nurse|other), guid. FHIR: Practitioner
- `organisation` — name, guid. FHIR: Organization. Single source of truth for all organisations across services.
- `user_organisation` — many-to-many junction: user_guid ↔ organisation_guid
- `group` — name, group_type enum (planning|request|provider|analysis), guid. FHIR: Group
- `membership` — user_guid, group_guid, status (pending|approved|rejected), is_admin, decided_by_guid
- `group_proposal` — proposed name, group_type, requested_by_guid, status
- `leader_request` — user_guid, group_guid, status, decided_by_guid
- `access_request` — email, password_hash, personal data, organisation_guid, phases, chosen_leader_guid, status (pending|endorsed|approved|rejected)
- `invite` — group_guid, token, expires_at, created_by_guid
- `revoked_token` — token_guid, expires_at

**2.c** Create `scripts/init_db.py`: drops and recreates all tables. Safe to run on fresh DB only. Destructive — must only run once per environment.

**2.d** Create `scripts/create_su.py`: bootstrap superuser from `.env`. Idempotent (skips if SU email already exists).

**2.e** Tests: model creation, GUID generation, enum constraints, FK relationships, user_organisation junction, init_db script, create_su idempotency.

---

### Phase 3 — Core Application

**3.a** Flask app factory (`src/app.py`): registers blueprints, initialises DB, loads config, sets up middleware (CSRF, CORS, rate limiting).

**3.b** `src/config.py`: loads `.env`, validates required vars on startup, exposes typed config object.

**3.c** `src/db.py`: SQLAlchemy engine + session factory. Cursor context that commits per request, rolls back on error.

**3.d** `src/services/jwt_service.py`: issue, decode, validate JWT (HS256). Check revoked_tokens table. Raise typed exceptions on expired/invalid/revoked.

**3.e** `src/middleware/auth_middleware.py`: `@require_auth` decorator (validates Bearer token), `@require_su`, `@require_professional`, `@require_patient`, `@require_group_admin`. All use GUID from token.

**3.f** `src/middleware/csrf.py`: CSRF protection using Flask-WTF. Active on all form POST endpoints.

**3.g** `src/middleware/cors.py`: CORS configuration. Allows origins from `ALLOWED_ORIGINS` in `.env`. Required for cross-origin SSO handshake.

**3.h** `src/middleware/rate_limit.py`: in-memory rate limiting for public and login endpoints.

**3.i** `src/services/audit_log.py`: file-based structured audit logging. Logs: login attempts (success/fail), admin actions, access decisions, token revocations. Writes to `LOG_DIR` with daily rotation.

**3.j** `GET /api/health` — returns service status, DB connectivity, uptime. Used by Docker health-check and reverse proxy.

**3.k** Tests: app factory starts, config validation, JWT issue/decode/expire/revoke, auth decorators block/allow correctly, CSRF rejects without token, CORS headers present, rate limiter triggers, audit log writes, health endpoint responds.

---

### Phase 4 — Authentication API

**4.a** `POST /api/auth/login` — email + password (bcrypt verify) → JWT. Supports `next` + `state` for SSO handshake (redirect with `?token=`). Validates `next` against allowlist. **Auto-redirect**: if user arrives at login page with `next` param and already has a valid session (cookie/token), skip the login form and redirect back immediately with a fresh token. Audit-logged.

**4.b** `GET /api/auth/me` — Bearer token → access blob. FHIR-shaped response including:
- `user_guid`, `email`, `user_type`, `is_su_admin`
- Patient: `patient_guid`, `organisation_guid`, `in_registry`, `registries`
- Professional: `professional_guid`, `professional_role`, `organization_ids: [...]`, `groups: [{group_guid, group_type, status, is_admin}]`, `effective_phases: [...]`

**4.c** `GET /api/auth/me/service` — same as /me but requires `X-SSO-Client-Id` + `X-SSO-Client-Secret` headers. Audit-logged.

**4.d** `POST /api/auth/logout` — adds token GUID to revoked_tokens. Audit-logged.

**4.e** `POST /api/auth/change-password` — authenticated user changes own password. Requires current password, minimum 8 chars for new. Audit-logged.

**4.f** Tests: login success/fail, JWT content, SSO handshake redirect, auto-redirect with existing session, `next` allowlist rejection, /me response shape for patient and professional, /me/service with valid/invalid credentials, logout + token revoked, change-password validation, audit log entries.

---

### Phase 5 — Patient API

**5.a** `POST /api/patient/register` — self-enrolment: creates user (type=patient) + patient row with personnummer (12 digits) and organisation_guid. Dev-mode only flag. FHIR Patient resource shape on response.

**5.b** `GET /api/patient/registry-status` — authenticated patient: returns `in_registry`, list of registries from IPS. Only own data (patient_guid match enforced).

**5.c** Tests: register creates user + patient, personnummer validation (12 digits), duplicate rejection, registry-status returns correct data, access control (patient A cannot see patient B), FHIR response shape.

---

### Phase 6 — Professional & Group API

**6.a** `GET /api/groups` — list own approved groups. FHIR Group resource shape.

**6.b** `POST /api/groups/request-membership` — professional requests membership in group (status=pending). Audit-logged.

**6.c** `POST /api/groups/request-admin` — professional requests group admin role (creates leader_request). Audit-logged.

**6.d** `GET /api/groups/admin/pending` — group admin: list pending membership requests for own group.

**6.e** `POST /api/groups/admin/decide` — group admin: approve or reject pending membership. Audit-logged.

**6.f** `POST /api/groups/admin/invite` — group admin or SU: create time-limited invite token.

**6.g** `POST /api/groups/join-by-invite` — professional: redeem invite token → pending membership.

**6.h** Tests: list groups (only approved), request membership (creates pending), request admin (creates leader_request), admin sees pending, approve/reject changes status, invite token generation + expiry, join-by-invite flow end-to-end, access control (non-admin cannot decide), FHIR response shapes.

---

### Phase 7 — SU Admin API

**7.a** `GET /api/admin/users` — list all users/professionals with full membership detail. SU only.

**7.b** `POST /api/admin/promote-su` — promote professional to SU admin (requires caller password confirmation). Audit-logged.

**7.c** `DELETE /api/admin/users/<user_guid>` — delete user with cascade null on decided_by refs. Audit-logged.

**7.d** `DELETE /api/admin/groups/<group_guid>` — delete group with cascade. Audit-logged.

**7.e** `POST /api/admin/assign-group-admin` — set user as admin in group. Audit-logged.

**7.f** `GET/POST /api/admin/group-proposals` — list pending group proposals; approve (creates group) or reject. Audit-logged.

**7.g** `GET/POST /api/admin/leader-requests` — list and decide group leader requests. Audit-logged.

**7.h** `GET/POST /api/admin/access-requests` — list, endorse, approve, reject access requests. Audit-logged.

**7.i** `GET/POST /api/admin/organisations` — list and create organisations. Organisation registry is the single source of truth for all services. FHIR Organization resource shape. Audit-logged.

**7.j** `GET /api/admin/export-users` — export users as CSV. `POST /api/admin/import-users` — import users from CSV. SU only.

**7.k** `GET/PUT /api/admin/oath-overview` — read and update `oath_overview.csv` (service registry). SU only. Audit-logged.

**7.l** Tests: all admin endpoints require SU, user list complete, promote-su requires password, delete user cascades correctly, delete group cascades, assign-group-admin, group proposal approve/reject, leader request approve/reject, access request full lifecycle, organisation CRUD, CSV export/import round-trip, oath-overview read/write, non-SU gets 403.

---

### Phase 8 — Public / Catalog API

**8.a** `GET /api/public/organisations` — list organisation names (for dropdowns). Rate limited. This is the **single source of truth** for organisations — downstream services consume this endpoint.

**8.b** `GET /api/public/groups` — read-only group catalog. Rate limited.

**8.c** `GET /api/public/group-leaders` — list group leaders + SU admins (for access request form).

**8.d** `POST /api/public/access-request` — submit access request (email, password min 8 chars, personal data, org, phases, chosen leader). Rate limited. Audit-logged.

**8.e** Tests: all public endpoints return data without auth, rate limiting triggers after threshold, access-request creates record with correct status, organisation list matches admin-created orgs, invalid access-request rejected.

---

### Phase 9 — Frontend (UI)

**9.a** Base template: navbar, flash messages, CSRF token injection (uses csrf.py from Phase 3).

**9.b** Login page + SSO handshake redirect handling. Auto-redirect for existing sessions.

**9.c** Dashboard (post-login landing): shows user type, groups, phases.

**9.d** SU admin page: user table (exportable CSV, importable CSV), `oath_overview.csv` viewer/editor (uses 7.k API), promote SU, group proposals, leader requests, access requests, organisations. Professional role shown as enum value.

**9.e** Group admin page: pending requests list, approve/reject, invite link generator.

**9.f** Onboarding pages: register patient, join group, request join, suggest group, request access, change password. Dropdowns for organisation (from 8.a) and professional role (enum: doctor/nurse/other).

**9.g** Docs page: static allowlisted document download (path-traversal safe).

**9.h** Landing/service list page: lists all registered downstream services from `oath_overview.csv`.

**9.i** Tests: all pages render without error, CSRF token present in forms, login flow end-to-end, SSO handshake redirect works, dashboard shows correct data per role, admin pages restricted to SU, group admin pages restricted to group admin.

---

### Phase 10 — FHIR 5 Compliance (CapabilityStatement & Validator)

Note: FHIR resource annotations and response shapes are already built into Phases 2–8. This phase adds formal compliance tooling.

**10.a** FHIR 5 Capability Statement: `GET /fhir/metadata` — returns CapabilityStatement resource describing all supported FHIR interactions.

**10.b** `src/services/fhir_validator.py`: validates FHIR resource payloads on inbound/outbound. Uses `fhir.resources` library (FHIR R5). Integrated as middleware on relevant endpoints.

**10.c** `oath_overview.csv` schema: `service_name`, `service_url`, `api_health_url`, `capability_statement_url`, `endpoints_url`, `privilege_level`, `notes`.

**10.d** Tests: capability statement valid FHIR R5, all API responses pass FHIR validation, oath_overview.csv schema correct.

---

### Phase 11 — Integration & Endpoint Testing

Note: Unit/integration tests are already written per phase. This phase produces the comprehensive endpoint test script and consolidates results.

**11.a** `tests/conftest.py`: ensure pytest fixtures cover all roles (patient, professional, group_admin, su_admin) and test DB lifecycle.

**11.b** `scripts/test_endpoints.sh`: bash script testing **all** API endpoints against the capability statement. Generates report in `results/<timestamp>_results/`.

**11.c** Full test suite run: execute all pytest tests + endpoint script. All must pass. Results archived in `results/`.

---

### Phase 12 — Documentation

Built with MkDocs (Material theme). Source in `app/docs/`, builds to polished static HTML. Can be served from the app itself or deployed separately. Diagrams use Mermaid (built into Material theme).

**12.a** `docs/mkdocs.yml` — MkDocs configuration: Material theme, navigation structure, Mermaid diagram support, search, syntax highlighting.

**12.b** **Architecture Overview** (`architecture.md`):
- System context diagram: SSO service, downstream microservices, reverse proxy, database
- Data model diagram (all tables, relationships, GUID references)
- Auth flow: login → JWT → Bearer → access blob
- SSO handshake sequence diagram (H1–H4: `next` → login → redirect with `token` → `/me` lookup)
- Decision tree: root → patient flow (ownership, registry) → professional flow (org scope, phase, admin)
- Access blob schema with annotated example

**12.c** **API Reference** (`api-reference.md`):
- Every endpoint grouped by blueprint (auth, patient, groups, admin, public, fhir)
- For each: method, path, auth requirement, request body/params, response body (with example JSON), error codes
- FHIR resource shapes noted where applicable
- Service-to-service auth headers documented

**12.d** **Integration Guide** (`integration-guide.md`):
- How downstream services connect to SSO: validate token via `/api/auth/me`, consume access blob
- Decision tree implementation (pseudocode for patient flow and professional flow)
- Organisation endpoint as single source of truth
- `map_action_to_phase()` reference implementation
- `oath_overview.csv` schema and how to register a new service
- Error handling: `token_expired`, `token_revoked`, `insufficient_privileges`
- Example: adding a new microservice under SSO step by step

**12.e** **Admin Manual** (`admin-manual.md`):
- SU admin: user management (list, promote, delete), group lifecycle (proposals, approve/reject, delete), leader requests, access request workflow (pending → endorsed → approved), organisation management, oath_overview editing, CSV export/import
- Group admin: pending membership requests, approve/reject, invite link creation
- Screenshots/annotated UI walkthrough for each operation

**12.f** **Deployment Guide** (`deployment-guide.md`):
- Prerequisites: Docker, Python 3.11+, PostgreSQL
- Local development setup: `start.sh`, `.env` configuration, DB init, bootstrap SU
- Server deployment (macmini): Docker compose, `.env` for production, `safe_restart.sh`
- Reverse proxy config snippet (nginx) for `sso.pdhc.se` — path-prefix isolated to avoid collision with existing services. Config is reference only — operator sets it manually. DNS configured before deployment.
- First-run checklist: DB init → create SU → verify health endpoint → test login
- Backup and restore procedures
- Log rotation and audit log location

**12.g** **User Guide** (`user-guide.md`):
- Professional: login, dashboard, request group membership, join by invite, change password
- Patient: registration, login with personnummer, view registry status
- Access request flow for new professionals (public onboarding)

**12.h** Build docs: `mkdocs build` produces `site/` with static HTML. Verify all pages render, links work, diagrams display. Optionally serve via `GET /docs/` on the app (static file mount).

---

### Phase 13 — Hardening & Final Check

**13.a** Review all endpoints for OWASP top 10: SQL injection (parameterised queries), XSS (Jinja2 autoescaping), open redirect (allowlist), path traversal (docs endpoint), rate limiting, CSRF on forms.

**13.b** Final check: all rules in top_rules.md verified, all required files present, all tests passing, progress.md up to date, `.env` fully prepared for first deployment, bootstrap SU works via `create_su.py`, documentation built and complete.

---

## Ports

| Service       | Port  |
|---------------|-------|
| SSO App       | 9000  |
| Reserved      | 9001  |
| Reserved      | 9002  |
| PostgreSQL DB | 9003  |


## Clinical-context schema role (per #294 RFC decision B1, 2026-06-28)

In the PDHC platform's canonical clinical-context schema, sso.pdhc owns
**org identity** (the org_guid values that appear in
`organization_ids` of the access blob) — not the
**requesting-vs-provider role**. The role distinction is contractual:
contract.pdhc returns `requesting_org_guid` and `provider_org_guids`
on `/internal/contract/<guid>/scope`, derived from the FHIR
Contract.party roles.

Consumers that need the role split (gateway, cdr, dashboard) should
query contract.pdhc, not sso.

See `~/T7_sidewinder/plans/pdhc_clinical_context_harmonisation_plan.md`
§3 + `clinical_context_audit_2026-06-28.md` §4 decision B.

