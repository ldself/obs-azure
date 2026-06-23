-- =====================================================================
-- OBS schema/bootstrap_pg.sql  (PostgreSQL — production/staging engine)
-- =====================================================================
-- OPEX Budgeting System — local DuckDB schema bootstrap.
-- Generated for Phase 0 / Phase 1 (Option A: foundational tables).
--
-- VERIFICATION STATUS LEGEND
--   [VERIFIED]  Column-level DDL taken directly from a named spec section.
--   [DERIVED]   Columns inferred from a spec that describes the data but
--               gives no explicit CREATE TABLE; verify before relying on it.
--   [UNVERIFIED] Table referenced by specs but NO column-level definition
--               exists in any available document. Stub only.
--
-- Engine note: this is the PostgreSQL variant. The DuckDB variant is in
-- schema/bootstrap.sql. Per Local Dev Spec v1.1 §7 the two are
-- column-for-column identical; only engine-specific syntax differs
-- (audit append-only trigger, timestamp defaults). Schema rules from
-- Claude Code Implementation Guide v1.2 §7:
--   - All primary keys are STRING (UUID) unless a spec states otherwise.
--   - All timestamps are UTC, type TIMESTAMPTZ.
--   - All monetary amounts are DECIMAL(15,2).
--   - All percentage rates are DECIMAL(6,4).
--   - All booleans default to FALSE unless a spec states otherwise.
-- =====================================================================

CREATE SCHEMA IF NOT EXISTS obs;

-- =====================================================================
-- SECTION 1 — SECURITY CORE
-- Source: Security and Access Control Specification v1.4 §7.3
-- Status: [VERIFIED] — column lists given verbatim in §7.3 Claude Code notes.
-- =====================================================================

-- obs.users — OBS user registry. Users are never deleted, only is_active=FALSE.
CREATE TABLE obs.users (
    user_id              TEXT NOT NULL,   -- Entra ID object ID (oid)
    display_name         TEXT NOT NULL,
    email                TEXT NOT NULL,
    is_active            BOOLEAN NOT NULL DEFAULT FALSE,
    is_administrator     BOOLEAN NOT NULL DEFAULT FALSE,
    is_system_modeler    BOOLEAN NOT NULL DEFAULT FALSE,
    is_report_developer  BOOLEAN NOT NULL DEFAULT FALSE,
    is_finance_reviewer  BOOLEAN NOT NULL DEFAULT FALSE,
    created_at           TIMESTAMPTZ NOT NULL,
    created_by           TEXT,
    updated_at           TIMESTAMPTZ,
    updated_by           TEXT,
    PRIMARY KEY (user_id)
);

-- obs.user_cost_center_grants — per-cost-center standard + confidential grants.
-- standard_grant    : ALL_WRITE | ALL_READ              (Security §4.1)
-- confidential_grant: ALL_WRITE | ALL_READ | ALL_NONE   (Security §4.2)
CREATE TABLE obs.user_cost_center_grants (
    user_id            TEXT NOT NULL,
    cost_center_id     TEXT NOT NULL,
    standard_grant     TEXT NOT NULL,
    confidential_grant TEXT NOT NULL DEFAULT 'ALL_NONE',
    granted_by         TEXT NOT NULL,
    granted_at         TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (user_id, cost_center_id),
    CHECK (standard_grant IN ('ALL_WRITE','ALL_READ')),
    CHECK (confidential_grant IN ('ALL_WRITE','ALL_READ','ALL_NONE'))
);

-- obs.user_rollup_grants — default grants applied across a rollup scope.
CREATE TABLE obs.user_rollup_grants (
    user_id                   TEXT NOT NULL,
    rollup_id                 TEXT NOT NULL,
    default_standard_grant    TEXT NOT NULL,
    default_confidential_grant TEXT NOT NULL DEFAULT 'ALL_NONE',
    granted_by                TEXT NOT NULL,
    granted_at                TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (user_id, rollup_id),
    CHECK (default_standard_grant IN ('ALL_WRITE','ALL_READ')),
    CHECK (default_confidential_grant IN ('ALL_WRITE','ALL_READ','ALL_NONE'))
);

-- obs.user_rollup_overrides — per-cost-center override within a rollup grant.
CREATE TABLE obs.user_rollup_overrides (
    user_id            TEXT NOT NULL,
    rollup_id          TEXT NOT NULL,
    cost_center_id     TEXT NOT NULL,
    standard_grant     TEXT NOT NULL,
    confidential_grant TEXT NOT NULL,
    set_by             TEXT NOT NULL,
    set_at             TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (user_id, rollup_id, cost_center_id),
    CHECK (standard_grant IN ('ALL_WRITE','ALL_READ')),
    CHECK (confidential_grant IN ('ALL_WRITE','ALL_READ','ALL_NONE'))
);

-- =====================================================================
-- SECTION 2 — AUDIT LOG
-- Source: Application Architecture Specification v3.6 §7.2;
--         INSERT column order from Azure Cloud Migration Spec v2.0 §6.5.
-- Status: [VERIFIED]
-- Append-only: enforced in PostgreSQL via a BEFORE UPDATE OR DELETE trigger
-- (see bootstrap_pg.sql). DuckDB has no equivalent trigger mechanism; the
-- append-only guarantee is enforced in application code (RULE 6) locally.
-- =====================================================================

CREATE TABLE obs.audit_log (
    event_id        TEXT NOT NULL,
    event_timestamp TIMESTAMPTZ NOT NULL,   -- UTC, millisecond precision
    event_type      TEXT NOT NULL,
    user_id         TEXT NOT NULL,     -- Entra ID oid of the actor
    session_id      TEXT,
    entity_type     TEXT,
    entity_id       TEXT,
    previous_value  TEXT,              -- JSON snapshot (mutations only)
    new_value       TEXT,              -- JSON snapshot (mutations only)
    ip_address      TEXT,
    outcome         TEXT NOT NULL,     -- SUCCESS | FAILURE(+error code)
    PRIMARY KEY (event_id)
);
-- Append-only enforcement (Architecture Spec v3.6 §7.3; Security Spec v1.4 §8.2):
-- a BEFORE UPDATE OR DELETE trigger that raises, rejecting any modification or
-- deletion of audit records — including by the API service identity.
CREATE OR REPLACE FUNCTION obs.audit_log_append_only()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'obs.audit_log is append-only: % not permitted', TG_OP;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_audit_log_append_only
    BEFORE UPDATE OR DELETE ON obs.audit_log
    FOR EACH ROW EXECUTE FUNCTION obs.audit_log_append_only();

-- =====================================================================
-- SECTION 3 — DIMENSION / INGESTION TABLES
-- Source: Data Integration Specification v1.8 §8 (carried forward in force
--         by v1.4 §8 and v1.6 §8). Status: [VERIFIED] except where noted.
-- =====================================================================

-- obs.actuals_staging — Data Integration Spec v1.8 §8.1. [VERIFIED]
CREATE TABLE obs.actuals_staging (
    ingestion_id                 TEXT NOT NULL,
    staging_id                   TEXT NOT NULL,
    entity                       INTEGER NOT NULL,
    year                         INTEGER NOT NULL,
    month                        INTEGER NOT NULL,
    cost_center                  TEXT NOT NULL,
    account                      TEXT NOT NULL,
    sub_account                  TEXT NOT NULL,
    bonus_type                   TEXT,
    amount                       DOUBLE PRECISION NOT NULL,
    currency                     TEXT NOT NULL DEFAULT 'USD',
    product                      TEXT NOT NULL,
    distribution_channel         TEXT,
    stat_category                TEXT,
    profit_center                TEXT NOT NULL,
    sender_cost_center           TEXT NOT NULL,
    assignment                   TEXT,
    functional_area              TEXT,
    partner_functional_area_text TEXT,
    source_file_name             TEXT NOT NULL,
    source_row_number            INTEGER NOT NULL,
    staged_at                    TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (staging_id)
);

-- obs.actuals — Data Integration Spec v1.8 §8.2. [VERIFIED]
-- Natural key: entity+year+month+cost_center+account+sub_account.
-- Immutable: versioning via is_deleted (reporting filters is_deleted=FALSE).
CREATE TABLE obs.actuals (
    actuals_id                   TEXT NOT NULL,
    ingestion_id                 TEXT NOT NULL,
    entity                       INTEGER NOT NULL,
    year                         INTEGER NOT NULL,
    month                        INTEGER NOT NULL,
    cost_center                  TEXT NOT NULL,
    account                      TEXT NOT NULL,
    sub_account                  TEXT NOT NULL,
    bonus_type                   TEXT,
    amount                       DOUBLE PRECISION NOT NULL,
    currency                     TEXT NOT NULL,
    product                      TEXT NOT NULL,
    distribution_channel         TEXT,
    stat_category                TEXT,
    profit_center                TEXT NOT NULL,
    sender_cost_center           TEXT NOT NULL,
    assignment                   TEXT,
    functional_area              TEXT,
    partner_functional_area_text TEXT,
    is_deleted                   BOOLEAN NOT NULL DEFAULT FALSE,
    deleted_by_ingestion_id      TEXT,
    promoted_at                  TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (actuals_id)
);
-- NOTE: amount is DOUBLE PRECISION per spec §8.2 (source measure), NOT DECIMAL(15,2).
-- The Implementation Guide money rule applies to OBS-computed money, not the
-- raw source amount, which the spec explicitly types DOUBLE PRECISION.

-- obs.employees — Data Integration Spec v1.8 §8.3. [VERIFIED]
-- Natural key: p_number + cost_center. Compensation columns are CONFIDENTIAL.
CREATE TABLE obs.employees (
    employee_id           TEXT NOT NULL,   -- UUID, stable across updates
    p_number              TEXT NOT NULL,   -- part of natural key
    company               TEXT NOT NULL,
    entity                TEXT NOT NULL,
    cost_center           TEXT NOT NULL,   -- part of natural key
    department            TEXT NOT NULL,
    last_name             TEXT NOT NULL,
    first_name            TEXT NOT NULL,
    last_hire_date        DATE NOT NULL,
    salary_structure      TEXT NOT NULL,
    aipeip_eligible       BOOLEAN NOT NULL,   -- derived: salary_structure IN ('EIP','AIP')
    title                 TEXT,
    annual_salary         INTEGER NOT NULL,   -- CONFIDENTIAL (USD)
    home_state            TEXT NOT NULL,
    termination_date      DATE,
    work_state            TEXT NOT NULL,
    office                TEXT NOT NULL,
    workplace_flexibility TEXT NOT NULL,
    management_production TEXT NOT NULL,
    job_grade             TEXT NOT NULL,
    full_time_part_time   TEXT NOT NULL,
    hours_worked          REAL,              -- CONFIDENTIAL
    ot_hours_worked       REAL,              -- CONFIDENTIAL
    fte                   REAL NOT NULL,     -- authoritative for calculations
    is_active             BOOLEAN NOT NULL DEFAULT TRUE,
    last_ingestion_id     TEXT NOT NULL,
    created_at            TIMESTAMPTZ NOT NULL,
    updated_at            TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (employee_id)
);

-- obs.employees_staging — Build Sequencing Plan v1.2 §4.0.3 (source: Data
-- Integration Spec v1.8). [DERIVED]
-- §4.0.3 mandates this table exist, but DI v1.8 §8 defines column-level DDL
-- only for obs.actuals_staging (§8.1) — there is NO §8.x employees_staging
-- schema. Columns below are DERIVED by mirroring the employee source columns
-- (as obs.employees_quarantine §8.7.2 preserves them) plus the staging
-- envelope columns from the actuals_staging pattern (§8.1). Records land here
-- in pipeline Step 4 and are promoted to obs.employees in Step 5, then deleted
-- from staging after successful promotion. VERIFY columns if an authoritative
-- employees_staging schema is published.
CREATE TABLE obs.employees_staging (
    ingestion_id          TEXT NOT NULL,
    staging_id            TEXT NOT NULL,   -- UUID generated per staging record
    p_number              TEXT NOT NULL,   -- part of natural key
    company               TEXT NOT NULL,
    entity                TEXT NOT NULL,
    cost_center           TEXT NOT NULL,   -- part of natural key
    department            TEXT NOT NULL,
    last_name             TEXT NOT NULL,
    first_name            TEXT NOT NULL,
    last_hire_date        DATE NOT NULL,
    salary_structure      TEXT NOT NULL,
    title                 TEXT,
    annual_salary         INTEGER NOT NULL,   -- CONFIDENTIAL (USD)
    home_state            TEXT NOT NULL,
    termination_date      DATE,
    work_state            TEXT NOT NULL,
    office                TEXT NOT NULL,
    workplace_flexibility TEXT NOT NULL,
    management_production TEXT NOT NULL,
    job_grade             TEXT NOT NULL,
    full_time_part_time   TEXT NOT NULL,
    hours_worked          REAL,              -- CONFIDENTIAL
    ot_hours_worked       REAL,              -- CONFIDENTIAL
    fte                   REAL NOT NULL,
    source_file_name      TEXT NOT NULL,
    source_row_number     INTEGER NOT NULL,
    staged_at             TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (staging_id)
);

-- obs.cost_center_hierarchy_nodes — Data Integration Spec v1.8 §8.4.1. [VERIFIED]
CREATE TABLE obs.cost_center_hierarchy_nodes (
    node_id           TEXT NOT NULL,
    hierarchy_id      TEXT NOT NULL,
    node_code         TEXT NOT NULL,   -- unique within hierarchy_id
    node_name         TEXT NOT NULL,
    node_depth        INTEGER NOT NULL,
    parent_node_code  TEXT,            -- NULL for root (level 1)
    is_active         BOOLEAN NOT NULL DEFAULT TRUE,
    last_ingestion_id TEXT NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL,
    updated_at        TIMESTAMPTZ NOT NULL,
    -- transfer_approver_user_id: amendment from Workforce Planning FR v1.1 §3.4
    transfer_approver_user_id TEXT,    -- nullable; FK to obs.users
    PRIMARY KEY (node_id)
);

-- obs.cost_center_hierarchy_memberships — Data Integration Spec v1.8 §8.4.2. [VERIFIED]
CREATE TABLE obs.cost_center_hierarchy_memberships (
    membership_id     TEXT NOT NULL,
    hierarchy_id      TEXT NOT NULL,
    cost_center_code  TEXT NOT NULL,
    cost_center_name  TEXT NOT NULL,
    level_1_code      TEXT NOT NULL,
    level_2_code      TEXT,
    level_3_code      TEXT,
    level_4_code      TEXT,
    level_5_code      TEXT,
    level_6_code      TEXT,
    max_depth         INTEGER NOT NULL,
    is_active         BOOLEAN NOT NULL DEFAULT TRUE,
    last_ingestion_id TEXT NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL,
    updated_at        TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (membership_id)
);

-- obs.account_hierarchy_nodes — Data Integration Spec v1.8 §8.5.1. [VERIFIED]
-- Plus amendments from Workforce Planning FR v1.1 §3.3 (is_personnel_expense,
-- personnel_expense_source).
CREATE TABLE obs.account_hierarchy_nodes (
    node_id                  TEXT NOT NULL,
    hierarchy_id             TEXT NOT NULL,
    node_code                TEXT NOT NULL,
    node_name                TEXT NOT NULL,
    node_depth               INTEGER NOT NULL,
    parent_node_code         TEXT,
    is_active                BOOLEAN NOT NULL DEFAULT TRUE,
    last_ingestion_id        TEXT NOT NULL,
    created_at               TIMESTAMPTZ NOT NULL,
    updated_at               TIMESTAMPTZ NOT NULL,
    is_personnel_expense     BOOLEAN NOT NULL DEFAULT FALSE,  -- WP FR v1.1 §3.3
    personnel_expense_source TEXT,                          -- component_code if is_personnel_expense
    PRIMARY KEY (node_id)
);

-- obs.account_hierarchy_memberships — Data Integration Spec v1.8 §8.5.2. [VERIFIED]
CREATE TABLE obs.account_hierarchy_memberships (
    membership_id     TEXT NOT NULL,
    hierarchy_id      TEXT NOT NULL,
    account           TEXT NOT NULL,
    sub_account       TEXT NOT NULL,
    account_name      TEXT NOT NULL,
    level_1_code      TEXT NOT NULL,
    level_2_code      TEXT,
    level_3_code      TEXT,
    level_4_code      TEXT,
    level_5_code      TEXT,
    level_6_code      TEXT,
    max_depth         INTEGER NOT NULL,
    is_active         BOOLEAN NOT NULL DEFAULT TRUE,
    last_ingestion_id TEXT NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL,
    updated_at        TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (membership_id)
);

-- obs.ingestion_control — Data Integration Spec v1.8 §8.6. [VERIFIED]
CREATE TABLE obs.ingestion_control (
    ingestion_id          TEXT NOT NULL,
    file_name             TEXT NOT NULL,
    content_hash          TEXT NOT NULL,   -- SHA-256 hex; + file_name = dedup key
    source_system         TEXT NOT NULL,   -- ACCOUNTING | HR_ADMIN
    file_type             TEXT NOT NULL,   -- ACTUALS|EMPLOYEES|COST_CENTER_HIERARCHY|ACCOUNT_HIERARCHY
    file_format           TEXT NOT NULL,   -- CSV | EXCEL
    status                TEXT NOT NULL,   -- RUNNING|COMPLETED|PARTIAL|FAILED|QUARANTINED
    re_ingestion          BOOLEAN NOT NULL DEFAULT FALSE,
    original_ingestion_id TEXT,
    total_rows            INTEGER,
    valid_rows            INTEGER,
    quarantined_rows      INTEGER,
    rejected_rows         INTEGER,
    promoted_rows         INTEGER,
    error_rate            DOUBLE PRECISION,             -- file rejected if > 0.80
    triggered_by          TEXT NOT NULL,   -- SCHEDULED | MANUAL(admin user_id)
    started_at            TIMESTAMPTZ NOT NULL,
    completed_at          TIMESTAMPTZ,
    error_detail          TEXT,
    PRIMARY KEY (ingestion_id)
);

-- obs.actuals_quarantine — Data Integration Spec v1.8 §8.7.1. [VERIFIED]
-- = obs.actuals_staging columns + quarantine fields below.
CREATE TABLE obs.actuals_quarantine (
    quarantine_id                TEXT NOT NULL,
    ingestion_id                 TEXT NOT NULL,
    quarantine_reason            TEXT NOT NULL,
    quarantine_status            TEXT NOT NULL,  -- PENDING|RESOLVED|REJECTED
    resolved_by                  TEXT,
    resolved_at                  TIMESTAMPTZ,
    -- preserved source record (obs.actuals_staging columns):
    staging_id                   TEXT,
    entity                       INTEGER,
    year                         INTEGER,
    month                        INTEGER,
    cost_center                  TEXT,
    account                      TEXT,
    sub_account                  TEXT,
    bonus_type                   TEXT,
    amount                       DOUBLE PRECISION,
    currency                     TEXT,
    product                      TEXT,
    distribution_channel         TEXT,
    stat_category                TEXT,
    profit_center                TEXT,
    sender_cost_center           TEXT,
    assignment                   TEXT,
    functional_area              TEXT,
    partner_functional_area_text TEXT,
    source_file_name             TEXT,
    source_row_number            INTEGER NOT NULL,
    staged_at                    TIMESTAMPTZ,
    PRIMARY KEY (quarantine_id)
);

-- obs.employees_quarantine — Data Integration Spec v1.8 §8.7.2. [VERIFIED pattern]
-- Same quarantine field pattern as obs.actuals_quarantine, with employee
-- columns in place of actuals columns.
CREATE TABLE obs.employees_quarantine (
    quarantine_id     TEXT NOT NULL,
    ingestion_id      TEXT NOT NULL,
    quarantine_reason TEXT NOT NULL,
    quarantine_status TEXT NOT NULL,  -- PENDING|RESOLVED|REJECTED
    resolved_by       TEXT,
    resolved_at       TIMESTAMPTZ,
    source_row_number INTEGER NOT NULL,
    -- preserved employee source columns (subset prior to obs.employees UUID assignment):
    p_number              TEXT,
    company               TEXT,
    entity                TEXT,
    cost_center           TEXT,
    department            TEXT,
    last_name             TEXT,
    first_name            TEXT,
    last_hire_date        DATE,
    salary_structure      TEXT,
    title                 TEXT,
    annual_salary         INTEGER,
    home_state            TEXT,
    termination_date      DATE,
    work_state            TEXT,
    office                TEXT,
    workplace_flexibility TEXT,
    management_production TEXT,
    job_grade             TEXT,
    full_time_part_time   TEXT,
    hours_worked          REAL,
    ot_hours_worked       REAL,
    fte                   REAL,
    PRIMARY KEY (quarantine_id)
);

-- obs.cost_center_hierarchy_quarantine — Data Integration Spec v1.8 §8.7.3. [VERIFIED pattern]
CREATE TABLE obs.cost_center_hierarchy_quarantine (
    quarantine_id     TEXT NOT NULL,
    ingestion_id      TEXT NOT NULL,
    quarantine_reason TEXT NOT NULL,
    quarantine_status TEXT NOT NULL,
    resolved_by       TEXT,
    resolved_at       TIMESTAMPTZ,
    source_row_number INTEGER NOT NULL,
    hierarchy_id      TEXT,
    hierarchy_name    TEXT,
    cost_center_code  TEXT,
    cost_center_name  TEXT,
    level_1_code      TEXT,
    level_2_code      TEXT,
    level_3_code      TEXT,
    level_4_code      TEXT,
    level_5_code      TEXT,
    level_6_code      TEXT,
    PRIMARY KEY (quarantine_id)
);

-- obs.account_hierarchy_quarantine — Data Integration Spec v1.8 §8.7.4. [VERIFIED pattern]
CREATE TABLE obs.account_hierarchy_quarantine (
    quarantine_id     TEXT NOT NULL,
    ingestion_id      TEXT NOT NULL,
    quarantine_reason TEXT NOT NULL,
    quarantine_status TEXT NOT NULL,
    resolved_by       TEXT,
    resolved_at       TIMESTAMPTZ,
    source_row_number INTEGER NOT NULL,
    hierarchy_id      TEXT,
    hierarchy_name    TEXT,
    account           TEXT,
    sub_account       TEXT,
    account_name      TEXT,
    level_1_code      TEXT,
    level_2_code      TEXT,
    level_3_code      TEXT,
    level_4_code      TEXT,
    level_5_code      TEXT,
    level_6_code      TEXT,
    PRIMARY KEY (quarantine_id)
);

-- ---------------------------------------------------------------------
-- obs.cost_centers  and  obs.expense_accounts
-- Status: [UNVERIFIED] — NO explicit CREATE TABLE exists in any available
-- spec. Both are referenced as existing dimension tables in Data Integration
-- Spec validation rules (§7.3.2) and the Phase 0 table list, but the FR specs
-- don't define their columns.
-- The columns below are DERIVED from usage (natural keys + names seen in
-- validation and membership tables) and MUST be confirmed before use.
-- Do not treat these as authoritative.
-- ---------------------------------------------------------------------
CREATE TABLE obs.cost_centers (
    cost_center_code TEXT NOT NULL,   -- DERIVED: key used in validation (§7.3.2)
    cost_center_name TEXT,            -- DERIVED
    is_active        BOOLEAN NOT NULL DEFAULT TRUE,  -- DERIVED
    -- UNVERIFIED: full column set unknown. Confirm against an authoritative
    -- cost center dimension definition before relying on this table.
    PRIMARY KEY (cost_center_code)
);

CREATE TABLE obs.expense_accounts (
    account     TEXT NOT NULL,        -- DERIVED: account+sub_account key (§7.3.2)
    sub_account TEXT NOT NULL,        -- DERIVED
    account_name TEXT,               -- DERIVED
    is_active   BOOLEAN NOT NULL DEFAULT TRUE,  -- DERIVED
    -- UNVERIFIED: full column set unknown. Read-only via API (RULE 9 / §6.5).
    PRIMARY KEY (account, sub_account)
);

-- =====================================================================
-- INDEXES (verified from spec where stated)
-- =====================================================================
-- Per-user notification query optimisation is defined for obs.notifications
-- (not in this Option-A subset). Add module indexes with their tables in the
-- Phase that introduces them.
CREATE INDEX idx_actuals_natural_key
    ON obs.actuals (entity, year, month, cost_center, account, sub_account);
CREATE INDEX idx_actuals_not_deleted
    ON obs.actuals (is_deleted);
CREATE INDEX idx_ingestion_control_dedup
    ON obs.ingestion_control (file_name, content_hash, status);

-- =====================================================================
-- SECTION 4 — BUSINESS RULES CONFIGURATION
-- Source: Workforce Planning FR v1.1 §3.1–3.3; Budget Planning FR v1.1 §7.2.3
-- Status: [VERIFIED]
-- =====================================================================

-- obs.merit_increase_rates — Workforce Planning FR v1.1 §3.1. [VERIFIED]
CREATE TABLE obs.merit_increase_rates (
    rate_id        TEXT NOT NULL,
    fiscal_year    INTEGER NOT NULL,
    rate_pct       DECIMAL(6,4) NOT NULL,   -- 3.0000 = 3%
    effective_date DATE NOT NULL,           -- spec default: <fiscal_year>-03-01
    updated_at     TIMESTAMPTZ,
    updated_by     TEXT,
    PRIMARY KEY (fiscal_year)
);

-- obs.compensation_burden_rates — Workforce Planning FR v1.1 §3.2. [VERIFIED]
CREATE TABLE obs.compensation_burden_rates (
    rate_id                     TEXT NOT NULL,
    fiscal_year                 INTEGER NOT NULL,
    fica_rate_pct               DECIMAL(6,4) NOT NULL,
    fica_wage_cap               DECIMAL(12,2) NOT NULL,
    medicare_rate_pct           DECIMAL(6,4) NOT NULL,
    state_income_tax_rate_pct   DECIMAL(6,4) NOT NULL,
    federal_income_tax_rate_pct DECIMAL(6,4) NOT NULL,
    suta_rate_pct               DECIMAL(6,4) NOT NULL,
    suta_wage_cap               DECIMAL(12,2) NOT NULL,
    futa_rate_pct               DECIMAL(6,4) NOT NULL,
    futa_wage_cap               DECIMAL(12,2) NOT NULL,
    other_benefits_rate_pct     DECIMAL(6,4) NOT NULL,
    updated_at                  TIMESTAMPTZ,
    updated_by                  TEXT,
    PRIMARY KEY (fiscal_year)
);

-- obs.compensation_component_mappings — Workforce Planning FR v1.1 §3.3. [VERIFIED]
CREATE TABLE obs.compensation_component_mappings (
    mapping_id     TEXT NOT NULL,
    component_code TEXT NOT NULL,   -- 'salary','aipeip_bonus','burden_fica',...
    account_code   TEXT NOT NULL,   -- FK to obs.account_hierarchy_nodes
    updated_at     TIMESTAMPTZ,
    updated_by     TEXT,
    PRIMARY KEY (component_code)
);

-- obs.overhead_allocation_rates — Budget Planning FR v1.1 §7.2.3. [VERIFIED]
-- rate_period: 'Monthly' | 'Annual'
CREATE TABLE obs.overhead_allocation_rates (
    rate_id             TEXT NOT NULL,
    account_code        TEXT NOT NULL,
    geography_code      TEXT,                       -- NULL = universal
    amount_per_employee DECIMAL(18,2) NOT NULL,
    rate_period         TEXT NOT NULL,
    fiscal_year         INTEGER NOT NULL,
    is_active           BOOLEAN NOT NULL DEFAULT TRUE,
    created_at          TIMESTAMPTZ,
    updated_at          TIMESTAMPTZ,
    updated_by          TEXT,
    PRIMARY KEY (rate_id),
    CHECK (rate_period IN ('Monthly','Annual'))
);

-- =====================================================================
-- SECTION 5 — WORKFORCE PLANNING
-- Source: Workforce Planning FR v1.1 §4 (positions), §7.4 (transfers)
-- Status: [VERIFIED]
-- =====================================================================

-- obs.positions — Workforce Planning FR v1.1 §4. [VERIFIED]
-- position_type: 'filled'|'open'; status: 'active'|'terminated'|'retired'|'transferred_out'
-- Compensation columns (base_salary, salary_adjustment, aipeip_bonus, other_bonus) are CONFIDENTIAL.
CREATE TABLE obs.positions (
    position_id       TEXT NOT NULL,
    fiscal_year       INTEGER NOT NULL,
    cost_center_id    TEXT NOT NULL,   -- FK to obs.cost_center_hierarchy_nodes
    employee_id       TEXT,            -- FK to obs.employees; NULL for open positions
    job_title         TEXT NOT NULL,
    position_type     TEXT NOT NULL,
    base_salary       DECIMAL(12,2) NOT NULL,
    fte               DECIMAL(4,2) NOT NULL,
    start_date        DATE NOT NULL,
    end_date          DATE,               -- set on termination/retirement
    status            TEXT NOT NULL DEFAULT 'active',
    salary_adjustment DECIMAL(12,2) NOT NULL DEFAULT 0.00,
    aipeip_bonus      DECIMAL(12,2) NOT NULL DEFAULT 0.00,
    other_bonus       DECIMAL(12,2) NOT NULL DEFAULT 0.00,
    state_code        TEXT,            -- overhead allocation geography match
    created_at        TIMESTAMPTZ,
    updated_at        TIMESTAMPTZ,
    created_by        TEXT,
    updated_by        TEXT,
    PRIMARY KEY (position_id),
    CHECK (position_type IN ('filled','open')),
    CHECK (status IN ('active','terminated','retired','transferred_out'))
);

-- obs.position_transfers — Workforce Planning FR v1.1 §7.4. [VERIFIED]
-- status: 'pending'|'approved'|'rejected'|'expired'; expires_at = initiated_at + 3 days
CREATE TABLE obs.position_transfers (
    transfer_id              TEXT NOT NULL,
    position_id              TEXT NOT NULL,   -- FK to obs.positions
    sending_cost_center_id   TEXT NOT NULL,
    receiving_cost_center_id TEXT NOT NULL,
    effective_date           DATE NOT NULL,
    status                   TEXT NOT NULL DEFAULT 'pending',
    initiated_by             TEXT NOT NULL,
    initiated_at             TIMESTAMPTZ NOT NULL,
    actioned_by              TEXT,
    actioned_at              TIMESTAMPTZ,
    expires_at               TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (transfer_id),
    CHECK (status IN ('pending','approved','rejected','expired'))
);

-- =====================================================================
-- SECTION 6 — BUDGET PLANNING
-- Source: Budget Planning FR v1.1 §13 (additions §13.2) + §4.3/§6.3/§7.2.3/§8.6
-- Status: [VERIFIED] for targets/vendor/locks; [DERIVED] for budget_versions
--         and budget_lines base tables (see notes).
-- =====================================================================

-- obs.budget_versions — [DERIVED]
-- The BASE table is defined in Budget Planning FR v1.0 §13 (NOT in project).
-- Columns below combine the verified ADDITIONS (Budget Planning FR v1.1 §13.2)
-- with DERIVED base columns inferred from field references throughout v1.1.
-- VERIFY the base columns (version_id, name, fiscal_year, is_active) against
-- Budget Planning FR v1.0 §13 before relying on this table.
CREATE TABLE obs.budget_versions (
    version_id              TEXT NOT NULL,   -- DERIVED (base)
    name                    TEXT NOT NULL,   -- DERIVED (base) — unique per fiscal_year
    fiscal_year             INTEGER NOT NULL,   -- DERIVED (base)
    is_active               BOOLEAN NOT NULL DEFAULT FALSE,  -- DERIVED (base)
    status                  TEXT NOT NULL DEFAULT 'Draft',          -- [VERIFIED] §13.2
    is_visible_to_non_admins BOOLEAN NOT NULL DEFAULT TRUE,            -- [VERIFIED] §13.2
    submitted_at            TIMESTAMPTZ,                                  -- [VERIFIED] §13.2
    submitted_by            TEXT,                                    -- [VERIFIED] §13.2
    created_at              TIMESTAMPTZ,          -- DERIVED (base)
    updated_at              TIMESTAMPTZ,          -- DERIVED (base)
    PRIMARY KEY (version_id),
    CHECK (status IN ('Draft','Submitted','Approved','Rejected','Archived'))
);

-- obs.budget_lines — [DERIVED]
-- The BASE table is defined in Budget Planning FR v1.0 §13 (NOT in project).
-- Verified ADDITIONS (§13.2): is_calculated, source, commentary.
-- DERIVED base columns (line_id, version_id, cost_center_id, account_code,
-- fiscal_year, period_number, amount) inferred from consistent field
-- references in v1.1 (e.g., §submission joins budget_amount/target_amount,
-- AC-BP-BL-04 stores NULL vs 0 amount). VERIFY base columns against
-- Budget Planning FR v1.0 §13 before relying on this table.
CREATE TABLE obs.budget_lines (
    line_id        TEXT NOT NULL,   -- DERIVED (base) — 'line_id' per v1.1 references
    version_id     TEXT NOT NULL,   -- DERIVED (base) — FK to obs.budget_versions
    cost_center_id TEXT NOT NULL,   -- DERIVED (base)
    account_code   TEXT NOT NULL,   -- DERIVED (base)
    fiscal_year    INTEGER NOT NULL,   -- DERIVED (base)
    period_number  INTEGER NOT NULL,   -- DERIVED (base) — 1..12
    amount         DECIMAL(18,2),      -- DERIVED (base) — nullable: NULL vs 0 distinct (AC-BP-BL-04)
    is_calculated  BOOLEAN NOT NULL DEFAULT FALSE,  -- [VERIFIED] §13.2
    source         TEXT NOT NULL DEFAULT 'user_entry',  -- [VERIFIED] §13.2
    commentary     TEXT,                                 -- [VERIFIED] §13.2
    created_at     TIMESTAMPTZ,          -- DERIVED (base)
    updated_at     TIMESTAMPTZ,          -- DERIVED (base)
    updated_by     TEXT,            -- DERIVED (base)
    PRIMARY KEY (line_id),
    CHECK (source IN ('user_entry','overhead_allocation','workforce_planning'))
);

-- obs.budget_targets — Budget Planning FR v1.1 §4.3. [VERIFIED]
CREATE TABLE obs.budget_targets (
    version_id     TEXT NOT NULL,   -- FK to obs.budget_versions
    cost_center_id TEXT NOT NULL,
    account_code   TEXT NOT NULL,
    fiscal_year    INTEGER NOT NULL,
    period_number  INTEGER NOT NULL,   -- 1..12
    target_amount  DECIMAL(18,2),
    created_at     TIMESTAMPTZ,
    updated_at     TIMESTAMPTZ,
    updated_by     TEXT,
    PRIMARY KEY (version_id, cost_center_id, account_code, fiscal_year, period_number)
);

-- obs.vendor_line_items — Budget Planning FR v1.1 §6.3. [VERIFIED]
CREATE TABLE obs.vendor_line_items (
    vendor_line_id TEXT NOT NULL,
    version_id     TEXT NOT NULL,
    cost_center_id TEXT NOT NULL,
    account_code   TEXT NOT NULL,
    fiscal_year    INTEGER NOT NULL,
    vendor_name    TEXT NOT NULL,   -- max 255 chars
    period_number  INTEGER NOT NULL,   -- 1..12 for budget year; 0 for annual
    amount         DECIMAL(18,2),
    created_at     TIMESTAMPTZ,
    updated_at     TIMESTAMPTZ,
    updated_by     TEXT,
    PRIMARY KEY (vendor_line_id)
);

-- obs.budget_version_locks — Budget Planning FR v1.1 §8.6. [VERIFIED]
CREATE TABLE obs.budget_version_locks (
    lock_id        TEXT NOT NULL,
    version_id     TEXT NOT NULL,
    cost_center_id TEXT NOT NULL,
    fiscal_year    INTEGER NOT NULL,
    is_locked      BOOLEAN NOT NULL DEFAULT FALSE,
    locked_at      TIMESTAMPTZ,
    locked_by      TEXT,            -- user_id of locking Administrator
    PRIMARY KEY (version_id, cost_center_id, fiscal_year)
);

-- =====================================================================
-- SECTION 7 — FINANCE APPROVAL WORKFLOW
-- Source: Finance Approval Workflow FR v1.2 §4.3. Status: [VERIFIED]
-- =====================================================================

-- obs.finance_review_records — Finance Approval Workflow FR v1.2 §4.3. [VERIFIED]
-- status: 'pending'|'approved'|'rejected'
CREATE TABLE obs.finance_review_records (
    review_id        TEXT NOT NULL,
    version_id       TEXT NOT NULL,   -- FK to obs.budget_versions
    cost_center_id   TEXT NOT NULL,   -- FK to obs.cost_center_hierarchy_nodes
    fiscal_year      INTEGER NOT NULL,
    status           TEXT NOT NULL DEFAULT 'pending',
    reviewed_by      TEXT,            -- Finance Reviewer or Administrator user_id
    reviewed_at      TIMESTAMPTZ,
    approval_reason  TEXT,            -- max 2,000 chars
    rejection_reason TEXT,            -- max 2,000 chars
    submitted_by     TEXT NOT NULL,
    submitted_at     TIMESTAMPTZ NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL,
    updated_at       TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (review_id),
    UNIQUE (version_id, cost_center_id, fiscal_year),
    CHECK (status IN ('pending','approved','rejected'))
);

-- =====================================================================
-- SECTION 8 — NOTIFICATIONS
-- Source: In-Application Notification Mechanism Specification v1.1 §7.1. [VERIFIED]
-- NOTE: NOT an audit log. is_read and read_at are the only mutable columns.
-- =====================================================================

CREATE TABLE obs.notifications (
    notification_id    TEXT NOT NULL,
    user_id            TEXT NOT NULL,   -- FK to obs.users (recipient)
    event_type         TEXT NOT NULL,
    title              TEXT NOT NULL,   -- max 200 chars
    body               TEXT NOT NULL,   -- max 1,000 chars
    deep_link_url      TEXT,
    is_read            BOOLEAN NOT NULL DEFAULT FALSE,
    read_at            TIMESTAMPTZ,
    source_entity_type TEXT NOT NULL,
    source_entity_id   TEXT NOT NULL,
    created_at         TIMESTAMPTZ NOT NULL,
    expires_at         TIMESTAMPTZ,
    PRIMARY KEY (notification_id)
);

-- =====================================================================
-- SECTION 9 — REPORTING
-- Source: Reporting FR v1.1 §12.1. Status: [VERIFIED]
-- =====================================================================

-- obs.report_definitions — Reporting FR v1.1 §12.1. [VERIFIED]
-- status: 'Draft' | 'Published'; available_columns is a JSON array (stored as text)
CREATE TABLE obs.report_definitions (
    report_id         TEXT NOT NULL,
    report_name       TEXT NOT NULL,   -- unique, max 100 chars
    description       TEXT,            -- max 500 chars
    status            TEXT NOT NULL,   -- 'Draft' | 'Published'
    available_columns TEXT NOT NULL,   -- JSON array
    created_by        TEXT NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL,
    published_at      TIMESTAMPTZ,
    published_by      TEXT,
    updated_at        TIMESTAMPTZ NOT NULL,
    updated_by        TEXT NOT NULL,
    PRIMARY KEY (report_id),
    CHECK (status IN ('Draft','Published'))
);

-- obs.report_annotations — Reporting FR v1.1 §12.1. [VERIFIED]
CREATE TABLE obs.report_annotations (
    annotation_id   TEXT NOT NULL,
    cost_center_id  TEXT NOT NULL,
    account_code    TEXT NOT NULL,
    fiscal_year     INTEGER NOT NULL,
    version_id      TEXT NOT NULL,   -- FK to obs.budget_versions
    annotation_text TEXT,            -- max 1000 chars
    created_by      TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL,
    updated_by      TEXT NOT NULL,
    updated_at      TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (cost_center_id, account_code, fiscal_year, version_id)
);
-- NOTE: annotation_id is a generated UUID per spec, but the PRIMARY KEY is the
-- (cost_center_id, account_code, fiscal_year, version_id) tuple as specified.

-- obs.saved_filters — Reporting FR v1.1 §12.1. [VERIFIED]
CREATE TABLE obs.saved_filters (
    filter_id            TEXT NOT NULL,
    user_id              TEXT NOT NULL,   -- Entra ID oid of owning user
    report_definition_id TEXT NOT NULL,   -- FK to obs.report_definitions
    filter_name          TEXT NOT NULL,   -- max 100 chars; unique per user
    filter_state         TEXT NOT NULL,   -- JSON blob
    created_at           TIMESTAMPTZ NOT NULL,
    updated_at           TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (filter_id),
    UNIQUE (user_id, filter_name)
);

-- =====================================================================
-- SECTION 10 — SECOND-PASS INDEXES (verified from spec where stated)
-- =====================================================================
-- Notification per-user query optimisation — Notification Spec v1.1 §7.1. [VERIFIED]
CREATE INDEX idx_notifications_user_unread_created
    ON obs.notifications (user_id, is_read, created_at DESC);

-- =====================================================================
-- SECTION 11 — PHASE 2 UNIQUE CONSTRAINTS (Data Integration Spec v1.8 §8)
-- Required for idempotent merge() upserts (RULE 10).
-- =====================================================================

-- obs.actuals: partial unique index scoped to active (non-deleted) rows.
-- Allows historical is_deleted=TRUE rows to share a natural key with the
-- current active row; only one active row per natural key is permitted.
CREATE UNIQUE INDEX uq_actuals_natural_key
    ON obs.actuals (entity, year, month, cost_center, account, sub_account)
    WHERE is_deleted = FALSE;

-- obs.employees natural key: p_number + cost_center (DI Spec v1.8 §8.3).
CREATE UNIQUE INDEX uq_employees_natural_key
    ON obs.employees (p_number, cost_center);

-- obs.cost_center_hierarchy_nodes natural key (DI Spec v1.8 §8.4.1).
CREATE UNIQUE INDEX uq_cc_nodes
    ON obs.cost_center_hierarchy_nodes (hierarchy_id, node_code);

-- obs.cost_center_hierarchy_memberships natural key (DI Spec v1.8 §8.4.2).
CREATE UNIQUE INDEX uq_cc_memberships
    ON obs.cost_center_hierarchy_memberships (hierarchy_id, cost_center_code);

-- obs.account_hierarchy_nodes natural key (DI Spec v1.8 §8.5.1).
CREATE UNIQUE INDEX uq_acct_nodes
    ON obs.account_hierarchy_nodes (hierarchy_id, node_code);

-- obs.account_hierarchy_memberships natural key (DI Spec v1.8 §8.5.2).
CREATE UNIQUE INDEX uq_acct_memberships
    ON obs.account_hierarchy_memberships (hierarchy_id, account, sub_account);

-- NOTE: obs.expense_accounts already has PRIMARY KEY (account, sub_account).
-- NOTE: obs.ingestion_control already has idx_ingestion_control_dedup.

-- =====================================================================
-- NOT INCLUDED (no column-level DDL exists in any available spec):
--   obs.fiscal_calendar — referenced only as a read API (Phase 3); the Phase 0
--     table list does not include it and no schema is defined. Add when its
--     authoritative definition is available.
-- TABLES STILL MARKED [UNVERIFIED] (Section 3): obs.cost_centers,
--   obs.expense_accounts — no authoritative DDL in any available document.
-- TABLES MARKED [DERIVED] (Section 6): obs.budget_versions, obs.budget_lines —
--   base columns inferred; verify against Budget Planning FR v1.0 §13.
-- =====================================================================
-- END OF SCHEMA (PostgreSQL) — full obs.* table set per Phase 0 list, plus the
-- hierarchy membership and quarantine tables revealed in Data Integration
-- Spec v1.8 §8.
-- =====================================================================
