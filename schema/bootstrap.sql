-- =====================================================================
-- OBS schema/bootstrap.sql  (DuckDB — local development engine)
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
-- Engine note: this is the DuckDB variant. The PostgreSQL variant is in
-- schema/bootstrap_pg.sql. Per Local Dev Spec v1.1 §7 the two are
-- column-for-column identical; only engine-specific syntax differs
-- (audit append-only trigger, timestamp defaults). Schema rules from
-- Claude Code Implementation Guide v1.2 §7:
--   - All primary keys are STRING (UUID) unless a spec states otherwise.
--   - All timestamps are UTC, type TIMESTAMP.
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
    user_id              VARCHAR NOT NULL,   -- Entra ID object ID (oid)
    display_name         VARCHAR NOT NULL,
    email                VARCHAR NOT NULL,
    is_active            BOOLEAN NOT NULL DEFAULT FALSE,
    is_administrator     BOOLEAN NOT NULL DEFAULT FALSE,
    is_system_modeler    BOOLEAN NOT NULL DEFAULT FALSE,
    is_report_developer  BOOLEAN NOT NULL DEFAULT FALSE,
    is_finance_reviewer  BOOLEAN NOT NULL DEFAULT FALSE,
    created_at           TIMESTAMP NOT NULL,
    created_by           VARCHAR,
    updated_at           TIMESTAMP,
    updated_by           VARCHAR,
    PRIMARY KEY (user_id)
);

-- obs.user_cost_center_grants — per-cost-center standard + confidential grants.
-- standard_grant    : ALL_WRITE | ALL_READ              (Security §4.1)
-- confidential_grant: ALL_WRITE | ALL_READ | ALL_NONE   (Security §4.2)
CREATE TABLE obs.user_cost_center_grants (
    user_id            VARCHAR NOT NULL,
    cost_center_id     VARCHAR NOT NULL,
    standard_grant     VARCHAR NOT NULL,
    confidential_grant VARCHAR NOT NULL DEFAULT 'ALL_NONE',
    granted_by         VARCHAR NOT NULL,
    granted_at         TIMESTAMP NOT NULL,
    PRIMARY KEY (user_id, cost_center_id),
    CHECK (standard_grant IN ('ALL_WRITE','ALL_READ')),
    CHECK (confidential_grant IN ('ALL_WRITE','ALL_READ','ALL_NONE'))
);

-- obs.user_rollup_grants — default grants applied across a rollup scope.
CREATE TABLE obs.user_rollup_grants (
    user_id                   VARCHAR NOT NULL,
    rollup_id                 VARCHAR NOT NULL,
    default_standard_grant    VARCHAR NOT NULL,
    default_confidential_grant VARCHAR NOT NULL DEFAULT 'ALL_NONE',
    granted_by                VARCHAR NOT NULL,
    granted_at                TIMESTAMP NOT NULL,
    PRIMARY KEY (user_id, rollup_id),
    CHECK (default_standard_grant IN ('ALL_WRITE','ALL_READ')),
    CHECK (default_confidential_grant IN ('ALL_WRITE','ALL_READ','ALL_NONE'))
);

-- obs.user_rollup_overrides — per-cost-center override within a rollup grant.
CREATE TABLE obs.user_rollup_overrides (
    user_id            VARCHAR NOT NULL,
    rollup_id          VARCHAR NOT NULL,
    cost_center_id     VARCHAR NOT NULL,
    standard_grant     VARCHAR NOT NULL,
    confidential_grant VARCHAR NOT NULL,
    set_by             VARCHAR NOT NULL,
    set_at             TIMESTAMP NOT NULL,
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
    event_id        VARCHAR NOT NULL,
    event_timestamp TIMESTAMP NOT NULL,   -- UTC, millisecond precision
    event_type      VARCHAR NOT NULL,
    user_id         VARCHAR NOT NULL,     -- Entra ID oid of the actor
    session_id      VARCHAR,
    entity_type     VARCHAR,
    entity_id       VARCHAR,
    previous_value  VARCHAR,              -- JSON snapshot (mutations only)
    new_value       VARCHAR,              -- JSON snapshot (mutations only)
    ip_address      VARCHAR,
    outcome         VARCHAR NOT NULL,     -- SUCCESS | FAILURE(+error code)
    PRIMARY KEY (event_id)
);
-- NOTE (DuckDB): append-only is NOT trigger-enforced here. Application code
-- must never issue UPDATE/DELETE against obs.audit_log (RULE 6). The
-- PostgreSQL bootstrap enforces this at the database level.

-- =====================================================================
-- SECTION 3 — DIMENSION / INGESTION TABLES
-- Source: Data Integration Specification v1.8 §8 (carried forward in force
--         by v1.4 §8 and v1.6 §8). Status: [VERIFIED] except where noted.
-- =====================================================================

-- obs.actuals_staging — Data Integration Spec v1.8 §8.1. [VERIFIED]
CREATE TABLE obs.actuals_staging (
    ingestion_id                 VARCHAR NOT NULL,
    staging_id                   VARCHAR NOT NULL,
    entity                       INTEGER NOT NULL,
    year                         INTEGER NOT NULL,
    month                        INTEGER NOT NULL,
    cost_center                  VARCHAR NOT NULL,
    account                      VARCHAR NOT NULL,
    sub_account                  VARCHAR NOT NULL,
    bonus_type                   VARCHAR,
    amount                       DOUBLE NOT NULL,
    currency                     VARCHAR NOT NULL DEFAULT 'USD',
    product                      VARCHAR NOT NULL,
    distribution_channel         VARCHAR,
    stat_category                VARCHAR,
    profit_center                VARCHAR NOT NULL,
    sender_cost_center           VARCHAR NOT NULL,
    assignment                   VARCHAR,
    functional_area              VARCHAR,
    partner_functional_area_text VARCHAR,
    source_file_name             VARCHAR NOT NULL,
    source_row_number            INTEGER NOT NULL,
    staged_at                    TIMESTAMP NOT NULL,
    PRIMARY KEY (staging_id),
    UNIQUE (ingestion_id, source_row_number)   -- RULE 10: atomic upsert guard
);

-- obs.actuals — Data Integration Spec v1.8 §8.2. [VERIFIED]
-- Natural key: entity+year+month+cost_center+account+sub_account.
-- Immutable: versioning via is_deleted (reporting filters is_deleted=FALSE).
CREATE TABLE obs.actuals (
    actuals_id                   VARCHAR NOT NULL,
    ingestion_id                 VARCHAR NOT NULL,
    entity                       INTEGER NOT NULL,
    year                         INTEGER NOT NULL,
    month                        INTEGER NOT NULL,
    cost_center                  VARCHAR NOT NULL,
    account                      VARCHAR NOT NULL,
    sub_account                  VARCHAR NOT NULL,
    bonus_type                   VARCHAR,
    amount                       DOUBLE NOT NULL,
    currency                     VARCHAR NOT NULL,
    product                      VARCHAR NOT NULL,
    distribution_channel         VARCHAR,
    stat_category                VARCHAR,
    profit_center                VARCHAR NOT NULL,
    sender_cost_center           VARCHAR NOT NULL,
    assignment                   VARCHAR,
    functional_area              VARCHAR,
    partner_functional_area_text VARCHAR,
    is_deleted                   BOOLEAN NOT NULL DEFAULT FALSE,
    deleted_by_ingestion_id      VARCHAR,
    promoted_at                  TIMESTAMP NOT NULL,
    PRIMARY KEY (actuals_id)
);
-- NOTE: amount is DOUBLE per spec §8.2 (source measure), NOT DECIMAL(15,2).
-- The Implementation Guide money rule applies to OBS-computed money, not the
-- raw source amount, which the spec explicitly types DOUBLE.

-- obs.employees — Data Integration Spec v1.8 §8.3. [VERIFIED]
-- Natural key: p_number + cost_center. Compensation columns are CONFIDENTIAL.
CREATE TABLE obs.employees (
    employee_id           VARCHAR NOT NULL,   -- UUID, stable across updates
    p_number              VARCHAR NOT NULL,   -- part of natural key
    company               VARCHAR NOT NULL,
    entity                VARCHAR NOT NULL,
    cost_center           VARCHAR NOT NULL,   -- part of natural key
    department            VARCHAR NOT NULL,
    last_name             VARCHAR NOT NULL,
    first_name            VARCHAR NOT NULL,
    last_hire_date        DATE NOT NULL,
    salary_structure      VARCHAR NOT NULL,
    aipeip_eligible       BOOLEAN NOT NULL,   -- derived: salary_structure IN ('EIP','AIP')
    title                 VARCHAR,
    annual_salary         INTEGER NOT NULL,   -- CONFIDENTIAL (USD)
    home_state            VARCHAR NOT NULL,
    termination_date      DATE,
    work_state            VARCHAR NOT NULL,
    office                VARCHAR NOT NULL,
    workplace_flexibility VARCHAR NOT NULL,
    management_production VARCHAR NOT NULL,
    job_grade             VARCHAR NOT NULL,
    full_time_part_time   VARCHAR NOT NULL,
    hours_worked          FLOAT,              -- CONFIDENTIAL
    ot_hours_worked       FLOAT,              -- CONFIDENTIAL
    fte                   FLOAT NOT NULL,     -- authoritative for calculations
    is_active             BOOLEAN NOT NULL DEFAULT TRUE,
    last_ingestion_id     VARCHAR NOT NULL,
    created_at            TIMESTAMP NOT NULL,
    updated_at            TIMESTAMP NOT NULL,
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
    ingestion_id          VARCHAR NOT NULL,
    staging_id            VARCHAR NOT NULL,   -- UUID generated per staging record
    p_number              VARCHAR NOT NULL,   -- part of natural key
    company               VARCHAR NOT NULL,
    entity                VARCHAR NOT NULL,
    cost_center           VARCHAR NOT NULL,   -- part of natural key
    department            VARCHAR NOT NULL,
    last_name             VARCHAR NOT NULL,
    first_name            VARCHAR NOT NULL,
    last_hire_date        DATE NOT NULL,
    salary_structure      VARCHAR NOT NULL,
    title                 VARCHAR,
    annual_salary         INTEGER NOT NULL,   -- CONFIDENTIAL (USD)
    home_state            VARCHAR NOT NULL,
    termination_date      DATE,
    work_state            VARCHAR NOT NULL,
    office                VARCHAR NOT NULL,
    workplace_flexibility VARCHAR NOT NULL,
    management_production VARCHAR NOT NULL,
    job_grade             VARCHAR NOT NULL,
    full_time_part_time   VARCHAR NOT NULL,
    hours_worked          FLOAT,              -- CONFIDENTIAL
    ot_hours_worked       FLOAT,              -- CONFIDENTIAL
    fte                   FLOAT NOT NULL,
    source_file_name      VARCHAR NOT NULL,
    source_row_number     INTEGER NOT NULL,
    staged_at             TIMESTAMP NOT NULL,
    PRIMARY KEY (staging_id)
);

-- obs.cost_center_hierarchy_nodes — Data Integration Spec v1.8 §8.4.1. [VERIFIED]
CREATE TABLE obs.cost_center_hierarchy_nodes (
    node_id           VARCHAR NOT NULL,
    hierarchy_id      VARCHAR NOT NULL,
    node_code         VARCHAR NOT NULL,   -- unique within hierarchy_id
    node_name         VARCHAR NOT NULL,
    node_depth        INTEGER NOT NULL,
    parent_node_code  VARCHAR,            -- NULL for root (level 1)
    is_active         BOOLEAN NOT NULL DEFAULT TRUE,
    last_ingestion_id VARCHAR NOT NULL,
    created_at        TIMESTAMP NOT NULL,
    updated_at        TIMESTAMP NOT NULL,
    -- transfer_approver_user_id: amendment from Workforce Planning FR v1.1 §3.4
    transfer_approver_user_id VARCHAR,    -- nullable; FK to obs.users
    PRIMARY KEY (node_id)
);

-- obs.cost_center_hierarchy_memberships — Data Integration Spec v1.8 §8.4.2. [VERIFIED]
CREATE TABLE obs.cost_center_hierarchy_memberships (
    membership_id     VARCHAR NOT NULL,
    hierarchy_id      VARCHAR NOT NULL,
    cost_center_code  VARCHAR NOT NULL,
    cost_center_name  VARCHAR NOT NULL,
    level_1_code      VARCHAR NOT NULL,
    level_2_code      VARCHAR,
    level_3_code      VARCHAR,
    level_4_code      VARCHAR,
    level_5_code      VARCHAR,
    level_6_code      VARCHAR,
    max_depth         INTEGER NOT NULL,
    is_active         BOOLEAN NOT NULL DEFAULT TRUE,
    last_ingestion_id VARCHAR NOT NULL,
    created_at        TIMESTAMP NOT NULL,
    updated_at        TIMESTAMP NOT NULL,
    PRIMARY KEY (membership_id)
);

-- obs.account_hierarchy_nodes — Data Integration Spec v1.8 §8.5.1. [VERIFIED]
-- Plus amendments from Workforce Planning FR v1.1 §3.3 (is_personnel_expense,
-- personnel_expense_source).
CREATE TABLE obs.account_hierarchy_nodes (
    node_id                  VARCHAR NOT NULL,
    hierarchy_id             VARCHAR NOT NULL,
    node_code                VARCHAR NOT NULL,
    node_name                VARCHAR NOT NULL,
    node_depth               INTEGER NOT NULL,
    parent_node_code         VARCHAR,
    is_active                BOOLEAN NOT NULL DEFAULT TRUE,
    last_ingestion_id        VARCHAR NOT NULL,
    created_at               TIMESTAMP NOT NULL,
    updated_at               TIMESTAMP NOT NULL,
    is_personnel_expense     BOOLEAN NOT NULL DEFAULT FALSE,  -- WP FR v1.1 §3.3
    personnel_expense_source VARCHAR,                          -- component_code if is_personnel_expense
    PRIMARY KEY (node_id)
);

-- obs.account_hierarchy_memberships — Data Integration Spec v1.8 §8.5.2. [VERIFIED]
CREATE TABLE obs.account_hierarchy_memberships (
    membership_id     VARCHAR NOT NULL,
    hierarchy_id      VARCHAR NOT NULL,
    account           VARCHAR NOT NULL,
    sub_account       VARCHAR NOT NULL,
    account_name      VARCHAR NOT NULL,
    level_1_code      VARCHAR NOT NULL,
    level_2_code      VARCHAR,
    level_3_code      VARCHAR,
    level_4_code      VARCHAR,
    level_5_code      VARCHAR,
    level_6_code      VARCHAR,
    max_depth         INTEGER NOT NULL,
    is_active         BOOLEAN NOT NULL DEFAULT TRUE,
    last_ingestion_id VARCHAR NOT NULL,
    created_at        TIMESTAMP NOT NULL,
    updated_at        TIMESTAMP NOT NULL,
    PRIMARY KEY (membership_id)
);

-- obs.ingestion_control — Data Integration Spec v1.8 §8.6. [VERIFIED]
CREATE TABLE obs.ingestion_control (
    ingestion_id          VARCHAR NOT NULL,
    file_name             VARCHAR NOT NULL,
    content_hash          VARCHAR NOT NULL,   -- SHA-256 hex; + file_name = dedup key
    source_system         VARCHAR NOT NULL,   -- ACCOUNTING | HR_ADMIN
    file_type             VARCHAR NOT NULL,   -- ACTUALS|EMPLOYEES|COST_CENTER_HIERARCHY|ACCOUNT_HIERARCHY
    file_format           VARCHAR NOT NULL,   -- CSV | EXCEL
    status                VARCHAR NOT NULL,   -- RUNNING|COMPLETED|PARTIAL|FAILED|QUARANTINED
    re_ingestion          BOOLEAN NOT NULL DEFAULT FALSE,
    original_ingestion_id VARCHAR,
    total_rows            INTEGER,
    valid_rows            INTEGER,
    quarantined_rows      INTEGER,
    rejected_rows         INTEGER,
    promoted_rows         INTEGER,
    error_rate            DOUBLE,             -- file rejected if > 0.80
    triggered_by          VARCHAR NOT NULL,   -- SCHEDULED | MANUAL(admin user_id)
    started_at            TIMESTAMP NOT NULL,
    completed_at          TIMESTAMP,
    error_detail          VARCHAR,
    PRIMARY KEY (ingestion_id)
);

-- obs.actuals_quarantine — Data Integration Spec v1.8 §8.7.1. [VERIFIED]
-- = obs.actuals_staging columns + quarantine fields below.
CREATE TABLE obs.actuals_quarantine (
    quarantine_id                VARCHAR NOT NULL,
    ingestion_id                 VARCHAR NOT NULL,
    quarantine_reason            VARCHAR NOT NULL,
    quarantine_status            VARCHAR NOT NULL,  -- PENDING|RESOLVED|REJECTED
    resolved_by                  VARCHAR,
    resolved_at                  TIMESTAMP,
    -- preserved source record (obs.actuals_staging columns):
    staging_id                   VARCHAR,
    entity                       INTEGER,
    year                         INTEGER,
    month                        INTEGER,
    cost_center                  VARCHAR,
    account                      VARCHAR,
    sub_account                  VARCHAR,
    bonus_type                   VARCHAR,
    amount                       DOUBLE,
    currency                     VARCHAR,
    product                      VARCHAR,
    distribution_channel         VARCHAR,
    stat_category                VARCHAR,
    profit_center                VARCHAR,
    sender_cost_center           VARCHAR,
    assignment                   VARCHAR,
    functional_area              VARCHAR,
    partner_functional_area_text VARCHAR,
    source_file_name             VARCHAR,
    source_row_number            INTEGER NOT NULL,
    staged_at                    TIMESTAMP,
    PRIMARY KEY (quarantine_id)
);

-- obs.employees_quarantine — Data Integration Spec v1.8 §8.7.2. [VERIFIED pattern]
-- Same quarantine field pattern as obs.actuals_quarantine, with employee
-- columns in place of actuals columns.
CREATE TABLE obs.employees_quarantine (
    quarantine_id     VARCHAR NOT NULL,
    ingestion_id      VARCHAR NOT NULL,
    quarantine_reason VARCHAR NOT NULL,
    quarantine_status VARCHAR NOT NULL,  -- PENDING|RESOLVED|REJECTED
    resolved_by       VARCHAR,
    resolved_at       TIMESTAMP,
    source_row_number INTEGER NOT NULL,
    -- preserved employee source columns (subset prior to obs.employees UUID assignment):
    p_number              VARCHAR,
    company               VARCHAR,
    entity                VARCHAR,
    cost_center           VARCHAR,
    department            VARCHAR,
    last_name             VARCHAR,
    first_name            VARCHAR,
    last_hire_date        DATE,
    salary_structure      VARCHAR,
    title                 VARCHAR,
    annual_salary         INTEGER,
    home_state            VARCHAR,
    termination_date      DATE,
    work_state            VARCHAR,
    office                VARCHAR,
    workplace_flexibility VARCHAR,
    management_production VARCHAR,
    job_grade             VARCHAR,
    full_time_part_time   VARCHAR,
    hours_worked          FLOAT,
    ot_hours_worked       FLOAT,
    fte                   FLOAT,
    PRIMARY KEY (quarantine_id)
);

-- obs.cost_center_hierarchy_quarantine — Data Integration Spec v1.8 §8.7.3. [VERIFIED pattern]
CREATE TABLE obs.cost_center_hierarchy_quarantine (
    quarantine_id     VARCHAR NOT NULL,
    ingestion_id      VARCHAR NOT NULL,
    quarantine_reason VARCHAR NOT NULL,
    quarantine_status VARCHAR NOT NULL,
    resolved_by       VARCHAR,
    resolved_at       TIMESTAMP,
    source_row_number INTEGER NOT NULL,
    hierarchy_id      VARCHAR,
    hierarchy_name    VARCHAR,
    cost_center_code  VARCHAR,
    cost_center_name  VARCHAR,
    level_1_code      VARCHAR,
    level_2_code      VARCHAR,
    level_3_code      VARCHAR,
    level_4_code      VARCHAR,
    level_5_code      VARCHAR,
    level_6_code      VARCHAR,
    PRIMARY KEY (quarantine_id)
);

-- obs.account_hierarchy_quarantine — Data Integration Spec v1.8 §8.7.4. [VERIFIED pattern]
CREATE TABLE obs.account_hierarchy_quarantine (
    quarantine_id     VARCHAR NOT NULL,
    ingestion_id      VARCHAR NOT NULL,
    quarantine_reason VARCHAR NOT NULL,
    quarantine_status VARCHAR NOT NULL,
    resolved_by       VARCHAR,
    resolved_at       TIMESTAMP,
    source_row_number INTEGER NOT NULL,
    hierarchy_id      VARCHAR,
    hierarchy_name    VARCHAR,
    account           VARCHAR,
    sub_account       VARCHAR,
    account_name      VARCHAR,
    level_1_code      VARCHAR,
    level_2_code      VARCHAR,
    level_3_code      VARCHAR,
    level_4_code      VARCHAR,
    level_5_code      VARCHAR,
    level_6_code      VARCHAR,
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
    cost_center_code VARCHAR NOT NULL,   -- DERIVED: key used in validation (§7.3.2)
    cost_center_name VARCHAR,            -- DERIVED
    is_active        BOOLEAN NOT NULL DEFAULT TRUE,  -- DERIVED
    -- UNVERIFIED: full column set unknown. Confirm against an authoritative
    -- cost center dimension definition before relying on this table.
    PRIMARY KEY (cost_center_code)
);

CREATE TABLE obs.expense_accounts (
    account     VARCHAR NOT NULL,        -- DERIVED: account+sub_account key (§7.3.2)
    sub_account VARCHAR NOT NULL,        -- DERIVED
    account_name VARCHAR,               -- DERIVED
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
-- NOTE: DuckDB does not support partial (filtered) unique indexes.
-- The RULE 10 atomic dedup guard (partial unique index on COMPLETED records) is
-- defined in bootstrap_pg.sql (PostgreSQL only).  Locally, check_duplicate()
-- provides the dedup guard; ON CONFLICT DO NOTHING in create_ingestion_record
-- is valid DuckDB syntax (simply never triggers) and satisfies the code contract.

-- =====================================================================
-- SECTION 4 — BUSINESS RULES CONFIGURATION
-- Source: Workforce Planning FR v1.1 §3.1–3.3; Budget Planning FR v1.1 §7.2.3
-- Status: [VERIFIED]
-- =====================================================================

-- obs.merit_increase_rates — Workforce Planning FR v1.1 §3.1. [VERIFIED]
CREATE TABLE obs.merit_increase_rates (
    rate_id        VARCHAR NOT NULL,
    fiscal_year    INTEGER NOT NULL,
    rate_pct       DECIMAL(6,4) NOT NULL,   -- 3.0000 = 3%
    effective_date DATE NOT NULL,           -- spec default: <fiscal_year>-03-01
    updated_at     TIMESTAMP,
    updated_by     VARCHAR,
    PRIMARY KEY (fiscal_year)
);

-- obs.compensation_burden_rates — Workforce Planning FR v1.1 §3.2. [VERIFIED]
CREATE TABLE obs.compensation_burden_rates (
    rate_id                     VARCHAR NOT NULL,
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
    updated_at                  TIMESTAMP,
    updated_by                  VARCHAR,
    PRIMARY KEY (fiscal_year)
);

-- obs.compensation_component_mappings — Workforce Planning FR v1.1 §3.3. [VERIFIED]
CREATE TABLE obs.compensation_component_mappings (
    mapping_id     VARCHAR NOT NULL,
    component_code VARCHAR NOT NULL,   -- 'salary','aipeip_bonus','burden_fica',...
    account_code   VARCHAR NOT NULL,   -- FK to obs.account_hierarchy_nodes
    updated_at     TIMESTAMP,
    updated_by     VARCHAR,
    PRIMARY KEY (component_code)
);

-- obs.overhead_allocation_rates — Budget Planning FR v1.1 §7.2.3. [VERIFIED]
-- rate_period: 'Monthly' | 'Annual'
CREATE TABLE obs.overhead_allocation_rates (
    rate_id             VARCHAR NOT NULL,
    account_code        VARCHAR NOT NULL,
    geography_code      VARCHAR,                       -- NULL = universal
    amount_per_employee DECIMAL(18,2) NOT NULL,
    rate_period         VARCHAR NOT NULL,
    fiscal_year         INTEGER NOT NULL,
    is_active           BOOLEAN NOT NULL DEFAULT TRUE,
    created_at          TIMESTAMP,
    updated_at          TIMESTAMP,
    updated_by          VARCHAR,
    PRIMARY KEY (rate_id),
    CHECK (rate_period IN ('Monthly','Annual'))
);

-- obs.fiscal_calendar — Application Architecture Spec v3.6 (Phase 3 read API).
-- Status: [INFERRED] — no explicit DDL in available spec; schema inferred from
--   fiscal_year + period_number usage in obs.budget_lines and obs.actuals.
CREATE TABLE obs.fiscal_calendar (
    calendar_id   VARCHAR NOT NULL,
    fiscal_year   INTEGER NOT NULL,
    period_number INTEGER NOT NULL,
    period_name   VARCHAR NOT NULL,   -- e.g. 'FY2026-P01'
    start_date    DATE    NOT NULL,
    end_date      DATE    NOT NULL,
    is_current    BOOLEAN NOT NULL DEFAULT FALSE,
    PRIMARY KEY (calendar_id),
    UNIQUE (fiscal_year, period_number),
    CHECK (period_number BETWEEN 1 AND 13)
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
    position_id       VARCHAR NOT NULL,
    fiscal_year       INTEGER NOT NULL,
    cost_center_id    VARCHAR NOT NULL,   -- FK to obs.cost_center_hierarchy_nodes
    employee_id       VARCHAR,            -- FK to obs.employees; NULL for open positions
    job_title         VARCHAR NOT NULL,
    position_type     VARCHAR NOT NULL,
    base_salary       DECIMAL(12,2) NOT NULL,
    fte               DECIMAL(4,2) NOT NULL,
    start_date        DATE NOT NULL,
    end_date          DATE,               -- set on termination/retirement
    status            VARCHAR NOT NULL DEFAULT 'active',
    salary_adjustment DECIMAL(12,2) NOT NULL DEFAULT 0.00,
    aipeip_bonus      DECIMAL(12,2) NOT NULL DEFAULT 0.00,
    other_bonus       DECIMAL(12,2) NOT NULL DEFAULT 0.00,
    state_code        VARCHAR,            -- overhead allocation geography match
    created_at        TIMESTAMP,
    updated_at        TIMESTAMP,
    created_by        VARCHAR,
    updated_by        VARCHAR,
    PRIMARY KEY (position_id),
    CHECK (position_type IN ('filled','open')),
    CHECK (status IN ('active','terminated','retired','transferred_out'))
);

-- obs.position_transfers — Workforce Planning FR v1.1 §7.4. [VERIFIED]
-- status: 'pending'|'approved'|'rejected'|'expired'; expires_at = initiated_at + 3 days
CREATE TABLE obs.position_transfers (
    transfer_id              VARCHAR NOT NULL,
    position_id              VARCHAR NOT NULL,   -- FK to obs.positions
    sending_cost_center_id   VARCHAR NOT NULL,
    receiving_cost_center_id VARCHAR NOT NULL,
    effective_date           DATE NOT NULL,
    status                   VARCHAR NOT NULL DEFAULT 'pending',
    initiated_by             VARCHAR NOT NULL,
    initiated_at             TIMESTAMP NOT NULL,
    actioned_by              VARCHAR,
    actioned_at              TIMESTAMP,
    expires_at               TIMESTAMP NOT NULL,
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
    version_id              VARCHAR NOT NULL,   -- DERIVED (base)
    name                    VARCHAR NOT NULL,   -- DERIVED (base) — unique per fiscal_year
    fiscal_year             INTEGER NOT NULL,   -- DERIVED (base)
    is_active               BOOLEAN NOT NULL DEFAULT FALSE,  -- DERIVED (base)
    status                  VARCHAR NOT NULL DEFAULT 'Draft',          -- [VERIFIED] §13.2
    is_visible_to_non_admins BOOLEAN NOT NULL DEFAULT TRUE,            -- [VERIFIED] §13.2
    submitted_at            TIMESTAMP,                                  -- [VERIFIED] §13.2
    submitted_by            VARCHAR,                                    -- [VERIFIED] §13.2
    created_at              TIMESTAMP,          -- DERIVED (base)
    updated_at              TIMESTAMP,          -- DERIVED (base)
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
    line_id        VARCHAR NOT NULL,   -- DERIVED (base) — 'line_id' per v1.1 references
    version_id     VARCHAR NOT NULL,   -- DERIVED (base) — FK to obs.budget_versions
    cost_center_id VARCHAR NOT NULL,   -- DERIVED (base)
    account_code   VARCHAR NOT NULL,   -- DERIVED (base)
    fiscal_year    INTEGER NOT NULL,   -- DERIVED (base)
    period_number  INTEGER NOT NULL,   -- DERIVED (base) — 1..12
    amount         DECIMAL(18,2),      -- DERIVED (base) — nullable: NULL vs 0 distinct (AC-BP-BL-04)
    is_calculated  BOOLEAN NOT NULL DEFAULT FALSE,  -- [VERIFIED] §13.2
    source         VARCHAR NOT NULL DEFAULT 'user_entry',  -- [VERIFIED] §13.2
    commentary     VARCHAR,                                 -- [VERIFIED] §13.2
    created_at     TIMESTAMP,          -- DERIVED (base)
    updated_at     TIMESTAMP,          -- DERIVED (base)
    updated_by     VARCHAR,            -- DERIVED (base)
    PRIMARY KEY (line_id),
    CHECK (source IN ('user_entry','overhead_allocation','workforce_planning'))
);

-- obs.budget_targets — Budget Planning FR v1.1 §4.3. [VERIFIED]
CREATE TABLE obs.budget_targets (
    version_id     VARCHAR NOT NULL,   -- FK to obs.budget_versions
    cost_center_id VARCHAR NOT NULL,
    account_code   VARCHAR NOT NULL,
    fiscal_year    INTEGER NOT NULL,
    period_number  INTEGER NOT NULL,   -- 1..12
    target_amount  DECIMAL(18,2),
    created_at     TIMESTAMP,
    updated_at     TIMESTAMP,
    updated_by     VARCHAR,
    PRIMARY KEY (version_id, cost_center_id, account_code, fiscal_year, period_number)
);

-- obs.vendor_line_items — Budget Planning FR v1.1 §6.3. [VERIFIED]
CREATE TABLE obs.vendor_line_items (
    vendor_line_id VARCHAR NOT NULL,
    version_id     VARCHAR NOT NULL,
    cost_center_id VARCHAR NOT NULL,
    account_code   VARCHAR NOT NULL,
    fiscal_year    INTEGER NOT NULL,
    vendor_name    VARCHAR NOT NULL,   -- max 255 chars
    period_number  INTEGER NOT NULL,   -- 1..12 for budget year; 0 for annual
    amount         DECIMAL(18,2),
    created_at     TIMESTAMP,
    updated_at     TIMESTAMP,
    updated_by     VARCHAR,
    PRIMARY KEY (vendor_line_id)
);

-- obs.budget_version_locks — Budget Planning FR v1.1 §8.6. [VERIFIED]
CREATE TABLE obs.budget_version_locks (
    lock_id        VARCHAR NOT NULL,
    version_id     VARCHAR NOT NULL,
    cost_center_id VARCHAR NOT NULL,
    fiscal_year    INTEGER NOT NULL,
    is_locked      BOOLEAN NOT NULL DEFAULT FALSE,
    locked_at      TIMESTAMP,
    locked_by      VARCHAR,            -- user_id of locking Administrator
    PRIMARY KEY (version_id, cost_center_id, fiscal_year)
);

-- =====================================================================
-- SECTION 7 — FINANCE APPROVAL WORKFLOW
-- Source: Finance Approval Workflow FR v1.2 §4.3. Status: [VERIFIED]
-- =====================================================================

-- obs.finance_review_records — Finance Approval Workflow FR v1.2 §4.3. [VERIFIED]
-- status: 'pending'|'approved'|'rejected'
CREATE TABLE obs.finance_review_records (
    review_id        VARCHAR NOT NULL,
    version_id       VARCHAR NOT NULL,   -- FK to obs.budget_versions
    cost_center_id   VARCHAR NOT NULL,   -- FK to obs.cost_center_hierarchy_nodes
    fiscal_year      INTEGER NOT NULL,
    status           VARCHAR NOT NULL DEFAULT 'pending',
    reviewed_by      VARCHAR,            -- Finance Reviewer or Administrator user_id
    reviewed_at      TIMESTAMP,
    approval_reason  VARCHAR,            -- max 2,000 chars
    rejection_reason VARCHAR,            -- max 2,000 chars
    submitted_by     VARCHAR NOT NULL,
    submitted_at     TIMESTAMP NOT NULL,
    created_at       TIMESTAMP NOT NULL,
    updated_at       TIMESTAMP NOT NULL,
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
    notification_id    VARCHAR NOT NULL,
    user_id            VARCHAR NOT NULL,   -- FK to obs.users (recipient)
    event_type         VARCHAR NOT NULL,
    title              VARCHAR NOT NULL,   -- max 200 chars
    body               VARCHAR NOT NULL,   -- max 1,000 chars
    deep_link_url      VARCHAR,
    is_read            BOOLEAN NOT NULL DEFAULT FALSE,
    read_at            TIMESTAMP,
    source_entity_type VARCHAR NOT NULL,
    source_entity_id   VARCHAR NOT NULL,
    created_at         TIMESTAMP NOT NULL,
    expires_at         TIMESTAMP,
    PRIMARY KEY (notification_id)
);

-- =====================================================================
-- SECTION 9 — REPORTING
-- Source: Reporting FR v1.1 §12.1. Status: [VERIFIED]
-- =====================================================================

-- obs.report_definitions — Reporting FR v1.1 §12.1. [VERIFIED]
-- status: 'Draft' | 'Published'; available_columns is a JSON array (stored as text)
CREATE TABLE obs.report_definitions (
    report_id         VARCHAR NOT NULL,
    report_name       VARCHAR NOT NULL,   -- unique, max 100 chars
    description       VARCHAR,            -- max 500 chars
    status            VARCHAR NOT NULL,   -- 'Draft' | 'Published'
    available_columns VARCHAR NOT NULL,   -- JSON array
    created_by        VARCHAR NOT NULL,
    created_at        TIMESTAMP NOT NULL,
    published_at      TIMESTAMP,
    published_by      VARCHAR,
    updated_at        TIMESTAMP NOT NULL,
    updated_by        VARCHAR NOT NULL,
    PRIMARY KEY (report_id),
    CHECK (status IN ('Draft','Published'))
);

-- obs.report_annotations — Reporting FR v1.1 §12.1. [VERIFIED]
CREATE TABLE obs.report_annotations (
    annotation_id   VARCHAR NOT NULL,
    cost_center_id  VARCHAR NOT NULL,
    account_code    VARCHAR NOT NULL,
    fiscal_year     INTEGER NOT NULL,
    version_id      VARCHAR NOT NULL,   -- FK to obs.budget_versions
    annotation_text VARCHAR,            -- max 1000 chars
    created_by      VARCHAR NOT NULL,
    created_at      TIMESTAMP NOT NULL,
    updated_by      VARCHAR NOT NULL,
    updated_at      TIMESTAMP NOT NULL,
    PRIMARY KEY (cost_center_id, account_code, fiscal_year, version_id)
);
-- NOTE: annotation_id is a generated UUID per spec, but the PRIMARY KEY is the
-- (cost_center_id, account_code, fiscal_year, version_id) tuple as specified.

-- obs.saved_filters — Reporting FR v1.1 §12.1. [VERIFIED]
CREATE TABLE obs.saved_filters (
    filter_id            VARCHAR NOT NULL,
    user_id              VARCHAR NOT NULL,   -- Entra ID oid of owning user
    report_definition_id VARCHAR NOT NULL,   -- FK to obs.report_definitions
    filter_name          VARCHAR NOT NULL,   -- max 100 chars; unique per user
    filter_state         VARCHAR NOT NULL,   -- JSON blob
    created_at           TIMESTAMP NOT NULL,
    updated_at           TIMESTAMP NOT NULL,
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

-- obs.actuals: no UNIQUE constraint — soft-delete versioning means multiple rows
-- may share a natural key (one active, N historical is_deleted=TRUE). Idempotency
-- is enforced at the file level via (file_name, content_hash) in ingestion_control
-- (RULE 10). DuckDB does not support partial indexes; PostgreSQL bootstrap carries
-- the partial UNIQUE index for active-row integrity (see bootstrap_pg.sql).

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
--   obs.fiscal_calendar — added in Phase 3 (schema inferred; see Section 4).
-- TABLES STILL MARKED [UNVERIFIED] (Section 3): obs.cost_centers,
--   obs.expense_accounts — no authoritative DDL in any available document.
-- TABLES MARKED [DERIVED] (Section 6): obs.budget_versions, obs.budget_lines —
--   base columns inferred; verify against Budget Planning FR v1.0 §13.
-- =====================================================================
-- END OF SCHEMA (DuckDB) — full obs.* table set per Phase 0 list, plus the
-- hierarchy membership and quarantine tables revealed in Data Integration
-- Spec v1.8 §8.
-- =====================================================================
