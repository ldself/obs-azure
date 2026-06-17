#!/usr/bin/env bash
#
# Phase 00 — Foundations / local validation: 02_health_endpoint
#
# Boot the FastAPI stub the supported way (`make api`) and confirm that
# GET /health returns HTTP 200 with the documented body. Generated from the
# approved plan "Confirm /health returns 200 locally via make api".
#
# Steps:
#   1. Resolve the repo root and the project venv python; put the venv on PATH
#      so `make api` launches the venv's uvicorn.
#   2. Start `make api` in its own process group (it uses --reload, which forks
#      a reloader + worker), capturing server output to a temp log.
#   3. Install an EXIT trap that kills the whole process group so no orphaned
#      uvicorn survives on port 8000 even on failure/interrupt.
#   4. Poll http://localhost:8000/health until it answers or a ~15s budget runs
#      out (print the server log and fail if it never becomes healthy).
#   5. Assert HTTP status == 200 and that the JSON body has status=="ok" and a
#      non-empty engine field (matches backend/app/main.py).
#
# Exit status: 0 only if the API serves HTTP 200 with the expected body from
# /health; non-zero otherwise.

set -euo pipefail

# --- Step 1: resolve repo root (this script lives in phase_validation/00_foundations/local/) ---
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
cd "${REPO_ROOT}"

# --- Choose the python interpreter (prefer the project venv) ---
if [[ -x "${REPO_ROOT}/.venv/bin/python" ]]; then
    PYTHON="${REPO_ROOT}/.venv/bin/python"
    # Ensure `make api`'s `uvicorn` resolves to the venv binary.
    export PATH="${REPO_ROOT}/.venv/bin:${PATH}"
else
    PYTHON="${PYTHON:-python}"
fi

HEALTH_URL="http://localhost:8000/health"
SERVER_LOG="$(mktemp -t obs_health_api.XXXXXX)"

echo "Repo root  : ${REPO_ROOT}"
echo "Python     : ${PYTHON}"
echo "Health URL : ${HEALTH_URL}"
echo "Server log : ${SERVER_LOG}"
echo

# --- Step 2: start the API in its own process group ---
echo "==> Starting the API (make api)"
set -m  # run the background job in its own process group so we can kill the tree
make api >"${SERVER_LOG}" 2>&1 &
API_PID=$!
set +m
PGID="${API_PID}"  # the job leader's PID is its process-group id

# --- Step 3: guarantee teardown of the whole process group on any exit ---
cleanup() {
    # Negative PID targets the entire process group (reloader + worker).
    kill -TERM "-${PGID}" 2>/dev/null || true
    sleep 1
    kill -KILL "-${PGID}" 2>/dev/null || true
    rm -f "${SERVER_LOG}"
}
trap cleanup EXIT

# --- Step 4: wait for the API to become healthy (~15s budget) ---
echo "==> Waiting for ${HEALTH_URL} to respond"
STATUS=""
for _ in $(seq 1 30); do
    # Bail early if the server process already died.
    if ! kill -0 "${API_PID}" 2>/dev/null; then
        echo "FAIL: API process exited before becoming healthy. Server log:"
        cat "${SERVER_LOG}"
        exit 1
    fi
    STATUS="$(curl -s -o /dev/null -w '%{http_code}' "${HEALTH_URL}" 2>/dev/null || true)"
    if [[ "${STATUS}" == "200" ]]; then
        break
    fi
    sleep 0.5
done

if [[ "${STATUS}" != "200" ]]; then
    echo "FAIL: API did not become healthy (last status: '${STATUS:-none}'). Server log:"
    cat "${SERVER_LOG}"
    exit 1
fi

# --- Step 5: assert the status and body contract ---
echo "==> Verifying /health response"
BODY="$(curl -s "${HEALTH_URL}")"
echo "HTTP status : ${STATUS}"
echo "Body        : ${BODY}"

OBS_HEALTH_BODY="${BODY}" "${PYTHON}" - <<'PY'
import json
import os
import sys

raw = os.environ["OBS_HEALTH_BODY"]
try:
    data = json.loads(raw)
except json.JSONDecodeError as exc:
    print(f"FAIL: /health body is not valid JSON: {exc}")
    sys.exit(1)

if data.get("status") != "ok":
    print(f"FAIL: expected status=='ok', got {data.get('status')!r}")
    sys.exit(1)
if not data.get("engine"):
    print(f"FAIL: expected a non-empty 'engine' field, got {data.get('engine')!r}")
    sys.exit(1)
PY

echo
echo "HEALTH ENDPOINT OK (200)"
