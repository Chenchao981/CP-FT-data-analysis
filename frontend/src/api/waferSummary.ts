import type {
  AnalyticsCapability,
  AnalyticsContextRequest,
  AnalyticsDatasetContext,
  AnalyticsFilterSummary,
  AnalyticsRuleContext,
} from "./analytics";
import { apiRequest } from "./auth";

export type WaferSummarySort = "DATASET" | "LOT" | "WAFER" | "UNIT_COUNT" | "YIELD";
export type WaferSummarySortDirection = "ASC" | "DESC";

export interface WaferSummaryRequest extends AnalyticsContextRequest {
  page: number;
  page_size: number;
  sort_by: WaferSummarySort;
  sort_direction: WaferSummarySortDirection;
}

export interface WaferParameterSummary {
  parameter: string;
  unit: string | null;
  measured_count: number;
  missing_count: number;
  out_of_spec_count: number;
  minimum: number | null;
  maximum: number | null;
  mean: number | null;
}

export interface WaferSummaryRow {
  dataset_id: number;
  version_no: number;
  lot_id: string;
  wafer_id: string;
  unit_count: number;
  pass_count: number;
  fail_count: number;
  unknown_count: number;
  abort_count: number;
  known_yield_denominator: number;
  yield_rate: number | null;
  parameters: WaferParameterSummary[];
  drilldown_context: {
    dataset_id: number;
    version_no: number;
    lot_id: string;
    wafer_id: string;
  } | null;
}

export interface WaferSummaryResult {
  contract_version: string;
  dataset_context: AnalyticsDatasetContext;
  filter_summary: AnalyticsFilterSummary;
  rule_context: AnalyticsRuleContext;
  capabilities: AnalyticsCapability[];
  page: number;
  page_size: number;
  total: number;
  sort_by: string;
  sort_direction: string;
  items: WaferSummaryRow[];
  warnings: string[];
  computed_at: string;
}

export function getWaferSummary(request: WaferSummaryRequest): Promise<WaferSummaryResult> {
  return apiRequest("/api/v1/analytics/wafer-summary", {
    method: "POST",
    body: JSON.stringify(request),
  });
}
