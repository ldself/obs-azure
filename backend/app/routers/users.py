"""User registry and cost center grant endpoints (Build Plan v1.2 §4.1.3).

Every endpoint enforces the §9.1 authorization flow via the dependencies in
:mod:`backend.app.auth.dependencies`. All endpoints except ``GET /me`` and
``GET /users/{user_id}/grants`` (self-or-admin) are Administrator-only
(Security Spec v1.4 §6.2). Every mutation writes its audit event(s) in the same
transaction as the change (RULE 6), and an unauthorized-but-existing resource is
always answered with 403, never 404 (RULE 5).
"""

from __future__ import annotations

from datetime import datetime
from datetime import timezone
from typing import Any

from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Request
from fastapi import status

from backend.app.auth.dependencies import get_current_user
from backend.app.auth.dependencies import require_administrator
from backend.app.auth.models import CurrentUser
from backend.app.db import engine
from backend.app.db import helpers
from backend.app.models.users import CostCenterGrantCreate
from backend.app.models.users import CostCenterGrantOut
from backend.app.models.users import MeOut
from backend.app.models.users import RollupGrantCreate
from backend.app.models.users import UserCreate
from backend.app.models.users import UserOut
from backend.app.models.users import UserUpdate
from backend.app.services import audit_service


router = APIRouter(prefix="/api/v1", tags=["users"])

# Capability flags that emit a CAPABILITY_FLAG_CHANGED event when changed
# (Security Spec v1.4 §8.1). is_finance_reviewer is audited separately as
# FINANCE_REVIEWER_FLAG_SET; is_active is audited as LOGIN_PERMISSION_*.
_CAPABILITY_FLAGS = ("is_administrator", "is_system_modeler", "is_report_developer")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _one(conn: Any, sql: str, params: tuple[Any, ...]) -> tuple[Any, ...] | None:
    return helpers.query_one(conn, sql, params)


def _user_row_to_out(row: tuple[Any, ...]) -> UserOut:
    return UserOut(
        user_id=row[0],
        display_name=row[1],
        email=row[2],
        is_active=bool(row[3]),
        is_administrator=bool(row[4]),
        is_system_modeler=bool(row[5]),
        is_report_developer=bool(row[6]),
        is_finance_reviewer=bool(row[7]),
    )


_USER_SELECT = (
    "SELECT user_id, display_name, email, is_active, is_administrator, "
    "is_system_modeler, is_report_developer, is_finance_reviewer FROM obs.users"
)


# =====================================================================
# GET /me — Steps 1-2 only (any active user)
# =====================================================================
@router.get("/me", response_model=MeOut)
def get_me(current_user: CurrentUser = Depends(get_current_user)) -> MeOut:
    """Return the authenticated caller's profile and capability flags.

    On the local LOCAL_AUTH_BYPASS path this also records the USER_LOGIN event
    (the cloud path records it in /api/v1/auth/callback), so every login is
    audited exactly once (AC-AUDIT-04).
    """
    from backend.app.config import settings

    if settings.local_auth_bypass:
        with helpers.transaction() as conn:
            audit_service.write_audit_event(
                conn,
                event_type=audit_service.AuditEvent.USER_LOGIN,
                user_id=current_user.user_id,
                entity_type="user",
                entity_id=current_user.user_id,
                new_value={
                    "is_active": current_user.is_active,
                    "is_administrator": current_user.is_administrator,
                    "is_system_modeler": current_user.is_system_modeler,
                    "is_report_developer": current_user.is_report_developer,
                    "is_finance_reviewer": current_user.is_finance_reviewer,
                },
                ip_address=current_user.ip_address,
                session_id=current_user.session_id,
            )

    return MeOut(
        user_id=current_user.user_id,
        display_name=current_user.display_name,
        email=current_user.email,
        is_active=current_user.is_active,
        is_administrator=current_user.is_administrator,
        is_system_modeler=current_user.is_system_modeler,
        is_report_developer=current_user.is_report_developer,
        is_finance_reviewer=current_user.is_finance_reviewer,
    )


# =====================================================================
# GET /users — list all (Administrator only)
# =====================================================================
@router.get("/users", response_model=list[UserOut])
def list_users(
    _admin: CurrentUser = Depends(require_administrator),
) -> list[UserOut]:
    rows = helpers.fetch_all(f"{_USER_SELECT} ORDER BY display_name")
    return [_user_row_to_out(row) for row in rows]


# =====================================================================
# POST /users — provision a registry record (Administrator only)
# =====================================================================
@router.post("/users", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user(
    body: UserCreate,
    admin: CurrentUser = Depends(require_administrator),
) -> UserOut:
    ph = engine.placeholder()
    now = _utc_now()
    with helpers.transaction() as conn:
        if _one(conn, f"{_USER_SELECT} WHERE user_id = {ph}", (body.user_id,)) is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A user with this user_id already exists.",
            )
        helpers.exec_write(
            conn,
            "INSERT INTO obs.users (user_id, display_name, email, is_active, "
            "is_administrator, is_system_modeler, is_report_developer, "
            "is_finance_reviewer, created_at, created_by, updated_at, updated_by) "
            f"VALUES ({', '.join([ph] * 12)})",
            (
                body.user_id,
                body.display_name,
                str(body.email),
                body.is_active,
                body.is_administrator,
                body.is_system_modeler,
                body.is_report_developer,
                body.is_finance_reviewer,
                now,
                admin.user_id,
                now,
                admin.user_id,
            ),
        )
        audit_service.write_audit_event(
            conn,
            event_type=audit_service.AuditEvent.USER_PROVISIONED,
            user_id=admin.user_id,
            entity_type="user",
            entity_id=body.user_id,
            new_value={
                "is_active": body.is_active,
                "is_administrator": body.is_administrator,
                "is_system_modeler": body.is_system_modeler,
                "is_report_developer": body.is_report_developer,
                "is_finance_reviewer": body.is_finance_reviewer,
            },
            ip_address=admin.ip_address,
            session_id=admin.session_id,
        )
        row = _one(conn, f"{_USER_SELECT} WHERE user_id = {ph}", (body.user_id,))
    assert row is not None  # just inserted
    return _user_row_to_out(row)


# =====================================================================
# PATCH /users/{user_id} — change flags / is_active (Administrator only)
# =====================================================================
@router.patch("/users/{user_id}", response_model=UserOut)
def update_user(
    user_id: str,
    body: UserUpdate,
    request: Request,
    admin: CurrentUser = Depends(require_administrator),
) -> UserOut:
    ph = engine.placeholder()
    changes = body.model_dump(exclude_none=True)

    with helpers.transaction() as conn:
        current = _one(conn, f"{_USER_SELECT} WHERE user_id = {ph}", (user_id,))
        if current is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="User not found."
            )
        before = _user_row_to_out(current)

        # Last-active-Administrator guard (AC-CAP-08): block a change that would
        # remove the final active Administrator's admin rights or login access.
        removing_admin = changes.get("is_administrator") is False and before.is_administrator
        deactivating = changes.get("is_active") is False and before.is_active
        if (removing_admin or deactivating) and before.is_administrator and before.is_active:
            active_admins = _one(
                conn,
                "SELECT COUNT(*) FROM obs.users WHERE is_active = TRUE AND is_administrator = TRUE",
                (),
            )
            if active_admins is not None and int(active_admins[0]) <= 1:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Cannot deactivate or remove Administrator from the last active Administrator.",
                )

        if not changes:
            return before  # nothing to update

        # Apply the update.
        set_clause = ", ".join(f"{field} = {ph}" for field in changes)
        params = (*changes.values(), _utc_now(), admin.user_id, user_id)
        helpers.exec_write(
            conn,
            f"UPDATE obs.users SET {set_clause}, updated_at = {ph}, updated_by = {ph} "
            f"WHERE user_id = {ph}",
            params,
        )

        _audit_user_changes(conn, admin, user_id, before, changes)
        row = _one(conn, f"{_USER_SELECT} WHERE user_id = {ph}", (user_id,))

    assert row is not None
    return _user_row_to_out(row)


def _audit_user_changes(
    conn: Any,
    admin: CurrentUser,
    user_id: str,
    before: UserOut,
    changes: dict[str, Any],
) -> None:
    """Emit one audit event per changed field (Security Spec v1.4 §8.1)."""
    for field, new_value in changes.items():
        old_value = getattr(before, field)
        if old_value == new_value:
            continue
        if field == "is_active":
            event_type = (
                audit_service.AuditEvent.LOGIN_PERMISSION_GRANTED
                if new_value
                else audit_service.AuditEvent.LOGIN_PERMISSION_REVOKED
            )
            audit_service.write_audit_event(
                conn,
                event_type=event_type,
                user_id=admin.user_id,
                entity_type="user",
                entity_id=user_id,
                previous_value={field: old_value},
                new_value={field: new_value},
                ip_address=admin.ip_address,
                session_id=admin.session_id,
            )
        elif field == "is_finance_reviewer":
            audit_service.write_audit_event(
                conn,
                event_type=audit_service.AuditEvent.FINANCE_REVIEWER_FLAG_SET,
                user_id=admin.user_id,
                entity_type="user",
                entity_id=user_id,
                previous_value={"is_finance_reviewer": old_value},
                new_value={"is_finance_reviewer": new_value},
                ip_address=admin.ip_address,
                session_id=admin.session_id,
            )
        elif field in _CAPABILITY_FLAGS:
            audit_service.write_audit_event(
                conn,
                event_type=audit_service.AuditEvent.CAPABILITY_FLAG_CHANGED,
                user_id=admin.user_id,
                entity_type="user",
                entity_id=user_id,
                previous_value={field: old_value},
                new_value={field: new_value},
                ip_address=admin.ip_address,
                session_id=admin.session_id,
            )


# =====================================================================
# GET /users/{user_id}/grants — list cost center grants (self or Administrator)
# =====================================================================
@router.get("/users/{user_id}/grants", response_model=list[CostCenterGrantOut])
def list_grants(
    user_id: str,
    current_user: CurrentUser = Depends(get_current_user),
) -> list[CostCenterGrantOut]:
    if not current_user.is_administrator and current_user.user_id != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to view this user's grants.",
        )
    ph = engine.placeholder()
    rows = helpers.fetch_all(
        "SELECT user_id, cost_center_id, standard_grant, confidential_grant, "
        f"granted_by, granted_at FROM obs.user_cost_center_grants WHERE user_id = {ph} "
        "ORDER BY cost_center_id",
        (user_id,),
    )
    return [
        CostCenterGrantOut(
            user_id=row[0],
            cost_center_id=row[1],
            standard_grant=row[2],
            confidential_grant=row[3],
            granted_by=row[4],
            granted_at=str(row[5]),
        )
        for row in rows
    ]


# =====================================================================
# POST /users/{user_id}/grants — add cost center grant (Administrator only)
# =====================================================================
@router.post(
    "/users/{user_id}/grants",
    response_model=CostCenterGrantOut,
    status_code=status.HTTP_201_CREATED,
)
def add_grant(
    user_id: str,
    body: CostCenterGrantCreate,
    admin: CurrentUser = Depends(require_administrator),
) -> CostCenterGrantOut:
    ph = engine.placeholder()
    now = _utc_now()
    with helpers.transaction() as conn:
        if _one(conn, f"{_USER_SELECT} WHERE user_id = {ph}", (user_id,)) is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="User not found."
            )
        existing = _one(
            conn,
            "SELECT standard_grant, confidential_grant FROM obs.user_cost_center_grants "
            f"WHERE user_id = {ph} AND cost_center_id = {ph}",
            (user_id, body.cost_center_id),
        )
        if existing is None:
            helpers.exec_write(
                conn,
                "INSERT INTO obs.user_cost_center_grants (user_id, cost_center_id, "
                "standard_grant, confidential_grant, granted_by, granted_at) "
                f"VALUES ({', '.join([ph] * 6)})",
                (user_id, body.cost_center_id, body.standard_grant,
                 body.confidential_grant, admin.user_id, now),
            )
            event_type = audit_service.AuditEvent.COST_CENTER_GRANT_ADDED
            previous_value: dict[str, Any] | None = None
        else:
            helpers.exec_write(
                conn,
                "UPDATE obs.user_cost_center_grants "
                f"SET standard_grant = {ph}, confidential_grant = {ph}, "
                f"granted_by = {ph}, granted_at = {ph} "
                f"WHERE user_id = {ph} AND cost_center_id = {ph}",
                (body.standard_grant, body.confidential_grant, admin.user_id, now,
                 user_id, body.cost_center_id),
            )
            event_type = audit_service.AuditEvent.COST_CENTER_GRANT_MODIFIED
            previous_value = {
                "standard_grant": existing[0],
                "confidential_grant": existing[1],
            }
        audit_service.write_audit_event(
            conn,
            event_type=event_type,
            user_id=admin.user_id,
            entity_type="cost_center_grant",
            entity_id=f"{user_id}:{body.cost_center_id}",
            previous_value=previous_value,
            new_value={
                "cost_center_id": body.cost_center_id,
                "standard_grant": body.standard_grant,
                "confidential_grant": body.confidential_grant,
            },
            ip_address=admin.ip_address,
            session_id=admin.session_id,
        )

    return CostCenterGrantOut(
        user_id=user_id,
        cost_center_id=body.cost_center_id,
        standard_grant=body.standard_grant,
        confidential_grant=body.confidential_grant,
        granted_by=admin.user_id,
        granted_at=str(now),
    )


# =====================================================================
# DELETE /users/{user_id}/grants/{grant_id} — remove grant (Administrator only)
# =====================================================================
@router.delete(
    "/users/{user_id}/grants/{grant_id}", status_code=status.HTTP_204_NO_CONTENT
)
def remove_grant(
    user_id: str,
    grant_id: str,
    admin: CurrentUser = Depends(require_administrator),
) -> None:
    """Remove a cost center grant. ``grant_id`` is the cost_center_id of the grant
    (the grant's identifier within a user; PK is (user_id, cost_center_id))."""
    ph = engine.placeholder()
    with helpers.transaction() as conn:
        existing = _one(
            conn,
            "SELECT standard_grant, confidential_grant FROM obs.user_cost_center_grants "
            f"WHERE user_id = {ph} AND cost_center_id = {ph}",
            (user_id, grant_id),
        )
        if existing is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Grant not found."
            )
        # Audit BEFORE deletion (Security Spec v1.4 §7.3 note: "Grant removals are
        # logged in the audit log before deletion.").
        audit_service.write_audit_event(
            conn,
            event_type=audit_service.AuditEvent.COST_CENTER_GRANT_REMOVED,
            user_id=admin.user_id,
            entity_type="cost_center_grant",
            entity_id=f"{user_id}:{grant_id}",
            previous_value={
                "cost_center_id": grant_id,
                "standard_grant": existing[0],
                "confidential_grant": existing[1],
            },
            ip_address=admin.ip_address,
            session_id=admin.session_id,
        )
        helpers.exec_write(
            conn,
            "DELETE FROM obs.user_cost_center_grants "
            f"WHERE user_id = {ph} AND cost_center_id = {ph}",
            (user_id, grant_id),
        )


# =====================================================================
# POST /users/{user_id}/rollup-grants — add rollup grant (Administrator only)
# =====================================================================
@router.post(
    "/users/{user_id}/rollup-grants", status_code=status.HTTP_201_CREATED
)
def add_rollup_grant(
    user_id: str,
    body: RollupGrantCreate,
    admin: CurrentUser = Depends(require_administrator),
) -> dict[str, str]:
    ph = engine.placeholder()
    now = _utc_now()
    with helpers.transaction() as conn:
        if _one(conn, f"{_USER_SELECT} WHERE user_id = {ph}", (user_id,)) is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="User not found."
            )
        existing = _one(
            conn,
            "SELECT default_standard_grant, default_confidential_grant "
            f"FROM obs.user_rollup_grants WHERE user_id = {ph} AND rollup_id = {ph}",
            (user_id, body.rollup_id),
        )
        if existing is None:
            helpers.exec_write(
                conn,
                "INSERT INTO obs.user_rollup_grants (user_id, rollup_id, "
                "default_standard_grant, default_confidential_grant, granted_by, granted_at) "
                f"VALUES ({', '.join([ph] * 6)})",
                (user_id, body.rollup_id, body.default_standard_grant,
                 body.default_confidential_grant, admin.user_id, now),
            )
            event_type = audit_service.AuditEvent.COST_CENTER_GRANT_ADDED
            previous_value: dict[str, Any] | None = None
        else:
            helpers.exec_write(
                conn,
                "UPDATE obs.user_rollup_grants "
                f"SET default_standard_grant = {ph}, default_confidential_grant = {ph}, "
                f"granted_by = {ph}, granted_at = {ph} "
                f"WHERE user_id = {ph} AND rollup_id = {ph}",
                (body.default_standard_grant, body.default_confidential_grant,
                 admin.user_id, now, user_id, body.rollup_id),
            )
            event_type = audit_service.AuditEvent.COST_CENTER_GRANT_MODIFIED
            previous_value = {
                "default_standard_grant": existing[0],
                "default_confidential_grant": existing[1],
            }
        audit_service.write_audit_event(
            conn,
            event_type=event_type,
            user_id=admin.user_id,
            entity_type="rollup_grant",
            entity_id=f"{user_id}:{body.rollup_id}",
            previous_value=previous_value,
            new_value={
                "rollup_id": body.rollup_id,
                "default_standard_grant": body.default_standard_grant,
                "default_confidential_grant": body.default_confidential_grant,
            },
            ip_address=admin.ip_address,
            session_id=admin.session_id,
        )
    return {"user_id": user_id, "rollup_id": body.rollup_id}
