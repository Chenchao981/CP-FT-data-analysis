import { PlayCircleOutlined } from "@ant-design/icons";
import { useMutation } from "@tanstack/react-query";
import { Alert, Button, Card, Col, Descriptions, Empty, Row, Select, Space, Statistic, Table, Tag, Typography, Input } from "antd";
import type { EChartsCoreOption } from "echarts/core";
import { useMemo, useState } from "react";

import { ApiError } from "../../api/auth";
import {
  evaluateQuality,
  type QualityAnalysisType,
  type QualityBinCooccurrenceCell,
  type QualityBinType,
  type QualityEvaluationRequest,
  type QualityEvaluationResult,
  type QualityGroupBy,
  type QualityMarginPoint,
} from "../../api/qualityEvaluation";
import { EChart, type EChartEventMap } from "../../components/EChart";
import { drilldownKeyFromChartEvent } from "./chartDrilldown";
import { ANALYSIS_COMPONENT_DEFAULTS, type QualityAnalysisViewConfig } from "./context/analysisViewConfig";
import {
  cooccurrenceHeatmapOption,
  cooccurrenceParetoOption,
  cooccurrenceScopeKey,
  marginDistributionOption,
  sblParetoOption,
  sblTrendOption,
  spcIMrOption,
  sylTrendOption,
  type QualityPercentAxisMode,
} from "./qualityVisuals";
import type { AnalyticsDrilldownOpener, AnalyticsSectionContext } from "./sections/sectionTypes";

export interface QualityEvaluationPanelProps extends AnalyticsSectionContext, AnalyticsDrilldownOpener {
  config?: QualityAnalysisViewConfig;
  onConfigChange?: (patch: Partial<QualityAnalysisViewConfig>) => void;
}

const analysisOptions: Array<{ label: string; value: QualityAnalysisType }> = [
  { label: "PAT Robust IQR", value: "PAT_ROBUST_IQR" },
  { label: "SPC I-MR", value: "SPC_I_MR" },
  { label: "Spec Margin / OOS", value: "MARGIN_OOS" },
  { label: "Bin Co-occurrence", value: "BIN_COOCCURRENCE" },
  { label: "SBL Grouped Limit", value: "SBL_GROUPED_LIMIT" },
  { label: "SYL Grouped Limit", value: "SYL_GROUPED_LIMIT" },
  { label: "Pass / Fail Distribution", value: "PASS_FAIL_DISTRIBUTION" },
];
const groupOptions: Array<{ label: string; value: QualityGroupBy }> = ["DATASET", "LOT", "WAFER", "RUN", "TESTER", "PROGRAM", "CONDITION"].map((value) => ({ label: value, value: value as QualityGroupBy }));
const binOptions: Array<{ label: string; value: QualityBinType }> = ["CP_BIN", "SOFT_BIN", "HARD_BIN", "ALL_MAPPED_FAILURE"].map((value) => ({ label: value, value: value as QualityBinType }));
const parameterMethods = new Set<QualityAnalysisType>(["PAT_ROBUST_IQR", "SPC_I_MR", "MARGIN_OOS", "PASS_FAIL_DISTRIBUTION"]);
const binMethods = new Set<QualityAnalysisType>(["BIN_COOCCURRENCE", "SBL_GROUPED_LIMIT"]);
const ruleCodePattern = /^[A-Z][A-Z0-9_]{2,127}$/;
const versionPattern = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/;
const percent = (value: number | null) => value == null ? "—" : `${(value * 100).toFixed(3)}%`;
const numeric = (value: number | null) => value == null ? "—" : String(value);
const validDrilldownKey = (value: string) => /^UNIT:[1-9][0-9]{0,18}$/.test(value);

function cloneRequestContext(request: Pick<QualityEvaluationRequest, "datasets" | "filters">) {
  return {
    datasets: request.datasets.map((item) => ({ ...item })),
    filters: {
      lot_ids: [...request.filters.lot_ids], wafer_ids: [...request.filters.wafer_ids], bin_codes: [...request.filters.bin_codes],
      overall_results: [...request.filters.overall_results], source_ids: [...request.filters.source_ids], tester_ids: [...request.filters.tester_ids],
      program_versions: [...request.filters.program_versions], test_conditions: [...request.filters.test_conditions],
    },
  };
}

function firstMarginPoints(result: QualityEvaluationResult, groupKey?: string, limit = 200): QualityMarginPoint[] {
  return result.margin
    .filter((group) => !groupKey || `${group.dataset_id}:${group.version_no}:${group.group_key}` === groupKey)
    .flatMap((group) => group.points)
    .sort((left, right) => Number(right.out_of_spec) - Number(left.out_of_spec) || left.nearest_margin - right.nearest_margin || left.measurement_id - right.measurement_id)
    .slice(0, limit);
}

const qualityGroupKey = (datasetId: number, versionNo: number, groupKey: string) => `${datasetId}:${versionNo}:${groupKey}`;

export function QualityEvaluationPanel({ context, overview, overviewError, onOpenDrilldown, config: controlledConfig, onConfigChange: controlledOnConfigChange }: QualityEvaluationPanelProps) {
  const [localConfig, setLocalConfig] = useState<QualityAnalysisViewConfig>(() => ({
    ...ANALYSIS_COMPONENT_DEFAULTS.quality,
    rule: { ...ANALYSIS_COMPONENT_DEFAULTS.quality.rule },
  }));
  const config = controlledConfig ?? localConfig;
  const onConfigChange = (patch: Partial<QualityAnalysisViewConfig>) => {
    if (!controlledConfig) setLocalConfig((current) => ({ ...current, ...patch }));
    controlledOnConfigChange?.(patch);
  };
  const analysis = config.analysis ?? undefined;
  const groupBy = config.groupBy ?? undefined;
  const parameter = config.parameter || undefined;
  const ruleCode = config.rule.ruleCode;
  const ruleVersion = config.rule.versionCode;
  const spcOrder = config.spcOrder ?? undefined;
  const spcPhase = config.spcPhase ?? undefined;
  const binType = config.binType ?? undefined;
  const [submittedSignature, setSubmittedSignature] = useState<string>();
  const [selectedMembers, setSelectedMembers] = useState<{ title: string; keys: string[] } | null>(null);
  const spcDisplayGroup = config.spcDisplayGroup || undefined;
  const distributionDisplayGroup = config.distributionDisplayGroup || undefined;
  const marginDisplayGroup = config.marginDisplayGroup || undefined;
  const cooccurrenceDisplayGroup = config.cooccurrenceDisplayGroup || undefined;
  const sblDisplayBin = config.sblDisplayBin || undefined;
  const sylDisplayDataset = config.sylDisplayDataset || undefined;
  const percentAxisMode = config.percentAxisMode as QualityPercentAxisMode;
  const parameterOptions = useMemo(() => Array.from(new Set([...(overview?.options.parameters ?? []), ...context.parameters])).sort().map((value) => ({ label: value, value })), [context.parameters, overview?.options.parameters]);
  const needsParameter = analysis !== undefined && parameterMethods.has(analysis);
  const needsBin = analysis !== undefined && binMethods.has(analysis);
  const request = useMemo<QualityEvaluationRequest | null>(() => analysis && groupBy ? {
    ...cloneRequestContext(context),
    parameters: needsParameter && parameter ? [parameter] : [],
    analysis,
    rule: { rule_code: ruleCode.trim(), version_code: ruleVersion.trim() },
    group_by: groupBy,
    spc_order: analysis === "SPC_I_MR" ? spcOrder ?? null : null,
    spc_phase: analysis === "SPC_I_MR" ? spcPhase ?? null : null,
    bin_type: needsBin ? binType ?? null : null,
  } : null, [analysis, binType, context, groupBy, needsBin, needsParameter, parameter, ruleCode, ruleVersion, spcOrder, spcPhase]);
  const requestSignature = request ? JSON.stringify(request) : "";
  const inputValid = request !== null
    && ruleCodePattern.test(ruleCode.trim())
    && versionPattern.test(ruleVersion.trim())
    && (!needsParameter || Boolean(parameter))
    && (!needsBin || Boolean(binType))
    && !(needsBin && groupBy === "CONDITION")
    && !(analysis === "SYL_GROUPED_LIMIT" && groupBy === "CONDITION")
    && !(analysis === "SBL_GROUPED_LIMIT" && binType === "ALL_MAPPED_FAILURE")
    && (analysis !== "SPC_I_MR" || (spcOrder === "UNIT_SEQUENCE" && spcPhase === "PHASE_I_BASELINE"));
  const mutation = useMutation({ mutationFn: evaluateQuality });
  const apiError = mutation.error instanceof ApiError ? mutation.error : null;
  const result = mutation.data && submittedSignature === requestSignature ? mutation.data : undefined;
  const stale = Boolean(mutation.data && submittedSignature !== requestSignature);
  const approvalGate = apiError?.code === "ANALYSIS_RULE_NOT_APPROVED";
  const noDeclaredRules = (overview?.rule_context.evaluation_rule_versions.length ?? 0) === 0;
  const run = () => {
    if (!request || !inputValid) return;
    setSubmittedSignature(requestSignature);
    mutation.mutate(request);
  };
  const changeAnalysis = (value: QualityAnalysisType | undefined) => {
    onConfigChange({ analysis: value ?? null, parameter: "", binType: null, spcOrder: null, spcPhase: null });
  };
  const drillButtons = (keys: readonly string[]) => {
    const members = Array.from(new Set(keys.filter(validDrilldownKey)));
    return members.length
      ? <Button type="link" size="small" onClick={() => setSelectedMembers({ title: "聚合成员 Unit", keys: members })}>成员 {members.length}</Button>
      : "—";
  };

  const activeSpc = result?.spc.find((item) => qualityGroupKey(item.dataset_id, item.version_no, item.group_key) === spcDisplayGroup) ?? result?.spc[0];
  const visibleSpcPoints = activeSpc?.points ?? [];
  const spcRuleHitPoints = activeSpc?.points.filter((item) => item.rule_hits.length > 0) ?? [];
  const spcOption = useMemo<EChartsCoreOption>(() => spcIMrOption(activeSpc), [activeSpc]);
  const chartEvents = useMemo<EChartEventMap>(() => ({ click: (payload) => {
    const keys = (payload as { data?: { drilldownKeys?: unknown } })?.data?.drilldownKeys;
    if (Array.isArray(keys)) {
      const members = Array.from(new Set(keys.filter((key): key is string => typeof key === "string" && validDrilldownKey(key))));
      if (members.length) setSelectedMembers({ title: "图表聚合成员 Unit", keys: members });
      return;
    }
    const key = drilldownKeyFromChartEvent(payload);
    if (key) onOpenDrilldown(key);
  } }), [onOpenDrilldown]);
  const activeDistribution = result?.pass_fail_distribution.find((item) => qualityGroupKey(item.dataset_id, item.version_no, item.group_key) === distributionDisplayGroup) ?? result?.pass_fail_distribution[0];
  const distributionOption = useMemo<EChartsCoreOption>(() => ({
    animation: false,
    tooltip: { trigger: "axis" },
    legend: { data: ["PASS", "FAIL"] },
    grid: { left: 64, right: 32, top: 48, bottom: 72 },
    xAxis: { type: "category", name: "服务端分箱", data: activeDistribution?.bins.map((item) => `${item.lower}–${item.upper}`) ?? [] },
    yAxis: { type: "value", name: "Unit Count", minInterval: 1 },
    dataZoom: [{ type: "inside" }, { type: "slider", bottom: 16 }],
    brush: { toolbox: ["rect", "clear"], xAxisIndex: "all" },
    toolbox: { feature: { dataZoom: {}, restore: {}, saveAsImage: { name: "pass-fail-distribution" } } },
    series: [
      { name: "PASS", type: "bar", data: activeDistribution?.bins.map((item) => ({ value: item.pass_count, drilldownKeys: item.pass_drilldown_keys })) ?? [] },
      { name: "FAIL", type: "bar", data: activeDistribution?.bins.map((item) => ({ value: item.fail_count, drilldownKeys: item.fail_drilldown_keys })) ?? [] },
    ],
  }), [activeDistribution]);
  const marginGroups = result?.margin ?? [];
  const activeMargin = marginGroups.find((item) => `${item.dataset_id}:${item.version_no}:${item.group_key}` === marginDisplayGroup) ?? marginGroups[0];
  const activeMarginKey = activeMargin ? `${activeMargin.dataset_id}:${activeMargin.version_no}:${activeMargin.group_key}` : undefined;
  const marginOption = useMemo<EChartsCoreOption>(() => marginDistributionOption(activeMargin), [activeMargin]);
  const cooccurrenceGroups = useMemo(() => {
    const grouped = new Map<string, QualityBinCooccurrenceCell[]>();
    for (const cell of result?.bin_cooccurrence ?? []) {
      const key = cooccurrenceScopeKey(cell);
      grouped.set(key, [...(grouped.get(key) ?? []), cell]);
    }
    return grouped;
  }, [result]);
  const activeCooccurrenceKey = cooccurrenceGroups.has(cooccurrenceDisplayGroup ?? "") ? cooccurrenceDisplayGroup! : cooccurrenceGroups.keys().next().value as string | undefined;
  const activeCooccurrence = activeCooccurrenceKey ? cooccurrenceGroups.get(activeCooccurrenceKey) ?? [] : [];
  const cooccurrenceHeatmap = useMemo<EChartsCoreOption>(() => cooccurrenceHeatmapOption(activeCooccurrence, percentAxisMode), [activeCooccurrence, percentAxisMode]);
  const cooccurrencePareto = useMemo<EChartsCoreOption>(() => cooccurrenceParetoOption(activeCooccurrence), [activeCooccurrence]);
  const sblLimits = result?.sbl ?? [];
  const activeSbl = sblLimits.find((item) => `${item.dataset_id}:${item.version_no}:${item.bin_code}` === sblDisplayBin) ?? sblLimits[0];
  const sblTrend = useMemo<EChartsCoreOption>(() => sblTrendOption(activeSbl, percentAxisMode), [activeSbl, percentAxisMode]);
  const sblPareto = useMemo<EChartsCoreOption>(() => sblParetoOption(sblLimits), [sblLimits]);
  const sylLimits = result?.syl ?? [];
  const activeSyl = sylLimits.find((item) => `${item.dataset_id}:${item.version_no}` === sylDisplayDataset) ?? sylLimits[0];
  const sylTrend = useMemo<EChartsCoreOption>(() => sylTrendOption(activeSyl, percentAxisMode), [activeSyl, percentAxisMode]);

  if (overviewError) return <Alert type="error" showIcon message="Quality Context 加载失败" description={overviewError.message} />;
  return <Space direction="vertical" size="large" style={{ width: "100%" }}>
    <Card title="Quality Evaluation（批准规则 / 显式执行）">
      <Space direction="vertical" size="middle" style={{ width: "100%" }}>
        <Alert type="info" showIcon message="前端不计算 Quality 算法" description="PAT / SPC / Margin / Bin / SBL / SYL / Pass-Fail Distribution 均由服务端按精确批准 Rule 执行；没有批准并激活的版本时必须失败关闭。" />
        {noDeclaredRules && <Alert type="warning" showIcon message="当前 Context 未声明已批准 Quality Rule" description="规则字段保持空白且无默认值。显式执行后，服务端零审批环境会返回 ANALYSIS_RULE_NOT_APPROVED；输入一个名称不会使规则变成已批准。" />}
        <Row gutter={[12, 12]}>
          <Col xs={24} sm={12} lg={8}><Typography.Text strong>方法</Typography.Text><Select aria-label="Quality 方法" allowClear value={analysis} onChange={changeAnalysis} options={analysisOptions} placeholder="显式选择方法" className="full-width" /></Col>
          <Col xs={24} sm={12} lg={8}><Typography.Text strong>Group By</Typography.Text><Select aria-label="Quality Group By" allowClear value={groupBy} onChange={(value) => onConfigChange({ groupBy: value ?? null })} options={groupOptions.map((item) => ({ ...item, disabled: (needsBin || analysis === "SYL_GROUPED_LIMIT") && item.value === "CONDITION" }))} placeholder="显式选择分组" className="full-width" /></Col>
          {needsParameter && <Col xs={24} sm={12} lg={8}><Typography.Text strong>精确参数</Typography.Text><Select aria-label="Quality 参数" allowClear showSearch value={parameter} onChange={(value) => onConfigChange({ parameter: value ?? "" })} options={parameterOptions} placeholder="恰好选择一个参数" className="full-width" /></Col>}
          <Col xs={24} sm={12} lg={8}><Typography.Text strong>Rule Code</Typography.Text><Input aria-label="Quality Rule Code" value={ruleCode} onChange={(event) => onConfigChange({ rule: { ...config.rule, ruleCode: event.target.value.toUpperCase() } })} placeholder="不提供默认规则" maxLength={128} /></Col>
          <Col xs={24} sm={12} lg={8}><Typography.Text strong>Rule Version</Typography.Text><Input aria-label="Quality Rule Version" value={ruleVersion} onChange={(event) => onConfigChange({ rule: { ...config.rule, versionCode: event.target.value } })} placeholder="精确版本" maxLength={64} /></Col>
          {analysis === "SPC_I_MR" && <>
            <Col xs={24} sm={12} lg={8}><Typography.Text strong>Order</Typography.Text><Select aria-label="SPC Order" allowClear value={spcOrder} onChange={(value) => onConfigChange({ spcOrder: value ?? null })} options={[{ label: "UNIT_SEQUENCE", value: "UNIT_SEQUENCE" }]} placeholder="显式选择" className="full-width" /></Col>
            <Col xs={24} sm={12} lg={8}><Typography.Text strong>Phase</Typography.Text><Select aria-label="SPC Phase" allowClear value={spcPhase} onChange={(value) => onConfigChange({ spcPhase: value ?? null })} options={[{ label: "PHASE_I_BASELINE", value: "PHASE_I_BASELINE" }]} placeholder="显式选择" className="full-width" /></Col>
          </>}
          {needsBin && <Col xs={24} sm={12} lg={8}><Typography.Text strong>Bin Type</Typography.Text><Select aria-label="Quality Bin Type" allowClear value={binType} onChange={(value) => onConfigChange({ binType: value ?? null })} options={binOptions.map((item) => ({ ...item, disabled: analysis === "SBL_GROUPED_LIMIT" && item.value === "ALL_MAPPED_FAILURE" }))} placeholder="显式选择物理 Bin" className="full-width" /></Col>}
        </Row>
        <Space wrap><Button type="primary" icon={<PlayCircleOutlined />} disabled={!inputValid} loading={mutation.isPending} onClick={run}>执行 Quality 分析</Button><Typography.Text type="secondary">所有筛选仍来自统一 Context；Quality 参数是该方法的唯一参数身份。</Typography.Text></Space>
        {stale && <Alert type="warning" showIcon message="输入已改变，旧 Quality 结果已隐藏" description="请重新显式执行；前端不会把旧规则/旧分组结果套到新 Context。" />}
        {approvalGate && <Alert type="error" showIcon message="Quality Rule 未批准或未激活" description={<Space direction="vertical"><Typography.Text>错误码：ANALYSIS_RULE_NOT_APPROVED</Typography.Text><Typography.Text>{apiError?.message}</Typography.Text>{apiError?.recommendedAction && <Typography.Text>建议：{apiError.recommendedAction}</Typography.Text>}</Space>} />}
        {mutation.isError && !approvalGate && <Alert type="error" showIcon message="Quality 分析失败" description={<Space direction="vertical"><Typography.Text>{mutation.error.message}</Typography.Text>{apiError?.code && <Typography.Text>错误码：{apiError.code}</Typography.Text>}</Space>} />}
      </Space>
    </Card>

    {result && <>
      <Card title={`${result.analysis} · 服务端权威结果`}>
        <Space direction="vertical" size="middle" style={{ width: "100%" }}>
          <Space wrap>
            <Tag color="blue">{result.contract_version}</Tag><Tag>Calculation {result.calculation_context_hash.slice(0, 12)}…</Tag>
            <Tag color={result.rule.approval_status === "APPROVED" ? "success" : "error"}>{result.rule.approval_status}</Tag>
            <Tag color={result.rule.activation_status === "ENABLED" ? "success" : "error"}>{result.rule.activation_status}</Tag>
          </Space>
          <Descriptions size="small" column={{ xs: 1, sm: 2, lg: 4 }} items={[
            { key: "rule", label: "Rule", children: `${result.rule.rule_code}@${result.rule.version_code}` },
            { key: "algorithm", label: "Algorithm", children: result.rule.algorithm_code },
            { key: "sha", label: "Parameters SHA-256", children: <Typography.Text code copyable>{result.rule.parameters_sha256}</Typography.Text> },
            { key: "parameter", label: "Parameter Identity", children: result.parameter_identity ? `${result.parameter_identity.canonical_parameter_code ?? result.parameter_identity.name} / ${result.parameter_identity.step_code}#${result.parameter_identity.sequence_no} / ${result.parameter_identity.unit ?? "unit unknown"}` : "Bin method" },
          ]} />
          <Row gutter={[12, 12]}>
            <Col xs={12} md={6}><Statistic title="Input Units" value={result.counts.input_units} /></Col>
            <Col xs={12} md={6}><Statistic title="Included Units" value={result.counts.included_units} /></Col>
            <Col xs={12} md={6}><Statistic title="Included Measurements" value={result.counts.included_measurements} /></Col>
            <Col xs={12} md={6}><Statistic title="Missing" value={result.counts.missing_measurements} /></Col>
          </Row>
          {result.capabilities.map((item) => <Alert key={item.code} type={item.status === "AVAILABLE" ? "success" : "warning"} showIcon message={`${item.code} · ${item.status}`} description={item.reason_code ?? item.message ?? undefined} />)}
          <Alert type={result.sampling_summary.sampled ? "warning" : "info"} showIcon message={result.sampling_summary.sampled ? "服务端已执行确定性绘图采样" : "服务端未采样绘图点"} description={`方法 ${result.sampling_summary.method ?? "NONE"}；返回 ${result.sampling_summary.returned_points} / 原始 ${result.sampling_summary.original_points}；保留 OOS / Rule Hit ${result.sampling_summary.preserved_out_of_spec_points}。`} />
          {result.warnings.map((warning) => <Alert key={warning} type="warning" showIcon message={warning} />)}
        </Space>
      </Card>

      {result.analysis === "PAT_ROBUST_IQR" && <Card title="PAT Robust IQR Groups"><Table rowKey={(row) => `${row.dataset_id}:${row.version_no}:${row.group_key}`} pagination={false} scroll={{ x: 1400 }} dataSource={result.pat} columns={[
        { title: "Group", dataIndex: "group_key", key: "group", fixed: "left", width: 260 },
        { title: "Valid / Missing", key: "n", render: (_, row) => `${row.valid_n} / ${row.missing_n}` },
        { title: "Q1 / Median / Q3", key: "quartiles", render: (_, row) => `${numeric(row.q1)} / ${numeric(row.median)} / ${numeric(row.q3)}` },
        { title: "IQR / Robust Sigma", key: "spread", render: (_, row) => `${numeric(row.iqr)} / ${numeric(row.robust_sigma)}` },
        { title: "Lower / Upper", key: "limits", render: (_, row) => `${numeric(row.lower_limit)} / ${numeric(row.upper_limit)}` },
        { title: "Outlier", key: "outlier", render: (_, row) => `${row.outlier_count} / ${percent(row.outlier_rate)}` },
        { title: "Status", dataIndex: "status", key: "status", render: (value) => <Tag>{value}</Tag> },
        { title: "Evidence", key: "evidence", render: (_, row) => drillButtons(row.evidence.map((item) => item.drilldown_key)) },
      ]} /></Card>}

      {result.analysis === "SPC_I_MR" && <Card title="SPC I-MR Groups"><Space direction="vertical" style={{ width: "100%" }}>
        <Table rowKey={(row) => `${row.dataset_id}:${row.version_no}:${row.group_key}`} pagination={false} scroll={{ x: 1200 }} dataSource={result.spc} columns={[
          { title: "Group", dataIndex: "group_key", key: "group", width: 260 }, { title: "Valid / Missing", key: "n", render: (_, row) => `${row.valid_n} / ${row.missing_n}` },
          { title: "CL", dataIndex: "center_line", key: "cl", render: numeric }, { title: "LCL / UCL", key: "limits", render: (_, row) => `${numeric(row.lower_control_limit)} / ${numeric(row.upper_control_limit)}` },
          { title: "MR Bar / MR UCL", key: "mr", render: (_, row) => `${numeric(row.mr_bar)} / ${numeric(row.mr_upper_control_limit)}` }, { title: "Boundary Reset", dataIndex: "boundary_reset", key: "reset", render: (value) => value ? "YES" : "NO" }, { title: "Status", dataIndex: "status", key: "status" },
        ]} />
        {result.spc.length ? <><Select aria-label="SPC 显示 Group" value={activeSpc ? qualityGroupKey(activeSpc.dataset_id, activeSpc.version_no, activeSpc.group_key) : undefined} onChange={(value) => onConfigChange({ spcDisplayGroup: value })} options={result.spc.map((item) => ({ label: `#${item.dataset_id} v${item.version_no} · ${item.group_key}`, value: qualityGroupKey(item.dataset_id, item.version_no, item.group_key) }))} style={{ width: "100%", maxWidth: 720 }} />
          {activeSpc && <><Typography.Text type="secondary">I 图与 MR 图直接绘制服务端返回点：{visibleSpcPoints.length} / 原始 {activeSpc.sampling_summary.original_points}；服务端方法 {activeSpc.sampling_summary.method ?? "NONE"}，全部 Rule Hits 已保留。</Typography.Text><EChart className="quality-imr-chart" ariaLabel="SPC I-MR Chart" option={spcOption} onEvents={chartEvents} />
            <Typography.Title level={5}>Rule Hit Evidence</Typography.Title>
            <Table rowKey="sequence" size="small" pagination={{ pageSize: 50, showSizeChanger: false }} dataSource={spcRuleHitPoints} locale={{ emptyText: "当前返回点没有命中已批准规则" }} columns={[
              { title: "Sequence", dataIndex: "sequence", key: "sequence" }, { title: "I Value", dataIndex: "value", key: "value" }, { title: "Moving Range", dataIndex: "moving_range", key: "mr", render: numeric }, { title: "Rule Hits", dataIndex: "rule_hits", key: "hits", render: (values) => values.map((value: string) => <Tag color="error" key={value}>{value}</Tag>) }, { title: "Evidence", dataIndex: "drilldown_key", key: "evidence", render: (key) => drillButtons([key]) },
            ]} />
          </>}</> : <Empty description="无 SPC Group" />}
      </Space></Card>}

      {result.analysis === "MARGIN_OOS" && <Card title="Spec Margin / OOS"><Space direction="vertical" style={{ width: "100%" }}>
        <Table rowKey={(row) => `${row.dataset_id}:${row.version_no}:${row.group_key}`} pagination={false} scroll={{ x: 1200 }} dataSource={result.margin} columns={[
          { title: "Group", dataIndex: "group_key", key: "group", width: 260 }, { title: "Spec", key: "spec", render: (_, row) => `#${row.spec_set_id} / ${row.spec_version} / ${row.spec_mode}` },
          { title: "LSL / USL", key: "limits", render: (_, row) => `${numeric(row.lsl)} / ${numeric(row.usl)}` }, { title: "Valid / Missing", key: "n", render: (_, row) => `${row.valid_n} / ${row.missing_n}` },
          { title: "OOS", key: "oos", render: (_, row) => `${row.out_of_spec_count} / ${percent(row.out_of_spec_rate)}` }, { title: "Minimum Margin", dataIndex: "minimum_margin", key: "minimum", render: numeric },
        ]} />
        {marginGroups.length ? <><Select aria-label="Margin 显示 Group" value={activeMarginKey} onChange={(value) => onConfigChange({ marginDisplayGroup: value })} options={marginGroups.map((item) => ({ label: `#${item.dataset_id} v${item.version_no} · ${item.group_key}`, value: `${item.dataset_id}:${item.version_no}:${item.group_key}` }))} style={{ width: "100%", maxWidth: 720 }} />
          <Typography.Text type="secondary">分布图只绘制服务端确定性采样返回的 {activeMargin?.sampling_summary.returned_points ?? 0} / 原始 {activeMargin?.sampling_summary.original_points ?? 0} 点；全部 OOS 由服务端保留。Brush、缩放和 Y 轴显示只影响视图，不重新判定 Spec。</Typography.Text>
          <EChart ariaLabel="Spec Margin OOS Distribution Chart" option={marginOption} onEvents={chartEvents} />
        </> : <Empty description="无 Margin Group" />}
        <Typography.Text type="secondary">当前 Group 证据表优先显示 OOS 和最小 Margin，最多 {firstMarginPoints(result, activeMarginKey).length} 个服务端结果；Group 汇总仍覆盖全部点。</Typography.Text>
        <Table rowKey="measurement_id" size="small" pagination={false} dataSource={firstMarginPoints(result, activeMarginKey)} columns={[
          { title: "Unit", dataIndex: "unit_id", key: "unit" }, { title: "Value", dataIndex: "value", key: "value" }, { title: "Nearest Margin", dataIndex: "nearest_margin", key: "margin" }, { title: "OOS", dataIndex: "out_of_spec", key: "oos", render: (value) => <Tag color={value ? "error" : "success"}>{value ? "YES" : "NO"}</Tag> }, { title: "Drilldown", dataIndex: "drilldown_key", key: "drill", render: (key) => drillButtons([key]) },
        ]} />
      </Space></Card>}

      {result.analysis === "BIN_COOCCURRENCE" && <Card title="Bin Co-occurrence"><Space direction="vertical" style={{ width: "100%" }}>
        {cooccurrenceGroups.size ? <><Space wrap><Select aria-label="Bin Co-occurrence 显示 Group" value={activeCooccurrenceKey} onChange={(value) => onConfigChange({ cooccurrenceDisplayGroup: value })} options={Array.from(cooccurrenceGroups.entries()).map(([key, cells]) => ({ label: `#${cells[0].dataset_id} v${cells[0].version_no} · ${cells[0].group_key}`, value: key }))} style={{ width: 720, maxWidth: "100%" }} /><Select aria-label="Bin Co-occurrence 百分比色阶" value={percentAxisMode} onChange={(value) => onConfigChange({ percentAxisMode: value })} options={[{ label: "百分比色阶：自适应", value: "AUTO" }, { label: "百分比色阶：0–100%", value: "FIXED_0_100" }]} style={{ width: 220 }} /></Space>
          <Alert type="info" showIcon message="共现分母与 Pareto 口径" description="Heatmap rate、Physical Units、Denominator、Pareto rank/share/cumulative 均为服务端权威字段；前端只绘制。" />
          <EChart ariaLabel="Bin Co-occurrence Heatmap" option={cooccurrenceHeatmap} onEvents={chartEvents} />
          <EChart ariaLabel="Bin Co-occurrence Pareto" option={cooccurrencePareto} onEvents={chartEvents} />
        </> : <Empty description="无 Bin 共现结果" />}
        <Table rowKey={(row) => `${row.dataset_id}:${row.version_no}:${row.group_key}:${row.left_bin}:${row.right_bin}`} pagination={{ pageSize: 100, showSizeChanger: false }} scroll={{ x: 1100 }} dataSource={result.bin_cooccurrence} columns={[
          { title: "Group", dataIndex: "group_key", key: "group", width: 260 }, { title: "Left Bin", dataIndex: "left_bin", key: "left" }, { title: "Right Bin", dataIndex: "right_bin", key: "right" },
          { title: "Rank", dataIndex: "pareto_rank", key: "rank" }, { title: "Physical Units", dataIndex: "physical_unit_count", key: "units" }, { title: "Denominator", dataIndex: "denominator_units", key: "denominator" }, { title: "Rate", dataIndex: "rate", key: "rate", render: percent }, { title: "Pair Share / Cumulative", key: "pareto", render: (_, row) => `${percent(row.pair_count_share)} / ${percent(row.cumulative_pair_count_share)}` }, { title: "Evidence", dataIndex: "drilldown_keys", key: "evidence", render: drillButtons },
        ]} />
      </Space></Card>}

      {result.analysis === "SBL_GROUPED_LIMIT" && <Card title="SBL Grouped Limit"><Space direction="vertical" style={{ width: "100%" }}>
        {sblLimits.length ? <><Space wrap><Select aria-label="SBL 显示 Fail Bin" value={activeSbl ? `${activeSbl.dataset_id}:${activeSbl.version_no}:${activeSbl.bin_code}` : undefined} onChange={(value) => onConfigChange({ sblDisplayBin: value })} options={sblLimits.map((item) => ({ label: `#${item.dataset_id} v${item.version_no} · Bin ${item.bin_code}`, value: `${item.dataset_id}:${item.version_no}:${item.bin_code}` }))} style={{ width: 360 }} /><Select aria-label="Quality 百分比 Y 轴" value={percentAxisMode} onChange={(value) => onConfigChange({ percentAxisMode: value })} options={[{ label: "百分比轴：自适应", value: "AUTO" }, { label: "百分比轴：0–100%", value: "FIXED_0_100" }]} style={{ width: 220 }} /></Space>
          <Typography.Text type="secondary">趋势点、SBL 线以及 Fail Bin Pareto 的 count/rank/share/cumulative 均来自服务端批准规则结果；前端只绘制。</Typography.Text>
          <EChart ariaLabel="SBL Quality Trend Chart" option={sblTrend} onEvents={chartEvents} />
          <EChart ariaLabel="SBL Fail Bin Pareto" option={sblPareto} onEvents={chartEvents} />
        </> : <Empty description="无 SBL 结果" />}
        <Table rowKey={(row) => `${row.dataset_id}:${row.version_no}:${row.bin_code}`} pagination={false} scroll={{ x: 1300 }} dataSource={result.sbl} expandable={{ expandedRowRender: (row) => <Table rowKey="group_key" size="small" pagination={{ pageSize: 50, showSizeChanger: false }} dataSource={row.groups} columns={[
          { title: "Group", dataIndex: "group_key", key: "group" }, { title: "Units / Fail", key: "counts", render: (_, item) => `${item.physical_unit_count} / ${item.fail_unit_count}` }, { title: "Rate", dataIndex: "rate", key: "rate", render: percent }, { title: "Evidence", dataIndex: "drilldown_keys", key: "evidence", render: drillButtons },
        ]} /> }} columns={[
          { title: "Rank", dataIndex: "pareto_rank", key: "rank" }, { title: "Bin", dataIndex: "bin_code", key: "bin" }, { title: "Fail Units", dataIndex: "fail_unit_count", key: "failCount" }, { title: "Share / Cumulative", key: "pareto", render: (_, row) => `${percent(row.fail_unit_share)} / ${percent(row.cumulative_fail_unit_share)}` }, { title: "Subgroups", dataIndex: "subgroup_count", key: "count" }, { title: "Mean Rate", dataIndex: "mean_rate", key: "mean", render: percent }, { title: "Sample Stddev", dataIndex: "sample_stddev", key: "std", render: numeric }, { title: "Upper Limit", dataIndex: "upper_limit", key: "upper", render: percent }, { title: "Status", dataIndex: "status", key: "status" }, { title: "Exceeding Groups", dataIndex: "exceeding_groups", key: "exceeding", render: (values) => values.length ? values.join(", ") : "—" },
        ]} />
      </Space></Card>}

      {result.analysis === "SYL_GROUPED_LIMIT" && <Card title="SYL Grouped Limit"><Space direction="vertical" style={{ width: "100%" }}>
        {sylLimits.length ? <><Space wrap><Select aria-label="SYL 显示 Dataset" value={activeSyl ? `${activeSyl.dataset_id}:${activeSyl.version_no}` : undefined} onChange={(value) => onConfigChange({ sylDisplayDataset: value })} options={sylLimits.map((item) => ({ label: `#${item.dataset_id} v${item.version_no}`, value: `${item.dataset_id}:${item.version_no}` }))} style={{ width: 260 }} /><Select aria-label="Quality 百分比 Y 轴" value={percentAxisMode} onChange={(value) => onConfigChange({ percentAxisMode: value })} options={[{ label: "百分比轴：自适应", value: "AUTO" }, { label: "百分比轴：0–100%", value: "FIXED_0_100" }]} style={{ width: 220 }} /></Space>
          <Typography.Text type="secondary">Known Yield 趋势与 SYL 线均为服务端批准版本返回值；UNKNOWN、ABORT 和 Other 继续单独计数，不进入 PASS/(PASS+FAIL)。</Typography.Text>
          <EChart ariaLabel="SYL Quality Trend Chart" option={sylTrend} onEvents={chartEvents} />
        </> : <Empty description="无 SYL 结果" />}
        <Table rowKey={(row) => `${row.dataset_id}:${row.version_no}`} pagination={false} scroll={{ x: 1400 }} dataSource={result.syl} expandable={{ expandedRowRender: (row) => <Table rowKey="group_key" size="small" pagination={{ pageSize: 50, showSizeChanger: false }} dataSource={row.groups} columns={[
          { title: "Group", dataIndex: "group_key", key: "group" }, { title: "PASS / FAIL", key: "counts", render: (_, item) => `${item.pass_unit_count} / ${item.fail_unit_count}` }, { title: "UNKNOWN / ABORT / Other", key: "excluded", render: (_, item) => `${item.unknown_excluded_count} / ${item.abort_excluded_count} / ${item.other_result_excluded_count}` }, { title: "Yield", dataIndex: "yield_rate", key: "yield", render: percent }, { title: "Evidence", dataIndex: "drilldown_keys", key: "evidence", render: drillButtons },
        ]} /> }} columns={[
          { title: "Dataset", key: "dataset", render: (_, row) => `#${row.dataset_id} v${row.version_no}` }, { title: "Subgroups", dataIndex: "subgroup_count", key: "count" }, { title: "Mean Yield", dataIndex: "mean_yield", key: "mean", render: percent }, { title: "Sample Stddev", dataIndex: "sample_stddev", key: "std", render: numeric }, { title: "Raw / Effective Lower", key: "lower", render: (_, row) => `${percent(row.raw_lower_limit)} / ${percent(row.lower_limit)}` }, { title: "Rounding", key: "rounding", render: (_, row) => `${row.rounding_policy}${row.rounding_step == null ? "" : ` @ ${row.rounding_step}`}` }, { title: "Status", dataIndex: "status", key: "status" }, { title: "Below Limit", dataIndex: "below_limit_groups", key: "below", render: (values) => values.length ? values.join(", ") : "—" },
        ]} />
      </Space></Card>}

      {result.analysis === "PASS_FAIL_DISTRIBUTION" && <Card title="Pass / Fail Parameter Distribution"><Space direction="vertical" style={{ width: "100%" }}>
        <Table rowKey={(row) => `${row.dataset_id}:${row.version_no}:${row.group_key}`} pagination={false} scroll={{ x: 1400 }} dataSource={result.pass_fail_distribution} columns={[
          { title: "Group", dataIndex: "group_key", key: "group", width: 260 }, { title: "PASS / FAIL", key: "counts", render: (_, row) => `${row.pass_count} / ${row.fail_count}` }, { title: "UNKNOWN / ABORT / Other", key: "excluded", render: (_, row) => `${row.unknown_excluded_count} / ${row.abort_excluded_count} / ${row.other_result_excluded_count}` }, { title: "Missing Measurement", dataIndex: "missing_measurements", key: "missing" }, { title: "PASS / FAIL Mean", key: "means", render: (_, row) => `${numeric(row.pass_mean)} / ${numeric(row.fail_mean)}` }, { title: "Min / Max", key: "range", render: (_, row) => `${numeric(row.minimum)} / ${numeric(row.maximum)}` }, { title: "Status", dataIndex: "status", key: "status" },
        ]} />
        {result.pass_fail_distribution.length ? <><Select aria-label="Pass Fail Distribution Group" value={activeDistribution ? qualityGroupKey(activeDistribution.dataset_id, activeDistribution.version_no, activeDistribution.group_key) : undefined} onChange={(value) => onConfigChange({ distributionDisplayGroup: value })} options={result.pass_fail_distribution.map((item) => ({ label: `#${item.dataset_id} v${item.version_no} · ${item.group_key}`, value: qualityGroupKey(item.dataset_id, item.version_no, item.group_key) }))} style={{ width: "100%", maxWidth: 720 }} />{activeDistribution && <><EChart ariaLabel="Pass Fail Distribution Chart" option={distributionOption} onEvents={chartEvents} /><Table rowKey="bin_index" size="small" pagination={false} dataSource={activeDistribution.bins} columns={[
          { title: "Bin", dataIndex: "bin_index", key: "bin" }, { title: "Lower / Upper", key: "range", render: (_, row) => `${row.lower} / ${row.upper}` }, { title: "PASS", dataIndex: "pass_count", key: "pass" }, { title: "FAIL", dataIndex: "fail_count", key: "fail" }, { title: "PASS Evidence", dataIndex: "pass_drilldown_keys", key: "passEvidence", render: drillButtons }, { title: "FAIL Evidence", dataIndex: "fail_drilldown_keys", key: "failEvidence", render: drillButtons },
        ]} /></>}</> : <Empty description="无可对比 PASS / FAIL 分布" />}
      </Space></Card>}
      {selectedMembers && <Card title={`${selectedMembers.title}（${selectedMembers.keys.length}）`} extra={<Button size="small" onClick={() => setSelectedMembers(null)}>关闭</Button>}><Table rowKey="key" size="small" pagination={{ pageSize: 50, showSizeChanger: false }} dataSource={selectedMembers.keys.map((key) => ({ key }))} columns={[{ title: "Unit Drilldown Key", dataIndex: "key", key: "key" }, { title: "操作", key: "action", render: (_, row) => <Button type="link" onClick={() => onOpenDrilldown(row.key)}>打开 Unit Drawer</Button> }]} /></Card>}
    </>}
  </Space>;
}
