import { apiRequest } from "./auth";

export type JobStatus = "QUEUED" | "RUNNING" | "SUCCESS" | "FAILED" | "CANCELLED";

export interface Job {
  job_id: number;
  source_file_id: number | null;
  import_batch_id: number | null;
  cleaner_release_id: number | null;
  job_type: "PARSE" | "REPROCESS" | "REEVALUATE" | "OTHER";
  trigger_type: "MANUAL" | "AUTO" | "API" | "SCHEDULED" | "SYSTEM";
  requested_by: string;
  reason: string | null;
  status: JobStatus;
  requested_at_utc: string;
  started_at_utc: string | null;
  finished_at_utc: string | null;
  error_code: string | null;
  error_message: string | null;
}

export interface CreateJobPayload {
  source_file_id: number;
  cleaner_release_id: number;
  requested_by: string;
  reason?: string;
}

export function createJob(payload: CreateJobPayload): Promise<Job> {
  return apiRequest<Job>("/api/v1/jobs", {
    method: "POST",
    body: JSON.stringify({ ...payload, job_type: "PARSE", trigger_type: "MANUAL" }),
  });
}

export function getJob(jobId: number): Promise<Job> {
  return apiRequest<Job>(`/api/v1/jobs/${jobId}`);
}

export function transitionJob(jobId: number, targetStatus: JobStatus): Promise<Job> {
  return apiRequest<Job>(`/api/v1/jobs/${jobId}/transitions`, {
    method: "POST",
    body: JSON.stringify({ target_status: targetStatus }),
  });
}
