import {
  CloudServerOutlined,
  DownloadOutlined,
  FolderOpenOutlined,
  LeftOutlined,
  PlayCircleOutlined,
  ReloadOutlined,
} from "@ant-design/icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Alert,
  Button,
  Card,
  Col,
  Empty,
  Row,
  Select,
  Space,
  Statistic,
  Table,
  Tag,
  Typography,
  message,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { useEffect, useMemo, useState } from "react";

import {
  createQuickPat,
  downloadQuickPat,
  listQuickAnalysisSessions,
  listQuickSourceDirectories,
  listQuickSourceRoots,
  type QuickAnalysisSession,
  type QuickSourceDirectory,
} from "../../api/quickAnalysis";

const statusColor: Record<string, string> = {
  QUEUED: "gold",
  RUNNING: "processing",
  SUCCESS: "success",
  FAILED: "error",
  CANCELLED: "default",
  EXPIRED: "default",
};
const statusName: Record<string, string> = {
  QUEUED: "排队中",
  RUNNING: "计算中",
  SUCCESS: "已完成",
  FAILED: "失败",
  CANCELLED: "已取消",
  EXPIRED: "已过期",
};
const dt = (value?: string | null) => value ? new Date(value).toLocaleString("zh-CN", { hour12: false }) : "—";
const size = (value?: number | null) => {
  if (value == null) return "—";
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  if (value < 1024 * 1024 * 1024) return `${(value / 1024 / 1024).toFixed(2)} MB`;
  return `${(value / 1024 / 1024 / 1024).toFixed(2)} GB`;
};
const count = (value?: number | null) => value == null ? "—" : value.toLocaleString("zh-CN");

export function QuickAnalysisWorkbench() {
  const [rootCode, setRootCode] = useState<string>();
  const [relativePath, setRelativePath] = useState(".");
  const [messageApi, contextHolder] = message.useMessage();
  const queryClient = useQueryClient();
  const roots = useQuery({ queryKey: ["quick-analysis", "roots"], queryFn: listQuickSourceRoots });
  useEffect(() => {
    if (!rootCode && roots.data?.length) {
      const first = roots.data.find((item) => item.available) ?? roots.data[0];
      setRootCode(first.code);
      setRelativePath(".");
    }
  }, [rootCode, roots.data]);
  const directories = useQuery({
    queryKey: ["quick-analysis", "directories", rootCode, relativePath],
    queryFn: () => listQuickSourceDirectories(rootCode!, relativePath),
    enabled: Boolean(rootCode),
  });
  const sessions = useQuery({
    queryKey: ["quick-analysis", "sessions"],
    queryFn: listQuickAnalysisSessions,
    refetchInterval: (query) => (query.state.data ?? []).some((item) => ["QUEUED", "RUNNING"].includes(item.status)) ? 3000 : false,
  });
  const createMutation = useMutation({
    mutationFn: () => createQuickPat(rootCode!, directories.data?.current_relative_path ?? relativePath),
    onSuccess: async (created) => {
      messageApi.success(`快速 PAT 会话 ${created.analysis_session_id} 已进入后台队列（任务 ${created.job_id}）`);
      await queryClient.invalidateQueries({ queryKey: ["quick-analysis", "sessions"] });
    },
    onError: (error) => messageApi.error(error.message),
  });
  const metrics = useMemo(() => ({
    total: sessions.data?.length ?? 0,
    running: (sessions.data ?? []).filter((item) => ["QUEUED", "RUNNING"].includes(item.status)).length,
    success: (sessions.data ?? []).filter((item) => item.status === "SUCCESS").length,
    failed: (sessions.data ?? []).filter((item) => item.status === "FAILED").length,
  }), [sessions.data]);

  const directoryColumns: ColumnsType<QuickSourceDirectory> = [
    { title: "目录", dataIndex: "name", render: (name, row) => <Button type="link" icon={<FolderOpenOutlined />} onClick={() => setRelativePath(row.relative_path)}>{name}</Button> },
    { title: "本层 CSV", dataIndex: "direct_file_count", width: 110, render: count },
    { title: "本层大小", dataIndex: "direct_total_bytes", width: 130, render: size },
  ];
  const sessionColumns: ColumnsType<QuickAnalysisSession> = [
    { title: "会话", dataIndex: "analysis_session_id", width: 85, fixed: "left" },
    { title: "数据源", dataIndex: "source_root_code", width: 150 },
    { title: "相对目录", dataIndex: "source_relative_path", width: 260, ellipsis: true },
    { title: "源文件", dataIndex: "source_file_count", width: 95, render: count },
    { title: "源数据量", dataIndex: "source_total_bytes", width: 115, render: size },
    { title: "状态", dataIndex: "status", width: 100, render: (value) => <Tag color={statusColor[value]}>{statusName[value] ?? value}</Tag> },
    { title: "参数", dataIndex: "parameter_count", width: 85, render: count },
    { title: "解析数据行", dataIndex: "record_count", width: 125, render: count },
    { title: "计算耗时", key: "elapsed", width: 105, render: (_, row) => row.summary?.elapsed_seconds == null ? "—" : `${row.summary.elapsed_seconds.toFixed(3)} 秒` },
    { title: "创建人", dataIndex: "owner_name", width: 100 },
    { title: "创建时间", dataIndex: "created_at_utc", width: 175, render: dt },
    { title: "结果到期", dataIndex: "expires_at_utc", width: 175, render: dt },
    { title: "错误", dataIndex: "error_message", width: 240, ellipsis: true, render: (value) => value || "—" },
    { title: "操作", key: "actions", width: 100, fixed: "right", render: (_, row) => row.status === "SUCCESS" && row.result_file_name ? <Button type="link" size="small" icon={<DownloadOutlined />} onClick={() => void downloadQuickPat(row.analysis_session_id, row.result_file_name!)}>下载 PAT</Button> : "—" },
  ];
  const selectedRoot = roots.data?.find((item) => item.code === rootCode);

  return <div className="workbench quick-analysis-workbench">
    {contextHolder}
    <div className="page-heading">
      <div><Typography.Text type="secondary">快速计算 / FT PAT</Typography.Text><Typography.Title level={2}>快速分析</Typography.Title><Typography.Text type="secondary">直接读取受控服务器目录，复用已发布杰群 PAT 工具；不上传原始文件，也不写入正式 Canonical 明细。</Typography.Text></div>
      <Button icon={<ReloadOutlined />} onClick={() => void Promise.all([roots.refetch(), directories.refetch(), sessions.refetch()])}>刷新</Button>
    </div>
    <Alert className="quick-analysis-alert" showIcon type="info" message="当前 P0：杰群统一 CSV 原始目录 → 低内存 PAT Excel" description="系统只保存来源 Manifest、工具版本、运行状态和结果文件，默认 7 天后过期。若要长期追溯或跨批次正式分析，请使用正式入库。" />
    <Row gutter={16} className="production-stats"><Col span={6}><Card><Statistic title="快速会话" value={metrics.total} /></Card></Col><Col span={6}><Card><Statistic title="排队/计算" value={metrics.running} valueStyle={{ color: "#1677ff" }} /></Card></Col><Col span={6}><Card><Statistic title="已完成" value={metrics.success} valueStyle={{ color: "#3f8600" }} /></Card></Col><Col span={6}><Card><Statistic title="失败" value={metrics.failed} valueStyle={{ color: metrics.failed ? "#cf1322" : undefined }} /></Card></Col></Row>
    <Card title={<Space><CloudServerOutlined />选择受控服务器目录</Space>} className="quick-source-card" extra={<Button type="primary" icon={<PlayCircleOutlined />} disabled={!selectedRoot?.available || !directories.data} loading={createMutation.isPending} onClick={() => createMutation.mutate()}>用当前目录计算 PAT</Button>}>
      {roots.isError ? <Alert type="error" showIcon message="数据源加载失败" description={roots.error.message} /> : !roots.isLoading && !roots.data?.length ? <Empty description="尚未配置快速分析数据源，请管理员设置 TMS_SOURCE_ROOTS_JSON。" /> : <>
        <Space wrap className="quick-source-toolbar">
          <Typography.Text strong>数据源</Typography.Text>
          <Select value={rootCode} loading={roots.isLoading} style={{ minWidth: 240 }} options={(roots.data ?? []).map((item) => ({ value: item.code, label: `${item.name}${item.available ? "" : "（不可用）"}`, disabled: !item.available }))} onChange={(value) => { setRootCode(value); setRelativePath("."); }} />
          <Button icon={<LeftOutlined />} disabled={!directories.data?.parent_relative_path} onClick={() => directories.data?.parent_relative_path != null && setRelativePath(directories.data.parent_relative_path)}>上一级</Button>
          <Typography.Text code>{directories.data?.current_relative_path ?? relativePath}</Typography.Text>
        </Space>
        {directories.isError && <Alert type="error" showIcon message="目录读取失败" description={directories.error.message} />}
        <Table rowKey="relative_path" size="small" loading={directories.isLoading} columns={directoryColumns} dataSource={directories.data?.directories ?? []} pagination={false} locale={{ emptyText: "当前目录没有子目录，可直接点击右上角开始计算。" }} />
      </>}
    </Card>
    <Card title="快速分析记录" className="production-table-card quick-session-card">
      {sessions.isError && <Alert type="error" showIcon message="快速分析记录加载失败" description={sessions.error.message} />}
      <Table rowKey="analysis_session_id" columns={sessionColumns} dataSource={sessions.data ?? []} loading={sessions.isLoading} scroll={{ x: 1900 }} pagination={{ pageSize: 20, showSizeChanger: true }} />
    </Card>
  </div>;
}
