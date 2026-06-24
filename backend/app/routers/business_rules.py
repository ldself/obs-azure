"""Business rules configuration endpoints (Build Plan v1.2 §4.3).

All four tables — obs.merit_increase_rates, obs.compensation_burden_rates,
obs.compensation_component_mappings, obs.overhead_allocation_rates — plus
the obs.fiscal_calendar read API are served here.

Authorization:
  Reads:  any authenticated active user (get_current_user, Steps 1-2).
  Writes: System Modeler OR Administrator (require_capability, Step 4 with Step 3
          short-circuit). AC-CAP-05 governs.

Every mutation writes its audit event in the same transaction as the change (RULE 6).
Component-mapping updates also call the Phase 3 recalculation stub (RULE 4).

No rate, cap, or mapping value is hardcoded — all values are read from and written
to the database (RULE 4).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from datetime import timezone
from typing import Any

from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from fastapi import status

from backend.app.auth.dependencies import get_current_user
from backend.app.auth.dependencies import require_capability
from backend.app.auth.models import CurrentUser
from backend.app.db import engine
from backend.app.db import helpers
from backend.app.models.business_rules import CompensationBurdenRateOut
from backend.app.models.business_rules import CompensationBurdenRateUpsert
from backend.app.models.business_rules import CompensationComponentMappingOut
from backend.app.models.business_rules import CompensationComponentMappingUpdate
from backend.app.models.business_rules import FiscalPeriodOut
from backend.app.models.business_rules import MeritIncreaseRateOut
from backend.app.models.business_rules import MeritIncreaseRateUpsert
from backend.app.models.business_rules import OverheadAllocationRateCreate
from backend.app.models.business_rules import OverheadAllocationRateOut
from backend.app.models.business_rules import OverheadAllocationRateUpdate
from backend.app.services import audit_service
from backend.app.services import calculation_service


router = APIRouter(prefix="/api/v1", tags=["business-rules"])

_require_modeler = require_capability("is_system_modeler")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _ph() -> str:
    return engine.placeholder()


# =====================================================================
# SELECT column lists (ordering matches the row→model helpers below)
# =====================================================================

_MERIT_COLS = (
    "rate_id, fiscal_year, rate_pct, effective_date, updated_at, updated_by"
)
_MERIT_KEYS = ["rate_id", "fiscal_year", "rate_pct", "effective_date", "updated_at", "updated_by"]

_BURDEN_COLS = (
    "rate_id, fiscal_year, "
    "fica_rate_pct, fica_wage_cap, medicare_rate_pct, "
    "state_income_tax_rate_pct, federal_income_tax_rate_pct, "
    "suta_rate_pct, suta_wage_cap, "
    "futa_rate_pct, futa_wage_cap, "
    "other_benefits_rate_pct, updated_at, updated_by"
)
_BURDEN_KEYS = [
    "rate_id", "fiscal_year", "fica_rate_pct", "fica_wage_cap", "medicare_rate_pct",
    "state_income_tax_rate_pct", "federal_income_tax_rate_pct",
    "suta_rate_pct", "suta_wage_cap", "futa_rate_pct", "futa_wage_cap",
    "other_benefits_rate_pct", "updated_at", "updated_by",
]

_MAPPING_COLS = "mapping_id, component_code, account_code, updated_at, updated_by"
_MAPPING_KEYS = ["mapping_id", "component_code", "account_code", "updated_at", "updated_by"]

_OVERHEAD_COLS = (
    "rate_id, account_code, geography_code, amount_per_employee, "
    "rate_period, fiscal_year, is_active, created_at, updated_at, updated_by"
)
_OVERHEAD_KEYS = [
    "rate_id", "account_code", "geography_code", "amount_per_employee",
    "rate_period", "fiscal_year", "is_active", "created_at", "updated_at", "updated_by",
]

_FISCAL_COLS = (
    "calendar_id, fiscal_year, period_number, period_name, "
    "start_date, end_date, is_current"
)


# =====================================================================
# Helpers: row → model
# =====================================================================

def _merit_row_to_out(row: tuple[Any, ...]) -> MeritIncreaseRateOut:
    return MeritIncreaseRateOut(
        rate_id=row[0],
        fiscal_year=row[1],
        rate_pct=row[2],
        effective_date=row[3],
        updated_at=row[4],
        updated_by=row[5],
    )


def _burden_row_to_out(row: tuple[Any, ...]) -> CompensationBurdenRateOut:
    return CompensationBurdenRateOut(
        rate_id=row[0],
        fiscal_year=row[1],
        fica_rate_pct=row[2],
        fica_wage_cap=row[3],
        medicare_rate_pct=row[4],
        state_income_tax_rate_pct=row[5],
        federal_income_tax_rate_pct=row[6],
        suta_rate_pct=row[7],
        suta_wage_cap=row[8],
        futa_rate_pct=row[9],
        futa_wage_cap=row[10],
        other_benefits_rate_pct=row[11],
        updated_at=row[12],
        updated_by=row[13],
    )


def _mapping_row_to_out(row: tuple[Any, ...]) -> CompensationComponentMappingOut:
    return CompensationComponentMappingOut(
        mapping_id=row[0],
        component_code=row[1],
        account_code=row[2],
        updated_at=row[3],
        updated_by=row[4],
    )


def _overhead_row_to_out(row: tuple[Any, ...]) -> OverheadAllocationRateOut:
    return OverheadAllocationRateOut(
        rate_id=row[0],
        account_code=row[1],
        geography_code=row[2],
        amount_per_employee=row[3],
        rate_period=row[4],
        fiscal_year=row[5],
        is_active=bool(row[6]),
        created_at=row[7],
        updated_at=row[8],
        updated_by=row[9],
    )


def _fiscal_row_to_out(row: tuple[Any, ...]) -> FiscalPeriodOut:
    return FiscalPeriodOut(
        calendar_id=row[0],
        fiscal_year=row[1],
        period_number=row[2],
        period_name=row[3],
        start_date=row[4],
        end_date=row[5],
        is_current=bool(row[6]),
    )


# =====================================================================
# Merit increase rates
# =====================================================================

@router.get(
    "/business-rules/merit-increase-rates",
    response_model=list[MeritIncreaseRateOut],
)
def list_merit_rates(
    _: CurrentUser = Depends(get_current_user),
) -> list[MeritIncreaseRateOut]:
    rows = helpers.fetch_all(
        f"SELECT {_MERIT_COLS} FROM obs.merit_increase_rates ORDER BY fiscal_year"
    )
    return [_merit_row_to_out(r) for r in rows]


@router.get(
    "/business-rules/merit-increase-rates/{fiscal_year}",
    response_model=MeritIncreaseRateOut,
)
def get_merit_rate(
    fiscal_year: int,
    _: CurrentUser = Depends(get_current_user),
) -> MeritIncreaseRateOut:
    ph = _ph()
    row = helpers.fetch_one(
        f"SELECT {_MERIT_COLS} FROM obs.merit_increase_rates WHERE fiscal_year = {ph}",
        (fiscal_year,),
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"No merit rate for fiscal year {fiscal_year}.")
    return _merit_row_to_out(row)


@router.put(
    "/business-rules/merit-increase-rates/{fiscal_year}",
    response_model=MeritIncreaseRateOut,
)
def upsert_merit_rate(
    fiscal_year: int,
    body: MeritIncreaseRateUpsert,
    caller: CurrentUser = Depends(_require_modeler),
) -> MeritIncreaseRateOut:
    ph = _ph()
    now = _utc_now()
    with helpers.transaction() as conn:
        existing = helpers.query_one(
            conn,
            f"SELECT {_MERIT_COLS} FROM obs.merit_increase_rates WHERE fiscal_year = {ph}",
            (fiscal_year,),
        )
        previous_value = dict(zip(_MERIT_KEYS, existing, strict=False)) if existing else None

        if existing is None:
            rate_id = str(uuid.uuid4())
            helpers.exec_write(
                conn,
                f"INSERT INTO obs.merit_increase_rates "
                f"(rate_id, fiscal_year, rate_pct, effective_date, updated_at, updated_by) "
                f"VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, {ph})",
                (rate_id, fiscal_year, body.rate_pct, body.effective_date, now, caller.user_id),
            )
        else:
            helpers.exec_write(
                conn,
                f"UPDATE obs.merit_increase_rates "
                f"SET rate_pct = {ph}, effective_date = {ph}, updated_at = {ph}, "
                f"updated_by = {ph} WHERE fiscal_year = {ph}",
                (body.rate_pct, body.effective_date, now, caller.user_id, fiscal_year),
            )

        updated = helpers.query_one(
            conn,
            f"SELECT {_MERIT_COLS} FROM obs.merit_increase_rates WHERE fiscal_year = {ph}",
            (fiscal_year,),
        )
        audit_service.write_audit_event(
            conn,
            event_type=audit_service.AuditEvent.MERIT_INCREASE_RATE_UPDATED,
            user_id=caller.user_id,
            entity_type="merit_increase_rate",
            entity_id=str(fiscal_year),
            previous_value=previous_value,
            new_value=dict(zip(_MERIT_KEYS, updated, strict=False)),
            ip_address=caller.ip_address,
            session_id=caller.session_id,
        )
    return _merit_row_to_out(updated)


# =====================================================================
# Compensation burden rates
# =====================================================================

@router.get(
    "/business-rules/compensation-burden-rates",
    response_model=list[CompensationBurdenRateOut],
)
def list_burden_rates(
    _: CurrentUser = Depends(get_current_user),
) -> list[CompensationBurdenRateOut]:
    rows = helpers.fetch_all(
        f"SELECT {_BURDEN_COLS} FROM obs.compensation_burden_rates ORDER BY fiscal_year"
    )
    return [_burden_row_to_out(r) for r in rows]


@router.get(
    "/business-rules/compensation-burden-rates/{fiscal_year}",
    response_model=CompensationBurdenRateOut,
)
def get_burden_rate(
    fiscal_year: int,
    _: CurrentUser = Depends(get_current_user),
) -> CompensationBurdenRateOut:
    ph = _ph()
    row = helpers.fetch_one(
        f"SELECT {_BURDEN_COLS} FROM obs.compensation_burden_rates WHERE fiscal_year = {ph}",
        (fiscal_year,),
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"No burden rate for fiscal year {fiscal_year}.")
    return _burden_row_to_out(row)


@router.put(
    "/business-rules/compensation-burden-rates/{fiscal_year}",
    response_model=CompensationBurdenRateOut,
)
def upsert_burden_rate(
    fiscal_year: int,
    body: CompensationBurdenRateUpsert,
    caller: CurrentUser = Depends(_require_modeler),
) -> CompensationBurdenRateOut:
    ph = _ph()
    now = _utc_now()
    with helpers.transaction() as conn:
        existing = helpers.query_one(
            conn,
            f"SELECT {_BURDEN_COLS} FROM obs.compensation_burden_rates WHERE fiscal_year = {ph}",
            (fiscal_year,),
        )
        previous_value = dict(zip(_BURDEN_KEYS, existing, strict=False)) if existing else None

        if existing is None:
            rate_id = str(uuid.uuid4())
            helpers.exec_write(
                conn,
                f"INSERT INTO obs.compensation_burden_rates "
                f"(rate_id, fiscal_year, fica_rate_pct, fica_wage_cap, medicare_rate_pct, "
                f"state_income_tax_rate_pct, federal_income_tax_rate_pct, "
                f"suta_rate_pct, suta_wage_cap, futa_rate_pct, futa_wage_cap, "
                f"other_benefits_rate_pct, updated_at, updated_by) "
                f"VALUES ({', '.join([ph] * 14)})",
                (
                    rate_id, fiscal_year,
                    body.fica_rate_pct, body.fica_wage_cap, body.medicare_rate_pct,
                    body.state_income_tax_rate_pct, body.federal_income_tax_rate_pct,
                    body.suta_rate_pct, body.suta_wage_cap,
                    body.futa_rate_pct, body.futa_wage_cap,
                    body.other_benefits_rate_pct, now, caller.user_id,
                ),
            )
        else:
            helpers.exec_write(
                conn,
                f"UPDATE obs.compensation_burden_rates SET "
                f"fica_rate_pct = {ph}, fica_wage_cap = {ph}, medicare_rate_pct = {ph}, "
                f"state_income_tax_rate_pct = {ph}, federal_income_tax_rate_pct = {ph}, "
                f"suta_rate_pct = {ph}, suta_wage_cap = {ph}, "
                f"futa_rate_pct = {ph}, futa_wage_cap = {ph}, "
                f"other_benefits_rate_pct = {ph}, updated_at = {ph}, updated_by = {ph} "
                f"WHERE fiscal_year = {ph}",
                (
                    body.fica_rate_pct, body.fica_wage_cap, body.medicare_rate_pct,
                    body.state_income_tax_rate_pct, body.federal_income_tax_rate_pct,
                    body.suta_rate_pct, body.suta_wage_cap,
                    body.futa_rate_pct, body.futa_wage_cap,
                    body.other_benefits_rate_pct, now, caller.user_id,
                    fiscal_year,
                ),
            )

        updated = helpers.query_one(
            conn,
            f"SELECT {_BURDEN_COLS} FROM obs.compensation_burden_rates WHERE fiscal_year = {ph}",
            (fiscal_year,),
        )
        audit_service.write_audit_event(
            conn,
            event_type=audit_service.AuditEvent.COMPENSATION_BURDEN_RATE_UPDATED,
            user_id=caller.user_id,
            entity_type="compensation_burden_rate",
            entity_id=str(fiscal_year),
            previous_value=previous_value,
            new_value=dict(zip(_BURDEN_KEYS, updated, strict=False)),
            ip_address=caller.ip_address,
            session_id=caller.session_id,
        )
    return _burden_row_to_out(updated)


# =====================================================================
# Compensation component mappings
# =====================================================================

@router.get(
    "/business-rules/compensation-component-mappings",
    response_model=list[CompensationComponentMappingOut],
)
def list_component_mappings(
    _: CurrentUser = Depends(get_current_user),
) -> list[CompensationComponentMappingOut]:
    rows = helpers.fetch_all(
        f"SELECT {_MAPPING_COLS} FROM obs.compensation_component_mappings "
        "ORDER BY component_code"
    )
    return [_mapping_row_to_out(r) for r in rows]


@router.get(
    "/business-rules/compensation-component-mappings/{component_code}",
    response_model=CompensationComponentMappingOut,
)
def get_component_mapping(
    component_code: str,
    _: CurrentUser = Depends(get_current_user),
) -> CompensationComponentMappingOut:
    ph = _ph()
    row = helpers.fetch_one(
        f"SELECT {_MAPPING_COLS} FROM obs.compensation_component_mappings "
        f"WHERE component_code = {ph}",
        (component_code,),
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"Component code '{component_code}' not found.")
    return _mapping_row_to_out(row)


@router.put(
    "/business-rules/compensation-component-mappings/{component_code}",
    response_model=CompensationComponentMappingOut,
)
def update_component_mapping(
    component_code: str,
    body: CompensationComponentMappingUpdate,
    caller: CurrentUser = Depends(_require_modeler),
) -> CompensationComponentMappingOut:
    ph = _ph()
    now = _utc_now()
    with helpers.transaction() as conn:
        existing = helpers.query_one(
            conn,
            f"SELECT {_MAPPING_COLS} FROM obs.compensation_component_mappings "
            f"WHERE component_code = {ph}",
            (component_code,),
        )
        if existing is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                                detail=f"Component code '{component_code}' not found.")

        helpers.exec_write(
            conn,
            f"UPDATE obs.compensation_component_mappings "
            f"SET account_code = {ph}, updated_at = {ph}, updated_by = {ph} "
            f"WHERE component_code = {ph}",
            (body.account_code, now, caller.user_id, component_code),
        )

        updated = helpers.query_one(
            conn,
            f"SELECT {_MAPPING_COLS} FROM obs.compensation_component_mappings "
            f"WHERE component_code = {ph}",
            (component_code,),
        )
        audit_service.write_audit_event(
            conn,
            event_type=audit_service.AuditEvent.COMPENSATION_MAPPING_UPDATED,
            user_id=caller.user_id,
            entity_type="compensation_component_mapping",
            entity_id=component_code,
            previous_value=dict(zip(_MAPPING_KEYS, existing, strict=False)),
            new_value=dict(zip(_MAPPING_KEYS, updated, strict=False)),
            ip_address=caller.ip_address,
            session_id=caller.session_id,
        )

    calculation_service.stub_recalculate_open_fiscal_years(component_code)
    return _mapping_row_to_out(updated)


# =====================================================================
# Overhead allocation rates
# =====================================================================

@router.get(
    "/business-rules/overhead-allocation-rates",
    response_model=list[OverheadAllocationRateOut],
)
def list_overhead_rates(
    fiscal_year: int | None = None,
    is_active: bool | None = None,
    _: CurrentUser = Depends(get_current_user),
) -> list[OverheadAllocationRateOut]:
    ph = _ph()
    clauses: list[str] = []
    params: list[Any] = []
    if fiscal_year is not None:
        clauses.append(f"fiscal_year = {ph}")
        params.append(fiscal_year)
    if is_active is not None:
        clauses.append(f"is_active = {ph}")
        params.append(is_active)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = helpers.fetch_all(
        f"SELECT {_OVERHEAD_COLS} FROM obs.overhead_allocation_rates "
        f"{where} ORDER BY fiscal_year, account_code, geography_code",
        params or None,
    )
    return [_overhead_row_to_out(r) for r in rows]


@router.get(
    "/business-rules/overhead-allocation-rates/{rate_id}",
    response_model=OverheadAllocationRateOut,
)
def get_overhead_rate(
    rate_id: str,
    _: CurrentUser = Depends(get_current_user),
) -> OverheadAllocationRateOut:
    ph = _ph()
    row = helpers.fetch_one(
        f"SELECT {_OVERHEAD_COLS} FROM obs.overhead_allocation_rates WHERE rate_id = {ph}",
        (rate_id,),
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"Overhead rate '{rate_id}' not found.")
    return _overhead_row_to_out(row)


@router.post(
    "/business-rules/overhead-allocation-rates",
    response_model=OverheadAllocationRateOut,
    status_code=status.HTTP_201_CREATED,
)
def create_overhead_rate(
    body: OverheadAllocationRateCreate,
    caller: CurrentUser = Depends(_require_modeler),
) -> OverheadAllocationRateOut:
    ph = _ph()
    now = _utc_now()
    rate_id = str(uuid.uuid4())
    with helpers.transaction() as conn:
        helpers.exec_write(
            conn,
            f"INSERT INTO obs.overhead_allocation_rates "
            f"(rate_id, account_code, geography_code, amount_per_employee, "
            f"rate_period, fiscal_year, is_active, created_at, updated_at, updated_by) "
            f"VALUES ({', '.join([ph] * 10)})",
            (
                rate_id, body.account_code, body.geography_code,
                body.amount_per_employee, body.rate_period, body.fiscal_year,
                True, now, now, caller.user_id,
            ),
        )
        created = helpers.query_one(
            conn,
            f"SELECT {_OVERHEAD_COLS} FROM obs.overhead_allocation_rates WHERE rate_id = {ph}",
            (rate_id,),
        )
        audit_service.write_audit_event(
            conn,
            event_type=audit_service.AuditEvent.OVERHEAD_RATE_CREATED,
            user_id=caller.user_id,
            entity_type="overhead_allocation_rate",
            entity_id=rate_id,
            previous_value=None,
            new_value=dict(zip(_OVERHEAD_KEYS, created, strict=False)),
            ip_address=caller.ip_address,
            session_id=caller.session_id,
        )
    return _overhead_row_to_out(created)


@router.put(
    "/business-rules/overhead-allocation-rates/{rate_id}",
    response_model=OverheadAllocationRateOut,
)
def update_overhead_rate(
    rate_id: str,
    body: OverheadAllocationRateUpdate,
    caller: CurrentUser = Depends(_require_modeler),
) -> OverheadAllocationRateOut:
    ph = _ph()
    now = _utc_now()
    with helpers.transaction() as conn:
        existing = helpers.query_one(
            conn,
            f"SELECT {_OVERHEAD_COLS} FROM obs.overhead_allocation_rates WHERE rate_id = {ph}",
            (rate_id,),
        )
        if existing is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                                detail=f"Overhead rate '{rate_id}' not found.")

        set_parts: list[str] = [f"updated_at = {ph}", f"updated_by = {ph}"]
        params: list[Any] = [now, caller.user_id]
        if body.amount_per_employee is not None:
            set_parts.append(f"amount_per_employee = {ph}")
            params.append(body.amount_per_employee)
        if body.rate_period is not None:
            set_parts.append(f"rate_period = {ph}")
            params.append(body.rate_period)
        params.append(rate_id)

        helpers.exec_write(
            conn,
            f"UPDATE obs.overhead_allocation_rates "
            f"SET {', '.join(set_parts)} WHERE rate_id = {ph}",
            params,
        )
        updated = helpers.query_one(
            conn,
            f"SELECT {_OVERHEAD_COLS} FROM obs.overhead_allocation_rates WHERE rate_id = {ph}",
            (rate_id,),
        )
        audit_service.write_audit_event(
            conn,
            event_type=audit_service.AuditEvent.OVERHEAD_RATE_UPDATED,
            user_id=caller.user_id,
            entity_type="overhead_allocation_rate",
            entity_id=rate_id,
            previous_value=dict(zip(_OVERHEAD_KEYS, existing, strict=False)),
            new_value=dict(zip(_OVERHEAD_KEYS, updated, strict=False)),
            ip_address=caller.ip_address,
            session_id=caller.session_id,
        )
    return _overhead_row_to_out(updated)


@router.delete(
    "/business-rules/overhead-allocation-rates/{rate_id}",
    response_model=OverheadAllocationRateOut,
)
def deactivate_overhead_rate(
    rate_id: str,
    caller: CurrentUser = Depends(_require_modeler),
) -> OverheadAllocationRateOut:
    ph = _ph()
    now = _utc_now()
    with helpers.transaction() as conn:
        existing = helpers.query_one(
            conn,
            f"SELECT {_OVERHEAD_COLS} FROM obs.overhead_allocation_rates WHERE rate_id = {ph}",
            (rate_id,),
        )
        if existing is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                                detail=f"Overhead rate '{rate_id}' not found.")

        helpers.exec_write(
            conn,
            f"UPDATE obs.overhead_allocation_rates "
            f"SET is_active = FALSE, updated_at = {ph}, updated_by = {ph} "
            f"WHERE rate_id = {ph}",
            (now, caller.user_id, rate_id),
        )
        updated = helpers.query_one(
            conn,
            f"SELECT {_OVERHEAD_COLS} FROM obs.overhead_allocation_rates WHERE rate_id = {ph}",
            (rate_id,),
        )
        audit_service.write_audit_event(
            conn,
            event_type=audit_service.AuditEvent.OVERHEAD_RATE_DEACTIVATED,
            user_id=caller.user_id,
            entity_type="overhead_allocation_rate",
            entity_id=rate_id,
            previous_value=dict(zip(_OVERHEAD_KEYS, existing, strict=False)),
            new_value=dict(zip(_OVERHEAD_KEYS, updated, strict=False)),
            ip_address=caller.ip_address,
            session_id=caller.session_id,
        )
    return _overhead_row_to_out(updated)


# =====================================================================
# Fiscal calendar (read-only; any authenticated active user)
# =====================================================================

@router.get("/fiscal-calendar", response_model=list[FiscalPeriodOut])
def list_fiscal_periods(
    fiscal_year: int | None = None,
    _: CurrentUser = Depends(get_current_user),
) -> list[FiscalPeriodOut]:
    ph = _ph()
    if fiscal_year is not None:
        rows = helpers.fetch_all(
            f"SELECT {_FISCAL_COLS} FROM obs.fiscal_calendar "
            f"WHERE fiscal_year = {ph} ORDER BY period_number",
            (fiscal_year,),
        )
    else:
        rows = helpers.fetch_all(
            f"SELECT {_FISCAL_COLS} FROM obs.fiscal_calendar "
            "ORDER BY fiscal_year, period_number"
        )
    return [_fiscal_row_to_out(r) for r in rows]
