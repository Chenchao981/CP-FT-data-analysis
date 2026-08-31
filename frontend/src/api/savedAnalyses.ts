import type { AnalyticsContextRequest, AnalyticsFilters, AnalyticsRuleContext } from "./analytics";
import { apiRequest } from "./auth";

export const SAVED_ANALYSIS_CONTRACT_VERSION = "SAVED_ANALYSIS_V1" as const;

export type SavedAnalysisRestoreStatus = "CURRENT" | "NON_CURRENT" | "RULE_CHANGED" | "ACCESS_REVOKED";
export type SavedAnalysisDatasetStatus = "CURRENT" | "NON_CURRENT" | "ACCESS_REVOKED";

export interface SavedAnalysisState extends AnalyticsContextRequest {
  contract_version: typeof SAVED_ANALYSIS_CONTRACT_VERSION;
  rule_context: AnalyticsRuleContext;
  chart_config: Record<string, unknown>;
  display_config: Record<string, unknown>;
}

export interface CreateSavedAnalysisRequest extends SavedAnalysisState {
  analysis_name: string;
  change_reason: string;
}

export interface CreateSavedAnalysisRevisionRequest extends SavedAnalysisState {
  expected_row_version: string;
  analysis_name?: string | null;
  change_reason: string;
}

export interface DeleteSavedAnalysisRequest {
  expected_row_version: string;
  reason: string;
}

export interface SavedAnalysisDatasetRecord {
  dataset_version_id: number;
  dataset_id: number;
  version_no: number;
  ordinal_no: number;
  test_stage: string;
  status: SavedAnalysisDatasetStatus;
}

export interface SavedAnalysisRevisionRecord {
  saved_analysis_revision_id: number;
  revision_no: number;
  contract_version: string;
  filters: AnalyticsFilters;
  parameters: string[];
  filter_hash: string;
  context_hash: string;
  rule_context: AnalyticsRuleContext;
  chart_config: Record<string, unknown>;
  display_config: Record<string, unknown>;
  datasets: SavedAnalysisDatasetRecord[];
  created_by_user_id: number;
  created_at_utc: string;
}

export interface SavedAnalysisRecord {
  saved_analysis_id: number;
  analysis_name: string;
  owner_user_id: number;
  lifecycle_status: string;
  current_revision_no: number;
  row_version: string;
  restore_status: SavedAnalysisRestoreStatus;
  revision: SavedAnalysisRevisionRecord;
  created_at_utc: string;
  updated_at_utc: string;
}

export interface SavedAnalysisPage {
  items: SavedAnalysisRecord[];
  total: number;
  page: number;
  page_size: number;
}

export interface ListSavedAnalysesRequest {
  page: number;
  page_size: number;
  include_deleted?: boolean;
}

const basePath = "/api/v1/analytics/saved-analyses";

export function createSavedAnalysis(request: CreateSavedAnalysisRequest): Promise<SavedAnalysisRecord> {
  return apiRequest(basePath, { method: "POST", body: JSON.stringify(request) });
}

export function listSavedAnalyses(request: ListSavedAnalysesRequest): Promise<SavedAnalysisPage> {
  const params = new URLSearchParams({
    page: String(request.page),
    page_size: String(request.page_size),
    include_deleted: String(request.include_deleted ?? false),
  });
  return apiRequest(`${basePath}?${params.toString()}`);
}

export function getSavedAnalysis(savedAnalysisId: number, revisionNo?: number): Promise<SavedAnalysisRecord> {
  const query = revisionNo === undefined ? "" : `?revision_no=${encodeURIComponent(String(revisionNo))}`;
  return apiRequest(`${basePath}/${encodeURIComponent(String(savedAnalysisId))}${query}`);
}

export function createSavedAnalysisRevision(
  savedAnalysisId: number,
  request: CreateSavedAnalysisRevisionRequest,
): Promise<SavedAnalysisRecord> {
  return apiRequest(`${basePath}/${encodeURIComponent(String(savedAnalysisId))}/revisions`, {
    method: "POST",
    body: JSON.stringify(request),
  });
}

export function deleteSavedAnalysis(
  savedAnalysisId: number,
  request: DeleteSavedAnalysisRequest,
): Promise<SavedAnalysisRecord> {
  return apiRequest(`${basePath}/${encodeURIComponent(String(savedAnalysisId))}`, {
    method: "DELETE",
    body: JSON.stringify(request),
  });
}
