// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  evaluateAnalyticsInstantRisk,
  type AnalyticsContextRequest,
  type AnalyticsInstantRiskResult,
} from "../../api/analytics";
import { ApiError } from "../../api/auth";
import { ANALYSIS_COMPONENT_DEFAULTS, type OverviewRiskViewConfig } from "./context/analysisViewConfig";
import { OverviewInstantRiskPanel } from "./OverviewInstantRiskPanel";

vi.mock("../../api/analytics", async (original) => ({
  ...(await original<typeof import("../../api/analytics")>()),
  evaluateAnalyticsInstantRisk: vi.fn(),
}));
Object.defineProperty(window, "matchMedia", { writable: true, value: () => ({ matches: false, addListener: () => undefined, removeListener: () => undefined, addEventListener: () => undefined, removeEventListener: () => undefined }) });

const context: AnalyticsContextRequest = {
  datasets: [{ dataset_id: 20, version_no: 1 }],
  filters: { lot_ids: ["LOT-A"], wafer_ids: [], bin_codes: [], overall_results: [], source_ids: [], tester_ids: [], program_versions: [], test_conditions: [] },
  parameters: ["VTH"],
};
const config: OverviewRiskViewConfig = {
  ...ANALYSIS_COMPONENT_DEFAULTS.overviewRisk,
  analyses: ["CAPABILITY", "SPC_I_MR"],
  parameter: "VTH",
  groupBy: "LOT",
  capability: { method: "CPK_POOLED_WITHIN_RUN_V1", ruleCode: "CPK_RULE", versionCode: "v1" },
  spc: { ruleCode: "SPC_RULE", versionCode: "v2" },
};
const response: AnalyticsInstantRiskResult = {
  contract_version: "ANALYTICS_INSTANT_RISK_V1",
  filter_summary: { normalized_filters: context.filters, parameters: context.parameters, filter_hash: "a".repeat(64), context_hash: "b".repeat(64) },
  calculation_context_hash: "c".repeat(64),
  requested_analyses: ["CAPABILITY", "SPC_I_MR"],
  items: [{
    code: "CAPABILITY:20:A", analysis: "CAPABILITY", category: "CAPABILITY", severity: "WARNING", status: "ACTIVE", reason_code: null,
    title: "VTH Capability", message: "MIN_CPK_PPK=1.1", dataset_id: 20, version_no: 1, group_key: "DATASET:20", parameter: "VTH",
    metric_code: "MIN_CPK_PPK", metric_value: 1.1, threshold_operator: "<", threshold_value: 1.33, affected_count: 1, denominator_count: 1, rate: 1,
    evidence_drilldown_keys: [], evidence_truncated: false,
    aggregate_drilldown_context: { dataset_id: 20, version_no: 1, parameter: "VTH", lower_bound: null, upper_bound: null, lower_inclusive: true, upper_inclusive: true },
    rule: { rule_code: "CPK_RULE", version_code: "v1", algorithm_code: "CPK_POOLED_WITHIN_RUN_V1", approval_status: "APPROVED", activation_status: "ENABLED", parameters_sha256: "d".repeat(64) },
  }],
  warnings: [], computed_at: "2026-08-31T00:00:00Z",
};

function renderPanel(value: OverviewRiskViewConfig, onOpen = vi.fn(), onOpenAggregate = vi.fn()) {
  const queryClient = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
  render(<QueryClientProvider client={queryClient}><OverviewInstantRiskPanel context={context} parameterOptions={["VTH"]} config={value} onConfigChange={vi.fn()} onOpenDrilldown={onOpen} onOpenAggregateDrilldown={onOpenAggregate} /></QueryClientProvider>);
  return { onOpen, onOpenAggregate };
}

describe("OverviewInstantRiskPanel", () => {
  beforeEach(() => vi.mocked(evaluateAnalyticsInstantRisk).mockResolvedValue(response));
  afterEach(() => { cleanup(); vi.clearAllMocks(); });

  it("never runs on entry and requires selected methods plus exact rule versions", () => {
    renderPanel(ANALYSIS_COMPONENT_DEFAULTS.overviewRisk);
    expect(evaluateAnalyticsInstantRisk).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "执行即时风险评估" })).toBeDisabled();
    expect(screen.getByText("尚未显式执行即时风险评估")).toBeInTheDocument();
  });

  it("posts the complete Context and exact rules only after an explicit click", async () => {
    const { onOpen, onOpenAggregate } = renderPanel(config);
    vi.mocked(evaluateAnalyticsInstantRisk).mockClear();

    fireEvent.click(screen.getByRole("button", { name: "执行即时风险评估" }));

    await waitFor(() => expect(evaluateAnalyticsInstantRisk).toHaveBeenCalledWith({
      ...context,
      evaluations: [
        { analysis: "CAPABILITY", parameter: "VTH", capability_method: "CPK_POOLED_WITHIN_RUN_V1", rule: { rule_code: "CPK_RULE", version_code: "v1" } },
        { analysis: "SPC_I_MR", parameter: "VTH", group_by: "LOT", rule: { rule_code: "SPC_RULE", version_code: "v2" }, spc_order: "UNIT_SEQUENCE", spc_phase: "PHASE_I_BASELINE" },
      ],
    }));
    expect(await screen.findByText("CPK_RULE@v1")).toBeInTheDocument();
    expect(screen.getByText("MIN_CPK_PPK: 1.10000")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "受影响总体" }));
    expect(onOpen).not.toHaveBeenCalled();
    expect(onOpenAggregate).toHaveBeenCalledWith(expect.objectContaining({ dataset: { dataset_id: 20, version_no: 1 }, parameters: ["VTH"] }));
  });

  it("shows the stable no-approval gate and never fabricates a result", async () => {
    vi.mocked(evaluateAnalyticsInstantRisk).mockRejectedValueOnce(new ApiError(409, {
      code: "ANALYSIS_RULE_NOT_APPROVED",
      message: "requested rule is not approved",
      retryable: false,
      recommended_action: "approve and activate the exact version",
    }, "failed"));
    renderPanel(config);

    fireEvent.click(screen.getByRole("button", { name: "执行即时风险评估" }));

    expect(await screen.findByText("即时风险评估失败（失败关闭）")).toBeInTheDocument();
    expect(screen.getByText(/ANALYSIS_RULE_NOT_APPROVED/)).toBeInTheDocument();
    expect(screen.queryByText("CPK_RULE@v1")).not.toBeInTheDocument();
  });
});
