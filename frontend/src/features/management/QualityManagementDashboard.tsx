import { BarChartOutlined, FilterOutlined, ReloadOutlined, UnorderedListOutlined } from "@ant-design/icons";
import { useQuery } from "@tanstack/react-query";
import { Alert, Button, Card, Col, Collapse, Descriptions, Empty, Form, Input, Row, Select, Space, Statistic, Table, Tabs, Tag, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import type { EChartsOption } from "echarts";
import { useEffect, useMemo } from "react";

import {
  getQualityManagementSummary,
  type FailBinSummary,
  type QualityBreakdown,
  type QualityDatasetDrilldown,
  type QualitySummaryRequest,
  type QualityTrendPoint,
} from "../../api/management";
import { formatShanghaiDate, formatUtcDateTime, recentShanghaiDayRange, shanghaiLocalInputToUtc, utcToShanghaiLocalInput } from "../../utils/dateTime";
import { EChart } from "../../components/EChart";
import { factoryNames } from "../capabilities/capabilityCatalog";
import { AnalysisRuleRegistryPanel } from "./AnalysisRuleRegistryPanel";

type QualityFilterValues = Omit<QualitySummaryRequest, "access_scope" | "data_domain_id" | "recent_limit" | "from_utc" | "to_utc"> & {
  from_local?: string;
  to_local?: string;
};

export interface QualityManagementDashboardProps {
  searchParams: URLSearchParams;
  onSearchParamsChange: (params: URLSearchParams) => void;
  onOpenAnalytics: (datasetId: number, versionNo: number) => void;
  onOpenJob: (jobId: number) => void;
  canOpenAnalytics: boolean;
  canReadManagement?: boolean;
  canGovernRules?: boolean;
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
  trend_period: "趋势业务日",
  failed_job_scope: "失败 Job 适用范围",
  access_scope: "数据权限范围",
};
const methodologyValue: Record<string, string> = {
  CURRENT_PUBLISHED_DATASET_VERSION_RUNS: "当前已发布 Dataset Version 及其正式 Run",
  "Only PUBLISHED is_current=1 Dataset Versions and their Canonical test.* rows are counted.": "仅统计已发布且当前生效的 Dataset Version 及其 Canonical test.* 事实行",
  "PASS/(PASS+FAIL); UNKNOWN_AND_ABORT_EXCLUDED": "PASS / (PASS + FAIL)，UNKNOWN 与 ABORT 不进入分母",
  "PASS / (PASS + FAIL); UNKNOWN and ABORT never enter the yield denominator.": "PASS / (PASS + FAIL)，UNKNOWN 与 ABORT 不进入分母",
  "UNKNOWN/(PASS+FAIL+UNKNOWN+ABORT)": "UNKNOWN / 全部单元（含 ABORT）",
  "UNKNOWN / all Current units; missing PASS/FAIL remains unknown and is never filled with zero.": "UNKNOWN / 全部 Current 单元；缺失的 PASS/FAIL 保持 UNKNOWN，不补为零",
  "MDM_PRODUCT_WITH_CANONICAL_FALLBACK": "优先使用主数据产品身份，缺失时使用 Canonical 保存值",
  "Product is the source-observed TMS identity, not an SAP material until an approved crosswalk exists.": "产品是来源观测到的 TMS 身份；在 Crosswalk 审批前不视为 SAP 物料",
  "[FROM_UTC,TO_UTC)_ON_PUBLISHED_AT_UTC": "按发布时间筛选，开始含、结束不含",
  "from_utc is inclusive and to_utc is exclusive, based on Dataset published_at_utc.": "按 Dataset published_at_utc 筛选：from_utc 含，to_utc 不含",
  "Trend periods are Asia/Shanghai business dates; period_start_utc is the UTC instant of Shanghai local midnight.": "趋势按 Asia/Shanghai 业务日分组；周期起点是上海当地零点对应的 UTC 时刻",
  "Failed Job counts use time, business domain, test stage, and factory filters only; Product and Lot filters do not apply.": "失败 Job 仅按时间、业务域、阶段和厂家筛选；产品与 Lot 筛选不适用",
  "PERSONAL is always owner-only; DOMAIN requires an active, unexpired grant. Dashboard queries never use break-glass access.": "我的数据始终只统计归属本人的数据；数据域必须有未过期的有效授权；驾驶舱不使用紧急数据访问权限。",
};
const breakdownDimensions = [
  { key: "FACTORY", label: "按厂家" },
  { key: "PRODUCT", label: "按产品" },
  { key: "TEST_STAGE", label: "按 CP/FT" },
  { key: "BUSINESS_DOMAIN", label: "按业务域" },
] as const;

export function QualityManagementDashboard({ searchParams, onSearchParamsChange, onOpenAnalytics, onOpenJob, canOpenAnalytics, canReadManagement = true, canGovernRules = false }: QualityManagementDashboardProps) {
  const [form] = Form.useForm<QualityFilterValues>();
  const searchKey = searchParams.toString();
  const request = useMemo<QualitySummaryRequest>(() => ({
    access_scope: "PERSONAL",
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
    if (!canReadManagement) return;
    form.resetFields();
    form.setFieldsValue({
      business_domain: request.business_domain,
      test_stage: request.test_stage,
      factory_code: request.factory_code,
      product_name: request.product_name,
      lot_id: request.lot_id,
      from_local: utcToShanghaiLocalInput(request.from_utc),
      to_local: utcToShanghaiLocalInput(request.to_utc),
    });
  }, [canReadManagement, form, request]);
  const summary = useQuery({
    queryKey: ["management", "quality-summary", request],
    queryFn: () => getQualityManagementSummary(request),
    enabled: canReadManagement,
  });
  const data = summary.data;
  const trendOption = useMemo<EChartsOption>(() => ({
    tooltip: { trigger: "axis" },
    legend: { data: ["已知良率", "UNKNOWN 占比", "总单元"] },
    grid: { left: 64, right: 72, top: 48, bottom: 54 },
    xAxis: {
      type: "category",
      data: data?.trends.map((item) => formatShanghaiDate(item.period_start_utc)) ?? [],
      axisLabel: { rotate: 30, hideOverlap: true },
    },
    yAxis: [
      { type: "value", name: "比例 %", min: 0, max: 100 },
      { type: "value", name: "单元数", min: 0 },
    ],
    series: [
      { name: "已知良率", type: "line", connectNulls: false, data: data?.trends.map((item) => item.yield_rate == null ? null : Number((item.yield_rate * 100).toFixed(4))) ?? [] },
      { name: "UNKNOWN 占比", type: "line", connectNulls: false, data: data?.trends.map((item) => item.unknown_rate == null ? null : Number((item.unknown_rate * 100).toFixed(4))) ?? [] },
      { name: "总单元", type: "bar", yAxisIndex: 1, opacity: 0.25, data: data?.trends.map((item) => item.total_units) ?? [] },
    ],
  }), [data?.trends]);

  const updateSearch = (values: QualityFilterValues) => {
    const next = new URLSearchParams(searchParams);
    for (const key of FILTER_KEYS) next.delete(key);
    for (const key of FILTER_KEYS.filter((key) => key !== "from_utc" && key !== "to_utc")) {
      const value = values[key as Exclude<typeof key, "from_utc" | "to_utc">]?.trim();
      if (value) next.set(key, value);
    }
    const fromUtc = shanghaiLocalInputToUtc(values.from_local);
    const toUtc = shanghaiLocalInputToUtc(values.to_local);
    if (fromUtc) next.set("from_utc", fromUtc);
    if (toUtc) next.set("to_utc", toUtc);
    onSearchParamsChange(next);
  };
  const applyRecentRange = (days: number) => {
    const range = recentShanghaiDayRange(days);
    const values = { ...form.getFieldsValue(), from_local: range.from, to_local: range.to };
    form.setFieldsValue(values);
    updateSearch(values);
  };
  const trendColumns: ColumnsType<QualityTrendPoint> = [
    { title: "上海业务日", dataIndex: "period_start_utc", width: 140, render: formatShanghaiDate },
    { title: "Dataset", dataIndex: "dataset_count", width: 90 },
    { title: "总单元", dataIndex: "total_units", width: 110 },
    { title: "PASS", dataIndex: "pass_units", width: 100 },
    { title: "FAIL", dataIndex: "fail_units", width: 100 },
    { title: "UNKNOWN", dataIndex: "unknown_units", width: 110 },
    { title: "PASS/(PASS+FAIL)", dataIndex: "yield_rate", width: 160, render: percent },
    { title: "UNKNOWN 占比", dataIndex: "unknown_rate", width: 125, render: percent },
  ];
  const breakdownColumns: ColumnsType<QualityBreakdown> = [
    { title: "分组", dataIndex: "label", width: 220, fixed: "left", render: (value, row) => row.dimension === "FACTORY" ? (factoryNames[String(value).toLowerCase()] ?? value) : row.dimension === "BUSINESS_DOMAIN" ? (value === "ENGINEERING" ? "工程" : value === "PRODUCTION" ? "量产" : value) : value },
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
        <Button type="link" size="small" icon={<BarChartOutlined />} disabled={!canOpenAnalytics} title={canOpenAnalytics ? undefined : "当前账户无权查看 Dataset 分析"} onClick={() => onOpenAnalytics(row.dataset_id, row.version_no)}>分析</Button>
        {row.job_id != null && <Button type="link" size="small" icon={<UnorderedListOutlined />} onClick={() => onOpenJob(row.job_id!)}>Job</Button>}
      </Space>,
    },
  ];

  const kpis = data?.kpis;
  const failedJobsApplicable = !request.product_name && !request.lot_id;
  const cards = kpis ? [
    ["已知良率", percent(kpis.yield_rate), kpis.yield_rate == null ? undefined : kpis.yield_rate < 0.9 ? "#cf1322" : "#1677ff"],
    ["UNKNOWN 占比", percent(kpis.unknown_rate), kpis.unknown_rate != null && kpis.unknown_rate > 0.1 ? "#d46b08" : undefined],
    ["失败 Job（批次口径）", failedJobsApplicable ? numberOrDash(kpis.failed_job_count) : "不适用", failedJobsApplicable && kpis.failed_job_count ? "#cf1322" : undefined],
    ["数据新鲜度", freshness(kpis.freshness_seconds), kpis.freshness_seconds != null && kpis.freshness_seconds > 86400 ? "#d46b08" : undefined],
    ["总单元", numberOrDash(kpis.total_units), undefined],
    ["Current Dataset", numberOrDash(kpis.dataset_count), undefined],
  ] as const : [];

  return <div className="workbench production-workbench">
    <div className="page-heading">
      <div>
        <Typography.Text type="secondary">管理驾驶舱 / Current Dataset 质量</Typography.Text>
        <Typography.Title level={2}>质量与良率管理摘要</Typography.Title>
        <Space wrap><Tag color="cyan">当前范围：我的数据</Tag><Typography.Text type="secondary">只基于后端返回的正式 Current 事实和已审批口径，UNKNOWN 不会被补成 FAIL 或零。</Typography.Text></Space>
      </div>
      {canReadManagement && <Button icon={<ReloadOutlined />} loading={summary.isFetching} onClick={() => void summary.refetch()}>刷新摘要</Button>}
    </div>

    {!canReadManagement && <><Form form={form} component={false} /><Alert type="info" showIcon message="当前仅开放 Rule Registry" description="当前账户拥有 RULE_GOVERN，但没有 MANAGEMENT_READ；质量摘要和筛选不会请求或展示。" className="review-alert" /></>}
    {canReadManagement && summary.isError && <Alert type="error" showIcon message="质量管理摘要加载失败" description="本页不展示底层连接或 SQL 详情；请稍后刷新。" className="review-alert" />}
    {data && <>
      <Row gutter={[16, 16]} className="production-stats">
        {cards.map(([title, value, color]) => <Col key={title} xs={24} sm={12} lg={8} xl={4}><Card><Statistic title={title} value={value} valueStyle={{ color }} /></Card></Col>)}
      </Row>
      <Typography.Paragraph type="secondary">统计范围 [{formatUtcDateTime(data.from_utc)}, {formatUtcDateTime(data.to_utc)})；最近 Dataset：{formatUtcDateTime(kpis?.latest_dataset_at_utc)}。已知良率分母 {numberOrDash(kpis?.known_yield_denominator)}，ABORT {numberOrDash(kpis?.abort_units)} 个，均未混入 FAIL。</Typography.Paragraph>
      {!failedJobsApplicable && <Alert type="warning" showIcon className="review-alert" message="失败 Job KPI 对当前筛选不适用" description="失败 Job 只能可靠按时间、业务域、阶段和厂家归属；当前产品或 Lot 筛选不会被强行套用。" />}
      <Card title="质量趋势" className="production-table-card" style={{ marginBottom: 18 }}>
        {data.trends.length ? <EChart option={trendOption} /> : <Empty description="当前口径没有趋势数据" />}
      </Card>
    </>}

    {canReadManagement && <Collapse
      className="review-filter-card"
      defaultActiveKey={["filters"]}
      items={[
        {
          key: "filters",
          label: "筛选条件（上海业务时间）",
          children: <Form<QualityFilterValues> form={form} layout="vertical" onFinish={updateSearch}>
            <Row gutter={[12, 0]}>
          <Col xs={24} sm={12} lg={6}><Form.Item label="开始时间（上海，含）" name="from_local"><Input type="datetime-local" allowClear /></Form.Item></Col>
          <Col xs={24} sm={12} lg={6}><Form.Item label="结束时间（上海，不含）" name="to_local"><Input type="datetime-local" allowClear /></Form.Item></Col>
          <Col xs={24} sm={12} lg={4}><Form.Item label="产品" name="product_name"><Input allowClear /></Form.Item></Col>
          <Col xs={24} sm={12} lg={4}><Form.Item label="Lot" name="lot_id"><Input allowClear /></Form.Item></Col>
          <Col xs={24} sm={12} lg={4}><Form.Item label="厂家" name="factory_code"><Select allowClear showSearch options={Object.entries(factoryNames).map(([value, label]) => ({ value, label }))} /></Form.Item></Col>
          <Col xs={12} sm={6} lg={4}><Form.Item label="业务域" name="business_domain"><Select allowClear options={[{ label: "工程", value: "ENGINEERING" }, { label: "量产", value: "PRODUCTION" }]} /></Form.Item></Col>
          <Col xs={12} sm={6} lg={4}><Form.Item label="阶段" name="test_stage"><Select allowClear options={[{ label: "CP", value: "CP" }, { label: "FT", value: "FT" }]} /></Form.Item></Col>
          <Col span={24}><Space wrap><Button type="primary" htmlType="submit" icon={<FilterOutlined />}>更新管理口径</Button><Button onClick={() => { form.resetFields(); updateSearch({}); }}>清空</Button>{[7, 30, 90].map((days) => <Button key={days} onClick={() => applyRecentRange(days)}>{`最近 ${days} 天`}</Button>)}</Space></Col>
            </Row>
          </Form>,
        },
        ...(data ? [{
          key: "methodology",
          label: "统计方法与趋势明细",
          children: <Space direction="vertical" size={16} className="full-width">
            <Alert type="info" showIcon message="PASS / (PASS + FAIL)；UNKNOWN 和 ABORT 不进入良率分母" description={`快照时间：${formatUtcDateTime(data.observed_at_utc)}`} />
            <Descriptions column={1} size="small" bordered>
              {Object.entries(data.methodology).map(([key, value]) => <Descriptions.Item key={key} label={methodologyName[key] ?? key}>{methodologyValue[value] ?? value}</Descriptions.Item>)}
            </Descriptions>
            <Table rowKey="period_start_utc" size="small" columns={trendColumns} dataSource={data.trends} pagination={false} scroll={{ x: 980 }} />
          </Space>,
        }] : []),
      ]}
    />}

    {data && <>
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
    {canGovernRules && <AnalysisRuleRegistryPanel />}
  </div>;
}
