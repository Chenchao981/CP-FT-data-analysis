import type {
  AnalyticsCapability,
  AnalyticsContextRequest,
  AnalyticsDatasetContext,
  AnalyticsFilterSummary,
  AnalyticsRuleContext,
  AnalyticsSamplingSummary,
} from "./analytics";
import { apiRequest } from "./auth";

export type QualityAnalysisType = "PAT_ROBUST_IQR" | "SPC_I_MR" | "MARGIN_OOS" | "BIN_COOCCURRENCE" | "SBL_GROUPED_LIMIT" | "SYL_GROUPED_LIMIT" | "PASS_FAIL_DISTRIBUTION";
export type QualityGroupBy = "DATASET" | "LOT" | "WAFER" | "RUN" | "TESTER" | "PROGRAM" | "CONDITION";
export type QualitySpcOrder = "UNIT_SEQUENCE";
export type QualitySpcPhase = "PHASE_I_BASELINE";
export type QualityBinType = "CP_BIN" | "SOFT_BIN" | "HARD_BIN" | "ALL_MAPPED_FAILURE";

export interface QualityRuleReference { rule_code: string; version_code: string }

export interface QualityEvaluationRequest extends AnalyticsContextRequest {
  analysis: QualityAnalysisType;
  rule: QualityRuleReference;
  group_by: QualityGroupBy;
  spc_order?: QualitySpcOrder | null;
  spc_phase?: QualitySpcPhase | null;
  bin_type?: QualityBinType | null;
}

export interface QualityRuleProvenance {
  rule_code: string;
  version_code: string;
  algorithm_code: string;
  approval_status: string;
  activation_status: string;
  parameters_sha256: string;
}

export interface QualityParameterIdentity {
  name: string;
  canonical_parameter_code: string | null;
  step_code: string;
  sequence_no: number;
  unit: string | null;
  test_condition: string | null;
  program_lsl: number | null;
  program_usl: number | null;
}

export interface QualityCalculationCounts {
  input_units: number;
  included_units: number;
  excluded_units: number;
  input_measurements: number;
  included_measurements: number;
  missing_measurements: number;
  excluded_measurements: number;
}

export interface QualityEvidencePoint {
  dataset_id: number;
  version_no: number;
  unit_id: number;
  measurement_id: number | null;
  value: number | null;
  drilldown_key: string;
  reason_code: string;
}

export interface QualityPatGroupResult {
  dataset_id: number;
  version_no: number;
  group_key: string;
  valid_n: number;
  missing_n: number;
  q1: number | null;
  median: number | null;
  q3: number | null;
  iqr: number | null;
  robust_sigma: number | null;
  lower_limit: number | null;
  upper_limit: number | null;
  outlier_count: number;
  outlier_rate: number | null;
  status: string;
  evidence: QualityEvidencePoint[];
}

export interface QualitySpcPoint {
  sequence: number;
  value: number;
  moving_range: number | null;
  drilldown_key: string;
  rule_hits: string[];
}

export interface QualitySpcGroupResult {
  dataset_id: number;
  version_no: number;
  group_key: string;
  valid_n: number;
  missing_n: number;
  center_line: number | null;
  lower_control_limit: number | null;
  upper_control_limit: number | null;
  mr_bar: number | null;
  mr_upper_control_limit: number | null;
  boundary_reset: boolean;
  baseline_context_hash: string;
  status: string;
  points: QualitySpcPoint[];
  sampling_summary: AnalyticsSamplingSummary;
}

export interface QualityMarginPoint {
  dataset_id: number;
  version_no: number;
  unit_id: number;
  measurement_id: number;
  value: number;
  lower_margin: number | null;
  upper_margin: number | null;
  nearest_margin: number;
  out_of_spec: boolean;
  drilldown_key: string;
}

export interface QualityMarginGroupResult {
  dataset_id: number;
  version_no: number;
  group_key: string;
  spec_set_id: number;
  spec_version: string;
  spec_mode: string;
  lsl: number | null;
  usl: number | null;
  valid_n: number;
  missing_n: number;
  out_of_spec_count: number;
  out_of_spec_rate: number | null;
  minimum_margin: number | null;
  points: QualityMarginPoint[];
  sampling_summary: AnalyticsSamplingSummary;
}

export interface QualityBinCooccurrenceCell {
  dataset_id: number;
  version_no: number;
  group_key: string;
  left_bin: string;
  right_bin: string;
  physical_unit_count: number;
  denominator_units: number;
  rate: number;
  drilldown_keys: string[];
  pareto_rank: number;
  pair_count_share: number | null;
  cumulative_pair_count_share: number | null;
}

export interface QualitySblGroupRate {
  group_key: string;
  physical_unit_count: number;
  fail_unit_count: number;
  rate: number;
  drilldown_keys: string[];
}

export interface QualitySblBinLimit {
  dataset_id: number;
  version_no: number;
  bin_code: string;
  subgroup_count: number;
  mean_rate: number | null;
  sample_stddev: number | null;
  upper_limit: number | null;
  status: string;
  exceeding_groups: string[];
  groups: QualitySblGroupRate[];
  pareto_rank: number;
  fail_unit_count: number;
  fail_unit_share: number | null;
  cumulative_fail_unit_share: number | null;
}

export interface QualitySylGroupYield {
  group_key: string;
  pass_unit_count: number;
  fail_unit_count: number;
  unknown_excluded_count: number;
  abort_excluded_count: number;
  other_result_excluded_count: number;
  yield_rate: number | null;
  drilldown_keys: string[];
}

export interface QualitySylDatasetLimit {
  dataset_id: number;
  version_no: number;
  subgroup_count: number;
  mean_yield: number | null;
  sample_stddev: number | null;
  raw_lower_limit: number | null;
  lower_limit: number | null;
  rounding_policy: string;
  rounding_step: number | null;
  status: string;
  below_limit_groups: string[];
  groups: QualitySylGroupYield[];
}

export interface QualityPassFailHistogramBin {
  bin_index: number;
  lower: number;
  upper: number;
  pass_count: number;
  fail_count: number;
  pass_drilldown_keys: string[];
  fail_drilldown_keys: string[];
}

export interface QualityPassFailDistributionGroup {
  dataset_id: number;
  version_no: number;
  group_key: string;
  pass_count: number;
  fail_count: number;
  unknown_excluded_count: number;
  abort_excluded_count: number;
  other_result_excluded_count: number;
  missing_measurements: number;
  pass_mean: number | null;
  fail_mean: number | null;
  minimum: number | null;
  maximum: number | null;
  status: string;
  bins: QualityPassFailHistogramBin[];
}

export interface QualityEvaluationResult {
  contract_version: string;
  analysis: QualityAnalysisType;
  dataset_context: AnalyticsDatasetContext;
  filter_summary: AnalyticsFilterSummary;
  calculation_context_hash: string;
  rule_context: AnalyticsRuleContext;
  rule: QualityRuleProvenance;
  parameter_identity: QualityParameterIdentity | null;
  capabilities: AnalyticsCapability[];
  counts: QualityCalculationCounts;
  sampling_summary: AnalyticsSamplingSummary;
  pat: QualityPatGroupResult[];
  spc: QualitySpcGroupResult[];
  margin: QualityMarginGroupResult[];
  bin_cooccurrence: QualityBinCooccurrenceCell[];
  sbl: QualitySblBinLimit[];
  syl: QualitySylDatasetLimit[];
  pass_fail_distribution: QualityPassFailDistributionGroup[];
  warnings: string[];
  computed_at: string;
}

export function evaluateQuality(request: QualityEvaluationRequest): Promise<QualityEvaluationResult> {
  return apiRequest("/api/v1/analytics/quality-evaluation", {
    method: "POST",
    body: JSON.stringify(request),
  });
}
