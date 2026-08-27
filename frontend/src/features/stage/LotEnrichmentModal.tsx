import { useMutation, useQuery } from "@tanstack/react-query";
import { Alert, Checkbox, Form, Input, Modal, Radio, Space, Spin, Typography } from "antd";
import { useEffect } from "react";

import {
  type BusinessDomain,
  getStageInputRequests,
  resolveStageInputRequests,
  type ResolveStageInputRequestsResult,
  type TestStage,
} from "../../api/stageData";

interface LotEnrichmentModalProps {
  open: boolean;
  businessDomain: BusinessDomain;
  testStage: TestStage;
  importBatchId?: number;
  onClose: () => void;
  onResolved: (result: ResolveStageInputRequestsResult) => void | Promise<void>;
}

interface LotEnrichmentFormValues {
  same_lot?: boolean;
  shared_lot?: string;
  lots?: Record<string, string>;
  confirmation_basis: string;
  reason_detail?: string;
}

const confirmationOptions = [
  { label: "从文件名确认", value: "FILE_NAME" },
  { label: "从生产或测试记录确认", value: "PRODUCTION_RECORD" },
  { label: "从厂家标签或邮件确认", value: "SUPPLIER_CONFIRMATION" },
  { label: "其他人工核实", value: "OTHER" },
] as const;

const confirmationReasons: Record<string, string> = {
  FILE_NAME: "根据源文件名人工确认 Lot",
  PRODUCTION_RECORD: "根据生产或测试记录人工确认 Lot",
  SUPPLIER_CONFIRMATION: "根据厂家标签或邮件人工确认 Lot",
  OTHER: "经人工核实确认 Lot",
};

export function LotEnrichmentModal({
  open,
  businessDomain,
  testStage,
  importBatchId,
  onClose,
  onResolved,
}: LotEnrichmentModalProps) {
  const [form] = Form.useForm<LotEnrichmentFormValues>();
  const sameLot = Form.useWatch("same_lot", form) ?? false;
  const confirmationBasis = Form.useWatch("confirmation_basis", form);
  const inputRequests = useQuery({
    queryKey: ["stage-input-requests", businessDomain, testStage, importBatchId],
    queryFn: () => getStageInputRequests(businessDomain, testStage, importBatchId!),
    enabled: open && importBatchId !== undefined,
  });
  const requests = inputRequests.data?.requests ?? [];
  const hasMultipleFiles = requests.length > 1;

  useEffect(() => {
    form.resetFields();
  }, [businessDomain, form, importBatchId, open, testStage]);

  const resolveMutation = useMutation({
    mutationFn: async (values: LotEnrichmentFormValues) => {
      if (importBatchId === undefined || !requests.length) throw new Error("当前没有需要补录批次号的文件");
      const resolutions = requests.map((request) => ({
        input_request_id: request.input_request_id,
        lot_id: (
          hasMultipleFiles && values.same_lot
            ? values.shared_lot
            : values.lots?.[String(request.input_request_id)]
        )!.trim(),
      }));
      const detail = values.reason_detail?.trim();
      const reason = `${confirmationReasons[values.confirmation_basis] ?? "经人工核实确认 Lot"}${detail ? `；${detail}` : ""}`;
      return resolveStageInputRequests(businessDomain, testStage, importBatchId, { resolutions, reason });
    },
    onSuccess: async (result) => {
      form.resetFields();
      await onResolved(result);
    },
  });

  return (
    <Modal
      title="补录批次号"
      open={open}
      width={720}
      okText="保存并重新处理"
      cancelText="暂不处理"
      confirmLoading={resolveMutation.isPending}
      okButtonProps={{ disabled: inputRequests.isLoading || !requests.length }}
      onOk={() => form.submit()}
      onCancel={() => !resolveMutation.isPending && onClose()}
      destroyOnHidden
    >
      <Space direction="vertical" size={16} className="full-width">
        <Alert
          showIcon
          type="info"
          message="补录不会修改原始文件"
          description="保存后系统会重新清洗，并按补录的 Lot 重新校验 Spec。"
        />
        {inputRequests.isLoading && <div className="page-loading"><Spin /></div>}
        {inputRequests.isError && <Alert showIcon type="error" message="无法读取待补录文件" description={inputRequests.error.message} />}
        {inputRequests.data && (
          <Form<LotEnrichmentFormValues>
            form={form}
            layout="vertical"
            initialValues={{ same_lot: false }}
            onFinish={(values) => resolveMutation.mutate(values)}
          >
            <Alert
              showIcon
              type="warning"
              message={inputRequests.data.prompt || "系统未能从源文件取得 Lot，请人工确认。"}
            />
            {hasMultipleFiles && (
              <Form.Item name="same_lot" valuePropName="checked" className="field-description">
                <Checkbox>这些文件属于同一个 Lot</Checkbox>
              </Form.Item>
            )}
            {hasMultipleFiles && sameLot ? (
              <Form.Item
                label="这些文件共同的 Lot"
                name="shared_lot"
                rules={[{ required: true, whitespace: true, message: "请输入共同的 Lot" }]}
              >
                <Input maxLength={128} placeholder="输入经确认的批次号" autoComplete="off" />
              </Form.Item>
            ) : requests.map((request) => (
              <Form.Item
                key={request.input_request_id}
                label={`${request.original_file_name} 的 Lot`}
                name={["lots", String(request.input_request_id)]}
                rules={[{ required: true, whitespace: true, message: `请输入 ${request.original_file_name} 的 Lot` }]}
              >
                <Input maxLength={128} placeholder="输入经确认的批次号" autoComplete="off" />
              </Form.Item>
            ))}
            <Form.Item label="确认依据" name="confirmation_basis" rules={[{ required: true, message: "请选择 Lot 的确认依据" }]}>
              <Radio.Group options={confirmationOptions.map((item) => ({ ...item }))} />
            </Form.Item>
            <Form.Item
              label="补充说明（可选）"
              name="reason_detail"
              rules={confirmationBasis === "OTHER" ? [{ required: true, whitespace: true, message: "请说明核实依据" }] : undefined}
            >
              <Input.TextArea rows={2} maxLength={300} placeholder="例如：与当班测试记录核对一致" />
            </Form.Item>
            {resolveMutation.isError && <Alert showIcon type="error" message="批次号保存失败" description={resolveMutation.error.message} />}
            <Typography.Text type="secondary">如多个文件实际属于不同 Lot，请逐项填写；系统不会自动把一个 Lot 套用到所有文件。</Typography.Text>
          </Form>
        )}
      </Space>
    </Modal>
  );
}
