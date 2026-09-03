// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { StrictMode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AnalyticsContextRequest } from "../../api/analytics";
import { ApiError } from "../../api/auth";
import {
  analyzeParameterRelationship,
  type ParameterRelationshipResult,
} from "../../api/parameterRelationship";
import { ParameterRelationshipPanel } from "./ParameterRelationshipPanel";
import { createDefaultAnalysisViewState, type AnalysisDisplayState, type AnalysisViewState } from "./context/analysisViewState";

vi.mock("../../api/parameterRelationship", () => ({
  analyzeParameterRelationship: vi.fn(),
}));
vi.mock("../../components/EChart", () => ({
  EChart: ({ option, ariaLabel, onEvents }: { option: unknown; ariaLabel?: string; onEvents?: { click?: (payload: unknown) => void } }) =>
    <div role="img" aria-label={ariaLabel} data-option={JSON.stringify(option)} onClick={() => onEvents?.click?.(
      ariaLabel === "Correlation Heatmap"
        ? { data: { scatterX: "RDON", scatterY: "VTH", groupKey: "TESTER:T-1", datasetId: 20, versionNo: 1, value: [1, 0, 0.82] } }
        : { data: { drilldownKey: "UNIT:501" }, dataIndex: 1234, unit_id: 1234 },
    )} />,
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
HTMLElement.prototype.scrollIntoView = vi.fn();

const context: AnalyticsContextRequest = {
  datasets: [{ dataset_id: 20, version_no: 1 }, { dataset_id: 21, version_no: 2 }],
  filters: {
    lot_ids: ["LOT-A"],
    wafer_ids: ["W1"],
    bin_codes: ["1"],
    overall_results: ["PASS", "FAIL"],
    source_ids: ["SRC-1"],
    tester_ids: ["T-1"],
    program_versions: ["P-1"],
    test_conditions: ["C-1"],
  },
  parameters: ["VTH", "RDON"],
};

const result: ParameterRelationshipResult = {
  contract_version: "PARAMETER_RELATIONSHIP_V1",
  dataset_context: {
    resolved_datasets: [
      { dataset_id: 20, version_no: 1, dataset_name: "DS20", test_stage: "FT", product_name: "P-A" },
      { dataset_id: 21, version_no: 2, dataset_name: "DS21", test_stage: "FT", product_name: "P-B" },
    ],
    test_stage: "FT",
    current_published_verified: true,
  },
  filter_summary: {
    normalized_filters: context.filters,
    parameters: ["VTH", "RDON"],
    filter_hash: "a".repeat(64),
    context_hash: "b".repeat(64),
  },
  rule_context: {
    spec_versions: ["SPEC:7:V2"],
    bin_mapping_versions: [],
    evaluation_rule_versions: ["RULE:CORRELATION_RULE:v1:PEARSON_PAIRWISE_V1"],
  },
  capabilities: [
    { code: "PARAMETER_SCATTER", status: "AVAILABLE", reason_code: null, message: null },
    { code: "PARAMETER_TREND", status: "AVAILABLE", reason_code: null, message: null },
    { code: "PARAMETER_CORRELATION", status: "AVAILABLE", reason_code: null, message: null },
  ],
  counts: { input_units: 100, included_units: 80, excluded_units: 20, pass_count: 60, fail_count: 20, unknown_count: 0, abort_count: 0, known_yield_denominator: 80, yield_rate: 0.75, unknown_abort_denominator: 80, unknown_abort_rate: 0, missing_measurements: 3 },
  sampling_summary: { sampled: true, method: "OOS_PRESERVING_HASH_RANK_V1", original_points: 20_000, returned_points: 5_000, preserved_out_of_spec_points: 2 },
  group_by: "TESTER",
  trend_order_basis: "DATASET_ORDINAL_THEN_RUN_SOURCE_TIME_THEN_RUN_ID_THEN_UNIT_SEQUENCE_THEN_UNIT_ID",
  items: [{
    dataset_id: 20,
    version_no: 1,
    group_key: "TESTER:T-1",
    identities: [
      { name: "VTH", canonical_parameter_code: "VTH_CANONICAL", step_code: "S1", sequence_no: 1, unit: "V", program_lsl: 100, program_usl: 500, test_condition: "C-1", formal_lsl: 1, formal_usl: 2, formal_lower_operator: ">=", formal_upper_operator: "<=", formal_spec_status: "RESOLVED", formal_spec_reason_codes: [], formal_spec_versions: ["SPEC:7:V2"] },
      { name: "RDON", canonical_parameter_code: "RDON_CANONICAL", step_code: "S2", sequence_no: 2, unit: "mOhm", program_lsl: 200, program_usl: 800, test_condition: "C-1", formal_lsl: 0, formal_usl: 5, formal_lower_operator: ">=", formal_upper_operator: "<=", formal_spec_status: "RESOLVED", formal_spec_reason_codes: [], formal_spec_versions: ["SPEC:7:V2"] },
    ],
    scatter_points: [
      { dataset_id: 20, version_no: 1, group_key: "TESTER:T-1", x_parameter: "VTH", y_parameter: "RDON", x_value: 1.2, y_value: 2.3, x_out_of_spec: false, y_out_of_spec: false, drilldown_key: "UNIT:501" },
      { dataset_id: 20, version_no: 1, group_key: "TESTER:T-1", x_parameter: "VTH", y_parameter: "RDON", x_value: 2.2, y_value: 6.3, x_out_of_spec: true, y_out_of_spec: true, drilldown_key: "UNIT:502" },
    ],
    trend_points: [
      { dataset_id: 20, version_no: 1, group_key: "TESTER:T-1", parameter: "VTH", sequence: 1, ordinal: 1, source_sequence: 1, run_id: 31, ordered_at: "2026-08-31T00:00:00Z", value: 1.2, out_of_spec: false, drilldown_key: "UNIT:501" },
      { dataset_id: 20, version_no: 1, group_key: "TESTER:T-1", parameter: "VTH", sequence: 1, ordinal: 2, source_sequence: 1, run_id: 32, ordered_at: "2026-08-31T00:01:00Z", value: 2.2, out_of_spec: true, drilldown_key: "UNIT:502" },
    ],
    correlations: [
      { dataset_id: 20, version_no: 1, group_key: "TESTER:T-1", x_parameter: "VTH", y_parameter: "VTH", sample_count: 82, coefficient: 1, status: "ELIGIBLE", reason_code: null, method: "PEARSON_PAIRWISE_V1", rule_code: "CORRELATION_RULE:v1" },
      { dataset_id: 20, version_no: 1, group_key: "TESTER:T-1", x_parameter: "VTH", y_parameter: "RDON", sample_count: 80, coefficient: 0.82, status: "ELIGIBLE", reason_code: null, method: "PEARSON_PAIRWISE_V1", rule_code: "CORRELATION_RULE:v1" },
      { dataset_id: 20, version_no: 1, group_key: "TESTER:T-1", x_parameter: "RDON", y_parameter: "VTH", sample_count: 80, coefficient: 0.82, status: "ELIGIBLE", reason_code: null, method: "PEARSON_PAIRWISE_V1", rule_code: "CORRELATION_RULE:v1" },
      { dataset_id: 20, version_no: 1, group_key: "TESTER:T-1", x_parameter: "RDON", y_parameter: "RDON", sample_count: 81, coefficient: 1, status: "ELIGIBLE", reason_code: null, method: "PEARSON_PAIRWISE_V1", rule_code: "CORRELATION_RULE:v1" },
    ],
  }],
  warnings: [],
  computed_at: "2026-08-31T00:00:00+00:00",
};

function renderPanel(
  onOpenDrilldown = vi.fn(),
  displayState: AnalysisDisplayState = createDefaultAnalysisViewState().display,
  onDisplayStateChange = vi.fn(),
  config?: AnalysisViewState["analysis"]["parameterRelationship"],
  onConfigChange = vi.fn(),
) {
  const queryClient = new QueryClient({ defaultOptions: { mutations: { retry: false }, queries: { retry: false } } });
  render(<QueryClientProvider client={queryClient}>
    <ParameterRelationshipPanel context={context} parameterOptions={["IDSS", "RDON", "VTH"]} suggestedParameters={["VTH", "RDON"]} onOpenDrilldown={onOpenDrilldown} displayState={displayState} onDisplayStateChange={onDisplayStateChange} config={config} onConfigChange={config ? onConfigChange : undefined} />
  </QueryClientProvider>);
  return { onOpenDrilldown, onDisplayStateChange, onConfigChange };
}

async function selectValue(label: string, value: string) {
  fireEvent.mouseDown(screen.getByRole("combobox", { name: label }));
  fireEvent.click(await screen.findByTitle(value));
}

function openAdvancedRuleSettings() {
  fireEvent.click(screen.getByRole("button", { name: /高级设置：查看或调试规则版本/ }));
}

describe("ParameterRelationshipPanel", () => {
  beforeEach(() => { vi.mocked(analyzeParameterRelationship).mockResolvedValue(result); });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("executes one deferred auto-run after the React StrictMode remount", async () => {
    const queryClient = new QueryClient({ defaultOptions: { mutations: { retry: false }, queries: { retry: false } } });
    const config = {
      ...createDefaultAnalysisViewState().analysis.parameterRelationship,
      xParameter: "VTH",
      yParameters: ["RDON"],
    };
    render(<StrictMode><QueryClientProvider client={queryClient}>
      <ParameterRelationshipPanel
        context={context}
        parameterOptions={["RDON", "VTH"]}
        suggestedParameters={["VTH", "RDON"]}
        onOpenDrilldown={vi.fn()}
        displayState={createDefaultAnalysisViewState().display}
        onDisplayStateChange={vi.fn()}
        config={config}
        onConfigChange={vi.fn()}
        autoRunKey="draw-1"
      />
    </QueryClientProvider></StrictMode>);

    await waitFor(() => expect(analyzeParameterRelationship).toHaveBeenCalledTimes(1));
    expect(await screen.findByRole("img", { name: "VTH / RDON Scatter" })).toBeInTheDocument();
  });

  it("sends exact X/Y plus all Context filters, renders server charts/sampling/correlation, and drills by backend key", async () => {
    vi.mocked(analyzeParameterRelationship).mockImplementation(async (request) => request.x_parameter === "RDON"
      ? {
          ...result,
          items: result.items.map((item) => ({
            ...item,
            scatter_points: [{
              dataset_id: 20, version_no: 1, group_key: "TESTER:T-1", x_parameter: "RDON", y_parameter: "VTH",
              x_value: 2.3, y_value: 1.2, x_out_of_spec: false, y_out_of_spec: false, drilldown_key: "UNIT:501",
            }],
          })),
        }
      : result);
    const { onOpenDrilldown } = renderPanel(vi.fn(), {
      ...createDefaultAnalysisViewState().display,
      yAxisMin: -1,
      yAxisMax: 7,
    });
    expect(analyzeParameterRelationship).not.toHaveBeenCalled();

    await selectValue("关系分析类型", "Trend");
    await selectValue("关系分析类型", "Correlation");
    await selectValue("关系分析分组", "Tester");
    fireEvent.change(screen.getByRole("spinbutton", { name: "关系分析最大点数" }), { target: { value: "5000" } });
    openAdvancedRuleSettings();
    fireEvent.change(screen.getByRole("textbox", { name: "Correlation Rule Code" }), { target: { value: "CORRELATION_RULE" } });
    fireEvent.change(screen.getByRole("textbox", { name: "Correlation Rule Version" }), { target: { value: "v1" } });
    expect(screen.getByText("当前相关性规则：CORRELATION_RULE@v1")).toBeInTheDocument();
    expect(analyzeParameterRelationship).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "执行参数关系分析" }));

    await waitFor(() => expect(analyzeParameterRelationship).toHaveBeenCalled());
    expect(vi.mocked(analyzeParameterRelationship).mock.calls.at(-1)?.[0]).toEqual({
      datasets: context.datasets,
      filters: context.filters,
      x_parameter: "VTH",
      y_parameters: ["RDON"],
      analyses: ["SCATTER", "TREND", "CORRELATION"],
      group_by: "TESTER",
      max_points: 5_000,
      correlation: { method: "PEARSON_PAIRWISE_V1", rule_code: "CORRELATION_RULE", version_code: "v1" },
    });
    expect(await screen.findByText("服务端已执行确定性采样")).toBeInTheDocument();
    expect(screen.getByText(/返回 5000 \/ 原始 20000 点；保留 OOS 2 点/)).toBeInTheDocument();
    expect(screen.getByText("Correlation capability：AVAILABLE")).toBeInTheDocument();
    expect(screen.getByText("VTH_CANONICAL")).toBeInTheDocument();
    expect(screen.getByText("RDON_CANONICAL")).toBeInTheDocument();
    expect(screen.getAllByText("0.82")).toHaveLength(2);

    const scatter = screen.getByRole("img", { name: "VTH / RDON Scatter" });
    const scatterOption = JSON.parse(scatter.getAttribute("data-option") ?? "{}");
    expect(scatterOption.series[0].data).toHaveLength(2);
    expect(scatterOption.series[0].data[1].drilldownKey).toBe("UNIT:502");
    expect(scatterOption.series[0].data[1].itemStyle.color).toBe("#d64545");
    expect(scatterOption.brush.toolbox).toEqual(["rect", "polygon", "clear"]);
    expect(scatterOption.toolbox.feature.saveAsImage.name).toBe("VTH-RDON-scatter");
    expect(scatterOption.yAxis).toEqual(expect.objectContaining({ min: -1, max: 7 }));
    expect(scatterOption.series[0].markLine.data).toEqual(expect.arrayContaining([
      expect.objectContaining({ name: "VTH Formal LSL", xAxis: 1 }),
      expect.objectContaining({ name: "RDON Formal USL", yAxis: 5 }),
    ]));
    fireEvent.click(scatter);
    expect(onOpenDrilldown).toHaveBeenCalledWith("UNIT:501");
    expect(onOpenDrilldown).not.toHaveBeenCalledWith("1234");

    const trendOption = JSON.parse(screen.getByRole("img", { name: "VTH Trend" }).getAttribute("data-option") ?? "{}");
    expect(trendOption.yAxis).toEqual(expect.objectContaining({ min: -1, max: 7 }));
    expect(trendOption.toolbox.feature.saveAsImage.name).toBe("VTH-trend");

    fireEvent.click(screen.getByLabelText("Out-of-spec"));
    await waitFor(() => {
      const nextOption = JSON.parse(screen.getByRole("img", { name: "VTH / RDON Scatter" }).getAttribute("data-option") ?? "{}");
      expect(nextOption.series[0].data).toHaveLength(1);
    });

    const heatmap = screen.getByRole("img", { name: "Correlation Heatmap" });
    const heatmapOption = JSON.parse(heatmap.getAttribute("data-option") ?? "{}");
    expect(heatmapOption.series[0].type).toBe("heatmap");
    expect(heatmapOption.xAxis.data).toEqual(["VTH", "RDON"]);
    expect(heatmapOption.yAxis.data).toEqual(["VTH", "RDON"]);
    expect(heatmapOption.series[0].data).toHaveLength(4);
    expect(heatmapOption.series[0].data).toEqual(expect.arrayContaining([
      expect.objectContaining({ groupKey: "TESTER:T-1", scatterX: "RDON", scatterY: "VTH", sampleCount: 80 }),
    ]));
    fireEvent.click(heatmap);
    await waitFor(() => expect(vi.mocked(analyzeParameterRelationship).mock.calls.at(-1)?.[0]).toEqual(expect.objectContaining({
      x_parameter: "RDON",
      y_parameters: ["VTH"],
    })));
    await waitFor(() => expect(HTMLElement.prototype.scrollIntoView).toHaveBeenCalled());
  }, 40_000);

  it("offers Lot/Wafer/Test Batch grouping and keeps display threshold outside the authority request", async () => {
    const onDisplayStateChange = vi.fn();
    renderPanel(vi.fn(), { ...createDefaultAnalysisViewState().display, yAxisMax: 7 }, onDisplayStateChange);
    await selectValue("关系分析类型", "Correlation");
    await selectValue("关系分析分组", "Wafer（Lot + Wafer）");
    openAdvancedRuleSettings();
    fireEvent.change(screen.getByRole("textbox", { name: "Correlation Rule Code" }), { target: { value: "CORRELATION_RULE" } });
    fireEvent.change(screen.getByRole("textbox", { name: "Correlation Rule Version" }), { target: { value: "v1" } });
    fireEvent.click(screen.getByRole("button", { name: "执行参数关系分析" }));

    await waitFor(() => expect(vi.mocked(analyzeParameterRelationship).mock.calls[0]?.[0]).toEqual(expect.objectContaining({ group_by: "WAFER" })));
    fireEvent.change(await screen.findByRole("spinbutton", { name: "Correlation 绝对值阈值" }), { target: { value: "0.9" } });
    expect(onDisplayStateChange).toHaveBeenCalledWith({ correlationMinAbs: 0.9 });
    fireEvent.change(screen.getByRole("spinbutton", { name: "参数关系 Y 轴最小值" }), { target: { value: "-3" } });
    fireEvent.change(screen.getByRole("spinbutton", { name: "参数关系 Y 轴最大值" }), { target: { value: "" } });
    expect(onDisplayStateChange).toHaveBeenCalledWith({ yAxisMin: -3 });
    expect(onDisplayStateChange).toHaveBeenCalledWith({ yAxisMax: null });
    fireEvent.click(screen.getByRole("checkbox", { name: "Scatter Brush" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Released Formal Spec" }));
    expect(onDisplayStateChange).toHaveBeenCalledWith({ brushEnabled: false });
    expect(onDisplayStateChange).toHaveBeenCalledWith({ showSpecOverlay: false });
    expect(vi.mocked(analyzeParameterRelationship).mock.calls).toHaveLength(1);
  }, 40_000);

  it("uses unique NxN row keys and applies one atomic config patch for a heatmap selection", async () => {
    vi.mocked(analyzeParameterRelationship).mockImplementation(async (request) => request.x_parameter === "RDON"
      ? {
          ...result,
          items: result.items.map((item) => ({
            ...item,
            scatter_points: [{
              dataset_id: 20, version_no: 1, group_key: "TESTER:T-1", x_parameter: "RDON", y_parameter: "VTH",
              x_value: 2.3, y_value: 1.2, x_out_of_spec: false, y_out_of_spec: false, drilldown_key: "UNIT:501",
            }],
          })),
        }
      : result);
    const defaults = createDefaultAnalysisViewState();
    const config: AnalysisViewState["analysis"]["parameterRelationship"] = {
      ...defaults.analysis.parameterRelationship,
      xParameter: "VTH",
      yParameters: ["RDON"],
      analyses: ["SCATTER", "CORRELATION"],
      groupBy: "TESTER",
      correlation: { method: "PEARSON_PAIRWISE_V1", ruleCode: "CORRELATION_RULE", versionCode: "v1" },
    };
    const onConfigChange = vi.fn();
    renderPanel(vi.fn(), defaults.display, vi.fn(), config, onConfigChange);

    fireEvent.click(screen.getByRole("button", { name: "执行参数关系分析" }));
    expect(await screen.findByRole("img", { name: "Correlation Heatmap" })).toBeInTheDocument();
    const correlationRowKeys = Array.from(document.querySelectorAll<HTMLTableRowElement>('tr[data-row-key*="TESTER:T-1"]'))
      .map((row) => row.dataset.rowKey);
    expect(correlationRowKeys).toHaveLength(4);
    expect(new Set(correlationRowKeys).size).toBe(4);

    const callsBeforeClick = onConfigChange.mock.calls.length;
    fireEvent.click(screen.getByRole("img", { name: "Correlation Heatmap" }));
    await waitFor(() => expect(vi.mocked(analyzeParameterRelationship).mock.calls.at(-1)?.[0].x_parameter).toBe("RDON"));
    expect(onConfigChange).toHaveBeenCalledTimes(callsBeforeClick + 1);
    expect(onConfigChange).toHaveBeenLastCalledWith({
      xParameter: "RDON",
      yParameters: ["VTH"],
      analyses: ["SCATTER", "CORRELATION"],
      scatterY: "VTH",
      scatterDataset: "20:1",
      displayGroups: ["TESTER:T-1"],
    });
  }, 40_000);

  it("omits correlation rule fields when Correlation is not requested", async () => {
    renderPanel();
    fireEvent.click(screen.getByRole("button", { name: "执行参数关系分析" }));

    await waitFor(() => expect(analyzeParameterRelationship).toHaveBeenCalled());
    expect(vi.mocked(analyzeParameterRelationship).mock.calls.at(-1)?.[0]).toEqual(expect.objectContaining({
      analyses: ["SCATTER"],
      correlation: {},
    }));
  });

  it("shows NO_SPEC and never draws Program Limit as a formal overlay", async () => {
    vi.mocked(analyzeParameterRelationship).mockResolvedValueOnce({
      ...result,
      items: result.items.map((item) => ({
        ...item,
        identities: item.identities.map((identity) => ({
          ...identity,
          formal_lsl: null,
          formal_usl: null,
          formal_spec_status: "NO_SPEC" as const,
          formal_spec_reason_codes: ["FORMAL_RELEASED_SPEC_NOT_FOUND"],
          formal_spec_versions: [],
        })),
      })),
      warnings: ["FORMAL_SPEC_NO_SPEC:20:V1:VTH:FORMAL_RELEASED_SPEC_NOT_FOUND"],
    });
    renderPanel();
    fireEvent.click(screen.getByRole("button", { name: "执行参数关系分析" }));

    expect(await screen.findByText("部分参数没有唯一 Released Formal Spec")).toBeInTheDocument();
    expect(screen.getByText(/绝不回退 Program Limit/)).toBeInTheDocument();
    const option = JSON.parse(screen.getByRole("img", { name: "VTH / RDON Scatter" }).getAttribute("data-option") ?? "{}");
    expect(option.series[0].markLine).toBeUndefined();
  }, 20_000);

  it("surfaces the server correlation approval gate without client-side fallback", async () => {
    vi.mocked(analyzeParameterRelationship).mockRejectedValueOnce(new ApiError(409, {
      code: "ANALYSIS_RULE_NOT_APPROVED",
      message: "the requested correlation rule has no approved server-side activation",
      retryable: false,
      recommended_action: "complete Rule Owner approval before activation",
    }, "request failed"));
    renderPanel();
    await selectValue("关系分析类型", "Correlation");
    openAdvancedRuleSettings();
    fireEvent.change(screen.getByRole("textbox", { name: "Correlation Rule Code" }), { target: { value: "CORRELATION_RULE" } });
    fireEvent.change(screen.getByRole("textbox", { name: "Correlation Rule Version" }), { target: { value: "v1" } });
    fireEvent.click(screen.getByRole("button", { name: "执行参数关系分析" }));

    expect(await screen.findByText("Correlation 规则未批准")).toBeInTheDocument();
    expect(screen.getByText("错误码：ANALYSIS_RULE_NOT_APPROVED")).toBeInTheDocument();
    expect(screen.getByText("建议操作：complete Rule Owner approval before activation")).toBeInTheDocument();
    expect(screen.queryByRole("img", { name: /Scatter/ })).not.toBeInTheDocument();
  }, 20_000);
});
