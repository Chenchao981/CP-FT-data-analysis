import type { AnalyticsContextRequest, AnalyticsFilters, AnalyticsRuleContext } from "./analytics";
import { apiRequest, downloadAuthenticatedFile } from "./auth";

export const ANALYTICS_EXPORT_CONTRACT_VERSION = "ANALYTICS_EXPORT_V1" as const;
export const ANALYTICS_EXPORT_TEMPLATE_VERSION = "v1" as const;

export type AnalyticsExportScope = "CURRENT_PAGE" | "FILTERED_RESULT" | "FULL_DATASET" | "REPORT";
export type AnalyticsExportFormat = "PNG" | "CSV" | "XLSX" | "BIN_TXT" | "HTML" | "PDF";
export type AnalyticsExportStatus = "QUEUED" | "RUNNING" | "SUCCESS" | "FAILED" | "CANCELLED" | "EXPIRED";
export type AnalyticsExportAvailability = "PENDING_GENERATION" | "GENERATING" | "ARTIFACT_METADATA_READY" | "FAILED" | "CANCELLED" | "EXPIRED" | "INTEGRITY_BLOCKED";
export type AnalyticsExportTemplateCode =
  | "ANALYTICS_DETAIL"
  | "PARAMETER_DETAIL"
  | "ANALYTICS_OVERVIEW"
  | "PARAMETER_ANALYSIS"
  | "PARAMETER_RELATIONSHIP"
  | "SPATIAL_ANALYSIS"
  | "FT_QUALITY"
  | "WAFER_SUMMARY";

export interface AnalyticsExportTemplateContract {
  code: AnalyticsExportTemplateCode;
  version: typeof ANALYTICS_EXPORT_TEMPLATE_VERSION;
  scopes: readonly AnalyticsExportScope[];
  formats: readonly AnalyticsExportFormat[];
  testStages: readonly ("CP" | "FT")[];
}

const DATA_SCOPES = ["CURRENT_PAGE", "FILTERED_RESULT", "FULL_DATASET"] as const;
const DATA_FORMATS = ["CSV", "XLSX", "BIN_TXT"] as const;
const REPORT_FORMATS = ["PNG", "CSV", "XLSX", "HTML", "PDF"] as const;
const CP_FT = ["CP", "FT"] as const;

/** Mirrors the server registry; this prevents offering combinations the API rejects. */
export const ANALYTICS_EXPORT_TEMPLATES: readonly AnalyticsExportTemplateContract[] = [
  { code: "ANALYTICS_DETAIL", version: "v1", scopes: DATA_SCOPES, formats: DATA_FORMATS, testStages: CP_FT },
  { code: "PARAMETER_DETAIL", version: "v1", scopes: DATA_SCOPES, formats: DATA_FORMATS, testStages: CP_FT },
  { code: "ANALYTICS_OVERVIEW", version: "v1", scopes: ["REPORT"], formats: REPORT_FORMATS, testStages: CP_FT },
  { code: "PARAMETER_ANALYSIS", version: "v1", scopes: ["REPORT"], formats: REPORT_FORMATS, testStages: CP_FT },
  { code: "PARAMETER_RELATIONSHIP", version: "v1", scopes: ["REPORT"], formats: REPORT_FORMATS, testStages: CP_FT },
  { code: "SPATIAL_ANALYSIS", version: "v1", scopes: ["REPORT"], formats: REPORT_FORMATS, testStages: ["CP"] },
  { code: "FT_QUALITY", version: "v1", scopes: ["REPORT"], formats: REPORT_FORMATS, testStages: ["FT"] },
  { code: "WAFER_SUMMARY", version: "v1", scopes: ["REPORT"], formats: REPORT_FORMATS, testStages: ["CP"] },
] as const;

export interface CreateAnalyticsExportRequest extends AnalyticsContextRequest {
  contract_version: typeof ANALYTICS_EXPORT_CONTRACT_VERSION;
  export_scope: AnalyticsExportScope;
  export_format: AnalyticsExportFormat;
  template_code: AnalyticsExportTemplateCode;
  template_version: typeof ANALYTICS_EXPORT_TEMPLATE_VERSION;
  rule_context: AnalyticsRuleContext;
  chart_config: Record<string, unknown>;
  display_config: Record<string, unknown>;
  artifact_ttl_hours: number;
  idempotency_key: string;
  page?: number | null;
  page_size?: number | null;
  reason: string;
}

export interface CancelAnalyticsExportRequest {
  confirmation: "CANCEL";
  expected_row_version: string;
  reason: string;
}

export interface AnalyticsExportDatasetRecord {
  dataset_version_id: number;
  dataset_id: number;
  version_no: number;
  ordinal_no: number;
  test_stage: string;
}

export interface AnalyticsExportRecord {
  export_job_id: number;
  requested_by: number;
  contract_version: string;
  worker_contract_version: string;
  generation_mode: string;
  status: AnalyticsExportStatus;
  export_scope: AnalyticsExportScope;
  export_format: AnalyticsExportFormat;
  template_code: AnalyticsExportTemplateCode;
  template_version: string;
  datasets: AnalyticsExportDatasetRecord[];
  filters: AnalyticsFilters;
  parameters: string[];
  filter_hash: string;
  context_hash: string;
  rule_context: AnalyticsRuleContext;
  chart_config: Record<string, unknown>;
  display_config: Record<string, unknown>;
  presentation_hash: string;
  artifact_ttl_hours: number;
  page: number | null;
  page_size: number | null;
  idempotency_key: string;
  request_reason_sha256: string;
  requested_at_utc: string;
  started_at_utc: string | null;
  finished_at_utc: string | null;
  exported_row_count: number | null;
  row_version: string;
  idempotent_replay: boolean;
}

export interface AnalyticsExportPage {
  items: AnalyticsExportRecord[];
  /** All access-visible jobs occupying pagination slots, including blocked jobs. */
  total: number;
  page: number;
  page_size: number;
  /** Integrity-blocked jobs on this page; they are deliberately absent from items. */
  integrity_blocked_job_ids: number[];
  integrity_blocked_count: number;
}

export interface AnalyticsExportArtifactMetadata {
  export_artifact_id: number;
  file_name: string;
  mime_type: string;
  file_size: number;
  sha256: string;
  created_at_utc: string;
  expires_at_utc: string;
}

export interface AnalyticsExportDownloadMetadata {
  export_job_id: number;
  job_status: AnalyticsExportStatus;
  availability: AnalyticsExportAvailability;
  download_enabled: boolean;
  reason_code: string;
  artifacts: AnalyticsExportArtifactMetadata[];
}

export interface ListAnalyticsExportsRequest { page: number; page_size: number }

const basePath = "/api/v1/analytics/exports";

export function createAnalyticsExport(request: CreateAnalyticsExportRequest): Promise<AnalyticsExportRecord> {
  return apiRequest(basePath, { method: "POST", body: JSON.stringify(request) });
}

export function listAnalyticsExports(request: ListAnalyticsExportsRequest): Promise<AnalyticsExportPage> {
  const params = new URLSearchParams({ page: String(request.page), page_size: String(request.page_size) });
  return apiRequest(`${basePath}?${params.toString()}`);
}

export function getAnalyticsExport(exportJobId: number): Promise<AnalyticsExportRecord> {
  return apiRequest(`${basePath}/${encodeURIComponent(String(exportJobId))}`);
}

export function getAnalyticsExportDownloadMetadata(exportJobId: number): Promise<AnalyticsExportDownloadMetadata> {
  return apiRequest(`${basePath}/${encodeURIComponent(String(exportJobId))}/download-metadata`);
}

export function downloadAnalyticsExportArtifact(exportJobId: number, exportArtifactId: number, fileName: string): Promise<void> {
  return downloadAuthenticatedFile(
    `${basePath}/${encodeURIComponent(String(exportJobId))}/artifacts/${encodeURIComponent(String(exportArtifactId))}/download`,
    fileName,
  );
}

export function cancelAnalyticsExport(
  exportJobId: number,
  request: CancelAnalyticsExportRequest,
): Promise<AnalyticsExportRecord> {
  return apiRequest(`${basePath}/${encodeURIComponent(String(exportJobId))}/cancel`, {
    method: "POST",
    body: JSON.stringify(request),
  });
}
