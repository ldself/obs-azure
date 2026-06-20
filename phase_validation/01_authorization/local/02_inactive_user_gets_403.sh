#!/bin/bash

# Phase 1 Validation: Inactive User Authorization (RULE 5, Security Spec v1.4 §9.2)
# ===================================================================================
# Confirms that a user with no active OBS registry record gets 403 on every
# authenticated request. Tests both:
#   1. User with no OBS registry record at all (Step 2 of 7-step flow)
#   2. User with is_active = FALSE (Step 2 of 7-step flow)

set -uo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../" && pwd)"
VENV_DIR="/tmp/obs_validation_venv"
DB_PATH="/tmp/obs_test_validation.duckdb"
API_URL="http://localhost:8000"
API_PID=""

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

pass_count=0
fail_count=0
test_count=0

# Cleanup on exit
cleanup() {
    [ -n "$API_PID" ] && kill "$API_PID" 2>/dev/null || true
    wait "$API_PID" 2>/dev/null || true
    rm -f "$DB_PATH"
}
trap cleanup EXIT

# Setup venv
if [ ! -d "$VENV_DIR" ]; then
    python3 -m venv "$VENV_DIR" >/dev/null 2>&1
    source "$VENV_DIR/bin/activate"
    pip install -q -r "$PROJECT_ROOT/backend/requirements.txt" 2>/dev/null
else
    source "$VENV_DIR/bin/activate"
fi

# Initialize test database with schema and optional test user
init_test_db() {
    local user_id=$1
    local is_active=$2

    python3 << PYSCRIPT
import duckdb
import os
db_path = "$DB_PATH"
project_root = "$PROJECT_ROOT"

# Connect and create schema
conn = duckdb.connect(db_path)
try:
    # Execute bootstrap schema
    schema_file = os.path.join(project_root, 'schema', 'bootstrap.sql')
    with open(schema_file) as f:
        conn.execute(f.read())

    # Seed a test user if requested
    if "$is_active" != "":
        is_active_val = int("$is_active") != 0
        conn.execute(
            "INSERT INTO obs.users (user_id, display_name, email, is_active, is_administrator, "
            "is_system_modeler, is_report_developer, is_finance_reviewer, created_at, created_by, "
            "updated_at, updated_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, 'system', CURRENT_TIMESTAMP, 'system')",
            ("$user_id", "Test User", "$user_id@obs.local", is_active_val, False, False, False, False)
        )

    conn.close()
except Exception as e:
    print(f"DB init error: {e}")
    import traceback
    traceback.print_exc()
    conn.close()
    raise
PYSCRIPT
}

# Start the API server with test configuration
start_api_server() {
    local user_id=$1

    export PYTHONPATH="$VENV_DIR/lib/python3.11/site-packages:$PROJECT_ROOT"
    export LOCAL_AUTH_BYPASS="true"
    export LOCAL_AUTH_USER_ID="$user_id"
    export LOCAL_AUTH_USER_EMAIL="$user_id@obs.local"
    export LOCAL_AUTH_USER_NAME="Test User"
    export DB_ENGINE="duckdb"
    export DUCKDB_PATH="$DB_PATH"
    export LOG_LEVEL="ERROR"

    # Start API server in background
    "$VENV_DIR/bin/python3" -m uvicorn backend.app.main:app \
        --host localhost \
        --port 8000 \
        --log-level error \
        > /tmp/obs_api_test.log 2>&1 &
    API_PID=$!

    # Wait for server to be ready
    local max_retries=30
    local retry=0
    while [ $retry -lt $max_retries ]; do
        if curl -s "$API_URL/api/v1/me" >/dev/null 2>&1; then
            return 0
        fi
        sleep 0.5
        retry=$((retry + 1))
    done

    echo "API server failed to start" >&2
    cat /tmp/obs_api_test.log >&2
    return 1
}

# Helper to run a single test
test_case() {
    local num=$1
    local name=$2
    local user_id=$3
    local is_active=$4
    local endpoint=$5

    ((test_count++))
    echo -e "\n${YELLOW}[Test $test_count]${NC} $name"

    # Kill previous API instance
    [ -n "$API_PID" ] && kill "$API_PID" 2>/dev/null || true
    wait "$API_PID" 2>/dev/null || true
    sleep 1
    API_PID=""

    # Remove old database
    rm -f "$DB_PATH"

    # Initialize database
    if ! init_test_db "$user_id" "$is_active"; then
        echo -e "${RED}✗ FAIL${NC} – Database initialization failed"
        ((fail_count++))
        return
    fi

    # Start fresh API server
    if ! start_api_server "$user_id"; then
        echo -e "${RED}✗ FAIL${NC} – API server failed to start"
        ((fail_count++))
        return
    fi

    # Make request to endpoint
    local http_code=$(curl -s -o /tmp/response_body.txt -w "%{http_code}" "$API_URL$endpoint" 2>/dev/null)
    local body=$(cat /tmp/response_body.txt 2>/dev/null || echo "")

    # Verify 403 response
    if [ "$http_code" = "403" ]; then
        echo -e "${GREEN}✓ PASS${NC} – Got expected 403 Forbidden"
        ((pass_count++))
    else
        echo -e "${RED}✗ FAIL${NC} – Expected 403, got $http_code"
        if [ -n "$body" ]; then
            echo "Response body: $body" >&2
        fi
        ((fail_count++))
    fi
}

echo "=============================================="
echo "Inactive User Authorization (Security §9.2)"
echo "=============================================="

# Test 1: User with no OBS registry record at all (empty string for is_active means don't seed)
test_case 1 "User with no registry record (Step 2)" "nonexistent-user-001" "" "/api/v1/me"

# Test 2: User with is_active = FALSE (seed user with is_active=0)
test_case 2 "User with is_active = FALSE (Step 2)" "test-inactive-user" "0" "/api/v1/me"

# Test 3: Another endpoint with no registry record (admin-only endpoint)
test_case 3 "No registry on GET /users (admin only)" "nonexistent-user-002" "" "/api/v1/users"

# Test 4: Another endpoint with is_active = FALSE (admin-only endpoint)
test_case 4 "Inactive on GET /users (admin only)" "test-inactive-user-2" "0" "/api/v1/users"

echo ""
echo "=============================================="
echo -e "Results: ${GREEN}$pass_count passed${NC}, ${RED}$fail_count failed${NC} (of $test_count tests)"
echo "=============================================="

[ $fail_count -eq 0 ] && echo -e "${GREEN}All tests passed!${NC}" && exit 0
echo -e "${RED}Some tests failed.${NC}" && exit 1
