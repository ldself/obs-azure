#!/usr/bin/env bash
#
# Phase 00 — Foundations / cloud validation: 01_provision_deploy
#
# Provision the validation-tier Azure footprint for a Build Sequencing Plan
# phase and deploy the API onto it, by driving the canonical infra/ scripts:
#   infra/provision.sh --phase <n> --tier <tier>   (Cloud Migration v2.0 §3)
#   infra/deploy.sh    --phase <n> --tier <tier>   (Cloud Migration v2.0 §5/§10)
#
# This is step 1 of the Phase 00 cloud validation loop; step 2 is
# 02_make_integration.sh, which runs the integration suite against the App
# Service URL this script provisions.
#
# Scope guardrails:
#   * Defaults to --phase 0 --tier validation (this is 00_foundations/cloud).
#   * Refuses --tier production: production is never a validation target here.
#   * Creates billable Azure resources, so it confirms the resource group and
#     tier before acting (skip with --yes for automation).
#
# It does not tear anything down — teardown stays explicit and manual
# (infra/teardown.sh), per the per-phase cloud validation strategy.
#
# Exit status: 0 only if both provision and deploy succeed; non-zero otherwise.

set -euo pipefail

# --- Resolve repo root (this script lives in phase_validation/00_foundations/cloud/) ---
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
cd "${REPO_ROOT}"
INFRA_DIR="${REPO_ROOT}/infra"

# --- Arguments -------------------------------------------------------------
PHASE="0"
TIER="validation"
ASSUME_YES=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --phase) PHASE="$2"; shift 2;;
        --tier)  TIER="$2";  shift 2;;
        -y|--yes) ASSUME_YES=1; shift;;
        -h|--help)
            echo "usage: 01_provision_deploy.sh [--phase <n>] [--tier validation] [--yes]"
            exit 0;;
        *) echo "unknown arg: $1" >&2; exit 2;;
    esac
done

if [[ "${TIER}" != "validation" ]]; then
    echo "FAIL: this validation script only targets the validation tier (got '${TIER}')." >&2
    echo "      Production provisioning is run directly via infra/ with intent." >&2
    exit 2
fi

RG="obs-val-${PHASE}-rg"

# --- Preflight: required scripts + a logged-in Azure CLI --------------------
for f in provision.sh deploy.sh tier.env; do
    if [[ ! -e "${INFRA_DIR}/${f}" ]]; then
        echo "FAIL: ${INFRA_DIR}/${f} not found." >&2
        echo "      Copy the -example templates and fill in tier.env (see infra/*-example.sh)." >&2
        exit 1
    fi
done
if ! command -v az >/dev/null 2>&1; then
    echo "FAIL: the Azure CLI ('az') is not installed or not on PATH." >&2
    exit 1
fi
if ! az account show >/dev/null 2>&1; then
    echo "FAIL: not logged in to Azure. Run 'az login' first." >&2
    exit 1
fi

echo "Repo root      : ${REPO_ROOT}"
echo "Phase / tier   : ${PHASE} / ${TIER}"
echo "Resource group : ${RG}"
echo "Subscription   : $(az account show --query name -o tsv 2>/dev/null || echo '?')"
echo

# --- Confirm (billable, outward-facing) ------------------------------------
if [[ "${ASSUME_YES}" -ne 1 ]]; then
    read -r -p "Provision + deploy the above into Azure? [y/N] " reply
    case "${reply}" in
        y|Y|yes|YES) ;;
        *) echo "Aborted."; exit 1;;
    esac
fi

# --- Step 1: provision the tier's resources --------------------------------
echo
echo "==> Provisioning (infra/provision.sh --phase ${PHASE} --tier ${TIER})"
"${INFRA_DIR}/provision.sh" --phase "${PHASE}" --tier "${TIER}"

# --- Step 2: deploy the API (and schema bootstrap) -------------------------
echo
echo "==> Deploying (infra/deploy.sh --phase ${PHASE} --tier ${TIER})"
"${INFRA_DIR}/deploy.sh" --phase "${PHASE}" --tier "${TIER}"

# --- Hand-off to the integration step --------------------------------------
API_URL="$(az webapp show --name "${RG}-api" --resource-group "${RG}" \
    --query defaultHostName -o tsv 2>/dev/null || true)"
echo
echo "PROVISION + DEPLOY OK (phase ${PHASE}, ${TIER} tier)"
if [[ -n "${API_URL}" ]]; then
    echo "API: https://${API_URL}"
    echo "Next: ./02_make_integration.sh --phase ${PHASE}"
    echo "      (runs the integration suite with DB_ENGINE=postgresql, LOCAL_AUTH_BYPASS absent)"
fi
