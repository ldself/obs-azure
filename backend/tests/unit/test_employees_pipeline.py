"""Unit tests for the employees ingestion pipeline (Phase 2).

AC coverage:
  AC-OI-DI-01: SHA-256 dedup skips duplicate COMPLETED file
  AC-OI-DI-08: INGESTION_COMPLETED audit event
  AC-OI-DI-09: Employee upsert preserves employee_id / created_at
"""

from __future__ import annotations

import uuid
from datetime import datetime
from datetime import timezone
from pathlib import Path

import pytest

from backend.app.db import helpers
from backend.app.services.audit_service import AuditEvent
from backend.pipeline import employees_pipeline
from backend.pipeline import pipeline_service as ps


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _seed_cost_center(temp_db: Path) -> None:
    now = datetime.now(timezone.utc)
    iid = "seed-hier-emp-001"
    with helpers.transaction() as conn:
        helpers.exec_write(conn,
            "INSERT INTO obs.ingestion_control "
            "(ingestion_id, file_name, content_hash, source_system, file_type, "
            " file_format, status, re_ingestion, triggered_by, started_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (iid, "seed.csv", "seedhashE", "ACCOUNTING", "COST_CENTER_HIERARCHY",
             "CSV", "COMPLETED", False, "MANUAL", now))
        helpers.exec_write(conn,
            "INSERT INTO obs.cost_center_hierarchy_nodes "
            "(node_id, hierarchy_id, node_code, node_name, node_depth, parent_node_code, "
            " is_active, last_ingestion_id, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), "H1", "CC-1001", "CC 1001", 1, None, True, iid, now, now))
        helpers.exec_write(conn,
            "INSERT INTO obs.cost_center_hierarchy_memberships "
            "(membership_id, hierarchy_id, cost_center_code, cost_center_name, "
            " level_1_code, max_depth, is_active, last_ingestion_id, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), "H1", "CC-1001", "CC 1001", "CC-1001", 1, True, iid, now, now))


_HEADER = (
    "p_number,company,entity,cost_center,department,last_name,first_name,"
    "last_hire_date,salary_structure,title,annual_salary,home_state,"
    "termination_date,work_state,office,workplace_flexibility,"
    "management_production,job_grade,full_time_part_time,hours_worked,"
    "ot_hours_worked,fte\n"
)

_VALID_ROW = "P001,ACME,1001,CC-1001,ENG,Smith,John,2020-01-15,EIP,Engineer,100000,CA,,CA,HQ,FLEX,PROD,G1,FT,,,1.0\n"


def _write_file(tmp_path: Path, name: str, content: str) -> str:
    p = tmp_path / name
    p.write_text(content)
    return str(p)


def _patch_landing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LANDING_ZONE_PATH", str(tmp_path))
    monkeypatch.setenv("ARCHIVE_PATH", str(tmp_path / "archive"))
    monkeypatch.setenv("ERROR_PATH", str(tmp_path / "error"))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_valid_rows_promoted(
    temp_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_cost_center(temp_db)
    _patch_landing(monkeypatch, tmp_path)

    f = _write_file(tmp_path, "employees_2026.csv", _HEADER + _VALID_ROW)
    iid = employees_pipeline.run(f)
    assert iid is not None

    row = helpers.fetch_one(
        "SELECT status, promoted_rows FROM obs.ingestion_control WHERE ingestion_id = ?",
        (iid,),
    )
    assert row[0] == ps.STATUS_COMPLETED
    assert row[1] == 1


def test_aipeip_eligible_derived(
    temp_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """aipeip_eligible must be derived from salary_structure, not ingested directly."""
    _seed_cost_center(temp_db)
    _patch_landing(monkeypatch, tmp_path)

    # EIP → aipeip_eligible = True
    row_eip = "P002,ACME,1001,CC-1001,ENG,Doe,Jane,2019-03-01,EIP,Sr Eng,120000,CA,,CA,HQ,FLEX,PROD,G2,FT,,,1.0\n"
    # EXEMPT → aipeip_eligible = False
    row_exempt = "P003,ACME,1001,CC-1001,ENG,Brown,Bob,2018-06-15,EXEMPT,Analyst,80000,NY,,NY,NYC,OFFICE,PROD,G1,FT,,,1.0\n"
    f = _write_file(tmp_path, "employees_aipeip.csv", _HEADER + row_eip + row_exempt)
    iid = employees_pipeline.run(f)
    assert iid is not None

    row = helpers.fetch_one(
        "SELECT aipeip_eligible FROM obs.employees WHERE p_number = ? AND cost_center = ?",
        ("P002", "CC-1001"),
    )
    assert row is not None
    assert bool(row[0]) is True

    row2 = helpers.fetch_one(
        "SELECT aipeip_eligible FROM obs.employees WHERE p_number = ? AND cost_center = ?",
        ("P003", "CC-1001"),
    )
    assert row2 is not None
    assert bool(row2[0]) is False


def test_upsert_preserves_employee_id_and_created_at(
    temp_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-OI-DI-09: employee_id and created_at survive a second ingest of the same natural key."""
    _seed_cost_center(temp_db)
    _patch_landing(monkeypatch, tmp_path)

    row1 = "P010,ACME,1001,CC-1001,ENG,Test,User,2021-05-01,AIP,Dev,90000,TX,,TX,DAL,OFFICE,PROD,G1,FT,,,1.0\n"
    f1 = tmp_path / "employees_emp1.csv"
    f1.write_text(_HEADER + row1)
    iid1 = employees_pipeline.run(str(f1))
    assert iid1 is not None

    emp_after_1 = helpers.fetch_one(
        "SELECT employee_id, created_at, annual_salary FROM obs.employees WHERE p_number = ?",
        ("P010",),
    )
    assert emp_after_1 is not None
    orig_employee_id = emp_after_1[0]
    orig_created_at = emp_after_1[1]

    # Update salary — same natural key, different salary
    row2 = "P010,ACME,1001,CC-1001,ENG,Test,User,2021-05-01,AIP,Sr Dev,110000,TX,,TX,DAL,OFFICE,PROD,G2,FT,,,1.0\n"
    f2 = tmp_path / "employees_emp2.csv"
    f2.write_text(_HEADER + row2)
    iid2 = employees_pipeline.run(str(f2))
    assert iid2 is not None

    emp_after_2 = helpers.fetch_one(
        "SELECT employee_id, created_at, annual_salary FROM obs.employees WHERE p_number = ?",
        ("P010",),
    )
    assert emp_after_2 is not None
    assert emp_after_2[0] == orig_employee_id   # employee_id unchanged
    assert emp_after_2[1] == orig_created_at    # created_at unchanged
    assert emp_after_2[2] == 110000             # salary updated


def test_missing_cost_center_quarantined(
    temp_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_cost_center(temp_db)
    _patch_landing(monkeypatch, tmp_path)

    # 1 valid row + 1 missing-CC row → error_rate = 50%, below 80% threshold
    good = "P001,ACME,1001,CC-1001,ENG,Smith,John,2020-01-15,EIP,Eng,100000,CA,,CA,HQ,FLEX,PROD,G1,FT,,,1.0\n"
    bad = "P020,ACME,1001,CC-UNKNOWN,ENG,Nobody,Jane,2020-01-01,EIP,Dev,80000,CA,,CA,HQ,FLEX,PROD,G1,FT,,,1.0\n"
    f = _write_file(tmp_path, "employees_badcc.csv", _HEADER + good + bad)
    iid = employees_pipeline.run(f)
    assert iid is not None

    q = helpers.fetch_one(
        "SELECT quarantine_reason FROM obs.employees_quarantine WHERE ingestion_id = ?",
        (iid,),
    )
    assert q is not None
    assert q[0] == ps.QR_MISSING_COST_CENTER


def test_idempotent_duplicate_file(
    temp_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-OI-DI-01: Running the same employees file twice skips on second run."""
    _seed_cost_center(temp_db)
    _patch_landing(monkeypatch, tmp_path)

    f = tmp_path / "employees_idem.csv"
    f.write_text(_HEADER + _VALID_ROW)
    iid1 = employees_pipeline.run(str(f))
    assert iid1 is not None

    f.write_text(_HEADER + _VALID_ROW)
    iid2 = employees_pipeline.run(str(f))
    assert iid2 is None  # skipped as duplicate

    count = helpers.fetch_one("SELECT COUNT(*) FROM obs.employees", ())
    assert count[0] == 1


def test_ingestion_completed_audit_event(
    temp_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-OI-DI-08: audit event written in same transaction (RULE 6)."""
    _seed_cost_center(temp_db)
    _patch_landing(monkeypatch, tmp_path)

    f = _write_file(tmp_path, "employees_audit.csv", _HEADER + _VALID_ROW)
    iid = employees_pipeline.run(f)
    assert iid is not None

    row = helpers.fetch_one(
        "SELECT event_type FROM obs.audit_log WHERE entity_id = ? AND event_type = ?",
        (iid, AuditEvent.INGESTION_COMPLETED),
    )
    assert row is not None
    assert row[0] == AuditEvent.INGESTION_COMPLETED


def test_merge_idempotency_different_file_name(
    temp_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Check 10: row-level merge() idempotency — same records in a differently-named file
    must not create duplicate rows (RULE 10).
    SHA-256 dedup is bypassed because file names differ; merge() prevents duplicates."""
    _seed_cost_center(temp_db)
    _patch_landing(monkeypatch, tmp_path)

    # First ingest
    f1 = tmp_path / "employees_v1.csv"
    f1.write_text(_HEADER + _VALID_ROW)
    iid1 = employees_pipeline.run(str(f1))
    assert iid1 is not None

    count_after_1 = helpers.fetch_one("SELECT COUNT(*) FROM obs.employees", ())
    assert count_after_1[0] == 1

    # Same record in a different file name — SHA-256 dedup does not trigger
    f2 = tmp_path / "employees_v2.csv"
    f2.write_text(_HEADER + _VALID_ROW)
    iid2 = employees_pipeline.run(str(f2))
    assert iid2 is not None  # new ingestion_id (different file name)

    # merge() must have idempotently updated rather than inserted a duplicate
    count_after_2 = helpers.fetch_one("SELECT COUNT(*) FROM obs.employees", ())
    assert count_after_2[0] == 1
