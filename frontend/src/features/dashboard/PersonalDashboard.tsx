import {
  ArrowRightOutlined,
  CheckCircleOutlined,
  CloudServerOutlined,
  DatabaseOutlined,
  ExperimentOutlined,
  RadarChartOutlined,
  SafetyCertificateOutlined,
  ThunderboltOutlined,
} from "@ant-design/icons";
import { Button, Card, Col, Progress, Row, Space, Tag, Typography } from "antd";
import type { EChartsOption } from "echarts";
import { useMemo } from "react";

import { EChart } from "../../components/EChart";

export interface PersonalDashboardProps {
  userName: string;
  onNavigate: (path: string) => void;
  canOpenQuality?: boolean;
  canRunQuickAnalysis?: boolean;
}

const kpis = [
  { label: "正式数据集", value: "128", delta: "+6 本周", tone: "cyan", icon: <DatabaseOutlined /> },
  { label: "今日处理单元", value: "248,630", delta: "+12.4%", tone: "blue", icon: <ThunderboltOutlined /> },
  { label: "已知良率", value: "98.73%", delta: "+0.18 pp", tone: "green", icon: <CheckCircleOutlined /> },
  { label: "待处理门禁", value: "3", delta: "需关注", tone: "amber", icon: <SafetyCertificateOutlined /> },
] as const;

const factoryReadiness = [
  { name: "华虹 CP", value: 96, color: "#45d6b5" },
  { name: "日月新 FT", value: 92, color: "#48a9ff" },
  { name: "日月光 FT", value: 88, color: "#8b7cff" },
  { name: "电基 FT", value: 84, color: "#ffb454" },
] as const;

export function PersonalDashboard({
  userName,
  onNavigate,
  canOpenQuality = false,
  canRunQuickAnalysis = false,
}: PersonalDashboardProps) {
  const trendOption = useMemo<EChartsOption>(() => ({
    color: ["#42d7c5", "#5a9cff", "#786cff"],
    tooltip: { trigger: "axis", backgroundColor: "rgba(8,22,43,.94)", borderColor: "#294865", textStyle: { color: "#eef8ff" } },
    legend: { data: ["CP 已知良率", "FT 已知良率", "处理单元"], textStyle: { color: "#91abc0" }, right: 4 },
    grid: { left: 48, right: 54, top: 58, bottom: 36 },
    xAxis: { type: "category", data: ["08/26", "08/27", "08/28", "08/29", "08/30", "08/31", "09/01"], axisLine: { lineStyle: { color: "#29445c" } }, axisLabel: { color: "#7894aa" } },
    yAxis: [
      { type: "value", min: 96, max: 100, axisLabel: { color: "#7894aa", formatter: "{value}%" }, splitLine: { lineStyle: { color: "rgba(91,126,151,.16)" } } },
      { type: "value", axisLabel: { color: "#7894aa", formatter: (value: number) => `${Math.round(value / 1000)}k` }, splitLine: { show: false } },
    ],
    series: [
      { name: "CP 已知良率", type: "line", smooth: true, symbolSize: 7, areaStyle: { opacity: 0.08 }, data: [98.1, 98.35, 98.22, 98.62, 98.55, 98.84, 98.91] },
      { name: "FT 已知良率", type: "line", smooth: true, symbolSize: 7, areaStyle: { opacity: 0.06 }, data: [97.82, 97.94, 98.02, 98.1, 98.36, 98.41, 98.57] },
      { name: "处理单元", type: "bar", yAxisIndex: 1, barMaxWidth: 22, itemStyle: { opacity: 0.28, borderRadius: [5, 5, 0, 0] }, data: [168200, 192600, 183900, 221400, 207800, 236100, 248630] },
    ],
  }), []);

  return <div className="personal-dashboard workbench">
    <section className="dashboard-hero">
      <div className="dashboard-hero-copy">
        <Space size={8} wrap><Tag color="cyan">PERSONAL COCKPIT</Tag><Tag className="demo-tag">演示数据 · 未连接生产</Tag></Space>
        <Typography.Title level={1}>早上好，{userName}</Typography.Title>
        <p className="dashboard-hero-description">从 CP、FT 到质量治理，把今天最需要关注的变化放在一个视图里。</p>
        <Space wrap>
          <Button type="primary" size="large" icon={<RadarChartOutlined />} disabled={!canOpenQuality} onClick={() => onNavigate("/management/quality")}>进入质量总览</Button>
          <Button ghost size="large" onClick={() => onNavigate("/datasets/current")}>查看正式数据 <ArrowRightOutlined /></Button>
        </Space>
      </div>
      <div className="dashboard-wafer" aria-label="半导体晶圆状态示意图">
        <div className="wafer-orbit orbit-one" />
        <div className="wafer-orbit orbit-two" />
        <div className="wafer-core"><span>98.73%</span><small>KNOWN YIELD</small></div>
        <div className="wafer-node node-a" /><div className="wafer-node node-b" /><div className="wafer-node node-c" />
      </div>
    </section>

    <Row gutter={[16, 16]} className="dashboard-kpis">
      {kpis.map((item) => <Col xs={24} sm={12} xl={6} key={item.label}>
        <Card className={`cockpit-card kpi-card kpi-${item.tone}`}>
          <div className="kpi-icon">{item.icon}</div>
          <span>{item.label}</span><strong>{item.value}</strong><em>{item.delta}</em>
        </Card>
      </Col>)}
    </Row>

    <Row gutter={[16, 16]}>
      <Col xs={24} xl={16}>
        <Card className="cockpit-card chart-panel" title={<span>制造质量脉冲 <small>近 7 天 Demo</small></span>} extra={<Tag color="processing">分钟级视图构想</Tag>}>
          <EChart option={trendOption} className="dashboard-trend-chart" ariaLabel="CP FT 良率与处理量演示趋势" />
        </Card>
      </Col>
      <Col xs={24} xl={8}>
        <Card className="cockpit-card readiness-panel" title="数据链路就绪度" extra={<CloudServerOutlined />}>
          {factoryReadiness.map((item) => <div className="readiness-row" key={item.name}>
            <div><span>{item.name}</span><b>{item.value}%</b></div>
            <Progress percent={item.value} showInfo={false} strokeColor={item.color} trailColor="rgba(125,157,180,.14)" />
          </div>)}
          <Typography.Text className="demo-footnote">演示口径：Release、Golden、数据新鲜度和最近任务状态的综合示意。</Typography.Text>
        </Card>
      </Col>
    </Row>

    <Row gutter={[16, 16]}>
      <Col xs={24} lg={14}>
        <Card className="cockpit-card attention-panel" title="我的今日关注" extra={<Tag color="warning">3 项</Tag>}>
          <div className="attention-item"><span className="attention-index">01</span><div><strong>电基 FT 动态参数 Golden 待补齐</strong><small>需要一组同产品“旧列 + 右侧新增列”的真实样本</small></div><Tag color="gold">待业务协同</Tag></div>
          <div className="attention-item"><span className="attention-index">02</span><div><strong>前端全量测试存在 7 个超时项</strong><small>功能定向已通过，仍需稳定 CI 与人工联合复测</small></div><Tag color="orange">测试门禁</Tag></div>
          <div className="attention-item"><span className="attention-index">03</span><div><strong>PASS / FAIL 与 Bin 口径待签字</strong><small>未批准前保持 UNKNOWN，良率不做猜测</small></div><Tag color="red">规则门禁</Tag></div>
        </Card>
      </Col>
      <Col xs={24} lg={10}>
        <Card className="cockpit-card quick-entry-panel" title="快速进入">
          <button type="button" onClick={() => onNavigate("/engineering/cp")}><ExperimentOutlined /><span><b>工程 CP</b><small>Wafer 清洗与分析</small></span><ArrowRightOutlined /></button>
          <button type="button" onClick={() => onNavigate("/production/ft")}><ThunderboltOutlined /><span><b>量产 FT</b><small>Lot 清洗与参数分析</small></span><ArrowRightOutlined /></button>
          <button type="button" disabled={!canRunQuickAnalysis} onClick={() => onNavigate("/quick-analysis")}><RadarChartOutlined /><span><b>快速分析</b><small>临时 PAT Workspace</small></span><ArrowRightOutlined /></button>
        </Card>
      </Col>
    </Row>
  </div>;
}

export default PersonalDashboard;
