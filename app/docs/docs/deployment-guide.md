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
