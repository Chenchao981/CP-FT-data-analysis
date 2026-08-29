import { apiRequest, downloadAuthenticatedFile } from "./auth";

export type BusinessDomain = "ENGINEERING" | "PRODUCTION";
export type TestStage = "CP" | "FT";

export interface PageResult<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

export interface StagePageRequest {
  page: number;
  page_size: number;
  factory_code?: string;
  status?: string;
  product_name?: string;
  lot_id?: string;
  from_utc?: string;
  to_utc?: string;
}

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
  queue_age_seconds?: number | null;
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
  job_id?: number | null;
  created_at_utc: string;
}

export interface FormalSourceRoot {
  code: string;
  name: string;
  test_stage: TestStage;
  factory_code: string;
  allowed_suffixes: string[];
  purpose: "FORMAL_IMPORT";
  business_domains: BusinessDomain[];
  available: boolean;
}

export interface FormalSourceDirectory {
  name: string;
  relative_path: string;
  direct_file_count: number;
  direct_total_bytes: number;
}

export interface FormalDirectoryListing {
  root_code: string;
  current_relative_path: string;
  parent_relative_path: string | null;
  directories: FormalSourceDirectory[];
}

export interface FormalSourceManifestPreview {
  root_code: string;
  relative_path: string;
  mode: string;
  recursive: boolean;
  file_count: number;
  total_bytes: number;
  sha: string;
  allowed_suffixes: string[];
}

const stageBase = (businessDomain: BusinessDomain, testStage: TestStage) =>
  `/api/v1/${businessDomain.toLowerCase()}/${testStage.toLowerCase()}`;

export const listStageUploads = (businessDomain: BusinessDomain, testStage: TestStage) =>
  apiRequest<StageUploadRow[]>(`${stageBase(businessDomain, testStage)}/uploads`);

export const listStageResults = (businessDomain: BusinessDomain, testStage: TestStage) =>
  apiRequest<StageResultRow[]>(`${stageBase(businessDomain, testStage)}/results`);

const stagePageQuery = (request: StagePageRequest) => {
  const query = new URLSearchParams({
    page: String(request.page),
    page_size: String(request.page_size),
  });
  for (const key of ["factory_code", "status", "product_name", "lot_id", "from_utc", "to_utc"] as const) {
    const value = request[key]?.trim();
    if (value) query.set(key, value);
  }
  return query;
};

export const listStageUploadsPage = (
  businessDomain: BusinessDomain,
  testStage: TestStage,
  request: StagePageRequest,
) => apiRequest<PageResult<StageUploadRow>>(
  `${stageBase(businessDomain, testStage)}/uploads/page?${stagePageQuery(request)}`,
);

export const listStageResultsPage = (
  businessDomain: BusinessDomain,
  testStage: TestStage,
  request: StagePageRequest,
) => apiRequest<PageResult<StageResultRow>>(
  `${stageBase(businessDomain, testStage)}/results/page?${stagePageQuery(request)}`,
);

export const listFormalSourceRoots = (
  businessDomain: BusinessDomain,
  testStage: TestStage,
  factoryCode: string,
) => {
  const query = new URLSearchParams({ factory_code: factoryCode });
  return apiRequest<FormalSourceRoot[]>(
    `${stageBase(businessDomain, testStage)}/source-roots?${query}`,
  );
};

export const listFormalSourceDirectories = (
  businessDomain: BusinessDomain,
  testStage: TestStage,
  factoryCode: string,
  rootCode: string,
  relativePath = ".",
) => {
  const query = new URLSearchParams({
    factory_code: factoryCode,
    relative_path: relativePath,
  });
  return apiRequest<FormalDirectoryListing>(
    `${stageBase(businessDomain, testStage)}/source-roots/${encodeURIComponent(rootCode)}/directories?${query}`,
  );
};

export const previewFormalSourceManifest = (
  businessDomain: BusinessDomain,
  testStage: TestStage,
  factoryCode: string,
  rootCode: string,
  relativePath = ".",
) => {
  const query = new URLSearchParams({
    factory_code: factoryCode,
    relative_path: relativePath,
  });
  return apiRequest<FormalSourceManifestPreview>(
    `${stageBase(businessDomain, testStage)}/source-roots/${encodeURIComponent(rootCode)}/manifest-preview?${query}`,
  );
};

export function uploadStageData(
  businessDomain: BusinessDomain,
  testStage: TestStage,
  files: File[],
  factoryCode: string,
  remark?: string,
  sourceRootCode?: string,
  sourceRelativePath?: string,
  sourceManifestMode?: string,
  sourceManifestSha256?: string,
) {
  const body = new FormData();
  files.forEach((file) => body.append("files", file));
  body.append("factory_code", factoryCode);
  if (sourceRootCode?.trim()) {
    body.append("source_root_code", sourceRootCode.trim());
    body.append("source_relative_path", sourceRelativePath?.trim() || ".");
    if (sourceManifestMode?.trim()) {
      body.append("source_manifest_mode", sourceManifestMode.trim());
    }
    if (sourceManifestSha256?.trim()) {
      body.append("source_manifest_sha256", sourceManifestSha256.trim());
    }
  }
  if (remark) body.append("remark", remark);
  return apiRequest<{
    import_batch_id: number;
    job_id: number;
    status: "QUEUED";
    input_mode: "WEB_UPLOAD" | "SOURCE_CATALOG";
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
  return downloadAuthenticatedFile(
    `${stageBase(businessDomain, testStage)}/uploads/${importBatchId}/files/${receiptId}/download`,
    fileName,
  );
}
