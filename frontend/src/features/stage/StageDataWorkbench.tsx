import { BarChartOutlined, CloudUploadOutlined, DownloadOutlined, FileSearchOutlined, RedoOutlined, ReloadOutlined } from "@ant-design/icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, Button, Card, Col, Form, Input, Modal, Popconfirm, Row, Select, Space, Statistic, Table, Tabs, Tag, Typography, Upload, message } from "antd";
import type { UploadFile } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useMemo, useState } from "react";
import { BusinessDomain, TestStage, downloadStageUploadFile, listStageResults, listStageUploads, reprocessStageBatch, StageResultRow, StageUploadRow, uploadStageData } from "../../api/stageData";
import { useAuth } from "../auth/AuthContext";

const statusColor: Record<string, string> = { RECEIVED: "blue", QUEUED: "gold", PROCESSING: "processing", PROCESSED: "success", FAILED: "error" };
const statusName: Record<string, string> = { RECEIVED: "已接收", QUEUED: "排队中", PROCESSING: "处理中", PROCESSED: "已处理", FAILED: "失败" };
const domainName: Record<BusinessDomain, string> = { ENGINEERING: "工程", PRODUCTION: "量产" };
const factoryName: Record<string, string> = { huahong: "华虹", riyuexin: "日月新" };
const stageDescription: Record<TestStage, string> = {
  CP: "上传华虹CP源文件后，系统自动调用现有CP清洗程序并形成Lot/Wafer分析数据。",
  FT: "上传日月新FT源文件后，系统自动调用现有FT清洗程序并形成以产品型号为主线的分析数据。",
};
const stageFactories: Record<TestStage, { value: string; label: string }[]> = {
  CP: [{ value: "huahong", label: "华虹" }],
  FT: [{ value: "riyuexin", label: "日月新" }],
};
const stageAccept: Record<TestStage, string> = { CP: ".zip,.7z,.txt", FT: ".xlsx" };
const stageHint: Record<TestStage, string> = {
  CP: "支持华虹 ZIP、7Z 或多个 TXT；上传后自动复用现有清洗逻辑。",
  FT: "支持日月新 DC XLSX 源文件；上传后自动复用现有清洗逻辑。",
};
const dt = (value?: string | null) => value ? new Date(value).toLocaleString("zh-CN", { hour12: false }) : "—";
const size = (value: number) => value < 1024 * 1024 ? `${(value / 1024).toFixed(1)} KB` : `${(value / 1024 / 1024).toFixed(2)} MB`;

export interface StageDataWorkbenchProps {
  businessDomain: BusinessDomain;
  testStage: TestStage;
  onOpenAnalytics?: (datasetId: number, versionNo: number) => void;
}

export function StageDataWorkbench({ businessDomain, testStage, onOpenAnalytics }: StageDataWorkbenchProps) {
  const { user, can } = useAuth();
  const [open, setOpen] = useState(false);
  const [files, setFiles] = useState<UploadFile[]>([]);
  const [form] = Form.useForm<{ factory_code: string; remark?: string }>();
  const [messageApi, contextHolder] = message.useMessage();
  const queryClient = useQueryClient();
  const scopeKey = ["stage", businessDomain, testStage];
  const uploads = useQuery({ queryKey: [...scopeKey, "uploads"], queryFn: () => listStageUploads(businessDomain, testStage), refetchInterval: (query) => (query.state.data ?? []).some((row) => ["RECEIVED", "QUEUED", "PROCESSING"].includes(row.status)) ? 3000 : false });
  const results = useQuery({ queryKey: [...scopeKey, "results"], queryFn: () => listStageResults(businessDomain, testStage), refetchInterval: (query) => (uploads.data ?? []).some((row) => ["QUEUED", "PROCESSING"].includes(row.status)) ? 3000 : false });
  const refresh = async () => Promise.all([uploads.refetch(), results.refetch()]);
  const mutation = useMutation({
    mutationFn: async (values: { factory_code: string; remark?: string }) => {
      const nativeFiles = files.flatMap((item) => item.originFileObj ? [item.originFileObj as File] : []);
      if (!nativeFiles.length) throw new Error(`请选择${testStage}源文件`);
      return uploadStageData(businessDomain, testStage, nativeFiles, values.factory_code, values.remark);
    },
    onSuccess: async (data) => { messageApi.success(`批次 ${data.import_batch_id} 已进入后台清洗队列（任务 ${data.job_id}）`); setOpen(false); setFiles([]); form.resetFields(); await queryClient.invalidateQueries({ queryKey: scopeKey }); },
    onError: (error) => messageApi.error(error.message),
  });
  const reprocessMutation = useMutation({
    mutationFn: (batchId: number) => reprocessStageBatch(businessDomain, testStage, batchId),
    onSuccess: async (data) => { messageApi.success(`批次 ${data.import_batch_id} 已进入重新处理队列`); await queryClient.invalidateQueries({ queryKey: scopeKey }); },
    onError: (error) => messageApi.error(error.message),
  });
  const uploadColumns: ColumnsType<StageUploadRow> = [
    { title: "批次编号", dataIndex: "import_batch_id", width: 95, fixed: "left" },
    { title: "SEQ", dataIndex: "sequence_no", width: 70 },
    { title: "源文件名称", dataIndex: "original_file_name", width: 300, ellipsis: true },
    { title: "扩展名", dataIndex: "extension", width: 80, render: (v) => v.toUpperCase() },
    { title: "大小", dataIndex: "size_bytes", width: 100, render: size },
    { title: testStage === "CP" ? "晶圆厂" : "封测厂", dataIndex: "factory_code", width: 100, render: (v) => factoryName[v] ?? v },
    { title: "上传时间", dataIndex: "upload_time_utc", width: 175, render: dt },
    { title: "完成时间", dataIndex: "completion_time_utc", width: 175, render: dt },
    { title: "上传账号", dataIndex: "uploader_login", width: 120 },
    { title: "上传人", dataIndex: "uploader_name", width: 110 },
    { title: "状态", dataIndex: "status", width: 100, fixed: "right", render: (v) => <Tag color={statusColor[v]}>{statusName[v] ?? v}</Tag> },
    { title: "操作", key: "actions", width: 90, fixed: "right", render: (_, row) => <Button type="link" size="small" icon={<DownloadOutlined />} onClick={() => void downloadStageUploadFile(businessDomain, testStage, row.import_batch_id, row.receipt_id, row.original_file_name)}>下载</Button> },
  ];
  const resultColumns: ColumnsType<StageResultRow> = [
    { title: "名称", dataIndex: "data_name", width: 180, fixed: "left" },
    { title: "产品名称", dataIndex: "product_name", width: 180, render: (v) => v || "—" },
    { title: "批号", dataIndex: "lot_id", width: 160, render: (v) => v || "—" },
    ...(testStage === "CP" ? [{ title: "晶圆数", dataIndex: "wafer_count", width: 90 }] : []),
    { title: testStage === "CP" ? "晶圆厂" : "封测厂", dataIndex: "factory_code", width: 100, render: (v) => factoryName[v] ?? v },
    { title: "测试项", dataIndex: "test_item_count", width: 90 },
    { title: "总数", dataIndex: "unit_count", width: 105 },
    { title: "良品数", dataIndex: "pass_count", width: 105 },
    { title: "良率", dataIndex: "yield_rate", width: 100, render: (v: number | null) => v == null ? "—" : `${(v * 100).toFixed(2)}%` },
    { title: "状态", dataIndex: "status", width: 100, render: (v) => <Tag color="success">{statusName[v] ?? v}</Tag> },
    { title: "Data Type", dataIndex: "data_type", width: 105 },
    { title: "处理时间", dataIndex: "created_at_utc", width: 175, render: dt },
    { title: "操作", key: "actions", width: 210, fixed: "right", render: (_, row) => <Space size={0}>
      {row.dataset_id && row.dataset_version_no && can("ANALYSIS_RUN") && <Button type="link" size="small" icon={<BarChartOutlined />} onClick={() => onOpenAnalytics?.(row.dataset_id!, row.dataset_version_no!)}>数据分析</Button>}
      {can("TASK_CREATE") && <Popconfirm title="重新处理该批次？" description="将重跑现有清洗程序并归档旧结果。" onConfirm={() => reprocessMutation.mutate(row.import_batch_id)}><Button type="link" size="small" icon={<RedoOutlined />} loading={reprocessMutation.isPending && reprocessMutation.variables === row.import_batch_id}>重新处理</Button></Popconfirm>}
    </Space> },
  ];
  const metrics = useMemo(() => ({ total: new Set((uploads.data ?? []).map((r) => r.import_batch_id)).size, processing: (uploads.data ?? []).filter((r) => ["QUEUED", "PROCESSING"].includes(r.status)).length, processed: results.data?.length ?? 0, failed: (uploads.data ?? []).filter((r) => r.status === "FAILED").length }), [uploads.data, results.data]);

  return <div className="workbench production-workbench">
    {contextHolder}
    <div className="page-heading"><div><Typography.Text type="secondary">{domainName[businessDomain]}数据 / {testStage}数据</Typography.Text><Typography.Title level={2}>{testStage}数据</Typography.Title><Typography.Text type="secondary">{stageDescription[testStage]}</Typography.Text></div><Space><Button icon={<ReloadOutlined />} onClick={() => void refresh()}>刷新</Button>{can("TASK_CREATE") && <Button type="primary" icon={<CloudUploadOutlined />} onClick={() => setOpen(true)}>上传数据</Button>}</Space></div>
    <Row gutter={16} className="production-stats"><Col span={6}><Card><Statistic title="上传批次" value={metrics.total} /></Card></Col><Col span={6}><Card><Statistic title="处理中" value={metrics.processing} valueStyle={{ color: "#1677ff" }} /></Card></Col><Col span={6}><Card><Statistic title="已处理结果" value={metrics.processed} valueStyle={{ color: "#3f8600" }} /></Card></Col><Col span={6}><Card><Statistic title="失败" value={metrics.failed} valueStyle={{ color: metrics.failed ? "#cf1322" : undefined }} /></Card></Col></Row>
    {(uploads.isError || results.isError) && <Alert type="error" showIcon message={`${testStage}数据加载失败`} description={(uploads.error ?? results.error)?.message} />}
    <Card className="production-table-card"><Tabs items={[
      { key: "source", label: "原始文件", children: <Table rowKey={(r) => `${r.import_batch_id}-${r.sequence_no}`} columns={uploadColumns} dataSource={uploads.data ?? []} loading={uploads.isLoading} scroll={{ x: 1450 }} pagination={{ pageSize: 20, showSizeChanger: true }} /> },
      { key: "result", label: "清洗结果", children: <Table rowKey="result_summary_id" columns={resultColumns} dataSource={results.data ?? []} loading={results.isLoading} scroll={{ x: 1500 }} pagination={{ pageSize: 20, showSizeChanger: true }} /> },
    ]} /></Card>
    <Modal title={`上传${domainName[businessDomain]}${testStage}数据`} open={open} width={700} onCancel={() => !mutation.isPending && setOpen(false)} onOk={() => form.submit()} okText="上传并提交后台清洗" confirmLoading={mutation.isPending} destroyOnHidden>
      <Alert showIcon type="info" message={`上传身份：${user?.display_name}（${user?.login_name}）`} description="系统从当前登录账号自动记录上传人，无需填写。" />
      <Form form={form} layout="vertical" initialValues={{ factory_code: stageFactories[testStage][0].value }} onFinish={(values) => mutation.mutate(values)} className="cp-upload-form">
        <Form.Item label="业务分类"><Space><Tag color="blue">{domainName[businessDomain]}</Tag><Tag color="cyan">{testStage}数据</Tag></Space></Form.Item>
        <Form.Item label={testStage === "CP" ? "晶圆厂" : "封测厂"} name="factory_code" rules={[{ required: true }]}><Select options={stageFactories[testStage]} /></Form.Item>
        <Form.Item label={`${testStage}源文件`} required><Upload.Dragger multiple accept={stageAccept[testStage]} fileList={files} beforeUpload={() => false} onChange={({ fileList }) => setFiles(fileList)}><p className="ant-upload-drag-icon"><FileSearchOutlined /></p><p className="ant-upload-text">点击或拖入{testStage}源文件</p><p className="ant-upload-hint">{stageHint[testStage]}</p></Upload.Dragger></Form.Item>
        <Form.Item label="备注（可选）" name="remark"><Input.TextArea maxLength={500} rows={3} placeholder="可填写本次上传说明" /></Form.Item>
      </Form>
    </Modal>
  </div>;
}
