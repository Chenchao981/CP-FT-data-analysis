export interface GateReason {
  code: string;
  count: number;
  message: string;
}

export interface DqGateResult {
  dataset_id: number;
  version_no: number;
  status: "PASS" | "BLOCKED";
  run_count: number;
  unit_count: number;
  measurement_count: number;
  reasons: GateReason[];
}

export interface DatasetResultSummary {
  dataset_id: number;
  dataset_code: string;
  dataset_name: string;
  version_no: number;
  version_status: "DRAFT" | "VALIDATING" | "PUBLISHED" | "SUPERSEDED" | "ARCHIVED";
  is_current: boolean;
  run_count: number;
  lot_count: number;
  wafer_count: number;
  unit_count: number;
  pass_count: number;
  fail_count: number;
  yield_rate: number;
  measurement_count: number;
  bin_counts: Record<string, number>;
}

export interface WaferOption { lot_id: string; wafer_id: string }
export interface WaferYieldPoint {
  lot_id: string;
  wafer_id: string;
  unit_count: number;
  pass_count: number;
  fail_count: number;
  yield_rate: number;
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

interface DatasetVersion {
  dataset_version_id: number;
  dataset_id: number;
  version_no: number;
  status: string;
  is_current: boolean;
}

interface ErrorEnvelope {
  error?: { message?: string };
}

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

export function getDatasetGate(datasetId: number, versionNo: number): Promise<DqGateResult> {
  return request(`/api/v1/datasets/${datasetId}/versions/${versionNo}/gate`);
}

export function getDatasetSummary(datasetId: number, versionNo: number): Promise<DatasetResultSummary> {
  return request(`/api/v1/datasets/${datasetId}/versions/${versionNo}/summary`);
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
  return request(`/api/v1/datasets/${datasetId}/versions/${versionNo}/charts${suffix}`);
}

export function publishDatasetVersion(
  datasetId: number,
  versionNo: number,
  publishedBy: number,
): Promise<DatasetVersion> {
  return request(`/api/v1/datasets/${datasetId}/versions/${versionNo}/publish`, {
    method: "POST",
    body: JSON.stringify({ published_by: publishedBy }),
  });
}
