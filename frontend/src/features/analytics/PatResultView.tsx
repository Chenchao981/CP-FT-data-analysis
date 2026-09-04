import { Col, Row, Statistic, Table, Tag } from "antd";
import type { ColumnsType } from "antd/es/table";
import type { EChartsCoreOption } from "echarts/core";
import type { ReactNode } from "react";
import { useMemo } from "react";

import { EChart } from "../../components/EChart";
import { AnalysisResultFrame, type AnalysisResultScope } from "../../components/AnalysisResultFrame";

export interface PatResultViewRow {
  key: string;
  label: string;
  count: number;
  missingCount?: number | null;
  q1?: number | null;
  median?: number | null;
  q3?: number | null;
  lowerLimit?: number | null;
  upperLimit?: number | null;
  outlierCount?: number | null;
  status?: string | null;
  actions?: ReactNode;
}

const value = (item?: number | null) => item == null ? "—" : Number(item.toPrecision(8)).toString();

export interface PatResultViewProps {
  title?: string;
  labelTitle?: string;
  rows: PatResultViewRow[];
  scope?: AnalysisResultScope;
}

export function PatResultView({ title = "PAT 分析结果", labelTitle = "参数 / 分组", rows, scope = "FORMAL" }: PatResultViewProps) {
  const option = useMemo<EChartsCoreOption>(() => ({
    animation: false,
    tooltip: { trigger: "axis" },
    legend: { data: ["下限", "中位数", "上限"] },
    grid: { left: 72, right: 36, top: 48, bottom: rows.length > 12 ? 86 : 56 },
    xAxis: { type: "category", data: rows.map((item) => item.label), axisLabel: { hideOverlap: true } },
    yAxis: { type: "value", scale: true, name: "参数值" },
    dataZoom: rows.length > 12 ? [{ type: "inside" }, { type: "slider", bottom: 18 }] : [],
    toolbox: { feature: { dataZoom: {}, restore: {}, saveAsImage: { name: "pat-result" } } },
    series: [
      { name: "下限", type: "line", symbolSize: 5, connectNulls: false, data: rows.map((item) => item.lowerLimit ?? null) },
      { name: "中位数", type: "line", symbolSize: 7, connectNulls: false, data: rows.map((item) => item.median ?? null) },
      { name: "上限", type: "line", symbolSize: 5, connectNulls: false, data: rows.map((item) => item.upperLimit ?? null) },
    ],
  }), [rows]);
  const hasActions = rows.some((item) => item.actions != null);
  const columns: ColumnsType<PatResultViewRow> = [
    { title: labelTitle, dataIndex: "label", fixed: "left", width: 240 },
    { title: "有效 / 缺失", width: 120, render: (_, row) => `${row.count.toLocaleString("zh-CN")} / ${row.missingCount == null ? "—" : row.missingCount.toLocaleString("zh-CN")}` },
    { title: "Q1 / 中位数 / Q3", width: 220, render: (_, row) => `${value(row.q1)} / ${value(row.median)} / ${value(row.q3)}` },
    { title: "PAT 下限 / 上限", width: 190, render: (_, row) => `${value(row.lowerLimit)} / ${value(row.upperLimit)}` },
    { title: "异常数", dataIndex: "outlierCount", width: 90, render: (item) => item == null ? "—" : item },
    { title: "状态", dataIndex: "status", width: 110, render: (item) => item ? <Tag color={item === "OK" || item === "UPDATED" ? "success" : "default"}>{item}</Tag> : "—" },
  ];
  if (hasActions) columns.push({ title: "操作", dataIndex: "actions", fixed: "right", width: 100, render: (item) => item ?? "—" });

  return <AnalysisResultFrame title={title} scope={scope} className="pat-result-view">
    <Row gutter={[12, 12]} className="pat-result-metrics">
      <Col xs={12} md={6}><Statistic title="参数 / 分组" value={rows.length} /></Col>
      <Col xs={12} md={6}><Statistic title="有效数据" value={rows.reduce((sum, item) => sum + item.count, 0)} /></Col>
      <Col xs={12} md={6}><Statistic title="异常数量" value={rows.reduce((sum, item) => sum + (item.outlierCount ?? 0), 0)} /></Col>
      <Col xs={12} md={6}><Statistic title="已计算上下限" value={rows.filter((item) => item.lowerLimit != null || item.upperLimit != null).length} /></Col>
    </Row>
    {rows.length > 0 && <EChart ariaLabel="PAT 分析结果图表" option={option} className="pat-result-chart" />}
    <Table rowKey="key" size="small" pagination={{ pageSize: 20, hideOnSinglePage: true }} scroll={{ x: hasActions ? 1050 : 950 }} columns={columns} dataSource={rows} />
  </AnalysisResultFrame>;
}
