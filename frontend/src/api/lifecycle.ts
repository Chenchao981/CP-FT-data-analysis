import { apiRequest, downloadAuthenticatedFile } from "./auth";

export interface LifecycleJobReceipt {
  job_id: number;
  job_type: string;
  dataset_id: number;
  dataset_version_id: number;
  action_type: "EXPORT_LATEST" | "REPROCESS_UPDATE" | "DELETE_TASK" | string;
  status: string;
  import_batch_id: number;
  cleaner_release_id: number | null;
  parent_job_id: number | null;
  idempotency_key: string;
  created: boolean;
  idempotent_replay: boolean;
}

export interface LifecycleExportArtifact {
  artifact_id: number;
  role: string;
  file_name: string;
  size_bytes: number;
  sha256: string;
  physical_status: string;
  expires_at_utc: string;
  download_url: string | null;
}

export interface LifecycleExportStatus {
  job_id: number;
  dataset_id: number;
  dataset_version_id: number;
  cleaner_release_id: number;
  status: string;
  error_code: string | null;
  availability: "PROCESSING" | "READY" | "FAILED" | "EXPIRED" | "CLEANED" | "UNAVAILABLE" | string;
  expires_at_utc: string | null;
  artifacts: LifecycleExportArtifact[];
}

export const createLatestExport = (datasetId: number, idempotencyKey: string) =>
  apiRequest<LifecycleJobReceipt>("/api/v1/lifecycle/exports", {
    method: "POST",
    body: JSON.stringify({ dataset_id: datasetId, idempotency_key: idempotencyKey }),
  });

export const createDatasetReprocess = (datasetId: number, reason: string, idempotencyKey: string) =>
  apiRequest<LifecycleJobReceipt>(`/api/v1/lifecycle/datasets/${datasetId}/reprocess`, {
    method: "POST",
    body: JSON.stringify({ confirmation: "REPROCESS", reason, idempotency_key: idempotencyKey }),
  });

export const archiveDataset = (datasetId: number, reason: string, idempotencyKey: string) =>
  apiRequest<LifecycleJobReceipt>(`/api/v1/lifecycle/datasets/${datasetId}/archive`, {
    method: "POST",
    body: JSON.stringify({ confirmation: "ARCHIVE", reason, idempotency_key: idempotencyKey }),
  });

export const getLatestExportStatus = (jobId: number) =>
  apiRequest<LifecycleExportStatus>(`/api/v1/lifecycle/exports/${jobId}`);

export const downloadLatestExportArtifact = (jobId: number, artifactId: number, fileName: string) =>
  downloadAuthenticatedFile(
    `/api/v1/lifecycle/exports/${jobId}/artifacts/${artifactId}/download`,
    fileName,
  );
