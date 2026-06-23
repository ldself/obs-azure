"""Unit tests for the hierarchy ingestion pipeline (Phase 2).

AC coverage:
  AC-OI-DI-01: SHA-256 dedup skips duplicate COMPLETED file
  AC-OI-DI-05: Auto re-promotion after dimension resolved
  AC-OI-DI-06: CC hierarchy re-import blocked post-seed → FAILED
  AC-OI-DI-08: INGESTION_COMPLETED audit event
"""

from __future__ import annotations

import uuid
from datetime import datetime
from datetime import timezone
from pathlib import Path

import pytest

from backend.app.db import helpers
from backend.app.services.audit_service import AuditEvent
from backend.pipeline import hierarchy_pipeline
from backend.pipeline import pipeline_service as ps


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CC_HEADER = "hierarchy_id,hierarchy_name,cost_center_code,cost_center_name,level_1_code,max_depth\n"
_CC_ROW = "H1,Main,CC-1001,Cost Center 1001,CC-1001,1\n"

_ACCT_HEADER = "hierarchy_id,hierarchy_name,account,sub_account,account_name,level_1_code,max_depth\n"
_ACCT_ROW = "HA,Accounts,6000,001,Test Account,6000,1\n"


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

def test_cc_hierarchy_promotes_nodes_and_memberships(
    temp_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_landing(monkeypatch, tmp_path)

    f = _write_file(tmp_path, "cost_center_hierarchy_2026.csv", _CC_HEADER + _CC_ROW)
    iid = hierarchy_pipeline.run(f, ps.FILE_TYPE_CC_HIERARCHY)
    assert iid is not None

    row = helpers.fetch_one(
        "SELECT status, promoted_rows FROM obs.ingestion_control WHERE ingestion_id = ?",
        (iid,),
    )
    assert row[0] == ps.STATUS_COMPLETED
    assert row[1] == 1

    node = helpers.fetch_one(
        "SELECT node_code FROM obs.cost_center_hierarchy_nodes WHERE node_code = ?",
        ("CC-1001",),
    )
    assert node is not None

    membership = helpers.fetch_one(
        "SELECT cost_center_code FROM obs.cost_center_hierarchy_memberships WHERE cost_center_code = ?",
        ("CC-1001",),
    )
    assert membership is not None


def test_cc_hierarchy_blocked_after_seed(
    temp_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-OI-DI-06: A second CC hierarchy import must fail with STATUS_FAILED."""
    _patch_landing(monkeypatch, tmp_path)

    f1 = tmp_path / "cost_center_hierarchy_seed.csv"
    f1.write_text(_CC_HEADER + _CC_ROW)
    iid1 = hierarchy_pipeline.run(str(f1), ps.FILE_TYPE_CC_HIERARCHY)
    assert iid1 is not None

    # Confirm nodes exist (seeded)
    count = helpers.fetch_one(
        "SELECT COUNT(*) FROM obs.cost_center_hierarchy_nodes WHERE is_active = TRUE", ()
    )
    assert count[0] > 0

    f2 = tmp_path / "cost_center_hierarchy_reimport.csv"
    f2.write_text(_CC_HEADER + "H1,Main,CC-1002,Cost Center 1002,CC-1002,1\n")
    iid2 = hierarchy_pipeline.run(str(f2), ps.FILE_TYPE_CC_HIERARCHY)
    assert iid2 is not None

    row = helpers.fetch_one(
        "SELECT status FROM obs.ingestion_control WHERE ingestion_id = ?",
        (iid2,),
    )
    assert row[0] == ps.STATUS_FAILED


def test_account_hierarchy_not_blocked_after_seed(
    temp_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Account hierarchy is never blocked — can be re-imported freely."""
    _patch_landing(monkeypatch, tmp_path)

    f1 = tmp_path / "account_hierarchy_v1.csv"
    f1.write_text(_ACCT_HEADER + _ACCT_ROW)
    iid1 = hierarchy_pipeline.run(str(f1), ps.FILE_TYPE_ACCT_HIERARCHY)
    assert iid1 is not None

    f2 = tmp_path / "account_hierarchy_v2.csv"
    f2.write_text(_ACCT_HEADER + "HA,Accounts,7000,001,Other Account,7000,1\n")
    iid2 = hierarchy_pipeline.run(str(f2), ps.FILE_TYPE_ACCT_HIERARCHY)
    assert iid2 is not None

    row = helpers.fetch_one(
        "SELECT status FROM obs.ingestion_control WHERE ingestion_id = ?",
        (iid2,),
    )
    assert row[0] == ps.STATUS_COMPLETED


def test_idempotent_duplicate_file(
    temp_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-OI-DI-01: Same account hierarchy file skipped on second run."""
    _patch_landing(monkeypatch, tmp_path)

    f = tmp_path / "account_hierarchy_idem.csv"
    f.write_text(_ACCT_HEADER + _ACCT_ROW)
    iid1 = hierarchy_pipeline.run(str(f), ps.FILE_TYPE_ACCT_HIERARCHY)
    assert iid1 is not None

    f.write_text(_ACCT_HEADER + _ACCT_ROW)
    iid2 = hierarchy_pipeline.run(str(f), ps.FILE_TYPE_ACCT_HIERARCHY)
    assert iid2 is None  # skipped duplicate


def test_ingestion_completed_audit_event(
    temp_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-OI-DI-08: Audit event in same transaction (RULE 6)."""
    _patch_landing(monkeypatch, tmp_path)

    f = _write_file(tmp_path, "account_hierarchy_audit.csv", _ACCT_HEADER + _ACCT_ROW)
    iid = hierarchy_pipeline.run(f, ps.FILE_TYPE_ACCT_HIERARCHY)
    assert iid is not None

    row = helpers.fetch_one(
        "SELECT event_type FROM obs.audit_log WHERE entity_id = ? AND event_type = ?",
        (iid, AuditEvent.INGESTION_COMPLETED),
    )
    assert row is not None


def test_merge_idempotency_different_file_name(
    temp_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Check 10: row-level merge() idempotency for hierarchy — same nodes/memberships
    in a differently-named file must not create duplicates (RULE 10)."""
    _patch_landing(monkeypatch, tmp_path)

    f1 = tmp_path / "account_hierarchy_a.csv"
    f1.write_text(_ACCT_HEADER + _ACCT_ROW)
    iid1 = hierarchy_pipeline.run(str(f1), ps.FILE_TYPE_ACCT_HIERARCHY)
    assert iid1 is not None

    node_count_1 = helpers.fetch_one("SELECT COUNT(*) FROM obs.account_hierarchy_nodes", ())
    assert node_count_1[0] == 1

    # Same content, different file name — SHA-256 dedup does not block
    f2 = tmp_path / "account_hierarchy_b.csv"
    f2.write_text(_ACCT_HEADER + _ACCT_ROW)
    iid2 = hierarchy_pipeline.run(str(f2), ps.FILE_TYPE_ACCT_HIERARCHY)
    assert iid2 is not None

    # merge() on (hierarchy_id, node_code) must not create a second node
    node_count_2 = helpers.fetch_one("SELECT COUNT(*) FROM obs.account_hierarchy_nodes", ())
    assert node_count_2[0] == 1


def test_sweep_repromotes_pending_quarantine_after_cc_hierarchy(
    temp_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-OI-DI-05: After CC hierarchy import, pending actuals quarantine records
    whose cost_center is now resolved are automatically re-promoted.
    """
    from backend.app.db import engine as db_engine
    _patch_landing(monkeypatch, tmp_path)

    # Seed an ingestion_control record to satisfy the FK for actuals_quarantine
    seed_iid = "seed-actuals-q-001"
    now = datetime.now(timezone.utc)
    with helpers.transaction() as conn:
        helpers.exec_write(conn,
            "INSERT INTO obs.ingestion_control "
            "(ingestion_id, file_name, content_hash, source_system, file_type, "
            " file_format, status, re_ingestion, triggered_by, started_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (seed_iid, "actuals_seed.csv", "hashQ1", "ACCOUNTING", "ACTUALS",
             "CSV", "QUARANTINED", False, "SCHEDULED", now))

        # Insert also an expense account so re-promotion can work
        helpers.exec_write(conn,
            "INSERT OR IGNORE INTO obs.expense_accounts "
            "(account, sub_account, account_name, is_active) VALUES (?, ?, ?, ?)",
            ("6000", "001", "Test Account", True))

        # Insert a PENDING actuals quarantine for cost center CC-9999 (not yet in hierarchy)
        ph = db_engine.placeholder()
        helpers.exec_write(conn,
            f"INSERT INTO obs.actuals_quarantine "
            f"(quarantine_id, ingestion_id, quarantine_reason, quarantine_status, "
            f" source_row_number, entity, year, month, cost_center, account, sub_account, "
            f" currency, amount, product, profit_center, sender_cost_center) "
            f"VALUES ({', '.join([ph] * 16)})",
            (str(uuid.uuid4()), seed_iid, ps.QR_MISSING_COST_CENTER, "PENDING",
             1, 1001, 2026, 1, "CC-9999", "6000", "001", "USD", 100.0,
             "PRD", "PC", "SCC"))

    # Now ingest CC hierarchy that includes CC-9999
    f = _write_file(
        tmp_path,
        "cost_center_hierarchy_resolve.csv",
        _CC_HEADER + "H1,Main,CC-9999,Cost Center 9999,CC-9999,1\n",
    )
    iid = hierarchy_pipeline.run(f, ps.FILE_TYPE_CC_HIERARCHY)
    assert iid is not None

    # The sweep should have resolved the pending quarantine record
    q_row = helpers.fetch_one(
        "SELECT quarantine_status FROM obs.actuals_quarantine WHERE quarantine_reason = ? AND ingestion_id = ?",
        (ps.QR_MISSING_COST_CENTER, seed_iid),
    )
    assert q_row is not None
    assert q_row[0] == "RESOLVED"

    # QUARANTINE_REPROMOTED audit event written
    audit_row = helpers.fetch_one(
        "SELECT event_type FROM obs.audit_log WHERE event_type = ?",
        (AuditEvent.QUARANTINE_REPROMOTED,),
    )
    assert audit_row is not None
