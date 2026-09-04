import {
  CloudServerOutlined,
  DownloadOutlined,
  FolderOpenOutlined,
  LaptopOutlined,
  LeftOutlined,
  LineChartOutlined,
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
import { MetricStrip } from "../../components/MetricStrip";
import {
  formatUtcDateTime,
  recentShanghaiDayRange,
  shanghaiLocalInputToUtc,
} from "../../utils/dateTime";
import { DirectPathAnalysisPanel } from "./DirectPathAnalysisPanel";
import { LocalQuickAnalysisPanel } from "./LocalQuickAnalysisPanel";
import { PatResultView } from "../analytics/PatResultView";

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
const analysisName: Record<QuickAnalysisSession["analysis_type"], string> = {
  QUICK_PAT: "PAT 参数分析",
  QUICK_CLEAN: "数据清洗",
  QUICK_CHART: "图表分析",
  QUICK_SYL_SBL: "SBL/SYL",
};

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
    { title: "功能", dataIndex: "analysis_type", width: 130, render: (value) => analysisName[value as QuickAnalysisSession["analysis_type"]] ?? value },
    { title: "数据源", dataIndex: "source_root_code", width: 150, render: (value) => value === "LOCAL_AGENT" ? "本机 / 直连目录" : value },
    { title: "目录", dataIndex: "source_relative_path", width: 300, ellipsis: true },
    { title: "源文件", dataIndex: "source_file_count", width: 95, render: count },
    { title: "源数据量", dataIndex: "source_total_bytes", width: 115, render: size },
    { title: "状态", dataIndex: "status", width: 100, render: (value) => <Tag color={statusColor[value]}>{statusName[value] ?? value}</Tag> },
    { title: "结果项", dataIndex: "parameter_count", width: 85, render: count },
    { title: "解析数据行", dataIndex: "record_count", width: 125, render: count },
    { title: "计算耗时", key: "elapsed", width: 105, render: (_, row) => row.summary?.elapsed_seconds == null ? "—" : `${row.summary.elapsed_seconds.toFixed(3)} 秒` },
    { title: "指定输出", key: "exported_result_path", width: 300, ellipsis: true, render: (_, row) => row.summary?.exported_result_path || "—" },
    { title: "发起人", dataIndex: "owner_name", width: 100 },
    { title: "创建时间", dataIndex: "created_at_utc", width: 175, render: formatUtcDateTime },
    { title: "结果保存", dataIndex: "retention_mode", width: 120, render: () => <Tag color="green">服务器历史</Tag> },
    { title: "错误", dataIndex: "error_message", width: 240, ellipsis: true, render: (value) => value || "—" },
    { title: "操作", key: "actions", width: 100, fixed: "right", render: (_, row) => row.status === "SUCCESS" && row.result_file_name ? <Button type="link" size="small" icon={<DownloadOutlined />} loading={downloadMutation.isPending && downloadMutation.variables?.analysis_session_id === row.analysis_session_id} onClick={() => downloadMutation.mutate(row)}>下载结果</Button> : "—" },
  ];
  const selectedRoot = roots.data?.find((item) => item.code === rootCode);

  const vdmosToolUrl = "/personal-tools/vdmos/VDMOS_Tool_v8.9.html";

  return <div className="workbench quick-analysis-workbench">
    {contextHolder}
    <div className="page-heading">
      <Typography.Title level={2}>个人分析工具</Typography.Title>
      <Button icon={<ReloadOutlined />} onClick={() => void Promise.all([roots.refetch(), directories.refetch(), manifest.refetch(), sessions.refetch()])}>刷新</Button>
    </div>
    <Tabs
      defaultActiveKey="local"
      className="quick-source-tabs"
      items={[
        {
          key: "local",
          label: <Space><LaptopOutlined />同机 / 共享路径</Space>,
          children: <DirectPathAnalysisPanel onCreated={() => queryClient.invalidateQueries({ queryKey: ["quick-analysis", "sessions"] })} />,
        },
        {
          key: "local-agent",
          label: <Space><LaptopOutlined />个人电脑（Agent）</Space>,
          children: <LocalQuickAnalysisPanel onRegistered={() => queryClient.invalidateQueries({ queryKey: ["quick-analysis", "sessions"] })} />,
        },
        {
          key: "vdmos",
          label: <Space><LineChartOutlined />VDMOS 个人工具</Space>,
          children: <Card className="quick-source-card" title={<Space><LineChartOutlined />VDMOS 综合分析工具</Space>}>
            <Button type="primary" icon={<LineChartOutlined />} href={vdmosToolUrl} target="_blank" rel="noreferrer">打开 VDMOS 个人工具</Button>
          </Card>,
        },
        {
          key: "server",
          label: <Space><CloudServerOutlined />已配置服务器</Space>,
          children: <Card title={<Space><CloudServerOutlined />选择服务器目录</Space>} className="quick-source-card" extra={<Button type="primary" icon={<PlayCircleOutlined />} disabled={!selectedRoot?.available || !manifest.data} loading={manifest.isFetching || createMutation.isPending} onClick={() => setConfirmOpen(true)}>确认范围并计算 PAT</Button>}>
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
          </Card>,
        },
      ]}
    />
    <MetricStrip ariaLabel="个人工具任务状态" items={[
      { label: "筛选结果", value: metrics.total },
      { label: "排队 / 计算", value: metrics.running, tone: "primary" },
      { label: "已完成", value: metrics.success, tone: "success" },
      { label: "失败", value: metrics.failed, tone: metrics.failed ? "danger" : "default" },
    ]} />
    <Card title="历史分析结果" className="production-table-card quick-session-card">
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
          aria-label="个人工具开始时间"
          type="datetime-local"
          value={sessionRange.from}
          style={{ width: 190 }}
          onChange={(event) => { setSessionRange((current) => ({ ...current, from: event.target.value })); setSessionPage(1); }}
        />
        <Typography.Text>至</Typography.Text>
        <Input
          aria-label="个人工具结束时间"
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
      {sessions.isError && <Alert type="error" showIcon message="个人工具任务记录加载失败" description={sessions.error.message} />}
      {downloadError && <Alert type="error" showIcon message="结果下载失败" description={`${downloadError}。请刷新记录后重试，如仍失败请联系系统管理员。`} style={{ marginBottom: 12 }} />}
      <Table
        rowKey="analysis_session_id"
        columns={sessionColumns}
        dataSource={sessions.data?.items ?? []}
        loading={sessions.isLoading}
        scroll={{ x: 2200 }}
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
        expandable={{
          rowExpandable: (row) => row.analysis_type === "QUICK_PAT" && row.status === "SUCCESS" && Boolean(row.summary?.parameters?.length),
          expandedRowRender: (row) => <PatResultView
            title={`${row.test_stage} · ${row.factory_code} · PAT分析结果`}
            labelTitle="测试参数"
            scope="PERSONAL"
            rows={(row.summary?.parameters ?? []).map((item) => ({
              key: item.parameter,
              label: item.parameter,
              count: item.count,
              q1: item.q1,
              median: item.median,
              q3: item.q3,
              lowerLimit: item.lcl_after ?? item.lcl_calculated,
              upperLimit: item.ucl_after ?? item.ucl_calculated,
              status: item.updated === true || item.updated === "YES" ? "UPDATED" : "OK",
            }))}
          />,
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
