import { CheckCircleOutlined, FormOutlined, InfoCircleOutlined } from "@ant-design/icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, Button, Card, Col, Form, Input, InputNumber, Radio, Row, Select, Space, Table, Tag, Typography, message } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useMemo, useState } from "react";

import {
  createFieldEnrichment,
  getBatchEnrichments,
  getEnrichmentFields,
  type CreateEnrichmentPayload,
  type EnrichmentAction,
  type EnrichmentStage,
  type FieldEnrichmentRecord,
} from "../../api/enrichments";

interface Props { stage: EnrichmentStage }
interface LoadBatchForm { import_batch_id: number }
interface EnrichmentForm {
  source_file_id?: number;
  field_code: string;
  action: EnrichmentAction;
  value_text?: string;
  reason: string;
}

const STAGE_COPY = {
  CP: {
    title: "CP数据接入与人工补录",
    description: "调用既有CP Cleaner后，以晶圆厂、Lot、Wafer、参数、测试条件和数值为分析主线。",
    sourceFacts: "Lot、Wafer、坐标、Bin、参数、测试条件和数值应优先来自CP Cleaner。",
  },
  FT: {
    title: "FT数据接入与人工补录",
    description: "调用既有FT Cleaner后，以Product、测试条件、参数和测试数值为分析主线。",
    sourceFacts: "Product、Unit、PASS/FAIL、Bin、参数、测试条件和数值应优先来自FT Cleaner。",
  },
} as const;

export function StageIntakeWorkbench({ stage }: Props) {
  const [batchId, setBatchId] = useState<number>();
  const [form] = Form.useForm<EnrichmentForm>();
  const action = Form.useWatch("action", form) ?? "FILL";
  const selectedField = Form.useWatch("field_code", form);
  const [messageApi, contextHolder] = message.useMessage();
  const queryClient = useQueryClient();
  const catalogQuery = useQuery({
    queryKey: ["enrichment-fields", stage],
    queryFn: () => getEnrichmentFields(stage),
  });
  const recordsQuery = useQuery({
    queryKey: ["batch-enrichments", batchId],
    queryFn: () => getBatchEnrichments(batchId!),
    enabled: Boolean(batchId),
  });
  const mutation = useMutation({
    mutationFn: (payload: CreateEnrichmentPayload) => createFieldEnrichment(payload),
    onSuccess: async () => {
      messageApi.success("人工补录决定已保存");
      form.resetFields(["source_file_id", "field_code", "value_text", "reason"]);
      form.setFieldValue("action", "FILL");
      await queryClient.invalidateQueries({ queryKey: ["batch-enrichments", batchId] });
    },
    onError: (error) => messageApi.error(error.message),
  });
  const fields = catalogQuery.data ?? [];
  const fieldLabels = useMemo(
    () => Object.fromEntries(fields.map((item) => [item.field_code, item.label])),
    [fields],
  );
  const selectedDefinition = fields.find((item) => item.field_code === selectedField);
  const columns: ColumnsType<FieldEnrichmentRecord> = [
    { title: "范围", key: "scope", render: (_, row) => row.source_file_id ? `源文件 ${row.source_file_id}` : "整个导入批次" },
    { title: "字段", dataIndex: "field_code", render: (value: string) => fieldLabels[value] ?? value },
    { title: "决定", dataIndex: "action", render: (value: EnrichmentAction) => <Tag color={value === "FILL" ? "blue" : "default"}>{value === "FILL" ? "人工填写" : "明确忽略"}</Tag> },
    { title: "值", dataIndex: "value_text", render: (value: string | null) => value ?? "—" },
    { title: "说明", dataIndex: "reason", ellipsis: true },
    { title: "操作人", dataIndex: "entered_by", width: 90 },
  ];

  return (
    <div className="workbench intake-workbench">
      {contextHolder}
      <div className="page-heading">
        <div>
          <Typography.Title level={2}>{STAGE_COPY[stage].title}</Typography.Title>
          <Typography.Text type="secondary">{STAGE_COPY[stage].description}</Typography.Text>
        </div>
        <Tag color={stage === "CP" ? "cyan" : "purple"}>{stage}独立流程</Tag>
      </div>

      <Alert showIcon icon={<InfoCircleOutlined />} type="info" message="源字段与人工字段分开" description={`${STAGE_COPY[stage].sourceFacts} 源文件没有但分析需要的业务字段在这里补录；不需要的字段可以明确忽略。`} />

      <Row gutter={[20, 20]} className="intake-grid">
        <Col xs={24} xl={8}>
          <Card title="1. 选择导入批次" className="intake-card">
            <Form<LoadBatchForm> layout="vertical" onFinish={(values) => setBatchId(values.import_batch_id)}>
              <Form.Item label="Import Batch编号" name="import_batch_id" rules={[{ required: true }]}>
                <InputNumber min={1} precision={0} className="full-width" placeholder="输入已登记的导入批次编号" />
              </Form.Item>
              <Button type="primary" htmlType="submit" block loading={recordsQuery.isFetching}>加载批次</Button>
            </Form>
            <Typography.Paragraph type="secondary" className="intake-hint">CP和FT批次分别进入对应页面，不在补录阶段猜测数据类型。</Typography.Paragraph>
          </Card>
        </Col>

        <Col xs={24} xl={16}>
          <Card title="2. 新增补录或忽略决定" className="intake-card">
            {!batchId ? <Alert type="warning" showIcon message="请先加载Import Batch" /> : (
              <Form<EnrichmentForm>
                form={form}
                layout="vertical"
                initialValues={{ action: "FILL" }}
                onFinish={(values) => mutation.mutate({
                  ...values,
                  import_batch_id: batchId,
                  test_stage: stage,
                  value_text: values.action === "FILL" ? values.value_text : undefined,
                })}
              >
                <Row gutter={16}>
                  <Col xs={24} md={12}>
                    <Form.Item label="适用源文件编号（可选）" name="source_file_id">
                      <InputNumber min={1} precision={0} className="full-width" placeholder="留空表示整个导入批次" />
                    </Form.Item>
                  </Col>
                  <Col xs={24} md={12}>
                    <Form.Item label="业务字段" name="field_code" rules={[{ required: true }]}>
                      <Select
                        placeholder="选择本Stage允许的字段"
                        loading={catalogQuery.isFetching}
                        options={fields.map((item) => ({
                          value: item.field_code,
                          label: `${item.label}${item.required_for_analysis ? "（分析必需）" : "（可选）"}`,
                        }))}
                      />
                    </Form.Item>
                  </Col>
                </Row>
                {selectedDefinition && <Alert className="field-description" type={selectedDefinition.required_for_analysis ? "warning" : "info"} showIcon message={selectedDefinition.description} />}
                <Form.Item label="处理决定" name="action" rules={[{ required: true }]}>
                  <Radio.Group optionType="button" buttonStyle="solid" options={[{ label: "人工填写", value: "FILL" }, { label: "明确忽略", value: "IGNORE" }]} />
                </Form.Item>
                {action === "FILL" && (
                  <Form.Item label="补录值" name="value_text" rules={[{ required: true, message: "请输入补录值" }]}>
                    <Input maxLength={500} placeholder="输入经人工确认的业务值" />
                  </Form.Item>
                )}
                <Row gutter={16}>
                  <Col span={24}>
                    <Form.Item label="补录/忽略说明" name="reason" rules={[{ required: true }]}>
                      <Input maxLength={500} placeholder="说明数据来源或为什么不使用该字段" />
                    </Form.Item>
                  </Col>
                </Row>
                <Button type="primary" htmlType="submit" icon={<FormOutlined />} loading={mutation.isPending}>保存决定</Button>
              </Form>
            )}
          </Card>
        </Col>
      </Row>

      <Card title={<Space><CheckCircleOutlined />3. 当前有效补录</Space>} className="intake-record-card">
        {recordsQuery.isError && <Alert type="error" showIcon message="补录记录加载失败" description={recordsQuery.error.message} />}
        <Table rowKey="enrichment_id" columns={columns} dataSource={(recordsQuery.data ?? []).filter((item) => item.test_stage === stage)} loading={recordsQuery.isFetching} pagination={false} locale={{ emptyText: batchId ? `该批次尚无${stage}人工补录` : "请先加载Import Batch" }} />
      </Card>
    </div>
  );
}
