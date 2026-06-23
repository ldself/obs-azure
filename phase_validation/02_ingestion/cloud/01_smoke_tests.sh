#!/usr/bin/env bash
#
# Phase 02 — Ingestion / cloud validation: 01_smoke_tests
#
# HTTP smoke tests against the deployed App Service, honouring the
# production configuration contract (Cloud Migration v2.0 §10):
#   DB_ENGINE=postgresql, LOCAL_AUTH_BYPASS absent.
#
# Run this after infra/deploy.sh --phase 2 --tier validation has completed.
# Step 1 of the Phase 02 cloud loop; no provision/teardown is done here.
#
# What it tests (all against the live App Service URL):
#
#   Auth / health (no seeded user required)
#   [health]      GET  /api/health                                  → 200
#   [noauth]      GET  /api/v1/dimensions/expense-accounts (no token) → 401
#
#   RULE 9 / OI-DI-06 write blocks (no auth required — returns 405 unconditionally)
#   [405-dim-ea]  POST /api/v1/dimensions/expense-accounts          → 405
#   [405-dim-eap] PUT  /api/v1/dimensions/expense-accounts          → 405
#   [405-dim-acn] POST /api/v1/dimensions/account-hierarchy/nodes   → 405
#   [405-ing-cch] POST /api/v1/ingestion/cost-center-hierarchy      → 405  (OI-DI-06)
#   [405-ing-ahn] POST /api/v1/ingestion/account-hierarchy-nodes    → 405
#   [405-ing-ea]  POST /api/v1/ingestion/expense-accounts           → 405
#
#   Dimension reads — any active authenticated user (AC-OI-DI-10)
#   [me]          GET  /api/v1/me                                   → 200
#   [dim-cc]      GET  /api/v1/dimensions/cost-centers              → 200
#   [dim-ea]      GET  /api/v1/dimensions/expense-accounts          → 200
#   [dim-acn]     GET  /api/v1/dimensions/account-hierarchy/nodes   → 200
#   [dim-acm]     GET  /api/v1/dimensions/account-hierarchy/memberships → 200
#   [dim-ccn]     GET  /api/v1/dimensions/cost-center-hierarchy/nodes  → 200
#   [dim-ccm]     GET  /api/v1/dimensions/cost-center-hierarchy/memberships → 200
#
#   Ingestion read — administrator only
#   [ing-list]    GET  /api/v1/ingestion                            → 200
#
# Prerequisites:
#   - Azure CLI installed and logged in (az login)
#   - infra/tier.env present with ENTRA_CLIENT_ID
#   - psql available (auto-seeds the calling user into obs.users as administrator)
#     If psql is absent, the 405 and 401 tests still run; auth-required tests are skipped.
#
# Override the target URL without Azure lookup:
#   OBS_API_BASE_URL=https://... ./01_smoke_tests.sh
#
# Exit status: 0 only if every scheduled test passes; non-zero otherwise.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
cd "${REPO_ROOT}"
INFRA_DIR="${REPO_ROOT}/infra"

# ---------------------------------------------------------------------------
# Colors + result tracking
# ---------------------------------------------------------------------------
GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; NC='\033[0m'

PASS=0; FAIL=0; SKIP=0

_pass()  { echo -e "  ${GREEN}✓ PASS${NC}  $*"; PASS=$((PASS + 1)); }
_fail()  { echo -e "  ${RED}✗ FAIL${NC}  $*"; FAIL=$((FAIL + 1)); }
_skip()  { echo -e "  ${YELLOW}~ SKIP${NC}  $*"; SKIP=$((SKIP + 1)); }

# check LABEL METHOD PATH EXPECTED [extra curl args...]
# Sends a request to ${API}${PATH} and compares the HTTP status code.
check() {
    local label="$1" method="$2" path="$3" expected="$4"
    shift 4
    local actual
    actual="$(curl -s -o /dev/null -w '%{http_code}' \
        -X "${method}" "${API}${path}" "$@" 2>/dev/null)"
    if [[ "${actual}" == "${expected}" ]]; then
        _pass "[${label}] ${method} ${path} → ${actual}"
    else
        _fail "[${label}] ${method} ${path}: expected ${expected}, got ${actual}"
    fi
}

# ---------------------------------------------------------------------------
# Arguments
# ---------------------------------------------------------------------------
PHASE="2"
TIER="validation"
while [[ $# -gt 0 ]]; do
    case "$1" in
        --phase) PHASE="$2"; shift 2;;
        --tier)  TIER="$2";  shift 2;;
        -h|--help)
            echo "usage: 01_smoke_tests.sh [--phase <n>] [--tier validation]"
            echo "       OBS_API_BASE_URL=<url> ./01_smoke_tests.sh   # skip Azure URL lookup"
            exit 0;;
        *) echo "unknown arg: $1" >&2; exit 2;;
    esac
done

if [[ "${TIER}" != "validation" ]]; then
    echo "FAIL: this script only targets the validation tier (got '${TIER}')." >&2
    exit 2
fi

RG="obs-val-${PHASE}-rg"

# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------
if ! command -v curl >/dev/null 2>&1; then
    echo "FAIL: curl is required but not installed." >&2; exit 1
fi
if ! command -v az >/dev/null 2>&1; then
    echo "FAIL: the Azure CLI ('az') is not installed or not on PATH." >&2; exit 1
fi
if ! az account show >/dev/null 2>&1; then
    echo "FAIL: not logged in to Azure. Run 'az login' first." >&2; exit 1
fi
if [[ ! -f "${INFRA_DIR}/tier.env" ]]; then
    echo "FAIL: ${INFRA_DIR}/tier.env not found." >&2
    echo "      Copy infra/tier.env.example and fill in ENTRA_CLIENT_ID." >&2
    exit 1
fi

# shellcheck source=/dev/null
source "${INFRA_DIR}/tier.env"

if [[ -z "${ENTRA_CLIENT_ID:-}" ]]; then
    echo "FAIL: ENTRA_CLIENT_ID is not set in infra/tier.env." >&2; exit 1
fi
if [[ -z "${ENTRA_TENANT_ID:-}" ]]; then
    echo "FAIL: ENTRA_TENANT_ID is not set in infra/tier.env." >&2; exit 1
fi
if [[ -z "${ENTRA_CLIENT_SECRET:-}" ]]; then
    echo "FAIL: ENTRA_CLIENT_SECRET is not set in infra/tier.env." >&2; exit 1
fi

# ---------------------------------------------------------------------------
# Resolve the App Service URL
# ---------------------------------------------------------------------------
if [[ -z "${OBS_API_BASE_URL:-}" ]]; then
    API_HOST="$(az webapp show --name "${RG}-api" --resource-group "${RG}" \
        --query defaultHostName -o tsv 2>/dev/null || true)"
    if [[ -z "${API_HOST}" ]]; then
        echo "FAIL: could not resolve ${RG}-api in resource group ${RG}." >&2
        echo "      Run infra/deploy.sh --phase ${PHASE} first, or set OBS_API_BASE_URL." >&2
        exit 1
    fi
    API="https://${API_HOST}"
else
    API="${OBS_API_BASE_URL%/}"
fi

echo -e "${BLUE}========================================================${NC}"
echo -e "${BLUE}Phase ${PHASE} — Ingestion Cloud Smoke Tests${NC}"
echo -e "${BLUE}========================================================${NC}"
echo "Resource group : ${RG}"
echo "Target API     : ${API}"
echo "DB_ENGINE      : postgresql (LOCAL_AUTH_BYPASS absent)"
echo "Entra client   : ${ENTRA_CLIENT_ID}"
echo

# ---------------------------------------------------------------------------
# Poll /api/health — fail fast before testing anything else
# ---------------------------------------------------------------------------
echo "==> Waiting for the deployed API to report healthy"
HEALTH_STATUS=""
for _ in $(seq 1 45); do
    HEALTH_STATUS="$(curl -s -o /dev/null -w '%{http_code}' \
        "${API}/api/health" 2>/dev/null || true)"
    if [[ "${HEALTH_STATUS}" == "200" ]]; then
        echo "    API healthy (HTTP 200)"; break
    fi
    sleep 2
done
if [[ "${HEALTH_STATUS}" != "200" ]]; then
    echo "FAIL: deployed API never became healthy at ${API}/api/health" >&2
    echo "      (last status: '${HEALTH_STATUS:-none}')" >&2
    exit 1
fi
echo

# ---------------------------------------------------------------------------
# Acquire Entra ID access token (client credentials — no interactive consent)
# ---------------------------------------------------------------------------
# Uses the OBS app's own client secret rather than a delegated user token.
# This avoids AADSTS65001 (Azure CLI consent not granted) and is appropriate
# for headless validation. The OBS app authenticates as itself; its service
# principal OID is seeded into obs.users below.
#
# If this fails with AADSTS7000218 ("no permissions have been added"):
#   Add an AppRole to the OBS app registration in Entra ID (Azure Portal →
#   App registrations → <app> → App roles → Create). Any role works; its
#   presence enables the client credentials /.default scope.
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
    echo "FAIL: could not acquire access token." >&2
    echo "      ${ERR}" >&2
    echo "      AADSTS7000218 → add an AppRole to the app registration in Entra ID." >&2
    echo "      AADSTS700016  → verify ENTRA_CLIENT_ID / ENTRA_TENANT_ID in tier.env." >&2
    exit 1
}
echo "    Token acquired (client credentials, scope: ${TOKEN_SCOPE})."

# Decode the OID from the JWT payload — for client credentials this is the
# service principal's object ID, not a human user's OID.
OID="$(echo "${TOKEN}" | cut -d. -f2 | python3 -c "
import sys, json, base64
p = sys.stdin.read().strip()
p += '=' * (4 - len(p) % 4)
print(json.loads(base64.urlsafe_b64decode(p)).get('oid', ''))
" 2>/dev/null || true)"

CALLER_NAME="OBS service principal (${ENTRA_CLIENT_ID})"
echo "    Caller   : ${CALLER_NAME}"
echo "    OID      : ${OID:-'(could not decode)'}"
echo

# ---------------------------------------------------------------------------
# Seed the calling user into obs.users (requires psql + Key Vault access)
# The user must exist as is_active=TRUE, is_administrator=TRUE to reach the
# dimension and ingestion read endpoints (7-step auth flow Steps 2–4).
# ---------------------------------------------------------------------------
USER_SEEDED=0
if [[ -z "${OID}" ]]; then
    echo "WARNING: could not decode OID from token — skipping user seed." >&2
    echo "         Auth-required tests will be skipped." >&2
elif ! command -v psql >/dev/null 2>&1; then
    echo "WARNING: psql not found — skipping auto-seed of obs.users." >&2
    echo "         Auth-required tests will be skipped." >&2
    echo "         Install psql or use the sql_insert_user pattern from" >&2
    echo "         phase_validation/01_authorization/cloud/sql_insert_user" >&2
    echo "         (fill in user_id='${OID}') to seed the user manually." >&2
else
    echo "==> Seeding calling user into obs.users"
    PG_CONN="$(az keyvault secret show \
        --vault-name "${RG}-kv" --name obs-pg-connection-string \
        --query value -o tsv 2>/dev/null || true)"
    if [[ -z "${PG_CONN}" ]]; then
        echo "WARNING: could not read obs-pg-connection-string from ${RG}-kv." >&2
        echo "         Auth-required tests will be skipped." >&2
    else
        # Escape single quotes in name for inline SQL.
        CALLER_NAME_ESC="${CALLER_NAME//\'/\'\'}"
        psql "${PG_CONN}" -v ON_ERROR_STOP=1 <<SQL
INSERT INTO obs.users (
    user_id, display_name, email,
    is_active, is_administrator,
    is_system_modeler, is_report_developer, is_finance_reviewer,
    created_at, created_by
) VALUES (
    '${OID}', '${CALLER_NAME_ESC}', 'smoke-test@obs.local',
    TRUE, TRUE,
    FALSE, FALSE, FALSE,
    NOW(), 'cloud-smoke-test'
)
ON CONFLICT (user_id) DO UPDATE
    SET is_active        = TRUE,
        is_administrator = TRUE,
        updated_at       = NOW(),
        updated_by       = 'cloud-smoke-test';
SQL
        echo "    User seeded (OID: ${OID})."
        USER_SEEDED=1
    fi
fi
echo

# Shorthand for authenticated curl args
AUTH=(-H "Authorization: Bearer ${TOKEN}")

# ---------------------------------------------------------------------------
# Tests: health + auth guard (no seeded user required)
# ---------------------------------------------------------------------------
echo -e "${YELLOW}-- Health + auth guard --${NC}"
check "health" GET  /api/health                               "200"
check "noauth" GET  "/api/v1/dimensions/expense-accounts"     "401"
echo

# ---------------------------------------------------------------------------
# Tests: RULE 9 / OI-DI-06 write blocks (no auth dependency — 405 always)
# ---------------------------------------------------------------------------
echo -e "${YELLOW}-- RULE 9 / OI-DI-06 write blocks (no auth required) --${NC}"
check "405-dim-ea"  POST "/api/v1/dimensions/expense-accounts"          "405" \
    -H "Content-Type: application/json" -d '{}'
check "405-dim-eap" PUT  "/api/v1/dimensions/expense-accounts"          "405" \
    -H "Content-Type: application/json" -d '{}'
check "405-dim-ead" DELETE "/api/v1/dimensions/expense-accounts"        "405"
check "405-dim-acn" POST "/api/v1/dimensions/account-hierarchy/nodes"   "405" \
    -H "Content-Type: application/json" -d '{}'
check "405-ing-cch" POST "/api/v1/ingestion/cost-center-hierarchy"      "405" \
    -H "Content-Type: application/json" -d '{}'
check "405-ing-ahn" POST "/api/v1/ingestion/account-hierarchy-nodes"    "405" \
    -H "Content-Type: application/json" -d '{}'
check "405-ing-ea"  POST "/api/v1/ingestion/expense-accounts"           "405" \
    -H "Content-Type: application/json" -d '{}'
echo

# ---------------------------------------------------------------------------
# Tests: dimension reads + ingestion list (require seeded active user)
# ---------------------------------------------------------------------------
if [[ "${USER_SEEDED}" -eq 1 ]]; then
    echo -e "${YELLOW}-- Dimension reads (AC-OI-DI-10: any active user) --${NC}"
    check "me"      GET "/api/v1/me"                                             "200" "${AUTH[@]}"
    check "dim-cc"  GET "/api/v1/dimensions/cost-centers"                        "200" "${AUTH[@]}"
    check "dim-ea"  GET "/api/v1/dimensions/expense-accounts"                    "200" "${AUTH[@]}"
    check "dim-acn" GET "/api/v1/dimensions/account-hierarchy/nodes"             "200" "${AUTH[@]}"
    check "dim-acm" GET "/api/v1/dimensions/account-hierarchy/memberships"       "200" "${AUTH[@]}"
    check "dim-ccn" GET "/api/v1/dimensions/cost-center-hierarchy/nodes"         "200" "${AUTH[@]}"
    check "dim-ccm" GET "/api/v1/dimensions/cost-center-hierarchy/memberships"   "200" "${AUTH[@]}"
    echo

    echo -e "${YELLOW}-- Ingestion read (administrator only) --${NC}"
    check "ing-list" GET "/api/v1/ingestion" "200" "${AUTH[@]}"
    echo
else
    _skip "me, dim-*, ing-list — user not seeded; see warnings above"
    echo
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
TOTAL=$((PASS + FAIL + SKIP))
echo -e "${BLUE}========================================================${NC}"
echo -e "Results: ${GREEN}${PASS} passed${NC}, ${RED}${FAIL} failed${NC}, ${YELLOW}${SKIP} skipped${NC} (of ${TOTAL} tests)"
echo -e "${BLUE}========================================================${NC}"

if [[ "${FAIL}" -gt 0 ]]; then
    echo -e "${RED}FAIL: ${FAIL} smoke test(s) did not pass.${NC}" >&2
    exit 1
fi
if [[ "${SKIP}" -gt 0 ]]; then
    echo -e "${YELLOW}WARNING: ${SKIP} test(s) skipped — cloud validation is incomplete.${NC}" >&2
    echo "         Install psql and ensure Key Vault access to run the full suite." >&2
    exit 1
fi

echo -e "${GREEN}CLOUD SMOKE TESTS OK (phase ${PHASE}, ${TIER} tier, DB_ENGINE=postgresql)${NC}"
