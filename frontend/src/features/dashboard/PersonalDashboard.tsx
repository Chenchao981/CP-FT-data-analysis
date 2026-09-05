import {
  ArrowRightOutlined,
  CloudServerOutlined,
  ExperimentOutlined,
  RadarChartOutlined,
  ThunderboltOutlined,
  UserOutlined,
} from "@ant-design/icons";
import { useQuery } from "@tanstack/react-query";
import { Alert, Button, Card, Col, Empty, Row, Select, Space, Spin, Table, Tabs, Tag, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import type { EChartsOption } from "echarts";
import { useEffect, useMemo, useState } from "react";

import { listMyDataDomains, type DataDomain } from "../../api/dataDomains";
import {
  getQualityManagementSummary,
  type QualityDatasetDrilldown,
  type QualityManagementSummary,
} from "../../api/management";
import { listQuickAnalysisSessions, type QuickAnalysisSession } from "../../api/quickAnalysis";
import { EChart } from "../../components/EChart";
import { MetricStrip } from "../../components/MetricStrip";
import { formatShanghaiDate, formatUtcDateTime } from "../../utils/dateTime";

export interface PersonalDashboardProps {
  userName: string;
  onNavigate: (path: string) => void;
  canOpenQuality?: boolean;
  canRunQuickAnalysis?: boolean;
}

type DashboardScope = "PERSONAL" | "DOMAIN";

const percent = (value: number | null | undefined) => value == null ? "—" : `${(value * 100).toFixed(2)}%`;
const count = (value: number | null | undefined) => value == null ? "—" : value.toLocaleString("zh-CN");

function SummaryView({ data, scope, onNavigate }: { data: QualityManagementSummary; scope: DashboardScope; onNavigate: (path: string) => void }) {
  const trendOption = useMemo<EChartsOption>(() => ({
    color: ["#1677ff", "#d46b08", "#91a4b7"],
    tooltip: { trigger: "axis" },
    legend: { data: ["已知良率", "UNKNOWN 占比", "总单元"], textStyle: { color: "#5c6b78" }, right: 4 },
    grid: { left: 58, right: 64, top: 58, bottom: 44 },
    xAxis: { type: "category", data: data.trends.map((item) => formatShanghaiDate(item.period_start_utc)), axisLine: { lineStyle: { color: "#d9e2ea" } }, axisLabel: { color: "#667786", rotate: 25 } },
    yAxis: [
      { type: "value", min: 0, max: 100, axisLabel: { color: "#667786", formatter: "{value}%" }, splitLine: { lineStyle: { color: "#edf1f5" } } },
      { type: "value", min: 0, axisLabel: { color: "#667786" }, splitLine: { show: false } },
    ],
    series: [
      { name: "已知良率", type: "line", connectNulls: false, smooth: true, data: data.trends.map((item) => item.yield_rate == null ? null : Number((item.yield_rate * 100).toFixed(4))) },
      { name: "UNKNOWN 占比", type: "line", connectNulls: false, smooth: true, data: data.trends.map((item) => item.unknown_rate == null ? null : Number((item.unknown_rate * 100).toFixed(4))) },
      { name: "总单元", type: "bar", yAxisIndex: 1, barMaxWidth: 22, itemStyle: { opacity: 0.28, borderRadius: [5, 5, 0, 0] }, data: data.trends.map((item) => item.total_units) },
    ],
  }), [data.trends]);

  const columns: ColumnsType<QualityDatasetDrilldown> = [
    { title: "Dataset", dataIndex: "dataset_id", width: 120, render: (value, row) => `#${value} / V${row.version_no}` },
    { title: "产品", dataIndex: "product_name", ellipsis: true },
    { title: "Lot", dataIndex: "lot_id", ellipsis: true },
    { title: "CP/FT", dataIndex: "test_stage", width: 80 },
    { title: "已知良率", dataIndex: "yield_rate", width: 120, render: percent },
    { title: "发布时间", dataIndex: "published_at_utc", width: 180, render: formatUtcDateTime },
    { title: "操作", key: "actions", width: 110, render: (_, row) => <Button type="link" size="small" onClick={() => {
      const params = new URLSearchParams({ dataset: `${row.dataset_id}:${row.version_no}` });
      onNavigate(`/analytics?${params.toString()}`);
    }}>查看分析</Button> },
  ];

  if ((data.kpis.dataset_count ?? 0) === 0) {
    return <Empty description={scope === "PERSONAL" ? "近30天没有归属于你的当前正式数据" : "近30天该数据域没有可见的当前正式数据"} />;
  }

  return <div className="dashboard-summary">
    <MetricStrip ariaLabel="近30天正式数据指标" items={[
      { label: "当前正式数据集", value: count(data.kpis.dataset_count) },
      { label: "单元数", value: count(data.kpis.total_units) },
      { label: "已知良率", value: percent(data.kpis.yield_rate), tone: "success", note: "PASS / (PASS + FAIL)" },
      { label: "UNKNOWN 占比", value: percent(data.kpis.unknown_rate), tone: data.kpis.unknown_rate ? "warning" : "default" },
    ]} />
    <Row gutter={[16, 16]}>
      <Col xs={24} xl={15}>
        <Card className="dashboard-panel chart-panel" title="近30天良率与数据覆盖趋势" extra={<Tag>{scope === "PERSONAL" ? "仅本人" : "仅当前数据域"}</Tag>}>
          <EChart option={trendOption} className="dashboard-trend-chart" ariaLabel={`${scope === "PERSONAL" ? "我的数据" : "数据域"}近30天已知良率与单元趋势`} />
        </Card>
      </Col>
      <Col xs={24} xl={9}>
        <Card className="dashboard-panel" title="需要关注">
          <Space direction="vertical" size="middle">
            <Typography.Text>产品 {count(data.kpis.product_count)} 个，Lot {count(data.kpis.lot_count)} 个</Typography.Text>
            <Typography.Text>PASS {count(data.kpis.pass_units)} / FAIL {count(data.kpis.fail_units)} / UNKNOWN {count(data.kpis.unknown_units)}</Typography.Text>
            <Typography.Text type={data.kpis.failed_job_count ? "danger" : undefined}>失败 Job：{count(data.kpis.failed_job_count)}</Typography.Text>
          </Space>
        </Card>
      </Col>
    </Row>
    <Card className="dashboard-panel" title="最近正式数据集">
      <Table rowKey={(row) => `${row.dataset_id}-${row.version_no}`} columns={columns} dataSource={data.recent_datasets} pagination={false} size="small" scroll={{ x: 850 }} />
    </Card>
  </div>;
}

export function PersonalDashboard({
  userName,
  onNavigate,
  canOpenQuality = false,
  canRunQuickAnalysis = false,
}: PersonalDashboardProps) {
  const [scope, setScope] = useState<DashboardScope>("PERSONAL");
  const [dataDomainId, setDataDomainId] = useState<number>();
  const range = useMemo(() => {
    const to = new Date();
    const from = new Date(to.getTime() - 30 * 24 * 60 * 60 * 1000);
    return { from_utc: from.toISOString(), to_utc: to.toISOString() };
  }, []);
  const domains = useQuery({ queryKey: ["data-domains", "mine"], queryFn: listMyDataDomains });
  useEffect(() => {
    if (dataDomainId == null && domains.data?.length) setDataDomainId(domains.data[0].data_domain_id);
  }, [dataDomainId, domains.data]);
  const selectedDomain = domains.data?.find((item) => item.data_domain_id === dataDomainId);
  const summary = useQuery({
    queryKey: ["dashboard", "quality-summary", scope, dataDomainId ?? null, range],
    queryFn: () => getQualityManagementSummary({
      ...range,
      access_scope: scope,
      data_domain_id: scope === "DOMAIN" ? dataDomainId : undefined,
      recent_limit: 8,
    }),
    enabled: scope === "PERSONAL" || dataDomainId != null,
    retry: false,
  });
  const personalQuick = useQuery({
    queryKey: ["dashboard", "quick-analysis", "PERSONAL"],
    queryFn: () => listQuickAnalysisSessions({
      page: 1,
      page_size: 5,
      access_scope: "PERSONAL",
    }),
    enabled: canRunQuickAnalysis,
    retry: false,
  });
  const personalQuickItems = (personalQuick.data?.items ?? []).filter(
    (item) => item.access_scope === "PERSONAL",
  );
  const quickColumns: ColumnsType<QuickAnalysisSession> = [
    { title: "会话", dataIndex: "analysis_session_id", width: 90 },
    { title: "来源", dataIndex: "source_root_code", width: 130, render: (value) => value === "LOCAL_AGENT" ? "本机目录" : value },
    { title: "范围", dataIndex: "access_scope", width: 90, render: () => <Tag color="cyan">仅本人</Tag> },
    { title: "状态", dataIndex: "status", width: 100 },
    { title: "参数", dataIndex: "parameter_count", width: 80, render: count },
    { title: "创建时间", dataIndex: "created_at_utc", width: 180, render: formatUtcDateTime },
  ];

  const scopeContent = scope === "DOMAIN" && domains.isPending
    ? <div className="page-loading"><Spin /></div>
    : scope === "DOMAIN" && domains.isError
      ? <Alert type="error" showIcon message="无法读取你的数据域" description={domains.error instanceof Error ? domains.error.message : "请稍后重试"} />
      : scope === "DOMAIN" && domains.data?.length === 0
        ? <Empty description="你当前没有有效的数据域授权" />
        : summary.isPending
          ? <div className="page-loading"><Spin /></div>
          : summary.isError
            ? <Alert type="error" showIcon message="统计数据暂时不可用" description={summary.error instanceof Error ? summary.error.message : "请稍后重试"} />
            : summary.data ? <SummaryView data={summary.data} scope={scope} onNavigate={onNavigate} /> : null;

  return <div className="personal-dashboard workbench">
    <div className="page-heading dashboard-page-heading">
      <Typography.Title level={2}>个人驾驶舱</Typography.Title>
      <Space wrap>
        <Button type="primary" icon={<RadarChartOutlined />} disabled={!canOpenQuality} onClick={() => onNavigate("/management/quality")}>进入质量总览</Button>
        <Button onClick={() => onNavigate("/datasets/current")}>查看正式数据 <ArrowRightOutlined /></Button>
      </Space>
    </div>

    <Card className="dashboard-panel dashboard-main-panel" title="我可见的正式数据" extra={<Typography.Text type="secondary">近30天 · 按正式发布时间</Typography.Text>}>
      <Tabs activeKey={scope} onChange={(key) => setScope(key as DashboardScope)} items={[
        { key: "PERSONAL", label: <span><UserOutlined /> 我的数据</span> },
        { key: "DOMAIN", label: <span><CloudServerOutlined /> 数据域</span>, children: <Space wrap><Select aria-label="选择数据域" loading={domains.isPending} placeholder="选择已授权数据域" value={dataDomainId} onChange={setDataDomainId} style={{ minWidth: 280 }} options={(domains.data ?? []).map((item: DataDomain) => ({ value: item.data_domain_id, label: `${item.domain_name} (${item.test_stage})` }))} />{selectedDomain && <><Tag color="blue">{selectedDomain.test_stage}</Tag>{selectedDomain.factory_code && <Tag>{selectedDomain.factory_code}</Tag>}</>}</Space> },
      ]} />
      {scopeContent}
    </Card>

    {canRunQuickAnalysis && <Card className="dashboard-panel" title="我的个人工具任务" extra={<Button type="link" onClick={() => onNavigate("/quick-analysis")}>查看全部 <ArrowRightOutlined /></Button>}>
      {personalQuick.isPending
        ? <div className="page-loading"><Spin /></div>
        : personalQuick.isError
          ? <Alert type="error" showIcon message="我的 Quick 暂时不可用" description={personalQuick.error instanceof Error ? personalQuick.error.message : "请稍后重试"} />
          : personalQuickItems.length === 0
            ? <Empty description="暂无个人工具结果" />
            : <Table rowKey="analysis_session_id" columns={quickColumns} dataSource={personalQuickItems} pagination={false} size="small" scroll={{ x: 670 }} />}
    </Card>}

    <Card className="dashboard-panel quick-entry-panel" title="常用入口">
      <Row gutter={[8, 8]}>
        {[
          ["/cp", "CP 数据", <ExperimentOutlined />],
          ["/ft", "FT 数据", <ThunderboltOutlined />],
        ].map(([path, title, icon]) => <Col xs={24} md={12} key={String(path)}><button type="button" onClick={() => onNavigate(String(path))}>{icon}<span><b>{title}</b></span><ArrowRightOutlined /></button></Col>)}
        <Col xs={24}><button type="button" disabled={!canRunQuickAnalysis} onClick={() => onNavigate("/quick-analysis")}><RadarChartOutlined /><span><b>个人分析工具</b></span><ArrowRightOutlined /></button></Col>
      </Row>
    </Card>
  </div>;
}

export default PersonalDashboard;
