"""Actuals ingestion pipeline (Phase 2).

Processes actuals_* files from the landing zone:
  1. Compute SHA-256 on raw bytes; skip if already COMPLETED (RULE 10).
  2. Parse CSV or Excel rows.
  3. Validate each row: format/type, non-USD rejection (OI-DI-03), and
     referential integrity against cost_center_hierarchy_memberships and
     expense_accounts.
  4. Reject the file entirely if error_rate > 0.80.
  5. Stage valid rows to obs.actuals_staging.
  6. Promote staged rows to obs.actuals via soft-delete + INSERT (the
     is_deleted versioning pattern). merge() is not used here; file-level
     SHA-256 dedup provides RULE 10 idempotency.
  7. Write INGESTION_COMPLETED or INGESTION_REJECTED audit event (RULE 6).
  8. Send non-fatal Teams + Email alerts (OI-DI-04, RULE 7).
"""

from __future__ import annotations

import csv
import logging
import os
import shutil
import uuid
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

logger = logging.getLogger("obs.pipeline.actuals")

SOURCE_SYSTEM = "ACCOUNTING"
FILE_TYPE = ps.FILE_TYPE_ACTUALS

# Required fields and their expected Python types
_REQUIRED_FIELDS: list[tuple[str, type]] = [
    ("entity", int),
    ("year", int),
    ("month", int),
    ("cost_center", str),
    ("account", str),
    ("sub_account", str),
    ("amount", float),
    ("currency", str),
    ("product", str),
    ("profit_center", str),
    ("sender_cost_center", str),
]

_OPTIONAL_FIELDS = [
    "bonus_type",
    "distribution_channel",
    "stat_category",
    "assignment",
    "functional_area",
    "partner_functional_area_text",
]


def run(
    file_path: str | None = None,
    *,
    actor_user_id: str | None = None,
    triggered_by: str | None = None,
) -> str | None:
    """Entry point invoked by the Makefile and the Azure Function trigger.

    Returns the ingestion_id on success, None if the file was skipped (duplicate).
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
        logger.info("No actuals file found in landing zone")
        return None

    file_name = Path(resolved_path).name
    logger.info("actuals_pipeline: processing %s", file_name)

    # Step 2: SHA-256 on raw bytes (must be before any parsing — Check 7)
    content_hash = ps.compute_sha256(resolved_path)

    # Fast-path dedup optimisation (avoids opening a transaction for obvious duplicates).
    # The authoritative atomic guard is the ON CONFLICT DO NOTHING inside _process().
    if ps.check_duplicate(file_name, content_hash):
        logger.info("Skipping duplicate file %s (hash=%s)", file_name, content_hash)
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
        logger.exception("actuals_pipeline: unhandled error for %s", file_name)
        _move_file(resolved_path, error_path, file_name)
        raise


def _resolve_file(file_path: str | None, landing_zone: str) -> str | None:
    if file_path:
        return file_path
    actuals_dir = Path(landing_zone) / "actuals"
    if not actuals_dir.exists():
        actuals_dir = Path(landing_zone)
    candidates = sorted(
        p for p in actuals_dir.iterdir()
        if p.is_file() and p.name.startswith("actuals_")
    )
    return str(candidates[0]) if candidates else None


def _move_file(src: str, dest_dir: str, file_name: str) -> None:
    try:
        dest = Path(dest_dir)
        dest.mkdir(parents=True, exist_ok=True)
        shutil.move(src, dest / file_name)
    except Exception as exc:
        logger.warning("Could not move %s to %s: %s", file_name, dest_dir, exc)


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


def _validate_row(
    row: dict[str, str],
    row_number: int,
    valid_cost_centers: set[str],
    valid_accounts: set[tuple[str, str]],
) -> tuple[dict[str, Any] | None, str | None]:
    """Return (typed_row, quarantine_reason) or (None, None) for hard format errors."""
    typed: dict[str, Any] = {}

    # Type coercion for required fields
    try:
        typed["entity"] = int(row.get("entity", ""))
        typed["year"] = int(row.get("year", ""))
        typed["month"] = int(row.get("month", ""))
        typed["amount"] = float(row.get("amount", ""))
    except (ValueError, TypeError):
        return None, ps.QR_VALIDATION_FAILED

    for field, _ in _REQUIRED_FIELDS:
        if field in ("entity", "year", "month", "amount"):
            continue
        val = row.get(field, "").strip()
        if not val:
            return None, ps.QR_VALIDATION_FAILED
        typed[field] = val

    # month range check
    if not (1 <= typed["month"] <= 12):
        return None, ps.QR_VALIDATION_FAILED

    # Optional fields
    for field in _OPTIONAL_FIELDS:
        typed[field] = row.get(field, "").strip() or None

    # Non-USD rejection (OI-DI-03)
    if typed["currency"].upper() != "USD":
        return typed, ps.QR_NON_USD

    # Referential integrity: cost_center
    if typed["cost_center"] not in valid_cost_centers:
        return typed, ps.QR_MISSING_COST_CENTER

    # Referential integrity: account + sub_account
    if (typed["account"], typed["sub_account"]) not in valid_accounts:
        return typed, ps.QR_MISSING_ACCOUNT

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

    # Load dimension lookup sets (outside transaction — read-only)
    valid_cost_centers: set[str] = {
        row[0]
        for row in helpers.fetch_all(
            "SELECT DISTINCT cost_center_code FROM obs.cost_center_hierarchy_memberships WHERE is_active = TRUE"
        )
    }
    valid_accounts: set[tuple[str, str]] = {
        (row[0], row[1])
        for row in helpers.fetch_all(
            "SELECT account, sub_account FROM obs.expense_accounts WHERE is_active = TRUE"
        )
    }

    raw_rows = _parse_rows(resolved_path, file_format)
    total_rows = len(raw_rows)

    staging_rows: list[dict[str, Any]] = []
    quarantine_rows: list[tuple[dict[str, Any] | None, str, int]] = []  # (typed, reason, row_num)
    hard_rejected = 0

    for row_num, raw in enumerate(raw_rows, start=1):
        typed, reason = _validate_row(raw, row_num, valid_cost_centers, valid_accounts)
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

        # File-level rejection: > 80% error rate (DI Spec v1.8 §8.6)
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
                error_detail=f"File rejected: error_rate={error_rate:.2%} exceeds 80% threshold",
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
            # Commit the rejection record, then alert non-fatally
            # (alert is outside the transaction per RULE 7 pattern — call after commit)

        else:
            staged_at = datetime.now(timezone.utc)

            # Stage valid rows. ON CONFLICT DO NOTHING guards the unique constraint
            # on (ingestion_id, source_row_number) — harmless for first-time runs,
            # prevents duplicate staging rows if the transaction is somehow replayed.
            for sr in staging_rows:
                staging_id = str(uuid.uuid4())
                helpers.exec_write(
                    conn,
                    f"INSERT INTO obs.actuals_staging "
                    f"(ingestion_id, staging_id, entity, year, month, cost_center, "
                    f" account, sub_account, bonus_type, amount, currency, product, "
                    f" distribution_channel, stat_category, profit_center, "
                    f" sender_cost_center, assignment, functional_area, "
                    f" partner_functional_area_text, source_file_name, "
                    f" source_row_number, staged_at) "
                    f"VALUES ({', '.join([ph] * 22)}) "
                    f"ON CONFLICT (ingestion_id, source_row_number) DO NOTHING",
                    (
                        ingestion_id, staging_id,
                        sr["entity"], sr["year"], sr["month"],
                        sr["cost_center"], sr["account"], sr["sub_account"],
                        sr.get("bonus_type"), sr["amount"], sr["currency"],
                        sr["product"], sr.get("distribution_channel"),
                        sr.get("stat_category"), sr["profit_center"],
                        sr["sender_cost_center"], sr.get("assignment"),
                        sr.get("functional_area"), sr.get("partner_functional_area_text"),
                        file_name, sr["source_row_number"], staged_at,
                    ),
                )

            # Quarantine invalid rows
            for typed, reason, row_num in quarantine_rows:
                row_data = typed or {}
                row_data.setdefault("source_file_name", file_name)
                ps.write_quarantine_record(
                    conn,
                    ingestion_id=ingestion_id,
                    quarantine_table="obs.actuals_quarantine",
                    quarantine_reason=reason,
                    source_row_number=row_num,
                    row_data={k: v for k, v in row_data.items() if k != "source_row_number"},
                )

            # Promote: soft-delete existing active actuals with same natural key,
            # then insert new records (versioning via is_deleted, not merge()).
            promoted = 0
            for sr in staging_rows:
                helpers.exec_write(
                    conn,
                    f"UPDATE obs.actuals SET is_deleted = TRUE, "
                    f"deleted_by_ingestion_id = {ph} "
                    f"WHERE entity = {ph} AND year = {ph} AND month = {ph} "
                    f"AND cost_center = {ph} AND account = {ph} "
                    f"AND sub_account = {ph} AND is_deleted = FALSE",
                    (
                        ingestion_id,
                        sr["entity"], sr["year"], sr["month"],
                        sr["cost_center"], sr["account"], sr["sub_account"],
                    ),
                )
                actuals_id = str(uuid.uuid4())
                # Soft-delete versioning pattern (DI Spec v1.8 §8.2): the UPDATE
                # above marks prior active rows deleted before this INSERT.
                # File-level SHA-256 dedup (RULE 10) prevents double-processing;
                # ON CONFLICT (actuals_id) DO NOTHING is a PK-level safety net.
                helpers.exec_write(
                    conn,
                    f"INSERT INTO obs.actuals "
                    f"(actuals_id, ingestion_id, entity, year, month, cost_center, "
                    f" account, sub_account, bonus_type, amount, currency, product, "
                    f" distribution_channel, stat_category, profit_center, "
                    f" sender_cost_center, assignment, functional_area, "
                    f" partner_functional_area_text, is_deleted, promoted_at) "
                    f"VALUES ({', '.join([ph] * 21)}) "
                    f"ON CONFLICT (actuals_id) DO NOTHING",
                    (
                        actuals_id, ingestion_id,
                        sr["entity"], sr["year"], sr["month"],
                        sr["cost_center"], sr["account"], sr["sub_account"],
                        sr.get("bonus_type"), sr["amount"], sr["currency"],
                        sr["product"], sr.get("distribution_channel"),
                        sr.get("stat_category"), sr["profit_center"],
                        sr["sender_cost_center"], sr.get("assignment"),
                        sr.get("functional_area"), sr.get("partner_functional_area_text"),
                        False, staged_at,
                    ),
                )
                promoted += 1

            # Determine final status
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

    # Auto re-promotion sweep (outside the main transaction — opens its own)
    try:
        with helpers.transaction() as conn:
            swept = ps.sweep_pending_quarantine(
                conn,
                ingestion_id=ingestion_id,
                file_type=FILE_TYPE,
            )
            if swept:
                logger.info("Swept %d pending quarantine records after actuals ingestion", swept)
    except Exception as exc:
        logger.warning("Quarantine sweep failed for ingestion %s: %s", ingestion_id, exc)

    # Non-fatal alerts (RULE 7)
    if error_rate > 0.80:
        try:
            pipeline_alert_service.send_pipeline_alert(
                event_type="REJECTED",
                file_name=file_name,
                file_type=FILE_TYPE,
                ingestion_id=ingestion_id,
                total_rows=total_rows,
                error_rate=error_rate,
                error_detail=f"error_rate={error_rate:.2%} exceeds 80% threshold",
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
