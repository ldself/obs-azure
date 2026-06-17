#!/usr/bin/env bash
#
# Phase 00 — Foundations / local validation: 03_append_only_trigger
#
# Validate that obs.audit_log is append-only (RULE 6: never UPDATE/DELETE
# obs.audit_log). The database-level guarantee is a PostgreSQL-only
# BEFORE UPDATE OR DELETE trigger (trg_audit_log_append_only ->
# obs.audit_log_append_only(), which RAISEs). The local engine is DuckDB, which
# has NO trigger mechanism (see schema/bootstrap.sql:117-119). So locally we
# cannot watch the trigger fire — instead we document/verify the
# DuckDB-equivalent: the application-code guarantee plus a static check that the
# real PostgreSQL trigger is defined correctly.
#
# Three checks, then a summary:
#   A. DuckDB behavioral probe (documentary, non-fatal): inside a rolled-back
#      transaction, insert a probe row and attempt UPDATE/DELETE. On DuckDB both
#      succeed -> empirically confirms "no equivalent trigger". Rollback proves
#      obs.audit_log is left untouched.
#   B. PostgreSQL trigger definition present & correct in schema/bootstrap_pg.sql
#      (fatal). The closest local proxy to "the trigger rejects UPDATE/DELETE".
#   C. Application-code guarantee (fatal): no UPDATE/DELETE against audit_log
#      anywhere under backend/. This is the mechanism the local DuckDB path
#      relies on in place of the trigger.
#
# Exit status: 0 iff checks B and C pass (A is reported but only fails the run on
# an unexpected error, e.g. engine != duckdb or a probe row surviving rollback).

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

# --- Ensure the local DuckDB database exists (bootstrap defensively if missing) ---
if [[ ! -f "${REPO_ROOT}/local-data/obs.duckdb" ]]; then
    echo "==> local-data/obs.duckdb not found; bootstrapping (make db-bootstrap)"
    PYTHON="${PYTHON}" make db-bootstrap
    echo
fi

# --- Run all three checks in one Python process; it owns the exit status ---
"${PYTHON}" - <<'PY'
import re
import sys
from pathlib import Path

from backend.app.db.engine import connect, engine_name, placeholder

REPO_ROOT = Path(__file__).resolve().parents[0] if False else Path.cwd()
PG_BOOTSTRAP = REPO_ROOT / "schema" / "bootstrap_pg.sql"
BACKEND_DIR = REPO_ROOT / "backend"

results = {}  # check -> (passed: bool|None, detail: str)  (None = informational)

# ---------------------------------------------------------------------------
# Check A — DuckDB behavioral probe (documents the equivalent; non-fatal)
# ---------------------------------------------------------------------------
print("==> Check A: DuckDB behavioral probe (documents the no-trigger equivalent)")
engine = engine_name()
print(f"    active engine: {engine}")
if engine != "duckdb":
    print(f"    FAIL: expected local engine 'duckdb', got {engine!r}")
    results["A"] = (False, f"engine was {engine!r}, not duckdb")
else:
    ph = placeholder()
    probe_id = "APPEND_ONLY_PROBE_00"
    conn = connect()
    update_raised = delete_raised = None
    leaked = None
    try:
        conn.execute("BEGIN TRANSACTION")
        # Insert a probe row supplying every NOT NULL column.
        conn.execute(
            f"INSERT INTO obs.audit_log "
            f"(event_id, event_timestamp, event_type, user_id, outcome) "
            f"VALUES ({ph}, CURRENT_TIMESTAMP, {ph}, {ph}, {ph})",
            [probe_id, "VALIDATION_PROBE", "validation", "SUCCESS"],
        )

        try:
            conn.execute(
                f"UPDATE obs.audit_log SET outcome = {ph} WHERE event_id = {ph}",
                ["FAILURE", probe_id],
            )
            update_raised = False
        except Exception as exc:  # noqa: BLE001 - we want to record whether it raised
            update_raised = True
            print(f"    UPDATE raised: {exc}")

        try:
            conn.execute(
                f"DELETE FROM obs.audit_log WHERE event_id = {ph}",
                [probe_id],
            )
            delete_raised = False
        except Exception as exc:  # noqa: BLE001
            delete_raised = True
            print(f"    DELETE raised: {exc}")
    finally:
        # Never persist the probe regardless of what happened above.
        try:
            conn.execute("ROLLBACK")
        except Exception:  # noqa: BLE001
            pass
        # Prove the table is untouched: the probe id must not exist post-rollback.
        leaked = conn.execute(
            f"SELECT COUNT(*) FROM obs.audit_log WHERE event_id = {ph}",
            [probe_id],
        ).fetchone()[0]
        conn.close()

    if leaked:
        print(f"    FAIL: {leaked} probe row(s) survived ROLLBACK — table was mutated")
        results["A"] = (False, "probe row leaked past rollback")
    else:
        def word(raised):
            return "rejected (trigger present!)" if raised else "accepted (no trigger)"
        print(f"    UPDATE {word(update_raised)}; DELETE {word(delete_raised)}")
        print("    probe rolled back — obs.audit_log unchanged")
        if not update_raised and not delete_raised:
            detail = "DuckDB accepted UPDATE/DELETE (no trigger — as documented)"
        else:
            detail = "DuckDB rejected a mutation (stronger than documented)"
        print(f"    {detail}")
        # Documentary: confirming the documented fact is a pass-for-information.
        results["A"] = (None, detail)
print()

# ---------------------------------------------------------------------------
# Check B — PostgreSQL trigger definition present & correct (fatal)
# ---------------------------------------------------------------------------
print("==> Check B: PostgreSQL append-only trigger defined in schema/bootstrap_pg.sql")
sql = PG_BOOTSTRAP.read_text()
# Collapse whitespace so multi-line DDL matches regardless of formatting.
flat = re.sub(r"\s+", " ", sql)

requirements = {
    "function obs.audit_log_append_only()":
        re.search(r"create or replace function\s+obs\.audit_log_append_only\s*\(", flat, re.I),
    "function RAISEs an exception":
        re.search(r"raise exception", flat, re.I),
    "CREATE TRIGGER trg_audit_log_append_only":
        re.search(r"create trigger\s+trg_audit_log_append_only", flat, re.I),
    "fires BEFORE UPDATE OR DELETE ON obs.audit_log":
        re.search(r"before update or delete on\s+obs\.audit_log", flat, re.I),
    "FOR EACH ROW":
        re.search(r"for each row", flat, re.I),
}
b_ok = True
for label, hit in requirements.items():
    mark = "OK  " if hit else "FAIL"
    if not hit:
        b_ok = False
    print(f"    {mark} {label}")
results["B"] = (b_ok, "trigger DDL present and correct" if b_ok else "trigger DDL missing/malformed")
print()

# ---------------------------------------------------------------------------
# Check C — Application-code guarantee: no UPDATE/DELETE of audit_log (fatal)
# ---------------------------------------------------------------------------
print("==> Check C: no UPDATE/DELETE against audit_log in backend/ application code")
# Match a SQL UPDATE/DELETE verb adjacent to audit_log (optionally schema- or
# catalog-qualified, optionally quoted). Ignores prose that merely names the table.
mut_re = re.compile(
    r"\b(update|delete\s+from)\b[^;]*?\b(?:obs|obsdb)?\.?\"?audit_log\"?",
    re.I,
)
offenders = []
for path in sorted(BACKEND_DIR.rglob("*")):
    if path.suffix not in (".py", ".sql") or not path.is_file():
        continue
    for lineno, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
        stripped = line.lstrip()
        # Skip obvious comment lines (Python # and SQL --).
        if stripped.startswith("#") or stripped.startswith("--"):
            continue
        if mut_re.search(line):
            offenders.append(f"{path.relative_to(REPO_ROOT)}:{lineno}: {line.strip()}")

if offenders:
    print(f"    FAIL: {len(offenders)} UPDATE/DELETE statement(s) target audit_log:")
    for o in offenders:
        print(f"      {o}")
    results["C"] = (False, f"{len(offenders)} forbidden mutation(s) in app code")
else:
    print("    OK   no UPDATE/DELETE against audit_log found under backend/")
    results["C"] = (True, "application code never mutates audit_log")
print()

# ---------------------------------------------------------------------------
# Summary + exit status
# ---------------------------------------------------------------------------
print("==> Summary")
print("    Local (DuckDB)     : append-only enforced in application code (RULE 6);")
a_passed, a_detail = results["A"]
print(f"                         no DB trigger — {a_detail}")
c_passed, c_detail = results["C"]
print(f"                         app-code check: {'PASS' if c_passed else 'FAIL'} ({c_detail})")
b_passed, b_detail = results["B"]
print("    Prod  (PostgreSQL) : append-only enforced by trg_audit_log_append_only;")
print(f"                         static check: {'PASS' if b_passed else 'FAIL'} ({b_detail})")
print()

# Fatal gates: B and C. A fails the run only on an unexpected error (passed is False).
fatal_fail = (results["A"][0] is False) or (not b_passed) or (not c_passed)
if fatal_fail:
    print("APPEND-ONLY VALIDATION FAILED")
    sys.exit(1)
print("APPEND-ONLY VALIDATION OK")
PY
