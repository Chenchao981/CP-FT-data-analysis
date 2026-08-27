import { apiRequest, storedToken } from "./auth";

export type BusinessDomain = "ENGINEERING" | "PRODUCTION";
export type TestStage = "CP" | "FT";

export interface StageUploadRow {
  import_batch_id: number;
  sequence_no: number;
  receipt_id: number;
  source_file_id: number;
  original_file_name: string;
  extension: string;
  size_bytes: number;
  factory_code: string;
  upload_time_utc: string;
  completion_time_utc: string | null;
  uploader_login: string;
  uploader_name: string;
  status: string;
  latest_job_id: number | null;
  error_code: string | null;
  error_message: string | null;
  action_required: "LOT_ID" | null;
}

export interface StageInputRequest {
  input_request_id: number;
  source_file_id: number;
  original_file_name: string;
  current_value: null;
}

export interface StageInputRequests {
  import_batch_id: number;
  status: string;
  field_code: "LOT_ID";
  prompt: string;
  latest_job_id: number | null;
  requests: StageInputRequest[];
}

export interface ResolveStageInputRequestsPayload {
  resolutions: Array<{ input_request_id: number; lot_id: string }>;
  reason: string;
}

export interface ResolveStageInputRequestsResult {
  import_batch_id: number;
  job_id: number;
  status: "QUEUED";
}

export interface StageResultRow {
  result_summary_id: number;
  import_batch_id: number;
  data_name: string;
  product_name: string | null;
  lot_id: string | null;
  wafer_count: number | null;
  factory_code: string;
  test_item_count: number | null;
  unit_count: number | null;
  pass_count: number | null;
  yield_rate: number | null;
  status: string;
  data_type: string;
  dataset_id: number | null;
  dataset_version_no: number | null;
  created_at_utc: string;
}

const stageBase = (businessDomain: BusinessDomain, testStage: TestStage) =>
  `/api/v1/${businessDomain.toLowerCase()}/${testStage.toLowerCase()}`;

export const listStageUploads = (businessDomain: BusinessDomain, testStage: TestStage) =>
  apiRequest<StageUploadRow[]>(`${stageBase(businessDomain, testStage)}/uploads`);

export const listStageResults = (businessDomain: BusinessDomain, testStage: TestStage) =>
  apiRequest<StageResultRow[]>(`${stageBase(businessDomain, testStage)}/results`);

export function uploadStageData(businessDomain: BusinessDomain, testStage: TestStage, files: File[], factoryCode: string, remark?: string, sourcePath?: string) {
  const body = new FormData();
  files.forEach((file) => body.append("files", file));
  body.append("factory_code", factoryCode);
  if (sourcePath?.trim()) body.append("source_path", sourcePath.trim());
  if (remark) body.append("remark", remark);
  return apiRequest<{
    import_batch_id: number;
    job_id: number;
    status: "QUEUED";
    business_domain: BusinessDomain;
    test_stage: TestStage;
    cleaner_release: {
      cleaner_release_id: number;
      cleaner_code: string;
      cleaner_version: string;
    };
  }>(`${stageBase(businessDomain, testStage)}/uploads`, { method: "POST", body });
}

export const reprocessStageBatch = (businessDomain: BusinessDomain, testStage: TestStage, importBatchId: number) =>
  apiRequest<{ import_batch_id: number; status: string }>(`${stageBase(businessDomain, testStage)}/uploads/${importBatchId}/reprocess`, { method: "POST" });

export const getStageInputRequests = (businessDomain: BusinessDomain, testStage: TestStage, importBatchId: number) =>
  apiRequest<StageInputRequests>(`${stageBase(businessDomain, testStage)}/uploads/${importBatchId}/input-requests`);

export const resolveStageInputRequests = (
  businessDomain: BusinessDomain,
  testStage: TestStage,
  importBatchId: number,
  payload: ResolveStageInputRequestsPayload,
) => apiRequest<ResolveStageInputRequestsResult>(
  `${stageBase(businessDomain, testStage)}/uploads/${importBatchId}/input-requests/resolve`,
  { method: "POST", body: JSON.stringify(payload) },
);

export async function downloadStageUploadFile(businessDomain: BusinessDomain, testStage: TestStage, importBatchId: number, receiptId: number, fileName: string) {
  const headers = new Headers();
  const token = storedToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const response = await fetch(`${stageBase(businessDomain, testStage)}/uploads/${importBatchId}/files/${receiptId}/download`, { headers });
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new Error(payload?.error?.message ?? `下载失败（${response.status}）`);
  }
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = fileName;
  anchor.click();
  URL.revokeObjectURL(url);
}
