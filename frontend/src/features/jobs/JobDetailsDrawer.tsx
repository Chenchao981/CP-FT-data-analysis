import { BranchesOutlined, ReloadOutlined } from "@ant-design/icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, Button, Descriptions, Drawer, Empty, Space, Table, Tag, Typography, message } from "antd";
import type { ColumnsType } from "antd/es/table";

import { getJobDetails, type JobDetails, type JobSafeSummary, type JobSourceLineage } from "../../api/jobs";
import { reprocessStageBatch } from "../../api/stageData";
import { formatUtcDateTime } from "../../utils/dateTime";

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
  PUBLISHED: "success",
  SUPERSEDED: "default",
};

const statusTag = (status: string | null | undefined) => status
  ? <Tag color={statusColor[status]}>{status}</Tag>
  : "—";
const queueAgeText = (seconds: number | null | undefined) => seconds == null ? "未提供" : `${seconds} 秒`;
const fileSizeText = (bytes: number | null | undefined) => {
  if (bytes == null) return "—";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
  return `${(bytes / 1024 ** 3).toFixed(1)} GB`;
};
const lineageBasis = (value: string | null | undefined) => {
  if (value === "WRITER_VERIFIED") return <Tag color="success">Writer 已验证</Tag>;
  if (value === "BATCH_RECEIPT_NOT_WRITER_VERIFIED") return <Tag color="warning">仅批次收件，Writer 未验证</Tag>;
  return value ? <Tag>{value}</Tag> : "—";
};
const jobTypeText = (job: Pick<JobSafeSummary, "job_type" | "lifecycle_action_type"> | null | undefined) => {
  if (job?.lifecycle_action_type === "REPROCESS_UPDATE") {
    return "显式重清洗（INITIAL_IMPORT 原子执行）";
  }
  return job?.job_type || "—";
};

export interface JobDetailsDrawerProps {
  jobId?: number;
  open: boolean;
  onClose: () => void;
  onSelectJob: (jobId: number) => void;
  onOpenAnalytics: (datasetId: number, versionNo: number) => void;
}

export function JobDetailsDrawer({ jobId, open, onClose, onSelectJob, onOpenAnalytics }: JobDetailsDrawerProps) {
  const [messageApi, contextHolder] = message.useMessage();
  const queryClient = useQueryClient();
  const detailsQuery = useQuery({
    queryKey: ["job-details", jobId],
    queryFn: () => getJobDetails(jobId!),
    enabled: open && jobId !== undefined,
    refetchInterval: ({ state }) => ["QUEUED", "RUNNING"].includes(state.data?.job?.status ?? "") ? 3000 : false,
  });
  const details = detailsQuery.data;
  const job = details?.job ?? {};
  const batch = details?.batch;
  const dataset = details?.dataset;
  const hasReprocessableBatch = Boolean(batch?.business_domain && batch?.test_stage && batch.import_batch_id != null);
  const hasDataset = Boolean(dataset?.dataset_id != null && dataset.version_no != null);
  const reprocessMutation = useMutation({
    mutationFn: async () => {
      if (!batch?.business_domain || !batch.test_stage || batch.import_batch_id == null) {
        throw new Error("当前任务没有可重新处理的入库批次");
      }
      return reprocessStageBatch(
        batch.business_domain,
        batch.test_stage,
        batch.import_batch_id,
      );
    },
    onSuccess: async (result) => {
      messageApi.success(`批次 ${result.import_batch_id} 已进入重新处理队列`);
      await Promise.all([
        detailsQuery.refetch(),
        queryClient.invalidateQueries({ queryKey: ["stage"] }),
        queryClient.invalidateQueries({ queryKey: ["datasets", "current"] }),
      ]);
    },
    onError: (error) => messageApi.error(error.message),
  });
  const relatedColumns: ColumnsType<JobSafeSummary> = [
    { title: "Job", dataIndex: "job_id", width: 90, render: (value) => value == null ? "—" : <Button type="link" onClick={() => onSelectJob(value)}>#{value}</Button> },
    { title: "类型", key: "job_type", width: 260, render: (_, row) => jobTypeText(row) },
    { title: "状态", dataIndex: "status", width: 110, render: statusTag },
    { title: "申请时间", dataIndex: "requested_at_utc", width: 175, render: formatUtcDateTime },
  ];
  const timelineColumns: ColumnsType<NonNullable<JobDetails["timeline"]>[number]> = [
    { title: "时间", dataIndex: "occurred_at_utc", width: 180, render: formatUtcDateTime },
    { title: "事件", dataIndex: "event_code", width: 210 },
    { title: "状态", dataIndex: "status", width: 120, render: statusTag },
  ];
  const sourceColumns: ColumnsType<JobSourceLineage> = [
    { title: "顺序", dataIndex: "ordinal_no", width: 70, render: (value) => value ?? "—" },
    { title: "Source File", dataIndex: "source_file_id", width: 115, render: (value) => value == null ? "—" : `#${value}` },
    { title: "原始文件名", dataIndex: "original_file_name", width: 230, ellipsis: true, render: (value) => value || "—" },
    { title: "文件大小", dataIndex: "file_size", width: 105, render: fileSizeText },
    { title: "SHA-256", dataIndex: "sha256", width: 210, render: (value) => value ? <Typography.Text code copyable ellipsis={{ tooltip: value }}>{value}</Typography.Text> : "—" },
    { title: "血缘依据", dataIndex: "lineage_basis", width: 230, render: lineageBasis },
  ];

  return <Drawer
    title={jobId ? `Job #${jobId} 详情` : "Job 详情"}
    open={open}
    width={760}
    onClose={onClose}
    destroyOnHidden
    extra={<Button icon={<ReloadOutlined />} loading={detailsQuery.isFetching} onClick={() => void detailsQuery.refetch()}>刷新</Button>}
  >
    {contextHolder}
    {detailsQuery.isLoading ? <Typography.Text type="secondary">正在读取安全任务摘要…</Typography.Text> : detailsQuery.isError ? (
      <Alert showIcon type="error" message="Job 详情加载失败" description={detailsQuery.error.message} />
    ) : details ? <Space direction="vertical" size={18} className="full-width">
      {job.status === "QUEUED" && (
        <Alert
          showIcon
          type="info"
          message="任务仍在队列中"
          description={`后端队列等待：${queueAgeText(job.queue_age_seconds)}。本页不会仅凭等待时长猜测 Worker 在线状态；具备 AUDIT_READ 权限的人员可在“运行一致性”查看 Worker 观测结果。`}
        />
      )}
      {job.error_code && (
        <Alert
          showIcon
          type="error"
          message={`错误分类：${job.error_code}`}
          description={job.error_message || "系统未提供可安全展示的错误说明。"}
        />
      )}
      <Descriptions bordered size="small" column={2} title="任务摘要">
        <Descriptions.Item label="状态">{statusTag(job.status)}</Descriptions.Item>
        <Descriptions.Item label="任务类型">{jobTypeText(job)}</Descriptions.Item>
        <Descriptions.Item label="生命周期动作">{job.lifecycle_action_type || "—"}</Descriptions.Item>
        <Descriptions.Item label="触发方式">{job.trigger_type || "—"}</Descriptions.Item>
        <Descriptions.Item label="尝试次数">{job.attempt_count ?? "—"} / {job.max_attempts ?? "—"}</Descriptions.Item>
        <Descriptions.Item label="队列等待">{queueAgeText(job.queue_age_seconds)}</Descriptions.Item>
        <Descriptions.Item label="申请时间">{formatUtcDateTime(job.requested_at_utc)}</Descriptions.Item>
        <Descriptions.Item label="开始时间">{formatUtcDateTime(job.started_at_utc)}</Descriptions.Item>
        <Descriptions.Item label="结束时间">{formatUtcDateTime(job.finished_at_utc)}</Descriptions.Item>
        <Descriptions.Item label="父 Job">{details.parent?.job_id != null ? <Space size={4}><Button type="link" icon={<BranchesOutlined />} onClick={() => onSelectJob(details.parent!.job_id!)}>#{details.parent.job_id}</Button><Typography.Text type="secondary">{jobTypeText(details.parent)}</Typography.Text></Space> : "—"}</Descriptions.Item>
      </Descriptions>

      <Descriptions bordered size="small" column={1} title="Cleaner Release">
        <Descriptions.Item label="Release">{details.release ? `${details.release.cleaner_code ?? "—"} ${details.release.cleaner_version ?? "—"}（#${details.release.cleaner_release_id ?? "—"}）` : "—"}</Descriptions.Item>
        <Descriptions.Item label="Release SHA-256">{details.release?.content_sha256 ? <Typography.Text code copyable>{details.release.content_sha256}</Typography.Text> : "—"}</Descriptions.Item>
      </Descriptions>

      <Descriptions bordered size="small" column={2} title="发布链">
        <Descriptions.Item label="Import Batch">{batch?.import_batch_id != null ? `#${batch.import_batch_id} · ${batch.status ?? "—"}` : "—"}</Descriptions.Item>
        <Descriptions.Item label="来源文件数">{batch?.source_file_count ?? "—"}</Descriptions.Item>
        <Descriptions.Item label="业务范围">{batch ? `${batch.business_domain ?? "—"} / ${batch.test_stage ?? "—"} / ${batch.factory_code ?? "—"}` : "—"}</Descriptions.Item>
        <Descriptions.Item label="Finalize Intent">{statusTag(details.intent?.status)}</Descriptions.Item>
        <Descriptions.Item label="Processing Run">{details.run ? `#${details.run.processing_run_id} · ${details.run.status}` : "—"}</Descriptions.Item>
        <Descriptions.Item label="Dataset Current">{hasDataset ? <Button type="link" onClick={() => onOpenAnalytics(dataset!.dataset_id!, dataset!.version_no!)}>Dataset #{dataset!.dataset_id} / V{dataset!.version_no}</Button> : "—"}</Descriptions.Item>
      </Descriptions>

      <div>
        <Typography.Title level={5}>来源血缘</Typography.Title>
        <Typography.Paragraph type="secondary">仅展示后端安全合约返回的文件身份与验证依据，不展示物理路径或 URI。</Typography.Paragraph>
        <Table
          rowKey={(row) => String(row.source_file_id ?? `${row.ordinal_no ?? "source"}-${row.original_file_name ?? "unknown"}`)}
          size="small"
          columns={sourceColumns}
          dataSource={details.sources ?? []}
          pagination={false}
          scroll={{ x: 960 }}
          locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前 Job 未提供来源血缘" /> }}
        />
      </div>

      <div>
        <Typography.Title level={5}>可执行动作</Typography.Title>
        <Space wrap>
          {details.actions?.length ? details.actions.map((action) => {
            const canReprocess = action.code === "REPROCESS" && hasReprocessableBatch;
            const canOpenDataset = ["OPEN_ANALYTICS", "VIEW_RESULT"].includes(action.code ?? "") && hasDataset;
            const supported = canReprocess || canOpenDataset;
            return <Button
              key={action.code ?? action.label ?? "unknown-action"}
              disabled={!action.enabled || !supported}
              loading={canReprocess && reprocessMutation.isPending}
              title={action.reason || (!supported ? "该动作尚未提供安全前端执行接口" : undefined)}
              onClick={() => {
                if (canReprocess) reprocessMutation.mutate();
                if (canOpenDataset) onOpenAnalytics(dataset!.dataset_id!, dataset!.version_no!);
              }}
            >{action.label || action.code || "未命名动作"}</Button>;
          }) : <Typography.Text type="secondary">当前没有可执行动作。</Typography.Text>}
        </Space>
      </div>

      <div>
        <Typography.Title level={5}>子 Job</Typography.Title>
        <Table rowKey={(row) => String(row.job_id ?? `${row.job_type ?? "job"}-${row.requested_at_utc ?? "unknown"}`)} size="small" columns={relatedColumns} dataSource={details.children ?? []} pagination={false} locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="没有子 Job" /> }} />
      </div>
      <div>
        <Typography.Title level={5}>状态历史</Typography.Title>
        <Table rowKey={(row) => `${row.occurred_at_utc}-${row.event_code}-${row.status}`} size="small" columns={timelineColumns} dataSource={details.timeline ?? []} pagination={false} locale={{ emptyText: "暂无状态历史" }} />
      </div>
    </Space> : null}
  </Drawer>;
}
