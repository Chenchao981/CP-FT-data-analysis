export type EnrichmentStage = "CP" | "FT";
export type EnrichmentAction = "FILL" | "IGNORE";

export interface EnrichmentFieldDefinition {
  field_code: string;
  label: string;
  required_for_analysis: boolean;
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
  entered_by: number;
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
  entered_by: number;
  reason: string;
}

interface ErrorEnvelope { error?: { message?: string } }

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!response.ok) {
    const payload = (await response.json().catch(() => ({}))) as ErrorEnvelope;
    throw new Error(payload.error?.message ?? `请求失败（${response.status}）`);
  }
  return response.json() as Promise<T>;
}

export function getEnrichmentFields(stage: EnrichmentStage): Promise<EnrichmentFieldDefinition[]> {
  return request(`/api/v1/enrichments/fields/${stage}`);
}

export function getBatchEnrichments(importBatchId: number): Promise<FieldEnrichmentRecord[]> {
  return request(`/api/v1/enrichments/batches/${importBatchId}`);
}

export function createFieldEnrichment(payload: CreateEnrichmentPayload): Promise<FieldEnrichmentRecord> {
  return request("/api/v1/enrichments", { method: "POST", body: JSON.stringify(payload) });
}
