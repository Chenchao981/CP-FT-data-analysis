import { apiRequest, downloadAuthenticatedFile } from "./auth";

export interface QuickSourceRoot {
  code: string;
  name: string;
  test_stage: "FT";
  factory_code: "JIEQUN";
  allowed_suffixes: string[];
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

export interface QuickAnalysisSession {
  analysis_session_id: number;
  owner_user_id: number;
  owner_login: string;
  owner_name: string;
  analysis_type: "QUICK_PAT";
  test_stage: "FT";
  factory_code: "JIEQUN";
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
  summary: { elapsed_seconds?: number; minimum_parameter_value_count?: number } | null;
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

export const createQuickPat = (sourceRootCode: string, sourceRelativePath: string) =>
  apiRequest<QuickAnalysisSession>(`${base}/pat`, {
    method: "POST",
    body: JSON.stringify({
      source_root_code: sourceRootCode,
      source_relative_path: sourceRelativePath,
    }),
  });

export const listQuickAnalysisSessions = () =>
  apiRequest<QuickAnalysisSession[]>(`${base}/sessions`);

export async function downloadQuickPat(sessionId: number, fileName: string) {
  return downloadAuthenticatedFile(`${base}/sessions/${sessionId}/download`, fileName);
}
