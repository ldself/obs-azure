// Shared API types. Field names mirror the backend obs.* schema and Pydantic
// models exactly (RULE 1). The frontend is a display layer only (RULE 3) — these
// types describe server payloads; no business logic is derived here.

export type StandardGrant = "ALL_WRITE" | "ALL_READ";
export type ConfidentialGrant = "ALL_WRITE" | "ALL_READ" | "ALL_NONE";

export interface User {
  user_id: string;
  display_name: string;
  email: string;
  is_active: boolean;
  is_administrator: boolean;
  is_system_modeler: boolean;
  is_report_developer: boolean;
  is_finance_reviewer: boolean;
}

// The authenticated caller's own profile (GET /api/v1/me). Identical shape to User.
export type Me = User;

export interface UserCreate {
  user_id: string;
  display_name: string;
  email: string;
  is_active?: boolean;
  is_administrator?: boolean;
  is_system_modeler?: boolean;
  is_report_developer?: boolean;
  is_finance_reviewer?: boolean;
}

export interface UserUpdate {
  is_active?: boolean;
  is_administrator?: boolean;
  is_system_modeler?: boolean;
  is_report_developer?: boolean;
  is_finance_reviewer?: boolean;
}

export interface CostCenterGrant {
  user_id: string;
  cost_center_id: string;
  standard_grant: StandardGrant;
  confidential_grant: ConfidentialGrant;
  granted_by: string;
  granted_at: string;
}

export interface CostCenterGrantCreate {
  cost_center_id: string;
  standard_grant: StandardGrant;
  confidential_grant?: ConfidentialGrant;
}

export interface RollupGrantCreate {
  rollup_id: string;
  default_standard_grant: StandardGrant;
  default_confidential_grant?: ConfidentialGrant;
}

// ---------------------------------------------------------------------------
// Phase 2 — Ingestion monitoring (Data Integration Spec v1.8 §8.6)
// ---------------------------------------------------------------------------

export type IngestionStatus =
  | "RUNNING"
  | "COMPLETED"
  | "PARTIAL"
  | "QUARANTINED"
  | "FAILED";

export type FileType =
  | "ACTUALS"
  | "EMPLOYEES"
  | "COST_CENTER_HIERARCHY"
  | "ACCOUNT_HIERARCHY";

export interface IngestionRecord {
  ingestion_id: string;
  file_name: string;
  content_hash: string;
  source_system: string;
  file_type: FileType;
  file_format: string;
  status: IngestionStatus;
  re_ingestion: boolean;
  original_ingestion_id: string | null;
  total_rows: number | null;
  valid_rows: number | null;
  quarantined_rows: number | null;
  rejected_rows: number | null;
  promoted_rows: number | null;
  error_rate: number | null;
  triggered_by: string;
  started_at: string;
  completed_at: string | null;
  error_detail: string | null;
}

export interface QuarantineRecord {
  quarantine_id: string;
  ingestion_id: string;
  quarantine_reason: string;
  quarantine_status: string;
  resolved_by: string | null;
  resolved_at: string | null;
  source_row_number: number;
  row_data: Record<string, unknown>;
}

// ---------------------------------------------------------------------------
// Phase 3 — Business Rules Configuration (Build Plan v1.2 §4.3)
// ---------------------------------------------------------------------------

export interface MeritIncreaseRate {
  rate_id: string;
  fiscal_year: number;
  rate_pct: string;
  effective_date: string;
  updated_at: string | null;
  updated_by: string | null;
}

export interface MeritIncreaseRateUpsert {
  rate_pct: string;
  effective_date: string;
}

export interface CompensationBurdenRate {
  rate_id: string;
  fiscal_year: number;
  fica_rate_pct: string;
  fica_wage_cap: string;
  medicare_rate_pct: string;
  state_income_tax_rate_pct: string;
  federal_income_tax_rate_pct: string;
  suta_rate_pct: string;
  suta_wage_cap: string;
  futa_rate_pct: string;
  futa_wage_cap: string;
  other_benefits_rate_pct: string;
  updated_at: string | null;
  updated_by: string | null;
}

export interface CompensationBurdenRateUpsert {
  fica_rate_pct: string;
  fica_wage_cap: string;
  medicare_rate_pct: string;
  state_income_tax_rate_pct: string;
  federal_income_tax_rate_pct: string;
  suta_rate_pct: string;
  suta_wage_cap: string;
  futa_rate_pct: string;
  futa_wage_cap: string;
  other_benefits_rate_pct: string;
}

export interface CompensationComponentMapping {
  mapping_id: string;
  component_code: string;
  account_code: string;
  updated_at: string | null;
  updated_by: string | null;
}

export interface CompensationComponentMappingUpdate {
  account_code: string;
}

export type RatePeriod = "Monthly" | "Annual";

export interface OverheadAllocationRate {
  rate_id: string;
  account_code: string;
  geography_code: string | null;
  amount_per_employee: string;
  rate_period: RatePeriod;
  fiscal_year: number;
  is_active: boolean;
  created_at: string | null;
  updated_at: string | null;
  updated_by: string | null;
}

export interface OverheadAllocationRateCreate {
  account_code: string;
  geography_code?: string | null;
  amount_per_employee: string;
  rate_period: RatePeriod;
  fiscal_year: number;
}

export interface OverheadAllocationRateUpdate {
  amount_per_employee?: string;
  rate_period?: RatePeriod;
}

export interface FiscalPeriod {
  calendar_id: string;
  fiscal_year: number;
  period_number: number;
  period_name: string;
  start_date: string;
  end_date: string;
  is_current: boolean;
}
