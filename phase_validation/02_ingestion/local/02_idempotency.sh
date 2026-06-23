#!/bin/bash

# Phase 2 Validation: Idempotency Test
# =====================================================================
# Validates RULE 10: running the same file through the pipeline twice
# produces the same result — no duplicate rows.
#
# Test cases:
#   TC-IDP-01  actuals — process identical file twice; second run skipped,
#              row count in obs.actuals unchanged
#   TC-IDP-02  employees — process identical file twice; second run skipped,
#              row count in obs.employees unchanged

set -uo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../" && pwd)"
VENV_DIR="/tmp/obs_validation_venv"

# ── venv setup ────────────────────────────────────────────────────────────────
if [ ! -d "$VENV_DIR" ]; then
    echo "Setting up validation venv at $VENV_DIR ..."
    python3 -m venv "$VENV_DIR" >/dev/null 2>&1
    source "$VENV_DIR/bin/activate"
    pip install -q -r "$PROJECT_ROOT/backend/requirements.txt" 2>/dev/null
else
    source "$VENV_DIR/bin/activate"
fi

# ── colors ────────────────────────────────────────────────────────────────────
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}======================================================${NC}"
echo -e "${BLUE}Phase 2 — Idempotency Test (RULE 10)${NC}"
echo -e "${BLUE}======================================================${NC}"
echo "Project root: $PROJECT_ROOT"
echo ""

# ── write Python test script to a temp file ───────────────────────────────────
PYTHON_SCRIPT=$(mktemp /tmp/obs_idempotency_test_XXXXXX.py)

cat > "$PYTHON_SCRIPT" << 'PYEOF'
"""
Phase 2 local validation: idempotency test (RULE 10).

Each test case spins up a fresh DuckDB, seeds dimension data, runs a fixture
CSV through the pipeline twice using the exact same file bytes, and asserts:
  - The first run returns an ingestion_id and promotes rows.
  - The second run returns None (skipped as a duplicate).
  - Row counts in the promotion table are unchanged after the second run.
"""
import logging
import os
import shutil
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(level=logging.WARNING)

PROJECT_ROOT = os.environ.get("PROJECT_ROOT", ".")
sys.path.insert(0, PROJECT_ROOT)

os.environ["DB_ENGINE"] = "duckdb"
os.environ["LOCAL_AUTH_BYPASS"] = "true"

from backend.app.config import settings
from backend.app.db import engine, helpers
from backend.app.db.bootstrap import apply_bootstrap
from backend.pipeline import actuals_pipeline, employees_pipeline
from backend.pipeline import pipeline_service as ps

settings.db_engine = "duckdb"

GREEN  = "\033[0;32m"
RED    = "\033[0;31m"
YELLOW = "\033[1;33m"
NC     = "\033[0m"

PASS: list[str] = []
FAIL: list[str] = []


def log_pass(msg: str) -> None:
    print(f"  {GREEN}✓ PASS{NC}  {msg}")
    PASS.append(msg)


def log_fail(msg: str, detail: str = "") -> None:
    print(f"  {RED}✗ FAIL{NC}  {msg}")
    if detail:
        print(f"         {detail}")
    FAIL.append(msg)


# ── DB bootstrap + dimension seed ─────────────────────────────────────────────

def make_fresh_db() -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="obs_idempotency_"))
    db_path = str(tmp / "test.duckdb")
    settings.duckdb_path = db_path

    conn = engine.connect_duckdb(db_path)
    try:
        apply_bootstrap(conn)
        _seed_dimensions(conn)
        conn.commit()
    finally:
        conn.close()

    return tmp


def _seed_dimensions(conn) -> None:
    now = datetime.now(timezone.utc)
    iid = "seed-hier-001"

    conn.execute(
        "INSERT INTO obs.ingestion_control "
        "(ingestion_id, file_name, content_hash, source_system, file_type, "
        " file_format, status, re_ingestion, triggered_by, started_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [iid, "seed.csv", "seedhash001", "ACCOUNTING", "COST_CENTER_HIERARCHY",
         "CSV", "COMPLETED", False, "MANUAL", now],
    )
    conn.execute(
        "INSERT INTO obs.cost_center_hierarchy_nodes "
        "(node_id, hierarchy_id, node_code, node_name, node_depth, parent_node_code, "
        " is_active, last_ingestion_id, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [str(uuid.uuid4()), "H1", "CC-1001", "Cost Center 1001", 1, None,
         True, iid, now, now],
    )
    conn.execute(
        "INSERT INTO obs.cost_center_hierarchy_memberships "
        "(membership_id, hierarchy_id, cost_center_code, cost_center_name, "
        " level_1_code, max_depth, is_active, last_ingestion_id, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [str(uuid.uuid4()), "H1", "CC-1001", "Cost Center 1001",
         "CC-1001", 1, True, iid, now, now],
    )
    conn.execute(
        "INSERT OR IGNORE INTO obs.expense_accounts "
        "(account, sub_account, account_name, is_active) VALUES (?, ?, ?, ?)",
        ["6000", "001", "Test Account", True],
    )


# ── fixture content ───────────────────────────────────────────────────────────

_ACT_CONTENT = (
    "entity,year,month,cost_center,account,sub_account,currency,amount,"
    "bonus_type,product,distribution_channel,stat_category,"
    "profit_center,sender_cost_center,assignment,functional_area,"
    "partner_functional_area_text\n"
    "1001,2026,1,CC-1001,6000,001,USD,1000.00,,PRD,CHN,STAT,PC,SCC,ASSN,FA,\n"
    "1001,2026,2,CC-1001,6000,001,USD,2000.00,,PRD,CHN,STAT,PC,SCC,ASSN,FA,\n"
    "1001,2026,3,CC-1001,6000,001,USD,3000.00,,PRD,CHN,STAT,PC,SCC,ASSN,FA,\n"
)

_EMP_CONTENT = (
    "p_number,company,entity,cost_center,department,last_name,first_name,"
    "salary_structure,home_state,work_state,office,workplace_flexibility,"
    "management_production,job_grade,full_time_part_time,"
    "annual_salary,fte,last_hire_date\n"
    "P001,ACME,1001,CC-1001,DEPT,Smith,Jane,EIP,CA,CA,HQ,FLEX,MGMT,G5,FT,80000,1.0,2020-01-15\n"
    "P002,ACME,1001,CC-1001,DEPT,Jones,Bob,EIP,NY,NY,HQ,FLEX,MGMT,G4,FT,75000,1.0,2019-06-01\n"
)


def write_fixture(tmp: Path, name: str, content: str) -> str:
    p = tmp / name
    p.write_text(content)
    return str(p)


# ── test cases ────────────────────────────────────────────────────────────────

def tc_idp_01_actuals_idempotent() -> None:
    print(f"\n{YELLOW}[TC-IDP-01] Actuals — identical file processed twice; second run skipped{NC}")
    tmp = make_fresh_db()
    file_path = write_fixture(tmp, "actuals_idp.csv", _ACT_CONTENT)

    # First run
    iid1 = actuals_pipeline.run(file_path)
    assert iid1, "First run: pipeline returned None unexpectedly"

    row1 = helpers.fetch_one(
        "SELECT status, promoted_rows FROM obs.ingestion_control WHERE ingestion_id = ?",
        (iid1,),
    )
    assert row1[0] == ps.STATUS_COMPLETED, f"First run status: expected COMPLETED, got {row1[0]}"
    assert row1[1] == 3,                  f"First run promoted_rows: expected 3, got {row1[1]}"

    count_after_first = helpers.fetch_one(
        "SELECT COUNT(*) FROM obs.actuals WHERE is_deleted = FALSE", ()
    )[0]
    assert count_after_first == 3, f"obs.actuals after first run: expected 3, got {count_after_first}"

    # The file was archived/moved by the pipeline; write the identical bytes again
    # so the duplicate check fires on the same (file_name, content_hash)
    file_path = write_fixture(tmp, "actuals_idp.csv", _ACT_CONTENT)

    # Second run — must be skipped
    iid2 = actuals_pipeline.run(file_path)
    assert iid2 is None, (
        f"Second run: expected None (duplicate skipped), got ingestion_id={iid2}"
    )

    count_after_second = helpers.fetch_one(
        "SELECT COUNT(*) FROM obs.actuals WHERE is_deleted = FALSE", ()
    )[0]
    assert count_after_second == count_after_first, (
        f"obs.actuals row count changed after second run: "
        f"before={count_after_first}, after={count_after_second}"
    )

    ingestion_count = helpers.fetch_one(
        "SELECT COUNT(*) FROM obs.ingestion_control "
        "WHERE file_name = ? AND content_hash != 'seedhash001'",
        ("actuals_idp.csv",),
    )[0]
    assert ingestion_count == 1, (
        f"ingestion_control records for this file: expected 1 (no duplicate record), got {ingestion_count}"
    )

    log_pass(
        "Actuals: first run promoted 3 rows (COMPLETED); "
        "second identical run returned None, row count unchanged at 3, "
        "no duplicate ingestion_control record"
    )
    shutil.rmtree(tmp, ignore_errors=True)


def tc_idp_02_employees_idempotent() -> None:
    print(f"\n{YELLOW}[TC-IDP-02] Employees — identical file processed twice; second run skipped{NC}")
    tmp = make_fresh_db()
    file_path = write_fixture(tmp, "employees_idp.csv", _EMP_CONTENT)

    # First run
    iid1 = employees_pipeline.run(file_path)
    assert iid1, "First run: pipeline returned None unexpectedly"

    row1 = helpers.fetch_one(
        "SELECT status, promoted_rows FROM obs.ingestion_control WHERE ingestion_id = ?",
        (iid1,),
    )
    assert row1[0] == ps.STATUS_COMPLETED, f"First run status: expected COMPLETED, got {row1[0]}"
    assert row1[1] == 2,                  f"First run promoted_rows: expected 2, got {row1[1]}"

    count_after_first = helpers.fetch_one(
        "SELECT COUNT(*) FROM obs.employees WHERE is_active = TRUE", ()
    )[0]
    assert count_after_first == 2, f"obs.employees after first run: expected 2, got {count_after_first}"

    # Re-write the identical file so the duplicate check fires on (file_name, content_hash)
    file_path = write_fixture(tmp, "employees_idp.csv", _EMP_CONTENT)

    # Second run — must be skipped
    iid2 = employees_pipeline.run(file_path)
    assert iid2 is None, (
        f"Second run: expected None (duplicate skipped), got ingestion_id={iid2}"
    )

    count_after_second = helpers.fetch_one(
        "SELECT COUNT(*) FROM obs.employees WHERE is_active = TRUE", ()
    )[0]
    assert count_after_second == count_after_first, (
        f"obs.employees row count changed after second run: "
        f"before={count_after_first}, after={count_after_second}"
    )

    ingestion_count = helpers.fetch_one(
        "SELECT COUNT(*) FROM obs.ingestion_control "
        "WHERE file_name = ? AND content_hash != 'seedhash001'",
        ("employees_idp.csv",),
    )[0]
    assert ingestion_count == 1, (
        f"ingestion_control records for this file: expected 1 (no duplicate record), got {ingestion_count}"
    )

    log_pass(
        "Employees: first run promoted 2 rows (COMPLETED); "
        "second identical run returned None, row count unchanged at 2, "
        "no duplicate ingestion_control record"
    )
    shutil.rmtree(tmp, ignore_errors=True)


# ── runner ────────────────────────────────────────────────────────────────────

TESTS = [
    tc_idp_01_actuals_idempotent,
    tc_idp_02_employees_idempotent,
]

for fn in TESTS:
    try:
        fn()
    except Exception as exc:
        import traceback
        last_line = traceback.format_exc().strip().splitlines()[-1]
        log_fail(fn.__name__, last_line)

print()
print("=" * 56)
total = len(PASS) + len(FAIL)
print(f"Results: {GREEN}{len(PASS)} passed{NC}, {RED}{len(FAIL)} failed{NC} (of {total} tests)")
print("=" * 56)

if FAIL:
    print(f"{RED}Some tests failed.{NC}")
    sys.exit(1)
print(f"{GREEN}All tests passed!{NC}")
sys.exit(0)
PYEOF

# ── execute ───────────────────────────────────────────────────────────────────
(
    export PROJECT_ROOT="$PROJECT_ROOT"
    export DB_ENGINE="duckdb"
    export LOCAL_AUTH_BYPASS="true"
    "$VENV_DIR/bin/python3" "$PYTHON_SCRIPT"
)
EXIT_CODE=$?
rm -f "$PYTHON_SCRIPT"
exit $EXIT_CODE
