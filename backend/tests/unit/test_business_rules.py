"""Unit tests for Phase 3 business rules (Build Plan v1.2 §4.3).

Covers:
- Pydantic model validation (rate_period, field constraints)
- Recalculation stub logging (AC-BR-08 partial; full API-level test in integration)
- obs.fiscal_calendar table exists and is queryable
"""

from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.app.db import helpers
from backend.app.models.business_rules import CompensationBurdenRateUpsert
from backend.app.models.business_rules import MeritIncreaseRateUpsert
from backend.app.models.business_rules import OverheadAllocationRateCreate
from backend.app.services import calculation_service


# =====================================================================
# Pydantic model validation
# =====================================================================

def test_overhead_rate_create_rejects_invalid_rate_period() -> None:
    with pytest.raises(ValidationError):
        OverheadAllocationRateCreate(
            account_code="6100",
            amount_per_employee=Decimal("500.00"),
            rate_period="Quarterly",  # type: ignore[arg-type]  # deliberate invalid literal
            fiscal_year=2026,
        )


def test_overhead_rate_create_accepts_monthly() -> None:
    obj = OverheadAllocationRateCreate(
        account_code="6100",
        amount_per_employee=Decimal("500.00"),
        rate_period="Monthly",
        fiscal_year=2026,
    )
    assert obj.rate_period == "Monthly"


def test_overhead_rate_create_accepts_annual() -> None:
    obj = OverheadAllocationRateCreate(
        account_code="6100",
        amount_per_employee=Decimal("5000.00"),
        rate_period="Annual",
        fiscal_year=2026,
    )
    assert obj.rate_period == "Annual"


def test_merit_rate_rejects_negative_rate() -> None:
    with pytest.raises(ValidationError):
        MeritIncreaseRateUpsert(rate_pct=Decimal("-1.0"), effective_date=date(2026, 3, 1))


def test_merit_rate_rejects_rate_over_100() -> None:
    with pytest.raises(ValidationError):
        MeritIncreaseRateUpsert(rate_pct=Decimal("100.0"), effective_date=date(2026, 3, 1))


def test_burden_rate_rejects_negative_fica_wage_cap() -> None:
    with pytest.raises(ValidationError):
        CompensationBurdenRateUpsert(
            fica_rate_pct=Decimal("6.2"),
            fica_wage_cap=Decimal("-1.00"),
            medicare_rate_pct=Decimal("1.45"),
            state_income_tax_rate_pct=Decimal("0"),
            federal_income_tax_rate_pct=Decimal("0"),
            suta_rate_pct=Decimal("2.7"),
            suta_wage_cap=Decimal("7000"),
            futa_rate_pct=Decimal("0.6"),
            futa_wage_cap=Decimal("7000"),
            other_benefits_rate_pct=Decimal("5.0"),
        )


def test_overhead_rate_create_rejects_negative_amount() -> None:
    with pytest.raises(ValidationError):
        OverheadAllocationRateCreate(
            account_code="6100",
            amount_per_employee=Decimal("-100.00"),
            rate_period="Monthly",
            fiscal_year=2026,
        )


# =====================================================================
# Recalculation stub (AC-BR-08 — stub exists, logs correctly)
# =====================================================================

def test_recalc_stub_logs_component_code(caplog: pytest.LogCaptureFixture) -> None:
    """Stub must log the component_code at INFO level; this is the Phase 4 wiring point."""
    with caplog.at_level(logging.INFO, logger="backend.app.services.calculation_service"):
        calculation_service.stub_recalculate_open_fiscal_years("salary")
    assert "salary" in caplog.text


def test_recalc_stub_is_callable_without_side_effects() -> None:
    """Stub must not raise (not raising is the Phase 4 wiring contract)."""
    calculation_service.stub_recalculate_open_fiscal_years("aipeip_bonus")


# =====================================================================
# Schema: obs.fiscal_calendar must exist and be queryable
# =====================================================================

def test_fiscal_calendar_table_exists(temp_db: Path) -> None:
    """obs.fiscal_calendar must be present after bootstrap (AC-BR prerequisite)."""
    row = helpers.fetch_one("SELECT count(*) FROM obs.fiscal_calendar")
    assert row is not None
    assert int(row[0]) >= 0
