#!/usr/bin/env bash
#
# Phase 00 — Foundations / local validation: 04_local_auto_test
#
# Automatic local test loop. Runs `make test-unit` and `make test-integration`
# against the local engine (DuckDB) with mock authentication
# (LOCAL_AUTH_BYPASS=true) and iterates until BOTH pass with zero failures.
#
# Why this is more than `make test`:
#   The integration suite skips itself when no API is reachable — see
#   backend/tests/integration/test_health_integration.py:21-27, which calls
#   pytest.skip(...) if GET http://localhost:8000/health is unreachable. To make
#   the integration run *meaningful* (executed, not silently skipped), this
#   script boots the API itself the supported way (`make api`) — the same
#   process-group launch + EXIT-trap teardown used by 02_health_endpoint.sh.
#
# "Iterate until both pass" = bounded retry with remediation:
#   Up to ${MAX_ATTEMPTS} attempts. Before each attempt we ensure the local
#   DuckDB database is bootstrapped. Each attempt runs the unit suite, boots the
#   API, polls /health, runs the integration suite, then tears the API down. On
#   the first attempt where BOTH suites pass we exit 0. If the budget is
#   exhausted we print which suite failed and exit 1 (with the captured server
#   log when the API was the problem).
#
# Mock auth is set via environment variables only (RULE 8: localhost only,
# env-var only, read once at startup — never via header or API).
#
# Exit status: 0 iff make test-unit AND make test-integration both pass with
# zero failures within ${MAX_ATTEMPTS} attempts; non-zero otherwise.

set -euo pipefail

# --- Resolve repo root (this script lives in phase_validation/00_foundations/local/) ---
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
cd "${REPO_ROOT}"

# --- Choose the python interpreter (prefer the project venv) ---
if [[ -x "${REPO_ROOT}/.venv/bin/python" ]]; then
    PYTHON="${REPO_ROOT}/.venv/bin/python"
    # Ensure `make api`/uvicorn/pytest resolve to the venv binaries.
    export PATH="${REPO_ROOT}/.venv/bin:${PATH}"
else
    PYTHON="${PYTHON:-python}"
fi
export PYTHON

# --- Mock-auth + DuckDB environment for the whole run (RULE 8: localhost only) ---
# Values mirror .env.local.example. Exported so the API process and pytest share
# them; env-var only — never set via request header or any runtime mechanism.
export LOCAL_AUTH_BYPASS=true
export LOCAL_AUTH_USER_ID="${LOCAL_AUTH_USER_ID:-dev-admin-001}"
export LOCAL_AUTH_USER_EMAIL="${LOCAL_AUTH_USER_EMAIL:-admin@obs.local}"
export LOCAL_AUTH_USER_NAME="${LOCAL_AUTH_USER_NAME:-Local Admin}"
export DB_ENGINE=duckdb
export DUCKDB_PATH="${DUCKDB_PATH:-./local-data/obs.duckdb}"

# --- Config knobs (overridable via env) ---
MAX_ATTEMPTS="${MAX_ATTEMPTS:-3}"
HEALTH_URL="${HEALTH_URL:-http://localhost:8000/health}"

echo "Repo root    : ${REPO_ROOT}"
echo "Python       : ${PYTHON}"
echo "Engine       : ${DB_ENGINE} (mock auth: LOCAL_AUTH_BYPASS=${LOCAL_AUTH_BYPASS})"
echo "Health URL   : ${HEALTH_URL}"
echo "Max attempts : ${MAX_ATTEMPTS}"
echo

# --- API lifecycle helpers -------------------------------------------------
# A single global tracks the currently-running API process group so the EXIT
# trap can always reap it even if an attempt is interrupted mid-flight.
API_PGID=""
SERVER_LOG=""

stop_api() {
    # Idempotent teardown of the current API process group, if any.
    if [[ -n "${API_PGID}" ]]; then
        # Negative PID targets the whole process group (reloader + worker).
        kill -TERM "-${API_PGID}" 2>/dev/null || true
        sleep 1
        kill -KILL "-${API_PGID}" 2>/dev/null || true
        API_PGID=""
    fi
}

cleanup() {
    stop_api
    [[ -n "${SERVER_LOG}" ]] && rm -f "${SERVER_LOG}" || true
}
trap cleanup EXIT

# start_api: boot `make api` in its own process group and poll /health until it
# answers 200 (~15s budget). Returns 0 if healthy, 1 otherwise (server log left
# in ${SERVER_LOG} for the caller to print).
start_api() {
    SERVER_LOG="$(mktemp -t obs_auto_api.XXXXXX)"
    echo "    ==> Starting the API (make api)"
    set -m  # run the background job in its own process group
    make api >"${SERVER_LOG}" 2>&1 &
    local api_pid=$!
    set +m
    API_PGID="${api_pid}"  # the job leader's PID is its process-group id

    echo "    ==> Waiting for ${HEALTH_URL} to respond"
    local status=""
    local i
    for i in $(seq 1 30); do
        # Bail early if the server process already died.
        if ! kill -0 "${api_pid}" 2>/dev/null; then
            echo "    FAIL: API process exited before becoming healthy. Server log:"
            sed 's/^/        /' "${SERVER_LOG}"
            return 1
        fi
        status="$(curl -s -o /dev/null -w '%{http_code}' "${HEALTH_URL}" 2>/dev/null || true)"
        if [[ "${status}" == "200" ]]; then
            echo "    API healthy (HTTP 200)"
            return 0
        fi
        sleep 0.5
    done

    echo "    FAIL: API did not become healthy (last status: '${status:-none}'). Server log:"
    sed 's/^/        /' "${SERVER_LOG}"
    return 1
}

# run_make: run a make target without tripping `set -e`; echo PASS/FAIL and
# return the target's exit status.
run_make() {
    local target="$1"
    local rc=0
    set +e
    make "${target}"
    rc=$?
    set -e
    return "${rc}"
}

# --- Retry loop ------------------------------------------------------------
overall_ok=1  # 1 == not yet green
for attempt in $(seq 1 "${MAX_ATTEMPTS}"); do
    echo "================================================================"
    echo "Attempt ${attempt} of ${MAX_ATTEMPTS}"
    echo "================================================================"

    unit_ok=0
    integ_ok=0

    # a. Remediate: ensure the local DuckDB database exists.
    if [[ ! -f "${DUCKDB_PATH}" ]]; then
        echo "    ==> ${DUCKDB_PATH} not found; bootstrapping (make db-bootstrap)"
        make db-bootstrap
    fi

    # b. Unit suite.
    echo "    ==> make test-unit"
    if run_make test-unit; then
        unit_ok=1
        echo "    test-unit: PASS"
    else
        echo "    test-unit: FAIL"
    fi

    # c. Start the API and poll /health (so integration runs, not skips).
    if start_api; then
        # d. Integration suite against the live API.
        echo "    ==> make test-integration"
        if run_make test-integration; then
            integ_ok=1
            echo "    test-integration: PASS"
        else
            echo "    test-integration: FAIL"
        fi
    else
        echo "    test-integration: FAIL (API never became healthy)"
    fi

    # e. Tear down this attempt's API (EXIT trap remains as a safety net).
    stop_api

    # f. Both green? Done.
    if [[ "${unit_ok}" -eq 1 && "${integ_ok}" -eq 1 ]]; then
        overall_ok=0
        echo
        echo "Both suites passed on attempt ${attempt}."
        break
    fi

    echo "    Attempt ${attempt} not green (unit=$([[ ${unit_ok} -eq 1 ]] && echo PASS || echo FAIL), integration=$([[ ${integ_ok} -eq 1 ]] && echo PASS || echo FAIL))."
    echo
done

# --- Summary + exit status -------------------------------------------------
echo "================================================================"
if [[ "${overall_ok}" -eq 0 ]]; then
    echo "LOCAL AUTO TEST OK (test-unit + test-integration, DuckDB + mock auth)"
    exit 0
fi

echo "LOCAL AUTO TEST FAILED after ${MAX_ATTEMPTS} attempt(s)."
echo "Re-run for detail with the API up in a second terminal:"
echo "    make api          # terminal 1"
echo "    make test-unit && make test-integration   # terminal 2"
exit 1
