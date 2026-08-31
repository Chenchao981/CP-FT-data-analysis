import type {
  AnalyticsCapability,
  AnalyticsCounts,
  AnalyticsDatasetContext,
  AnalyticsDatasetReference,
  AnalyticsFilterSummary,
  AnalyticsFilters,
  AnalyticsRuleContext,
  AnalyticsSamplingSummary,
} from "./analytics";
import { apiRequest } from "./auth";

export type ParameterRelationshipAnalysis = "SCATTER" | "TREND" | "CORRELATION";
export type ParameterRelationshipGroupBy = "DATASET" | "TEST_BATCH" | "LOT" | "WAFER" | "SOURCE" | "TESTER" | "PROGRAM" | "CONDITION";
export type ParameterCorrelationMethod = "PEARSON_PAIRWISE_V1";

export interface ParameterCorrelationConfig {
  method?: ParameterCorrelationMethod | null;
  rule_code?: string | null;
  version_code?: string | null;
}

export interface ParameterRelationshipRequest {
  datasets: AnalyticsDatasetReference[];
  filters: AnalyticsFilters;
  x_parameter: string;
  y_parameters: string[];
  analyses: ParameterRelationshipAnalysis[];
  group_by: ParameterRelationshipGroupBy;
  max_points: number;
  correlation: ParameterCorrelationConfig;
}

export interface ParameterRelationshipIdentity {
  name: string;
  canonical_parameter_code: string | null;
  step_code: string;
  sequence_no: number;
  unit: string | null;
  program_lsl: number | null;
  program_usl: number | null;
  test_condition: string | null;
  formal_lsl: number | null;
  formal_usl: number | null;
  formal_lower_operator: string | null;
  formal_upper_operator: string | null;
  formal_spec_status: "RESOLVED" | "NO_SPEC";
  formal_spec_reason_codes: string[];
  formal_spec_versions: string[];
}

export interface ParameterScatterPoint {
  dataset_id: number;
  version_no: number;
  group_key: string;
  x_parameter: string;
  y_parameter: string;
  x_value: number;
  y_value: number;
  x_out_of_spec: boolean;
  y_out_of_spec: boolean;
  drilldown_key: string;
}

export interface ParameterTrendPoint {
  dataset_id: number;
  version_no: number;
  group_key: string;
  parameter: string;
  sequence: number;
  ordinal: number;
  source_sequence: number | null;
  run_id: number;
  ordered_at: string | null;
  value: number;
  out_of_spec: boolean;
  drilldown_key: string;
}

export interface ParameterCorrelationResult {
  dataset_id: number;
  version_no: number;
  group_key: string;
  x_parameter: string;
  y_parameter: string;
  sample_count: number;
  coefficient: number | null;
  status: string;
  reason_code: string | null;
  method: string;
  rule_code: string;
}

export interface ParameterRelationshipItem {
  dataset_id: number;
  version_no: number;
  group_key: string;
  identities: ParameterRelationshipIdentity[];
  scatter_points: ParameterScatterPoint[];
  trend_points: ParameterTrendPoint[];
  correlations: ParameterCorrelationResult[];
}

export interface ParameterRelationshipResult {
  contract_version: string;
  dataset_context: AnalyticsDatasetContext;
  filter_summary: AnalyticsFilterSummary;
  rule_context: AnalyticsRuleContext;
  capabilities: AnalyticsCapability[];
  counts: AnalyticsCounts;
  sampling_summary: AnalyticsSamplingSummary;
  group_by: string;
  trend_order_basis: string;
  items: ParameterRelationshipItem[];
  warnings: string[];
  computed_at: string;
}

export function analyzeParameterRelationship(request: ParameterRelationshipRequest): Promise<ParameterRelationshipResult> {
  return apiRequest("/api/v1/analytics/parameter-relationship", {
    method: "POST",
    body: JSON.stringify(request),
  });
}
