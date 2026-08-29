import { ApartmentOutlined, DatabaseOutlined, DashboardOutlined, ExperimentOutlined, LinkOutlined, LogoutOutlined, PlayCircleOutlined, ProfileOutlined, SafetyCertificateOutlined, ThunderboltOutlined, UserOutlined } from "@ant-design/icons";
import { PageContainer, ProLayout } from "@ant-design/pro-components";
import { Avatar, Dropdown, Result, Spin, Typography } from "antd";
import { lazy, Suspense, useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { LoginPage } from "./features/auth/LoginPage";
import { useAuth } from "./features/auth/AuthContext";
import { UserManagement } from "./features/users/UserManagement";
import { StageDataWorkbench } from "./features/stage/StageDataWorkbench";
import { JobDetailsDrawer } from "./features/jobs/JobDetailsDrawer";
import type { PermissionCode } from "./api/auth";
import "./styles.css";

const AnalyticsWorkbench = lazy(() => import("./features/analytics/AnalyticsWorkbench").then((module) => ({ default: module.AnalyticsWorkbench })));
const QuickAnalysisWorkbench = lazy(() => import("./features/quick-analysis/QuickAnalysisWorkbench").then((module) => ({ default: module.QuickAnalysisWorkbench })));
const OperationsConsistency = lazy(() => import("./features/operations/OperationsConsistency").then((module) => ({ default: module.OperationsConsistency })));
const DatasetCurrentCatalog = lazy(() => import("./features/datasets/DatasetCurrentCatalog").then((module) => ({ default: module.DatasetCurrentCatalog })));
const QualityManagementDashboard = lazy(() => import("./features/management/QualityManagementDashboard").then((module) => ({ default: module.QualityManagementDashboard })));
const ProductCrosswalkWorkbench = lazy(() => import("./features/master-data/ProductCrosswalkWorkbench").then((module) => ({ default: module.ProductCrosswalkWorkbench })));

type RoutePermission = PermissionCode | readonly PermissionCode[];
interface AppRoute {
  path: string;
  name: string;
  icon: ReactNode;
  permission: RoutePermission;
  routes?: AppRoute[];
}

const routes: AppRoute[] = [
  { path: "/engineering", name: "工程数据", icon: <ApartmentOutlined />, permission: "DATASET_READ", routes: [
    { path: "/engineering/cp", name: "CP数据", icon: <ExperimentOutlined />, permission: "DATASET_READ" },
    { path: "/engineering/ft", name: "FT数据", icon: <ThunderboltOutlined />, permission: "DATASET_READ" },
  ] },
  { path: "/production", name: "量产数据", icon: <DatabaseOutlined />, permission: "DATASET_READ", routes: [
    { path: "/production/cp", name: "CP数据", icon: <ExperimentOutlined />, permission: "DATASET_READ" },
    { path: "/production/ft", name: "FT数据", icon: <ThunderboltOutlined />, permission: "DATASET_READ" },
  ] },
  { path: "/quick-analysis", name: "快速分析", icon: <PlayCircleOutlined />, permission: "ANALYSIS_RUN" },
  { path: "/datasets/current", name: "历史正式数据", icon: <ProfileOutlined />, permission: "DATASET_READ" },
  { path: "/management/quality", name: "质量管理摘要", icon: <DashboardOutlined />, permission: "MANAGEMENT_READ" },
  { path: "/master-data/product-crosswalks", name: "产品 Crosswalk", icon: <LinkOutlined />, permission: ["MANAGEMENT_READ", "RULE_GOVERN"] },
  { path: "/operations", name: "运行一致性", icon: <SafetyCertificateOutlined />, permission: "AUDIT_READ" },
  { path: "/users", name: "用户与权限", icon: <UserOutlined />, permission: "USER_ADMIN" },
];

const readBrowserLocation = () => ({
  pathname: window.location.pathname || "/",
  search: window.location.search,
});

const positiveQueryInt = (params: URLSearchParams, key: string) => {
  const value = Number(params.get(key));
  return Number.isSafeInteger(value) && value > 0 ? value : undefined;
};

interface AnalyticsDatasetSelection { datasetId: number; versionNo: number }

const analyticsDatasetKey = (selection: AnalyticsDatasetSelection) => `${selection.datasetId}:${selection.versionNo}`;
const parseAnalyticsDatasets = (params: URLSearchParams): AnalyticsDatasetSelection[] => {
  const selected = params.getAll("dataset").flatMap((value) => {
    const match = /^(\d+):(\d+)$/.exec(value.trim());
    if (!match) return [];
    const datasetId = Number(match[1]);
    const versionNo = Number(match[2]);
    return Number.isSafeInteger(datasetId) && datasetId > 0 && Number.isSafeInteger(versionNo) && versionNo > 0 ? [{ datasetId, versionNo }] : [];
  });
  if (!selected.length) {
    const datasetId = positiveQueryInt(params, "dataset_id");
    const versionNo = positiveQueryInt(params, "version_no");
    if (datasetId && versionNo) selected.push({ datasetId, versionNo });
  }
  return selected
    .filter((item, index, items) => items.findIndex((candidate) => candidate.datasetId === item.datasetId) === index)
    .slice(0, 8);
};

const analyticsSearch = (datasets: AnalyticsDatasetSelection[]) => {
  const params = new URLSearchParams();
  const valid = datasets
    .filter((item) => Number.isSafeInteger(item.datasetId) && item.datasetId > 0 && Number.isSafeInteger(item.versionNo) && item.versionNo > 0)
    .filter((item, index, items) => items.findIndex((candidate) => candidate.datasetId === item.datasetId) === index)
    .slice(0, 8);
  for (const dataset of valid) params.append("dataset", analyticsDatasetKey(dataset));
  return params;
};

export default function App() {
  const { user, loading, logout, can } = useAuth();
  const routeAllowed = (permission: RoutePermission) => typeof permission === "string"
    ? can(permission)
    : permission.some((item) => can(item));
  const permittedRoutes = useMemo(() => routes.filter((route) => routeAllowed(route.permission)).map((route) => ({ ...route, routes: route.routes?.filter((child) => routeAllowed(child.permission)) })), [user, can]);
  const [browserLocation, setBrowserLocation] = useState(readBrowserLocation);
  useEffect(() => {
    const onPopState = () => setBrowserLocation(readBrowserLocation());
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);
  const searchParams = useMemo(() => new URLSearchParams(browserLocation.search), [browserLocation.search]);
  const navigate = useCallback((path: string, params = new URLSearchParams(), replace = false) => {
    const url = `${path}${params.size ? `?${params.toString()}` : ""}`;
    window.history[replace ? "replaceState" : "pushState"]({}, "", url);
    setBrowserLocation({ pathname: path, search: params.size ? `?${params.toString()}` : "" });
  }, []);
  const openAnalytics = (datasetId: number, versionNo: number) => {
    navigate("/analytics", analyticsSearch([{ datasetId, versionNo }]));
  };
  const openComparison = (datasets: AnalyticsDatasetSelection[]) => navigate("/analytics", analyticsSearch(datasets));
  const openJob = (jobId: number) => {
    const next = new URLSearchParams(searchParams);
    next.set("job_id", String(jobId));
    navigate(browserLocation.pathname, next);
  };
  const closeJob = () => {
    const next = new URLSearchParams(searchParams);
    next.delete("job_id");
    navigate(browserLocation.pathname, next, true);
  };
  const visibleLeafPaths = permittedRoutes.flatMap((route) => route.routes?.length
    ? route.routes.map((child) => child.path)
    : [route.path]);
  const parentDefaultPath = permittedRoutes
    .find((route) => route.path === browserLocation.pathname)
    ?.routes?.[0]?.path;
  const redirectPath = browserLocation.pathname === "/"
    ? visibleLeafPaths[0]
    : parentDefaultPath;
  useEffect(() => {
    if (!loading && user && redirectPath && redirectPath !== browserLocation.pathname) {
      navigate(redirectPath, new URLSearchParams(browserLocation.search), true);
    }
  }, [browserLocation.pathname, browserLocation.search, loading, navigate, redirectPath, user]);
  if (loading) return <div className="page-loading fullscreen"><Spin size="large" /></div>;
  if (!user) return <LoginPage />;
  const resolvedPath = redirectPath ?? browserLocation.pathname;
  const hiddenAnalyticsAllowed = resolvedPath === "/analytics" && can("DATASET_READ");
  const activePage = visibleLeafPaths.includes(resolvedPath) || hiddenAnalyticsAllowed ? resolvedPath : "/forbidden";
  const analyticsDatasets = parseAnalyticsDatasets(searchParams);
  const jobId = positiveQueryInt(searchParams, "job_id");
  return <ProLayout
    title="TMS"
    logo={<div className="brand-mark">T</div>}
    layout="mix"
    fixedHeader
    fixSiderbar
    location={{ pathname: activePage }}
    route={{ routes: permittedRoutes }}
    menuItemRender={(item, dom) => <a href={String(item.path || "/")} onClick={(event) => { event.preventDefault(); if (item.path) navigate(String(item.path)); }}>{dom}</a>}
    avatarProps={{ src: <Avatar>{user.display_name.slice(0, 1)}</Avatar>, title: user.display_name, render: (_, dom) => <Dropdown menu={{ items: [
      { key: "profile", label: <span>{user.login_name}<br/><Typography.Text type="secondary">{user.roles.join("、") || "未分配角色"}</Typography.Text></span>, disabled: true },
      { type: "divider" }, { key: "logout", icon: <LogoutOutlined />, label: "退出登录", onClick: () => void logout() },
    ] }}>{dom}</Dropdown> }}
    actionsRender={() => []}
    token={{ sider: { colorMenuBackground: "#082f52", colorTextMenu: "#c8d8e5", colorTextMenuSelected: "#ffffff", colorBgMenuItemSelected: "#1167a8" } }}
  ><PageContainer title={false} className="app-content">
    {activePage === "/engineering/cp" ? <StageDataWorkbench businessDomain="ENGINEERING" testStage="CP" searchParams={searchParams} onSearchParamsChange={(params) => navigate("/engineering/cp", params)} onOpenAnalytics={openAnalytics} onOpenJob={openJob} /> : activePage === "/engineering/ft" ? <StageDataWorkbench businessDomain="ENGINEERING" testStage="FT" searchParams={searchParams} onSearchParamsChange={(params) => navigate("/engineering/ft", params)} onOpenAnalytics={openAnalytics} onOpenJob={openJob} /> : activePage === "/production/cp" ? <StageDataWorkbench businessDomain="PRODUCTION" testStage="CP" searchParams={searchParams} onSearchParamsChange={(params) => navigate("/production/cp", params)} onOpenAnalytics={openAnalytics} onOpenJob={openJob} /> : activePage === "/production/ft" ? <StageDataWorkbench businessDomain="PRODUCTION" testStage="FT" searchParams={searchParams} onSearchParamsChange={(params) => navigate("/production/ft", params)} onOpenAnalytics={openAnalytics} onOpenJob={openJob} /> : activePage === "/quick-analysis" ? <Suspense fallback={<div className="page-loading"><Spin size="large" /></div>}><QuickAnalysisWorkbench /></Suspense> : activePage === "/datasets/current" ? <Suspense fallback={<div className="page-loading"><Spin size="large" /></div>}><DatasetCurrentCatalog searchParams={searchParams} onSearchParamsChange={(params) => navigate("/datasets/current", params)} onOpenAnalytics={openAnalytics} onOpenComparison={openComparison} onOpenJob={openJob} /></Suspense> : activePage === "/analytics" ? <Suspense fallback={<div className="page-loading"><Spin size="large" /></div>}><AnalyticsWorkbench datasets={analyticsDatasets} searchParams={searchParams} onSearchParamsChange={(params) => navigate("/analytics", params)} onOpenCatalog={() => navigate("/datasets/current")} /></Suspense> : activePage === "/management/quality" ? <Suspense fallback={<div className="page-loading"><Spin size="large" /></div>}><QualityManagementDashboard searchParams={searchParams} onSearchParamsChange={(params) => navigate("/management/quality", params)} onOpenAnalytics={openAnalytics} onOpenJob={openJob} canOpenAnalytics={can("DATASET_READ")} /></Suspense> : activePage === "/master-data/product-crosswalks" ? <Suspense fallback={<div className="page-loading"><Spin size="large" /></div>}><ProductCrosswalkWorkbench searchParams={searchParams} onSearchParamsChange={(params) => navigate("/master-data/product-crosswalks", params)} /></Suspense> : activePage === "/operations" ? <Suspense fallback={<div className="page-loading"><Spin size="large" /></div>}><OperationsConsistency /></Suspense> : activePage === "/users" ? <UserManagement /> : <Result status="403" title="无权访问" subTitle="当前 URL 对应的功能不存在，或当前账户没有访问权限。" />}
  </PageContainer><JobDetailsDrawer jobId={jobId} open={jobId !== undefined} onClose={closeJob} onSelectJob={openJob} onOpenAnalytics={openAnalytics} /></ProLayout>;
}
