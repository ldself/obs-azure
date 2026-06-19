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
