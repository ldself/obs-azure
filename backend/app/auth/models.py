"""The authenticated-caller type shared across the authorization pipeline.

A single :class:`CurrentUser` is produced regardless of how the caller was
authenticated — a validated Entra ID access token in the cloud, or the
``LOCAL_AUTH_BYPASS`` mock path locally (Local Dev Spec v1.1 §6.1). Every
downstream authorization step (capability flags, cost center scope, confidential
grant) reads the same fields, so the rest of the pipeline is identical in both
environments.
"""

from __future__ import annotations

from pydantic import BaseModel


class CurrentUser(BaseModel):
    """Resolved OBS identity for the duration of one request.

    ``user_id`` is the Entra ID object id (``oid``) and is the primary key in
    ``obs.users`` and the actor recorded in every audit event (Security Spec
    v1.4 §5). Capability flags mirror ``obs.users`` columns exactly (RULE 1).
    """

    user_id: str
    display_name: str
    email: str
    is_active: bool
    is_administrator: bool
    is_system_modeler: bool
    is_report_developer: bool
    is_finance_reviewer: bool
    # Per-request context captured for the audit log (Architecture Spec v3.6 §7.2).
    session_id: str | None = None
    ip_address: str | None = None
