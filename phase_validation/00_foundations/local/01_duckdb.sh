#!/usr/bin/env bash
#
# Phase 00 — Foundations / local validation: 01_duckdb
#
# Bootstrap the local DuckDB database from schema/bootstrap.sql and confirm that
# every table it creates is queryable. Generated from the approved plan
# "Bootstrap local DuckDB and confirm every table is queryable".
#
# Steps:
#   1. Resolve the repo root and the project venv python.
#   2. Clean-rebuild the DuckDB database via `make db-reset`
#      (CREATE TABLE statements have no IF NOT EXISTS, so a stale file must be
#      removed first — db-reset does rm + db-bootstrap + db-seed).
#   3. List every table in schema `obs` and run SELECT COUNT(*) against each,
#      reusing the app's own connect() so catalog/schema wiring is identical.
#
# Exit status: 0 only if the bootstrap applies cleanly AND every obs table is
# selectable; non-zero otherwise.

set -euo pipefail

# --- Resolve repo root (this script lives in phase_validation/00_foundations/local/) ---
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
cd "${REPO_ROOT}"

# --- Choose the python interpreter (prefer the project venv) ---
if [[ -x "${REPO_ROOT}/.venv/bin/python" ]]; then
    PYTHON="${REPO_ROOT}/.venv/bin/python"
else
    PYTHON="${PYTHON:-python}"
fi
echo "Repo root : ${REPO_ROOT}"
echo "Python    : ${PYTHON}"
echo

# --- Step 2: clean-rebuild the local DuckDB database ---
echo "==> Rebuilding local DuckDB (make db-reset)"
PYTHON="${PYTHON}" make db-reset
echo

# --- Step 3: verify every table in schema obs is queryable ---
echo "==> Verifying every obs table is queryable"
"${PYTHON}" - <<'PY'
import sys
from backend.app.db.engine import connect

conn = connect()
tables = [r[0] for r in conn.execute(
    "SELECT table_name FROM information_schema.tables "
    "WHERE table_schema='obs' ORDER BY table_name"
).fetchall()]
print(f"Found {len(tables)} tables in schema obs")

failures = []
for t in tables:
    try:
        n = conn.execute(f'SELECT COUNT(*) FROM obs."{t}"').fetchone()[0]
        print(f"  OK   obs.{t} ({n} rows)")
    except Exception as e:  # noqa: BLE001 - report any table that fails to select
        failures.append((t, str(e)))
        print(f"  FAIL obs.{t}: {e}")
conn.close()

if failures:
    print(f"{len(failures)} FAILED")
    sys.exit(1)
if not tables:
    print("NO TABLES FOUND — bootstrap did not create schema obs")
    sys.exit(1)
print("ALL TABLES QUERYABLE")
PY
