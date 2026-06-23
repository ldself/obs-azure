// Ingestion monitoring API client (Phase 2, Data Integration Spec v1.8 §8.6).
// RULE 3: display layer only — no business logic.

import { api } from "./client";
import type { IngestionRecord, QuarantineRecord } from "../types";

export function listIngestionRecords(params?: {
  file_type?: string;
  ingestion_status?: string;
}): Promise<IngestionRecord[]> {
  const qs = new URLSearchParams();
  if (params?.file_type) qs.set("file_type", params.file_type);
  if (params?.ingestion_status) qs.set("ingestion_status", params.ingestion_status);
  const query = qs.toString() ? `?${qs.toString()}` : "";
  return api.get<IngestionRecord[]>(`/api/v1/ingestion${query}`);
}

export function getIngestionRecord(ingestionId: string): Promise<IngestionRecord> {
  return api.get<IngestionRecord>(`/api/v1/ingestion/${ingestionId}`);
}

export function getQuarantineRecords(ingestionId: string): Promise<QuarantineRecord[]> {
  return api.get<QuarantineRecord[]>(`/api/v1/ingestion/${ingestionId}/quarantine`);
}
