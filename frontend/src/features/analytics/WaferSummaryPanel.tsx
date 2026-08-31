import { useQuery } from "@tanstack/react-query";
import { Alert, Button, Card, Empty, Select, Space, Table, Tag, Typography } from "antd";
import type { ColumnsType, TablePaginationConfig } from "antd/es/table";
import { useState } from "react";

import type { AnalyticsContextRequest } from "../../api/analytics";
import {
  getWaferSummary,
  type WaferParameterSummary,
  type WaferSummaryRow,
  type WaferSummarySort,
  type WaferSummarySortDirection,
} from "../../api/waferSummary";
import { ANALYSIS_COMPONENT_DEFAULTS, type WaferSummaryViewConfig } from "./context/analysisViewConfig";
import type { AnalyticsAggregateDrilldown } from "./sections/sectionTypes";

export interface WaferSummaryPanelProps {
  context: AnalyticsContextRequest;
  testStage: string | undefined;
  page: number;
  pageSize: number;
  onPaginationChange: (page: number, pageSize: number) => void;
  config?: WaferSummaryViewConfig;
  onConfigChange?: (patch: Partial<WaferSummaryViewConfig>) => void;
  onOpenAggregateDrilldown?: (target: AnalyticsAggregateDrilldown) => void;
}

const sortOptions: Array<{ label: string; value: WaferSummarySort }> = [
  { label: "Dataset", value: "DATASET" },
  { label: "Lot", value: "LOT" },
  { label: "Wafer", value: "WAFER" },
  { label: "Unit Count", value: "UNIT_COUNT" },
  { label: "Yield", value: "YIELD" },
];
const directionOptions: Array<{ label: string; value: WaferSummarySortDirection }> = [
  { label: "Ascending", value: "ASC" },
  { label: "Descending", value: "DESC" },
];
const columnSort: Partial<Record<WaferSummarySort, string>> = {
  DATASET: "dataset",
  LOT: "lot_id",
  WAFER: "wafer_id",
  UNIT_COUNT: "unit_count",
  YIELD: "yield_rate",
};
const sortForColumn = (sortBy: WaferSummarySort, direction: WaferSummarySortDirection, columnKey: string) =>
  columnSort[sortBy] === columnKey ? direction === "ASC" ? "ascend" as const : "descend" as const : null;
const formatValue = (value: number | null) => value == null ? "—" : String(value);

function parameterCell(summary: WaferParameterSummary | undefined) {
  if (!summary) return <Typography.Text type="secondary">—</Typography.Text>;
  return <Space direction="vertical" size={0}>
    <Typography.Text>Mean {formatValue(summary.mean)} {summary.unit ?? ""}</Typography.Text>
    <Typography.Text type="secondary">Min / Max {formatValue(summary.minimum)} / {formatValue(summary.maximum)}</Typography.Text>
    <Typography.Text type="secondary">Measured / Missing / OOS {summary.measured_count} / {summary.missing_count} / {summary.out_of_spec_count}</Typography.Text>
  </Space>;
}

export function WaferSummaryPanel({ context, testStage, page, pageSize, onPaginationChange, config: controlledConfig, onConfigChange: controlledOnConfigChange, onOpenAggregateDrilldown }: WaferSummaryPanelProps) {
  const [localConfig, setLocalConfig] = useState<WaferSummaryViewConfig>(() => ({ ...ANALYSIS_COMPONENT_DEFAULTS.waferSummary }));
  const config = controlledConfig ?? localConfig;
  const onConfigChange = (patch: Partial<WaferSummaryViewConfig>) => {
    if (!controlledConfig) setLocalConfig((current) => ({ ...current, ...patch }));
    controlledOnConfigChange?.(patch);
  };
  const sortBy = config.sortBy as WaferSummarySort;
  const sortDirection = config.sortDirection as WaferSummarySortDirection;
  const isCp = testStage === "CP";
  const query = useQuery({
    queryKey: ["analytics", "wafer-summary", context, page, pageSize, sortBy, sortDirection],
    queryFn: () => getWaferSummary({ ...context, page, page_size: pageSize, sort_by: sortBy, sort_direction: sortDirection }),
    enabled: isCp,
    retry: false,
  });

  const changeSort = (nextSort: WaferSummarySort, nextDirection: WaferSummarySortDirection = sortDirection) => {
    onConfigChange({ sortBy: nextSort, sortDirection: nextDirection });
    onPaginationChange(1, pageSize);
  };
  const columns: ColumnsType<WaferSummaryRow> = [
    { title: "Dataset", key: "dataset", width: 135, fixed: "left", sorter: true, sortOrder: sortForColumn(sortBy, sortDirection, "dataset"), render: (_, row) => `#${row.dataset_id} / V${row.version_no}` },
    { title: "Lot", dataIndex: "lot_id", key: "lot_id", width: 150, fixed: "left", sorter: true, sortOrder: sortForColumn(sortBy, sortDirection, "lot_id") },
    { title: "Wafer", dataIndex: "wafer_id", key: "wafer_id", width: 105, fixed: "left", sorter: true, sortOrder: sortForColumn(sortBy, sortDirection, "wafer_id") },
    { title: "Unit", dataIndex: "unit_count", key: "unit_count", width: 95, sorter: true, sortOrder: sortForColumn(sortBy, sortDirection, "unit_count") },
    { title: "PASS", dataIndex: "pass_count", width: 90 },
    { title: "FAIL", dataIndex: "fail_count", width: 90 },
    { title: "UNKNOWN", dataIndex: "unknown_count", width: 105 },
    { title: "ABORT", dataIndex: "abort_count", width: 90 },
    { title: "Known Denominator", dataIndex: "known_yield_denominator", width: 145 },
    {
      title: "Yield",
      dataIndex: "yield_rate",
      key: "yield_rate",
      width: 165,
      sorter: true,
      sortOrder: sortForColumn(sortBy, sortDirection, "yield_rate"),
      render: (value: number | null) => value == null
        ? <Typography.Text type="secondary">—（无 PASS/FAIL 分母）</Typography.Text>
        : `${(value * 100).toFixed(3)}%`,
    },
    ...context.parameters.map((parameter) => ({
      title: parameter,
      key: `parameter:${parameter}`,
      width: 280,
      render: (_: unknown, row: WaferSummaryRow) => parameterCell(row.parameters.find((item) => item.parameter === parameter)),
    })),
    {
      title: "下钻",
      key: "drilldown",
      width: 100,
      fixed: "right",
      render: (_: unknown, row: WaferSummaryRow) => <Button
        size="small"
        aria-label={`查看 ${row.lot_id} Wafer ${row.wafer_id} 明细`}
        disabled={!row.drilldown_context || !onOpenAggregateDrilldown}
        onClick={() => {
          if (!row.drilldown_context || !onOpenAggregateDrilldown) return;
          onOpenAggregateDrilldown({
            dataset: { dataset_id: row.drilldown_context.dataset_id, version_no: row.drilldown_context.version_no },
            filters: { lot_ids: [row.drilldown_context.lot_id], wafer_ids: [row.drilldown_context.wafer_id] },
            parameters: [...context.parameters],
          });
        }}
      >Detail</Button>,
    },
  ];

  const onTableChange = (pagination: TablePaginationConfig, _filters: unknown, sorter: unknown, extra: { action: string }) => {
    if (extra.action === "sort") {
      const selected = Array.isArray(sorter) ? sorter[0] : sorter as { columnKey?: unknown; order?: unknown };
      const keyToSort: Record<string, WaferSummarySort> = { dataset: "DATASET", lot_id: "LOT", wafer_id: "WAFER", unit_count: "UNIT_COUNT", yield_rate: "YIELD" };
      const nextSort = typeof selected?.columnKey === "string" ? keyToSort[selected.columnKey] : undefined;
      if (nextSort) changeSort(nextSort, selected.order === "descend" ? "DESC" : "ASC");
      return;
    }
    onPaginationChange(pagination.current ?? page, pagination.pageSize ?? pageSize);
  };

  if (testStage && !isCp) return <Alert type="info" showIcon message="Wafer Summary 只适用于 CP Dataset" description={`当前阶段为 ${testStage}，未请求后端 Wafer Summary。`} />;

  return <Card title="Wafer Summary" extra={<Space><Tag>后端分页</Tag><Tag>后端排序</Tag></Space>}>
    <Space wrap style={{ marginBottom: 12 }}>
      <Select aria-label="Wafer Summary 排序字段" value={sortBy} options={sortOptions} onChange={(value) => changeSort(value)} style={{ minWidth: 160 }} />
      <Select aria-label="Wafer Summary 排序方向" value={sortDirection} options={directionOptions} onChange={(value) => changeSort(sortBy, value)} style={{ minWidth: 150 }} />
      <Typography.Text type="secondary">参数列来自统一 Context；Mean/Min/Max/Measured/Missing/OOS 全部使用后端汇总。</Typography.Text>
    </Space>
    {query.isError && <Alert type="error" showIcon message="Wafer Summary 加载失败" description={query.error.message} style={{ marginBottom: 12 }} />}
    {query.data?.warnings.length ? <Alert type="warning" showIcon message="服务端提示" description={query.data.warnings.join("、")} style={{ marginBottom: 12 }} /> : null}
    {query.data && <Space wrap style={{ marginBottom: 12 }}><Tag>合同 {query.data.contract_version}</Tag><Tag>{query.data.capabilities[0]?.code ?? "WAFER_SUMMARY"} {query.data.capabilities[0]?.status ?? "UNKNOWN"}</Tag><Tag>总数 {query.data.total}</Tag><Tag>排序 {query.data.sort_by} {query.data.sort_direction}</Tag></Space>}
    <Table<WaferSummaryRow>
      rowKey={(row) => `${row.dataset_id}:${row.version_no}:${row.lot_id}:${row.wafer_id}`}
      columns={columns}
      dataSource={query.data?.items ?? []}
      loading={query.isLoading}
      scroll={{ x: 1290 + context.parameters.length * 280 }}
      pagination={{ current: query.data?.page ?? page, pageSize: query.data?.page_size ?? pageSize, total: query.data?.total ?? 0, showSizeChanger: true }}
      onChange={onTableChange}
      locale={{ emptyText: <Empty description="当前 Context 无 Wafer Summary" /> }}
    />
  </Card>;
}
