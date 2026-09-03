// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AnalyticsContextRequest, AnalyticsOverviewResult } from "../../api/analytics";
import { ApiError } from "../../api/auth";
import { evaluateQuality, type QualityEvaluationResult } from "../../api/qualityEvaluation";
import { QualityEvaluationPanel } from "./QualityEvaluationPanel";

vi.mock("../../api/qualityEvaluation", () => ({ evaluateQuality: vi.fn() }));
vi.mock("../../components/EChart", () => ({
  EChart: ({ ariaLabel, onEvents }: { ariaLabel: string; onEvents?: { click?: (payload: unknown) => void } }) => <div role="img" aria-label={ariaLabel} onClick={() => onEvents?.click?.({ data: { drilldownKey: "UNIT:501" }, dataIndex: 999 })} />,
}));

Object.defineProperty(window, "matchMedia", { writable: true, value: () => ({ matches: false, addListener: () => undefined, removeListener: () => undefined, addEventListener: () => undefined, removeEventListener: () => undefined }) });
vi.stubGlobal("ResizeObserver", class { observe() { return undefined; } unobserve() { return undefined; } disconnect() { return undefined; } });

const context: AnalyticsContextRequest = {
  datasets: [{ dataset_id: 20, version_no: 1 }],
  filters: { lot_ids: ["LOT-A"], wafer_ids: ["W1"], bin_codes: [], overall_results: ["FAIL"], source_ids: ["S1"], tester_ids: ["T1"], program_versions: ["P1"], test_conditions: ["C1"] },
  parameters: ["VTH", "RDON"],
};
const overview: AnalyticsOverviewResult = {
  contract_version: "ANALYTICS_CONTEXT_V1",
  dataset_context: { resolved_datasets: [{ dataset_id: 20, version_no: 1, dataset_name: "CP20", test_stage: "CP", product_name: "P1" }], test_stage: "CP", current_published_verified: true },
  filter_summary: { normalized_filters: context.filters, parameters: context.parameters, filter_hash: "a".repeat(64), context_hash: "b".repeat(64) },
  rule_context: { spec_versions: ["SPEC:7"], bin_mapping_versions: ["BIN:2"], evaluation_rule_versions: [] },
  capabilities: [],
  counts: { input_units: 10, included_units: 10, excluded_units: 0, pass_count: 8, fail_count: 2, unknown_count: 0, abort_count: 0, known_yield_denominator: 10, yield_rate: 0.8, unknown_abort_denominator: 10, unknown_abort_rate: 0, missing_measurements: 0 },
  sampling_summary: { sampled: false, method: null, original_points: 0, returned_points: 0, preserved_out_of_spec_points: 0 },
  options: { lot_ids: ["LOT-A"], wafer_ids: ["W1"], bin_codes: ["1"], source_ids: ["S1"], tester_ids: ["T1"], program_versions: ["P1"], test_conditions: ["C1"], parameters: ["VTH", "RDON"] },
  datasets: [], yield_trend: [], bin_pareto: [], wafer_map: [], risk_summary: [], warnings: [], computed_at: "2026-08-31T00:00:00Z",
};
const spcResult: QualityEvaluationResult = {
  contract_version: "ANALYTICS_QUALITY_EVALUATION_V1", analysis: "SPC_I_MR", dataset_context: overview.dataset_context, filter_summary: overview.filter_summary,
  calculation_context_hash: "c".repeat(64), rule_context: { ...overview.rule_context, evaluation_rule_versions: ["RULE:CP_SPC:V1"] },
  rule: { rule_code: "CP_SPC", version_code: "V1", algorithm_code: "SPC_I_MR_V1", approval_status: "APPROVED", activation_status: "ENABLED", parameters_sha256: "d".repeat(64) },
  parameter_identity: { name: "VTH", canonical_parameter_code: "VTH_CANONICAL", step_code: "S1", sequence_no: 1, unit: "V", test_condition: "C1", program_lsl: 1, program_usl: 2 },
  capabilities: [{ code: "SPC_I_MR", status: "AVAILABLE", reason_code: null, message: null }],
  counts: { input_units: 10, included_units: 10, excluded_units: 0, input_measurements: 10, included_measurements: 10, missing_measurements: 0, excluded_measurements: 0 },
  sampling_summary: { sampled: false, method: null, original_points: 2, returned_points: 2, preserved_out_of_spec_points: 0 },
  pat: [], margin: [], bin_cooccurrence: [], sbl: [], syl: [], pass_fail_distribution: [],
  spc: [{ dataset_id: 20, version_no: 1, group_key: "D:20:V:1|LOT:LOT-A|WAFER:W1", valid_n: 2, missing_n: 0, center_line: 1.5, lower_control_limit: 1, upper_control_limit: 2, mr_bar: 0.5, mr_upper_control_limit: 1.6, boundary_reset: true, baseline_context_hash: "b".repeat(64), status: "ASSESSABLE", sampling_summary: { sampled: false, method: null, original_points: 2, returned_points: 2, preserved_out_of_spec_points: 1 }, points: [{ sequence: 1, value: 1.4, moving_range: null, drilldown_key: "UNIT:501", rule_hits: [] }, { sequence: 2, value: 1.6, moving_range: 0.2, drilldown_key: "UNIT:502", rule_hits: ["I_BEYOND_UCL"] }] }],
  warnings: ["SPC Phase-I only"], computed_at: "2026-08-31T00:00:00Z",
};

function renderPanel(onOpenDrilldown = vi.fn()) {
  const queryClient = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
  render(<QueryClientProvider client={queryClient}><QualityEvaluationPanel context={context} focusDatasetId={20} overview={overview} overviewLoading={false} overviewError={null} onOpenDrilldown={onOpenDrilldown} /></QueryClientProvider>);
  return onOpenDrilldown;
}

async function select(label: string, option: string) {
  fireEvent.mouseDown(screen.getByRole("combobox", { name: label }));
  fireEvent.click(await screen.findByTitle(option));
}

describe("QualityEvaluationPanel", () => {
  const openRuleSettings = () => fireEvent.click(screen.getByRole("button", { name: /高级设置：查看或调试规则版本/ }));
  beforeEach(() => { vi.mocked(evaluateQuality).mockResolvedValue(spcResult); });
  afterEach(() => { cleanup(); vi.clearAllMocks(); });

  it("has no default rule or method and renders the server zero-approval gate", async () => {
    vi.mocked(evaluateQuality).mockRejectedValueOnce(new ApiError(409, { code: "ANALYSIS_RULE_NOT_APPROVED", message: "requested rule is not approved", retryable: false, recommended_action: "approve and activate exact version" }, "failed"));
    renderPanel();
    expect(screen.getByText("当前数据还没有可自动使用的分析规则")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /执行 Quality 分析/ })).toBeDisabled();
    expect(evaluateQuality).not.toHaveBeenCalled();

    await select("Quality 方法", "PAT Robust IQR");
    await select("Quality Group By", "LOT");
    await select("Quality 参数", "VTH");
    openRuleSettings();
    fireEvent.change(screen.getByRole("textbox", { name: "Quality Rule Code" }), { target: { value: "CP_PAT" } });
    fireEvent.change(screen.getByRole("textbox", { name: "Quality Rule Version" }), { target: { value: "V1" } });
    fireEvent.click(screen.getByRole("button", { name: /执行 Quality 分析/ }));

    await waitFor(() => expect(vi.mocked(evaluateQuality).mock.calls[0]?.[0]).toEqual({
      datasets: context.datasets, filters: context.filters, parameters: ["VTH"], analysis: "PAT_ROBUST_IQR", rule: { rule_code: "CP_PAT", version_code: "V1" }, group_by: "LOT", spc_order: null, spc_phase: null, bin_type: null,
    }));
    expect(await screen.findByText("Quality Rule 未批准或未激活")).toBeInTheDocument();
    expect(screen.getByText("错误码：ANALYSIS_RULE_NOT_APPROVED")).toBeInTheDocument();
  }, 20_000);

  it("requires explicit SPC order/phase, renders server limits, and drills only by backend key", async () => {
    const onOpen = renderPanel();
    await select("Quality 方法", "SPC I-MR");
    await select("Quality Group By", "WAFER");
    await select("Quality 参数", "VTH");
    openRuleSettings();
    fireEvent.change(screen.getByRole("textbox", { name: "Quality Rule Code" }), { target: { value: "CP_SPC" } });
    fireEvent.change(screen.getByRole("textbox", { name: "Quality Rule Version" }), { target: { value: "V1" } });
    expect(screen.getByRole("button", { name: /执行 Quality 分析/ })).toBeDisabled();
    await select("SPC Order", "UNIT_SEQUENCE");
    await select("SPC Phase", "PHASE_I_BASELINE");
    fireEvent.click(screen.getByRole("button", { name: /执行 Quality 分析/ }));

    expect(await screen.findByText("SPC_I_MR · 服务端权威结果")).toBeInTheDocument();
    expect(screen.getByText("SPC_I_MR_V1")).toBeInTheDocument();
    const chart = screen.getByRole("img", { name: "SPC I-MR Chart" });
    fireEvent.click(chart);
    expect(onOpen).toHaveBeenCalledWith("UNIT:501");
    expect(screen.getByText("Rule Hit Evidence")).toBeInTheDocument();
    expect(screen.getByText("I_BEYOND_UCL")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "成员 1" }));
    fireEvent.click(screen.getByRole("button", { name: "打开 Unit Drawer" }));
    expect(onOpen).toHaveBeenCalledWith("UNIT:502");
    expect(vi.mocked(evaluateQuality).mock.calls[0][0]).toMatchObject({ spc_order: "UNIT_SEQUENCE", spc_phase: "PHASE_I_BASELINE", group_by: "WAFER", parameters: ["VTH"] });
  }, 40_000);

  it("offers both Bin methods and sends an explicit physical Bin with no parameter", async () => {
    vi.mocked(evaluateQuality).mockResolvedValueOnce({ ...spcResult, analysis: "BIN_COOCCURRENCE", parameter_identity: null, spc: [], bin_cooccurrence: [] });
    renderPanel();
    await select("Quality 方法", "Bin Co-occurrence");
    await select("Quality Group By", "DATASET");
    await select("Quality Bin Type", "CP_BIN");
    openRuleSettings();
    fireEvent.change(screen.getByRole("textbox", { name: "Quality Rule Code" }), { target: { value: "CP_BIN_COOCCURRENCE" } });
    fireEvent.change(screen.getByRole("textbox", { name: "Quality Rule Version" }), { target: { value: "V1" } });
    fireEvent.click(screen.getByRole("button", { name: /执行 Quality 分析/ }));
    await waitFor(() => expect(vi.mocked(evaluateQuality).mock.calls[0]?.[0]).toEqual(expect.objectContaining({ analysis: "BIN_COOCCURRENCE", group_by: "DATASET", bin_type: "CP_BIN", parameters: [] })));
  }, 20_000);

  it("runs governed SYL without a fake measurement parameter and renders exclusions", async () => {
    vi.mocked(evaluateQuality).mockResolvedValueOnce({
      ...spcResult,
      analysis: "SYL_GROUPED_LIMIT",
      parameter_identity: null,
      spc: [],
      rule: { ...spcResult.rule, rule_code: "FT_SYL", algorithm_code: "SYL_GROUPED_LIMIT_V1" },
      syl: [{ dataset_id: 20, version_no: 1, subgroup_count: 2, mean_yield: 0.95, sample_stddev: 0.01, raw_lower_limit: 0.92, lower_limit: 0.92, rounding_policy: "NONE", rounding_step: null, status: "ASSESSABLE", below_limit_groups: ["LOT-B"], groups: [{ group_key: "LOT-A", pass_unit_count: 95, fail_unit_count: 5, unknown_excluded_count: 1, abort_excluded_count: 2, other_result_excluded_count: 0, yield_rate: 0.95, drilldown_keys: ["UNIT:501"] }] }],
    });
    renderPanel();
    await select("Quality 方法", "SYL Grouped Limit");
    await select("Quality Group By", "LOT");
    openRuleSettings();
    fireEvent.change(screen.getByRole("textbox", { name: "Quality Rule Code" }), { target: { value: "FT_SYL" } });
    fireEvent.change(screen.getByRole("textbox", { name: "Quality Rule Version" }), { target: { value: "V1" } });
    fireEvent.click(screen.getByRole("button", { name: /执行 Quality 分析/ }));
    expect(await screen.findByText("SYL_GROUPED_LIMIT_V1")).toBeInTheDocument();
    expect(screen.getAllByText("SYL Grouped Limit").length).toBeGreaterThan(1);
    expect(screen.getByRole("img", { name: "SYL Quality Trend Chart" })).toBeInTheDocument();
    expect(vi.mocked(evaluateQuality).mock.calls[0][0]).toMatchObject({ analysis: "SYL_GROUPED_LIMIT", group_by: "LOT", parameters: [], bin_type: null });
  }, 20_000);

  it("renders server-binned PASS/FAIL comparison and preserves evidence drilldown", async () => {
    vi.mocked(evaluateQuality).mockResolvedValueOnce({
      ...spcResult,
      analysis: "PASS_FAIL_DISTRIBUTION",
      spc: [],
      rule: { ...spcResult.rule, rule_code: "FT_PF_DIST", algorithm_code: "PASS_FAIL_DISTRIBUTION_V1" },
      pass_fail_distribution: [{ dataset_id: 20, version_no: 1, group_key: "LOT-A", pass_count: 8, fail_count: 2, unknown_excluded_count: 1, abort_excluded_count: 1, other_result_excluded_count: 0, missing_measurements: 0, pass_mean: 1.2, fail_mean: 1.8, minimum: 1, maximum: 2, status: "ASSESSABLE", bins: [{ bin_index: 0, lower: 1, upper: 1.5, pass_count: 8, fail_count: 0, pass_drilldown_keys: ["UNIT:501"], fail_drilldown_keys: [] }, { bin_index: 1, lower: 1.5, upper: 2, pass_count: 0, fail_count: 2, pass_drilldown_keys: [], fail_drilldown_keys: ["UNIT:502"] }] }],
    });
    const onOpen = renderPanel();
    await select("Quality 方法", "Pass / Fail Distribution");
    await select("Quality Group By", "LOT");
    await select("Quality 参数", "VTH");
    openRuleSettings();
    fireEvent.change(screen.getByRole("textbox", { name: "Quality Rule Code" }), { target: { value: "FT_PF_DIST" } });
    fireEvent.change(screen.getByRole("textbox", { name: "Quality Rule Version" }), { target: { value: "V1" } });
    fireEvent.click(screen.getByRole("button", { name: /执行 Quality 分析/ }));
    expect(await screen.findByRole("img", { name: "Pass Fail Distribution Chart" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("img", { name: "Pass Fail Distribution Chart" }));
    expect(onOpen).toHaveBeenCalledWith("UNIT:501");
    expect(vi.mocked(evaluateQuality).mock.calls[0][0]).toMatchObject({ analysis: "PASS_FAIL_DISTRIBUTION", group_by: "LOT", parameters: ["VTH"] });
  }, 30_000);

  it("renders Margin distribution, Bin co-occurrence visuals, and SBL trend/Pareto with evidence", async () => {
    const marginResult: QualityEvaluationResult = {
      ...spcResult,
      analysis: "MARGIN_OOS",
      spc: [],
      rule: { ...spcResult.rule, rule_code: "CP_MARGIN", algorithm_code: "SPEC_MARGIN_V1" },
      margin: [{ dataset_id: 20, version_no: 1, group_key: "LOT-A", spec_set_id: 7, spec_version: "V1", spec_mode: "BOTH", lsl: 1, usl: 2, valid_n: 2, missing_n: 0, out_of_spec_count: 1, out_of_spec_rate: 0.5, minimum_margin: -0.1, sampling_summary: { sampled: false, method: null, original_points: 1, returned_points: 1, preserved_out_of_spec_points: 1 }, points: [{ dataset_id: 20, version_no: 1, unit_id: 501, measurement_id: 9001, value: 2.1, lower_margin: 1.1, upper_margin: -0.1, nearest_margin: -0.1, out_of_spec: true, drilldown_key: "UNIT:501" }] }],
    };
    const cooccurrenceResult: QualityEvaluationResult = {
      ...spcResult,
      analysis: "BIN_COOCCURRENCE",
      parameter_identity: null,
      spc: [],
      rule: { ...spcResult.rule, rule_code: "CP_BIN_PAIR", algorithm_code: "BIN_COOCCURRENCE_UNIT_V1" },
      bin_cooccurrence: [{ dataset_id: 20, version_no: 1, group_key: "LOT-A", left_bin: "5", right_bin: "7", physical_unit_count: 2, denominator_units: 10, rate: 0.2, drilldown_keys: ["UNIT:501"], pareto_rank: 1, pair_count_share: 1, cumulative_pair_count_share: 1 }],
    };
    const sblResult: QualityEvaluationResult = {
      ...spcResult,
      analysis: "SBL_GROUPED_LIMIT",
      parameter_identity: null,
      spc: [],
      rule: { ...spcResult.rule, rule_code: "FT_SBL", algorithm_code: "SBL_GROUPED_LIMIT_V1" },
      sbl: [{ dataset_id: 20, version_no: 1, bin_code: "5", subgroup_count: 2, mean_rate: 0.1, sample_stddev: 0.01, upper_limit: 0.13, status: "ASSESSABLE", exceeding_groups: ["LOT-A"], groups: [{ group_key: "LOT-A", physical_unit_count: 10, fail_unit_count: 2, rate: 0.2, drilldown_keys: ["UNIT:501"] }], pareto_rank: 1, fail_unit_count: 2, fail_unit_share: 1, cumulative_fail_unit_share: 1 }],
    };
    vi.mocked(evaluateQuality).mockResolvedValueOnce(marginResult).mockResolvedValueOnce(cooccurrenceResult).mockResolvedValueOnce(sblResult);
    const onOpen = renderPanel();

    await select("Quality 方法", "Spec Margin / OOS");
    await select("Quality Group By", "LOT");
    await select("Quality 参数", "VTH");
    openRuleSettings();
    fireEvent.change(screen.getByRole("textbox", { name: "Quality Rule Code" }), { target: { value: "CP_MARGIN" } });
    fireEvent.change(screen.getByRole("textbox", { name: "Quality Rule Version" }), { target: { value: "V1" } });
    fireEvent.click(screen.getByRole("button", { name: /执行 Quality 分析/ }));
    expect(await screen.findByRole("img", { name: "Spec Margin OOS Distribution Chart" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("img", { name: "Spec Margin OOS Distribution Chart" }));
    expect(onOpen).toHaveBeenCalledWith("UNIT:501");

    await select("Quality 方法", "Bin Co-occurrence");
    await select("Quality Bin Type", "CP_BIN");
    fireEvent.change(screen.getByRole("textbox", { name: "Quality Rule Code" }), { target: { value: "CP_BIN_PAIR" } });
    fireEvent.click(screen.getByRole("button", { name: /执行 Quality 分析/ }));
    expect(await screen.findByRole("img", { name: "Bin Co-occurrence Heatmap" })).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "Bin Co-occurrence Pareto" })).toBeInTheDocument();

    await select("Quality 方法", "SBL Grouped Limit");
    await select("Quality Bin Type", "SOFT_BIN");
    fireEvent.change(screen.getByRole("textbox", { name: "Quality Rule Code" }), { target: { value: "FT_SBL" } });
    fireEvent.click(screen.getByRole("button", { name: /执行 Quality 分析/ }));
    expect(await screen.findByRole("img", { name: "SBL Quality Trend Chart" })).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "SBL Fail Bin Pareto" })).toBeInTheDocument();
    expect(screen.getByText(/Fail Bin Pareto 的 count\/rank\/share\/cumulative 均来自服务端/)).toBeInTheDocument();
  }, 60_000);
});
