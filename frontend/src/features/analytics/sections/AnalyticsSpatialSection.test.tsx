// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AnalyticsContextRequest, AnalyticsOverviewResult } from "../../../api/analytics";
import { ApiError } from "../../../api/auth";
import { analyzeSpatial, type SpatialAnalysisResult } from "../../../api/spatialAnalysis";
import { createDefaultAnalysisViewState } from "../context/analysisViewState";
import { AnalyticsSpatialSection } from "./AnalyticsSpatialSection";

vi.mock("../../../api/spatialAnalysis", () => ({ analyzeSpatial: vi.fn() }));
vi.mock("../../../components/EChart", () => ({
  EChart: ({ option, ariaLabel, onEvents }: { option: unknown; ariaLabel?: string; onEvents?: { click?: (payload: unknown) => void } }) =>
    <div role="img" aria-label={ariaLabel} data-option={JSON.stringify(option)} onClick={() => {
      const firstDatum = (option as { series?: Array<{ data?: unknown[] }> }).series?.[0]?.data?.[0];
      onEvents?.click?.({ data: firstDatum, dataIndex: 501 });
    }} />,
}));

Object.defineProperty(window, "matchMedia", { writable: true, value: () => ({ matches: false, addListener: () => undefined, removeListener: () => undefined, addEventListener: () => undefined, removeEventListener: () => undefined }) });
vi.stubGlobal("ResizeObserver", class { observe() { return undefined; } unobserve() { return undefined; } disconnect() { return undefined; } });

const context: AnalyticsContextRequest = {
  datasets: [{ dataset_id: 20, version_no: 1 }],
  filters: { lot_ids: ["LOT-A"], wafer_ids: ["W1"], bin_codes: ["1"], overall_results: ["PASS", "FAIL"], source_ids: ["SRC-1"], tester_ids: ["T-1"], program_versions: ["P-1"], test_conditions: ["C-1"] },
  parameters: ["VTH"],
};
const overview: AnalyticsOverviewResult = {
  contract_version: "ANALYTICS_CONTEXT_V1",
  dataset_context: { resolved_datasets: [{ dataset_id: 20, version_no: 1, dataset_name: "CP20", test_stage: "CP", product_name: "P-A" }], test_stage: "CP", current_published_verified: true },
  filter_summary: { normalized_filters: context.filters, parameters: ["VTH"], filter_hash: "a".repeat(64), context_hash: "b".repeat(64) },
  rule_context: { spec_versions: ["SPEC:7"], bin_mapping_versions: ["BIN:2"], evaluation_rule_versions: [] },
  capabilities: [],
  counts: { input_units: 2, included_units: 2, excluded_units: 0, pass_count: 1, fail_count: 1, unknown_count: 0, abort_count: 0, known_yield_denominator: 2, missing_measurements: 1 },
  sampling_summary: { sampled: false, method: null, original_points: 0, returned_points: 0, preserved_out_of_spec_points: 0 },
  options: { lot_ids: ["LOT-A"], wafer_ids: ["W1", "W2"], bin_codes: ["1"], source_ids: ["SRC-1"], tester_ids: ["T-1"], program_versions: ["P-1"], test_conditions: ["C-1"], parameters: ["VTH", "RDON"] },
  datasets: [], yield_trend: [], bin_pareto: [], wafer_map: [], risk_summary: [], warnings: [], computed_at: "2026-08-31T00:00:00Z",
};
const heatmap: SpatialAnalysisResult = {
  contract_version: "ANALYTICS_SPATIAL_V1",
  dataset_context: overview.dataset_context,
  filter_summary: overview.filter_summary,
  rule_context: overview.rule_context,
  capabilities: [{ code: "PARAMETER_HEATMAP", status: "AVAILABLE", reason_code: null, message: null }],
  mode: "PARAMETER_HEATMAP",
  parameter: "VTH",
  color_domain: { minimum: 0, maximum: 10, p02: 1, p98: 9 },
  data_quality: { input_units: 2, returned_points: 2, wafer_count: 1, missing_coordinate_count: 0, duplicate_coordinate_count: 0, measured_count: 1, missing_measurement_count: 1, layer_point_count: 0 },
  points: [
    { dataset_id: 20, version_no: 1, lot_id: "LOT-A", wafer_id: "W1", x: 1, y: 1, bin_code: "1", result: "PASS", value: 3.2, unit: "V", lsl: 1, usl: 5, spec_status: "IN_SPEC", drilldown_key: "UNIT:501", observed_count: 1, fail_count: 0, fail_ratio: 0, wafer_count: 1, raw_bin_code: "1", bin_mapping_set_id: 2, bin_mapping_version: "BIN-V2", bin_name: "PASS", failure_mode: null, bin_is_pass: true, spec_set_id: 7, spec_version: "SPEC-V7" },
    { dataset_id: 20, version_no: 1, lot_id: "LOT-A", wafer_id: "W1", x: 2, y: 1, bin_code: "2", result: "FAIL", value: null, unit: "V", lsl: 1, usl: 5, spec_status: "MISSING", drilldown_key: "UNIT:502", observed_count: 1, fail_count: 1, fail_ratio: 1, wafer_count: 1, raw_bin_code: "2", bin_mapping_set_id: 2, bin_mapping_version: "BIN-V2", bin_name: "FAIL", failure_mode: "LEAKAGE", bin_is_pass: false, spec_set_id: 7, spec_version: "SPEC-V7" },
  ],
  wafer_manifest: [{ key: "20:V1:LOT:LOT-A:WAFER:W1", dataset_id: 20, version_no: 1, lot_id: "LOT-A", wafer_id: "W1" }],
  wafer_layers: [],
  zones: [], warnings: ["MISSING_MEASUREMENTS_EXCLUDED_FROM_COLOR_DOMAIN"], computed_at: "2026-08-31T00:00:00Z",
};
const composite: SpatialAnalysisResult = {
  ...heatmap,
  capabilities: [{ code: "COMPOSITE_FAILURE", status: "AVAILABLE", reason_code: null, message: null }],
  mode: "COMPOSITE_FAILURE",
  parameter: null,
  color_domain: null,
  data_quality: { ...heatmap.data_quality, wafer_count: 2, measured_count: 0, missing_measurement_count: 0, layer_point_count: 2 },
  points: [{ dataset_id: null, version_no: null, lot_id: null, wafer_id: null, x: 1, y: 1, bin_code: null, result: null, value: null, unit: null, lsl: null, usl: null, spec_status: null, drilldown_key: null, observed_count: 2, fail_count: 1, fail_ratio: 0.5, wafer_count: 2, member_drilldown_keys: ["UNIT:501", "UNIT:502"] }],
  wafer_manifest: [
    { key: "20:V1:LOT:LOT-A:WAFER:W1", dataset_id: 20, version_no: 1, lot_id: "LOT-A", wafer_id: "W1" },
    { key: "20:V1:LOT:LOT-A:WAFER:W2", dataset_id: 20, version_no: 1, lot_id: "LOT-A", wafer_id: "W2" },
  ],
  wafer_layers: [
    heatmap.points[0],
    { ...heatmap.points[1], wafer_id: "W2" },
  ],
  warnings: [],
};
const zoneResult: SpatialAnalysisResult = {
  ...heatmap,
  mode: "ZONE_COMPARISON",
  capabilities: [{ code: "ZONE_COMPARISON", status: "AVAILABLE", reason_code: null, message: null }],
  points: [
    { ...heatmap.points[0], zone: "CENTER", quadrant: "FAB_A" },
    { ...heatmap.points[1], value: 7.2, zone: "EDGE", quadrant: "FAB_B" },
  ],
  zone_geometry: { center_x: 0, center_y: 0, radius: 10, center_ratio: 0.33, mid_ratio: 0.66, quadrant_axis_rotation_degrees: 15, quadrant_y_direction: "UP", quadrant_labels_ccw: ["FAB_A", "FAB_B", "FAB_C", "FAB_D"] },
  zones: [
    { zone: "CENTER", unit_count: 1, pass_count: 1, fail_count: 0, unknown_count: 0, yield_rate: 1, measured_count: 1, missing_measurement_count: 0, mean: 3.2, minimum: 3.2, maximum: 3.2, drilldown_key: "UNIT:501", member_drilldown_keys: ["UNIT:501"] },
    { zone: "EDGE", unit_count: 1, pass_count: 0, fail_count: 1, unknown_count: 0, yield_rate: 0, measured_count: 1, missing_measurement_count: 0, mean: 7.2, minimum: 7.2, maximum: 7.2, drilldown_key: "UNIT:502", member_drilldown_keys: ["UNIT:502"] },
  ],
  quadrants: [
    { quadrant: "FAB_A", unit_count: 1, pass_count: 1, fail_count: 0, unknown_count: 0, yield_rate: 1, measured_count: 1, missing_measurement_count: 0, mean: 3.2, minimum: 3.2, maximum: 3.2, member_drilldown_keys: ["UNIT:501"] },
    { quadrant: "FAB_B", unit_count: 1, pass_count: 0, fail_count: 1, unknown_count: 0, yield_rate: 0, measured_count: 1, missing_measurement_count: 0, mean: 7.2, minimum: 7.2, maximum: 7.2, member_drilldown_keys: ["UNIT:502"] },
    { quadrant: "FAB_C", unit_count: 0, pass_count: 0, fail_count: 0, unknown_count: 0, yield_rate: null, measured_count: 0, missing_measurement_count: 0, mean: null, minimum: null, maximum: null, member_drilldown_keys: [] },
    { quadrant: "FAB_D", unit_count: 0, pass_count: 0, fail_count: 0, unknown_count: 0, yield_rate: null, measured_count: 0, missing_measurement_count: 0, mean: null, minimum: null, maximum: null, member_drilldown_keys: [] },
  ],
};

function renderSpatial(customContext = context, onOpenDrilldown = vi.fn(), customOverview = overview) {
  const queryClient = new QueryClient({ defaultOptions: { mutations: { retry: false }, queries: { retry: false } } });
  render(<QueryClientProvider client={queryClient}><AnalyticsSpatialSection context={customContext} focusDatasetId={20} overview={customOverview} overviewLoading={false} overviewError={null} onOpenDrilldown={onOpenDrilldown} displayState={createDefaultAnalysisViewState().display} onDisplayStateChange={vi.fn()} /></QueryClientProvider>);
  return { onOpenDrilldown };
}

async function selectValue(label: string, value: string) {
  fireEvent.mouseDown(screen.getByRole("combobox", { name: label }));
  fireEvent.click(await screen.findByTitle(value));
}

describe("AnalyticsSpatialSection", () => {
  beforeEach(() => { vi.mocked(analyzeSpatial).mockResolvedValue(heatmap); });
  afterEach(() => { cleanup(); vi.clearAllMocks(); });

  it("requests a one-parameter heatmap with the complete Context and keeps color/display controls separate from facts", async () => {
    const { onOpenDrilldown } = renderSpatial();
    await selectValue("Spatial Mode", "Parameter Heatmap");
    fireEvent.click(screen.getByRole("button", { name: "执行 Spatial 分析" }));

    await waitFor(() => expect(analyzeSpatial).toHaveBeenCalled());
    expect(vi.mocked(analyzeSpatial).mock.calls.at(-1)?.[0]).toEqual({
      datasets: context.datasets,
      filters: context.filters,
      parameters: ["VTH"],
      mode: "PARAMETER_HEATMAP",
      focus_dataset_id: 20,
      max_points: 20_000,
      rule_code: null,
      rule_version: null,
    });
    expect(await screen.findByText("MISSING_MEASUREMENTS_EXCLUDED_FROM_COLOR_DOMAIN")).toBeInTheDocument();
    expect(screen.getByText(/服务端 Color Domain：Min 0 \/ P02 1 \/ P98 9 \/ Max 10/)).toBeInTheDocument();
    const map = screen.getByRole("img", { name: "PARAMETER_HEATMAP Spatial Map" });
    let option = JSON.parse(map.getAttribute("data-option") ?? "{}");
    expect(option.visualMap.min).toBe(1);
    expect(option.visualMap.max).toBe(9);
    expect(option.series[0].data).toHaveLength(2);

    fireEvent.click(screen.getByRole("radio", { name: "Min–Max" }));
    option = JSON.parse(screen.getByRole("img", { name: "PARAMETER_HEATMAP Spatial Map" }).getAttribute("data-option") ?? "{}");
    expect(option.visualMap.min).toBe(0);
    expect(option.visualMap.max).toBe(10);
    fireEvent.click(screen.getByRole("checkbox", { name: "Missing Measurement" }));
    option = JSON.parse(screen.getByRole("img", { name: "PARAMETER_HEATMAP Spatial Map" }).getAttribute("data-option") ?? "{}");
    expect(option.series[0].data).toHaveLength(1);

    fireEvent.click(screen.getByRole("img", { name: "PARAMETER_HEATMAP Spatial Map" }));
    expect(onOpenDrilldown).toHaveBeenCalledWith("UNIT:501");
  }, 20_000);

  it("fails closed until a single wafer is explicitly selected", () => {
    renderSpatial({ ...context, filters: { ...context.filters, lot_ids: [], wafer_ids: [] } });
    expect(screen.getByText("该 Mode 要求明确的单 Wafer")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "执行 Spatial 分析" })).toBeDisabled();
    expect(analyzeSpatial).not.toHaveBeenCalled();
  });

  it("fails closed when BIN_MAP or overlay has no versioned Bin Mapping", async () => {
    const withoutBinMapping = { ...overview, rule_context: { ...overview.rule_context, bin_mapping_versions: [] } };
    renderSpatial(context, vi.fn(), withoutBinMapping);
    expect(screen.getByText("Bin Mapping 尚未就绪")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "执行 Spatial 分析" })).toBeDisabled();

    await selectValue("Spatial Mode", "Parameter Fail Overlay");
    expect(screen.getByText("Bin Mapping 尚未就绪")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "执行 Spatial 分析" })).toBeDisabled();
    expect(analyzeSpatial).not.toHaveBeenCalled();
  }, 20_000);

  it("shows every reconciled composite member before opening the real Unit drawer", async () => {
    vi.mocked(analyzeSpatial).mockResolvedValue(composite);
    const { onOpenDrilldown } = renderSpatial({ ...context, filters: { ...context.filters, wafer_ids: ["W1", "W2"] } });
    await selectValue("Spatial Mode", "Composite Failure");
    fireEvent.click(screen.getByRole("button", { name: "执行 Spatial 分析" }));

    await screen.findByRole("img", { name: "COMPOSITE_FAILURE Spatial Map" });
    expect(vi.mocked(analyzeSpatial).mock.calls.at(-1)?.[0]).toEqual(expect.objectContaining({ mode: "COMPOSITE_FAILURE", parameters: [] }));
    fireEvent.click(screen.getByRole("img", { name: "COMPOSITE_FAILURE Spatial Map" }));
    expect(onOpenDrilldown).not.toHaveBeenCalled();
    expect(screen.getByText("共 2 个服务端稳定 Unit key；逐个打开现有 Drawer，不使用代表 Unit。")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "打开成员 UNIT:502" }));
    expect(onOpenDrilldown).toHaveBeenCalledWith("UNIT:502");
  }, 20_000);

  it("fails the composite display closed when member keys do not reconcile", async () => {
    vi.mocked(analyzeSpatial).mockResolvedValue({
      ...composite,
      points: [{ ...composite.points[0], member_drilldown_keys: ["UNIT:501"] }],
    });
    renderSpatial({ ...context, filters: { ...context.filters, wafer_ids: ["W1", "W2"] } });
    await selectValue("Spatial Mode", "Composite Failure");
    fireEvent.click(screen.getByRole("button", { name: "执行 Spatial 分析" }));

    expect(await screen.findByText("Composite 成员下钻合同不完整")).toBeInTheDocument();
    expect(screen.queryByRole("img", { name: "COMPOSITE_FAILURE Spatial Map" })).not.toBeInTheDocument();
  }, 20_000);

  it("renders the server parameter fail overlay as a separate display layer", async () => {
    vi.mocked(analyzeSpatial).mockResolvedValue({ ...heatmap, mode: "PARAMETER_FAIL_OVERLAY", capabilities: [{ code: "PARAMETER_FAIL_OVERLAY", status: "AVAILABLE", reason_code: null, message: null }] });
    renderSpatial();
    await selectValue("Spatial Mode", "Parameter Fail Overlay");
    fireEvent.click(screen.getByRole("button", { name: "执行 Spatial 分析" }));

    const map = await screen.findByRole("img", { name: "PARAMETER_FAIL_OVERLAY Spatial Map" });
    expect(vi.mocked(analyzeSpatial).mock.calls.at(-1)?.[0]).toEqual(expect.objectContaining({ mode: "PARAMETER_FAIL_OVERLAY", parameters: ["VTH"] }));
    const option = JSON.parse(map.getAttribute("data-option") ?? "{}");
    expect(option.series).toHaveLength(3);
    expect(option.series[1].name).toBe("FAIL Overlay");
    expect(option.series[1].data).toHaveLength(1);
  }, 20_000);

  it("sends an explicit Zone rule and surfaces the server Rule Gate", async () => {
    vi.mocked(analyzeSpatial).mockRejectedValueOnce(new ApiError(409, { code: "ANALYSIS_RULE_NOT_APPROVED", message: "zone comparison requires an approved and active rule", retryable: false, recommended_action: "activate the approved rule" }, "request failed"));
    renderSpatial();
    await selectValue("Spatial Mode", "Zone Comparison");
    fireEvent.change(screen.getByRole("textbox", { name: "Spatial Rule Code" }), { target: { value: "cp_wafer_zone" } });
    fireEvent.change(screen.getByRole("textbox", { name: "Spatial Rule Version" }), { target: { value: "1.0.0" } });
    fireEvent.click(screen.getByRole("button", { name: "执行 Spatial 分析" }));

    expect(await screen.findByText("Spatial Rule 未批准或合同无效")).toBeInTheDocument();
    expect(screen.getByText("错误码：ANALYSIS_RULE_NOT_APPROVED")).toBeInTheDocument();
    expect(vi.mocked(analyzeSpatial).mock.calls.at(-1)?.[0]).toEqual(expect.objectContaining({ rule_code: "CP_WAFER_ZONE", rule_version: "1.0.0" }));
  }, 20_000);

  it("renders approved Zone identity and geometry, with point and area drilldown", async () => {
    vi.mocked(analyzeSpatial).mockResolvedValue(zoneResult);
    const { onOpenDrilldown } = renderSpatial();
    await selectValue("Spatial Mode", "Zone Comparison");
    fireEvent.change(screen.getByRole("textbox", { name: "Spatial Rule Code" }), { target: { value: "cp_wafer_zone" } });
    fireEvent.change(screen.getByRole("textbox", { name: "Spatial Rule Version" }), { target: { value: "1.0.0" } });
    fireEvent.click(screen.getByRole("button", { name: "执行 Spatial 分析" }));

    const map = await screen.findByRole("img", { name: "ZONE_COMPARISON Spatial Map" });
    const option = JSON.parse(map.getAttribute("data-option") ?? "{}");
    expect(option.series[0].data.map((point: { zone: string }) => point.zone)).toEqual(["CENTER", "EDGE"]);
    expect(option.series.slice(-5).map((series: { name: string }) => series.name)).toEqual(["Center boundary", "Mid boundary", "Wafer boundary", "Approved quadrant X axis", "Approved quadrant Y axis"]);
    expect(option.visualMap.pieces.map((piece: { label: string }) => piece.label)).toEqual(["CENTER", "MID", "EDGE"]);
    expect(option.series[0].data.map((point: { quadrant: string }) => point.quadrant)).toEqual(["FAB_A", "FAB_B"]);
    expect(screen.getByText("CCW FAB_A → FAB_B → FAB_C → FAB_D")).toBeInTheDocument();
    fireEvent.click(map);
    expect(onOpenDrilldown).toHaveBeenCalledWith("UNIT:501");
    fireEvent.click(screen.getByRole("button", { name: "打开 EDGE Zone Detail" }));
    expect(screen.getByText("EDGE Radial Zone 成员 Unit")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "打开成员 UNIT:502" }));
    expect(onOpenDrilldown).toHaveBeenCalledWith("UNIT:502");
    fireEvent.click(screen.getByRole("button", { name: "打开 FAB_A Quadrant Detail" }));
    expect(screen.getByText("FAB_A Quadrant 成员 Unit")).toBeInTheDocument();
  }, 20_000);

  it("fails the display closed if a Zone response lacks approved geometry or point identity", async () => {
    vi.mocked(analyzeSpatial).mockResolvedValue({ ...zoneResult, zone_geometry: null, points: zoneResult.points.map((point) => ({ ...point, zone: null })) });
    renderSpatial();
    await selectValue("Spatial Mode", "Zone Comparison");
    fireEvent.change(screen.getByRole("textbox", { name: "Spatial Rule Code" }), { target: { value: "CP_WAFER_ZONE" } });
    fireEvent.change(screen.getByRole("textbox", { name: "Spatial Rule Version" }), { target: { value: "1.0.0" } });
    fireEvent.click(screen.getByRole("button", { name: "执行 Spatial 分析" }));

    expect(await screen.findByText("Zone 几何合同不完整")).toBeInTheDocument();
    expect(screen.queryByRole("img", { name: "ZONE_COMPARISON Spatial Map" })).not.toBeInTheDocument();
  }, 20_000);
});
