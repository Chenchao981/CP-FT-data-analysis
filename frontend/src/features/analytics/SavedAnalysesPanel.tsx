import { DeleteOutlined, EditOutlined, ReloadOutlined, SaveOutlined } from "@ant-design/icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, Button, Card, Form, Input, Pagination, Popconfirm, Select, Space, Table, Tag, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useMemo, useState } from "react";

import type { AnalyticsContextRequest, AnalyticsRuleContext } from "../../api/analytics";
import {
  SAVED_ANALYSIS_CONTRACT_VERSION,
  createSavedAnalysis,
  createSavedAnalysisRevision,
  deleteSavedAnalysis,
  listSavedAnalyses,
  type SavedAnalysisRecord,
  type SavedAnalysisState,
} from "../../api/savedAnalyses";
import { formatUtcDateTime } from "../../utils/dateTime";
import { useAuth } from "../auth/AuthContext";
import { ANALYSIS_SECTIONS, createDefaultAnalysisViewState, type AnalysisDisplayState, type AnalysisSection, type AnalysisViewState } from "./context/analysisViewState";
import { savedChartConfig } from "./context/analysisPresentation";

interface CreateValues { analysisName: string; reason: string; section: AnalysisSection }

export interface SavedAnalysesPanelProps {
  context: AnalyticsContextRequest;
  ruleContext: AnalyticsRuleContext;
  page: number;
  pageSize: number;
  focusDatasetId: number;
  viewState?: AnalysisViewState;
  /** @deprecated test/backward compatibility; production passes the complete viewState. */
  chartDisplayState?: AnalysisDisplayState;
  onRestore: (record: SavedAnalysisRecord) => void;
}

const restoreLabel: Record<SavedAnalysisRecord["restore_status"], string> = {
  CURRENT: "可精确恢复",
  NON_CURRENT: "Dataset 非 Current",
  RULE_CHANGED: "规则已变化",
  ACCESS_REVOKED: "访问已撤销",
};
const restoreColor: Record<SavedAnalysisRecord["restore_status"], string> = {
  CURRENT: "success", NON_CURRENT: "warning", RULE_CHANGED: "error", ACCESS_REVOKED: "error",
};
const sectionOptions = ANALYSIS_SECTIONS.filter((section) => section !== "delivery")
  .map((section) => ({ label: section[0].toUpperCase() + section.slice(1), value: section }));
const cloneContext = (context: AnalyticsContextRequest): AnalyticsContextRequest => ({
  datasets: context.datasets.map((item) => ({ ...item })),
  filters: {
    lot_ids: [...context.filters.lot_ids], wafer_ids: [...context.filters.wafer_ids], bin_codes: [...context.filters.bin_codes],
    overall_results: [...context.filters.overall_results], source_ids: [...context.filters.source_ids], tester_ids: [...context.filters.tester_ids],
    program_versions: [...context.filters.program_versions], test_conditions: [...context.filters.test_conditions],
  },
  parameters: [...context.parameters],
});

export function SavedAnalysesPanel({ context, ruleContext, page, pageSize, focusDatasetId, viewState, chartDisplayState, onRestore }: SavedAnalysesPanelProps) {
  const defaults = createDefaultAnalysisViewState();
  const effectiveViewState = viewState ?? { ...defaults, display: chartDisplayState ?? defaults.display };
  const { can } = useAuth();
  const canWrite = can("ANALYSIS_RUN") && can("DATASET_READ");
  const queryClient = useQueryClient();
  const [form] = Form.useForm<CreateValues>();
  const [listPage, setListPage] = useState(1);
  const [listPageSize, setListPageSize] = useState(10);
  const [selected, setSelected] = useState<SavedAnalysisRecord | null>(null);
  const [revisionName, setRevisionName] = useState("");
  const [revisionReason, setRevisionReason] = useState("");
  const [revisionSection, setRevisionSection] = useState<AnalysisSection>("overview");
  const [deleteReason, setDeleteReason] = useState("");
  const [success, setSuccess] = useState<string>();

  const query = useQuery({
    queryKey: ["analytics", "saved-analyses", listPage, listPageSize],
    queryFn: () => listSavedAnalyses({ page: listPage, page_size: listPageSize, include_deleted: false }),
    retry: false,
  });
  const snapshot = (section: AnalysisSection): SavedAnalysisState => ({
    ...cloneContext(context),
    contract_version: SAVED_ANALYSIS_CONTRACT_VERSION,
    rule_context: {
      spec_versions: [...ruleContext.spec_versions],
      bin_mapping_versions: [...ruleContext.bin_mapping_versions],
      evaluation_rule_versions: [...ruleContext.evaluation_rule_versions],
    },
    chart_config: savedChartConfig({ ...effectiveViewState, display: { ...effectiveViewState.display, section } }),
    display_config: { section, page, page_size: pageSize, focus_dataset_id: focusDatasetId },
  });
  const refresh = async () => queryClient.invalidateQueries({ queryKey: ["analytics", "saved-analyses"] });
  const createMutation = useMutation({
    mutationFn: (values: CreateValues) => createSavedAnalysis({
      ...snapshot(values.section),
      analysis_name: values.analysisName,
      change_reason: values.reason,
    }),
    onSuccess: async (record) => {
      setSuccess(`分析方案 #${record.saved_analysis_id} / 版本 ${record.current_revision_no} 已保存`);
      form.resetFields();
      await refresh();
    },
  });
  const revisionMutation = useMutation({
    mutationFn: () => createSavedAnalysisRevision(selected!.saved_analysis_id, {
      ...snapshot(revisionSection),
      expected_row_version: selected!.row_version,
      analysis_name: revisionName.trim() || null,
      change_reason: revisionReason.trim(),
    }),
    onSuccess: async (record) => {
      setSelected(record);
      setSuccess(`分析方案 #${record.saved_analysis_id} 已更新至版本 ${record.current_revision_no}`);
      setRevisionReason("");
      await refresh();
    },
  });
  const deleteMutation = useMutation({
    mutationFn: () => deleteSavedAnalysis(selected!.saved_analysis_id, {
      expected_row_version: selected!.row_version,
      reason: deleteReason.trim(),
    }),
    onSuccess: async (record) => {
      setSuccess(`分析方案 #${record.saved_analysis_id} 已逻辑删除`);
      setSelected(null);
      setDeleteReason("");
      await refresh();
    },
  });
  const operationError = createMutation.error ?? revisionMutation.error ?? deleteMutation.error;

  const columns = useMemo<ColumnsType<SavedAnalysisRecord>>(() => [
    { title: "名称", dataIndex: "analysis_name", key: "name", render: (value: string, row) => <Space direction="vertical" size={0}><Typography.Text strong>{value}</Typography.Text><Typography.Text type="secondary">#{row.saved_analysis_id} · Owner {row.owner_user_id}</Typography.Text></Space> },
    { title: "Revision", dataIndex: "current_revision_no", key: "revision", width: 100, render: (value: number) => `R${value}` },
    { title: "恢复门禁", dataIndex: "restore_status", key: "restore", width: 160, render: (value: SavedAnalysisRecord["restore_status"]) => <Tag color={restoreColor[value]}>{restoreLabel[value]}</Tag> },
    { title: "Dataset", key: "datasets", render: (_, row) => <Space wrap>{row.revision.datasets.map((item) => <Tag key={item.dataset_version_id} color={item.status === "CURRENT" ? "blue" : "warning"}>#{item.dataset_id}/V{item.version_no} · {item.status}</Tag>)}</Space> },
    { title: "更新时间", dataIndex: "updated_at_utc", key: "updated", width: 180, render: formatUtcDateTime },
    { title: "操作", key: "actions", width: 190, render: (_, row) => <Space>
      <Button size="small" disabled={row.lifecycle_status !== "ACTIVE" || row.restore_status !== "CURRENT"} onClick={() => onRestore(row)}>恢复</Button>
      {canWrite && <Button size="small" icon={<EditOutlined />} onClick={() => { setSelected(row); setRevisionName(row.analysis_name); setRevisionSection("overview"); }}>管理</Button>}
    </Space> },
  ], [canWrite, onRestore]);

  return <Card title="图表组合方案" extra={<Button icon={<ReloadOutlined />} onClick={() => void query.refetch()} loading={query.isFetching}>刷新</Button>}>
    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
      {!canWrite && <Alert type="warning" showIcon message="只读模式" />}
      {success && <Alert type="success" showIcon closable onClose={() => setSuccess(undefined)} message={success} />}
      {operationError && <Alert type="error" showIcon message="分析方案操作失败" description={operationError.message} />}
      {canWrite && <Form<CreateValues> form={form} layout="vertical" initialValues={{ section: "overview" }} onFinish={(values) => createMutation.mutate({ ...values, analysisName: values.analysisName.trim(), reason: values.reason.trim() })}>
        <Space align="start" wrap>
          <Form.Item label="名称" name="analysisName" rules={[{ required: true, min: 1, max: 300 }]}><Input aria-label="Saved Analysis 名称" maxLength={300} style={{ width: 240 }} /></Form.Item>
          <Form.Item label="恢复目标 Section" name="section" rules={[{ required: true }]}><Select aria-label="Saved Analysis Section" options={sectionOptions} style={{ width: 180 }} /></Form.Item>
          <Form.Item label="变更原因" name="reason" rules={[{ required: true, min: 8, max: 1000 }]}><Input aria-label="Saved Analysis 变更原因" maxLength={1000} style={{ width: 320 }} /></Form.Item>
          <Form.Item label=" "><Button htmlType="submit" type="primary" icon={<SaveOutlined />} loading={createMutation.isPending}>保存当前图表组合</Button></Form.Item>
        </Space>
      </Form>}
      <Table<SavedAnalysisRecord> rowKey="saved_analysis_id" columns={columns} dataSource={query.data?.items ?? []} loading={query.isLoading} pagination={false} scroll={{ x: 1000 }} locale={{ emptyText: query.isError ? query.error.message : "暂无图表组合方案" }} />
      {(query.data?.total ?? 0) > 0 && <Pagination current={listPage} pageSize={listPageSize} total={query.data?.total ?? 0} showSizeChanger pageSizeOptions={[10, 20, 50]} onChange={(nextPage, nextSize) => { setListPage(nextPage); setListPageSize(nextSize); }} />}
      {selected && <Card size="small" title={`管理 #${selected.saved_analysis_id} / R${selected.current_revision_no}`}>
        <Space direction="vertical" style={{ width: "100%" }}>
          <Space wrap>
            <Input aria-label="Revision 新名称" value={revisionName} onChange={(event) => setRevisionName(event.target.value)} maxLength={300} style={{ width: 240 }} />
            <Select aria-label="Revision Section" value={revisionSection} onChange={setRevisionSection} options={sectionOptions} style={{ width: 180 }} />
            <Input aria-label="Revision 变更原因" value={revisionReason} onChange={(event) => setRevisionReason(event.target.value)} maxLength={1000} placeholder="至少 8 个字符" style={{ width: 320 }} />
            <Button icon={<EditOutlined />} disabled={revisionReason.trim().length < 8} loading={revisionMutation.isPending} onClick={() => revisionMutation.mutate()}>以当前 Context 新建 Revision</Button>
          </Space>
          <Space wrap>
            <Input aria-label="Saved Analysis 删除原因" value={deleteReason} onChange={(event) => setDeleteReason(event.target.value)} maxLength={1000} placeholder="逻辑删除原因，至少 8 个字符" style={{ width: 380 }} />
            <Popconfirm title="逻辑删除 Saved Analysis？" description="历史 Revision 与审计信息仍保留。" disabled={deleteReason.trim().length < 8} onConfirm={() => deleteMutation.mutate()}>
              <Button danger icon={<DeleteOutlined />} disabled={deleteReason.trim().length < 8} loading={deleteMutation.isPending}>逻辑删除</Button>
            </Popconfirm>
            <Button onClick={() => setSelected(null)}>关闭</Button>
          </Space>
        </Space>
      </Card>}
    </Space>
  </Card>;
}
