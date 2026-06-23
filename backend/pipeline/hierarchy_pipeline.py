"""Hierarchy ingestion pipeline (Phase 2).

Handles both cost_center_hierarchy_* and account_hierarchy_* files from the
landing zone (blob-trigger in Azure, manual/scan in local dev).

Cost center hierarchy re-imports are blocked once the initial seed is present
(OI-DI-06). Account hierarchy imports are never blocked.

Each row in the input file represents one hierarchy membership record. The
pipeline derives unique nodes from the level codes and upserts both nodes and
memberships via merge() (RULE 10). After promotion the pipeline triggers the
automatic quarantine sweep so that pending actuals/employees quarantine records
whose blocking dimension is now resolved are re-promoted (OI-DI-05).
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


logger = logging.getLogger("obs.pipeline.hierarchy")

SOURCE_SYSTEM_HIERARCHY = "ACCOUNTING"

# Maximum number of level columns supported in hierarchy files
_MAX_LEVELS = 6


def run(
    file_path: str | None = None,
    file_type: str | None = None,
    *,
    actor_user_id: str | None = None,
    triggered_by: str | None = None,
) -> str | None:
    """Entry point invoked by the Makefile and the Azure Function trigger.

    Returns the ingestion_id on success, None if the file was skipped.
    ``file_type`` may be ps.FILE_TYPE_CC_HIERARCHY or ps.FILE_TYPE_ACCT_HIERARCHY;
    if omitted it is inferred from the file name prefix.
    ``actor_user_id`` defaults to SYSTEM_USER_ID for scheduled runs; pass the
    authenticated user's ID for manual triggers (RULE 6).
    ``triggered_by`` defaults to TRIGGERED_SCHEDULED.
    """
    resolved_actor = actor_user_id or ps.SYSTEM_USER_ID
    resolved_triggered_by = triggered_by or ps.TRIGGERED_SCHEDULED

    landing_zone = os.environ.get("LANDING_ZONE_PATH", "./local-data/landing-zone")
    archive_path = os.environ.get("ARCHIVE_PATH", "./local-data/archive")
    error_path = os.environ.get("ERROR_PATH", "./local-data/error")

    resolved_path, resolved_type = _resolve_file(file_path, file_type, landing_zone)
    if resolved_path is None:
        logger.info("No hierarchy file found in landing zone")
        return None

    file_name = Path(resolved_path).name
    logger.info("hierarchy_pipeline: processing %s (%s)", file_name, resolved_type)

    content_hash = ps.compute_sha256(resolved_path)
    if ps.check_duplicate(file_name, content_hash):
        logger.info("Skipping duplicate hierarchy file %s", file_name)
        return None

    file_format = "EXCEL" if resolved_path.lower().endswith((".xlsx", ".xls")) else "CSV"
    ingestion_id = str(uuid.uuid4())

    try:
        _process(
            resolved_path=resolved_path,
            file_name=file_name,
            content_hash=content_hash,
            file_format=file_format,
            file_type=resolved_type,
            ingestion_id=ingestion_id,
            actor_user_id=resolved_actor,
            triggered_by=resolved_triggered_by,
        )
        _move_file(resolved_path, archive_path, file_name)
        return ingestion_id
    except Exception:
        logger.exception("hierarchy_pipeline: unhandled error for %s", file_name)
        _move_file(resolved_path, error_path, file_name)
        raise


def _resolve_file(
    file_path: str | None,
    file_type: str | None,
    landing_zone: str,
) -> tuple[str | None, str]:
    if file_path:
        if file_type:
            return file_path, file_type
        name = Path(file_path).name.lower()
        if name.startswith("cost_center_hierarchy"):
            return file_path, ps.FILE_TYPE_CC_HIERARCHY
        if name.startswith("account_hierarchy"):
            return file_path, ps.FILE_TYPE_ACCT_HIERARCHY
        return file_path, ps.FILE_TYPE_CC_HIERARCHY

    base = Path(landing_zone)
    for prefix, ftype in [
        ("cost_center_hierarchy_", ps.FILE_TYPE_CC_HIERARCHY),
        ("account_hierarchy_", ps.FILE_TYPE_ACCT_HIERARCHY),
    ]:
        candidates = sorted(
            p for p in (base / "hierarchies" if (base / "hierarchies").exists() else base).iterdir()
            if p.is_file() and p.name.startswith(prefix)
        )
        if candidates:
            return str(candidates[0]), ftype

    return None, ps.FILE_TYPE_CC_HIERARCHY


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


def _process(
    *,
    resolved_path: str,
    file_name: str,
    content_hash: str,
    file_format: str,
    file_type: str,
    ingestion_id: str,
    actor_user_id: str,
    triggered_by: str,
) -> None:
    now = datetime.now(timezone.utc)

    raw_rows = _parse_rows(resolved_path, file_format)
    total_rows = len(raw_rows)

    if file_type == ps.FILE_TYPE_CC_HIERARCHY:
        _process_cc_hierarchy(
            raw_rows=raw_rows,
            file_name=file_name,
            content_hash=content_hash,
            file_format=file_format,
            ingestion_id=ingestion_id,
            total_rows=total_rows,
            now=now,
            actor_user_id=actor_user_id,
            triggered_by=triggered_by,
        )
    else:
        _process_acct_hierarchy(
            raw_rows=raw_rows,
            file_name=file_name,
            content_hash=content_hash,
            file_format=file_format,
            ingestion_id=ingestion_id,
            total_rows=total_rows,
            now=now,
            actor_user_id=actor_user_id,
            triggered_by=triggered_by,
        )


# ---------------------------------------------------------------------------
# Cost center hierarchy
# ---------------------------------------------------------------------------

def _cc_hierarchy_is_seeded(conn: Any) -> bool:
    """Return True if any active CC hierarchy nodes exist (OI-DI-06).
    Runs on the caller's connection to avoid opening a second DuckDB connection.
    """
    row = helpers.query_one(
        conn,
        "SELECT COUNT(*) FROM obs.cost_center_hierarchy_nodes WHERE is_active = TRUE",
    )
    return (row[0] if row else 0) > 0


def _validate_cc_row(row: dict[str, str], row_num: int) -> tuple[dict[str, Any] | None, str | None]:
    required = ["hierarchy_id", "cost_center_code", "cost_center_name", "level_1_code"]
    for field in required:
        if not row.get(field, "").strip():
            return None, ps.QR_VALIDATION_FAILED

    try:
        max_depth = int(row.get("max_depth", 1))
    except (ValueError, TypeError):
        max_depth = 1

    typed: dict[str, Any] = {
        "hierarchy_id": row["hierarchy_id"].strip(),
        "hierarchy_name": row.get("hierarchy_name", "").strip() or row["hierarchy_id"].strip(),
        "cost_center_code": row["cost_center_code"].strip(),
        "cost_center_name": row["cost_center_name"].strip(),
        "max_depth": max_depth,
    }
    for i in range(1, _MAX_LEVELS + 1):
        typed[f"level_{i}_code"] = row.get(f"level_{i}_code", "").strip() or None

    return typed, None


def _process_cc_hierarchy(
    *,
    raw_rows: list[dict[str, str]],
    file_name: str,
    content_hash: str,
    file_format: str,
    ingestion_id: str,
    total_rows: int,
    now: datetime,
    actor_user_id: str,
    triggered_by: str,
) -> None:
    ph = engine.placeholder()

    with helpers.transaction() as conn:
        if not ps.create_ingestion_record(
            conn,
            ingestion_id=ingestion_id,
            file_name=file_name,
            content_hash=content_hash,
            file_type=ps.FILE_TYPE_CC_HIERARCHY,
            source_system=SOURCE_SYSTEM_HIERARCHY,
            file_format=file_format,
            triggered_by=triggered_by,
        ):
            logger.info("Atomic dedup: COMPLETED record exists for %s, skipping", file_name)
            return

        if _cc_hierarchy_is_seeded(conn):
            ps.update_ingestion_record(
                conn,
                ingestion_id=ingestion_id,
                status=ps.STATUS_FAILED,
                total_rows=total_rows,
                valid_rows=0,
                quarantined_rows=0,
                rejected_rows=total_rows,
                promoted_rows=0,
                error_rate=1.0,
                error_detail="Cost center hierarchy re-import blocked (OI-DI-06): active nodes already exist",
            )
            audit_service.write_audit_event(
                conn,
                event_type=audit_service.AuditEvent.INGESTION_REJECTED,
                user_id=actor_user_id,
                entity_type="ingestion",
                entity_id=ingestion_id,
                new_value={"file_name": file_name, "reason": "OI-DI-06 blocked re-import"},
            )
            logger.warning(
                "CC hierarchy re-import blocked (OI-DI-06) for %s ingestion_id=%s",
                file_name, ingestion_id,
            )
            return

        valid_rows: list[tuple[int, dict[str, Any]]] = []
        quarantine_rows: list[tuple[int, dict[str, Any] | None, str]] = []

        for row_num, raw in enumerate(raw_rows, start=1):
            typed, reason = _validate_cc_row(raw, row_num)
            if reason:
                quarantine_rows.append((row_num, typed, reason))
            else:
                valid_rows.append((row_num, typed))

        hard_rejected = sum(1 for _, t, _ in quarantine_rows if t is None)
        quarantined_count = len(quarantine_rows)
        valid_count = len(valid_rows)
        error_rate = quarantined_count / total_rows if total_rows > 0 else 0.0

        if error_rate > 0.80:
            ps.update_ingestion_record(
                conn,
                ingestion_id=ingestion_id,
                status=ps.STATUS_FAILED,
                total_rows=total_rows,
                valid_rows=valid_count,
                quarantined_rows=quarantined_count,
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
                new_value={"file_name": file_name, "error_rate": error_rate},
            )
            return

        # Quarantine invalid rows
        for row_num, typed, reason in quarantine_rows:
            quarantine_data = typed or {}
            ps.write_quarantine_record(
                conn,
                ingestion_id=ingestion_id,
                quarantine_table="obs.cost_center_hierarchy_quarantine",
                quarantine_reason=reason,
                source_row_number=row_num,
                row_data=quarantine_data,
            )

        # Derive unique nodes from membership data
        seen_nodes: set[tuple[str, str]] = set()
        nodes_to_upsert: list[dict[str, Any]] = []

        for _, typed in valid_rows:
            hierarchy_id = typed["hierarchy_id"]
            for depth, i in enumerate(range(1, _MAX_LEVELS + 1), start=1):
                code = typed.get(f"level_{i}_code")
                if not code:
                    break
                key = (hierarchy_id, code)
                if key not in seen_nodes:
                    seen_nodes.add(key)
                    parent_code = typed.get(f"level_{i - 1}_code") if i > 1 else None
                    nodes_to_upsert.append({
                        "node_id": str(uuid.uuid4()),
                        "hierarchy_id": hierarchy_id,
                        "node_code": code,
                        "node_name": code,
                        "node_depth": depth,
                        "parent_node_code": parent_code,
                        "is_active": True,
                        "last_ingestion_id": ingestion_id,
                        "created_at": now,
                        "updated_at": now,
                    })

        # Upsert nodes (node_id is immutable once created)
        existing_node_ids: dict[tuple[str, str], str] = {}
        for n in nodes_to_upsert:
            key = (n["hierarchy_id"], n["node_code"])
            existing = helpers.query_one(
                conn,
                f"SELECT node_id FROM obs.cost_center_hierarchy_nodes "
                f"WHERE hierarchy_id = {ph} AND node_code = {ph}",
                (n["hierarchy_id"], n["node_code"]),
            )
            if existing:
                n["node_id"] = existing[0]
                existing_node_ids[key] = existing[0]
            helpers.merge(
                conn,
                "obs.cost_center_hierarchy_nodes",
                key_cols=["hierarchy_id", "node_code"],
                data=n,
                immutable_cols=["node_id", "created_at"],
            )

        promoted = 0
        for _, typed in valid_rows:
            existing_m = helpers.query_one(
                conn,
                f"SELECT membership_id, created_at FROM obs.cost_center_hierarchy_memberships "
                f"WHERE hierarchy_id = {ph} AND cost_center_code = {ph}",
                (typed["hierarchy_id"], typed["cost_center_code"]),
            )
            membership_id = existing_m[0] if existing_m else str(uuid.uuid4())
            created_at = existing_m[1] if existing_m else now

            membership_data: dict[str, Any] = {
                "membership_id": membership_id,
                "hierarchy_id": typed["hierarchy_id"],
                "cost_center_code": typed["cost_center_code"],
                "cost_center_name": typed["cost_center_name"],
                "level_1_code": typed.get("level_1_code"),
                "level_2_code": typed.get("level_2_code"),
                "level_3_code": typed.get("level_3_code"),
                "level_4_code": typed.get("level_4_code"),
                "level_5_code": typed.get("level_5_code"),
                "level_6_code": typed.get("level_6_code"),
                "max_depth": typed["max_depth"],
                "is_active": True,
                "last_ingestion_id": ingestion_id,
                "created_at": created_at,
                "updated_at": now,
            }
            helpers.merge(
                conn,
                "obs.cost_center_hierarchy_memberships",
                key_cols=["hierarchy_id", "cost_center_code"],
                data=membership_data,
                immutable_cols=["membership_id", "created_at"],
            )
            promoted += 1

        if quarantined_count == 0:
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
            valid_rows=valid_count,
            quarantined_rows=quarantined_count,
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
                "promoted_rows": promoted,
                "quarantined_rows": quarantined_count,
            },
        )

    # Quarantine sweep (own transaction) — critical: CC hierarchy imports resolve pending quarantine
    try:
        with helpers.transaction() as conn:
            swept = ps.sweep_pending_quarantine(
                conn, ingestion_id=ingestion_id, file_type=ps.FILE_TYPE_CC_HIERARCHY
            )
            if swept:
                logger.info("CC hierarchy sweep re-promoted %d quarantine records", swept)
    except Exception as exc:
        logger.warning("Quarantine sweep failed: %s", exc)

    _send_hierarchy_alerts(
        file_name=file_name,
        file_type=ps.FILE_TYPE_CC_HIERARCHY,
        ingestion_id=ingestion_id,
        total_rows=total_rows,
        error_rate=error_rate,
        quarantined_rows=quarantined_count,
        rejected=(error_rate > 0.80),
    )


# ---------------------------------------------------------------------------
# Account hierarchy
# ---------------------------------------------------------------------------

def _validate_acct_node_row(row: dict[str, str]) -> tuple[dict[str, Any] | None, str | None]:
    required = ["hierarchy_id", "account", "sub_account", "account_name", "level_1_code"]
    for field in required:
        if not row.get(field, "").strip():
            return None, ps.QR_VALIDATION_FAILED

    try:
        max_depth = int(row.get("max_depth", 1))
    except (ValueError, TypeError):
        max_depth = 1

    is_personnel_expense_raw = row.get("is_personnel_expense", "").strip().lower()
    is_personnel_expense = is_personnel_expense_raw in ("true", "1", "yes")

    typed: dict[str, Any] = {
        "hierarchy_id": row["hierarchy_id"].strip(),
        "hierarchy_name": row.get("hierarchy_name", "").strip() or row["hierarchy_id"].strip(),
        "account": row["account"].strip(),
        "sub_account": row["sub_account"].strip(),
        "account_name": row["account_name"].strip(),
        "max_depth": max_depth,
        "is_personnel_expense": is_personnel_expense,
        "personnel_expense_source": row.get("personnel_expense_source", "").strip() or None,
    }
    for i in range(1, _MAX_LEVELS + 1):
        typed[f"level_{i}_code"] = row.get(f"level_{i}_code", "").strip() or None

    return typed, None


def _process_acct_hierarchy(
    *,
    raw_rows: list[dict[str, str]],
    file_name: str,
    content_hash: str,
    file_format: str,
    ingestion_id: str,
    total_rows: int,
    now: datetime,
    actor_user_id: str,
    triggered_by: str,
) -> None:
    ph = engine.placeholder()

    valid_rows: list[tuple[int, dict[str, Any]]] = []
    quarantine_rows: list[tuple[int, dict[str, Any] | None, str]] = []

    for row_num, raw in enumerate(raw_rows, start=1):
        typed, reason = _validate_acct_node_row(raw)
        if reason:
            quarantine_rows.append((row_num, typed, reason))
        else:
            valid_rows.append((row_num, typed))

    hard_rejected = sum(1 for _, t, _ in quarantine_rows if t is None)
    quarantined_count = len(quarantine_rows)
    valid_count = len(valid_rows)
    error_rate = quarantined_count / total_rows if total_rows > 0 else 0.0

    with helpers.transaction() as conn:
        if not ps.create_ingestion_record(
            conn,
            ingestion_id=ingestion_id,
            file_name=file_name,
            content_hash=content_hash,
            file_type=ps.FILE_TYPE_ACCT_HIERARCHY,
            source_system=SOURCE_SYSTEM_HIERARCHY,
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
                valid_rows=valid_count,
                quarantined_rows=quarantined_count,
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
                new_value={"file_name": file_name, "error_rate": error_rate},
            )
            return

        # Quarantine invalid rows
        for row_num, typed, reason in quarantine_rows:
            ps.write_quarantine_record(
                conn,
                ingestion_id=ingestion_id,
                quarantine_table="obs.account_hierarchy_quarantine",
                quarantine_reason=reason,
                source_row_number=row_num,
                row_data=typed or {},
            )

        # Derive unique nodes from membership rows
        seen_nodes: set[tuple[str, str]] = set()
        nodes_to_upsert: list[dict[str, Any]] = []

        for _, typed in valid_rows:
            hierarchy_id = typed["hierarchy_id"]
            for depth, i in enumerate(range(1, _MAX_LEVELS + 1), start=1):
                code = typed.get(f"level_{i}_code")
                if not code:
                    break
                key = (hierarchy_id, code)
                if key not in seen_nodes:
                    seen_nodes.add(key)
                    parent_code = typed.get(f"level_{i - 1}_code") if i > 1 else None
                    nodes_to_upsert.append({
                        "node_id": str(uuid.uuid4()),
                        "hierarchy_id": hierarchy_id,
                        "node_code": code,
                        "node_name": code,
                        "node_depth": depth,
                        "parent_node_code": parent_code,
                        "is_active": True,
                        "last_ingestion_id": ingestion_id,
                        "created_at": now,
                        "updated_at": now,
                        "is_personnel_expense": False,
                        "personnel_expense_source": None,
                    })

        for n in nodes_to_upsert:
            existing = helpers.query_one(
                conn,
                f"SELECT node_id FROM obs.account_hierarchy_nodes "
                f"WHERE hierarchy_id = {ph} AND node_code = {ph}",
                (n["hierarchy_id"], n["node_code"]),
            )
            if existing:
                n["node_id"] = existing[0]
            helpers.merge(
                conn,
                "obs.account_hierarchy_nodes",
                key_cols=["hierarchy_id", "node_code"],
                data=n,
                immutable_cols=["node_id", "created_at"],
            )

        promoted = 0
        for _, typed in valid_rows:
            existing_m = helpers.query_one(
                conn,
                f"SELECT membership_id, created_at FROM obs.account_hierarchy_memberships "
                f"WHERE hierarchy_id = {ph} AND account = {ph} AND sub_account = {ph}",
                (typed["hierarchy_id"], typed["account"], typed["sub_account"]),
            )
            membership_id = existing_m[0] if existing_m else str(uuid.uuid4())
            created_at = existing_m[1] if existing_m else now

            membership_data: dict[str, Any] = {
                "membership_id": membership_id,
                "hierarchy_id": typed["hierarchy_id"],
                "account": typed["account"],
                "sub_account": typed["sub_account"],
                "account_name": typed["account_name"],
                "level_1_code": typed.get("level_1_code"),
                "level_2_code": typed.get("level_2_code"),
                "level_3_code": typed.get("level_3_code"),
                "level_4_code": typed.get("level_4_code"),
                "level_5_code": typed.get("level_5_code"),
                "level_6_code": typed.get("level_6_code"),
                "max_depth": typed["max_depth"],
                "is_active": True,
                "last_ingestion_id": ingestion_id,
                "created_at": created_at,
                "updated_at": now,
            }
            helpers.merge(
                conn,
                "obs.account_hierarchy_memberships",
                key_cols=["hierarchy_id", "account", "sub_account"],
                data=membership_data,
                immutable_cols=["membership_id", "created_at"],
            )
            promoted += 1

        if quarantined_count == 0:
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
            valid_rows=valid_count,
            quarantined_rows=quarantined_count,
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
                "promoted_rows": promoted,
                "quarantined_rows": quarantined_count,
            },
        )

    # Quarantine sweep (own transaction) — account hierarchy imports resolve pending quarantine
    try:
        with helpers.transaction() as conn:
            swept = ps.sweep_pending_quarantine(
                conn, ingestion_id=ingestion_id, file_type=ps.FILE_TYPE_ACCT_HIERARCHY
            )
            if swept:
                logger.info("Account hierarchy sweep re-promoted %d quarantine records", swept)
    except Exception as exc:
        logger.warning("Quarantine sweep failed: %s", exc)

    _send_hierarchy_alerts(
        file_name=file_name,
        file_type=ps.FILE_TYPE_ACCT_HIERARCHY,
        ingestion_id=ingestion_id,
        total_rows=total_rows,
        error_rate=error_rate,
        quarantined_rows=quarantined_count,
        rejected=(error_rate > 0.80),
    )


def _send_hierarchy_alerts(
    *,
    file_name: str,
    file_type: str,
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
                file_type=file_type,
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
                file_type=file_type,
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
                    file_type=file_type,
                    ingestion_id=ingestion_id,
                    quarantined_rows=quarantined_rows,
                )
            except Exception as exc:
                logger.warning("Alert send failed: %s", exc)
