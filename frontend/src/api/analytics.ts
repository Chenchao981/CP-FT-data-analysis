import { apiRequest } from "./auth";

export interface AnalyticsDatasetReference {
  dataset_id: number;
  version_no: number;
}

export type AnalyticsOverallResult = "PASS" | "FAIL" | "UNKNOWN" | "ABORT";
export type AnalyticsDetailView = "WIDE" | "LONG";
export type AnalyticsDetailSort = "UNIT_SEQUENCE" | "LOT" | "WAFER" | "SOURCE_ROW" | "RESULT" | "SOFT_BIN" | "HARD_BIN";
export type AnalyticsSortDirection = "ASC" | "DESC";
export type AnalyticsEvaluationType = "SPEC" | "PAT" | "SBL" | "SAFE_LAUNCH" | "OTHER";
export type AnalyticsEvaluationResult = "PASS" | "FAIL" | "NOT_EVALUATED" | "NO_MATCH" | "CONFIG_AMBIGUOUS" | "INVALID_VALUE";

export interface AnalyticsFilters {
  lot_ids: string[];
  wafer_ids: string[];
  bin_codes: string[];
  overall_results: AnalyticsOverallResult[];
  source_ids: string[];
  tester_ids: string[];
  program_versions: string[];
  test_conditions: string[];
}

export interface AnalyticsContextRequest {
  datasets: AnalyticsDatasetReference[];
  filters: AnalyticsFilters;
  parameters: string[];
}

export interface AnalyticsOverviewRequest extends AnalyticsContextRequest {
  focus_dataset_id?: number | null;
  max_points?: number;
}

export interface AnalyticsEvaluationFilter {
  evaluation_type: AnalyticsEvaluationType;
  evaluation_results: AnalyticsEvaluationResult[];
  rule_code: string | null;
  rule_version: string | null;
}

export interface AnalyticsMeasurementFilter {
  parameter: string;
  lower_bound: number | null;
  upper_bound: number | null;
  lower_inclusive: boolean;
  upper_inclusive: boolean;
}

export interface AnalyticsMeasurementAggregateDrilldownContext extends AnalyticsMeasurementFilter {
  dataset_id: number;
  version_no: number;
}

export interface AnalyticsDetailRequest extends AnalyticsContextRequest {
  focus_dataset_id: number;
  page: number;
  page_size: number;
  view: AnalyticsDetailView;
  sort_by: AnalyticsDetailSort;
  sort_direction: AnalyticsSortDirection;
  evaluation_filter?: AnalyticsEvaluationFilter | null;
  measurement_filter?: AnalyticsMeasurementFilter | null;
}

export interface AnalyticsDrilldownRequest extends AnalyticsContextRequest {
  drilldown_key: string;
}

export type AnalyticsFeatureGroupCode = "OVERVIEW" | "DETAIL" | "PARAMETER" | "SPATIAL" | "QUALITY" | "DELIVERY";

export interface AnalyticsFeatureGroup {
  code: AnalyticsFeatureGroupCode;
  enabled: boolean;
  reason_code: string | null;
  message: string | null;
}

export interface AnalyticsFeatureFlagsResult {
  contract_version: "ANALYTICS_FEATURE_FLAGS_V1";
  groups: AnalyticsFeatureGroup[];
}

export interface AnalyticsResolvedDataset {
  dataset_id: number;
  version_no: number;
  dataset_name: string;
  test_stage: string;
  product_name: string | null;
}

export interface AnalyticsDatasetContext {
  resolved_datasets: AnalyticsResolvedDataset[];
  test_stage: string;
  current_published_verified: boolean;
}

export interface AnalyticsNormalizedFilters extends AnalyticsFilters {}

export interface AnalyticsFilterSummary {
  normalized_filters: AnalyticsNormalizedFilters;
  parameters: string[];
  filter_hash: string;
  context_hash: string;
}

export interface AnalyticsRuleContext {
  spec_versions: string[];
  bin_mapping_versions: string[];
  evaluation_rule_versions: string[];
}

export interface AnalyticsCapability {
  code: string;
  status: string;
  reason_code: string | null;
  message: string | null;
}

export interface AnalyticsCounts {
  input_units: number;
  included_units: number;
  excluded_units: number;
  pass_count: number;
  fail_count: number;
  unknown_count: number;
  abort_count: number;
  known_yield_denominator: number;
  missing_measurements: number;
  yield_rate: number | null;
  unknown_abort_denominator: number;
  unknown_abort_rate: number | null;
}

export interface AnalyticsRiskItem {
  code: string;
  category: string;
  severity: "INFO" | "WARNING" | "CRITICAL" | string;
  status: "ACTIVE" | "GATED" | "CLEAR" | string;
  reason_code: string | null;
  title: string;
  message: string;
  affected_count: number;
  denominator_count: number;
  rate: number | null;
  drilldown_target: string | null;
  rule_versions: string[];
  aggregate_drilldown_context?: AnalyticsEvaluationFilter | null;
}

export type AnalyticsRiskAnalysis = "CAPABILITY" | "PAT_ROBUST_IQR" | "SPC_I_MR" | "MARGIN_OOS" | "SBL_GROUPED_LIMIT" | "SYL_GROUPED_LIMIT";
export type AnalyticsRiskGroupBy = "DATASET" | "LOT" | "WAFER" | "RUN" | "TESTER" | "PROGRAM" | "CONDITION";
export type AnalyticsRiskBinType = "CP_BIN" | "SOFT_BIN" | "HARD_BIN";

export interface AnalyticsExactRuleReference {
  rule_code: string;
  version_code: string;
}

export interface AnalyticsRiskEvaluationConfig {
  analysis: AnalyticsRiskAnalysis;
  rule: AnalyticsExactRuleReference;
  parameter?: string | null;
  group_by?: AnalyticsRiskGroupBy | null;
  capability_method?: "CPK_POOLED_WITHIN_RUN_V1" | "CPK_POOLED_WITHIN_LOT_WAFER_V1" | null;
  spc_order?: "UNIT_SEQUENCE" | null;
  spc_phase?: "PHASE_I_BASELINE" | null;
  bin_type?: AnalyticsRiskBinType | null;
}

export interface AnalyticsInstantRiskRequest extends AnalyticsContextRequest {
  evaluations: AnalyticsRiskEvaluationConfig[];
}

export interface AnalyticsRiskRuleProvenance {
  rule_code: string;
  version_code: string;
  algorithm_code: string;
  approval_status: string;
  activation_status: string;
  parameters_sha256: string;
}

export interface AnalyticsEvaluatedRiskItem {
  code: string;
  analysis: AnalyticsRiskAnalysis;
  category: string;
  severity: string;
  status: "ACTIVE" | "CLEAR" | "GATED" | string;
  reason_code: string | null;
  title: string;
  message: string;
  dataset_id: number;
  version_no: number;
  group_key: string;
  parameter: string | null;
  metric_code: string;
  metric_value: number | null;
  threshold_operator: string | null;
  threshold_value: number | null;
  affected_count: number;
  denominator_count: number;
  rate: number | null;
  evidence_drilldown_keys: string[];
  evidence_truncated: boolean;
  rule: AnalyticsRiskRuleProvenance;
  aggregate_drilldown_context: AnalyticsMeasurementAggregateDrilldownContext | null;
}

export interface AnalyticsInstantRiskResult {
  contract_version: "ANALYTICS_INSTANT_RISK_V1";
  filter_summary: AnalyticsFilterSummary;
  calculation_context_hash: string;
  requested_analyses: AnalyticsRiskAnalysis[];
  items: AnalyticsEvaluatedRiskItem[];
  warnings: string[];
  computed_at: string;
}

export interface AnalyticsSamplingSummary {
  sampled: boolean;
  method: string | null;
  original_points: number;
  returned_points: number;
  preserved_out_of_spec_points: number;
}

export interface AnalyticsOptionSet {
  lot_ids: string[];
  wafer_ids: string[];
  bin_codes: string[];
  source_ids: string[];
  tester_ids: string[];
  program_versions: string[];
  test_conditions: string[];
  parameters: string[];
}

export interface AnalyticsDatasetOverview {
  dataset_id: number;
  version_no: number;
  unit_count: number;
  pass_count: number;
  fail_count: number;
  unknown_count: number;
  abort_count: number;
  known_yield_denominator: number;
  yield_rate: number | null;
}

export interface AnalyticsYieldPoint {
  dataset_id: number;
  version_no: number;
  test_batch_id: number;
  run_id: number;
  sequence: number;
  ordered_at: string | null;
  order_basis: string;
  source_id: string;
  lot_id: string;
  wafer_id: string | null;
  unit_count: number;
  pass_count: number;
  fail_count: number;
  unknown_count: number;
  abort_count: number;
  yield_rate: number | null;
  drilldown_key: string;
}

export interface AnalyticsBinPoint {
  dataset_id: number;
  version_no: number;
  mapping_set_id: number;
  mapping_version: string;
  bin_type: string;
  bin_code: string;
  bin_name: string | null;
  failure_mode: string | null;
  is_pass: boolean;
  unit_count: number;
  percent: number;
  cumulative_percent: number;
  drilldown_key: string;
}

export interface AnalyticsWaferMapPoint {
  x: number;
  y: number;
  bin_code: string | null;
  result: string;
  drilldown_key: string;
}

export interface AnalyticsOverviewResult {
  contract_version: string;
  dataset_context: AnalyticsDatasetContext;
  filter_summary: AnalyticsFilterSummary;
  rule_context: AnalyticsRuleContext;
  capabilities: AnalyticsCapability[];
  counts: AnalyticsCounts;
  sampling_summary: AnalyticsSamplingSummary;
  options: AnalyticsOptionSet;
  datasets: AnalyticsDatasetOverview[];
  yield_trend: AnalyticsYieldPoint[];
  bin_pareto: AnalyticsBinPoint[];
  wafer_map: AnalyticsWaferMapPoint[];
  risk_summary: AnalyticsRiskItem[];
  warnings: string[];
  computed_at: string;
}

export interface AnalyticsShellContextResult {
  contract_version: string;
  dataset_context: AnalyticsDatasetContext;
  filter_summary: AnalyticsFilterSummary;
  rule_context: AnalyticsRuleContext;
  capabilities: AnalyticsCapability[];
  counts: AnalyticsCounts;
  sampling_summary: AnalyticsSamplingSummary;
  options: AnalyticsOptionSet;
  warnings: string[];
  computed_at: string;
}

export interface AnalyticsDetailSourceFile {
  source_file_id: number;
  receipt_id: number | null;
  original_file_name: string | null;
  sha256: string | null;
  ordinal_no: number | null;
  file_role: string | null;
  lineage_basis: string;
}

export interface AnalyticsDetailBinEvaluation {
  unit_bin_evaluation_id: number;
  bin_type: string;
  raw_bin_code: string;
  mapping_status: string;
  bin_mapping_set_id: number | null;
  mapping_version: string | null;
  bin_definition_id: number | null;
  mapped_bin_name: string | null;
  failure_mode_snapshot: string | null;
  is_pass_snapshot: boolean | null;
  processing_run_id: number | null;
  evaluated_at_utc: string;
}

export interface AnalyticsDetailMeasurementEvaluation {
  evaluation_id: number;
  evaluation_type: string;
  evaluation_scope_key: string;
  evaluation_result: string;
  evaluation_reason: string | null;
  evaluation_run_id: number | null;
  rule_code: string | null;
  rule_version_id: number | null;
  rule_version: string | null;
  spec_binding_id: number | null;
  spec_set_id: number | null;
  spec_version: string | null;
  spec_item_id: number | null;
  lsl_applied: number | null;
  usl_applied: number | null;
  lower_operator_applied: string | null;
  upper_operator_applied: string | null;
  processing_run_id: number | null;
  evaluated_at_utc: string;
}

export interface AnalyticsDetailFormalSpec {
  status: "RESOLVED" | "NO_SPEC" | "CONFIG_AMBIGUOUS" | "INVALID";
  reason_code: string | null;
  evaluation_id: number | null;
  evaluation_result: string | null;
  evaluation_scope_key: string | null;
  spec_binding_id: number | null;
  spec_set_id: number | null;
  spec_version: string | null;
  spec_item_id: number | null;
  lsl_applied: number | null;
  usl_applied: number | null;
  lower_operator_applied: string | null;
  upper_operator_applied: string | null;
}

export interface AnalyticsDetailMeasurement {
  measurement_id: number;
  parameter: string;
  canonical_parameter_code: string | null;
  step_code: string;
  sequence_no: number;
  value_numeric: number | null;
  value_text: string | null;
  status: string;
  unit: string | null;
  program_lsl: number | null;
  program_usl: number | null;
  program_limit_source: "TEST_PROGRAM_CONFIGURATION_NOT_FORMAL_SPEC";
  formal_spec: AnalyticsDetailFormalSpec;
  evaluations: AnalyticsDetailMeasurementEvaluation[];
}

export interface AnalyticsDetailRow {
  drilldown_key: string;
  unit_id: number;
  logical_unit_key: string;
  lot_id: string;
  wafer_id: string | null;
  x: number | null;
  y: number | null;
  soft_bin: string | null;
  hard_bin: string | null;
  overall_result: string;
  source_row_no: number | null;
  processing_run_id: number;
  source_file_id: number;
  receipt_id: number | null;
  original_file_name: string | null;
  sha256: string | null;
  source_id: string;
  tester_id: string | null;
  program_version: string | null;
  cleaner_release: string | null;
  source_files: AnalyticsDetailSourceFile[];
  bin_evaluations: AnalyticsDetailBinEvaluation[];
  measurements: AnalyticsDetailMeasurement[];
}

export interface AnalyticsDetailResult {
  contract_version: string;
  dataset_context: AnalyticsDatasetContext;
  filter_summary: AnalyticsFilterSummary;
  rule_context: AnalyticsRuleContext;
  capabilities: AnalyticsCapability[];
  counts: AnalyticsCounts;
  sampling_summary: AnalyticsSamplingSummary;
  evaluation_filter: AnalyticsEvaluationFilter | null;
  measurement_filter: AnalyticsMeasurementFilter | null;
  page: number;
  page_size: number;
  total: number;
  view: string;
  sort_by: AnalyticsDetailSort;
  sort_direction: AnalyticsSortDirection;
  items: AnalyticsDetailRow[];
  warnings: string[];
  computed_at: string;
}

export interface AnalyticsDrilldownResult {
  contract_version: string;
  dataset_context: AnalyticsDatasetContext;
  filter_summary: AnalyticsFilterSummary;
  rule_context: AnalyticsRuleContext;
  unit: AnalyticsDetailRow;
  warnings: string[];
  computed_at: string;
}

export function getAnalyticsOverview(request: AnalyticsOverviewRequest): Promise<AnalyticsOverviewResult> {
  return apiRequest("/api/v1/analytics/overview", {
    method: "POST",
    body: JSON.stringify(request),
  });
}

export function evaluateAnalyticsInstantRisk(request: AnalyticsInstantRiskRequest): Promise<AnalyticsInstantRiskResult> {
  return apiRequest("/api/v1/analytics/instant-risk", {
    method: "POST",
    body: JSON.stringify(request),
  });
}

export function getAnalyticsShellContext(request: AnalyticsOverviewRequest): Promise<AnalyticsShellContextResult> {
  return apiRequest("/api/v1/analytics/context", {
    method: "POST",
    body: JSON.stringify(request),
  });
}

export function getAnalyticsFeatureFlags(): Promise<AnalyticsFeatureFlagsResult> {
  return apiRequest("/api/v1/analytics/features");
}

export function getAnalyticsDetail(request: AnalyticsDetailRequest): Promise<AnalyticsDetailResult> {
  return apiRequest("/api/v1/analytics/detail", {
    method: "POST",
    body: JSON.stringify(request),
  });
}

export function getAnalyticsDrilldown(request: AnalyticsDrilldownRequest): Promise<AnalyticsDrilldownResult> {
  return apiRequest("/api/v1/analytics/drilldown", {
    method: "POST",
    body: JSON.stringify(request),
  });
}
