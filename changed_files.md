# Changed Files Tracking

All edited files listed with full path (Rule 17).

---

| Date       | File                          | Action  |
|------------|-------------------------------|---------|
| 2026-03-18 | claude1/CLAUDE.md             | Created |
| 2026-03-18 | claude1/readme.md             | Updated — fixed folder structure, references, missing models, start.sh location |
| 2026-03-18 | claude1/progress.md           | Created |
| 2026-03-18 | claude1/changed_files.md      | Created |
| 2026-03-18 | claude1/open_questions.md     | Created — CTO review questions, answered by operator |
| 2026-03-18 | claude1/readme.md             | Rewritten — incorporated all 14 CTO review decisions |
| 2026-03-18 | claude1/progress.md           | Updated — new step numbering to match revised plan |
| 2026-03-18 | claude1/readme.md             | Updated — added docs/ folder, Phase 12 documentation (7 docs), Phase 13 hardening |
| 2026-03-18 | claude1/progress.md           | Updated — Phase 12/13 steps added |
| 2026-03-18 | claude1/.gitignore            | Created |
| 2026-03-18 | claude1/app/requirements.txt  | Created |
| 2026-03-18 | claude1/app/.env.example      | Created |
| 2026-03-18 | claude1/app/.env              | Created (not committed) |
| 2026-03-18 | claude1/app/Dockerfile        | Created |
| 2026-03-18 | claude1/app/docker-compose.yml | Created |
| 2026-03-18 | claude1/start.sh              | Created |
| 2026-03-18 | claude1/app/safe_restart.sh   | Created |
| 2026-03-18 | claude1/app/src/__init__.py   | Created |
| 2026-03-18 | claude1/app/src/app.py        | Created (stub for Phase 1) |
| 2026-03-18 | claude1/app/src/models/__init__.py | Created |
| 2026-03-18 | claude1/app/src/routes/__init__.py | Created |
| 2026-03-18 | claude1/app/src/services/__init__.py | Created |
| 2026-03-18 | claude1/app/src/fhir/__init__.py | Created |
| 2026-03-18 | claude1/app/src/middleware/__init__.py | Created |
| 2026-03-18 | claude1/app/tests/__init__.py | Created |
| 2026-03-18 | claude1/app/tests/conftest.py | Created |
| 2026-03-18 | claude1/app/tests/test_foundation.py | Created |
| 2026-03-18 | claude1/progress.md           | Updated — Phase 1 complete |
| 2026-03-18 | claude1/initial_sql_design.txt | Created |
| 2026-03-18 | claude1/app/src/db.py         | Updated — session management, request scope, context manager |
| 2026-03-18 | claude1/app/src/models/user.py | Created |
| 2026-03-18 | claude1/app/src/models/patient.py | Created |
| 2026-03-18 | claude1/app/src/models/professional.py | Created |
| 2026-03-18 | claude1/app/src/models/organisation.py | Created |
| 2026-03-18 | claude1/app/src/models/user_organisation.py | Created |
| 2026-03-18 | claude1/app/src/models/group.py | Created |
| 2026-03-18 | claude1/app/src/models/membership.py | Created |
| 2026-03-18 | claude1/app/src/models/group_proposal.py | Created |
| 2026-03-18 | claude1/app/src/models/leader_request.py | Created |
| 2026-03-18 | claude1/app/src/models/access_request.py | Created |
| 2026-03-18 | claude1/app/src/models/invite.py | Created |
| 2026-03-18 | claude1/app/src/models/revoked_token.py | Created |
| 2026-03-18 | claude1/app/src/models/__init__.py | Updated — register all models |
| 2026-03-18 | claude1/app/scripts/init_db.py | Created |
| 2026-03-18 | claude1/app/scripts/create_su.py | Created |
| 2026-03-18 | claude1/app/tests/test_models.py | Created |
| 2026-03-18 | claude1/app/src/config.py      | Created |
| 2026-03-18 | claude1/app/src/app.py         | Updated — full factory with config, DB, middleware |
| 2026-03-18 | claude1/app/src/services/jwt_service.py | Created |
| 2026-03-18 | claude1/app/src/services/audit_log.py | Created |
| 2026-03-18 | claude1/app/src/middleware/auth_middleware.py | Created |
| 2026-03-18 | claude1/app/src/middleware/csrf.py | Created |
| 2026-03-18 | claude1/app/src/middleware/cors.py | Created |
| 2026-03-18 | claude1/app/src/middleware/rate_limit.py | Created |
| 2026-03-18 | claude1/app/tests/test_core.py | Created |
| 2026-03-18 | claude1/progress.md           | Updated — Phase 2+3 complete |
| 2026-03-18 | claude1/app/src/services/auth_service.py | Created — authenticate_user, build_access_blob, hash/verify password |
| 2026-03-18 | claude1/app/src/routes/auth.py | Created — login, me, me/service, logout, change-password |
| 2026-03-18 | claude1/app/src/app.py         | Updated — registered auth blueprint |
| 2026-03-18 | claude1/app/src/middleware/auth_middleware.py | Updated — per-request cache fix for _get_current_user |
| 2026-03-18 | claude1/app/tests/test_auth.py | Created — 20 tests for auth API |
| 2026-03-18 | claude1/progress.md           | Updated — Phase 4 complete |
| 2026-03-18 | claude1/app/src/routes/patient.py | Created — register, registry-status |
| 2026-03-18 | claude1/app/src/app.py         | Updated — registered patient blueprint |
| 2026-03-18 | claude1/app/tests/test_patient.py | Created — 13 tests for patient API |
| 2026-03-18 | claude1/app/tests/test_auth.py | Updated — rate limit reset in fixture |
| 2026-03-18 | claude1/progress.md           | Updated — Phase 5 complete |
| 2026-03-18 | claude1/app/src/routes/groups.py | Created — groups, membership, admin, invites (8 endpoints) |
| 2026-03-18 | claude1/app/src/app.py         | Updated — registered groups blueprint |
| 2026-03-18 | claude1/app/tests/test_groups.py | Created — 22 tests for group API |
| 2026-03-18 | claude1/progress.md           | Updated — Phase 6 complete |
| 2026-03-18 | claude1/app/src/routes/admin.py | Created — 16 SU admin endpoints |
| 2026-03-18 | claude1/app/src/app.py         | Updated — registered admin blueprint |
| 2026-03-18 | claude1/app/tests/test_admin.py | Created — 31 tests for admin API |
| 2026-03-18 | claude1/progress.md           | Updated — Phase 7 complete |
| 2026-03-18 | claude1/app/src/routes/public.py | Created — public/catalog API (4 endpoints) |
| 2026-03-18 | claude1/app/src/app.py         | Updated — registered public blueprint |
| 2026-03-18 | claude1/app/tests/test_public.py | Created — 12 tests for public API |
| 2026-03-18 | claude1/progress.md           | Updated — Phase 8 complete |
| 2026-03-18 | claude1_sso/app/src/routes/frontend.py | Created — frontend blueprint (landing, login/logout, dashboard, SU admin, group admin, onboarding, docs) |
| 2026-03-18 | claude1_sso/app/src/templates/base.html | Created — PDHC Layout Standard base template |
| 2026-03-18 | claude1_sso/app/src/templates/login.html | Created — login page with SSO handshake |
| 2026-03-18 | claude1_sso/app/src/templates/dashboard.html | Created — dashboard per role |
| 2026-03-18 | claude1_sso/app/src/templates/su_admin.html | Created — SU admin panel |
| 2026-03-18 | claude1_sso/app/src/templates/group_admin.html | Created — group admin panel |
| 2026-03-18 | claude1_sso/app/src/templates/register_patient.html | Created — patient registration |
| 2026-03-18 | claude1_sso/app/src/templates/request_access.html | Created — professional access request |
| 2026-03-18 | claude1_sso/app/src/templates/request_join.html | Created — request group membership |
| 2026-03-18 | claude1_sso/app/src/templates/suggest_group.html | Created — suggest new group |
| 2026-03-18 | claude1_sso/app/src/templates/join.html | Created — join by invite |
| 2026-03-18 | claude1_sso/app/src/templates/change_password.html | Created — change password |
| 2026-03-18 | claude1_sso/app/src/templates/docs.html | Created — docs download page |
| 2026-03-18 | claude1_sso/app/src/templates/landing.html | Created — landing/service list |
| 2026-03-18 | claude1_sso/app/src/app.py     | Updated — registered frontend blueprint |
| 2026-03-18 | claude1_sso/app/tests/test_frontend.py | Created — 32 frontend tests |
| 2026-03-18 | claude1_sso/app/requirements.txt | Updated — removed pytest-flask (caused session cache interference) |
| 2026-03-18 | claude1_sso/progress.md        | Updated — Phase 9 complete |
| 2026-03-18 | claude1_sso/app/src/fhir/capability_statement.py | Created — CapabilityStatement endpoint + FHIR blueprint |
| 2026-03-18 | claude1_sso/app/src/fhir/schemas.py | Created — FHIR R5 schema helpers (patient, practitioner, organization, group) |
| 2026-03-18 | claude1_sso/app/src/services/fhir_validator.py | Created — FHIR R5 validator using fhir.resources |
| 2026-03-18 | claude1_sso/app/src/app.py     | Updated — registered FHIR blueprint |
| 2026-03-18 | claude1_sso/app/oath_overview.csv | Created — service registry CSV with schema header |
| 2026-03-18 | claude1_sso/app/tests/test_fhir.py | Created — 20 FHIR compliance tests |
| 2026-03-18 | claude1_sso/app/tests/test_admin.py | Updated — oath_overview.csv backup/restore in fixture |
| 2026-03-18 | claude1_sso/progress.md        | Updated — Phase 10 complete |
| 2026-03-18 | claude1_sso/app/tests/conftest.py | Updated — comprehensive shared fixtures for all roles |
| 2026-03-18 | claude1_sso/app/scripts/test_endpoints.sh | Created — endpoint test script against capability statement |
| 2026-03-18 | claude1_sso/progress.md        | Updated — Phase 11 complete |
| 2026-03-18 | claude1_sso/app/docs/mkdocs.yml | Created — MkDocs Material config |
| 2026-03-18 | claude1_sso/app/docs/docs/index.md | Created — documentation landing page |
| 2026-03-18 | claude1_sso/app/docs/docs/architecture.md | Created — system context, data model, auth flows, decision tree |
| 2026-03-18 | claude1_sso/app/docs/docs/api-reference.md | Created — all API endpoints with examples |
| 2026-03-18 | claude1_sso/app/docs/docs/integration-guide.md | Created — downstream service integration guide |
| 2026-03-18 | claude1_sso/app/docs/docs/admin-manual.md | Created — SU and group admin operations |
| 2026-03-18 | claude1_sso/app/docs/docs/deployment-guide.md | Created — local dev and server deployment |
| 2026-03-18 | claude1_sso/app/docs/docs/user-guide.md | Created — professional and patient workflows |
| 2026-03-18 | claude1_sso/progress.md        | Updated — Phase 12 complete |
| 2026-03-18 | claude1_sso/changed_files.md   | Updated — Phase 12 files tracked |
| 2026-03-18 | claude1_sso/app/src/app.py      | Updated — security headers, cookie security, MAX_CONTENT_LENGTH |
| 2026-03-18 | claude1_sso/progress.md        | Updated — Phase 13 complete, all phases done |
| 2026-03-18 | claude1_sso/changed_files.md   | Updated — Phase 13 files tracked |
| 2026-03-18 | start.sh                       | Updated — renamed formserviceFHIR → sso.pdhc, fixed python3 → python for venv |
| 2026-03-18 | app/src/templates/base.html    | Updated — renamed formserviceFHIR → sso.pdhc |
| 2026-03-18 | app/src/templates/landing.html | Updated — renamed formserviceFHIR → sso.pdhc |
| 2026-03-18 | app/src/templates/change_password.html | Updated — renamed formserviceFHIR → sso.pdhc |
| 2026-03-18 | app/src/templates/dashboard.html | Updated — renamed formserviceFHIR → sso.pdhc |
| 2026-03-18 | app/src/templates/docs.html    | Updated — renamed formserviceFHIR → sso.pdhc |
| 2026-03-18 | app/src/templates/group_admin.html | Updated — renamed formserviceFHIR → sso.pdhc |
| 2026-03-18 | app/src/templates/join.html    | Updated — renamed formserviceFHIR → sso.pdhc |
| 2026-03-18 | app/src/templates/login.html   | Updated — renamed formserviceFHIR → sso.pdhc |
| 2026-03-18 | app/src/templates/register_patient.html | Updated — renamed formserviceFHIR → sso.pdhc |
| 2026-03-18 | app/src/templates/request_access.html | Updated — renamed formserviceFHIR → sso.pdhc |
| 2026-03-18 | app/src/templates/request_join.html | Updated — renamed formserviceFHIR → sso.pdhc |
| 2026-03-18 | app/src/templates/su_admin.html | Updated — renamed formserviceFHIR → sso.pdhc |
| 2026-03-18 | app/src/templates/suggest_group.html | Updated — renamed formserviceFHIR → sso.pdhc |
| 2026-03-18 | app/src/fhir/capability_statement.py | Updated — renamed formserviceFHIR → sso.pdhc |
| 2026-03-18 | app/.env                       | Updated — renamed formserviceFHIR → sso.pdhc |
| 2026-03-18 | app/.env.example               | Updated — renamed formserviceFHIR → sso.pdhc |
| 2026-03-18 | app/safe_restart.sh            | Updated — renamed formserviceFHIR → sso.pdhc |
| 2026-03-18 | app/scripts/test_endpoints.sh  | Updated — renamed formserviceFHIR → sso.pdhc |
| 2026-03-18 | app/tests/conftest.py          | Updated — renamed formserviceFHIR → sso.pdhc |
| 2026-03-18 | app/tests/test_frontend.py     | Updated — renamed formserviceFHIR → sso.pdhc |
| 2026-03-18 | app/docs/mkdocs.yml            | Updated — renamed formserviceFHIR → sso.pdhc |
| 2026-03-18 | app/docs/docs/index.md         | Updated — renamed formserviceFHIR → sso.pdhc |
| 2026-03-18 | app/docs/docs/integration-guide.md | Updated — renamed formserviceFHIR → sso.pdhc |
| 2026-03-18 | app/docs/docs/api-reference.md | Updated — renamed formserviceFHIR → sso.pdhc |
| 2026-03-18 | initial_sql_design.txt         | Updated — renamed formserviceFHIR → sso.pdhc |
| 2026-03-18 | progress.md                    | Updated — renamed formserviceFHIR → sso.pdhc |
| 2026-03-18 | readme.md                      | Updated — renamed formserviceFHIR → sso.pdhc |
| 2026-03-18 | app/docs/docs/subservice-onboarding.md | Created — comprehensive subservice onboarding & acceptance specification |
| 2026-03-18 | app/docs/mkdocs.yml            | Updated — added subservice-onboarding + pre-deployment-checklist to nav |
| 2026-03-18 | app/docs/docs/pre-deployment-checklist.md | Created — comprehensive pre-deployment checklist (keys, secrets, first login, all config) |
| 2026-03-18 | app/src/routes/frontend.py     | Updated — added subservice-onboarding + pre-deployment-checklist to ALLOWED_DOCS |
| 2026-03-18 | app/src/templates/su_admin.html | Updated — added documentation download section |
| 2026-03-18 | pack_deploy.sh                 | Created — builds deployment tarball excluding venv, .env, .git, caches |
| 2026-03-18 | server_deploy.sh               | Created — server-side deployment script (unpack, venv, deps, DB init, gunicorn) |
| 2026-03-19 | nginx_implement_server19March.md | Created — generic nginx server installation guide (isolated environment) |
| 2026-03-19 | app/scripts/server_backup.sh   | Created — full server backup script (files + DB dump to ~/backups) |
| 2026-03-19 | app/src/app.py                 | Updated — ProxyFix middleware, WTF_CSRF_SSL_STRICT, db import fix |
| 2026-03-19 | start.sh                       | Updated — Colima socket, venv auto-create, gunicorn daemon, health check |
| 2026-03-19 | start.sh                       | Updated — comprehensive pre-flight checks, auto-start Colima/Docker, dc wrapper, DB timeout, keep-alive loop |
| 2026-03-19 | start.sh                       | Updated — Colima auto-recovery: 3 attempts, force-stop stale VM, delete+recreate on 2nd failure, clean Lima socket/pid files |
| 2026-03-19 | start.sh                       | Updated — runs fully in background, no blocking loop, exits after health check |
| 2026-03-19 | stop.sh                        | Created — graceful shutdown: gunicorn, port cleanup, docker-compose down |
| 2026-03-24 | app/src/services/auth_service.py | Updated — added `organisation_warning` flag to access blob for org-less professionals |
| 2026-03-24 | app/src/middleware/auth_middleware.py | Updated (prev session) — added `@require_organisation` decorator |
| 2026-03-24 | app/src/routes/admin.py         | Updated (prev session) — added user-org assignment/removal API endpoints |
| 2026-03-24 | app/src/routes/frontend.py      | Updated (prev session) — added org column + assign/remove org UI handlers |
| 2026-03-24 | app/src/templates/su_admin.html | Updated (prev session) — org column, assign/remove buttons in admin panel |
| 2026-03-25 | app/src/config.py              | Updated — added HEALTH_CHECK_INTERVAL config var (default 300s) |
| 2026-03-25 | app/src/app.py                 | Updated — added /api/service-health endpoint (server-side aggregated health checks) |
| 2026-03-25 | app/src/templates/su_admin.html | Updated — 3 health dots per service (API/DB/Frontend), cyclic polling via setTimeout |
| 2026-03-25 | app/requirements.txt           | Updated — added requests>=2.32.0 for server-side health checks |
| 2026-03-25 | app/.env                       | Updated — added HEALTH_CHECK_INTERVAL=300 |
| 2026-04-05 | app/src/models/organisation.py | Updated — added push_endpoint_url, push_secret columns |
| 2026-04-05 | app/src/routes/public.py       | Updated — include push_endpoint_url in public org listing |
| 2026-04-05 | app/src/routes/admin.py        | Updated — org PUT endpoint, push-config GET endpoint |
| 2026-04-05 | app/src/middleware/auth_middleware.py | Updated — added require_service_key decorator |
| 2026-04-05 | app/src/config.py              | Updated — added INTERNAL_SERVICE_KEY config |
| 2026-04-05 | app/src/routes/internal.py     | Created — internal service-to-service push-config endpoint |
| 2026-04-05 | app/src/app.py                 | Updated — registered internal blueprint, CSRF exempt |
| 2026-04-14 | app/src/templates/landing.html | Updated — status-dot probe tries `mode:'cors'` + JSON parse first, driving the dot from the service's `status`/`database` fields. Falls back to `mode:'no-cors'` reachability for services that haven't enabled CORS on /health yet. Ticket #38. |
| 2026-04-14 | app/src/templates/landing.html | Follow-up — relaxed CORS probe to match `app.py::_check_one_service`: services returning `{"status":"ok"}` without a `database` field (plan.pdhc, contract.pdhc) now show green with `API ok · DB not reported`, instead of red `Degraded: DB unknown`. Reload + screenshot from user revealed the regression. |
| 2026-04-15 | app/src/templates/dashboard.html | Renamed dashboard card "Organisations" → "Assigned Organisations" (clarifies it's the user's assignments, not a directory). Ticket #42 part 1. |
| 2026-04-15 | app/src/templates/su_admin.html | Org listing first table now shows Name only; GUID moved to the row's `title` attribute (hover-tooltip). SU still has GUID access for debugging without it dominating the visual. Ticket #42 part 2. |
| 2026-04-15 | app/src/models/user.py | Added `force_change_on_next_login: bool DEFAULT FALSE` and `password_changed_at: datetime NULL`. Ticket #43. Schema delta applied on server via `ALTER TABLE users ADD COLUMN IF NOT EXISTS …` (no alembic in sso.pdhc). |
| 2026-04-15 | app/src/routes/admin.py | `POST /api/admin/users/<guid>/reset-password` (SU-only) — generates a 16-char alphanumeric temp password (~95 bits entropy, shell-safe charset), sets `force_change_on_next_login=True`, stamps `password_changed_at`, returns plaintext temp pw **once** (never persisted). Accepts optional `{temp_password}` override. Ticket #43. |
| 2026-04-15 | app/src/routes/auth.py | `/api/auth/login` now includes `must_change_password` in the JSON response (and `?must_change_password=1` in the SSO-handshake redirect). `/api/auth/change-password` clears the flag on success + stamps `password_changed_at`. Ticket #43. |
| 2026-04-15 | app/src/services/auth_service.py | `build_access_blob` exposes `must_change_password` so sibling services' /me/service validations can honour the SU-forced reset too. Ticket #43. |
| 2026-04-15 | app/src/routes/frontend.py | Added `/admin/reset-password` HTML form handler (SU-only, flashes plaintext temp pw once for out-of-band hand-off). Added `before_app_request` gate that redirects logged-in users with `force_change_on_next_login` to `/change-password` (skips /api/*, /static/*, /change-password itself, /login, /logout). Cleared flag + stamped `password_changed_at` in the existing page-level change_password() handler. Ticket #43. |
| 2026-04-15 | app/src/templates/su_admin.html | Added "Reset pw" button per user in the Actions column, with confirm + reminder that the temp password is shown once. Ticket #43. |
| 2026-04-15 | app/src/models/user.py | Added `token_revocation_epoch: datetime NULL`. Ticket #44. Nullable so pre-existing tokens remain valid; SU bump → all outstanding tokens rejected on next use. Schema delta applied via `ALTER TABLE … ADD COLUMN IF NOT EXISTS`. |
| 2026-04-15 | app/src/services/jwt_service.py | `validate_token` now loads the subject user and, if `token_revocation_epoch` is set, rejects tokens with `iat < epoch` (raises `TokenRevokedError`). Handles naive-UTC datetimes from Postgres `timestamp without time zone`. Ticket #44. |
| 2026-04-15 | app/src/routes/admin.py | `POST /api/admin/users/<guid>/flush-sessions` (SU-only) — bumps `token_revocation_epoch` to `now()`, audit-logs, returns the new epoch. Ticket #44. |
| 2026-04-15 | app/src/routes/frontend.py | `/admin/flush-sessions` HTML form handler (SU-only) — same mechanic as the API route; refuses to flush the SU's own sessions to avoid self-logout. Ticket #44. |
| 2026-04-15 | app/src/templates/su_admin.html | Added "Flush" button per user (alongside Reset pw) with confirm modal. Ticket #44. |
| 2026-04-15 | app/src/routes/admin.py | `DELETE /api/admin/organisations/<guid>` (SU-only): hard-blocks with 409 when patients still reference the org (lists count + up to 5 sample patient GUIDs); cascades UserOrganisation and ALL AccessRequest rows (pending and decided — history survives in audit log). Plus `GET /api/admin/organisations/<guid>/dependents` helper for UI counts. Ticket #45. |
| 2026-04-15 | app/src/routes/frontend.py | `/admin/delete-organisation` HTML form handler mirrors the API logic (hard patient-block, cascades UserOrg+AccessRequest, audit). `admin_page()` now enriches each organisation with `patient_count` / `user_assignment_count` / `access_request_count` for the confirm modal. Ticket #45. |
| 2026-04-15 | app/src/templates/su_admin.html | Organisations table now columns Name / Users / Patients / Requests / Actions. Delete button per row with confirm showing the cascade counts; disabled (with tooltip) when `patient_count > 0`. Ticket #45. |
| 2026-04-15 | app/src/models/user_phase.py | Created — `UserPhase` junction (user_guid, phase, granted_by_guid, granted_at) with `UniqueConstraint('user_guid','phase')`. `PHASE_NAMES = ('planning','request','provider','analysis')` matches group_type_enum so `build_access_blob` can union group-derived and direct grants without name translation. Ticket #46. |
| 2026-04-15 | app/src/models/__init__.py | Registered `UserPhase` with `Base.metadata`. Ticket #46. |
| 2026-04-15 | app/src/services/auth_service.py | `build_access_blob.effective_phases` now unions approved-group-derived phases AND direct `UserPhase` grants. Direct-phase query wrapped in try/except so a missing `user_phases` table degrades gracefully. Shape of `effective_phases` unchanged — downstream services unaffected. Ticket #46. |
| 2026-04-15 | app/src/routes/admin.py | Three new SU-only endpoints: `GET /api/admin/users/<guid>/phases` (lists with source `direct` vs `via_group:<name>`), `POST /api/admin/users/<guid>/phases {phase}` (idempotent grant), `DELETE /api/admin/users/<guid>/phases/<phase>` (revoke — flags `still_implicit_via_group` in response). Ticket #46. |
| 2026-04-15 | app/src/routes/frontend.py | `/admin/grant-phase` + `/admin/revoke-phase` HTML form handlers (SU-only, audit-logged). `admin_page()` now enriches each professional user with `direct_phases: list[str]` and `group_phases: list[{phase,group_name}]` for the template, and passes `phase_names` for the grant dropdown. Ticket #46. |
| 2026-04-15 | app/src/templates/su_admin.html | New "Phases" column in users table. Direct grants rendered as removable green pills with `×`; group-derived as blue pills tagged `(grp)` with the group name in the title attribute. Per-user `+ Phase` dropdown + Grant button. Ticket #46. |
| 2026-04-15 | sso_db schema delta | Server-side: `CREATE TYPE phase_enum AS ENUM ('planning','request','provider','analysis')` + `CREATE TABLE user_phases (...)` with `uq_user_phase`. Idempotent backfill from approved memberships populated 13 rows (planning:3 / request:4 / provider:3 / analysis:3). Gunicorn master pid 1142 HUPed → worker 32291; health green. Ticket #46. |
| 2026-04-15 | app/src/routes/frontend.py | `group_admin_page()` now loads per-admin-group member rosters with each member's orgs (with names), direct phases, and group-derived phases. Four new SU-only routes: `/group-admin/{assign-org,remove-org,grant-phase,revoke-phase}` mirror the `/admin` equivalents but redirect back to `/group-admin` so SU can manage groups + orgs + phases without leaving the pane. Ticket #47. |
| 2026-04-15 | app/src/templates/group_admin.html | New per-group "Members" card listing approved members with org pills (green `×` remove for SU) and phase pills (direct green + group-derived blue `(grp)`). Add-org dropdown + grant-phase dropdown per row, visible only when `current_user.is_su_admin`. Non-SU group admins see read-only pills + keep existing invite/decide flows. Ticket #47. |
| 2026-04-15 | app/docs/docs/api-reference.md | Updated /me + /me/service + change-password sections for must_change_password (#43), token_revocation_epoch 401 (#44), and effective_phases union (#46). Added 5 admin endpoints: POST /admin/users/<guid>/reset-password, POST .../flush-sessions, GET/POST .../phases, DELETE .../phases/<phase>. Ticket #56. |
| 2026-04-15 | app/docs/docs/architecture.md | Added force_change_on_next_login, token_revocation_epoch, UserPhase to ER diagram. Updated both blob JSON examples with must_change_password. Added sequence diagrams for forced-password-reset (#43) and bulk-session-flush (#44). Middleware stack step 4 now mentions the epoch check. Ticket #56. |
| 2026-04-15 | app/docs/docs/admin-manual.md | New SU sections: Forcing a Password Reset (#43), Flushing User Sessions (#44), Managing Direct Phase Grants (#46). Audit-trail list extended to cover new admin actions. Ticket #56. |
| 2026-04-15 | app/docs/docs/subservice-onboarding.md | §4.2 now explicitly forbids blob caching (with rationale). New §4.7 (must_change_password handling) and §4.8 (401-on-epoch handling). Error-handling table + acceptance tests (F13–F15) + Phase C checklist (C.7–C.9) extended. Ticket #56. |
| 2026-04-15 | app/docs/docs/integration-guide.md | `authorize_professional_action()` now short-circuits on must_change_password (before SU bypass). Error-handling table adds #44 + #43 rows. New Troubleshooting subsection for common caching/grant-source pitfalls. Ticket #56. |
| 2026-04-15 | app/docs/docs/deployment-guide.md | First-Run checklist now verifies users.force_change_on_next_login, users.token_revocation_epoch, user_phases table presence. Added "Downstream services must not cache the access blob" warning alongside BOOTSTRAP warning. Ticket #56. |
| 2026-04-15 | app/docs/docs/pre-deployment-checklist.md | New Section J: Downstream Service Compatibility (#43, #44, #46) — J.1 per-request blob validation, J.2 must_change_password, J.3 flush-sessions, J.4/J.5 direct phase grant/revoke. Ticket #56. |
| 2026-04-15 | app/docs/docs/user-guide.md | New end-user section under "Changing Your Password": "When an Admin Resets Your Password" explains the auto-redirect-to-change-password flow and contrasts with session-flush. Ticket #56. |
| 2026-04-15 | app/src/services/auth_service.py | Ticket #57: decoupled `effective_phases` from group_type. Now sourced exclusively from `UserPhase` grants; groups are orthogonal category metadata. `build_access_blob.groups[]` entries keep `group_type` as a free-form category label but never feed phase access. Shape of `effective_phases` unchanged — field name + membership check semantics preserved. |
| 2026-04-15 | app/src/models/user_phase.py | Ticket #57: docstring now describes `UserPhase` as the **sole** source of phase access (previously shared with group-derived union). PHASE_NAMES values unchanged. |
| 2026-04-15 | app/src/routes/admin.py | Ticket #57: `decide_access_request` no longer auto-creates phase memberships on approval. Response now includes `requested_phases_pending_su_grant` so the approving SU sees which phases need explicit grant calls. |
| 2026-04-15 | app/src/routes/frontend.py | Ticket #57: `admin_decide_access_request` mirrors the admin.py decoupling (no auto-grant on approval). Flash message instructs SU to grant phases explicitly. `admin_page()` now surfaces `requested_phases` on each AccessRequest row for the decision UI. |
| 2026-04-15 | app/src/templates/su_admin.html | Ticket #57: "Type" column on All Groups / Group Proposals renamed to "Category" with tooltip clarifying no phase-access implication. Explanatory paragraph. Access Requests table gained a "Requested" column showing `requested_phases` as badges. |
| 2026-04-15 | app/scripts/phases_migration_report.py | Created. Read-only CLI listing `(user_guid, email, phase, via_group_guid, via_group_name)` for users who previously held a phase via group membership but have no matching `UserPhase` row. SU reviews + grants explicitly; no auto-backfill. Ticket #57. |
| 2026-04-15 | app/tests/test_auth.py | Ticket #57: seed_data now explicitly adds `UserPhase(user=pro, phase='planning')`. New `test_me_effective_phases_ignores_group_type` verifies that a user with approved membership in a `planning`-typed group but no UserPhase row has `effective_phases == []`. |
| 2026-04-15 | app/tests/test_admin.py | Ticket #57: `test_approve_access_request_creates_user` now asserts `requested_phases_pending_su_grant` in the response. New `test_approve_access_request_does_not_grant_phases` confirms approval does NOT create UserPhase or any new Membership rows. |
| 2026-04-15 | app/tests/test_models.py | Bumped EXPECTED_TABLES to 13 (adds `user_phases`) and imported UserPhase so Base.metadata.create_all registers it. Fixed stale count from #46. |
| 2026-04-15 | app/docs/docs/architecture.md | Ticket #57: `effective_phases` section rewritten from "union of group-derived + direct grants" to "direct UserPhase grants only; groups are orthogonal category metadata". ER diagram `Group.group_type` annotated as category label that does NOT confer phase access. |
| 2026-04-15 | app/docs/docs/api-reference.md | Ticket #57: Blob field table entry for `effective_phases` updated to "direct UserPhase grants only". Admin endpoints rewritten: GET phases no longer returns `group_derived_phases`; DELETE phase no longer mentions "group-derived branch" preserving the phase. |
| 2026-04-15 | app/docs/docs/admin-manual.md | Ticket #57: Managing Phase Grants section rewritten — direct UserPhase is the sole source of phase access; access-request approval no longer auto-grants phases; reference to `scripts/phases_migration_report.py`. |
| 2026-04-15 | app/docs/docs/subservice-onboarding.md | Ticket #57: §4.2 blob field table for `effective_phases` + `groups` updated. Groups documented as orthogonal category metadata, never to be treated as phase-access signals. |
| 2026-04-15 | app/docs/docs/integration-guide.md | Ticket #57: code comment on phase check rewritten for decoupled model. Troubleshooting row added: user approved into a planning-typed group but without `planning` phase access is expected behaviour (#57). |
| 2026-04-15 | app/docs/docs/pre-deployment-checklist.md | Ticket #57: Section J gained J.6 (groups do NOT grant phases), J.7 (access-request approval does not auto-grant), J.8 (pre-#57 migration review via `phases_migration_report.py`). |
| 2026-04-15 | app/docs/docs/user-guide.md | Ticket #57: Dashboard legend clarifies groups = organisational metadata, phases = access. "Requesting Group Membership" admonition: joining a group does not grant phase access. Access-request section explains SU grants phases explicitly as a separate step after approval. |
| 2026-04-15 | app/docs/docs/deployment-guide.md | Ticket #57: First-run checklist gained items for `effective_phases` decoupling verification and access-request approval not auto-granting. |
| 2026-04-16 | app/src/app.py | Ticket #58: `handle_exception` now short-circuits for `werkzeug.exceptions.HTTPException` (returns `e` unchanged), so 404/405/403/etc. keep their native status + response instead of being coerced into 500 by the catch-all handler. Imports `HTTPException` at module top. |
| 2026-04-16 | app/tests/test_frontend.py | Ticket #58: added `test_nonexistent_route_returns_404` and `test_wrong_method_returns_405` to `TestDocsPage`, guarding against any future change that re-routes HTTPException subclasses through the generic 500 branch. |
| 2026-04-16 | app/src/templates/landing.html | Ticket #59: committed the provider-grid rewrite (two sections: "Platform Services" + "Connected Services") that had been sitting as uncommitted drift. Switches service-health probe from `no-cors` (false-green) to `cors` + JSON with `no-cors` fallback; drives each dot from the real `status` / `database` fields. |
| 2026-04-16 | app/tests/test_frontend.py | Ticket #59: `test_landing_renders` now asserts 'Platform Services' and 'Connected Services' landmarks instead of the pre-rewrite 'Registered Services' string. |
| 2026-04-16 | app/src/models/group.py | Ticket #60: renamed `group_type` column → `category`; loosened the 4-value Enum to `String(64)` so SU can invent new category labels without a schema migration. Docstring + `__repr__` updated. |
| 2026-04-16 | app/src/models/group_proposal.py | Ticket #60: same rename + enum→varchar on the proposed-group model. |
| 2026-04-16 | app/src/models/user_phase.py | Ticket #60: PHASE_NAMES comment rewritten — phases live in their own enum here and are decoupled from group categories. |
| 2026-04-16 | app/src/services/auth_service.py | Ticket #60: access-blob group dict key renamed `group_type` → `category`; source attr `group.group_type` → `group.category`. |
| 2026-04-16 | app/src/fhir/schemas.py | Ticket #60: FHIR Group resource uses `urn:pdhc:group-category` as the coding system (was `group-type`); code/display sourced from `group.category`. |
| 2026-04-16 | app/src/routes/admin.py | Ticket #60: `list_user_phases` and phase-revoke handlers read `group.category`; `list_group_proposals` emits `category`; approving a proposal creates `Group(category=proposal.category)`. Docstrings note post-#57/#60 semantics. |
| 2026-04-16 | app/src/routes/groups.py | Ticket #60: `GET /api/groups` list entries emit `category` instead of `group_type`. |
| 2026-04-16 | app/src/routes/public.py | Ticket #60: `GET /api/public/groups` entries emit `category`. |
| 2026-04-16 | app/src/routes/frontend.py | Ticket #60: all `.group_type` reads → `.category`; blob/roster/group dicts emit `category`. Create-group and suggest-group form handlers relaxed — no longer enforce the 4-value whitelist; accept `category` form field with `group_type` fallback for backwards compat. Audit detail fields renamed. |
| 2026-04-16 | app/scripts/phases_migration_report.py | Ticket #60: walks `Group.category` and filters against PHASE_NAMES to preserve pre-#57 semantics on free-form category data. |
| 2026-04-16 | app/scripts/migrations/rename_group_type_to_category.sql | Ticket #60: created — idempotent, transactional migration: ALTER TYPE → varchar + RENAME for both `groups` and `group_proposals`, then DROP TYPE group_type_enum. Includes post-run sanity-check SELECTs. |
| 2026-04-16 | app/src/templates/su_admin.html | Ticket #60: SU group proposals/list tables render `p.category`/`g.category`; create-group form replaced `<select name="group_type">` with `<input name="category">` + datalist of legacy suggestions. |
| 2026-04-16 | app/src/templates/suggest_group.html | Ticket #60: select-enum replaced by free-form `<input name="category">` + datalist; validation message loosened. |
| 2026-04-16 | app/src/templates/request_join.html | Ticket #60: option label reads `g.category` instead of `g.group_type`. |
| 2026-04-16 | app/src/templates/group_admin.html | Ticket #60: per-group header badge reads `mg.category`. |
| 2026-04-16 | app/src/templates/dashboard.html | Ticket #60: groups table "Type" column renamed to "Category"; cell reads `g.category`. |
| 2026-04-16 | app/tests/test_models.py | Ticket #60: `test_create_group` + `test_create_proposal` use `category=` kwarg; new `test_category_is_free_form` asserts free-form varchar behaviour. |
| 2026-04-16 | app/tests/conftest.py, test_admin.py, test_auth.py, test_frontend.py, test_groups.py, test_public.py, test_fhir.py | Ticket #60: sweep — constructors pass `category=` instead of `group_type=`; blob-shape assertions read `['category']`; `test_me_effective_phases_ignores_group_type` renamed to `…ignores_group_category`. |
| 2026-04-16 | app/docs/docs/api-reference.md | Ticket #60: `/api/auth/me` + `/api/groups` example blobs use `category` key. |
| 2026-04-16 | app/docs/docs/architecture.md | Ticket #60: ERD Group.column renamed `enum group_type` → `string category`; GroupProposal likewise; example blob + decoupling paragraph updated. |
| 2026-04-16 | app/docs/docs/subservice-onboarding.md | Ticket #60: §4.2 `groups` field table updated — now lists `category` as a free-form string (with #60 back-reference). |
| 2026-04-16 | plan.pdhc/planp/tests/conftest.py | Ticket #60: sample SSO blobs updated — groups objects emit `category` instead of `group_type` so plan.pdhc's blob assertions match the new SSO contract. |
| 2026-04-16 | plan.pdhc/planp/docs/sso_technical_manual.md | Ticket #60: example blob in onboarding doc uses `category` key. |
| 2026-04-16 | sso.pdhc/SSO_Service_Functions_SV.md | Ticket #61: top-level Swedish policy doc rewritten to reflect post-#57/#60 model. "Fas-behörighet via gruppmedlemskap" replaced with "Fas-behörighet (direkta grants)" — phases come only from UserPhase grants via `POST /api/admin/users/<guid>/phases`. New "Grupper (organisatorisk metadata)" bullet makes groups/phases orthogonality explicit. Gruppadmin rewritten as within-group admin (not phase-scoped). Blob field list + pseudocode gate updated to `phase in blob["effective_phases"]`. Example JSON blob expanded to real shape (user_guid, must_change_password, professional/patient field split, groups[*].category, effective_phases). Access-request prose notes SU must grant phases as a separate step. Membership-request prose clarifies joining a group does not grant phase access. |
