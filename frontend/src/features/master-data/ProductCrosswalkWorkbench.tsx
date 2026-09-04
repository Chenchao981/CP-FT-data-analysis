import { CheckOutlined, CloseOutlined, FilterOutlined, ReloadOutlined } from "@ant-design/icons";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, Button, Card, Col, Form, Input, Modal, Row, Select, Space, Table, Tag, Typography, message } from "antd";
import type { ColumnsType, TablePaginationConfig } from "antd/es/table";
import { useEffect, useMemo, useState } from "react";

import {
  approveProductCrosswalk,
  listProductCrosswalks,
  rejectProductCrosswalk,
  type ProductCrosswalk,
  type ProductCrosswalkRequest,
  type ProductCrosswalkStatus,
} from "../../api/masterData";
import { formatUtcDateTime } from "../../utils/dateTime";
import { useAuth } from "../auth/AuthContext";

interface CrosswalkFilterValues {
  status?: ProductCrosswalkStatus;
  supplier_code?: string;
  test_stage?: "CP" | "FT";
  raw_product_code?: string;
}

interface DecisionFormValues {
  enterprise_key?: string;
  reason: string;
}

export interface ProductCrosswalkWorkbenchProps {
  searchParams: URLSearchParams;
  onSearchParamsChange: (params: URLSearchParams) => void;
}

const FILTER_KEYS = ["status", "supplier_code", "test_stage", "raw_product_code"] as const;
const statusName: Record<ProductCrosswalkStatus, string> = {
  PENDING: "待审批",
  APPROVED: "已审批",
  REJECTED: "已拒绝",
  RETIRED: "已停用",
};
const statusColor: Record<ProductCrosswalkStatus, string> = {
  PENDING: "processing",
  APPROVED: "success",
  REJECTED: "error",
  RETIRED: "default",
};
const positiveInt = (value: string | null, fallback: number) => {
  const parsed = Number(value);
  return Number.isInteger(parsed) && parsed > 0 ? parsed : fallback;
};

export function ProductCrosswalkWorkbench({ searchParams, onSearchParamsChange }: ProductCrosswalkWorkbenchProps) {
  const { can } = useAuth();
  const canGovern = can("RULE_GOVERN");
  const [filterForm] = Form.useForm<CrosswalkFilterValues>();
  const [decisionForm] = Form.useForm<DecisionFormValues>();
  const [decision, setDecision] = useState<{ kind: "APPROVE" | "REJECT"; row: ProductCrosswalk }>();
  const [submitting, setSubmitting] = useState(false);
  const [messageApi, messageContext] = message.useMessage();
  const [modalApi, modalContext] = Modal.useModal();
  const queryClient = useQueryClient();
  const searchKey = searchParams.toString();
  const request = useMemo<ProductCrosswalkRequest>(() => ({
    page: positiveInt(searchParams.get("page"), 1),
    page_size: Math.min(100, positiveInt(searchParams.get("page_size"), 20)),
    status: (searchParams.get("status") as ProductCrosswalkStatus) || undefined,
    supplier_code: searchParams.get("supplier_code") || undefined,
    test_stage: (searchParams.get("test_stage") as ProductCrosswalkRequest["test_stage"]) || undefined,
    raw_product_code: searchParams.get("raw_product_code") || undefined,
  }), [searchKey]);
  useEffect(() => {
    filterForm.resetFields();
    filterForm.setFieldsValue({
      status: request.status,
      supplier_code: request.supplier_code,
      test_stage: request.test_stage,
      raw_product_code: request.raw_product_code,
    });
  }, [filterForm, request]);
  const query = useQuery({
    queryKey: ["master-data", "product-crosswalks", request],
    queryFn: () => listProductCrosswalks(request),
  });

  const updateSearch = (values: CrosswalkFilterValues, page = 1, pageSize = request.page_size) => {
    const next = new URLSearchParams(searchParams);
    for (const key of FILTER_KEYS) next.delete(key);
    next.set("page", String(page));
    next.set("page_size", String(pageSize));
    for (const key of FILTER_KEYS) {
      const value = values[key]?.trim();
      if (value) next.set(key, value);
    }
    onSearchParamsChange(next);
  };
  const currentFilters = (): CrosswalkFilterValues => ({
    status: request.status,
    supplier_code: request.supplier_code,
    test_stage: request.test_stage,
    raw_product_code: request.raw_product_code,
  });
  const onPageChange = (pagination: TablePaginationConfig) => updateSearch(currentFilters(), pagination.current ?? 1, pagination.pageSize ?? request.page_size);
  const submitDecision = (values: DecisionFormValues) => {
    if (!decision) return;
    const isApproval = decision.kind === "APPROVE";
    modalApi.confirm({
      title: isApproval ? "确认审批 SAP-B1 产品映射？" : "确认拒绝该产品映射？",
      content: <Space direction="vertical">
        <Typography.Text>来源产品标识：{decision.row.tms_product_code}</Typography.Text>
        {isApproval && <Typography.Text>SAP-B1 物料编码：{values.enterprise_key}</Typography.Text>}
        <Typography.Text>决策原因：{values.reason}</Typography.Text>
      </Space>,
      okText: isApproval ? "确认审批" : "确认拒绝",
      okButtonProps: { danger: !isApproval },
      cancelText: "返回检查",
      onOk: async () => {
        setSubmitting(true);
        try {
          if (isApproval) {
            await approveProductCrosswalk(decision.row.crosswalk_id, {
              enterprise_system: "SAP_B1",
              enterprise_key: values.enterprise_key!.trim(),
              reason: values.reason.trim(),
            });
          } else {
            await rejectProductCrosswalk(decision.row.crosswalk_id, values.reason.trim());
          }
          messageApi.success(isApproval ? "产品映射已审批" : "产品映射已拒绝");
          setDecision(undefined);
          await queryClient.invalidateQueries({ queryKey: ["master-data", "product-crosswalks"] });
        } catch (error) {
          messageApi.error("主数据决策失败；为避免暴露后端细节，请记录操作时间并联系管理员。");
          throw error;
        } finally {
          setSubmitting(false);
        }
      },
    });
  };

  const columns: ColumnsType<ProductCrosswalk> = [
    { title: "ID", dataIndex: "crosswalk_id", width: 75, fixed: "left", render: (value) => `#${value}` },
    { title: "供应商", key: "supplier", width: 150, render: (_, row) => <><Typography.Text strong>{row.supplier_name}</Typography.Text><br/><Typography.Text type="secondary">{row.supplier_code}</Typography.Text></> },
    { title: "阶段", dataIndex: "test_stage", width: 75 },
    { title: "原始产品文本", dataIndex: "raw_product_code", width: 230, ellipsis: true },
    { title: "来源产品标识(TMS)", dataIndex: "tms_product_code", width: 240, ellipsis: true },
    {
      title: "SAP-B1物料编码(仅已审批)",
      dataIndex: "enterprise_key",
      width: 260,
      // 映射边界是数据合同：PENDING/REJECTED 只是来源产品身份，只有 APPROVED + ENTERPRISE_MAPPED 才展示 SAP-B1 物料。
      render: (value, row) => row.status === "APPROVED" && row.identity_class === "ENTERPRISE_MAPPED" && value
        ? <Space><Typography.Text strong>{value}</Typography.Text><Tag color="success">已审批企业映射</Tag></Space>
        : <Typography.Text type="warning">—（{statusName[row.status]}，不可视为企业映射）</Typography.Text>,
    },
    { title: "身份类别", dataIndex: "identity_class", width: 170 },
    { title: "状态", dataIndex: "status", width: 105, render: (value: ProductCrosswalkStatus) => <Tag color={statusColor[value]}>{statusName[value]}</Tag> },
    { title: "首次观测", dataIndex: "first_observed_at_utc", width: 180, render: formatUtcDateTime },
    { title: "最近观测", dataIndex: "last_observed_at_utc", width: 180, render: formatUtcDateTime },
    { title: "决策原因", dataIndex: "decision_reason", width: 220, ellipsis: true, render: (value) => value || "—" },
    {
      title: "治理操作",
      key: "actions",
      fixed: "right",
      width: 175,
      render: (_, row) => canGovern && row.status !== "RETIRED" ? <Space size={0}>
        <Button type="link" size="small" icon={<CheckOutlined />} onClick={() => setDecision({ kind: "APPROVE", row })}>审批</Button>
        <Button type="link" size="small" danger icon={<CloseOutlined />} onClick={() => setDecision({ kind: "REJECT", row })}>拒绝</Button>
      </Space> : "—",
    },
  ];

  return <div className="workbench production-workbench">
    {messageContext}{modalContext}
    <div className="page-heading">
      <Typography.Title level={2}>产品映射</Typography.Title>
      <Button icon={<ReloadOutlined />} loading={query.isFetching} onClick={() => void query.refetch()}>刷新</Button>
    </div>
    <Card className="review-filter-card">
      <Form<CrosswalkFilterValues> form={filterForm} layout="vertical" onFinish={(values) => updateSearch(values)}>
        <Row gutter={[12, 0]}>
          <Col xs={24} sm={12} lg={6}><Form.Item label="状态" name="status"><Select allowClear options={Object.entries(statusName).map(([value, label]) => ({ value, label }))} /></Form.Item></Col>
          <Col xs={24} sm={12} lg={6}><Form.Item label="供应商编码" name="supplier_code"><Input allowClear /></Form.Item></Col>
          <Col xs={24} sm={12} lg={4}><Form.Item label="阶段" name="test_stage"><Select allowClear options={[{ value: "CP", label: "CP" }, { value: "FT", label: "FT" }]} /></Form.Item></Col>
          <Col xs={24} sm={12} lg={8}><Form.Item label="原始产品标识" name="raw_product_code"><Input allowClear /></Form.Item></Col>
          <Col span={24}><Space><Button type="primary" htmlType="submit" icon={<FilterOutlined />}>检索</Button><Button onClick={() => { filterForm.resetFields(); updateSearch({}); }}>清空</Button></Space></Col>
        </Row>
      </Form>
    </Card>
    {query.isError && <Alert type="error" showIcon message="产品映射加载失败，请稍后刷新" className="review-alert" />}
    <Card className="production-table-card">
      <Table
        rowKey="crosswalk_id"
        columns={columns}
        dataSource={query.data?.items ?? []}
        loading={query.isLoading}
        scroll={{ x: 2200 }}
        pagination={{
          current: query.data?.page ?? request.page,
          pageSize: query.data?.page_size ?? request.page_size,
          total: query.data?.total ?? 0,
          showSizeChanger: true,
          pageSizeOptions: [10, 20, 50, 100],
          showTotal: (total) => `共 ${total} 条候选映射`,
        }}
        onChange={onPageChange}
      />
    </Card>
    <Modal
      title={decision?.kind === "APPROVE" ? "审批 SAP-B1 产品映射" : "拒绝产品映射"}
      open={Boolean(decision)}
      onCancel={() => !submitting && setDecision(undefined)}
      onOk={() => decisionForm.submit()}
      okText={decision?.kind === "APPROVE" ? "检查并确认" : "检查并拒绝"}
      okButtonProps={{ danger: decision?.kind === "REJECT" }}
      confirmLoading={submitting}
      destroyOnHidden
    >
      <Typography.Text strong>来源产品标识：{decision?.row.tms_product_code ?? "—"}</Typography.Text>
      <Form<DecisionFormValues> form={decisionForm} layout="vertical" preserve={false} onFinish={submitDecision}>
        {decision?.kind === "APPROVE" && <>
          <Form.Item label="企业系统"><Input value="SAP Business One (SAP_B1)" disabled /></Form.Item>
          <Form.Item label="SAP-B1物料编码(仅已审批)" name="enterprise_key" rules={[{ required: true, whitespace: true, message: "请输入已由 SAP 主数据 Owner 确认的物料编码" }]}><Input maxLength={128} /></Form.Item>
        </>}
        <Form.Item label="决策 reason" name="reason" rules={[{ required: true, whitespace: true, message: "请填写完整决策原因" }, { min: 4, message: "决策原因至少 4 个字符" }]}><Input.TextArea rows={4} maxLength={1000} showCount placeholder="说明确认人、依据和变更原因" /></Form.Item>
      </Form>
    </Modal>
  </div>;
}
