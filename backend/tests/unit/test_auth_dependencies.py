"""Unit tests for the 7-step authorization dependencies (Security Spec v1.4 §9.1).

Covers both the LOCAL_AUTH_BYPASS path and the cloud token path (token validation
mocked), the capability gates, and the cost-center grant-resolution framework
(Steps 5–6) used by later phases. Maps to AC-AUTH, AC-CAP, AC-GRANT, AC-CONF.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from backend.app.auth import dependencies as deps
from backend.app.auth.dependencies import EffectiveGrant
from backend.app.auth.dependencies import build_cost_center_scope_predicate
from backend.app.auth.dependencies import get_current_user
from backend.app.auth.dependencies import require_administrator
from backend.app.auth.dependencies import require_capability
from backend.app.auth.dependencies import resolve_effective_grant
from backend.app.auth.dependencies import strip_confidential_fields
from backend.app.config import settings
from backend.app.db import engine
from backend.app.db import helpers


def _request(headers: dict[str, str] | None = None) -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    scope = {"type": "http", "headers": raw, "client": ("10.0.0.1", 5555)}
    return Request(scope)


def _insert(sql: str, params: tuple[object, ...]) -> None:
    conn = engine.connect()
    try:
        helpers.exec_write(conn, sql, params)
        conn.commit()
    finally:
        conn.close()


def _seed_membership(cost_center_code: str, levels: tuple[str, ...]) -> None:
    level_cols = ["level_1_code", "level_2_code", "level_3_code",
                  "level_4_code", "level_5_code", "level_6_code"]
    level_values = list(levels) + [None] * (6 - len(levels))
    params = (
        "m-" + cost_center_code, "H1", cost_center_code, cost_center_code,
        *level_values, len(levels), True, "seed", "2026-01-01", "2026-01-01",
    )
    _insert(
        "INSERT INTO obs.cost_center_hierarchy_memberships "
        "(membership_id, hierarchy_id, cost_center_code, cost_center_name, "
        + ", ".join(level_cols)
        + ", max_depth, is_active, last_ingestion_id, created_at, updated_at) "
        "VALUES (" + ", ".join(["?"] * len(params)) + ")",
        params,
    )


# --- Step 1: bearer extraction / identity resolution ----------------------------
def test_bearer_token_valid() -> None:
    assert deps._bearer_token(_request({"authorization": "Bearer xyz"})) == "xyz"


def test_bearer_token_missing_raises() -> None:
    from backend.app.auth.token import InvalidTokenError

    with pytest.raises(InvalidTokenError):
        deps._bearer_token(_request({}))


def test_bypass_identity_requires_user_id(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.app.auth.token import InvalidTokenError

    monkeypatch.setattr(settings, "local_auth_bypass", True)
    monkeypatch.setattr(settings, "local_auth_user_id", "")
    with pytest.raises(InvalidTokenError):
        deps._resolve_caller_identity(_request({}))


def test_cloud_identity_uses_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "local_auth_bypass", False)
    monkeypatch.setattr(
        deps, "validate_access_token",
        lambda _t: {"oid": "o-1", "email": "e@example.com", "name": "N"},
    )
    oid, email, name = deps._resolve_caller_identity(_request({"authorization": "Bearer t"}))
    assert (oid, email, name) == ("o-1", "e@example.com", "N")


def test_cloud_identity_without_oid_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.app.auth.token import InvalidTokenError

    monkeypatch.setattr(settings, "local_auth_bypass", False)
    monkeypatch.setattr(deps, "validate_access_token", lambda _t: {"name": "N"})
    with pytest.raises(InvalidTokenError):
        deps._resolve_caller_identity(_request({"authorization": "Bearer t"}))


# --- Step 2: user resolution / active check (AC-AUTH-03/04) ----------------------
def test_get_current_user_active_admin(temp_db: Path, act_as: Callable[..., None]) -> None:
    act_as("dev-admin-001")
    user = get_current_user(_request({}))
    assert user.user_id == "dev-admin-001"
    assert user.is_administrator is True


def test_invalid_token_is_401(temp_db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "local_auth_bypass", False)
    with pytest.raises(HTTPException) as exc:
        get_current_user(_request({}))  # no bearer token
    assert exc.value.status_code == 401


def test_unknown_user_is_403_and_audited(temp_db: Path, act_as: Callable[..., None]) -> None:
    act_as("ghost-user")
    with pytest.raises(HTTPException) as exc:
        get_current_user(_request({}))
    assert exc.value.status_code == 403
    rows = helpers.fetch_all(
        "SELECT count(*) FROM obs.audit_log WHERE event_type = 'USER_LOGIN_DENIED'"
    )
    assert rows[0][0] == 1


def test_inactive_user_is_403(temp_db: Path, act_as: Callable[..., None]) -> None:
    _insert(
        "INSERT INTO obs.users (user_id, display_name, email, is_active, "
        "is_administrator, is_system_modeler, is_report_developer, "
        "is_finance_reviewer, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
        ("inactive-1", "Inactive", "i@example.com", False, False, False, False, False, "2026-01-01"),
    )
    act_as("inactive-1")
    with pytest.raises(HTTPException) as exc:
        get_current_user(_request({}))
    assert exc.value.status_code == 403


# --- Steps 3–4: administrator and capability gates (AC-CAP) ----------------------
def _user(
    *,
    is_administrator: bool = False,
    is_system_modeler: bool = False,
    is_report_developer: bool = False,
    is_finance_reviewer: bool = False,
) -> deps.CurrentUser:
    return deps.CurrentUser(
        user_id="u",
        display_name="U",
        email="u@example.com",
        is_active=True,
        is_administrator=is_administrator,
        is_system_modeler=is_system_modeler,
        is_report_developer=is_report_developer,
        is_finance_reviewer=is_finance_reviewer,
    )


def test_require_administrator_allows_admin() -> None:
    assert require_administrator(_user(is_administrator=True)).is_administrator


def test_require_administrator_blocks_non_admin() -> None:
    with pytest.raises(HTTPException) as exc:
        require_administrator(_user())
    assert exc.value.status_code == 403


def test_require_capability_allows_flag_holder() -> None:
    dep = require_capability("is_finance_reviewer")
    assert dep(_user(is_finance_reviewer=True)).is_finance_reviewer


def test_require_capability_allows_administrator() -> None:
    dep = require_capability("is_finance_reviewer")
    assert dep(_user(is_administrator=True)).is_administrator


def test_require_capability_blocks_others() -> None:
    dep = require_capability("is_finance_reviewer")
    with pytest.raises(HTTPException) as exc:
        dep(_user())
    assert exc.value.status_code == 403


# --- Steps 5–6: grant resolution framework (AC-GRANT / AC-CONF) ------------------
def test_individual_grant_resolves(temp_db: Path) -> None:
    _insert(
        "INSERT INTO obs.user_cost_center_grants (user_id, cost_center_id, "
        "standard_grant, confidential_grant, granted_by, granted_at) VALUES (?,?,?,?,?,?)",
        ("u1", "CC-1", "ALL_WRITE", "ALL_READ", "admin", "2026-01-01"),
    )
    grant = resolve_effective_grant("u1", "CC-1")
    assert grant is not None and grant.can_write and grant.can_read_confidential


def test_no_grant_returns_none(temp_db: Path) -> None:
    assert resolve_effective_grant("u1", "CC-NOPE") is None  # AC-GRANT-01


def test_rollup_grant_resolves(temp_db: Path) -> None:
    _seed_membership("CC-2", ("ROOT", "DIVISION"))  # AC-GRANT-04/05
    _insert(
        "INSERT INTO obs.user_rollup_grants (user_id, rollup_id, "
        "default_standard_grant, default_confidential_grant, granted_by, granted_at) "
        "VALUES (?,?,?,?,?,?)",
        ("u1", "DIVISION", "ALL_READ", "ALL_NONE", "admin", "2026-01-01"),
    )
    grant = resolve_effective_grant("u1", "CC-2")
    assert grant is not None and grant.can_read and not grant.can_write


def test_rollup_override_takes_precedence(temp_db: Path) -> None:
    _seed_membership("CC-3", ("ROOT", "DIVISION"))  # AC-GRANT-06
    _insert(
        "INSERT INTO obs.user_rollup_grants (user_id, rollup_id, "
        "default_standard_grant, default_confidential_grant, granted_by, granted_at) "
        "VALUES (?,?,?,?,?,?)",
        ("u1", "DIVISION", "ALL_READ", "ALL_NONE", "admin", "2026-01-01"),
    )
    _insert(
        "INSERT INTO obs.user_rollup_overrides (user_id, rollup_id, cost_center_id, "
        "standard_grant, confidential_grant, set_by, set_at) VALUES (?,?,?,?,?,?,?)",
        ("u1", "DIVISION", "CC-3", "ALL_WRITE", "ALL_WRITE", "admin", "2026-01-01"),
    )
    grant = resolve_effective_grant("u1", "CC-3")
    assert grant is not None and grant.can_write and grant.can_write_confidential


def test_membership_without_any_rollup_grant_returns_none(temp_db: Path) -> None:
    _seed_membership("CC-6", ("ROOT", "DIVISION"))
    assert resolve_effective_grant("u1", "CC-6") is None  # membership exists, no grant


def test_rollup_grant_on_shallower_level_resolves(temp_db: Path) -> None:
    # Deepest level (DIVISION) has no grant → fall through to ROOT, which does.
    _seed_membership("CC-7", ("ROOT", "DIVISION"))
    _insert(
        "INSERT INTO obs.user_rollup_grants (user_id, rollup_id, "
        "default_standard_grant, default_confidential_grant, granted_by, granted_at) "
        "VALUES (?,?,?,?,?,?)",
        ("u1", "ROOT", "ALL_READ", "ALL_NONE", "admin", "2026-01-01"),
    )
    grant = resolve_effective_grant("u1", "CC-7")
    assert grant is not None and grant.can_read


def test_individual_grant_beats_rollup(temp_db: Path) -> None:
    _seed_membership("CC-4", ("ROOT", "DIVISION"))  # AC-GRANT-07
    _insert(
        "INSERT INTO obs.user_rollup_grants (user_id, rollup_id, "
        "default_standard_grant, default_confidential_grant, granted_by, granted_at) "
        "VALUES (?,?,?,?,?,?)",
        ("u1", "DIVISION", "ALL_READ", "ALL_NONE", "admin", "2026-01-01"),
    )
    _insert(
        "INSERT INTO obs.user_cost_center_grants (user_id, cost_center_id, "
        "standard_grant, confidential_grant, granted_by, granted_at) VALUES (?,?,?,?,?,?)",
        ("u1", "CC-4", "ALL_WRITE", "ALL_WRITE", "admin", "2026-01-01"),
    )
    grant = resolve_effective_grant("u1", "CC-4")
    assert grant is not None and grant.can_write


def test_scope_predicate_lists_readable_cost_centers(temp_db: Path) -> None:
    _insert(
        "INSERT INTO obs.user_cost_center_grants (user_id, cost_center_id, "
        "standard_grant, confidential_grant, granted_by, granted_at) VALUES (?,?,?,?,?,?)",
        ("u1", "CC-1", "ALL_READ", "ALL_NONE", "admin", "2026-01-01"),
    )
    sql, params = build_cost_center_scope_predicate("u1")  # AC-GRANT-08
    assert "cost_center_id IN" in sql
    assert params == ["CC-1"]


def test_scope_predicate_empty_is_false(temp_db: Path) -> None:
    sql, params = build_cost_center_scope_predicate("nobody")
    assert sql == "1 = 0" and params == []


def test_scope_predicate_includes_rollup_members(temp_db: Path) -> None:
    _seed_membership("CC-5", ("ROOT", "DIVISION"))
    _insert(
        "INSERT INTO obs.user_rollup_grants (user_id, rollup_id, "
        "default_standard_grant, default_confidential_grant, granted_by, granted_at) "
        "VALUES (?,?,?,?,?,?)",
        ("u1", "DIVISION", "ALL_READ", "ALL_NONE", "admin", "2026-01-01"),
    )
    sql, params = build_cost_center_scope_predicate("u1")
    assert "CC-5" in params


def test_strip_confidential_for_all_none() -> None:
    # All raw obs.positions confidential columns AND derived comp fields are stripped.
    payload = {
        "position_id": "p1",
        "base_salary": 100000,
        "salary_adjustment": 5000,
        "aipeip_bonus": 12000,
        "other_bonus": 3000,
        "burden_rate": 0.25,
        "burden_amount": 25000,
        "total_compensation": 145000,
    }
    stripped = strip_confidential_fields(payload, EffectiveGrant("ALL_READ", "ALL_NONE"))
    for field in ("base_salary", "salary_adjustment", "aipeip_bonus", "other_bonus",
                  "burden_rate", "burden_amount", "total_compensation"):
        assert field not in stripped  # AC-CONF-01 — absent, not null
    assert stripped["position_id"] == "p1"


def test_strip_confidential_kept_for_all_read() -> None:
    payload = {"position_id": "p1", "base_salary": 100000}
    kept = strip_confidential_fields(payload, EffectiveGrant("ALL_READ", "ALL_READ"))
    assert kept["base_salary"] == 100000  # AC-CONF-03


def test_strip_confidential_for_no_grant() -> None:
    payload = {"position_id": "p1", "base_salary": 100000}
    stripped = strip_confidential_fields(payload, None)
    assert "base_salary" not in stripped  # AC-CONF-04
