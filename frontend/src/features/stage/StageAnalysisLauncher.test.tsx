// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { getAnalyticsShellContext } from "../../api/analytics";
import type { StageResultRow } from "../../api/stageData";
import { StageAnalysisLauncher } from "./StageAnalysisLauncher";

vi.mock("../../api/analytics", () => ({ getAnalyticsShellContext: vi.fn() }));

Object.defineProperty(window, "matchMedia", { writable: true, value: () => ({ matches: false, addListener: () => undefined, removeListener: () => undefined, addEventListener: () => undefined, removeEventListener: () => undefined }) });
vi.stubGlobal("ResizeObserver", class { observe() { return undefined; } unobserve() { return undefined; } disconnect() { return undefined; } });

const result = (id: number, lot: string, sourceChannel: string, uploader = "operator"): StageResultRow => ({
  result_summary_id: id,
  import_batch_id: 100 + id,
  data_name: "PRODUCT-A",
  product_name: "PRODUCT-A",
  lot_id: lot,
  wafer_count: 1,
  factory_code: "huahong",
  uploader_login: uploader,
  uploader_name: uploader,
  source_channel: sourceChannel,
  can_manage: true,
  test_item_count: 3,
  unit_count: 100,
  pass_count: 99,
  yield_rate: 0.99,
  status: "PROCESSED",
  data_type: "CP",
  dataset_id: id,
  dataset_version_no: 1,
  created_at_utc: "2026-09-03T00:00:00Z",
});

const rows = [
  result(1, "LOT-A", "WEB"),
  result(2, "LOT-B", "WEB"),
  result(3, "LOT-FTP", "SOURCE_CATALOG", "SYSTEM_INGESTION"),
];

async function chooseSelect(label: string, value: string) {
  fireEvent.mouseDown(screen.getByRole("combobox", { name: label }));
  fireEvent.click(await screen.findByTitle(value));
}

describe("StageAnalysisLauncher", () => {
  beforeEach(() => {
    vi.mocked(getAnalyticsShellContext).mockResolvedValue({
      options: { parameters: ["BVDSS", "RDON", "VTH"] },
    } as never);
  });
  afterEach(() => { cleanup(); vi.clearAllMocks(); });

  it("loads parameters only after product and multi-lot selection, then draws once", async () => {
    const onDraw = vi.fn();
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={queryClient}><StageAnalysisLauncher testStage="CP" rows={rows} loading={false} currentLogin="operator" onDraw={onDraw} /></QueryClientProvider>);

    expect(screen.queryByText("BVDSS")).not.toBeInTheDocument();
    await chooseSelect("CP分析产品", "PRODUCT-A");
    await chooseSelect("CP分析批次", "LOT-A");
    await chooseSelect("CP分析批次", "LOT-B");

    await waitFor(() => expect(getAnalyticsShellContext).toHaveBeenCalled());
    expect(await screen.findByText("BVDSS")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /全选/ }));
    fireEvent.click(screen.getByRole("button", { name: /绘制所选图表/ }));

    expect(onDraw).toHaveBeenCalledWith({
      datasets: [{ datasetId: 1, versionNo: 1 }, { datasetId: 2, versionNo: 1 }],
      lotIds: ["LOT-A", "LOT-B"],
      parameters: ["BVDSS", "RDON", "VTH"],
      chartTypes: ["SCATTER"],
    });
  }, 10_000);

  it("separates server-directory results from personal web uploads", async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={queryClient}><StageAnalysisLauncher testStage="CP" rows={rows} loading={false} currentLogin="operator" onDraw={vi.fn()} /></QueryClientProvider>);

    expect(screen.getByText("我的本机上传（2）")).toBeInTheDocument();
    fireEvent.click(screen.getByText("服务器目录 / 自动清洗（1）"));
    expect(screen.getByText("服务器目录 / 自动清洗（1）")).toBeInTheDocument();
  });

  it("re-resolves parameter-scoped rules after the user selects parameters", async () => {
    vi.mocked(getAnalyticsShellContext).mockImplementation(async (request) => ({
      options: { parameters: ["BVDSS", "RDON", "VTH"] },
      rule_context: {
        applicable_rule_versions: request.parameters.includes("BVDSS")
          ? ["RULE:CPK_DEFAULT:V1"]
          : [],
      },
    } as never));
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={queryClient}><StageAnalysisLauncher testStage="CP" rows={rows} loading={false} currentLogin="operator" onDraw={vi.fn()} /></QueryClientProvider>);

    await chooseSelect("CP分析产品", "PRODUCT-A");
    await chooseSelect("CP分析批次", "LOT-A");
    expect(await screen.findByRole("checkbox", { name: "能力分析（不可用）" })).toBeDisabled();

    fireEvent.click(screen.getByRole("checkbox", { name: "BVDSS" }));

    await waitFor(() => expect(getAnalyticsShellContext).toHaveBeenLastCalledWith(
      expect.objectContaining({ parameters: ["BVDSS"] }),
    ));
    expect(await screen.findByRole("checkbox", { name: "能力分析" })).toBeEnabled();
  }, 15_000);
});
