import { useQuery } from "@tanstack/react-query";
import { Alert, Button, Card, Empty, Segmented, Space, Table, Tag, Typography } from "antd";
import type { ColumnsType, TablePaginationConfig } from "antd/es/table";
import type { FilterValue, SorterResult } from "antd/es/table/interface";
import { useMemo, useState } from "react";

import {
  getAnalyticsDetail,
  type AnalyticsDetailMeasurement,
  type AnalyticsDetailRow,
  type AnalyticsDetailSort,
  type AnalyticsDetailView,
  type AnalyticsSortDirection,
} from "../../../api/analytics";
import { ANALYSIS_COMPONENT_DEFAULTS, type DetailViewConfig } from "../context/analysisViewConfig";
import type { AnalyticsDrilldownOpener, AnalyticsSectionContext } from "./sectionTypes";

export interface AnalyticsDetailSectionProps extends AnalyticsSectionContext, AnalyticsDrilldownOpener {
  page: number;
  pageSize: number;
  onPaginationChange: (page: number, pageSize: number) => void;
  config?: DetailViewConfig;
  onConfigChange?: (patch: Partial<DetailViewConfig>) => void;
}

interface LongRow {
  key: string;
  unit: AnalyticsDetailRow;
  measurement: AnalyticsDetailMeasurement | null;
}

const measurementValue = (measurement: AnalyticsDetailMeasurement) => {
  const value = measurement.value_numeric ?? measurement.value_text ?? "—";
  return `${value}${measurement.unit ? ` ${measurement.unit}` : ""}`;
};

export function AnalyticsDetailSection({ context, focusDatasetId, page, pageSize, onPaginationChange, onOpenDrilldown, config: controlledConfig, onConfigChange: controlledOnConfigChange }: AnalyticsDetailSectionProps) {
  const [localConfig, setLocalConfig] = useState<DetailViewConfig>(() => ({ ...ANALYSIS_COMPONENT_DEFAULTS.detail }));
  const config = controlledConfig ?? localConfig;
  const onConfigChange = (patch: Partial<DetailViewConfig>) => {
    if (!controlledConfig) setLocalConfig((current) => ({ ...current, ...patch }));
    controlledOnConfigChange?.(patch);
  };
  const view = config.view as AnalyticsDetailView;
  const sortBy = config.sortBy as AnalyticsDetailSort;
  const sortDirection = config.sortDirection as AnalyticsSortDirection;
  const evaluationFilter = config.evaluation_filter;
  const measurementFilter = config.measurement_filter;
  const query = useQuery({
    queryKey: ["analytics", "detail", context, focusDatasetId, page, pageSize, view, sortBy, sortDirection, evaluationFilter, measurementFilter],
    queryFn: () => getAnalyticsDetail({ ...context, focus_dataset_id: focusDatasetId, page, page_size: pageSize, view, sort_by: sortBy, sort_direction: sortDirection, ...(evaluationFilter ? { evaluation_filter: evaluationFilter } : {}), ...(measurementFilter ? { measurement_filter: measurementFilter } : {}) }),
    retry: false,
  });

  const sortOrder = (field: AnalyticsDetailSort) => sortBy === field ? (sortDirection === "ASC" ? "ascend" : "descend") : null;

  const openButton = (row: AnalyticsDetailRow) => <Button size="small" onClick={() => onOpenDrilldown(row.drilldown_key)}>钻取</Button>;
  const wideColumns: ColumnsType<AnalyticsDetailRow> = [
    { title: "Unit", key: "UNIT_SEQUENCE", dataIndex: "logical_unit_key", width: 190, fixed: "left", ellipsis: true, sorter: true, sortOrder: sortOrder("UNIT_SEQUENCE") },
    { title: "Lot", key: "LOT", dataIndex: "lot_id", width: 140, sorter: true, sortOrder: sortOrder("LOT") },
    { title: "Wafer", key: "WAFER", dataIndex: "wafer_id", width: 100, render: (value) => value ?? "—", sorter: true, sortOrder: sortOrder("WAFER") },
    { title: "X / Y", width: 95, render: (_, row) => row.x == null || row.y == null ? "—" : `${row.x} / ${row.y}` },
    { title: "Soft Bin", key: "SOFT_BIN", dataIndex: "soft_bin", width: 105, render: (value) => value ?? "—", sorter: true, sortOrder: sortOrder("SOFT_BIN") },
    { title: "Hard Bin", key: "HARD_BIN", dataIndex: "hard_bin", width: 105, render: (value) => value ?? "—", sorter: true, sortOrder: sortOrder("HARD_BIN") },
    { title: "Result", key: "RESULT", dataIndex: "overall_result", width: 105, render: (value) => <Tag>{value}</Tag>, sorter: true, sortOrder: sortOrder("RESULT") },
    { title: "Source", width: 220, ellipsis: true, render: (_, row) => `${row.source_id} · File #${row.source_file_id}` },
    { title: "Original File", dataIndex: "original_file_name", width: 220, ellipsis: true, render: (value) => value ?? "—" },
    { title: "Source Row", key: "SOURCE_ROW", dataIndex: "source_row_no", width: 125, render: (value) => value ?? "—", sorter: true, sortOrder: sortOrder("SOURCE_ROW") },
    { title: "Tester", dataIndex: "tester_id", width: 130, render: (value) => value ?? "—" },
    { title: "Program", dataIndex: "program_version", width: 150, render: (value) => value ?? "—" },
    { title: "Cleaner", dataIndex: "cleaner_release", width: 150, render: (value) => value ?? "—" },
    { title: "Bin Evaluation", width: 180, render: (_, row) => row.bin_evaluations.length ? row.bin_evaluations.map((item) => `${item.bin_type}:${item.mapping_status}`).join(" · ") : "NO EVALUATION" },
    {
      title: "Measurements",
      dataIndex: "measurements",
      width: 380,
      render: (measurements: AnalyticsDetailMeasurement[]) => measurements.length
        ? <Space direction="vertical" size={0}>{measurements.map((item) => <Typography.Text key={item.measurement_id}>{item.parameter}: {measurementValue(item)} · {item.status} · Formal {item.formal_spec.status} · {item.evaluations.length} evaluations</Typography.Text>)}</Space>
        : <Typography.Text type="secondary">无测量值</Typography.Text>,
    },
    { title: "操作", width: 90, fixed: "right", render: (_, row) => openButton(row) },
  ];

  const longRows = useMemo<LongRow[]>(() => query.data?.items.flatMap<LongRow>((unit) => unit.measurements.length
    ? unit.measurements.map((measurement): LongRow => ({ key: `${unit.drilldown_key}:${measurement.measurement_id}`, unit, measurement }))
    : [{ key: `${unit.drilldown_key}:empty`, unit, measurement: null }]) ?? [], [query.data]);
  const longColumns: ColumnsType<LongRow> = [
    { title: "Unit", key: "UNIT_SEQUENCE", width: 190, fixed: "left", render: (_, row) => row.unit.logical_unit_key, sorter: true, sortOrder: sortOrder("UNIT_SEQUENCE") },
    { title: "Lot / Wafer", key: "LOT", width: 190, render: (_, row) => `${row.unit.lot_id} / ${row.unit.wafer_id ?? "—"}`, sorter: true, sortOrder: sortOrder("LOT") },
    { title: "Source", width: 210, render: (_, row) => `${row.unit.source_id} · File #${row.unit.source_file_id}` },
    { title: "参数", width: 160, render: (_, row) => row.measurement?.parameter ?? "—" },
    { title: "Canonical", width: 160, render: (_, row) => row.measurement?.canonical_parameter_code ?? "—" },
    { title: "测量值 / Unit", width: 170, render: (_, row) => row.measurement ? measurementValue(row.measurement) : "—" },
    { title: "状态", width: 120, render: (_, row) => row.measurement?.status ?? "—" },
    { title: "Formal Spec", width: 180, render: (_, row) => row.measurement ? `${row.measurement.formal_spec.status} / ${row.measurement.formal_spec.spec_version ?? "—"}` : "—" },
    { title: "Evaluations", width: 220, render: (_, row) => row.measurement?.evaluations.map((item) => `${item.evaluation_type}:${item.rule_version ?? "—"}`).join(" · ") || "—" },
    { title: "操作", width: 90, fixed: "right", render: (_, row) => openButton(row.unit) },
  ];

  const sortFields = new Set<AnalyticsDetailSort>(["UNIT_SEQUENCE", "LOT", "WAFER", "SOURCE_ROW", "RESULT", "SOFT_BIN", "HARD_BIN"]);
  const onTableChange = <T,>(
    pagination: TablePaginationConfig,
    _filters: Record<string, FilterValue | null>,
    sorter: SorterResult<T> | SorterResult<T>[],
  ) => {
    const selected = Array.isArray(sorter) ? sorter[0] : sorter;
    const candidate = typeof selected?.columnKey === "string" ? selected.columnKey as AnalyticsDetailSort : null;
    if (candidate && sortFields.has(candidate) && selected.order) {
      const nextDirection: AnalyticsSortDirection = selected.order === "descend" ? "DESC" : "ASC";
      const changed = candidate !== sortBy || nextDirection !== sortDirection;
      onConfigChange({ sortBy: candidate, sortDirection: nextDirection });
      onPaginationChange(changed ? 1 : pagination.current ?? page, pagination.pageSize ?? pageSize);
      return;
    }
    onPaginationChange(pagination.current ?? page, pagination.pageSize ?? pageSize);
  };

  return <Card
    title="Unit 明细"
    extra={<Segmented<AnalyticsDetailView> aria-label="明细视图" value={view} options={[{ label: "WIDE", value: "WIDE" }, { label: "LONG", value: "LONG" }]} onChange={(value) => onConfigChange({ view: value })} />}
  >
    {query.isError && <Alert type="error" showIcon message="Detail 加载失败" description={query.error.message} style={{ marginBottom: 12 }} />}
    {query.data?.evaluation_filter && <Alert type="info" showIcon message="当前为持久化评价风险限定总体" description={`${query.data.evaluation_filter.evaluation_type} · ${query.data.evaluation_filter.rule_code ?? "UNVERSIONED_RULE"}@${query.data.evaluation_filter.rule_version ?? "UNVERSIONED"} · Result ${query.data.evaluation_filter.evaluation_results.join(" / ")}；后端只返回至少包含一条完全匹配 current evaluation 的 Unit。`} style={{ marginBottom: 12 }} />}
    {query.data?.measurement_filter && <Alert type="info" showIcon message="当前为测量聚合限定总体" description={`${query.data.measurement_filter.parameter} · ${query.data.measurement_filter.lower_bound ?? "-∞"}${query.data.measurement_filter.lower_inclusive ? " ≤" : " <"} value ${query.data.measurement_filter.upper_inclusive ? "≤" : "<"} ${query.data.measurement_filter.upper_bound ?? "+∞"}；Unit 资格由后端 Measurement EXISTS 精确限定。`} style={{ marginBottom: 12 }} />}
    {query.data?.warnings.length ? <Alert type="warning" showIcon message="服务端提示" description={query.data.warnings.join("、")} style={{ marginBottom: 12 }} /> : null}
    {query.data && <Space wrap style={{ marginBottom: 12 }}><Tag>服务端排序 {query.data.sort_by} {query.data.sort_direction}</Tag><Typography.Text type="secondary">仅白名单字段可排序，排序与分页均由后端执行。</Typography.Text></Space>}
    {view === "LONG" && <Alert type="info" showIcon message="LONG 表格展开 Measurement" description="分页 total 仍以后端 Unit 数为准；前端只展开本页返回的 measurements。" style={{ marginBottom: 12 }} />}
    {view === "WIDE" ? <Table<AnalyticsDetailRow>
      rowKey="drilldown_key"
      columns={wideColumns}
      dataSource={query.data?.items ?? []}
      loading={query.isLoading}
      scroll={{ x: 1880 }}
      pagination={{ current: query.data?.page ?? page, pageSize: query.data?.page_size ?? pageSize, total: query.data?.total ?? 0, showSizeChanger: true }}
      onChange={onTableChange}
      locale={{ emptyText: <Empty description="当前 Context 无 Unit 明细" /> }}
    /> : <Table<LongRow>
      rowKey="key"
      columns={longColumns}
      dataSource={longRows}
      loading={query.isLoading}
      scroll={{ x: 1450 }}
      pagination={{ current: query.data?.page ?? page, pageSize: query.data?.page_size ?? pageSize, total: query.data?.total ?? 0, showSizeChanger: true }}
      onChange={onTableChange}
      locale={{ emptyText: <Empty description="当前 Context 无 Unit 明细" /> }}
    />}
  </Card>;
}

export default AnalyticsDetailSection;
