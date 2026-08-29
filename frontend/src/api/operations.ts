import { apiRequest } from "./auth";

export interface OperationalStatusCount {
  status: string;
  count: number;
}

export interface ConsistencyIssueCounts {
  batch_job_intent: number | null;
  dataset_current: number | null;
}

export interface RecentFailedJob {
  job_id: number;
  job_type: string;
  import_batch_id: number | null;
  business_domain: string | null;
  test_stage: string | null;
  error_code: string;
  attempt_count: number;
  failed_at_utc: string;
}

export interface SystemConsistencySummary {
  observed_at_utc: string;
  database_ready: boolean;
  schema_revision: string;
  atomic_schema_ready: boolean;
  overall_state: "HEALTHY" | "ATTENTION_REQUIRED" | "SCHEMA_UPGRADE_REQUIRED" | string;
  management_message: string;
  job_status_counts: OperationalStatusCount[];
  active_atomic_initial_import_count: number | null;
  intent_status_counts: OperationalStatusCount[] | null;
  issue_counts: ConsistencyIssueCounts;
  current_unknown_result_count: number;
  recent_failed_jobs: RecentFailedJob[];
  environment?: string | null;
  database_name?: string | null;
  database_server?: string | null;
}

export interface WorkerHealth {
  worker_id: string;
  worker_kind: string;
  state: string;
  desired_state: string;
  started_at_utc: string;
  last_seen_at_utc: string;
  stopped_at_utc: string | null;
  database_name: string;
  schema_revision: string;
  is_stale: boolean;
}

export interface WorkerFleetHealth {
  observed_at_utc: string;
  stale_after_seconds: number;
  active_worker_count: number;
  ready_worker_count: number;
  draining_worker_count: number;
  stale_worker_count: number;
  failed_worker_count: number;
  last_heartbeat_at_utc: string | null;
  queued_job_count: number;
  oldest_queued_seconds: number | null;
  alert_codes: string[];
  workers: WorkerHealth[];
}

export interface WorkerControlState {
  worker_id: string;
  worker_kind: string;
  state: string;
  desired_state: string;
  last_seen_at_utc: string;
}

export const getOperationsConsistency = (recentFailureLimit = 5) => {
  const query = new URLSearchParams({ recent_failure_limit: String(recentFailureLimit) });
  return apiRequest<SystemConsistencySummary>(`/api/v1/operations/consistency?${query}`);
};

export const getWorkerFleetHealth = (staleAfterSeconds = 90) => {
  const query = new URLSearchParams({ stale_after_seconds: String(staleAfterSeconds) });
  return apiRequest<WorkerFleetHealth>(`/api/v1/operations/workers?${query}`);
};

export const drainWorker = (workerId: string) => apiRequest<WorkerControlState>(
  `/api/v1/operations/workers/${encodeURIComponent(workerId)}/drain`,
  { method: "POST" },
);

export const resumeWorker = (workerId: string) => apiRequest<WorkerControlState>(
  `/api/v1/operations/workers/${encodeURIComponent(workerId)}/resume`,
  { method: "POST" },
);
