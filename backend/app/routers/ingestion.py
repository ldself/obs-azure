"""Ingestion monitoring and manual trigger API (Phase 2, Build Plan v1.2 §4.2).

All endpoints are Administrator-only (Security Spec v1.4 §6.2). RULE 9 write
endpoints for source-system-owned tables return 405 permanently.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from fastapi import status

from backend.app.auth.dependencies import require_administrator
from backend.app.auth.models import CurrentUser
from backend.app.db import helpers
from backend.app.models.ingestion import IngestionRecordOut
from backend.app.models.ingestion import QuarantineRecordOut
from backend.app.models.ingestion import TriggerIngestionBody
from backend.pipeline import pipeline_service as ps

logger = logging.getLogger("obs.routers.ingestion")

router = APIRouter(prefix="/api/v1", tags=["ingestion"])

# Standard quarantine columns common to all quarantine tables.
# Anything outside this set is considered source row_data.
_STANDARD_QUARANTINE_COLS = {
    "quarantine_id", "ingestion_id", "quarantine_reason",
    "quarantine_status", "resolved_by", "resolved_at", "source_row_number",
}

# Maps file_type → quarantine table name
_QUARANTINE_TABLE: dict[str, str] = {
    ps.FILE_TYPE_ACTUALS: "obs.actuals_quarantine",
    ps.FILE_TYPE_EMPLOYEES: "obs.employees_quarantine",
    ps.FILE_TYPE_CC_HIERARCHY: "obs.cost_center_hierarchy_quarantine",
    ps.FILE_TYPE_ACCT_HIERARCHY: "obs.account_hierarchy_quarantine",
}


def _row_to_ingestion_out(row: tuple[Any, ...]) -> IngestionRecordOut:
    return IngestionRecordOut(
        ingestion_id=row[0],
        file_name=row[1],
        content_hash=row[2],
        source_system=row[3],
        file_type=row[4],
        file_format=row[5],
        status=row[6],
        re_ingestion=bool(row[7]),
        original_ingestion_id=row[8],
        total_rows=row[9],
        valid_rows=row[10],
        quarantined_rows=row[11],
        rejected_rows=row[12],
        promoted_rows=row[13],
        error_rate=row[14],
        triggered_by=row[15],
        started_at=row[16],
        completed_at=row[17],
        error_detail=row[18],
    )


_INGESTION_SELECT = (
    "SELECT ingestion_id, file_name, content_hash, source_system, file_type, "
    "file_format, status, re_ingestion, original_ingestion_id, total_rows, "
    "valid_rows, quarantined_rows, rejected_rows, promoted_rows, error_rate, "
    "triggered_by, started_at, completed_at, error_detail FROM obs.ingestion_control"
)


@router.get("/ingestion", response_model=list[IngestionRecordOut])
def list_ingestion_records(
    file_type: str | None = None,
    ingestion_status: str | None = None,
    _: CurrentUser = Depends(require_administrator),
) -> list[IngestionRecordOut]:
    """List all ingestion records; optionally filter by file_type or status."""
    ph = helpers.engine.placeholder()
    sql = _INGESTION_SELECT
    params: list[Any] = []
    conditions: list[str] = []

    if file_type:
        conditions.append(f"file_type = {ph}")
        params.append(file_type)
    if ingestion_status:
        conditions.append(f"status = {ph}")
        params.append(ingestion_status)

    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    sql += " ORDER BY started_at DESC"

    rows = helpers.fetch_all(sql, params or None)
    return [_row_to_ingestion_out(r) for r in rows]


@router.get("/ingestion/{ingestion_id}", response_model=IngestionRecordOut)
def get_ingestion_record(
    ingestion_id: str,
    _: CurrentUser = Depends(require_administrator),
) -> IngestionRecordOut:
    ph = helpers.engine.placeholder()
    row = helpers.fetch_one(
        f"{_INGESTION_SELECT} WHERE ingestion_id = {ph}",
        (ingestion_id,),
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ingestion record not found")
    return _row_to_ingestion_out(row)


@router.get("/ingestion/{ingestion_id}/quarantine", response_model=list[QuarantineRecordOut])
def get_quarantine_records(
    ingestion_id: str,
    _: CurrentUser = Depends(require_administrator),
) -> list[QuarantineRecordOut]:
    """Return all quarantine rows for the given ingestion run."""
    ph = helpers.engine.placeholder()

    ingestion_row = helpers.fetch_one(
        f"SELECT file_type FROM obs.ingestion_control WHERE ingestion_id = {ph}",
        (ingestion_id,),
    )
    if ingestion_row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ingestion record not found")

    file_type = ingestion_row[0]
    quarantine_table = _QUARANTINE_TABLE.get(file_type)
    if quarantine_table is None:
        return []

    col_names, rows = helpers.fetch_all_with_cols(
        f"SELECT * FROM {quarantine_table} WHERE ingestion_id = {ph} ORDER BY source_row_number",
        (ingestion_id,),
    )

    results: list[QuarantineRecordOut] = []
    for row in rows:
        row_dict = dict(zip(col_names, row, strict=False))
        row_data = {k: v for k, v in row_dict.items() if k not in _STANDARD_QUARANTINE_COLS}
        results.append(
            QuarantineRecordOut(
                quarantine_id=row_dict["quarantine_id"],
                ingestion_id=row_dict["ingestion_id"],
                quarantine_reason=row_dict["quarantine_reason"],
                quarantine_status=row_dict["quarantine_status"],
                resolved_by=row_dict.get("resolved_by"),
                resolved_at=row_dict.get("resolved_at"),
                source_row_number=row_dict["source_row_number"],
                row_data={k: v for k, v in row_data.items() if v is not None},
            )
        )
    return results


@router.post("/ingestion/trigger", response_model=IngestionRecordOut)
def trigger_manual_ingestion(
    body: TriggerIngestionBody,
    current_user: CurrentUser = Depends(require_administrator),
) -> IngestionRecordOut:
    """Manually trigger an ingestion run for a file in the landing zone."""
    from backend.pipeline import actuals_pipeline
    from backend.pipeline import employees_pipeline
    from backend.pipeline import hierarchy_pipeline

    file_type = body.file_type.upper()
    landing_zone = os.environ.get("LANDING_ZONE_PATH", "./local-data/landing-zone")

    file_path: str | None = None
    if body.file_name:
        import pathlib
        for subdir in ("actuals", "employees", "hierarchies", ""):
            candidate = pathlib.Path(landing_zone) / subdir / body.file_name
            if candidate.exists():
                file_path = str(candidate)
                break

    try:
        if file_type == ps.FILE_TYPE_ACTUALS:
            ingestion_id = actuals_pipeline.run(file_path)
        elif file_type == ps.FILE_TYPE_EMPLOYEES:
            ingestion_id = employees_pipeline.run(file_path)
        elif file_type in (ps.FILE_TYPE_CC_HIERARCHY, ps.FILE_TYPE_ACCT_HIERARCHY):
            ingestion_id = hierarchy_pipeline.run(file_path, file_type)
        else:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Unknown file_type: {body.file_type}",
            )
    except Exception as exc:
        logger.error("Manual trigger failed: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Ingestion pipeline raised an unexpected error",
        ) from exc

    if ingestion_id is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No eligible file found in the landing zone",
        )

    ph = helpers.engine.placeholder()
    row = helpers.fetch_one(
        f"{_INGESTION_SELECT} WHERE ingestion_id = {ph}",
        (ingestion_id,),
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Ingestion record missing after run",
        )
    return _row_to_ingestion_out(row)


# ---------------------------------------------------------------------------
# RULE 9 / OI-DI-06: write attempts on source-system-owned tables → 405
# ---------------------------------------------------------------------------

@router.post("/ingestion/cost-center-hierarchy", status_code=status.HTTP_405_METHOD_NOT_ALLOWED)
@router.put("/ingestion/cost-center-hierarchy", status_code=status.HTTP_405_METHOD_NOT_ALLOWED)
@router.delete("/ingestion/cost-center-hierarchy", status_code=status.HTTP_405_METHOD_NOT_ALLOWED)
def cc_hierarchy_write_not_allowed() -> None:
    """OI-DI-06 / RULE 9: cost center hierarchy re-imports are blocked. Return 405."""
    raise HTTPException(
        status_code=status.HTTP_405_METHOD_NOT_ALLOWED,
        detail="Cost center hierarchy re-imports are blocked (OI-DI-06). Use the seed file on initial load only.",
    )


@router.post("/ingestion/account-hierarchy-nodes", status_code=status.HTTP_405_METHOD_NOT_ALLOWED)
@router.put("/ingestion/account-hierarchy-nodes", status_code=status.HTTP_405_METHOD_NOT_ALLOWED)
@router.delete("/ingestion/account-hierarchy-nodes", status_code=status.HTTP_405_METHOD_NOT_ALLOWED)
def acct_hierarchy_nodes_write_not_allowed() -> None:
    """RULE 9: account hierarchy nodes are never written directly via API. Return 405."""
    raise HTTPException(
        status_code=status.HTTP_405_METHOD_NOT_ALLOWED,
        detail="Account hierarchy nodes are read-only via API (RULE 9). Ingest via pipeline file drop.",
    )


@router.post("/ingestion/expense-accounts", status_code=status.HTTP_405_METHOD_NOT_ALLOWED)
@router.put("/ingestion/expense-accounts", status_code=status.HTTP_405_METHOD_NOT_ALLOWED)
@router.delete("/ingestion/expense-accounts", status_code=status.HTTP_405_METHOD_NOT_ALLOWED)
def expense_accounts_write_not_allowed() -> None:
    """RULE 9: expense accounts are read-only via API. Return 405."""
    raise HTTPException(
        status_code=status.HTTP_405_METHOD_NOT_ALLOWED,
        detail="Expense accounts are read-only via API (RULE 9). Ingest via pipeline file drop.",
    )
