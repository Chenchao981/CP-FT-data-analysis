import { AnalysisEvidence } from "../../components/AnalysisEvidence";
import { explainAnalysisReason } from "./analysisReasons";
import { ReloadOutlined } from "@ant-design/icons";
import { useMutation } from "@tanstack/react-query";
import { Alert, Button, Card, Col, Collapse, Empty, Input, InputNumber, Row, Select, Space, Table, Tag, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import type { EChartsOption } from "echarts";
import { useEffect, useMemo, useRef, useState } from "react";

import {
  analyzeDatasetParameters,
  type DatasetAnalysisOverallResult,
  type DatasetParameterAnalysis,
  type DatasetParameterAnalysisItem,
  type DatasetParameterAnalysisRequest,
  type DatasetParameterAnalysisType,
  type DatasetMeasurementAggregateContext,
  type DatasetReference,
} from "../../api/datasets";
import { ApiError } from "../../api/auth";
import { EChart, type EChartEventMap } from "../../components/EChart";
import { AnalysisResultFrame } from "../../components/AnalysisResultFrame";
import { drilldownKeyFromChartEvent } from "./chartDrilldown";
import { ANALYSIS_COMPONENT_DEFAULTS, type ParameterAnalysisViewConfig } from "./context/analysisViewConfig";
import type { AnalysisDisplayState } from "./context/analysisViewState";
import type { AnalyticsAggregateDrilldown } from "./sections/sectionTypes";

export interface ParameterAnalysisPanelProps {
  datasets: DatasetReference[];
  parameterOptions: string[];
  parameters: string[];
  onParametersChange: (parameters: string[]) => void;
  lotIds: string[];
  waferIds: string[];
  binCodes: string[];
  overallResults: DatasetAnalysisOverallResult[];
  onOverallResultsChange: (results: DatasetAnalysisOverallResult[]) => void;
  sourceIds: string[];
  testerIds: string[];
  programVersions: string[];
  testConditions: string[];
  onOpenDrilldown: (drilldownKey: string) => void;
  onOpenAggregateDrilldown: (target: AnalyticsAggregateDrilldown) => void;
  displayState: AnalysisDisplayState;
  onDisplayStateChange: (patch: Partial<AnalysisDisplayState>) => void;
  config?: ParameterAnalysisViewConfig;
  onConfigChange?: (patch: Partial<ParameterAnalysisViewConfig>) => void;
  autoRunKey?: string;
}

interface AnalysisRow {
  key: string;
  datasetKey: string;
  datasetLabel: string;
  item: DatasetParameterAnalysisItem;
  analysis: DatasetParameterAnalysis;
}

const analysisOptions: Array<{ label: string; value: DatasetParameterAnalysisType }> = [
  { label: "描述统计", value: "DESCRIPTIVE" },
  { label: "箱线图", value: "BOX_PLOT" },
  { label: "直方图", value: "HISTOGRAM" },
  { label: "Normal Fit", value: "NORMAL_FIT" },
  { label: "Capability", value: "CAPABILITY" },
];
const overallResultOptions: Array<{ label: string; value: DatasetAnalysisOverallResult }> = [
  { label: "PASS", value: "PASS" },
  { label: "FAIL", value: "FAIL" },
  { label: "UNKNOWN", value: "UNKNOWN" },
  { label: "ABORT", value: "ABORT" },
];

const datasetKey = (datasetId: number, versionNo: number) => `${datasetId}:${versionNo}`;
const datasetLabel = (datasetId: number, versionNo: number) => `Dataset #${datasetId} / V${versionNo}`;
const formatNumber = (value: number | null) => value == null ? "—" : String(value);
const selectOptions = (values: string[]) => values.map((value) => ({ label: value, value }));
const filterValues = (values: string[]) => values.length ? values.join("、") : "全部";
const cloneRequest = (request: DatasetParameterAnalysisRequest): DatasetParameterAnalysisRequest => ({
  datasets: request.datasets.map((item) => ({ ...item })),
  group_by: request.group_by,
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
  parameters: [...request.parameters],
  analyses: [...request.analyses],
  box_plot: { ...request.box_plot },
  histogram: { ...request.histogram },
  normal_fit: { ...request.normal_fit },
  capability: { ...request.capability },
});

const statusCounts = (analysis: DatasetParameterAnalysis) => analysis.status_counts.length
  ? <Space size={[4, 4]} wrap>{analysis.status_counts.map((item) => <Tag key={item.status}>{item.status} {item.count}</Tag>)}</Space>
  : "—";

const intervalLabel = (lower: number, upper: number, lowerInclusive: boolean, upperInclusive: boolean) =>
  `${lowerInclusive ? "[" : "("}${lower}, ${upper}${upperInclusive ? "]" : ")"}`;

export function ParameterAnalysisPanel({
  datasets,
  parameterOptions,
  parameters,
  onParametersChange,
  lotIds,
  waferIds,
  binCodes,
  overallResults,
  onOverallResultsChange,
  sourceIds,
  testerIds,
  programVersions,
  testConditions,
  onOpenDrilldown,
  onOpenAggregateDrilldown,
  displayState,
  onDisplayStateChange,
  config: controlledConfig,
  onConfigChange: controlledOnConfigChange,
  autoRunKey,
}: ParameterAnalysisPanelProps) {
  const [localConfig, setLocalConfig] = useState<ParameterAnalysisViewConfig>(() => ({
    ...ANALYSIS_COMPONENT_DEFAULTS.parameterAnalysis,
    analyses: [...ANALYSIS_COMPONENT_DEFAULTS.parameterAnalysis.analyses],
    boxPlot: { ...ANALYSIS_COMPONENT_DEFAULTS.parameterAnalysis.boxPlot }, histogram: { ...ANALYSIS_COMPONENT_DEFAULTS.parameterAnalysis.histogram },
    normalFit: { ...ANALYSIS_COMPONENT_DEFAULTS.parameterAnalysis.normalFit }, capability: { ...ANALYSIS_COMPONENT_DEFAULTS.parameterAnalysis.capability },
  }));
  const config = controlledConfig ?? localConfig;
  const onConfigChange = (patch: Partial<ParameterAnalysisViewConfig>) => {
    if (!controlledConfig) setLocalConfig((current) => ({ ...current, ...patch }));
    controlledOnConfigChange?.(patch);
  };
  const analyses = [...config.analyses] as DatasetParameterAnalysisType[];
  const [submittedRequest, setSubmittedRequest] = useState<DatasetParameterAnalysisRequest | null>(null);
  const [submittedSignature, setSubmittedSignature] = useState<string | null>(null);
  const boxParameterChoice = config.boxParameter || undefined;
  const histogramDatasetChoice = config.histogramDataset || undefined;
  const histogramParameterChoice = config.histogramParameter || undefined;
  const normalFitDatasetChoice = config.normalFitDataset || undefined;
  const normalFitParameterChoice = config.normalFitParameter || undefined;
  const boxRuleCode = config.boxPlot.ruleCode;
  const boxRuleVersion = config.boxPlot.versionCode;
  const histogramRuleCode = config.histogram.ruleCode;
  const histogramRuleVersion = config.histogram.versionCode;
  const normalFitRuleCode = config.normalFit.ruleCode;
  const normalFitRuleVersion = config.normalFit.versionCode;
  const capabilityRuleCode = config.capability.ruleCode;
  const capabilityRuleVersion = config.capability.versionCode;
  const capabilityMethod = config.capability.method;

  const currentRequest = useMemo<DatasetParameterAnalysisRequest>(() => ({
    datasets: datasets.map((item) => ({ ...item })),
    group_by: config.groupBy,
    filters: {
      lot_ids: [...lotIds],
      wafer_ids: [...waferIds],
      bin_codes: [...binCodes],
      overall_results: [...overallResults],
      source_ids: [...sourceIds],
      tester_ids: [...testerIds],
      program_versions: [...programVersions],
      test_conditions: [...testConditions],
    },
    parameters: [...parameters],
    analyses: [...analyses],
    box_plot: analyses.includes("BOX_PLOT") ? { rule_code: boxRuleCode, version_code: boxRuleVersion } : {},
    histogram: analyses.includes("HISTOGRAM") ? { rule_code: histogramRuleCode, version_code: histogramRuleVersion } : {},
    normal_fit: analyses.includes("NORMAL_FIT") ? { rule_code: normalFitRuleCode, version_code: normalFitRuleVersion } : {},
    capability: analyses.includes("CAPABILITY") ? { method: capabilityMethod, rule_code: capabilityRuleCode, version_code: capabilityRuleVersion } : {},
  }), [analyses, binCodes, boxRuleCode, boxRuleVersion, capabilityMethod, capabilityRuleCode, capabilityRuleVersion, config.groupBy, datasets, histogramRuleCode, histogramRuleVersion, lotIds, normalFitRuleCode, normalFitRuleVersion, overallResults, parameters, programVersions, sourceIds, testConditions, testerIds, waferIds]);
  const currentSignature = JSON.stringify(currentRequest);
  const exactRulesComplete = (!analyses.includes("BOX_PLOT") || Boolean(boxRuleCode && boxRuleVersion))
    && (!analyses.includes("HISTOGRAM") || Boolean(histogramRuleCode && histogramRuleVersion))
    && (!analyses.includes("NORMAL_FIT") || Boolean(normalFitRuleCode && normalFitRuleVersion))
    && (!analyses.includes("CAPABILITY") || Boolean(capabilityRuleCode && capabilityRuleVersion));
  const canRun = datasets.length >= 1 && datasets.length <= 8
    && parameters.length >= 1 && parameters.length <= 20
    && analyses.length >= 1 && analyses.length <= 5
    && exactRulesComplete;

  const mutation = useMutation({
    mutationFn: analyzeDatasetParameters,
    retry: false,
  });

  const execute = () => {
    if (!canRun) return;
    const snapshot = cloneRequest(currentRequest);
    setSubmittedRequest(snapshot);
    setSubmittedSignature(JSON.stringify(snapshot));
    mutation.mutate(snapshot);
  };
  const handledAutoRun = useRef<string | undefined>(undefined);
  useEffect(() => {
    if (!autoRunKey || !canRun || handledAutoRun.current === autoRunKey) return;
    const timeout = window.setTimeout(() => {
      if (handledAutoRun.current === autoRunKey) return;
      handledAutoRun.current = autoRunKey;
      execute();
    }, 0);
    return () => window.clearTimeout(timeout);
  // execute is intentionally represented by the complete request signature.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoRunKey, canRun, currentSignature]);
  const retrySubmitted = () => {
    if (submittedRequest) mutation.mutate(cloneRequest(submittedRequest));
  };

  const rows = useMemo<AnalysisRow[]>(() => mutation.data?.items.flatMap((item) =>
    item.parameters.map((analysis) => ({
      key: `${item.dataset_id}-${item.version_no}-${analysis.identity.name}`,
      datasetKey: datasetKey(item.dataset_id, item.version_no),
      datasetLabel: datasetLabel(item.dataset_id, item.version_no),
      item,
      analysis,
    }))) ?? [], [mutation.data]);
  const resultParameters = Array.from(new Set(rows.map((row) => row.analysis.identity.name)));
  const resultDatasets = Array.from(new Map(rows.map((row) => [row.datasetKey, row.datasetLabel])).entries());
  const isStale = Boolean(mutation.data && submittedSignature && submittedSignature !== currentSignature);

  const descriptiveRows = rows.filter((row) => row.analysis.descriptive !== null);
  const zeroNumericRows = descriptiveRows.filter((row) => row.analysis.descriptive?.numeric_count === 0);
  const boxParameter = resultParameters.includes(boxParameterChoice ?? "") ? boxParameterChoice! : resultParameters[0];
  const boxRows = rows.filter((row) => row.analysis.identity.name === boxParameter && row.analysis.box_plot !== null);
  const histogramDataset = resultDatasets.some(([key]) => key === histogramDatasetChoice)
    ? histogramDatasetChoice!
    : resultDatasets[0]?.[0];
  const histogramParameter = resultParameters.includes(histogramParameterChoice ?? "")
    ? histogramParameterChoice!
    : resultParameters[0];
  const histogramRow = rows.find((row) => row.datasetKey === histogramDataset
    && row.analysis.identity.name === histogramParameter
    && row.analysis.histogram !== null);
  const capabilityRows = rows.filter((row) => row.analysis.capability !== null);
  const normalFitRows = rows.filter((row) => row.analysis.normal_fit !== null);
  const normalFitDataset = resultDatasets.some(([key]) => key === normalFitDatasetChoice)
    ? normalFitDatasetChoice!
    : resultDatasets[0]?.[0];
  const normalFitParameter = resultParameters.includes(normalFitParameterChoice ?? "")
    ? normalFitParameterChoice!
    : resultParameters[0];
  const normalFitRow = rows.find((row) => row.datasetKey === normalFitDataset
    && row.analysis.identity.name === normalFitParameter
    && row.analysis.normal_fit !== null);

  const chartEvents = useMemo<EChartEventMap>(() => ({
    click: (payload) => {
      const key = drilldownKeyFromChartEvent(payload);
      if (key) onOpenDrilldown(key);
    },
  }), [onOpenDrilldown]);
  const openAggregateContext = (aggregate: DatasetMeasurementAggregateContext) => {
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
  };
  const histogramEvents = useMemo<EChartEventMap>(() => ({
    click: (payload) => {
      const aggregate = (payload as { data?: { aggregateContext?: DatasetMeasurementAggregateContext | null } })?.data?.aggregateContext;
      if (aggregate) openAggregateContext(aggregate);
    },
  // The opener is supplied by the Workbench and follows the current controlled Context.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }), [onOpenAggregateDrilldown]);

  const boxOption = useMemo<EChartsOption>(() => ({
    tooltip: { trigger: "item" },
    grid: { left: 72, right: 28, top: 34, bottom: 70 },
    xAxis: {
      type: "category",
      data: boxRows.map((row) => row.datasetLabel),
      axisLabel: { rotate: boxRows.length > 3 ? 30 : 0, hideOverlap: true },
    },
    yAxis: {
      type: "value",
      name: boxRows[0]?.analysis.identity.unit ?? undefined,
      min: displayState.yAxisMin ?? undefined,
      max: displayState.yAxisMax ?? undefined,
    },
    toolbox: { feature: { saveAsImage: { name: `${boxParameter ?? "parameter"}-box-plot` } } },
    dataZoom: boxRows.length > 6 ? [{ type: "inside" }, { type: "slider", height: 18, bottom: 8 }] : undefined,
    series: [{
      name: boxParameter ?? "参数",
      type: "boxplot",
      data: boxRows.map((row) => {
        const box = row.analysis.box_plot!;
        return [box.lower_whisker, box.q1, box.median, box.q3, box.upper_whisker];
      }),
    }, {
      name: "离群点 evidence",
      type: "scatter",
      symbolSize: 9,
      itemStyle: { color: "#d64545" },
      data: boxRows.flatMap((row, datasetIndex) => row.analysis.box_plot!.outlier_evidence.map((point) => ({
        value: [datasetIndex, point.value],
        drilldownKey: point.drilldown_key,
        measurementId: point.measurement_id,
        specStatus: point.spec_status,
      }))),
    }],
  }), [boxParameter, boxRows, displayState.yAxisMax, displayState.yAxisMin]);

  const histogram = histogramRow?.analysis.histogram;
  const histogramLsl = histogramRow?.analysis.identity.formal_lsl ?? null;
  const histogramUsl = histogramRow?.analysis.identity.formal_usl ?? null;
  const histogramLimitMarker = (name: "LSL" | "USL", limit: number | null) => {
    if (limit == null || !histogram?.bins.length) return [];
    let index = histogram.bins.findIndex((bin) => limit >= bin.lower_bound && (limit < bin.upper_bound || (bin.upper_inclusive && limit <= bin.upper_bound)));
    if (index < 0) index = limit < histogram.bins[0].lower_bound ? 0 : histogram.bins.length - 1;
    return [{ name, xAxis: index, label: { formatter: `${name} ${limit}` } }];
  };
  const histogramOption = useMemo<EChartsOption>(() => ({
    tooltip: { trigger: "axis" },
    grid: { left: 70, right: 24, top: 34, bottom: 92 },
    xAxis: {
      type: "category",
      name: histogramRow?.analysis.identity.unit ?? undefined,
      data: histogram?.bins.map((bin) => intervalLabel(
        bin.lower_bound,
        bin.upper_bound,
        bin.lower_inclusive,
        bin.upper_inclusive,
      )) ?? [],
      axisLabel: { rotate: 45, hideOverlap: true },
    },
    yAxis: {
      type: "value",
      name: "Count",
      minInterval: 1,
      min: displayState.yAxisMin ?? undefined,
      max: displayState.yAxisMax ?? undefined,
    },
    toolbox: { feature: { saveAsImage: { name: `${histogramParameter ?? "parameter"}-histogram` } } },
    dataZoom: (histogram?.bins.length ?? 0) > 20 ? [{ type: "inside" }, { type: "slider", height: 18, bottom: 8 }] : undefined,
    series: [{
      name: "后端分箱计数",
      type: "bar",
      data: histogram?.bins.map((bin) => ({
        value: bin.count,
        aggregateContext: bin.aggregate_drilldown_context,
        specRegion: bin.spec_region,
        itemStyle: bin.spec_region === "OUT_OF_SPEC"
          ? { color: "#d64545" }
          : bin.spec_region === "CROSSES_SPEC"
            ? { color: "#f0a429" }
            : undefined,
      })) ?? [],
      markLine: histogramLsl == null && histogramUsl == null ? undefined : {
        symbol: "none",
        data: [
          ...histogramLimitMarker("LSL", histogramLsl),
          ...histogramLimitMarker("USL", histogramUsl),
        ],
      },
    }],
  }), [displayState.yAxisMax, displayState.yAxisMin, histogram, histogramLsl, histogramParameter, histogramRow, histogramUsl]);
  const normalFit = normalFitRow?.analysis.normal_fit;
  const normalFitLsl = normalFitRow?.analysis.identity.formal_lsl ?? null;
  const normalFitUsl = normalFitRow?.analysis.identity.formal_usl ?? null;
  const normalFitOption = useMemo<EChartsOption>(() => ({
    animation: false,
    tooltip: { trigger: "axis" },
    grid: { left: 78, right: 28, top: 34, bottom: 72 },
    xAxis: { type: "value", name: normalFitRow?.analysis.identity.unit ?? undefined, scale: true },
    yAxis: {
      type: "value",
      name: "Probability density",
      min: displayState.yAxisMin ?? 0,
      max: displayState.yAxisMax ?? undefined,
    },
    toolbox: { feature: { saveAsImage: { name: `${normalFitParameter ?? "parameter"}-normal-fit` } } },
    dataZoom: (normalFit?.points.length ?? 0) > 50 ? [{ type: "inside" }, { type: "slider", height: 18, bottom: 8 }] : undefined,
    series: [{
      name: normalFit?.method ?? "NORMAL_FIT_MLE_V1",
      type: "line",
      showSymbol: false,
      smooth: true,
      data: normalFit?.points.map((point) => [point.x, point.probability_density]) ?? [],
      markLine: normalFitLsl == null && normalFitUsl == null ? undefined : {
        symbol: "none",
        data: [
          ...(normalFitLsl == null ? [] : [{ name: "LSL", xAxis: normalFitLsl }]),
          ...(normalFitUsl == null ? [] : [{ name: "USL", xAxis: normalFitUsl }]),
        ],
      },
    }, {
      name: "Observed evidence",
      type: "scatter",
      symbolSize: 9,
      data: normalFit?.observed_evidence.map((point) => ({
        value: [point.value, 0],
        drilldownKey: point.drilldown_key,
        measurementId: point.measurement_id,
        specStatus: point.spec_status,
        itemStyle: { color: point.spec_status === "OUT_OF_SPEC" ? "#d64545" : "#247ba0" },
      })) ?? [],
    }],
  }), [displayState.yAxisMax, displayState.yAxisMin, normalFit, normalFitLsl, normalFitParameter, normalFitRow, normalFitUsl]);

  const descriptiveColumns: ColumnsType<AnalysisRow> = [
    { title: "Dataset", dataIndex: "datasetLabel", width: 185, fixed: "left" },
    { title: "参数", width: 150, render: (_, row) => row.analysis.identity.name },
    { title: "单位", width: 85, render: (_, row) => row.analysis.identity.unit ?? "—" },
    { title: "行数", width: 90, render: (_, row) => row.analysis.descriptive?.row_count ?? "—" },
    { title: "数值数", width: 90, render: (_, row) => row.analysis.descriptive?.numeric_count ?? "—" },
    { title: "非数值/未纳入统计", width: 155, render: (_, row) => row.analysis.descriptive?.excluded_count ?? "—" },
    { title: "最小", width: 105, render: (_, row) => formatNumber(row.analysis.descriptive?.minimum ?? null) },
    { title: "最大", width: 105, render: (_, row) => formatNumber(row.analysis.descriptive?.maximum ?? null) },
    { title: "平均", width: 105, render: (_, row) => formatNumber(row.analysis.descriptive?.average ?? null) },
    { title: "样本标准差", width: 120, render: (_, row) => formatNumber(row.analysis.descriptive?.sample_stddev ?? null) },
    { title: "测量状态", width: 300, render: (_, row) => statusCounts(row.analysis) },
  ];
  const filterSummaryColumns: ColumnsType<DatasetParameterAnalysisItem> = [
    { title: "Dataset", width: 185, fixed: "left", render: (_, item) => datasetLabel(item.dataset_id, item.version_no) },
    { title: "Lot", width: 180, render: (_, item) => filterValues(item.filter_summary.lot_ids) },
    { title: "Wafer", width: 180, render: (_, item) => filterValues(item.filter_summary.wafer_ids) },
    { title: "Bin", width: 150, render: (_, item) => filterValues(item.filter_summary.bin_codes) },
    { title: "总体结果", width: 180, render: (_, item) => filterValues(item.filter_summary.overall_results) },
    { title: "源文件", width: 200, render: (_, item) => filterValues(item.filter_summary.source_ids) },
    { title: "Tester", width: 180, render: (_, item) => filterValues(item.filter_summary.tester_ids) },
    { title: "Program", width: 180, render: (_, item) => filterValues(item.filter_summary.program_versions) },
    { title: "Test Condition", width: 220, render: (_, item) => filterValues(item.filter_summary.test_conditions) },
    { title: "命中 Unit", width: 110, render: (_, item) => item.filter_summary.matched_unit_count },
    { title: "候选测量值", width: 125, render: (_, item) => item.filter_summary.candidate_measurement_count },
  ];
  const identityColumns: ColumnsType<AnalysisRow> = [
    { title: "Dataset", dataIndex: "datasetLabel", width: 185, fixed: "left" },
    { title: "参数", width: 145, render: (_, row) => row.analysis.identity.name },
    { title: "Canonical Code", width: 170, render: (_, row) => row.analysis.identity.canonical_parameter_code ?? "—" },
    { title: "单位", width: 85, render: (_, row) => row.analysis.identity.unit ?? "—" },
    { title: "Program LSL / USL", width: 175, render: (_, row) => `${formatNumber(row.analysis.identity.program_lsl)} / ${formatNumber(row.analysis.identity.program_usl)}` },
    { title: "Formal Spec LSL / USL", width: 190, render: (_, row) => `${formatNumber(row.analysis.identity.formal_lsl)} / ${formatNumber(row.analysis.identity.formal_usl)}` },
    { title: "测试条件", width: 220, render: (_, row) => row.analysis.identity.test_condition ?? "—" },
    { title: "Spec Set", width: 150, render: (_, row) => row.analysis.identity.spec_set_ids.length ? row.analysis.identity.spec_set_ids.join("、") : "—" },
    { title: "限值来源", width: 180, render: (_, row) => row.analysis.identity.limit_source || "—" },
  ];
  const boxColumns: ColumnsType<AnalysisRow> = [
    { title: "Dataset", dataIndex: "datasetLabel", width: 185, fixed: "left" },
    { title: "原始最小", width: 105, render: (_, row) => formatNumber(row.analysis.box_plot?.minimum ?? null) },
    { title: "下须", width: 95, render: (_, row) => formatNumber(row.analysis.box_plot?.lower_whisker ?? null) },
    { title: "Q1", width: 90, render: (_, row) => formatNumber(row.analysis.box_plot?.q1 ?? null) },
    { title: "中位数", width: 95, render: (_, row) => formatNumber(row.analysis.box_plot?.median ?? null) },
    { title: "Q3", width: 90, render: (_, row) => formatNumber(row.analysis.box_plot?.q3 ?? null) },
    { title: "上须", width: 95, render: (_, row) => formatNumber(row.analysis.box_plot?.upper_whisker ?? null) },
    { title: "原始最大", width: 105, render: (_, row) => formatNumber(row.analysis.box_plot?.maximum ?? null) },
    { title: "离群点数", width: 105, render: (_, row) => row.analysis.box_plot?.outlier_count ?? "—" },
    { title: "方法", width: 180, render: (_, row) => row.analysis.box_plot?.method ?? "—" },
  ];
  const capabilityColumns: ColumnsType<AnalysisRow> = [
    { title: "Dataset", dataIndex: "datasetLabel", width: 185, fixed: "left" },
    { title: "参数", width: 140, render: (_, row) => row.analysis.identity.name },
    { title: "总状态", width: 120, render: (_, row) => <Tag>{row.analysis.capability?.status ?? "—"}</Tag> },
    { title: "Ppk 状态", width: 120, render: (_, row) => row.analysis.capability?.ppk_status ?? "—" },
    { title: "Ppk", width: 90, render: (_, row) => formatNumber(row.analysis.capability?.ppk ?? null) },
    { title: "Cpk 状态", width: 125, render: (_, row) => row.analysis.capability?.cpk_status ?? "—" },
    { title: "Cpk", width: 90, render: (_, row) => formatNumber(row.analysis.capability?.cpk ?? null) },
    { title: "LSL / USL", width: 150, render: (_, row) => `${formatNumber(row.analysis.capability?.lsl ?? null)} / ${formatNumber(row.analysis.capability?.usl ?? null)}` },
    { title: "样本 / 子组", width: 125, render: (_, row) => `${row.analysis.capability?.sample_count ?? 0} / ${row.analysis.capability?.subgroup_count ?? 0}` },
    { title: "规则", width: 245, render: (_, row) => row.analysis.capability?.rule_code ?? "后端未选择 Cpk 子组规则" },
    { title: "不适用原因", width: 330, render: (_, row) => row.analysis.capability?.reason_codes.length ? row.analysis.capability.reason_codes.map(explainAnalysisReason).join("、") : "—" },
    { title: "联动", width: 210, fixed: "right", render: (_, row) => {
      const context = row.analysis.capability?.drilldown_context;
      return <Space size={4}>
        <Button size="small" aria-label={`查看 ${row.analysis.identity.name} 分布异常`} onClick={() => {
          onConfigChange({
            histogramDataset: row.datasetKey,
            histogramParameter: row.analysis.identity.name,
            normalFitDataset: row.datasetKey,
            normalFitParameter: row.analysis.identity.name,
          });
          document.getElementById("parameter-distribution-card")?.scrollIntoView({ block: "start" });
        }}>分布/异常</Button>
        <Button size="small" aria-label={`打开 ${row.analysis.identity.name} Detail`} disabled={!context} onClick={() => {
          if (context) openAggregateContext(context);
        }}>Detail</Button>
      </Space>;
    } },
  ];
  const normalFitColumns: ColumnsType<AnalysisRow> = [
    { title: "Dataset", dataIndex: "datasetLabel", width: 185, fixed: "left" },
    { title: "参数", width: 140, render: (_, row) => row.analysis.identity.name },
    { title: "状态", width: 130, render: (_, row) => <Tag color={row.analysis.normal_fit?.status === "AVAILABLE" ? "success" : "warning"}>{row.analysis.normal_fit?.status ?? "—"}</Tag> },
    { title: "Sample Count", width: 120, render: (_, row) => row.analysis.normal_fit?.sample_count ?? "—" },
    { title: "Mean", width: 120, render: (_, row) => formatNumber(row.analysis.normal_fit?.mean ?? null) },
    { title: "MLE Stddev", width: 130, render: (_, row) => formatNumber(row.analysis.normal_fit?.standard_deviation ?? null) },
    { title: "Method", width: 190, render: (_, row) => row.analysis.normal_fit?.method ?? "—" },
    { title: "不适用原因", width: 260, render: (_, row) => row.analysis.normal_fit?.reason_code ? explainAnalysisReason(row.analysis.normal_fit.reason_code) : "—" },
  ];

  const error = mutation.error;
  const apiError = error instanceof ApiError ? error : null;
  const ruleApprovalPending = apiError?.code === "ANALYSIS_RULE_NOT_APPROVED";
  const nonEligibleCapabilities = capabilityRows.filter((row) => row.analysis.capability?.status !== "ELIGIBLE");

  return <AnalysisResultFrame
    title="参数统计与分布"
    scope="FORMAL"
    extra={<Tag color="blue">当前 {datasets.length} 个数据集 · 最多 20 个参数</Tag>}
    className="production-table-card"
  >
    <Row gutter={[12, 12]}>
      <Col xs={24} xl={9}>
        <Typography.Text strong>测试参数（执行时最多 20 个）</Typography.Text>
        <Select
          aria-label="参数分析参数"
          mode="multiple"
          allowClear
          maxCount={20}
          value={parameters}
          options={selectOptions(parameterOptions)}
          onChange={(values) => onParametersChange(values.slice(0, 20))}
          placeholder="在共享 Context 中选择参数"
          className="full-width"
        />
      </Col>
      <Col xs={24} sm={12} xl={7}>
        <Typography.Text strong>测试结果</Typography.Text>
        <Select
          aria-label="参数分析总体结果"
          mode="multiple"
          allowClear
          maxCount={4}
          value={overallResults}
          options={overallResultOptions}
          onChange={(values) => onOverallResultsChange(values.slice(0, 4))}
          placeholder="全部 PASS / FAIL / UNKNOWN / ABORT"
          className="full-width"
        />
      </Col>
      <Col xs={24} sm={12} xl={8}>
        <Typography.Text strong>分析类型</Typography.Text>
        <Select
          aria-label="参数分析类型"
          mode="multiple"
          allowClear
          maxCount={20}
          value={analyses}
          options={analysisOptions}
          onChange={(values) => onConfigChange({ analyses: values.slice(0, 5) })}
          placeholder="至少选择一种分析"
          className="full-width"
        />
      </Col>
    </Row>
    {analyses.some((analysis) => analysis !== "DESCRIPTIVE") && <>
      <Alert
        style={{ marginTop: 12 }}
        type={exactRulesComplete ? "success" : "warning"}
        showIcon
        message={exactRulesComplete ? "所选方法已具备分析规则" : "部分方法没有找到唯一可用规则"}
      />
      <Collapse style={{ marginTop: 12 }} size="small" items={[{ key: "rules", label: "高级设置：查看或调试规则版本", children: <>
        <Row gutter={[12, 12]}>
        {analyses.includes("BOX_PLOT") && <><Col xs={24} md={12}><Typography.Text strong>Box Rule Code</Typography.Text><Input aria-label="Box Rule Code" value={boxRuleCode} onChange={(event) => onConfigChange({ boxPlot: { ...config.boxPlot, ruleCode: event.target.value.toUpperCase() } })} placeholder="例如 CP_BOX_STANDARD" /></Col><Col xs={24} md={12}><Typography.Text strong>Box Version</Typography.Text><Input aria-label="Box Rule Version" value={boxRuleVersion} onChange={(event) => onConfigChange({ boxPlot: { ...config.boxPlot, versionCode: event.target.value } })} placeholder="例如 v1" /></Col></>}
        {analyses.includes("HISTOGRAM") && <><Col xs={24} md={12}><Typography.Text strong>Histogram Rule Code</Typography.Text><Input aria-label="Histogram Rule Code" value={histogramRuleCode} onChange={(event) => onConfigChange({ histogram: { ...config.histogram, ruleCode: event.target.value.toUpperCase() } })} /></Col><Col xs={24} md={12}><Typography.Text strong>Histogram Version</Typography.Text><Input aria-label="Histogram Rule Version" value={histogramRuleVersion} onChange={(event) => onConfigChange({ histogram: { ...config.histogram, versionCode: event.target.value } })} /></Col></>}
        {analyses.includes("NORMAL_FIT") && <><Col xs={24} md={12}><Typography.Text strong>Normal Fit Rule Code</Typography.Text><Input aria-label="Normal Fit Rule Code" value={normalFitRuleCode} onChange={(event) => onConfigChange({ normalFit: { ...config.normalFit, ruleCode: event.target.value.toUpperCase() } })} /></Col><Col xs={24} md={12}><Typography.Text strong>Normal Fit Version</Typography.Text><Input aria-label="Normal Fit Rule Version" value={normalFitRuleVersion} onChange={(event) => onConfigChange({ normalFit: { ...config.normalFit, versionCode: event.target.value } })} /></Col></>}
        {analyses.includes("CAPABILITY") && <><Col xs={24} md={8}><Typography.Text strong>Capability Method</Typography.Text><Select aria-label="Capability Method" value={capabilityMethod} options={[{ value: "CPK_POOLED_WITHIN_RUN_V1", label: "Pooled within Run" }, { value: "CPK_POOLED_WITHIN_LOT_WAFER_V1", label: "Pooled within Lot-Wafer" }]} onChange={(value) => onConfigChange({ capability: { ...config.capability, method: value } })} className="full-width" /></Col><Col xs={24} md={8}><Typography.Text strong>Capability Rule Code</Typography.Text><Input aria-label="Capability Rule Code" value={capabilityRuleCode} onChange={(event) => onConfigChange({ capability: { ...config.capability, ruleCode: event.target.value.toUpperCase() } })} /></Col><Col xs={24} md={8}><Typography.Text strong>Capability Version</Typography.Text><Input aria-label="Capability Rule Version" value={capabilityRuleVersion} onChange={(event) => onConfigChange({ capability: { ...config.capability, versionCode: event.target.value } })} /></Col></>}
        </Row>
      </> }]} />
    </>}
    <Space wrap style={{ marginTop: 12 }}>
      <Button type="primary" aria-label="执行参数分析" loading={mutation.isPending} disabled={!canRun} onClick={execute}>执行参数分析</Button>
    </Space>
    <Card size="small" title="显示范围" style={{ marginTop: 12 }}>
      <Row gutter={[12, 12]}>
        <Col xs={24} sm={12} xl={6}>
          <Typography.Text strong>Y 轴最小值</Typography.Text>
          <InputNumber
            aria-label="参数分析 Y 轴最小值"
            value={displayState.yAxisMin}
            placeholder="自动"
            onChange={(value) => onDisplayStateChange({ yAxisMin: value ?? null })}
            className="full-width"
          />
        </Col>
        <Col xs={24} sm={12} xl={6}>
          <Typography.Text strong>Y 轴最大值</Typography.Text>
          <InputNumber
            aria-label="参数分析 Y 轴最大值"
            value={displayState.yAxisMax}
            placeholder="自动"
            onChange={(value) => onDisplayStateChange({ yAxisMax: value ?? null })}
            className="full-width"
          />
        </Col>
      </Row>
    </Card>
    {!parameters.length && <Alert type="info" showIcon message="请选择 1–20 个分析参数后点击执行" style={{ marginTop: 12 }} />}
    {!analyses.length && <Alert type="warning" showIcon message="至少选择一种分析类型" style={{ marginTop: 12 }} />}
    {!exactRulesComplete && <Alert type="warning" showIcon message="所选统计方法没有可用规则" style={{ marginTop: 12 }} />}
    {isStale && <Alert
      type="warning"
      showIcon
      message="当前结果已过期，请重新执行"
      style={{ marginTop: 12 }}
    />}

    {mutation.isError && <Alert
      type="error"
      showIcon
      message={ruleApprovalPending ? "统计口径待业务批准" : "参数分析失败"}
      description={<Space direction="vertical" size={4}>
        {ruleApprovalPending && <Typography.Text>服务端已失败关闭本次统计；请完成 Owner、Validator、批准日期和正式 Golden 记录后再启用。</Typography.Text>}
        <Typography.Text>{error instanceof Error ? error.message : "未知错误"}</Typography.Text>
        <Typography.Text>错误代码：{apiError?.code ?? "UNKNOWN_ERROR"}</Typography.Text>
        <Typography.Text>HTTP：{apiError?.httpStatus ?? "—"}</Typography.Text>
        <Typography.Text>建议操作：{apiError?.recommendedAction ?? "请核对筛选条件或联系管理员"}</Typography.Text>
        <Typography.Text>可重试：{apiError?.retryable ? "是" : "否"}</Typography.Text>
        {apiError?.retryable && submittedRequest && <Button size="small" icon={<ReloadOutlined />} aria-label="重试参数分析" onClick={retrySubmitted}>重试上次请求</Button>}
      </Space>}
      style={{ marginTop: 12 }}
    />}

    {mutation.data && <Space direction="vertical" size="large" style={{ width: "100%", marginTop: 16 }}>
      <Space wrap>
        <Tag>合同 {mutation.data.contract_version}</Tag>
        <Tag>分组 {mutation.data.group_by}</Tag>
        <Tag color={mutation.data.compatibility === "COMPATIBLE" ? "success" : "default"}>兼容性 {mutation.data.compatibility}</Tag>
        <Tag color={mutation.data.dataset_context.current_published_verified ? "success" : "error"}>
          Current+PUBLISHED {mutation.data.dataset_context.current_published_verified ? "已验证" : "未验证"}
        </Tag>
        <Tag>Filter Hash {mutation.data.filter_summary.filter_hash.slice(0, 12)}…</Tag>
        <Tag>规则 {mutation.data.rule_context.capability_rule_approval_status}</Tag>
        <Typography.Text type="secondary">返回 {mutation.data.items.length} 个 Dataset、{rows.length} 个 Dataset-参数结果</Typography.Text>
      </Space>

      <Card size="small" title="可复现上下文">
        <Space direction="vertical" size={4}>
          <Typography.Text>计算时间（UTC）：{mutation.data.computed_at}</Typography.Text>
          <AnalysisEvidence source="所选正式数据及筛选范围" method="所选参数分析方法" inputCount={mutation.data.counts.input_units} includedCount={mutation.data.counts.included_units} excludedCount={mutation.data.counts.excluded_units} missingCount={mutation.data.counts.missing_measurements} />
          <Typography.Text>Spec：{mutation.data.rule_context.spec_versions.length ? mutation.data.rule_context.spec_versions.join("、") : "未使用"}</Typography.Text>
          <Typography.Text>评价规则：{mutation.data.rule_context.evaluation_rule_versions.length ? mutation.data.rule_context.evaluation_rule_versions.join("、") : "未使用"}</Typography.Text>
        </Space>
      </Card>

      {mutation.data.warnings.length > 0 && <Alert
        type="warning"
        showIcon
        message="分析能力提示"
        description={mutation.data.warnings.map(explainAnalysisReason).join("、")}
      />}

      <Card size="small" title="Dataset 筛选与命中摘要">
        <Table
          rowKey={(item) => `${item.dataset_id}-${item.version_no}`}
          columns={filterSummaryColumns}
          dataSource={mutation.data.items}
          pagination={false}
          scroll={{ x: 1310 }}
          size="small"
        />
      </Card>

      {rows.length > 0 && <Card size="small" title="参数身份与规格来源">
        <Table rowKey="key" columns={identityColumns} dataSource={rows} pagination={false} scroll={{ x: 1310 }} size="small" />
      </Card>}

      {zeroNumericRows.length > 0 && <Alert
        type="warning"
        showIcon
        message="当前范围没有可分析数值"
        description={`${zeroNumericRows.map((row) => `${row.datasetLabel} · ${row.analysis.identity.name}`).join("；")} 的 numeric_count 为 0；请检查筛选和测量状态。`}
      />}

      {descriptiveRows.length > 0 && <Card size="small" title="描述统计">
        <Table rowKey="key" columns={descriptiveColumns} dataSource={descriptiveRows} pagination={false} scroll={{ x: 1490 }} size="small" />
      </Card>}

      {rows.some((row) => row.analysis.box_plot !== null) && <Card size="small" title="箱线图">
        <Space direction="vertical" size="middle" style={{ width: "100%" }}>
          <Select aria-label="箱线图参数" value={boxParameter} options={selectOptions(resultParameters)} onChange={(value) => onConfigChange({ boxParameter: value })} style={{ minWidth: 220 }} />
          {boxRows.length ? <>
            <EChart option={boxOption} ariaLabel={`${boxParameter} 按 Dataset 的箱线图`} onEvents={chartEvents} />
            {boxRows.map((row) => row.analysis.box_plot?.outlier_sampling).filter(Boolean).map((sampling, index) => <Tag key={`${sampling!.method}-${index}`}>离群 evidence {sampling!.returned_points}/{sampling!.original_points}{sampling!.sampled ? "（已采样）" : "（完整）"}</Tag>)}
            <Table rowKey="key" columns={boxColumns} dataSource={boxRows} pagination={false} scroll={{ x: 1240 }} size="small" />
          </> : <Empty description="当前参数不适用于箱线图" />}
        </Space>
      </Card>}

      {rows.some((row) => row.analysis.histogram !== null) && <Card id="parameter-distribution-card" size="small" title="直方图（后端分箱）">
        <Space direction="vertical" size="middle" style={{ width: "100%" }}>
          <Space wrap>
            <Select aria-label="直方图 Dataset" value={histogramDataset} options={resultDatasets.map(([value, label]) => ({ value, label }))} onChange={(value) => onConfigChange({ histogramDataset: value })} style={{ minWidth: 220 }} />
            <Select aria-label="直方图参数" value={histogramParameter} options={selectOptions(resultParameters)} onChange={(value) => onConfigChange({ histogramParameter: value })} style={{ minWidth: 180 }} />
          </Space>
          {histogram ? <>
            {(histogramLsl == null && histogramUsl == null) && <Alert type="warning" showIcon message="当前参数没有可用正式规格" />}
            <EChart option={histogramOption} ariaLabel={`${histogramParameter} 在 ${histogramRow?.datasetLabel} 的后端分箱直方图`} onEvents={histogramEvents} />
            <Space wrap><Tag>{histogram.bin_count} 个分箱</Tag><Tag>{formatNumber(histogram.range_min)} – {formatNumber(histogram.range_max)}</Tag><Tag>{histogram.method}</Tag></Space>
          </> : <Empty description="当前 Dataset 与参数组合不适用于直方图" />}
        </Space>
      </Card>}

      {normalFitRows.length > 0 && <Card size="small" title="Normal Fit（服务端 MLE 拟合线）">
        <Space direction="vertical" size="middle" style={{ width: "100%" }}>
          <Space wrap>
            <Select aria-label="Normal Fit Dataset" value={normalFitDataset} options={resultDatasets.map(([value, label]) => ({ value, label }))} onChange={(value) => onConfigChange({ normalFitDataset: value })} style={{ minWidth: 220 }} />
            <Select aria-label="Normal Fit 参数" value={normalFitParameter} options={selectOptions(resultParameters)} onChange={(value) => onConfigChange({ normalFitParameter: value })} style={{ minWidth: 180 }} />
          </Space>
          {normalFit?.status === "AVAILABLE" && normalFit.points.length > 0
            ? <><>{(normalFitLsl == null && normalFitUsl == null) && <Alert type="warning" showIcon message="当前参数没有可用正式规格" />}</><EChart option={normalFitOption} ariaLabel={`${normalFitParameter} 在 ${normalFitRow?.datasetLabel} 的服务端 Normal Fit 曲线`} onEvents={chartEvents} /><Space wrap><Tag>{normalFit.points.length} 个曲线点</Tag><Tag>Mean {formatNumber(normalFit.mean)}</Tag><Tag>Stddev {formatNumber(normalFit.standard_deviation)}</Tag><Tag>{normalFit.method}</Tag></Space></>
            : <Alert type="warning" showIcon message="Normal Fit 不适用" description={normalFit?.reason_code ? explainAnalysisReason(normalFit.reason_code) : "当前数据 / 参数无拟合结果"} />}
          <Table rowKey="key" columns={normalFitColumns} dataSource={normalFitRows} pagination={false} scroll={{ x: 1275 }} size="small" />
        </Space>
      </Card>}

      {capabilityRows.length > 0 && <Card size="small" title="Capability">
        {nonEligibleCapabilities.length > 0 && <Alert
          type="warning"
          showIcon
          message={capabilityRows.some((row) => row.analysis.capability?.reason_codes.includes("CAPABILITY_RULE_REQUIRED"))
            ? "统计规则尚未批准，能力指数保持关闭"
            : "部分 Capability 指标不适用或未请求"}
          style={{ marginBottom: 12 }}
        />}
        <Table rowKey="key" columns={capabilityColumns} dataSource={capabilityRows} pagination={false} scroll={{ x: 1750 }} size="small" />
      </Card>}

      {!rows.length && <Empty description="当前筛选没有可分析的数据" />}
    </Space>}
  </AnalysisResultFrame>;
}
