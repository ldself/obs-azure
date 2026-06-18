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
# PG_CONNECTION_STRING is injected as a Key Vault reference (Cloud Migration v2.0 §10):
# App Service resolves it at runtime via the managed identity granted in provision.sh
# (§4.2). The secret value never appears in app settings, the portal, or code.
echo "Configuring API app settings with Key Vault reference to the PostgreSQL connection string secret..."
KV_URI="$(az keyvault show --name "${RG}-kv" --resource-group "$RG" --query properties.vaultUri -o tsv)"
az webapp config appsettings set --name "${RG}-api" --resource-group "$RG" --settings \
  DB_ENGINE=postgresql \
  "PG_CONNECTION_STRING=@Microsoft.KeyVault(SecretUri=${KV_URI}secrets/obs-pg-connection-string/)" \
  ENTRA_TENANT_ID="$ENTRA_TENANT_ID" \
  ENTRA_CLIENT_ID="$ENTRA_CLIENT_ID" \
  KEY_VAULT_NAME="${RG}-kv" \
  SCM_DO_BUILD_DURING_DEPLOYMENT=true

# Startup command (Cloud Migration v2.0 §3.5). App Service cannot guess the module
# path; without this, Oryx's default gunicorn guess never finds the API and the site
# serves a default "Not Found" page. Pin Gunicorn + UvicornWorker to backend.app.main:app
# — the same module path used locally (uvicorn backend.app.main:app). The deployment zip
# (below) therefore places the backend/ package directory at the site root.
echo "Setting the App Service startup command (Gunicorn + UvicornWorker)..."
az webapp config set --name "${RG}-api" --resource-group "$RG" \
  --startup-file "gunicorn -w 4 -k uvicorn.workers.UvicornWorker backend.app.main:app"

# Bootstrap the PostgreSQL schema with the same script used for production
# (Cloud Migration v2.0 §5). Requires schema/bootstrap_pg.sql (open item, Cloud
# Migration v2.0 §12 — must exist before this step runs).
if [[ -f schema/bootstrap_pg.sql ]]; then
  echo "Applying schema/bootstrap_pg.sql via the obs-pg-connection-string secret (Cloud Migration v2.0 §5)..."
  # Connect with the admin connection string from Key Vault — the same secret the app
  # reads at runtime. It carries user/password and dbname=obs_db; never echo it.
  PG_CONN="$(az keyvault secret show --vault-name "${RG}-kv" --name obs-pg-connection-string --query value -o tsv)"
  # Ensure the obs_db database exists before bootstrapping its schema (Cloud Migration
  # v2.0 §3.2 CREATE DATABASE). PostgreSQL has no CREATE DATABASE IF NOT EXISTS, so guard
  # on pg_database. The maintenance connection reuses the same admin credentials against
  # the default "postgres" database (dbname swapped). Requires the deploy host's IP to be
  # allowed through the PostgreSQL firewall (run from Cloud Shell, or add a temporary rule).
  PG_MAINT_CONN="${PG_CONN/dbname=obs_db/dbname=postgres}"
  if ! psql "$PG_MAINT_CONN" -tAc "SELECT 1 FROM pg_database WHERE datname='obs_db'" | grep -q 1; then
    echo "Creating obs_db database..."
    psql "$PG_MAINT_CONN" -c "CREATE DATABASE obs_db"
  fi
  unset PG_MAINT_CONN
  psql "$PG_CONN" -f schema/bootstrap_pg.sql
  unset PG_CONN
else
  echo "WARNING: schema/bootstrap_pg.sql not found — schema not applied." >&2
fi

# Deploy the API code. The site root must contain the backend/ package DIRECTORY
# (the startup command imports backend.app.main:app and main.py uses absolute
# `from backend.app...` imports), plus a requirements.txt at the root for Oryx to
# install runtime deps. Stage both in a temp dir, then zip from there.
echo "Deploying API code to Web App..."
STAGE="$(mktemp -d)"
cp -R backend "$STAGE/backend"
cp backend/requirements.txt "$STAGE/requirements.txt"
( cd "$STAGE" && zip -r "$OLDPWD/_api.zip" . -x '*/__pycache__/*' >/dev/null )
rm -rf "$STAGE"
az webapp deploy --name "${RG}-api" --resource-group "$RG" --src-path _api.zip --type zip
rm -f _api.zip

# Deploy the frontend to Static Web Apps where the phase has a UI surface.
case "$PHASE" in
  3|4|5|6|7|8|9)
    echo "Deploying frontend to Static Web App..."
    ( cd frontend && npm ci && npm run build )
    SWA_TOKEN="$(az staticwebapp secrets list --name "${RG}-swa" --resource-group "$RG" --query 'properties.apiKey' -o tsv)"
    npx --yes @azure/static-web-apps-cli deploy frontend/dist --deployment-token "$SWA_TOKEN" --env production;;
esac

# Deploy the Functions pipeline where the phase requires it.
case "$PHASE" in
  2|10)
    echo "Deploying Functions pipeline..."
    ( cd backend/pipeline && func azure functionapp publish "${RG}-pipeline" --python );;
esac

API_URL="$(az webapp show --name "${RG}-api" --resource-group "$RG" --query defaultHostName -o tsv)"
echo "Deployed phase $PHASE to $TIER tier. API: https://${API_URL}"
echo "Run integration tests against https://${API_URL} with DB_ENGINE=postgresql, LOCAL_AUTH_BYPASS absent."
