import { CloseCircleOutlined, DownloadOutlined, ExportOutlined, ReloadOutlined, SafetyCertificateOutlined } from "@ant-design/icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, Button, Card, Descriptions, Form, Input, InputNumber, Pagination, Popconfirm, Select, Space, Table, Tag, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useMemo, useState } from "react";

import type { AnalyticsContextRequest, AnalyticsRuleContext } from "../../api/analytics";
import {
  ANALYTICS_EXPORT_CONTRACT_VERSION,
  ANALYTICS_EXPORT_TEMPLATES,
  cancelAnalyticsExport,
  createAnalyticsExport,
  downloadAnalyticsExportArtifact,
  getAnalyticsExportDownloadMetadata,
  listAnalyticsExports,
  type AnalyticsExportFormat,
  type AnalyticsExportRecord,
  type AnalyticsExportScope,
  type AnalyticsExportTemplateCode,
  type CreateAnalyticsExportRequest,
} from "../../api/analyticsExports";
import { formatUtcDateTime } from "../../utils/dateTime";
import { useAuth } from "../auth/AuthContext";
import { createDefaultAnalysisViewState, type AnalysisDisplayState, type AnalysisViewState } from "./context/analysisViewState";
import { exportChartConfig } from "./context/analysisPresentation";

interface ExportValues {
  templateCode: AnalyticsExportTemplateCode;
  exportScope: AnalyticsExportScope;
  exportFormat: AnalyticsExportFormat;
  artifactTtlHours: number;
  reason: string;
}

export interface AnalyticsExportPanelProps {
  context: AnalyticsContextRequest;
  ruleContext: AnalyticsRuleContext;
  testStage: string | undefined;
  focusDatasetId: number;
  page: number;
  pageSize: number;
  viewState?: AnalysisViewState;
  /** @deprecated test/backward compatibility; production passes the complete viewState. */
  chartDisplayState?: AnalysisDisplayState;
}

const statusColor: Record<string, string> = {
  QUEUED: "processing", RUNNING: "processing", SUCCESS: "success", FAILED: "error", CANCELLED: "default", EXPIRED: "default",
};
const statusLabel: Record<string, string> = {
  QUEUED: "排队中", RUNNING: "生成中", SUCCESS: "成功", FAILED: "失败", CANCELLED: "已取消", EXPIRED: "已过期",
};
const scopeLabel: Record<AnalyticsExportScope, string> = {
  CURRENT_PAGE: "当前页", FILTERED_RESULT: "全部筛选结果", FULL_DATASET: "完整 Dataset", REPORT: "分析报告",
};
const sizeText = (bytes: number) => bytes < 1024 ? `${bytes} B`
  : bytes < 1024 ** 2 ? `${(bytes / 1024).toFixed(1)} KB`
    : bytes < 1024 ** 3 ? `${(bytes / 1024 ** 2).toFixed(1)} MB`
      : `${(bytes / 1024 ** 3).toFixed(1)} GB`;
const newIdempotencyKey = () => {
  const random = globalThis.crypto?.randomUUID?.() ?? Math.random().toString(36).slice(2);
  return `analytics-${Date.now()}-${random}`.slice(0, 128);
};
const cloneContext = (context: AnalyticsContextRequest): AnalyticsContextRequest => ({
  datasets: context.datasets.map((item) => ({ ...item })),
  filters: {
    lot_ids: [...context.filters.lot_ids], wafer_ids: [...context.filters.wafer_ids], bin_codes: [...context.filters.bin_codes],
    overall_results: [...context.filters.overall_results], source_ids: [...context.filters.source_ids], tester_ids: [...context.filters.tester_ids],
    program_versions: [...context.filters.program_versions], test_conditions: [...context.filters.test_conditions],
  },
  parameters: [...context.parameters],
});

export function AnalyticsExportPanel({ context, ruleContext, testStage, focusDatasetId, page, pageSize, viewState, chartDisplayState }: AnalyticsExportPanelProps) {
  const defaults = createDefaultAnalysisViewState();
  const effectiveViewState = viewState ?? { ...defaults, display: chartDisplayState ?? defaults.display };
  const { can } = useAuth();
  const canExport = can("EXPORT_DATA") && can("DATASET_READ");
  const queryClient = useQueryClient();
  const [form] = Form.useForm<ExportValues>();
  const [listPage, setListPage] = useState(1);
  const [listPageSize, setListPageSize] = useState(10);
  const [idempotencyKey, setIdempotencyKey] = useState(newIdempotencyKey);
  const [metadataJobId, setMetadataJobId] = useState<number>();
  const [cancelTarget, setCancelTarget] = useState<AnalyticsExportRecord>();
  const [cancelReason, setCancelReason] = useState("");
  const [success, setSuccess] = useState<string>();
  const eligibleTemplates = useMemo(() => ANALYTICS_EXPORT_TEMPLATES.filter((item) => testStage === undefined || item.testStages.includes(testStage as "CP" | "FT")), [testStage]);
  const watchedTemplate = Form.useWatch("templateCode", form) ?? eligibleTemplates[0]?.code;
  const watchedScope = Form.useWatch("exportScope", form);
  const selectedTemplate = eligibleTemplates.find((item) => item.code === watchedTemplate) ?? eligibleTemplates[0];

  const listQuery = useQuery({
    queryKey: ["analytics", "exports", listPage, listPageSize],
    queryFn: () => listAnalyticsExports({ page: listPage, page_size: listPageSize }),
    enabled: canExport,
    retry: false,
    refetchInterval: (query) => query.state.data?.items.some((item) => item.status === "QUEUED" || item.status === "RUNNING") ? 3000 : false,
  });
  const metadataQuery = useQuery({
    queryKey: ["analytics", "exports", metadataJobId, "download-metadata"],
    queryFn: () => getAnalyticsExportDownloadMetadata(metadataJobId!),
    enabled: canExport && metadataJobId !== undefined,
    retry: false,
  });
  const refresh = async () => queryClient.invalidateQueries({ queryKey: ["analytics", "exports"] });
  const createMutation = useMutation({
    mutationFn: (values: ExportValues) => {
      const request: CreateAnalyticsExportRequest = {
        ...cloneContext(context),
        contract_version: ANALYTICS_EXPORT_CONTRACT_VERSION,
        export_scope: values.exportScope,
        export_format: values.exportFormat,
        template_code: values.templateCode,
        template_version: "v1",
        rule_context: {
          spec_versions: [...ruleContext.spec_versions],
          bin_mapping_versions: [...ruleContext.bin_mapping_versions],
          evaluation_rule_versions: [...ruleContext.evaluation_rule_versions],
        },
        chart_config: exportChartConfig(values.templateCode, effectiveViewState, context.parameters, focusDatasetId),
        display_config: {
          section: effectiveViewState.display.section,
          page,
          page_size: pageSize,
          focus_dataset_id: focusDatasetId,
        },
        artifact_ttl_hours: values.artifactTtlHours,
        idempotency_key: idempotencyKey,
        reason: values.reason.trim(),
        ...(values.exportScope === "CURRENT_PAGE" ? { page, page_size: pageSize } : {}),
      };
      return createAnalyticsExport(request);
    },
    onSuccess: async (record) => {
      setSuccess(`Export Job #${record.export_job_id} 已提交${record.idempotent_replay ? "（幂等重放）" : ""}`);
      setMetadataJobId(record.export_job_id);
      setIdempotencyKey(newIdempotencyKey());
      await refresh();
    },
  });
  const cancelMutation = useMutation({
    mutationFn: () => cancelAnalyticsExport(cancelTarget!.export_job_id, {
      confirmation: "CANCEL",
      expected_row_version: cancelTarget!.row_version,
      reason: cancelReason.trim(),
    }),
    onSuccess: async (record) => {
      setSuccess(`Export Job #${record.export_job_id} 已取消`);
      setCancelTarget(undefined);
      setCancelReason("");
      await refresh();
    },
  });
  const downloadMutation = useMutation({
    mutationFn: (artifact: { export_artifact_id: number; file_name: string }) => downloadAnalyticsExportArtifact(
      metadataJobId!,
      artifact.export_artifact_id,
      artifact.file_name,
    ),
  });

  const columns = useMemo<ColumnsType<AnalyticsExportRecord>>(() => [
    { title: "Job", dataIndex: "export_job_id", key: "job", width: 90, render: (value: number) => `#${value}` },
    { title: "状态", dataIndex: "status", key: "status", width: 110, render: (value: string) => <Tag color={statusColor[value]}>{statusLabel[value] ?? value}</Tag> },
    { title: "Template", key: "template", render: (_, row) => <Space direction="vertical" size={0}><Typography.Text>{row.template_code}@{row.template_version}</Typography.Text><Typography.Text type="secondary">{scopeLabel[row.export_scope]} · {row.export_format}</Typography.Text></Space> },
    { title: "Context / 显示", key: "context", render: (_, row) => <Space direction="vertical" size={0}><Typography.Text code title="Context Hash">C {row.context_hash.slice(0, 12)}…</Typography.Text><Typography.Text code title="Presentation Hash">P {row.presentation_hash.slice(0, 12)}…</Typography.Text><Typography.Text type="secondary">{row.datasets.map((item) => `#${item.dataset_id}/V${item.version_no}`).join(", ")}</Typography.Text></Space> },
    { title: "行数", dataIndex: "exported_row_count", key: "rows", width: 100, render: (value: number | null) => value ?? "—" },
    { title: "请求时间", dataIndex: "requested_at_utc", key: "requested", width: 180, render: formatUtcDateTime },
    { title: "操作", key: "actions", width: 190, render: (_, row) => <Space>
      <Button size="small" icon={<SafetyCertificateOutlined />} onClick={() => setMetadataJobId(row.export_job_id)}>制品元数据</Button>
      {(row.status === "QUEUED" || row.status === "RUNNING") && <Button danger size="small" icon={<CloseCircleOutlined />} onClick={() => setCancelTarget(row)}>取消</Button>}
    </Space> },
  ], []);

  if (!canExport) return <Card title="一键生成报告"><Alert type="warning" showIcon message="无导出权限" /></Card>;
  if (!selectedTemplate) return <Card title="一键生成报告"><Alert type="error" showIcon message="当前测试阶段没有可用报告模板" /></Card>;

  const metadata = metadataQuery.data;
  return <Card title="一键生成 HTML / PDF / XLSX 报告" extra={<Button icon={<ReloadOutlined />} onClick={() => void listQuery.refetch()} loading={listQuery.isFetching}>刷新</Button>}>
    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
      {(listQuery.data?.integrity_blocked_count ?? 0) > 0 && <Alert
        type="error"
        showIcon
        message="历史 Job 完整性阻断"
        description={`${listQuery.data!.integrity_blocked_count} 个：${listQuery.data!.integrity_blocked_job_ids.map((id) => `#${id}`).join("、")}`}
      />}
      {success && <Alert type="success" showIcon closable onClose={() => setSuccess(undefined)} message={success} />}
      {createMutation.error && <Alert type="error" showIcon message="Export Job 提交失败" description={createMutation.error.message} />}
      {cancelMutation.error && <Alert type="error" showIcon message="Export Job 取消失败" description={cancelMutation.error.message} />}
      <Form<ExportValues>
        form={form}
        layout="vertical"
        initialValues={{ templateCode: selectedTemplate.code, exportScope: selectedTemplate.scopes[0], exportFormat: selectedTemplate.formats[0], artifactTtlHours: 24 }}
        onValuesChange={(changed) => {
          if (changed.templateCode) {
            const template = eligibleTemplates.find((item) => item.code === changed.templateCode);
            if (template) form.setFieldsValue({ exportScope: template.scopes[0], exportFormat: template.formats[0] });
          }
        }}
        onFinish={(values) => createMutation.mutate(values)}
      >
        <Space align="start" wrap>
          <Form.Item label="Template" name="templateCode" rules={[{ required: true }]}><Select aria-label="Export Template" options={eligibleTemplates.map((item) => ({ label: `${item.code}@${item.version}`, value: item.code }))} style={{ width: 250 }} /></Form.Item>
          <Form.Item label="Scope" name="exportScope" rules={[{ required: true }]}><Select aria-label="Export Scope" options={selectedTemplate.scopes.map((value) => ({ label: scopeLabel[value], value }))} style={{ width: 180 }} /></Form.Item>
          <Form.Item label="Format" name="exportFormat" rules={[{ required: true }]}><Select aria-label="Export Format" options={selectedTemplate.formats.map((value) => ({ label: value, value }))} style={{ width: 130 }} /></Form.Item>
          <Form.Item label="制品保留小时" name="artifactTtlHours" rules={[{ required: true, type: "number", min: 1, max: 168 }]}><InputNumber aria-label="Export TTL" min={1} max={168} /></Form.Item>
          <Form.Item label="导出原因" name="reason" rules={[{ required: true, min: 8, max: 1000 }]}><Input aria-label="Export 原因" maxLength={1000} style={{ width: 320 }} /></Form.Item>
          <Form.Item label=" "><Button htmlType="submit" type="primary" icon={<ExportOutlined />} loading={createMutation.isPending}>生成报告</Button></Form.Item>
        </Space>
      </Form>
      <Typography.Text type="secondary">Idempotency Key：<Typography.Text code>{idempotencyKey}</Typography.Text>{watchedScope === "CURRENT_PAGE" ? ` · 当前页 ${page} / ${pageSize} 行` : ""}</Typography.Text>
      <Table<AnalyticsExportRecord> rowKey="export_job_id" columns={columns} dataSource={listQuery.data?.items ?? []} loading={listQuery.isLoading} pagination={false} scroll={{ x: 1050 }} locale={{ emptyText: listQuery.isError ? listQuery.error.message : "暂无 Export Job" }} />
      {(listQuery.data?.total ?? 0) > 0 && <Pagination current={listPage} pageSize={listPageSize} total={listQuery.data?.total ?? 0} showSizeChanger pageSizeOptions={[10, 20, 50]} onChange={(nextPage, nextSize) => { setListPage(nextPage); setListPageSize(nextSize); }} />}
      {cancelTarget && <Card size="small" title={`取消 Export Job #${cancelTarget.export_job_id}`}>
        <Space wrap>
          <Input aria-label="Export 取消原因" value={cancelReason} onChange={(event) => setCancelReason(event.target.value)} maxLength={1000} placeholder="至少 8 个字符" style={{ width: 380 }} />
          <Popconfirm title="确认取消该 Export Job？" disabled={cancelReason.trim().length < 8} onConfirm={() => cancelMutation.mutate()}>
            <Button danger disabled={cancelReason.trim().length < 8} loading={cancelMutation.isPending}>确认取消</Button>
          </Popconfirm>
          <Button onClick={() => setCancelTarget(undefined)}>关闭</Button>
        </Space>
      </Card>}
      {metadataJobId !== undefined && <Card size="small" title={`Export Job #${metadataJobId} 制品元数据`} extra={<Button size="small" onClick={() => setMetadataJobId(undefined)}>关闭</Button>}>
        {metadataQuery.isLoading && <Typography.Text>读取元数据…</Typography.Text>}
        {metadataQuery.isError && <Alert type="error" showIcon message="制品元数据读取失败" description={metadataQuery.error.message} />}
        {metadata && <Space direction="vertical" style={{ width: "100%" }}>
          <Descriptions size="small" column={{ xs: 1, sm: 2, lg: 4 }} items={[
            { key: "status", label: "Job Status", children: metadata.job_status },
            { key: "availability", label: "Availability", children: metadata.availability },
            { key: "enabled", label: "Download Enabled", children: metadata.download_enabled ? "YES" : "NO" },
            { key: "reason", label: "Reason Code", children: metadata.reason_code },
          ]} />
          {metadata.download_enabled
            ? <Alert type="success" showIcon message="制品可下载" />
            : <Alert type="warning" showIcon message="制品不可下载" description={`Reason Code：${metadata.reason_code}`} />}
          {downloadMutation.error && <Alert type="error" showIcon message="制品下载失败" description={downloadMutation.error.message} />}
          <Table rowKey="export_artifact_id" size="small" pagination={false} dataSource={metadata.artifacts} columns={[
            { title: "文件", dataIndex: "file_name", key: "file" },
            { title: "MIME", dataIndex: "mime_type", key: "mime" },
            { title: "大小", dataIndex: "file_size", key: "size", render: sizeText },
            { title: "SHA-256", dataIndex: "sha256", key: "sha", render: (value: string) => <Typography.Text code copyable>{value}</Typography.Text> },
            { title: "到期", dataIndex: "expires_at_utc", key: "expires", render: formatUtcDateTime },
            { title: "下载", key: "download", width: 100, render: (_: unknown, artifact) => <Button type="link" size="small" icon={<DownloadOutlined />} disabled={!metadata.download_enabled} loading={downloadMutation.isPending && downloadMutation.variables?.export_artifact_id === artifact.export_artifact_id} onClick={() => downloadMutation.mutate(artifact)}>下载</Button> },
          ]} />
        </Space>}
      </Card>}
    </Space>
  </Card>;
}
