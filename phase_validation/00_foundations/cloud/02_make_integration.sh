#!/usr/bin/env bash
#
# Phase 00 — Foundations / cloud validation: 02_make_integration
#
# Run the integration suite against the DEPLOYED App Service, honouring the
# production configuration contract (Cloud Migration v2.0 §10):
#   DB_ENGINE=postgresql, LOCAL_AUTH_BYPASS absent.
#
# This is step 2 of the Phase 00 cloud validation loop; run it after
# 01_provision_deploy.sh has provisioned + deployed the tier.
#
# How the target URL reaches the test:
#   backend/tests/integration/test_health_integration.py reads OBS_API_BASE_URL
#   from the environment (default http://localhost:8000). Nothing loads a dotenv
#   file in the pytest process, so we resolve the App Service hostname from Azure
#   and export OBS_API_BASE_URL directly; `make test-integration` inherits it.
#
# Why we poll /health first:
#   That same test SKIPS (does not fail) when the API is unreachable. A skipped
#   run would let `make test-integration` "pass" without testing anything. So we
#   poll the deployed /health until it answers 200 and FAIL fast if it never
#   does — a skip must not masquerade as a green validation.
#
# Exit status: 0 only if the deployed API is reachable AND make test-integration
# passes; non-zero otherwise.

set -euo pipefail

# --- Resolve repo root (this script lives in phase_validation/00_foundations/cloud/) ---
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
cd "${REPO_ROOT}"

# --- Choose the python interpreter (prefer the project venv) ---
if [[ -x "${REPO_ROOT}/.venv/bin/python" ]]; then
    PYTHON="${REPO_ROOT}/.venv/bin/python"
    # Ensure `make test-integration`/pytest resolve to the venv binaries.
    export PATH="${REPO_ROOT}/.venv/bin:${PATH}"
else
    PYTHON="${PYTHON:-python}"
fi
export PYTHON

# --- Arguments -------------------------------------------------------------
PHASE="0"
TIER="validation"
while [[ $# -gt 0 ]]; do
    case "$1" in
        --phase) PHASE="$2"; shift 2;;
        --tier)  TIER="$2";  shift 2;;
        -h|--help)
            echo "usage: 02_make_integration.sh [--phase <n>] [--tier validation]"
            echo "       OBS_API_BASE_URL=<url> 02_make_integration.sh   # skip Azure lookup"
            exit 0;;
        *) echo "unknown arg: $1" >&2; exit 2;;
    esac
done

if [[ "${TIER}" == "validation" ]]; then RG="obs-val-${PHASE}-rg"; else RG="obs-prod-rg"; fi

# --- Resolve the deployed App Service URL ----------------------------------
# Pre-set OBS_API_BASE_URL to point the suite at an arbitrary URL and skip the
# Azure lookup entirely; otherwise discover the App Service hostname from the RG.
if [[ -z "${OBS_API_BASE_URL:-}" ]]; then
    if ! command -v az >/dev/null 2>&1; then
        echo "FAIL: 'az' not found and OBS_API_BASE_URL not set — cannot resolve the API URL." >&2
        exit 1
    fi
    if ! az account show >/dev/null 2>&1; then
        echo "FAIL: not logged in to Azure. Run 'az login' (or set OBS_API_BASE_URL)." >&2
        exit 1
    fi
    API_HOST="$(az webapp show --name "${RG}-api" --resource-group "${RG}" \
        --query defaultHostName -o tsv 2>/dev/null || true)"
    if [[ -z "${API_HOST}" ]]; then
        echo "FAIL: could not resolve ${RG}-api in resource group ${RG}." >&2
        echo "      Run 01_provision_deploy.sh first, or set OBS_API_BASE_URL." >&2
        exit 1
    fi
    OBS_API_BASE_URL="https://${API_HOST}"
fi

# --- Production configuration contract (Cloud Migration v2.0 §10) ----------
# DB_ENGINE=postgresql and LOCAL_AUTH_BYPASS absent. We unset the bypass in the
# runner env too so nothing local leaks into the cloud validation (RULE 8).
export OBS_API_BASE_URL
export DB_ENGINE=postgresql
unset LOCAL_AUTH_BYPASS || true

HEALTH_URL="${OBS_API_BASE_URL%/}/health"

echo "Repo root      : ${REPO_ROOT}"
echo "Phase / tier   : ${PHASE} / ${TIER}"
echo "Target API     : ${OBS_API_BASE_URL}"
echo "DB_ENGINE      : ${DB_ENGINE} (LOCAL_AUTH_BYPASS absent)"
echo "Health URL     : ${HEALTH_URL}"
echo

# --- Poll the deployed /health so a skip can't masquerade as a pass --------
# Cloud cold starts are slower than local, so allow a ~90s budget.
echo "==> Waiting for the deployed API to report healthy"
STATUS=""
for _ in $(seq 1 45); do
    STATUS="$(curl -s -o /dev/null -w '%{http_code}' "${HEALTH_URL}" 2>/dev/null || true)"
    if [[ "${STATUS}" == "200" ]]; then
        echo "    API healthy (HTTP 200)"
        break
    fi
    sleep 2
done
if [[ "${STATUS}" != "200" ]]; then
    echo "FAIL: deployed API never became healthy at ${HEALTH_URL} (last status: '${STATUS:-none}')." >&2
    echo "      The integration suite would SKIP rather than test; treating as failure." >&2
    exit 1
fi

# --- Run the integration suite against the deployed API --------------------
echo
echo "==> make test-integration (against ${OBS_API_BASE_URL})"
make test-integration

echo
echo "CLOUD INTEGRATION OK (phase ${PHASE}, ${TIER} tier, DB_ENGINE=postgresql)"
