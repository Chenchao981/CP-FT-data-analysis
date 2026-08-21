import { CheckCircleOutlined, SafetyCertificateOutlined } from "@ant-design/icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, Button, Card, Col, Descriptions, Form, InputNumber, Row, Space, Statistic, Tag, Typography, message } from "antd";
import { useState } from "react";

import { getDatasetGate, getDatasetSummary, publishDatasetVersion } from "../../api/datasets";

interface Selection { datasetId: number; versionNo: number }
interface ReviewForm { dataset_id: number; version_no: number; published_by: number }

export function DatasetReview() {
  const [selection, setSelection] = useState<Selection>();
  const [publisherId, setPublisherId] = useState<number>();
  const [messageApi, contextHolder] = message.useMessage();
  const queryClient = useQueryClient();
  const gateQuery = useQuery({
    queryKey: ["dataset-gate", selection],
    queryFn: () => getDatasetGate(selection!.datasetId, selection!.versionNo),
    enabled: Boolean(selection),
  });
  const summaryQuery = useQuery({
    queryKey: ["dataset-summary", selection],
    queryFn: () => getDatasetSummary(selection!.datasetId, selection!.versionNo),
    enabled: Boolean(selection),
  });
  const publishMutation = useMutation({
    mutationFn: () => publishDatasetVersion(selection!.datasetId, selection!.versionNo, publisherId!),
    onSuccess: async () => {
      messageApi.success("Dataset版本已发布");
      await queryClient.invalidateQueries({ queryKey: ["dataset-gate", selection] });
      await queryClient.invalidateQueries({ queryKey: ["dataset-summary", selection] });
    },
    onError: (error) => messageApi.error(error.message),
  });
  const gate = gateQuery.data;
  const summary = summaryQuery.data;

  return (
    <div className="workbench">
      {contextHolder}
      <div className="page-heading">
        <div>
          <Typography.Title level={2}>结果审核与发布</Typography.Title>
          <Typography.Text type="secondary">按Dataset版本复核血缘、质量门禁、Yield与Bin，再执行正式发布。</Typography.Text>
        </div>
        <Tag color={gate?.status === "PASS" ? "success" : "default"} icon={<SafetyCertificateOutlined />}>
          {gate ? `DQ ${gate.status}` : "等待复核"}
        </Tag>
      </div>

      <Card className="review-filter-card">
        <Form<ReviewForm>
          layout="inline"
          onFinish={(values) => {
            setSelection({ datasetId: values.dataset_id, versionNo: values.version_no });
            setPublisherId(values.published_by);
          }}
        >
          <Form.Item label="Dataset编号" name="dataset_id" rules={[{ required: true }]}>
            <InputNumber min={1} precision={0} />
          </Form.Item>
          <Form.Item label="版本" name="version_no" rules={[{ required: true }]}>
            <InputNumber min={1} precision={0} />
          </Form.Item>
          <Form.Item label="审核用户编号" name="published_by" rules={[{ required: true }]}>
            <InputNumber min={1} precision={0} />
          </Form.Item>
          <Button type="primary" htmlType="submit" loading={gateQuery.isFetching || summaryQuery.isFetching}>加载复核结果</Button>
        </Form>
      </Card>

      {(gateQuery.isError || summaryQuery.isError) && (
        <Alert className="review-alert" type="error" showIcon message="无法加载复核结果" description={(gateQuery.error || summaryQuery.error)?.message} />
      )}
      {gate?.status === "BLOCKED" && (
        <Alert
          className="review-alert"
          type="error"
          showIcon
          message="DQ门禁未通过，禁止发布"
          description={gate.reasons.map((reason) => `${reason.code}（${reason.count}）：${reason.message}`).join("；")}
        />
      )}

      {summary && gate && (
        <Space direction="vertical" size={20} className="full-width review-results">
          <Row gutter={[16, 16]}>
            <Col xs={12} lg={4}><Card><Statistic title="Lot" value={summary.lot_count} /></Card></Col>
            <Col xs={12} lg={4}><Card><Statistic title="Wafer" value={summary.wafer_count} /></Card></Col>
            <Col xs={12} lg={4}><Card><Statistic title="Die" value={summary.unit_count} /></Card></Col>
            <Col xs={12} lg={4}><Card><Statistic title="Pass Die" value={summary.pass_count} /></Card></Col>
            <Col xs={12} lg={4}><Card><Statistic title="Fail Die" value={summary.fail_count} /></Card></Col>
            <Col xs={12} lg={4}><Card><Statistic title="Yield" value={summary.yield_rate * 100} precision={3} suffix="%" /></Card></Col>
          </Row>
          <Row gutter={[20, 20]}>
            <Col xs={24} xl={14}>
              <Card title="版本信息" className="panel-card review-panel">
                <Descriptions bordered size="small" column={1}>
                  <Descriptions.Item label="Dataset">{summary.dataset_code} · {summary.dataset_name}</Descriptions.Item>
                  <Descriptions.Item label="版本">V{summary.version_no}</Descriptions.Item>
                  <Descriptions.Item label="状态"><Tag color={summary.is_current ? "success" : "default"}>{summary.version_status}</Tag></Descriptions.Item>
                  <Descriptions.Item label="Processing Run">{summary.run_count}</Descriptions.Item>
                  <Descriptions.Item label="Measurement">{summary.measurement_count.toLocaleString()}</Descriptions.Item>
                  <Descriptions.Item label="Bin分布">
                    <Space wrap>{Object.entries(summary.bin_counts).map(([bin, count]) => <Tag key={bin}>Bin {bin}: {count}</Tag>)}</Space>
                  </Descriptions.Item>
                </Descriptions>
              </Card>
            </Col>
            <Col xs={24} xl={10}>
              <Card title="发布判定" className="panel-card review-panel">
                <Space direction="vertical" size={18} className="full-width">
                  <Alert type={gate.status === "PASS" ? "success" : "error"} showIcon message={`DQ Gate：${gate.status}`} description={`已核对 ${gate.run_count} 个Run、${gate.unit_count} 个Die及${gate.measurement_count}条测量。`} />
                  <Button
                    type="primary"
                    block
                    icon={<CheckCircleOutlined />}
                    disabled={gate.status !== "PASS" || summary.version_status === "PUBLISHED" || !publisherId}
                    loading={publishMutation.isPending}
                    onClick={() => publishMutation.mutate()}
                  >
                    {summary.version_status === "PUBLISHED" ? "当前版本已发布" : "发布当前版本"}
                  </Button>
                </Space>
              </Card>
            </Col>
          </Row>
        </Space>
      )}
    </div>
  );
}
