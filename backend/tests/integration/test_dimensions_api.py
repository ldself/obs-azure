"""Integration tests for the dimension read API (Phase 2).

AC coverage:
  AC-OI-DI-07: POST expense-accounts / account-hierarchy-nodes → 405
  AC-OI-DI-10: Dimension GETs accessible to any active user
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.config import settings


# ---------------------------------------------------------------------------
# Auth guard: unknown user is rejected
# ---------------------------------------------------------------------------

def test_dimension_reads_require_auth(
    temp_db: Path, act_as: Callable[..., None], client: TestClient
) -> None:
    """GET /dimensions/* with an unknown user_id → 403."""
    act_as("ghost-user-unknown")
    assert client.get("/api/v1/dimensions/expense-accounts").status_code == 403


# ---------------------------------------------------------------------------
# AC-OI-DI-10: Any active authenticated user can read dimensions
# ---------------------------------------------------------------------------

def test_dimension_reads_accessible_to_non_admin(
    temp_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-OI-DI-10: A non-admin active user can GET all dimension endpoints."""
    from backend.app.main import app

    monkeypatch.setattr(settings, "local_auth_bypass", True)
    # dev-costcenter-001 is NOT an administrator
    monkeypatch.setattr(settings, "local_auth_user_id", "dev-costcenter-001")

    endpoints = [
        "/api/v1/dimensions/cost-centers",
        "/api/v1/dimensions/cost-center-hierarchy/nodes",
        "/api/v1/dimensions/cost-center-hierarchy/memberships",
        "/api/v1/dimensions/account-hierarchy/nodes",
        "/api/v1/dimensions/account-hierarchy/memberships",
        "/api/v1/dimensions/expense-accounts",
    ]

    with TestClient(app) as tc:
        for path in endpoints:
            resp = tc.get(path)
            assert resp.status_code == 200, f"Expected 200 for {path}, got {resp.status_code}"
            assert isinstance(resp.json(), list)


# ---------------------------------------------------------------------------
# AC-OI-DI-07: RULE 9 write blocks
# ---------------------------------------------------------------------------

def test_expense_accounts_is_readonly(
    temp_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-OI-DI-07: POST, PUT, DELETE on expense-accounts → 405."""
    from backend.app.main import app

    monkeypatch.setattr(settings, "local_auth_bypass", True)
    monkeypatch.setattr(settings, "local_auth_user_id", "dev-admin-001")

    with TestClient(app) as tc:
        assert tc.post("/api/v1/dimensions/expense-accounts").status_code == 405
        assert tc.put("/api/v1/dimensions/expense-accounts").status_code == 405
        assert tc.delete("/api/v1/dimensions/expense-accounts").status_code == 405


def test_account_hierarchy_nodes_is_readonly(
    temp_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-OI-DI-07: POST, PUT, DELETE on account-hierarchy/nodes → 405."""
    from backend.app.main import app

    monkeypatch.setattr(settings, "local_auth_bypass", True)
    monkeypatch.setattr(settings, "local_auth_user_id", "dev-admin-001")

    with TestClient(app) as tc:
        assert tc.post("/api/v1/dimensions/account-hierarchy/nodes").status_code == 405
        assert tc.put("/api/v1/dimensions/account-hierarchy/nodes").status_code == 405
        assert tc.delete("/api/v1/dimensions/account-hierarchy/nodes").status_code == 405
