// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { getQualityManagementSummary, type QualityManagementSummary } from "../../api/management";
import { QualityManagementDashboard } from "./QualityManagementDashboard";

vi.mock("../../api/management", () => ({ getQualityManagementSummary: vi.fn() }));
vi.mock("../../components/EChart", () => ({
  EChart: ({ option }: { option: unknown }) => <div data-testid="quality-trend-chart" data-option={JSON.stringify(option)} />,
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

const qualitySummary: QualityManagementSummary = {
  observed_at_utc: "2026-08-29T02:00:00.000Z",
  from_utc: "2026-08-01T00:00:00.000Z",
  to_utc: "2026-09-01T00:00:00.000Z",
  filters: {},
  methodology: {
    fact_source: "Only PUBLISHED is_current=1 Dataset Versions and their Canonical test.* rows are counted.",
    yield: "PASS / (PASS + FAIL); UNKNOWN and ABORT never enter the yield denominator.",
    unknown: "UNKNOWN / all Current units; missing PASS/FAIL remains unknown and is never filled with zero.",
    product_identity: "Product is the source-observed TMS identity, not an SAP material until an approved crosswalk exists.",
    time_range: "from_utc is inclusive and to_utc is exclusive, based on Dataset published_at_utc.",
    trend_period: "Trend periods are Asia/Shanghai business dates; period_start_utc is the UTC instant of Shanghai local midnight.",
    failed_job_scope: "Failed Job counts use time, business domain, test stage, and factory filters only; Product and Lot filters do not apply.",
  },
  kpis: {
    dataset_count: 2,
    product_count: 1,
    lot_count: 2,
    total_units: 8,
    pass_units: 0,
    fail_units: 0,
    abort_units: 0,
    unknown_units: 8,
    known_yield_denominator: 0,
    yield_rate: null,
    unknown_rate: 1,
    failed_job_count: 1,
    latest_dataset_at_utc: "2026-08-29T01:55:00.000Z",
    freshness_seconds: 300,
  },
  trends: [{
    period_start_utc: "2026-08-28T16:00:00.000Z",
    dataset_count: 2,
    total_units: 8,
    pass_units: 0,
    fail_units: 0,
    unknown_units: 8,
    yield_rate: null,
    unknown_rate: 1,
  }],
  breakdowns: [
    { dimension: "FACTORY", key: "riyuexin", label: "日月新", dataset_count: 2, lot_count: 2, total_units: 8, pass_units: 0, fail_units: 0, unknown_units: 8, yield_rate: null, unknown_rate: 1 },
    { dimension: "PRODUCT", key: "NCE-MOS", label: "NCE-MOS", dataset_count: 2, lot_count: 2, total_units: 8, pass_units: 0, fail_units: 0, unknown_units: 8, yield_rate: null, unknown_rate: 1 },
    { dimension: "TEST_STAGE", key: "FT", label: "FT", dataset_count: 2, lot_count: 2, total_units: 8, pass_units: 0, fail_units: 0, unknown_units: 8, yield_rate: null, unknown_rate: 1 },
    { dimension: "BUSINESS_DOMAIN", key: "PRODUCTION", label: "量产", dataset_count: 2, lot_count: 2, total_units: 8, pass_units: 0, fail_units: 0, unknown_units: 8, yield_rate: null, unknown_rate: 1 },
  ],
  fail_bins: [{ bin_code: "BIN_5", fail_units: 3, share_of_failed: null }],
  recent_datasets: [{
    dataset_id: 20,
    version_no: 3,
    import_batch_id: 14,
    job_id: 96,
    product_name: "NCE-MOS",
    lot_id: "LOT-001",
    factory_code: "riyuexin",
    business_domain: "PRODUCTION",
    test_stage: "FT",
    unit_count: 8,
    pass_count: 0,
    fail_count: 0,
    unknown_count: 8,
    yield_rate: null,
    source_file_count: 6,
    published_at_utc: "2026-08-29T01:55:00.000Z",
  }],
};

const renderDashboard = (searchParams = new URLSearchParams(), canOpenAnalytics = true) => {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const props = {
    searchParams,
    onSearchParamsChange: vi.fn(),
    onOpenAnalytics: vi.fn(),
    onOpenJob: vi.fn(),
    canOpenAnalytics,
  };
  render(
    <QueryClientProvider client={queryClient}>
      <QualityManagementDashboard {...props} />
    </QueryClientProvider>,
  );
  return props;
};

describe("QualityManagementDashboard", () => {
  beforeEach(() => vi.mocked(getQualityManagementSummary).mockResolvedValue(qualitySummary));

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("shows the explicit quality methodology, UNKNOWN metrics, and null yield as a dash", async () => {
    renderDashboard();

    expect(await screen.findByText("质量趋势")).toBeInTheDocument();
    expect(document.body).toHaveTextContent("已知良率分母 0，ABORT 0 个，均未混入 FAIL");

    const yieldTitle = screen.getByText("已知良率");
    expect(within(yieldTitle.closest(".ant-card") as HTMLElement).getByText("—")).toBeInTheDocument();
    const unknownTitle = screen.getAllByText("UNKNOWN 占比").find((element) => element.classList.contains("ant-statistic-title"))!;
    expect(within(unknownTitle.closest(".ant-card") as HTMLElement).getByText("100.00%")).toBeInTheDocument();
    expect(screen.getByText("Fail Bin 分布")).toBeInTheDocument();
    expect(screen.getByText("BIN_5")).toBeInTheDocument();
    expect(screen.getByText("最近 Current Dataset")).toBeInTheDocument();
    expect(screen.getByText(/Lot 与 Source 追溯边界/)).toBeInTheDocument();
    expect(screen.getByTestId("quality-trend-chart").getAttribute("data-option")).toContain('"data":[null]');
    expect(screen.getByTestId("quality-trend-chart").getAttribute("data-option")).toContain("2026-08-29");

    fireEvent.click(screen.getByRole("button", { name: /统计方法与趋势明细/ }));
    expect(await screen.findByText("产品身份口径")).toBeInTheDocument();
    expect(document.body).toHaveTextContent("在 Crosswalk 审批前不视为 SAP 物料");
    expect(document.body).toHaveTextContent("按 Asia/Shanghai 业务日分组");
    expect(screen.getAllByText("上海业务日").length).toBeGreaterThan(0);
  }, 15_000);

  it("preserves URL filters and drills through real Dataset and Job identities", async () => {
    const props = renderDashboard(new URLSearchParams({ product_name: "NCE-MOS", test_stage: "FT", from_utc: "2026-08-01T00:00:00Z" }));

    await waitFor(() => expect(getQualityManagementSummary).toHaveBeenCalledWith(expect.objectContaining({ product_name: "NCE-MOS", test_stage: "FT", recent_limit: 20 })));
    fireEvent.change(screen.getByLabelText("Lot"), { target: { value: "LOT-002" } });
    fireEvent.change(screen.getByLabelText("开始时间（上海，含）"), { target: { value: "2026-08-02T08:30" } });
    fireEvent.change(screen.getByLabelText("结束时间（上海，不含）"), { target: { value: "2026-09-01T08:00" } });
    fireEvent.click(screen.getByRole("button", { name: /更新管理口径/ }));
    await waitFor(() => expect(props.onSearchParamsChange).toHaveBeenCalled());
    const next = props.onSearchParamsChange.mock.calls[0][0] as URLSearchParams;
    expect(next.get("product_name")).toBe("NCE-MOS");
    expect(next.get("test_stage")).toBe("FT");
    expect(next.get("lot_id")).toBe("LOT-002");
    expect(next.get("from_utc")).toBe("2026-08-02T00:30:00.000Z");
    expect(next.get("to_utc")).toBe("2026-09-01T00:00:00.000Z");
    expect(screen.getByText("失败 Job KPI 对当前筛选不适用")).toBeInTheDocument();
    const failedJobTitle = screen.getByText("失败 Job（批次口径）");
    expect(within(failedJobTitle.closest(".ant-card") as HTMLElement).getByText("不适用")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /分析/ }));
    expect(props.onOpenAnalytics).toHaveBeenCalledWith(20, 3);
    fireEvent.click(screen.getByRole("button", { name: /Job$/ }));
    expect(props.onOpenJob).toHaveBeenCalledWith(96);
  }, 15_000);

  it("keeps analysis disabled without catalog read access while preserving Job drilldown", async () => {
    const props = renderDashboard(new URLSearchParams(), false);

    const analysis = await screen.findByRole("button", { name: /分析/ });
    expect(analysis).toBeDisabled();
    expect(analysis).toHaveAttribute("title", "当前账户无权查看 Dataset 分析");
    fireEvent.click(analysis);
    expect(props.onOpenAnalytics).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: /Job$/ }));
    expect(props.onOpenJob).toHaveBeenCalledWith(96);
  }, 15_000);

  it("sanitizes backend failures", async () => {
    vi.mocked(getQualityManagementSummary).mockRejectedValueOnce(new Error("server=db;password=secret;path=C:\\raw"));

    renderDashboard();

    expect(await screen.findByText("质量管理摘要加载失败")).toBeInTheDocument();
    expect(screen.queryByText(/password|C:\\raw|server=db/)).not.toBeInTheDocument();
  });
});
