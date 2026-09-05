// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AnalyticsContextRequest } from "../../api/analytics";
import {
  cancelAnalyticsExport,
  createAnalyticsExport,
  downloadAnalyticsExportArtifact,
  getAnalyticsExportDownloadMetadata,
  listAnalyticsExports,
  type AnalyticsExportRecord,
} from "../../api/analyticsExports";
import { useAuth } from "../auth/AuthContext";
import { AnalyticsExportPanel } from "./AnalyticsExportPanel";
import { createDefaultAnalysisViewState, type AnalysisViewState } from "./context/analysisViewState";

vi.mock("../../api/analyticsExports", async (importOriginal) => {
  const original = await importOriginal<typeof import("../../api/analyticsExports")>();
  return { ...original, cancelAnalyticsExport: vi.fn(), createAnalyticsExport: vi.fn(), downloadAnalyticsExportArtifact: vi.fn(), getAnalyticsExportDownloadMetadata: vi.fn(), listAnalyticsExports: vi.fn() };
});
vi.mock("../auth/AuthContext", () => ({ useAuth: vi.fn() }));

Object.defineProperty(window, "matchMedia", { writable: true, value: () => ({ matches: false, addListener: () => undefined, removeListener: () => undefined, addEventListener: () => undefined, removeEventListener: () => undefined }) });
vi.stubGlobal("ResizeObserver", class { observe() { return undefined; } unobserve() { return undefined; } disconnect() { return undefined; } });

const context: AnalyticsContextRequest = {
  datasets: [{ dataset_id: 20, version_no: 1 }],
  filters: { lot_ids: ["LOT-A"], wafer_ids: ["W1"], bin_codes: ["1"], overall_results: ["FAIL"], source_ids: ["SRC-1"], tester_ids: ["T-1"], program_versions: ["P-1"], test_conditions: ["C-1"] },
  parameters: ["VTH"],
};
const ruleContext = { spec_versions: ["SPEC:7"], bin_mapping_versions: ["BIN:2"], evaluation_rule_versions: ["RULE:3"] };
const queued: AnalyticsExportRecord = {
  export_job_id: 81, requested_by: 7, contract_version: "ANALYTICS_EXPORT_V1", worker_contract_version: "ANALYTICS_EXPORT_WORKER_V1", generation_mode: "ASYNC", status: "QUEUED",
  export_scope: "CURRENT_PAGE", export_format: "CSV", template_code: "ANALYTICS_DETAIL", template_version: "v1",
  datasets: [{ dataset_version_id: 201, dataset_id: 20, version_no: 1, ordinal_no: 1, test_stage: "CP" }], filters: context.filters, parameters: context.parameters,
  filter_hash: "a".repeat(64), context_hash: "b".repeat(64), rule_context: ruleContext, artifact_ttl_hours: 24, page: 2, page_size: 50,
  chart_config: { show_spec_overlay: true, correlation_min_abs: 0 }, display_config: { section: "overview", page: 2, page_size: 50, focus_dataset_id: 20 }, presentation_hash: "e".repeat(64),
  idempotency_key: "analytics-request-0001", request_reason_sha256: "c".repeat(64), requested_at_utc: "2026-08-31T00:00:00Z", started_at_utc: null, finished_at_utc: null,
  exported_row_count: null, row_version: "00000000000000AF", idempotent_replay: false,
};

function renderPanel(testStage = "CP", permissions = ["DATASET_READ", "EXPORT_DATA"], viewState?: AnalysisViewState) {
  vi.mocked(useAuth).mockReturnValue({
    user: { user_id: 7, login_name: "exporter", display_name: "Exporter", department_code: null, roles: ["EXPORTER"], permissions },
    loading: false, login: vi.fn(), logout: vi.fn(), can: (permission) => permissions.includes(permission),
  });
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(<QueryClientProvider client={queryClient}><AnalyticsExportPanel context={context} ruleContext={ruleContext} testStage={testStage} focusDatasetId={20} page={2} pageSize={50} viewState={viewState} chartDisplayState={createDefaultAnalysisViewState().display} /></QueryClientProvider>);
}

describe("AnalyticsExportPanel", () => {
  beforeEach(() => {
    vi.mocked(listAnalyticsExports).mockResolvedValue({ items: [queued], total: 1, page: 1, page_size: 10, integrity_blocked_job_ids: [], integrity_blocked_count: 0 });
    vi.mocked(createAnalyticsExport).mockResolvedValue(queued);
    vi.mocked(cancelAnalyticsExport).mockResolvedValue({ ...queued, status: "CANCELLED", row_version: "00000000000000B0" });
    vi.mocked(downloadAnalyticsExportArtifact).mockResolvedValue();
    vi.mocked(getAnalyticsExportDownloadMetadata).mockResolvedValue({
      export_job_id: 81, job_status: "SUCCESS", availability: "ARTIFACT_METADATA_READY", download_enabled: true, reason_code: "READY",
      artifacts: [{ export_artifact_id: 501, file_name: "analytics.csv", mime_type: "text/csv", file_size: 2048, sha256: "d".repeat(64), created_at_utc: "2026-08-31T01:00:00Z", expires_at_utc: "2026-09-01T01:00:00Z" }],
    });
  });
  afterEach(() => { cleanup(); vi.useRealTimers(); vi.clearAllMocks(); });

  it("refreshes pending report metadata until the artifact becomes downloadable", async () => {
    const ready = await getAnalyticsExportDownloadMetadata(81);
    vi.mocked(getAnalyticsExportDownloadMetadata).mockClear();
    vi.mocked(getAnalyticsExportDownloadMetadata)
      .mockResolvedValueOnce({ ...ready, job_status: "QUEUED", availability: "PENDING_GENERATION", download_enabled: false, artifacts: [] })
      .mockResolvedValue(ready);
    renderPanel();
    await screen.findByText("#81");
    fireEvent.click(screen.getByRole("button", { name: /查看结果/ }));
    await screen.findByText("制品不可下载");
    expect(await screen.findByText("制品可下载", {}, { timeout: 6000 })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /下载/ })).toBeEnabled();
    expect(getAnalyticsExportDownloadMetadata).toHaveBeenCalledTimes(2);
  }, 20_000);

  it("reuses a failed submission key only while its request is unchanged", async () => {
    vi.mocked(createAnalyticsExport).mockRejectedValue(new Error("temporary disconnect"));
    renderPanel();
    await screen.findByText("#81");
    const reason = screen.getByRole("textbox", { name: "Export 原因" });
    fireEvent.change(reason, { target: { value: "Export current reviewed selection" } });
    fireEvent.click(screen.getByRole("button", { name: /生成报告/ }));
    await screen.findByText("Export Job 提交失败");
    const first = vi.mocked(createAnalyticsExport).mock.calls[0][0].idempotency_key;
    fireEvent.click(screen.getByRole("button", { name: /生成报告/ }));
    await waitFor(() => expect(createAnalyticsExport).toHaveBeenCalledTimes(2));
    await screen.findByText("Export Job 提交失败");
    expect(vi.mocked(createAnalyticsExport).mock.calls[1][0].idempotency_key).toBe(first);
    fireEvent.change(reason, { target: { value: "Export corrected reviewed selection" } });
    fireEvent.click(screen.getByRole("button", { name: /生成报告/ }));
    await waitFor(() => expect(createAnalyticsExport).toHaveBeenCalledTimes(3));
    expect(vi.mocked(createAnalyticsExport).mock.calls[2][0].idempotency_key).not.toBe(first);
  }, 20_000);

  it("submits an allowed CURRENT_PAGE request with full Context, Rule Context and page bounds", async () => {
    const defaults = createDefaultAnalysisViewState();
    const viewState: AnalysisViewState = {
      ...defaults,
      analysis: {
        ...defaults.analysis,
        detail: {
          view: "LONG", sortBy: "RESULT", sortDirection: "DESC",
          evaluation_filter: { evaluation_type: "PAT", evaluation_results: ["FAIL"], rule_code: "CP_PAT", rule_version: "V2" },
          measurement_filter: { parameter: "VTH", lower_bound: 1.2, upper_bound: null, lower_inclusive: true, upper_inclusive: true },
        },
      },
    };
    renderPanel("CP", ["DATASET_READ", "EXPORT_DATA"], viewState);
    expect(await screen.findByText("#81")).toBeInTheDocument();
    fireEvent.change(screen.getByRole("textbox", { name: "Export 原因" }), { target: { value: "Export reviewed current page" } });
    fireEvent.click(screen.getByRole("button", { name: /生成报告/ }));

    await waitFor(() => expect(createAnalyticsExport).toHaveBeenCalledWith({
      ...context,
      contract_version: "ANALYTICS_EXPORT_V1",
      export_scope: "CURRENT_PAGE",
      export_format: "CSV",
      template_code: "ANALYTICS_DETAIL",
      template_version: "v1",
      rule_context: ruleContext,
      chart_config: expect.objectContaining({
        y_axis_min: null, y_axis_max: null, color_min: null, color_max: null,
        brush_enabled: true, show_spec_overlay: true, spatial_layer_mode: "STACK",
        visible_wafer_keys: [], correlation_min_abs: 0,
        analysis_view_state: expect.objectContaining({
          contract_version: "ANALYSIS_VIEW_STATE_V1",
          components: expect.objectContaining({
            detail: expect.objectContaining({
              view: "LONG", sortBy: "RESULT", sortDirection: "DESC",
              evaluation_filter: { evaluation_type: "PAT", evaluation_results: ["FAIL"], rule_code: "CP_PAT", rule_version: "V2" },
              measurement_filter: { parameter: "VTH", lower_bound: 1.2, upper_bound: null, lower_inclusive: true, upper_inclusive: true },
            }),
          }),
        }),
      }),
      display_config: { section: "overview", page: 2, page_size: 50, focus_dataset_id: 20 },
      artifact_ttl_hours: 24,
      idempotency_key: expect.stringMatching(/^analytics-/),
      page: 2,
      page_size: 50,
      reason: "Export reviewed current page",
    }));
    expect(screen.getByTitle("Presentation Hash")).toHaveTextContent(/^P e{12}…$/);
  }, 20_000);

  it("warns about page-local historical integrity blocks while preserving normal jobs and creation", async () => {
    vi.mocked(listAnalyticsExports).mockResolvedValue({
      items: [queued],
      total: 3,
      page: 1,
      page_size: 10,
      integrity_blocked_job_ids: [1, 77],
      integrity_blocked_count: 2,
    });

    renderPanel();

    expect(await screen.findByText("历史 Job 完整性阻断")).toBeInTheDocument();
    expect(screen.getByText(/#1、#77/)).toBeInTheDocument();
    expect(screen.getByText("#81")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /生成报告/ })).toBeEnabled();
  }, 20_000);

  it("shows verified artifact metadata and uses the fixed authenticated download contract", async () => {
    renderPanel();
    await screen.findByText("#81");
    fireEvent.click(screen.getByRole("button", { name: /查看结果/ }));

    expect(await screen.findByText("制品可下载")).toBeInTheDocument();
    expect(screen.getByText("analytics.csv")).toBeInTheDocument();
    expect(screen.getByText("2.0 KB")).toBeInTheDocument();
    expect(getAnalyticsExportDownloadMetadata).toHaveBeenCalledWith(81);
    fireEvent.click(screen.getByRole("button", { name: /下载/ }));
    await waitFor(() => expect(downloadAnalyticsExportArtifact).toHaveBeenCalledWith(81, 501, "analytics.csv"));
  }, 20_000);

  it("submits the exact report analysis envelope and blocks a selected analysis without its Rule", async () => {
    const viewState = createDefaultAnalysisViewState();
    renderPanel("CP", ["DATASET_READ", "EXPORT_DATA"], viewState);
    await screen.findByText("#81");
    fireEvent.mouseDown(screen.getByRole("combobox", { name: "Export Template" }));
    fireEvent.click(await screen.findByTitle("ANALYTICS_OVERVIEW@v1"));
    fireEvent.change(screen.getByRole("textbox", { name: "Export 原因" }), { target: { value: "Export exact overview analysis" } });
    fireEvent.click(screen.getByRole("button", { name: /生成报告/ }));

    await waitFor(() => expect(createAnalyticsExport).toHaveBeenCalledWith(expect.objectContaining({
      template_code: "ANALYTICS_OVERVIEW",
      export_scope: "REPORT",
      export_format: "PNG",
      chart_config: expect.objectContaining({
        analysis: {
          contract_version: "ANALYTICS_EXPORT_ANALYSIS_CONFIG_V1",
          section: "OVERVIEW",
          overview: { evaluations: [] },
        },
        analysis_view_state: {
          contract_version: "ANALYSIS_VIEW_STATE_V1",
          components: viewState.analysis,
        },
      }),
    })));

    cleanup();
    vi.clearAllMocks();
    const missingRuleState: AnalysisViewState = {
      ...viewState,
      analysis: {
        ...viewState.analysis,
        parameterAnalysis: {
          ...viewState.analysis.parameterAnalysis,
          analyses: ["BOX_PLOT"],
          boxPlot: { ruleCode: "", versionCode: "" },
        },
      },
    };
    renderPanel("CP", ["DATASET_READ", "EXPORT_DATA"], missingRuleState);
    await screen.findByText("#81");
    fireEvent.mouseDown(screen.getByRole("combobox", { name: "Export Template" }));
    fireEvent.click(await screen.findByTitle("PARAMETER_ANALYSIS@v1"));
    fireEvent.change(screen.getByRole("textbox", { name: "Export 原因" }), { target: { value: "Export invalid parameter analysis" } });
    fireEvent.click(screen.getByRole("button", { name: /生成报告/ }));

    expect(await screen.findByText("Export Job 提交失败")).toBeInTheDocument();
    expect(screen.getByText(/Box Plot Rule Code 未配置/)).toBeInTheDocument();
    expect(createAnalyticsExport).not.toHaveBeenCalled();
  }, 35_000);

  it("filters the template registry by Test Stage and blocks missing export permission", async () => {
    renderPanel("FT");
    fireEvent.mouseDown(screen.getByRole("combobox", { name: "Export Template" }));
    expect(await screen.findByTitle("FT_QUALITY@v1")).toBeInTheDocument();
    expect(screen.queryByTitle("SPATIAL_ANALYSIS@v1")).not.toBeInTheDocument();
    cleanup();
    vi.clearAllMocks();

    renderPanel("CP", ["DATASET_READ"]);
    expect(screen.getByText("无导出权限")).toBeInTheDocument();
    expect(listAnalyticsExports).not.toHaveBeenCalled();
    expect(createAnalyticsExport).not.toHaveBeenCalled();
  }, 20_000);
});
