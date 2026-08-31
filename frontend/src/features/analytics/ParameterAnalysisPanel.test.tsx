// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  analyzeDatasetParameters,
  type DatasetParameterAnalysisResult,
} from "../../api/datasets";
import { ApiError } from "../../api/auth";
import { ParameterAnalysisPanel, type ParameterAnalysisPanelProps } from "./ParameterAnalysisPanel";
import { createDefaultAnalysisViewState } from "./context/analysisViewState";

vi.mock("../../api/datasets", () => ({
  analyzeDatasetParameters: vi.fn(),
}));
vi.mock("../../components/EChart", () => ({
  EChart: ({ option, ariaLabel, onEvents }: { option: unknown; ariaLabel?: string; onEvents?: { click?: (payload: unknown) => void } }) =>
    <div role="img" aria-label={ariaLabel} data-option={JSON.stringify(option)} onClick={() => {
      const series = (option as { series?: Array<{ data?: unknown[] }> }).series ?? [];
      const datum = series.find((item) => item.data?.some((value) => typeof value === "object" && value !== null && "drilldownKey" in value))?.data?.find((value) => typeof value === "object" && value !== null && "drilldownKey" in value);
      onEvents?.click?.({ data: datum });
    }} />,
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

const panelProps: ParameterAnalysisPanelProps = {
  datasets: [{ dataset_id: 20, version_no: 1 }, { dataset_id: 21, version_no: 2 }],
  parameterOptions: ["VTH", "RDON"],
  parameters: ["VTH"],
  onParametersChange: vi.fn(),
  lotIds: ["LOT-A"],
  waferIds: ["W1"],
  binCodes: ["1"],
  overallResults: ["PASS", "UNKNOWN"],
  onOverallResultsChange: vi.fn(),
  sourceIds: [],
  testerIds: [],
  programVersions: [],
  testConditions: [],
  onOpenDrilldown: vi.fn(),
  onOpenAggregateDrilldown: vi.fn(),
  displayState: createDefaultAnalysisViewState().display,
  onDisplayStateChange: vi.fn(),
};

const analysisResult: DatasetParameterAnalysisResult = {
  contract_version: "PARAMETER_ANALYSIS_V1",
  group_by: "DATASET",
  compatibility: "COMPATIBLE",
  dataset_context: {
    resolved_datasets: [{ dataset_id: 20, version_no: 1 }, { dataset_id: 21, version_no: 2 }],
    test_stage: "FT",
    current_published_verified: true,
  },
  filter_summary: {
    normalized_filters: {
      lot_ids: ["LOT-A"],
      wafer_ids: ["W1"],
      bin_codes: ["1"],
      overall_results: ["PASS", "UNKNOWN"],
      source_ids: [],
      tester_ids: [],
      program_versions: [],
      test_conditions: [],
    },
    filter_hash: "a".repeat(64),
  },
  rule_context: {
    spec_versions: ["SPEC_SET:7"],
    bin_mapping_versions: [],
    evaluation_rule_versions: [
      "RULE:BOX_RULE:v1:TUKEY_BOX_V1",
      "RULE:HISTOGRAM_RULE:v1:EQUAL_WIDTH_HISTOGRAM_V1",
      "RULE:CPK_RULE:v1:CPK_POOLED_WITHIN_RUN_V1",
    ],
    capability_rule_code: "CPK_RULE",
    capability_rule_approval_status: "APPROVED",
  },
  capabilities: [
    { code: "DESCRIPTIVE", status: "AVAILABLE", reason_code: null },
    { code: "BOX_PLOT", status: "AVAILABLE", reason_code: null },
    { code: "HISTOGRAM", status: "AVAILABLE", reason_code: null },
    { code: "CAPABILITY", status: "AVAILABLE", reason_code: null },
  ],
  counts: {
    input_units: 10,
    included_units: 4,
    excluded_units: 6,
    missing_measurements: 0,
  },
  sampling_summary: {
    sampled: false,
    method: null,
    original_points: 0,
    returned_points: 0,
    preserved_out_of_spec_points: 0,
  },
  warnings: [],
  computed_at: "2026-08-30T00:00:00+00:00",
  items: [{
    dataset_id: 20,
    version_no: 1,
    test_stage: "FT",
    group_key: "DATASET:20:1",
    filter_summary: {
      lot_ids: ["LOT-A"],
      wafer_ids: ["W1"],
      bin_codes: ["1"],
      overall_results: ["PASS", "UNKNOWN"],
      source_ids: [],
      tester_ids: [],
      program_versions: [],
      test_conditions: [],
      matched_unit_count: 4,
      candidate_measurement_count: 5,
    },
    parameters: [{
      identity: {
        name: "VTH",
        canonical_parameter_code: "VTH_CANONICAL",
        unit: "V",
        program_lsl: 100,
        program_usl: 500,
        test_condition: "VGE=0V",
        spec_set_ids: [7],
        limit_source: "RELEASED_SPEC",
        formal_lsl: 1,
        formal_usl: 5,
        formal_lower_operator: ">=",
        formal_upper_operator: "<=",
        formal_spec_status: "RESOLVED",
        formal_spec_reason_codes: [],
        formal_spec_versions: ["SPEC:7:v1"],
      },
      status_counts: [{ status: "MEASURED", count: 4 }, { status: "INVALID", count: 1 }],
      descriptive: {
        row_count: 5,
        numeric_count: 4,
        excluded_count: 1,
        minimum: 0,
        maximum: 10,
        average: 3,
        sample_stddev: 1.25,
      },
      box_plot: {
        minimum: 0,
        q1: 2,
        median: 3,
        q3: 4,
        maximum: 10,
        lower_whisker: 1,
        upper_whisker: 5,
        outlier_count: 2,
        method: "TUKEY_BOX_V1",
        outlier_evidence: [
          { measurement_id: 501, value: 0, drilldown_key: "UNIT:701", spec_status: "OUT_OF_SPEC" },
          { measurement_id: 502, value: 10, drilldown_key: "UNIT:702", spec_status: "OUT_OF_SPEC" },
        ],
        outlier_sampling: { sampled: false, method: "EXTREME_BOTH_TAILS_BY_VALUE_THEN_MEASUREMENT_ID_V1", original_points: 2, returned_points: 2 },
      },
        histogram: {
          bin_count: 2,
          requested_bin_count: 20,
          range_min: 0,
          range_max: 10,
          method: "EQUAL_WIDTH_HISTOGRAM_V1",
          bins: [
          { index: 0, lower_bound: 0, upper_bound: 5, count: 3, lower_inclusive: true, upper_inclusive: false, spec_region: "CROSSES_SPEC", aggregate_drilldown_context: { dataset_id: 20, version_no: 1, parameter: "VTH", lower_bound: 0, upper_bound: 5, lower_inclusive: true, upper_inclusive: false } },
          { index: 1, lower_bound: 5, upper_bound: 10, count: 1, lower_inclusive: true, upper_inclusive: true, spec_region: "OUT_OF_SPEC", aggregate_drilldown_context: { dataset_id: 20, version_no: 1, parameter: "VTH", lower_bound: 5, upper_bound: 10, lower_inclusive: true, upper_inclusive: true } },
        ],
      },
      capability: {
        status: "ELIGIBLE",
        ppk_status: "ELIGIBLE",
        cpk_status: "ELIGIBLE",
        reason_codes: [],
        spec_mode: "TWO_SIDED",
        lsl: 1,
        usl: 5,
        sample_count: 4,
        subgroup_count: 0,
        overall_sigma: 1.25,
        within_sigma: 1.2,
        ppl: null,
        ppu: null,
        ppk: 0.53,
        cpl: null,
        cpu: null,
        cpk: 0.56,
        rule_code: "CPK_RULE:v1",
        drilldown_context: { dataset_id: 20, version_no: 1, parameter: "VTH", lower_bound: null, upper_bound: null, lower_inclusive: true, upper_inclusive: true },
      },
      normal_fit: null,
    }],
  }],
};

const normalFitResult: DatasetParameterAnalysisResult = {
  ...analysisResult,
  rule_context: {
    ...analysisResult.rule_context,
    evaluation_rule_versions: [...analysisResult.rule_context.evaluation_rule_versions, "RULE:NORMAL_FIT_RULE:v1:NORMAL_FIT_MLE_V1"],
  },
  capabilities: [...analysisResult.capabilities, { code: "NORMAL_FIT", status: "AVAILABLE", reason_code: null }],
  items: [{
    ...analysisResult.items[0],
    parameters: [{
      ...analysisResult.items[0].parameters[0],
      normal_fit: {
        status: "AVAILABLE",
        reason_code: null,
        sample_count: 4,
        mean: 2.5,
        standard_deviation: 1.118033988749895,
        method: "NORMAL_FIT_MLE_V1",
        points: [
          { x: 0, probability_density: 0.029 },
          { x: 2.5, probability_density: 0.357 },
          { x: 5, probability_density: 0.029 },
        ],
        observed_evidence: [
          { measurement_id: 501, value: 0, drilldown_key: "UNIT:701", spec_status: "OUT_OF_SPEC" },
          { measurement_id: 503, value: 2.5, drilldown_key: "UNIT:703", spec_status: "IN_SPEC" },
        ],
        evidence_sampling: { sampled: true, method: "QUANTILES_PLUS_BOUNDED_OOS_BY_MEASUREMENT_ID_V1", original_points: 4, returned_points: 2 },
      },
    }],
  }],
};

const noSpecDistributionResult: DatasetParameterAnalysisResult = {
  ...normalFitResult,
  items: [{
    ...normalFitResult.items[0],
    parameters: [{
      ...normalFitResult.items[0].parameters[0],
      identity: {
        ...normalFitResult.items[0].parameters[0].identity,
        formal_lsl: null,
        formal_usl: null,
        formal_spec_status: "NO_SPEC",
        formal_spec_reason_codes: ["FORMAL_RELEASED_SPEC_NOT_FOUND"],
        formal_spec_versions: [],
      },
      capability: null,
      histogram: {
        ...analysisResult.items[0].parameters[0].histogram!,
        bins: analysisResult.items[0].parameters[0].histogram!.bins.map((bin) => ({ ...bin, spec_region: "NO_SPEC" as const })),
      },
    }],
  }],
};

const zeroNumericResult: DatasetParameterAnalysisResult = {
  ...analysisResult,
  items: [{
    ...analysisResult.items[0],
    parameters: [{
      ...analysisResult.items[0].parameters[0],
      descriptive: {
        row_count: 3,
        numeric_count: 0,
        excluded_count: 3,
        minimum: null,
        maximum: null,
        average: null,
        sample_stddev: null,
      },
      box_plot: null,
      histogram: null,
      capability: null,
    }],
  }],
};

function renderPanel(props: ParameterAnalysisPanelProps = panelProps) {
  const queryClient = new QueryClient({
    defaultOptions: { mutations: { retry: false }, queries: { retry: false } },
  });
  const view = render(
    <QueryClientProvider client={queryClient}><ParameterAnalysisPanel {...props} /></QueryClientProvider>,
  );
  return {
    ...view,
    rerenderPanel: (nextProps: ParameterAnalysisPanelProps) => view.rerender(
      <QueryClientProvider client={queryClient}><ParameterAnalysisPanel {...nextProps} /></QueryClientProvider>,
    ),
  };
}

async function selectValue(label: string, value: string) {
  fireEvent.mouseDown(screen.getByRole("combobox", { name: label }));
  fireEvent.click(await screen.findByTitle(value));
}

describe("ParameterAnalysisPanel", () => {
  beforeEach(() => {
    vi.mocked(analyzeDatasetParameters).mockResolvedValue(analysisResult);
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("runs only after an explicit click and preserves the backend chart/statistics contract", async () => {
    renderPanel();

    expect(analyzeDatasetParameters).not.toHaveBeenCalled();
    expect(screen.getByText(/BoxPlot、Histogram、Normal Fit 和显式 Capability Rule/)).toBeInTheDocument();
    await selectValue("参数分析类型", "箱线图");
    await selectValue("参数分析类型", "直方图");
    await selectValue("参数分析类型", "Capability");
    fireEvent.change(screen.getByRole("textbox", { name: "Box Rule Code" }), { target: { value: "BOX_RULE" } });
    fireEvent.change(screen.getByRole("textbox", { name: "Box Rule Version" }), { target: { value: "v1" } });
    fireEvent.change(screen.getByRole("textbox", { name: "Histogram Rule Code" }), { target: { value: "HISTOGRAM_RULE" } });
    fireEvent.change(screen.getByRole("textbox", { name: "Histogram Rule Version" }), { target: { value: "v1" } });
    fireEvent.change(screen.getByRole("textbox", { name: "Capability Rule Code" }), { target: { value: "CPK_RULE" } });
    fireEvent.change(screen.getByRole("textbox", { name: "Capability Rule Version" }), { target: { value: "v1" } });
    expect(analyzeDatasetParameters).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "执行参数分析" }));

    await waitFor(() => expect(analyzeDatasetParameters).toHaveBeenCalledTimes(1));
    expect(vi.mocked(analyzeDatasetParameters).mock.calls[0][0]).toEqual({
      datasets: [{ dataset_id: 20, version_no: 1 }, { dataset_id: 21, version_no: 2 }],
      group_by: "DATASET",
      filters: {
        lot_ids: ["LOT-A"],
        wafer_ids: ["W1"],
        bin_codes: ["1"],
        overall_results: ["PASS", "UNKNOWN"],
        source_ids: [],
        tester_ids: [],
        program_versions: [],
        test_conditions: [],
      },
      parameters: ["VTH"],
      analyses: ["DESCRIPTIVE", "BOX_PLOT", "HISTOGRAM", "CAPABILITY"],
      box_plot: { rule_code: "BOX_RULE", version_code: "v1" },
      histogram: { rule_code: "HISTOGRAM_RULE", version_code: "v1" },
      normal_fit: {},
      capability: { method: "CPK_POOLED_WITHIN_RUN_V1", rule_code: "CPK_RULE", version_code: "v1" },
    });

    expect(await screen.findByText("Dataset 筛选与命中摘要")).toBeInTheDocument();
    expect(screen.getByText("可复现上下文")).toBeInTheDocument();
    expect(screen.getByText("Current+PUBLISHED 已验证")).toBeInTheDocument();
    expect(screen.getByText(/Filter Hash aaaaaaaaaaaa/)).toBeInTheDocument();
    expect(screen.getByText("输入 / 纳入 / 排除 Unit：10 / 4 / 6")).toBeInTheDocument();
    expect(screen.getAllByText("候选测量值").length).toBeGreaterThan(0);
    expect(screen.getByText("参数身份与规格来源")).toBeInTheDocument();
    expect(screen.getByText("VGE=0V")).toBeInTheDocument();
    expect(screen.getByText("VTH_CANONICAL")).toBeInTheDocument();
    expect(screen.getByText("RELEASED_SPEC")).toBeInTheDocument();

    const boxChart = screen.getByRole("img", { name: "VTH 按 Dataset 的箱线图" });
    const boxOption = JSON.parse(boxChart.getAttribute("data-option") ?? "{}");
    expect(boxOption.toolbox.feature.saveAsImage.name).toBe("VTH-box-plot");
    expect(boxOption.series[0].data).toEqual([[1, 2, 3, 4, 5]]);
    expect(boxOption.series[1].data).toEqual([
      expect.objectContaining({ value: [0, 0], drilldownKey: "UNIT:701", measurementId: 501 }),
      expect.objectContaining({ value: [0, 10], drilldownKey: "UNIT:702", measurementId: 502 }),
    ]);
    fireEvent.click(boxChart);
    expect(panelProps.onOpenDrilldown).toHaveBeenCalledWith("UNIT:701");
    const boxRow = screen.getByText("TUKEY_BOX_V1").closest("tr");
    expect(boxRow).toHaveTextContent("0");
    expect(boxRow).toHaveTextContent("10");
    expect(boxRow).toHaveTextContent("2");

    const histogramChart = screen.getByRole("img", { name: "VTH 在 Dataset #20 / V1 的后端分箱直方图" });
    const histogramOption = JSON.parse(histogramChart.getAttribute("data-option") ?? "{}");
    expect(histogramOption.toolbox.feature.saveAsImage.name).toBe("VTH-histogram");
    expect(histogramOption.xAxis.data).toEqual(["[0, 5)", "[5, 10]"]);
    expect(histogramOption.series[0].data.map((item: { value: number }) => item.value)).toEqual([3, 1]);
    expect(histogramOption.series[0].data[1]).toEqual(expect.objectContaining({ aggregateContext: expect.objectContaining({ parameter: "VTH", lower_bound: 5 }), specRegion: "OUT_OF_SPEC" }));
    expect(histogramOption.series[0].markLine.data.map((item: { name: string }) => item.name)).toEqual(["LSL", "USL"]);
    expect(histogramOption.series[0].markLine.data.map((item: { xAxis: number }) => item.xAxis)).toEqual([0, 1]);
    expect(histogramOption.series[0].markLine.data.map((item: { label: { formatter: string } }) => item.label.formatter)).toEqual(["LSL 1", "USL 5"]);
    expect(screen.getByText(/方法 EQUAL_WIDTH_HISTOGRAM_V1/)).toBeInTheDocument();
    expect(screen.getByText("CPK_RULE:v1")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "打开 VTH Detail" }));
    expect(panelProps.onOpenAggregateDrilldown).toHaveBeenCalledWith(expect.objectContaining({ dataset: { dataset_id: 20, version_no: 1 }, parameters: ["VTH"] }));
  }, 20_000);

  it("preserves Tester, Program and Test Condition from the unified Context", async () => {
    renderPanel({
      ...panelProps,
      testerIds: ["T-1"],
      programVersions: ["P-1"],
      testConditions: ["VGE=0V"],
    });
    fireEvent.click(screen.getByRole("button", { name: "执行参数分析" }));

    await waitFor(() => expect(analyzeDatasetParameters).toHaveBeenCalledTimes(1));
    expect(vi.mocked(analyzeDatasetParameters).mock.calls[0][0].filters).toMatchObject({
      tester_ids: ["T-1"],
      program_versions: ["P-1"],
      test_conditions: ["VGE=0V"],
    });
  });

  it("requests approved NORMAL_FIT and plots only the server-returned MLE density points", async () => {
    vi.mocked(analyzeDatasetParameters).mockResolvedValue(normalFitResult);
    renderPanel();
    expect(screen.getByText(/Normal Fit 和显式 Capability Rule/)).toBeInTheDocument();
    await selectValue("参数分析类型", "Normal Fit");
    fireEvent.change(screen.getByRole("textbox", { name: "Normal Fit Rule Code" }), { target: { value: "NORMAL_FIT_RULE" } });
    fireEvent.change(screen.getByRole("textbox", { name: "Normal Fit Rule Version" }), { target: { value: "v1" } });
    fireEvent.click(screen.getByRole("button", { name: "执行参数分析" }));

    await waitFor(() => expect(vi.mocked(analyzeDatasetParameters).mock.calls[0][0].analyses).toEqual(["DESCRIPTIVE", "NORMAL_FIT"]));
    const chart = await screen.findByRole("img", { name: "VTH 在 Dataset #20 / V1 的服务端 Normal Fit 曲线" });
    const option = JSON.parse(chart.getAttribute("data-option") ?? "{}");
    expect(option.toolbox.feature.saveAsImage.name).toBe("VTH-normal-fit");
    expect(option.series[0].data).toEqual([[0, 0.029], [2.5, 0.357], [5, 0.029]]);
    expect(option.series[1].data[0]).toEqual(expect.objectContaining({ value: [0, 0], drilldownKey: "UNIT:701" }));
    expect(option.series[0].markLine.data.map((item: { name: string }) => item.name)).toEqual(["LSL", "USL"]);
    expect(option.series[0].markLine.data.map((item: { xAxis: number }) => item.xAxis)).toEqual([1, 5]);
    fireEvent.click(chart);
    expect(panelProps.onOpenDrilldown).toHaveBeenCalledWith("UNIT:701");
    expect(screen.getByText(/服务端返回 3 个曲线点/)).toBeInTheDocument();
    expect(screen.getAllByText("NORMAL_FIT_MLE_V1").length).toBeGreaterThan(0);
  }, 20_000);

  it("applies and clears the shared Y-axis display range without changing the request", async () => {
    const onDisplayStateChange = vi.fn();
    renderPanel({
      ...panelProps,
      displayState: { ...createDefaultAnalysisViewState().display, yAxisMin: -2, yAxisMax: 12 },
      onDisplayStateChange,
    });
    await selectValue("参数分析类型", "箱线图");
    await selectValue("参数分析类型", "直方图");
    fireEvent.change(screen.getByRole("textbox", { name: "Box Rule Code" }), { target: { value: "BOX_RULE" } });
    fireEvent.change(screen.getByRole("textbox", { name: "Box Rule Version" }), { target: { value: "v1" } });
    fireEvent.change(screen.getByRole("textbox", { name: "Histogram Rule Code" }), { target: { value: "HISTOGRAM_RULE" } });
    fireEvent.change(screen.getByRole("textbox", { name: "Histogram Rule Version" }), { target: { value: "v1" } });
    fireEvent.click(screen.getByRole("button", { name: "执行参数分析" }));

    const boxOption = JSON.parse((await screen.findByRole("img", { name: "VTH 按 Dataset 的箱线图" })).getAttribute("data-option") ?? "{}");
    const histogramOption = JSON.parse(screen.getByRole("img", { name: /后端分箱直方图/ }).getAttribute("data-option") ?? "{}");
    expect(boxOption.yAxis).toEqual(expect.objectContaining({ min: -2, max: 12 }));
    expect(histogramOption.yAxis).toEqual(expect.objectContaining({ min: -2, max: 12 }));
    fireEvent.change(screen.getByRole("spinbutton", { name: "参数分析 Y 轴最小值" }), { target: { value: "" } });
    expect(onDisplayStateChange).toHaveBeenCalledWith({ yAxisMin: null });
    expect(analyzeDatasetParameters).toHaveBeenCalledTimes(1);
  }, 20_000);

  it("shows a fail-closed missing-Spec hint for Histogram and Normal Fit", async () => {
    vi.mocked(analyzeDatasetParameters).mockResolvedValue(noSpecDistributionResult);
    renderPanel();
    await selectValue("参数分析类型", "直方图");
    await selectValue("参数分析类型", "Normal Fit");
    fireEvent.change(screen.getByRole("textbox", { name: "Histogram Rule Code" }), { target: { value: "HISTOGRAM_RULE" } });
    fireEvent.change(screen.getByRole("textbox", { name: "Histogram Rule Version" }), { target: { value: "v1" } });
    fireEvent.change(screen.getByRole("textbox", { name: "Normal Fit Rule Code" }), { target: { value: "NORMAL_FIT_RULE" } });
    fireEvent.change(screen.getByRole("textbox", { name: "Normal Fit Rule Version" }), { target: { value: "v1" } });
    fireEvent.click(screen.getByRole("button", { name: "执行参数分析" }));

    expect((await screen.findAllByText("当前参数没有可用 Released Formal Spec")).length).toBe(2);
  }, 20_000);

  it("preserves a source filter for a single dataset", async () => {
    renderPanel({
      ...panelProps,
      datasets: [{ dataset_id: 20, version_no: 1 }],
      sourceIds: ["SOURCE-1"],
    });
    fireEvent.click(screen.getByRole("button", { name: "执行参数分析" }));

    await waitFor(() => expect(analyzeDatasetParameters).toHaveBeenCalledTimes(1));
    expect(vi.mocked(analyzeDatasetParameters).mock.calls[0][0].filters.source_ids).toEqual(["SOURCE-1"]);
  });

  it("marks a successful result stale when inherited conditions change without auto-running", async () => {
    const view = renderPanel();
    fireEvent.click(screen.getByRole("button", { name: "执行参数分析" }));
    expect(await screen.findByText("合同 PARAMETER_ANALYSIS_V1")).toBeInTheDocument();
    expect(analyzeDatasetParameters).toHaveBeenCalledTimes(1);

    view.rerenderPanel({ ...panelProps, lotIds: ["LOT-B"] });

    expect(await screen.findByText("当前结果已过期")).toBeInTheDocument();
    expect(screen.getByText("合同 PARAMETER_ANALYSIS_V1")).toBeInTheDocument();
    expect(analyzeDatasetParameters).toHaveBeenCalledTimes(1);
  });

  it("shows a numeric-count-zero result as not applicable instead of an empty response", async () => {
    vi.mocked(analyzeDatasetParameters).mockResolvedValue(zeroNumericResult);
    renderPanel();
    fireEvent.click(screen.getByRole("button", { name: "执行参数分析" }));

    expect(await screen.findByText("当前范围没有可分析数值")).toBeInTheDocument();
    expect(screen.getAllByText("非数值/未纳入统计").length).toBeGreaterThan(0);
    expect(screen.queryByText("当前筛选没有可分析的数据")).not.toBeInTheDocument();
  });

  it("shows structured ApiError fields and retries only from the explicit retry action", async () => {
    vi.mocked(analyzeDatasetParameters).mockRejectedValueOnce(new ApiError(503, {
      code: "ANALYSIS_TEMPORARILY_UNAVAILABLE",
      message: "分析服务暂不可用",
      retryable: true,
      recommended_action: "稍后重试",
    }, "请求失败"));
    renderPanel();
    fireEvent.click(screen.getByRole("button", { name: "执行参数分析" }));

    expect(await screen.findByText("错误代码：ANALYSIS_TEMPORARILY_UNAVAILABLE")).toBeInTheDocument();
    expect(screen.getByText("HTTP：503")).toBeInTheDocument();
    expect(screen.getByText("建议操作：稍后重试")).toBeInTheDocument();
    expect(screen.getByText("可重试：是")).toBeInTheDocument();
    expect(analyzeDatasetParameters).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole("button", { name: "重试参数分析" }));
    await waitFor(() => expect(analyzeDatasetParameters).toHaveBeenCalledTimes(2));
    expect(await screen.findByText("合同 PARAMETER_ANALYSIS_V1")).toBeInTheDocument();
  });

  it("shows an explicit business-approval gate for an unapproved statistical rule", async () => {
    vi.mocked(analyzeDatasetParameters).mockRejectedValueOnce(new ApiError(409, {
      code: "ANALYSIS_RULE_NOT_APPROVED",
      message: "the requested statistical rule has no approved server-side activation",
      retryable: false,
      recommended_action: "complete Rule Owner approval before activation",
    }, "请求失败"));
    renderPanel();
    await selectValue("参数分析类型", "Normal Fit");
    fireEvent.change(screen.getByRole("textbox", { name: "Normal Fit Rule Code" }), { target: { value: "NORMAL_FIT_RULE" } });
    fireEvent.change(screen.getByRole("textbox", { name: "Normal Fit Rule Version" }), { target: { value: "v1" } });
    fireEvent.click(screen.getByRole("button", { name: "执行参数分析" }));

    expect(await screen.findByText("统计口径待业务批准")).toBeInTheDocument();
    expect(screen.getByText(/服务端已失败关闭本次统计/)).toBeInTheDocument();
    expect(screen.getByText("错误代码：ANALYSIS_RULE_NOT_APPROVED")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "重试参数分析" })).not.toBeInTheDocument();
  });
});
