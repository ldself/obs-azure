"""Read-only dimension data API (Phase 2, Build Plan v1.2 §4.2).

All GET endpoints are accessible to any authenticated active user. RULE 9: no
write operations are permitted on obs.expense_accounts or obs.account_hierarchy_nodes
via API — any non-GET method returns 405.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from fastapi import status

from backend.app.auth.dependencies import get_current_user
from backend.app.auth.models import CurrentUser
from backend.app.db import helpers
from backend.app.models.dimensions import AccountHierarchyNodeOut
from backend.app.models.dimensions import AccountMembershipOut
from backend.app.models.dimensions import CostCenterHierarchyNodeOut
from backend.app.models.dimensions import CostCenterMembershipOut
from backend.app.models.dimensions import CostCenterOut
from backend.app.models.dimensions import ExpenseAccountOut

router = APIRouter(prefix="/api/v1/dimensions", tags=["dimensions"])


def _405() -> None:
    raise HTTPException(
        status_code=status.HTTP_405_METHOD_NOT_ALLOWED,
        detail="This endpoint is read-only (RULE 9). Writes are blocked.",
    )


# ---------------------------------------------------------------------------
# Cost centers
# ---------------------------------------------------------------------------

@router.get("/cost-centers", response_model=list[CostCenterOut])
def list_cost_centers(
    is_active: bool | None = None,
    _: CurrentUser = Depends(get_current_user),
) -> list[CostCenterOut]:
    ph = helpers.engine.placeholder()
    sql = "SELECT cost_center_code, cost_center_name, is_active FROM obs.cost_centers"
    params: list[Any] = []
    if is_active is not None:
        sql += f" WHERE is_active = {ph}"
        params.append(is_active)
    sql += " ORDER BY cost_center_code"
    rows = helpers.fetch_all(sql, params or None)
    return [CostCenterOut(cost_center_code=r[0], cost_center_name=r[1], is_active=bool(r[2])) for r in rows]


# ---------------------------------------------------------------------------
# Cost center hierarchy
# ---------------------------------------------------------------------------

@router.get("/cost-center-hierarchy/nodes", response_model=list[CostCenterHierarchyNodeOut])
def list_cc_hierarchy_nodes(
    hierarchy_id: str | None = None,
    is_active: bool | None = None,
    _: CurrentUser = Depends(get_current_user),
) -> list[CostCenterHierarchyNodeOut]:
    ph = helpers.engine.placeholder()
    sql = (
        "SELECT node_id, hierarchy_id, node_code, node_name, node_depth, "
        "parent_node_code, is_active, last_ingestion_id "
        "FROM obs.cost_center_hierarchy_nodes"
    )
    params: list[Any] = []
    conditions: list[str] = []
    if hierarchy_id is not None:
        conditions.append(f"hierarchy_id = {ph}")
        params.append(hierarchy_id)
    if is_active is not None:
        conditions.append(f"is_active = {ph}")
        params.append(is_active)
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    sql += " ORDER BY hierarchy_id, node_depth, node_code"
    rows = helpers.fetch_all(sql, params or None)
    return [
        CostCenterHierarchyNodeOut(
            node_id=r[0], hierarchy_id=r[1], node_code=r[2], node_name=r[3],
            node_depth=r[4], parent_node_code=r[5], is_active=bool(r[6]),
            last_ingestion_id=r[7],
        )
        for r in rows
    ]


@router.get("/cost-center-hierarchy/memberships", response_model=list[CostCenterMembershipOut])
def list_cc_hierarchy_memberships(
    hierarchy_id: str | None = None,
    is_active: bool | None = None,
    _: CurrentUser = Depends(get_current_user),
) -> list[CostCenterMembershipOut]:
    ph = helpers.engine.placeholder()
    sql = (
        "SELECT membership_id, hierarchy_id, cost_center_code, cost_center_name, "
        "level_1_code, level_2_code, level_3_code, level_4_code, level_5_code, level_6_code, "
        "max_depth, is_active, last_ingestion_id "
        "FROM obs.cost_center_hierarchy_memberships"
    )
    params: list[Any] = []
    conditions: list[str] = []
    if hierarchy_id is not None:
        conditions.append(f"hierarchy_id = {ph}")
        params.append(hierarchy_id)
    if is_active is not None:
        conditions.append(f"is_active = {ph}")
        params.append(is_active)
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    sql += " ORDER BY hierarchy_id, cost_center_code"
    rows = helpers.fetch_all(sql, params or None)
    return [
        CostCenterMembershipOut(
            membership_id=r[0], hierarchy_id=r[1], cost_center_code=r[2],
            cost_center_name=r[3], level_1_code=r[4], level_2_code=r[5],
            level_3_code=r[6], level_4_code=r[7], level_5_code=r[8],
            level_6_code=r[9], max_depth=r[10], is_active=bool(r[11]),
            last_ingestion_id=r[12],
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Account hierarchy (RULE 9: all writes → 405)
# ---------------------------------------------------------------------------

@router.post("/account-hierarchy/nodes", status_code=status.HTTP_405_METHOD_NOT_ALLOWED)
@router.put("/account-hierarchy/nodes", status_code=status.HTTP_405_METHOD_NOT_ALLOWED)
@router.delete("/account-hierarchy/nodes", status_code=status.HTTP_405_METHOD_NOT_ALLOWED)
def acct_nodes_write_not_allowed() -> None:
    _405()


@router.get("/account-hierarchy/nodes", response_model=list[AccountHierarchyNodeOut])
def list_acct_hierarchy_nodes(
    hierarchy_id: str | None = None,
    is_active: bool | None = None,
    _: CurrentUser = Depends(get_current_user),
) -> list[AccountHierarchyNodeOut]:
    ph = helpers.engine.placeholder()
    sql = (
        "SELECT node_id, hierarchy_id, node_code, node_name, node_depth, "
        "parent_node_code, is_active, last_ingestion_id, "
        "is_personnel_expense, personnel_expense_source "
        "FROM obs.account_hierarchy_nodes"
    )
    params: list[Any] = []
    conditions: list[str] = []
    if hierarchy_id is not None:
        conditions.append(f"hierarchy_id = {ph}")
        params.append(hierarchy_id)
    if is_active is not None:
        conditions.append(f"is_active = {ph}")
        params.append(is_active)
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    sql += " ORDER BY hierarchy_id, node_depth, node_code"
    rows = helpers.fetch_all(sql, params or None)
    return [
        AccountHierarchyNodeOut(
            node_id=r[0], hierarchy_id=r[1], node_code=r[2], node_name=r[3],
            node_depth=r[4], parent_node_code=r[5], is_active=bool(r[6]),
            last_ingestion_id=r[7], is_personnel_expense=bool(r[8]),
            personnel_expense_source=r[9],
        )
        for r in rows
    ]


@router.get("/account-hierarchy/memberships", response_model=list[AccountMembershipOut])
def list_acct_hierarchy_memberships(
    hierarchy_id: str | None = None,
    is_active: bool | None = None,
    _: CurrentUser = Depends(get_current_user),
) -> list[AccountMembershipOut]:
    ph = helpers.engine.placeholder()
    sql = (
        "SELECT membership_id, hierarchy_id, account, sub_account, account_name, "
        "level_1_code, level_2_code, level_3_code, level_4_code, level_5_code, level_6_code, "
        "max_depth, is_active, last_ingestion_id "
        "FROM obs.account_hierarchy_memberships"
    )
    params: list[Any] = []
    conditions: list[str] = []
    if hierarchy_id is not None:
        conditions.append(f"hierarchy_id = {ph}")
        params.append(hierarchy_id)
    if is_active is not None:
        conditions.append(f"is_active = {ph}")
        params.append(is_active)
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    sql += " ORDER BY hierarchy_id, account, sub_account"
    rows = helpers.fetch_all(sql, params or None)
    return [
        AccountMembershipOut(
            membership_id=r[0], hierarchy_id=r[1], account=r[2], sub_account=r[3],
            account_name=r[4], level_1_code=r[5], level_2_code=r[6],
            level_3_code=r[7], level_4_code=r[8], level_5_code=r[9],
            level_6_code=r[10], max_depth=r[11], is_active=bool(r[12]),
            last_ingestion_id=r[13],
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Expense accounts (RULE 9: all writes → 405)
# ---------------------------------------------------------------------------

@router.post("/expense-accounts", status_code=status.HTTP_405_METHOD_NOT_ALLOWED)
@router.put("/expense-accounts", status_code=status.HTTP_405_METHOD_NOT_ALLOWED)
@router.delete("/expense-accounts", status_code=status.HTTP_405_METHOD_NOT_ALLOWED)
def expense_accounts_write_not_allowed() -> None:
    _405()


@router.get("/expense-accounts", response_model=list[ExpenseAccountOut])
def list_expense_accounts(
    is_active: bool | None = None,
    _: CurrentUser = Depends(get_current_user),
) -> list[ExpenseAccountOut]:
    ph = helpers.engine.placeholder()
    sql = "SELECT account, sub_account, account_name, is_active FROM obs.expense_accounts"
    params: list[Any] = []
    if is_active is not None:
        sql += f" WHERE is_active = {ph}"
        params.append(is_active)
    sql += " ORDER BY account, sub_account"
    rows = helpers.fetch_all(sql, params or None)
    return [
        ExpenseAccountOut(account=r[0], sub_account=r[1], account_name=r[2], is_active=bool(r[3]))
        for r in rows
    ]
