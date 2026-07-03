#!/usr/bin/env bash
# ============================================================
# sso.pdhc — start.sh
# Single entry point: kills owned ports, starts DB + app.
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$SCRIPT_DIR/app"
VENV_DIR="$APP_DIR/venv"
PORTS=(9000 9001 9002 9003)
PREFLIGHT_OK=true

# --- macOS fork safety (must be set before any fork/gunicorn) ---
export OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES

# --- Colima Docker socket ---
colima_sock="$HOME/.colima/default/docker.sock"
if [ -S "$colima_sock" ]; then
    export DOCKER_HOST="unix://$colima_sock"
fi

# --- Colors ---
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; }
check() { echo -e "${CYAN}[CHECK]${NC} $*"; }

# --- docker-compose wrapper (v2 plugin or standalone) ---
dc() {
    if docker compose version >/dev/null 2>&1; then
        docker compose "$@"
    else
        docker-compose "$@"
    fi
}

# ============================================================
# Pre-flight checks
# ============================================================
preflight() {
    echo ""
    info "=========================================="
    info "  sso.pdhc — Pre-flight checks"
    info "=========================================="
    echo ""

    # 1. Required files
    check "Required files..."
    local required_files=(
        "$APP_DIR/docker-compose.yml"
        "$APP_DIR/requirements.txt"
        "$APP_DIR/.env"
        "$APP_DIR/scripts/init_db.py"
        "$APP_DIR/scripts/create_su.py"
    )
    for f in "${required_files[@]}"; do
        if [ ! -f "$f" ]; then
            error "  Missing: $f"
            if [[ "$f" == *".env" ]]; then
                error "  → Copy from .env.example:  cp $APP_DIR/.env.example $APP_DIR/.env"
            fi
            PREFLIGHT_OK=false
        else
            info "  OK: $(basename "$f")"
        fi
    done

    # 2. Python3
    check "Python3..."
    if command -v python3 >/dev/null 2>&1; then
        info "  OK: $(python3 --version 2>&1)"
    else
        error "  python3 not found. Install: brew install python3"
        PREFLIGHT_OK=false
    fi

    # 3. venv module
    check "Python venv module..."
    if python3 -m venv --help >/dev/null 2>&1; then
        info "  OK: venv module available"
    else
        error "  venv module not available. Install: brew install python3"
        PREFLIGHT_OK=false
    fi

    # 4. Docker runtime
    check "Docker runtime..."
    ensure_docker

    # 5. docker-compose
    check "docker-compose..."
    if docker compose version >/dev/null 2>&1; then
        info "  OK: $(docker compose version 2>&1)"
    elif command -v docker-compose >/dev/null 2>&1; then
        info "  OK: $(docker-compose --version 2>&1)"
    else
        error "  Neither 'docker compose' nor 'docker-compose' found."
        PREFLIGHT_OK=false
    fi

    # 6. Required .env variables
    check "Required .env variables..."
    if [ -f "$APP_DIR/.env" ]; then
        local required_vars=(SECRET_KEY DATABASE_URL BOOTSTRAP_SU_EMAIL BOOTSTRAP_SU_PASSWORD)
        for var in "${required_vars[@]}"; do
            if grep -q "^${var}=" "$APP_DIR/.env" 2>/dev/null; then
                local val
                val=$(grep "^${var}=" "$APP_DIR/.env" | cut -d'=' -f2-)
                if [[ "$val" == "change-me"* && "$var" != "BOOTSTRAP_SU_PASSWORD" ]]; then
                    warn "  $var still has placeholder value"
                else
                    info "  OK: $var is set"
                fi
            else
                error "  Missing variable: $var in .env"
                PREFLIGHT_OK=false
            fi
        done
    fi

    # 7. Port status
    check "Port status (9000-9003)..."
    for port in "${PORTS[@]}"; do
        local pids
        pids=$(lsof -ti :"$port" 2>/dev/null || true)
        if [ -n "$pids" ]; then
            warn "  Port $port in use (PID: $pids) — will be freed"
        else
            info "  OK: Port $port is free"
        fi
    done

    # 8. Disk space
    check "Disk space..."
    local avail_kb
    avail_kb=$(df -k "$SCRIPT_DIR" | awk 'NR==2{print $4}')
    if [ "$avail_kb" -lt 1048576 ] 2>/dev/null; then
        warn "  Less than 1 GB free disk space"
    else
        info "  OK: $(( avail_kb / 1048576 )) GB free"
    fi

    echo ""
    if [ "$PREFLIGHT_OK" = false ]; then
        error "=========================================="
        error "  Pre-flight FAILED — fix errors above"
        error "=========================================="
        exit 1
    fi
    info "=========================================="
    info "  All pre-flight checks passed"
    info "=========================================="
    echo ""
}

# --- Ensure Docker is available (Colima or Docker Desktop) ---
ensure_docker() {
    if docker info >/dev/null 2>&1; then
        info "  OK: Docker is running"
        return 0
    fi

    # Try Colima
    if command -v colima >/dev/null 2>&1; then
        _start_colima "$colima_sock"
        return $?
    fi

    # Try Docker Desktop
    if [ -d "/Applications/Docker.app" ]; then
        _start_docker_desktop
        return $?
    fi

    error "  Docker is not installed or not reachable."
    error "  → brew install colima docker docker-compose && colima start"
    PREFLIGHT_OK=false
    return 1
}

# --- Start Colima with auto-recovery ---
_start_colima() {
    local colima_sock="$1"
    local max_attempts=3
    local attempt=0

    while [ $attempt -lt $max_attempts ]; do
        attempt=$((attempt + 1))
        warn "  Starting Colima (attempt $attempt/$max_attempts)..."

        local colima_output
        colima_output=$(colima start 2>&1) || true

        while IFS= read -r line; do
            [ -n "$line" ] && info "  colima: $line"
        done <<< "$colima_output"

        if [ -S "$colima_sock" ]; then
            export DOCKER_HOST="unix://$colima_sock"
        fi
        sleep 2
        if docker info >/dev/null 2>&1; then
            info "  OK: Colima started — Docker is now running"
            return 0
        fi

        if echo "$colima_output" | grep -qi "exiting\|fatal\|exit status\|already running\|stale"; then
            warn "  Colima VM is in a bad state. Cleaning up..."
            colima stop 2>/dev/null || true
            sleep 1
            if [ $attempt -ge 2 ]; then
                warn "  Deleting stale Colima VM..."
                colima delete --force 2>/dev/null || true
                local lima_dir="$HOME/.colima/_lima/colima"
                if [ -d "$lima_dir" ]; then
                    rm -f "$lima_dir/ha.sock" "$lima_dir"/*.pid 2>/dev/null || true
                fi
                sleep 2
            fi
        else
            break
        fi
    done

    error "  Failed to start Colima after $max_attempts attempts."
    error "  Manual recovery: colima delete --force && colima start"
    PREFLIGHT_OK=false
    return 1
}

# --- Start Docker Desktop (fallback for dev machines) ---
_start_docker_desktop() {
    warn "  Starting Docker Desktop..."
    open -a Docker
    local waited=0
    while [ $waited -lt 60 ]; do
        if docker info >/dev/null 2>&1; then
            info "  OK: Docker Desktop is now running"
            return 0
        fi
        sleep 2
        waited=$((waited + 2))
    done
    error "  Docker Desktop did not start within 60s."
    PREFLIGHT_OK=false
    return 1
}

# --- Stop existing services ---
stop_existing() {
    info "Stopping existing services..."
    cd "$APP_DIR" && dc down 2>/dev/null || true
    for port in 9000 9001 9002; do
        pids=$(lsof -ti :"$port" 2>/dev/null || true)
        if [ -n "$pids" ]; then
            echo "$pids" | xargs kill -9 2>/dev/null || true
            info "  Killed process(es) on port $port"
        fi
    done
    if [ -f "$APP_DIR/gunicorn.pid" ]; then
        kill "$(cat "$APP_DIR/gunicorn.pid")" 2>/dev/null || true
        rm -f "$APP_DIR/gunicorn.pid"
    fi
    sleep 1
}

# --- Ensure and activate venv ---
activate_venv() {
    if [ ! -d "$VENV_DIR" ]; then
        info "Creating virtual environment at $VENV_DIR..."
        python3 -m venv "$VENV_DIR"
    fi
    source "$VENV_DIR/bin/activate"
    info "Virtual environment activated."
    info "Installing/updating dependencies..."
    pip install --quiet --upgrade pip
    pip install --quiet -r "$APP_DIR/requirements.txt"
    info "Dependencies ready."
}

# ============================================================
# Main
# ============================================================
info "=== sso.pdhc starting ==="

preflight
stop_existing
activate_venv

# Start DB
info "Starting PostgreSQL container..."
cd "$APP_DIR"
dc up -d db
info "Waiting for DB to be healthy..."
db_waited=0
until dc exec db pg_isready -U sso_user -d sso_db >/dev/null 2>&1; do
    sleep 1
    db_waited=$((db_waited + 1))
    if [ $db_waited -ge 30 ]; then
        error "PostgreSQL did not become ready within 30s. Check: docker logs sso_db"
        exit 1
    fi
done
info "PostgreSQL is ready on port 9003."

# Create required directories
mkdir -p "$APP_DIR/logs"
mkdir -p "$APP_DIR/results"

# Initialise DB tables and bootstrap SU (idempotent)
info "Initialising database (idempotent)..."
cd "$APP_DIR"
python scripts/init_db.py
python scripts/create_su.py
info "Database ready."

# Start gunicorn (background via nohup — not --daemon which crashes on macOS)
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

# Wait for health check
info "Waiting for gunicorn to boot..."
health_waited=0
HTTP_CODE="000"
while [ $health_waited -lt 15 ]; do
    sleep 1
    health_waited=$((health_waited + 1))
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:9000/api/health 2>/dev/null || echo "000")
    if [ "$HTTP_CODE" = "200" ]; then
        break
    fi
done

if [ "$HTTP_CODE" = "200" ]; then
    info "Health check: PASSED (HTTP 200)"
else
    warn "Health check: HTTP $HTTP_CODE — check logs at $APP_DIR/logs/"
fi

info "=== sso.pdhc is running ==="
info "  App:  http://127.0.0.1:9000"
info "  DB:   localhost:9003"
info "  PID:  $APP_DIR/gunicorn.pid"
info "  Logs: $APP_DIR/logs/"
info "  Stop: $SCRIPT_DIR/stop.sh"
