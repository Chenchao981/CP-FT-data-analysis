import { BarChartOutlined } from "@ant-design/icons";
import { Alert, Button, Empty } from "antd";
import type { EChartsCoreOption } from "echarts/core";
import { useMemo } from "react";

import type { StageResultRow, TestStage } from "../../api/stageData";
import { EChart, type EChartEventMap } from "../../components/EChart";
import { AnalysisResultFrame } from "../../components/AnalysisResultFrame";

interface StageResultsChartPanelProps {
  testStage: TestStage;
  rows: StageResultRow[];
  loading: boolean;
  canOpenAnalytics: boolean;
  onOpenAnalytics?: (datasetId: number, versionNo: number) => void;
}

interface StageChartPoint {
  value: number | null;
  datasetId: number;
  versionNo: number;
}

const isChartPoint = (value: unknown): value is StageChartPoint => {
  if (typeof value !== "object" || value == null) return false;
  const point = value as Partial<StageChartPoint>;
  return Number.isInteger(point.datasetId) && Number.isInteger(point.versionNo);
};

export function StageResultsChartPanel({ testStage, rows, loading, canOpenAnalytics, onOpenAnalytics }: StageResultsChartPanelProps) {
  const chartRows = useMemo(() => rows
    .filter((row) => row.dataset_id != null && row.dataset_version_no != null)
    .slice(0, 12)
    .reverse(), [rows]);
  const latest = chartRows.at(-1);
  const option = useMemo<EChartsCoreOption>(() => ({
    animation: false,
    tooltip: {
      trigger: "axis",
      renderMode: "richText",
      formatter: (items: unknown) => {
        const list = Array.isArray(items) ? items as Array<{ dataIndex?: number }> : [];
        const row = chartRows[list[0]?.dataIndex ?? -1];
        if (!row) return "";
        const yieldText = row.yield_rate == null ? "未知（未补零）" : `${(row.yield_rate * 100).toFixed(2)}%`;
        return `${row.product_name || "未识别产品"}\n批次：${row.lot_id || "—"}\n测试数量：${row.unit_count?.toLocaleString("zh-CN") ?? "—"}\n已知良率：${yieldText}`;
      },
    },
    legend: { data: ["测试数量", "已知良率"] },
    grid: { left: 72, right: 72, top: 54, bottom: 92 },
    xAxis: {
      type: "category",
      data: chartRows.map((row) => row.lot_id || `批次 #${row.import_batch_id}`),
      axisLabel: { interval: 0, rotate: chartRows.length > 6 ? 22 : 0, width: 110, overflow: "truncate" },
    },
    yAxis: [
      { type: "value", name: "测试数量", min: 0 },
      { type: "value", name: "良率 %", min: 0, max: 100 },
    ],
    toolbox: { feature: { dataZoom: {}, restore: {}, saveAsImage: { name: `${testStage.toLowerCase()}-recent-results` } } },
    series: [
      {
        name: "测试数量",
        type: "bar",
        barMaxWidth: 38,
        data: chartRows.map((row) => ({ value: row.unit_count, datasetId: row.dataset_id, versionNo: row.dataset_version_no })),
      },
      {
        name: "已知良率",
        type: "line",
        yAxisIndex: 1,
        connectNulls: false,
        symbolSize: 8,
        data: chartRows.map((row) => ({ value: row.yield_rate == null ? null : Number((row.yield_rate * 100).toFixed(4)), datasetId: row.dataset_id, versionNo: row.dataset_version_no })),
      },
    ],
  }), [chartRows, testStage]);
  const chartEvents = useMemo<EChartEventMap>(() => ({
    click: (payload) => {
      const point = (payload as { data?: unknown } | null)?.data;
      if (canOpenAnalytics && isChartPoint(point)) onOpenAnalytics?.(point.datasetId, point.versionNo);
    },
  }), [canOpenAnalytics, onOpenAnalytics]);

  return <AnalysisResultFrame
    className="stage-results-chart-card"
    loading={loading}
    title={`${testStage} 最近清洗结果图表`}
    scope="FORMAL"
    extra={latest && canOpenAnalytics ? <Button type="primary" icon={<BarChartOutlined />} onClick={() => onOpenAnalytics?.(latest.dataset_id!, latest.dataset_version_no!)}>打开最新完整图表</Button> : null}
  >
    {chartRows.length ? <>
      <EChart className="stage-results-chart" ariaLabel={`${testStage} 最近清洗结果图表`} option={option} onEvents={chartEvents} />
      {chartRows.some((row) => row.yield_rate == null) && <Alert type="info" showIcon message="部分结果没有已知良率" />}
    </> : !loading ? <Empty description="当前查询没有可展示的正式清洗结果" /> : null}
  </AnalysisResultFrame>;
}
