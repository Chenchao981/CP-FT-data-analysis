// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AnalyticsContextRequest } from "../../api/analytics";
import { getWaferSummary, type WaferSummaryResult } from "../../api/waferSummary";
import { WaferSummaryPanel } from "./WaferSummaryPanel";

vi.mock("../../api/waferSummary", () => ({ getWaferSummary: vi.fn() }));

Object.defineProperty(window, "matchMedia", { writable: true, value: () => ({ matches: false, addListener: () => undefined, removeListener: () => undefined, addEventListener: () => undefined, removeEventListener: () => undefined }) });
vi.stubGlobal("ResizeObserver", class { observe() { return undefined; } unobserve() { return undefined; } disconnect() { return undefined; } });

const context: AnalyticsContextRequest = {
  datasets: [{ dataset_id: 20, version_no: 1 }],
  filters: { lot_ids: ["LOT-A"], wafer_ids: [], bin_codes: ["1"], overall_results: ["UNKNOWN"], source_ids: ["SRC-1"], tester_ids: ["T-1"], program_versions: ["P-1"], test_conditions: ["C-1"] },
  parameters: ["RDON", "VTH"],
};
const result: WaferSummaryResult = {
  contract_version: "WAFER_SUMMARY_V1",
  dataset_context: { resolved_datasets: [{ dataset_id: 20, version_no: 1, dataset_name: "CP20", test_stage: "CP", product_name: "P-A" }], test_stage: "CP", current_published_verified: true },
  filter_summary: { normalized_filters: context.filters, parameters: context.parameters, filter_hash: "a".repeat(64), context_hash: "b".repeat(64) },
  rule_context: { spec_versions: ["SPEC:7"], bin_mapping_versions: ["BIN:2"], evaluation_rule_versions: [] },
  capabilities: [{ code: "WAFER_SUMMARY", status: "AVAILABLE", reason_code: null, message: null }],
  page: 2,
  page_size: 20,
  total: 60,
  sort_by: "DATASET",
  sort_direction: "ASC",
  items: [{
    dataset_id: 20, version_no: 1, lot_id: "LOT-A", wafer_id: "W1", unit_count: 10, pass_count: 0, fail_count: 0, unknown_count: 10, abort_count: 0, known_yield_denominator: 0, yield_rate: null,
    parameters: [
      { parameter: "RDON", unit: "mOhm", measured_count: 8, missing_count: 2, out_of_spec_count: 1, minimum: 1, maximum: 5, mean: 2.5 },
      { parameter: "VTH", unit: "V", measured_count: 9, missing_count: 1, out_of_spec_count: 0, minimum: 1.1, maximum: 1.9, mean: 1.5 },
    ],
    drilldown_context: { dataset_id: 20, version_no: 1, lot_id: "LOT-A", wafer_id: "W1" },
  }],
  warnings: ["YIELD_DENOMINATOR_EMPTY"],
  computed_at: "2026-08-31T00:00:00Z",
};

function renderPanel(testStage = "CP", onOpenAggregateDrilldown = vi.fn()) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  function Stateful() {
    const [page, setPage] = useState(2);
    const [pageSize, setPageSize] = useState(20);
    return <WaferSummaryPanel context={context} testStage={testStage} page={page} pageSize={pageSize} onPaginationChange={(nextPage, nextSize) => { setPage(nextPage); setPageSize(nextSize); }} onOpenAggregateDrilldown={onOpenAggregateDrilldown} />;
  }
  render(<QueryClientProvider client={queryClient}><Stateful /></QueryClientProvider>);
  return { onOpenAggregateDrilldown };
}

async function selectValue(label: string, value: string) {
  fireEvent.mouseDown(screen.getByRole("combobox", { name: label }));
  fireEvent.click(await screen.findByTitle(value));
}

describe("WaferSummaryPanel", () => {
  beforeEach(() => { vi.mocked(getWaferSummary).mockImplementation(async (request) => ({ ...result, page: request.page, page_size: request.page_size, sort_by: request.sort_by, sort_direction: request.sort_direction })); });
  afterEach(() => { cleanup(); vi.clearAllMocks(); });

  it("uses server paging/sorting, renders dynamic parameter columns, and keeps empty Yield unknown", async () => {
    const { onOpenAggregateDrilldown } = renderPanel();
    await waitFor(() => expect(getWaferSummary).toHaveBeenCalled());
    expect(vi.mocked(getWaferSummary).mock.calls.at(-1)?.[0]).toEqual({ ...context, page: 2, page_size: 20, sort_by: "DATASET", sort_direction: "ASC" });
    expect(await screen.findByText("—（无 PASS/FAIL 分母）")).toBeInTheDocument();
    expect(screen.queryByText("0.000%")).not.toBeInTheDocument();
    expect(screen.getByText("YIELD_DENOMINATOR_EMPTY")).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "RDON" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "VTH" })).toBeInTheDocument();
    expect(screen.getByText("Mean 2.5 mOhm")).toBeInTheDocument();
    expect(screen.getByText("Measured / Missing / OOS 8 / 2 / 1")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "查看 LOT-A Wafer W1 明细" }));
    expect(onOpenAggregateDrilldown).toHaveBeenCalledWith({
      dataset: { dataset_id: 20, version_no: 1 },
      filters: { lot_ids: ["LOT-A"], wafer_ids: ["W1"] },
      parameters: ["RDON", "VTH"],
    });

    await selectValue("Wafer Summary 排序字段", "Yield");
    await selectValue("Wafer Summary 排序方向", "Descending");
    await waitFor(() => expect(vi.mocked(getWaferSummary).mock.calls.at(-1)?.[0]).toEqual({ ...context, page: 1, page_size: 20, sort_by: "YIELD", sort_direction: "DESC" }));

    fireEvent.click(screen.getByTitle("3"));
    await waitFor(() => expect(vi.mocked(getWaferSummary).mock.calls.at(-1)?.[0]).toEqual({ ...context, page: 3, page_size: 20, sort_by: "YIELD", sort_direction: "DESC" }));
  }, 20_000);

  it("does not call the CP-only endpoint for an FT Context", () => {
    renderPanel("FT");
    expect(screen.getByText("Wafer Summary 仅适用于 CP 数据")).toBeInTheDocument();
    expect(getWaferSummary).not.toHaveBeenCalled();
  });
});
