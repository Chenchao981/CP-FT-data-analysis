// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { getDatasetChartData, type DatasetChartData } from "../../api/datasets";
import { AnalyticsWorkbench } from "./AnalyticsWorkbench";

vi.mock("../../api/datasets", () => ({ getDatasetChartData: vi.fn() }));
vi.mock("../../components/EChart", () => ({ EChart: () => <div data-testid="echart" /> }));

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

function makeChartData(
  lotId?: string,
  sourceId?: string,
  parameter?: string,
): DatasetChartData {
  const isLotB = lotId === "LOT-B";
  const useParameterB = isLotB || sourceId === "SOURCE-B";
  const parameterName = useParameterB ? "PARAM-B" : "PARAM-A";
  return {
    dataset_id: 20,
    version_no: 1,
    test_stage: "FT",
    product_name: "PRODUCT-1",
    selected_lot_id: lotId ?? null,
    selected_wafer_id: null,
    selected_source_id: sourceId ?? null,
    selected_parameter: parameter ?? null,
    lot_options: ["LOT-A", "LOT-B"],
    wafer_options: [],
    source_options: isLotB ? ["SOURCE-B"] : ["SOURCE-A", "SOURCE-B"],
    parameter_options: [{
      name: parameterName,
      unit: "V",
      lsl: 1,
      usl: 2,
      test_condition: "VGE=0V",
    }],
    wafer_yield: [],
    bin_counts: [],
    wafer_map: [],
    ft_parameter_points: [],
    ft_total_point_count: 0,
    ft_sampled: false,
  };
}

function renderWorkbench() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <AnalyticsWorkbench initialSelection={{ datasetId: 20, versionNo: 1 }} />
    </QueryClientProvider>,
  );
}

describe("AnalyticsWorkbench FT dependent filters", () => {
  beforeEach(() => {
    vi.mocked(getDatasetChartData).mockImplementation(
      async (_datasetId, _versionNo, lotId, _waferId, sourceId, parameter) => (
        makeChartData(lotId, sourceId, parameter)
      ),
    );
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("clears parameter on source change and clears both source and parameter on Lot change", async () => {
    renderWorkbench();

    await waitFor(() => expect(getDatasetChartData).toHaveBeenCalledWith(
      20, 1, undefined, undefined, undefined, "PARAM-A",
    ));
    await screen.findByRole("heading", { name: "FT 参数分析" });

    fireEvent.mouseDown(screen.getAllByRole("combobox")[1]);
    fireEvent.click(await screen.findByTitle("SOURCE-A"));
    await waitFor(() => expect(getDatasetChartData).toHaveBeenCalledWith(
      20, 1, undefined, undefined, "SOURCE-A", undefined,
    ));
    await waitFor(() => expect(getDatasetChartData).toHaveBeenCalledWith(
      20, 1, undefined, undefined, "SOURCE-A", "PARAM-A",
    ));
    await waitFor(() => expect(screen.getAllByRole("combobox")).toHaveLength(3));

    vi.mocked(getDatasetChartData).mockClear();
    fireEvent.mouseDown(screen.getAllByRole("combobox")[0]);
    fireEvent.click(await screen.findByTitle("LOT-B"));

    await waitFor(() => expect(getDatasetChartData).toHaveBeenCalledWith(
      20, 1, "LOT-B", undefined, undefined, undefined,
    ));
  });
});
