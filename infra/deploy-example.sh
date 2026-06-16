#!/usr/bin/env bash
# infra/deploy-example.sh — copy to infra/deploy.sh, make executable.
# Deploys the API (and SWA/Functions where the phase requires them) to the resources
# provisioned by provision.sh, then bootstraps the PostgreSQL schema.
# Enforces the production configuration contract (Cloud Migration v2.0 §10):
#   DB_ENGINE=postgresql, LOCAL_AUTH_BYPASS absent, secrets via Key Vault managed identity.
set -euo pipefail
source "$(dirname "$0")/tier.env"

PHASE=""; TIER="validation"
while [[ $# -gt 0 ]]; do case "$1" in
  --phase) PHASE="$2"; shift 2;;
  --tier)  TIER="$2";  shift 2;;
  *) echo "unknown arg: $1" >&2; exit 2;; esac; done
[[ -z "$PHASE" ]] && { echo 'usage: deploy.sh --phase <n> [--tier validation|production]'; exit 2; }

if [[ "$TIER" == "validation" ]]; then RG="obs-val-${PHASE}-rg"; else RG="obs-prod-rg"; fi

az account set --subscription "$SUBSCRIPTION_ID"

# App settings: enforce the production configuration contract. Note LOCAL_AUTH_BYPASS is
# deliberately NOT set — the startup assertion (RULE 8) must see it absent off localhost.
az webapp config appsettings set --name "${RG}-api" --resource-group "$RG" --settings \
  DB_ENGINE=postgresql \
  ENTRA_TENANT_ID="$ENTRA_TENANT_ID" \
  ENTRA_CLIENT_ID="$ENTRA_CLIENT_ID" \
  KEY_VAULT_NAME="${RG}-kv"

# Bootstrap the PostgreSQL schema with the same script used for production
# (Cloud Migration v2.0 §5). Requires schema/bootstrap_pg.sql (open item, Cloud
# Migration v2.0 §12 — must exist before this step runs).
PG_HOST="$(az postgres flexible-server show --name "${RG}-pg" --resource-group "$RG" --query fullyQualifiedDomainName -o tsv)"
if [[ -f schema/bootstrap_pg.sql ]]; then
  echo "Applying schema/bootstrap_pg.sql to ${PG_HOST}..."
  # psql connection uses Entra token or admin creds per your tier.env / Key Vault setup.
  psql "host=${PG_HOST} dbname=obs sslmode=require" -f schema/bootstrap_pg.sql
else
  echo "WARNING: schema/bootstrap_pg.sql not found — schema not applied." >&2
fi

# Deploy the API code.
( cd backend && zip -r ../_api.zip . -x '*/__pycache__/*' >/dev/null )
az webapp deploy --name "${RG}-api" --resource-group "$RG" --src-path _api.zip --type zip
rm -f _api.zip

# Deploy the frontend to Static Web Apps where the phase has a UI surface.
case "$PHASE" in
  0|3|4|5|6|7|8|9)
    ( cd frontend && npm ci && npm run build )
    SWA_TOKEN="$(az staticwebapp secrets list --name "${RG}-swa" --resource-group "$RG" --query 'properties.apiKey' -o tsv)"
    npx --yes @azure/static-web-apps-cli deploy frontend/dist --deployment-token "$SWA_TOKEN" --env production;;
esac

# Deploy the Functions pipeline where the phase requires it.
case "$PHASE" in
  2|10)
    ( cd backend/pipeline && func azure functionapp publish "${RG}-pipeline" --python );;
esac

API_URL="$(az webapp show --name "${RG}-api" --resource-group "$RG" --query defaultHostName -o tsv)"
echo "Deployed phase $PHASE to $TIER tier. API: https://${API_URL}"
echo "Run integration tests against https://${API_URL} with DB_ENGINE=postgresql, LOCAL_AUTH_BYPASS absent."
