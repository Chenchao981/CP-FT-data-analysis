import { ArrowUpOutlined, FileOutlined, FileZipOutlined, FolderOpenOutlined, PlayCircleOutlined, SearchOutlined } from "@ant-design/icons";
import { useMutation } from "@tanstack/react-query";
import { Alert, Button, Card, Col, Input, List, Modal, Row, Select, Space, Statistic, Table, Tag, Typography, message } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useState } from "react";

import {
  browseDirectPath,
  createDirectPathPat,
  previewDirectPath,
  type DirectPathBrowseItem,
  type DirectPathPreview,
  type DirectPathToolCode,
} from "../../api/quickAnalysis";

const displaySize = (value: number) => {
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  if (value < 1024 * 1024 * 1024) return `${(value / 1024 / 1024).toFixed(2)} MB`;
  return `${(value / 1024 / 1024 / 1024).toFixed(2)} GB`;
};

export function DirectPathAnalysisPanel({ onCreated }: { onCreated: () => void }) {
  const [path, setPath] = useState("");
  const [toolCode, setToolCode] = useState<DirectPathToolCode>("JIEQUN_FT_QUICK_PAT_EXISTING");
  const [preview, setPreview] = useState<DirectPathPreview>();
  const [browserOpen, setBrowserOpen] = useState(false);
  const [browserPath, setBrowserPath] = useState("");
  const [messageApi, contextHolder] = message.useMessage();
  const previewMutation = useMutation({
    mutationFn: () => previewDirectPath(path, toolCode),
    onSuccess: (value) => setPreview(value),
    onError: (error) => { setPreview(undefined); messageApi.error(error.message); },
  });
  const browseMutation = useMutation({
    mutationFn: (targetPath: string) => browseDirectPath(targetPath, toolCode),
    onSuccess: (value) => {
      setBrowserPath(value.path ?? "");
      setBrowserOpen(true);
    },
    onError: (error) => messageApi.error(error.message),
  });
  const runMutation = useMutation({
    mutationFn: () => createDirectPathPat(preview!),
    onSuccess: (session) => {
      messageApi.success(`个人快速分析 ${session.analysis_session_id} 已进入后台队列`);
      onCreated();
    },
    onError: (error) => messageApi.error(error.message),
  });
  const chooseSource = (sourcePath: string) => {
    setPath(sourcePath);
    setPreview(undefined);
    setBrowserOpen(false);
  };
  const browseColumns: ColumnsType<DirectPathBrowseItem> = [
    {
      title: "名称",
      dataIndex: "name",
      ellipsis: true,
      render: (name, row) => <Button type="link" disabled={!row.selectable} title={row.selection_hint ?? undefined} icon={row.kind === "DIRECTORY" ? <FolderOpenOutlined /> : row.is_archive ? <FileZipOutlined /> : <FileOutlined />} onClick={() => row.kind === "DIRECTORY" ? browseMutation.mutate(row.path) : chooseSource(row.path)}>{name}</Button>,
    },
    { title: "类型", dataIndex: "kind", width: 100, render: (_, row) => row.kind === "DIRECTORY" ? <Tag color="blue">文件夹</Tag> : row.is_archive ? <Tag color="purple">压缩包</Tag> : <Tag>源文件</Tag> },
    { title: "大小", dataIndex: "size_bytes", width: 120, render: (value: number | null) => value == null ? "—" : displaySize(value) },
    { title: "操作", key: "action", width: 120, render: (_, row) => <Button size="small" disabled={!row.selectable} title={row.selection_hint ?? undefined} onClick={() => row.kind === "DIRECTORY" ? browseMutation.mutate(row.path) : chooseSource(row.path)}>{row.kind === "DIRECTORY" ? "打开" : row.selectable ? "选择" : "选所在文件夹"}</Button> },
  ];

  return <>
    {contextHolder}
      <Alert
        showIcon
        type="info"
        className="compact-info-alert"
        message="可浏览或输入当前电脑可访问的本地盘、映射盘及 UNC 路径；支持选择文件夹或当前工具认可的单个源文件/压缩包。"
      />
    <Card title={<Space><FolderOpenOutlined />目录与分析工具</Space>}>
      <Space direction="vertical" size={12} style={{ width: "100%" }}>
        <Space.Compact style={{ width: "100%" }}>
          <Input
            aria-label="本机、NAS目录或源文件"
            value={path}
            placeholder={String.raw`例如 F:\data\FT数据\...\NCEAP020N10LL、源文件或 \\nas\share\lot`}
            onChange={(event) => { setPath(event.target.value); setPreview(undefined); }}
            onPressEnter={() => path.trim() && previewMutation.mutate()}
          />
          <Button icon={<FolderOpenOutlined />} loading={browseMutation.isPending} onClick={() => browseMutation.mutate(path.trim())}>浏览路径</Button>
          <Button type="primary" icon={<SearchOutlined />} disabled={!path.trim()} loading={previewMutation.isPending} onClick={() => previewMutation.mutate()}>预览解析范围</Button>
        </Space.Compact>
        <Space wrap>
          <Typography.Text strong>分析工具</Typography.Text>
          <Select
            aria-label="分析工具"
            value={toolCode}
            style={{ width: 330 }}
            onChange={(value: DirectPathToolCode) => { setToolCode(value); setPreview(undefined); setBrowserPath(""); }}
            options={[
              { value: "JIEQUN_FT_QUICK_PAT_EXISTING", label: "FT 工具 · 杰群原始目录 PAT" },
              { value: "RIYUEXIN_FT_QUICK_PAT_EXISTING", label: "FT 工具 · 日月新原始目录 PAT" },
              { value: "RIYUEGUANG_FT_QUICK_PAT_EXISTING", label: "FT 工具 · 日月光原始目录 PAT" },
              { value: "DIANJI_FT_QUICK_PAT_EXISTING", label: "FT 工具 · 电基原始目录 PAT" },
              { value: "JIJIA_FT_QUICK_PAT_EXISTING", label: "FT 工具 · 集佳原始目录 PAT" },
              { value: "HUAHONG_CP_QUICK_PAT_EXISTING", label: "CP 工具 · 华虹原始目录 PAT" },
              { value: "JETECH_CP_QUICK_PAT_EXISTING", label: "CP 工具 · 积塔原始目录 PAT" },
              { value: "LION_CP_QUICK_PAT_EXISTING", label: "CP 工具 · 立昂微原始目录 PAT" },
              { value: "GUOYU_CP_QUICK_PAT_EXISTING", label: "CP 工具 · 国宇原始目录 PAT" },
            ]}
          />
          <Typography.Text type="secondary">仅做 PAT，不清洗、不写入正式数据库</Typography.Text>
        </Space>
        {preview && <Card size="small" type="inner" title={`已预览：${preview.source_label}`} extra={<Button type="primary" icon={<PlayCircleOutlined />} loading={runMutation.isPending} onClick={() => runMutation.mutate()}>开始后台 PAT</Button>}>
          <Row gutter={16}>
            <Col span={8}><Statistic title="源文件" value={preview.file_count} /></Col>
            <Col span={8}><Statistic title="源数据大小" value={displaySize(preview.total_bytes)} /></Col>
            <Col span={8}><Statistic title="执行工具" value={`${preview.test_stage} · ${preview.factory_code}`} /></Col>
          </Row>
          <Space wrap style={{ marginTop: 12 }}>
            <Tag color={preview.input_kind === "DIRECTORY" ? "blue" : "purple"}>{preview.input_kind === "DIRECTORY" ? "文件夹" : "单个文件"}</Tag>
            {preview.archive_count > 0 && <Tag color="purple">压缩包 {preview.archive_count} 个</Tag>}
            <Typography.Text><strong>解析来源：</strong><Typography.Text code copyable>{preview.path}</Typography.Text></Typography.Text>
          </Space>
          <List
            size="small"
            header={<Typography.Text strong>将解析的文件{preview.sample_truncated ? "（前 100 个）" : ""}</Typography.Text>}
            dataSource={preview.sample_files}
            renderItem={(item) => <List.Item><Typography.Text code>{item}</Typography.Text></List.Item>}
            style={{ marginTop: 10, maxHeight: 260, overflow: "auto" }}
          />
        </Card>}
      </Space>
    </Card>
    <Modal
      title="浏览本机 / NAS 数据来源"
      open={browserOpen}
      width={900}
      onCancel={() => setBrowserOpen(false)}
      footer={<Space>
        <Button onClick={() => setBrowserOpen(false)}>取消</Button>
        <Button type="primary" disabled={!browseMutation.data?.path} onClick={() => browseMutation.data?.path && chooseSource(browseMutation.data.path)}>使用当前文件夹</Button>
      </Space>}
    >
      <Alert
        showIcon
        type="info"
        className="compact-info-alert"
        message={`当前工具支持：${browseMutation.data?.allowed_suffixes.join("、") || "正在读取"}`}
        description={toolCode === "HUAHONG_CP_QUICK_PAT_EXISTING"
          ? "双击或打开文件夹继续浏览；华虹原始 TXT 需选择所在文件夹以保留产品/批次信息，ZIP/7z 可直接选择。数据不会上传。"
          : "双击或打开文件夹继续浏览；可选择单个源文件、ZIP/7z，或使用当前文件夹。数据不会上传。"}
      />
      <Space.Compact style={{ width: "100%", marginBottom: 12 }}>
        <Button icon={<ArrowUpOutlined />} disabled={!browseMutation.data?.parent_path} onClick={() => browseMutation.data?.parent_path && browseMutation.mutate(browseMutation.data.parent_path)}>上一级</Button>
        <Input aria-label="路径浏览地址" value={browserPath} placeholder="输入本地盘、映射盘或 UNC 路径" onChange={(event) => setBrowserPath(event.target.value)} onPressEnter={() => browseMutation.mutate(browserPath)} />
        <Button icon={<SearchOutlined />} loading={browseMutation.isPending} onClick={() => browseMutation.mutate(browserPath)}>转到</Button>
      </Space.Compact>
      {browseMutation.data?.truncated && <Alert type="warning" showIcon message="当前目录项目较多，仅显示前 2000 项，请进入更具体的文件夹。" style={{ marginBottom: 12 }} />}
      <Table
        rowKey="path"
        size="small"
        loading={browseMutation.isPending}
        columns={browseColumns}
        dataSource={browseMutation.data?.items ?? []}
        pagination={false}
        scroll={{ y: 420 }}
        onRow={(row) => ({ onDoubleClick: () => row.kind === "DIRECTORY" ? browseMutation.mutate(row.path) : row.selectable && chooseSource(row.path) })}
      />
    </Modal>
  </>;
}
