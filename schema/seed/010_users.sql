-- =====================================================================
-- schema/seed/010_users.sql
-- Phase 1 local test users (Local Dev Environment Spec v1.1 §6.3).
-- =====================================================================
-- LOCAL_AUTH_BYPASS resolves LOCAL_AUTH_USER_ID against obs.users, so the local
-- environment needs these records to exist. Switch LOCAL_AUTH_USER_ID between
-- them to develop as different roles. Idempotent (RULE 10-style): ON CONFLICT
-- DO NOTHING means re-running db-seed produces no duplicates and no errors.
--
-- Capability flags and grants match the §6.3 role table exactly. Grants are on
-- the local fixture cost center CC-1001.
-- =====================================================================

-- dev-admin-001 — full system access; the default local user.
INSERT INTO obs.users
    (user_id, display_name, email, is_active, is_administrator,
     is_system_modeler, is_report_developer, is_finance_reviewer,
     created_at, created_by, updated_at, updated_by)
VALUES
    ('dev-admin-001', 'Local Admin', 'admin@obs.local', TRUE, TRUE,
     FALSE, FALSE, FALSE,
     '2026-01-01 00:00:00', 'seed', '2026-01-01 00:00:00', 'seed')
ON CONFLICT (user_id) DO NOTHING;

-- dev-costcenter-001 — cost center owner with confidential access on CC-1001.
INSERT INTO obs.users
    (user_id, display_name, email, is_active, is_administrator,
     is_system_modeler, is_report_developer, is_finance_reviewer,
     created_at, created_by, updated_at, updated_by)
VALUES
    ('dev-costcenter-001', 'Cost Center Owner', 'owner@obs.local', TRUE, FALSE,
     FALSE, FALSE, FALSE,
     '2026-01-01 00:00:00', 'seed', '2026-01-01 00:00:00', 'seed')
ON CONFLICT (user_id) DO NOTHING;

-- dev-readonly-001 — read-only on CC-1001, no confidential access.
INSERT INTO obs.users
    (user_id, display_name, email, is_active, is_administrator,
     is_system_modeler, is_report_developer, is_finance_reviewer,
     created_at, created_by, updated_at, updated_by)
VALUES
    ('dev-readonly-001', 'Read Only User', 'readonly@obs.local', TRUE, FALSE,
     FALSE, FALSE, FALSE,
     '2026-01-01 00:00:00', 'seed', '2026-01-01 00:00:00', 'seed')
ON CONFLICT (user_id) DO NOTHING;

-- dev-finance-001 — Finance Reviewer, read-only on CC-1001.
INSERT INTO obs.users
    (user_id, display_name, email, is_active, is_administrator,
     is_system_modeler, is_report_developer, is_finance_reviewer,
     created_at, created_by, updated_at, updated_by)
VALUES
    ('dev-finance-001', 'Finance Reviewer', 'finance@obs.local', TRUE, FALSE,
     FALSE, FALSE, TRUE,
     '2026-01-01 00:00:00', 'seed', '2026-01-01 00:00:00', 'seed')
ON CONFLICT (user_id) DO NOTHING;

-- dev-modeler-001 — System Modeler.
INSERT INTO obs.users
    (user_id, display_name, email, is_active, is_administrator,
     is_system_modeler, is_report_developer, is_finance_reviewer,
     created_at, created_by, updated_at, updated_by)
VALUES
    ('dev-modeler-001', 'System Modeler', 'modeler@obs.local', TRUE, FALSE,
     TRUE, FALSE, FALSE,
     '2026-01-01 00:00:00', 'seed', '2026-01-01 00:00:00', 'seed')
ON CONFLICT (user_id) DO NOTHING;

-- dev-reportdev-001 — Report Developer.
INSERT INTO obs.users
    (user_id, display_name, email, is_active, is_administrator,
     is_system_modeler, is_report_developer, is_finance_reviewer,
     created_at, created_by, updated_at, updated_by)
VALUES
    ('dev-reportdev-001', 'Report Developer', 'reportdev@obs.local', TRUE, FALSE,
     FALSE, TRUE, FALSE,
     '2026-01-01 00:00:00', 'seed', '2026-01-01 00:00:00', 'seed')
ON CONFLICT (user_id) DO NOTHING;

-- Cost center grants on the local fixture cost center CC-1001 (§6.3).
INSERT INTO obs.user_cost_center_grants
    (user_id, cost_center_id, standard_grant, confidential_grant, granted_by, granted_at)
VALUES
    ('dev-costcenter-001', 'CC-1001', 'ALL_WRITE', 'ALL_WRITE', 'seed', '2026-01-01 00:00:00')
ON CONFLICT (user_id, cost_center_id) DO NOTHING;

INSERT INTO obs.user_cost_center_grants
    (user_id, cost_center_id, standard_grant, confidential_grant, granted_by, granted_at)
VALUES
    ('dev-readonly-001', 'CC-1001', 'ALL_READ', 'ALL_NONE', 'seed', '2026-01-01 00:00:00')
ON CONFLICT (user_id, cost_center_id) DO NOTHING;

INSERT INTO obs.user_cost_center_grants
    (user_id, cost_center_id, standard_grant, confidential_grant, granted_by, granted_at)
VALUES
    ('dev-finance-001', 'CC-1001', 'ALL_READ', 'ALL_NONE', 'seed', '2026-01-01 00:00:00')
ON CONFLICT (user_id, cost_center_id) DO NOTHING;
