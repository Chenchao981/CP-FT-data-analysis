import { apiRequest } from "./auth";
import type { BusinessDomain, TestStage } from "./stageData";

export interface QualitySummaryRequest {
  access_scope: "PERSONAL" | "DOMAIN";
  data_domain_id?: number;
  from_utc?: string;
  to_utc?: string;
  business_domain?: BusinessDomain;
  test_stage?: TestStage;
  factory_code?: string;
  product_name?: string;
  lot_id?: string;
  recent_limit?: number;
}

export interface QualityKpis {
  dataset_count: number | null;
  product_count: number | null;
  lot_count: number | null;
  total_units: number | null;
  pass_units: number | null;
  fail_units: number | null;
  abort_units: number | null;
  unknown_units: number | null;
  known_yield_denominator: number | null;
  yield_rate: number | null;
  unknown_rate: number | null;
  failed_job_count: number | null;
  latest_dataset_at_utc: string | null;
  freshness_seconds: number | null;
}

export interface QualityTrendPoint {
  period_start_utc: string;
  dataset_count: number;
  total_units: number;
  pass_units: number;
  fail_units: number;
  unknown_units: number;
  yield_rate: number | null;
  unknown_rate: number | null;
}

export interface QualityBreakdown {
  dimension: "FACTORY" | "PRODUCT" | "TEST_STAGE" | "BUSINESS_DOMAIN" | string;
  key: string;
  label: string;
  dataset_count: number;
  lot_count: number;
  total_units: number;
  pass_units: number;
  fail_units: number;
  unknown_units: number;
  yield_rate: number | null;
  unknown_rate: number | null;
}

export interface FailBinSummary {
  bin_code: string;
  fail_units: number;
  share_of_failed: number | null;
}

export interface QualityDatasetDrilldown {
  dataset_id: number;
  version_no: number;
  import_batch_id: number;
  job_id: number | null;
  product_name: string;
  lot_id: string;
  factory_code: string;
  business_domain: BusinessDomain;
  test_stage: TestStage;
  unit_count: number;
  pass_count: number;
  fail_count: number;
  unknown_count: number;
  yield_rate: number | null;
  source_file_count: number;
  published_at_utc: string;
}

export interface QualityManagementSummary {
  observed_at_utc: string;
  from_utc: string;
  to_utc: string;
  filters: Record<string, string | number | null>;
  methodology: Record<string, string>;
  kpis: QualityKpis;
  trends: QualityTrendPoint[];
  breakdowns: QualityBreakdown[];
  fail_bins: FailBinSummary[];
  recent_datasets: QualityDatasetDrilldown[];
}

export function getQualityManagementSummary(request: QualitySummaryRequest): Promise<QualityManagementSummary> {
  const query = new URLSearchParams();
  query.set("access_scope", request.access_scope);
  if (request.access_scope === "DOMAIN" && request.data_domain_id != null) {
    query.set("data_domain_id", String(request.data_domain_id));
  }
  for (const key of ["from_utc", "to_utc", "business_domain", "test_stage", "factory_code", "product_name", "lot_id"] as const) {
    const value = request[key]?.trim();
    if (value) query.set(key, value);
  }
  query.set("recent_limit", String(request.recent_limit ?? 20));
  return apiRequest<QualityManagementSummary>(`/api/v1/management/quality-summary?${query}`);
}
