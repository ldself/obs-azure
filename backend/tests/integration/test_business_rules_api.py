"""Integration tests for the Phase 3 business rules configuration API (Build Plan v1.2 §4.3).

Each test exercises the full ASGI request/response cycle against a freshly
bootstrapped DuckDB. Test data is inserted via helpers.execute() within the test
body (after temp_db bootstraps the schema and seeds the §6.3 users).

AC coverage:
  AC-BR-01: any authenticated user can read all four business-rules tables
  AC-BR-02: merit rate write requires System Modeler or Administrator
  AC-BR-03: merit rate upsert writes MERIT_INCREASE_RATE_UPDATED audit event
  AC-BR-04: burden rate write requires System Modeler or Administrator
  AC-BR-05: burden rate upsert writes COMPENSATION_BURDEN_RATE_UPDATED audit event
  AC-BR-06: component mapping write requires System Modeler or Administrator
  AC-BR-07: component mapping update writes COMPENSATION_MAPPING_UPDATED audit event
  AC-BR-08: component mapping update calls recalculation stub
  AC-BR-09: overhead rate write requires System Modeler or Administrator
  AC-BR-10: overhead rate mutations write correct audit events
  AC-FC-01: any authenticated user can read fiscal calendar
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.app.db import helpers
from backend.app.services import calculation_service


def _audit_count(event_type: str) -> int:
    row = helpers.fetch_one(
        "SELECT count(*) FROM obs.audit_log WHERE event_type = ?", (event_type,)
    )
    return int(row[0]) if row else 0


def _seed_merit_rate(fiscal_year: int = 2026, rate_pct: str = "3.0000") -> None:
    helpers.execute(
        "INSERT INTO obs.merit_increase_rates "
        "(rate_id, fiscal_year, rate_pct, effective_date, updated_at, updated_by) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (f"merit-{fiscal_year}-test", fiscal_year, Decimal(rate_pct),
         "2026-03-01", "2026-01-01 00:00:00", "seed"),
    )


def _seed_burden_rate(fiscal_year: int = 2026) -> None:
    helpers.execute(
        "INSERT INTO obs.compensation_burden_rates "
        "(rate_id, fiscal_year, fica_rate_pct, fica_wage_cap, medicare_rate_pct, "
        "state_income_tax_rate_pct, federal_income_tax_rate_pct, "
        "suta_rate_pct, suta_wage_cap, futa_rate_pct, futa_wage_cap, "
        "other_benefits_rate_pct, updated_at, updated_by) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (f"burden-{fiscal_year}-test", fiscal_year,
         Decimal("6.2000"), Decimal("168600.00"), Decimal("1.4500"),
         Decimal("0.0000"), Decimal("0.0000"),
         Decimal("2.7000"), Decimal("7000.00"),
         Decimal("0.6000"), Decimal("7000.00"),
         Decimal("5.0000"), "2026-01-01 00:00:00", "seed"),
    )


def _seed_component_mapping(component_code: str = "salary", account_code: str = "5000") -> None:
    helpers.execute(
        "INSERT INTO obs.compensation_component_mappings "
        "(mapping_id, component_code, account_code, updated_at, updated_by) "
        "VALUES (?, ?, ?, ?, ?)",
        (f"cm-{component_code}-test", component_code, account_code,
         "2026-01-01 00:00:00", "seed"),
    )


def _seed_overhead_rate(rate_id: str = "oar-test-001") -> None:
    helpers.execute(
        "INSERT INTO obs.overhead_allocation_rates "
        "(rate_id, account_code, geography_code, amount_per_employee, "
        "rate_period, fiscal_year, is_active, created_at, updated_at, updated_by) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (rate_id, "6100", None, Decimal("450.00"), "Monthly", 2026, True,
         "2026-01-01 00:00:00", "2026-01-01 00:00:00", "seed"),
    )


def _seed_fiscal_period(fiscal_year: int = 2026, period_number: int = 6) -> None:
    helpers.execute(
        "INSERT INTO obs.fiscal_calendar "
        "(calendar_id, fiscal_year, period_number, period_name, "
        "start_date, end_date, is_current) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (f"fc-{fiscal_year}-{period_number:02d}-test", fiscal_year, period_number,
         f"FY{fiscal_year}-P{period_number:02d}", "2026-06-01", "2026-06-30", True),
    )


# =====================================================================
# AC-BR-01: reads accessible to any authenticated user
# =====================================================================

def test_reads_accessible_to_any_authenticated_user(
    temp_db: Path, act_as: Callable[..., None], client: TestClient
) -> None:
    """AC-BR-01: All business-rules GET endpoints return 200 for any active user."""
    _seed_merit_rate()
    _seed_burden_rate()
    _seed_component_mapping()
    _seed_overhead_rate()

    # dev-readonly-001 has no capability flags and no is_administrator
    act_as("dev-readonly-001")

    endpoints = [
        "/api/v1/business-rules/merit-increase-rates",
        "/api/v1/business-rules/merit-increase-rates/2026",
        "/api/v1/business-rules/compensation-burden-rates",
        "/api/v1/business-rules/compensation-burden-rates/2026",
        "/api/v1/business-rules/compensation-component-mappings",
        "/api/v1/business-rules/compensation-component-mappings/salary",
        "/api/v1/business-rules/overhead-allocation-rates",
        "/api/v1/business-rules/overhead-allocation-rates/oar-test-001",
    ]
    for path in endpoints:
        resp = client.get(path)
        assert resp.status_code == 200, f"Expected 200 at {path}, got {resp.status_code}"


# =====================================================================
# AC-BR-02 + AC-BR-03: merit increase rates
# =====================================================================

def test_merit_rate_write_requires_system_modeler(
    temp_db: Path, act_as: Callable[..., None], client: TestClient
) -> None:
    """AC-BR-02: Non-modeler non-admin receives 403 on PUT merit rate."""
    act_as("dev-readonly-001")
    resp = client.put(
        "/api/v1/business-rules/merit-increase-rates/2027",
        json={"rate_pct": "3.5000", "effective_date": "2027-03-01"},
    )
    assert resp.status_code == 403


def test_merit_rate_upsert_creates_new_row(
    temp_db: Path, act_as: Callable[..., None], client: TestClient
) -> None:
    """Merit rate for a new fiscal year is created on first PUT."""
    act_as("dev-modeler-001")
    resp = client.put(
        "/api/v1/business-rules/merit-increase-rates/2027",
        json={"rate_pct": "3.5000", "effective_date": "2027-03-01"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["fiscal_year"] == 2027
    assert Decimal(body["rate_pct"]) == Decimal("3.5000")


def test_merit_rate_upsert_updates_existing_row(
    temp_db: Path, act_as: Callable[..., None], client: TestClient
) -> None:
    """Upserting an existing fiscal year updates rate_pct (idempotent)."""
    _seed_merit_rate(fiscal_year=2026, rate_pct="3.0000")
    act_as("dev-modeler-001")

    resp1 = client.put(
        "/api/v1/business-rules/merit-increase-rates/2026",
        json={"rate_pct": "3.2500", "effective_date": "2026-03-01"},
    )
    assert resp1.status_code == 200
    assert Decimal(resp1.json()["rate_pct"]) == Decimal("3.2500")

    # Second call same data — idempotent (same result)
    resp2 = client.put(
        "/api/v1/business-rules/merit-increase-rates/2026",
        json={"rate_pct": "3.2500", "effective_date": "2026-03-01"},
    )
    assert resp2.status_code == 200
    assert Decimal(resp2.json()["rate_pct"]) == Decimal("3.2500")


def test_merit_rate_upsert_writes_audit_event(
    temp_db: Path, act_as: Callable[..., None], client: TestClient
) -> None:
    """AC-BR-03: merit rate upsert writes MERIT_INCREASE_RATE_UPDATED to obs.audit_log."""
    act_as("dev-modeler-001")
    before = _audit_count("MERIT_INCREASE_RATE_UPDATED")
    client.put(
        "/api/v1/business-rules/merit-increase-rates/2028",
        json={"rate_pct": "4.0000", "effective_date": "2028-03-01"},
    )
    assert _audit_count("MERIT_INCREASE_RATE_UPDATED") == before + 1


def test_merit_rate_404_for_unknown_fiscal_year(
    temp_db: Path, act_as: Callable[..., None], client: TestClient
) -> None:
    act_as("dev-readonly-001")
    resp = client.get("/api/v1/business-rules/merit-increase-rates/9999")
    assert resp.status_code == 404


# =====================================================================
# AC-BR-04 + AC-BR-05: compensation burden rates
# =====================================================================

_BURDEN_BODY = {
    "fica_rate_pct": "6.2000",
    "fica_wage_cap": "168600.00",
    "medicare_rate_pct": "1.4500",
    "state_income_tax_rate_pct": "0.0000",
    "federal_income_tax_rate_pct": "0.0000",
    "suta_rate_pct": "2.7000",
    "suta_wage_cap": "7000.00",
    "futa_rate_pct": "0.6000",
    "futa_wage_cap": "7000.00",
    "other_benefits_rate_pct": "5.0000",
}


def test_burden_rate_write_requires_system_modeler(
    temp_db: Path, act_as: Callable[..., None], client: TestClient
) -> None:
    """AC-BR-04: Non-modeler non-admin receives 403 on PUT burden rate."""
    act_as("dev-readonly-001")
    resp = client.put("/api/v1/business-rules/compensation-burden-rates/2027", json=_BURDEN_BODY)
    assert resp.status_code == 403


def test_burden_rate_upsert_creates_and_reads_back(
    temp_db: Path, act_as: Callable[..., None], client: TestClient
) -> None:
    act_as("dev-modeler-001")
    resp = client.put("/api/v1/business-rules/compensation-burden-rates/2027", json=_BURDEN_BODY)
    assert resp.status_code == 200
    body = resp.json()
    assert body["fiscal_year"] == 2027
    assert Decimal(body["fica_wage_cap"]) == Decimal("168600.00")

    # Read it back
    get_resp = client.get("/api/v1/business-rules/compensation-burden-rates/2027")
    assert get_resp.status_code == 200
    assert get_resp.json()["fiscal_year"] == 2027


def test_burden_rate_upsert_writes_audit_event(
    temp_db: Path, act_as: Callable[..., None], client: TestClient
) -> None:
    """AC-BR-05: burden rate upsert writes COMPENSATION_BURDEN_RATE_UPDATED to audit_log."""
    act_as("dev-modeler-001")
    before = _audit_count("COMPENSATION_BURDEN_RATE_UPDATED")
    client.put("/api/v1/business-rules/compensation-burden-rates/2029", json=_BURDEN_BODY)
    assert _audit_count("COMPENSATION_BURDEN_RATE_UPDATED") == before + 1


# =====================================================================
# AC-BR-06 + AC-BR-07 + AC-BR-08: compensation component mappings
# =====================================================================

def test_component_mapping_write_requires_system_modeler(
    temp_db: Path, act_as: Callable[..., None], client: TestClient
) -> None:
    """AC-BR-06: Non-modeler non-admin receives 403 on PUT component mapping."""
    _seed_component_mapping("salary", "5000")
    act_as("dev-readonly-001")
    resp = client.put(
        "/api/v1/business-rules/compensation-component-mappings/salary",
        json={"account_code": "5001"},
    )
    assert resp.status_code == 403


def test_component_mapping_update_changes_account_code(
    temp_db: Path, act_as: Callable[..., None], client: TestClient
) -> None:
    _seed_component_mapping("salary", "5000")
    act_as("dev-modeler-001")
    resp = client.put(
        "/api/v1/business-rules/compensation-component-mappings/salary",
        json={"account_code": "5001"},
    )
    assert resp.status_code == 200
    assert resp.json()["account_code"] == "5001"


def test_component_mapping_404_for_unknown_code(
    temp_db: Path, act_as: Callable[..., None], client: TestClient
) -> None:
    """PUT on unknown component_code returns 404 (genuine not found)."""
    act_as("dev-modeler-001")
    resp = client.put(
        "/api/v1/business-rules/compensation-component-mappings/nonexistent_code",
        json={"account_code": "9999"},
    )
    assert resp.status_code == 404


def test_component_mapping_update_writes_audit_event(
    temp_db: Path, act_as: Callable[..., None], client: TestClient
) -> None:
    """AC-BR-07: component mapping update writes COMPENSATION_MAPPING_UPDATED to audit_log."""
    _seed_component_mapping("salary", "5000")
    act_as("dev-modeler-001")
    before = _audit_count("COMPENSATION_MAPPING_UPDATED")
    client.put(
        "/api/v1/business-rules/compensation-component-mappings/salary",
        json={"account_code": "5002"},
    )
    assert _audit_count("COMPENSATION_MAPPING_UPDATED") == before + 1


def test_component_mapping_update_calls_recalc_stub(
    temp_db: Path, act_as: Callable[..., None], client: TestClient
) -> None:
    """AC-BR-08: updating a mapping triggers stub_recalculate_open_fiscal_years."""
    _seed_component_mapping("aipeip_bonus", "5100")
    act_as("dev-modeler-001")

    with patch.object(
        calculation_service, "stub_recalculate_open_fiscal_years"
    ) as mock_stub:
        resp = client.put(
            "/api/v1/business-rules/compensation-component-mappings/aipeip_bonus",
            json={"account_code": "5101"},
        )
        assert resp.status_code == 200
        mock_stub.assert_called_once_with("aipeip_bonus")


# =====================================================================
# AC-BR-09 + AC-BR-10: overhead allocation rates
# =====================================================================

def test_overhead_rate_write_requires_system_modeler(
    temp_db: Path, act_as: Callable[..., None], client: TestClient
) -> None:
    """AC-BR-09: Non-modeler non-admin receives 403 on POST overhead rate."""
    act_as("dev-readonly-001")
    resp = client.post(
        "/api/v1/business-rules/overhead-allocation-rates",
        json={
            "account_code": "6100",
            "amount_per_employee": "500.00",
            "rate_period": "Monthly",
            "fiscal_year": 2026,
        },
    )
    assert resp.status_code == 403


def test_overhead_rate_create_returns_201_with_id(
    temp_db: Path, act_as: Callable[..., None], client: TestClient
) -> None:
    act_as("dev-modeler-001")
    resp = client.post(
        "/api/v1/business-rules/overhead-allocation-rates",
        json={
            "account_code": "6200",
            "geography_code": "CA",
            "amount_per_employee": "125.00",
            "rate_period": "Monthly",
            "fiscal_year": 2026,
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["account_code"] == "6200"
    assert body["geography_code"] == "CA"
    assert body["is_active"] is True
    assert "rate_id" in body


def test_overhead_rate_update(
    temp_db: Path, act_as: Callable[..., None], client: TestClient
) -> None:
    _seed_overhead_rate("oar-update-test")
    act_as("dev-modeler-001")
    resp = client.put(
        "/api/v1/business-rules/overhead-allocation-rates/oar-update-test",
        json={"amount_per_employee": "500.00"},
    )
    assert resp.status_code == 200
    assert Decimal(resp.json()["amount_per_employee"]) == Decimal("500.00")


def test_overhead_rate_deactivate(
    temp_db: Path, act_as: Callable[..., None], client: TestClient
) -> None:
    _seed_overhead_rate("oar-deact-test")
    act_as("dev-modeler-001")
    resp = client.delete("/api/v1/business-rules/overhead-allocation-rates/oar-deact-test")
    assert resp.status_code == 200
    assert resp.json()["is_active"] is False


def test_overhead_rate_404_for_unknown(
    temp_db: Path, act_as: Callable[..., None], client: TestClient
) -> None:
    act_as("dev-modeler-001")
    assert client.get(
        "/api/v1/business-rules/overhead-allocation-rates/does-not-exist"
    ).status_code == 404


def test_overhead_rate_lifecycle_audit_events(
    temp_db: Path, act_as: Callable[..., None], client: TestClient
) -> None:
    """AC-BR-10: CREATE, UPDATE, and DEACTIVATE each write the correct audit event."""
    act_as("dev-modeler-001")

    # CREATE
    before_create = _audit_count("OVERHEAD_RATE_CREATED")
    create_resp = client.post(
        "/api/v1/business-rules/overhead-allocation-rates",
        json={"account_code": "6300", "amount_per_employee": "75.00",
              "rate_period": "Monthly", "fiscal_year": 2026},
    )
    assert create_resp.status_code == 201
    rate_id = create_resp.json()["rate_id"]
    assert _audit_count("OVERHEAD_RATE_CREATED") == before_create + 1

    # UPDATE
    before_update = _audit_count("OVERHEAD_RATE_UPDATED")
    client.put(
        f"/api/v1/business-rules/overhead-allocation-rates/{rate_id}",
        json={"amount_per_employee": "80.00"},
    )
    assert _audit_count("OVERHEAD_RATE_UPDATED") == before_update + 1

    # DEACTIVATE
    before_deact = _audit_count("OVERHEAD_RATE_DEACTIVATED")
    client.delete(f"/api/v1/business-rules/overhead-allocation-rates/{rate_id}")
    assert _audit_count("OVERHEAD_RATE_DEACTIVATED") == before_deact + 1


def test_overhead_rate_filter_by_fiscal_year(
    temp_db: Path, act_as: Callable[..., None], client: TestClient
) -> None:
    _seed_overhead_rate("oar-2026-filter")
    act_as("dev-readonly-001")
    resp = client.get("/api/v1/business-rules/overhead-allocation-rates?fiscal_year=2026")
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) >= 1
    assert all(item["fiscal_year"] == 2026 for item in items)


def test_overhead_rate_filter_by_is_active(
    temp_db: Path, act_as: Callable[..., None], client: TestClient
) -> None:
    _seed_overhead_rate("oar-active-filter")
    act_as("dev-readonly-001")
    resp = client.get("/api/v1/business-rules/overhead-allocation-rates?is_active=true")
    assert resp.status_code == 200
    items = resp.json()
    assert all(item["is_active"] is True for item in items)


# =====================================================================
# AC-FC-01: fiscal calendar read
# =====================================================================

def test_fiscal_calendar_read(
    temp_db: Path, act_as: Callable[..., None], client: TestClient
) -> None:
    """AC-FC-01: Any authenticated active user can read obs.fiscal_calendar."""
    _seed_fiscal_period(2026, 6)
    act_as("dev-readonly-001")
    resp = client.get("/api/v1/fiscal-calendar")
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) >= 1
    period = items[0]
    assert "fiscal_year" in period
    assert "period_number" in period
    assert "period_name" in period
    assert "start_date" in period
    assert "end_date" in period
    assert "is_current" in period


def test_fiscal_calendar_filter_by_fiscal_year(
    temp_db: Path, act_as: Callable[..., None], client: TestClient
) -> None:
    _seed_fiscal_period(2026, 1)
    _seed_fiscal_period(2027, 1)
    act_as("dev-readonly-001")
    resp = client.get("/api/v1/fiscal-calendar?fiscal_year=2026")
    assert resp.status_code == 200
    items = resp.json()
    assert all(item["fiscal_year"] == 2026 for item in items)


def test_fiscal_calendar_requires_auth(
    temp_db: Path, act_as: Callable[..., None], client: TestClient
) -> None:
    act_as("ghost-user-unknown")
    assert client.get("/api/v1/fiscal-calendar").status_code == 403


# =====================================================================
# Administrator path also has write access (Step 3 short-circuit)
# =====================================================================

def test_admin_can_write_merit_rate(
    temp_db: Path, act_as: Callable[..., None], client: TestClient
) -> None:
    """require_capability's Step 3 short-circuit means Administrators can write."""
    act_as("dev-admin-001")
    resp = client.put(
        "/api/v1/business-rules/merit-increase-rates/2030",
        json={"rate_pct": "4.5000", "effective_date": "2030-03-01"},
    )
    assert resp.status_code == 200
    assert resp.json()["fiscal_year"] == 2030
