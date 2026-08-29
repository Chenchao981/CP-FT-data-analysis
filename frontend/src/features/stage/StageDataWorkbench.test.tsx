// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { downloadStageUploadFile, listFormalSourceDirectories, listFormalSourceRoots, listStageResultsPage, listStageUploadsPage, previewFormalSourceManifest, uploadStageData } from "../../api/stageData";
import { useAuth } from "../auth/AuthContext";
import { StageDataWorkbench, type StageDataWorkbenchProps } from "./StageDataWorkbench";

vi.mock("../../api/stageData", () => ({
  downloadStageUploadFile: vi.fn(),
  listFormalSourceDirectories: vi.fn(),
  listFormalSourceRoots: vi.fn(),
  listStageResultsPage: vi.fn(),
  listStageUploadsPage: vi.fn(),
  previewFormalSourceManifest: vi.fn(),
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
    vi.mocked(listFormalSourceRoots).mockResolvedValue([]);
    vi.mocked(listFormalSourceDirectories).mockResolvedValue({ root_code: "ROOT", current_relative_path: ".", parent_relative_path: null, directories: [] });
    vi.mocked(previewFormalSourceManifest).mockResolvedValue({
      root_code: "ROOT",
      relative_path: ".",
      mode: "PATH_SIZE_MTIME_V1",
      recursive: false,
      file_count: 1,
      total_bytes: 1024,
      sha: "a".repeat(64),
      allowed_suffixes: [".xlsx"],
    });
    vi.mocked(listStageUploadsPage).mockResolvedValue({ items: uploadRows, total: uploadRows.length, page: 1, page_size: 20 });
    vi.mocked(listStageResultsPage).mockResolvedValue({ items: resultRows, total: resultRows.length, page: 1, page_size: 20 });
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

  it("shows one action per batch and reports server totals plus current-page states", async () => {
    renderWorkbench();

    await screen.findByText("missing-a.xlsx", {}, { timeout: 15_000 });
    expect(screen.getAllByRole("button", { name: /补录批次号/ })).toHaveLength(1);
    expect(screen.getAllByText("Job #1").length).toBeGreaterThan(0);
    expect(screen.getAllByRole("button", { name: /失败详情/ })).toHaveLength(1);
    const cards = [
      ["查询上传记录", "6"],
      ["当前页处理中", "1"],
      ["查询清洗结果", "2"],
      ["当前页待补录", "1"],
      ["当前页失败", "1"],
    ] as const;
    for (const [title, value] of cards) {
      const titleNode = screen.getAllByText(title).find((node) => node.classList.contains("ant-statistic-title"));
      const card = (titleNode?.closest(".ant-card") ?? null) as HTMLElement | null;
      expect(card).not.toBeNull();
      expect(within(card!).getByText(value)).toBeInTheDocument();
    }
  }, 30_000);

  it("opens Lot input only for authorized users and clears it when the CP/FT route changes", async () => {
    const view = renderWorkbench();
    await screen.findByText("missing-a.xlsx", {}, { timeout: 15_000 });
    fireEvent.click(screen.getAllByRole("button", { name: /补录批次号/ })[0]);
    expect(screen.getByRole("dialog", { name: "Lot补录弹窗" })).toBeInTheDocument();

    view.rerenderWorkbench({ businessDomain: "PRODUCTION", testStage: "CP" });
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Lot补录弹窗" })).not.toBeInTheDocument());
  }, 30_000);

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
  }, 30_000);

  it("refreshes results once when an active upload reaches a terminal status", async () => {
    const activeRows = uploadRows.filter((row) => row.import_batch_id === 13);
    const completedRows = activeRows.map((row) => ({
      ...row,
      status: "PROCESSED",
      completion_time_utc: "2026-08-27T08:02:00Z",
    }));
    vi.mocked(listStageUploadsPage)
      .mockResolvedValueOnce({ items: activeRows, total: 1, page: 1, page_size: 20 })
      .mockResolvedValue({ items: completedRows, total: 1, page: 1, page_size: 20 });

    renderWorkbench();
    await screen.findByText("running.xlsx");
    expect(listStageResultsPage).toHaveBeenCalledTimes(1);

    await waitFor(() => expect(listStageUploadsPage).toHaveBeenCalledTimes(2), { timeout: 5_000 });
    await waitFor(() => expect(listStageResultsPage).toHaveBeenCalledTimes(2), { timeout: 5_000 });
  }, 30_000);

  it("submits shared filters to both server-paged entry points", async () => {
    renderWorkbench();
    await screen.findByText("missing-a.xlsx");

    fireEvent.change(screen.getByLabelText("产品"), { target: { value: "NCE-IGBT" } });
    fireEvent.change(screen.getByLabelText("Lot"), { target: { value: "LOT-202608" } });
    fireEvent.click(screen.getByRole("button", { name: /服务端检索/ }));

    await waitFor(() => expect(listStageUploadsPage).toHaveBeenLastCalledWith(
      "PRODUCTION",
      "FT",
      expect.objectContaining({ page: 1, page_size: 20, product_name: "NCE-IGBT", lot_id: "LOT-202608" }),
    ));
    expect(listStageResultsPage).toHaveBeenLastCalledWith(
      "PRODUCTION",
      "FT",
      expect.objectContaining({ page: 1, page_size: 20, product_name: "NCE-IGBT", lot_id: "LOT-202608" }),
    );
  }, 30_000);

  it("keeps upload pagination controlled by the server response", async () => {
    const processedRows = uploadRows.filter((row) => row.status === "PROCESSED");
    vi.mocked(listStageUploadsPage).mockResolvedValue({ items: processedRows, total: 45, page: 1, page_size: 20 });
    renderWorkbench();
    await screen.findByText("done.xlsx", {}, { timeout: 15_000 });

    fireEvent.click(screen.getByTitle("2"));

    await waitFor(() => expect(listStageUploadsPage).toHaveBeenLastCalledWith(
      "PRODUCTION",
      "FT",
      expect.objectContaining({ page: 2, page_size: 20 }),
    ));
  }, 30_000);

  it("shows backend queue age without claiming a Worker state", async () => {
    vi.mocked(listStageUploadsPage).mockResolvedValue({
      items: [{ ...baseUpload, import_batch_id: 15, sequence_no: 1, status: "QUEUED", queue_age_seconds: 73 }],
      total: 1,
      page: 1,
      page_size: 20,
    });
    renderWorkbench();

    expect(await screen.findByText("队列等待观测（当前页）", {}, { timeout: 15_000 })).toBeInTheDocument();
    expect(screen.getAllByText(/73 秒/).length).toBeGreaterThan(0);
    expect(screen.getByText(/AUDIT_READ/)).toBeInTheDocument();
    expect(screen.queryByText("Worker 在线")).not.toBeInTheDocument();
  }, 30_000);

  it("opens Job details and keeps unknown yield visibly unknown", async () => {
    const onOpenJob = vi.fn();
    renderWorkbench({ businessDomain: "PRODUCTION", testStage: "FT", onOpenJob });

    fireEvent.click((await screen.findAllByRole("button", { name: /Job #1/ }))[0]);
    expect(onOpenJob).toHaveBeenCalledWith(1);

    fireEvent.click(screen.getByRole("tab", { name: "清洗结果" }));
    const resultCell = await screen.findByText("result-1");
    const resultRow = resultCell.closest("tr");
    expect(resultRow).not.toBeNull();
    expect(within(resultRow!).getAllByText("—").length).toBeGreaterThan(0);
    expect(within(resultRow!).queryByText("0.00%")).not.toBeInTheDocument();
  }, 30_000);

  it("shows a visible error when an authenticated source download fails", async () => {
    vi.mocked(downloadStageUploadFile).mockRejectedValueOnce(new Error("源文件已不在受管区"));
    renderWorkbench();

    await screen.findByText("missing-a.xlsx", {}, { timeout: 15_000 });
    fireEvent.click(screen.getAllByRole("button", { name: /下载/ })[0]);

    expect(await screen.findByText("源文件已不在受管区")).toBeInTheDocument();
    expect(screen.getAllByText("源文件下载失败").length).toBeGreaterThan(0);
  }, 30_000);

  it("submits a formal catalog directory without sending browser-local files", async () => {
    vi.mocked(listFormalSourceRoots).mockResolvedValue([{
      code: "RIYUEXIN_PRODUCTION_G2",
      name: "日月新量产灰度目录",
      test_stage: "FT",
      factory_code: "RIYUEXIN",
      allowed_suffixes: [".xlsx"],
      purpose: "FORMAL_IMPORT",
      business_domains: ["PRODUCTION"],
      available: true,
    }]);
    vi.mocked(listFormalSourceDirectories).mockResolvedValue({
      root_code: "RIYUEXIN_PRODUCTION_G2",
      current_relative_path: "accepted-lot",
      parent_relative_path: ".",
      directories: [],
    });
    const manifestSha = "b".repeat(64);
    vi.mocked(previewFormalSourceManifest).mockResolvedValue({
      root_code: "RIYUEXIN_PRODUCTION_G2",
      relative_path: "accepted-lot",
      mode: "PATH_SIZE_MTIME_V1",
      recursive: false,
      file_count: 2,
      total_bytes: 4096,
      sha: manifestSha,
      allowed_suffixes: [".xlsx"],
    });
    vi.mocked(uploadStageData).mockResolvedValue({
      import_batch_id: 99,
      job_id: 199,
      status: "QUEUED",
      input_mode: "SOURCE_CATALOG",
      business_domain: "PRODUCTION",
      test_stage: "FT",
      cleaner_release: { cleaner_release_id: 1, cleaner_code: "FT_XLSX_SCATTER_V1", cleaner_version: "1.0.0" },
    });

    renderWorkbench();
    fireEvent.click(await screen.findByRole("button", { name: /上传数据/ }));
    fireEvent.click(screen.getByRole("radio", { name: /受控服务器目录/ }));

    expect(await screen.findByText("日月新量产灰度目录")).toBeInTheDocument();
    await waitFor(() => expect(listFormalSourceDirectories).toHaveBeenCalledWith(
      "PRODUCTION",
      "FT",
      "riyuexin",
      "RIYUEXIN_PRODUCTION_G2",
      ".",
    ));
    await waitFor(() => expect(previewFormalSourceManifest).toHaveBeenCalledWith(
      "PRODUCTION",
      "FT",
      "riyuexin",
      "RIYUEXIN_PRODUCTION_G2",
      "accepted-lot",
    ));
    expect(await screen.findByText("仅当前目录")).toBeInTheDocument();
    expect(screen.getByText(manifestSha)).toBeInTheDocument();
    const submit = screen.getByRole("button", { name: "提交后台清洗" });
    expect(submit).toBeDisabled();
    fireEvent.click(screen.getByRole("checkbox"));
    expect(submit).toBeEnabled();
    fireEvent.click(submit);

    await waitFor(() => expect(uploadStageData).toHaveBeenCalledWith(
      "PRODUCTION",
      "FT",
      [],
      "riyuexin",
      undefined,
      "RIYUEXIN_PRODUCTION_G2",
      "accepted-lot",
      "PATH_SIZE_MTIME_V1",
      manifestSha,
    ));
  }, 30_000);

  it("clears catalog confirmation and refreshes the manifest after submit fails", async () => {
    vi.mocked(listFormalSourceRoots).mockResolvedValue([{
      code: "RIYUEXIN_PRODUCTION_G2",
      name: "日月新量产灰度目录",
      test_stage: "FT",
      factory_code: "RIYUEXIN",
      allowed_suffixes: [".xlsx"],
      purpose: "FORMAL_IMPORT",
      business_domains: ["PRODUCTION"],
      available: true,
    }]);
    vi.mocked(listFormalSourceDirectories).mockResolvedValue({
      root_code: "RIYUEXIN_PRODUCTION_G2",
      current_relative_path: "accepted-lot",
      parent_relative_path: ".",
      directories: [],
    });
    vi.mocked(previewFormalSourceManifest).mockResolvedValue({
      root_code: "RIYUEXIN_PRODUCTION_G2",
      relative_path: "accepted-lot",
      mode: "PATH_SIZE_MTIME_V1",
      recursive: false,
      file_count: 2,
      total_bytes: 4096,
      sha: "c".repeat(64),
      allowed_suffixes: [".xlsx"],
    });
    vi.mocked(uploadStageData).mockRejectedValueOnce(new Error("源目录清单已变化"));

    renderWorkbench();
    fireEvent.click(await screen.findByRole("button", { name: /上传数据/ }));
    fireEvent.click(screen.getByRole("radio", { name: /受控服务器目录/ }));
    const confirmation = await screen.findByRole("checkbox");
    fireEvent.click(confirmation);
    fireEvent.click(screen.getByRole("button", { name: "提交后台清洗" }));

    await waitFor(() => expect(uploadStageData).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(previewFormalSourceManifest).toHaveBeenCalledTimes(2));
    expect(confirmation).not.toBeChecked();
    expect(screen.getByRole("button", { name: "提交后台清洗" })).toBeDisabled();
  }, 30_000);
});
