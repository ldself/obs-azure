"""Unit tests for the shared pipeline service utilities (Phase 2).

AC-OI-DI-01 coverage: SHA-256 dedup skips duplicate COMPLETED files.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from backend.app.db import helpers
from backend.pipeline import pipeline_service as ps


def _write_csv(tmp_path: Path, name: str, content: str) -> str:
    p = tmp_path / name
    p.write_text(content)
    return str(p)


def test_compute_sha256_returns_64char_hex(tmp_path: Path, temp_db: Path) -> None:
    f = tmp_path / "file.csv"
    f.write_bytes(b"hello")
    h = ps.compute_sha256(str(f))
    assert len(h) == 64
    assert all(c in "0123456789abcdef" for c in h)


def test_compute_sha256_different_content_gives_different_hash(tmp_path: Path, temp_db: Path) -> None:
    a = tmp_path / "a.csv"
    b = tmp_path / "b.csv"
    a.write_bytes(b"aaa")
    b.write_bytes(b"bbb")
    assert ps.compute_sha256(str(a)) != ps.compute_sha256(str(b))


def test_check_duplicate_false_on_empty(temp_db: Path) -> None:
    assert not ps.check_duplicate("file.csv", "abc123")


def test_check_duplicate_true_for_completed(temp_db: Path) -> None:
    iid = "test-iid-001"
    with helpers.transaction() as conn:
        ps.create_ingestion_record(
            conn,
            ingestion_id=iid,
            file_name="file.csv",
            content_hash="deadbeef",
            file_type=ps.FILE_TYPE_ACTUALS,
            source_system="ACCOUNTING",
            file_format="CSV",
            triggered_by=ps.TRIGGERED_SCHEDULED,
        )
        ps.update_ingestion_record(
            conn, ingestion_id=iid, status=ps.STATUS_COMPLETED, total_rows=1,
            valid_rows=1, quarantined_rows=0, rejected_rows=0, promoted_rows=1,
            error_rate=0.0,
        )
    assert ps.check_duplicate("file.csv", "deadbeef")


def test_check_duplicate_false_for_failed(temp_db: Path) -> None:
    """A FAILED record for same (file_name, content_hash) should NOT block re-ingestion."""
    iid = "test-iid-002"
    with helpers.transaction() as conn:
        ps.create_ingestion_record(
            conn,
            ingestion_id=iid,
            file_name="file.csv",
            content_hash="deadbeef",
            file_type=ps.FILE_TYPE_ACTUALS,
            source_system="ACCOUNTING",
            file_format="CSV",
            triggered_by=ps.TRIGGERED_SCHEDULED,
        )
        ps.update_ingestion_record(
            conn, ingestion_id=iid, status=ps.STATUS_FAILED, total_rows=10,
            valid_rows=1, quarantined_rows=9, rejected_rows=9, promoted_rows=0,
            error_rate=0.9,
        )
    assert not ps.check_duplicate("file.csv", "deadbeef")


def test_create_ingestion_record_status_running(temp_db: Path) -> None:
    iid = "iid-running-001"
    with helpers.transaction() as conn:
        ps.create_ingestion_record(
            conn,
            ingestion_id=iid,
            file_name="actuals.csv",
            content_hash="hash1",
            file_type=ps.FILE_TYPE_ACTUALS,
            source_system="ACCOUNTING",
            file_format="CSV",
            triggered_by=ps.TRIGGERED_SCHEDULED,
        )
    row = helpers.fetch_one(
        "SELECT status, file_name FROM obs.ingestion_control WHERE ingestion_id = ?",
        (iid,),
    )
    assert row is not None
    assert row[0] == ps.STATUS_RUNNING
    assert row[1] == "actuals.csv"


def test_update_ingestion_record_sets_counts(temp_db: Path) -> None:
    iid = "iid-update-001"
    with helpers.transaction() as conn:
        ps.create_ingestion_record(
            conn, ingestion_id=iid, file_name="f.csv", content_hash="h",
            file_type=ps.FILE_TYPE_EMPLOYEES, source_system="HR_ADMIN",
            file_format="CSV", triggered_by=ps.TRIGGERED_SCHEDULED,
        )
        ps.update_ingestion_record(
            conn, ingestion_id=iid, status=ps.STATUS_PARTIAL,
            total_rows=10, valid_rows=8, quarantined_rows=2,
            rejected_rows=0, promoted_rows=8, error_rate=0.2,
        )
    row = helpers.fetch_one(
        "SELECT status, total_rows, promoted_rows, error_rate FROM obs.ingestion_control WHERE ingestion_id = ?",
        (iid,),
    )
    assert row[0] == ps.STATUS_PARTIAL
    assert row[1] == 10
    assert row[3] == pytest.approx(0.2)


def test_write_quarantine_record_persists(temp_db: Path) -> None:
    iid = "iid-q-001"
    with helpers.transaction() as conn:
        ps.create_ingestion_record(
            conn, ingestion_id=iid, file_name="a.csv", content_hash="h2",
            file_type=ps.FILE_TYPE_ACTUALS, source_system="ACCOUNTING",
            file_format="CSV", triggered_by=ps.TRIGGERED_SCHEDULED,
        )
        qid = ps.write_quarantine_record(
            conn,
            ingestion_id=iid,
            quarantine_table="obs.actuals_quarantine",
            quarantine_reason=ps.QR_NON_USD,
            source_row_number=3,
            row_data={"currency": "EUR", "cost_center": "CC-1001"},
        )
    row = helpers.fetch_one(
        "SELECT quarantine_reason, quarantine_status, source_row_number "
        "FROM obs.actuals_quarantine WHERE quarantine_id = ?",
        (qid,),
    )
    assert row is not None
    assert row[0] == ps.QR_NON_USD
    assert row[1] == "PENDING"
    assert row[2] == 3
