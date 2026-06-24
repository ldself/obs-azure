-- =====================================================================
-- schema/seed/020_business_rules.sql
-- Phase 3 local seed data — business rules configuration for FY 2026.
-- All values are illustrative development defaults; they are NOT authoritative
-- rate data. Real values are loaded by Administrators via the Phase 3 API.
-- Idempotent: ON CONFLICT DO NOTHING / DO UPDATE so re-running is safe.
-- =====================================================================

-- -----------------------------------------------------------------------
-- obs.merit_increase_rates — FY 2026 (3.00%)
-- Source: Workforce Planning FR v1.1 §3.1
-- -----------------------------------------------------------------------
INSERT INTO obs.merit_increase_rates
    (rate_id, fiscal_year, rate_pct, effective_date, updated_at, updated_by)
VALUES
    ('merit-2026-seed', 2026, 3.0000, '2026-03-01', '2026-01-01 00:00:00', 'seed')
ON CONFLICT (fiscal_year) DO NOTHING;

-- -----------------------------------------------------------------------
-- obs.compensation_burden_rates — FY 2026
-- Source: Workforce Planning FR v1.1 §3.2
-- FICA: 6.20% / $168,600 cap; Medicare: 1.45% (no cap);
-- SUTA: 2.70% / $7,000 cap (federal reference); FUTA: 0.60% / $7,000 cap
-- -----------------------------------------------------------------------
INSERT INTO obs.compensation_burden_rates
    (rate_id, fiscal_year,
     fica_rate_pct, fica_wage_cap,
     medicare_rate_pct,
     state_income_tax_rate_pct, federal_income_tax_rate_pct,
     suta_rate_pct, suta_wage_cap,
     futa_rate_pct, futa_wage_cap,
     other_benefits_rate_pct,
     updated_at, updated_by)
VALUES
    ('burden-2026-seed', 2026,
     6.2000, 168600.00,
     1.4500,
     0.0000, 0.0000,
     2.7000, 7000.00,
     0.6000, 7000.00,
     5.0000,
     '2026-01-01 00:00:00', 'seed')
ON CONFLICT (fiscal_year) DO NOTHING;

-- -----------------------------------------------------------------------
-- obs.compensation_component_mappings
-- Source: Workforce Planning FR v1.1 §3.3
-- Component codes from bootstrap.sql comment: salary, aipeip_bonus, burden_fica, etc.
-- Account codes are illustrative chart-of-accounts codes.
-- -----------------------------------------------------------------------
INSERT INTO obs.compensation_component_mappings
    (mapping_id, component_code, account_code, updated_at, updated_by)
VALUES
    ('ccm-salary-seed',      'salary',          '5000', '2026-01-01 00:00:00', 'seed'),
    ('ccm-aipeip-seed',      'aipeip_bonus',    '5100', '2026-01-01 00:00:00', 'seed'),
    ('ccm-other-bonus-seed', 'other_bonus',     '5110', '2026-01-01 00:00:00', 'seed'),
    ('ccm-fica-seed',        'burden_fica',     '5200', '2026-01-01 00:00:00', 'seed'),
    ('ccm-medicare-seed',    'burden_medicare', '5210', '2026-01-01 00:00:00', 'seed'),
    ('ccm-suta-seed',        'burden_suta',     '5220', '2026-01-01 00:00:00', 'seed'),
    ('ccm-futa-seed',        'burden_futa',     '5230', '2026-01-01 00:00:00', 'seed'),
    ('ccm-other-seed',       'burden_other',    '5290', '2026-01-01 00:00:00', 'seed')
ON CONFLICT (component_code) DO NOTHING;

-- -----------------------------------------------------------------------
-- obs.overhead_allocation_rates — FY 2026
-- Source: Budget Planning FR v1.1 §7.2.3
-- Universal (NULL geography) monthly rates by account code.
-- -----------------------------------------------------------------------
INSERT INTO obs.overhead_allocation_rates
    (rate_id, account_code, geography_code, amount_per_employee,
     rate_period, fiscal_year, is_active, created_at, updated_at, updated_by)
VALUES
    ('oar-office-2026-seed', '6100', NULL,  450.00, 'Monthly', 2026, TRUE,
     '2026-01-01 00:00:00', '2026-01-01 00:00:00', 'seed'),
    ('oar-it-2026-seed',     '6200', NULL,  125.00, 'Monthly', 2026, TRUE,
     '2026-01-01 00:00:00', '2026-01-01 00:00:00', 'seed'),
    ('oar-hr-2026-seed',     '6300', NULL,   75.00, 'Monthly', 2026, TRUE,
     '2026-01-01 00:00:00', '2026-01-01 00:00:00', 'seed')
ON CONFLICT (rate_id) DO NOTHING;

-- -----------------------------------------------------------------------
-- obs.fiscal_calendar — FY 2026 (January-based fiscal year, 12 periods)
-- Source: Application Architecture Spec v3.6 (Phase 3 read API; schema inferred)
-- -----------------------------------------------------------------------
INSERT INTO obs.fiscal_calendar
    (calendar_id, fiscal_year, period_number, period_name, start_date, end_date, is_current)
VALUES
    ('fc-2026-01-seed', 2026,  1, 'FY2026-P01', '2026-01-01', '2026-01-31', FALSE),
    ('fc-2026-02-seed', 2026,  2, 'FY2026-P02', '2026-02-01', '2026-02-28', FALSE),
    ('fc-2026-03-seed', 2026,  3, 'FY2026-P03', '2026-03-01', '2026-03-31', FALSE),
    ('fc-2026-04-seed', 2026,  4, 'FY2026-P04', '2026-04-01', '2026-04-30', FALSE),
    ('fc-2026-05-seed', 2026,  5, 'FY2026-P05', '2026-05-01', '2026-05-31', FALSE),
    ('fc-2026-06-seed', 2026,  6, 'FY2026-P06', '2026-06-01', '2026-06-30', TRUE),
    ('fc-2026-07-seed', 2026,  7, 'FY2026-P07', '2026-07-01', '2026-07-31', FALSE),
    ('fc-2026-08-seed', 2026,  8, 'FY2026-P08', '2026-08-01', '2026-08-31', FALSE),
    ('fc-2026-09-seed', 2026,  9, 'FY2026-P09', '2026-09-01', '2026-09-30', FALSE),
    ('fc-2026-10-seed', 2026, 10, 'FY2026-P10', '2026-10-01', '2026-10-31', FALSE),
    ('fc-2026-11-seed', 2026, 11, 'FY2026-P11', '2026-11-01', '2026-11-30', FALSE),
    ('fc-2026-12-seed', 2026, 12, 'FY2026-P12', '2026-12-01', '2026-12-31', FALSE)
ON CONFLICT (calendar_id) DO NOTHING;
