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

export interface JobSafeSummary {
  job_id?: number | null;
  job_type?: string | null;
  lifecycle_action_type?: string | null;
  status?: string | null;
  import_batch_id?: number | null;
  parent_job_id?: number | null;
  requested_at_utc?: string | null;
  started_at_utc?: string | null;
  finished_at_utc?: string | null;
  error_code?: string | null;
  error_message?: string | null;
  attempt_count?: number | null;
  max_attempts?: number | null;
}

export interface JobSourceLineage {
  source_file_id?: number | null;
  ordinal_no?: number | null;
  original_file_name?: string | null;
  file_size?: number | null;
  sha256?: string | null;
  lineage_basis?: string | null;
}

export interface JobDetails {
  job?: (JobSafeSummary & {
    source_file_id?: number | null;
    analysis_session_id?: number | null;
    trigger_type?: string | null;
    cleaner_release_id?: number | null;
    requested_by?: string | null;
    reason?: string | null;
    not_before_utc?: string | null;
    heartbeat_at_utc?: string | null;
    lease_expires_at_utc?: string | null;
    finalize_protocol?: string | null;
    queue_age_seconds?: number | null;
  }) | null;
  parent?: JobSafeSummary | null;
  children?: JobSafeSummary[] | null;
  release?: {
    cleaner_release_id?: number | null;
    cleaner_code?: string | null;
    cleaner_version?: string | null;
    content_sha256?: string | null;
  } | null;
  batch?: {
    import_batch_id?: number | null;
    status?: string | null;
    business_domain?: "ENGINEERING" | "PRODUCTION" | null;
    test_stage?: "CP" | "FT" | null;
    factory_code?: string | null;
    source_file_count?: number | null;
  } | null;
  intent?: {
    status?: string | null;
    staged_at_utc?: string | null;
    finalized_at_utc?: string | null;
    aborted_at_utc?: string | null;
  } | null;
  run?: {
    processing_run_id?: number | null;
    status?: string | null;
    started_at_utc?: string | null;
    finished_at_utc?: string | null;
  } | null;
  dataset?: {
    dataset_id?: number | null;
    dataset_version_id?: number | null;
    version_no?: number | null;
    status?: string | null;
    is_current?: boolean | null;
  } | null;
  timeline?: Array<{
    event_code: string;
    status: string;
    occurred_at_utc: string;
  }> | null;
  sources?: JobSourceLineage[] | null;
  actions?: Array<{
    code?: string | null;
    label?: string | null;
    enabled?: boolean | null;
    reason?: string | null;
  }> | null;
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

export function getJobDetails(jobId: number): Promise<JobDetails> {
  return apiRequest<JobDetails>(`/api/v1/jobs/${jobId}/details`);
}

export function transitionJob(jobId: number, targetStatus: JobStatus): Promise<Job> {
  return apiRequest<Job>(`/api/v1/jobs/${jobId}/transitions`, {
    method: "POST",
    body: JSON.stringify({ target_status: targetStatus }),
  });
}
