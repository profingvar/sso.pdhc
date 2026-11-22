#!/usr/bin/env bash
# =============================================================================
# test_endpoints.sh — Test all API endpoints against the capability statement
#
# Usage: ./scripts/test_endpoints.sh [BASE_URL]
#        Default BASE_URL: http://localhost:9000
#
# Generates report in results/<timestamp>_results/endpoint_test_report.txt
# =============================================================================
set -euo pipefail

BASE_URL="${1:-http://localhost:9000}"
TIMESTAMP=$(date -u +"%Y-%m-%dT%H-%M-%SZ")
RESULTS_DIR="$(cd "$(dirname "$0")/.." && pwd)/results/${TIMESTAMP}_results"
mkdir -p "$RESULTS_DIR"
REPORT="$RESULTS_DIR/endpoint_test_report.txt"

PASS=0
FAIL=0
TOTAL=0

# Colours (if terminal supports)
GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

log() {
    echo "$1" | tee -a "$REPORT"
}

check() {
    local desc="$1"
    local method="$2"
    local endpoint="$3"
    local expected_code="$4"
    shift 4
    local extra_args=("$@")

    TOTAL=$((TOTAL + 1))
    local actual_code
    actual_code=$(curl -s -o /dev/null -w "%{http_code}" -X "$method" "${extra_args[@]}" "${BASE_URL}${endpoint}" 2>/dev/null || echo "000")

    if [ "$actual_code" = "$expected_code" ]; then
        PASS=$((PASS + 1))
        log "  PASS  $method $endpoint ($actual_code) — $desc"
    else
        FAIL=$((FAIL + 1))
        log "  FAIL  $method $endpoint — expected $expected_code, got $actual_code — $desc"
    fi
}

log "============================================================"
log "sso.pdhc SSO — Endpoint Test Report"
log "Base URL: $BASE_URL"
log "Timestamp: $TIMESTAMP"
log "============================================================"
log ""

# -------------------------------------------------------------------
# Health
# -------------------------------------------------------------------
log "--- Health ---"
check "Health endpoint" GET "/api/health" "200"
log ""

# -------------------------------------------------------------------
# FHIR Metadata
# -------------------------------------------------------------------
log "--- FHIR Metadata ---"
check "CapabilityStatement" GET "/fhir/metadata" "200"
log ""

# -------------------------------------------------------------------
# Auth — unauthenticated
# -------------------------------------------------------------------
log "--- Auth (unauthenticated) ---"
check "Login without credentials" POST "/api/auth/login" "400" \
    -H "Content-Type: application/json" -d '{}'
check "/me without token" GET "/api/auth/me" "401"
check "/me/service without token" GET "/api/auth/me/service" "401"
check "Logout without token" POST "/api/auth/logout" "401"
check "Change password without token" POST "/api/auth/change-password" "401"
log ""

# -------------------------------------------------------------------
# Auth — login + authenticated
# -------------------------------------------------------------------
log "--- Auth (authenticated — requires running app with seed data) ---"

# Try to login with bootstrap SU if available
TOKEN=""
LOGIN_RESP=$(curl -s -X POST "${BASE_URL}/api/auth/login" \
    -H "Content-Type: application/json" \
    -d '{"email":"admin@pdhc.se","password":"changeme01"}' 2>/dev/null || echo '{}')
TOKEN=$(echo "$LOGIN_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('token',''))" 2>/dev/null || echo "")

if [ -n "$TOKEN" ]; then
    AUTH="-H Authorization: Bearer $TOKEN"
    check "Login success" POST "/api/auth/login" "200" \
        -H "Content-Type: application/json" -d '{"email":"admin@pdhc.se","password":"changeme01"}'
    check "/me with valid token" GET "/api/auth/me" "200" -H "Authorization: Bearer $TOKEN"
    check "/me/service missing service creds" GET "/api/auth/me/service" "403" -H "Authorization: Bearer $TOKEN"
    check "Change password (missing fields)" POST "/api/auth/change-password" "400" \
        -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{}'

    # Logout (last — revokes token)
    check "Logout" POST "/api/auth/logout" "200" -H "Authorization: Bearer $TOKEN"
    check "/me after logout (revoked)" GET "/api/auth/me" "401" -H "Authorization: Bearer $TOKEN"
else
    log "  SKIP  No login token available — skipping authenticated auth tests"
    log "        (Start the app with a seeded DB to test authenticated endpoints)"
fi
log ""

# -------------------------------------------------------------------
# Public endpoints (no auth required)
# -------------------------------------------------------------------
log "--- Public Endpoints ---"
check "Public organisations" GET "/api/public/organisations" "200"
check "Public groups" GET "/api/public/groups" "200"
check "Public group leaders" GET "/api/public/group-leaders" "200"
check "Access request (missing data)" POST "/api/public/access-request" "400" \
    -H "Content-Type: application/json" -d '{}'
log ""

# -------------------------------------------------------------------
# Patient endpoints (require auth)
# -------------------------------------------------------------------
log "--- Patient Endpoints (require auth) ---"
check "Register patient (missing data)" POST "/api/patient/register" "400" \
    -H "Content-Type: application/json" -d '{}'
check "Registry status (no auth)" GET "/api/patient/registry-status" "401"

# #1040: patient link + second factor. No-auth probes only: each must refuse.
check "Link code (no auth)" POST "/api/patient/link-code" "401"
check "Patient invite (no auth)" POST "/api/patient-links/invites" "401"
check "Redeem link code (no auth)" POST "/api/patient-links/redeem" "401"
check "Second-factor status (no auth)" GET "/api/auth/second-factor" "401"
check "Unknown patient invite" GET "/patient-invite/not-a-real-token" "404"
log ""

# -------------------------------------------------------------------
# Group endpoints (require auth)
# -------------------------------------------------------------------
log "--- Group Endpoints (require auth) ---"
check "List groups (no auth)" GET "/api/groups" "401"
check "Request membership (no auth)" POST "/api/groups/request-membership" "401"
check "Request admin (no auth)" POST "/api/groups/request-admin" "401"
check "Admin pending (no auth)" GET "/api/groups/admin/pending" "401"
check "Admin decide (no auth)" POST "/api/groups/admin/decide" "401"
check "Admin invite (no auth)" POST "/api/groups/admin/invite" "401"
check "Join by invite (no auth)" POST "/api/groups/join-by-invite" "401"
log ""

# -------------------------------------------------------------------
# Admin endpoints (require SU)
# -------------------------------------------------------------------
log "--- Admin Endpoints (require SU auth) ---"
check "Admin users (no auth)" GET "/api/admin/users" "401"
check "Admin promote-su (no auth)" POST "/api/admin/promote-su" "401"
check "Admin delete user (no auth)" DELETE "/api/admin/users/fake-guid" "401"
check "Admin delete group (no auth)" DELETE "/api/admin/groups/fake-guid" "401"
check "Admin assign-group-admin (no auth)" POST "/api/admin/assign-group-admin" "401"
check "Admin group-proposals (no auth)" GET "/api/admin/group-proposals" "401"
check "Admin leader-requests (no auth)" GET "/api/admin/leader-requests" "401"
check "Admin access-requests (no auth)" GET "/api/admin/access-requests" "401"
check "Admin organisations (no auth)" GET "/api/admin/organisations" "401"
check "Admin export-users (no auth)" GET "/api/admin/export-users" "401"
check "Admin oath-overview (no auth)" GET "/api/admin/oath-overview" "401"
log ""

# -------------------------------------------------------------------
# Frontend pages (should render HTML)
# -------------------------------------------------------------------
log "--- Frontend Pages ---"
check "Landing page" GET "/" "200"
check "Login page" GET "/login" "200"
check "Register patient page" GET "/register-patient" "200"
check "Request access page" GET "/request-access" "200"
check "Docs page" GET "/docs" "200"
log ""

# -------------------------------------------------------------------
# Summary
# -------------------------------------------------------------------
log "============================================================"
log "SUMMARY: $PASS passed, $FAIL failed, $TOTAL total"
log "Report saved to: $REPORT"
log "============================================================"

if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
exit 0
