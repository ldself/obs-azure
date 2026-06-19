#!/usr/bin/env bash
#
# Phase 00 — Foundations / cloud validation: 03_teardown
#
# Tear down the validation-tier Azure footprint for a Build Sequencing Plan
# phase, by driving the canonical infra/ script:
#   infra/teardown.sh --phase <n> --tier <tier>   (Cloud Migration v2.0 §3)
#
# This is the final step of the Phase 00 cloud validation loop; run it after
# 01_provision_deploy.sh and 02_make_integration.sh, once validation is done
# and the billable footprint is no longer needed.
#
# Scope guardrails:
#   * Defaults to --phase 0 --tier validation (this is 00_foundations/cloud).
#   * Refuses --tier production: production is never a validation target here.
#   * Deletes Azure resources irreversibly, so it confirms the resource group
#     and tier before acting (skip with --yes for automation).
#
# infra/teardown.sh enforces its own guardrails (validation tier only, never a
# production-named group), deletes the resource group, purges the soft-deleted
# Key Vault, and verifies no residual resources remain.
#
# Exit status: 0 only if teardown succeeds; non-zero otherwise.

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
            echo "usage: 03_teardown.sh [--phase <n>] [--tier validation] [--yes]"
            exit 0;;
        *) echo "unknown arg: $1" >&2; exit 2;;
    esac
done

if [[ "${TIER}" != "validation" ]]; then
    echo "FAIL: this validation script only targets the validation tier (got '${TIER}')." >&2
    echo "      Production teardown is run directly via infra/ with intent." >&2
    exit 2
fi

RG="obs-val-${PHASE}-rg"

# --- Preflight: required script + a logged-in Azure CLI --------------------
for f in teardown.sh tier.env; do
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

# --- Confirm (irreversible deletion) ---------------------------------------
if [[ "${ASSUME_YES}" -ne 1 ]]; then
    read -r -p "Delete the above resource group and all its resources? [y/N] " reply
    case "${reply}" in
        y|Y|yes|YES) ;;
        *) echo "Aborted."; exit 1;;
    esac
fi

# --- Tear down the tier's resources ----------------------------------------
echo
echo "==> Tearing down (infra/teardown.sh --phase ${PHASE} --tier ${TIER})"
"${INFRA_DIR}/teardown.sh" --phase "${PHASE}" --tier "${TIER}"

echo
echo "TEARDOWN OK (phase ${PHASE}, ${TIER} tier)"
