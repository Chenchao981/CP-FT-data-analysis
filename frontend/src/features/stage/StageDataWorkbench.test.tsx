// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { listStageResults, listStageUploads } from "../../api/stageData";
import { useAuth } from "../auth/AuthContext";
import { StageDataWorkbench, type StageDataWorkbenchProps } from "./StageDataWorkbench";

vi.mock("../../api/stageData", () => ({
  downloadStageUploadFile: vi.fn(),
  listStageResults: vi.fn(),
  listStageUploads: vi.fn(),
  reprocessStageBatch: vi.fn(),
  uploadStageData: vi.fn(),
}));

vi.mock("../auth/AuthContext", () => ({ useAuth: vi.fn() }));

vi.mock("./LotEnrichmentModal", () => ({
  LotEnrichmentModal: ({ open }: { open: boolean }) => open ? <div role="dialog" aria-label="Lot补录弹窗">Lot补录弹窗</div> : null,
}));

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

const baseUpload = {
  receipt_id: 1,
  source_file_id: 101,
  original_file_name: "source.xlsx",
  extension: "xlsx",
  size_bytes: 1024,
  factory_code: "riyuexin",
  upload_time_utc: "2026-08-27T08:00:00Z",
  completion_time_utc: null,
  uploader_login: "operator",
  uploader_name: "操作员",
  latest_job_id: 1,
  error_code: null,
  error_message: null,
  action_required: null,
};

const uploadRows = [
  { ...baseUpload, import_batch_id: 11, sequence_no: 1, receipt_id: 11, source_file_id: 111, original_file_name: "missing-a.xlsx", status: "NEEDS_INPUT", action_required: "LOT_ID" as const, error_code: "LOT_ID_MISSING", error_message: "未取得 Lot" },
  { ...baseUpload, import_batch_id: 11, sequence_no: 2, receipt_id: 12, source_file_id: 112, original_file_name: "missing-b.xlsx", status: "NEEDS_INPUT", action_required: "LOT_ID" as const, error_code: "LOT_ID_MISSING", error_message: "未取得 Lot" },
  { ...baseUpload, import_batch_id: 12, sequence_no: 1, receipt_id: 21, source_file_id: 121, original_file_name: "failed-a.xlsx", status: "FAILED", error_code: "PARSER_FAILED", error_message: "文件格式不符合要求" },
  { ...baseUpload, import_batch_id: 12, sequence_no: 2, receipt_id: 22, source_file_id: 122, original_file_name: "failed-b.xlsx", status: "FAILED", error_code: "PARSER_FAILED", error_message: "文件格式不符合要求" },
  { ...baseUpload, import_batch_id: 13, sequence_no: 1, receipt_id: 31, source_file_id: 131, original_file_name: "running.xlsx", status: "PROCESSING" },
  { ...baseUpload, import_batch_id: 14, sequence_no: 1, receipt_id: 41, source_file_id: 141, original_file_name: "done.xlsx", status: "PROCESSED", completion_time_utc: "2026-08-27T08:01:00Z" },
];

const resultRows = [1, 2].map((resultSummaryId) => ({
  result_summary_id: resultSummaryId,
  import_batch_id: 14,
  data_name: `result-${resultSummaryId}`,
  product_name: "PRODUCT",
  lot_id: "LOT-DONE",
  wafer_count: null,
  factory_code: "riyuexin",
  test_item_count: 10,
  unit_count: 100,
  pass_count: null,
  yield_rate: null,
  status: "PROCESSED",
  data_type: "FT",
  dataset_id: 20,
  dataset_version_no: 1,
  created_at_utc: "2026-08-27T08:01:00Z",
}));

const user = {
  user_id: 1,
  login_name: "operator",
  display_name: "操作员",
  department_code: null,
  roles: ["OPERATOR"],
  permissions: ["DATASET_READ", "TASK_CREATE", "ANALYSIS_RUN"],
};

function renderWorkbench(props: StageDataWorkbenchProps = { businessDomain: "PRODUCTION", testStage: "FT" }) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const view = render(
    <QueryClientProvider client={queryClient}>
      <StageDataWorkbench {...props} />
    </QueryClientProvider>,
  );
  return {
    ...view,
    rerenderWorkbench: (next: StageDataWorkbenchProps) => view.rerender(
      <QueryClientProvider client={queryClient}>
        <StageDataWorkbench {...next} />
      </QueryClientProvider>,
    ),
  };
}

describe("StageDataWorkbench Lot input states", () => {
  beforeEach(() => {
    vi.mocked(listStageUploads).mockResolvedValue(uploadRows);
    vi.mocked(listStageResults).mockResolvedValue(resultRows);
    vi.mocked(useAuth).mockReturnValue({
      user,
      loading: false,
      login: vi.fn(),
      logout: vi.fn(),
      can: (permission: string) => user.permissions.includes(permission),
    });
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("shows one action per batch and counts every KPI by distinct batch", async () => {
    renderWorkbench();

    expect(await screen.findAllByRole("button", { name: /补录批次号/ })).toHaveLength(1);
    expect(screen.getAllByText("Job #1").length).toBeGreaterThan(0);
    expect(screen.getAllByRole("button", { name: /失败详情/ })).toHaveLength(1);
    const cards = [
      ["上传批次", "4"],
      ["处理中", "1"],
      ["已处理批次", "1"],
      ["待补录批次", "1"],
      ["失败批次", "1"],
    ] as const;
    for (const [title, value] of cards) {
      const titleNode = screen.getAllByText(title).find((node) => node.classList.contains("ant-statistic-title"));
      const card = (titleNode?.closest(".ant-card") ?? null) as HTMLElement | null;
      expect(card).not.toBeNull();
      expect(within(card!).getByText(value)).toBeInTheDocument();
    }
  }, 15_000);

  it("opens Lot input only for authorized users and clears it when the CP/FT route changes", async () => {
    const view = renderWorkbench();
    fireEvent.click((await screen.findAllByRole("button", { name: /补录批次号/ }))[0]);
    expect(screen.getByRole("dialog", { name: "Lot补录弹窗" })).toBeInTheDocument();

    view.rerenderWorkbench({ businessDomain: "PRODUCTION", testStage: "CP" });
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Lot补录弹窗" })).not.toBeInTheDocument());
  }, 15_000);

  it("hides the Lot correction action without TASK_CREATE permission", async () => {
    vi.mocked(useAuth).mockReturnValue({
      user: { ...user, permissions: ["DATASET_READ"] },
      loading: false,
      login: vi.fn(),
      logout: vi.fn(),
      can: (permission: string) => permission === "DATASET_READ",
    });
    renderWorkbench();
    await screen.findByText("missing-a.xlsx");
    expect(screen.queryByRole("button", { name: /补录批次号/ })).not.toBeInTheDocument();
  }, 15_000);

  it("refreshes results once when an active upload reaches a terminal status", async () => {
    const activeRows = uploadRows.filter((row) => row.import_batch_id === 13);
    const completedRows = activeRows.map((row) => ({
      ...row,
      status: "PROCESSED",
      completion_time_utc: "2026-08-27T08:02:00Z",
    }));
    vi.mocked(listStageUploads)
      .mockResolvedValueOnce(activeRows)
      .mockResolvedValue(completedRows);

    renderWorkbench();
    await screen.findByText("running.xlsx");
    expect(listStageResults).toHaveBeenCalledTimes(1);

    await waitFor(() => expect(listStageUploads).toHaveBeenCalledTimes(2), { timeout: 5_000 });
    await waitFor(() => expect(listStageResults).toHaveBeenCalledTimes(2), { timeout: 5_000 });
  }, 15_000);
});
