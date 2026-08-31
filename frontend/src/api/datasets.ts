import { apiRequest } from "./auth";

export interface WaferOption { lot_id: string; wafer_id: string }
export interface WaferYieldPoint {
  lot_id: string;
  wafer_id: string;
  unit_count: number;
  pass_count: number;
  fail_count: number;
  unknown_count: number;
  abort_count: number;
  known_yield_denominator: number;
  yield_rate: number | null;
}

export interface DatasetReference { dataset_id: number; version_no: number }
export type DatasetAnalysisGroupBy = "DATASET";
export type DatasetParameterAnalysisType = "DESCRIPTIVE" | "BOX_PLOT" | "HISTOGRAM" | "NORMAL_FIT" | "CAPABILITY";
export type DatasetAnalysisOverallResult = "PASS" | "FAIL" | "UNKNOWN" | "ABORT";
export interface DatasetParameterAnalysisFilters {
  lot_ids: string[];
  wafer_ids: string[];
  bin_codes: string[];
  overall_results: DatasetAnalysisOverallResult[];
  source_ids: string[];
  tester_ids: string[];
  program_versions: string[];
  test_conditions: string[];
}
export interface DatasetAnalysisRuleReference {
  rule_code?: string | null;
  version_code?: string | null;
}
export type DatasetCapabilityMethod = "CPK_POOLED_WITHIN_RUN_V1" | "CPK_POOLED_WITHIN_LOT_WAFER_V1";
export interface DatasetParameterAnalysisRequest {
  datasets: DatasetReference[];
  group_by: DatasetAnalysisGroupBy;
  filters: DatasetParameterAnalysisFilters;
  parameters: string[];
  analyses: DatasetParameterAnalysisType[];
  box_plot?: DatasetAnalysisRuleReference;
  histogram?: DatasetAnalysisRuleReference;
  normal_fit?: DatasetAnalysisRuleReference;
  capability?: DatasetAnalysisRuleReference & { method?: DatasetCapabilityMethod | null };
}
export interface DatasetParameterAnalysisFilterSummary extends DatasetParameterAnalysisFilters {
  matched_unit_count: number;
  candidate_measurement_count: number;
}
export interface DatasetAnalysisParameterIdentity {
  name: string;
  canonical_parameter_code: string | null;
  unit: string | null;
  program_lsl: number | null;
  program_usl: number | null;
  test_condition: string | null;
  spec_set_ids: number[];
  limit_source: string;
  formal_lsl: number | null;
  formal_usl: number | null;
  formal_lower_operator: string | null;
  formal_upper_operator: string | null;
  formal_spec_status: "RESOLVED" | "NO_SPEC";
  formal_spec_reason_codes: string[];
  formal_spec_versions: string[];
}
export interface DatasetMeasurementStatusCount { status: string; count: number }
export interface DatasetDescriptiveStatistics {
  row_count: number;
  numeric_count: number;
  excluded_count: number;
  minimum: number | null;
  maximum: number | null;
  average: number | null;
  sample_stddev: number | null;
}
export interface DatasetMeasurementEvidence {
  measurement_id: number;
  value: number;
  drilldown_key: string;
  spec_status: "IN_SPEC" | "OUT_OF_SPEC" | "NO_SPEC";
}
export interface DatasetEvidenceSampling {
  sampled: boolean;
  method: string;
  original_points: number;
  returned_points: number;
}
export interface DatasetBoxPlotStatistics {
  minimum: number;
  q1: number;
  median: number;
  q3: number;
  maximum: number;
  lower_whisker: number;
  upper_whisker: number;
  outlier_count: number;
  method: string;
  outlier_evidence: DatasetMeasurementEvidence[];
  outlier_sampling: DatasetEvidenceSampling | null;
}
export interface DatasetHistogramBin {
  index: number;
  lower_bound: number;
  upper_bound: number;
  count: number;
  lower_inclusive: boolean;
  upper_inclusive: boolean;
  spec_region: "IN_SPEC" | "OUT_OF_SPEC" | "CROSSES_SPEC" | "NO_SPEC";
  aggregate_drilldown_context: DatasetMeasurementAggregateContext | null;
}
export interface DatasetMeasurementAggregateContext {
  dataset_id: number;
  version_no: number;
  parameter: string;
  lower_bound: number | null;
  upper_bound: number | null;
  lower_inclusive: boolean;
  upper_inclusive: boolean;
}
export interface DatasetHistogramStatistics {
  bin_count: number;
  requested_bin_count: number;
  range_min: number | null;
  range_max: number | null;
  bins: DatasetHistogramBin[];
  method: string;
}
export interface DatasetNormalFitPoint {
  x: number;
  probability_density: number;
}
export interface DatasetNormalFitStatistics {
  status: string;
  reason_code: string | null;
  sample_count: number;
  mean: number | null;
  standard_deviation: number | null;
  points: DatasetNormalFitPoint[];
  method: "NORMAL_FIT_MLE_V1";
  observed_evidence: DatasetMeasurementEvidence[];
  evidence_sampling: DatasetEvidenceSampling | null;
}
export interface DatasetCapabilityStatistics {
  status: string;
  ppk_status: string;
  cpk_status: string;
  reason_codes: string[];
  spec_mode: string | null;
  lsl: number | null;
  usl: number | null;
  sample_count: number;
  subgroup_count: number;
  overall_sigma: number | null;
  within_sigma: number | null;
  ppl: number | null;
  ppu: number | null;
  ppk: number | null;
  cpl: number | null;
  cpu: number | null;
  cpk: number | null;
  rule_code: string | null;
  drilldown_context: DatasetMeasurementAggregateContext | null;
}
export interface DatasetParameterAnalysis {
  identity: DatasetAnalysisParameterIdentity;
  status_counts: DatasetMeasurementStatusCount[];
  descriptive: DatasetDescriptiveStatistics | null;
  box_plot: DatasetBoxPlotStatistics | null;
  histogram: DatasetHistogramStatistics | null;
  capability: DatasetCapabilityStatistics | null;
  normal_fit: DatasetNormalFitStatistics | null;
}
export interface DatasetParameterAnalysisItem {
  dataset_id: number;
  version_no: number;
  test_stage: string;
  group_key: string;
  filter_summary: DatasetParameterAnalysisFilterSummary;
  parameters: DatasetParameterAnalysis[];
}
export interface DatasetParameterAnalysisResult {
  contract_version: string;
  group_by: string;
  compatibility: string;
  dataset_context: {
    resolved_datasets: DatasetReference[];
    test_stage: string;
    current_published_verified: boolean;
  };
  filter_summary: {
    normalized_filters: DatasetParameterAnalysisFilters;
    filter_hash: string;
  };
  rule_context: {
    spec_versions: string[];
    bin_mapping_versions: string[];
    evaluation_rule_versions: string[];
    capability_rule_code: string | null;
    capability_rule_approval_status: string;
  };
  capabilities: Array<{ code: string; status: string; reason_code: string | null }>;
  counts: {
    input_units: number;
    included_units: number;
    excluded_units: number;
    missing_measurements: number;
  };
  sampling_summary: {
    sampled: boolean;
    method: string | null;
    original_points: number;
    returned_points: number;
    preserved_out_of_spec_points: number;
  };
  warnings: string[];
  computed_at: string;
  items: DatasetParameterAnalysisItem[];
}
export interface DatasetParameterStatistic {
  name: string;
  unit: string | null;
  lsl: number | null;
  usl: number | null;
  test_condition: string | null;
  measured_count: number;
  missing_count: number;
  minimum: number | null;
  maximum: number | null;
  average: number | null;
}
export interface DatasetComparisonItem {
  dataset_id: number;
  version_no: number;
  test_stage: string;
  product_name: string | null;
  unit_count: number;
  pass_count: number;
  fail_count: number;
  unknown_count: number;
  abort_count: number;
  known_yield_denominator: number;
  yield_rate: number | null;
  parameter_statistics: DatasetParameterStatistic[];
}
export interface DatasetComparisonResult {
  test_stage: string;
  spec_compatibility: "SINGLE_DATASET" | "COMPATIBLE" | "NOT_EVALUATED";
  lot_ids: string[];
  wafer_ids: string[];
  bin_codes: string[];
  parameters: string[];
  items: DatasetComparisonItem[];
}
export interface DatasetDetailMeasurement {
  parameter: string;
  value_numeric: number | null;
  value_text: string | null;
  status: string;
  unit: string | null;
  lsl: number | null;
  usl: number | null;
}
export interface DatasetDetailRow {
  unit_id: number;
  logical_unit_key: string;
  lot_id: string | null;
  wafer_id: string | null;
  x: number | null;
  y: number | null;
  soft_bin: string | null;
  hard_bin: string | null;
  overall_result: string;
  source_row_no: number | null;
  measurements: DatasetDetailMeasurement[];
}
export interface DatasetDetailPage {
  dataset_id: number;
  version_no: number;
  test_stage: string;
  page: number;
  page_size: number;
  total: number;
  lot_options: string[];
  wafer_options: string[];
  bin_options: string[];
  parameter_options: string[];
  items: DatasetDetailRow[];
}
export interface BinCountPoint { soft_bin: string; unit_count: number; percent: number }
export interface WaferMapPoint { x: number; y: number; soft_bin: string | null; result: string }
export interface FtParameterOption {
  name: string;
  unit: string | null;
  lsl: number | null;
  usl: number | null;
  test_condition: string | null;
}
export interface FtParameterPoint {
  sequence: number;
  lot_id: string;
  source_id: string;
  value: number | null;
  status: string;
}
export interface DatasetChartData {
  dataset_id: number;
  version_no: number;
  test_stage: string;
  product_name: string | null;
  selected_lot_id: string | null;
  selected_wafer_id: string | null;
  selected_source_id: string | null;
  selected_parameter: string | null;
  lot_options: string[];
  wafer_options: WaferOption[];
  source_options: string[];
  parameter_options: FtParameterOption[];
  wafer_yield: WaferYieldPoint[];
  bin_counts: BinCountPoint[];
  wafer_map: WaferMapPoint[];
  ft_parameter_points: FtParameterPoint[];
  ft_total_point_count: number;
  ft_sampled: boolean;
}

export function getDatasetChartData(
  datasetId: number,
  versionNo: number,
  lotId?: string,
  waferId?: string,
  sourceId?: string,
  parameter?: string,
): Promise<DatasetChartData> {
  const query = new URLSearchParams();
  if (lotId) query.set("lot_id", lotId);
  if (waferId) query.set("wafer_id", waferId);
  if (sourceId) query.set("source_id", sourceId);
  if (parameter) query.set("parameter", parameter);
  const suffix = query.size ? `?${query.toString()}` : "";
  return apiRequest(`/api/v1/datasets/${datasetId}/versions/${versionNo}/charts${suffix}`);
}

export function compareDatasets(payload: {
  datasets: DatasetReference[];
  lot_ids?: string[];
  wafer_ids?: string[];
  bin_codes?: string[];
  parameters?: string[];
}): Promise<DatasetComparisonResult> {
  return apiRequest("/api/v1/datasets/compare", {
    method: "POST",
    body: JSON.stringify({
      datasets: payload.datasets,
      lot_ids: payload.lot_ids ?? [],
      wafer_ids: payload.wafer_ids ?? [],
      bin_codes: payload.bin_codes ?? [],
      parameters: payload.parameters ?? [],
    }),
  });
}

export function analyzeDatasetParameters(
  payload: DatasetParameterAnalysisRequest,
): Promise<DatasetParameterAnalysisResult> {
  return apiRequest("/api/v1/datasets/parameter-analysis", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function getDatasetDetails(
  datasetId: number,
  versionNo: number,
  request: {
    page: number;
    page_size: number;
    lot_ids?: string[];
    wafer_ids?: string[];
    bin_codes?: string[];
    parameters?: string[];
  },
): Promise<DatasetDetailPage> {
  const query = new URLSearchParams({ page: String(request.page), page_size: String(request.page_size) });
  for (const value of request.lot_ids ?? []) query.append("lot_id", value);
  for (const value of request.wafer_ids ?? []) query.append("wafer_id", value);
  for (const value of request.bin_codes ?? []) query.append("bin_code", value);
  for (const value of request.parameters ?? []) query.append("parameter", value);
  return apiRequest(`/api/v1/datasets/${datasetId}/versions/${versionNo}/details?${query}`);
}
