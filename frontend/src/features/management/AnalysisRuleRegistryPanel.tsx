import { CheckCircleOutlined, PlusOutlined, ReloadOutlined, SafetyCertificateOutlined } from "@ant-design/icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, Button, Card, Col, Collapse, Form, Input, InputNumber, Row, Select, Space, Table, Tag, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useState } from "react";

import {
  activateAnalysisRuleVersion,
  createAnalysisRule,
  createAnalysisRuleVersion,
  decideAnalysisRuleVersion,
  listAnalysisRules,
  listAnalysisRuleVersions,
  type AnalysisAlgorithmCode,
  type AnalysisRuleSetRecord,
  type AnalysisRuleType,
  type AnalysisRuleVersionRecord,
  type CreateAnalysisRuleVersionRequest,
  type OutlierPolicy,
  type MissingValuePolicy,
  type RetestPolicy,
  type RuleApprovalDecision,
  type RuleApprovalRole,
  type SigmaDefinition,
  type LimitRoundingPolicy,
  type SpcRunRuleMode,
  type CapabilityRiskMetric,
} from "../../api/analysisRules";
import { shanghaiLocalInputToUtc } from "../../utils/dateTime";

interface RuleSetValues {
  ruleCode: string; ruleName: string; evaluationType: AnalysisRuleType;
  businessOwner: number; technicalOwner: number; qualityValidator: number; description: string;
}
interface VersionValues {
  versionCode: string; implementationVersion: string; algorithmCode: AnalysisAlgorithmCode;
  missingValuePolicy: MissingValuePolicy; retestPolicy: RetestPolicy; outlierPolicy: OutlierPolicy; minimumSampleSize: number;
  histogramBinCount?: number; whiskerMultiplier?: number; sigmaDefinition?: SigmaDefinition; subgroupDimension?: string;
  lowerMultiplier?: number; upperMultiplier?: number; zoneCenterX?: number; zoneCenterY?: number; zoneRadius?: number; zoneCenterRatio?: number; zoneMidRatio?: number;
  quadrantAxisRotationDegrees?: number; quadrantYDirection?: "UP" | "DOWN"; quadrantLabelsCcw?: string;
  equalityIsInSpec?: boolean; sparseMinimumCount?: number; testStages: Array<"CP" | "FT">; supplierIds?: string; productIds?: string; parameterPatterns?: string;
  limitRoundingPolicy?: LimitRoundingPolicy; limitRoundingStep?: number; spcRunRuleMode?: SpcRunRuleMode;
  spcConsecutiveBeyondCount?: number; spcConsecutiveBeyondSigma?: number; spcSameSideRunLength?: number; spcMonotonicRunLength?: number;
  capabilityRiskMetric?: CapabilityRiskMetric; capabilityRiskThreshold?: number;
  algorithmSha256: string; goldenManifestSha256: string; effectiveFromLocal?: string; effectiveToLocal?: string; supersedesRuleVersionId?: number;
}
interface DecisionValues { approvalRole: RuleApprovalRole; decision: RuleApprovalDecision; decisionNote: string; goldenManifestSha256?: string }
interface ActivationValues { confirmation: string; testStage: "CP" | "FT"; supplierId?: number; productId?: number; parameterPattern?: string; effectiveFromLocal?: string; effectiveToLocal?: string }

const ruleTypes: AnalysisRuleType[] = ["PAT", "SBL", "SYL", "CPK", "SPC", "HISTOGRAM", "BOX_PLOT", "NORMAL_FIT", "CORRELATION", "MARGIN", "ZONE", "BIN_COOCCURRENCE", "PASS_FAIL_DISTRIBUTION"];
const algorithmsByType: Record<AnalysisRuleType, AnalysisAlgorithmCode[]> = {
  PAT: ["PAT_SHARED_IQR_1_35_V1"], SBL: ["SBL_GROUPED_LIMIT_V1"], SYL: ["SYL_GROUPED_LIMIT_V1"], CPK: ["CPK_POOLED_WITHIN_RUN_V1", "CPK_POOLED_WITHIN_LOT_WAFER_V1"], SPC: ["SPC_I_MR_V1"],
  HISTOGRAM: ["EQUAL_WIDTH_HISTOGRAM_V1"], BOX_PLOT: ["TUKEY_BOX_V1"], NORMAL_FIT: ["NORMAL_FIT_MLE_V1"], CORRELATION: ["PEARSON_PAIRWISE_V1", "SPEARMAN_PAIRWISE_V1"],
  MARGIN: ["SPEC_MARGIN_V1"], ZONE: ["WAFER_ZONE_GEOMETRY_V2", "WAFER_ZONE_GEOMETRY_V1"], BIN_COOCCURRENCE: ["BIN_COOCCURRENCE_UNIT_V1"], PASS_FAIL_DISTRIBUTION: ["PASS_FAIL_DISTRIBUTION_V1"],
};
const qualitySubgroupAlgorithms = new Set<AnalysisAlgorithmCode>(["CPK_POOLED_WITHIN_RUN_V1", "CPK_POOLED_WITHIN_LOT_WAFER_V1", "PAT_SHARED_IQR_1_35_V1", "SBL_GROUPED_LIMIT_V1", "SYL_GROUPED_LIMIT_V1", "SPC_I_MR_V1", "SPEC_MARGIN_V1", "BIN_COOCCURRENCE_UNIT_V1", "PASS_FAIL_DISTRIBUTION_V1"]);
const sigmaAlgorithms = new Set<AnalysisAlgorithmCode>(["CPK_POOLED_WITHIN_RUN_V1", "CPK_POOLED_WITHIN_LOT_WAFER_V1", "SBL_GROUPED_LIMIT_V1", "SYL_GROUPED_LIMIT_V1", "SPC_I_MR_V1"]);
const zoneAlgorithms = new Set<AnalysisAlgorithmCode>(["WAFER_ZONE_GEOMETRY_V1", "WAFER_ZONE_GEOMETRY_V2"]);
const formalPatAdapterManifestSha256 = "3564929accfae8af9745d7ed08f42bc7b08503d17373a8e45d6d7a63bff85c34";
const hexRule = { pattern: /^[a-f0-9]{64}$/, message: "必须是 64 位小写 SHA-256" };
const parseCsv = (value?: string) => value?.trim() ? value.split(",").map((item) => item.trim()) : [];
const hasDuplicates = <T,>(items: T[]) => new Set(items).size !== items.length;
const optionalUtc = (value?: string) => value ? shanghaiLocalInputToUtc(value) ?? null : null;

export function AnalysisRuleRegistryPanel() {
  const queryClient = useQueryClient();
  const [ruleForm] = Form.useForm<RuleSetValues>();
  const [versionForm] = Form.useForm<VersionValues>();
  const [decisionForm] = Form.useForm<DecisionValues>();
  const [activationForm] = Form.useForm<ActivationValues>();
  const [selectedRuleCode, setSelectedRuleCode] = useState<string>();
  const [selectedVersion, setSelectedVersion] = useState<AnalysisRuleVersionRecord>();
  const [success, setSuccess] = useState<string>();
  const [clientError, setClientError] = useState<string>();
  const rules = useQuery({ queryKey: ["analysis-rules"], queryFn: listAnalysisRules, retry: false });
  const activeRule = rules.data?.find((item) => item.rule_code === selectedRuleCode);
  const versions = useQuery({ queryKey: ["analysis-rules", selectedRuleCode, "versions"], queryFn: () => listAnalysisRuleVersions(selectedRuleCode!), enabled: Boolean(selectedRuleCode), retry: false });
  const algorithm = Form.useWatch("algorithmCode", versionForm);
  const spcRunRuleMode = Form.useWatch("spcRunRuleMode", versionForm);
  const limitRoundingPolicy = Form.useWatch("limitRoundingPolicy", versionForm);
  const decisionRole = Form.useWatch("approvalRole", decisionForm);
  const decision = Form.useWatch("decision", decisionForm);

  const invalidate = async () => {
    await queryClient.invalidateQueries({ queryKey: ["analysis-rules"] });
  };
  const createRuleMutation = useMutation({
    mutationFn: (values: RuleSetValues) => createAnalysisRule({
      rule_code: values.ruleCode.trim().toUpperCase(), rule_name: values.ruleName.trim(), evaluation_type: values.evaluationType,
      business_owner_user_id: values.businessOwner, technical_owner_user_id: values.technicalOwner, quality_validator_user_id: values.qualityValidator, description: values.description.trim(),
    }),
    onSuccess: async (record) => { setSuccess(`Rule Set ${record.rule_code} 已创建`); setSelectedRuleCode(record.rule_code); ruleForm.resetFields(); await invalidate(); },
  });
  const createVersionMutation = useMutation({
    mutationFn: ({ ruleCode, request }: { ruleCode: string; request: CreateAnalysisRuleVersionRequest }) => createAnalysisRuleVersion(ruleCode, request),
    onSuccess: async (record) => { setSuccess(`${record.rule_code}@${record.version_code} 已创建为 ${record.status}/${record.activation_status}`); setSelectedVersion(record); versionForm.resetFields(); await invalidate(); },
  });
  const decisionMutation = useMutation({
    mutationFn: (values: DecisionValues) => decideAnalysisRuleVersion(selectedVersion!.evaluation_rule_version_id, {
      approval_role: values.approvalRole, decision: values.decision, decision_note: values.decisionNote.trim(),
      ...(values.approvalRole === "QUALITY" && values.decision === "APPROVED" ? { golden_manifest_sha256: values.goldenManifestSha256!.trim() } : {}),
    }),
    onSuccess: async (record) => { setSuccess(`Version #${record.evaluation_rule_version_id} 决策已记录`); setSelectedVersion(record); decisionForm.resetFields(); await invalidate(); },
  });
  const activationMutation = useMutation({
    mutationFn: (values: ActivationValues) => activateAnalysisRuleVersion(selectedVersion!.evaluation_rule_version_id, {
      confirmation: "ACTIVATE", test_stage: values.testStage, supplier_id: values.supplierId ?? null, product_id: values.productId ?? null,
      parameter_pattern: values.parameterPattern?.trim() || null, effective_from_utc: optionalUtc(values.effectiveFromLocal), effective_to_utc: optionalUtc(values.effectiveToLocal),
    }),
    onSuccess: async (record) => { setSuccess(`Activation #${record.rule_activation_id} 已创建`); activationForm.resetFields(); await invalidate(); },
  });

  const createRuleSet = (values: RuleSetValues) => {
    if (new Set([values.businessOwner, values.technicalOwner, values.qualityValidator]).size !== 3) {
      setClientError("Business Owner、Technical Owner、Quality Validator 必须是三个不同用户。");
      return;
    }
    setClientError(undefined);
    createRuleMutation.mutate(values);
  };
  const createVersion = (values: VersionValues) => {
    if (!selectedRuleCode) return;
    const supplierTokens = parseCsv(values.supplierIds);
    const productTokens = parseCsv(values.productIds);
    const supplierIds = supplierTokens.map(Number);
    const productIds = productTokens.map(Number);
    const parameterPatterns = parseCsv(values.parameterPatterns);
    const quadrantLabelsCcw = parseCsv(values.quadrantLabelsCcw);
    if ([supplierTokens, productTokens].some((tokens) => tokens.some((item) => !/^\d+$/.test(item)))
      || [...supplierIds, ...productIds].some((item) => !Number.isSafeInteger(item) || item < 1)
      || hasDuplicates(supplierIds) || hasDuplicates(productIds)) {
      setClientError("Supplier / Product IDs 必须是唯一、逗号分隔的正整数，不能包含空项。"); return;
    }
    if (parameterPatterns.some((item) => !item || item === "*" || item.slice(0, -1).includes("*") || item.length > 300 || [...item].some((character) => character.charCodeAt(0) < 32)) || hasDuplicates(parameterPatterns)) {
      setClientError("Parameter Pattern 必须唯一，只允许末尾可选 *，且不能是单独的 * 或包含控制字符。"); return;
    }
    if (values.algorithmCode === "WAFER_ZONE_GEOMETRY_V2" && (quadrantLabelsCcw.length !== 4 || hasDuplicates(quadrantLabelsCcw) || quadrantLabelsCcw.some((item) => !item || item.length > 64 || [...item].some((character) => character.charCodeAt(0) < 32)))) {
      setClientError("Quadrant Labels CCW 必须显式填写四个唯一、非空、逗号分隔的业务标签。"); return;
    }
    const effectiveFromUtc = optionalUtc(values.effectiveFromLocal);
    const effectiveToUtc = optionalUtc(values.effectiveToLocal);
    if ((values.effectiveFromLocal && !effectiveFromUtc) || (values.effectiveToLocal && !effectiveToUtc)) {
      setClientError("Version 生效时间不是有效的上海本地时间。"); return;
    }
    if (effectiveFromUtc && effectiveToUtc && effectiveToUtc <= effectiveFromUtc) {
      setClientError("Version Effective To 必须晚于 Effective From。"); return;
    }
    const parameters: CreateAnalysisRuleVersionRequest["parameters"] = {
      missing_value_policy: values.missingValuePolicy, retest_policy: values.retestPolicy, outlier_policy: values.outlierPolicy, minimum_sample_size: values.minimumSampleSize,
      ...(values.histogramBinCount !== undefined ? { histogram_bin_count: values.histogramBinCount } : {}),
      ...(values.whiskerMultiplier !== undefined ? { whisker_multiplier: values.whiskerMultiplier } : {}),
      ...(values.sigmaDefinition ? { sigma_definition: values.sigmaDefinition } : {}),
      ...(values.subgroupDimension ? { subgroup_dimension: values.subgroupDimension } : {}),
      ...(values.lowerMultiplier !== undefined ? { lower_multiplier: values.lowerMultiplier } : {}),
      ...(values.upperMultiplier !== undefined ? { upper_multiplier: values.upperMultiplier } : {}),
      ...(values.zoneCenterX !== undefined ? { zone_layout_center_x: values.zoneCenterX } : {}), ...(values.zoneCenterY !== undefined ? { zone_layout_center_y: values.zoneCenterY } : {}),
      ...(values.zoneRadius !== undefined ? { zone_layout_radius_die: values.zoneRadius } : {}), ...(values.zoneCenterRatio !== undefined ? { zone_center_ratio: values.zoneCenterRatio } : {}), ...(values.zoneMidRatio !== undefined ? { zone_mid_ratio: values.zoneMidRatio } : {}),
      ...(values.quadrantAxisRotationDegrees !== undefined ? { quadrant_axis_rotation_degrees: values.quadrantAxisRotationDegrees } : {}),
      ...(values.quadrantYDirection ? { quadrant_y_direction: values.quadrantYDirection } : {}),
      ...(quadrantLabelsCcw.length ? { quadrant_labels_ccw: quadrantLabelsCcw } : {}),
      ...(values.equalityIsInSpec !== undefined ? { equality_is_in_spec: values.equalityIsInSpec } : {}), ...(values.sparseMinimumCount !== undefined ? { sparse_matrix_minimum_count: values.sparseMinimumCount } : {}),
      ...(values.limitRoundingPolicy ? { limit_rounding_policy: values.limitRoundingPolicy } : {}),
      ...(values.limitRoundingStep !== undefined ? { limit_rounding_step: values.limitRoundingStep } : {}),
      ...(values.spcRunRuleMode ? { spc_run_rule_mode: values.spcRunRuleMode } : {}),
      ...(values.spcConsecutiveBeyondCount !== undefined ? { spc_consecutive_beyond_count: values.spcConsecutiveBeyondCount } : {}),
      ...(values.spcConsecutiveBeyondSigma !== undefined ? { spc_consecutive_beyond_sigma: values.spcConsecutiveBeyondSigma } : {}),
      ...(values.spcSameSideRunLength !== undefined ? { spc_same_side_run_length: values.spcSameSideRunLength } : {}),
      ...(values.spcMonotonicRunLength !== undefined ? { spc_monotonic_run_length: values.spcMonotonicRunLength } : {}),
      ...(values.capabilityRiskMetric ? { capability_risk_metric: values.capabilityRiskMetric } : {}),
      ...(values.capabilityRiskThreshold !== undefined ? { capability_risk_threshold: values.capabilityRiskThreshold } : {}),
    };
    setClientError(undefined);
    createVersionMutation.mutate({ ruleCode: selectedRuleCode, request: {
      version_code: values.versionCode.trim(), implementation_version: values.implementationVersion.trim(), algorithm_code: values.algorithmCode, parameters,
      applicability: { test_stages: values.testStages, supplier_ids: supplierIds, product_ids: productIds, parameter_patterns: parameterPatterns },
      algorithm_sha256: values.algorithmSha256.trim(), golden_manifest_sha256: values.goldenManifestSha256.trim(),
      effective_from_utc: effectiveFromUtc, effective_to_utc: effectiveToUtc, supersedes_rule_version_id: values.supersedesRuleVersionId ?? null,
    } });
  };
  const activateVersion = (values: ActivationValues) => {
    if (values.confirmation !== "ACTIVATE") {
      setClientError("Activation Confirmation 必须精确输入 ACTIVATE。"); return;
    }
    const parameterPattern = values.parameterPattern?.trim() || null;
    if (parameterPattern && (parameterPattern === "*" || parameterPattern.slice(0, -1).includes("*") || [...parameterPattern].some((character) => character.charCodeAt(0) < 32))) {
      setClientError("Activation Parameter Pattern 只允许末尾可选 *，且不能是单独的 * 或包含控制字符。"); return;
    }
    const effectiveFromUtc = optionalUtc(values.effectiveFromLocal);
    const effectiveToUtc = optionalUtc(values.effectiveToLocal);
    if ((values.effectiveFromLocal && !effectiveFromUtc) || (values.effectiveToLocal && !effectiveToUtc)) {
      setClientError("Activation 生效时间不是有效的上海本地时间。"); return;
    }
    if (effectiveFromUtc && effectiveToUtc && effectiveToUtc <= effectiveFromUtc) {
      setClientError("Activation Effective To 必须晚于 Effective From。"); return;
    }
    setClientError(undefined);
    activationMutation.mutate({ ...values, parameterPattern: parameterPattern ?? undefined, effectiveFromLocal: values.effectiveFromLocal, effectiveToLocal: values.effectiveToLocal });
  };
  const operationError = createRuleMutation.error ?? createVersionMutation.error ?? decisionMutation.error ?? activationMutation.error;

  const ruleColumns: ColumnsType<AnalysisRuleSetRecord> = [
    { title: "Rule Code", dataIndex: "rule_code", key: "code", render: (value) => <Typography.Text code>{value}</Typography.Text> },
    { title: "Name", dataIndex: "rule_name", key: "name" }, { title: "Type", dataIndex: "evaluation_type", key: "type", render: (value) => <Tag>{value}</Tag> },
    { title: "Owners B / T / Q", key: "owners", render: (_, row) => `${row.business_owner_user_id} / ${row.technical_owner_user_id} / ${row.quality_validator_user_id}` },
    { title: "Active", dataIndex: "active", key: "active", render: (value) => <Tag color={value ? "success" : "default"}>{value ? "YES" : "NO"}</Tag> },
    { title: "操作", key: "action", render: (_, row) => <Button size="small" type={selectedRuleCode === row.rule_code ? "primary" : "default"} onClick={() => { setSelectedRuleCode(row.rule_code); setSelectedVersion(undefined); }}>查看版本</Button> },
  ];
  const versionColumns: ColumnsType<AnalysisRuleVersionRecord> = [
    { title: "Version", dataIndex: "version_code", key: "version", render: (value, row) => <Space direction="vertical" size={0}><Typography.Text strong>{value}</Typography.Text><Typography.Text type="secondary">#{row.evaluation_rule_version_id}</Typography.Text></Space> },
    { title: "Implementation", dataIndex: "implementation_version", key: "implementation" }, { title: "Algorithm", dataIndex: "algorithm_code", key: "algorithm" },
    { title: "Status", dataIndex: "status", key: "status", render: (value) => <Tag color={value === "APPROVED" ? "success" : "warning"}>{value}</Tag> },
    { title: "Activation", dataIndex: "activation_status", key: "activation", render: (value) => <Tag color={value === "ENABLED" ? "success" : "default"}>{value}</Tag> },
    { title: "Approvals", dataIndex: "approvals", key: "approvals", render: (values: string[]) => values.length ? <Space wrap>{values.map((value) => <Tag key={value}>{value}</Tag>)}</Space> : <Typography.Text type="secondary">尚无决策</Typography.Text> },
    { title: "操作", key: "action", render: (_, row) => <Button size="small" onClick={() => setSelectedVersion(row)}>审批 / 激活</Button> },
  ];

  return <Card title="Analysis Rule Registry（仅 RULE_GOVERN）" extra={<Button icon={<ReloadOutlined />} loading={rules.isFetching || versions.isFetching} onClick={() => void invalidate()}>刷新</Button>}>
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      <Alert type="warning" showIcon message="创建不等于批准，批准不等于激活" description="新 Version 必须保持 DRAFT / DISABLED；三方决策和 Activation 都是独立显式操作。Golden SHA、ACTIVATE 确认和审批角色不会被预填。" />
      {success && <Alert type="success" showIcon closable onClose={() => setSuccess(undefined)} message={success} />}
      {clientError && <Alert type="error" showIcon message="Rule 输入无效" description={clientError} />}
      {operationError && <Alert type="error" showIcon message="Rule Registry 操作失败" description={operationError.message} />}
      <Table rowKey="evaluation_rule_set_id" columns={ruleColumns} dataSource={rules.data ?? []} loading={rules.isLoading} pagination={false} scroll={{ x: 1000 }} locale={{ emptyText: rules.isError ? rules.error.message : "暂无 Rule Set" }} />

      <Collapse items={[{ key: "create-rule", label: "新建 Rule Set", children: <Form<RuleSetValues> form={ruleForm} layout="vertical" onFinish={createRuleSet}><Row gutter={12}>
        <Col xs={24} md={8}><Form.Item label="Rule Code" name="ruleCode" normalize={(value: string) => value.toUpperCase()} rules={[{ required: true, pattern: /^[A-Z][A-Z0-9_]{2,127}$/ }]}><Input aria-label="Rule Set Code" /></Form.Item></Col>
        <Col xs={24} md={8}><Form.Item label="Rule Name" name="ruleName" rules={[{ required: true, max: 300 }]}><Input aria-label="Rule Set Name" /></Form.Item></Col>
        <Col xs={24} md={8}><Form.Item label="Evaluation Type" name="evaluationType" rules={[{ required: true }]}><Select aria-label="Rule Evaluation Type" options={ruleTypes.map((value) => ({ label: value, value }))} /></Form.Item></Col>
        <Col xs={24} md={8}><Form.Item label="Business Owner User ID" name="businessOwner" rules={[{ required: true, type: "number", min: 1 }]}><InputNumber aria-label="Business Owner ID" min={1} className="full-width" /></Form.Item></Col>
        <Col xs={24} md={8}><Form.Item label="Technical Owner User ID" name="technicalOwner" rules={[{ required: true, type: "number", min: 1 }]}><InputNumber aria-label="Technical Owner ID" min={1} className="full-width" /></Form.Item></Col>
        <Col xs={24} md={8}><Form.Item label="Quality Validator User ID" name="qualityValidator" rules={[{ required: true, type: "number", min: 1 }]}><InputNumber aria-label="Quality Validator ID" min={1} className="full-width" /></Form.Item></Col>
        <Col span={24}><Form.Item label="Description" name="description" rules={[{ required: true, min: 8, max: 1000 }]}><Input.TextArea aria-label="Rule Set Description" rows={2} /></Form.Item></Col>
        <Col span={24}><Button htmlType="submit" type="primary" icon={<PlusOutlined />} loading={createRuleMutation.isPending}>创建 Rule Set</Button></Col>
      </Row></Form> }]} />

      {activeRule && <Card size="small" title={`${activeRule.rule_code} · ${activeRule.rule_name} · ${activeRule.evaluation_type}`}>
        <Space direction="vertical" size="middle" style={{ width: "100%" }}>
          <Table rowKey="evaluation_rule_version_id" columns={versionColumns} dataSource={versions.data ?? []} loading={versions.isLoading} pagination={false} scroll={{ x: 1100 }} locale={{ emptyText: versions.isError ? versions.error.message : "暂无 Version" }} />
          <Collapse items={[{ key: "create-version", label: "新建 Version（默认 DRAFT / DISABLED）", children: <Form<VersionValues> form={versionForm} layout="vertical" onFinish={createVersion}><Row gutter={12}>
            <Col xs={24} md={8}><Form.Item label="Version Code" name="versionCode" rules={[{ required: true, pattern: /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/ }]}><Input aria-label="Rule Version Code" /></Form.Item></Col>
            <Col xs={24} md={8}><Form.Item label="Implementation Version" name="implementationVersion" rules={[{ required: true, pattern: /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/ }]}><Input aria-label="Rule Implementation Version" /></Form.Item></Col>
            <Col xs={24} md={8}><Form.Item label="Algorithm" name="algorithmCode" rules={[{ required: true }]}><Select aria-label="Rule Algorithm" options={algorithmsByType[activeRule.evaluation_type].map((value) => ({ label: value, value }))} /></Form.Item></Col>
            <Col xs={24} md={8}><Form.Item label="Missing Value Policy" name="missingValuePolicy" rules={[{ required: true }]}><Select aria-label="Missing Value Policy" options={["EXCLUDE_AND_COUNT", "PAIRWISE_EXCLUDE_AND_COUNT", "FAIL_IF_ANY"].map((value) => ({ label: value, value }))} /></Form.Item></Col>
            <Col xs={24} md={8}><Form.Item label="Retest Policy" name="retestPolicy" rules={[{ required: true }]}><Select aria-label="Retest Policy" options={["EACH_ATTEMPT", "LATEST_ATTEMPT", "FIRST_ATTEMPT"].map((value) => ({ label: value, value }))} /></Form.Item></Col>
            <Col xs={24} md={8}><Form.Item label="Outlier Policy" name="outlierPolicy" rules={[{ required: true }]}><Select aria-label="Outlier Policy" options={["MARK_ONLY", "EXCLUDE_WITH_AUDIT"].map((value) => ({ label: value, value }))} /></Form.Item></Col>
            <Col xs={24} md={8}><Form.Item label="Minimum Sample Size" name="minimumSampleSize" rules={[{ required: true, type: "number", min: 2, max: 1000000 }]}><InputNumber aria-label="Minimum Sample Size" min={2} max={1000000} className="full-width" /></Form.Item></Col>
            {(algorithm === "EQUAL_WIDTH_HISTOGRAM_V1" || algorithm === "PASS_FAIL_DISTRIBUTION_V1") && <Col xs={24} md={8}><Form.Item preserve={false} label="Histogram Bin Count" name="histogramBinCount" rules={[{ required: true, type: "number", min: 5, max: 100 }]}><InputNumber aria-label="Histogram Bin Count" min={5} max={100} className="full-width" /></Form.Item></Col>}
            {algorithm === "TUKEY_BOX_V1" && <Col xs={24} md={8}><Form.Item preserve={false} label="Whisker Multiplier" name="whiskerMultiplier" rules={[{ required: true, type: "number", min: 0.000001, max: 10 }]}><InputNumber aria-label="Whisker Multiplier" min={0.000001} max={10} className="full-width" /></Form.Item></Col>}
            {algorithm && sigmaAlgorithms.has(algorithm) && <Col xs={24} md={8}><Form.Item preserve={false} label="Sigma Definition" name="sigmaDefinition" rules={[{ required: true }]}><Select aria-label="Sigma Definition" options={(algorithm === "SPC_I_MR_V1" ? ["POOLED_WITHIN"] : algorithm === "SBL_GROUPED_LIMIT_V1" || algorithm === "SYL_GROUPED_LIMIT_V1" ? ["SAMPLE"] : ["SAMPLE", "POPULATION", "POOLED_WITHIN"]).map((value) => ({ label: value, value }))} /></Form.Item></Col>}
            {(algorithm === "CPK_POOLED_WITHIN_RUN_V1" || algorithm === "CPK_POOLED_WITHIN_LOT_WAFER_V1") && <><Col xs={24} md={8}><Form.Item preserve={false} label="Capability Risk Metric" name="capabilityRiskMetric" rules={[{ required: true }]}><Select aria-label="Capability Risk Metric" options={["CPK", "PPK", "MIN_CPK_PPK"].map((value) => ({ label: value, value }))} /></Form.Item></Col><Col xs={24} md={8}><Form.Item preserve={false} label="Capability Risk Threshold" name="capabilityRiskThreshold" rules={[{ required: true, type: "number", min: 0.000001, max: 100 }]}><InputNumber aria-label="Capability Risk Threshold" min={0.000001} max={100} className="full-width" /></Form.Item></Col></>}
            {algorithm && qualitySubgroupAlgorithms.has(algorithm) && <Col xs={24} md={8}><Form.Item preserve={false} label="Subgroup Dimension" name="subgroupDimension" rules={[{ required: true }]}><Select aria-label="Subgroup Dimension" options={["DATASET", "LOT", "WAFER", "LOT_WAFER", "RUN", "TESTER", "PROGRAM", "CONDITION"].map((value) => ({ label: value, value }))} /></Form.Item></Col>}
            {algorithm === "PAT_SHARED_IQR_1_35_V1" && <><Col xs={24} md={8}><Form.Item preserve={false} label="Lower Multiplier (fixed by shared engine)" name="lowerMultiplier" initialValue={6} rules={[{ required: true, type: "number", min: 6, max: 6 }]}><InputNumber aria-label="Lower Multiplier" min={6} max={6} disabled className="full-width" /></Form.Item></Col><Col xs={24} md={8}><Form.Item preserve={false} label="Upper Multiplier (fixed by shared engine)" name="upperMultiplier" initialValue={6} rules={[{ required: true, type: "number", min: 6, max: 6 }]}><InputNumber aria-label="Upper Multiplier" min={6} max={6} disabled className="full-width" /></Form.Item></Col></>}
            {algorithm === "PAT_SHARED_IQR_1_35_V1" && <Col span={24}><Alert type="info" showIcon message="正式 PAT 复用已冻结共享引擎 Adapter" description={<span>算法 SHA 必须人工填写并精确匹配 <Typography.Text code copyable>{formalPatAdapterManifestSha256}</Typography.Text>；Golden SHA 仍须由 Quality Validator 独立批准，系统不会预填。</span>} /></Col>}
            {algorithm === "SBL_GROUPED_LIMIT_V1" && <Col xs={24} md={8}><Form.Item preserve={false} label="Upper Multiplier" name="upperMultiplier" rules={[{ required: true, type: "number", min: 0.000001, max: 100 }]}><InputNumber aria-label="Upper Multiplier" className="full-width" /></Form.Item></Col>}
            {algorithm === "SYL_GROUPED_LIMIT_V1" && <><Col xs={24} md={8}><Form.Item preserve={false} label="Lower Multiplier" name="lowerMultiplier" rules={[{ required: true, type: "number", min: 0.000001, max: 100 }]}><InputNumber aria-label="SYL Lower Multiplier" className="full-width" /></Form.Item></Col><Col xs={24} md={8}><Form.Item preserve={false} label="Limit Rounding Policy" name="limitRoundingPolicy" rules={[{ required: true }]}><Select aria-label="SYL Rounding Policy" options={["NONE", "FLOOR_TO_STEP", "CEILING_TO_STEP"].map((value) => ({ label: value, value }))} /></Form.Item></Col>{limitRoundingPolicy && limitRoundingPolicy !== "NONE" && <Col xs={24} md={8}><Form.Item preserve={false} label="Rounding Step" name="limitRoundingStep" rules={[{ required: true, type: "number", min: 0.000001, max: 1 }]}><InputNumber aria-label="SYL Rounding Step" className="full-width" /></Form.Item></Col>}</>}
            {algorithm === "SPC_I_MR_V1" && <><Col xs={24} md={8}><Form.Item preserve={false} label="SPC Run Rule Mode" name="spcRunRuleMode" rules={[{ required: true }]}><Select aria-label="SPC Run Rule Mode" options={["NONE", "BASIC"].map((value) => ({ label: value, value }))} /></Form.Item></Col>{spcRunRuleMode === "BASIC" && <><Col xs={24} md={8}><Form.Item preserve={false} label="Consecutive Beyond Count" name="spcConsecutiveBeyondCount" rules={[{ required: true, type: "number", min: 2, max: 20 }]}><InputNumber aria-label="SPC Consecutive Beyond Count" className="full-width" /></Form.Item></Col><Col xs={24} md={8}><Form.Item preserve={false} label="Consecutive Beyond Sigma" name="spcConsecutiveBeyondSigma" rules={[{ required: true, type: "number", min: 0.000001, max: 10 }]}><InputNumber aria-label="SPC Consecutive Beyond Sigma" className="full-width" /></Form.Item></Col><Col xs={24} md={8}><Form.Item preserve={false} label="Same Side Run Length" name="spcSameSideRunLength" rules={[{ required: true, type: "number", min: 2, max: 50 }]}><InputNumber aria-label="SPC Same Side Run Length" className="full-width" /></Form.Item></Col><Col xs={24} md={8}><Form.Item preserve={false} label="Monotonic Run Length" name="spcMonotonicRunLength" rules={[{ required: true, type: "number", min: 3, max: 50 }]}><InputNumber aria-label="SPC Monotonic Run Length" className="full-width" /></Form.Item></Col></>}</>}
            {algorithm === "SPEC_MARGIN_V1" && <Col xs={24} md={8}><Form.Item preserve={false} label="Equality Is In Spec" name="equalityIsInSpec" rules={[{ required: true }]}><Select aria-label="Equality Is In Spec" options={[{ label: "YES", value: true }, { label: "NO", value: false }]} /></Form.Item></Col>}
            {algorithm === "BIN_COOCCURRENCE_UNIT_V1" && <Col xs={24} md={8}><Form.Item preserve={false} label="Sparse Minimum Count" name="sparseMinimumCount" rules={[{ required: true, type: "number", min: 1 }]}><InputNumber aria-label="Sparse Minimum Count" min={1} className="full-width" /></Form.Item></Col>}
            {algorithm && zoneAlgorithms.has(algorithm) && <><Col xs={12} md={4}><Form.Item preserve={false} label="Center X" name="zoneCenterX" rules={[{ required: true }]}><InputNumber aria-label="Zone Center X" className="full-width" /></Form.Item></Col><Col xs={12} md={4}><Form.Item preserve={false} label="Center Y" name="zoneCenterY" rules={[{ required: true }]}><InputNumber aria-label="Zone Center Y" className="full-width" /></Form.Item></Col><Col xs={12} md={4}><Form.Item preserve={false} label="Radius Die" name="zoneRadius" rules={[{ required: true, type: "number", min: 0.000001 }]}><InputNumber aria-label="Zone Radius" className="full-width" /></Form.Item></Col><Col xs={12} md={4}><Form.Item preserve={false} label="Center Ratio" name="zoneCenterRatio" rules={[{ required: true }]}><InputNumber aria-label="Zone Center Ratio" className="full-width" /></Form.Item></Col><Col xs={12} md={4}><Form.Item preserve={false} label="Mid Ratio" name="zoneMidRatio" rules={[{ required: true }]}><InputNumber aria-label="Zone Mid Ratio" className="full-width" /></Form.Item></Col></>}
            {algorithm === "WAFER_ZONE_GEOMETRY_V2" && <><Col xs={24} md={8}><Form.Item preserve={false} label="Quadrant Axis Rotation (degrees)" name="quadrantAxisRotationDegrees" rules={[{ required: true, type: "number", min: 0, max: 359.999999 }]}><InputNumber aria-label="Quadrant Axis Rotation" min={0} max={359.999999} className="full-width" /></Form.Item></Col><Col xs={24} md={8}><Form.Item preserve={false} label="Quadrant Y Direction" name="quadrantYDirection" rules={[{ required: true }]}><Select aria-label="Quadrant Y Direction" options={[{ label: "UP", value: "UP" }, { label: "DOWN", value: "DOWN" }]} /></Form.Item></Col><Col xs={24} md={8}><Form.Item preserve={false} label="Quadrant Labels CCW（4 个，逗号分隔）" name="quadrantLabelsCcw" rules={[{ required: true }]}><Input aria-label="Quadrant Labels CCW" placeholder="必须由业务 Owner 明确，例如标签A,标签B,标签C,标签D" /></Form.Item></Col></>}
            <Col xs={24} md={8}><Form.Item label="Test Stages" name="testStages" rules={[{ required: true, type: "array", min: 1 }]}><Select aria-label="Rule Test Stages" mode="multiple" options={[{ label: "CP", value: "CP" }, { label: "FT", value: "FT" }]} /></Form.Item></Col>
            <Col xs={24} md={8}><Form.Item label="Supplier IDs（逗号分隔，可空）" name="supplierIds"><Input aria-label="Rule Supplier IDs" /></Form.Item></Col><Col xs={24} md={8}><Form.Item label="Product IDs（逗号分隔，可空）" name="productIds"><Input aria-label="Rule Product IDs" /></Form.Item></Col>
            <Col xs={24} md={8}><Form.Item label="Parameter Patterns（逗号分隔，可空）" name="parameterPatterns"><Input aria-label="Rule Parameter Patterns" placeholder="例如 VTH*，不能是 *" /></Form.Item></Col>
            <Col xs={24} md={8}><Form.Item label="Algorithm SHA-256" name="algorithmSha256" rules={[{ required: true }, hexRule]}><Input aria-label="Algorithm SHA-256" maxLength={64} /></Form.Item></Col>
            <Col xs={24} md={8}><Form.Item label="Golden Manifest SHA-256" name="goldenManifestSha256" rules={[{ required: true }, hexRule]}><Input aria-label="Golden Manifest SHA-256" maxLength={64} placeholder="不预填" /></Form.Item></Col>
            <Col xs={24} md={8}><Form.Item label="Supersedes Version ID（可空）" name="supersedesRuleVersionId"><InputNumber aria-label="Supersedes Version ID" min={1} className="full-width" /></Form.Item></Col>
            <Col xs={24} md={8}><Form.Item label="Effective From（上海，可空）" name="effectiveFromLocal"><Input aria-label="Rule Effective From" type="datetime-local" /></Form.Item></Col><Col xs={24} md={8}><Form.Item label="Effective To（上海，可空）" name="effectiveToLocal"><Input aria-label="Rule Effective To" type="datetime-local" /></Form.Item></Col>
            <Col span={24}><Button htmlType="submit" type="primary" icon={<PlusOutlined />} loading={createVersionMutation.isPending}>创建 Version</Button></Col>
          </Row></Form> }]} />
        </Space>
      </Card>}

      {selectedVersion && <Card size="small" title={`治理 Version #${selectedVersion.evaluation_rule_version_id} · ${selectedVersion.rule_code}@${selectedVersion.version_code}`}>
        <Space direction="vertical" size="middle" style={{ width: "100%" }}>
          <Space wrap><Tag>{selectedVersion.status}</Tag><Tag>{selectedVersion.activation_status}</Tag>{selectedVersion.approvals.map((item) => <Tag key={item}>{item}</Tag>)}</Space>
          <Alert type="info" showIcon message="三方决策逐次记录" description="Business / Technical / Quality 决策不会批量提交。Quality APPROVED 必须手工输入与 Version 对应的 Golden Manifest SHA-256。" />
          <Form<DecisionValues> form={decisionForm} layout="vertical" onFinish={(values) => decisionMutation.mutate(values)}><Row gutter={12}>
            <Col xs={24} md={6}><Form.Item label="Approval Role" name="approvalRole" rules={[{ required: true }]}><Select aria-label="Decision Approval Role" options={["BUSINESS", "TECHNICAL", "QUALITY"].map((value) => ({ label: value, value }))} /></Form.Item></Col>
            <Col xs={24} md={6}><Form.Item label="Decision" name="decision" rules={[{ required: true }]}><Select aria-label="Rule Decision" options={["APPROVED", "REJECTED", "REVOKED"].map((value) => ({ label: value, value }))} /></Form.Item></Col>
            <Col xs={24} md={6}><Form.Item label="Decision Note" name="decisionNote" rules={[{ required: true, min: 8, max: 1000 }]}><Input aria-label="Decision Note" /></Form.Item></Col>
            {decisionRole === "QUALITY" && decision === "APPROVED" && <Col xs={24} md={6}><Form.Item preserve={false} label="Golden Manifest SHA-256" name="goldenManifestSha256" rules={[{ required: true }, hexRule]}><Input aria-label="Decision Golden SHA-256" maxLength={64} placeholder="不预填" /></Form.Item></Col>}
            <Col span={24}><Button htmlType="submit" icon={<CheckCircleOutlined />} loading={decisionMutation.isPending}>提交单次决策</Button></Col>
          </Row></Form>
          <Alert type="warning" showIcon message="Activation 是独立动作" description="输入 ACTIVATE 才可提交；实际能否启用仍由服务端检查三方批准、Golden、范围冲突和有效期。" />
          <Form<ActivationValues> form={activationForm} layout="vertical" onFinish={activateVersion}><Row gutter={12}>
            <Col xs={24} md={6}><Form.Item label="Confirmation" name="confirmation" rules={[{ required: true, pattern: /^ACTIVATE$/ }]}><Input aria-label="Activation Confirmation" placeholder="手工输入 ACTIVATE" /></Form.Item></Col>
            <Col xs={24} md={6}><Form.Item label="Test Stage" name="testStage" rules={[{ required: true }]}><Select aria-label="Activation Test Stage" options={[{ label: "CP", value: "CP" }, { label: "FT", value: "FT" }]} /></Form.Item></Col>
            <Col xs={24} md={6}><Form.Item label="Supplier ID（可空）" name="supplierId"><InputNumber aria-label="Activation Supplier ID" min={1} className="full-width" /></Form.Item></Col><Col xs={24} md={6}><Form.Item label="Product ID（可空）" name="productId"><InputNumber aria-label="Activation Product ID" min={1} className="full-width" /></Form.Item></Col>
            <Col xs={24} md={6}><Form.Item label="Parameter Pattern（可空）" name="parameterPattern"><Input aria-label="Activation Parameter Pattern" /></Form.Item></Col><Col xs={24} md={6}><Form.Item label="Effective From（上海，可空）" name="effectiveFromLocal"><Input aria-label="Activation Effective From" type="datetime-local" /></Form.Item></Col><Col xs={24} md={6}><Form.Item label="Effective To（上海，可空）" name="effectiveToLocal"><Input aria-label="Activation Effective To" type="datetime-local" /></Form.Item></Col>
            <Col span={24}><Button htmlType="submit" type="primary" danger icon={<SafetyCertificateOutlined />} loading={activationMutation.isPending}>创建 Activation</Button></Col>
          </Row></Form>
        </Space>
      </Card>}
    </Space>
  </Card>;
}
