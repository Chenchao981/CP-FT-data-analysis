// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { getQualityManagementSummary, type QualityManagementSummary } from "../../api/management";
import { QualityManagementDashboard } from "./QualityManagementDashboard";

vi.mock("../../api/management", () => ({ getQualityManagementSummary: vi.fn() }));

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
    fact_source: "PUBLISHED Current Dataset（is_current = 1）",
    yield: "PASS / (PASS + FAIL)，UNKNOWN 与 ABORT 排除在分母外",
    unknown: "UNKNOWN / 全部单元",
    product_identity: "使用来源观测产品身份，未审批 Crosswalk 不视为 SAP 物料",
    time_range: "published_at_utc 的 [from, to) 边界",
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
    period_start_utc: "2026-08-29T00:00:00.000Z",
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

const renderDashboard = (searchParams = new URLSearchParams()) => {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const props = {
    searchParams,
    onSearchParamsChange: vi.fn(),
    onOpenAnalytics: vi.fn(),
    onOpenJob: vi.fn(),
    canOpenAnalytics: true,
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

    expect(await screen.findByText("后端返回的方法说明")).toBeInTheDocument();
    expect(document.body).toHaveTextContent("PASS / (PASS + FAIL)；UNKNOWN 和 ABORT 不进入良率分母。");
    expect(document.body).toHaveTextContent("开始含、结束不含");
    expect(screen.getByText("产品身份口径")).toBeInTheDocument();
    expect(document.body).toHaveTextContent("未审批 Crosswalk 不视为 SAP 物料");

    const yieldTitle = screen.getByText("PASS / (PASS + FAIL) 良率");
    expect(within(yieldTitle.closest(".ant-card") as HTMLElement).getByText("—")).toBeInTheDocument();
    const unknownTitle = screen.getAllByText("UNKNOWN 占比").find((element) => element.classList.contains("ant-statistic-title"))!;
    expect(within(unknownTitle.closest(".ant-card") as HTMLElement).getByText("100.00%")).toBeInTheDocument();
    expect(screen.getByText("Fail Bin 分布")).toBeInTheDocument();
    expect(screen.getByText("BIN_5")).toBeInTheDocument();
    expect(screen.getByText("最近 Current Dataset")).toBeInTheDocument();
    expect(screen.getByText(/Lot 与 Source 追溯边界/)).toBeInTheDocument();
  }, 15_000);

  it("preserves URL filters and drills through real Dataset and Job identities", async () => {
    const props = renderDashboard(new URLSearchParams({ product_name: "NCE-MOS", test_stage: "FT" }));

    await waitFor(() => expect(getQualityManagementSummary).toHaveBeenCalledWith(expect.objectContaining({ product_name: "NCE-MOS", test_stage: "FT", recent_limit: 20 })));
    fireEvent.change(screen.getByLabelText("Lot"), { target: { value: "LOT-002" } });
    fireEvent.click(screen.getByRole("button", { name: /更新管理口径/ }));
    await waitFor(() => expect(props.onSearchParamsChange).toHaveBeenCalled());
    const next = props.onSearchParamsChange.mock.calls[0][0] as URLSearchParams;
    expect(next.get("product_name")).toBe("NCE-MOS");
    expect(next.get("test_stage")).toBe("FT");
    expect(next.get("lot_id")).toBe("LOT-002");

    fireEvent.click(screen.getByRole("button", { name: /分析/ }));
    expect(props.onOpenAnalytics).toHaveBeenCalledWith(20, 3);
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
