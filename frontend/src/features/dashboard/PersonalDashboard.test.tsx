// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { listMyDataDomains } from "../../api/dataDomains";
import { getQualityManagementSummary, type QualityManagementSummary } from "../../api/management";
import { listQuickAnalysisSessions } from "../../api/quickAnalysis";
import { PersonalDashboard } from "./PersonalDashboard";

vi.mock("../../components/EChart", () => ({
  EChart: ({ ariaLabel }: { ariaLabel?: string }) => <div role="img" aria-label={ariaLabel} />,
}));
vi.mock("../../api/dataDomains", () => ({ listMyDataDomains: vi.fn() }));
vi.mock("../../api/management", () => ({ getQualityManagementSummary: vi.fn() }));
vi.mock("../../api/quickAnalysis", () => ({ listQuickAnalysisSessions: vi.fn() }));

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

const summary: QualityManagementSummary = {
  observed_at_utc: "2026-09-01T00:00:00.000Z",
  from_utc: "2026-08-02T00:00:00.000Z",
  to_utc: "2026-09-01T00:00:00.000Z",
  filters: { access_scope: "PERSONAL", data_domain_id: null },
  methodology: {},
  kpis: {
    dataset_count: 2,
    product_count: 1,
    lot_count: 2,
    total_units: 100,
    pass_units: 80,
    fail_units: 10,
    abort_units: 0,
    unknown_units: 10,
    known_yield_denominator: 90,
    yield_rate: 80 / 90,
    unknown_rate: 0.1,
    failed_job_count: 1,
    latest_dataset_at_utc: "2026-08-31T00:00:00.000Z",
    freshness_seconds: 86400,
  },
  trends: [{
    period_start_utc: "2026-08-31T16:00:00.000Z",
    dataset_count: 2,
    total_units: 100,
    pass_units: 80,
    fail_units: 10,
    unknown_units: 10,
    yield_rate: 80 / 90,
    unknown_rate: 0.1,
  }],
  breakdowns: [],
  fail_bins: [],
  recent_datasets: [{
    dataset_id: 21,
    version_no: 1,
    import_batch_id: 31,
    job_id: 41,
    product_name: "NCE-PRODUCT",
    lot_id: "LOT-01",
    factory_code: "JIEQUN",
    business_domain: "PRODUCTION",
    test_stage: "FT",
    unit_count: 100,
    pass_count: 80,
    fail_count: 10,
    unknown_count: 10,
    yield_rate: 80 / 90,
    source_file_count: 520,
    published_at_utc: "2026-08-31T00:00:00.000Z",
  }],
};

function renderDashboard(onNavigate = vi.fn()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return {
    onNavigate,
    ...render(<QueryClientProvider client={client}><PersonalDashboard userName="测试员" onNavigate={onNavigate} canOpenQuality canRunQuickAnalysis /></QueryClientProvider>),
  };
}

describe("PersonalDashboard data scopes", () => {
  beforeEach(() => {
    vi.mocked(listQuickAnalysisSessions).mockResolvedValue({
      items: [],
      total: 0,
      page: 1,
      page_size: 5,
    });
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("opens the exact formal version from recent dashboard evidence", async () => {
    vi.mocked(listMyDataDomains).mockResolvedValue([]);
    vi.mocked(getQualityManagementSummary).mockResolvedValue(summary);
    const { onNavigate } = renderDashboard();
    fireEvent.click(await screen.findByRole("button", { name: "查看分析" }));
    expect(onNavigate).toHaveBeenCalledWith("/analytics?dataset=21%3A1");
  });

  it("loads only the current user's PERSONAL 30-day summary without demo metrics", async () => {
    vi.mocked(listMyDataDomains).mockResolvedValue([]);
    vi.mocked(getQualityManagementSummary).mockResolvedValue(summary);

    renderDashboard();

    expect(screen.getByText("个人驾驶舱")).toBeInTheDocument();
    expect(screen.queryByText(/Demo|演示数据/)).not.toBeInTheDocument();
    expect(document.body).not.toHaveTextContent("仅展示当前权限范围");
    await waitFor(() => expect(getQualityManagementSummary).toHaveBeenCalledWith(expect.objectContaining({
      access_scope: "PERSONAL",
      data_domain_id: undefined,
      recent_limit: 8,
    })));
    await waitFor(() => expect(listQuickAnalysisSessions).toHaveBeenCalledWith({
      page: 1,
      page_size: 5,
      access_scope: "PERSONAL",
    }));
    expect(await screen.findByText("当前正式数据集")).toBeInTheDocument();
  });

  it("renders only PERSONAL Quick rows even if a response is contaminated", async () => {
    vi.mocked(listMyDataDomains).mockResolvedValue([]);
    vi.mocked(getQualityManagementSummary).mockResolvedValue(summary);
    vi.mocked(listQuickAnalysisSessions).mockResolvedValue({
      items: [
        { analysis_session_id: 71, access_scope: "PERSONAL", source_root_code: "LOCAL_AGENT", status: "SUCCESS", parameter_count: 23, created_at_utc: "2026-09-01T00:00:00Z" },
        { analysis_session_id: 72, access_scope: "DOMAIN", data_domain_id: 9, data_domain_code: "SECRET_DOMAIN", source_root_code: "SECRET_ROOT", status: "SUCCESS", parameter_count: 23, created_at_utc: "2026-09-01T00:00:00Z" },
      ],
      total: 2,
      page: 1,
      page_size: 5,
    } as never);

    renderDashboard();

    expect(await screen.findByText("本机目录")).toBeInTheDocument();
    expect(screen.queryByText("SECRET_ROOT")).not.toBeInTheDocument();
    expect(screen.queryByText("SECRET_DOMAIN")).not.toBeInTheDocument();
  });

  it("selects an active grant from data-domains before loading DOMAIN summary", async () => {
    vi.mocked(listMyDataDomains).mockResolvedValue([{
      data_domain_id: 11,
      domain_code: "JIEQUN_FT",
      domain_name: "杰群 FT 源数据",
      test_stage: "FT",
      factory_code: "JIEQUN",
      active: true,
      grant_expires_at_utc: null,
      grants: [],
    }]);
    vi.mocked(getQualityManagementSummary).mockResolvedValue(summary);

    renderDashboard();
    fireEvent.click(screen.getByRole("tab", { name: /数据域/ }));

    expect((await screen.findAllByLabelText("选择数据域")).length).toBeGreaterThan(0);
    await waitFor(() => expect(getQualityManagementSummary).toHaveBeenCalledWith(expect.objectContaining({
      access_scope: "DOMAIN",
      data_domain_id: 11,
    })));
  });

  it("keeps unified CP and FT entries plus personal analysis tools", async () => {
    vi.mocked(listMyDataDomains).mockResolvedValue([]);
    vi.mocked(getQualityManagementSummary).mockResolvedValue(summary);
    const navigate = vi.fn();
    renderDashboard(navigate);

    fireEvent.click(screen.getByRole("button", { name: /FT 数据/ }));
    fireEvent.click(screen.getByRole("button", { name: /CP 数据/ }));
    fireEvent.click(screen.getByRole("button", { name: /个人分析工具/ }));
    expect(navigate.mock.calls).toEqual([
      ["/ft"],
      ["/cp"],
      ["/quick-analysis"],
    ]);
  });

  it("shows an honest empty state instead of filling missing metrics with demo values", async () => {
    vi.mocked(listMyDataDomains).mockResolvedValue([]);
    vi.mocked(getQualityManagementSummary).mockResolvedValue({
      ...summary,
      kpis: { ...summary.kpis, dataset_count: 0, total_units: 0 },
      trends: [],
      recent_datasets: [],
    });

    renderDashboard();

    expect(await screen.findByText("近30天没有归属于你的当前正式数据")).toBeInTheDocument();
    expect(screen.queryByText("98.73%")).not.toBeInTheDocument();
  });

  it("surfaces a summary failure without falling back to mixed or static data", async () => {
    vi.mocked(listMyDataDomains).mockResolvedValue([]);
    vi.mocked(getQualityManagementSummary).mockRejectedValue(new Error("质量摘要不可用"));

    renderDashboard();

    expect(await screen.findByText("统计数据暂时不可用")).toBeInTheDocument();
    expect(screen.getByText("质量摘要不可用")).toBeInTheDocument();
    expect(screen.queryByText("当前正式数据集")).not.toBeInTheDocument();
  });
});
