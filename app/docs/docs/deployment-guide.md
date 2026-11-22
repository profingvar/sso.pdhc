# Deployment Guide

## Prerequisites

| Component | Version |
|-----------|---------|
| Python | 3.11+ |
| Docker | 24+ |
| Docker Compose | v2+ |
| PostgreSQL | 15+ (via Docker) |

## Local Development Setup

### 1. Clone and Configure

```bash
cd claude1_sso
cp app/.env.example app/.env
```

Edit `app/.env` with your settings:

```bash
SECRET_KEY=your-random-secret-key-min-32-chars
DATABASE_URL=postgresql://sso_user:sso_pass@localhost:9003/sso_db
SESSION_EXPIRY_HOURS=24
FLASK_ENV=development

BOOTSTRAP_SU_EMAIL=admin@pdhc.se
BOOTSTRAP_SU_PASSWORD=changeme01

ALLOWED_ORIGINS=http://localhost:9000
ALLOWED_CALLBACK_URLS=http://localhost:9000/callback

KEY_CREATED_AT=2026-03-18
LOG_DIR=./logs

# Service credentials (one pair per downstream service)
SSO_CLIENT_ID_EXAMPLE=example-client-id
SSO_CLIENT_SECRET_EXAMPLE=example-client-secret
```

### 2. Start Everything

The single entry point is `start.sh` at the repo root:

```bash
chmod +x start.sh
./start.sh
```

`start.sh` does the following:

1. Kills any processes on ports 9000–9003
2. Checks Docker is running
3. Starts the PostgreSQL container via docker-compose
4. Activates the Python virtual environment
5. Installs/updates dependencies from `requirements.txt`
6. Starts the Flask app on port 9000
7. On `Ctrl+C`: gracefully shuts down app, stops DB, deactivates venv

### 3. Initialize Database

After the first start:

```bash
cd app
source venv/bin/activate
python scripts/init_db.py
python scripts/create_su.py
```

`init_db.py` creates all tables. `create_su.py` creates the bootstrap SU admin from `.env` values (`BOOTSTRAP_SU_EMAIL`, `BOOTSTRAP_SU_PASSWORD`).

### 4. Verify

```bash
# Health check
curl http://localhost:9000/api/health

# FHIR capability statement
curl http://localhost:9000/fhir/metadata

# Login with bootstrap SU
curl -X POST http://localhost:9000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@pdhc.se","password":"changeme01"}'
```

### 5. Run Tests

```bash
cd app
source venv/bin/activate
pytest tests/ -v
```

All 226 tests should pass. Results are saved to `results/`.

For the endpoint test script:

```bash
chmod +x scripts/test_endpoints.sh
./scripts/test_endpoints.sh http://localhost:9000
```

---

## Server Deployment (macmini)

### Docker Compose

The `docker-compose.yml` under `app/` defines two services:

- **db** — PostgreSQL on port 9003
- **app** — Flask/Gunicorn on port 9000

```bash
cd app
docker-compose up -d
```

### Production `.env`

Production settings differ from development:

```bash
SECRET_KEY=<cryptographically-random-64-char-string>
DATABASE_URL=postgresql://sso_user:<strong-password>@db:9003/sso_db
SESSION_EXPIRY_HOURS=24
FLASK_ENV=production

BOOTSTRAP_SU_EMAIL=admin@pdhc.se
BOOTSTRAP_SU_PASSWORD=<strong-initial-password>

ALLOWED_ORIGINS=https://sso.pdhc.se,https://app1.pdhc.se
ALLOWED_CALLBACK_URLS=https://app1.pdhc.se/callback

KEY_CREATED_AT=2026-03-18
LOG_DIR=/var/log/sso
```

#### Patient app (#1040): what the operator sets

Nothing here is done by a deploy script; all of it is operator-only. No values
are written in this document.

**In sso.pdhc's `.env`** (`shared/.env` on the server):

| variable | set to | why |
|---|---|---|
| `SSO_CLIENT_ID_PATIENTAPP` | a new client id | the `_PATIENTAPP` pair patientapp.pdhc presents on `/api/auth/me/service` |
| `SSO_CLIENT_SECRET_PATIENTAPP` | a new random secret | same pair. Both must be non-empty or the pair is not loaded |
| `ALLOWED_ORIGINS` | append `https://patientapp.pdhc.se` | the handshake only redirects to listed origins |
| `ALLOWED_CALLBACK_URLS` | optionally append patientapp's exact callback URL (take it from patientapp.pdhc's own docs) | exact-URL form of the same allowlist; the origin entry above is already sufficient |
| `WEBAUTHN_RP_ID` | `sso.pdhc.se` | the site passkeys are bound to |
| `WEBAUTHN_ORIGIN` | `https://sso.pdhc.se` | the origin a passkey answer must come from |
| `WEBAUTHN_RP_NAME` | optional, defaults to `PDHC` | shown by the device when saving a passkey |
| `PATIENTAPP_URL` | `https://patientapp.pdhc.se` | the "go to the patient app" link; empty hides it |
| `EPS_BASE_URL` | `https://eps.pdhc.se` | where SSO asks whether a patient guid exists and is listed at the care unit. No default: unset, every link is refused with `eps_not_configured` |
| `EPS_API_KEY` | a key minted **in eps** for sso (see below) | sent as `Authorization: ApiKey <key>` |

**Mint the eps key first.** In eps.pdhc, create an API key labelled for
sso.pdhc. It needs **no scope and no user**: the one route SSO calls
(`GET /api/v1/patients/<guid>/clinics`) is gated by eps's `require_auth`
alone, which admits any active, unexpired, unrevoked key. Give it no scopes,
so it opens none of eps's scoped routes. The key value goes into
`EPS_API_KEY` and nowhere else.

`EPS_LINK_CHECK_DISABLED` must **not** be set on the server: sso refuses to
start with it unless `FLASK_ENV=development`.

#### Patient self-registration (#1049): new keys

| variable | default | set to |
|---|---|---|
| `PATIENT_REGISTRATION_UNITS` | empty = every care unit | comma list of care-unit guids the registration form offers (the pilot's units). Narrows only; a guid that is not a care unit is ignored |
| `PATIENT_REGISTRATION_SYNTHETIC_ONLY` | `true` | leave `true` in the pilot: a personnummer with a VALID check digit is refused. Only the operator sets `false`, and only when the platform is cleared for real patients |
| `PATIENT_REGISTRATION_MIN_SECONDS` | `1.0` | minimum time of every non-error registration answer (no timing oracle). Raise it if eps round trips get slower than about half of it |

**The eps key now needs a scope.** Self-registration calls
`POST /api/v1/patients/self-register`, gated in eps by
`@require_scope("patients:self-register")`. Give SSO's existing eps key that
ONE scope (and no other), or registration answers "Försök igen om en stund."
for every new registrant (`eps_unavailable`, eps 403). The link check route
still needs no scope.

No new table needs data: `python scripts/init_db.py` creates
`patient_profiles` (additive; `patients` is not altered).

**In patientapp.pdhc's `.env`**: `SSO_CLIENT_ID` and `SSO_CLIENT_SECRET` with
the same two values as the pair above.

Then:

1. Rebuild the image (`docker-compose up -d --build`): `requirements.txt`
   gained `webauthn`, `pyotp` and `cryptography`. A plain `docker restart`
   neither installs them nor re-reads `.env`.
2. Take a `pg_dump`, then run `python scripts/init_db.py` in the app
   container. It creates the six new tables and touches nothing else.
3. Check, in this order:
    - `/api/health` answers 200.
    - The pair: `GET /api/auth/me/service` with a **valid** Bearer and the new
      pair answers 200, and the same call with a wrong secret answers 403. A
      junk Bearer answers 401 before the pair is looked at, so it proves
      nothing about the pair.
    - eps: as a professional, create an invite for a synthetic patient listed
      at your unit (expect the link), then for a made-up guid (expect
      `patient_not_found`). `eps_unavailable` on both means the key or the URL
      is wrong: eps answered 401/403 or did not answer.
    - Passkeys: log in as a synthetic patient and add a passkey at
      `/second-factor`. If the page says passkeys are not switched on, the two
      `WEBAUTHN_*` variables did not reach the container.

!!! warning "Do not change these afterwards without knowing the cost"
    - `WEBAUTHN_RP_ID`: changing it orphans every registered passkey.
    - `SECRET_KEY`: rotating it makes enrolled authenticator-app secrets
      unreadable (those patients enrol again; passkeys keep working) and voids
      outstanding invites and link codes — in addition to logging everyone out.

!!! warning
    Change `BOOTSTRAP_SU_PASSWORD` immediately after first login. Rotate `SECRET_KEY` if compromised — this invalidates all active JWTs.

!!! warning "Downstream services must not cache the access blob"
    After deploy, confirm that every integrated downstream service re-validates each protected request against `/api/auth/me/service`. Caching the blob silently defeats `must_change_password` (#43), the per-user session flush (#44), and direct phase grants/revocations (#46). See `subservice-onboarding.md` §4.2.

### `safe_restart.sh`

For restarting the service on the server without downtime issues:

```bash
cd app
chmod +x safe_restart.sh
./safe_restart.sh
```

This script:

1. Pulls latest code (if applicable)
2. Rebuilds containers
3. Runs DB migrations (if any)
4. Restarts services gracefully

---

## Reverse Proxy (nginx)

Reference configuration for `sso.pdhc.se`. The operator sets this manually on the server.

```nginx
server {
    listen 443 ssl;
    server_name sso.pdhc.se;

    ssl_certificate     /etc/letsencrypt/live/sso.pdhc.se/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/sso.pdhc.se/privkey.pem;

    # SSO service — isolated path prefix to avoid collision
    location / {
        proxy_pass http://127.0.0.1:9000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # Timeouts
        proxy_connect_timeout 10s;
        proxy_read_timeout 30s;
    }

    # Static docs (if built and served separately)
    location /docs/static/ {
        alias /path/to/app/docs/site/;
        expires 1d;
    }
}

# HTTP redirect
server {
    listen 80;
    server_name sso.pdhc.se;
    return 301 https://$host$request_uri;
}
```

!!! warning "Rule 22"
    The server reverse proxy is fragile. Ensure the SSO configuration is path-isolated and does not interfere with other services. Test in a staging environment before applying to production.

### DNS

Configure `sso.pdhc.se` to point to the macmini's IP before deployment. The DNS must be active before nginx can obtain an SSL certificate.

---

## First-Run Checklist

After deploying to a new environment:

- [ ] `.env` configured with all required variables
- [ ] Docker running, containers started
- [ ] `init_db.py` executed (creates tables)
- [ ] `create_su.py` executed (bootstrap admin account)
- [ ] `GET /api/health` returns 200
- [ ] `GET /fhir/metadata` returns valid CapabilityStatement
- [ ] Login with bootstrap SU succeeds
- [ ] Change bootstrap SU password immediately
- [ ] nginx reverse proxy configured and tested
- [ ] SSL certificate active
- [ ] `oath_overview.csv` populated with initial service entry
- [ ] Audit log directory exists and is writable
- [ ] Schema includes `users.force_change_on_next_login` (bool, default false) and `users.token_revocation_epoch` (timestamp, nullable) — migrations from #43/#44
- [ ] Schema includes `user_phases` table (columns: `id`, `guid`, `user_guid`, `phase`, `granted_by_guid`, `granted_at`) — migration from #46
- [ ] Spot-check a downstream `/me/service` call returns `must_change_password` and `effective_phases` fields in the blob
- [ ] `effective_phases` sourced only from `UserPhase` (#57) — verified via `scripts/phases_migration_report.py`, with SU having explicitly granted phases to every user who should retain access after the cutover
- [ ] Schema includes the six #1040 tables (`webauthn_credentials`, `totp_secrets`, `patient_links`, `patient_link_history`, `patient_invites`, `patient_link_codes`) — created by `init_db.py`
- [ ] Schema includes `login_lockouts` (#1040) — created by `init_db.py`. No `.env` variable: 5 failures / 15 minutes are constants in `src/services/login_lockout.py`
- [ ] `EPS_BASE_URL` / `EPS_API_KEY` set; an invite for a made-up patient guid answers `patient_not_found`, not `eps_unavailable` (#1040)
- [ ] `WEBAUTHN_RP_ID` / `WEBAUTHN_ORIGIN` set; a patient can add a passkey at `/second-factor` (#1040)
- [ ] Access-request approval on this deployment no longer auto-creates `UserPhase` rows — confirm by test-approving a request and checking the response includes `requested_phases_pending_su_grant` (#57)

---

## Backup and Restore

### Database Backup

```bash
docker exec sso_db pg_dump -U sso_user sso_db > backup_$(date -u +%Y%m%dT%H%M%SZ).sql
```

### Database Restore

```bash
docker exec -i sso_db psql -U sso_user sso_db < backup_20260318T120000Z.sql
```

### Application Data

Back up these files:

- `app/.env` — secrets and configuration
- `app/oath_overview.csv` — service registry
- `logs/` — audit logs (for compliance)

---

## Login rate limit and lockout (#1040)

Nothing to configure. After deploy:

- `init_db.py` creates the `login_lockouts` table (additive). Without the
  table every password login fails with a 500, so run it before sending
  traffic.
- The rate limit on `/login` counts per client address as the app sees it.
  Confirm the reverse proxy sets `X-Forwarded-For`; if it does not, or if
  another proxy sits in front, all users share one address and 20 logins a
  minute in total (about 40 with two workers) will produce `429` for everyone.
- Emergency unlock when no SU can log in: `python scripts/unlock_login.py
  <email>` in the app container.

## Log Rotation

Audit logs are written to `LOG_DIR` (default `./logs`). Configure logrotate on the server:

```
/var/log/sso/*.log {
    daily
    missingok
    rotate 90
    compress
    delaycompress
    notifempty
}
```

The audit log service writes structured JSON entries with daily rotation.

---

## API Key Management

| Key Type | Location | Rotation Frequency |
|----------|----------|--------------------|
| `SECRET_KEY` | `.env` | On compromise only (invalidates all JWTs) |
| `BOOTSTRAP_SU_PASSWORD` | `.env` | Change after first login |
| Service credentials | `.env` (`SSO_CLIENT_ID_*`) | Every 90 days |
| `KEY_CREATED_AT` | `.env` | Updated on each rotation |

### Rotation Procedure

1. Generate new credentials
2. Update `.env` with new values and `KEY_CREATED_AT`
3. Notify downstream services of new credentials
4. Restart SSO service (`safe_restart.sh`)
5. Verify downstream services can authenticate
6. Revoke old credentials (remove from `.env`)

---

## Port Allocation

| Port | Service | Protocol |
|------|---------|----------|
| 9000 | Flask/Gunicorn | HTTP |
| 9003 | PostgreSQL | TCP |
| 443 | nginx (SSL) | HTTPS |
| 80 | nginx (redirect) | HTTP |
