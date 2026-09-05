import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, Button, Card, Descriptions, Form, Input, InputNumber, Modal, Select, Space, Table, Tag, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import { checkFtpConnection, createFtpSource, getFtpOptions, listFtpPackages, listFtpSources, requestFtpScan, retryFtpPackage, setFtpSourceActive, type FtpPackageRow, type FtpSourceConfig, type FtpSourceRow } from "../../api/ftpSources";
import { formatUtcDateTime } from "../../utils/dateTime";
import { factoryNames } from "../capabilities/capabilityCatalog";

const factories = { CP: ["HUAHONG", "JETECH", "LION"], FT: ["RIYUEXIN", "RIYUEGUANG", "DIANJI"] };
const suffixes: Record<string, string[]> = { HUAHONG: [".zip", ".7z", ".txt"], JETECH: [".zip", ".xls", ".xlsx"], LION: [".zip", ".xls", ".xlsx"], RIYUEXIN: [".xlsx"], RIYUEGUANG: [".xlsx"], DIANJI: [".xls", ".xlsx"] };
const states: Record<string, string> = { IDLE: "尚未采集", RUNNING: "采集中", SUCCESS: "完成", FAILED: "失败", INTERRUPTED: "执行中断", WAITING: "等待完整与稳定", RETRY: "等待重试", SUBMITTED: "已提交入库", CHANGED: "源文件已变化", QUEUED: "排队中", NEEDS_INPUT: "待补充信息", CANCELLED: "已取消" };

export function FtpSourcesPanel({ canManage, onOpenJob }: { canManage: boolean; onOpenJob: (id: number) => void }) {
  const queryClient = useQueryClient();
  const [form] = Form.useForm<FtpSourceConfig>();
  const [creating, setCreating] = useState(false);
  const [selected, setSelected] = useState<FtpSourceRow>();
  const [page, setPage] = useState(1);
  const [message, setMessage] = useState<string>();
  const sources = useQuery({ queryKey: ["ftp-sources"], queryFn: listFtpSources, retry: false, refetchInterval: 5000 });
  const options = useQuery({ queryKey: ["ftp-options"], queryFn: getFtpOptions, enabled: canManage && creating, retry: false });
  const packages = useQuery({ queryKey: ["ftp-packages", selected?.source_definition_id, page], queryFn: () => listFtpPackages(selected!.source_definition_id, page), enabled: Boolean(selected), retry: false, refetchInterval: 5000 });
  const stage = Form.useWatch("test_stage", form) ?? "CP";
  const factory = Form.useWatch("factory_code", form);
  const mode = Form.useWatch("package_mode", form);
  const refresh = async () => { await queryClient.invalidateQueries({ queryKey: ["ftp-sources"] }); await queryClient.invalidateQueries({ queryKey: ["ftp-packages"] }); };
  const control = useMutation({ mutationFn: async ({ source, action }: { source: FtpSourceRow; action: "toggle" | "scan" | "check" }) => {
    setMessage(undefined);
    if (action === "check") { const result = await checkFtpConnection(source.source_definition_id); return result.message; }
    if (action === "scan") { await requestFtpScan(source.source_definition_id); return "采集请求已登记，等待后台执行；稳定性检查仍会执行。"; }
    await setFtpSourceActive(source.source_definition_id, !source.active);
    return source.active ? "已暂停后续采集，正在执行的采集会在提交前重新检查。" : "已启用定时采集。";
  }, onSuccess: async (text) => { setMessage(text); await refresh(); } });
  const create = useMutation({ mutationFn: (values: FtpSourceConfig) => createFtpSource({ ...values, ready_marker: values.package_mode === "DIRECTORY" ? values.ready_marker : null }), onSuccess: async () => {
    setCreating(false); form.resetFields(); setMessage("FTP 数据源已保存，当前处于暂停状态。请检查连接后启用采集。"); await refresh();
  } });
  const retry = useMutation({ mutationFn: (packageId: number) => retryFtpPackage(selected!.source_definition_id, packageId), onSuccess: refresh });
  const columns: ColumnsType<FtpSourceRow> = [
    { title: "数据源", dataIndex: "source_name", width: 180 },
    { title: "阶段 / 厂家", key: "scope", width: 160, render: (_, row) => `${row.test_stage} / ${factoryNames[row.factory_code.toLowerCase()] ?? row.factory_code}` },
    { title: "数据归属", dataIndex: "domain_name", width: 140 },
    { title: "协议 / 周期", key: "protocol", width: 140, render: (_, row) => `${row.protocol} / ${row.interval_seconds} 秒` },
    { title: "采集状态", key: "status", width: 180, render: (_, row) => <Space direction="vertical" size={0}><Tag color={row.active ? "blue" : "default"}>{row.active ? "已启用" : "已暂停"}</Tag><Typography.Text>{row.last_status === "RUNNING" && row.lease_expires_at_utc && new Date(row.lease_expires_at_utc + (row.lease_expires_at_utc.endsWith("Z") ? "" : "Z")).getTime() < Date.now() ? "执行中断，等待恢复" : states[row.last_status] ?? row.last_status}</Typography.Text>{row.error_message && <Typography.Text type="danger">{row.error_message}</Typography.Text>}</Space> },
    { title: "上次完成", dataIndex: "last_finished_at_utc", width: 170, render: formatUtcDateTime },
    { title: "操作", key: "actions", width: canManage ? 340 : 100, render: (_, row) => <Space wrap>
      <Button size="small" onClick={() => { setSelected(row); setPage(1); }}>采集记录</Button>
      {canManage && <><Button size="small" disabled={control.isPending} onClick={() => control.mutate({ source: row, action: "check" })}>检查连接</Button><Button size="small" disabled={control.isPending} onClick={() => control.mutate({ source: row, action: "toggle" })}>{row.active ? "暂停" : "启用"}</Button><Button size="small" disabled={!row.active || control.isPending} onClick={() => control.mutate({ source: row, action: "scan" })}>立即采集</Button></>}
    </Space> },
  ];
  const packageColumns: ColumnsType<FtpPackageRow> = [
    { title: "源文件 / 批次目录", dataIndex: "relative_path", ellipsis: true },
    { title: "文件数", dataIndex: "file_count", width: 90 },
    { title: "大小", dataIndex: "total_bytes", width: 130, render: (value: number) => `${(value / 1024 / 1024).toFixed(2)} MB` },
    { title: "状态", key: "status", width: 210, render: (_, row) => <Space direction="vertical" size={0}><Typography.Text>{states[row.status] ?? row.status}{row.job_status ? ` / 清洗${states[row.job_status] ?? row.job_status}` : ""}</Typography.Text>{row.error_message && <Typography.Text type="danger">{row.error_message}</Typography.Text>}</Space> },
    { title: "操作", key: "actions", width: 140, render: (_, row) => row.job_id ? <Button size="small" type="link" onClick={() => onOpenJob(row.job_id!)}>查看入库任务</Button> : canManage && ["FAILED", "RETRY"].includes(row.status) ? <Button size="small" loading={retry.isPending} onClick={() => retry.mutate(row.ftp_package_id)}>重试采集</Button> : "—" },
  ];
  return <Card title="FTP 自动采集" className="production-table-card" style={{ marginBottom: 18 }} extra={canManage && <Button type="primary" onClick={() => { setCreating(true); create.reset(); }}>新增 FTP 数据源</Button>}>
    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
      <Typography.Text type="secondary">源文件只读采集，正式结果以清洗任务成功为准。已采集路径变化会保留异常记录，历史数据不会自动覆盖。</Typography.Text>
      {message && <Alert type="success" showIcon message={message} />}
      {(sources.isError || control.isError) && <Alert type="error" showIcon message="FTP 数据源操作失败" description={(sources.error ?? control.error)?.message} />}
      <Table rowKey="source_definition_id" columns={columns} dataSource={sources.data ?? []} loading={sources.isLoading} pagination={{ pageSize: 10 }} scroll={{ x: 1250 }} locale={{ emptyText: "暂无可见的 FTP 自动采集数据源" }} expandable={canManage ? { expandedRowRender: (row) => row.config ? <Descriptions size="small" column={2} items={[
        { key: "host", label: "服务器", children: `${row.config.host}:${row.config.port}` }, { key: "root", label: "根目录", children: row.config.remote_root },
        { key: "credential", label: "凭据引用", children: row.config.credential_ref }, { key: "mode", label: "采集单位", children: row.config.package_mode === "DIRECTORY" ? `第 ${row.config.package_depth} 层目录，完成标记 ${row.config.ready_marker}` : "单个文件" },
      ]} /> : null } : undefined} />
    </Space>
    <Modal title={`采集记录${selected ? ` · ${selected.source_name}` : ""}`} open={Boolean(selected)} onCancel={() => setSelected(undefined)} footer={null} width={1100}>
      {(packages.isError || retry.isError) && <Alert type="error" message={(packages.error ?? retry.error)?.message} />}
      <Table rowKey="ftp_package_id" dataSource={packages.data?.items ?? []} columns={packageColumns} loading={packages.isLoading} pagination={{ current: page, pageSize: 30, total: packages.data?.total ?? 0, onChange: setPage, showSizeChanger: false }} />
    </Modal>
    <Modal title="新增 FTP 自动采集数据源" open={creating} onCancel={() => setCreating(false)} footer={null} width={850}>
      <Alert type="info" showIcon message="凭据需在 API/采集 Worker 运行账号下通过本机配置入口保存。此处只填写引用；数据源保存后默认暂停。" style={{ marginBottom: 18 }} />
      {(create.isError || options.isError) && <Alert type="error" showIcon message={(create.error ?? options.error)?.message} />}
      <Form form={form} layout="vertical" initialValues={{ protocol: "FTP", port: 21, encoding: "utf-8", test_stage: "CP", package_mode: "DIRECTORY", package_depth: 1, interval_seconds: 300, stable_seconds: 120 }} onFinish={(values) => create.mutate(values)}>
        <Space align="start" wrap>
          <Form.Item name="source_code" label="数据源编码" rules={[{ required: true, pattern: /^[A-Z][A-Z0-9_-]{1,63}$/ }]}><Input placeholder="例如 HUAHONG_CP_FTP" /></Form.Item>
          <Form.Item name="source_name" label="数据源名称" rules={[{ required: true }]}><Input /></Form.Item>
          <Form.Item name="protocol" label="协议" rules={[{ required: true }]}><Select style={{ width: 110 }} options={[{ value: "FTP" }, { value: "FTPS", label: "FTPS 显式 TLS" }]} /></Form.Item>
          <Form.Item name="host" label="服务器地址" rules={[{ required: true }]}><Input placeholder="主机名或 IPv4" /></Form.Item>
          <Form.Item name="port" label="端口" rules={[{ required: true }]}><InputNumber min={1} max={65535} /></Form.Item>
          <Form.Item name="remote_root" label="远端根目录" rules={[{ required: true }]}><Input placeholder="/CP/厂家" /></Form.Item>
          <Form.Item name="credential_ref" label="本机凭据引用" rules={[{ required: true, pattern: /^[A-Z][A-Z0-9_-]{1,63}$/ }]}><Input placeholder="已保存的凭据引用" /></Form.Item>
          <Form.Item name="encoding" label="文件名编码"><Select style={{ width: 140 }} options={[{ value: "utf-8", label: "UTF-8" }, { value: "gb18030", label: "GB18030" }]} /></Form.Item>
          <Form.Item name="test_stage" label="测试阶段" rules={[{ required: true }]}><Select style={{ width: 110 }} options={[{ value: "CP" }, { value: "FT" }]} onChange={() => form.setFieldsValue({ factory_code: undefined, data_domain_id: undefined, cleaner_release_id: undefined, allowed_suffixes: [] })} /></Form.Item>
          <Form.Item name="factory_code" label="厂家" rules={[{ required: true }]}><Select style={{ width: 140 }} options={factories[stage as "CP" | "FT"].map(value => ({ value, label: factoryNames[value.toLowerCase()] ?? value }))} onChange={() => form.setFieldsValue({ data_domain_id: undefined, cleaner_release_id: undefined, allowed_suffixes: [] })} /></Form.Item>
          <Form.Item name="data_domain_id" label="数据归属域" rules={[{ required: true }]}><Select style={{ width: 200 }} options={(options.data?.domains ?? []).filter(item => item.test_stage === stage && (!item.factory_code || item.factory_code === factory)).map(item => ({ value: item.data_domain_id, label: item.domain_name }))} /></Form.Item>
          <Form.Item name="cleaner_release_id" label="固定清洗版本" rules={[{ required: true }]}><Select style={{ width: 200 }} options={(options.data?.releases ?? []).filter(item => item.test_stage === stage && item.factory_code === factory).map(item => ({ value: item.cleaner_release_id, label: `${item.cleaner_version}（登记 ${item.cleaner_release_id}）` }))} /></Form.Item>
          <Form.Item name="allowed_suffixes" label="允许的源文件类型" rules={[{ required: true }]}><Select mode="multiple" style={{ width: 230 }} options={(suffixes[factory] ?? []).map(value => ({ value }))} /></Form.Item>
          <Form.Item name="package_mode" label="一个入库批次包含" rules={[{ required: true }]}><Select style={{ width: 190 }} options={[{ value: "DIRECTORY", label: "一个完整子目录" }, { value: "SINGLE_FILE", label: "单个文件或归档包" }]} /></Form.Item>
          {mode === "DIRECTORY" && <><Form.Item name="package_depth" label="批次目录层级" rules={[{ required: true }]}><InputNumber min={1} max={4} /></Form.Item><Form.Item name="ready_marker" label="完成标记文件名" rules={[{ required: true }]}><Input placeholder="由数据提供方确认" /></Form.Item></>}
          <Form.Item name="interval_seconds" label="扫描周期（秒）" rules={[{ required: true }]}><InputNumber min={30} max={86400} /></Form.Item>
          <Form.Item name="stable_seconds" label="稳定窗口（秒）" rules={[{ required: true }]}><InputNumber min={30} max={86400} /></Form.Item>
        </Space>
        <Typography.Paragraph type="secondary">目录层级从根目录向下计算：根目录/批次为第 1 层，根目录/产品/批次为第 2 层。每个源固定阶段与厂家；修改目录或输入合同请新建数据源。</Typography.Paragraph>
        <Button htmlType="submit" type="primary" loading={create.isPending} disabled={options.isError}>保存为暂停状态</Button>
      </Form>
    </Modal>
  </Card>;
}
