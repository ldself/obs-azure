"""Shared pytest fixtures for the OBS backend test suite.

The local engine is DuckDB. Each test that needs persistence gets a freshly
bootstrapped, seeded database in a temp directory, and authenticates through the
``LOCAL_AUTH_BYPASS`` path acting as a chosen seeded user (Local Dev Spec v1.1
§6 / §10). Settings are monkeypatched on the shared ``settings`` singleton, which
every module reads at request time, so the override is seen everywhere.
"""

from __future__ import annotations

from collections.abc import Callable
from collections.abc import Iterator
from pathlib import Path

import pytest

from backend.app.config import settings
from backend.app.db import engine
from backend.app.db.bootstrap import apply_bootstrap


REPO_ROOT = Path(__file__).resolve().parents[2]
SEED_USERS = REPO_ROOT / "schema" / "seed" / "010_users.sql"


@pytest.fixture
def temp_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A fresh DuckDB database bootstrapped and seeded with the §6.3 test users."""
    db_path = tmp_path / "test.duckdb"
    monkeypatch.setattr(settings, "db_engine", "duckdb")
    monkeypatch.setattr(settings, "duckdb_path", str(db_path))
    conn = engine.connect()
    try:
        apply_bootstrap(conn)
        apply_bootstrap(conn, SEED_USERS)
        conn.commit()
    finally:
        conn.close()
    return db_path


@pytest.fixture
def act_as(monkeypatch: pytest.MonkeyPatch) -> Callable[[str], None]:
    """Authenticate subsequent requests as ``user_id`` via the bypass path."""

    def _set(user_id: str, email: str = "", name: str = "") -> None:
        monkeypatch.setattr(settings, "local_auth_bypass", True)
        monkeypatch.setattr(settings, "local_auth_user_id", user_id)
        monkeypatch.setattr(settings, "local_auth_user_email", email)
        monkeypatch.setattr(settings, "local_auth_user_name", name)

    return _set


@pytest.fixture
def client() -> Iterator["TestClient"]:  # noqa: F821 - forward ref for type only
    """A TestClient over the OBS app (full ASGI request/response cycle)."""
    from fastapi.testclient import TestClient

    from backend.app.main import app

    with TestClient(app) as test_client:
        yield test_client
