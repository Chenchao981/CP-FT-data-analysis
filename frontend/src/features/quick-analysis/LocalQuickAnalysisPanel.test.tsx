// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  clearLocalAgentRunReference,
  deleteLocalRun,
  getLocalAgentHealth,
  getLocalRun,
  getLocalRunReceipt,
  getLocalRunResult,
  listLocalAgentTools,
  previewLocalSelection,
  runLocalSelection,
  saveLocalAgentRunReference,
  selectLocalFolder,
  storedLocalAgentToken,
  storedLocalAgentRunReference,
} from "../../api/localAgent";
import { getLocalQuickCapability, registerLocalQuickResult } from "../../api/quickAnalysis";
import { LocalQuickAnalysisPanel } from "./LocalQuickAnalysisPanel";

vi.mock("../../api/localAgent", () => ({
  clearLocalAgentRunReference: vi.fn(),
  deleteLocalRun: vi.fn(),
  getLocalAgentHealth: vi.fn(),
  getLocalRun: vi.fn(),
  getLocalRunReceipt: vi.fn(),
  getLocalRunResult: vi.fn(),
  listLocalAgentTools: vi.fn(),
  previewLocalSelection: vi.fn(),
  runLocalSelection: vi.fn(),
  saveLocalAgentRunReference: vi.fn(),
  saveLocalAgentToken: vi.fn(),
  selectLocalFolder: vi.fn(),
  storedLocalAgentToken: vi.fn(),
  storedLocalAgentRunReference: vi.fn(),
}));

vi.mock("../../api/quickAnalysis", () => ({
  getLocalQuickCapability: vi.fn(),
  registerLocalQuickResult: vi.fn(),
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

const releaseSha = "c".repeat(64);
const ftTool = {
  tool_code: "JIEQUN_FT_QUICK_PAT_EXISTING",
  display_name: "杰群 FT 原始目录低内存 PAT",
  test_stage: "FT" as const,
  factory_code: "JIEQUN",
  analysis_type: "QUICK_PAT",
  input_contract_version: "JIEQUN_UNIFIED_CSV_DIRECTORY_V1",
  output_contract_version: "FT_PAT_RESULT_V1",
  entrypoint: "factories.jiequn.pat_cleaner.generate_raw_pat",
  allowed_suffixes: [".csv"],
  enabled: true,
  disabled_reason: null,
  package_sha256: releaseSha,
  timeout_seconds: 7200,
  max_output_bytes: 64 * 1024 * 1024,
};
const cpGate = {
  ...ftTool,
  tool_code: "CP_RAW_QUICK_PAT",
  display_name: "CP 原始目录快速 PAT",
  test_stage: "CP" as const,
  factory_code: "UNAPPROVED",
  enabled: false,
  disabled_reason: "现有 CP 工具尚无已批准的原始目录 Quick PAT 入口和输出合同",
  package_sha256: null,
  timeout_seconds: null,
  max_output_bytes: null,
};

function renderPanel(onRegistered = vi.fn()) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={queryClient}><LocalQuickAnalysisPanel onRegistered={onRegistered} /></QueryClientProvider>);
  return onRegistered;
}

describe("LocalQuickAnalysisPanel", () => {
  beforeEach(() => {
    vi.mocked(storedLocalAgentToken).mockReturnValue("pairing-token");
    vi.mocked(storedLocalAgentRunReference).mockReturnValue(null);
    vi.mocked(getLocalAgentHealth).mockResolvedValue({ status: "ok", service: "tms-local-agent", version: "1.0", pairing_required: true, pairing_token_ttl_seconds: 28_800 });
    vi.mocked(deleteLocalRun).mockResolvedValue(undefined);
    vi.mocked(listLocalAgentTools).mockResolvedValue([ftTool, cpGate]);
    vi.mocked(getLocalQuickCapability).mockResolvedValue({
      contract_version: "TMS_LOCAL_RESULT_V1",
      tool_code: ftTool.tool_code,
      test_stage: "FT",
      factory_code: "JIEQUN",
      analysis_type: "QUICK_PAT",
      release: {
        cleaner_release_id: 21,
        cleaner_code: ftTool.tool_code,
        cleaner_version: "2.19.0",
        sha256: releaseSha,
        entrypoint: ftTool.entrypoint,
        adapter_code: "JIEQUN_FT_QUICK_PAT_PYZ",
        input_contract_version: ftTool.input_contract_version,
        output_contract_version: ftTool.output_contract_version,
        timeout_seconds: 7200,
        max_output_bytes: 64 * 1024 * 1024,
      },
      upload: {
        multipart_receipt_field: "receipt_json",
        multipart_result_field: "result_file",
        accepted_extension: ".xlsx",
      },
    });
    vi.mocked(selectLocalFolder).mockResolvedValue({ selection_id: "selection-1", source_label: "NCEAP020N10LL" });
    vi.mocked(previewLocalSelection).mockResolvedValue({
      mode: "LOCAL_PATH_SIZE_MTIME_V1",
      file_count: 520,
      total_bytes: 3_041_085_645,
      sha256: "a".repeat(64),
      source_label: "NCEAP020N10LL",
      tool_code: ftTool.tool_code,
      allowed_suffixes: [".csv"],
    });
    vi.mocked(runLocalSelection).mockResolvedValue({
      run_id: "run-1",
      status: "QUEUED",
    });
    vi.mocked(getLocalRun).mockResolvedValue({
      run_id: "run-1", selection_id: "selection-1", tool_code: ftTool.tool_code, source_label: "NCEAP020N10LL",
      status: "SUCCESS", parameter_count: 23, record_count: 6_813_800, elapsed_seconds: 127.745, error_code: null, error_message: null,
      created_at_utc: "2026-09-01T00:00:00Z", started_at_utc: "2026-09-01T00:00:01Z", finished_at_utc: "2026-09-01T00:02:09Z",
    });
    vi.mocked(getLocalRunReceipt).mockResolvedValue({
      contract_version: "TMS_LOCAL_RESULT_V1",
      tool_code: ftTool.tool_code,
      analysis_type: "QUICK_PAT",
      test_stage: "FT",
      factory_code: "JIEQUN",
      release_sha256: releaseSha,
      source_label: "NCEAP020N10LL",
      manifest: { mode: "LOCAL_PATH_SIZE_MTIME_V1", sha256: "a".repeat(64), file_count: 520, total_bytes: 3_041_085_645 },
      summary: { parameter_count: 23, record_count: 6_813_800, elapsed_seconds: 127.745 },
      result: { filename: "PAT_001.xlsx", size_bytes: 4, sha256: "b".repeat(64) },
    });
    vi.mocked(getLocalRunResult).mockResolvedValue(new Blob(["xlsx"]));
    vi.mocked(registerLocalQuickResult).mockResolvedValue({ analysis_session_id: 91 } as never);
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("selects an opaque local folder, runs beside the data, and uploads only the result", async () => {
    const onRegistered = renderPanel();
    fireEvent.click(screen.getByRole("button", { name: /连\s*接/ }));
    expect(await screen.findByText("杰群 FT 原始目录低内存 PAT")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /选择本机目录/ }));
    expect(await screen.findByText("NCEAP020N10LL")).toBeInTheDocument();
    expect(document.body).toHaveTextContent("520");
    expect(document.body).toHaveTextContent("2.83 GB");
    expect(document.body).not.toHaveTextContent("F:\\data");

    fireEvent.click(screen.getByRole("button", { name: /确认 Manifest 并在本机计算/ }));
    expect((await screen.findAllByText("本机计算完成")).length).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole("button", { name: /仅上传并登记结果/ }));

    await waitFor(() => expect(registerLocalQuickResult).toHaveBeenCalledTimes(1));
    expect(registerLocalQuickResult).toHaveBeenCalledWith(expect.objectContaining({ source_label: "NCEAP020N10LL" }), expect.any(Blob));
    expect(saveLocalAgentRunReference).toHaveBeenNthCalledWith(1, "run-1");
    expect(saveLocalAgentRunReference).toHaveBeenNthCalledWith(2, "run-1", 91);
    expect(deleteLocalRun).toHaveBeenCalledWith("run-1");
    expect(clearLocalAgentRunReference).toHaveBeenCalledWith("run-1");
    expect(onRegistered).toHaveBeenCalled();
    expect((await screen.findAllByText("结果已登记为个人快速分析会话 91")).length).toBeGreaterThan(0);
  }, 15_000);

  it("shows the CP contract gate instead of pretending CP PAT is available", async () => {
    renderPanel();
    fireEvent.click(screen.getByRole("button", { name: /连\s*接/ }));
    await screen.findByText("杰群 FT 原始目录低内存 PAT");
    fireEvent.click(screen.getByText("CP 工具"));

    expect(await screen.findByText("CP 原始目录快速 PAT")).toBeInTheDocument();
    expect(screen.getByText("现有 CP 工具尚无已批准的原始目录 Quick PAT 入口和输出合同")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /选择本机目录/ })).toBeDisabled();
  });

  it("blocks execution when the Agent and released execution limits differ", async () => {
    vi.mocked(listLocalAgentTools).mockResolvedValue([
      { ...ftTool, max_output_bytes: 32 * 1024 * 1024 },
      cpGate,
    ]);
    renderPanel();
    fireEvent.click(screen.getByRole("button", { name: /连\s*接/ }));

    expect(await screen.findByText("Agent 与 TMS 登记的工具合同不一致")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /选择本机目录/ }));
    expect(await screen.findByText("NCEAP020N10LL")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /确认 Manifest 并在本机计算/ })).toBeDisabled();
    expect(runLocalSelection).not.toHaveBeenCalled();
  });

  it("restores a persisted active run after a page reload and keeps it while switching stages", async () => {
    vi.mocked(storedLocalAgentRunReference).mockReturnValue({
      run_id: "run-1",
      registered_session_id: null,
    });
    renderPanel();

    expect((await screen.findAllByText("本机计算完成")).length).toBeGreaterThan(0);
    expect(getLocalRun).toHaveBeenCalledWith("run-1");
    fireEvent.click(screen.getByText("CP 工具"));
    expect(screen.getByRole("button", { name: /仅上传并登记结果/ })).toBeInTheDocument();
  });

  it("explicitly cleans a recovered failed run before clearing its persisted reference", async () => {
    vi.mocked(storedLocalAgentRunReference).mockReturnValue({
      run_id: "run-1",
      registered_session_id: null,
    });
    vi.mocked(getLocalRun).mockResolvedValue({
      run_id: "run-1", selection_id: "selection-1", tool_code: ftTool.tool_code, source_label: "NCEAP020N10LL",
      status: "FAILED", parameter_count: null, record_count: null, elapsed_seconds: null, error_code: "LOCAL_TOOL_FAILED", error_message: "格式不符合合同",
      created_at_utc: "2026-09-01T00:00:00Z", started_at_utc: "2026-09-01T00:00:01Z", finished_at_utc: "2026-09-01T00:02:09Z",
    });
    renderPanel();

    fireEvent.click(await screen.findByRole("button", { name: /清理失败任务/ }));
    await waitFor(() => expect(deleteLocalRun).toHaveBeenCalledWith("run-1"));
    expect(clearLocalAgentRunReference).toHaveBeenCalledWith("run-1");
  });
});
