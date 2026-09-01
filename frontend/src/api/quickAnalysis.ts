import { apiRequest, downloadAuthenticatedFile } from "./auth";
import type { PageResult } from "./stageData";
import type { LocalResultReceipt } from "./localAgent";

export interface QuickSourceRoot {
  code: string;
  name: string;
  test_stage: "FT";
  factory_code: "JIEQUN";
  allowed_suffixes: string[];
  data_domain_code: string;
  data_domain_id: number;
  available: boolean;
}

export interface QuickSourceDirectory {
  name: string;
  relative_path: string;
  direct_file_count: number;
  direct_total_bytes: number;
}

export interface QuickDirectoryListing {
  root_code: string;
  current_relative_path: string;
  parent_relative_path: string | null;
  directories: QuickSourceDirectory[];
}

export interface QuickManifestPreview {
  root_code: string;
  relative_path: string;
  mode: string;
  recursive: boolean;
  file_count: number;
  total_bytes: number;
  sha: string;
  allowed_suffixes: string[];
  tool_code: string;
}

export interface QuickAnalysisSession {
  analysis_session_id: number;
  owner_user_id: number;
  owner_login: string;
  owner_name: string;
  access_scope: "PERSONAL" | "DOMAIN";
  data_domain_id: number | null;
  data_domain_code: string | null;
  analysis_type: "QUICK_PAT";
  test_stage: "CP" | "FT";
  factory_code: string;
  source_root_code: string;
  source_relative_path: string;
  source_manifest_mode: string;
  source_manifest_sha256: string;
  source_file_count: number;
  source_total_bytes: number;
  retention_mode: "RESULT_ONLY";
  cleaner_release_id: number;
  status: "QUEUED" | "RUNNING" | "SUCCESS" | "FAILED" | "CANCELLED" | "EXPIRED";
  job_id: number | null;
  job_status: string | null;
  parameter_count: number | null;
  record_count: number | null;
  summary: {
    elapsed_seconds?: number;
    minimum_parameter_value_count?: number;
    execution_mode?: "SERVER_CATALOG" | "LOCAL_AGENT";
    tool_code?: string;
  } | null;
  result_file_name: string | null;
  result_size_bytes: number | null;
  error_code: string | null;
  error_message: string | null;
  expires_at_utc: string;
  created_at_utc: string;
  started_at_utc: string | null;
  finished_at_utc: string | null;
}

const base = "/api/v1/quick-analysis";

export const listQuickSourceRoots = () =>
  apiRequest<QuickSourceRoot[]>(`${base}/source-roots`);

export const listQuickSourceDirectories = (rootCode: string, relativePath = ".") => {
  const query = new URLSearchParams({ relative_path: relativePath });
  return apiRequest<QuickDirectoryListing>(
    `${base}/source-roots/${encodeURIComponent(rootCode)}/directories?${query}`,
  );
};

export const previewQuickSourceManifest = (rootCode: string, relativePath = ".") => {
  const query = new URLSearchParams({ relative_path: relativePath });
  return apiRequest<QuickManifestPreview>(
    `${base}/source-roots/${encodeURIComponent(rootCode)}/manifest-preview?${query}`,
  );
};

export const createQuickPat = (
  sourceRootCode: string,
  sourceRelativePath: string,
  sourceManifestMode: string,
  sourceManifestSha256: string,
) =>
  apiRequest<QuickAnalysisSession>(`${base}/pat`, {
    method: "POST",
    body: JSON.stringify({
      source_root_code: sourceRootCode,
      source_relative_path: sourceRelativePath,
      source_manifest_mode: sourceManifestMode,
      source_manifest_sha256: sourceManifestSha256,
    }),
  });

export interface QuickSessionRequest {
  page: number;
  page_size: number;
  status?: QuickAnalysisSession["status"];
  from_utc?: string;
  to_utc?: string;
  access_scope?: QuickAnalysisSession["access_scope"];
}

export const listQuickAnalysisSessions = (request: QuickSessionRequest) => {
  const query = new URLSearchParams({
    page: String(request.page),
    page_size: String(request.page_size),
  });
  if (request.status) query.set("status", request.status);
  if (request.from_utc) query.set("from_utc", request.from_utc);
  if (request.to_utc) query.set("to_utc", request.to_utc);
  if (request.access_scope) query.set("access_scope", request.access_scope);
  return apiRequest<PageResult<QuickAnalysisSession>>(`${base}/sessions?${query}`);
};

export async function downloadQuickPat(sessionId: number, fileName: string) {
  return downloadAuthenticatedFile(`${base}/sessions/${sessionId}/download`, fileName);
}

export interface LocalQuickCapability {
  contract_version: "TMS_LOCAL_RESULT_V1";
  tool_code: string;
  test_stage: "FT";
  factory_code: "JIEQUN";
  analysis_type: "QUICK_PAT";
  release: {
    cleaner_release_id: number;
    cleaner_code: string;
    cleaner_version: string;
    sha256: string;
    entrypoint: string;
    adapter_code: string;
    input_contract_version: string;
    output_contract_version: string;
    timeout_seconds: number;
    max_output_bytes: number;
  };
  upload: {
    multipart_receipt_field: "receipt_json";
    multipart_result_field: "result_file";
    accepted_extension: ".xlsx";
  };
}

export interface LocalQuickRegistration {
  contract_version: "TMS_LOCAL_RESULT_V1";
  analysis_session_id: number;
  job_id: number;
  status: "SUCCESS";
  parameter_count: number;
  record_count: number;
  reserved_bytes: number;
  result: { filename: string; size_bytes: number; sha256: string };
  artifacts: Array<{ role: string; filename: string; size_bytes: number; sha256: string }>;
}

export const getLocalQuickCapability = () =>
  apiRequest<LocalQuickCapability>(`${base}/local-capability`);

export const registerLocalQuickResult = (
  receipt: LocalResultReceipt,
  result: Blob,
) => {
  const form = new FormData();
  form.append("receipt_json", JSON.stringify(receipt));
  form.append("result_file", result, receipt.result.filename);
  return apiRequest<LocalQuickRegistration>(`${base}/local-results`, {
    method: "POST",
    body: form,
  });
};
