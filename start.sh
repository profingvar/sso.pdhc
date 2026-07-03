#!/usr/bin/env bash
# ============================================================
# sso.pdhc — start.sh
# Starts DB container + gunicorn. Docker must already be running.
# IMPORTANT: Do NOT kill -9 on Docker-forwarded ports (9003).
#            That kills Colima's port forwarding and breaks Docker.
# ============================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$SCRIPT_DIR/app"
VENV_DIR="$APP_DIR/venv"

export OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES

info()  { echo -e "\033[0;32m[SSO]\033[0m $*"; }
warn()  { echo -e "\033[1;33m[SSO]\033[0m $*"; }
err()   { echo -e "\033[0;31m[SSO]\033[0m $*"; }

# --- docker-compose wrapper ---
dc() {
    if docker compose version >/dev/null 2>&1; then
        docker compose "$@"
    else
        docker-compose "$@"
    fi
}

# ── 1. Docker must be running ────────────────────────────────
info "=== sso.pdhc starting ==="

if ! docker info >/dev/null 2>&1; then
    err "Docker is not running."
    err "  Run: bash /usr/local/www/restart_all.sh"
    exit 1
fi
info "Docker OK"

# ── 2. Stop existing ─────────────────────────────────────────
info "Stopping existing services..."

# Stop gunicorn first (port 9000 — host process, safe to kill)
if [ -f "$APP_DIR/gunicorn.pid" ]; then
    kill "$(cat "$APP_DIR/gunicorn.pid")" 2>/dev/null || true
    rm -f "$APP_DIR/gunicorn.pid"
    info "  Stopped gunicorn"
fi

# Stop Docker containers (releases port 9003 safely)
cd "$APP_DIR"
dc down 2>/dev/null || true

# Also try stopping any old containers under previous project name
docker stop sso_db sso_app 2>/dev/null || true
sleep 1

# ── 3. Virtual environment ───────────────────────────────────
if [ ! -d "$VENV_DIR" ]; then
    info "Creating virtual environment..."
    python3 -m venv "$VENV_DIR"
fi
source "$VENV_DIR/bin/activate"
info "Venv activated. Installing deps..."
pip install --quiet --upgrade pip
pip install --quiet -r "$APP_DIR/requirements.txt"
info "Dependencies ready."

# ── 4. Start DB ──────────────────────────────────────────────
info "Starting PostgreSQL container..."
cd "$APP_DIR"
dc up -d db

if [ $? -ne 0 ]; then
    err "Failed to start DB container."
    exit 1
fi

info "Waiting for DB..."
for i in $(seq 1 30); do
    if dc exec -T db pg_isready -U sso_user -d sso_db >/dev/null 2>&1; then
        info "PostgreSQL ready on port 9003."
        break
    fi
    if [ "$i" -eq 30 ]; then
        err "PostgreSQL did not become ready within 30s."
        exit 1
    fi
    sleep 1
done

# ── 5. Init DB + bootstrap SU ────────────────────────────────
mkdir -p "$APP_DIR/logs" "$APP_DIR/results"
info "Initialising database..."
cd "$APP_DIR"
python scripts/init_db.py
python scripts/create_su.py
info "Database ready."

# ── 6. Start gunicorn ────────────────────────────────────────
info "Starting gunicorn on port 9000..."
cd "$APP_DIR"
nohup gunicorn \
    --bind 127.0.0.1:9000 \
    --workers 1 \
    --timeout 120 \
    --max-requests 500 \
    --max-requests-jitter 50 \
    --access-logfile "$APP_DIR/logs/access.log" \
    --error-logfile "$APP_DIR/logs/error.log" \
    --pid "$APP_DIR/gunicorn.pid" \
    "src.app:create_app()" >> "$APP_DIR/logs/gunicorn.out" 2>&1 &

# ── 7. Health check ──────────────────────────────────────────
info "Waiting for gunicorn..."
HTTP_CODE="000"
for i in $(seq 1 15); do
    sleep 1
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:9000/api/health 2>/dev/null || echo "000")
    if [ "$HTTP_CODE" = "200" ]; then
        break
    fi
done

if [ "$HTTP_CODE" = "200" ]; then
    info "Health check: PASSED"
else
    warn "Health check: HTTP $HTTP_CODE — check $APP_DIR/logs/"
fi

info "=== sso.pdhc is running ==="
info "  App:  http://127.0.0.1:9000"
info "  DB:   localhost:9003"
info "  Logs: $APP_DIR/logs/"
