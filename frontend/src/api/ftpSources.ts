import { apiRequest } from "./auth";

export interface FtpSourceConfig {
  source_code: string; source_name: string; protocol: "FTP" | "FTPS"; host: string; port: number;
  remote_root: string; credential_ref: string; encoding: "utf-8" | "gb18030";
  test_stage: "CP" | "FT"; factory_code: string; data_domain_id: number; cleaner_release_id: number;
  package_mode: "SINGLE_FILE" | "DIRECTORY"; package_depth: number; ready_marker?: string | null;
  allowed_suffixes: string[]; interval_seconds: number; stable_seconds: number;
}
export interface FtpSourceRow {
  source_definition_id: number; source_code: string; source_name: string; test_stage: "CP" | "FT";
  factory_code: string; domain_name: string; cleaner_release_id: number; active: boolean;
  protocol: "FTP" | "FTPS"; package_mode: string; interval_seconds: number; last_status: string;
  last_finished_at_utc: string | null; next_scan_at_utc: string; error_message: string | null;
  lease_expires_at_utc: string | null; scan_requested: boolean; config?: FtpSourceConfig;
}
export interface FtpPackageRow {
  ftp_package_id: number; relative_path: string; status: string; attempts: number; file_count: number;
  total_bytes: number; job_id: number | null; import_batch_id: number | null; job_status: string | null;
  error_message: string | null;
}
export interface FtpOptions {
  domains: { data_domain_id: number; domain_name: string; test_stage: string; factory_code: string | null }[];
  releases: { cleaner_release_id: number; cleaner_version: string; test_stage: string; factory_code: string }[];
}

const base = "/api/v1/ftp-sources";
export const listFtpSources = () => apiRequest<FtpSourceRow[]>(base);
export const getFtpOptions = () => apiRequest<FtpOptions>(`${base}/options`);
export const createFtpSource = (config: FtpSourceConfig) => apiRequest<{ source_definition_id: number; active: boolean }>(base, { method: "POST", body: JSON.stringify(config) });
export const setFtpSourceActive = (id: number, active: boolean) => apiRequest(`${base}/${id}/state`, { method: "PATCH", body: JSON.stringify({ active }) });
export const requestFtpScan = (id: number) => apiRequest(`${base}/${id}/scan`, { method: "POST" });
export const checkFtpConnection = (id: number) => apiRequest<{ message: string }>(`${base}/${id}/connection-check`, { method: "POST" });
export const listFtpPackages = (id: number, page: number) => apiRequest<{ total: number; items: FtpPackageRow[] }>(`${base}/${id}/packages?page=${page}&page_size=30`);
export const retryFtpPackage = (id: number, packageId: number) => apiRequest(`${base}/${id}/packages/${packageId}/retry`, { method: "POST" });
