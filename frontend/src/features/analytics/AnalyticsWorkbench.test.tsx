// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  getAnalyticsDetail,
  getAnalyticsDrilldown,
  getAnalyticsFeatureFlags,
  getAnalyticsOverview,
  getAnalyticsShellContext,
  type AnalyticsDetailResult,
  type AnalyticsDrilldownResult,
  type AnalyticsEvaluationFilter,
  type AnalyticsOverviewResult,
  type AnalyticsShellContextResult,
} from "../../api/analytics";
import { analyzeParameterRelationship } from "../../api/parameterRelationship";
import { listSavedAnalyses } from "../../api/savedAnalyses";
import { analyzeSpatial, type SpatialAnalysisResult } from "../../api/spatialAnalysis";
import { useAuth } from "../auth/AuthContext";
import { AnalyticsWorkbench, type DatasetSelection } from "./AnalyticsWorkbench";

vi.mock("../../api/analytics", () => ({
  getAnalyticsFeatureFlags: vi.fn(),
  getAnalyticsOverview: vi.fn(),
  getAnalyticsShellContext: vi.fn(),
  getAnalyticsDetail: vi.fn(),
  getAnalyticsDrilldown: vi.fn(),
}));
vi.mock("../../api/parameterRelationship", () => ({
  analyzeParameterRelationship: vi.fn(),
}));
vi.mock("../../api/spatialAnalysis", () => ({
  analyzeSpatial: vi.fn(),
}));
vi.mock("../../api/savedAnalyses", async (importOriginal) => {
  const original = await importOriginal<typeof import("../../api/savedAnalyses")>();
  return { ...original, listSavedAnalyses: vi.fn() };
});
vi.mock("../auth/AuthContext", () => ({ useAuth: vi.fn() }));
vi.mock("../../components/EChart", () => ({
  EChart: ({ ariaLabel, onEvents }: { ariaLabel?: string; onEvents?: { click?: (payload: unknown) => void } }) =>
    <div
      role="img"
      aria-label={ariaLabel}
      onClick={() => onEvents?.click?.(
        ariaLabel === "服务端良率趋势"
          ? { dataIndex: 0, seriesName: "Known Yield" }
          : ariaLabel === "服务端 Bin Pareto"
            ? { dataIndex: 0, seriesName: "Unit 数" }
            : { data: { drilldownKey: "UNIT:501" }, dataIndex: 999, unit_id: 999 },
      )}
    />,
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

const selections: DatasetSelection[] = [
  { datasetId: 20, versionNo: 1 },
  { datasetId: 21, versionNo: 2 },
];

const overview: AnalyticsOverviewResult = {
  contract_version: "ANALYTICS_CONTEXT_V1",
  dataset_context: {
    resolved_datasets: [
      { dataset_id: 20, version_no: 1, dataset_name: "DS20", test_stage: "FT", product_name: "PRODUCT-A" },
      { dataset_id: 21, version_no: 2, dataset_name: "DS21", test_stage: "FT", product_name: "PRODUCT-B" },
    ],
    test_stage: "FT",
    current_published_verified: true,
  },
  filter_summary: {
    normalized_filters: { lot_ids: [], wafer_ids: [], bin_codes: [], overall_results: [], source_ids: [], tester_ids: [], program_versions: [], test_conditions: [] },
    parameters: [],
    filter_hash: "a".repeat(64),
    context_hash: "b".repeat(64),
  },
  rule_context: { spec_versions: ["SPEC:7"], bin_mapping_versions: ["BIN:2"], evaluation_rule_versions: ["RULE:3"] },
  capabilities: [
    { code: "OVERVIEW", status: "AVAILABLE", reason_code: null, message: null },
    { code: "YIELD", status: "AVAILABLE", reason_code: null, message: null },
    { code: "BIN_PARETO", status: "AVAILABLE", reason_code: null, message: null },
    { code: "WAFER_MAP", status: "AVAILABLE", reason_code: null, message: null },
  ],
  counts: { input_units: 120, included_units: 100, excluded_units: 20, pass_count: 80, fail_count: 10, unknown_count: 10, abort_count: 0, known_yield_denominator: 90, yield_rate: 80 / 90, unknown_abort_denominator: 100, unknown_abort_rate: 0.1, missing_measurements: 3 },
  sampling_summary: { sampled: false, method: null, original_points: 1, returned_points: 1, preserved_out_of_spec_points: 0 },
  options: {
    lot_ids: ["LOT-A", "LOT-B", "LOT-C"],
    wafer_ids: ["W1", "W2"],
    bin_codes: ["1", "2"],
    source_ids: ["SRC-1", "SRC-2"],
    tester_ids: ["T-1", "T-2"],
    program_versions: ["P-1", "P-2"],
    test_conditions: ["C-1", "C-2"],
    parameters: ["RDON", "VTH"],
  },
  datasets: [
    { dataset_id: 20, version_no: 1, unit_count: 100, pass_count: 80, fail_count: 10, unknown_count: 10, abort_count: 0, known_yield_denominator: 90, yield_rate: 80 / 90 },
    { dataset_id: 21, version_no: 2, unit_count: 20, pass_count: 0, fail_count: 0, unknown_count: 20, abort_count: 0, known_yield_denominator: 0, yield_rate: null },
  ],
  yield_trend: [{ dataset_id: 20, version_no: 1, test_batch_id: 9, run_id: 30, sequence: 1, ordered_at: "2026-08-31T00:00:00Z", order_basis: "SOURCE_STARTED_AT_UTC_THEN_RUN_ID", source_id: "SRC-1", lot_id: "LOT-A", wafer_id: null, unit_count: 100, pass_count: 80, fail_count: 10, unknown_count: 10, abort_count: 0, yield_rate: 80 / 90, drilldown_key: "UNIT:501" }],
  bin_pareto: [{ dataset_id: 20, version_no: 1, mapping_set_id: 2, mapping_version: "V1", bin_type: "SOFT_BIN", bin_code: "1", bin_name: "PASS", failure_mode: null, is_pass: true, unit_count: 10, percent: 0.1, cumulative_percent: 0.1, drilldown_key: "UNIT:501" }],
  wafer_map: [{ x: 2, y: 3, bin_code: "1", result: "PASS", drilldown_key: "UNIT:501" }],
  risk_summary: [{ code: "UNKNOWN_OR_ABORT_RESULT", category: "DATA_QUALITY", severity: "WARNING", status: "ACTIVE", reason_code: "ANALYSIS_RESULT_POPULATION_INCOMPLETE", title: "存在 UNKNOWN / ABORT", message: "这些 Unit 不进入 Yield 分母", affected_count: 10, denominator_count: 100, rate: 0.1, drilldown_target: "DETAIL:RESULT", rule_versions: [] }],
  warnings: [],
  computed_at: "2026-08-31T00:00:00+00:00",
};

const shellContext: AnalyticsShellContextResult = {
  contract_version: overview.contract_version,
  dataset_context: overview.dataset_context,
  filter_summary: overview.filter_summary,
  rule_context: overview.rule_context,
  capabilities: overview.capabilities,
  counts: overview.counts,
  sampling_summary: overview.sampling_summary,
  options: overview.options,
  warnings: overview.warnings,
  computed_at: overview.computed_at,
};

const detail: AnalyticsDetailResult = {
  contract_version: "ANALYTICS_CONTEXT_V1",
  dataset_context: overview.dataset_context,
  filter_summary: overview.filter_summary,
  rule_context: overview.rule_context,
  capabilities: [{ code: "DETAIL", status: "AVAILABLE", reason_code: null, message: null }],
  counts: overview.counts,
  sampling_summary: overview.sampling_summary,
  evaluation_filter: null,
  measurement_filter: null,
  page: 2,
  page_size: 20,
  total: 60,
  view: "WIDE",
  sort_by: "UNIT_SEQUENCE",
  sort_direction: "ASC",
  items: [{
    drilldown_key: "UNIT:501",
    unit_id: 501,
    logical_unit_key: "FT-UNIT-501",
    lot_id: "LOT-A",
    wafer_id: null,
    x: null,
    y: null,
    soft_bin: "1",
    hard_bin: null,
    overall_result: "PASS",
    source_row_no: 51,
    processing_run_id: 901,
    source_file_id: 801,
    receipt_id: 701,
    original_file_name: "LOT-A.xlsx",
    sha256: "a".repeat(64),
    source_id: "SRC-1",
    tester_id: "T-1",
    program_version: "P-1",
    cleaner_release: "cleaner@1",
    source_files: [{ source_file_id: 801, receipt_id: 701, original_file_name: "LOT-A.xlsx", sha256: "a".repeat(64), ordinal_no: 1, file_role: "DETAIL", lineage_basis: "WRITER_VERIFIED" }],
    bin_evaluations: [{ unit_bin_evaluation_id: 6001, bin_type: "SOFT_BIN", raw_bin_code: "1", mapping_status: "MATCHED", bin_mapping_set_id: 501, mapping_version: "BIN-V1", bin_definition_id: 502, mapped_bin_name: "PASS", failure_mode_snapshot: "PASS_BIN", is_pass_snapshot: true, processing_run_id: 901, evaluated_at_utc: "2026-08-31T00:00:00+00:00" }],
    measurements: [{ measurement_id: 7001, parameter: "VTH", canonical_parameter_code: "VTH_CANONICAL", step_code: "S1", sequence_no: 1, value_numeric: 1.55, value_text: null, status: "MEASURED", unit: "V", program_lsl: 1, program_usl: 2, program_limit_source: "TEST_PROGRAM_CONFIGURATION_NOT_FORMAL_SPEC", formal_spec: { status: "RESOLVED", reason_code: null, evaluation_id: 7101, evaluation_result: "PASS", evaluation_scope_key: "DATASET:11", spec_binding_id: 401, spec_set_id: 402, spec_version: "SPEC-V1", spec_item_id: 403, lsl_applied: 1.1, usl_applied: 1.9, lower_operator_applied: ">=", upper_operator_applied: "<=" }, evaluations: [{ evaluation_id: 7101, evaluation_type: "SPEC", evaluation_scope_key: "DATASET:11", evaluation_result: "PASS", evaluation_reason: null, evaluation_run_id: 7201, rule_code: "SPEC_RULE", rule_version_id: 7202, rule_version: "RULE-7", spec_binding_id: 401, spec_set_id: 402, spec_version: "SPEC-V1", spec_item_id: 403, lsl_applied: 1.1, usl_applied: 1.9, lower_operator_applied: ">=", upper_operator_applied: "<=", processing_run_id: 901, evaluated_at_utc: "2026-08-31T00:00:00+00:00" }] }],
  }],
  warnings: [],
  computed_at: "2026-08-31T00:00:00+00:00",
};

const drilldown: AnalyticsDrilldownResult = {
  contract_version: "ANALYTICS_CONTEXT_V1",
  dataset_context: overview.dataset_context,
  filter_summary: overview.filter_summary,
  rule_context: overview.rule_context,
  unit: detail.items[0],
  warnings: [],
  computed_at: "2026-08-31T00:00:00+00:00",
};

const spatial: SpatialAnalysisResult = {
  contract_version: "ANALYTICS_SPATIAL_V1",
  dataset_context: { ...overview.dataset_context, test_stage: "CP", resolved_datasets: overview.dataset_context.resolved_datasets.map((item) => ({ ...item, test_stage: "CP" })) },
  filter_summary: overview.filter_summary,
  rule_context: overview.rule_context,
  capabilities: [{ code: "BIN_MAP", status: "AVAILABLE", reason_code: null, message: null }],
  mode: "BIN_MAP",
  parameter: null,
  color_domain: null,
  data_quality: { input_units: 1, returned_points: 1, wafer_count: 1, missing_coordinate_count: 0, duplicate_coordinate_count: 0, measured_count: 0, missing_measurement_count: 0, layer_point_count: 0 },
  points: [{ dataset_id: 20, version_no: 1, lot_id: "LOT-A", wafer_id: "W1", x: 2, y: 3, bin_code: "1", result: "PASS", value: null, unit: null, lsl: null, usl: null, spec_status: null, drilldown_key: "UNIT:501", observed_count: 1, fail_count: 0, fail_ratio: 0, wafer_count: 1 }],
  wafer_manifest: [{ key: "20:V1:LOT:LOT-A:WAFER:W1", dataset_id: 20, version_no: 1, lot_id: "LOT-A", wafer_id: "W1" }],
  wafer_layers: [],
  zones: [],
  warnings: [],
  computed_at: "2026-08-31T00:00:00+00:00",
};

function renderAnalytics({
  datasets = selections,
  initialSearch = "dataset=20%3A1&dataset=21%3A2",
  onOpenCatalog = vi.fn(),
  historySearch,
}: {
  datasets?: DatasetSelection[];
  initialSearch?: string;
  onOpenCatalog?: () => void;
  historySearch?: string;
} = {}) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  function StatefulAnalytics() {
    const [params, setParams] = useState(() => new URLSearchParams(initialSearch));
    return <>
      <output data-testid="analytics-search">{params.toString()}</output>
      {historySearch !== undefined && <button aria-label="模拟浏览器历史导航" onClick={() => setParams(new URLSearchParams(historySearch))}>history</button>}
      <AnalyticsWorkbench datasets={datasets} searchParams={params} onSearchParamsChange={setParams} onOpenCatalog={onOpenCatalog} />
    </>;
  }
  render(<QueryClientProvider client={queryClient}><StatefulAnalytics /></QueryClientProvider>);
  return { onOpenCatalog };
}

describe("AnalyticsWorkbench ANALYTICS_CONTEXT_V1 flow", () => {
  beforeEach(() => {
    const permissions = ["DATASET_READ"];
    vi.mocked(useAuth).mockReturnValue({
      user: { user_id: 7, login_name: "reader", display_name: "Reader", department_code: null, roles: ["READER"], permissions },
      loading: false, login: vi.fn(), logout: vi.fn(), can: (permission) => permissions.includes(permission),
    });
    vi.mocked(getAnalyticsOverview).mockResolvedValue(overview);
    vi.mocked(getAnalyticsShellContext).mockResolvedValue(shellContext);
    vi.mocked(getAnalyticsFeatureFlags).mockResolvedValue({
      contract_version: "ANALYTICS_FEATURE_FLAGS_V1",
      groups: ["OVERVIEW", "DETAIL", "PARAMETER", "SPATIAL", "QUALITY", "DELIVERY"].map((code) => ({ code: code as "OVERVIEW" | "DETAIL" | "PARAMETER" | "SPATIAL" | "QUALITY" | "DELIVERY", enabled: true, reason_code: null, message: null })),
    });
    vi.mocked(getAnalyticsDetail).mockResolvedValue(detail);
    vi.mocked(getAnalyticsDrilldown).mockResolvedValue(drilldown);
    vi.mocked(analyzeSpatial).mockResolvedValue(spatial);
    vi.mocked(listSavedAnalyses).mockResolvedValue({ items: [], total: 0, page: 1, page_size: 10 });
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("shows the catalog CTA and does not call analytics when no Dataset is selected", () => {
    const onOpenCatalog = vi.fn();
    renderAnalytics({ datasets: [], initialSearch: "", onOpenCatalog });

    expect(screen.getByText("尚未选择 Dataset")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /返回历史正式数据选择/ }));
    expect(onOpenCatalog).toHaveBeenCalledOnce();
    expect(getAnalyticsOverview).not.toHaveBeenCalled();
    expect(getAnalyticsShellContext).not.toHaveBeenCalled();
    expect(getAnalyticsFeatureFlags).not.toHaveBeenCalled();
    expect(getAnalyticsDetail).not.toHaveBeenCalled();
    expect(getAnalyticsDrilldown).not.toHaveBeenCalled();
  });

  it("renders backend kill-switch reasons and never calls a disabled overview", async () => {
    vi.mocked(getAnalyticsFeatureFlags).mockResolvedValue({
      contract_version: "ANALYTICS_FEATURE_FLAGS_V1",
      groups: [
        { code: "OVERVIEW", enabled: false, reason_code: "ANALYSIS_FEATURE_DISABLED", message: "Overview paused for rollback" },
        ...["DETAIL", "PARAMETER", "SPATIAL", "QUALITY", "DELIVERY"].map((code) => ({ code: code as "DETAIL" | "PARAMETER" | "SPATIAL" | "QUALITY" | "DELIVERY", enabled: true, reason_code: null, message: null })),
      ],
    });
    renderAnalytics();
    expect(await screen.findByText("OVERVIEW 分析已被发布开关关闭")).toBeInTheDocument();
    expect(screen.getByText("Overview paused for rollback")).toBeInTheDocument();
    expect(getAnalyticsOverview).not.toHaveBeenCalled();
    expect(getAnalyticsShellContext).toHaveBeenCalled();
  });

  it("keeps Detail operational through the shell Context when Overview is disabled", async () => {
    vi.mocked(getAnalyticsFeatureFlags).mockResolvedValue({
      contract_version: "ANALYTICS_FEATURE_FLAGS_V1",
      groups: [
        { code: "OVERVIEW", enabled: false, reason_code: "ANALYSIS_FEATURE_DISABLED", message: "Overview paused" },
        ...["DETAIL", "PARAMETER", "SPATIAL", "QUALITY", "DELIVERY"].map((code) => ({ code: code as "DETAIL" | "PARAMETER" | "SPATIAL" | "QUALITY" | "DELIVERY", enabled: true, reason_code: null, message: null })),
      ],
    });

    renderAnalytics({ initialSearch: "dataset=20%3A1&section=detail" });

    expect(await screen.findByText("FT-UNIT-501", {}, { timeout: 15_000 })).toBeInTheDocument();
    expect(getAnalyticsShellContext).toHaveBeenCalled();
    expect(getAnalyticsDetail).toHaveBeenCalled();
    expect(getAnalyticsOverview).not.toHaveBeenCalled();
  }, 20_000);

  it("sends all authoritative multi-value filters in one overview request", async () => {
    renderAnalytics({
      initialSearch: "dataset=20%3A1&dataset=21%3A2&detail_dataset=21%3A2&lot_id=LOT-B&lot_id=LOT-A&wafer_id=W2&wafer_id=W1&bin_code=2&bin_code=1&overall_result=FAIL&overall_result=PASS&source_id=SRC-2&source_id=SRC-1&tester_id=T-2&tester_id=T-1&program_version=P-2&program_version=P-1&test_condition=C-2&test_condition=C-1&parameter=VTH&parameter=RDON",
    });

    await waitFor(() => expect(getAnalyticsOverview).toHaveBeenCalledWith({
      datasets: [{ dataset_id: 20, version_no: 1 }, { dataset_id: 21, version_no: 2 }],
      filters: {
        lot_ids: ["LOT-A", "LOT-B"],
        wafer_ids: ["W1", "W2"],
        bin_codes: ["1", "2"],
        overall_results: ["PASS", "FAIL"],
        source_ids: ["SRC-1", "SRC-2"],
        tester_ids: ["T-1", "T-2"],
        program_versions: ["P-1", "P-2"],
        test_conditions: ["C-1", "C-2"],
      },
      parameters: ["RDON", "VTH"],
      focus_dataset_id: 21,
      max_points: 10_000,
    }));
    expect(await screen.findByText("Dataset Overview")).toBeInTheDocument();
    expect(screen.getByText("基础风险摘要")).toBeInTheDocument();
    expect(screen.getByText("存在 UNKNOWN / ABORT")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "DETAIL:RESULT" }));
    expect(await screen.findByText("FT-UNIT-501", {}, { timeout: 15_000 })).toBeInTheDocument();
  }, 20_000);

  it("uses five Chinese business groups and hides wafer spatial analysis for FT", async () => {
    renderAnalytics();
    expect(await screen.findByText("FT 数据分析")).toBeInTheDocument();
    expect(await screen.findByRole("tab", { name: "分析总览" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "参数图表" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "质量管控" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "报告与数据" })).toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "晶圆空间" })).not.toBeInTheDocument();
  });

  it("writes KPI aggregate predicates into the shared Context before opening Detail", async () => {
    renderAnalytics();
    expect(await screen.findByText("Dataset Overview")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "PASS 明细" }));

    await waitFor(() => {
      const params = new URLSearchParams(screen.getByTestId("analytics-search").textContent ?? "");
      expect(params.get("section")).toBe("detail");
      expect(params.getAll("overall_result")).toEqual(["PASS"]);
      expect(params.getAll("dataset")).toEqual(["20:1", "21:2"]);
    });
    await waitFor(() => expect(getAnalyticsDetail).toHaveBeenCalledWith(expect.objectContaining({
      filters: expect.objectContaining({ overall_results: ["PASS"] }),
    })));
  }, 20_000);

  it("renders an empty aggregate Yield as a dash instead of 0 percent", async () => {
    vi.mocked(getAnalyticsOverview).mockResolvedValue({
      ...overview,
      counts: {
        ...overview.counts,
        pass_count: 0,
        fail_count: 0,
        unknown_count: overview.counts.included_units,
        known_yield_denominator: 0,
        yield_rate: null,
      },
    });
    renderAnalytics();

    expect(await screen.findByText("Dataset Overview")).toBeInTheDocument();
    const yieldCard = screen.getByText("已知良率", { selector: ".ant-statistic-title" }).closest(".ant-card");
    expect(yieldCard).toHaveTextContent("—");
    expect(yieldCard).not.toHaveTextContent("0.000%");
  }, 20_000);

  it("opens persisted evaluation risk through the exact server Detail whitelist", async () => {
    const evaluationFilter: AnalyticsEvaluationFilter = {
      evaluation_type: "PAT",
      evaluation_results: ["FAIL", "NOT_EVALUATED"],
      rule_code: "CP_PAT",
      rule_version: "V2",
    };
    vi.mocked(getAnalyticsOverview).mockResolvedValue({
      ...overview,
      risk_summary: [{
        code: "EVALUATION_PAT:CP_PAT:V2",
        category: "EVALUATION",
        severity: "CRITICAL",
        status: "ACTIVE",
        reason_code: "ANALYSIS_EVALUATION_FAILED",
        title: "PAT / CP_PAT@V2 评价异常",
        message: "只对应一个 exact Rule Version",
        affected_count: 3,
        denominator_count: 10,
        rate: 0.3,
        drilldown_target: "DETAIL:EVALUATION",
        rule_versions: ["V2"],
        aggregate_drilldown_context: evaluationFilter,
      }],
    });
    vi.mocked(getAnalyticsDetail).mockResolvedValue({
      ...detail,
      evaluation_filter: evaluationFilter,
    });
    renderAnalytics();
    expect(await screen.findByText("PAT / CP_PAT@V2 评价异常", {}, { timeout: 15_000 })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "DETAIL:EVALUATION" }));

    await waitFor(() => expect(getAnalyticsDetail).toHaveBeenCalledWith(expect.objectContaining({
      evaluation_filter: evaluationFilter,
    })));
    await waitFor(() => {
      const params = new URLSearchParams(screen.getByTestId("analytics-search").textContent ?? "");
      expect(params.get("detail_eval_type")).toBe("PAT");
      expect(params.getAll("detail_eval_result")).toEqual(["FAIL", "NOT_EVALUATED"]);
      expect(params.get("detail_eval_rule")).toBe("CP_PAT");
      expect(params.get("detail_eval_version")).toBe("V2");
    });
    expect(await screen.findByText("当前为持久化评价风险限定总体", {}, { timeout: 15_000 })).toBeInTheDocument();
    expect(screen.getByText(/PAT · CP_PAT@V2 · Result FAIL \/ NOT_EVALUATED/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: "汇总、保存与导出" }));
    await waitFor(() => {
      const params = new URLSearchParams(screen.getByTestId("analytics-search").textContent ?? "");
      expect(params.get("section")).toBe("delivery");
      expect(params.get("detail_eval_type")).toBe("PAT");
      expect(params.getAll("detail_eval_result")).toEqual(["FAIL", "NOT_EVALUATED"]);
    });
  }, 25_000);

  it("narrows a Yield point to its Dataset, Lot and Source aggregate instead of opening one representative Unit", async () => {
    renderAnalytics();
    expect(await screen.findByText("Dataset Overview", {}, { timeout: 15_000 })).toBeInTheDocument();
    const chart = await screen.findByRole("img", { name: "服务端良率趋势" }, { timeout: 15_000 });

    fireEvent.click(chart);

    await waitFor(() => {
      const params = new URLSearchParams(screen.getByTestId("analytics-search").textContent ?? "");
      expect(params.getAll("dataset")).toEqual(["20:1"]);
      expect(params.get("detail_dataset")).toBe("20:1");
      expect(params.get("section")).toBe("detail");
      expect(params.getAll("lot_id")).toEqual(["LOT-A"]);
      expect(params.getAll("source_id")).toEqual(["SRC-1"]);
      expect(params.getAll("wafer_id")).toEqual([]);
    });
    expect(getAnalyticsDrilldown).not.toHaveBeenCalled();
  }, 20_000);

  it("loads Detail lazily with the same Context and URL pagination", async () => {
    renderAnalytics({ initialSearch: "dataset=20%3A1&dataset=21%3A2&detail_dataset=21%3A2&section=detail&lot_id=LOT-A&tester_id=T-1&program_version=P-1&test_condition=C-1&parameter=VTH&page=2&page_size=20" });

    expect(await screen.findByText("FT-UNIT-501", {}, { timeout: 15_000 })).toBeInTheDocument();
    expect(getAnalyticsDetail).toHaveBeenCalledWith({
      datasets: [{ dataset_id: 20, version_no: 1 }, { dataset_id: 21, version_no: 2 }],
      filters: {
        lot_ids: ["LOT-A"], wafer_ids: [], bin_codes: [], overall_results: [], source_ids: [], tester_ids: ["T-1"], program_versions: ["P-1"], test_conditions: ["C-1"],
      },
      parameters: ["VTH"],
      focus_dataset_id: 21,
      page: 2,
      page_size: 20,
      view: "WIDE",
      sort_by: "UNIT_SEQUENCE",
      sort_direction: "ASC",
    });
  }, 20_000);

  it("loads Parameter Relationship lazily without auto-running the expensive analysis", async () => {
    renderAnalytics({ initialSearch: "dataset=20%3A1&dataset=21%3A2&section=parameter&parameter=VTH&parameter=RDON" });

    expect(await screen.findByText("参数关系与趋势", {}, { timeout: 15_000 })).toBeInTheDocument();
    expect(screen.getByText("参数统计与分布")).toBeInTheDocument();
    expect(analyzeParameterRelationship).not.toHaveBeenCalled();
  }, 20_000);

  it("writes filter changes through the strict URL codec and preserves external keys", async () => {
    renderAnalytics({ initialSearch: "dataset=20%3A1&dataset=21%3A2&future_flag=keep&lot_id=LOT-A&page=3&page_size=20" });
    expect(await screen.findByText("Dataset Overview")).toBeInTheDocument();

    fireEvent.mouseDown(screen.getByRole("combobox", { name: "Lot 筛选" }));
    fireEvent.click(await screen.findByTitle("LOT-C"));

    await waitFor(() => {
      const params = new URLSearchParams(screen.getByTestId("analytics-search").textContent ?? "");
      expect(params.getAll("dataset")).toEqual(["20:1", "21:2"]);
      expect(params.getAll("lot_id")).toEqual(["LOT-A", "LOT-C"]);
      expect(params.get("page")).toBeNull();
      expect(params.get("page_size")).toBe("20");
      expect(params.get("future_flag")).toBe("keep");
    });
  }, 20_000);

  it("uses only the backend drilldown_key and shows formal source, Bin, Spec and full evaluation evidence", async () => {
    vi.mocked(getAnalyticsShellContext).mockResolvedValueOnce({ ...shellContext, dataset_context: spatial.dataset_context });
    renderAnalytics({ initialSearch: "dataset=20%3A1&dataset=21%3A2&section=spatial&lot_id=LOT-A&wafer_id=W1&tester_id=T-1" });
    fireEvent.click(await screen.findByRole("button", { name: "执行 Spatial 分析" }, { timeout: 15_000 }));
    await waitFor(() => expect(analyzeSpatial).toHaveBeenCalled());
    const waferMap = await screen.findByRole("img", { name: "BIN_MAP Spatial Map" }, { timeout: 15_000 });
    fireEvent.click(waferMap);

    await waitFor(() => expect(getAnalyticsDrilldown).toHaveBeenCalledWith({
      datasets: [{ dataset_id: 20, version_no: 1 }, { dataset_id: 21, version_no: 2 }],
      filters: { lot_ids: ["LOT-A"], wafer_ids: ["W1"], bin_codes: [], overall_results: [], source_ids: [], tester_ids: ["T-1"], program_versions: [], test_conditions: [] },
      parameters: [],
      drilldown_key: "UNIT:501",
    }));
    expect(vi.mocked(getAnalyticsDrilldown).mock.calls[0][0]).not.toHaveProperty("dataIndex");
    expect(await screen.findByText("cleaner@1")).toBeInTheDocument();
    expect(screen.getByText("SRC-1")).toBeInTheDocument();
    expect(screen.getAllByText("LOT-A.xlsx").length).toBeGreaterThan(0);
    expect(screen.getAllByText(/BIN-V1/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/SPEC-V1/).length).toBeGreaterThan(0);
    expect(screen.getByText("VTH_CANONICAL")).toBeInTheDocument();
    expect(screen.getByText("1.55 V")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Expand row" }));
    expect(await screen.findByText("SPEC_RULE / RULE-7")).toBeInTheDocument();
  }, 20_000);

  it("shows the Quality gate and permission-aware Saved/Export Delivery controls", async () => {
    renderAnalytics({ initialSearch: "dataset=20%3A1&section=quality" });
    expect(await screen.findByText("质量管控分析", {}, { timeout: 15_000 })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /执行 Quality 分析/ })).toBeDisabled();

    fireEvent.click(screen.getByRole("tab", { name: "报告与数据" }));
    fireEvent.click(screen.getByRole("tab", { name: "汇总、保存与导出" }));
    expect(await screen.findByText("Saved Analysis（版本化 Context）")).toBeInTheDocument();
    expect(screen.getByText("只读模式")).toBeInTheDocument();
    expect(screen.getByText("无导出权限")).toBeInTheDocument();
  }, 20_000);

  it("hydrates a complete Quality deep link and writes controlled rule changes back to the URL", async () => {
    renderAnalytics({
      initialSearch: "dataset=20%3A1&section=quality&view_contract=ANALYSIS_VIEW_STATE_V1&q_analysis=SPC_I_MR&q_parameter=VTH&q_group=RUN&q_rule=FT_SPC&q_rule_version=v1&q_spc_order=UNIT_SEQUENCE&q_spc_phase=PHASE_I_BASELINE&q_percent_axis=FIXED_0_100",
      historySearch: "dataset=20%3A1&section=quality&view_contract=ANALYSIS_VIEW_STATE_V1&q_analysis=SPC_I_MR&q_parameter=VTH&q_group=RUN&q_rule=FT_SPC&q_rule_version=v1&q_spc_order=UNIT_SEQUENCE&q_spc_phase=PHASE_I_BASELINE&q_percent_axis=FIXED_0_100",
    });
    expect(await screen.findByRole("button", { name: /执行 Quality 分析/ }, { timeout: 15_000 })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: /高级设置：查看或调试规则版本/ }));
    expect(screen.getByRole("textbox", { name: "Quality Rule Code" })).toHaveValue("FT_SPC");
    expect(screen.getByRole("textbox", { name: "Quality Rule Version" })).toHaveValue("v1");

    fireEvent.change(screen.getByRole("textbox", { name: "Quality Rule Version" }), { target: { value: "v2" } });
    await waitFor(() => {
      const params = new URLSearchParams(screen.getByTestId("analytics-search").textContent ?? "");
      expect(params.get("q_analysis")).toBe("SPC_I_MR");
      expect(params.get("q_parameter")).toBe("VTH");
      expect(params.get("q_group")).toBe("RUN");
      expect(params.get("q_rule")).toBe("FT_SPC");
      expect(params.get("q_rule_version")).toBe("v2");
      expect(params.get("q_spc_order")).toBe("UNIT_SEQUENCE");
      expect(params.get("q_spc_phase")).toBe("PHASE_I_BASELINE");
    });
    fireEvent.click(screen.getByRole("button", { name: "模拟浏览器历史导航" }));
    await waitFor(() => expect(screen.getByRole("textbox", { name: "Quality Rule Version" })).toHaveValue("v1"));
  }, 20_000);
});
