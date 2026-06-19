"""Unit tests for the Phase 0 stub API health endpoint and engine switch."""

from __future__ import annotations

from fastapi.testclient import TestClient

from backend.app.db import engine
from backend.app.main import app


client = TestClient(app)


def test_health_returns_200() -> None:
    """AC-P0-HEALTH: GET /health returns HTTP 200 (Build Sequencing Plan §4.0.4)."""
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["engine"] in ("duckdb", "postgresql")


def test_engine_default_is_duckdb() -> None:
    """Local default engine is DuckDB (Architecture Spec v3.6 §2.4)."""
    assert engine.engine_name() == "duckdb"


def test_engine_placeholder_matches_engine() -> None:
    """Parameter placeholder follows the active engine's paramstyle."""
    assert engine.placeholder() == "?"
