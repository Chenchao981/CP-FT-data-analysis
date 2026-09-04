// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { getAnalyticsDrilldown, type AnalyticsDrilldownResult } from "../../api/analytics";
import { AnalysisDrilldownDrawer } from "./AnalysisDrilldownDrawer";

vi.mock("../../api/analytics", async (original) => ({
  ...(await original<typeof import("../../api/analytics")>()),
  getAnalyticsDrilldown: vi.fn(),
}));
Object.defineProperty(window, "matchMedia", { writable: true, value: () => ({ matches: false, addListener: () => undefined, removeListener: () => undefined, addEventListener: () => undefined, removeEventListener: () => undefined }) });
vi.stubGlobal("ResizeObserver", class { observe() { return undefined; } unobserve() { return undefined; } disconnect() { return undefined; } });

const context = { datasets: [{ dataset_id: 1, version_no: 1 }], filters: { lot_ids: [], wafer_ids: [], bin_codes: [], overall_results: [], source_ids: [], tester_ids: [], program_versions: [], test_conditions: [] }, parameters: ["VTH"] };
const result: AnalyticsDrilldownResult = {
  contract_version: "ANALYTICS_CONTEXT_V1",
  dataset_context: { resolved_datasets: [{ dataset_id: 1, version_no: 1, dataset_name: "CP", test_stage: "CP", product_name: "P" }], test_stage: "CP", current_published_verified: true },
  filter_summary: { normalized_filters: context.filters, parameters: ["VTH"], filter_hash: "a".repeat(64), context_hash: "b".repeat(64) },
  rule_context: { spec_versions: [], bin_mapping_versions: [], evaluation_rule_versions: [] },
  unit: {
    drilldown_key: "UNIT:1", unit_id: 1, logical_unit_key: "CP:L:W:1:1", lot_id: "L", wafer_id: "W", x: 1, y: 1, soft_bin: "7", hard_bin: null, overall_result: "FAIL", source_row_no: 88,
    processing_run_id: 9, source_file_id: 8, receipt_id: 7, original_file_name: "raw.csv", sha256: "c".repeat(64), source_id: "SRC", tester_id: "T", program_version: "PV", cleaner_release: "CP:1",
    source_files: [{ source_file_id: 8, receipt_id: 7, original_file_name: "raw.csv", sha256: "c".repeat(64), ordinal_no: 1, file_role: "DETAIL", lineage_basis: "WRITER_VERIFIED" }],
    bin_evaluations: [{ unit_bin_evaluation_id: 6, bin_type: "CP_BIN", raw_bin_code: "7", mapping_status: "NO_MATCH", bin_mapping_set_id: null, mapping_version: null, bin_definition_id: null, mapped_bin_name: null, failure_mode_snapshot: null, is_pass_snapshot: null, processing_run_id: 9, evaluated_at_utc: "2026-08-31T00:00:00Z" }],
    measurements: [{ measurement_id: 5, parameter: "VTH", canonical_parameter_code: "VTH", step_code: "S1", sequence_no: 1, value_numeric: 1.5, value_text: null, status: "MEASURED", unit: "V", program_lsl: 1, program_usl: 2, program_limit_source: "TEST_PROGRAM_CONFIGURATION_NOT_FORMAL_SPEC", formal_spec: { status: "NO_SPEC", reason_code: "FORMAL_RELEASED_SPEC_NOT_FOUND", evaluation_id: null, evaluation_result: null, evaluation_scope_key: null, spec_binding_id: null, spec_set_id: null, spec_version: null, spec_item_id: null, lsl_applied: null, usl_applied: null, lower_operator_applied: null, upper_operator_applied: null }, evaluations: [] }],
  },
  warnings: [], computed_at: "2026-08-31T00:00:00Z",
};

describe("AnalysisDrilldownDrawer", () => {
  afterEach(() => { cleanup(); vi.clearAllMocks(); });

  it("shows formal lineage and fails closed at NO_SPEC without treating Program limits as Spec", async () => {
    vi.mocked(getAnalyticsDrilldown).mockResolvedValue(result);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><AnalysisDrilldownDrawer context={context} drilldownKey="UNIT:1" onClose={vi.fn()} /></QueryClientProvider>);

    expect(await screen.findByText("部分测量项没有正式规格")).toBeInTheDocument();
    expect(screen.getAllByText("raw.csv").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Tester Program Limit（非正式规格）").length).toBeGreaterThan(0);
    expect(screen.getByText("1 / 2")).toBeInTheDocument();
    expect(screen.getAllByText("NO_MATCH").length).toBeGreaterThan(0);
  });
});
