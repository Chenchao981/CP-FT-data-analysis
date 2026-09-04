// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  createQuickPat,
  downloadQuickPat,
  listQuickAnalysisSessions,
  listQuickSourceDirectories,
  listQuickSourceRoots,
  previewQuickSourceManifest,
  type QuickAnalysisSession,
} from "../../api/quickAnalysis";
import { QuickAnalysisWorkbench } from "./QuickAnalysisWorkbench";

vi.mock("./DirectPathAnalysisPanel", () => ({
  DirectPathAnalysisPanel: () => <div>direct-path-panel</div>,
}));

vi.mock("./LocalQuickAnalysisPanel", () => ({
  LocalQuickAnalysisPanel: () => <div>local-agent-panel</div>,
}));

vi.mock("./TemporaryFtpPanel", () => ({
  TemporaryFtpPanel: () => <div>temporary-ftp-panel</div>,
}));

vi.mock("../../components/EChart", () => ({
  EChart: ({ ariaLabel }: { ariaLabel?: string }) => <div role="img" aria-label={ariaLabel} />,
}));

vi.mock("../../api/quickAnalysis", () => ({
  createQuickPat: vi.fn(),
  downloadQuickPat: vi.fn(),
  listQuickAnalysisSessions: vi.fn(),
  listQuickSourceDirectories: vi.fn(),
  listQuickSourceRoots: vi.fn(),
  previewQuickSourceManifest: vi.fn(),
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

const completed: QuickAnalysisSession = {
  analysis_session_id: 9,
  owner_user_id: 1,
  owner_login: "engineer",
  owner_name: "一线工程师",
  access_scope: "DOMAIN",
  data_domain_id: 7,
  data_domain_code: "JIEQUN_FT",
  analysis_type: "QUICK_PAT",
  test_stage: "FT",
  factory_code: "JIEQUN",
  source_root_code: "JIEQUN_SHARED",
  source_relative_path: "NCEAP020N10LL/LOT-01",
  source_manifest_mode: "PATH_SIZE_MTIME_V1",
  source_manifest_sha256: "a".repeat(64),
  source_file_count: 520,
  source_total_bytes: 1024,
  retention_mode: "RESULT_ONLY",
  cleaner_release_id: 15,
  status: "SUCCESS",
  job_id: 109,
  job_status: "SUCCESS",
  parameter_count: 18,
  record_count: 6813800,
  summary: {
    elapsed_seconds: 97.927,
    parameters: [{ parameter: "VTH", count: 6813800, q1: 3.9, median: 4.1, q3: 4.3, lcl_after: 2.8, ucl_after: 5.4, updated: true }],
  },
  result_file_name: "PAT.xlsx",
  result_size_bytes: 2048,
  error_code: null,
  error_message: null,
  expires_at_utc: "2026-09-05T00:00:00Z",
  created_at_utc: "2026-08-29T00:00:00Z",
  started_at_utc: "2026-08-29T00:00:01Z",
  finished_at_utc: "2026-08-29T00:01:39Z",
};

function renderWorkbench() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <QuickAnalysisWorkbench />
    </QueryClientProvider>,
  );
}

describe("QuickAnalysisWorkbench", () => {
  beforeEach(() => {
    vi.mocked(listQuickSourceRoots).mockResolvedValue([{
      code: "JIEQUN_SHARED",
      name: "杰群原始数据",
      test_stage: "FT",
      factory_code: "JIEQUN",
      allowed_suffixes: [".csv"],
      data_domain_code: "JIEQUN_FT",
      data_domain_id: 7,
      available: true,
    }]);
    vi.mocked(listQuickSourceDirectories).mockResolvedValue({
      root_code: "JIEQUN_SHARED",
      current_relative_path: ".",
      parent_relative_path: null,
      directories: [],
    });
    vi.mocked(previewQuickSourceManifest).mockResolvedValue({
      root_code: "JIEQUN_SHARED",
      relative_path: ".",
      mode: "PATH_SIZE_MTIME_V1",
      recursive: true,
      file_count: 520,
      total_bytes: 1024,
      sha: "a".repeat(64),
      allowed_suffixes: [".csv"],
      tool_code: "JIEQUN_FT_QUICK_PAT_EXISTING",
    });
    vi.mocked(listQuickAnalysisSessions).mockResolvedValue({
      items: [completed],
      total: 1,
      page: 1,
      page_size: 20,
    });
    vi.mocked(createQuickPat).mockResolvedValue({ ...completed, analysis_session_id: 10, status: "QUEUED" });
    vi.mocked(downloadQuickPat).mockResolvedValue(undefined);
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("previews and confirms the exact recursive manifest before creating PAT", async () => {
    renderWorkbench();

    expect(await screen.findByText("direct-path-panel")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: /已配置服务器/ }));
    await screen.findByText("杰群原始数据");
    expect(screen.getByText("数据域 JIEQUN_FT")).toBeInTheDocument();
    await waitFor(() => expect(listQuickAnalysisSessions).toHaveBeenCalledWith(expect.objectContaining({
      page: 1,
      page_size: 20,
      from_utc: expect.stringMatching(/Z$/),
      to_utc: expect.stringMatching(/Z$/),
    })));
    const createButton = screen.getByRole("button", { name: /确认范围并计算 PAT/ });
    await waitFor(() => expect(createButton).toBeEnabled());
    fireEvent.click(createButton);
    expect(await screen.findByText("确认快速 PAT 处理范围")).toBeInTheDocument();
    expect(document.body).toHaveTextContent("520");

    fireEvent.click(screen.getByRole("button", { name: /确认并创建任务/ }));
    await waitFor(() => expect(createQuickPat).toHaveBeenCalledWith(
      "JIEQUN_SHARED",
      ".",
      "PATH_SIZE_MTIME_V1",
      "a".repeat(64),
    ));
  }, 15_000);

  it("keeps VDMOS as a separate personal tool outside formal CP and FT data", async () => {
    renderWorkbench();

    fireEvent.click(await screen.findByRole("tab", { name: /VDMOS 个人工具/ }));
    const link = screen.getByRole("link", { name: /打开 VDMOS 个人工具/ });
    expect(link).toHaveAttribute("href", "/personal-tools/vdmos/VDMOS_Tool_v8.9.html");
    expect(document.body).not.toHaveTextContent("正式数据资产");
  }, 15_000);

  it("offers a personal-computer agent path that never uploads raw source", async () => {
    renderWorkbench();

    fireEvent.click(await screen.findByRole("tab", { name: /个人电脑（Agent）/ }));
    expect(screen.getByText("local-agent-panel")).toBeInTheDocument();
  }, 15_000);

  it("keeps a missing download failure visible", async () => {
    vi.mocked(downloadQuickPat).mockRejectedValueOnce(new Error("结果文件不可用"));
    renderWorkbench();

    const button = await screen.findByRole(
      "button",
      { name: /下载结果/ },
      { timeout: 5_000 },
    );
    fireEvent.click(button);
    expect(await screen.findByText("结果下载失败")).toBeInTheDocument();
    expect(document.body).toHaveTextContent("如仍失败请联系系统管理员");
  }, 15_000);

  it("shows completed results as server history instead of expiring output", async () => {
    renderWorkbench();

    expect(await screen.findByText("历史分析结果")).toBeInTheDocument();
    expect(await screen.findByText("服务器历史")).toBeInTheDocument();
    expect(document.body).not.toHaveTextContent("结果到期");
  }, 15_000);

  it("expands a completed PAT session into the shared result chart and table", async () => {
    renderWorkbench();
    const expand = await screen.findByRole("button", { name: "Expand row" });
    fireEvent.click(expand);
    expect(await screen.findByRole("img", { name: "PAT 分析结果图表" })).toBeInTheDocument();
    expect(screen.getByText("FT · JIEQUN · PAT分析结果")).toBeInTheDocument();
    expect(screen.getAllByText("VTH").length).toBeGreaterThan(0);
    expect(screen.getByText("2.8 / 5.4")).toBeInTheDocument();
  }, 15_000);
});
