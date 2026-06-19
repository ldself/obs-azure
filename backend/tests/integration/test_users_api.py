"""Integration tests for the Phase 1 user/grant API (Build Plan v1.2 §4.1.3).

Each test exercises the full ASGI request/response cycle against a freshly
bootstrapped + seeded local DuckDB (Local Dev Spec v1.1 §10.2), authenticating
through LOCAL_AUTH_BYPASS as a seeded user. Every mutation is verified by
querying obs.audit_log (phase-gate requirement; AC-AUDIT). 403-not-404 and the
error-code standards (§9.2) are asserted directly.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.db import helpers


def _audit_count(event_type: str) -> int:
    row = helpers.fetch_one(
        "SELECT count(*) FROM obs.audit_log WHERE event_type = ?", (event_type,)
    )
    return int(row[0]) if row else 0


# --- GET /me + USER_LOGIN audit (AC-AUDIT-04) -----------------------------------
def test_me_returns_profile_and_audits_login(
    temp_db: Path, act_as: Callable[..., None], client: TestClient
) -> None:
    act_as("dev-admin-001")
    resp = client.get("/api/v1/me")
    assert resp.status_code == 200
    assert resp.json()["is_administrator"] is True
    assert _audit_count("USER_LOGIN") == 1


# --- AC-AUTH-03: valid identity, no/inactive registry record → 403 --------------
def test_unknown_user_forbidden(
    temp_db: Path, act_as: Callable[..., None], client: TestClient
) -> None:
    act_as("ghost")
    assert client.get("/api/v1/me").status_code == 403


# --- GET /users (Administrator only) --------------------------------------------
def test_list_users_admin_only(
    temp_db: Path, act_as: Callable[..., None], client: TestClient
) -> None:
    act_as("dev-admin-001")
    resp = client.get("/api/v1/users")
    assert resp.status_code == 200
    assert len(resp.json()) == 6  # the six seeded §6.3 users


def test_list_users_forbidden_for_non_admin(
    temp_db: Path, act_as: Callable[..., None], client: TestClient
) -> None:
    act_as("dev-readonly-001")
    # Resource exists; caller is unauthorized → 403, never 404 (RULE 5).
    assert client.get("/api/v1/users").status_code == 403


# --- POST /users: provision + audit + 409 conflict + 422 validation -------------
def test_create_user_provisions_and_audits(
    temp_db: Path, act_as: Callable[..., None], client: TestClient
) -> None:
    act_as("dev-admin-001")
    resp = client.post(
        "/api/v1/users",
        json={"user_id": "new-1", "display_name": "New User", "email": "new@example.com"},
    )
    assert resp.status_code == 201
    assert resp.json()["is_active"] is False  # flags default FALSE (§6.2)
    assert _audit_count("USER_PROVISIONED") == 1


def test_create_duplicate_user_conflicts(
    temp_db: Path, act_as: Callable[..., None], client: TestClient
) -> None:
    act_as("dev-admin-001")
    body = {"user_id": "dev-admin-001", "display_name": "Dup", "email": "dup@example.com"}
    assert client.post("/api/v1/users", json=body).status_code == 409


def test_create_user_validation_error(
    temp_db: Path, act_as: Callable[..., None], client: TestClient
) -> None:
    act_as("dev-admin-001")
    # Missing display_name → 422 validation error (§9.2).
    resp = client.post("/api/v1/users", json={"user_id": "x", "email": "x@example.com"})
    assert resp.status_code == 422


def test_create_user_forbidden_for_non_admin(
    temp_db: Path, act_as: Callable[..., None], client: TestClient
) -> None:
    act_as("dev-modeler-001")
    resp = client.post(
        "/api/v1/users",
        json={"user_id": "z", "display_name": "Z", "email": "z@example.com"},
    )
    assert resp.status_code == 403


# --- PATCH /users/{id}: per-field audit events ----------------------------------
def test_patch_emits_capability_and_login_and_finance_events(
    temp_db: Path, act_as: Callable[..., None], client: TestClient
) -> None:
    act_as("dev-admin-001")
    client.post(
        "/api/v1/users",
        json={"user_id": "p-1", "display_name": "P", "email": "p@example.com"},
    )
    resp = client.patch(
        "/api/v1/users/p-1",
        json={"is_active": True, "is_system_modeler": True, "is_finance_reviewer": True},
    )
    assert resp.status_code == 200
    assert _audit_count("LOGIN_PERMISSION_GRANTED") == 1
    assert _audit_count("CAPABILITY_FLAG_CHANGED") == 1
    assert _audit_count("FINANCE_REVIEWER_FLAG_SET") == 1


def test_patch_missing_user_404(
    temp_db: Path, act_as: Callable[..., None], client: TestClient
) -> None:
    act_as("dev-admin-001")
    # Genuinely non-existent resource that an admin could access if it existed → 404.
    assert client.patch("/api/v1/users/nope", json={"is_active": True}).status_code == 404


# --- Last-active-Administrator guard (AC-CAP-08) --------------------------------
def test_cannot_deactivate_last_active_administrator(
    temp_db: Path, act_as: Callable[..., None], client: TestClient
) -> None:
    act_as("dev-admin-001")  # the only seeded administrator
    assert client.patch("/api/v1/users/dev-admin-001", json={"is_active": False}).status_code == 409
    assert client.patch(
        "/api/v1/users/dev-admin-001", json={"is_administrator": False}
    ).status_code == 409


def test_can_demote_admin_when_another_exists(
    temp_db: Path, act_as: Callable[..., None], client: TestClient
) -> None:
    act_as("dev-admin-001")
    client.post(
        "/api/v1/users",
        json={"user_id": "admin-2", "display_name": "Admin Two",
              "email": "a2@example.com", "is_active": True, "is_administrator": True},
    )
    # With two active admins, demoting one is allowed.
    assert client.patch(
        "/api/v1/users/admin-2", json={"is_administrator": False}
    ).status_code == 200


# --- Grants: add / modify / remove with audit ----------------------------------
def test_grant_lifecycle_and_audit(
    temp_db: Path, act_as: Callable[..., None], client: TestClient
) -> None:
    act_as("dev-admin-001")
    client.post(
        "/api/v1/users",
        json={"user_id": "g-1", "display_name": "G", "email": "g@example.com"},
    )
    add = client.post(
        "/api/v1/users/g-1/grants",
        json={"cost_center_id": "CC-1001", "standard_grant": "ALL_WRITE",
              "confidential_grant": "ALL_READ"},
    )
    assert add.status_code == 201
    assert _audit_count("COST_CENTER_GRANT_ADDED") == 1

    mod = client.post(
        "/api/v1/users/g-1/grants",
        json={"cost_center_id": "CC-1001", "standard_grant": "ALL_READ",
              "confidential_grant": "ALL_NONE"},
    )
    assert mod.status_code == 201
    assert _audit_count("COST_CENTER_GRANT_MODIFIED") == 1

    listed = client.get("/api/v1/users/g-1/grants")
    assert listed.status_code == 200
    assert listed.json()[0]["standard_grant"] == "ALL_READ"

    deleted = client.delete("/api/v1/users/g-1/grants/CC-1001")
    assert deleted.status_code == 204
    assert _audit_count("COST_CENTER_GRANT_REMOVED") == 1


def test_delete_missing_grant_404(
    temp_db: Path, act_as: Callable[..., None], client: TestClient
) -> None:
    act_as("dev-admin-001")
    assert client.delete("/api/v1/users/dev-admin-001/grants/CC-NONE").status_code == 404


def test_grant_endpoints_forbidden_for_non_admin(
    temp_db: Path, act_as: Callable[..., None], client: TestClient
) -> None:
    act_as("dev-costcenter-001")
    resp = client.post(
        "/api/v1/users/dev-costcenter-001/grants",
        json={"cost_center_id": "CC-1001", "standard_grant": "ALL_READ"},
    )
    assert resp.status_code == 403


def test_user_can_read_own_grants_but_not_others(
    temp_db: Path, act_as: Callable[..., None], client: TestClient
) -> None:
    act_as("dev-readonly-001")
    assert client.get("/api/v1/users/dev-readonly-001/grants").status_code == 200
    assert client.get("/api/v1/users/dev-admin-001/grants").status_code == 403


# --- Rollup grant ---------------------------------------------------------------
def test_add_rollup_grant_audits(
    temp_db: Path, act_as: Callable[..., None], client: TestClient
) -> None:
    act_as("dev-admin-001")
    client.post(
        "/api/v1/users",
        json={"user_id": "r-1", "display_name": "R", "email": "r@example.com"},
    )
    resp = client.post(
        "/api/v1/users/r-1/rollup-grants",
        json={"rollup_id": "DIVISION-1", "default_standard_grant": "ALL_READ"},
    )
    assert resp.status_code == 201
    assert _audit_count("COST_CENTER_GRANT_ADDED") == 1

    # Re-adding the same rollup updates it and audits a MODIFIED event.
    modified = client.post(
        "/api/v1/users/r-1/rollup-grants",
        json={"rollup_id": "DIVISION-1", "default_standard_grant": "ALL_WRITE"},
    )
    assert modified.status_code == 201
    assert _audit_count("COST_CENTER_GRANT_MODIFIED") == 1


# --- Logout audit (USER_LOGOUT) -------------------------------------------------
def test_logout_audits(
    temp_db: Path, act_as: Callable[..., None], client: TestClient
) -> None:
    act_as("dev-admin-001")
    resp = client.post("/api/v1/auth/logout")
    assert resp.status_code == 200
    assert _audit_count("USER_LOGOUT") == 1
