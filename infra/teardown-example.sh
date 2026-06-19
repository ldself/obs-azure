#!/usr/bin/env bash
# infra/teardown-example.sh — copy to infra/teardown.sh, make executable.
# Deletes the validation resource group for a phase and verifies nothing remains.
# Guardrails: refuses any non-validation tier and any production-named target.
set -euo pipefail
source "$(dirname "$0")/tier.env"

PHASE=""; TIER="validation"
while [[ $# -gt 0 ]]; do case "$1" in
  --phase) PHASE="$2"; shift 2;;
  --tier)  TIER="$2";  shift 2;;
  *) echo "unknown arg: $1" >&2; exit 2;; esac; done
[[ -z "$PHASE" ]] && { echo 'usage: teardown.sh --phase <n> [--tier validation]'; exit 2; }

# GUARDRAIL: teardown is for the validation tier only.
if [[ "$TIER" != "validation" ]]; then
  echo 'REFUSED: teardown.sh only operates on the validation tier.' >&2; exit 1; fi
RG="obs-val-${PHASE}-rg"
# GUARDRAIL: never target a production-named group.
if [[ "$RG" == obs-prod* ]]; then
  echo 'REFUSED: target resolves to a production-named group.' >&2; exit 1; fi

az account set --subscription "$SUBSCRIPTION_ID"

echo "Tearing down validation tier for phase $PHASE by deleting resource group $RG..."
az group delete --name "$RG" --yes

# Purge the now soft-deleted Key Vault so its globally-unique name is free to
# recreate on the next provision (validation vaults carry no purge protection).
KV="${RG}-kv"
if [[ -n "$(az keyvault list-deleted --query "[?name=='$KV'].name" -o tsv)" ]]; then
  echo "Purging soft-deleted Key Vault $KV..."
  az keyvault purge --name "$KV"
fi

# Verify zero residual resources.
if [[ "$(az group exists --name "$RG")" == "true" ]]; then
  echo 'WARNING: resource group still present after delete.' >&2; exit 1; fi
echo "Tore down $RG; no residual resources."
