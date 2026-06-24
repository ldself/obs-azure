#!/bin/bash

# Phase 3 Validation: Non-Administrator Write Rejection (RULE 5, Phase 3 Conventions)
# ====================================================================================
# Confirms that an active, non-Administrator, non-System-Modeler user receives HTTP 403
# on every business-rules write endpoint (Security Spec v1.4 §9.1 Step 3/Step 4).
#
# Endpoints under test (all mutations, all four business-rules tables):
#   PUT    /api/v1/business-rules/merit-increase-rates/{fiscal_year}
#   PUT    /api/v1/business-rules/compensation-burden-rates/{fiscal_year}
#   PUT    /api/v1/business-rules/compensation-component-mappings/{component_code}
#   POST   /api/v1/business-rules/overhead-allocation-rates
#   PUT    /api/v1/business-rules/overhead-allocation-rates/{rate_id}
#   DELETE /api/v1/business-rules/overhead-allocation-rates/{rate_id}

set -uo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../" && pwd)"
VENV_DIR="/tmp/obs_validation_venv"
DB_PATH="/tmp/obs_test_biz_rules_403.duckdb"
TEST_PORT=8766
API_URL="http://localhost:$TEST_PORT"
API_PID=""
USER_ID="plain-active-user-001"

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

pass_count=0
fail_count=0
test_count=0

cleanup() {
    [ -n "$API_PID" ] && kill "$API_PID" 2>/dev/null || true
    wait "$API_PID" 2>/dev/null || true
    rm -f "$DB_PATH"
}
trap cleanup EXIT

if [ ! -d "$VENV_DIR" ]; then
    python3 -m venv "$VENV_DIR" >/dev/null 2>&1
    source "$VENV_DIR/bin/activate"
    pip install -q -r "$PROJECT_ROOT/backend/requirements.txt" 2>/dev/null
else
    source "$VENV_DIR/bin/activate"
fi

init_test_db() {
    python3 << PYSCRIPT
import duckdb, os, sys
conn = duckdb.connect("$DB_PATH")
try:
    with open(os.path.join("$PROJECT_ROOT", "schema", "bootstrap.sql")) as f:
        conn.execute(f.read())
    conn.execute(
        "INSERT INTO obs.users "
        "(user_id, display_name, email, is_active, is_administrator, "
        "is_system_modeler, is_report_developer, is_finance_reviewer, "
        "created_at, created_by, updated_at, updated_by) "
        "VALUES (?, ?, ?, TRUE, FALSE, FALSE, FALSE, FALSE, "
        "CURRENT_TIMESTAMP, 'system', CURRENT_TIMESTAMP, 'system')",
        ("$USER_ID", "Plain User", "$USER_ID@obs.local"),
    )
    conn.close()
except Exception as e:
    print(f"DB init error: {e}", file=sys.stderr)
    conn.close()
    sys.exit(1)
PYSCRIPT
}

start_api_server() {
    PYTHON_VER=$("$VENV_DIR/bin/python3" -c "import sys; v=sys.version_info; print(f'python{v.major}.{v.minor}')")
    export PYTHONPATH="$VENV_DIR/lib/$PYTHON_VER/site-packages:$PROJECT_ROOT"
    export LOCAL_AUTH_BYPASS="true"
    export LOCAL_AUTH_USER_ID="$USER_ID"
    export LOCAL_AUTH_USER_EMAIL="$USER_ID@obs.local"
    export LOCAL_AUTH_USER_NAME="Plain User"
    export DB_ENGINE="duckdb"
    export DUCKDB_PATH="$DB_PATH"
    export LOG_LEVEL="ERROR"

    "$VENV_DIR/bin/python3" -m uvicorn backend.app.main:app \
        --host localhost \
        --port "$TEST_PORT" \
        --log-level error \
        > /tmp/obs_biz_rules_api_test.log 2>&1 &
    API_PID=$!

    local max_retries=30
    local retry=0
    while [ $retry -lt $max_retries ]; do
        if ! kill -0 "$API_PID" 2>/dev/null; then
            echo "API server process exited unexpectedly" >&2
            cat /tmp/obs_biz_rules_api_test.log >&2
            return 1
        fi
        if curl -s "$API_URL/api/v1/me" >/dev/null 2>&1; then
            return 0
        fi
        sleep 0.5
        retry=$((retry + 1))
    done

    echo "API server failed to start" >&2
    cat /tmp/obs_biz_rules_api_test.log >&2
    return 1
}

# Assert a given HTTP method + URL returns 403. Pass an empty string for body on DELETE.
assert_403() {
    local method=$1
    local url=$2
    local body=$3
    local label=$4

    ((test_count++))
    echo -e "\n${YELLOW}[Test $test_count]${NC} $label"

    local args=(-s -o /tmp/obs_biz_403_body.txt -w "%{http_code}" -X "$method" -H "Content-Type: application/json")
    [ -n "$body" ] && args+=(-d "$body")

    local http_code
    http_code=$(curl "${args[@]}" "$url" 2>/dev/null)
    local response_body
    response_body=$(cat /tmp/obs_biz_403_body.txt 2>/dev/null || echo "")

    if [ "$http_code" = "403" ]; then
        echo -e "${GREEN}✓ PASS${NC} – Got expected 403 Forbidden"
        ((pass_count++))
    else
        echo -e "${RED}✗ FAIL${NC} – Expected 403, got $http_code"
        [ -n "$response_body" ] && echo "  Response: $response_body" >&2
        ((fail_count++))
    fi
}

echo "========================================================================"
echo "Phase 3: Non-Administrator Write Rejection (RULE 5 / Phase 3 Conventions)"
echo "========================================================================"
echo "User: $USER_ID"
echo "Flags: is_active=TRUE  is_administrator=FALSE  is_system_modeler=FALSE"
echo ""

if ! init_test_db; then
    echo -e "${RED}FATAL: Database initialization failed.${NC}" >&2
    exit 1
fi

if ! start_api_server; then
    echo -e "${RED}FATAL: API server failed to start.${NC}" >&2
    exit 1
fi

echo "API server ready on $API_URL"

FAKE_UUID="00000000-0000-0000-0000-000000000099"

MERIT_BODY='{"rate_pct":"3.0000","effective_date":"2026-01-01"}'

BURDEN_BODY='{"fica_rate_pct":"6.2","fica_wage_cap":"160200","medicare_rate_pct":"1.45","state_income_tax_rate_pct":"5.0","federal_income_tax_rate_pct":"22.0","suta_rate_pct":"0.6","suta_wage_cap":"7000","futa_rate_pct":"0.6","futa_wage_cap":"7000","other_benefits_rate_pct":"3.0"}'

MAPPING_BODY='{"account_code":"54321"}'

OVERHEAD_POST_BODY='{"account_code":"OH-001","geography_code":"US-WEST","amount_per_employee":"500.00","rate_period":"Monthly","fiscal_year":2026}'

OVERHEAD_PUT_BODY='{"amount_per_employee":"600.00"}'

assert_403 "PUT" \
    "$API_URL/api/v1/business-rules/merit-increase-rates/2026" \
    "$MERIT_BODY" \
    "PUT merit-increase-rates/{fiscal_year}"

assert_403 "PUT" \
    "$API_URL/api/v1/business-rules/compensation-burden-rates/2026" \
    "$BURDEN_BODY" \
    "PUT compensation-burden-rates/{fiscal_year}"

assert_403 "PUT" \
    "$API_URL/api/v1/business-rules/compensation-component-mappings/SALARY" \
    "$MAPPING_BODY" \
    "PUT compensation-component-mappings/{component_code}"

assert_403 "POST" \
    "$API_URL/api/v1/business-rules/overhead-allocation-rates" \
    "$OVERHEAD_POST_BODY" \
    "POST overhead-allocation-rates (create)"

assert_403 "PUT" \
    "$API_URL/api/v1/business-rules/overhead-allocation-rates/$FAKE_UUID" \
    "$OVERHEAD_PUT_BODY" \
    "PUT overhead-allocation-rates/{rate_id} (update)"

assert_403 "DELETE" \
    "$API_URL/api/v1/business-rules/overhead-allocation-rates/$FAKE_UUID" \
    "" \
    "DELETE overhead-allocation-rates/{rate_id} (deactivate)"

echo ""
echo "========================================================================"
echo -e "Results: ${GREEN}$pass_count passed${NC}, ${RED}$fail_count failed${NC} (of $test_count tests)"
echo "========================================================================"

[ $fail_count -eq 0 ] && echo -e "${GREEN}All tests passed!${NC}" && exit 0
echo -e "${RED}Some tests failed.${NC}" && exit 1
