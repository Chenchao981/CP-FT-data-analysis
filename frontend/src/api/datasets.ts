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
export interface DatasetChartData {
  dataset_id: number;
  version_no: number;
  selected_lot_id: string | null;
  selected_wafer_id: string | null;
  lot_options: string[];
  wafer_options: WaferOption[];
  wafer_yield: WaferYieldPoint[];
  bin_counts: BinCountPoint[];
  wafer_map: WaferMapPoint[];
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
): Promise<DatasetChartData> {
  const query = new URLSearchParams();
  if (lotId) query.set("lot_id", lotId);
  if (waferId) query.set("wafer_id", waferId);
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
