import { FolderOpenOutlined, PlayCircleOutlined, SearchOutlined } from "@ant-design/icons";
import { useMutation } from "@tanstack/react-query";
import { Alert, Button, Card, Col, Input, Row, Select, Space, Statistic, Typography, message } from "antd";
import { useState } from "react";

import { createDirectPathPat, previewDirectPath, type DirectPathPreview } from "../../api/quickAnalysis";

const displaySize = (value: number) => {
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  if (value < 1024 * 1024 * 1024) return `${(value / 1024 / 1024).toFixed(2)} MB`;
  return `${(value / 1024 / 1024 / 1024).toFixed(2)} GB`;
};

export function DirectPathAnalysisPanel({ onCreated }: { onCreated: () => void }) {
  const [path, setPath] = useState("");
  const [preview, setPreview] = useState<DirectPathPreview>();
  const [messageApi, contextHolder] = message.useMessage();
  const previewMutation = useMutation({
    mutationFn: () => previewDirectPath(path),
    onSuccess: (value) => setPreview(value),
    onError: (error) => { setPreview(undefined); messageApi.error(error.message); },
  });
  const runMutation = useMutation({
    mutationFn: () => createDirectPathPat(preview!),
    onSuccess: (session) => {
      messageApi.success(`个人快速分析 ${session.analysis_session_id} 已进入后台队列`);
      onCreated();
    },
    onError: (error) => messageApi.error(error.message),
  });

  return <>
    {contextHolder}
    <Alert
      showIcon
      type="info"
      message="直接输入当前电脑可访问的目录"
      description="开发环境的 TMS 后端就在本机，可直接读取 F:\\ 等本地目录；NAS 若已映射盘符或当前 Windows 账号可访问 UNC 路径，也可直接填写。无需上传 520 个源文件。"
      style={{ marginBottom: 16 }}
    />
    <Card title={<Space><FolderOpenOutlined />目录与分析工具</Space>}>
      <Space direction="vertical" size={12} style={{ width: "100%" }}>
        <Space.Compact style={{ width: "100%" }}>
          <Input
            aria-label="本机或NAS目录"
            value={path}
            placeholder={String.raw`例如 F:\data\FT数据\...\NCEAP020N10LL 或 \\nas\share\lot`}
            onChange={(event) => { setPath(event.target.value); setPreview(undefined); }}
            onPressEnter={() => path.trim() && previewMutation.mutate()}
          />
          <Button type="primary" icon={<SearchOutlined />} disabled={!path.trim()} loading={previewMutation.isPending} onClick={() => previewMutation.mutate()}>预览解析范围</Button>
        </Space.Compact>
        <Space wrap>
          <Typography.Text strong>分析工具</Typography.Text>
          <Select
            value="JIEQUN_FT_QUICK_PAT_EXISTING"
            style={{ width: 300 }}
            options={[
              { value: "JIEQUN_FT_QUICK_PAT_EXISTING", label: "FT 工具 · 杰群原始目录 PAT" },
              { value: "CP_RAW_QUICK_PAT", label: "CP 工具 · 原始目录 PAT（接口待接入）", disabled: true },
            ]}
          />
          <Typography.Text type="secondary">仅做 PAT，不清洗、不写入正式数据库</Typography.Text>
        </Space>
        {preview && <Card size="small" type="inner" title={`已预览：${preview.source_label}`} extra={<Button type="primary" icon={<PlayCircleOutlined />} loading={runMutation.isPending} onClick={() => runMutation.mutate()}>开始后台 PAT</Button>}>
          <Row gutter={16}>
            <Col span={8}><Statistic title="CSV 文件" value={preview.file_count} /></Col>
            <Col span={8}><Statistic title="源数据大小" value={displaySize(preview.total_bytes)} /></Col>
            <Col span={8}><Statistic title="执行工具" value="FT PAT" /></Col>
          </Row>
          <Typography.Paragraph style={{ marginTop: 12, marginBottom: 0 }}><strong>目录：</strong><Typography.Text code copyable>{preview.path}</Typography.Text></Typography.Paragraph>
        </Card>}
      </Space>
    </Card>
  </>;
}
