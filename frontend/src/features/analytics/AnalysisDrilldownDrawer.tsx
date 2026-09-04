import { useQuery } from "@tanstack/react-query";
import { Alert, Descriptions, Drawer, Empty, Space, Table, Tag, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";

import {
  getAnalyticsDrilldown,
  type AnalyticsContextRequest,
  type AnalyticsDetailBinEvaluation,
  type AnalyticsDetailMeasurement,
  type AnalyticsDetailMeasurementEvaluation,
  type AnalyticsDetailSourceFile,
} from "../../api/analytics";

export interface AnalysisDrilldownDrawerProps {
  context: AnalyticsContextRequest;
  drilldownKey: string | null;
  onClose: () => void;
}

const valueText = (measurement: AnalyticsDetailMeasurement) => {
  const value = measurement.value_numeric ?? measurement.value_text ?? "—";
  return `${value}${measurement.unit ? ` ${measurement.unit}` : ""}`;
};

const measurementColumns: ColumnsType<AnalyticsDetailMeasurement> = [
  { title: "Measurement", dataIndex: "measurement_id", width: 125 },
  { title: "参数", dataIndex: "parameter", width: 150, fixed: "left" },
  { title: "Canonical", dataIndex: "canonical_parameter_code", width: 160, render: (value) => value ?? "—" },
  { title: "Step / Seq", width: 120, render: (_, row) => `${row.step_code || "—"} / ${row.sequence_no}` },
  { title: "测量值 / Unit", width: 170, render: (_, row) => valueText(row) },
  { title: "测量状态", dataIndex: "status", width: 120 },
  { title: "Released Formal Spec", width: 220, render: (_, row) => <Space direction="vertical" size={0}><Tag color={row.formal_spec.status === "RESOLVED" ? "green" : "red"}>{row.formal_spec.status}</Tag><Typography.Text type="secondary">{row.formal_spec.spec_version ?? row.formal_spec.reason_code ?? "—"}</Typography.Text></Space> },
  { title: "Formal LSL / USL", width: 170, render: (_, row) => `${row.formal_spec.lsl_applied ?? "—"} / ${row.formal_spec.usl_applied ?? "—"}` },
  { title: "Tester Program Limit（非正式规格）", width: 220, render: (_, row) => <Space direction="vertical" size={0}><span>{`${row.program_lsl ?? "—"} / ${row.program_usl ?? "—"}`}</span><Typography.Text type="secondary">仅测试程序配置</Typography.Text></Space> },
  { title: "Current Evaluations", width: 150, render: (_, row) => <Tag>{row.evaluations.length}</Tag> },
];

const sourceColumns: ColumnsType<AnalyticsDetailSourceFile> = [
  { title: "Source File", dataIndex: "source_file_id", width: 110 },
  { title: "Receipt", dataIndex: "receipt_id", width: 100, render: (value) => value ?? "—" },
  { title: "Original File", dataIndex: "original_file_name", width: 240, render: (value) => value ?? "—" },
  { title: "SHA256", dataIndex: "sha256", width: 460, render: (value) => value ?? "MISSING" },
  { title: "Role / Ordinal", width: 150, render: (_, row) => `${row.file_role ?? "—"} / ${row.ordinal_no ?? "—"}` },
  { title: "Lineage", dataIndex: "lineage_basis", width: 190 },
];

const binColumns: ColumnsType<AnalyticsDetailBinEvaluation> = [
  { title: "Type / Raw", width: 140, render: (_, row) => `${row.bin_type} / ${row.raw_bin_code}` },
  { title: "Status", dataIndex: "mapping_status", width: 170, render: (value) => <Tag color={value === "MATCHED" ? "green" : "red"}>{value}</Tag> },
  { title: "Mapping Set / Version", width: 190, render: (_, row) => `${row.bin_mapping_set_id ?? "—"} / ${row.mapping_version ?? "—"}` },
  { title: "Mapped Bin", dataIndex: "mapped_bin_name", width: 160, render: (value) => value ?? "—" },
  { title: "Failure Snapshot", dataIndex: "failure_mode_snapshot", width: 190, render: (value) => value ?? "—" },
  { title: "PASS Snapshot", dataIndex: "is_pass_snapshot", width: 130, render: (value) => value == null ? "—" : value ? "PASS" : "FAIL" },
];

const evaluationColumns: ColumnsType<AnalyticsDetailMeasurementEvaluation> = [
  { title: "Type / Result", width: 180, render: (_, row) => <Space><Tag>{row.evaluation_type}</Tag><Tag>{row.evaluation_result}</Tag></Space> },
  { title: "Scope", dataIndex: "evaluation_scope_key", width: 220 },
  { title: "Rule / Version", width: 210, render: (_, row) => `${row.rule_code ?? "—"} / ${row.rule_version ?? "—"}` },
  { title: "Spec Binding / Item", width: 190, render: (_, row) => `${row.spec_binding_id ?? "—"} / ${row.spec_item_id ?? "—"}` },
  { title: "Applied LSL / USL", width: 170, render: (_, row) => `${row.lsl_applied ?? "—"} / ${row.usl_applied ?? "—"}` },
  { title: "Reason", dataIndex: "evaluation_reason", width: 260, render: (value) => value ?? "—" },
];

export function AnalysisDrilldownDrawer({ context, drilldownKey, onClose }: AnalysisDrilldownDrawerProps) {
  const validKey = drilldownKey !== null && /^UNIT:[1-9][0-9]{0,18}$/.test(drilldownKey);
  const query = useQuery({
    queryKey: ["analytics", "drilldown", context, drilldownKey],
    queryFn: () => getAnalyticsDrilldown({ ...context, drilldown_key: drilldownKey! }),
    enabled: validKey,
    retry: false,
  });
  const result = query.data;
  const unit = result?.unit;

  return <Drawer
    title={drilldownKey ? `Unit 钻取 · ${drilldownKey}` : "Unit 钻取"}
    open={drilldownKey !== null}
    onClose={onClose}
    width={920}
    destroyOnHidden
  >
    {!validKey && drilldownKey !== null && <Alert type="error" showIcon message="无效钻取身份" />}
    {query.isLoading && <Typography.Text type="secondary">正在加载服务端钻取记录…</Typography.Text>}
    {query.isError && <Alert type="error" showIcon message="钻取加载失败" description={query.error.message} />}
    {unit && <Space direction="vertical" size="large" style={{ width: "100%" }}>
      {result.warnings.length > 0 && <Alert type="warning" showIcon message="服务端提示" description={result.warnings.join("、")} />}
      <Descriptions title="Unit" bordered size="small" column={{ xs: 1, sm: 2 }}>
        <Descriptions.Item label="Logical Unit">{unit.logical_unit_key}</Descriptions.Item>
        <Descriptions.Item label="Result"><Tag>{unit.overall_result}</Tag></Descriptions.Item>
        <Descriptions.Item label="Lot">{unit.lot_id || "—"}</Descriptions.Item>
        <Descriptions.Item label="Wafer">{unit.wafer_id ?? "—"}</Descriptions.Item>
        <Descriptions.Item label="X / Y">{unit.x == null || unit.y == null ? "—" : `${unit.x} / ${unit.y}`}</Descriptions.Item>
        <Descriptions.Item label="Raw Soft / Hard Bin">{`${unit.soft_bin ?? "—"} / ${unit.hard_bin ?? "—"}`}</Descriptions.Item>
      </Descriptions>
      <Descriptions title="Source / Cleaner" bordered size="small" column={{ xs: 1, sm: 2 }}>
        <Descriptions.Item label="Source Identity">{unit.source_id}</Descriptions.Item>
        <Descriptions.Item label="Source Row">{unit.source_row_no ?? "—"}</Descriptions.Item>
        <Descriptions.Item label="Processing Run">{unit.processing_run_id}</Descriptions.Item>
        <Descriptions.Item label="Source File / Receipt">{`${unit.source_file_id} / ${unit.receipt_id ?? "—"}`}</Descriptions.Item>
        <Descriptions.Item label="Original File" span={2}>{unit.original_file_name ?? "—"}</Descriptions.Item>
        <Descriptions.Item label="SHA256" span={2}>{unit.sha256 ?? "MISSING"}</Descriptions.Item>
        <Descriptions.Item label="Tester">{unit.tester_id ?? "—"}</Descriptions.Item>
        <Descriptions.Item label="Program">{unit.program_version ?? "—"}</Descriptions.Item>
        <Descriptions.Item label="Cleaner Release" span={2}>{unit.cleaner_release ?? "—"}</Descriptions.Item>
      </Descriptions>
      <Table
        title={() => "Writer-verified Source Lineage"}
        rowKey={(row) => `${row.source_file_id}:${row.receipt_id ?? "none"}:${row.ordinal_no ?? "none"}`}
        columns={sourceColumns}
        dataSource={unit.source_files}
        pagination={false}
        scroll={{ x: 1250 }}
        size="small"
      />
      <Table
        title={() => "Versioned Bin Evaluations"}
        rowKey="unit_bin_evaluation_id"
        columns={binColumns}
        dataSource={unit.bin_evaluations}
        pagination={false}
        scroll={{ x: 1000 }}
        locale={{ emptyText: <Empty description="无 Bin Evaluation；Bin 分析失败关闭" /> }}
        size="small"
      />
      {unit.measurements.some((item) => item.formal_spec.status !== "RESOLVED") && <Alert
        type="error"
        showIcon
        message="部分测量项没有正式规格"
      />}
      <Table
        title={() => "Measurement / Released Formal Spec / Current Evaluation Chain"}
        rowKey="measurement_id"
        columns={measurementColumns}
        dataSource={unit.measurements}
        pagination={false}
        expandable={{
          expandedRowRender: (measurement) => <Table
            rowKey="evaluation_id"
            columns={evaluationColumns}
            dataSource={measurement.evaluations}
            pagination={false}
            scroll={{ x: 1200 }}
            locale={{ emptyText: <Empty description="无 current Measurement Evaluation" /> }}
            size="small"
          />,
          rowExpandable: (measurement) => measurement.evaluations.length > 0,
        }}
        scroll={{ x: 1550 }}
        locale={{ emptyText: <Empty description="该 Unit 无测量值" /> }}
        size="small"
      />
    </Space>}
  </Drawer>;
}
