"""Append-only audit logging for security events and mutations (RULE 6).

Every create, update, and delete — and every security event (login, login
denial, grant change) — writes one row to ``obs.audit_log``. The write is
synchronous and happens on the SAME connection/transaction as the mutation it
records: if the audit insert fails, the mutation is rolled back (Security Spec
v1.4 §8; Architecture Spec v3.6 §7.2). Audit rows are never updated or deleted —
the PostgreSQL deployment enforces this with a BEFORE UPDATE OR DELETE trigger;
locally the guarantee is upheld by never issuing such statements.

The canonical event types are defined in Security Spec v1.4 §8.1 and exposed here
as :class:`AuditEvent` so callers cannot misspell them (RULE 1).
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from datetime import timezone
from typing import Any

from backend.app.db import engine
from backend.app.db import helpers


class AuditEvent:
    """Canonical audit event type constants (Security Spec v1.4 §8.1; DI Spec v1.8 §8.6)."""

    # Phase 1 — Security / User Management
    USER_PROVISIONED = "USER_PROVISIONED"
    LOGIN_PERMISSION_GRANTED = "LOGIN_PERMISSION_GRANTED"
    LOGIN_PERMISSION_REVOKED = "LOGIN_PERMISSION_REVOKED"
    CAPABILITY_FLAG_CHANGED = "CAPABILITY_FLAG_CHANGED"
    FINANCE_REVIEWER_FLAG_SET = "FINANCE_REVIEWER_FLAG_SET"
    COST_CENTER_GRANT_ADDED = "COST_CENTER_GRANT_ADDED"
    COST_CENTER_GRANT_MODIFIED = "COST_CENTER_GRANT_MODIFIED"
    COST_CENTER_GRANT_REMOVED = "COST_CENTER_GRANT_REMOVED"
    ROLLUP_OVERRIDE_SET = "ROLLUP_OVERRIDE_SET"
    USER_LOGIN = "USER_LOGIN"
    USER_LOGIN_DENIED = "USER_LOGIN_DENIED"
    USER_LOGOUT = "USER_LOGOUT"

    # Phase 2 — Data Integration (Data Integration Spec v1.8 §8.6)
    INGESTION_COMPLETED = "INGESTION_COMPLETED"
    INGESTION_REJECTED = "INGESTION_REJECTED"
    INGESTION_QUARANTINED = "INGESTION_QUARANTINED"
    QUARANTINE_REPROMOTED = "QUARANTINE_REPROMOTED"


class Outcome:
    """Audit ``outcome`` values (Architecture Spec v3.6 §7.2)."""

    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _encode(value: Any) -> str | None:
    """JSON-encode a snapshot value for ``previous_value``/``new_value``."""
    if value is None:
        return None
    return json.dumps(value, default=str, sort_keys=True)


def write_audit_event(
    conn: Any,
    *,
    event_type: str,
    user_id: str,
    outcome: str = Outcome.SUCCESS,
    entity_type: str | None = None,
    entity_id: str | None = None,
    previous_value: Any = None,
    new_value: Any = None,
    ip_address: str | None = None,
    session_id: str | None = None,
) -> str:
    """Insert one audit row on ``conn`` and return the generated ``event_id``.

    ``conn`` MUST be the same connection driving the triggering mutation (see
    :func:`backend.app.db.helpers.transaction`) so the two commit atomically.
    ``user_id`` is the actor's Entra ID oid. ``previous_value``/``new_value`` are
    JSON-encoded snapshots, used on mutations to capture before/after state.
    """
    event_id = str(uuid.uuid4())
    ph = engine.placeholder()
    sql = (
        "INSERT INTO obs.audit_log "
        "(event_id, event_timestamp, event_type, user_id, session_id, entity_type, "
        " entity_id, previous_value, new_value, ip_address, outcome) "
        f"VALUES ({', '.join([ph] * 11)})"
    )
    helpers.exec_write(
        conn,
        sql,
        (
            event_id,
            _utc_now(),
            event_type,
            user_id,
            session_id,
            entity_type,
            entity_id,
            _encode(previous_value),
            _encode(new_value),
            ip_address,
            outcome,
        ),
    )
    return event_id
