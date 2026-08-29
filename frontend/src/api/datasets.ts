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
