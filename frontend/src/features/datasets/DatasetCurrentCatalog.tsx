import { BarChartOutlined, DeleteOutlined, DownloadOutlined, FilterOutlined, ReloadOutlined, SyncOutlined, UnorderedListOutlined } from "@ant-design/icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, Button, Card, Checkbox, Col, Descriptions, Drawer, Empty, Form, Input, InputNumber, Modal, Row, Select, Space, Table, Tag, Typography, message } from "antd";
import type { ColumnsType, TablePaginationConfig } from "antd/es/table";
import { useEffect, useMemo, useState } from "react";

import { listCurrentDatasets, type CurrentDatasetRequest, type CurrentDatasetRow } from "../../api/catalog";
import { createFieldEnrichment } from "../../api/enrichments";
import {
  archiveDataset,
  createDatasetReprocess,
  createLatestExport,
  downloadLatestExportArtifact,
  getLatestExportStatus,
  type LifecycleExportArtifact,
  type LifecycleJobReceipt,
} from "../../api/lifecycle";
import { formatUtcDateTime, shanghaiLocalInputToUtc, utcToShanghaiLocalInput } from "../../utils/dateTime";
import { useAuth } from "../auth/AuthContext";
import { factoryNames } from "../capabilities/capabilityCatalog";

interface CatalogFilterValues {
  product_name?: string;
  lot_id?: string;
  wafer_id?: string;
  import_batch_id?: number;
  cleaner_version?: string;
  owner_login?: string;
  factory_code?: string;
  business_domain?: "ENGINEERING" | "PRODUCTION";
  test_stage?: "CP" | "FT";
  status?: string;
  from_local?: string;
  to_local?: string;
}

type LifecycleActionKind = "EXPORT" | "REPROCESS" | "ARCHIVE";
interface LifecycleActionTarget {
  kind: LifecycleActionKind;
  row: CurrentDatasetRow;
  idempotencyKey: string;
}
interface LifecycleActionValues {
  confirmed?: boolean;
  confirmation?: string;
  reason?: string;
}
interface ProductEnrichmentValues {
  action: "FILL" | "IGNORE";
  value_text?: string;
  reason: string;
}

export interface DatasetCurrentCatalogProps {
  searchParams: URLSearchParams;
  onSearchParamsChange: (params: URLSearchParams) => void;
  onOpenAnalytics: (datasetId: number, versionNo: number) => void;
  onOpenComparison?: (datasets: Array<{ datasetId: number; versionNo: number }>) => void;
  onOpenJob: (jobId: number) => void;
}

const positiveInt = (value: string | null, fallback: number) => {
  const parsed = Number(value);
  return Number.isInteger(parsed) && parsed > 0 ? parsed : fallback;
};
const optionalPositiveInt = (value: string | null) => {
  const parsed = Number(value);
  return Number.isInteger(parsed) && parsed > 0 ? parsed : undefined;
};
const actionKey = (kind: LifecycleActionKind, datasetId: number) => {
  const randomPart = globalThis.crypto?.randomUUID?.() ?? Math.random().toString(36).slice(2);
  return `${kind.toLowerCase()}-${datasetId}-${Date.now()}-${randomPart}`.slice(0, 128);
};
const fileSizeText = (bytes: number | null | undefined) => {
  if (bytes == null) return "—";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
  return `${(bytes / 1024 ** 3).toFixed(1)} GB`;
};

const exportAvailabilityColor: Record<string, string> = {
  PROCESSING: "processing",
  READY: "success",
  FAILED: "error",
  EXPIRED: "default",
  CLEANED: "default",
  UNAVAILABLE: "warning",
};

const FILTER_KEYS = ["product_name", "lot_id", "wafer_id", "cleaner_version", "owner_login", "factory_code", "business_domain", "test_stage", "status"] as const;

const displayLot = (row: CurrentDatasetRow) => (
  row.lot_count > 1 ? `多 Lot（${row.lot_count}）` : row.lot_id || "—"
);
const URL_FILTER_KEYS = [...FILTER_KEYS, "import_batch_id", "from_utc", "to_utc"] as const;

export function DatasetCurrentCatalog({ searchParams, onSearchParamsChange, onOpenAnalytics, onOpenComparison, onOpenJob }: DatasetCurrentCatalogProps) {
  const { user, can } = useAuth();
  const canExport = can("EXPORT_DATA");
  const canReprocess = can("TASK_CREATE");
  const isSystemAdmin = Boolean(user?.roles.includes("SYSTEM_ADMIN"));
  const queryClient = useQueryClient();
  const [form] = Form.useForm<CatalogFilterValues>();
  const [actionForm] = Form.useForm<LifecycleActionValues>();
  const [action, setAction] = useState<LifecycleActionTarget>();
  const [actionError, setActionError] = useState<string>();
  const [downloadError, setDownloadError] = useState<string>();
  const [selectedRows, setSelectedRows] = useState<CurrentDatasetRow[]>([]);
  const [productTarget, setProductTarget] = useState<CurrentDatasetRow>();
  const [productForm] = Form.useForm<ProductEnrichmentValues>();
  const [messageApi, messageContext] = message.useMessage();
  const searchKey = searchParams.toString();
  const request = useMemo<CurrentDatasetRequest>(() => ({
    page: positiveInt(searchParams.get("page"), 1),
    page_size: Math.min(100, positiveInt(searchParams.get("page_size"), 20)),
    product_name: searchParams.get("product_name") || undefined,
    lot_id: searchParams.get("lot_id") || undefined,
    wafer_id: searchParams.get("wafer_id") || undefined,
    import_batch_id: optionalPositiveInt(searchParams.get("import_batch_id")),
    cleaner_version: searchParams.get("cleaner_version") || undefined,
    owner_login: searchParams.get("owner_login") || undefined,
    factory_code: searchParams.get("factory_code") || undefined,
    business_domain: (searchParams.get("business_domain") as CurrentDatasetRequest["business_domain"]) || undefined,
    test_stage: (searchParams.get("test_stage") as CurrentDatasetRequest["test_stage"]) || undefined,
    status: searchParams.get("status") || undefined,
    from_utc: searchParams.get("from_utc") || undefined,
    to_utc: searchParams.get("to_utc") || undefined,
  }), [searchKey]);
  useEffect(() => {
    setSelectedRows([]);
    form.resetFields();
    form.setFieldsValue({
      product_name: request.product_name,
      lot_id: request.lot_id,
      wafer_id: request.wafer_id,
      import_batch_id: request.import_batch_id,
      cleaner_version: request.cleaner_version,
      owner_login: request.owner_login,
      factory_code: request.factory_code,
      business_domain: request.business_domain,
      test_stage: request.test_stage,
      status: request.status,
      from_local: utcToShanghaiLocalInput(request.from_utc),
      to_local: utcToShanghaiLocalInput(request.to_utc),
    });
  }, [form, request]);
  const query = useQuery({
    queryKey: ["datasets", "current", request],
    queryFn: () => listCurrentDatasets(request),
  });
  const exportJobId = optionalPositiveInt(searchParams.get("export_job_id"));
  const exportStatus = useQuery({
    queryKey: ["lifecycle", "export", exportJobId],
    queryFn: () => getLatestExportStatus(exportJobId!),
    enabled: exportJobId !== undefined && canExport,
    refetchInterval: ({ state }) => state.data?.availability === "PROCESSING" ? 3000 : false,
  });
  const lifecycleMutation = useMutation({
    mutationFn: async ({ target, values }: { target: LifecycleActionTarget; values: LifecycleActionValues }): Promise<LifecycleJobReceipt> => {
      if (target.kind === "EXPORT") return createLatestExport(target.row.dataset_id, target.idempotencyKey);
      if (target.kind === "REPROCESS") return createDatasetReprocess(target.row.dataset_id, values.reason!.trim(), target.idempotencyKey);
      return archiveDataset(target.row.dataset_id, values.reason!.trim(), target.idempotencyKey);
    },
    onSuccess: async (receipt, variables) => {
      setActionError(undefined);
      setAction(undefined);
      if (variables.target.kind === "EXPORT") {
        const next = new URLSearchParams(searchParams);
        next.set("export_job_id", String(receipt.job_id));
        onSearchParamsChange(next);
        messageApi.success(`导出 Job #${receipt.job_id} 已进入队列`);
      } else {
        messageApi.success(`${variables.target.kind === "REPROCESS" ? "重处理" : "逻辑归档"} Job #${receipt.job_id} 已进入队列`);
        await queryClient.invalidateQueries({ queryKey: ["datasets", "current"] });
        onOpenJob(receipt.job_id);
      }
    },
    onError: (_error, variables) => {
      const label = variables.target.kind === "EXPORT" ? "导出" : variables.target.kind === "REPROCESS" ? "重处理" : "逻辑归档";
      setActionError(`${label}任务未创建。系统不展示底层路径或连接详情；请核对权限、Owner 范围和当前 Dataset 状态后重试。`);
    },
  });
  const downloadMutation = useMutation({
    mutationFn: (artifact: LifecycleExportArtifact) => downloadLatestExportArtifact(exportJobId!, artifact.artifact_id, artifact.file_name),
    onMutate: () => setDownloadError(undefined),
    onSuccess: () => messageApi.success("导出文件下载已开始"),
    onError: () => setDownloadError("导出文件下载失败。该 Artifact 可能已过期、已清理或完整性校验未通过；请刷新状态或重新发起导出。"),
  });
  const productMutation = useMutation({
    mutationFn: (values: ProductEnrichmentValues) => createFieldEnrichment({
      import_batch_id: productTarget!.import_batch_id,
      test_stage: productTarget!.test_stage,
      field_code: "PRODUCT_CODE",
      action: values.action,
      value_text: values.action === "FILL" ? values.value_text?.trim() : undefined,
      reason: values.reason.trim(),
    }),
    onSuccess: async () => {
      setProductTarget(undefined);
      productForm.resetFields();
      messageApi.success("产品业务信息已保存；Cleaner 原始解析值未被改写");
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["datasets", "current"] }),
        queryClient.invalidateQueries({ queryKey: ["management", "quality-summary"] }),
      ]);
    },
    onError: (error) => messageApi.error(error.message),
  });

  const updateSearch = (values: CatalogFilterValues, page = 1, pageSize = request.page_size) => {
    const next = new URLSearchParams(searchParams);
    for (const key of URL_FILTER_KEYS) next.delete(key);
    next.set("page", String(page));
    next.set("page_size", String(pageSize));
    for (const key of FILTER_KEYS) {
      const value = values[key];
      if (typeof value === "string" && value.trim()) next.set(key, value.trim());
    }
    if (values.import_batch_id) next.set("import_batch_id", String(values.import_batch_id));
    const fromUtc = shanghaiLocalInputToUtc(values.from_local);
    const toUtc = shanghaiLocalInputToUtc(values.to_local);
    if (fromUtc) next.set("from_utc", fromUtc);
    if (toUtc) next.set("to_utc", toUtc);
    onSearchParamsChange(next);
  };
  const currentValues = (): CatalogFilterValues => ({
    product_name: request.product_name,
    lot_id: request.lot_id,
    wafer_id: request.wafer_id,
    import_batch_id: request.import_batch_id,
    cleaner_version: request.cleaner_version,
    owner_login: request.owner_login,
    factory_code: request.factory_code,
    business_domain: request.business_domain,
    test_stage: request.test_stage,
    status: request.status,
    from_local: utcToShanghaiLocalInput(request.from_utc),
    to_local: utcToShanghaiLocalInput(request.to_utc),
  });
  const onPageChange = (pagination: TablePaginationConfig) => {
    updateSearch(currentValues(), pagination.current ?? 1, pagination.pageSize ?? request.page_size);
  };
  const openLifecycleAction = (kind: LifecycleActionKind, row: CurrentDatasetRow) => {
    setActionError(undefined);
    setAction({ kind, row, idempotencyKey: actionKey(kind, row.dataset_id) });
  };
  const closeExportStatus = () => {
    const next = new URLSearchParams(searchParams);
    next.delete("export_job_id");
    onSearchParamsChange(next);
    setDownloadError(undefined);
  };
  const selectedStage = selectedRows[0]?.test_stage;
  const columns: ColumnsType<CurrentDatasetRow> = [
    { title: "产品", dataIndex: "product_name", width: 190, fixed: "left", ellipsis: true, render: (value) => value || "待补录" },
    { title: "Lot", key: "lot_scope", width: 160, ellipsis: true, render: (_, row) => displayLot(row) },
    { title: "厂家", dataIndex: "factory_code", width: 110, render: (value) => factoryNames[String(value).toLowerCase()] ?? value },
    { title: "范围", key: "scope", width: 125, render: (_, row) => `${row.business_domain === "ENGINEERING" ? "工程" : "量产"} / ${row.test_stage}` },
    { title: "状态", dataIndex: "status", width: 105, render: (value) => <Tag color={value === "PUBLISHED" ? "success" : "default"}>{value}</Tag> },
    { title: "Unit/Die", dataIndex: "unit_count", width: 110, render: (value) => value == null ? "—" : value },
    { title: "Pass", dataIndex: "pass_count", width: 100, render: (value) => value == null ? "—" : value },
    { title: "良率", dataIndex: "yield_rate", width: 100, render: (value: number | null) => value == null ? "—" : `${(value * 100).toFixed(2)}%` },
    { title: "源文件", dataIndex: "source_file_count", width: 90 },
    { title: "Cleaner", dataIndex: "cleaner_version", width: 120, render: (value) => value || "—" },
    { title: "上传人", dataIndex: "owner_name", width: 120, ellipsis: true },
    { title: "处理时间", dataIndex: "processed_at_utc", width: 180, render: formatUtcDateTime },
    {
      title: "操作",
      key: "actions",
      width: 430,
      fixed: "right",
      render: (_, row) => <Space size={0} wrap>
        <Button type="link" size="small" icon={<BarChartOutlined />} onClick={() => onOpenAnalytics(row.dataset_id, row.version_no)}>分析</Button>
        {row.job_id != null && <Button type="link" size="small" icon={<UnorderedListOutlined />} onClick={() => onOpenJob(row.job_id!)}>Job #{row.job_id}</Button>}
        {canReprocess && row.can_archive && <Button type="link" size="small" onClick={() => { productForm.setFieldsValue({ action: "FILL", value_text: row.product_name ?? undefined, reason: "补充或纠正正式数据产品业务信息" }); setProductTarget(row); }}>{row.product_name ? "修正产品" : "补录产品"}</Button>}
        {canExport && <Button type="link" size="small" icon={<DownloadOutlined />} onClick={() => openLifecycleAction("EXPORT", row)}>导出最新</Button>}
        {canReprocess && <Button type="link" size="small" icon={<SyncOutlined />} onClick={() => openLifecycleAction("REPROCESS", row)}>显式重处理</Button>}
        {row.can_archive && <Button type="link" danger size="small" icon={<DeleteOutlined />} title="仅 Dataset Owner 或 SYSTEM_ADMIN 可创建" onClick={() => openLifecycleAction("ARCHIVE", row)}>逻辑归档</Button>}
      </Space>,
    },
  ];

  return <div className="workbench production-workbench">
    {messageContext}
    <div className="page-heading">
      <div>
        <Typography.Text type="secondary">正式事实 / Dataset Current</Typography.Text>
        <Typography.Title level={2}>历史正式数据</Typography.Title>
        <Typography.Text type="secondary">按业务身份检索当前正式版本，多选后进入比较；日常操作无需手填 Dataset、Job 或 Run 内部编号。</Typography.Text>
      </div>
      <Button icon={<ReloadOutlined />} loading={query.isFetching} onClick={() => void query.refetch()}>刷新</Button>
    </div>
    <Alert
      type="info"
      showIcon
      className="review-alert"
      message="Dataset 生命周期边界"
      description={<Space direction="vertical" size={2}>
        <Typography.Text><strong>导出最新</strong>：由后端选择最新 Cleaner 生成有 TTL 的临时 Artifact；不调用 Canonical Importer，不创建或切换 Dataset Version，不改动人工补录。</Typography.Text>
        <Typography.Text><strong>显式重处理</strong>：生成新 Dataset Version，只有全量校验成功后才原子切换 Current；失败时旧 Current 仍可用。</Typography.Text>
        <Typography.Text><strong>逻辑归档</strong>：仅 Owner / SYSTEM_ADMIN 可执行；不删除 FTP/NAS 原始文件、Source Receipt，也不影响其他 Owner 的同 Lot 数据。{isSystemAdmin ? "当前账户具有 SYSTEM_ADMIN 角色。" : "是否为 Dataset Owner 由后端行级授权最终判定。"}</Typography.Text>
      </Space>}
    />
    <Card
      className="review-filter-card"
      extra={<Space wrap>
        <Typography.Text type="secondary">同次比较仅支持同一测试阶段（CP 或 FT），最多 8 个</Typography.Text>
        {selectedRows.length ? <Button type="primary" icon={<BarChartOutlined />} onClick={() => selectedRows.length === 1 ? onOpenAnalytics(selectedRows[0].dataset_id, selectedRows[0].version_no) : onOpenComparison?.(selectedRows.map((row) => ({ datasetId: row.dataset_id, versionNo: row.version_no })))} disabled={selectedRows.length > 1 && !onOpenComparison}>{selectedRows.length > 1 ? `比较分析（${selectedRows.length}）` : "分析所选数据"}</Button> : null}
      </Space>}
    >
      <Form<CatalogFilterValues> form={form} layout="vertical" onFinish={(values) => updateSearch(values)}>
        <Row gutter={[12, 0]}>
          <Col xs={24} sm={12} lg={6}><Form.Item label="产品" name="product_name"><Input allowClear placeholder="产品名称（精确或后端支持的匹配口径）" /></Form.Item></Col>
          <Col xs={24} sm={12} lg={6}><Form.Item label="Lot" name="lot_id"><Input allowClear placeholder="业务 Lot" /></Form.Item></Col>
          <Col xs={24} sm={12} lg={6}><Form.Item label="Wafer" name="wafer_id"><Input allowClear placeholder="Wafer ID" /></Form.Item></Col>
          <Col xs={24} sm={12} lg={6}><Form.Item label="上传任务" name="import_batch_id"><InputNumber min={1} precision={0} className="full-width" placeholder="Batch 编号" /></Form.Item></Col>
          <Col xs={24} sm={12} lg={6}><Form.Item label="Cleaner 版本" name="cleaner_version"><Input allowClear /></Form.Item></Col>
          {isSystemAdmin && <Col xs={24} sm={12} lg={6}><Form.Item label="上传账号" name="owner_login"><Input allowClear /></Form.Item></Col>}
          <Col xs={24} sm={12} lg={6}><Form.Item label="厂家" name="factory_code"><Select allowClear showSearch placeholder="全部厂家" options={Object.entries(factoryNames).map(([value, label]) => ({ value, label }))} /></Form.Item></Col>
          <Col xs={12} sm={6} lg={3}><Form.Item label="业务域" name="business_domain"><Select allowClear options={[{ label: "工程", value: "ENGINEERING" }, { label: "量产", value: "PRODUCTION" }]} /></Form.Item></Col>
          <Col xs={12} sm={6} lg={3}><Form.Item label="阶段" name="test_stage"><Select allowClear options={[{ label: "CP", value: "CP" }, { label: "FT", value: "FT" }]} /></Form.Item></Col>
          <Col xs={24} sm={12} lg={6}><Form.Item label="状态" name="status"><Select allowClear options={[{ label: "PUBLISHED", value: "PUBLISHED" }]} /></Form.Item></Col>
          <Col xs={24} sm={12} lg={6}><Form.Item label="开始时间（上海，含）" name="from_local"><Input type="datetime-local" allowClear /></Form.Item></Col>
          <Col xs={24} sm={12} lg={6}><Form.Item label="结束时间（上海，不含）" name="to_local"><Input type="datetime-local" allowClear /></Form.Item></Col>
          <Col xs={24} sm={12} lg={6} style={{ display: "flex", alignItems: "end" }}><Form.Item><Space><Button type="primary" htmlType="submit" icon={<FilterOutlined />}>检索</Button><Button onClick={() => { form.resetFields(); updateSearch({}); }}>清空</Button></Space></Form.Item></Col>
        </Row>
      </Form>
    </Card>
    {query.isError && <Alert type="error" showIcon message="Dataset Current 目录加载失败" description="本页不展示底层连接、路径或账号详情；请稍后刷新。" className="review-alert" />}
    <Card className="production-table-card">
      <Table
        rowKey={(row) => `${row.dataset_id}-${row.version_no}`}
        rowSelection={{
          selectedRowKeys: selectedRows.map((row) => `${row.dataset_id}-${row.version_no}`),
          preserveSelectedRowKeys: false,
          hideSelectAll: true,
          onChange: (_keys, rows) => {
            const stage = rows[0]?.test_stage;
            setSelectedRows(rows.filter((row) => row.test_stage === stage).slice(0, 8));
          },
          getCheckboxProps: (row) => {
            const isSelected = selectedRows.some((selected) => selected.dataset_id === row.dataset_id && selected.version_no === row.version_no);
            const stageMismatch = !isSelected && selectedStage !== undefined && row.test_stage !== selectedStage;
            return {
              disabled: !isSelected && (selectedRows.length >= 8 || stageMismatch),
            };
          },
        }}
        columns={columns}
        dataSource={query.data?.items ?? []}
        loading={query.isLoading}
        scroll={{ x: 2050 }}
        pagination={{
          current: query.data?.page ?? request.page,
          pageSize: query.data?.page_size ?? request.page_size,
          total: query.data?.total ?? 0,
          showSizeChanger: true,
          pageSizeOptions: [10, 20, 50, 100],
          showTotal: (total) => `共 ${total} 个 Current Dataset`,
        }}
        onChange={onPageChange}
      />
    </Card>
    <Modal
      title={action?.kind === "EXPORT" ? "确认导出最新 Cleaner 结果" : action?.kind === "REPROCESS" ? "显式重处理 Dataset" : "逻辑归档 Dataset"}
      open={Boolean(action)}
      onCancel={() => { if (!lifecycleMutation.isPending) setAction(undefined); }}
      onOk={() => actionForm.submit()}
      okText={action?.kind === "EXPORT" ? "创建导出 Job" : action?.kind === "REPROCESS" ? "创建重处理 Job" : "创建逻辑归档 Job"}
      okButtonProps={{ danger: action?.kind === "ARCHIVE" }}
      confirmLoading={lifecycleMutation.isPending}
      maskClosable={!lifecycleMutation.isPending}
      destroyOnHidden
    >
      <Descriptions size="small" bordered column={1} style={{ marginBottom: 16 }}>
        <Descriptions.Item label="Dataset">{action ? `#${action.row.dataset_id} / V${action.row.version_no}` : "—"}</Descriptions.Item>
        <Descriptions.Item label="产品 / Lot">{action ? `${action.row.product_name || "—"} / ${displayLot(action.row)}` : "—"}</Descriptions.Item>
      </Descriptions>
      {action?.kind === "EXPORT" && <Alert type="info" showIcon message="非变异临时导出" description="导出只生成临时文件并登记 SHA-256/TTL；Current Dataset、Canonical 数据与补录在导出前后保持不变。" style={{ marginBottom: 16 }} />}
      {action?.kind === "REPROCESS" && <Alert type="warning" showIcon message="将创建新版本" description="新版本全量校验成功后才会取代旧 Current；Cleaner、入库或切换失败时，旧 Current 不变。" style={{ marginBottom: 16 }} />}
      {action?.kind === "ARCHIVE" && <Alert type="error" showIcon message="仅逻辑归档，不删除源文件" description="后端仅允许 Dataset Owner 或 SYSTEM_ADMIN；FTP/NAS 原始文件、Source Receipt 和其他 Owner 的数据均不在删除范围。" style={{ marginBottom: 16 }} />}
      {actionError && <Alert type="error" showIcon message="生命周期操作失败" description={actionError} style={{ marginBottom: 16 }} />}
      <Form<LifecycleActionValues>
        form={actionForm}
        layout="vertical"
        preserve={false}
        onFinish={(values) => { if (action) lifecycleMutation.mutate({ target: action, values }); }}
      >
        {action?.kind === "EXPORT" ? <Form.Item name="confirmed" valuePropName="checked" rules={[{ validator: (_, value) => value ? Promise.resolve() : Promise.reject(new Error("请确认导出为非变异临时任务")) }]}>
          <Checkbox>我确认本操作不会更改 Current Dataset，并会在 TTL 到期前下载所需 Artifact。</Checkbox>
        </Form.Item> : <>
          <Form.Item
            label={`输入 ${action?.kind === "REPROCESS" ? "REPROCESS" : "ARCHIVE"} 确认`}
            name="confirmation"
            rules={[{ validator: (_, value) => value === (action?.kind === "REPROCESS" ? "REPROCESS" : "ARCHIVE") ? Promise.resolve() : Promise.reject(new Error(`请完整输入 ${action?.kind === "REPROCESS" ? "REPROCESS" : "ARCHIVE"}`)) }]}
          ><Input autoComplete="off" /></Form.Item>
          <Form.Item label="完整操作原因" name="reason" rules={[{ required: true, whitespace: true, message: "请填写操作原因" }, { min: 8, message: "操作原因至少 8 个字符" }]}>
            <Input.TextArea rows={4} maxLength={1000} showCount />
          </Form.Item>
        </>}
      </Form>
    </Modal>
    <Modal
      title={productTarget?.product_name ? "修正产品业务信息" : "补录产品业务信息"}
      open={Boolean(productTarget)}
      okText="保存业务信息"
      confirmLoading={productMutation.isPending}
      onCancel={() => { if (!productMutation.isPending) { setProductTarget(undefined); productForm.resetFields(); } }}
      onOk={() => productForm.submit()}
      destroyOnHidden
    >
      <Alert type="info" showIcon message="人工补录与 Cleaner 原值分离" description="本操作保存可追溯的业务有效值，用于后续检索和管理汇总，不改写原始文件或 Cleaner 原始解析值。" style={{ marginBottom: 16 }} />
      <Form<ProductEnrichmentValues>
        form={productForm}
        layout="vertical"
        preserve={false}
        initialValues={{ action: "FILL" }}
        onFinish={(values) => productMutation.mutate(values)}
      >
        <Form.Item label="处理方式" name="action" rules={[{ required: true }]}>
          <Select options={[{ label: "填写/修正产品", value: "FILL" }, { label: "本任务暂不提供产品", value: "IGNORE" }]} />
        </Form.Item>
        <Form.Item noStyle shouldUpdate={(previous, current) => previous.action !== current.action}>
          {({ getFieldValue }) => getFieldValue("action") === "FILL" ? <Form.Item label="产品型号" name="value_text" rules={[{ required: true, whitespace: true, message: "请填写产品型号" }, { max: 500 }]}><Input autoComplete="off" /></Form.Item> : <Alert type="warning" showIcon message="跳过后仍可按 Lot、Wafer 和参数分析，但不能按 Product 检索" style={{ marginBottom: 16 }} />}
        </Form.Item>
        <Form.Item label="补录或修正原因" name="reason" rules={[{ required: true, whitespace: true }, { min: 8, message: "原因至少 8 个字符" }, { max: 500 }]}>
          <Input.TextArea rows={3} showCount maxLength={500} />
        </Form.Item>
      </Form>
    </Modal>
    <Drawer
      title={exportJobId ? `导出 Job #${exportJobId}` : "导出状态"}
      open={exportJobId !== undefined}
      width={760}
      onClose={closeExportStatus}
      destroyOnHidden
      extra={<Space><Button onClick={() => exportJobId && onOpenJob(exportJobId)}>Job 详情</Button><Button icon={<ReloadOutlined />} loading={exportStatus.isFetching} disabled={!canExport} onClick={() => void exportStatus.refetch()}>刷新</Button></Space>}
    >
      {!canExport ? <Alert type="error" showIcon message="无权查看导出状态" description="需要 EXPORT_DATA 权限。" /> : exportStatus.isLoading ? <Typography.Text type="secondary">正在读取导出状态…</Typography.Text> : exportStatus.isError ? <Alert type="error" showIcon message="导出状态加载失败" description="本页不展示底层存储路径或连接详情；请稍后刷新。" /> : exportStatus.data ? <Space direction="vertical" size={16} className="full-width">
        <Alert type="info" showIcon message="导出语义" description="本 Job 只生成有 TTL 的临时 Artifact，不修改 Canonical、Current Dataset Version 或人工补录。" />
        {exportStatus.data.error_code && <Alert type="error" showIcon message={`导出错误分类：${exportStatus.data.error_code}`} description="失败仅影响本次导出，不影响 Current Dataset。" />}
        {downloadError && <Alert type="error" showIcon message="Artifact 下载失败" description={downloadError} />}
        <Descriptions bordered size="small" column={2}>
          <Descriptions.Item label="处理状态"><Tag>{exportStatus.data.status}</Tag></Descriptions.Item>
          <Descriptions.Item label="可用状态"><Tag color={exportAvailabilityColor[exportStatus.data.availability]}>{exportStatus.data.availability}</Tag></Descriptions.Item>
          <Descriptions.Item label="Dataset">#{exportStatus.data.dataset_id}</Descriptions.Item>
          <Descriptions.Item label="Dataset Version ID">#{exportStatus.data.dataset_version_id}</Descriptions.Item>
          <Descriptions.Item label="Cleaner Release">#{exportStatus.data.cleaner_release_id}</Descriptions.Item>
          <Descriptions.Item label="最晚到期">{formatUtcDateTime(exportStatus.data.expires_at_utc)}</Descriptions.Item>
        </Descriptions>
        <Table<LifecycleExportArtifact>
          rowKey="artifact_id"
          size="small"
          pagination={false}
          dataSource={exportStatus.data.artifacts}
          locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={exportStatus.data.availability === "PROCESSING" ? "导出仍在处理，尚未生成 Artifact" : "没有可用 Artifact"} /> }}
          scroll={{ x: 980 }}
          columns={[
            { title: "Artifact", dataIndex: "artifact_id", width: 95, render: (value) => `#${value}` },
            { title: "Role", dataIndex: "role", width: 120 },
            { title: "文件名", dataIndex: "file_name", width: 220, ellipsis: true },
            { title: "大小", dataIndex: "size_bytes", width: 105, render: fileSizeText },
            { title: "SHA-256", dataIndex: "sha256", width: 190, render: (value) => <Typography.Text code copyable ellipsis={{ tooltip: value }}>{value}</Typography.Text> },
            { title: "物理状态", dataIndex: "physical_status", width: 115, render: (value) => <Tag color={value === "PRESENT" ? "success" : "default"}>{value}</Tag> },
            { title: "到期时间", dataIndex: "expires_at_utc", width: 180, render: formatUtcDateTime },
            { title: "下载", key: "download", width: 100, fixed: "right", render: (_, artifact) => <Button type="link" size="small" icon={<DownloadOutlined />} disabled={!artifact.download_url || artifact.physical_status !== "PRESENT"} loading={downloadMutation.isPending && downloadMutation.variables?.artifact_id === artifact.artifact_id} onClick={() => downloadMutation.mutate(artifact)}>下载</Button> },
          ]}
        />
      </Space> : null}
    </Drawer>
  </div>;
}
