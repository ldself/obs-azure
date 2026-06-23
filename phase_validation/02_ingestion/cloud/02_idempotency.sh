#!/usr/bin/env bash
#
# Phase 02 — Ingestion / cloud validation: 02_idempotency
#
# Verifies RULE 10 against the live Azure Blob + Functions pipeline:
# upload the same fixture file twice to the Blob landing zone and confirm
# that row counts in obs.actuals / obs.employees are unchanged after the
# second upload, and that obs.ingestion_control contains no duplicate record.
#
# Test cases:
#   TC-IDP-CLOUD-01  actuals  — upload identical file twice; row count unchanged
#   TC-IDP-CLOUD-02  employees — upload identical file twice; row count unchanged
#
# Prerequisites:
#   - Azure CLI installed and logged in (az login)
#   - infra/tier.env present (ENTRA_CLIENT_ID, ENTRA_TENANT_ID, ENTRA_CLIENT_SECRET)
#   - infra/deploy.sh --phase 2 --tier validation has completed (Functions deployed)
#   - psql installed and the deploy-host IP allowed through the PostgreSQL firewall
#
# Run after: 01_smoke_tests.sh passes.
#
# Override the post-second-upload wait with --wait <seconds> (default 180).
# Exit status: 0 if all checks pass; non-zero on any failure.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
INFRA_DIR="${REPO_ROOT}/infra"

GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; NC='\033[0m'

PASS=0; FAIL=0
_pass() { echo -e "  ${GREEN}✓ PASS${NC}  $*"; PASS=$((PASS + 1)); }
_fail() { echo -e "  ${RED}✗ FAIL${NC}  $*"; FAIL=$((FAIL + 1)); }

# ---------------------------------------------------------------------------
# Arguments
# ---------------------------------------------------------------------------
PHASE="2"; TIER="validation"; SECOND_UPLOAD_WAIT=180
while [[ $# -gt 0 ]]; do case "$1" in
    --phase) PHASE="$2"; shift 2;;
    --tier)  TIER="$2";  shift 2;;
    --wait)  SECOND_UPLOAD_WAIT="$2"; shift 2;;
    -h|--help)
        echo "usage: 02_idempotency.sh [--phase <n>] [--tier validation] [--wait <seconds>]"
        exit 0;;
    *) echo "unknown arg: $1" >&2; exit 2;;
esac; done

[[ "$TIER" != "validation" ]] && { echo "FAIL: this script only targets the validation tier." >&2; exit 2; }

RG="obs-val-${PHASE}-rg"
STORAGE_ACCOUNT="obsval${PHASE}landing"

# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------
for cmd in curl az python3 psql; do
    command -v "$cmd" >/dev/null 2>&1 || {
        echo "FAIL: $cmd is required but not found." >&2
        [[ "$cmd" == "psql" ]] && echo "      psql is needed for row-count verification via Key Vault." >&2
        exit 1
    }
done
az account show >/dev/null 2>&1 || { echo "FAIL: not logged in — run 'az login' first." >&2; exit 1; }
[[ -f "${INFRA_DIR}/tier.env" ]] || { echo "FAIL: ${INFRA_DIR}/tier.env not found." >&2; exit 1; }
# shellcheck source=/dev/null
source "${INFRA_DIR}/tier.env"

for var in ENTRA_CLIENT_ID ENTRA_TENANT_ID ENTRA_CLIENT_SECRET; do
    [[ -z "${!var:-}" ]] && { echo "FAIL: ${var} is not set in infra/tier.env." >&2; exit 1; }
done

# ---------------------------------------------------------------------------
# Resolve endpoints
# ---------------------------------------------------------------------------
echo "==> Resolving resource endpoints"
API_HOST="$(az webapp show --name "${RG}-api" --resource-group "${RG}" \
    --query defaultHostName -o tsv 2>/dev/null || true)"
[[ -z "$API_HOST" ]] && {
    echo "FAIL: could not resolve ${RG}-api. Run infra/deploy.sh --phase ${PHASE} first." >&2; exit 1; }
API="https://${API_HOST}"

az storage account show --name "${STORAGE_ACCOUNT}" --resource-group "${RG}" \
    >/dev/null 2>&1 || { echo "FAIL: storage account ${STORAGE_ACCOUNT} not found." >&2; exit 1; }

echo "  Resource group    : ${RG}"
echo "  API               : ${API}"
echo "  Storage account   : ${STORAGE_ACCOUNT}"
echo "  Post-upload wait  : ${SECOND_UPLOAD_WAIT}s"
echo

# ---------------------------------------------------------------------------
# Acquire Entra ID access token (client credentials — same as 01_smoke_tests.sh)
# ---------------------------------------------------------------------------
echo "==> Acquiring Entra ID access token (client credentials)"
TOKEN_SCOPE="${TOKEN_SCOPE:-${ENTRA_CLIENT_ID}/.default}"
TOKEN_RESPONSE="$(curl -s -X POST \
    "https://login.microsoftonline.com/${ENTRA_TENANT_ID}/oauth2/v2.0/token" \
    -H "Content-Type: application/x-www-form-urlencoded" \
    --data-urlencode "client_id=${ENTRA_CLIENT_ID}" \
    --data-urlencode "client_secret=${ENTRA_CLIENT_SECRET}" \
    --data-urlencode "scope=${TOKEN_SCOPE}" \
    --data-urlencode "grant_type=client_credentials")"

TOKEN="$(echo "${TOKEN_RESPONSE}" | python3 -c "
import sys, json
d = json.load(sys.stdin)
if 'access_token' not in d:
    print('FAIL: token error:', d.get('error_description', d.get('error', str(d))), file=sys.stderr)
    sys.exit(1)
print(d['access_token'])
" 2>/dev/null)" || {
    ERR="$(echo "${TOKEN_RESPONSE}" | python3 -c \
        "import sys,json; d=json.load(sys.stdin); print(d.get('error_description', d.get('error','')))" \
        2>/dev/null || echo "${TOKEN_RESPONSE}")"
    echo "FAIL: could not acquire access token. ${ERR}" >&2
    echo "      AADSTS7000218 → add an AppRole to the app registration in Entra ID." >&2
    exit 1
}

OID="$(echo "${TOKEN}" | cut -d. -f2 | python3 -c "
import sys, json, base64
p = sys.stdin.read().strip()
p += '=' * (4 - len(p) % 4)
print(json.loads(base64.urlsafe_b64decode(p)).get('oid', ''))
" 2>/dev/null || true)"

echo "  Token acquired. OID: ${OID:-'(could not decode)'}"
AUTH=(-H "Authorization: Bearer ${TOKEN}")
echo

# ---------------------------------------------------------------------------
# Read PostgreSQL connection string from Key Vault
# ---------------------------------------------------------------------------
echo "==> Reading PostgreSQL connection string from Key Vault"
PG_CONN="$(az keyvault secret show --vault-name "${RG}-kv" --name obs-pg-connection-string \
    --query value -o tsv 2>/dev/null || true)"
[[ -z "$PG_CONN" ]] && {
    echo "FAIL: could not read obs-pg-connection-string from ${RG}-kv." >&2
    echo "      Ensure the deploy-host IP is allowed through the PostgreSQL firewall." >&2
    exit 1
}
echo "  Connection string retrieved."
echo

# ---------------------------------------------------------------------------
# Seed the service principal into obs.users (administrator)
# ---------------------------------------------------------------------------
if [[ -n "$OID" ]]; then
    echo "==> Seeding service principal into obs.users"
    SP_NAME_ESC="OBS service principal (${ENTRA_CLIENT_ID//\'/\'\'})"
    psql "${PG_CONN}" -v ON_ERROR_STOP=1 -q <<SQL
INSERT INTO obs.users (
    user_id, display_name, email,
    is_active, is_administrator,
    is_system_modeler, is_report_developer, is_finance_reviewer,
    created_at, created_by
) VALUES (
    '${OID}', '${SP_NAME_ESC}', 'cloud-idp-test@obs.local',
    TRUE, TRUE, FALSE, FALSE, FALSE,
    NOW(), 'cloud-idp-test'
)
ON CONFLICT (user_id) DO UPDATE
    SET is_active        = TRUE,
        is_administrator = TRUE,
        updated_at       = NOW(),
        updated_by       = 'cloud-idp-test';
SQL
    echo "  User seeded (OID: ${OID})."
    echo
fi

# ---------------------------------------------------------------------------
# Storage account key for blob uploads
# ---------------------------------------------------------------------------
STORAGE_KEY="$(az storage account keys list --account-name "${STORAGE_ACCOUNT}" \
    --resource-group "${RG}" --query '[0].value' -o tsv)"

# ---------------------------------------------------------------------------
# Fixture definitions (identical to local/02_idempotency.sh)
# Each test run gets a timestamp-qualified file name so the first upload is
# never treated as a duplicate from a prior run.  Both uploads within a single
# test share the same name, so SHA-256(content) + file_name triggers dedup.
# ---------------------------------------------------------------------------
TEST_RUN_ID="$(date +%Y%m%d%H%M%S)"

# poll_ingestion_list FILE_NAME TIMEOUT_SECONDS
# Polls GET /api/v1/ingestion every 15 s until a record for FILE_NAME reaches
# COMPLETED or FAILED.  Prints the ingestion_id to stdout on success.
poll_ingestion_list() {
    local file_name="$1" timeout_sec="$2"
    local elapsed=0 interval=15 status ingestion_id body

    echo "  Polling ${API}/api/v1/ingestion for '${file_name}' (up to ${timeout_sec}s)..."
    while [[ $elapsed -lt $timeout_sec ]]; do
        body="$(curl -s "${API}/api/v1/ingestion" "${AUTH[@]}" 2>/dev/null || true)"
        read -r status ingestion_id < <(echo "${body}" | python3 - <<PYEOF 2>/dev/null || echo " "
import sys, json
records = json.load(sys.stdin)
for r in records:
    if r.get('file_name') == '${file_name}':
        print(r.get('status',''), r.get('ingestion_id',''))
        sys.exit(0)
print(' ', '')
PYEOF
)
        case "$status" in
            COMPLETED)
                echo "  Reached COMPLETED: ingestion_id=${ingestion_id}"
                echo "$ingestion_id"
                return 0;;
            FAILED)
                echo "  Reached FAILED for '${file_name}' — pipeline reported a failure." >&2
                return 1;;
        esac
        sleep "$interval"
        elapsed=$((elapsed + interval))
        echo "  Waiting... (${elapsed}s elapsed, status='${status:-none}')"
    done

    echo "  FAIL: timed out waiting for '${file_name}' after ${timeout_sec}s" >&2
    return 1
}

# run_idempotency_test LABEL FILE_NAME FIXTURE_FILE CONTAINER TABLE ACTIVE_FILTER EXPECTED_ROWS
run_idempotency_test() {
    local label="$1" file_name="$2" fixture_file="$3" container="$4"
    local table="$5" active_filter="$6" expected_rows="$7"
    local iid count_after_first count_after_second ic_count

    echo -e "${YELLOW}========================================${NC}"
    echo -e "${YELLOW}[${label}]${NC}"
    echo -e "${YELLOW}Upload 1 — initial file processing${NC}"

    echo "  Uploading '${file_name}' to ${container}..."
    az storage blob upload \
        --account-name "${STORAGE_ACCOUNT}" \
        --account-key "${STORAGE_KEY}" \
        --container-name "${container}" \
        --name "${file_name}" \
        --file "${fixture_file}" \
        --overwrite \
        --output none 2>/dev/null
    echo "  Blob uploaded."

    iid="$(poll_ingestion_list "${file_name}" 300)" || {
        _fail "${label}: first upload did not reach COMPLETED within 5 minutes"
        return
    }

    count_after_first="$(psql "${PG_CONN}" -tAq \
        -c "SELECT COUNT(*) FROM ${table} WHERE ${active_filter} AND ingestion_id = '${iid}'" \
        2>/dev/null | tr -d '[:space:]')"

    if [[ "$count_after_first" -eq "$expected_rows" ]]; then
        _pass "${label} upload 1: promoted ${count_after_first} row(s) (ingestion_id=${iid})"
    else
        _fail "${label} upload 1: expected ${expected_rows} promoted rows, got ${count_after_first}"
        return
    fi

    echo
    echo -e "${YELLOW}Upload 2 — identical file; must be skipped by pipeline (RULE 10)${NC}"
    echo "  Re-uploading identical '${file_name}' to ${container}..."
    az storage blob upload \
        --account-name "${STORAGE_ACCOUNT}" \
        --account-key "${STORAGE_KEY}" \
        --container-name "${container}" \
        --name "${file_name}" \
        --file "${fixture_file}" \
        --overwrite \
        --output none 2>/dev/null
    echo "  Blob uploaded. Waiting ${SECOND_UPLOAD_WAIT}s for Function trigger + pipeline..."
    sleep "$SECOND_UPLOAD_WAIT"

    # Row count in the promotion table must not have increased for this ingestion_id.
    count_after_second="$(psql "${PG_CONN}" -tAq \
        -c "SELECT COUNT(*) FROM ${table} WHERE ${active_filter} AND ingestion_id = '${iid}'" \
        2>/dev/null | tr -d '[:space:]')"

    if [[ "$count_after_second" -eq "$count_after_first" ]]; then
        _pass "${label} upload 2: row count unchanged at ${count_after_second} (RULE 10 satisfied)"
    else
        _fail "${label} upload 2: row count changed — before=${count_after_first}, after=${count_after_second}"
    fi

    # ingestion_control must have exactly 1 record for this file_name; no duplicate row created.
    ic_count="$(psql "${PG_CONN}" -tAq \
        -c "SELECT COUNT(*) FROM obs.ingestion_control WHERE file_name = '${file_name}'" \
        2>/dev/null | tr -d '[:space:]')"

    if [[ "$ic_count" -eq 1 ]]; then
        _pass "${label} upload 2: ingestion_control has 1 record (no duplicate ingestion entry)"
    else
        _fail "${label} upload 2: ingestion_control has ${ic_count} records for '${file_name}' (expected 1)"
    fi

    echo
}

# ---------------------------------------------------------------------------
# Write fixtures to temp dir (shared between both uploads of each test)
# ---------------------------------------------------------------------------
TMPDIR_FIXTURES="$(mktemp -d)"
trap 'rm -rf "${TMPDIR_FIXTURES}"' EXIT

ACTUALS_FILE="cloud-idp-actuals-${TEST_RUN_ID}.csv"
cat > "${TMPDIR_FIXTURES}/${ACTUALS_FILE}" << 'CSV'
entity,year,month,cost_center,account,sub_account,currency,amount,bonus_type,product,distribution_channel,stat_category,profit_center,sender_cost_center,assignment,functional_area,partner_functional_area_text
1001,2026,1,CC-1001,6000,001,USD,1000.00,,PRD,CHN,STAT,PC,SCC,ASSN,FA,
1001,2026,2,CC-1001,6000,001,USD,2000.00,,PRD,CHN,STAT,PC,SCC,ASSN,FA,
1001,2026,3,CC-1001,6000,001,USD,3000.00,,PRD,CHN,STAT,PC,SCC,ASSN,FA,
CSV

EMPLOYEES_FILE="cloud-idp-employees-${TEST_RUN_ID}.csv"
cat > "${TMPDIR_FIXTURES}/${EMPLOYEES_FILE}" << 'CSV'
p_number,company,entity,cost_center,department,last_name,first_name,salary_structure,home_state,work_state,office,workplace_flexibility,management_production,job_grade,full_time_part_time,annual_salary,fte,last_hire_date
P001,ACME,1001,CC-1001,DEPT,Smith,Jane,EIP,CA,CA,HQ,FLEX,MGMT,G5,FT,80000,1.0,2020-01-15
P002,ACME,1001,CC-1001,DEPT,Jones,Bob,EIP,NY,NY,HQ,FLEX,MGMT,G4,FT,75000,1.0,2019-06-01
CSV

# ---------------------------------------------------------------------------
# Banner
# ---------------------------------------------------------------------------
echo -e "${BLUE}========================================================${NC}"
echo -e "${BLUE}Phase ${PHASE} — Cloud Idempotency Tests (RULE 10)${NC}"
echo -e "${BLUE}========================================================${NC}"
echo "Resource group    : ${RG}"
echo "Storage account   : ${STORAGE_ACCOUNT}"
echo "API               : ${API}"
echo "Post-upload wait  : ${SECOND_UPLOAD_WAIT}s"
echo

# ---------------------------------------------------------------------------
# TC-IDP-CLOUD-01: actuals
# ---------------------------------------------------------------------------
run_idempotency_test \
    "TC-IDP-CLOUD-01 actuals" \
    "${ACTUALS_FILE}" \
    "${TMPDIR_FIXTURES}/${ACTUALS_FILE}" \
    "landing-actuals" \
    "obs.actuals" \
    "is_deleted = FALSE" \
    3

# ---------------------------------------------------------------------------
# TC-IDP-CLOUD-02: employees
# ---------------------------------------------------------------------------
run_idempotency_test \
    "TC-IDP-CLOUD-02 employees" \
    "${EMPLOYEES_FILE}" \
    "${TMPDIR_FIXTURES}/${EMPLOYEES_FILE}" \
    "landing-employees" \
    "obs.employees" \
    "is_active = TRUE" \
    2

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo -e "${BLUE}========================================================${NC}"
TOTAL=$((PASS + FAIL))
echo -e "Results: ${GREEN}${PASS} passed${NC}, ${RED}${FAIL} failed${NC} (of ${TOTAL} tests)"
echo -e "${BLUE}========================================================${NC}"

if [[ $FAIL -gt 0 ]]; then
    echo -e "${RED}FAIL: ${FAIL} cloud idempotency check(s) did not pass.${NC}" >&2
    exit 1
fi
echo -e "${GREEN}CLOUD IDEMPOTENCY OK (phase ${PHASE}, ${TIER} tier, RULE 10 confirmed)${NC}"
