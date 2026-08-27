import { BarChartOutlined, CloudUploadOutlined, DownloadOutlined, FileSearchOutlined, FormOutlined, InfoCircleOutlined, RedoOutlined, ReloadOutlined } from "@ant-design/icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, Button, Card, Col, Form, Input, Modal, Popconfirm, Row, Select, Space, Statistic, Table, Tabs, Tag, Typography, Upload, message } from "antd";
import type { UploadFile } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useEffect, useMemo, useState } from "react";
import { BusinessDomain, TestStage, downloadStageUploadFile, listStageResults, listStageUploads, reprocessStageBatch, StageResultRow, StageUploadRow, uploadStageData } from "../../api/stageData";
import { useAuth } from "../auth/AuthContext";
import { factoryInputs, factoryNames, formalFactoryOptions, isFormalFactory } from "../capabilities/capabilityCatalog";
import { LotEnrichmentModal } from "./LotEnrichmentModal";

const statusColor: Record<string, string> = { RECEIVED: "blue", QUEUED: "gold", PROCESSING: "processing", PROCESSED: "success", NEEDS_INPUT: "orange", FAILED: "error" };
const statusName: Record<string, string> = { RECEIVED: "已接收", QUEUED: "排队中", PROCESSING: "处理中", PROCESSED: "已处理", NEEDS_INPUT: "待补录", FAILED: "失败" };
const domainName: Record<BusinessDomain, string> = { ENGINEERING: "工程", PRODUCTION: "量产" };
const stageDescription: Record<TestStage, string> = {
  CP: "选择晶圆厂并上传对应CP源文件后，系统自动调用该厂现有清洗程序并形成Wafer分析数据。",
  FT: "选择日月新或日月光并提交已验收的FT DC XLSX，系统按各自格式严格校验后形成产品/Lot分析数据。",
};
const dt = (value?: string | null) => value ? new Date(value).toLocaleString("zh-CN", { hour12: false }) : "—";
const size = (value: number) => value < 1024 * 1024 ? `${(value / 1024).toFixed(1)} KB` : `${(value / 1024 / 1024).toFixed(2)} MB`;
const needsLotInput = (row: StageUploadRow) => row.status === "NEEDS_INPUT" || row.action_required === "LOT_ID";

export interface StageDataWorkbenchProps {
  businessDomain: BusinessDomain;
  testStage: TestStage;
  onOpenAnalytics?: (datasetId: number, versionNo: number) => void;
}

export function StageDataWorkbench({ businessDomain, testStage, onOpenAnalytics }: StageDataWorkbenchProps) {
  const { user, can } = useAuth();
  const [open, setOpen] = useState(false);
  const [lotBatchId, setLotBatchId] = useState<number>();
  const [failureRow, setFailureRow] = useState<StageUploadRow>();
  const [files, setFiles] = useState<UploadFile[]>([]);
  const [form] = Form.useForm<{ factory_code: string; remark?: string; source_path?: string }>();
  const defaultFactory = formalFactoryOptions[testStage][0].value;
  const watchedFactory = Form.useWatch("factory_code", form);
  const selectedFactory = watchedFactory && isFormalFactory(testStage, watchedFactory) ? watchedFactory : defaultFactory;
  const selectedInput = factoryInputs[selectedFactory];
  const [messageApi, contextHolder] = message.useMessage();
  const queryClient = useQueryClient();
  const scopeKey = ["stage", businessDomain, testStage];
  useEffect(() => {
    setOpen(false);
    setLotBatchId(undefined);
    setFailureRow(undefined);
    setFiles([]);
    form.resetFields();
    form.setFieldsValue({ factory_code: defaultFactory, remark: undefined, source_path: undefined });
  }, [businessDomain, defaultFactory, form, testStage]);
  const uploads = useQuery({ queryKey: [...scopeKey, "uploads"], queryFn: () => listStageUploads(businessDomain, testStage), refetchInterval: (query) => (query.state.data ?? []).some((row) => ["RECEIVED", "QUEUED", "PROCESSING"].includes(row.status)) ? 3000 : false });
  const results = useQuery({ queryKey: [...scopeKey, "results"], queryFn: () => listStageResults(businessDomain, testStage), refetchInterval: (query) => (uploads.data ?? []).some((row) => ["QUEUED", "PROCESSING"].includes(row.status)) ? 3000 : false });
  const refresh = async () => Promise.all([uploads.refetch(), results.refetch()]);
  const mutation = useMutation({
    mutationFn: async (values: { factory_code: string; remark?: string; source_path?: string }) => {
      const nativeFiles = files.flatMap((item) => item.originFileObj ? [item.originFileObj as File] : []);
      if (!nativeFiles.length && !values.source_path?.trim()) throw new Error(`请选择${testStage}源文件或填写服务器数据路径`);
      if (nativeFiles.length && values.source_path?.trim()) throw new Error("源文件上传和服务器数据路径只能选择一种");
      return uploadStageData(businessDomain, testStage, nativeFiles, values.factory_code, values.remark, values.source_path);
    },
    onSuccess: async (data) => { messageApi.success(`批次 ${data.import_batch_id} 已进入后台清洗队列（任务 ${data.job_id}）`); setOpen(false); setFiles([]); form.resetFields(); await queryClient.invalidateQueries({ queryKey: scopeKey }); },
    onError: (error) => messageApi.error(error.message),
  });
  const reprocessMutation = useMutation({
    mutationFn: (batchId: number) => reprocessStageBatch(businessDomain, testStage, batchId),
    onSuccess: async (data) => { messageApi.success(`批次 ${data.import_batch_id} 已进入重新处理队列`); await queryClient.invalidateQueries({ queryKey: scopeKey }); },
    onError: (error) => messageApi.error(error.message),
  });
  const batchRows = useMemo(() => {
    const firstByBatch = new Map<number, StageUploadRow>();
    for (const row of uploads.data ?? []) {
      const current = firstByBatch.get(row.import_batch_id);
      if (!current || row.sequence_no < current.sequence_no) firstByBatch.set(row.import_batch_id, row);
    }
    return [...firstByBatch.values()];
  }, [uploads.data]);
  const firstSequenceByBatch = useMemo(
    () => new Map(batchRows.map((row) => [row.import_batch_id, row.sequence_no])),
    [batchRows],
  );
  const uploadColumns: ColumnsType<StageUploadRow> = [
    { title: "批次编号", dataIndex: "import_batch_id", width: 95, fixed: "left" },
    { title: "SEQ", dataIndex: "sequence_no", width: 70 },
    { title: "源文件名称", dataIndex: "original_file_name", width: 300, ellipsis: true },
    { title: "扩展名", dataIndex: "extension", width: 80, render: (v) => v.toUpperCase() },
    { title: "大小", dataIndex: "size_bytes", width: 100, render: size },
    { title: testStage === "CP" ? "晶圆厂" : "封测厂", dataIndex: "factory_code", width: 100, render: (v) => factoryNames[String(v).toLowerCase()] ?? v },
    { title: "上传时间", dataIndex: "upload_time_utc", width: 175, render: dt },
    { title: "完成时间", dataIndex: "completion_time_utc", width: 175, render: dt },
    { title: "上传账号", dataIndex: "uploader_login", width: 120 },
    { title: "上传人", dataIndex: "uploader_name", width: 110 },
    { title: "状态", dataIndex: "status", width: 100, fixed: "right", render: (_, row) => {
      const displayStatus = needsLotInput(row) ? "NEEDS_INPUT" : row.status;
      return <Tag color={statusColor[displayStatus]}>{statusName[displayStatus] ?? displayStatus}</Tag>;
    } },
    { title: "操作", key: "actions", width: 310, fixed: "right", render: (_, row) => {
      const firstRow = firstSequenceByBatch.get(row.import_batch_id) === row.sequence_no;
      const awaitingLot = needsLotInput(row);
      const ordinaryFailure = row.status === "FAILED" && !awaitingLot;
      return <Space size={0} wrap>
        <Button type="link" size="small" icon={<DownloadOutlined />} onClick={() => void downloadStageUploadFile(businessDomain, testStage, row.import_batch_id, row.receipt_id, row.original_file_name)}>下载</Button>
        {firstRow && awaitingLot && can("TASK_CREATE") && <Button type="link" size="small" icon={<FormOutlined />} onClick={() => setLotBatchId(row.import_batch_id)}>补录批次号</Button>}
        {firstRow && ordinaryFailure && <Button type="link" size="small" icon={<InfoCircleOutlined />} onClick={() => setFailureRow(row)}>失败详情</Button>}
        {firstRow && ordinaryFailure && can("TASK_CREATE") && isFormalFactory(testStage, row.factory_code) && <Popconfirm title="重新处理该批次？" description="系统会使用现有源文件重新运行清洗程序。" onConfirm={() => reprocessMutation.mutate(row.import_batch_id)}><Button type="link" size="small" icon={<RedoOutlined />} loading={reprocessMutation.isPending && reprocessMutation.variables === row.import_batch_id}>重新处理</Button></Popconfirm>}
      </Space>;
    } },
  ];
  const resultColumns: ColumnsType<StageResultRow> = [
    { title: "名称", dataIndex: "data_name", width: 180, fixed: "left" },
    { title: "产品名称", dataIndex: "product_name", width: 180, render: (v) => v || "—" },
    { title: "批号", dataIndex: "lot_id", width: 160, render: (v) => v || "—" },
    ...(testStage === "CP" ? [{ title: "晶圆数", dataIndex: "wafer_count", width: 90 }] : []),
    { title: testStage === "CP" ? "晶圆厂" : "封测厂", dataIndex: "factory_code", width: 100, render: (v) => factoryNames[String(v).toLowerCase()] ?? v },
    { title: "测试项", dataIndex: "test_item_count", width: 90 },
    { title: "总数", dataIndex: "unit_count", width: 105 },
    { title: "良品数", dataIndex: "pass_count", width: 105 },
    { title: "良率", dataIndex: "yield_rate", width: 100, render: (v: number | null) => v == null ? "—" : `${(v * 100).toFixed(2)}%` },
    { title: "状态", dataIndex: "status", width: 100, render: (v) => <Tag color="success">{statusName[v] ?? v}</Tag> },
    { title: "Data Type", dataIndex: "data_type", width: 105 },
    { title: "处理时间", dataIndex: "created_at_utc", width: 175, render: dt },
    { title: "操作", key: "actions", width: 210, fixed: "right", render: (_, row) => <Space size={0}>
      {row.dataset_id && row.dataset_version_no && can("ANALYSIS_RUN") && <Button type="link" size="small" icon={<BarChartOutlined />} onClick={() => onOpenAnalytics?.(row.dataset_id!, row.dataset_version_no!)}>数据分析</Button>}
      {can("TASK_CREATE") && isFormalFactory(testStage, row.factory_code) && <Popconfirm title="重新处理该批次？" description="将重跑现有清洗程序并归档旧结果。" onConfirm={() => reprocessMutation.mutate(row.import_batch_id)}><Button type="link" size="small" icon={<RedoOutlined />} loading={reprocessMutation.isPending && reprocessMutation.variables === row.import_batch_id}>重新处理</Button></Popconfirm>}
    </Space> },
  ];
  const metrics = useMemo(() => ({
    total: batchRows.length,
    processing: batchRows.filter((row) => ["QUEUED", "PROCESSING"].includes(row.status)).length,
    processed: new Set((results.data ?? []).map((row) => row.import_batch_id)).size,
    needsInput: batchRows.filter(needsLotInput).length,
    failed: batchRows.filter((row) => row.status === "FAILED" && !needsLotInput(row)).length,
  }), [batchRows, results.data]);

  return <div className="workbench production-workbench">
    {contextHolder}
    <div className="page-heading"><div><Typography.Text type="secondary">{domainName[businessDomain]}数据 / {testStage}数据</Typography.Text><Typography.Title level={2}>{testStage}数据</Typography.Title><Typography.Text type="secondary">{stageDescription[testStage]}</Typography.Text></div><Space><Button icon={<ReloadOutlined />} onClick={() => void refresh()}>刷新</Button>{can("TASK_CREATE") && <Button type="primary" icon={<CloudUploadOutlined />} onClick={() => setOpen(true)}>上传数据</Button>}</Space></div>
    <Row gutter={[16, 16]} className="production-stats"><Col flex="1 1 170px"><Card><Statistic title="上传批次" value={metrics.total} /></Card></Col><Col flex="1 1 170px"><Card><Statistic title="处理中" value={metrics.processing} valueStyle={{ color: "#1677ff" }} /></Card></Col><Col flex="1 1 170px"><Card><Statistic title="已处理批次" value={metrics.processed} valueStyle={{ color: "#3f8600" }} /></Card></Col><Col flex="1 1 170px"><Card><Statistic title="待补录批次" value={metrics.needsInput} valueStyle={{ color: metrics.needsInput ? "#d46b08" : undefined }} /></Card></Col><Col flex="1 1 170px"><Card><Statistic title="失败批次" value={metrics.failed} valueStyle={{ color: metrics.failed ? "#cf1322" : undefined }} /></Card></Col></Row>
    {(uploads.isError || results.isError) && <Alert type="error" showIcon message={`${testStage}数据加载失败`} description={(uploads.error ?? results.error)?.message} />}
    <Card className="production-table-card"><Tabs items={[
      { key: "source", label: "原始文件", children: <Table rowKey={(r) => `${r.import_batch_id}-${r.sequence_no}`} columns={uploadColumns} dataSource={uploads.data ?? []} loading={uploads.isLoading} scroll={{ x: 1680 }} pagination={{ pageSize: 20, showSizeChanger: true }} /> },
      { key: "result", label: "清洗结果", children: <Table rowKey="result_summary_id" columns={resultColumns} dataSource={results.data ?? []} loading={results.isLoading} scroll={{ x: 1500 }} pagination={{ pageSize: 20, showSizeChanger: true }} /> },
    ]} /></Card>
    <Modal title={`上传${domainName[businessDomain]}${testStage}数据`} open={open} width={700} onCancel={() => !mutation.isPending && setOpen(false)} onOk={() => form.submit()} okText="上传并提交后台清洗" confirmLoading={mutation.isPending} destroyOnHidden>
      <Alert showIcon type="info" message={`上传身份：${user?.display_name}（${user?.login_name}）`} description="系统从当前登录账号自动记录上传人，无需填写。" />
      <Form form={form} layout="vertical" initialValues={{ factory_code: defaultFactory }} onFinish={(values) => mutation.mutate(values)} className="cp-upload-form">
        <Form.Item label="业务分类"><Space><Tag color="blue">{domainName[businessDomain]}</Tag><Tag color="cyan">{testStage}数据</Tag></Space></Form.Item>
        <Form.Item label={testStage === "CP" ? "晶圆厂" : "封测厂"} name="factory_code" rules={[{ required: true }]}><Select options={formalFactoryOptions[testStage]} /></Form.Item>
        <Form.Item label="服务器数据路径（与文件上传二选一）" name="source_path"><Input placeholder={testStage === "CP" ? "例如 F:\\data\\CP源数据\\厂家\\批次目录" : "请选择只包含本次 DC XLSX 的目录"} /></Form.Item>
        <Form.Item label={`${testStage}源文件`} required><Upload.Dragger multiple accept={selectedInput.accept} fileList={files} beforeUpload={() => false} onChange={({ fileList }) => setFiles(fileList)}><p className="ant-upload-drag-icon"><FileSearchOutlined /></p><p className="ant-upload-text">点击或拖入{factoryNames[selectedFactory]}{testStage}源文件</p><p className="ant-upload-hint">{selectedInput.hint} 上传后自动复用现有清洗逻辑。</p></Upload.Dragger></Form.Item>
        <Form.Item label="备注（可选）" name="remark"><Input.TextArea maxLength={500} rows={3} placeholder="可填写本次上传说明" /></Form.Item>
      </Form>
    </Modal>
    <LotEnrichmentModal
      open={lotBatchId !== undefined}
      businessDomain={businessDomain}
      testStage={testStage}
      importBatchId={lotBatchId}
      onClose={() => setLotBatchId(undefined)}
      onResolved={async (data) => {
        messageApi.success(`批次号已保存，批次已进入重新处理队列（任务 ${data.job_id}）`);
        setLotBatchId(undefined);
        await queryClient.invalidateQueries({ queryKey: scopeKey });
      }}
    />
    <Modal title="处理失败" open={failureRow !== undefined} footer={<Button onClick={() => setFailureRow(undefined)}>关闭</Button>} onCancel={() => setFailureRow(undefined)} destroyOnHidden>
      <Alert
        showIcon
        type="error"
        message="该批次未能完成处理"
        description={failureRow?.error_message || "系统未返回详细原因，请重新处理；如仍失败请联系管理员。"}
      />
      {failureRow?.error_code && <Typography.Paragraph type="secondary" style={{ marginTop: 16 }}>错误类型：{failureRow.error_code}</Typography.Paragraph>}
    </Modal>
  </div>;
}
