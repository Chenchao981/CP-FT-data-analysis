// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  compareDatasets,
  getDatasetChartData,
  getDatasetDetails,
  type DatasetChartData,
  type DatasetComparisonResult,
  type DatasetDetailPage,
} from "../../api/datasets";
import { AnalyticsWorkbench, type DatasetSelection } from "./AnalyticsWorkbench";

vi.mock("../../api/datasets", () => ({
  compareDatasets: vi.fn(),
  getDatasetChartData: vi.fn(),
  getDatasetDetails: vi.fn(),
}));
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

const selections: DatasetSelection[] = [
  { datasetId: 20, versionNo: 1 },
  { datasetId: 21, versionNo: 2 },
];

const comparison: DatasetComparisonResult = {
  test_stage: "FT",
  spec_compatibility: "COMPATIBLE",
  lot_ids: [],
  wafer_ids: [],
  bin_codes: [],
  parameters: [],
  items: [
    { dataset_id: 20, version_no: 1, test_stage: "FT", product_name: "PRODUCT-A", unit_count: 100, pass_count: 80, fail_count: 10, unknown_count: 10, abort_count: 0, known_yield_denominator: 90, yield_rate: 80 / 90, parameter_statistics: [] },
    { dataset_id: 21, version_no: 2, test_stage: "FT", product_name: "PRODUCT-B", unit_count: 120, pass_count: 0, fail_count: 0, unknown_count: 120, abort_count: 0, known_yield_denominator: 0, yield_rate: null, parameter_statistics: [] },
  ],
};

const detailPage: DatasetDetailPage = {
  dataset_id: 21,
  version_no: 2,
  test_stage: "FT",
  page: 1,
  page_size: 20,
  total: 60,
  lot_options: ["LOT-A", "LOT-B"],
  wafer_options: ["W1", "W2"],
  bin_options: ["1", "2"],
  parameter_options: ["VTH", "RDON"],
  items: [{
    unit_id: 501,
    logical_unit_key: "UNIT-501",
    lot_id: "LOT-A",
    wafer_id: "W1",
    x: null,
    y: null,
    soft_bin: "1",
    hard_bin: null,
    overall_result: "UNKNOWN",
    source_row_no: 51,
    measurements: [{ parameter: "VTH", value_numeric: 1.55, value_text: null, status: "VALID", unit: "V", lsl: 1, usl: 2 }],
  }],
};

const chartData: DatasetChartData = {
  dataset_id: 21,
  version_no: 2,
  test_stage: "FT",
  product_name: "PRODUCT-B",
  selected_lot_id: null,
  selected_wafer_id: null,
  selected_source_id: null,
  selected_parameter: null,
  lot_options: ["LOT-A", "LOT-B"],
  wafer_options: [],
  source_options: ["SRC-1", "SRC-2"],
  parameter_options: [{ name: "VTH", unit: "V", lsl: 1, usl: 2, test_condition: "VGE=0V" }],
  wafer_yield: [],
  bin_counts: [],
  wafer_map: [],
  ft_parameter_points: [{ sequence: 1, lot_id: "LOT-A", source_id: "SRC-1", value: 1.55, status: "VALID" }],
  ft_total_point_count: 1,
  ft_sampled: false,
};

function renderAnalytics({
  datasets = selections,
  initialSearch = "dataset=20%3A1&dataset=21%3A2",
  onOpenCatalog = vi.fn(),
}: {
  datasets?: DatasetSelection[];
  initialSearch?: string;
  onOpenCatalog?: () => void;
} = {}) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  function StatefulAnalytics() {
    const [params, setParams] = useState(() => new URLSearchParams(initialSearch));
    return <>
      <output data-testid="analytics-search">{params.toString()}</output>
      <AnalyticsWorkbench datasets={datasets} searchParams={params} onSearchParamsChange={setParams} onOpenCatalog={onOpenCatalog} />
    </>;
  }
  render(<QueryClientProvider client={queryClient}><StatefulAnalytics /></QueryClientProvider>);
  return { onOpenCatalog };
}

describe("AnalyticsWorkbench formal Dataset flow", () => {
  beforeEach(() => {
    vi.mocked(compareDatasets).mockResolvedValue(comparison);
    vi.mocked(getDatasetDetails).mockImplementation(async (datasetId, versionNo, request) => ({
      ...detailPage,
      dataset_id: datasetId,
      version_no: versionNo,
      page: request.page,
      page_size: request.page_size,
    }));
    vi.mocked(getDatasetChartData).mockResolvedValue(chartData);
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("replaces manual Dataset entry with a clear return-to-catalog CTA", async () => {
    const onOpenCatalog = vi.fn();
    renderAnalytics({ datasets: [], initialSearch: "", onOpenCatalog });

    expect(screen.getByText("尚未选择 Dataset")).toBeInTheDocument();
    expect(screen.queryByText("Dataset编号")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /返回历史正式数据选择/ }));

    expect(onOpenCatalog).toHaveBeenCalledOnce();
    expect(compareDatasets).not.toHaveBeenCalled();
    expect(getDatasetDetails).not.toHaveBeenCalled();
    expect(getDatasetChartData).not.toHaveBeenCalled();
  });

  it("runs server comparison, filtered chart, and paged details for the selected Dataset set", async () => {
    renderAnalytics({
      initialSearch: "dataset=20%3A1&dataset=21%3A2&detail_dataset=21%3A2&lot_id=LOT-A&lot_id=LOT-B&wafer_id=W1&bin_code=1&parameter=VTH&source_id=SRC-1&page=2&page_size=20",
    });

    expect(await screen.findByText("UNIT-501", {}, { timeout: 15_000 })).toBeInTheDocument();
    await waitFor(() => expect(compareDatasets).toHaveBeenCalledWith({
      datasets: [{ dataset_id: 20, version_no: 1 }, { dataset_id: 21, version_no: 2 }],
      lot_ids: ["LOT-A", "LOT-B"],
      wafer_ids: ["W1"],
      bin_codes: ["1"],
      parameters: ["VTH"],
    }));
    expect(getDatasetDetails).toHaveBeenCalledWith(21, 2, {
      page: 2,
      page_size: 20,
      lot_ids: ["LOT-A", "LOT-B"],
      wafer_ids: ["W1"],
      bin_codes: ["1"],
      parameters: ["VTH"],
    });
    expect(getDatasetChartData).toHaveBeenCalledWith(21, 2, "LOT-A", "W1", "SRC-1", "VTH");
    expect(screen.getByText("VTH: 1.55 V (VALID)")).toBeInTheDocument();
    expect(screen.queryByText("Dataset编号")).not.toBeInTheDocument();
  }, 20_000);

  it("writes filter and detail pagination changes to URL while preserving Dataset selections", async () => {
    renderAnalytics({ initialSearch: "dataset=20%3A1&dataset=21%3A2&page=3&page_size=20" });
    expect(await screen.findByText("UNIT-501", {}, { timeout: 15_000 })).toBeInTheDocument();

    fireEvent.mouseDown(screen.getByRole("combobox", { name: "Lot 筛选" }));
    fireEvent.click(await screen.findByTitle("LOT-B"));

    await waitFor(() => {
      const params = new URLSearchParams(screen.getByTestId("analytics-search").textContent ?? "");
      expect(params.getAll("dataset")).toEqual(["20:1", "21:2"]);
      expect(params.getAll("lot_id")).toEqual(["LOT-B"]);
      expect(params.get("page")).toBe("1");
    });
    await waitFor(() => expect(getDatasetDetails).toHaveBeenLastCalledWith(20, 1, expect.objectContaining({ page: 1, lot_ids: ["LOT-B"] })));

    fireEvent.click(screen.getByTitle("2"));
    await waitFor(() => {
      const params = new URLSearchParams(screen.getByTestId("analytics-search").textContent ?? "");
      expect(params.get("page")).toBe("2");
      expect(params.get("page_size")).toBe("20");
    });
  }, 20_000);

  it("keeps an unknown yield visibly unknown", async () => {
    vi.mocked(compareDatasets).mockResolvedValue({ ...comparison, items: [comparison.items[1]], spec_compatibility: "SINGLE_DATASET" });
    renderAnalytics({ datasets: [{ datasetId: 21, versionNo: 2 }], initialSearch: "dataset=21%3A2" });

    expect(await screen.findByText("服务端比较（1 个 Dataset）", {}, { timeout: 15_000 })).toBeInTheDocument();
    const row = screen.getAllByText("PRODUCT-B").map((node) => node.closest("tr")).find(Boolean);
    expect(row).not.toBeNull();
    expect(row).toHaveTextContent("—");
    expect(row).not.toHaveTextContent("0.000%");
  }, 20_000);
});
