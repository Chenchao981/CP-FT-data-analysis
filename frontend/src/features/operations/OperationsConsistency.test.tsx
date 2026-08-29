// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { drainWorker, getOperationsConsistency, getWorkerFleetHealth, resumeWorker, SystemConsistencySummary, type WorkerFleetHealth } from "../../api/operations";
import { useAuth } from "../auth/AuthContext";
import { OperationsConsistency } from "./OperationsConsistency";

vi.mock("../../api/operations", () => ({
  getOperationsConsistency: vi.fn(),
  getWorkerFleetHealth: vi.fn(),
  drainWorker: vi.fn(),
  resumeWorker: vi.fn(),
}));
vi.mock("../auth/AuthContext", () => ({ useAuth: vi.fn() }));

Object.defineProperty(window, "matchMedia", {
  writable: true,
  value: (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => undefined,
    removeListener: () => undefined,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    dispatchEvent: () => false,
  }),
});

vi.stubGlobal("ResizeObserver", class {
  observe() { return undefined; }
  unobserve() { return undefined; }
  disconnect() { return undefined; }
});

const healthySummary: SystemConsistencySummary = {
  observed_at_utc: "2026-08-29T01:02:03.000Z",
  database_ready: true,
  schema_revision: "sql2014_0015",
  atomic_schema_ready: true,
  overall_state: "HEALTHY",
  management_message: "未发现发布链路一致性异常，可继续按计划灰度。",
  job_status_counts: [
    { status: "QUEUED", count: 1 },
    { status: "RUNNING", count: 2 },
    { status: "FAILED", count: 3 },
  ],
  active_atomic_initial_import_count: 2,
  intent_status_counts: [
    { status: "STAGED", count: 1 },
    { status: "FINALIZED", count: 8 },
    { status: "ABORTED", count: 0 },
  ],
  issue_counts: { batch_job_intent: 0, dataset_current: 0 },
  current_unknown_result_count: 13,
  recent_failed_jobs: [{
    job_id: 91,
    job_type: "INITIAL_IMPORT",
    import_batch_id: 7,
    business_domain: "PRODUCTION",
    test_stage: "FT",
    error_code: "CLEANER_FAILED",
    attempt_count: 2,
    failed_at_utc: "2026-08-29T00:59:00.000Z",
  }],
  environment: "PRODUCTION",
  database_name: "TMS_PROD",
  database_server: "SQL-PRIMARY",
};

const healthyFleet: WorkerFleetHealth = {
  observed_at_utc: "2026-08-29T01:02:03.000Z",
  stale_after_seconds: 90,
  active_worker_count: 2,
  ready_worker_count: 1,
  draining_worker_count: 1,
  stale_worker_count: 1,
  failed_worker_count: 0,
  last_heartbeat_at_utc: "2026-08-29T01:02:01.000Z",
  queued_job_count: 4,
  oldest_queued_seconds: 123,
  alert_codes: ["WORKER_STALE"],
  workers: [
    {
      worker_id: "route-a-01",
      worker_kind: "ROUTE_A",
      state: "READY",
      desired_state: "RUN",
      started_at_utc: "2026-08-29T00:00:00.000Z",
      last_seen_at_utc: "2026-08-29T01:02:01.000Z",
      stopped_at_utc: null,
      database_name: "TMS_PROD",
      schema_revision: "sql2014_0017",
      is_stale: false,
    },
    {
      worker_id: "route-a-stale",
      worker_kind: "ROUTE_A",
      state: "DRAINING",
      desired_state: "DRAIN",
      started_at_utc: "2026-08-28T22:00:00.000Z",
      last_seen_at_utc: "2026-08-29T00:59:00.000Z",
      stopped_at_utc: null,
      database_name: "TMS_PROD",
      schema_revision: "sql2014_0017",
      is_stale: true,
    },
  ],
};

const authForRoles = (roles: string[]) => ({
  user: {
    user_id: 1,
    login_name: "auditor",
    display_name: "审计员",
    department_code: null,
    roles,
    permissions: ["AUDIT_READ"],
  },
  loading: false,
  login: vi.fn(async () => undefined),
  logout: vi.fn(async () => undefined),
  can: vi.fn(() => true),
});

function renderSummary() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <OperationsConsistency />
    </QueryClientProvider>,
  );
}

describe("OperationsConsistency", () => {
  beforeEach(() => {
    vi.mocked(getOperationsConsistency).mockResolvedValue(healthySummary);
    vi.mocked(getWorkerFleetHealth).mockResolvedValue(healthyFleet);
    vi.mocked(drainWorker).mockResolvedValue({ worker_id: "route-a-01", worker_kind: "ROUTE_A", state: "READY", desired_state: "DRAIN", last_seen_at_utc: healthyFleet.observed_at_utc });
    vi.mocked(resumeWorker).mockResolvedValue({ worker_id: "route-a-stale", worker_kind: "ROUTE_A", state: "DRAINING", desired_state: "RUN", last_seen_at_utc: healthyFleet.observed_at_utc });
    vi.mocked(useAuth).mockReturnValue(authForRoles(["AUDITOR"]));
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("shows a management-readable healthy snapshot and only sanitized failures", async () => {
    renderSummary();

    expect(await screen.findByText("发布链路一致性正常（HEALTHY）")).toBeInTheDocument();
    expect(screen.getAllByText("sql2014_0015").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("运行中：2")).toBeInTheDocument();
    expect(screen.getByText("待发布：1")).toBeInTheDocument();
    expect(screen.getByText("CLEANER_FAILED")).toBeInTheDocument();
    expect(screen.getByText("量产 / FT")).toBeInTheDocument();
    expect(screen.getByText("PRODUCTION")).toBeInTheDocument();
    expect(screen.getAllByText("TMS_PROD").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("SQL-PRIMARY")).toBeInTheDocument();

    const unknownTitle = screen.getByText("Dataset Current 中 UNKNOWN 单元");
    const unknownCard = unknownTitle.closest(".ant-card") as HTMLElement;
    expect(within(unknownCard).getByText("13")).toBeInTheDocument();
    expect(screen.queryByText(/password|source_path|error_message|login_name/i)).not.toBeInTheDocument();
    expect(await screen.findByText("WORKER_STALE")).toBeInTheDocument();
    expect(screen.getByText("route-a-01")).toBeInTheDocument();
    expect(screen.getAllByText("STALE").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("123 秒")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Drain|Resume/ })).not.toBeInTheDocument();
  });

  it("makes the 0015 upgrade gate explicit without presenting unknown checks as zero", async () => {
    vi.mocked(getOperationsConsistency).mockResolvedValue({
      ...healthySummary,
      atomic_schema_ready: false,
      overall_state: "SCHEMA_UPGRADE_REQUIRED",
      management_message: "原子发布一致性检查尚未就绪，请先完成数据库升级。",
      active_atomic_initial_import_count: null,
      intent_status_counts: null,
      issue_counts: { batch_job_intent: null, dataset_current: null },
    });

    renderSummary();

    expect(await screen.findByText("数据库结构升级未完成（SCHEMA_UPGRADE_REQUIRED）")).toBeInTheDocument();
    expect(screen.getByText(/需要先完成 0015 数据库升级后/)).toBeInTheDocument();
    expect(screen.getAllByText("待 0015 升级").length).toBeGreaterThanOrEqual(3);
    expect(screen.getByText("待完成 0015 数据库升级后提供。")).toBeInTheDocument();
  });

  it("does not expose underlying connection details when the summary request fails", async () => {
    vi.mocked(getOperationsConsistency).mockRejectedValueOnce(
      new Error("server=db.internal;password=secret;path=C:\\private"),
    );

    renderSummary();

    expect(await screen.findByText("运行一致性摘要加载失败")).toBeInTheDocument();
    expect(screen.getByText("本页不展示底层错误详情；请稍后刷新，如持续失败请联系管理员。")).toBeInTheDocument();
    expect(screen.queryByText(/db\.internal|password|C:\\private/)).not.toBeInTheDocument();
  });

  it("shows Worker controls only for the SYSTEM_ADMIN role", async () => {
    vi.mocked(useAuth).mockReturnValue(authForRoles(["SYSTEM_ADMIN"]));

    renderSummary();

    expect(await screen.findByRole("button", { name: /Drain/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Resume/ })).toBeInTheDocument();
    expect(screen.getAllByText("系统管理员操作").length).toBeGreaterThanOrEqual(1);
  });

  it("states that no active Worker exists without inferring availability from queued jobs", async () => {
    vi.mocked(getWorkerFleetHealth).mockResolvedValue({
      ...healthyFleet,
      active_worker_count: 0,
      ready_worker_count: 0,
      draining_worker_count: 0,
      stale_worker_count: 0,
      workers: [],
      queued_job_count: 9,
    });

    renderSummary();

    expect(await screen.findByText("无活动Worker")).toBeInTheDocument();
    expect(screen.getByText(/Worker 状态来自心跳记录/)).toBeInTheDocument();
    expect(screen.queryByText("Worker 在线")).not.toBeInTheDocument();
  });

  it("keeps Worker backend failures sanitized", async () => {
    vi.mocked(getWorkerFleetHealth).mockRejectedValueOnce(new Error("server=private;password=secret;path=C:\\workers"));

    renderSummary();

    expect(await screen.findByText("Worker 运维摘要加载失败")).toBeInTheDocument();
    expect(screen.getByText("本页不展示底层数据库、主机或连接详情；请稍后刷新或联系系统管理员。")).toBeInTheDocument();
    expect(screen.queryByText(/private|password|C:\\workers/)).not.toBeInTheDocument();
  });
});
