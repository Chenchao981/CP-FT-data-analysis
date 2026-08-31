import { useMutation } from "@tanstack/react-query";
import { Alert, Button, Card, Col, Empty, Input, Row, Select, Space, Table, Tag, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useMemo, useState } from "react";

import {
  evaluateAnalyticsInstantRisk,
  type AnalyticsContextRequest,
  type AnalyticsEvaluatedRiskItem,
  type AnalyticsRiskAnalysis,
  type AnalyticsRiskEvaluationConfig,
  type AnalyticsRiskGroupBy,
} from "../../api/analytics";
import { ApiError } from "../../api/auth";
import type { ExactRuleState, OverviewRiskViewConfig } from "./context/analysisViewConfig";
import type { AnalyticsAggregateDrilldown } from "./sections/sectionTypes";

const ANALYSES: Array<{ value: AnalyticsRiskAnalysis; label: string }> = [
  { value: "CAPABILITY", label: "Cpk / Ppk" },
  { value: "PAT_ROBUST_IQR", label: "PAT" },
  { value: "SPC_I_MR", label: "SPC I-MR" },
  { value: "MARGIN_OOS", label: "Spec Margin" },
  { value: "SBL_GROUPED_LIMIT", label: "SBL" },
  { value: "SYL_GROUPED_LIMIT", label: "SYL" },
];
const GROUPS: AnalyticsRiskGroupBy[] = ["DATASET", "LOT", "WAFER", "RUN", "TESTER", "PROGRAM", "CONDITION"];

interface OverviewInstantRiskPanelProps {
  context: AnalyticsContextRequest;
  parameterOptions: readonly string[];
  config: OverviewRiskViewConfig;
  onConfigChange: (patch: Partial<OverviewRiskViewConfig>) => void;
  onOpenDrilldown: (drilldownKey: string) => void;
  onOpenAggregateDrilldown: (target: AnalyticsAggregateDrilldown) => void;
}

const exact = (rule: ExactRuleState) => ({ rule_code: rule.ruleCode, version_code: rule.versionCode });
const percent = (value: number | null) => value == null ? "—" : `${(value * 100).toFixed(3)}%`;
const number = (value: number | null) => value == null ? "—" : value.toPrecision(6);

function buildEvaluations(config: OverviewRiskViewConfig): AnalyticsRiskEvaluationConfig[] {
  return config.analyses.map((analysis) => {
    if (analysis === "CAPABILITY") return {
      analysis,
      parameter: config.parameter,
      capability_method: config.capability.method,
      rule: exact(config.capability),
    };
    if (analysis === "PAT_ROBUST_IQR") return {
      analysis, parameter: config.parameter, group_by: config.groupBy, rule: exact(config.pat),
    };
    if (analysis === "SPC_I_MR") return {
      analysis, parameter: config.parameter, group_by: config.groupBy, rule: exact(config.spc),
      spc_order: "UNIT_SEQUENCE", spc_phase: "PHASE_I_BASELINE",
    };
    if (analysis === "MARGIN_OOS") return {
      analysis, parameter: config.parameter, group_by: config.groupBy, rule: exact(config.margin),
    };
    if (analysis === "SBL_GROUPED_LIMIT") return {
      analysis, group_by: config.groupBy, bin_type: config.sbl.binType, rule: exact(config.sbl),
    };
    return { analysis, group_by: config.groupBy, rule: exact(config.syl) };
  });
}

const referencesFor = (config: OverviewRiskViewConfig): ExactRuleState[] => config.analyses.map((analysis) => {
  if (analysis === "CAPABILITY") return config.capability;
  if (analysis === "PAT_ROBUST_IQR") return config.pat;
  if (analysis === "SPC_I_MR") return config.spc;
  if (analysis === "MARGIN_OOS") return config.margin;
  if (analysis === "SBL_GROUPED_LIMIT") return config.sbl;
  return config.syl;
});

export function OverviewInstantRiskPanel({ context, parameterOptions, config, onConfigChange, onOpenDrilldown, onOpenAggregateDrilldown }: OverviewInstantRiskPanelProps) {
  const [evaluatedSignature, setEvaluatedSignature] = useState("");
  const [attemptedSignature, setAttemptedSignature] = useState("");
  const [selectedEvidence, setSelectedEvidence] = useState<{ title: string; keys: string[]; truncated: boolean } | null>(null);
  const evaluations = useMemo(() => buildEvaluations(config), [config]);
  const signature = JSON.stringify({ context, evaluations });
  const selected = new Set(config.analyses);
  const requiresParameter = config.analyses.some((analysis) => ["CAPABILITY", "PAT_ROBUST_IQR", "SPC_I_MR", "MARGIN_OOS"].includes(analysis));
  const exactRulesComplete = referencesFor(config).every((rule) => Boolean(rule.ruleCode && rule.versionCode));
  const groupContractValid = config.groupBy !== "CONDITION" || !config.analyses.some((analysis) => analysis === "SBL_GROUPED_LIMIT" || analysis === "SYL_GROUPED_LIMIT");
  const runnable = config.analyses.length > 0 && (!requiresParameter || Boolean(config.parameter)) && exactRulesComplete && groupContractValid;
  const mutation = useMutation({
    mutationFn: () => evaluateAnalyticsInstantRisk({ ...context, evaluations }),
    onSuccess: () => setEvaluatedSignature(signature),
  });
  const result = evaluatedSignature === signature ? mutation.data : undefined;
  const currentError = attemptedSignature === signature ? mutation.error : null;
  const errorDescription = currentError instanceof ApiError
    ? `${currentError.code} · ${currentError.message}${currentError.recommendedAction ? ` · ${currentError.recommendedAction}` : ""}`
    : currentError?.message;
  const updateRule = (field: "capability" | "pat" | "spc" | "margin" | "sbl" | "syl", patch: Partial<ExactRuleState>) => onConfigChange({
    ...config,
    [field]: { ...config[field], ...patch },
  });
  const ruleFields = (label: string, field: "capability" | "pat" | "spc" | "margin" | "sbl" | "syl") => <>
    <Col xs={24} md={6}><Typography.Text strong>{label} Rule Code</Typography.Text><Input aria-label={`${label} Risk Rule Code`} value={config[field].ruleCode} onChange={(event) => updateRule(field, { ruleCode: event.target.value.toUpperCase() })} /></Col>
    <Col xs={24} md={6}><Typography.Text strong>{label} Version</Typography.Text><Input aria-label={`${label} Risk Rule Version`} value={config[field].versionCode} onChange={(event) => updateRule(field, { versionCode: event.target.value })} /></Col>
  </>;
  const columns: ColumnsType<AnalyticsEvaluatedRiskItem> = [
    { title: "状态", dataIndex: "status", width: 95, render: (value) => <Tag color={value === "ACTIVE" ? "warning" : value === "CLEAR" ? "success" : "default"}>{value}</Tag> },
    { title: "分析", dataIndex: "analysis", width: 145 },
    { title: "Dataset / Group", width: 230, render: (_, row) => `#${row.dataset_id}/V${row.version_no} · ${row.group_key}` },
    { title: "参数", dataIndex: "parameter", width: 130, render: (value) => value ?? "—" },
    { title: "指标", width: 185, render: (_, row) => `${row.metric_code}: ${number(row.metric_value)}` },
    { title: "批准判定", width: 145, render: (_, row) => row.threshold_operator ? `${row.threshold_operator} ${number(row.threshold_value)}` : "—" },
    { title: "影响 / 分母", width: 125, render: (_, row) => `${row.affected_count} / ${row.denominator_count}` },
    { title: "比例", dataIndex: "rate", width: 100, render: percent },
    { title: "Rule Provenance", width: 300, render: (_, row) => <Space direction="vertical" size={0}><Typography.Text code>{row.rule.rule_code}@{row.rule.version_code}</Typography.Text><Typography.Text type="secondary">{row.rule.algorithm_code} · {row.rule.parameters_sha256.slice(0, 12)}…</Typography.Text><Space size={4}><Tag color="success">{row.rule.approval_status}</Tag><Tag color="success">{row.rule.activation_status}</Tag></Space></Space> },
    { title: "原因", dataIndex: "reason_code", width: 190, render: (value) => value ?? "—" },
    { title: "证据下钻", width: 150, render: (_, row) => row.aggregate_drilldown_context
      ? <Button size="small" onClick={() => {
          const aggregate = row.aggregate_drilldown_context!;
          onOpenAggregateDrilldown({
            dataset: { dataset_id: aggregate.dataset_id, version_no: aggregate.version_no },
            filters: {},
            parameters: [aggregate.parameter],
            measurementFilter: {
              parameter: aggregate.parameter,
              lower_bound: aggregate.lower_bound,
              upper_bound: aggregate.upper_bound,
              lower_inclusive: aggregate.lower_inclusive,
              upper_inclusive: aggregate.upper_inclusive,
            },
          });
        }}>受影响总体</Button>
      : row.evidence_drilldown_keys.length
        ? <Button size="small" onClick={() => setSelectedEvidence({ title: `${row.title} 证据 Unit`, keys: row.evidence_drilldown_keys, truncated: row.evidence_truncated })}>证据成员 {row.evidence_drilldown_keys.length}{row.evidence_truncated ? "+" : ""}</Button>
        : "—" },
  ];

  return <Card title="即时统计风险（显式执行）" extra={<Tag>ANALYTICS_INSTANT_RISK_V1</Tag>}>
    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
      <Alert type="info" showIcon message="不会在进入 Overview 时自动运行" description="仅在选择分析方法、参数及已批准的 exact Rule Code + Version 后点击执行。前端不选择阈值、不重算风险；Capability 风险指标和阈值必须写入获批 CPK Rule。" />
      <Row gutter={[12, 12]}>
        <Col xs={24} md={12}><Typography.Text strong>风险分析方法</Typography.Text><Select aria-label="Overview Risk Analyses" mode="multiple" value={[...config.analyses]} options={ANALYSES} onChange={(values) => onConfigChange({ ...config, analyses: values })} className="full-width" /></Col>
        {requiresParameter && <Col xs={24} md={6}><Typography.Text strong>风险参数</Typography.Text><Select aria-label="Overview Risk Parameter" showSearch value={config.parameter || undefined} options={Array.from(new Set([config.parameter, ...parameterOptions].filter(Boolean))).map((value) => ({ label: value, value }))} onChange={(parameter) => onConfigChange({ ...config, parameter })} className="full-width" /></Col>}
        {config.analyses.some((analysis) => analysis !== "CAPABILITY") && <Col xs={24} md={6}><Typography.Text strong>Quality Group By</Typography.Text><Select aria-label="Overview Risk Group By" value={config.groupBy} options={GROUPS.map((value) => ({ label: value, value }))} onChange={(groupBy) => onConfigChange({ ...config, groupBy })} className="full-width" /></Col>}
        {selected.has("CAPABILITY") && <><Col xs={24} md={6}><Typography.Text strong>Capability Method</Typography.Text><Select aria-label="Overview Capability Method" value={config.capability.method} options={["CPK_POOLED_WITHIN_RUN_V1", "CPK_POOLED_WITHIN_LOT_WAFER_V1"].map((value) => ({ label: value, value }))} onChange={(method) => onConfigChange({ ...config, capability: { ...config.capability, method } })} className="full-width" /></Col>{ruleFields("Capability", "capability")}</>}
        {selected.has("PAT_ROBUST_IQR") && ruleFields("PAT", "pat")}
        {selected.has("SPC_I_MR") && ruleFields("SPC", "spc")}
        {selected.has("MARGIN_OOS") && ruleFields("Margin", "margin")}
        {selected.has("SBL_GROUPED_LIMIT") && <><Col xs={24} md={6}><Typography.Text strong>SBL Bin Type</Typography.Text><Select aria-label="Overview SBL Bin Type" value={config.sbl.binType} options={["CP_BIN", "SOFT_BIN", "HARD_BIN"].map((value) => ({ label: value, value }))} onChange={(binType) => onConfigChange({ ...config, sbl: { ...config.sbl, binType } })} className="full-width" /></Col>{ruleFields("SBL", "sbl")}</>}
        {selected.has("SYL_GROUPED_LIMIT") && ruleFields("SYL", "syl")}
      </Row>
      <Space wrap><Button type="primary" disabled={!runnable} loading={mutation.isPending && attemptedSignature === signature} onClick={() => { setAttemptedSignature(signature); mutation.mutate(); }}>执行即时风险评估</Button><Typography.Text type="secondary">当前 Context / exact Rule / 参数任一变化后，旧结果立即隐藏，必须重新执行。</Typography.Text></Space>
      {!runnable && config.analyses.length > 0 && <Alert type="warning" showIcon message="风险请求尚不完整" description={groupContractValid ? "请补齐所选方法需要的参数及每个 exact Rule Code + Version。" : "SBL / SYL 不允许使用 CONDITION 分组，请选择可追溯的物理子组。"} />}
      {currentError && <Alert type="error" showIcon message="即时风险评估失败（失败关闭）" description={errorDescription} />}
      {result?.warnings.length ? <Alert type="warning" showIcon message="服务端风险提示" description={result.warnings.join("、")} /> : null}
      {result ? <><Space wrap><Tag>Context {result.filter_summary.context_hash.slice(0, 12)}…</Tag><Tag>Calculation {result.calculation_context_hash.slice(0, 12)}…</Tag><Tag>{result.requested_analyses.join(" / ")}</Tag></Space>{result.items.length ? <Table rowKey="code" columns={columns} dataSource={result.items} pagination={false} size="small" scroll={{ x: 1750 }} /> : <Empty description="批准规则计算完成，当前 Context 无风险分组结果" />}</> : <Empty description="尚未显式执行即时风险评估" />}
      {selectedEvidence && <Card size="small" title={selectedEvidence.title} extra={<Button size="small" onClick={() => setSelectedEvidence(null)}>关闭</Button>}>
        {selectedEvidence.truncated && <Alert type="warning" showIcon message="服务端返回的是有界证据集，不声称代表全部受影响总体" style={{ marginBottom: 8 }} />}
        <Table rowKey="key" size="small" pagination={{ pageSize: 25 }} dataSource={selectedEvidence.keys.map((key) => ({ key }))} columns={[{ title: "Unit Key", dataIndex: "key" }, { title: "操作", render: (_, row: { key: string }) => <Button size="small" onClick={() => onOpenDrilldown(row.key)}>打开 Drawer</Button> }]} />
      </Card>}
    </Space>
  </Card>;
}

export default OverviewInstantRiskPanel;
