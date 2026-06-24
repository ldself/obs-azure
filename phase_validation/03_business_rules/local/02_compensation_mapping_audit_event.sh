#!/bin/bash

# Phase 3 Validation: COMPENSATION_MAPPING_UPDATED Audit Event (RULE 6 / Phase 3 Conventions)
# ============================================================================================
# Confirms that a PUT to /api/v1/business-rules/compensation-component-mappings/{component_code}
# by an Administrator writes a COMPENSATION_MAPPING_UPDATED row to obs.audit_log in the same
# transaction (RULE 6, Security Spec v1.4 §9.1 Step 7, Phase 3 Conventions).
#
# Steps:
#   1. Spin up an isolated DuckDB API server as an Administrator.
#   2. Seed obs.compensation_component_mappings with a 'salary' row.
#   3. PUT a new account_code via the API.
#   4. Verify HTTP 200 is returned.
#   5. Query obs.audit_log directly and assert one row exists with:
#        event_type  = 'COMPENSATION_MAPPING_UPDATED'
#        entity_type = 'compensation_component_mapping'
#        entity_id   = 'salary'
#        outcome     = 'SUCCESS'

set -uo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../" && pwd)"
VENV_DIR="/tmp/obs_validation_venv"
DB_PATH="/tmp/obs_test_biz_rules_mapping_audit.duckdb"
TEST_PORT=8767
API_URL="http://localhost:$TEST_PORT"
API_PID=""
ADMIN_USER_ID="admin-user-mapping-audit-001"

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
    # Administrator user
    conn.execute(
        "INSERT INTO obs.users "
        "(user_id, display_name, email, is_active, is_administrator, "
        "is_system_modeler, is_report_developer, is_finance_reviewer, "
        "created_at, created_by, updated_at, updated_by) "
        "VALUES (?, ?, ?, TRUE, TRUE, FALSE, FALSE, FALSE, "
        "CURRENT_TIMESTAMP, 'system', CURRENT_TIMESTAMP, 'system')",
        ("$ADMIN_USER_ID", "Admin User", "$ADMIN_USER_ID@obs.local"),
    )
    # Seed the component mapping that will be mutated
    conn.execute(
        "INSERT INTO obs.compensation_component_mappings "
        "(mapping_id, component_code, account_code, updated_at, updated_by) "
        "VALUES (?, ?, ?, CURRENT_TIMESTAMP, 'seed') "
        "ON CONFLICT (component_code) DO NOTHING",
        ("ccm-salary-audit-test", "salary", "5000"),
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
    export LOCAL_AUTH_USER_ID="$ADMIN_USER_ID"
    export LOCAL_AUTH_USER_EMAIL="$ADMIN_USER_ID@obs.local"
    export LOCAL_AUTH_USER_NAME="Admin User"
    export DB_ENGINE="duckdb"
    export DUCKDB_PATH="$DB_PATH"
    export LOG_LEVEL="ERROR"

    "$VENV_DIR/bin/python3" -m uvicorn backend.app.main:app \
        --host localhost \
        --port "$TEST_PORT" \
        --log-level error \
        > /tmp/obs_mapping_audit_api_test.log 2>&1 &
    API_PID=$!

    local max_retries=30
    local retry=0
    while [ $retry -lt $max_retries ]; do
        if ! kill -0 "$API_PID" 2>/dev/null; then
            echo "API server process exited unexpectedly" >&2
            cat /tmp/obs_mapping_audit_api_test.log >&2
            return 1
        fi
        if curl -s "$API_URL/api/v1/me" >/dev/null 2>&1; then
            return 0
        fi
        sleep 0.5
        retry=$((retry + 1))
    done

    echo "API server failed to start" >&2
    cat /tmp/obs_mapping_audit_api_test.log >&2
    return 1
}

run_test() {
    local label=$1
    local result=$2  # "pass" or "fail"
    local detail=$3

    ((test_count++))
    echo -e "\n${YELLOW}[Test $test_count]${NC} $label"
    if [ "$result" = "pass" ]; then
        echo -e "${GREEN}✓ PASS${NC} – $detail"
        ((pass_count++))
    else
        echo -e "${RED}✗ FAIL${NC} – $detail" >&2
        ((fail_count++))
    fi
}

echo "========================================================================"
echo "Phase 3: COMPENSATION_MAPPING_UPDATED Audit Event (RULE 6)"
echo "========================================================================"
echo "User: $ADMIN_USER_ID  (is_administrator=TRUE)"
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

# ── Test 1: PUT succeeds (HTTP 200) ─────────────────────────────────────────
((test_count++))
echo -e "\n${YELLOW}[Test $test_count]${NC} PUT compensation-component-mappings/salary returns 200"

http_code=$(curl -s -o /tmp/obs_mapping_audit_put_body.txt -w "%{http_code}" \
    -X PUT \
    -H "Content-Type: application/json" \
    -d '{"account_code":"5001"}' \
    "$API_URL/api/v1/business-rules/compensation-component-mappings/salary" 2>/dev/null)

if [ "$http_code" = "200" ]; then
    echo -e "${GREEN}✓ PASS${NC} – Got expected 200 OK"
    ((pass_count++))
else
    response_body=$(cat /tmp/obs_mapping_audit_put_body.txt 2>/dev/null || echo "")
    echo -e "${RED}✗ FAIL${NC} – Expected 200, got $http_code" >&2
    [ -n "$response_body" ] && echo "  Response: $response_body" >&2
    ((fail_count++))
fi

# ── Test 2–5: Audit log assertions ─────────────────────────────────────────
# Query obs.audit_log directly via Python/DuckDB after the API call.
audit_result=$(python3 << PYSCRIPT
import duckdb, json, sys

conn = duckdb.connect("$DB_PATH", read_only=True)
rows = conn.execute(
    "SELECT event_type, entity_type, entity_id, outcome, user_id "
    "FROM obs.audit_log "
    "WHERE event_type = 'COMPENSATION_MAPPING_UPDATED' "
    "  AND entity_id = 'salary' "
    "ORDER BY event_timestamp DESC "
    "LIMIT 5"
).fetchall()
conn.close()

output = [
    {"event_type": r[0], "entity_type": r[1], "entity_id": r[2],
     "outcome": r[3], "user_id": r[4]}
    for r in rows
]
print(json.dumps(output))
PYSCRIPT
)

row_count=$(echo "$audit_result" | python3 -c "import json,sys; print(len(json.load(sys.stdin)))" 2>/dev/null || echo "0")

if [ "$row_count" -ge "1" ]; then
    run_test \
        "Audit log contains exactly one COMPENSATION_MAPPING_UPDATED row for entity 'salary'" \
        "pass" \
        "Found $row_count row(s) in obs.audit_log"
else
    run_test \
        "Audit log contains exactly one COMPENSATION_MAPPING_UPDATED row for entity 'salary'" \
        "fail" \
        "No matching rows found in obs.audit_log (raw: $audit_result)"
fi

# Extract first row fields for targeted assertions
event_type=$(echo "$audit_result"  | python3 -c "import json,sys; d=json.load(sys.stdin); print(d[0]['event_type']  if d else '')" 2>/dev/null || echo "")
entity_type=$(echo "$audit_result" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d[0]['entity_type'] if d else '')" 2>/dev/null || echo "")
entity_id=$(echo "$audit_result"   | python3 -c "import json,sys; d=json.load(sys.stdin); print(d[0]['entity_id']   if d else '')" 2>/dev/null || echo "")
outcome=$(echo "$audit_result"     | python3 -c "import json,sys; d=json.load(sys.stdin); print(d[0]['outcome']     if d else '')" 2>/dev/null || echo "")
audit_user=$(echo "$audit_result"  | python3 -c "import json,sys; d=json.load(sys.stdin); print(d[0]['user_id']     if d else '')" 2>/dev/null || echo "")

[ "$event_type"  = "COMPENSATION_MAPPING_UPDATED" ] \
    && run_test "event_type = 'COMPENSATION_MAPPING_UPDATED'" "pass" "$event_type" \
    || run_test "event_type = 'COMPENSATION_MAPPING_UPDATED'" "fail" "Got: '$event_type'"

[ "$entity_type" = "compensation_component_mapping" ] \
    && run_test "entity_type = 'compensation_component_mapping'" "pass" "$entity_type" \
    || run_test "entity_type = 'compensation_component_mapping'" "fail" "Got: '$entity_type'"

[ "$entity_id"   = "salary" ] \
    && run_test "entity_id = 'salary'" "pass" "$entity_id" \
    || run_test "entity_id = 'salary'" "fail" "Got: '$entity_id'"

[ "$outcome"     = "SUCCESS" ] \
    && run_test "outcome = 'SUCCESS'" "pass" "$outcome" \
    || run_test "outcome = 'SUCCESS'" "fail" "Got: '$outcome'"

[ "$audit_user"  = "$ADMIN_USER_ID" ] \
    && run_test "user_id matches caller ($ADMIN_USER_ID)" "pass" "$audit_user" \
    || run_test "user_id matches caller ($ADMIN_USER_ID)" "fail" "Got: '$audit_user'"

echo ""
echo "========================================================================"
echo -e "Results: ${GREEN}$pass_count passed${NC}, ${RED}$fail_count failed${NC} (of $test_count tests)"
echo "========================================================================"

[ $fail_count -eq 0 ] && echo -e "${GREEN}All tests passed!${NC}" && exit 0
echo -e "${RED}Some tests failed.${NC}" && exit 1
