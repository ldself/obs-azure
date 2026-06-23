"""Employees ingestion pipeline (Phase 2).

Processes employees_* files from the landing zone:
  1. Compute SHA-256 on raw bytes; skip if already COMPLETED (RULE 10).
  2. Parse CSV or Excel rows.
  3. Validate each row: required fields, type coercion, and referential integrity
     against cost_center_hierarchy_memberships.
  4. Reject the file entirely if error_rate > 0.80.
  5. Stage valid rows to obs.employees_staging.
  6. Promote staged rows to obs.employees via merge() on (p_number, cost_center).
     Stable employee_id UUID and created_at are preserved across updates.
  7. Write INGESTION_COMPLETED or INGESTION_REJECTED audit event (RULE 6).
  8. Send non-fatal Teams + Email alerts (OI-DI-04, RULE 7).
"""

from __future__ import annotations

import contextlib
import csv
import logging
import os
import shutil
import uuid
from datetime import date
from datetime import datetime
from datetime import timezone
from pathlib import Path
from typing import Any

import openpyxl

from backend.app.db import engine
from backend.app.db import helpers
from backend.app.services import audit_service
from backend.pipeline import pipeline_alert_service
from backend.pipeline import pipeline_service as ps

logger = logging.getLogger("obs.pipeline.employees")

SOURCE_SYSTEM = "HR_ADMIN"
FILE_TYPE = ps.FILE_TYPE_EMPLOYEES

# Required string fields (non-empty)
_REQUIRED_STR_FIELDS = [
    "p_number", "company", "entity", "cost_center", "department",
    "last_name", "first_name", "salary_structure",
    "home_state", "work_state", "office",
    "workplace_flexibility", "management_production",
    "job_grade", "full_time_part_time",
]


def run(
    file_path: str | None = None,
    *,
    actor_user_id: str | None = None,
    triggered_by: str | None = None,
) -> str | None:
    """Entry point invoked by the Makefile and the Azure Function trigger.

    Returns the ingestion_id on success, None if the file was skipped.
    ``actor_user_id`` defaults to SYSTEM_USER_ID for scheduled runs; pass the
    authenticated user's ID for manual triggers (RULE 6).
    ``triggered_by`` defaults to TRIGGERED_SCHEDULED.
    """
    resolved_actor = actor_user_id or ps.SYSTEM_USER_ID
    resolved_triggered_by = triggered_by or ps.TRIGGERED_SCHEDULED

    landing_zone = os.environ.get("LANDING_ZONE_PATH", "./local-data/landing-zone")
    archive_path = os.environ.get("ARCHIVE_PATH", "./local-data/archive")
    error_path = os.environ.get("ERROR_PATH", "./local-data/error")

    resolved_path = _resolve_file(file_path, landing_zone)
    if resolved_path is None:
        logger.info("No employees file found in landing zone")
        return None

    file_name = Path(resolved_path).name
    logger.info("employees_pipeline: processing %s", file_name)

    content_hash = ps.compute_sha256(resolved_path)
    if ps.check_duplicate(file_name, content_hash):
        logger.info("Skipping duplicate file %s", file_name)
        return None

    file_format = "EXCEL" if resolved_path.lower().endswith((".xlsx", ".xls")) else "CSV"
    ingestion_id = str(uuid.uuid4())

    try:
        _process(
            resolved_path=resolved_path,
            file_name=file_name,
            content_hash=content_hash,
            file_format=file_format,
            ingestion_id=ingestion_id,
            actor_user_id=resolved_actor,
            triggered_by=resolved_triggered_by,
        )
        _move_file(resolved_path, archive_path, file_name)
        return ingestion_id
    except Exception:
        logger.exception("employees_pipeline: unhandled error for %s", file_name)
        _move_file(resolved_path, error_path, file_name)
        raise


def _resolve_file(file_path: str | None, landing_zone: str) -> str | None:
    if file_path:
        return file_path
    emp_dir = Path(landing_zone) / "employees"
    if not emp_dir.exists():
        emp_dir = Path(landing_zone)
    candidates = sorted(
        p for p in emp_dir.iterdir()
        if p.is_file() and p.name.startswith("employees_")
    )
    return str(candidates[0]) if candidates else None


def _move_file(src: str, dest_dir: str, file_name: str) -> None:
    try:
        dest = Path(dest_dir)
        dest.mkdir(parents=True, exist_ok=True)
        shutil.move(src, dest / file_name)
    except Exception as exc:
        logger.warning("Could not move %s: %s", file_name, exc)


def _parse_rows(resolved_path: str, file_format: str) -> list[dict[str, str]]:
    if file_format == "EXCEL":
        wb = openpyxl.load_workbook(resolved_path, read_only=True, data_only=True)
        ws = wb.active
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            return []
        headers = [str(h).strip().lower() if h is not None else "" for h in rows[0]]
        return [dict(zip(headers, [str(v) if v is not None else "" for v in row], strict=False)) for row in rows[1:]]
    else:
        with open(resolved_path, newline="", encoding="utf-8-sig") as fh:
            reader = csv.DictReader(fh)
            return [
                {k.strip().lower(): v.strip() for k, v in row.items()}
                for row in reader
            ]


_DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%Y/%m/%d")


def _parse_date(val: str) -> date | None:
    for fmt in _DATE_FORMATS:
        with contextlib.suppress(ValueError):
            return datetime.strptime(val, fmt).date()
    return None


def _validate_row(
    row: dict[str, str],
    valid_cost_centers: set[str],
) -> tuple[dict[str, Any] | None, str | None]:
    """Return (typed_row, quarantine_reason) or (None, QR_VALIDATION_FAILED) on error."""
    typed: dict[str, Any] = {}

    for field in _REQUIRED_STR_FIELDS:
        val = row.get(field, "").strip()
        if not val:
            return None, ps.QR_VALIDATION_FAILED
        typed[field] = val

    # annual_salary: integer
    try:
        typed["annual_salary"] = int(float(row.get("annual_salary", "")))
    except (ValueError, TypeError):
        return None, ps.QR_VALIDATION_FAILED

    # fte: float > 0
    try:
        typed["fte"] = float(row.get("fte", ""))
        if typed["fte"] <= 0:
            return None, ps.QR_VALIDATION_FAILED
    except (ValueError, TypeError):
        return None, ps.QR_VALIDATION_FAILED

    # last_hire_date: required date
    lhd = _parse_date(row.get("last_hire_date", ""))
    if lhd is None:
        return None, ps.QR_VALIDATION_FAILED
    typed["last_hire_date"] = lhd

    # Optional numeric fields
    for field in ("hours_worked", "ot_hours_worked"):
        val = row.get(field, "").strip()
        typed[field] = float(val) if val else None

    # Optional date field
    td = row.get("termination_date", "").strip()
    typed["termination_date"] = _parse_date(td) if td else None

    # Optional text field
    typed["title"] = row.get("title", "").strip() or None

    # Derived field (RULE 1: aipeip_eligible not abbreviated)
    typed["aipeip_eligible"] = typed["salary_structure"].upper() in ("EIP", "AIP")

    # Referential integrity: cost_center
    if typed["cost_center"] not in valid_cost_centers:
        return typed, ps.QR_MISSING_COST_CENTER

    return typed, None


def _process(
    *,
    resolved_path: str,
    file_name: str,
    content_hash: str,
    file_format: str,
    ingestion_id: str,
    actor_user_id: str,
    triggered_by: str,
) -> None:
    ph = engine.placeholder()
    now = datetime.now(timezone.utc)

    valid_cost_centers: set[str] = {
        row[0]
        for row in helpers.fetch_all(
            "SELECT DISTINCT cost_center_code FROM obs.cost_center_hierarchy_memberships WHERE is_active = TRUE"
        )
    }

    raw_rows = _parse_rows(resolved_path, file_format)
    total_rows = len(raw_rows)

    staging_rows: list[dict[str, Any]] = []
    quarantine_rows: list[tuple[dict[str, Any] | None, str, int]] = []
    hard_rejected = 0

    for row_num, raw in enumerate(raw_rows, start=1):
        typed, reason = _validate_row(raw, valid_cost_centers)
        if reason == ps.QR_VALIDATION_FAILED:
            hard_rejected += 1
        elif reason is not None:
            quarantine_rows.append((typed, reason, row_num))
        else:
            staging_rows.append({**typed, "source_row_number": row_num})

    quarantined_rows = len(quarantine_rows)
    valid_rows = len(staging_rows)
    error_rate = (quarantined_rows + hard_rejected) / total_rows if total_rows > 0 else 0.0

    with helpers.transaction() as conn:
        if not ps.create_ingestion_record(
            conn,
            ingestion_id=ingestion_id,
            file_name=file_name,
            content_hash=content_hash,
            file_type=FILE_TYPE,
            source_system=SOURCE_SYSTEM,
            file_format=file_format,
            triggered_by=triggered_by,
        ):
            logger.info("Atomic dedup: COMPLETED record exists for %s, skipping", file_name)
            return

        if error_rate > 0.80:
            ps.update_ingestion_record(
                conn,
                ingestion_id=ingestion_id,
                status=ps.STATUS_FAILED,
                total_rows=total_rows,
                valid_rows=valid_rows,
                quarantined_rows=quarantined_rows,
                rejected_rows=hard_rejected,
                promoted_rows=0,
                error_rate=error_rate,
                error_detail=f"error_rate={error_rate:.2%} exceeds 80% threshold",
            )
            audit_service.write_audit_event(
                conn,
                event_type=audit_service.AuditEvent.INGESTION_REJECTED,
                user_id=actor_user_id,
                entity_type="ingestion",
                entity_id=ingestion_id,
                new_value={
                    "file_name": file_name,
                    "error_rate": error_rate,
                    "total_rows": total_rows,
                },
            )
        else:
            staged_at = now

            # Stage valid rows
            for sr in staging_rows:
                staging_id = str(uuid.uuid4())
                helpers.exec_write(
                    conn,
                    f"INSERT INTO obs.employees_staging "
                    f"(ingestion_id, staging_id, p_number, company, entity, cost_center, "
                    f" department, last_name, first_name, last_hire_date, salary_structure, "
                    f" title, annual_salary, home_state, termination_date, work_state, "
                    f" office, workplace_flexibility, management_production, job_grade, "
                    f" full_time_part_time, hours_worked, ot_hours_worked, fte, "
                    f" source_file_name, source_row_number, staged_at) "
                    f"VALUES ({', '.join([ph] * 27)})",
                    (
                        ingestion_id, staging_id,
                        sr["p_number"], sr["company"], sr["entity"], sr["cost_center"],
                        sr["department"], sr["last_name"], sr["first_name"],
                        sr["last_hire_date"], sr["salary_structure"], sr.get("title"),
                        sr["annual_salary"], sr["home_state"], sr.get("termination_date"),
                        sr["work_state"], sr["office"], sr["workplace_flexibility"],
                        sr["management_production"], sr["job_grade"],
                        sr["full_time_part_time"], sr.get("hours_worked"),
                        sr.get("ot_hours_worked"), sr["fte"],
                        file_name, sr["source_row_number"], staged_at,
                    ),
                )

            # Quarantine invalid rows.
            # aipeip_eligible is a derived field not present in employees_quarantine;
            # source_row_number is a standard quarantine col passed separately.
            emp_q_exclude = {"aipeip_eligible", "source_row_number"}
            for typed, reason, row_num in quarantine_rows:
                q_data = {k: v for k, v in (typed or {}).items() if k not in emp_q_exclude}
                ps.write_quarantine_record(
                    conn,
                    ingestion_id=ingestion_id,
                    quarantine_table="obs.employees_quarantine",
                    quarantine_reason=reason,
                    source_row_number=row_num,
                    row_data=q_data,
                )

            # Promote: merge() on natural key (p_number, cost_center).
            # employee_id and created_at are immutable across updates.
            promoted = 0
            for sr in staging_rows:
                existing = helpers.query_one(
                    conn,
                    f"SELECT employee_id, created_at FROM obs.employees "
                    f"WHERE p_number = {ph} AND cost_center = {ph}",
                    (sr["p_number"], sr["cost_center"]),
                )
                employee_id = existing[0] if existing else str(uuid.uuid4())
                created_at = existing[1] if existing else now

                merge_data: dict[str, Any] = {
                    "employee_id": employee_id,
                    "p_number": sr["p_number"],
                    "company": sr["company"],
                    "entity": sr["entity"],
                    "cost_center": sr["cost_center"],
                    "department": sr["department"],
                    "last_name": sr["last_name"],
                    "first_name": sr["first_name"],
                    "last_hire_date": sr["last_hire_date"],
                    "salary_structure": sr["salary_structure"],
                    "aipeip_eligible": sr["aipeip_eligible"],
                    "title": sr.get("title"),
                    "annual_salary": sr["annual_salary"],
                    "home_state": sr["home_state"],
                    "termination_date": sr.get("termination_date"),
                    "work_state": sr["work_state"],
                    "office": sr["office"],
                    "workplace_flexibility": sr["workplace_flexibility"],
                    "management_production": sr["management_production"],
                    "job_grade": sr["job_grade"],
                    "full_time_part_time": sr["full_time_part_time"],
                    "hours_worked": sr.get("hours_worked"),
                    "ot_hours_worked": sr.get("ot_hours_worked"),
                    "fte": sr["fte"],
                    "is_active": True,
                    "last_ingestion_id": ingestion_id,
                    "created_at": created_at,
                    "updated_at": now,
                }
                helpers.merge(
                    conn,
                    "obs.employees",
                    key_cols=["p_number", "cost_center"],
                    data=merge_data,
                    immutable_cols=["employee_id", "created_at"],
                )
                promoted += 1

            if quarantined_rows == 0 and hard_rejected == 0:
                final_status = ps.STATUS_COMPLETED
            elif promoted == 0:
                final_status = ps.STATUS_QUARANTINED
            else:
                final_status = ps.STATUS_PARTIAL

            ps.update_ingestion_record(
                conn,
                ingestion_id=ingestion_id,
                status=final_status,
                total_rows=total_rows,
                valid_rows=valid_rows,
                quarantined_rows=quarantined_rows,
                rejected_rows=hard_rejected,
                promoted_rows=promoted,
                error_rate=error_rate,
            )
            audit_service.write_audit_event(
                conn,
                event_type=audit_service.AuditEvent.INGESTION_COMPLETED,
                user_id=actor_user_id,
                entity_type="ingestion",
                entity_id=ingestion_id,
                new_value={
                    "file_name": file_name,
                    "status": final_status,
                    "total_rows": total_rows,
                    "promoted_rows": promoted,
                    "quarantined_rows": quarantined_rows,
                },
            )
            if quarantined_rows > 0:
                audit_service.write_audit_event(
                    conn,
                    event_type=audit_service.AuditEvent.INGESTION_QUARANTINED,
                    user_id=actor_user_id,
                    entity_type="ingestion",
                    entity_id=ingestion_id,
                    new_value={"quarantined_rows": quarantined_rows},
                )

    # Quarantine sweep (own transaction)
    try:
        with helpers.transaction() as conn:
            swept = ps.sweep_pending_quarantine(
                conn, ingestion_id=ingestion_id, file_type=FILE_TYPE
            )
            if swept:
                logger.info("Swept %d pending quarantine records", swept)
    except Exception as exc:
        logger.warning("Quarantine sweep failed: %s", exc)

    # Non-fatal alerts (RULE 7)
    _send_alerts(
        file_name=file_name,
        ingestion_id=ingestion_id,
        total_rows=total_rows,
        error_rate=error_rate,
        quarantined_rows=quarantined_rows,
        rejected=(error_rate > 0.80),
    )


def _send_alerts(
    *,
    file_name: str,
    ingestion_id: str,
    total_rows: int,
    error_rate: float,
    quarantined_rows: int,
    rejected: bool,
) -> None:
    if rejected:
        try:
            pipeline_alert_service.send_pipeline_alert(
                event_type="REJECTED",
                file_name=file_name,
                file_type=FILE_TYPE,
                ingestion_id=ingestion_id,
                total_rows=total_rows,
                error_rate=error_rate,
            )
        except Exception as exc:
            logger.warning("Alert send failed: %s", exc)
    else:
        try:
            pipeline_alert_service.send_pipeline_alert(
                event_type="COMPLETED",
                file_name=file_name,
                file_type=FILE_TYPE,
                ingestion_id=ingestion_id,
                total_rows=total_rows,
                error_rate=error_rate,
            )
        except Exception as exc:
            logger.warning("Alert send failed: %s", exc)

        if quarantined_rows > 0:
            try:
                pipeline_alert_service.send_pipeline_alert(
                    event_type="QUARANTINED",
                    file_name=file_name,
                    file_type=FILE_TYPE,
                    ingestion_id=ingestion_id,
                    quarantined_rows=quarantined_rows,
                )
            except Exception as exc:
                logger.warning("Alert send failed: %s", exc)
