import { BarChartOutlined, FilterOutlined, ReloadOutlined, UnorderedListOutlined } from "@ant-design/icons";
import { useQuery } from "@tanstack/react-query";
import { Alert, Button, Card, Col, Descriptions, Empty, Form, Input, Row, Select, Space, Statistic, Table, Tabs, Tag, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useEffect, useMemo } from "react";

import {
  getQualityManagementSummary,
  type FailBinSummary,
  type QualityBreakdown,
  type QualityDatasetDrilldown,
  type QualitySummaryRequest,
  type QualityTrendPoint,
} from "../../api/management";
import { formatUtcDateTime } from "../../utils/dateTime";
import { factoryNames } from "../capabilities/capabilityCatalog";

type QualityFilterValues = Omit<QualitySummaryRequest, "recent_limit">;

export interface QualityManagementDashboardProps {
  searchParams: URLSearchParams;
  onSearchParamsChange: (params: URLSearchParams) => void;
  onOpenAnalytics: (datasetId: number, versionNo: number) => void;
  onOpenJob: (jobId: number) => void;
  canOpenAnalytics: boolean;
}

const FILTER_KEYS = ["from_utc", "to_utc", "business_domain", "test_stage", "factory_code", "product_name", "lot_id"] as const;
const percent = (value: number | null | undefined) => value == null ? "—" : `${(value * 100).toFixed(2)}%`;
const numberOrDash = (value: number | null | undefined) => value == null ? "—" : value;
const freshness = (seconds: number | null | undefined) => {
  if (seconds == null) return "—";
  if (seconds < 60) return `${seconds} 秒`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟`;
  if (seconds < 86400) return `${(seconds / 3600).toFixed(1)} 小时`;
  return `${(seconds / 86400).toFixed(1)} 天`;
};
const methodologyName: Record<string, string> = {
  fact_source: "事实数据范围",
  yield: "良率口径",
  unknown: "UNKNOWN 口径",
  product_identity: "产品身份口径",
  time_range: "时间边界",
};
const breakdownDimensions = [
  { key: "FACTORY", label: "按厂家" },
  { key: "PRODUCT", label: "按产品" },
  { key: "TEST_STAGE", label: "按 CP/FT" },
  { key: "BUSINESS_DOMAIN", label: "按业务域" },
] as const;

export function QualityManagementDashboard({ searchParams, onSearchParamsChange, onOpenAnalytics, onOpenJob, canOpenAnalytics }: QualityManagementDashboardProps) {
  const [form] = Form.useForm<QualityFilterValues>();
  const searchKey = searchParams.toString();
  const request = useMemo<QualitySummaryRequest>(() => ({
    from_utc: searchParams.get("from_utc") || undefined,
    to_utc: searchParams.get("to_utc") || undefined,
    business_domain: (searchParams.get("business_domain") as QualitySummaryRequest["business_domain"]) || undefined,
    test_stage: (searchParams.get("test_stage") as QualitySummaryRequest["test_stage"]) || undefined,
    factory_code: searchParams.get("factory_code") || undefined,
    product_name: searchParams.get("product_name") || undefined,
    lot_id: searchParams.get("lot_id") || undefined,
    recent_limit: 20,
  }), [searchKey]);
  useEffect(() => {
    form.resetFields();
    form.setFieldsValue(request);
  }, [form, request]);
  const summary = useQuery({
    queryKey: ["management", "quality-summary", request],
    queryFn: () => getQualityManagementSummary(request),
  });
  const data = summary.data;

  const updateSearch = (values: QualityFilterValues) => {
    const next = new URLSearchParams(searchParams);
    for (const key of FILTER_KEYS) next.delete(key);
    for (const key of FILTER_KEYS) {
      const value = values[key]?.trim();
      if (value) next.set(key, value);
    }
    onSearchParamsChange(next);
  };
  const trendColumns: ColumnsType<QualityTrendPoint> = [
    { title: "周期起点（UTC）", dataIndex: "period_start_utc", width: 180, render: formatUtcDateTime },
    { title: "Dataset", dataIndex: "dataset_count", width: 90 },
    { title: "总单元", dataIndex: "total_units", width: 110 },
    { title: "PASS", dataIndex: "pass_units", width: 100 },
    { title: "FAIL", dataIndex: "fail_units", width: 100 },
    { title: "UNKNOWN", dataIndex: "unknown_units", width: 110 },
    { title: "PASS/(PASS+FAIL)", dataIndex: "yield_rate", width: 160, render: percent },
    { title: "UNKNOWN 占比", dataIndex: "unknown_rate", width: 125, render: percent },
  ];
  const breakdownColumns: ColumnsType<QualityBreakdown> = [
    { title: "分组", dataIndex: "label", width: 220, fixed: "left" },
    { title: "Dataset", dataIndex: "dataset_count", width: 90 },
    { title: "Lot", dataIndex: "lot_count", width: 90 },
    { title: "总单元", dataIndex: "total_units", width: 105 },
    { title: "PASS", dataIndex: "pass_units", width: 95 },
    { title: "FAIL", dataIndex: "fail_units", width: 95 },
    { title: "UNKNOWN", dataIndex: "unknown_units", width: 105 },
    { title: "良率", dataIndex: "yield_rate", width: 105, render: percent },
    { title: "UNKNOWN 占比", dataIndex: "unknown_rate", width: 125, render: percent },
  ];
  const failBinColumns: ColumnsType<FailBinSummary> = [
    { title: "Fail Bin", dataIndex: "bin_code" },
    { title: "失败单元", dataIndex: "fail_units", width: 130 },
    { title: "占全部 FAIL", dataIndex: "share_of_failed", width: 140, render: percent },
  ];
  const recentColumns: ColumnsType<QualityDatasetDrilldown> = [
    { title: "Dataset", dataIndex: "dataset_id", width: 105, fixed: "left", render: (value, row) => `#${value} / V${row.version_no}` },
    { title: "产品", dataIndex: "product_name", width: 210, ellipsis: true },
    {
      title: "Lot / 追溯",
      dataIndex: "lot_id",
      width: 220,
      render: (value, row) => <Space size={4}><span>{value || "—"}</span>{row.job_id != null && <Button type="link" size="small" onClick={() => onOpenJob(row.job_id!)}>查看链路</Button>}</Space>,
    },
    { title: "厂家", dataIndex: "factory_code", width: 110, render: (value) => factoryNames[String(value).toLowerCase()] ?? value },
    { title: "范围", key: "scope", width: 125, render: (_, row) => `${row.business_domain === "ENGINEERING" ? "工程" : "量产"} / ${row.test_stage}` },
    { title: "总单元", dataIndex: "unit_count", width: 105 },
    { title: "PASS", dataIndex: "pass_count", width: 90 },
    { title: "FAIL", dataIndex: "fail_count", width: 90 },
    { title: "UNKNOWN", dataIndex: "unknown_count", width: 105 },
    { title: "良率", dataIndex: "yield_rate", width: 100, render: percent },
    {
      title: "来源文件",
      dataIndex: "source_file_count",
      width: 150,
      render: (value, row) => row.job_id != null
        ? <Button type="link" size="small" onClick={() => onOpenJob(row.job_id!)}>{value} 个（Job 链路）</Button>
        : <Typography.Text type="secondary">{value} 个（无可用 Job 链路）</Typography.Text>,
    },
    { title: "发布时间", dataIndex: "published_at_utc", width: 180, render: formatUtcDateTime },
    {
      title: "操作",
      key: "actions",
      width: 190,
      fixed: "right",
      render: (_, row) => <Space size={0}>
        <Button type="link" size="small" icon={<BarChartOutlined />} disabled={!canOpenAnalytics} title={canOpenAnalytics ? undefined : "缺少 ANALYSIS_RUN 权限"} onClick={() => onOpenAnalytics(row.dataset_id, row.version_no)}>分析</Button>
        {row.job_id != null && <Button type="link" size="small" icon={<UnorderedListOutlined />} onClick={() => onOpenJob(row.job_id!)}>Job</Button>}
      </Space>,
    },
  ];

  const kpis = data?.kpis;
  const cards = kpis ? [
    ["Dataset 数", numberOrDash(kpis.dataset_count)],
    ["产品数", numberOrDash(kpis.product_count)],
    ["Lot 数", numberOrDash(kpis.lot_count)],
    ["总单元", numberOrDash(kpis.total_units)],
    ["PASS", numberOrDash(kpis.pass_units)],
    ["FAIL", numberOrDash(kpis.fail_units)],
    ["UNKNOWN", numberOrDash(kpis.unknown_units)],
    ["良率样本数 PASS+FAIL", numberOrDash(kpis.known_yield_denominator)],
    ["PASS / (PASS + FAIL) 良率", percent(kpis.yield_rate)],
    ["UNKNOWN 占比", percent(kpis.unknown_rate)],
    ["失败 Job", numberOrDash(kpis.failed_job_count)],
    ["数据新鲜度", freshness(kpis.freshness_seconds)],
  ] as const : [];

  return <div className="workbench production-workbench">
    <div className="page-heading">
      <div>
        <Typography.Text type="secondary">管理驾驶舱 / Current Dataset 质量</Typography.Text>
        <Typography.Title level={2}>质量与良率管理摘要</Typography.Title>
        <Typography.Text type="secondary">只基于后端返回的正式 Current 事实和已审批口径，UNKNOWN 不会被补成 FAIL 或零。</Typography.Text>
      </div>
      <Button icon={<ReloadOutlined />} loading={summary.isFetching} onClick={() => void summary.refetch()}>刷新摘要</Button>
    </div>

    <Card className="review-filter-card">
      <Form<QualityFilterValues> form={form} layout="vertical" onFinish={updateSearch}>
        <Row gutter={[12, 0]}>
          <Col xs={24} sm={12} lg={6}><Form.Item label="开始时间（UTC，含）" name="from_utc"><Input allowClear placeholder="2026-08-01T00:00:00Z" /></Form.Item></Col>
          <Col xs={24} sm={12} lg={6}><Form.Item label="结束时间（UTC，不含）" name="to_utc"><Input allowClear placeholder="2026-09-01T00:00:00Z" /></Form.Item></Col>
          <Col xs={24} sm={12} lg={4}><Form.Item label="产品" name="product_name"><Input allowClear /></Form.Item></Col>
          <Col xs={24} sm={12} lg={4}><Form.Item label="Lot" name="lot_id"><Input allowClear /></Form.Item></Col>
          <Col xs={24} sm={12} lg={4}><Form.Item label="厂家编码" name="factory_code"><Input allowClear /></Form.Item></Col>
          <Col xs={12} sm={6} lg={4}><Form.Item label="业务域" name="business_domain"><Select allowClear options={[{ label: "工程", value: "ENGINEERING" }, { label: "量产", value: "PRODUCTION" }]} /></Form.Item></Col>
          <Col xs={12} sm={6} lg={4}><Form.Item label="阶段" name="test_stage"><Select allowClear options={[{ label: "CP", value: "CP" }, { label: "FT", value: "FT" }]} /></Form.Item></Col>
          <Col span={24}><Space><Button type="primary" htmlType="submit" icon={<FilterOutlined />}>更新管理口径</Button><Button onClick={() => { form.resetFields(); updateSearch({}); }}>清空</Button></Space></Col>
        </Row>
      </Form>
    </Card>

    {summary.isError && <Alert type="error" showIcon message="质量管理摘要加载失败" description="本页不展示底层连接或 SQL 详情；请稍后刷新。" className="review-alert" />}
    {data && <>
      <Alert
        type="info"
        showIcon
        className="review-alert"
        message="方法口径与时间边界"
        description={<Space direction="vertical" size={2}>
          <Typography.Text>统计时间：[{formatUtcDateTime(data.from_utc)}, {formatUtcDateTime(data.to_utc)})，开始含、结束不含；快照时间：{formatUtcDateTime(data.observed_at_utc)}。</Typography.Text>
          <Typography.Text strong>PASS / (PASS + FAIL)；UNKNOWN 和 ABORT 不进入良率分母。</Typography.Text>
        </Space>}
      />
      <Card title="后端返回的方法说明" className="review-filter-card">
        <Descriptions column={1} size="small" bordered>
          {Object.entries(data.methodology).map(([key, value]) => <Descriptions.Item key={key} label={methodologyName[key] ?? key}>{value}</Descriptions.Item>)}
        </Descriptions>
      </Card>
      <Row gutter={[16, 16]} className="production-stats">
        {cards.map(([title, value]) => <Col key={title} xs={24} sm={12} lg={6} xl={4}><Card><Statistic title={title} value={value} /></Card></Col>)}
      </Row>
      <Typography.Paragraph type="secondary">最新 Dataset：{formatUtcDateTime(kpis?.latest_dataset_at_utc)}。ABORT 单元：{numberOrDash(kpis?.abort_units)}，单独保留且不进入良率分母。</Typography.Paragraph>

      <Card title="质量趋势" className="production-table-card" style={{ marginBottom: 18 }}>
        <Table rowKey="period_start_utc" size="small" columns={trendColumns} dataSource={data.trends} pagination={false} scroll={{ x: 980 }} locale={{ emptyText: <Empty description="当前口径没有趋势数据" /> }} />
      </Card>
      <Card title="质量分解" className="production-table-card" style={{ marginBottom: 18 }}>
        <Tabs items={breakdownDimensions.map((dimension) => ({
          key: dimension.key,
          label: dimension.label,
          children: <Table rowKey={(row) => `${row.dimension}-${row.key}`} size="small" columns={breakdownColumns} dataSource={data.breakdowns.filter((row) => row.dimension === dimension.key)} pagination={false} scroll={{ x: 1050 }} locale={{ emptyText: <Empty description={`${dimension.label}暂无数据`} /> }} />,
        }))} />
      </Card>
      <Card title="Fail Bin 分布" className="production-table-card" style={{ marginBottom: 18 }}>
        <Table rowKey="bin_code" size="small" columns={failBinColumns} dataSource={data.fail_bins} pagination={false} locale={{ emptyText: <Empty description="当前口径没有 Fail Bin" /> }} />
      </Card>
      <Card title="最近 Current Dataset" extra={<Tag color="blue">最多 20 个</Tag>} className="production-table-card">
        <Alert type="info" showIcon message="Lot 与 Source 追溯边界" description="当前仅通过真实 Job / Import Batch 发布链路下钻；没有独立 Lot 或 Source 明细 API 时，本页不会伪造入口。" style={{ marginBottom: 12 }} />
        <Table rowKey={(row) => `${row.dataset_id}-${row.version_no}`} columns={recentColumns} dataSource={data.recent_datasets} pagination={false} scroll={{ x: 1900 }} locale={{ emptyText: <Empty description="当前口径没有 Current Dataset" /> }} />
      </Card>
    </>}
  </div>;
}
