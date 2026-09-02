import {
  CloudServerOutlined,
  DownloadOutlined,
  FolderOpenOutlined,
  LaptopOutlined,
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
  Input,
  Modal,
  Row,
  Select,
  Space,
  Statistic,
  Table,
  Tabs,
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
  previewQuickSourceManifest,
  type QuickAnalysisSession,
  type QuickSourceDirectory,
} from "../../api/quickAnalysis";
import {
  formatUtcDateTime,
  recentShanghaiDayRange,
  shanghaiLocalInputToUtc,
} from "../../utils/dateTime";
import { DirectPathAnalysisPanel } from "./DirectPathAnalysisPanel";
import { TemporaryFtpPanel } from "./TemporaryFtpPanel";

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
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [sessionPage, setSessionPage] = useState(1);
  const [sessionPageSize, setSessionPageSize] = useState(20);
  const [sessionStatus, setSessionStatus] = useState<QuickAnalysisSession["status"]>();
  const [sessionRange, setSessionRange] = useState(() => recentShanghaiDayRange(30));
  const [downloadError, setDownloadError] = useState<string>();
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
  const manifest = useQuery({
    queryKey: ["quick-analysis", "manifest", rootCode, directories.data?.current_relative_path ?? relativePath],
    queryFn: () => previewQuickSourceManifest(rootCode!, directories.data?.current_relative_path ?? relativePath),
    enabled: Boolean(rootCode && directories.data),
  });
  const sessions = useQuery({
    queryKey: ["quick-analysis", "sessions", sessionPage, sessionPageSize, sessionStatus, sessionRange.from, sessionRange.to],
    queryFn: () => listQuickAnalysisSessions({
      page: sessionPage,
      page_size: sessionPageSize,
      status: sessionStatus,
      from_utc: shanghaiLocalInputToUtc(sessionRange.from),
      to_utc: shanghaiLocalInputToUtc(sessionRange.to),
    }),
    refetchInterval: (query) => (query.state.data?.items ?? []).some((item) => ["QUEUED", "RUNNING"].includes(item.status)) ? 3000 : false,
  });
  const createMutation = useMutation({
    mutationFn: () => createQuickPat(
      rootCode!,
      manifest.data!.relative_path,
      manifest.data!.mode,
      manifest.data!.sha,
    ),
    onSuccess: async (created) => {
      setConfirmOpen(false);
      setSessionPage(1);
      messageApi.success(`快速 PAT 会话 ${created.analysis_session_id} 已进入后台队列（任务 ${created.job_id}）`);
      await queryClient.invalidateQueries({ queryKey: ["quick-analysis", "sessions"] });
    },
    onError: (error) => messageApi.error(error.message),
  });
  const metrics = useMemo(() => ({
    total: sessions.data?.total ?? 0,
    running: (sessions.data?.items ?? []).filter((item) => ["QUEUED", "RUNNING"].includes(item.status)).length,
    success: (sessions.data?.items ?? []).filter((item) => item.status === "SUCCESS").length,
    failed: (sessions.data?.items ?? []).filter((item) => item.status === "FAILED").length,
  }), [sessions.data]);
  const downloadMutation = useMutation({
    mutationFn: (row: QuickAnalysisSession) => downloadQuickPat(row.analysis_session_id, row.result_file_name!),
    onMutate: () => setDownloadError(undefined),
    onError: (error) => setDownloadError(error.message),
  });

  const directoryColumns: ColumnsType<QuickSourceDirectory> = [
    { title: "目录", dataIndex: "name", render: (name, row) => <Button type="link" icon={<FolderOpenOutlined />} onClick={() => setRelativePath(row.relative_path)}>{name}</Button> },
    { title: "本层 CSV", dataIndex: "direct_file_count", width: 110, render: count },
    { title: "本层大小", dataIndex: "direct_total_bytes", width: 130, render: size },
  ];
  const sessionColumns: ColumnsType<QuickAnalysisSession> = [
    { title: "会话", dataIndex: "analysis_session_id", width: 85, fixed: "left" },
    { title: "权限范围", dataIndex: "access_scope", width: 140, render: (value, row) => value === "PERSONAL" ? <Tag color="cyan">个人</Tag> : <Tag color="blue">数据域 {row.data_domain_code ?? `#${row.data_domain_id}`}</Tag> },
    { title: "数据源", dataIndex: "source_root_code", width: 150, render: (value) => value === "LOCAL_AGENT" ? "本机 / 直连目录" : value },
    { title: "目录", dataIndex: "source_relative_path", width: 300, ellipsis: true },
    { title: "源文件", dataIndex: "source_file_count", width: 95, render: count },
    { title: "源数据量", dataIndex: "source_total_bytes", width: 115, render: size },
    { title: "状态", dataIndex: "status", width: 100, render: (value) => <Tag color={statusColor[value]}>{statusName[value] ?? value}</Tag> },
    { title: "参数", dataIndex: "parameter_count", width: 85, render: count },
    { title: "解析数据行", dataIndex: "record_count", width: 125, render: count },
    { title: "计算耗时", key: "elapsed", width: 105, render: (_, row) => row.summary?.elapsed_seconds == null ? "—" : `${row.summary.elapsed_seconds.toFixed(3)} 秒` },
    { title: "发起人", dataIndex: "owner_name", width: 100 },
    { title: "创建时间", dataIndex: "created_at_utc", width: 175, render: formatUtcDateTime },
    { title: "结果到期", dataIndex: "expires_at_utc", width: 175, render: formatUtcDateTime },
    { title: "错误", dataIndex: "error_message", width: 240, ellipsis: true, render: (value) => value || "—" },
    { title: "操作", key: "actions", width: 100, fixed: "right", render: (_, row) => row.status === "SUCCESS" && row.result_file_name ? <Button type="link" size="small" icon={<DownloadOutlined />} loading={downloadMutation.isPending && downloadMutation.variables?.analysis_session_id === row.analysis_session_id} onClick={() => downloadMutation.mutate(row)}>下载 PAT</Button> : "—" },
  ];
  const selectedRoot = roots.data?.find((item) => item.code === rootCode);

  return <div className="workbench quick-analysis-workbench">
    {contextHolder}
    <div className="page-heading">
      <div><Typography.Text type="secondary">快速计算 / 复用 CP 与 FT 个人工具</Typography.Text><Typography.Title level={2}>快速分析</Typography.Title><Typography.Text type="secondary">输入目录先预览，再选择工具计算；源文件无需通过浏览器逐个上传，快速结果不写入正式 Canonical 明细。</Typography.Text></div>
      <Button icon={<ReloadOutlined />} onClick={() => void Promise.all([roots.refetch(), directories.refetch(), manifest.refetch(), sessions.refetch()])}>刷新</Button>
    </div>
    <Alert className="quick-analysis-alert" showIcon type="info" message="当前可运行：CP / FT 原始目录 → 厂商工具 PAT Excel" description="FT 直接复用杰群、日月新、日月光、电基和集佳工具的统一低内存 PAT；CP 先调用华虹、积塔、立昂微或国宇的已发布 Cleaner，再由同一 CP 工具包执行 PAT。系统只保存结果，不写入正式 Canonical，默认 7 天后过期。" />
    <Row gutter={16} className="production-stats"><Col span={6}><Card><Statistic title="筛选结果" value={metrics.total} /></Card></Col><Col span={6}><Card><Statistic title="本页排队/计算" value={metrics.running} valueStyle={{ color: "#1677ff" }} /></Card></Col><Col span={6}><Card><Statistic title="本页已完成" value={metrics.success} valueStyle={{ color: "#3f8600" }} /></Card></Col><Col span={6}><Card><Statistic title="本页失败" value={metrics.failed} valueStyle={{ color: metrics.failed ? "#cf1322" : undefined }} /></Card></Col></Row>
    <Tabs
      defaultActiveKey="local"
      className="quick-source-tabs"
      items={[
        {
          key: "local",
          label: <Space><LaptopOutlined />本机 / NAS 路径</Space>,
          children: <DirectPathAnalysisPanel onCreated={() => queryClient.invalidateQueries({ queryKey: ["quick-analysis", "sessions"] })} />,
        },
        {
          key: "server",
          label: <Space><CloudServerOutlined />已配置服务器</Space>,
          children: <>
            <Alert type="info" showIcon message="后台已配置的数据源" description="适用于经常使用或定时拉取的 FTP、NAS、服务器挂载目录。用户无需重复输入地址和密码，可直接选择目录并后台计算。" style={{ marginBottom: 16 }} />
            <Card title={<Space><CloudServerOutlined />选择受控服务器目录</Space>} className="quick-source-card" extra={<Button type="primary" icon={<PlayCircleOutlined />} disabled={!selectedRoot?.available || !manifest.data} loading={manifest.isFetching || createMutation.isPending} onClick={() => setConfirmOpen(true)}>确认范围并计算 PAT</Button>}>
              {roots.isError ? <Alert type="error" showIcon message="数据源加载失败" description={roots.error.message} /> : !roots.isLoading && !roots.data?.length ? <Empty description="尚未配置快速分析数据源，请管理员设置 TMS_SOURCE_ROOTS_JSON。" /> : <>
                <Space wrap className="quick-source-toolbar">
                  <Typography.Text strong>数据源</Typography.Text>
                  <Select value={rootCode} loading={roots.isLoading} style={{ minWidth: 240 }} options={(roots.data ?? []).map((item) => ({ value: item.code, label: `${item.name}${item.available ? "" : "（不可用）"}`, disabled: !item.available }))} onChange={(value) => { setRootCode(value); setRelativePath("."); }} />
                  <Button icon={<LeftOutlined />} disabled={!directories.data?.parent_relative_path} onClick={() => directories.data?.parent_relative_path != null && setRelativePath(directories.data.parent_relative_path)}>上一级</Button>
                  <Typography.Text code>{directories.data?.current_relative_path ?? relativePath}</Typography.Text>
                </Space>
                {directories.isError && <Alert type="error" showIcon message="目录读取失败" description={directories.error.message} />}
                {manifest.isError && <Alert type="error" showIcon message="递归文件范围预览失败" description={manifest.error.message} />}
                <Table rowKey="relative_path" size="small" loading={directories.isLoading} columns={directoryColumns} dataSource={directories.data?.directories ?? []} pagination={false} locale={{ emptyText: "当前目录没有子目录，可直接点击右上角开始计算。" }} />
              </>}
            </Card>
          </>,
        },
        {
          key: "temporary-ftp",
          label: <Space><CloudServerOutlined />临时 FTP 预览</Space>,
          children: <TemporaryFtpPanel />,
        },
      ]}
    />
    <Card title="快速分析记录" className="production-table-card quick-session-card" extra={<Typography.Text type="secondary">个人结果仅本人；数据域结果仅当前有效成员</Typography.Text>}>
      <Space wrap style={{ marginBottom: 12 }}>
        <Typography.Text strong>状态</Typography.Text>
        <Select
          allowClear
          placeholder="全部状态"
          value={sessionStatus}
          style={{ width: 160 }}
          options={Object.entries(statusName).map(([value, label]) => ({ value, label }))}
          onChange={(value) => { setSessionStatus(value); setSessionPage(1); }}
        />
        <Typography.Text strong>创建时间（上海）</Typography.Text>
        <Input
          aria-label="快速分析开始时间"
          type="datetime-local"
          value={sessionRange.from}
          style={{ width: 190 }}
          onChange={(event) => { setSessionRange((current) => ({ ...current, from: event.target.value })); setSessionPage(1); }}
        />
        <Typography.Text>至</Typography.Text>
        <Input
          aria-label="快速分析结束时间"
          type="datetime-local"
          value={sessionRange.to}
          style={{ width: 190 }}
          onChange={(event) => { setSessionRange((current) => ({ ...current, to: event.target.value })); setSessionPage(1); }}
        />
        {[7, 30, 90].map((days) => (
          <Button
            key={days}
            size="small"
            onClick={() => { setSessionRange(recentShanghaiDayRange(days)); setSessionPage(1); }}
          >
            近 {days} 天
          </Button>
        ))}
      </Space>
      {sessions.isError && <Alert type="error" showIcon message="快速分析记录加载失败" description={sessions.error.message} />}
      {downloadError && <Alert type="error" showIcon message="PAT 下载失败" description={`${downloadError}。结果可能已过期或已清理，请刷新记录后重试。`} style={{ marginBottom: 12 }} />}
      <Table
        rowKey="analysis_session_id"
        columns={sessionColumns}
        dataSource={sessions.data?.items ?? []}
        loading={sessions.isLoading}
        scroll={{ x: 1900 }}
        pagination={{
          current: sessions.data?.page ?? sessionPage,
          pageSize: sessions.data?.page_size ?? sessionPageSize,
          total: sessions.data?.total ?? 0,
          showSizeChanger: true,
          pageSizeOptions: [10, 20, 50, 100],
          showTotal: (total) => `共 ${total} 个会话`,
        }}
        onChange={(pagination) => {
          setSessionPage(pagination.current ?? 1);
          setSessionPageSize(pagination.pageSize ?? 20);
        }}
      />
    </Card>
    <Modal
      title="确认快速 PAT 处理范围"
      open={confirmOpen}
      okText="确认并创建任务"
      cancelText="返回选择"
      confirmLoading={createMutation.isPending}
      onCancel={() => { if (!createMutation.isPending) setConfirmOpen(false); }}
      onOk={() => createMutation.mutate()}
    >
      {manifest.data ? <>
        <Alert type="info" showIcon message="系统将递归处理以下范围" description="实际创建任务时会重新构建同一规则的 Manifest；目录内容发生变化时会阻止提交并要求重新确认。" style={{ marginBottom: 16 }} />
        <Row gutter={[12, 12]}>
          <Col span={12}><Statistic title="源文件数" value={manifest.data.file_count} /></Col>
          <Col span={12}><Statistic title="源数据大小" value={size(manifest.data.total_bytes)} /></Col>
        </Row>
        <Typography.Paragraph><strong>相对目录：</strong><Typography.Text code>{manifest.data.relative_path}</Typography.Text></Typography.Paragraph>
        <Typography.Paragraph><strong>文件类型：</strong>{manifest.data.allowed_suffixes.join("、")}</Typography.Paragraph>
        <Typography.Paragraph><strong>计算工具：</strong>杰群低内存 PAT</Typography.Paragraph>
      </> : <Alert type="warning" showIcon message="尚未取得文件范围，请关闭后刷新目录" />}
    </Modal>
  </div>;
}
