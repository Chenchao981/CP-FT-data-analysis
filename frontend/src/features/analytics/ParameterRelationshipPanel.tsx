import { ReloadOutlined } from "@ant-design/icons";
import { useMutation } from "@tanstack/react-query";
import { Alert, Button, Card, Checkbox, Col, Collapse, Empty, Input, InputNumber, Row, Select, Space, Table, Tag, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import type { EChartsCoreOption } from "echarts/core";
import { useEffect, useMemo, useState } from "react";

import type { AnalyticsContextRequest } from "../../api/analytics";
import { ApiError } from "../../api/auth";
import {
  analyzeParameterRelationship,
  type ParameterCorrelationResult,
  type ParameterRelationshipAnalysis,
  type ParameterRelationshipGroupBy,
  type ParameterRelationshipIdentity,
  type ParameterRelationshipRequest,
  type ParameterScatterPoint,
  type ParameterTrendPoint,
} from "../../api/parameterRelationship";
import { EChart, type EChartEventMap } from "../../components/EChart";
import { drilldownKeyFromChartEvent } from "./chartDrilldown";
import { ANALYSIS_COMPONENT_DEFAULTS, type ParameterRelationshipViewConfig } from "./context/analysisViewConfig";
import type { AnalysisDisplayState } from "./context/analysisViewState";

export interface ParameterRelationshipPanelProps {
  context: AnalyticsContextRequest;
  parameterOptions: string[];
  suggestedParameters: string[];
  onOpenDrilldown: (drilldownKey: string) => void;
  displayState: AnalysisDisplayState;
  onDisplayStateChange: (patch: Partial<AnalysisDisplayState>) => void;
  config?: ParameterRelationshipViewConfig;
  onConfigChange?: (patch: Partial<ParameterRelationshipViewConfig>) => void;
}

type PointVisibility = "IN_SPEC" | "OUT_OF_SPEC";

interface IdentityRow extends ParameterRelationshipIdentity {
  key: string;
  dataset: string;
}

const analysisOptions: Array<{ label: string; value: ParameterRelationshipAnalysis }> = [
  { label: "Scatter", value: "SCATTER" },
  { label: "Trend", value: "TREND" },
  { label: "Correlation", value: "CORRELATION" },
];
const groupOptions: Array<{ label: string; value: ParameterRelationshipGroupBy }> = [
  { label: "Dataset", value: "DATASET" },
  { label: "Test Batch", value: "TEST_BATCH" },
  { label: "Lot", value: "LOT" },
  { label: "Wafer（Lot + Wafer）", value: "WAFER" },
  { label: "Source", value: "SOURCE" },
  { label: "Tester", value: "TESTER" },
  { label: "Program", value: "PROGRAM" },
  { label: "Test Condition", value: "CONDITION" },
];
const visibilityOptions: Array<{ label: string; value: PointVisibility }> = [
  { label: "In-spec", value: "IN_SPEC" },
  { label: "Out-of-spec", value: "OUT_OF_SPEC" },
];
const CORRELATION_METHOD = "PEARSON_PAIRWISE_V1" as const;
const SERIES_COLORS = ["#1167a8", "#2d9d78", "#d64545", "#f0a429", "#7b61a8", "#247ba0", "#8d6e63"];

const selectOptions = (values: string[]) => Array.from(new Set(values)).sort().map((value) => ({ label: value, value }));
const datasetLabel = (datasetId: number, versionNo: number) => `#${datasetId} / V${versionNo}`;
const cloneRequest = (request: ParameterRelationshipRequest): ParameterRelationshipRequest => ({
  datasets: request.datasets.map((item) => ({ ...item })),
  filters: {
    lot_ids: [...request.filters.lot_ids],
    wafer_ids: [...request.filters.wafer_ids],
    bin_codes: [...request.filters.bin_codes],
    overall_results: [...request.filters.overall_results],
    source_ids: [...request.filters.source_ids],
    tester_ids: [...request.filters.tester_ids],
    program_versions: [...request.filters.program_versions],
    test_conditions: [...request.filters.test_conditions],
  },
  x_parameter: request.x_parameter,
  y_parameters: [...request.y_parameters],
  analyses: [...request.analyses],
  group_by: request.group_by,
  max_points: request.max_points,
  correlation: { ...request.correlation },
});

function groupPoints<T extends { group_key: string }>(points: T[]): Map<string, T[]> {
  const grouped = new Map<string, T[]>();
  for (const point of points) {
    const group = grouped.get(point.group_key);
    if (group) group.push(point);
    else grouped.set(point.group_key, [point]);
  }
  return grouped;
}

interface CorrelationHeatmapDatum {
  value: [number, number, number | null];
  groupKey: string;
  scopeKey: string;
  datasetId: number;
  versionNo: number;
  scatterX: string;
  scatterY: string;
  sampleCount: number;
  status: string;
  ruleCode: string;
}

const correlationScopeKey = (item: ParameterCorrelationResult) => `${item.dataset_id}:V${item.version_no}|${item.group_key}`;
const correlationScopeLabel = (item: ParameterCorrelationResult) => `#${item.dataset_id}/V${item.version_no} · ${item.group_key}`;

function scatterSpecLines(
  xIdentity: ParameterRelationshipIdentity | undefined,
  yIdentity: ParameterRelationshipIdentity | undefined,
) {
  const lines: Array<Record<string, unknown>> = [];
  if (xIdentity?.formal_lsl != null) lines.push({ name: `${xIdentity.name} Formal LSL`, xAxis: xIdentity.formal_lsl, lineStyle: { color: "#d48806", type: "dashed" } });
  if (xIdentity?.formal_usl != null) lines.push({ name: `${xIdentity.name} Formal USL`, xAxis: xIdentity.formal_usl, lineStyle: { color: "#d48806", type: "dashed" } });
  if (yIdentity?.formal_lsl != null) lines.push({ name: `${yIdentity.name} Formal LSL`, yAxis: yIdentity.formal_lsl, lineStyle: { color: "#722ed1", type: "dashed" } });
  if (yIdentity?.formal_usl != null) lines.push({ name: `${yIdentity.name} Formal USL`, yAxis: yIdentity.formal_usl, lineStyle: { color: "#722ed1", type: "dashed" } });
  return lines;
}

export function ParameterRelationshipPanel({
  context,
  parameterOptions,
  suggestedParameters,
  onOpenDrilldown,
  displayState,
  onDisplayStateChange,
  config: controlledConfig,
  onConfigChange: controlledOnConfigChange,
}: ParameterRelationshipPanelProps) {
  const initialParameters = suggestedParameters.filter((value, index, values) => value && values.indexOf(value) === index);
  const [localConfig, setLocalConfig] = useState<ParameterRelationshipViewConfig>(() => ({
    ...ANALYSIS_COMPONENT_DEFAULTS.parameterRelationship,
    xParameter: initialParameters[0] ?? "",
    yParameters: initialParameters.slice(1, 6),
    analyses: [...ANALYSIS_COMPONENT_DEFAULTS.parameterRelationship.analyses],
    correlation: { ...ANALYSIS_COMPONENT_DEFAULTS.parameterRelationship.correlation },
    displayGroups: [], pointVisibility: [...ANALYSIS_COMPONENT_DEFAULTS.parameterRelationship.pointVisibility],
  }));
  const config = controlledConfig ?? localConfig;
  const onConfigChange = (patch: Partial<ParameterRelationshipViewConfig>) => {
    if (!controlledConfig) setLocalConfig((current) => ({ ...current, ...patch }));
    controlledOnConfigChange?.(patch);
  };
  const xParameter = config.xParameter;
  const yParameters = [...config.yParameters];
  const analyses = [...config.analyses] as ParameterRelationshipAnalysis[];
  const groupBy = config.groupBy as ParameterRelationshipGroupBy;
  const maxPoints = config.maxPoints;
  const [submittedRequest, setSubmittedRequest] = useState<ParameterRelationshipRequest | null>(null);
  const [submittedSignature, setSubmittedSignature] = useState<string | null>(null);
  const scatterYChoice = config.scatterY || undefined;
  const scatterDatasetMatch = /^(\d+):(\d+)$/.exec(config.scatterDataset);
  const scatterDatasetScope = scatterDatasetMatch ? { datasetId: Number(scatterDatasetMatch[1]), versionNo: Number(scatterDatasetMatch[2]) } : null;
  const trendParameterChoice = config.trendParameter || undefined;
  const correlationScopeChoice = config.correlationScope || undefined;
  const displayGroups = [...config.displayGroups];
  const pointVisibility = [...config.pointVisibility] as PointVisibility[];
  const correlationRuleCode = config.correlation.ruleCode;
  const correlationRuleVersion = config.correlation.versionCode;
  const [pendingScatterScroll, setPendingScatterScroll] = useState(false);

  const correlationRequested = analyses.includes("CORRELATION");
  const currentRequest = useMemo<ParameterRelationshipRequest>(() => ({
    datasets: context.datasets.map((item) => ({ ...item })),
    filters: {
      lot_ids: [...context.filters.lot_ids],
      wafer_ids: [...context.filters.wafer_ids],
      bin_codes: [...context.filters.bin_codes],
      overall_results: [...context.filters.overall_results],
      source_ids: [...context.filters.source_ids],
      tester_ids: [...context.filters.tester_ids],
      program_versions: [...context.filters.program_versions],
      test_conditions: [...context.filters.test_conditions],
    },
    x_parameter: xParameter,
    y_parameters: [...yParameters],
    analyses: [...analyses],
    group_by: groupBy,
    max_points: maxPoints,
    correlation: correlationRequested ? { method: CORRELATION_METHOD, rule_code: correlationRuleCode, version_code: correlationRuleVersion } : {},
  }), [analyses, context, correlationRequested, correlationRuleCode, correlationRuleVersion, groupBy, maxPoints, xParameter, yParameters]);
  const currentSignature = JSON.stringify(currentRequest);
  const canRun = context.datasets.length >= 1
    && xParameter.length > 0
    && yParameters.length >= 1
    && yParameters.length <= 5
    && !yParameters.includes(xParameter)
    && analyses.length >= 1
    && (!correlationRequested || Boolean(correlationRuleCode && correlationRuleVersion))
    && Number.isInteger(maxPoints)
    && maxPoints >= 100
    && maxPoints <= 20_000;

  const mutation = useMutation({
    mutationFn: analyzeParameterRelationship,
    retry: false,
  });
  const execute = () => {
    if (!canRun) return;
    const snapshot = cloneRequest(currentRequest);
    onConfigChange({ displayGroups: [], scatterDataset: "" });
    setSubmittedRequest(snapshot);
    setSubmittedSignature(JSON.stringify(snapshot));
    mutation.mutate(snapshot);
  };
  const retrySubmitted = () => {
    if (submittedRequest) mutation.mutate(cloneRequest(submittedRequest));
  };
  const isStale = Boolean(mutation.data && submittedSignature !== currentSignature);
  const error = mutation.error;
  const apiError = error instanceof ApiError ? error : null;
  const ruleGateError = apiError?.code === "ANALYSIS_RULE_NOT_APPROVED" || apiError?.code === "ANALYSIS_RULE_VERSION_REQUIRED";

  const allScatterPoints = mutation.data?.items.flatMap((item) => item.scatter_points) ?? [];
  const allTrendPoints = mutation.data?.items.flatMap((item) => item.trend_points) ?? [];
  const allCorrelations = mutation.data?.items.flatMap((item) => item.correlations) ?? [];
  const returnedYParameters = Array.from(new Set(allScatterPoints.map((point) => point.y_parameter)));
  const scatterY = returnedYParameters.includes(scatterYChoice ?? "") ? scatterYChoice! : returnedYParameters[0];
  const returnedTrendParameters = Array.from(new Set(allTrendPoints.map((point) => point.parameter)));
  const trendParameter = returnedTrendParameters.includes(trendParameterChoice ?? "") ? trendParameterChoice! : returnedTrendParameters[0];
  const availableGroups = Array.from(new Set(mutation.data?.items.map((item) => item.group_key) ?? [])).sort();
  const activeGroups = displayGroups.length ? new Set(displayGroups) : null;
  const showInSpec = pointVisibility.includes("IN_SPEC");
  const showOutOfSpec = pointVisibility.includes("OUT_OF_SPEC");
  const pointVisible = (outOfSpec: boolean) => outOfSpec ? showOutOfSpec : showInSpec;
  const selectedScatter = allScatterPoints.filter((point) => point.y_parameter === scatterY
    && (!scatterDatasetScope || (point.dataset_id === scatterDatasetScope.datasetId && point.version_no === scatterDatasetScope.versionNo))
    && (!activeGroups || activeGroups.has(point.group_key))
    && pointVisible(point.x_out_of_spec || point.y_out_of_spec));
  const selectedTrend = allTrendPoints.filter((point) => point.parameter === trendParameter
    && (!activeGroups || activeGroups.has(point.group_key))
    && pointVisible(point.out_of_spec));

  useEffect(() => {
    if (!pendingScatterScroll || !selectedScatter.length) return;
    document.getElementById("parameter-relationship-scatter")?.scrollIntoView?.({ behavior: "smooth", block: "start" });
    setPendingScatterScroll(false);
  }, [pendingScatterScroll, selectedScatter]);

  const identityRows = useMemo<IdentityRow[]>(() => {
    const rows = new Map<string, IdentityRow>();
    for (const item of mutation.data?.items ?? []) {
      for (const identity of item.identities) {
        const key = `${item.dataset_id}:${item.version_no}:${identity.name}:${identity.step_code}:${identity.sequence_no}`;
        rows.set(key, { ...identity, key, dataset: datasetLabel(item.dataset_id, item.version_no) });
      }
    }
    return Array.from(rows.values());
  }, [mutation.data]);
  const xIdentity = identityRows.find((item) => item.name === xParameter);
  const scatterYIdentity = identityRows.find((item) => item.name === scatterY);
  const trendIdentity = identityRows.find((item) => item.name === trendParameter);
  const scatterLimits = scatterSpecLines(xIdentity, scatterYIdentity);
  const missingFormalSpecIdentities = identityRows.filter((item) => item.formal_spec_status !== "RESOLVED");

  const scatterOption = useMemo<EChartsCoreOption>(() => ({
    color: SERIES_COLORS,
    tooltip: {
      trigger: "item",
      formatter: (payload: unknown) => {
        const data = (payload as { data?: { value?: unknown; groupKey?: unknown; outOfSpec?: unknown } })?.data;
        const value = Array.isArray(data?.value) ? data.value : [];
        return `${String(data?.groupKey ?? "—")}<br/>${xParameter}: ${value[0] ?? "—"}<br/>${scatterY ?? "Y"}: ${value[1] ?? "—"}<br/>${data?.outOfSpec ? "Out-of-spec" : "In-spec"}`;
      },
    },
    legend: { type: "scroll" },
    grid: { left: 76, right: 28, top: 56, bottom: 62 },
    xAxis: { type: "value", name: xIdentity?.unit ? `${xParameter} (${xIdentity.unit})` : xParameter },
    yAxis: {
      type: "value",
      name: scatterYIdentity?.unit ? `${scatterY} (${scatterYIdentity.unit})` : scatterY,
      min: displayState.yAxisMin ?? undefined,
      max: displayState.yAxisMax ?? undefined,
    },
    dataZoom: [{ type: "inside" }, { type: "slider", height: 18, bottom: 8 }],
    brush: displayState.brushEnabled ? { toolbox: ["rect", "polygon", "clear"], xAxisIndex: "all", yAxisIndex: "all" } : undefined,
    toolbox: { feature: { dataZoom: {}, restore: {}, saveAsImage: { name: `${xParameter}-${scatterY ?? "Y"}-scatter` } } },
    series: Array.from(groupPoints(selectedScatter).entries()).map(([groupKey, points], index) => ({
      name: groupKey,
      type: "scatter",
      symbolSize: 7,
      markLine: displayState.showSpecOverlay && index === 0 && scatterLimits.length
        ? { silent: true, symbol: "none", label: { formatter: "{b}" }, data: scatterLimits }
        : undefined,
      data: points.map((point) => ({
        value: [point.x_value, point.y_value],
        groupKey: point.group_key,
        outOfSpec: point.x_out_of_spec || point.y_out_of_spec,
        drilldownKey: point.drilldown_key,
        itemStyle: point.x_out_of_spec || point.y_out_of_spec ? { color: "#d64545", borderColor: "#7d1f1f", borderWidth: 1 } : undefined,
      })),
    })),
  }), [displayState.brushEnabled, displayState.showSpecOverlay, displayState.yAxisMax, displayState.yAxisMin, scatterLimits, scatterY, scatterYIdentity?.unit, selectedScatter, xIdentity?.unit, xParameter]);

  const trendOption = useMemo<EChartsCoreOption>(() => ({
    color: SERIES_COLORS,
    tooltip: {
      trigger: "item",
      formatter: (payload: unknown) => {
        const data = (payload as { data?: { value?: unknown; groupKey?: unknown; outOfSpec?: unknown; sourceSequence?: unknown; runId?: unknown; orderedAt?: unknown } })?.data;
        const value = Array.isArray(data?.value) ? data.value : [];
        return `${String(data?.groupKey ?? "—")}<br/>Stable ordinal ${value[0] ?? "—"}<br/>Source sequence ${data?.sourceSequence ?? "—"}<br/>Run ${data?.runId ?? "—"}<br/>Source time ${data?.orderedAt ?? "—"}<br/>${trendParameter ?? "参数"}: ${value[1] ?? "—"}<br/>${data?.outOfSpec ? "Out-of-spec" : "In-spec"}`;
      },
    },
    legend: { type: "scroll" },
    grid: { left: 76, right: 28, top: 56, bottom: 62 },
    xAxis: { type: "value", name: "Stable ordinal", minInterval: 1 },
    yAxis: {
      type: "value",
      name: trendIdentity?.unit ? `${trendParameter} (${trendIdentity.unit})` : trendParameter,
      min: displayState.yAxisMin ?? undefined,
      max: displayState.yAxisMax ?? undefined,
    },
    dataZoom: [{ type: "inside" }, { type: "slider", height: 18, bottom: 8 }],
    toolbox: { feature: { dataZoom: {}, restore: {}, saveAsImage: { name: `${trendParameter ?? "parameter"}-trend` } } },
    series: Array.from(groupPoints(selectedTrend).entries()).map(([groupKey, points]) => ({
      name: groupKey,
      type: "line",
      showSymbol: true,
      symbolSize: 6,
      connectNulls: false,
      data: [...points].sort((left, right) => left.ordinal - right.ordinal).map((point) => ({
        value: [point.ordinal, point.value],
        groupKey: point.group_key,
        sourceSequence: point.source_sequence,
        runId: point.run_id,
        orderedAt: point.ordered_at,
        outOfSpec: point.out_of_spec,
        drilldownKey: point.drilldown_key,
        itemStyle: point.out_of_spec ? { color: "#d64545", borderColor: "#7d1f1f", borderWidth: 1 } : undefined,
      })),
    })),
  }), [displayState.yAxisMax, displayState.yAxisMin, selectedTrend, trendIdentity?.unit, trendParameter]);

  const chartEvents = useMemo<EChartEventMap>(() => ({
    click: (payload) => {
      const key = drilldownKeyFromChartEvent(payload);
      if (key) onOpenDrilldown(key);
    },
  }), [onOpenDrilldown]);

  const correlationScopeRecords = Array.from(
    new Map(allCorrelations.map((item) => [correlationScopeKey(item), item])).values(),
  ).sort((left, right) => left.dataset_id - right.dataset_id || left.version_no - right.version_no || left.group_key.localeCompare(right.group_key));
  const correlationScopeOptions = correlationScopeRecords.map((item) => ({ label: correlationScopeLabel(item), value: correlationScopeKey(item) }));
  const activeCorrelationScope = correlationScopeOptions.some((item) => item.value === correlationScopeChoice)
    ? correlationScopeChoice!
    : correlationScopeOptions[0]?.value;
  const scopeCorrelations = allCorrelations.filter((item) => correlationScopeKey(item) === activeCorrelationScope);
  const requestedParameterOrder = submittedRequest ? [submittedRequest.x_parameter, ...submittedRequest.y_parameters] : [];
  const matrixParameters = Array.from(new Set([
    ...requestedParameterOrder,
    ...scopeCorrelations.flatMap((item) => [item.x_parameter, item.y_parameter]),
  ])).filter((parameter) => scopeCorrelations.some((item) => item.x_parameter === parameter || item.y_parameter === parameter));
  const visibleCorrelations = scopeCorrelations.filter((item) => item.coefficient == null
    || Math.abs(item.coefficient) >= displayState.correlationMinAbs);
  const correlationHeatmapData: CorrelationHeatmapDatum[] = visibleCorrelations.map((item) => ({
    value: [matrixParameters.indexOf(item.x_parameter), matrixParameters.indexOf(item.y_parameter), item.coefficient],
    groupKey: item.group_key,
    scopeKey: correlationScopeKey(item),
    datasetId: item.dataset_id,
    versionNo: item.version_no,
    scatterX: item.x_parameter,
    scatterY: item.y_parameter,
    sampleCount: item.sample_count,
    status: item.status,
    ruleCode: item.rule_code,
  }));
  const correlationOption = useMemo<EChartsCoreOption>(() => ({
    tooltip: {
      formatter: (payload: unknown) => {
        const data = (payload as { data?: CorrelationHeatmapDatum })?.data;
        if (!data) return "—";
        return `${data.groupKey}<br/>${data.scatterX} / ${data.scatterY}<br/>r=${data.value[2] ?? "N/A"}<br/>Pairwise N=${data.sampleCount}<br/>${data.status}<br/>${data.ruleCode}`;
      },
    },
    grid: { left: 110, right: 36, top: 42, bottom: 96 },
    xAxis: { type: "category", data: matrixParameters },
    yAxis: { type: "category", data: matrixParameters },
    visualMap: { min: -1, max: 1, calculable: false, orient: "horizontal", left: "center", bottom: 12, inRange: { color: ["#2166ac", "#f7f7f7", "#b2182b"] } },
    toolbox: { feature: { restore: {}, saveAsImage: { name: "correlation-matrix" } } },
    series: [{
      name: "Pearson r",
      type: "heatmap",
      data: correlationHeatmapData,
      label: { show: true, formatter: (payload: unknown) => {
        const data = (payload as { data?: CorrelationHeatmapDatum })?.data;
        return data ? data.value[2]?.toFixed(3) ?? "N/A" : "";
      } },
      emphasis: { itemStyle: { shadowBlur: 8, shadowColor: "rgba(0,0,0,0.35)" } },
    }],
  }), [correlationHeatmapData, matrixParameters]);
  const correlationEvents = useMemo<EChartEventMap>(() => ({
    click: (payload) => {
      const data = (payload as { data?: Partial<CorrelationHeatmapDatum> })?.data;
      if (typeof data?.scatterX !== "string" || typeof data.scatterY !== "string" || data.scatterX === data.scatterY
        || typeof data.groupKey !== "string" || typeof data.datasetId !== "number" || typeof data.versionNo !== "number") return;
      let configPatch: Partial<ParameterRelationshipViewConfig> = {
        xParameter: data.scatterX,
        scatterY: data.scatterY,
        scatterDataset: `${data.datasetId}:${data.versionNo}`,
        displayGroups: [data.groupKey],
      };
      const hasMatchingScatter = allScatterPoints.some((point) => point.x_parameter === data.scatterX && point.y_parameter === data.scatterY
        && point.group_key === data.groupKey && point.dataset_id === data.datasetId && point.version_no === data.versionNo);
      if (!hasMatchingScatter && submittedRequest) {
        const selectedParameters = Array.from(new Set([submittedRequest.x_parameter, ...submittedRequest.y_parameters]));
        const snapshot = cloneRequest({
          ...submittedRequest,
          x_parameter: data.scatterX,
          y_parameters: [data.scatterY, ...selectedParameters.filter((parameter) => parameter !== data.scatterX && parameter !== data.scatterY)],
          analyses: Array.from(new Set([...submittedRequest.analyses, "SCATTER" as const])),
        });
        configPatch = {
          ...configPatch,
          yParameters: snapshot.y_parameters,
          analyses: snapshot.analyses,
        };
        onConfigChange(configPatch);
        setSubmittedRequest(snapshot);
        setSubmittedSignature(JSON.stringify(snapshot));
        setPendingScatterScroll(true);
        mutation.mutate(snapshot);
      } else {
        onConfigChange(configPatch);
        document.getElementById("parameter-relationship-scatter")?.scrollIntoView?.({ behavior: "smooth", block: "start" });
      }
    },
  }), [allScatterPoints, mutation, onConfigChange, submittedRequest]);

  const identityColumns: ColumnsType<IdentityRow> = [
    { title: "Dataset", dataIndex: "dataset", width: 130, fixed: "left" },
    { title: "参数精确身份", dataIndex: "name", width: 160 },
    { title: "Canonical", dataIndex: "canonical_parameter_code", width: 170, render: (value) => value ?? "—" },
    { title: "Step / Seq", width: 120, render: (_, row) => `${row.step_code} / ${row.sequence_no}` },
    { title: "Unit", dataIndex: "unit", width: 90, render: (value) => value ?? "—" },
    { title: "Program LSL / USL", width: 170, render: (_, row) => `${row.program_lsl ?? "—"} / ${row.program_usl ?? "—"}` },
    { title: "Formal Spec LSL / USL", width: 190, render: (_, row) => `${row.formal_lsl ?? "—"} / ${row.formal_usl ?? "—"}` },
    { title: "Formal Spec 状态", width: 190, render: (_, row) => row.formal_spec_status === "RESOLVED" ? row.formal_spec_versions.join("、") : `NO_SPEC${row.formal_spec_reason_codes.length ? `：${row.formal_spec_reason_codes.join("、")}` : ""}` },
    { title: "Test Condition", dataIndex: "test_condition", width: 240, render: (value) => value ?? "—" },
  ];
  const correlationColumns: ColumnsType<ParameterCorrelationResult> = [
    { title: "Dataset", width: 130, render: (_, row) => datasetLabel(row.dataset_id, row.version_no), fixed: "left" },
    { title: "分组", dataIndex: "group_key", width: 230 },
    { title: "X / Y", width: 180, render: (_, row) => `${row.x_parameter} / ${row.y_parameter}` },
    { title: "Pairwise Sample", dataIndex: "sample_count", width: 140 },
    { title: "Coefficient", dataIndex: "coefficient", width: 130, render: (value) => value ?? "—" },
    { title: "状态", dataIndex: "status", width: 130, render: (value) => <Tag>{value}</Tag> },
    { title: "原因码", dataIndex: "reason_code", width: 220, render: (value) => value ?? "—" },
    { title: "Method", dataIndex: "method", width: 245 },
    { title: "Rule", dataIndex: "rule_code", width: 290 },
  ];

  const correlationCapability = mutation.data?.capabilities.find((item) => item.code === "PARAMETER_CORRELATION");
  const parameterChoices = selectOptions([...parameterOptions, ...suggestedParameters, xParameter, ...yParameters].filter(Boolean));

  return <Card
    title="参数关系与趋势"
    extra={<Tag color="blue">X 1 个 · Y 1–5 个 · 后端权威计算</Tag>}
    className="production-table-card"
  >
    <Row gutter={[12, 12]}>
      <Col xs={24} md={12} xl={6}>
        <Typography.Text strong>X 参数（精确身份）</Typography.Text>
        <Select aria-label="关系分析 X 参数" showSearch value={xParameter || undefined} options={parameterChoices} onChange={(value) => onConfigChange({ xParameter: value, scatterDataset: "", yParameters: yParameters.filter((item) => item !== value) })} className="full-width" placeholder="选择 X" />
      </Col>
      <Col xs={24} md={12} xl={6}>
        <Typography.Text strong>Y 参数（1–5 个）</Typography.Text>
        <Select aria-label="关系分析 Y 参数" mode="multiple" allowClear maxCount={5} value={yParameters} options={parameterChoices.filter((item) => item.value !== xParameter)} onChange={(values) => onConfigChange({ scatterDataset: "", yParameters: values.slice(0, 5) })} className="full-width" placeholder="选择 Y" />
      </Col>
      <Col xs={24} md={12} xl={6}>
        <Typography.Text strong>分析类型</Typography.Text>
        <Select aria-label="关系分析类型" mode="multiple" maxCount={3} value={analyses} options={analysisOptions} onChange={(values) => onConfigChange({ analyses: values.slice(0, 3) })} className="full-width" />
      </Col>
      <Col xs={24} md={12} xl={6}>
        <Typography.Text strong>分组</Typography.Text>
        <Select aria-label="关系分析分组" value={groupBy} options={groupOptions} onChange={(value) => onConfigChange({ groupBy: value })} className="full-width" />
      </Col>
      <Col xs={24} md={12} xl={6}>
        <Typography.Text strong>Max Points（100–20000）</Typography.Text>
        <InputNumber aria-label="关系分析最大点数" min={100} max={20_000} step={100} precision={0} value={maxPoints} onChange={(value) => onConfigChange({ maxPoints: value ?? 10_000 })} className="full-width" />
      </Col>
    </Row>
    {correlationRequested && <>
      <Alert
        style={{ marginTop: 12 }}
        type={correlationRuleCode && correlationRuleVersion ? "success" : "warning"}
        showIcon
        message={correlationRuleCode && correlationRuleVersion ? `当前相关性规则：${correlationRuleCode}@${correlationRuleVersion}` : "没有找到唯一可用的相关性规则"}
        description={correlationRuleCode && correlationRuleVersion ? "服务器会在执行时核对当前厂家、产品和参数范围。" : "请联系管理员启用规则后刷新。"}
      />
      <Collapse style={{ marginTop: 12 }} size="small" items={[{ key: "rule", label: "高级设置：查看或调试规则版本", children: <Row gutter={[12, 12]}>
        <Col xs={24} md={12}><Typography.Text strong>规则编号</Typography.Text><Input aria-label="Correlation Rule Code" value={correlationRuleCode} onChange={(event) => onConfigChange({ correlation: { ...config.correlation, ruleCode: event.target.value.toUpperCase() } })} placeholder="系统自动匹配" /></Col>
        <Col xs={24} md={12}><Typography.Text strong>规则版本</Typography.Text><Input aria-label="Correlation Rule Version" value={correlationRuleVersion} onChange={(event) => onConfigChange({ correlation: { ...config.correlation, versionCode: event.target.value } })} placeholder="系统自动匹配" /></Col>
      </Row> }]} />
    </>}
    <Space wrap style={{ marginTop: 12 }}>
      <Button type="primary" aria-label="执行参数关系分析" loading={mutation.isPending} disabled={!canRun} onClick={execute}>执行参数关系分析</Button>
      <Typography.Text type="secondary">完整沿用统一 Context 的 8 组筛选；结果的配对、采样、OOS 保留和 Correlation 均由后端决定。</Typography.Text>
    </Space>
    {!xParameter && <Alert type="info" showIcon message="请选择 X 参数" style={{ marginTop: 12 }} />}
    {!yParameters.length && <Alert type="info" showIcon message="请选择至少 1 个 Y 参数" style={{ marginTop: 12 }} />}
    {isStale && <Alert type="warning" showIcon message="当前参数关系结果已过期" description="Context、X/Y、分析类型、分组或 Max Points 已变化；旧结果保留供核对，需手动重新执行。" style={{ marginTop: 12 }} />}
    {mutation.isError && <Alert
      type="error"
      showIcon
      message={ruleGateError ? "Correlation 规则未批准" : "参数关系分析失败"}
      description={<Space direction="vertical" size={4}>
        <Typography.Text>{error instanceof Error ? error.message : "未知错误"}</Typography.Text>
        <Typography.Text>错误码：{apiError?.code ?? "UNKNOWN_ERROR"}</Typography.Text>
        <Typography.Text>建议操作：{apiError?.recommendedAction ?? "请核对精确参数身份、筛选或联系管理员"}</Typography.Text>
        {apiError?.retryable && submittedRequest && <Button size="small" icon={<ReloadOutlined />} aria-label="重试参数关系分析" onClick={retrySubmitted}>重试上次请求</Button>}
      </Space>}
      style={{ marginTop: 12 }}
    />}

    {mutation.data && <Space direction="vertical" size="large" style={{ width: "100%", marginTop: 16 }}>
      <Space wrap>
        <Tag>合同 {mutation.data.contract_version}</Tag>
        <Tag>分组 {mutation.data.group_by}</Tag>
        <Tag>Context {mutation.data.filter_summary.context_hash.slice(0, 12)}…</Tag>
        <Tag>Filter {mutation.data.filter_summary.filter_hash.slice(0, 12)}…</Tag>
        {mutation.data.capabilities.map((item) => <Tag key={item.code} color={item.status === "AVAILABLE" ? "success" : "warning"}>{item.code} {item.status}</Tag>)}
      </Space>
      {mutation.data.warnings.length > 0 && <Alert type="warning" showIcon message="服务端提示" description={mutation.data.warnings.join("、")} />}
      {analyses.some((analysis) => analysis === "SCATTER" || analysis === "TREND") && missingFormalSpecIdentities.length > 0 && <Alert
        type="warning"
        showIcon
        message="部分参数没有唯一 Released Formal Spec"
        description={`NO_SPEC：${missingFormalSpecIdentities.map((item) => `${item.name}（${item.formal_spec_reason_codes.join("/") || "FORMAL_RELEASED_SPEC_NOT_FOUND"}）`).join("、")}。图表不会使用 Program Limit 冒充正式规格；仅保留服务端测量状态已明确标记的 OOS。`}
      />}
      {mutation.data.sampling_summary.sampled
        ? <Alert type="warning" showIcon message="服务端已执行确定性采样" description={`方法 ${mutation.data.sampling_summary.method ?? "未标注"}；返回 ${mutation.data.sampling_summary.returned_points} / 原始 ${mutation.data.sampling_summary.original_points} 点；保留 OOS ${mutation.data.sampling_summary.preserved_out_of_spec_points} 点。`} />
        : <Typography.Text type="secondary">服务端未采样：{mutation.data.sampling_summary.returned_points} / {mutation.data.sampling_summary.original_points} 点；保留 OOS {mutation.data.sampling_summary.preserved_out_of_spec_points} 点。</Typography.Text>}
      {correlationRequested && <Alert type={correlationCapability?.status === "AVAILABLE" ? "success" : "info"} showIcon message={`Correlation capability：${correlationCapability?.status ?? "服务端未声明"}`} description={correlationCapability?.message ?? correlationCapability?.reason_code ?? `Rule ${correlationRuleCode}:${correlationRuleVersion}`} />}

      <Card size="small" title="参数精确身份">
        <Table rowKey="key" columns={identityColumns} dataSource={identityRows} pagination={false} size="small" scroll={{ x: 1080 }} />
      </Card>

      {(allScatterPoints.length > 0 || allTrendPoints.length > 0 || allCorrelations.length > 0) && <Card size="small" title="纯显示控制（不改变后端 Context）">
        <Row gutter={[12, 12]}>
          <Col xs={24} md={12} xl={8}>
            <Typography.Text strong>显示分组</Typography.Text>
            <Select aria-label="参数关系显示分组" mode="multiple" allowClear value={displayGroups} options={selectOptions(availableGroups)} onChange={(values) => onConfigChange({ scatterDataset: "", displayGroups: values })} className="full-width" placeholder="空=全部分组" />
          </Col>
          <Col xs={24} md={12} xl={8}>
            <Typography.Text strong>显示规格状态</Typography.Text>
            <Checkbox.Group aria-label="参数关系点显示" value={pointVisibility} options={visibilityOptions} onChange={(values) => onConfigChange({ pointVisibility: values as PointVisibility[] })} />
          </Col>
          <Col xs={24} md={12} xl={8}>
            <Space wrap>
              <Checkbox checked={displayState.brushEnabled} onChange={(event) => onDisplayStateChange({ brushEnabled: event.target.checked })}>Scatter Brush</Checkbox>
              <Checkbox checked={displayState.showSpecOverlay} onChange={(event) => onDisplayStateChange({ showSpecOverlay: event.target.checked })}>Released Formal Spec</Checkbox>
            </Space>
          </Col>
          <Col xs={24} md={12} xl={6}>
            <Typography.Text strong>Y 轴最小值</Typography.Text>
            <InputNumber aria-label="参数关系 Y 轴最小值" value={displayState.yAxisMin} placeholder="自动" onChange={(value) => onDisplayStateChange({ yAxisMin: value ?? null })} className="full-width" />
          </Col>
          <Col xs={24} md={12} xl={6}>
            <Typography.Text strong>Y 轴最大值</Typography.Text>
            <InputNumber aria-label="参数关系 Y 轴最大值" value={displayState.yAxisMax} placeholder="自动" onChange={(value) => onDisplayStateChange({ yAxisMax: value ?? null })} className="full-width" />
          </Col>
        </Row>
      </Card>}

      {allScatterPoints.length > 0 && <Card id="parameter-relationship-scatter" size="small" title="Scatter">
        <Space direction="vertical" size="middle" style={{ width: "100%" }}>
          <Select aria-label="Scatter Y 参数" value={scatterY} options={selectOptions(returnedYParameters)} onChange={(value) => onConfigChange({ scatterDataset: "", scatterY: value })} style={{ minWidth: 220 }} />
          {selectedScatter.length ? <EChart option={scatterOption} ariaLabel={`${xParameter} / ${scatterY} Scatter`} onEvents={chartEvents} /> : <Empty description="当前显示条件无 Scatter 点" />}
          <Typography.Text type="secondary">红色点是后端按 Released Formal Spec 或测量状态标记的 X/Y out-of-spec；虚线只来自唯一且兼容的 Released Formal Spec，绝不回退 Program Limit；点击只使用后端 drilldown_key 钻取。</Typography.Text>
        </Space>
      </Card>}

      {allTrendPoints.length > 0 && <Card size="small" title="Trend">
        <Space direction="vertical" size="middle" style={{ width: "100%" }}>
          <Select aria-label="Trend 参数" value={trendParameter} options={selectOptions(returnedTrendParameters)} onChange={(value) => onConfigChange({ trendParameter: value })} style={{ minWidth: 220 }} />
          {selectedTrend.length ? <EChart option={trendOption} ariaLabel={`${trendParameter} Trend`} onEvents={chartEvents} /> : <Empty description="当前显示条件无 Trend 点" />}
          <Typography.Text type="secondary">X 轴使用后端稳定 ordinal；来源 Sequence 只在 Tooltip 中展示，重复值不会被合并。排序口径：{mutation.data.trend_order_basis}。</Typography.Text>
        </Space>
      </Card>}

      {correlationRequested && <Card size="small" title="Correlation（后端 Rule 结果）">
        <Space direction="vertical" size="middle" style={{ width: "100%" }}>
          <Row gutter={[12, 12]} align="middle">
            <Col xs={24} md={14} xl={10}>
              <Typography.Text strong>Matrix Dataset / 分组</Typography.Text>
              <Select aria-label="Correlation Matrix 分组" value={activeCorrelationScope} options={correlationScopeOptions} onChange={(value) => onConfigChange({ correlationScope: value })} className="full-width" />
            </Col>
            <Col xs={24} md={10} xl={6}>
              <Typography.Text strong>Heatmap |r| 最小值</Typography.Text>
              <InputNumber
                aria-label="Correlation 绝对值阈值"
                min={0}
                max={1}
                step={0.05}
                precision={3}
                value={displayState.correlationMinAbs}
                onChange={(value) => onDisplayStateChange({ correlationMinAbs: value ?? 0 })}
                className="full-width"
              />
            </Col>
            <Col xs={24} xl={8}>
              <Typography.Text type="secondary">后端返回已选 2–6 参数的完整对称 N×N Matrix；阈值只筛选显示，不改 Pearson 或 Pairwise N。点击任意非对角单元格进入对应 Scatter。</Typography.Text>
            </Col>
          </Row>
          {correlationHeatmapData.length
            ? <EChart option={correlationOption} ariaLabel="Correlation Heatmap" onEvents={correlationEvents} />
            : <Empty description={`|r| ≥ ${displayState.correlationMinAbs} 的可用 Correlation 为 0`} />}
          <Typography.Text type="secondary">Heatmap 显示 {correlationHeatmapData.length} / {scopeCorrelations.length} 个当前 Matrix 单元；下表保留所有 Dataset/分组的 Pairwise N、状态、方法和不可评估原因。</Typography.Text>
        <Table rowKey={(row) => `${row.dataset_id}:${row.version_no}:${row.group_key}:${row.x_parameter}:${row.y_parameter}`} columns={correlationColumns} dataSource={allCorrelations} pagination={false} size="small" scroll={{ x: 1620 }} locale={{ emptyText: <Empty description="后端未返回 Correlation 结果" /> }} />
        </Space>
      </Card>}

      {!identityRows.length && !allScatterPoints.length && !allTrendPoints.length && !allCorrelations.length && <Empty description="当前范围无参数关系结果" />}
    </Space>}
  </Card>;
}
