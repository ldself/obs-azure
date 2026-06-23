"""Shared pipeline utilities: SHA-256 dedup, ingestion lifecycle, quarantine writes,
and the automatic quarantine re-promotion sweep (OI-DI-05).

All functions that take a ``conn`` argument operate within the caller's open
transaction. The sole exception is :func:`check_duplicate`, which opens its own
short-lived read-only connection.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import datetime
from datetime import timezone
from typing import Any

from backend.app.db import engine
from backend.app.db import helpers
from backend.app.services import audit_service

logger = logging.getLogger("obs.pipeline.service")

# ---------------------------------------------------------------------------
# Quarantine reason constants (Data Integration Spec v1.8 §8.7)
# ---------------------------------------------------------------------------
QR_NON_USD = "NON_USD_CURRENCY"
QR_MISSING_COST_CENTER = "MISSING_COST_CENTER"
QR_MISSING_ACCOUNT = "MISSING_ACCOUNT"
QR_VALIDATION_FAILED = "VALIDATION_FAILED"

# Ingestion status constants (Data Integration Spec v1.8 §8.6)
STATUS_RUNNING = "RUNNING"
STATUS_COMPLETED = "COMPLETED"
STATUS_PARTIAL = "PARTIAL"
STATUS_QUARANTINED = "QUARANTINED"
STATUS_FAILED = "FAILED"

# File type constants
FILE_TYPE_ACTUALS = "ACTUALS"
FILE_TYPE_EMPLOYEES = "EMPLOYEES"
FILE_TYPE_CC_HIERARCHY = "COST_CENTER_HIERARCHY"
FILE_TYPE_ACCT_HIERARCHY = "ACCOUNT_HIERARCHY"

# Triggered-by constants
TRIGGERED_SCHEDULED = "SCHEDULED"
TRIGGERED_MANUAL = "MANUAL"

# System actor user_id used in audit events for pipeline operations
SYSTEM_USER_ID = "system:pipeline"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# SHA-256 deduplication
# ---------------------------------------------------------------------------

def compute_sha256(file_path: str) -> str:
    """Return the SHA-256 hex digest of the raw file bytes.

    Must be called on raw bytes BEFORE any parsing (audit-and-idempotency-
    reviewer Check 7 / RULE 10).
    """
    h = hashlib.sha256()
    with open(file_path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def check_duplicate(file_name: str, content_hash: str) -> bool:
    """Return True if a COMPLETED ingestion_control record exists for
    (file_name, content_hash). Opens its own short-lived read connection.

    A FAILED record for the same (file_name, content_hash) does NOT count as a
    duplicate — the corrected re-submission should proceed (Check 11).
    """
    ph = engine.placeholder()
    row = helpers.fetch_one(
        f"SELECT ingestion_id FROM obs.ingestion_control "
        f"WHERE file_name = {ph} AND content_hash = {ph} AND status = {ph}",
        (file_name, content_hash, STATUS_COMPLETED),
    )
    return row is not None


# ---------------------------------------------------------------------------
# Ingestion control record lifecycle
# ---------------------------------------------------------------------------

def create_ingestion_record(
    conn: Any,
    *,
    ingestion_id: str,
    file_name: str,
    content_hash: str,
    file_type: str,
    source_system: str,
    file_format: str,
    triggered_by: str,
) -> None:
    """INSERT an ingestion_control row with status=RUNNING (within caller txn)."""
    ph = engine.placeholder()
    helpers.exec_write(
        conn,
        f"INSERT INTO obs.ingestion_control "
        f"(ingestion_id, file_name, content_hash, source_system, file_type, "
        f" file_format, status, re_ingestion, triggered_by, started_at) "
        f"VALUES ({', '.join([ph] * 8)}, {ph}, {ph})",
        (
            ingestion_id,
            file_name,
            content_hash,
            source_system,
            file_type,
            file_format,
            STATUS_RUNNING,
            False,
            triggered_by,
            _utc_now(),
        ),
    )


def update_ingestion_record(
    conn: Any,
    *,
    ingestion_id: str,
    status: str,
    total_rows: int | None = None,
    valid_rows: int | None = None,
    quarantined_rows: int | None = None,
    rejected_rows: int | None = None,
    promoted_rows: int | None = None,
    error_rate: float | None = None,
    error_detail: str | None = None,
) -> None:
    """UPDATE the ingestion_control row to a terminal status (within caller txn)."""
    ph = engine.placeholder()
    helpers.exec_write(
        conn,
        f"UPDATE obs.ingestion_control SET "
        f"status = {ph}, total_rows = {ph}, valid_rows = {ph}, "
        f"quarantined_rows = {ph}, rejected_rows = {ph}, promoted_rows = {ph}, "
        f"error_rate = {ph}, completed_at = {ph}, error_detail = {ph} "
        f"WHERE ingestion_id = {ph}",
        (
            status,
            total_rows,
            valid_rows,
            quarantined_rows,
            rejected_rows,
            promoted_rows,
            error_rate,
            _utc_now(),
            error_detail,
            ingestion_id,
        ),
    )


# ---------------------------------------------------------------------------
# Quarantine record writes
# ---------------------------------------------------------------------------

def write_quarantine_record(
    conn: Any,
    *,
    ingestion_id: str,
    quarantine_table: str,
    quarantine_reason: str,
    source_row_number: int,
    row_data: dict[str, Any],
) -> str:
    """INSERT one quarantine row; return the generated quarantine_id (UUID).

    ``quarantine_table`` must be one of the four obs.*_quarantine tables.
    ``row_data`` contains all source-row columns to preserve.
    """
    quarantine_id = str(uuid.uuid4())
    ph = engine.placeholder()

    standard_cols = {
        "quarantine_id": quarantine_id,
        "ingestion_id": ingestion_id,
        "quarantine_reason": quarantine_reason,
        "quarantine_status": "PENDING",
        "resolved_by": None,
        "resolved_at": None,
        "source_row_number": source_row_number,
    }
    all_data = {**standard_cols, **row_data}
    col_list = ", ".join(all_data.keys())
    placeholders = ", ".join(ph for _ in all_data)
    helpers.exec_write(
        conn,
        f"INSERT INTO {quarantine_table} ({col_list}) VALUES ({placeholders})",
        list(all_data.values()),
    )
    return quarantine_id


# ---------------------------------------------------------------------------
# Automatic quarantine re-promotion sweep (OI-DI-05)
# ---------------------------------------------------------------------------

def sweep_pending_quarantine(
    conn: Any,
    *,
    ingestion_id: str,
    file_type: str,
    triggered_by_user_id: str = SYSTEM_USER_ID,
) -> int:
    """Re-promote PENDING quarantine rows whose blocking dimension is now resolved.

    Called at the end of every pipeline run (OI-DI-05). Hierarchy imports are the
    primary trigger because they add new dimension records that unblock quarantined
    actuals/employees.

    Returns the number of records successfully re-promoted.
    """
    ph = engine.placeholder()
    promoted = 0

    if file_type in (FILE_TYPE_CC_HIERARCHY, FILE_TYPE_ACTUALS, FILE_TYPE_EMPLOYEES):
        promoted += _sweep_actuals_missing_cost_center(
            conn, ph, ingestion_id, triggered_by_user_id
        )
        promoted += _sweep_employees_missing_cost_center(
            conn, ph, ingestion_id, triggered_by_user_id
        )

    if file_type in (FILE_TYPE_ACCT_HIERARCHY, FILE_TYPE_ACTUALS):
        promoted += _sweep_actuals_missing_account(
            conn, ph, ingestion_id, triggered_by_user_id
        )

    return promoted


def _sweep_actuals_missing_cost_center(
    conn: Any,
    ph: str,
    ingestion_id: str,
    user_id: str,
) -> int:
    """Re-promote obs.actuals_quarantine rows blocked by a missing cost center."""
    rows = helpers.query_all(
        conn,
        f"SELECT q.* FROM obs.actuals_quarantine q "
        f"INNER JOIN obs.cost_center_hierarchy_memberships m "
        f"  ON m.cost_center_code = q.cost_center AND m.is_active = TRUE "
        f"WHERE q.quarantine_status = {ph} AND q.quarantine_reason = {ph}",
        ("PENDING", QR_MISSING_COST_CENTER),
    )
    return _repromote_actuals(conn, ph, rows, ingestion_id, user_id)


def _sweep_actuals_missing_account(
    conn: Any,
    ph: str,
    ingestion_id: str,
    user_id: str,
) -> int:
    """Re-promote obs.actuals_quarantine rows blocked by a missing account."""
    rows = helpers.query_all(
        conn,
        f"SELECT q.* FROM obs.actuals_quarantine q "
        f"INNER JOIN obs.account_hierarchy_memberships m "
        f"  ON m.account = q.account AND m.sub_account = q.sub_account AND m.is_active = TRUE "
        f"WHERE q.quarantine_status = {ph} AND q.quarantine_reason = {ph}",
        ("PENDING", QR_MISSING_ACCOUNT),
    )
    return _repromote_actuals(conn, ph, rows, ingestion_id, user_id)


def _sweep_employees_missing_cost_center(
    conn: Any,
    ph: str,
    ingestion_id: str,
    user_id: str,
) -> int:
    """Re-promote obs.employees_quarantine rows blocked by a missing cost center."""
    rows = helpers.query_all(
        conn,
        f"SELECT q.* FROM obs.employees_quarantine q "
        f"INNER JOIN obs.cost_center_hierarchy_memberships m "
        f"  ON m.cost_center_code = q.cost_center AND m.is_active = TRUE "
        f"WHERE q.quarantine_status = {ph} AND q.quarantine_reason = {ph}",
        ("PENDING", QR_MISSING_COST_CENTER),
    )
    promoted = 0
    for row in rows:
        quarantine_id = row[0]
        _mark_quarantine_resolved(conn, ph, "obs.employees_quarantine", quarantine_id, ingestion_id)
        audit_service.write_audit_event(
            conn,
            event_type=audit_service.AuditEvent.QUARANTINE_REPROMOTED,
            user_id=user_id,
            entity_type="employees_quarantine",
            entity_id=quarantine_id,
            previous_value={"quarantine_status": row[3], "quarantine_reason": row[2]},
            new_value={"quarantine_status": "RESOLVED", "re_ingestion_id": ingestion_id},
        )
        promoted += 1
        logger.info("Re-promoted employees_quarantine %s", quarantine_id)
    return promoted


def _repromote_actuals(
    conn: Any,
    ph: str,
    rows: list[tuple[Any, ...]],
    ingestion_id: str,
    user_id: str,
) -> int:
    """Promote a batch of resolved actuals quarantine rows to obs.actuals."""
    # Column order in obs.actuals_quarantine (see schema):
    # 0=quarantine_id, 1=ingestion_id, 2=quarantine_reason, 3=quarantine_status,
    # 4=resolved_by, 5=resolved_at, 6=staging_id, 7=entity, 8=year, 9=month,
    # 10=cost_center, 11=account, 12=sub_account, 13=bonus_type, 14=amount,
    # 15=currency, 16=product, 17=distribution_channel, 18=stat_category,
    # 19=profit_center, 20=sender_cost_center, 21=assignment,
    # 22=functional_area, 23=partner_functional_area_text,
    # 24=source_file_name, 25=source_row_number, 26=staged_at
    promoted = 0
    for row in rows:
        quarantine_id = row[0]
        entity = row[7]
        year = row[8]
        month = row[9]
        cost_center = row[10]
        account = row[11]
        sub_account = row[12]
        bonus_type = row[13]
        amount = row[14]
        currency = row[15]
        product = row[16]
        distribution_channel = row[17]
        stat_category = row[18]
        profit_center = row[19]
        sender_cost_center = row[20]
        assignment = row[21]
        functional_area = row[22]
        partner_functional_area_text = row[23]

        # Soft-delete any active actuals with same natural key
        helpers.exec_write(
            conn,
            f"UPDATE obs.actuals SET is_deleted = TRUE, deleted_by_ingestion_id = {ph} "
            f"WHERE entity = {ph} AND year = {ph} AND month = {ph} "
            f"AND cost_center = {ph} AND account = {ph} AND sub_account = {ph} "
            f"AND is_deleted = FALSE",
            (ingestion_id, entity, year, month, cost_center, account, sub_account),
        )

        actuals_id = str(uuid.uuid4())
        helpers.exec_write(
            conn,
            f"INSERT INTO obs.actuals "
            f"(actuals_id, ingestion_id, entity, year, month, cost_center, account, "
            f" sub_account, bonus_type, amount, currency, product, distribution_channel, "
            f" stat_category, profit_center, sender_cost_center, assignment, "
            f" functional_area, partner_functional_area_text, "
            f" is_deleted, promoted_at) "
            f"VALUES ({', '.join([ph] * 21)})",
            (
                actuals_id, ingestion_id, entity, year, month, cost_center, account,
                sub_account, bonus_type, amount, currency, product, distribution_channel,
                stat_category, profit_center, sender_cost_center, assignment,
                functional_area, partner_functional_area_text,
                False, _utc_now(),
            ),
        )

        _mark_quarantine_resolved(conn, ph, "obs.actuals_quarantine", quarantine_id, ingestion_id)
        audit_service.write_audit_event(
            conn,
            event_type=audit_service.AuditEvent.QUARANTINE_REPROMOTED,
            user_id=user_id,
            entity_type="actuals_quarantine",
            entity_id=quarantine_id,
            previous_value={"quarantine_status": row[3], "quarantine_reason": row[2]},
            new_value={"quarantine_status": "RESOLVED", "re_ingestion_id": ingestion_id, "actuals_id": actuals_id},
        )
        promoted += 1
        logger.info("Re-promoted actuals_quarantine %s → actuals %s", quarantine_id, actuals_id)
    return promoted


def _mark_quarantine_resolved(
    conn: Any,
    ph: str,
    quarantine_table: str,
    quarantine_id: str,
    resolved_by: str,
) -> None:
    helpers.exec_write(
        conn,
        f"UPDATE {quarantine_table} SET quarantine_status = {ph}, "
        f"resolved_by = {ph}, resolved_at = {ph} "
        f"WHERE quarantine_id = {ph}",
        ("RESOLVED", resolved_by, _utc_now(), quarantine_id),
    )
