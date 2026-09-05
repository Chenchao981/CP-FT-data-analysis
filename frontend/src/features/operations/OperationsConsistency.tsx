import { CleanerCapabilityCatalog } from "./CleanerCapabilityCatalog";
import { PauseCircleOutlined, PlayCircleOutlined, ReloadOutlined } from "@ant-design/icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, Button, Card, Descriptions, Empty, Popconfirm, Space, Spin, Table, Tag, Typography, message } from "antd";
import type { ColumnsType } from "antd/es/table";

import { drainWorker, getOperationsConsistency, getWorkerFleetHealth, OperationalStatusCount, RecentFailedJob, resumeWorker, type WorkerHealth } from "../../api/operations";
import { formatUtcDateTime } from "../../utils/dateTime";
import { MetricStrip } from "../../components/MetricStrip";
import { useAuth } from "../auth/AuthContext";

const jobStatusName: Record<string, string> = {
  QUEUED: "排队中",
  RUNNING: "运行中",
  NEEDS_INPUT: "待补录",
  SUCCESS: "成功",
  FAILED: "失败",
  CANCELLED: "已取消",
};

const intentStatusName: Record<string, string> = {
  STAGED: "待发布",
  FINALIZED: "已发布",
  ABORTED: "已终止",
};

const statusColor: Record<string, string> = {
  QUEUED: "gold",
  RUNNING: "processing",
  NEEDS_INPUT: "orange",
  SUCCESS: "success",
  FAILED: "error",
  CANCELLED: "default",
  STAGED: "processing",
  FINALIZED: "success",
  ABORTED: "default",
};

const scopeName: Record<string, string> = {
  ENGINEERING: "工程",
  PRODUCTION: "量产",
};

function StatusCounts({ counts, names }: { counts: OperationalStatusCount[]; names: Record<string, string> }) {
  return <Space size={[8, 10]} wrap>
    {counts.map((item) => (
      <Tag key={item.status} color={statusColor[item.status]}>
        {names[item.status] ?? item.status}：{item.count}
      </Tag>
    ))}
  </Space>;
}

const issueValue = (value: number | null) => value == null ? "待 0015 升级" : value;

export function OperationsConsistency() {
  const { can } = useAuth();
  const canOperateWorkers = can("SYSTEM_OPERATE");
  const queryClient = useQueryClient();
  const [messageApi, messageContext] = message.useMessage();
  const summary = useQuery({
    queryKey: ["operations", "consistency"],
    queryFn: () => getOperationsConsistency(5),
  });
  const workerFleet = useQuery({
    queryKey: ["operations", "workers", 90],
    queryFn: () => getWorkerFleetHealth(90),
  });
  const workerControl = useMutation({
    mutationFn: ({ workerId, action }: { workerId: string; action: "DRAIN" | "RESUME" }) => action === "DRAIN" ? drainWorker(workerId) : resumeWorker(workerId),
    onSuccess: async (result) => {
      messageApi.success(`Worker ${result.worker_id} 期望状态已更新为 ${result.desired_state}`);
      await queryClient.invalidateQueries({ queryKey: ["operations", "workers"] });
    },
    onError: () => messageApi.error("Worker 控制请求失败；本页不展示底层连接详情。"),
  });
  const data = summary.data;
  const schemaUpgradeRequired = data?.overall_state === "SCHEMA_UPGRADE_REQUIRED";
  const attentionRequired = data?.overall_state === "ATTENTION_REQUIRED";
  const issueTotal = data
    ? [data.issue_counts.batch_job_intent, data.issue_counts.dataset_current]
      .filter((value): value is number => value != null)
      .reduce((total, value) => total + value, 0)
    : 0;
  const failureColumns: ColumnsType<RecentFailedJob> = [
    { title: "Job", dataIndex: "job_id", width: 90, render: (value) => `#${value}` },
    { title: "任务类型", dataIndex: "job_type", width: 150 },
    { title: "入库批次", dataIndex: "import_batch_id", width: 110, render: (value) => value == null ? "—" : `#${value}` },
    {
      title: "业务范围",
      key: "scope",
      width: 120,
      render: (_, row) => [row.business_domain ? (scopeName[row.business_domain] ?? row.business_domain) : null, row.test_stage]
        .filter(Boolean)
        .join(" / ") || "—",
    },
    { title: "错误分类", dataIndex: "error_code", width: 210, render: (value) => <Tag color="error">{value}</Tag> },
    { title: "尝试次数", dataIndex: "attempt_count", width: 100 },
    { title: "失败时间", dataIndex: "failed_at_utc", width: 180, render: formatUtcDateTime },
  ];
  const workerColumns: ColumnsType<WorkerHealth> = [
    { title: "Worker", dataIndex: "worker_id", width: 250, fixed: "left", render: (value) => <Typography.Text code>{value}</Typography.Text> },
    { title: "类型", dataIndex: "worker_kind", width: 110 },
    { title: "心跳状态", dataIndex: "state", width: 120, render: (value, row) => <Space><Tag color={row.is_stale ? "error" : value === "READY" ? "success" : value === "DRAINING" ? "warning" : "default"}>{value}</Tag>{row.is_stale && <Tag color="error">STALE</Tag>}</Space> },
    { title: "期望状态", dataIndex: "desired_state", width: 110, render: (value) => <Tag color={value === "RUN" ? "blue" : "orange"}>{value}</Tag> },
    { title: "启动时间", dataIndex: "started_at_utc", width: 180, render: formatUtcDateTime },
    { title: "最后心跳", dataIndex: "last_seen_at_utc", width: 180, render: formatUtcDateTime },
    { title: "停止时间", dataIndex: "stopped_at_utc", width: 180, render: formatUtcDateTime },
    { title: "数据库", dataIndex: "database_name", width: 150, render: (value) => value || "—" },
    { title: "Schema", dataIndex: "schema_revision", width: 155, render: (value) => value || "—" },
    ...(canOperateWorkers ? [{
      title: "Worker 操作",
      key: "actions",
      width: 170,
      fixed: "right" as const,
      render: (_: unknown, row: WorkerHealth) => row.desired_state === "RUN" ? <Popconfirm title="请求 Worker 安全 Drain？" description="Worker 将不再领取新任务，已领取任务仍按后端协议处理。" onConfirm={() => workerControl.mutate({ workerId: row.worker_id, action: "DRAIN" })}><Button type="link" danger size="small" icon={<PauseCircleOutlined />} loading={workerControl.isPending && workerControl.variables?.workerId === row.worker_id}>Drain</Button></Popconfirm> : <Popconfirm title="请求 Worker Resume？" description="恢复后是否 READY 仍以后端心跳为准。" onConfirm={() => workerControl.mutate({ workerId: row.worker_id, action: "RESUME" })}><Button type="link" size="small" icon={<PlayCircleOutlined />} loading={workerControl.isPending && workerControl.variables?.workerId === row.worker_id}>Resume</Button></Popconfirm>,
    }] : []),
  ];
  const fleet = workerFleet.data;

  return <div className="workbench production-workbench">
    {messageContext}
    <div className="page-heading">
      <Typography.Title level={2}>运行一致性</Typography.Title>
      <Button icon={<ReloadOutlined />} loading={summary.isFetching} onClick={() => void summary.refetch()}>刷新摘要</Button>
    </div>

    {summary.isLoading ? <div className="page-loading"><Spin size="large" /></div> : summary.isError ? (
      <Alert showIcon type="error" message="运行一致性摘要加载失败" />
    ) : data ? <>
      <Alert
        className="quick-analysis-alert"
        showIcon
        type={schemaUpgradeRequired || attentionRequired ? "warning" : "success"}
        message={schemaUpgradeRequired
          ? "数据库结构升级未完成（SCHEMA_UPGRADE_REQUIRED）"
          : attentionRequired
            ? "发现运行一致性异常（ATTENTION_REQUIRED）"
            : "发布链路一致性正常（HEALTHY）"}
        description={schemaUpgradeRequired
          ? `需要先完成 0015 数据库升级后，才能执行原子发布一致性检查。${data.management_message}`
          : data.management_message}
      />

      <MetricStrip ariaLabel="发布链路状态" items={[
        { label: "数据库连接", value: data.database_ready ? "已连接" : "未连接", tone: data.database_ready ? "success" : "danger" },
        { label: "Schema", value: data.schema_revision || "未知" },
        { label: "原子发布结构", value: data.atomic_schema_ready ? "已就绪" : "需要升级", tone: data.atomic_schema_ready ? "success" : "warning" },
        { label: "当前原子入库", value: data.active_atomic_initial_import_count == null ? "待升级" : data.active_atomic_initial_import_count },
        { label: "入库链路异常", value: issueValue(data.issue_counts.batch_job_intent), tone: data.issue_counts.batch_job_intent ? "danger" : "default" },
        { label: "Current 异常", value: issueValue(data.issue_counts.dataset_current), tone: data.issue_counts.dataset_current ? "danger" : "default" },
        { label: "异常合计", value: schemaUpgradeRequired ? "待升级" : issueTotal, tone: issueTotal ? "danger" : "success" },
        { label: "UNKNOWN 单元", value: data.current_unknown_result_count, tone: data.current_unknown_result_count ? "warning" : "default" },
      ]} />

      <Card title="最近失败任务（脱敏）" extra={<Typography.Text type="secondary">快照时间：{formatUtcDateTime(data.observed_at_utc)}</Typography.Text>} className="production-table-card">
        <Table
          rowKey="job_id"
          size="small"
          columns={failureColumns}
          dataSource={data.recent_failed_jobs}
          pagination={false}
          scroll={{ x: 960 }}
          locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="最近没有失败任务" /> }}
        />
      </Card>
      <details className="operational-detail-panel">
        <summary>运行身份与状态分布</summary>
        <Descriptions bordered size="small" column={{ xs: 1, sm: 2, lg: 3 }}>
          <Descriptions.Item label="API">{data ? <Tag color="success">已响应</Tag> : "—"}</Descriptions.Item>
          <Descriptions.Item label="运行环境">{data.environment || "未提供"}</Descriptions.Item>
          <Descriptions.Item label="数据库名称">{data.database_name || "未提供"}</Descriptions.Item>
          <Descriptions.Item label="数据库服务身份">{data.database_server || "未提供"}</Descriptions.Item>
          <Descriptions.Item label="数据库连接">{data.database_ready ? "已连接" : "未连接"}</Descriptions.Item>
          <Descriptions.Item label="Schema Revision">{data.schema_revision || "未知"}</Descriptions.Item>
        </Descriptions>
        <div className="status-count-grid">
          <section><Typography.Text strong>Job 状态</Typography.Text><StatusCounts counts={data.job_status_counts} names={jobStatusName} /></section>
          <section><Typography.Text strong>原子发布 Intent 状态</Typography.Text>{data.intent_status_counts
            ? <StatusCounts counts={data.intent_status_counts} names={intentStatusName} />
            : <Typography.Text type="warning">待完成 0015 数据库升级后提供。</Typography.Text>}</section>
        </div>
      </details>
    </> : null}

    <div className="page-heading" style={{ marginTop: 28 }}>
      <Typography.Title level={3}>Worker 心跳与队列</Typography.Title>
      <Button icon={<ReloadOutlined />} loading={workerFleet.isFetching} onClick={() => void workerFleet.refetch()}>刷新 Worker</Button>
    </div>
    {workerFleet.isLoading ? <div className="page-loading"><Spin size="large" /></div> : workerFleet.isError ? (
      <Alert showIcon type="error" message="Worker 运维摘要加载失败" />
    ) : fleet ? <>
      {fleet.alert_codes.length > 0 && <Alert type="warning" showIcon message="Worker 运维告警" description={<Space wrap>{fleet.alert_codes.map((code) => <Tag color="warning" key={code}>{code}</Tag>)}</Space>} className="review-alert" />}
      <MetricStrip ariaLabel="Worker 与队列状态" items={[
        { label: "活动 Worker", value: fleet.active_worker_count },
        { label: "READY", value: fleet.ready_worker_count, tone: fleet.ready_worker_count ? "success" : "default" },
        { label: "DRAINING", value: fleet.draining_worker_count, tone: fleet.draining_worker_count ? "warning" : "default" },
        { label: "STALE", value: fleet.stale_worker_count, tone: fleet.stale_worker_count ? "danger" : "default" },
        { label: "FAILED", value: fleet.failed_worker_count, tone: fleet.failed_worker_count ? "danger" : "default" },
        { label: "排队 Job", value: fleet.queued_job_count, tone: fleet.queued_job_count ? "warning" : "default" },
        { label: "最早等待", value: fleet.oldest_queued_seconds == null ? "—" : `${fleet.oldest_queued_seconds} 秒` },
        { label: "最后心跳", value: formatUtcDateTime(fleet.last_heartbeat_at_utc) },
      ]} />
      <Space wrap style={{ marginBottom: 12 }}><Tag>快照：{formatUtcDateTime(fleet.observed_at_utc)}</Tag><Tag>超时阈值：{fleet.stale_after_seconds} 秒</Tag></Space>
      <Card className="production-table-card">
        <Table rowKey="worker_id" columns={workerColumns} dataSource={fleet.workers} pagination={false} scroll={{ x: canOperateWorkers ? 1600 : 1400 }} locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="无活动Worker" /> }} />
      </Card>
    </> : null}
    <CleanerCapabilityCatalog />
  </div>;
}
