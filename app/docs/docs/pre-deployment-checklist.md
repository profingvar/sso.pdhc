# Pre-Deployment Checklist — sso.pdhc

Complete every item before starting the service on the production server (macmini). Items are grouped by category. Do not proceed to the next category until the current one is fully signed off.

---

## A. Environment File (`.env`)

The `.env` file controls all secrets and runtime configuration. Copy `.env.example` to `.env` and change every value marked **CHANGE**.

| # | Item | Default (dev) | Production Action | Done |
|---|------|---------------|-------------------|------|
| A.1 | `SECRET_KEY` | random dev key | **CHANGE** — generate 64-char cryptographically random string: `python3 -c "import secrets; print(secrets.token_urlsafe(48))"` | [ ] |
| A.2 | `DATABASE_URL` | `postgresql://sso_user:sso_password@localhost:9003/sso_db` | **CHANGE** — set strong password for `sso_user`. Format: `postgresql://sso_user:<STRONG_PASSWORD>@localhost:9003/sso_db` | [ ] |
| A.3 | `FLASK_ENV` | `development` | **CHANGE** to `production` | [ ] |
| A.4 | `SESSION_EXPIRY_HOURS` | `24` | Review — adjust to organisational policy (e.g. `8` for stricter security) | [ ] |
| A.5 | `BOOTSTRAP_SU_EMAIL` | `admin@example.com` | **CHANGE** — set to the real admin email address | [ ] |
| A.6 | `BOOTSTRAP_SU_PASSWORD` | `change-me-minimum-8-chars` | **CHANGE** — set a strong initial password (min 12 chars, mixed case, digits, symbols). Will be changed again after first login | [ ] |
| A.7 | `ALLOWED_ORIGINS` | `http://localhost:9000` | **CHANGE** — comma-separated production origins, e.g. `https://sso.pdhc.se,https://forms.pdhc.se` | [ ] |
| A.8 | `ALLOWED_CALLBACK_URLS` | `http://localhost:9000/callback` | **CHANGE** — comma-separated production callback URLs, e.g. `https://forms.pdhc.se/callback` | [ ] |
| A.9 | `KEY_CREATED_AT` | `2026-03-18T00:00:00Z` | **CHANGE** — set to current date/time (ISO-8601 UTC) | [ ] |
| A.10 | `LOG_DIR` | `./logs` | Review — set to `/var/log/sso` or appropriate server path with write permissions | [ ] |
| A.11 | Service credentials | commented out | **ADD** — for each downstream service: `SSO_CLIENT_ID_<NAME>=...` and `SSO_CLIENT_SECRET_<NAME>=...` | [ ] |

### Generating Credentials

```bash
# SECRET_KEY
python3 -c "import secrets; print(secrets.token_urlsafe(48))"

# Database password
python3 -c "import secrets; print(secrets.token_urlsafe(24))"

# Service client ID
python3 -c "import uuid; print(str(uuid.uuid4()))"

# Service client secret
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

---

## B. Database

| # | Item | Action | Done |
|---|------|--------|------|
| B.1 | Docker installed and running | Verify: `docker info` returns without error | [ ] |
| B.2 | Docker Compose v2 available | Verify: `docker compose version` | [ ] |
| B.3 | PostgreSQL password in `docker-compose.yml` | **CHANGE** — must match the password in `DATABASE_URL` in `.env` | [ ] |
| B.4 | Port 9003 available | Verify: `lsof -i :9003` shows nothing | [ ] |
| B.5 | Database data volume | Verify Docker volume location has sufficient disk space | [ ] |

---

## C. Application

| # | Item | Action | Done |
|---|------|--------|------|
| C.1 | Python 3.11+ installed | Verify: `python3 --version` | [ ] |
| C.2 | Virtual environment created | Run: `python3 -m venv app/venv` | [ ] |
| C.3 | Dependencies installed | Run: `source app/venv/bin/activate && pip install -r app/requirements.txt` | [ ] |
| C.4 | Port 9000 available | Verify: `lsof -i :9000` shows nothing | [ ] |
| C.5 | Log directory exists and is writable | Run: `mkdir -p <LOG_DIR> && test -w <LOG_DIR>` | [ ] |
| C.6 | `start.sh` is executable | Run: `chmod +x start.sh` | [ ] |
| C.7 | `safe_restart.sh` is executable | Run: `chmod +x app/safe_restart.sh` | [ ] |

---

## D. First Start & Initialisation

| # | Item | Action | Done |
|---|------|--------|------|
| D.1 | Start the service | Run: `./start.sh` | [ ] |
| D.2 | Initialise database tables | In another terminal: `cd app && source venv/bin/activate && python scripts/init_db.py` | [ ] |
| D.3 | Create bootstrap superuser | Run: `python scripts/create_su.py` | [ ] |
| D.4 | Health check passes | Verify: `curl http://localhost:9000/api/health` returns `{"status":"ok",...}` | [ ] |
| D.5 | FHIR metadata returns | Verify: `curl http://localhost:9000/fhir/metadata` returns valid CapabilityStatement | [ ] |

---

## E. First Login & Password Change

| # | Item | Action | Done |
|---|------|--------|------|
| E.1 | Login with bootstrap SU | Open `http://localhost:9000/login`, enter `BOOTSTRAP_SU_EMAIL` / `BOOTSTRAP_SU_PASSWORD` | [ ] |
| E.2 | Change bootstrap password | Go to Change Password page immediately. Set a permanent strong password | [ ] |
| E.3 | Verify dashboard loads | Confirm SU Admin panel is visible with admin badge | [ ] |
| E.4 | Update `.env` | Remove or blank out `BOOTSTRAP_SU_PASSWORD` from `.env` (no longer needed) | [ ] |

---

## F. SSL & Reverse Proxy

| # | Item | Action | Done |
|---|------|--------|------|
| F.1 | DNS configured | `sso.pdhc.se` resolves to the server IP | [ ] |
| F.2 | nginx installed | Verify: `nginx -v` | [ ] |
| F.3 | nginx config deployed | Place SSO config in `/etc/nginx/sites-available/` and symlink to `sites-enabled/` | [ ] |
| F.4 | SSL certificate obtained | Run certbot or install certificate manually | [ ] |
| F.5 | nginx config tested | Run: `nginx -t` | [ ] |
| F.6 | nginx reloaded | Run: `sudo nginx -s reload` | [ ] |
| F.7 | HTTPS access verified | Open `https://sso.pdhc.se` — login page loads without certificate warning | [ ] |
| F.8 | HTTP redirect works | `http://sso.pdhc.se` redirects to `https://sso.pdhc.se` | [ ] |
| F.9 | No interference with other services | Verify all other services on the server still respond correctly (Rule 22) | [ ] |

---

## G. Security Hardening

| # | Item | Action | Done |
|---|------|--------|------|
| G.1 | `.env` file permissions | Run: `chmod 600 app/.env` — only owner can read | [ ] |
| G.2 | `.env` not in git | Verify: `.env` is in `.gitignore` and not tracked | [ ] |
| G.3 | Firewall | Only ports 80, 443 exposed externally. Ports 9000 and 9003 bound to localhost only | [ ] |
| G.4 | Database not exposed | Port 9003 accessible only from localhost (verify `docker-compose.yml` binds to `127.0.0.1:9003`) | [ ] |
| G.5 | Log rotation configured | Deploy logrotate config for `LOG_DIR` (daily, 90-day retention, compressed) | [ ] |
| G.6 | Backup procedure in place | Verify: `docker exec sso_db pg_dump -U sso_user sso_db > backup.sql` works | [ ] |

---

## H. Smoke Test (complete before declaring production-ready)

| # | Test | Expected Result | Done |
|---|------|-----------------|------|
| H.1 | `GET /api/health` | `200 {"status":"ok","database":"connected",...}` | [ ] |
| H.2 | `GET /fhir/metadata` | `200` with valid FHIR R5 CapabilityStatement | [ ] |
| H.3 | `POST /api/auth/login` with valid creds | `200` with JWT token | [ ] |
| H.4 | `GET /api/auth/me` with Bearer token | `200` with access blob | [ ] |
| H.5 | Login via web UI | Dashboard loads, user info shown | [ ] |
| H.6 | Logout via web UI | Session cleared, redirect to landing page | [ ] |
| H.7 | Access admin panel | SU Admin page loads with user list | [ ] |
| H.8 | Download docs from admin | All documents download correctly | [ ] |
| H.9 | HTTPS from external client | `curl https://sso.pdhc.se/api/health` returns 200 | [ ] |
| H.10 | Run endpoint test script | `./app/scripts/test_endpoints.sh https://sso.pdhc.se` — all pass | [ ] |

---

## I. Post-Deployment

| # | Item | Action | Done |
|---|------|--------|------|
| I.1 | Register SSO itself in `oath_overview.csv` | Via SU admin panel or `PUT /api/admin/oath-overview` | [ ] |
| I.2 | Register downstream services | Add each subservice with credentials, callbacks, origins | [ ] |
| I.3 | Credential rotation schedule | Set calendar reminder: rotate service credentials every 90 days | [ ] |
| I.4 | Backup schedule | Set daily automated backup of database and `.env` | [ ] |
| I.5 | Monitoring | Configure alerting on `/api/health` endpoint | [ ] |
| I.6 | Document the deployment | Record server IP, paths, ports, contacts in ops documentation | [ ] |

---

## J. Downstream Service Compatibility (#43, #44, #46, #57)

These items verify that every downstream service correctly consumes the SSO blob semantics introduced in the #43/#44/#46/#57 ticket run. Run per subservice before declaring cross-platform ready.

| # | Item | Action | Done |
|---|------|--------|------|
| J.1 | Blob re-validation per request | Confirm the service calls `/api/auth/me/service` on every protected request (no session-side blob caching). See `subservice-onboarding.md` §4.2 | [ ] |
| J.2 | `must_change_password` honoured (#43) | Force `force_change_on_next_login = True` on a test user, hit a protected route on the service, confirm the user is routed to `${SSO_BASE_URL}/change-password` (HTML) or gets a 403 with the URL (API/FHIR) | [ ] |
| J.3 | 401-on-epoch handled (#44) | Call `POST /api/admin/users/<guid>/flush-sessions`, then retry a pre-flush Bearer on the service — expect the service to clear local session and bounce back to SSO login | [ ] |
| J.4 | Phase grants visible (#46) | Grant a phase via `POST /api/admin/users/<guid>/phases`, confirm the service's next authorisation check sees the phase via `effective_phases` without requiring a user re-login | [ ] |
| J.5 | Phase revocation (#46 + #57) | Revoke via `DELETE /api/admin/users/<guid>/phases/<phase>`, confirm the phase disappears from `effective_phases`. (Under #57 this is the only source — there is no "group-derived" branch that could preserve it.) | [ ] |
| J.6 | Groups do NOT grant phases (#57) | Add a test professional to an approved `planning`-typed group membership with no `UserPhase` row. Confirm the service returns 403 on a `planning`-gated route. Groups are orthogonal category metadata and must not be treated as phase grants | [ ] |
| J.7 | Access-request approval does not auto-grant (#57) | Approve an access request whose `requested_phases` includes `planning`. Confirm no `UserPhase` rows are created and the approval response reports `requested_phases_pending_su_grant` | [ ] |
| J.8 | Pre-#57 migration review | Run `python scripts/phases_migration_report.py` on a copy of the production DB. Review the list of users who previously held a phase via group membership and explicitly grant via the admin API for those who should keep access | [ ] |

---

## Quick Reference — What Must Be Changed

For rapid scanning, here is every value in `.env` that **must** be changed from the development default before production use:

```
SECRET_KEY          → new random 64-char string
DATABASE_URL        → new strong database password
FLASK_ENV           → production
BOOTSTRAP_SU_EMAIL  → real admin email
BOOTSTRAP_SU_PASSWORD → strong initial password
ALLOWED_ORIGINS     → production URLs
ALLOWED_CALLBACK_URLS → production callback URLs
KEY_CREATED_AT      → current date
```
