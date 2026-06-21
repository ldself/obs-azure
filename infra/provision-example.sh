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
  PG_SKU=$VAL_PG_SKU; PG_TIER=$VAL_PG_TIER; PG_ZONAL=$VAL_PG_ZONAL
  PG_STORAGE=$VAL_PG_STORAGE; PG_BACKUP=$VAL_PG_BACKUP_RETENTION
  APP_SKU=$VAL_APP_SKU; APP_WORKERS=$VAL_APP_WORKERS
  BLOB_SKU=$VAL_BLOB_SKU; SWA_SKU=$VAL_SWA_SKU; KV_RET=$VAL_KV_RETENTION
else
  RG="obs-prod-rg"
  PG_SKU=$PROD_PG_SKU; PG_TIER=$PROD_PG_TIER; PG_ZONAL=$PROD_PG_ZONAL
  PG_STORAGE=$PROD_PG_STORAGE; PG_BACKUP=$PROD_PG_BACKUP_RETENTION
  APP_SKU=$PROD_APP_SKU; APP_WORKERS=$PROD_APP_WORKERS
  BLOB_SKU=$PROD_BLOB_SKU; SWA_SKU=$PROD_SWA_SKU; KV_RET=$PROD_KV_RETENTION
fi

APP_SERVICE_NAME="${RG}-api"
SWA_NAME="${RG}-swa"

echo "Provisioning $TIER tier for phase $PHASE into $RG..."
echo "  App Service: $APP_SERVICE_NAME"
echo "  Static Web App: $SWA_NAME"
az account set --subscription "$SUBSCRIPTION_ID"
az group create --name "$RG" --location "$AZURE_REGION"

echo "Provisioning Key Vault with ${KV_RET}-day retention..."
# Access-policy permission model (not RBAC) so the §4.2/§4.3 `az keyvault set-policy`
# grants below apply. Without this, subscriptions that default Key Vault to RBAC
# would reject set-policy and the managed identities could not read secrets.
az keyvault create --name "${RG}-kv" --resource-group "$RG" \
  --retention-days "$KV_RET" --enable-rbac-authorization false

# PostgreSQL Flexible Server (Cloud Migration v2.0 §3.2, tier-parameterized)
# §3.2 pins the admin login to the literal "obsadmin" (PG_ADMIN_USER in tier.env);
# the password is generated here, passed once to --admin-password, and persisted
# only in Key Vault. §3.2 acceptance: "The admin password is not stored in any
# file or CI/CD pipeline definition — Key Vault only." Never echo it; never write
# it to tier.env. Complexity guaranteed: 28 alnum chars + one upper/lower/digit/symbol.
PG_ADMIN_PASSWORD="$(openssl rand -base64 48 | tr -dc 'A-Za-z0-9' | head -c 28)Aa1#"
echo "Provisioning PostgreSQL Flexible Server (${PG_TIER} tier, ${PG_SKU} SKU, ${PG_STORAGE}GB storage, ${PG_BACKUP}-day backup retention, zonal resiliency: ${PG_ZONAL})..."
az postgres flexible-server create --name "${RG}-pg" --resource-group "$RG" \
  --location "$AZURE_REGION" --sku-name "$PG_SKU" --tier "$PG_TIER" \
  --admin-user "$PG_ADMIN_USER" --admin-password "$PG_ADMIN_PASSWORD" \
  --storage-size "$PG_STORAGE" --version 16 --zonal-resiliency "$PG_ZONAL" \
  --backup-retention "$PG_BACKUP"

# Persist the connection string in Key Vault as obs-pg-connection-string
# (Cloud Migration v2.0 §3.2 / §3.4 secret table). The App Service and Functions
# managed identities read this secret at runtime (§4.2/§4.3); no static credential
# lives in code or config. dbname=obs_db is created later by schema bootstrap.
PG_FQDN="$(az postgres flexible-server show --name "${RG}-pg" --resource-group "$RG" \
  --query fullyQualifiedDomainName -o tsv)"
echo "Storing obs-pg-connection-string secret in ${RG}-kv..."
az keyvault secret set --vault-name "${RG}-kv" --name obs-pg-connection-string \
  --value "host=${PG_FQDN} port=5432 dbname=obs_db user=${PG_ADMIN_USER} password=${PG_ADMIN_PASSWORD} sslmode=require" \
  --output none
unset PG_ADMIN_PASSWORD

# Always-on per phase: App Service + managed identity + Application Insights
#### VALIDATE QUOTA LIMITS
echo "Provisioning App Service plan (${APP_SKU} SKU, ${APP_WORKERS} workers)..."
az appservice plan create --name "${RG}-asp" --resource-group "$RG" \
  --sku "$APP_SKU" --is-linux --number-of-workers "$APP_WORKERS"
echo "Provisioning Web App..."
az webapp create --name "$APP_SERVICE_NAME" --resource-group "$RG" \
  --plan "${RG}-asp" --runtime PYTHON:3.11
echo "Disabling App Service platform-level authentication (letting FastAPI handle auth)..."
az webapp auth-classic update --name "$APP_SERVICE_NAME" --resource-group "$RG" \
  --enabled false
echo "Assigning managed identity and granting Key Vault secret access (§4.2)..."
APP_PRINCIPAL_ID="$(az webapp identity assign --name "$APP_SERVICE_NAME" --resource-group "$RG" \
  --query principalId -o tsv)"
az keyvault set-policy --name "${RG}-kv" --object-id "$APP_PRINCIPAL_ID" \
  --secret-permissions get list --output none
echo "Creating Application Insights component..."
az monitor app-insights component create --app "${RG}-ai" --location "$AZURE_REGION" \
  --resource-group "$RG" --application-type web

echo "Storing secrets in Key Vault (Cloud Migration v2.0 §10)..."
# ENTRA_CLIENT_SECRET is stored as a secret so deploy.sh and future operations
# read it from Key Vault, not regenerate it. ENTRA_REDIRECT_URI and FRONTEND_BASE_URL
# are derived from the SWA hostname after creation (below).
az keyvault secret set --vault-name "${RG}-kv" --name obs-entra-client-secret \
  --value "${ENTRA_CLIENT_SECRET}" --output none

# PostgreSQL firewall: allow the App Service outbound IPs (Cloud Migration v2.0 §4.5).
# outboundIpAddresses is a comma-separated list; each gets its own single-IP rule.
echo "Configuring PostgreSQL firewall for App Service outbound IPs (§4.5)..."
APP_OUTBOUND_IPS="$(az webapp show --name "$APP_SERVICE_NAME" --resource-group "$RG" \
  --query outboundIpAddresses -o tsv)"
IFS=',' read -ra _app_ips <<< "$APP_OUTBOUND_IPS"
rule_n=0
for ip in "${_app_ips[@]}"; do
  rule_n=$((rule_n + 1))
  az postgres flexible-server firewall-rule create --name "${RG}-pg" --resource-group "$RG" \
    --rule-name "allow-app-service-${rule_n}" --start-ip-address "$ip" --end-ip-address "$ip" \
    --output none
done

# Phase-gated resources per the §4 resource matrix of the Cloud Validation Strategy.
# Static Web Apps: all UI-bearing phases.
case "$PHASE" in
  0|1|2|3|4|5|6|7|8|9)
    echo "Provisioning Static Web App (${SWA_SKU} SKU)..."
    az staticwebapp create --name "$SWA_NAME" --resource-group "$RG" \
      --sku "$SWA_SKU" --location "$AZURE_REGION"
    SWA_URL=$(az staticwebapp show --name "$SWA_NAME" --resource-group "$RG" \
      --query defaultHostname --output tsv)
    # Derive FRONTEND_BASE_URL and ENTRA_REDIRECT_URI from the SWA hostname
    FRONTEND_BASE_URL="https://${SWA_URL}"
    ENTRA_REDIRECT_URI="https://${SWA_URL}/api/v1/auth/callback"
    echo "Storing derived URLs in Key Vault..."
    az keyvault secret set --vault-name "${RG}-kv" --name obs-frontend-base-url \
      --value "${FRONTEND_BASE_URL}" --output none
    az keyvault secret set --vault-name "${RG}-kv" --name obs-entra-redirect-uri \
      --value "${ENTRA_REDIRECT_URI}" --output none
    echo "  FRONTEND_BASE_URL: ${FRONTEND_BASE_URL}"
    echo "  ENTRA_REDIRECT_URI: ${ENTRA_REDIRECT_URI}";;
esac
# Blob landing zone + containers: phases that touch ingestion.
case "$PHASE" in
  0|2|10)
    echo "Provisioning Blob Storage landing zone (${BLOB_SKU} SKU)..."
    az storage account create --name "obsval${PHASE}landing" --resource-group "$RG" \
      --sku "$BLOB_SKU" --kind StorageV2
    for ct in landing-actuals landing-employees landing-hierarchies archive error; do
      az storage container create --name "$ct" --account-name "obsval${PHASE}landing"
    done;;
esac
# Functions app for the ingestion pipeline.
case "$PHASE" in
  2|10)
    echo "Provisioning Functions App for ingestion pipeline (${APP_SKU} SKU, ${APP_WORKERS} workers)..."
    az storage account create --name "obsval${PHASE}fnstore" --resource-group "$RG" \
      --sku Standard_LRS --kind StorageV2
    az functionapp create --name "${RG}-pipeline" --resource-group "$RG" \
      --consumption-plan-location "$AZURE_REGION" --runtime python --runtime-version 3.11 \
      --functions-version 4 --os-type linux --storage-account "obsval${PHASE}fnstore"
    echo "Assigning Functions managed identity and granting Key Vault secret access (§4.3)..."
    FN_PRINCIPAL_ID="$(az functionapp identity assign --name "${RG}-pipeline" --resource-group "$RG" \
      --query principalId -o tsv)"
    az keyvault set-policy --name "${RG}-kv" --object-id "$FN_PRINCIPAL_ID" \
      --secret-permissions get list --output none
    # Functions MI needs blob read/write on the landing zone (Cloud Migration v2.0 §4.4).
    # object-id + principal-type avoids the Graph lookup that can fail on AAD replication lag.
    echo "Granting Functions managed identity Storage Blob Data Contributor on landing zone (§4.4)..."
    LANDING_ID="$(az storage account show --name "obsval${PHASE}landing" --resource-group "$RG" \
      --query id -o tsv)"
    az role assignment create --assignee-object-id "$FN_PRINCIPAL_ID" \
      --assignee-principal-type ServicePrincipal \
      --role "Storage Blob Data Contributor" --scope "$LANDING_ID" --output none
    # Functions on Consumption have dynamic outbound IPs; per §4.5's Claude Code note,
    # allow Azure services through the PostgreSQL firewall for the validation tier
    # (0.0.0.0/0.0.0.0 is the Azure-services rule). Tighten via VNet before production.
    echo "Allowing Azure services through PostgreSQL firewall for Functions dynamic IPs (§4.5 note)..."
    az postgres flexible-server firewall-rule create --name "${RG}-pg" --resource-group "$RG" \
      --rule-name AllowAzureServices --start-ip-address 0.0.0.0 --end-ip-address 0.0.0.0 \
      --output none;;
esac

echo "Provisioned $TIER tier for phase $PHASE into $RG"
