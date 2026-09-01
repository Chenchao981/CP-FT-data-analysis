// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useAuth } from "./features/auth/AuthContext";
import App from "./App";

interface MenuRoute {
  path?: string;
  name?: string;
  routes?: MenuRoute[];
}

vi.mock("@ant-design/pro-components", () => ({
  ProLayout: ({ route, menuItemRender, children }: { route: { routes?: MenuRoute[] }; menuItemRender: (item: MenuRoute, dom: ReactNode) => ReactNode; children: ReactNode }) => {
    const flatten = (items: MenuRoute[]): MenuRoute[] => items.flatMap((item) => [item, ...flatten(item.routes ?? [])]);
    return <div>
      <nav aria-label="main-menu">{flatten(route.routes ?? []).map((item) => <span key={item.path}>{menuItemRender(item, <span>{item.name}</span>)}</span>)}</nav>
      {children}
    </div>;
  },
  PageContainer: ({ children }: { children: ReactNode }) => <main>{children}</main>,
}));

vi.mock("./features/auth/AuthContext", () => ({ useAuth: vi.fn() }));
vi.mock("./features/auth/LoginPage", () => ({ LoginPage: () => <div>login</div> }));
vi.mock("./features/users/UserManagement", () => ({ UserManagement: () => <div>users</div> }));
vi.mock("./features/stage/StageDataWorkbench", () => ({
  StageDataWorkbench: ({ businessDomain, testStage }: { businessDomain: string; testStage: string }) => <div>{`stage:${businessDomain}/${testStage}`}</div>,
}));
vi.mock("./features/jobs/JobDetailsDrawer", () => ({
  JobDetailsDrawer: ({ jobId, open }: { jobId?: number; open: boolean }) => open ? <div>{`job-drawer:${jobId}`}</div> : null,
}));
vi.mock("./features/analytics/AnalyticsWorkbench", () => ({
  AnalyticsWorkbench: ({ datasets, searchParams, onSearchParamsChange, onOpenCatalog }: {
    datasets: Array<{ datasetId: number; versionNo: number }>;
    searchParams: URLSearchParams;
    onSearchParamsChange: (params: URLSearchParams) => void;
    onOpenCatalog: () => void;
  }) => <div>
    <span>{`analytics:${datasets.length ? datasets.map((item) => `${item.datasetId}/${item.versionNo}`).join(",") : "none"}`}</span>
    <span>{`analytics-search:${searchParams.toString()}`}</span>
    <button onClick={() => { const next = new URLSearchParams(searchParams); next.append("lot_id", "LOT-8"); next.set("page", "1"); onSearchParamsChange(next); }}>analytics-filter</button>
    <button onClick={onOpenCatalog}>analytics-catalog</button>
  </div>,
}));
vi.mock("./features/quick-analysis/QuickAnalysisWorkbench", () => ({ QuickAnalysisWorkbench: () => <div>quick-analysis</div> }));
vi.mock("./features/operations/OperationsConsistency", () => ({ OperationsConsistency: () => <div>operations</div> }));
vi.mock("./features/management/QualityManagementDashboard", () => ({
  QualityManagementDashboard: ({ searchParams, onSearchParamsChange, onOpenAnalytics, onOpenJob, canOpenAnalytics, canReadManagement, canGovernRules }: {
    searchParams: URLSearchParams;
    onSearchParamsChange: (params: URLSearchParams) => void;
    onOpenAnalytics: (datasetId: number, versionNo: number) => void;
    onOpenJob: (jobId: number) => void;
    canOpenAnalytics: boolean;
    canReadManagement: boolean;
    canGovernRules: boolean;
  }) => <div>
    <span>{`quality:${searchParams.toString()}:analytics-${canOpenAnalytics}`}</span>
    <span>{`quality-management-${canReadManagement}`}</span>
    <span>{`quality-govern-${canGovernRules}`}</span>
    <button onClick={() => onSearchParamsChange(new URLSearchParams({ test_stage: "FT", lot_id: "LOT-8" }))}>quality-filter</button>
    <button onClick={() => onOpenAnalytics(31, 2)}>quality-analytics</button>
    <button onClick={() => onOpenJob(96)}>quality-job</button>
  </div>,
}));
vi.mock("./features/master-data/ProductCrosswalkWorkbench", () => ({
  ProductCrosswalkWorkbench: ({ searchParams, onSearchParamsChange }: { searchParams: URLSearchParams; onSearchParamsChange: (params: URLSearchParams) => void }) => <div>
    <span>{`crosswalk:${searchParams.toString()}`}</span>
    <button onClick={() => onSearchParamsChange(new URLSearchParams({ status: "PENDING", page: "2" }))}>crosswalk-page</button>
  </div>,
}));
vi.mock("./features/datasets/DatasetCurrentCatalog", () => ({
  DatasetCurrentCatalog: ({ searchParams, onSearchParamsChange, onOpenAnalytics, onOpenComparison }: {
    searchParams: URLSearchParams;
    onSearchParamsChange: (params: URLSearchParams) => void;
    onOpenAnalytics: (datasetId: number, versionNo: number) => void;
    onOpenComparison?: (datasets: Array<{ datasetId: number; versionNo: number }>) => void;
  }) => <div>
    <span>{`catalog:${searchParams.toString()}`}</span>
    <button onClick={() => onSearchParamsChange(new URLSearchParams({ page: "3", product_name: "NCE-MOS" }))}>catalog-page-3</button>
    <button onClick={() => onOpenAnalytics(20, 3)}>catalog-analysis</button>
    <button onClick={() => onOpenComparison?.([{ datasetId: 20, versionNo: 3 }, { datasetId: 21, versionNo: 4 }])}>catalog-comparison</button>
  </div>,
}));
vi.mock("./features/dashboard/PersonalDashboard", () => ({
  PersonalDashboard: ({ userName, onNavigate }: { userName: string; onNavigate: (path: string) => void }) => <div>
    <span>{`dashboard:${userName}`}</span>
    <button onClick={() => onNavigate("/production/ft")}>dashboard-production-ft</button>
  </div>,
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

const authFor = (permissions: string[]) => ({
  user: {
    user_id: 1,
    login_name: "tester",
    display_name: "测试员",
    department_code: null,
    roles: ["TESTER"],
    permissions,
  },
  loading: false,
  login: vi.fn(),
  logout: vi.fn(),
  can: (permission: string) => permissions.includes(permission),
});

describe("App navigation and deep links", () => {
  beforeEach(() => {
    window.history.replaceState({}, "", "/");
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it.each([
    [["DATASET_READ"], "/dashboard", "dashboard:测试员"],
    [["MANAGEMENT_READ"], "/management/quality", "quality::analytics-false"],
    [["AUDIT_READ"], "/operations", "operations"],
  ])("redirects the root route to the first permitted leaf for %j", async (permissions, expectedPath, expectedPage) => {
    vi.mocked(useAuth).mockReturnValue(authFor(permissions));

    render(<App />);

    expect(await screen.findByText(expectedPage)).toBeInTheDocument();
    await waitFor(() => expect(window.location.pathname).toBe(expectedPath));
  }, 15_000);

  it("opens the personal cockpit and follows its fixed-route shortcuts", async () => {
    vi.mocked(useAuth).mockReturnValue(authFor(["DATASET_READ"]));
    window.history.replaceState({}, "", "/dashboard");

    render(<App />);

    expect(await screen.findByText("dashboard:测试员")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "个人驾驶舱" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "dashboard-production-ft" }));
    expect(await screen.findByText("stage:PRODUCTION/FT")).toBeInTheDocument();
    expect(window.location.pathname).toBe("/production/ft");
  }, 15_000);

  it.each([
    ["/engineering", "/engineering/cp", "stage:ENGINEERING/CP"],
    ["/production", "/production/cp", "stage:PRODUCTION/CP"],
  ])("redirects the permitted parent route %s to its first leaf", async (path, expectedPath, expectedPage) => {
    vi.mocked(useAuth).mockReturnValue(authFor(["DATASET_READ"]));
    window.history.replaceState({}, "", path);

    render(<App />);

    expect(await screen.findByText(expectedPage)).toBeInTheDocument();
    await waitFor(() => expect(window.location.pathname).toBe(expectedPath));
  }, 15_000);

  it("shows Dataset Current by permission and preserves catalog URL filters", async () => {
    vi.mocked(useAuth).mockReturnValue(authFor(["DATASET_READ"]));
    window.history.replaceState({}, "", "/datasets/current?page=2&page_size=50&product_name=NCE-IGBT");

    render(<App />);

    expect(await screen.findByText("catalog:page=2&page_size=50&product_name=NCE-IGBT")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "历史正式数据" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "运行一致性" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "catalog-page-3" }));
    await waitFor(() => expect(window.location.pathname).toBe("/datasets/current"));
    expect(window.location.search).toBe("?page=3&product_name=NCE-MOS");
    expect(await screen.findByText("catalog:page=3&product_name=NCE-MOS")).toBeInTheDocument();
  }, 15_000);

  it("restores analytics selection and Job drawer, then follows browser history to a fixed entry", async () => {
    vi.mocked(useAuth).mockReturnValue(authFor(["DATASET_READ", "ANALYSIS_RUN"]));
    window.history.replaceState({}, "", "/analytics?dataset_id=20&version_no=3&job_id=91");

    render(<App />);

    expect(await screen.findByText("analytics:20/3")).toBeInTheDocument();
    expect(screen.getByText("job-drawer:91")).toBeInTheDocument();

    window.history.pushState({}, "", "/production/ft?job_id=92");
    window.dispatchEvent(new PopStateEvent("popstate"));
    expect(await screen.findByText("stage:PRODUCTION/FT")).toBeInTheDocument();
    expect(screen.getByText("job-drawer:92")).toBeInTheDocument();
  }, 15_000);

  it("allows DATASET_READ to open analytics without ANALYSIS_RUN", async () => {
    vi.mocked(useAuth).mockReturnValue(authFor(["DATASET_READ"]));
    window.history.replaceState({}, "", "/analytics?dataset_id=20&version_no=3");

    render(<App />);

    expect(await screen.findByText("analytics:20/3")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "分析图表" })).not.toBeInTheDocument();
  }, 15_000);

  it("opens one or multiple catalog selections in analytics and keeps URL-driven filters", async () => {
    vi.mocked(useAuth).mockReturnValue(authFor(["DATASET_READ"]));
    window.history.replaceState({}, "", "/datasets/current");

    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: "catalog-comparison" }));
    expect(await screen.findByText("analytics:20/3,21/4")).toBeInTheDocument();
    expect(window.location.search).toBe("?dataset=20%3A3&dataset=21%3A4");

    fireEvent.click(screen.getByRole("button", { name: "analytics-filter" }));
    await waitFor(() => expect(window.location.search).toContain("lot_id=LOT-8"));
    expect(window.location.search).toContain("dataset=20%3A3&dataset=21%3A4");
    expect(await screen.findByText(/analytics-search:.*lot_id=LOT-8/)).toBeInTheDocument();
  }, 15_000);

  it("caps a modern analytics deep link at eight unique Dataset selections", async () => {
    vi.mocked(useAuth).mockReturnValue(authFor(["DATASET_READ"]));
    const params = new URLSearchParams();
    for (let datasetId = 1; datasetId <= 9; datasetId += 1) params.append("dataset", `${datasetId}:1`);
    window.history.replaceState({}, "", `/analytics?${params}`);

    render(<App />);

    expect(await screen.findByText("analytics:1/1,2/1,3/1,4/1,5/1,6/1,7/1,8/1")).toBeInTheDocument();
    expect(screen.queryByText(/9\/1/)).not.toBeInTheDocument();
  }, 15_000);

  it("keeps an explicit analytics URL forbidden without DATASET_READ", async () => {
    vi.mocked(useAuth).mockReturnValue(authFor(["ANALYSIS_RUN"]));
    window.history.replaceState({}, "", "/analytics?dataset_id=20&version_no=3");

    render(<App />);

    expect(await screen.findByText("无权访问")).toBeInTheDocument();
    expect(screen.queryByText(/^analytics:/)).not.toBeInTheDocument();
    expect(window.location.pathname).toBe("/analytics");
  }, 15_000);

  it("removes the standalone capability route from the product navigation", async () => {
    vi.mocked(useAuth).mockReturnValue(authFor(["DATASET_READ"]));
    window.history.replaceState({}, "", "/capabilities");

    render(<App />);

    expect(await screen.findByText("无权访问")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "能力与定制工具" })).not.toBeInTheDocument();
    expect(window.location.pathname).toBe("/capabilities");
  }, 15_000);

  it.each([
    ["/engineering/cp", "stage:ENGINEERING/CP"],
    ["/engineering/ft", "stage:ENGINEERING/FT"],
    ["/production/cp", "stage:PRODUCTION/CP"],
    ["/production/ft", "stage:PRODUCTION/FT"],
  ])("restores the fixed entry deep link %s on refresh", async (path, expected) => {
    vi.mocked(useAuth).mockReturnValue(authFor(["DATASET_READ"]));
    window.history.replaceState({}, "", path);

    render(<App />);

    expect(await screen.findByText(expected)).toBeInTheDocument();
    expect(window.location.pathname).toBe(path);
  }, 15_000);

  it("exposes operations only to AUDIT_READ users", async () => {
    vi.mocked(useAuth).mockReturnValue(authFor(["AUDIT_READ"]));
    window.history.replaceState({}, "", "/operations");

    render(<App />);

    expect(await screen.findByText("operations")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "运行一致性" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Dataset Current" })).not.toBeInTheDocument();
  }, 15_000);

  it("exposes quality management and Crosswalk to MANAGEMENT_READ and preserves deep links", async () => {
    vi.mocked(useAuth).mockReturnValue(authFor(["MANAGEMENT_READ", "DATASET_READ"]));
    window.history.replaceState({}, "", "/management/quality?from_utc=2026-08-01T00%3A00%3A00Z&product_name=NCE-MOS");

    render(<App />);

    expect(await screen.findByText("quality:from_utc=2026-08-01T00%3A00%3A00Z&product_name=NCE-MOS:analytics-true")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "质量管理摘要" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "产品 Crosswalk" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "运行一致性" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "quality-filter" }));
    await waitFor(() => expect(window.location.pathname).toBe("/management/quality"));
    expect(window.location.search).toBe("?test_stage=FT&lot_id=LOT-8");
    expect(await screen.findByText("quality:test_stage=FT&lot_id=LOT-8:analytics-true")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "quality-job" }));
    expect(await screen.findByText("job-drawer:96")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "quality-analytics" }));
    expect(await screen.findByText("analytics:31/2")).toBeInTheDocument();
    expect(window.location.pathname).toBe("/analytics");
  }, 15_000);

  it("allows RULE_GOVERN to manage Crosswalk and Rule Registry without requesting the management summary", async () => {
    vi.mocked(useAuth).mockReturnValue(authFor(["RULE_GOVERN"]));
    window.history.replaceState({}, "", "/master-data/product-crosswalks?status=PENDING&page=1");

    render(<App />);

    expect(await screen.findByText("crosswalk:status=PENDING&page=1")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "产品 Crosswalk" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "质量管理摘要" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "crosswalk-page" }));
    await waitFor(() => expect(window.location.search).toBe("?status=PENDING&page=2"));
    expect(await screen.findByText("crosswalk:status=PENDING&page=2")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("link", { name: "质量管理摘要" }));
    expect(await screen.findByText("quality-govern-true")).toBeInTheDocument();
    expect(screen.getByText("quality-management-false")).toBeInTheDocument();
  }, 15_000);

  it("fails closed for a management deep link without MANAGEMENT_READ", async () => {
    vi.mocked(useAuth).mockReturnValue(authFor(["DATASET_READ"]));
    window.history.replaceState({}, "", "/management/quality");

    render(<App />);

    expect(await screen.findByText("无权访问")).toBeInTheDocument();
    expect(screen.queryByText(/^quality:/)).not.toBeInTheDocument();
  }, 15_000);
});
