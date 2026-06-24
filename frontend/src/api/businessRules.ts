// Typed API functions for the Phase 3 business rules configuration endpoints
// (Build Plan v1.2 §4.3). Thin wrappers over the HTTP client (RULE 3 — display
// layer only; no business logic here).

import { api } from "./client";
import type {
  CompensationBurdenRate,
  CompensationBurdenRateUpsert,
  CompensationComponentMapping,
  CompensationComponentMappingUpdate,
  FiscalPeriod,
  MeritIncreaseRate,
  MeritIncreaseRateUpsert,
  OverheadAllocationRate,
  OverheadAllocationRateCreate,
  OverheadAllocationRateUpdate,
} from "../types";

// --- Merit increase rates ---------------------------------------------------

export const listMeritRates = (): Promise<MeritIncreaseRate[]> =>
  api.get<MeritIncreaseRate[]>("/api/v1/business-rules/merit-increase-rates");

export const getMeritRate = (fiscalYear: number): Promise<MeritIncreaseRate> =>
  api.get<MeritIncreaseRate>(`/api/v1/business-rules/merit-increase-rates/${fiscalYear}`);

export const upsertMeritRate = (
  fiscalYear: number,
  body: MeritIncreaseRateUpsert,
): Promise<MeritIncreaseRate> =>
  api.put<MeritIncreaseRate>(`/api/v1/business-rules/merit-increase-rates/${fiscalYear}`, body);

// --- Compensation burden rates ----------------------------------------------

export const listBurdenRates = (): Promise<CompensationBurdenRate[]> =>
  api.get<CompensationBurdenRate[]>("/api/v1/business-rules/compensation-burden-rates");

export const getBurdenRate = (fiscalYear: number): Promise<CompensationBurdenRate> =>
  api.get<CompensationBurdenRate>(`/api/v1/business-rules/compensation-burden-rates/${fiscalYear}`);

export const upsertBurdenRate = (
  fiscalYear: number,
  body: CompensationBurdenRateUpsert,
): Promise<CompensationBurdenRate> =>
  api.put<CompensationBurdenRate>(
    `/api/v1/business-rules/compensation-burden-rates/${fiscalYear}`,
    body,
  );

// --- Compensation component mappings ----------------------------------------

export const listComponentMappings = (): Promise<CompensationComponentMapping[]> =>
  api.get<CompensationComponentMapping[]>(
    "/api/v1/business-rules/compensation-component-mappings",
  );

export const updateComponentMapping = (
  componentCode: string,
  body: CompensationComponentMappingUpdate,
): Promise<CompensationComponentMapping> =>
  api.put<CompensationComponentMapping>(
    `/api/v1/business-rules/compensation-component-mappings/${encodeURIComponent(componentCode)}`,
    body,
  );

// --- Overhead allocation rates ----------------------------------------------

export const listOverheadRates = (params?: {
  fiscal_year?: number;
  is_active?: boolean;
}): Promise<OverheadAllocationRate[]> => {
  const qs = new URLSearchParams();
  if (params?.fiscal_year !== undefined) qs.set("fiscal_year", String(params.fiscal_year));
  if (params?.is_active !== undefined) qs.set("is_active", String(params.is_active));
  const query = qs.toString();
  return api.get<OverheadAllocationRate[]>(
    `/api/v1/business-rules/overhead-allocation-rates${query ? `?${query}` : ""}`,
  );
};

export const createOverheadRate = (body: OverheadAllocationRateCreate): Promise<OverheadAllocationRate> =>
  api.post<OverheadAllocationRate>("/api/v1/business-rules/overhead-allocation-rates", body);

export const updateOverheadRate = (
  rateId: string,
  body: OverheadAllocationRateUpdate,
): Promise<OverheadAllocationRate> =>
  api.put<OverheadAllocationRate>(
    `/api/v1/business-rules/overhead-allocation-rates/${encodeURIComponent(rateId)}`,
    body,
  );

export const deactivateOverheadRate = (rateId: string): Promise<OverheadAllocationRate> =>
  api.del<OverheadAllocationRate>(
    `/api/v1/business-rules/overhead-allocation-rates/${encodeURIComponent(rateId)}`,
  );

// --- Fiscal calendar --------------------------------------------------------

export const listFiscalPeriods = (fiscalYear?: number): Promise<FiscalPeriod[]> => {
  const query = fiscalYear !== undefined ? `?fiscal_year=${fiscalYear}` : "";
  return api.get<FiscalPeriod[]>(`/api/v1/fiscal-calendar${query}`);
};
