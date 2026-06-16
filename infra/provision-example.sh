#!/usr/bin/env bash
# infra/provision-example.sh — copy to infra/provision.sh, make executable, fill in tier.env.
# Provisions a tier's Azure resources for a single Build Sequencing Plan phase.
# Reuses the canonical az commands from Azure Cloud Migration Specification v2.0 §3,
# parameterized by tier (validation = cost-reduced/ephemeral, production = full footprint).
set -euo pipefail
source "$(dirname "$0")/tier.env"

PHASE=""; TIER="validation"
while [[ $# -gt 0 ]]; do case "$1" in
  --phase) PHASE="$2"; shift 2;;
  --tier)  TIER="$2";  shift 2;;
  *) echo "unknown arg: $1" >&2; exit 2;; esac; done
[[ -z "$PHASE" ]] && { echo 'usage: provision.sh --phase <n> [--tier validation|production]'; exit 2; }

if [[ "$TIER" == "validation" ]]; then
  RG="obs-val-${PHASE}-rg"
  PG_SKU=$VAL_PG_SKU; PG_TIER=$VAL_PG_TIER; PG_HA=$VAL_PG_HA
  PG_STORAGE=$VAL_PG_STORAGE; PG_BACKUP=$VAL_PG_BACKUP_RETENTION
  APP_SKU=$VAL_APP_SKU; APP_WORKERS=$VAL_APP_WORKERS
  BLOB_SKU=$VAL_BLOB_SKU; SWA_SKU=$VAL_SWA_SKU; KV_RET=$VAL_KV_RETENTION
else
  RG="obs-prod-rg"
  PG_SKU=$PROD_PG_SKU; PG_TIER=$PROD_PG_TIER; PG_HA=$PROD_PG_HA
  PG_STORAGE=$PROD_PG_STORAGE; PG_BACKUP=$PROD_PG_BACKUP_RETENTION
  APP_SKU=$PROD_APP_SKU; APP_WORKERS=$PROD_APP_WORKERS
  BLOB_SKU=$PROD_BLOB_SKU; SWA_SKU=$PROD_SWA_SKU; KV_RET=$PROD_KV_RETENTION
fi

az account set --subscription "$SUBSCRIPTION_ID"
az group create --name "$RG" --location "$AZURE_REGION"
az keyvault create --name "${RG}-kv" --resource-group "$RG" \
  --enable-soft-delete true --retention-days "$KV_RET"

# PostgreSQL Flexible Server (Cloud Migration v2.0 §3.2, tier-parameterized)
az postgres flexible-server create --name "${RG}-pg" --resource-group "$RG" \
  --location "$AZURE_REGION" --sku-name "$PG_SKU" --tier "$PG_TIER" \
  --storage-size "$PG_STORAGE" --version 16 --high-availability "$PG_HA" \
  --backup-retention "$PG_BACKUP"

# Always-on per phase: App Service + managed identity + Application Insights
az appservice plan create --name "${RG}-asp" --resource-group "$RG" \
  --sku "$APP_SKU" --is-linux --number-of-workers "$APP_WORKERS"
az webapp create --name "${RG}-api" --resource-group "$RG" \
  --plan "${RG}-asp" --runtime PYTHON:3.11
az webapp identity assign --name "${RG}-api" --resource-group "$RG"
az monitor app-insights component create --app "${RG}-ai" --location "$AZURE_REGION" \
  --resource-group "$RG" --application-type web

# Phase-gated resources per the §4 resource matrix of the Cloud Validation Strategy.
# Static Web Apps: all UI-bearing phases.
case "$PHASE" in
  0|3|4|5|6|7|8|9)
    az staticwebapp create --name "${RG}-swa" --resource-group "$RG" \
      --sku "$SWA_SKU" --location "$AZURE_REGION";;
esac
# Blob landing zone + containers: phases that touch ingestion.
case "$PHASE" in
  0|2|10)
    az storage account create --name "obsval${PHASE}landing" --resource-group "$RG" \
      --sku "$BLOB_SKU" --kind StorageV2
    for ct in landing-actuals landing-employees landing-hierarchies archive error; do
      az storage container create --name "$ct" --account-name "obsval${PHASE}landing"
    done;;
esac
# Functions app for the ingestion pipeline.
case "$PHASE" in
  2|10)
    az storage account create --name "obsval${PHASE}fnstore" --resource-group "$RG" \
      --sku Standard_LRS --kind StorageV2
    az functionapp create --name "${RG}-pipeline" --resource-group "$RG" \
      --consumption-plan-location "$AZURE_REGION" --runtime python --runtime-version 3.11 \
      --functions-version 4 --os-type linux --storage-account "obsval${PHASE}fnstore";;
esac

echo "Provisioned $TIER tier for phase $PHASE into $RG"
