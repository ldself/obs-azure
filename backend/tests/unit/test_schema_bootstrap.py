"""Validate that schema/bootstrap.sql creates every required obs.* table.

Directly exercises Build Sequencing Plan v1.2 §4.0.4: "All obs. tables exist in
the obs schema with correct schemas and are queryable." The single logical
obs.quarantine in §4.0.3 is realised as the four per-type quarantine tables
defined by Data Integration Spec v1.8 §8.7.
"""

from __future__ import annotations

import duckdb
import pytest

from backend.app.db.bootstrap import apply_bootstrap


# Tables enumerated in Build Sequencing Plan v1.2 §4.0.3 ...
REQUIRED_TABLES_4_0_3 = {
    "users",
    "user_cost_center_grants",
    "user_rollup_grants",
    "user_rollup_overrides",
    "audit_log",
    "cost_center_hierarchy_nodes",
    "expense_accounts",
    "account_hierarchy_nodes",
    "employees",
    "actuals",
    "ingestion_control",
    "actuals_staging",
    "employees_staging",
    "budget_versions",
    "budget_lines",
    "budget_targets",
    "vendor_line_items",
    "budget_version_locks",
    "overhead_allocation_rates",
    "positions",
    "position_transfers",
    "compensation_component_mappings",
    "compensation_burden_rates",
    "merit_increase_rates",
    "finance_review_records",
    "notifications",
    "report_definitions",
    "report_annotations",
    "saved_filters",
}

# ... plus the four per-type quarantine tables (DI v1.8 §8.7) realising the
# single "obs.quarantine" entry, and the dimension/membership tables DI v1.8
# §8.4.2/§8.5.2 require.
ADDITIONAL_TABLES = {
    "actuals_quarantine",
    "employees_quarantine",
    "cost_center_hierarchy_quarantine",
    "account_hierarchy_quarantine",
    "cost_center_hierarchy_memberships",
    "account_hierarchy_memberships",
    "cost_centers",
}

EXPECTED_TABLES = REQUIRED_TABLES_4_0_3 | ADDITIONAL_TABLES


@pytest.fixture
def bootstrapped_conn() -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(":memory:")
    apply_bootstrap(conn)
    return conn


def _obs_tables(conn: duckdb.DuckDBPyConnection) -> set[str]:
    rows = conn.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'obs'"
    ).fetchall()
    return {r[0] for r in rows}


def test_all_required_tables_exist(bootstrapped_conn: duckdb.DuckDBPyConnection) -> None:
    """AC-P0-SCHEMA: every §4.0.3 table (and required DI v1.8 tables) exists."""
    present = _obs_tables(bootstrapped_conn)
    missing = EXPECTED_TABLES - present
    assert not missing, f"Missing obs.* tables: {sorted(missing)}"


def test_every_table_is_queryable(bootstrapped_conn: duckdb.DuckDBPyConnection) -> None:
    """AC-P0-SCHEMA: every obs.* table can be queried and starts empty."""
    for table in sorted(_obs_tables(bootstrapped_conn)):
        count = bootstrapped_conn.execute(f"SELECT COUNT(*) FROM obs.{table}").fetchone()[0]
        assert count == 0
