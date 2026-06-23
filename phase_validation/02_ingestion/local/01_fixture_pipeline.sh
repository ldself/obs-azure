#!/bin/bash

# Phase 2 Validation: Fixture-Based Pipeline Promote/Quarantine Test
# ====================================================================
# Validates the actuals and employees ingestion pipelines by:
#   1. Bootstrapping a fresh DuckDB for each test case
#   2. Seeding minimal dimension data (cost center CC-1001, account 6000/001)
#   3. Running sample fixture CSVs through the pipeline
#   4. Asserting valid records promoted and invalid ones quarantined
#
# Test cases:
#   TC-01  actuals — 3 valid USD rows → COMPLETED, all 3 promoted
#   TC-02  actuals — 2 USD + 1 EUR → PARTIAL, EUR row quarantined (OI-DI-03)
#   TC-03  actuals — unknown cost center → MISSING_COST_CENTER quarantine
#   TC-04  actuals — unknown account → MISSING_ACCOUNT quarantine
#   TC-05  actuals — >80% error rate → FAILED, 0 promoted (OI-DI-02)
#   TC-06  employees — 2 valid rows → COMPLETED, both promoted
#   TC-07  employees — unknown cost center → MISSING_COST_CENTER quarantine

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
echo -e "${BLUE}Phase 2 — Fixture Pipeline Promote / Quarantine Test${NC}"
echo -e "${BLUE}======================================================${NC}"
echo "Project root: $PROJECT_ROOT"
echo ""

# ── write Python test script to a temp file ───────────────────────────────────
PYTHON_SCRIPT=$(mktemp /tmp/obs_fixture_test_XXXXXX.py)

cat > "$PYTHON_SCRIPT" << 'PYEOF'
"""
Phase 2 local validation: fixture-based pipeline promote/quarantine test.

Each test case spins up a fresh DuckDB, seeds dimension data, runs a fixture
CSV through the pipeline, and asserts expected ingestion_control state and
promote/quarantine table counts.
"""
import logging
import os
import shutil
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

# Suppress pipeline INFO noise so test result lines are readable
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

GREEN = "\033[0;32m"
RED   = "\033[0;31m"
YELLOW = "\033[1;33m"
NC    = "\033[0m"

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
    """Create a temp dir with a bootstrapped, dimension-seeded DuckDB.

    Mutates settings.duckdb_path so subsequent pipeline calls use this DB.
    Returns the temp dir Path (caller should shutil.rmtree after assertions).
    """
    tmp = Path(tempfile.mkdtemp(prefix="obs_fixture_"))
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
    """Insert cost center CC-1001 and expense account 6000/001."""
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


# ── fixture builders ──────────────────────────────────────────────────────────

_ACT_HDR = (
    "entity,year,month,cost_center,account,sub_account,currency,amount,"
    "bonus_type,product,distribution_channel,stat_category,"
    "profit_center,sender_cost_center,assignment,functional_area,"
    "partner_functional_area_text\n"
)


def _act_row(
    month: int,
    amount: float,
    cc: str = "CC-1001",
    acct: str = "6000",
    sub: str = "001",
    currency: str = "USD",
) -> str:
    return (
        f"1001,2026,{month},{cc},{acct},{sub},{currency},{amount:.2f},"
        f",PRD,CHN,STAT,PC,SCC,ASSN,FA,\n"
    )


_EMP_HDR = (
    "p_number,company,entity,cost_center,department,last_name,first_name,"
    "salary_structure,home_state,work_state,office,workplace_flexibility,"
    "management_production,job_grade,full_time_part_time,"
    "annual_salary,fte,last_hire_date\n"
)


def _emp_row(p_number: str, cc: str = "CC-1001") -> str:
    return (
        f"{p_number},ACME,1001,{cc},DEPT,Smith,Jane,"
        f"EIP,CA,CA,HQ,FLEX,MGMT,G5,FT,80000,1.0,2020-01-15\n"
    )


def write_fixture(tmp: Path, name: str, content: str) -> str:
    p = tmp / name
    p.write_text(content)
    return str(p)


# ── test cases ────────────────────────────────────────────────────────────────

def tc01_actuals_valid_promote() -> None:
    print(f"\n{YELLOW}[TC-01] Actuals — 3 valid USD rows → COMPLETED, all promoted{NC}")
    tmp = make_fresh_db()
    content = _ACT_HDR + "".join(_act_row(m, 1000.0 * m) for m in (1, 2, 3))
    f = write_fixture(tmp, "actuals_valid.csv", content)

    iid = actuals_pipeline.run(f)
    assert iid, "Pipeline returned None (file skipped unexpectedly)"

    row = helpers.fetch_one(
        "SELECT status, promoted_rows, quarantined_rows "
        "FROM obs.ingestion_control WHERE ingestion_id = ?",
        (iid,),
    )
    assert row[0] == ps.STATUS_COMPLETED, f"status: expected COMPLETED, got {row[0]}"
    assert row[1] == 3,                  f"promoted_rows: expected 3, got {row[1]}"
    assert row[2] == 0,                  f"quarantined_rows: expected 0, got {row[2]}"

    n = helpers.fetch_one("SELECT COUNT(*) FROM obs.actuals WHERE is_deleted = FALSE", ())[0]
    assert n == 3, f"obs.actuals active rows: expected 3, got {n}"

    log_pass("3 valid USD actuals → COMPLETED, 3 rows in obs.actuals, 0 quarantined")
    shutil.rmtree(tmp, ignore_errors=True)


def tc02_actuals_non_usd_quarantines() -> None:
    print(f"\n{YELLOW}[TC-02] Actuals — EUR row quarantines, USD rows promote (OI-DI-03){NC}")
    tmp = make_fresh_db()
    content = (
        _ACT_HDR
        + _act_row(1, 100.0)
        + _act_row(2, 200.0)
        + _act_row(3, 300.0, currency="EUR")
    )
    f = write_fixture(tmp, "actuals_mixed_currency.csv", content)

    iid = actuals_pipeline.run(f)
    assert iid

    row = helpers.fetch_one(
        "SELECT status, promoted_rows, quarantined_rows "
        "FROM obs.ingestion_control WHERE ingestion_id = ?",
        (iid,),
    )
    assert row[0] == ps.STATUS_PARTIAL, f"status: expected PARTIAL, got {row[0]}"
    assert row[1] == 2,                 f"promoted_rows: expected 2, got {row[1]}"
    assert row[2] == 1,                 f"quarantined_rows: expected 1, got {row[2]}"

    q = helpers.fetch_one(
        "SELECT quarantine_reason, quarantine_status "
        "FROM obs.actuals_quarantine WHERE ingestion_id = ?",
        (iid,),
    )
    assert q,            "No actuals_quarantine record found"
    assert q[0] == ps.QR_NON_USD, f"reason: expected NON_USD_CURRENCY, got {q[0]}"
    assert q[1] == "PENDING",     f"quarantine_status: expected PENDING, got {q[1]}"

    n = helpers.fetch_one("SELECT COUNT(*) FROM obs.actuals WHERE is_deleted = FALSE", ())[0]
    assert n == 2, f"obs.actuals active rows: expected 2, got {n}"

    log_pass("EUR row → NON_USD_CURRENCY quarantine (PENDING); 2 USD rows promoted → PARTIAL")
    shutil.rmtree(tmp, ignore_errors=True)


def tc03_actuals_missing_cc_quarantines() -> None:
    print(f"\n{YELLOW}[TC-03] Actuals — unknown cost center → MISSING_COST_CENTER quarantine{NC}")
    tmp = make_fresh_db()
    content = (
        _ACT_HDR
        + _act_row(1, 100.0)
        + _act_row(2, 200.0, cc="CC-UNKNOWN")
    )
    f = write_fixture(tmp, "actuals_bad_cc.csv", content)

    iid = actuals_pipeline.run(f)
    assert iid

    q = helpers.fetch_one(
        "SELECT quarantine_reason "
        "FROM obs.actuals_quarantine WHERE ingestion_id = ?",
        (iid,),
    )
    assert q,                                   "No actuals_quarantine record found"
    assert q[0] == ps.QR_MISSING_COST_CENTER,   f"reason: expected MISSING_COST_CENTER, got {q[0]}"

    log_pass("Unknown cost center → MISSING_COST_CENTER in actuals_quarantine")
    shutil.rmtree(tmp, ignore_errors=True)


def tc04_actuals_missing_account_quarantines() -> None:
    print(f"\n{YELLOW}[TC-04] Actuals — unknown expense account → MISSING_ACCOUNT quarantine{NC}")
    tmp = make_fresh_db()
    content = (
        _ACT_HDR
        + _act_row(1, 100.0)
        + _act_row(2, 200.0, acct="9999", sub="XXX")
    )
    f = write_fixture(tmp, "actuals_bad_account.csv", content)

    iid = actuals_pipeline.run(f)
    assert iid

    q = helpers.fetch_one(
        "SELECT quarantine_reason "
        "FROM obs.actuals_quarantine WHERE ingestion_id = ?",
        (iid,),
    )
    assert q,                               "No actuals_quarantine record found"
    assert q[0] == ps.QR_MISSING_ACCOUNT,   f"reason: expected MISSING_ACCOUNT, got {q[0]}"

    log_pass("Unknown account/sub_account → MISSING_ACCOUNT in actuals_quarantine")
    shutil.rmtree(tmp, ignore_errors=True)


def tc05_actuals_high_error_rate_rejected() -> None:
    print(f"\n{YELLOW}[TC-05] Actuals — >80% error rate → FAILED, 0 promoted (OI-DI-02){NC}")
    tmp = make_fresh_db()
    # 9 EUR rows + 1 valid USD = 90% quarantinable → file-level FAILED
    content = (
        _ACT_HDR
        + "".join(_act_row(m, 100.0, currency="EUR") for m in range(1, 10))
        + _act_row(10, 200.0)
    )
    f = write_fixture(tmp, "actuals_high_err.csv", content)

    iid = actuals_pipeline.run(f)
    assert iid

    row = helpers.fetch_one(
        "SELECT status, promoted_rows "
        "FROM obs.ingestion_control WHERE ingestion_id = ?",
        (iid,),
    )
    assert row[0] == ps.STATUS_FAILED, f"status: expected FAILED, got {row[0]}"
    assert row[1] == 0,               f"promoted_rows: expected 0, got {row[1]}"

    n = helpers.fetch_one("SELECT COUNT(*) FROM obs.actuals WHERE is_deleted = FALSE", ())[0]
    assert n == 0, f"obs.actuals active rows: expected 0, got {n}"

    log_pass(">80% error rate → FAILED ingestion, 0 rows promoted to obs.actuals")
    shutil.rmtree(tmp, ignore_errors=True)


def tc06_employees_valid_promote() -> None:
    print(f"\n{YELLOW}[TC-06] Employees — 2 valid rows → COMPLETED, both promoted{NC}")
    tmp = make_fresh_db()
    content = _EMP_HDR + _emp_row("P001") + _emp_row("P002")
    f = write_fixture(tmp, "employees_valid.csv", content)

    iid = employees_pipeline.run(f)
    assert iid

    row = helpers.fetch_one(
        "SELECT status, promoted_rows, quarantined_rows "
        "FROM obs.ingestion_control WHERE ingestion_id = ?",
        (iid,),
    )
    assert row[0] == ps.STATUS_COMPLETED, f"status: expected COMPLETED, got {row[0]}"
    assert row[1] == 2,                  f"promoted_rows: expected 2, got {row[1]}"
    assert row[2] == 0,                  f"quarantined_rows: expected 0, got {row[2]}"

    n = helpers.fetch_one("SELECT COUNT(*) FROM obs.employees WHERE is_active = TRUE", ())[0]
    assert n == 2, f"obs.employees active rows: expected 2, got {n}"

    log_pass("2 valid employees → COMPLETED, 2 rows in obs.employees, 0 quarantined")
    shutil.rmtree(tmp, ignore_errors=True)


def tc07_employees_missing_cc_quarantines() -> None:
    print(f"\n{YELLOW}[TC-07] Employees — unknown cost center → MISSING_COST_CENTER quarantine{NC}")
    tmp = make_fresh_db()
    content = (
        _EMP_HDR
        + _emp_row("P003")
        + _emp_row("P004", cc="CC-MISSING")
    )
    f = write_fixture(tmp, "employees_bad_cc.csv", content)

    iid = employees_pipeline.run(f)
    assert iid

    row = helpers.fetch_one(
        "SELECT status, promoted_rows, quarantined_rows "
        "FROM obs.ingestion_control WHERE ingestion_id = ?",
        (iid,),
    )
    assert row[0] == ps.STATUS_PARTIAL, f"status: expected PARTIAL, got {row[0]}"
    assert row[1] == 1,                 f"promoted_rows: expected 1, got {row[1]}"
    assert row[2] == 1,                 f"quarantined_rows: expected 1, got {row[2]}"

    q = helpers.fetch_one(
        "SELECT quarantine_reason, quarantine_status "
        "FROM obs.employees_quarantine WHERE ingestion_id = ?",
        (iid,),
    )
    assert q,                                  "No employees_quarantine record found"
    assert q[0] == ps.QR_MISSING_COST_CENTER,  f"reason: expected MISSING_COST_CENTER, got {q[0]}"
    assert q[1] == "PENDING",                  f"quarantine_status: expected PENDING, got {q[1]}"

    log_pass("Employee with unknown CC → MISSING_COST_CENTER quarantine (PENDING) → PARTIAL")
    shutil.rmtree(tmp, ignore_errors=True)


# ── runner ────────────────────────────────────────────────────────────────────

TESTS = [
    tc01_actuals_valid_promote,
    tc02_actuals_non_usd_quarantines,
    tc03_actuals_missing_cc_quarantines,
    tc04_actuals_missing_account_quarantines,
    tc05_actuals_high_error_rate_rejected,
    tc06_employees_valid_promote,
    tc07_employees_missing_cc_quarantines,
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
