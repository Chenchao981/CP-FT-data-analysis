// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { getJobDetails, type JobDetails } from "../../api/jobs";
import { reprocessStageBatch } from "../../api/stageData";
import { JobDetailsDrawer } from "./JobDetailsDrawer";

vi.mock("../../api/jobs", () => ({ getJobDetails: vi.fn() }));
vi.mock("../../api/stageData", () => ({ reprocessStageBatch: vi.fn() }));

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

const summary = (jobId: number, status = "SUCCESS", lifecycleActionType: string | null = null) => ({
  job_id: jobId,
  job_type: lifecycleActionType === "REPROCESS_UPDATE" ? "INITIAL_IMPORT" : "PARSE",
  lifecycle_action_type: lifecycleActionType,
  status,
  import_batch_id: 14,
  parent_job_id: jobId === 91 ? 90 : null,
  requested_at_utc: "2026-08-28T08:00:00Z",
  started_at_utc: "2026-08-28T08:00:10Z",
  finished_at_utc: "2026-08-28T08:01:00Z",
  error_code: null,
  error_message: null,
  attempt_count: 1,
  max_attempts: 3,
});

const details: JobDetails = {
  job: {
    ...summary(91, "FAILED", "REPROCESS_UPDATE"),
    trigger_type: "AUTO",
    cleaner_release_id: 7,
    error_code: "PARSER_FAILED",
    error_message: "文件格式不符合已批准模板",
  },
  parent: summary(90, "SUCCESS", "REPROCESS_UPDATE"),
  children: [summary(92, "QUEUED", "REPROCESS_UPDATE")],
  release: {
    cleaner_release_id: 7,
    cleaner_code: "FT_XLSX_SCATTER_V1",
    cleaner_version: "1.0.0",
    content_sha256: "a".repeat(64),
  },
  batch: {
    import_batch_id: 14,
    status: "FAILED",
    business_domain: "PRODUCTION",
    test_stage: "FT",
    factory_code: "riyuexin",
    source_file_count: 2,
  },
  intent: {
    status: "ABORTED",
    staged_at_utc: "2026-08-28T08:00:30Z",
    finalized_at_utc: null,
    aborted_at_utc: "2026-08-28T08:01:00Z",
  },
  run: {
    processing_run_id: 51,
    status: "FAILED",
    started_at_utc: "2026-08-28T08:00:10Z",
    finished_at_utc: "2026-08-28T08:01:00Z",
  },
  dataset: { dataset_id: 20, version_no: 3, status: "PUBLISHED", is_current: true },
  timeline: [
    { event_code: "JOB_QUEUED", status: "QUEUED", occurred_at_utc: "2026-08-28T08:00:00Z" },
    { event_code: "JOB_FAILED", status: "FAILED", occurred_at_utc: "2026-08-28T08:01:00Z" },
  ],
  sources: [
    {
      source_file_id: 101,
      ordinal_no: 1,
      original_file_name: "LOT-001-A.xlsx",
      file_size: 2048,
      sha256: "b".repeat(64),
      lineage_basis: "WRITER_VERIFIED",
    },
    {
      source_file_id: 102,
      ordinal_no: 2,
      original_file_name: "LOT-001-B.xlsx",
      file_size: 4096,
      sha256: null,
      lineage_basis: "BATCH_RECEIPT_NOT_WRITER_VERIFIED",
    },
  ],
  actions: [
    { code: "REPROCESS_BATCH", label: "重新处理", enabled: true, reason: null },
    { code: "VIEW_RESULT", label: "打开分析", enabled: true, reason: null },
    { code: "UNSAFE_ACTION", label: "未接入动作", enabled: true, reason: null },
  ],
};

const renderDrawer = (value: JobDetails = details) => {
  vi.mocked(getJobDetails).mockResolvedValue(value);
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const props = {
    jobId: 91,
    open: true,
    onClose: vi.fn(),
    onSelectJob: vi.fn(),
    onOpenAnalytics: vi.fn(),
  };
  render(
    <QueryClientProvider client={queryClient}>
      <JobDetailsDrawer {...props} />
    </QueryClientProvider>,
  );
  return props;
};

describe("JobDetailsDrawer", () => {
  beforeEach(() => {
    vi.mocked(reprocessStageBatch).mockResolvedValue({ import_batch_id: 14, status: "QUEUED" });
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("shows safe history, lineage, release, errors, limits, and supported actions", async () => {
    const props = renderDrawer();

    expect(await screen.findByText("错误分类：PARSER_FAILED")).toBeInTheDocument();
    expect(screen.getByText("文件格式不符合已批准模板")).toBeInTheDocument();
    expect(screen.getByText(/FT_XLSX_SCATTER_V1 1.0.0/)).toBeInTheDocument();
    expect(screen.getByText("1 / 3")).toBeInTheDocument();
    expect(screen.getByText("REPROCESS_UPDATE")).toBeInTheDocument();
    expect(screen.getAllByText("显式重清洗（INITIAL_IMPORT 原子执行）")).toHaveLength(3);
    expect(screen.queryByText(/^INITIAL_IMPORT$/)).not.toBeInTheDocument();
    expect(screen.getByText("JOB_QUEUED")).toBeInTheDocument();
    expect(screen.getByText("JOB_FAILED")).toBeInTheDocument();
    expect(screen.getByText("来源血缘")).toBeInTheDocument();
    expect(screen.getByText("LOT-001-A.xlsx")).toBeInTheDocument();
    expect(screen.getByText("Writer 已验证")).toBeInTheDocument();
    expect(screen.getByText("仅批次收件，Writer 未验证")).toBeInTheDocument();
    expect(screen.queryByText(/source_path|file:\/\/|C:\\/i)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "未接入动作" })).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: /#90/ }));
    fireEvent.click(screen.getByRole("button", { name: /#92/ }));
    expect(props.onSelectJob).toHaveBeenNthCalledWith(1, 90);
    expect(props.onSelectJob).toHaveBeenNthCalledWith(2, 92);

    fireEvent.click(screen.getByRole("button", { name: /Dataset #20/ }));
    expect(props.onOpenAnalytics).toHaveBeenCalledWith(20, 3);
    fireEvent.click(screen.getByRole("button", { name: "打开分析" }));
    expect(props.onOpenAnalytics).toHaveBeenCalledTimes(2);

    fireEvent.click(screen.getByRole("button", { name: "重新处理" }));
    await waitFor(() => expect(reprocessStageBatch).toHaveBeenCalledWith("PRODUCTION", "FT", 14));
  }, 15_000);

  it("does not infer a fake Worker-online state for queued work", async () => {
    renderDrawer({
      ...details,
      job: {
        ...details.job,
        status: "QUEUED",
        error_code: null,
        error_message: null,
        started_at_utc: null,
        finished_at_utc: null,
      },
    });

    expect(await screen.findByText(/排队等待：/)).toBeInTheDocument();
    expect(screen.queryByText("Worker 在线")).not.toBeInTheDocument();
  }, 15_000);

  it("does not infer a management action when the backend disables it", async () => {
    renderDrawer({
      ...details,
      actions: [{ code: "REPROCESS_BATCH", label: "重新处理", enabled: false, reason: "仅上传人可以重新处理" }],
    });

    const action = await screen.findByRole("button", { name: "重新处理" });
    expect(action).toBeDisabled();
    expect(action).toHaveAttribute("title", "仅上传人可以重新处理");
    fireEvent.click(action);
    expect(reprocessStageBatch).not.toHaveBeenCalled();
  }, 15_000);

  it("renders nullable or omitted optional details without a blank drawer", async () => {
    renderDrawer({ job: { status: "SUCCESS" }, children: null, timeline: null, actions: null });

    expect(await screen.findByText("任务摘要")).toBeInTheDocument();
    expect(screen.getByText("Cleaner Release")).toBeInTheDocument();
    expect(screen.getByText("当前没有可执行动作。")).toBeInTheDocument();
    expect(screen.getByText("当前 Job 未提供来源血缘")).toBeInTheDocument();
    expect(screen.getByText("暂无状态历史")).toBeInTheDocument();
  }, 15_000);
});
