"""The 7-step request authorization flow as reusable FastAPI dependencies.

Security Spec v1.4 §9.1 defines a single ordered flow that every endpoint must
enforce:

    Step 1  Validate the access token .............................. 401 on failure
    Step 2  Resolve the user in obs.users; check is_active .......... 403 on failure
    Step 3  If is_administrator → full access, skip Steps 4-6
    Step 4  Capability flag check ................................... 403 on failure
    Step 5  Cost center scope check ................................. 403 on failure
    Step 6  Standard + confidential grant check (strip or 403)
    Step 7  Execute and write the audit log

Steps 1-4 are provided here as composable dependencies that Phase 1's admin-only
endpoints consume directly. Steps 5-6 are provided as a grant-resolution
framework (:func:`resolve_effective_grant`, :func:`build_cost_center_scope_predicate`,
:func:`strip_confidential_fields`) that the cost-center data endpoints in later
phases will use; they are unit-tested now so the security core is proven before
any data endpoint relies on it.

A failure NEVER surfaces as 404 to hide a resource from an authorized-but-
forbidden caller — it is always 403 (Security Spec §9.2; RULE 5).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any
from typing import Callable

from fastapi import Depends
from fastapi import HTTPException
from fastapi import Request
from fastapi import status

from backend.app.auth.models import CurrentUser
from backend.app.auth.token import InvalidTokenError
from backend.app.auth.token import validate_access_token
from backend.app.config import settings
from backend.app.db import engine
from backend.app.db import helpers
from backend.app.services import audit_service


# --- Grant value constants (Security Spec v1.4 §4.1 / §4.2; RULE 1) -------------
ALL_WRITE = "ALL_WRITE"
ALL_READ = "ALL_READ"
ALL_NONE = "ALL_NONE"

# Confidential compensation fields stripped for ALL_NONE grants. The set is the
# union of (a) the raw confidential columns on obs.positions — base_salary,
# salary_adjustment, aipeip_bonus, other_bonus (schema/bootstrap.sql §590,
# Workforce Planning FR v1.1 §4) — and (b) the derived compensation fields named
# in the Security Spec v1.4 §4.2 Claude Code note that may appear in response
# payloads (burden_rate, burden_amount, total_compensation). Both raw and derived
# fields must be absent (not null) for an ALL_NONE caller; used by later
# position/finance-review payloads.
CONFIDENTIAL_FIELDS = frozenset(
    {
        # raw confidential columns (obs.positions)
        "base_salary",
        "salary_adjustment",
        "aipeip_bonus",
        "other_bonus",
        # derived compensation fields in response payloads (Security Spec §4.2)
        "burden_rate",
        "burden_amount",
        "total_compensation",
    }
)


# =====================================================================
# Steps 1-2 — token validation and user resolution
# =====================================================================
def _bearer_token(request: Request) -> str:
    """Extract the bearer access token from the Authorization header."""
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise InvalidTokenError("Missing bearer token.")
    return token


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _resolve_caller_identity(request: Request) -> tuple[str, str, str]:
    """Step 1. Return ``(oid, email, display_name)`` for the caller.

    Local path: ``LOCAL_AUTH_BYPASS`` supplies a synthetic identity from the
    ``LOCAL_AUTH_USER_*`` environment (RULE 8 — read once at startup, never from a
    request). Cloud path: validate the Entra ID access token. Either way only the
    identity is taken from the token/mock; the authoritative capability flags come
    from obs.users in Step 2.
    """
    if settings.local_auth_bypass:
        if not settings.local_auth_user_id:
            raise InvalidTokenError("LOCAL_AUTH_BYPASS is on but LOCAL_AUTH_USER_ID is unset.")
        return (
            settings.local_auth_user_id,
            settings.local_auth_user_email,
            settings.local_auth_user_name,
        )
    claims = validate_access_token(_bearer_token(request))
    oid = claims.get("oid") or claims.get("sub") or ""
    if not oid:
        raise InvalidTokenError("Token has no oid/sub claim.")
    email = claims.get("email") or claims.get("preferred_username") or ""
    name = claims.get("name") or email
    return oid, email, name


def _lookup_user(oid: str) -> tuple[Any, ...] | None:
    ph = engine.placeholder()
    return helpers.fetch_one(
        "SELECT user_id, display_name, email, is_active, is_administrator, "
        "is_system_modeler, is_report_developer, is_finance_reviewer "
        f"FROM obs.users WHERE user_id = {ph}",
        (oid,),
    )


def get_current_user(request: Request) -> CurrentUser:
    """Steps 1-2. Resolve and return the active OBS user, or raise 401/403.

    Step 1 failures (no/invalid token) → 401. Step 2 failures (no registry record
    or ``is_active = FALSE``) → 403, and a ``USER_LOGIN_DENIED`` audit event is
    written (AC-AUDIT-04). The returned :class:`CurrentUser` carries the
    authoritative flags from obs.users.
    """
    try:
        oid, _email, _name = _resolve_caller_identity(request)
    except InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)
        ) from exc

    row = _lookup_user(oid)
    if row is None or not bool(row[3]):
        reason = "no OBS registry record" if row is None else "is_active = FALSE"
        _record_login_denied(oid, reason, _client_ip(request))
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User is not authorized to access OBS.",
        )

    return CurrentUser(
        user_id=row[0],
        display_name=row[1],
        email=row[2],
        is_active=bool(row[3]),
        is_administrator=bool(row[4]),
        is_system_modeler=bool(row[5]),
        is_report_developer=bool(row[6]),
        is_finance_reviewer=bool(row[7]),
        session_id=str(uuid.uuid4()),
        ip_address=_client_ip(request),
    )


def _record_login_denied(oid: str, reason: str, ip_address: str | None) -> None:
    with helpers.transaction() as conn:
        audit_service.write_audit_event(
            conn,
            event_type=audit_service.AuditEvent.USER_LOGIN_DENIED,
            user_id=oid,
            outcome=audit_service.Outcome.FAILURE,
            entity_type="user",
            entity_id=oid,
            new_value={"reason": reason},
            ip_address=ip_address,
        )


# =====================================================================
# Steps 3-4 — administrator short-circuit and capability flags
# =====================================================================
def require_administrator(
    current_user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    """Step 3 gate. Allow only Administrators; 403 otherwise."""
    if not current_user.is_administrator:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrator privilege required.",
        )
    return current_user


def require_capability(flag_name: str) -> Callable[[CurrentUser], CurrentUser]:
    """Step 4 gate factory. Allow Administrators (Step 3) or holders of ``flag_name``.

    ``flag_name`` is an obs.users capability column, e.g. ``is_system_modeler``,
    ``is_report_developer``, ``is_finance_reviewer`` (RULE 1).
    """

    def _dependency(
        current_user: CurrentUser = Depends(get_current_user),
    ) -> CurrentUser:
        if current_user.is_administrator or getattr(current_user, flag_name):
            return current_user
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Capability '{flag_name}' required.",
        )

    return _dependency


# =====================================================================
# Steps 5-6 — cost center scope and grant resolution (framework for later phases)
# =====================================================================
@dataclass(frozen=True)
class EffectiveGrant:
    """A user's resolved grant on a single cost center (Security Spec v1.4 §4)."""

    standard_grant: str
    confidential_grant: str

    @property
    def can_read(self) -> bool:
        return self.standard_grant in (ALL_READ, ALL_WRITE)

    @property
    def can_write(self) -> bool:
        return self.standard_grant == ALL_WRITE

    @property
    def can_read_confidential(self) -> bool:
        return self.confidential_grant in (ALL_READ, ALL_WRITE)

    @property
    def can_write_confidential(self) -> bool:
        return self.confidential_grant == ALL_WRITE


def resolve_effective_grant(user_id: str, cost_center_id: str) -> EffectiveGrant | None:
    """Resolve a user's effective grant on a cost center (Security Spec v1.4 §4.4).

    Resolution order (most specific wins):
      1. Individual ``obs.user_cost_center_grants`` row (AC-GRANT-07).
      2. Otherwise the deepest covering ``obs.user_rollup_grants`` rollup, with a
         matching ``obs.user_rollup_overrides`` row taking precedence over the
         rollup default for that cost center (AC-GRANT-06).
    Returns ``None`` when the user has no covering grant (AC-GRANT-01).

    Note: ``cost_center_id`` in the grant tables is the cost center code used by
    ``obs.cost_center_hierarchy_memberships`` (the system's single cost center key).
    """
    ph = engine.placeholder()

    individual = helpers.fetch_one(
        "SELECT standard_grant, confidential_grant FROM obs.user_cost_center_grants "
        f"WHERE user_id = {ph} AND cost_center_id = {ph}",
        (user_id, cost_center_id),
    )
    if individual is not None:
        return EffectiveGrant(individual[0], individual[1])

    membership = helpers.fetch_one(
        "SELECT level_1_code, level_2_code, level_3_code, level_4_code, "
        "level_5_code, level_6_code FROM obs.cost_center_hierarchy_memberships "
        f"WHERE cost_center_code = {ph} AND is_active = TRUE",
        (cost_center_id,),
    )
    if membership is None:
        return None

    # Deepest level first = most specific rollup the user might be granted.
    for rollup_id in [code for code in reversed(membership) if code]:
        rollup = helpers.fetch_one(
            "SELECT default_standard_grant, default_confidential_grant "
            f"FROM obs.user_rollup_grants WHERE user_id = {ph} AND rollup_id = {ph}",
            (user_id, rollup_id),
        )
        if rollup is None:
            continue
        override = helpers.fetch_one(
            "SELECT standard_grant, confidential_grant FROM obs.user_rollup_overrides "
            f"WHERE user_id = {ph} AND rollup_id = {ph} AND cost_center_id = {ph}",
            (user_id, rollup_id, cost_center_id),
        )
        if override is not None:
            return EffectiveGrant(override[0], override[1])
        return EffectiveGrant(rollup[0], rollup[1])

    return None


def build_cost_center_scope_predicate(
    user_id: str, column: str = "cost_center_id"
) -> tuple[str, list[str]]:
    """Build a WHERE predicate restricting ``column`` to the user's readable scope.

    Returns ``(sql_fragment, params)`` for injection into a query's WHERE clause
    BEFORE execution — scope is applied at query construction time, never by
    filtering retrieved rows (Security Spec v1.4 §4.4; AC-GRANT-08). A user with no
    scope yields the always-false predicate ``1 = 0`` so no rows leak. Callers must
    short-circuit for Administrators (full access) before calling this.
    """
    ph = engine.placeholder()
    cost_center_ids = _readable_cost_center_ids(user_id)
    if not cost_center_ids:
        return "1 = 0", []
    placeholders = ", ".join([ph] * len(cost_center_ids))
    return f"{column} IN ({placeholders})", list(cost_center_ids)


def _readable_cost_center_ids(user_id: str) -> set[str]:
    """All cost center codes the user can at least read (individual + rollup)."""
    ph = engine.placeholder()
    readable: set[str] = set()

    for (cost_center_id, standard_grant) in helpers.fetch_all(
        "SELECT cost_center_id, standard_grant FROM obs.user_cost_center_grants "
        f"WHERE user_id = {ph}",
        (user_id,),
    ):
        if standard_grant in (ALL_READ, ALL_WRITE):
            readable.add(cost_center_id)

    rollup_rows = helpers.fetch_all(
        "SELECT rollup_id, default_standard_grant FROM obs.user_rollup_grants "
        f"WHERE user_id = {ph}",
        (user_id,),
    )
    for (rollup_id, default_standard_grant) in rollup_rows:
        if default_standard_grant not in (ALL_READ, ALL_WRITE):  # pragma: no cover
            continue  # defensive — a CHECK constraint already restricts the values
        for (cost_center_code,) in helpers.fetch_all(
            "SELECT cost_center_code FROM obs.cost_center_hierarchy_memberships "
            "WHERE is_active = TRUE AND ("
            f"level_1_code = {ph} OR level_2_code = {ph} OR level_3_code = {ph} "
            f"OR level_4_code = {ph} OR level_5_code = {ph} OR level_6_code = {ph})",
            (rollup_id,) * 6,
        ):
            readable.add(cost_center_code)

    # Individual grants already added above take precedence; nothing to subtract
    # here because an individual grant only ever widens or matches readability.
    return readable


def strip_confidential_fields(
    payload: dict[str, Any], grant: EffectiveGrant | None
) -> dict[str, Any]:
    """Remove compensation fields unless the grant permits reading them.

    For ALL_NONE (or no grant), the confidential fields are ABSENT from the
    returned payload — not null, not zero (Security Spec v1.4 §4.2; AC-CONF-01).
    Their absence is the frontend's signal that the caller lacks access.
    """
    if grant is not None and grant.can_read_confidential:
        return payload
    return {k: v for k, v in payload.items() if k not in CONFIDENTIAL_FIELDS}
