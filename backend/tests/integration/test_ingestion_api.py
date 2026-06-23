"""Integration tests for the ingestion monitoring API (Phase 2).

Tests run against the full FastAPI ASGI stack with a live DuckDB test database.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import datetime
from datetime import timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.config import settings
from backend.app.db import helpers
from backend.pipeline import pipeline_service as ps


def _seed_ingestion_record(status: str = ps.STATUS_COMPLETED) -> str:
    iid = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    with helpers.transaction() as conn:
        ps.create_ingestion_record(
            conn,
            ingestion_id=iid,
            file_name=f"test_{iid[:8]}.csv",
            content_hash=f"hash_{iid[:8]}",
            file_type=ps.FILE_TYPE_ACTUALS,
            source_system="ACCOUNTING",
            file_format="CSV",
            triggered_by=ps.TRIGGERED_MANUAL,
        )
        ps.update_ingestion_record(
            conn, ingestion_id=iid, status=status,
            total_rows=5, valid_rows=5, quarantined_rows=0,
            rejected_rows=0, promoted_rows=5, error_rate=0.0,
        )
    return iid


# ---------------------------------------------------------------------------
# GET /ingestion — list
# ---------------------------------------------------------------------------

def test_list_ingestion_requires_admin(
    temp_db: Path, act_as: Callable[..., None], client: TestClient
) -> None:
    """Non-admin user gets 403 on admin-only endpoint."""
    act_as("dev-costcenter-001")
    assert client.get("/api/v1/ingestion").status_code == 403


def test_list_ingestion_returns_records(
    client: object, temp_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastapi.testclient import TestClient
    from backend.app.main import app

    monkeypatch.setattr(settings, "local_auth_bypass", True)
    monkeypatch.setattr(settings, "local_auth_user_id", "dev-admin-001")
    iid = _seed_ingestion_record()

    with TestClient(app) as tc:
        resp = tc.get("/api/v1/ingestion")
    assert resp.status_code == 200
    data = resp.json()
    assert any(r["ingestion_id"] == iid for r in data)


def test_get_ingestion_404(
    client: object, temp_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastapi.testclient import TestClient
    from backend.app.main import app

    monkeypatch.setattr(settings, "local_auth_bypass", True)
    monkeypatch.setattr(settings, "local_auth_user_id", "dev-admin-001")

    with TestClient(app) as tc:
        resp = tc.get(f"/api/v1/ingestion/{uuid.uuid4()}")
    assert resp.status_code == 404


def test_get_ingestion_record(
    client: object, temp_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastapi.testclient import TestClient
    from backend.app.main import app

    monkeypatch.setattr(settings, "local_auth_bypass", True)
    monkeypatch.setattr(settings, "local_auth_user_id", "dev-admin-001")
    iid = _seed_ingestion_record()

    with TestClient(app) as tc:
        resp = tc.get(f"/api/v1/ingestion/{iid}")
    assert resp.status_code == 200
    assert resp.json()["ingestion_id"] == iid


def test_get_quarantine_returns_records(
    client: object, temp_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastapi.testclient import TestClient
    from backend.app.main import app

    monkeypatch.setattr(settings, "local_auth_bypass", True)
    monkeypatch.setattr(settings, "local_auth_user_id", "dev-admin-001")
    iid = _seed_ingestion_record(status=ps.STATUS_QUARANTINED)

    with helpers.transaction() as conn:
        ps.write_quarantine_record(
            conn,
            ingestion_id=iid,
            quarantine_table="obs.actuals_quarantine",
            quarantine_reason=ps.QR_NON_USD,
            source_row_number=1,
            row_data={"currency": "EUR"},
        )

    with TestClient(app) as tc:
        resp = tc.get(f"/api/v1/ingestion/{iid}/quarantine")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["quarantine_reason"] == ps.QR_NON_USD


# ---------------------------------------------------------------------------
# RULE 9 / OI-DI-06: write endpoints return 405
# ---------------------------------------------------------------------------

def test_cc_hierarchy_post_returns_405(
    client: object, temp_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """OI-DI-06: POST /ingestion/cost-center-hierarchy → 405."""
    from fastapi.testclient import TestClient
    from backend.app.main import app

    monkeypatch.setattr(settings, "local_auth_bypass", True)
    monkeypatch.setattr(settings, "local_auth_user_id", "dev-admin-001")

    with TestClient(app) as tc:
        resp = tc.post("/api/v1/ingestion/cost-center-hierarchy")
    assert resp.status_code == 405


def test_account_hierarchy_nodes_post_returns_405(
    client: object, temp_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RULE 9: POST /ingestion/account-hierarchy-nodes → 405."""
    from fastapi.testclient import TestClient
    from backend.app.main import app

    monkeypatch.setattr(settings, "local_auth_bypass", True)
    monkeypatch.setattr(settings, "local_auth_user_id", "dev-admin-001")

    with TestClient(app) as tc:
        resp = tc.post("/api/v1/ingestion/account-hierarchy-nodes")
    assert resp.status_code == 405


def test_expense_accounts_post_returns_405(
    client: object, temp_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RULE 9: POST /ingestion/expense-accounts → 405."""
    from fastapi.testclient import TestClient
    from backend.app.main import app

    monkeypatch.setattr(settings, "local_auth_bypass", True)
    monkeypatch.setattr(settings, "local_auth_user_id", "dev-admin-001")

    with TestClient(app) as tc:
        resp = tc.post("/api/v1/ingestion/expense-accounts")
    assert resp.status_code == 405
