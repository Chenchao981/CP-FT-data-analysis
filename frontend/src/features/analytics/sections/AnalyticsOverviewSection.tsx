import { Alert, Button, Card, Col, Empty, Row, Space, Statistic, Table, Tag, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import type { EChartsCoreOption } from "echarts/core";
import { useMemo } from "react";

import type { AnalyticsBinPoint, AnalyticsDatasetOverview, AnalyticsOverviewResult, AnalyticsRiskItem } from "../../../api/analytics";
import { EChart, type EChartEventMap } from "../../../components/EChart";
import { OverviewInstantRiskPanel } from "../OverviewInstantRiskPanel";
import type { OverviewRiskViewConfig } from "../context/analysisViewConfig";
import { capabilityFor, isCapabilityAvailable, type AnalyticsAggregateDrilldown, type AnalyticsAggregateDrilldownOpener, type AnalyticsDrilldownOpener, type AnalyticsSectionContext } from "./sectionTypes";

const percent = (value: number | null) => value == null ? "—" : `${(value * 100).toFixed(3)}%`;
const datasetLabel = (datasetId: number, versionNo: number) => `#${datasetId} / V${versionNo}`;

interface AnalyticsOverviewSectionProps extends Omit<AnalyticsSectionContext, "overview">, AnalyticsDrilldownOpener, AnalyticsAggregateDrilldownOpener {
  overview: AnalyticsOverviewResult | undefined;
  onNavigateSection?: (section: "detail" | "quality") => void;
  riskConfig: OverviewRiskViewConfig;
  onRiskConfigChange: (patch: Partial<OverviewRiskViewConfig>) => void;
  parameterOptions: readonly string[];
}

export function AnalyticsOverviewSection({ overview, overviewLoading, overviewError, onNavigateSection, onOpenDrilldown, onOpenAggregateDrilldown, riskConfig, onRiskConfigChange, parameterOptions, context }: AnalyticsOverviewSectionProps) {
  const yieldAvailable = isCapabilityAvailable(overview, "YIELD");
  const paretoAvailable = isCapabilityAvailable(overview, "BIN_PARETO");
  const yieldCapability = capabilityFor(overview, "YIELD");
  const paretoCapability = capabilityFor(overview, "BIN_PARETO");

  const yieldOption = useMemo<EChartsCoreOption>(() => ({
    color: ["#1167a8"],
    tooltip: { trigger: "axis" },
    grid: { left: 62, right: 24, top: 30, bottom: 80 },
    xAxis: {
      type: "category",
      data: overview?.yield_trend.map((item) => `#${item.sequence} · Batch ${item.test_batch_id}\n${item.lot_id}${item.wafer_id ? ` / W${item.wafer_id}` : ""}`) ?? [],
      axisLabel: { rotate: 35, hideOverlap: true },
    },
    yAxis: { type: "value", min: 0, max: 100, name: "Yield %" },
    dataZoom: [{ type: "inside" }, { type: "slider", height: 18, bottom: 8 }],
    series: [{
      name: "Known Yield",
      type: "line",
      symbolSize: 7,
      data: overview?.yield_trend.map((item) => ({
        value: item.yield_rate == null ? null : Number((item.yield_rate * 100).toFixed(4)),
        testBatchId: item.test_batch_id,
        sourceId: item.source_id,
        orderBasis: item.order_basis,
        orderedAt: item.ordered_at,
      })) ?? [],
    }],
    toolbox: { feature: { saveAsImage: { name: "yield-trend" }, dataZoom: {} } },
  }), [overview]);

  const paretoOption = useMemo<EChartsCoreOption>(() => ({
    color: ["#d64545", "#f0a429"],
    tooltip: { trigger: "axis" },
    legend: { data: ["Unit 数", "累计占比"] },
    grid: { left: 62, right: 62, top: 48, bottom: 60 },
    xAxis: {
      type: "category",
      data: overview?.bin_pareto.map((item) => `${datasetLabel(item.dataset_id, item.version_no)}\nBin ${item.bin_code}`) ?? [],
      axisLabel: { rotate: 30, hideOverlap: true },
    },
    yAxis: [
      { type: "value", name: "Unit 数", minInterval: 1 },
      { type: "value", name: "累计 %", min: 0, max: 100 },
    ],
    series: [
      { name: "Unit 数", type: "bar", data: overview?.bin_pareto.map((item) => ({ value: item.unit_count })) ?? [] },
      { name: "累计占比", type: "line", yAxisIndex: 1, data: overview?.bin_pareto.map((item) => ({ value: Number((item.cumulative_percent * 100).toFixed(4)) })) ?? [] },
    ],
    toolbox: { feature: { saveAsImage: { name: "bin-pareto" }, dataZoom: {} } },
  }), [overview]);

  const openYieldAggregate = (index: number) => {
    const item = overview?.yield_trend[index];
    if (!item) return;
    onOpenAggregateDrilldown({
      dataset: { dataset_id: item.dataset_id, version_no: item.version_no },
      filters: {
        lot_ids: [item.lot_id],
        wafer_ids: item.wafer_id ? [item.wafer_id] : [],
        source_ids: [item.source_id],
      },
    });
  };
  const openBinAggregate = (index: number) => {
    const item = overview?.bin_pareto[index];
    if (!item) return;
    onOpenAggregateDrilldown({
      dataset: { dataset_id: item.dataset_id, version_no: item.version_no },
      filters: { bin_codes: [item.bin_code] },
    });
  };
  const chartEvents = useMemo<EChartEventMap>(() => ({
    click: (payload) => {
      if (typeof payload !== "object" || payload === null) return;
      const event = payload as { dataIndex?: unknown; seriesName?: unknown };
      if (!Number.isSafeInteger(event.dataIndex) || (event.dataIndex as number) < 0) return;
      if (event.seriesName === "Known Yield") openYieldAggregate(event.dataIndex as number);
      if (event.seriesName === "Unit 数" || event.seriesName === "累计占比") openBinAggregate(event.dataIndex as number);
    },
  }), [overview, onOpenAggregateDrilldown]);

  const datasetColumns: ColumnsType<AnalyticsDatasetOverview> = [
    { title: "Dataset", width: 150, render: (_, row) => datasetLabel(row.dataset_id, row.version_no) },
    { title: "Unit", dataIndex: "unit_count", width: 100 },
    { title: "PASS", dataIndex: "pass_count", width: 90 },
    { title: "FAIL", dataIndex: "fail_count", width: 90 },
    { title: "UNKNOWN", dataIndex: "unknown_count", width: 105 },
    { title: "ABORT", dataIndex: "abort_count", width: 90 },
    { title: "已知结果分母", dataIndex: "known_yield_denominator", width: 130 },
    { title: "已知良率", dataIndex: "yield_rate", width: 120, render: percent },
  ];

  const binColumns: ColumnsType<AnalyticsBinPoint> = [
    { title: "Dataset", width: 150, render: (_, row) => datasetLabel(row.dataset_id, row.version_no) },
    { title: "Mapping", width: 170, render: (_, row) => `#${row.mapping_set_id} / ${row.mapping_version}` },
    { title: "类型 / Bin", width: 145, render: (_, row) => `${row.bin_type} / ${row.bin_code}` },
    { title: "含义", width: 180, render: (_, row) => row.bin_name ?? row.failure_mode ?? "—" },
    { title: "语义", dataIndex: "is_pass", width: 90, render: (value: boolean) => <Tag color={value ? "success" : "error"}>{value ? "PASS" : "FAIL"}</Tag> },
    { title: "Unit", dataIndex: "unit_count", width: 100 },
    { title: "占比", dataIndex: "percent", width: 110, render: (value: number) => percent(value) },
    { title: "后端累计占比", dataIndex: "cumulative_percent", width: 150, render: (value: number) => percent(value) },
    { title: "聚合下钻", width: 105, render: (_, row) => <Button size="small" onClick={() => openBinAggregate(overview?.bin_pareto.indexOf(row) ?? -1)}>查看明细</Button> },
  ];

  const openRisk = (row: AnalyticsRiskItem) => {
    if (row.drilldown_target === "DETAIL:EVALUATION" && row.aggregate_drilldown_context) {
      onOpenAggregateDrilldown({
        filters: {},
        evaluationFilter: row.aggregate_drilldown_context,
      });
      return;
    }
    if (row.drilldown_target === "QUALITY") {
      onNavigateSection?.("quality");
      return;
    }
    if (row.drilldown_target === "DETAIL:RESULT:FAIL") {
      onOpenAggregateDrilldown({ filters: { overall_results: ["FAIL"] } });
      return;
    }
    if (row.code === "UNKNOWN_OR_ABORT_RESULT" || row.code === "YIELD_NOT_ASSESSABLE") {
      onOpenAggregateDrilldown({ filters: { overall_results: ["UNKNOWN", "ABORT"] } });
      return;
    }
    onNavigateSection?.("detail");
  };

  const riskColumns: ColumnsType<AnalyticsRiskItem> = [
    { title: "状态", dataIndex: "status", width: 100, render: (value: string) => <Tag color={value === "ACTIVE" ? "warning" : value === "GATED" ? "default" : "success"}>{value}</Tag> },
    { title: "类别", dataIndex: "category", width: 120 },
    { title: "风险 / 门禁", dataIndex: "title", width: 210 },
    { title: "说明", dataIndex: "message", width: 360 },
    { title: "影响 / 总体", width: 125, render: (_, row) => `${row.affected_count} / ${row.denominator_count}` },
    { title: "比例", dataIndex: "rate", width: 100, render: percent },
    { title: "原因", dataIndex: "reason_code", width: 220, render: (value: string | null) => value ?? "—" },
    { title: "Rule Version", dataIndex: "rule_versions", width: 180, render: (value: string[]) => value.length ? value.join(" / ") : "—" },
    { title: "下钻入口", dataIndex: "drilldown_target", width: 150, render: (value: string | null, row) => value ? <Button type="link" size="small" onClick={() => openRisk(row)}>{value}</Button> : "—" },
  ];

  if (overviewError) return <Alert type="error" showIcon message="Overview 加载失败" description={overviewError.message} />;
  if (overviewLoading && !overview) return <Card loading title="Overview" />;
  if (!overview) return <Empty description="服务端未返回 Overview" />;

  return <Space direction="vertical" size="large" style={{ width: "100%" }}>
    {overview.warnings.length > 0 && <Alert type="warning" showIcon message="服务端提示" description={overview.warnings.join("、")} />}
    <Row gutter={[12, 12]}>
      <Col xs={12} md={6}><Card><Statistic title="纳入 Unit" value={overview.counts.included_units} /><Button type="link" onClick={() => onOpenAggregateDrilldown({ filters: {} })}>Unit 明细</Button></Card></Col>
      <Col xs={12} md={6}><Card><Statistic title="PASS" value={overview.counts.pass_count} /><Button type="link" onClick={() => onOpenAggregateDrilldown({ filters: { overall_results: ["PASS"] } })}>PASS 明细</Button></Card></Col>
      <Col xs={12} md={6}><Card><Statistic title="FAIL" value={overview.counts.fail_count} /><Button type="link" onClick={() => onOpenAggregateDrilldown({ filters: { overall_results: ["FAIL"] } })}>FAIL 明细</Button></Card></Col>
      <Col xs={12} md={6}><Card><Statistic title="Missing Measurement" value={overview.counts.missing_measurements} /><Button type="link" onClick={() => onNavigateSection?.("detail")}>测量明细</Button></Card></Col>
    </Row>
    <Row gutter={[12, 12]}>
      <Col xs={12} md={6}><Card><Statistic title="PASS + FAIL 分母" value={overview.counts.known_yield_denominator} /></Card></Col>
      <Col xs={12} md={6}><Card>{overview.counts.yield_rate == null
        ? <Statistic title="已知良率" value="—" />
        : <Statistic title="已知良率" value={overview.counts.yield_rate * 100} precision={3} suffix="%" />}</Card></Col>
      <Col xs={12} md={6}><Card><Statistic title="UNKNOWN + ABORT" value={overview.counts.unknown_count + overview.counts.abort_count} /><Button type="link" onClick={() => onOpenAggregateDrilldown({ filters: { overall_results: ["UNKNOWN", "ABORT"] } })}>未知结果明细</Button></Card></Col>
      <Col xs={12} md={6}><Card><Statistic title="未知占比" value={overview.counts.unknown_abort_rate == null ? undefined : overview.counts.unknown_abort_rate * 100} precision={3} suffix="%" /><Typography.Text type="secondary">分母 {overview.counts.unknown_abort_denominator}</Typography.Text></Card></Col>
    </Row>
    <Card title="基础风险摘要" extra={<Tag>数据事实与持久化评价</Tag>}>
      {overview.risk_summary.length
        ? <Table rowKey="code" columns={riskColumns} dataSource={overview.risk_summary} pagination={false} size="small" scroll={{ x: 1300 }} />
        : <Alert type="success" showIcon message="当前 Context 未发现已知风险或门禁" />}
    </Card>
    <OverviewInstantRiskPanel context={context} parameterOptions={parameterOptions} config={riskConfig} onConfigChange={onRiskConfigChange} onOpenDrilldown={onOpenDrilldown} onOpenAggregateDrilldown={onOpenAggregateDrilldown} />
    <Card title="Dataset Overview" extra={<Tag color={overview.dataset_context.current_published_verified ? "success" : "error"}>Current+PUBLISHED {overview.dataset_context.current_published_verified ? "已验证" : "未验证"}</Tag>}>
      <Table rowKey={(row) => `${row.dataset_id}:${row.version_no}`} columns={datasetColumns} dataSource={overview.datasets} pagination={false} scroll={{ x: 900 }} />
    </Card>
    <Card title="Yield Trend">
      {yieldAvailable
        ? overview.yield_trend.length ? <EChart option={yieldOption} ariaLabel="服务端良率趋势" onEvents={chartEvents} /> : <Empty description="当前 Context 无良率点" />
        : <Alert type="info" showIcon message="Yield 能力不可用" description={yieldCapability?.message ?? yieldCapability?.reason_code ?? "服务端未声明 YIELD capability"} />}
    </Card>
    <Card title="Bin Pareto">
      {paretoAvailable
        ? overview.bin_pareto.length ? <><EChart option={paretoOption} ariaLabel="服务端 Bin Pareto" onEvents={chartEvents} /><Table rowKey={(row) => `${row.dataset_id}:${row.version_no}:${row.mapping_set_id}:${row.bin_type}:${row.bin_code}`} columns={binColumns} dataSource={overview.bin_pareto} pagination={false} size="small" scroll={{ x: 1120 }} /></> : <Empty description="当前 Context 无 Bin 结果" />
        : <Alert type="info" showIcon message="Bin Pareto 能力不可用" description={paretoCapability?.message ?? paretoCapability?.reason_code ?? "服务端未声明 BIN_PARETO capability"} />}
    </Card>
  </Space>;
}

export default AnalyticsOverviewSection;
