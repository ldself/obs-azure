"""Unit tests for the actuals ingestion pipeline (Phase 2).

AC coverage:
  AC-OI-DI-01: SHA-256 dedup: duplicate COMPLETED file skipped
  AC-OI-DI-02: >80% error rate → file FAILED, 0 promoted
  AC-OI-DI-03: Non-USD actuals quarantined, never promoted
  AC-OI-DI-04: Alert failure non-fatal
  AC-OI-DI-08: INGESTION_COMPLETED audit event on every mutation
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.app.db import helpers
from backend.app.services.audit_service import AuditEvent
from backend.pipeline import actuals_pipeline
from backend.pipeline import pipeline_service as ps


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _seed_cost_center(temp_db: Path) -> None:
    """Insert a minimal cost center hierarchy membership for CC-1001."""
    import uuid
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    iid = "seed-hier-001"
    with helpers.transaction() as conn:
        helpers.exec_write(conn,
            "INSERT INTO obs.ingestion_control "
            "(ingestion_id, file_name, content_hash, source_system, file_type, "
            " file_format, status, re_ingestion, triggered_by, started_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (iid, "seed.csv", "seedhash", "ACCOUNTING", "COST_CENTER_HIERARCHY",
             "CSV", "COMPLETED", False, "MANUAL", now))
        helpers.exec_write(conn,
            "INSERT INTO obs.cost_center_hierarchy_nodes "
            "(node_id, hierarchy_id, node_code, node_name, node_depth, parent_node_code, "
            " is_active, last_ingestion_id, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), "H1", "CC-1001", "Cost Center 1001", 1, None,
             True, iid, now, now))
        helpers.exec_write(conn,
            "INSERT INTO obs.cost_center_hierarchy_memberships "
            "(membership_id, hierarchy_id, cost_center_code, cost_center_name, "
            " level_1_code, max_depth, is_active, last_ingestion_id, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), "H1", "CC-1001", "Cost Center 1001",
             "CC-1001", 1, True, iid, now, now))


def _seed_expense_account(temp_db: Path) -> None:
    """Insert a minimal expense account 6000/001."""
    with helpers.transaction() as conn:
        helpers.exec_write(conn,
            "INSERT OR IGNORE INTO obs.expense_accounts "
            "(account, sub_account, account_name, is_active) VALUES (?, ?, ?, ?)",
            ("6000", "001", "Test Account", True))


def _write_file(tmp_path: Path, name: str, content: str) -> str:
    p = tmp_path / name
    p.write_text(content)
    return str(p)


_VALID_ROW = (
    "entity,year,month,cost_center,account,sub_account,currency,amount,"
    "bonus_type,product,distribution_channel,stat_category,"
    "profit_center,sender_cost_center,assignment,functional_area,partner_functional_area_text\n"
    "1001,2026,1,CC-1001,6000,001,USD,1000.00,,"
    "PRD,CHN,STAT,PC,SCC,ASSN,FA,\n"
)


def _patch_landing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LANDING_ZONE_PATH", str(tmp_path))
    monkeypatch.setenv("ARCHIVE_PATH", str(tmp_path / "archive"))
    monkeypatch.setenv("ERROR_PATH", str(tmp_path / "error"))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_valid_usd_rows_promoted(
    temp_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_cost_center(temp_db)
    _seed_expense_account(temp_db)
    _patch_landing(monkeypatch, tmp_path)

    csv = (
        "entity,year,month,cost_center,account,sub_account,currency,amount,"
        "bonus_type,product,distribution_channel,stat_category,"
        "profit_center,sender_cost_center,assignment,functional_area,partner_functional_area_text\n"
        "1001,2026,1,CC-1001,6000,001,USD,100.00,,PRD,CHN,STAT,PC,SCC,ASSN,FA,\n"
        "1001,2026,2,CC-1001,6000,001,USD,200.00,,PRD,CHN,STAT,PC,SCC,ASSN,FA,\n"
        "1001,2026,3,CC-1001,6000,001,USD,300.00,,PRD,CHN,STAT,PC,SCC,ASSN,FA,\n"
    )
    f = _write_file(tmp_path, "actuals_2026.csv", csv)
    iid = actuals_pipeline.run(f)
    assert iid is not None

    row = helpers.fetch_one(
        "SELECT status, promoted_rows FROM obs.ingestion_control WHERE ingestion_id = ?",
        (iid,),
    )
    assert row[0] == ps.STATUS_COMPLETED
    assert row[1] == 3

    count = helpers.fetch_one("SELECT COUNT(*) FROM obs.actuals WHERE is_deleted = FALSE", ())
    assert count[0] == 3


def test_non_usd_quarantined(
    temp_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """OI-DI-03: Non-USD rows go to actuals_quarantine, never obs.actuals.
    2 valid USD rows + 1 EUR row → PARTIAL, EUR row quarantined, 2 promoted.
    """
    _seed_cost_center(temp_db)
    _seed_expense_account(temp_db)
    _patch_landing(monkeypatch, tmp_path)

    csv = (
        "entity,year,month,cost_center,account,sub_account,currency,amount,"
        "bonus_type,product,distribution_channel,stat_category,"
        "profit_center,sender_cost_center,assignment,functional_area,partner_functional_area_text\n"
        "1001,2026,1,CC-1001,6000,001,USD,100.00,,PRD,CHN,STAT,PC,SCC,ASSN,FA,\n"
        "1001,2026,2,CC-1001,6000,001,USD,200.00,,PRD,CHN,STAT,PC,SCC,ASSN,FA,\n"
        "1001,2026,3,CC-1001,6000,001,EUR,100.00,,PRD,CHN,STAT,PC,SCC,ASSN,FA,\n"
    )
    f = _write_file(tmp_path, "actuals_eur.csv", csv)
    iid = actuals_pipeline.run(f)
    assert iid is not None

    row = helpers.fetch_one(
        "SELECT status, quarantined_rows, promoted_rows FROM obs.ingestion_control WHERE ingestion_id = ?",
        (iid,),
    )
    assert row[0] == ps.STATUS_PARTIAL
    assert row[1] == 1
    assert row[2] == 2

    actuals_count = helpers.fetch_one("SELECT COUNT(*) FROM obs.actuals WHERE is_deleted = FALSE", ())
    assert actuals_count[0] == 2

    q_row = helpers.fetch_one(
        "SELECT quarantine_reason FROM obs.actuals_quarantine WHERE ingestion_id = ?",
        (iid,),
    )
    assert q_row is not None
    assert q_row[0] == ps.QR_NON_USD


def test_missing_cost_center_quarantined(
    temp_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_cost_center(temp_db)
    _seed_expense_account(temp_db)
    _patch_landing(monkeypatch, tmp_path)

    # 1 valid row + 1 missing-CC row → error_rate = 50%, below 80% threshold
    csv = (
        "entity,year,month,cost_center,account,sub_account,currency,amount,"
        "bonus_type,product,distribution_channel,stat_category,"
        "profit_center,sender_cost_center,assignment,functional_area,partner_functional_area_text\n"
        "1001,2026,1,CC-1001,6000,001,USD,100.00,,PRD,CHN,STAT,PC,SCC,ASSN,FA,\n"
        "1001,2026,2,CC-DOES-NOT-EXIST,6000,001,USD,100.00,,PRD,CHN,STAT,PC,SCC,ASSN,FA,\n"
    )
    f = _write_file(tmp_path, "actuals_badcc.csv", csv)
    iid = actuals_pipeline.run(f)
    assert iid is not None

    q_row = helpers.fetch_one(
        "SELECT quarantine_reason FROM obs.actuals_quarantine WHERE ingestion_id = ?",
        (iid,),
    )
    assert q_row is not None
    assert q_row[0] == ps.QR_MISSING_COST_CENTER


def test_missing_account_quarantined(
    temp_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_cost_center(temp_db)
    _seed_expense_account(temp_db)
    _patch_landing(monkeypatch, tmp_path)

    # 1 valid row + 1 bad-account row → error_rate = 50%, below 80% threshold
    csv = (
        "entity,year,month,cost_center,account,sub_account,currency,amount,"
        "bonus_type,product,distribution_channel,stat_category,"
        "profit_center,sender_cost_center,assignment,functional_area,partner_functional_area_text\n"
        "1001,2026,1,CC-1001,6000,001,USD,100.00,,PRD,CHN,STAT,PC,SCC,ASSN,FA,\n"
        "1001,2026,2,CC-1001,9999,XXX,USD,100.00,,PRD,CHN,STAT,PC,SCC,ASSN,FA,\n"
    )
    f = _write_file(tmp_path, "actuals_badacct.csv", csv)
    iid = actuals_pipeline.run(f)
    assert iid is not None

    q_row = helpers.fetch_one(
        "SELECT quarantine_reason FROM obs.actuals_quarantine WHERE ingestion_id = ?",
        (iid,),
    )
    assert q_row is not None
    assert q_row[0] == ps.QR_MISSING_ACCOUNT


def test_80pct_error_rate_rejects_file(
    temp_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-OI-DI-02: >80% error rate → FAILED, 0 rows promoted."""
    _seed_cost_center(temp_db)
    _seed_expense_account(temp_db)
    _patch_landing(monkeypatch, tmp_path)

    header = (
        "entity,year,month,cost_center,account,sub_account,currency,amount,"
        "bonus_type,product,distribution_channel,stat_category,"
        "profit_center,sender_cost_center,assignment,functional_area,partner_functional_area_text\n"
    )
    # 9 EUR rows (quarantined) + 1 USD row = 90% error rate
    bad_rows = (
        "1001,2026,1,CC-1001,6000,001,EUR,100.00,,PRD,CHN,STAT,PC,SCC,ASSN,FA,\n" * 9
    )
    good_row = "1001,2026,2,CC-1001,6000,001,USD,200.00,,PRD,CHN,STAT,PC,SCC,ASSN,FA,\n"
    f = _write_file(tmp_path, "actuals_high_err.csv", header + bad_rows + good_row)
    iid = actuals_pipeline.run(f)
    assert iid is not None

    row = helpers.fetch_one(
        "SELECT status, promoted_rows FROM obs.ingestion_control WHERE ingestion_id = ?",
        (iid,),
    )
    assert row[0] == ps.STATUS_FAILED
    assert row[1] == 0

    count = helpers.fetch_one("SELECT COUNT(*) FROM obs.actuals WHERE is_deleted = FALSE", ())
    assert count[0] == 0


def test_idempotent_duplicate_file(
    temp_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-OI-DI-01: Running the same file twice produces the same row count."""
    _seed_cost_center(temp_db)
    _seed_expense_account(temp_db)
    _patch_landing(monkeypatch, tmp_path)

    csv = (
        "entity,year,month,cost_center,account,sub_account,currency,amount,"
        "bonus_type,product,distribution_channel,stat_category,"
        "profit_center,sender_cost_center,assignment,functional_area,partner_functional_area_text\n"
        "1001,2026,1,CC-1001,6000,001,USD,100.00,,PRD,CHN,STAT,PC,SCC,ASSN,FA,\n"
    )
    import shutil

    src = tmp_path / "actuals_idem.csv"
    src.write_text(csv)

    iid1 = actuals_pipeline.run(str(src))
    assert iid1 is not None

    # File was archived; write it again with same content
    src.write_text(csv)
    iid2 = actuals_pipeline.run(str(src))
    # Second run should be skipped (duplicate detected via SHA-256)
    assert iid2 is None

    count = helpers.fetch_one("SELECT COUNT(*) FROM obs.actuals WHERE is_deleted = FALSE", ())
    assert count[0] == 1


def test_alert_failure_is_non_fatal(
    temp_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-OI-DI-04: Alert failures must not roll back the pipeline (RULE 7)."""
    _seed_cost_center(temp_db)
    _seed_expense_account(temp_db)
    _patch_landing(monkeypatch, tmp_path)

    def _fail(*args: object, **kwargs: object) -> None:
        raise RuntimeError("intentional alert failure")

    monkeypatch.setattr(
        "backend.pipeline.pipeline_alert_service.send_pipeline_alert", _fail
    )

    csv = (
        "entity,year,month,cost_center,account,sub_account,currency,amount,"
        "bonus_type,product,distribution_channel,stat_category,"
        "profit_center,sender_cost_center,assignment,functional_area,partner_functional_area_text\n"
        "1001,2026,1,CC-1001,6000,001,USD,100.00,,PRD,CHN,STAT,PC,SCC,ASSN,FA,\n"
    )
    f = _write_file(tmp_path, "actuals_alert.csv", csv)
    iid = actuals_pipeline.run(f)
    assert iid is not None

    row = helpers.fetch_one(
        "SELECT status FROM obs.ingestion_control WHERE ingestion_id = ?",
        (iid,),
    )
    assert row[0] == ps.STATUS_COMPLETED


def test_ingestion_completed_audit_event(
    temp_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-OI-DI-08: INGESTION_COMPLETED audit event written in same transaction (RULE 6)."""
    _seed_cost_center(temp_db)
    _seed_expense_account(temp_db)
    _patch_landing(monkeypatch, tmp_path)

    csv = (
        "entity,year,month,cost_center,account,sub_account,currency,amount,"
        "bonus_type,product,distribution_channel,stat_category,"
        "profit_center,sender_cost_center,assignment,functional_area,partner_functional_area_text\n"
        "1001,2026,1,CC-1001,6000,001,USD,100.00,,PRD,CHN,STAT,PC,SCC,ASSN,FA,\n"
    )
    f = _write_file(tmp_path, "actuals_audit.csv", csv)
    iid = actuals_pipeline.run(f)
    assert iid is not None

    row = helpers.fetch_one(
        "SELECT event_type, entity_id FROM obs.audit_log "
        "WHERE entity_id = ? AND event_type = ?",
        (iid, AuditEvent.INGESTION_COMPLETED),
    )
    assert row is not None
    assert row[0] == AuditEvent.INGESTION_COMPLETED


def test_ingestion_rejected_audit_event(
    temp_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """INGESTION_REJECTED audit event on >80% error rate."""
    _seed_cost_center(temp_db)
    _seed_expense_account(temp_db)
    _patch_landing(monkeypatch, tmp_path)

    header = (
        "entity,year,month,cost_center,account,sub_account,currency,amount,"
        "bonus_type,product,distribution_channel,stat_category,"
        "profit_center,sender_cost_center,assignment,functional_area,partner_functional_area_text\n"
    )
    bad_rows = (
        "1001,2026,1,CC-1001,6000,001,EUR,100.00,,PRD,CHN,STAT,PC,SCC,ASSN,FA,\n" * 9
    )
    good_row = "1001,2026,2,CC-1001,6000,001,USD,200.00,,PRD,CHN,STAT,PC,SCC,ASSN,FA,\n"
    f = _write_file(tmp_path, "actuals_rej_audit.csv", header + bad_rows + good_row)
    iid = actuals_pipeline.run(f)
    assert iid is not None

    row = helpers.fetch_one(
        "SELECT event_type FROM obs.audit_log WHERE entity_id = ? AND event_type = ?",
        (iid, AuditEvent.INGESTION_REJECTED),
    )
    assert row is not None


def test_audit_write_failure_rolls_back_promotion(
    temp_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Check 5 (RULE 6): if audit_write() raises inside the transaction, the
    promoted actuals row must also be absent (atomic rollback)."""
    from backend.app.services import audit_service as _audit_svc
    _seed_cost_center(temp_db)
    _seed_expense_account(temp_db)
    _patch_landing(monkeypatch, tmp_path)

    original_write = _audit_svc.write_audit_event

    def _fail_on_completed(*args: object, **kwargs: object) -> None:
        if kwargs.get("event_type") == AuditEvent.INGESTION_COMPLETED:
            raise RuntimeError("forced audit failure for test")
        original_write(*args, **kwargs)

    monkeypatch.setattr(_audit_svc, "write_audit_event", _fail_on_completed)

    csv = (
        "entity,year,month,cost_center,account,sub_account,currency,amount,"
        "bonus_type,product,distribution_channel,stat_category,"
        "profit_center,sender_cost_center,assignment,functional_area,partner_functional_area_text\n"
        "1001,2026,1,CC-1001,6000,001,USD,100.00,,PRD,CHN,STAT,PC,SCC,ASSN,FA,\n"
    )
    f = _write_file(tmp_path, "actuals_audit_fail.csv", csv)

    with pytest.raises(RuntimeError, match="forced audit failure"):
        actuals_pipeline.run(f)

    # Promotion was rolled back — no actuals row should be present
    count = helpers.fetch_one("SELECT COUNT(*) FROM obs.actuals WHERE is_deleted = FALSE", ())
    assert count[0] == 0
