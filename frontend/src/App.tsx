import { ApartmentOutlined, BarChartOutlined, DatabaseOutlined, ExperimentOutlined, LogoutOutlined, SettingOutlined, ThunderboltOutlined, UserOutlined } from "@ant-design/icons";
import { PageContainer, ProLayout } from "@ant-design/pro-components";
import { Avatar, Button, Dropdown, Result, Spin, Typography } from "antd";
import { lazy, Suspense, useMemo, useState } from "react";
import { LoginPage } from "./features/auth/LoginPage";
import { useAuth } from "./features/auth/AuthContext";
import { UserManagement } from "./features/users/UserManagement";
import { StageDataWorkbench } from "./features/stage/StageDataWorkbench";
import "./styles.css";

const AnalyticsWorkbench = lazy(() => import("./features/analytics/AnalyticsWorkbench").then((module) => ({ default: module.AnalyticsWorkbench })));
const routes = [
  { path: "/engineering", name: "工程数据", icon: <ApartmentOutlined />, permission: "DATASET_READ", routes: [
    { path: "/engineering/cp", name: "CP数据", icon: <ExperimentOutlined />, permission: "DATASET_READ" },
    { path: "/engineering/ft", name: "FT数据", icon: <ThunderboltOutlined />, permission: "DATASET_READ" },
  ] },
  { path: "/production", name: "量产数据", icon: <DatabaseOutlined />, permission: "DATASET_READ", routes: [
    { path: "/production/cp", name: "CP数据", icon: <ExperimentOutlined />, permission: "DATASET_READ" },
    { path: "/production/ft", name: "FT数据", icon: <ThunderboltOutlined />, permission: "DATASET_READ" },
  ] },
  { path: "/analytics", name: "分析图表", icon: <BarChartOutlined />, permission: "ANALYSIS_RUN" },
  { path: "/users", name: "用户与权限", icon: <UserOutlined />, permission: "USER_ADMIN" },
  { path: "/governance", name: "规则治理", icon: <SettingOutlined />, permission: "RULE_GOVERN", disabled: true },
];

export default function App() {
  const { user, loading, logout, can } = useAuth();
  const permittedRoutes = useMemo(() => routes.filter((route) => can(route.permission)).map((route) => ({ ...route, routes: route.routes?.filter((child) => can(child.permission)) })), [user, can]);
  const [page, setPage] = useState("/production/cp");
  if (loading) return <div className="page-loading fullscreen"><Spin size="large" /></div>;
  if (!user) return <LoginPage />;
  const visiblePaths = permittedRoutes.flatMap((route) => [route.path, ...(route.routes?.map((child) => child.path) ?? [])]);
  const activePage = visiblePaths.includes(page) ? page : permittedRoutes[0]?.path ?? "/forbidden";
  return <ProLayout
    title="TMS"
    logo={<div className="brand-mark">T</div>}
    layout="mix"
    fixedHeader
    fixSiderbar
    location={{ pathname: activePage }}
    route={{ routes: permittedRoutes }}
    menuItemRender={(item, dom) => <a onClick={() => item.path && setPage(String(item.path))}>{dom}</a>}
    avatarProps={{ src: <Avatar>{user.display_name.slice(0, 1)}</Avatar>, title: user.display_name, render: (_, dom) => <Dropdown menu={{ items: [
      { key: "profile", label: <span>{user.login_name}<br/><Typography.Text type="secondary">{user.roles.join("、") || "未分配角色"}</Typography.Text></span>, disabled: true },
      { type: "divider" }, { key: "logout", icon: <LogoutOutlined />, label: "退出登录", onClick: () => void logout() },
    ] }}>{dom}</Dropdown> }}
    actionsRender={() => [<Button key="env" type="text">开发环境</Button>]}
    token={{ sider: { colorMenuBackground: "#082f52", colorTextMenu: "#c8d8e5", colorTextMenuSelected: "#ffffff", colorBgMenuItemSelected: "#1167a8" } }}
  ><PageContainer title={false} className="app-content">
    {activePage === "/engineering/cp" ? <StageDataWorkbench businessDomain="ENGINEERING" testStage="CP" /> : activePage === "/engineering/ft" ? <StageDataWorkbench businessDomain="ENGINEERING" testStage="FT" /> : activePage === "/production/cp" ? <StageDataWorkbench businessDomain="PRODUCTION" testStage="CP" /> : activePage === "/production/ft" ? <StageDataWorkbench businessDomain="PRODUCTION" testStage="FT" /> : activePage === "/analytics" ? <Suspense fallback={<div className="page-loading"><Spin size="large" /></div>}><AnalyticsWorkbench /></Suspense> : activePage === "/users" ? <UserManagement /> : <Result status="403" title="无权访问" subTitle="当前账户没有此功能权限。" />}
  </PageContainer></ProLayout>;
}
