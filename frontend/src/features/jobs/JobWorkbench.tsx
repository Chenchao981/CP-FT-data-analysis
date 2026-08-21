import { CheckCircleOutlined, CloudUploadOutlined, DatabaseOutlined, SyncOutlined } from "@ant-design/icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, Button, Card, Col, Descriptions, Form, Input, InputNumber, Row, Space, Steps, Tag, Typography, message } from "antd";
import { useState } from "react";

import { createJob, getJob, transitionJob, type CreateJobPayload, type JobStatus } from "../../api/jobs";

const statusMap: Record<JobStatus, { text: string; color: string; step: number }> = {
  QUEUED: { text: "等待处理", color: "default", step: 0 },
  RUNNING: { text: "清洗处理中", color: "processing", step: 1 },
  SUCCESS: { text: "清洗完成", color: "success", step: 2 },
  FAILED: { text: "处理失败", color: "error", step: 1 },
  CANCELLED: { text: "已取消", color: "warning", step: 0 },
};

export function JobWorkbench() {
  const [jobId, setJobId] = useState<number>();
  const queryClient = useQueryClient();
  const [messageApi, contextHolder] = message.useMessage();
  const jobQuery = useQuery({
    queryKey: ["job", jobId],
    queryFn: () => getJob(jobId!),
    enabled: jobId !== undefined,
    refetchInterval: ({ state }) => state.data?.status === "RUNNING" ? 2500 : false,
  });
  const createMutation = useMutation({
    mutationFn: createJob,
    onSuccess: (job) => {
      setJobId(job.job_id);
      queryClient.setQueryData(["job", job.job_id], job);
      messageApi.success(`任务 #${job.job_id} 已创建`);
    },
    onError: (error) => messageApi.error(error.message),
  });
  const transitionMutation = useMutation({
    mutationFn: ({ id, status }: { id: number; status: JobStatus }) => transitionJob(id, status),
    onSuccess: (job) => queryClient.setQueryData(["job", job.job_id], job),
    onError: (error) => messageApi.error(error.message),
  });
  const job = jobQuery.data;
  const status = job ? statusMap[job.status] : undefined;

  return (
    <div className="workbench">
      {contextHolder}
      <div className="page-heading">
        <div>
          <Typography.Title level={2}>清洗任务工作台</Typography.Title>
          <Typography.Text type="secondary">统一承接 CP、FT 原始数据，保留来源、清洗版本和处理状态。</Typography.Text>
        </div>
        <Tag color="blue" icon={<DatabaseOutlined />}>SQL Server 2014</Tag>
      </div>

      <Row gutter={[20, 20]}>
        <Col xs={24} xl={9}>
          <Card title="新建清洗任务" className="panel-card">
            <Form<CreateJobPayload>
              layout="vertical"
              initialValues={{ requested_by: "当前用户", reason: "CP/FT 数据清洗" }}
              onFinish={(values) => createMutation.mutate(values)}
            >
              <Form.Item label="源文件编号" name="source_file_id" rules={[{ required: true, message: "请输入源文件编号" }]}>
                <InputNumber min={1} precision={0} placeholder="例如：1001" className="full-width" />
              </Form.Item>
              <Form.Item label="清洗器版本编号" name="cleaner_release_id" rules={[{ required: true, message: "请输入已批准的清洗器版本" }]}>
                <InputNumber min={1} precision={0} placeholder="例如：12" className="full-width" />
              </Form.Item>
              <Form.Item label="申请人" name="requested_by" rules={[{ required: true, whitespace: true }]}>
                <Input maxLength={128} />
              </Form.Item>
              <Form.Item label="处理说明" name="reason">
                <Input.TextArea maxLength={1000} rows={3} />
              </Form.Item>
              <Button type="primary" htmlType="submit" block icon={<CloudUploadOutlined />} loading={createMutation.isPending}>
                创建任务
              </Button>
            </Form>
          </Card>
        </Col>

        <Col xs={24} xl={15}>
          <Card title="当前任务" className="panel-card">
            {!job && !jobQuery.isLoading && (
              <div className="empty-state">
                <SyncOutlined />
                <Typography.Text type="secondary">创建任务后，这里显示可审计的处理进度。</Typography.Text>
              </div>
            )}
            {jobQuery.isError && <Alert type="error" showIcon message="无法读取任务" description={jobQuery.error.message} />}
            {job && status && (
              <Space direction="vertical" size={24} className="full-width">
                <div className="job-title">
                  <Typography.Title level={4}>任务 #{job.job_id}</Typography.Title>
                  <Tag color={status.color}>{status.text}</Tag>
                </div>
                <Steps
                  current={status.step}
                  status={job.status === "FAILED" ? "error" : job.status === "SUCCESS" ? "finish" : "process"}
                  items={[{ title: "已入队" }, { title: "清洗与校验" }, { title: "可查看结果" }]}
                />
                <Descriptions bordered size="small" column={1}>
                  <Descriptions.Item label="源文件">#{job.source_file_id}</Descriptions.Item>
                  <Descriptions.Item label="清洗器版本">#{job.cleaner_release_id}</Descriptions.Item>
                  <Descriptions.Item label="申请人">{job.requested_by}</Descriptions.Item>
                  <Descriptions.Item label="触发方式">{job.trigger_type}</Descriptions.Item>
                  <Descriptions.Item label="说明">{job.reason || "—"}</Descriptions.Item>
                </Descriptions>
                <Space wrap>
                  {job.status === "QUEUED" && (
                    <Button type="primary" onClick={() => transitionMutation.mutate({ id: job.job_id, status: "RUNNING" })}>开始处理</Button>
                  )}
                  {job.status === "RUNNING" && (
                    <Button type="primary" icon={<CheckCircleOutlined />} onClick={() => transitionMutation.mutate({ id: job.job_id, status: "SUCCESS" })}>标记完成</Button>
                  )}
                  {(job.status === "QUEUED" || job.status === "RUNNING") && (
                    <Button danger onClick={() => transitionMutation.mutate({ id: job.job_id, status: "CANCELLED" })}>取消任务</Button>
                  )}
                </Space>
              </Space>
            )}
          </Card>
        </Col>
      </Row>
    </div>
  );
}
