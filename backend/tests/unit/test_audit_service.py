"""Unit tests for the append-only audit service (RULE 6; Security Spec v1.4 §8)."""

from __future__ import annotations

import json
from pathlib import Path

from backend.app.db import engine
from backend.app.db import helpers
from backend.app.services import audit_service
from backend.app.services.audit_service import AuditEvent
from backend.app.services.audit_service import Outcome


def test_write_audit_event_persists_row(temp_db: Path) -> None:
    with helpers.transaction() as conn:
        event_id = audit_service.write_audit_event(
            conn,
            event_type=AuditEvent.USER_PROVISIONED,
            user_id="admin-1",
            entity_type="user",
            entity_id="u-9",
            new_value={"is_active": True},
        )
    row = helpers.fetch_one(
        "SELECT event_id, event_type, user_id, entity_id, new_value, outcome "
        "FROM obs.audit_log WHERE event_id = ?",
        (event_id,),
    )
    assert row is not None
    assert row[1] == AuditEvent.USER_PROVISIONED
    assert row[2] == "admin-1"
    assert row[5] == Outcome.SUCCESS
    assert json.loads(row[4]) == {"is_active": True}


def test_previous_and_new_values_are_json(temp_db: Path) -> None:
    with helpers.transaction() as conn:
        event_id = audit_service.write_audit_event(
            conn,
            event_type=AuditEvent.CAPABILITY_FLAG_CHANGED,
            user_id="admin-1",
            previous_value={"is_system_modeler": False},
            new_value={"is_system_modeler": True},
        )
    row = helpers.fetch_one(
        "SELECT previous_value, new_value FROM obs.audit_log WHERE event_id = ?",
        (event_id,),
    )
    assert row is not None
    assert json.loads(row[0]) == {"is_system_modeler": False}
    assert json.loads(row[1]) == {"is_system_modeler": True}


def test_none_values_stay_null(temp_db: Path) -> None:
    with helpers.transaction() as conn:
        event_id = audit_service.write_audit_event(
            conn,
            event_type=AuditEvent.USER_LOGIN,
            user_id="u-1",
        )
    row = helpers.fetch_one(
        "SELECT previous_value, new_value FROM obs.audit_log WHERE event_id = ?",
        (event_id,),
    )
    assert row == (None, None)


def test_transaction_rolls_back_on_failure(temp_db: Path) -> None:
    """RULE 6: a failure inside the transaction discards the audit write too."""
    initial = helpers.fetch_one("SELECT count(*) FROM obs.audit_log")
    assert initial is not None
    try:
        with helpers.transaction() as conn:
            audit_service.write_audit_event(
                conn, event_type=AuditEvent.USER_LOGIN, user_id="u-1"
            )
            raise RuntimeError("simulated mutation failure")
    except RuntimeError:
        pass
    after = helpers.fetch_one("SELECT count(*) FROM obs.audit_log")
    assert after == initial
