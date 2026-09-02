import { CloudServerOutlined, SearchOutlined } from "@ant-design/icons";
import { useMutation } from "@tanstack/react-query";
import { Alert, Button, Card, Col, Input, InputNumber, List, Row, Select, Space, Statistic, Typography, message } from "antd";
import { useState } from "react";

import { previewTemporaryFtp, type TemporaryFtpPreview, type TemporaryFtpRequest } from "../../api/quickAnalysis";

const displaySize = (value: number) => value < 1024 ** 2
  ? `${(value / 1024).toFixed(1)} KB`
  : `${(value / 1024 ** 2).toFixed(2)} MB`;

export function TemporaryFtpPanel() {
  const [form, setForm] = useState<TemporaryFtpRequest>({ protocol: "FTP", server: "", username: "", password: "", remote_path: "/" });
  const [preview, setPreview] = useState<TemporaryFtpPreview>();
  const [messageApi, contextHolder] = message.useMessage();
  const mutation = useMutation({
    mutationFn: () => previewTemporaryFtp(form),
    onSuccess: setPreview,
    onError: (error) => { setPreview(undefined); messageApi.error(error.message); },
  });
  const ready = form.server.trim() && form.username.trim() && form.password && form.remote_path.trim();

  return <>
    {contextHolder}
    <Alert
      showIcon
      type="info"
      message="临时 FTP 账号只用于本次目录预览"
      description="页面不会保存密码。确认需要反复分析后，请在后台配置为已配置服务器数据源，再由 Worker 后台执行；NAS 建议使用映射盘符/UNC 路径或后台配置。"
      style={{ marginBottom: 16 }}
    />
    <Card title={<Space><CloudServerOutlined />临时连接</Space>} extra={<Button type="primary" icon={<SearchOutlined />} disabled={!ready} loading={mutation.isPending} onClick={() => mutation.mutate()}>连接并预览</Button>}>
      <Space direction="vertical" size={12} style={{ width: "100%" }}>
        <Space wrap>
          <Select value={form.protocol} style={{ width: 100 }} options={[{ value: "FTP" }, { value: "FTPS" }]} onChange={(protocol) => setForm((value) => ({ ...value, protocol }))} />
          <Input aria-label="FTP服务器" style={{ width: 260 }} placeholder="FTP 服务器地址" value={form.server} onChange={(event) => setForm((value) => ({ ...value, server: event.target.value }))} />
          <InputNumber aria-label="FTP端口" min={1} max={65535} placeholder="端口（默认21）" value={form.port} onChange={(port) => setForm((value) => ({ ...value, port: port ?? undefined }))} />
        </Space>
        <Space wrap>
          <Input aria-label="FTP账号" style={{ width: 220 }} placeholder="账号" value={form.username} autoComplete="username" onChange={(event) => setForm((value) => ({ ...value, username: event.target.value }))} />
          <Input.Password aria-label="FTP密码" style={{ width: 220 }} placeholder="密码" value={form.password} autoComplete="current-password" onChange={(event) => setForm((value) => ({ ...value, password: event.target.value }))} />
          <Input aria-label="FTP目录" style={{ width: 360 }} placeholder="远程目录，例如 /CPFT/FT" value={form.remote_path} onChange={(event) => setForm((value) => ({ ...value, remote_path: event.target.value }))} />
        </Space>
        {preview && <Card size="small" type="inner" title={`${preview.protocol}://${preview.server}:${preview.port}${preview.remote_path}`}>
          <Row gutter={16}>
            <Col span={8}><Statistic title="CSV 文件" value={preview.file_count} /></Col>
            <Col span={8}><Statistic title="源数据大小" value={displaySize(preview.total_bytes)} /></Col>
            <Col span={8}><Statistic title="用途" value="目录确认" /></Col>
          </Row>
          <Typography.Text strong>文件示例（最多 20 个）</Typography.Text>
          <List size="small" dataSource={preview.sample_files} renderItem={(item) => <List.Item><Typography.Text code>{item}</Typography.Text></List.Item>} />
        </Card>}
      </Space>
    </Card>
  </>;
}
