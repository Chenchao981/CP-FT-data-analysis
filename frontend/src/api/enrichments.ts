import { apiRequest } from "./auth";

export type EnrichmentStage = "CP" | "FT";
export type EnrichmentAction = "FILL" | "IGNORE";

export interface EnrichmentFieldDefinition {
  field_code: string;
  label: string;
  required_for_analysis: boolean;
  can_ignore: boolean;
  description: string;
}

export interface FieldEnrichmentRecord {
  enrichment_id: number;
  import_batch_id: number;
  source_file_id: number | null;
  test_stage: EnrichmentStage;
  field_code: string;
  action: EnrichmentAction;
  value_text: string | null;
  reason: string;
  is_current: boolean;
}

export interface CreateEnrichmentPayload {
  import_batch_id: number;
  source_file_id?: number;
  test_stage: EnrichmentStage;
  field_code: string;
  action: EnrichmentAction;
  value_text?: string;
  reason: string;
}

export function getEnrichmentFields(stage: EnrichmentStage): Promise<EnrichmentFieldDefinition[]> {
  return apiRequest(`/api/v1/enrichments/fields/${stage}`);
}

export function getBatchEnrichments(importBatchId: number): Promise<FieldEnrichmentRecord[]> {
  return apiRequest(`/api/v1/enrichments/batches/${importBatchId}`);
}

export function createFieldEnrichment(payload: CreateEnrichmentPayload): Promise<FieldEnrichmentRecord> {
  return apiRequest("/api/v1/enrichments", { method: "POST", body: JSON.stringify(payload) });
}
