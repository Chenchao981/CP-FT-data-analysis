import { useMutation } from "@tanstack/react-query";
import { Alert, Button, Card, Checkbox, Col, Collapse, Empty, Input, InputNumber, Row, Segmented, Select, Space, Statistic, Table, Tag, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import type { EChartsCoreOption } from "echarts/core";
import { useMemo, useState } from "react";

import { ApiError } from "../../../api/auth";
import {
  analyzeSpatial,
  type SpatialAnalysisMode,
  type SpatialAnalysisRequest,
  type SpatialPoint,
  type SpatialQuadrantSummary,
  type SpatialZoneSummary,
} from "../../../api/spatialAnalysis";
import { EChart, type EChartEventMap } from "../../../components/EChart";
import { drilldownKeyFromChartEvent } from "../chartDrilldown";
import { ANALYSIS_COMPONENT_DEFAULTS, type SpatialAnalysisViewConfig } from "../context/analysisViewConfig";
import type { AnalysisDisplayState } from "../context/analysisViewState";
import type { AnalyticsDrilldownOpener, AnalyticsSectionContext } from "./sectionTypes";

type ColorScale = "ROBUST" | "FULL";

interface AnalyticsSpatialSectionProps extends AnalyticsSectionContext, AnalyticsDrilldownOpener {
  displayState: AnalysisDisplayState;
  onDisplayStateChange: (patch: Partial<AnalysisDisplayState>) => void;
  config?: SpatialAnalysisViewConfig;
  onConfigChange?: (patch: Partial<SpatialAnalysisViewConfig>) => void;
}

const modeOptions: Array<{ label: string; value: SpatialAnalysisMode }> = [
  { label: "Bin Map", value: "BIN_MAP" },
  { label: "Parameter Heatmap", value: "PARAMETER_HEATMAP" },
  { label: "Parameter Fail Overlay", value: "PARAMETER_FAIL_OVERLAY" },
  { label: "Composite Failure", value: "COMPOSITE_FAILURE" },
  { label: "Zone Comparison", value: "ZONE_COMPARISON" },
];
const BIN_COLORS = ["#2d9d78", "#d64545", "#f0a429", "#7b61a8", "#247ba0", "#8d6e63", "#607d8b", "#00a6a6"];
const ZONE_NAMES = ["CENTER", "MID", "EDGE"] as const;
const ZONE_COLORS = ["#2d9d78", "#f0a429", "#d64545"];
const parameterModes = new Set<SpatialAnalysisMode>(["PARAMETER_HEATMAP", "PARAMETER_FAIL_OVERLAY"]);
const singleWaferModes = new Set<SpatialAnalysisMode>(["BIN_MAP", "PARAMETER_HEATMAP", "PARAMETER_FAIL_OVERLAY", "ZONE_COMPARISON"]);

const selectOptions = (values: string[]) => Array.from(new Set(values.filter(Boolean))).sort().map((value) => ({ label: value, value }));
const percent = (value: number | null) => value == null ? "—" : `${(value * 100).toFixed(3)}%`;
const cloneRequest = (request: SpatialAnalysisRequest): SpatialAnalysisRequest => ({
  datasets: request.datasets.map((item) => ({ ...item })),
  filters: {
    lot_ids: [...request.filters.lot_ids], wafer_ids: [...request.filters.wafer_ids], bin_codes: [...request.filters.bin_codes],
    overall_results: [...request.filters.overall_results], source_ids: [...request.filters.source_ids], tester_ids: [...request.filters.tester_ids],
    program_versions: [...request.filters.program_versions], test_conditions: [...request.filters.test_conditions],
  },
  parameters: [...request.parameters],
  mode: request.mode,
  focus_dataset_id: request.focus_dataset_id,
  max_points: request.max_points,
  rule_code: request.rule_code,
  rule_version: request.rule_version,
});

function safeDomain(minimum: number, maximum: number): [number, number] {
  if (minimum !== maximum) return [minimum, maximum];
  const padding = Math.max(Math.abs(minimum) * 0.01, 0.5);
  return [minimum - padding, maximum + padding];
}

export function AnalyticsSpatialSection({ context, focusDatasetId, overview, overviewError, onOpenDrilldown, displayState, onDisplayStateChange, config: controlledConfig, onConfigChange: controlledOnConfigChange }: AnalyticsSpatialSectionProps) {
  const [localConfig, setLocalConfig] = useState<SpatialAnalysisViewConfig>(() => ({
    ...ANALYSIS_COMPONENT_DEFAULTS.spatial,
    parameter: context.parameters[0] ?? "",
    rule: { ...ANALYSIS_COMPONENT_DEFAULTS.spatial.rule },
  }));
  const config = controlledConfig ?? localConfig;
  const onConfigChange = (patch: Partial<SpatialAnalysisViewConfig>) => {
    if (!controlledConfig) setLocalConfig((current) => ({ ...current, ...patch }));
    controlledOnConfigChange?.(patch);
  };
  const mode = config.mode as SpatialAnalysisMode;
  const parameter = config.parameter;
  const maxPoints = config.maxPoints;
  const ruleCode = config.rule.ruleCode;
  const ruleVersion = config.rule.versionCode;
  const [submittedSignature, setSubmittedSignature] = useState<string | null>(null);
  const [selectedMembers, setSelectedMembers] = useState<{ title: string; keys: string[] } | null>(null);
  const colorScale = config.colorScale as ColorScale;
  const symbolSize = config.symbolSize;
  const showMissing = config.showMissing;

  const needsParameter = parameterModes.has(mode);
  const acceptsOptionalParameter = mode === "ZONE_COMPARISON";
  const requestParameters = needsParameter || (acceptsOptionalParameter && parameter) ? [parameter] : [];
  const currentRequest = useMemo<SpatialAnalysisRequest>(() => ({
    datasets: context.datasets.map((item) => ({ ...item })),
    filters: {
      lot_ids: [...context.filters.lot_ids], wafer_ids: [...context.filters.wafer_ids], bin_codes: [...context.filters.bin_codes],
      overall_results: [...context.filters.overall_results], source_ids: [...context.filters.source_ids], tester_ids: [...context.filters.tester_ids],
      program_versions: [...context.filters.program_versions], test_conditions: [...context.filters.test_conditions],
    },
    parameters: requestParameters,
    mode,
    focus_dataset_id: focusDatasetId,
    max_points: maxPoints,
    rule_code: mode === "ZONE_COMPARISON" ? ruleCode || null : null,
    rule_version: mode === "ZONE_COMPARISON" ? ruleVersion || null : null,
  }), [context, focusDatasetId, maxPoints, mode, requestParameters, ruleCode, ruleVersion]);
  const currentSignature = JSON.stringify(currentRequest);
  const isCp = overview?.dataset_context.test_stage === "CP";
  const explicitSingleWafer = context.filters.lot_ids.length === 1 && context.filters.wafer_ids.length === 1;
  const explicitMultiWafer = context.filters.wafer_ids.length >= 2;
  const waferScopeValid = singleWaferModes.has(mode) ? explicitSingleWafer : explicitMultiWafer;
  const needsBinMapping = mode === "BIN_MAP" || mode === "PARAMETER_FAIL_OVERLAY";
  const hasBinMapping = (overview?.rule_context.bin_mapping_versions.length ?? 0) > 0;
  const ruleValid = mode !== "ZONE_COMPARISON"
    || (/^[A-Z][A-Z0-9_]{2,127}$/.test(ruleCode) && /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/.test(ruleVersion));
  const canRun = isCp
    && waferScopeValid
    && (!needsBinMapping || hasBinMapping)
    && (!needsParameter || Boolean(parameter))
    && Number.isInteger(maxPoints)
    && maxPoints >= 100
    && maxPoints <= 50_000
    && ruleValid;

  const mutation = useMutation({ mutationFn: analyzeSpatial, retry: false });
  const execute = () => {
    if (!canRun) return;
    const snapshot = cloneRequest(currentRequest);
    setSubmittedSignature(JSON.stringify(snapshot));
    setSelectedMembers(null);
    mutation.mutate(snapshot);
  };
  const isStale = Boolean(mutation.data && submittedSignature !== currentSignature);
  const apiError = mutation.error instanceof ApiError ? mutation.error : null;
  const ruleGate = apiError?.code === "ANALYSIS_RULE_NOT_APPROVED" || apiError?.code === "ANALYSIS_RULE_CONTRACT_INVALID";
  const binMappingGate = apiError?.code === "ANALYSIS_BIN_MAPPING_REQUIRED";

  const result = mutation.data;
  const isComposite = result?.mode === "COMPOSITE_FAILURE";
  const availableWaferKeys = result?.wafer_manifest.map((item) => item.key) ?? [];
  const requestedWaferKeys = displayState.visibleWaferKeys.filter((key) => availableWaferKeys.includes(key));
  const activeWaferKeys = new Set(requestedWaferKeys.length ? requestedWaferKeys : availableWaferKeys);
  const waferKey = (point: SpatialPoint) => point.dataset_id == null || point.version_no == null || point.lot_id == null || point.wafer_id == null
    ? null
    : `${point.dataset_id}:V${point.version_no}:LOT:${point.lot_id}:WAFER:${point.wafer_id}`;
  const isWaferOverlay = isComposite && displayState.spatialLayerMode === "OVERLAY";
  const authoritativeDisplayPoints = isWaferOverlay
    ? result?.wafer_layers.filter((point) => {
      const key = waferKey(point);
      return key !== null && activeWaferKeys.has(key);
    }) ?? []
    : result?.points ?? [];
  const visiblePoints = authoritativeDisplayPoints.filter((point) => showMissing || point.value !== null || !result?.parameter);
  const bins = Array.from(new Set(visiblePoints.map((point) => point.bin_code ?? "UNKNOWN"))).sort();
  const isCompositeStack = isComposite && !isWaferOverlay;
  const isZoneMap = result?.mode === "ZONE_COMPARISON";
  const approvedQuadrantLabels = result?.zone_geometry?.quadrant_labels_ccw ?? [];
  const zoneContractValid = !isZoneMap || (Boolean(result?.zone_geometry)
    && approvedQuadrantLabels.length === 4
    && new Set(approvedQuadrantLabels).size === 4
    && (result?.quadrants?.length ?? 0) === 4
    && result?.quadrants?.every((summary) => approvedQuadrantLabels.includes(summary.quadrant) && summary.member_drilldown_keys.length === summary.unit_count)
    && visiblePoints.every((point) => ZONE_NAMES.includes(point.zone as typeof ZONE_NAMES[number]) && approvedQuadrantLabels.includes(point.quadrant ?? "")));
  const compositeMemberContractValid = !isCompositeStack || visiblePoints.every((point) => (point.member_drilldown_keys?.length ?? 0) === point.observed_count);
  const spatialContractValid = zoneContractValid && compositeMemberContractValid;
  const usesContinuousColor = !isZoneMap && (isCompositeStack || Boolean(result?.parameter));
  const colorDomain = result?.color_domain;
  const automaticDomain = isCompositeStack
    ? [0, 100] as [number, number]
    : colorDomain
      ? colorScale === "ROBUST" ? safeDomain(colorDomain.p02, colorDomain.p98) : safeDomain(colorDomain.minimum, colorDomain.maximum)
      : [0, 1] as [number, number];
  const requestedColorDomain: [number, number] = [displayState.colorMin ?? automaticDomain[0], displayState.colorMax ?? automaticDomain[1]];
  const rawDomain = requestedColorDomain[0] < requestedColorDomain[1] ? requestedColorDomain : automaticDomain;
  const pointValue = (point: SpatialPoint) => isCompositeStack
    ? (point.fail_ratio == null ? null : point.fail_ratio * 100)
    : isWaferOverlay
      ? point.result === "PASS" ? 0 : point.result === "FAIL" ? 1 : 2
    : isZoneMap
      ? ZONE_NAMES.indexOf(point.zone as typeof ZONE_NAMES[number])
      : result?.parameter ? point.value : bins.indexOf(point.bin_code ?? "UNKNOWN");

  const option = useMemo<EChartsCoreOption>(() => {
    const chartPoint = (point: SpatialPoint) => ({
      value: [point.x, point.y, pointValue(point)],
      drilldownKey: point.drilldown_key,
      lotId: point.lot_id,
      waferId: point.wafer_id,
      binCode: point.bin_code,
      rawBinCode: point.raw_bin_code,
      binName: point.bin_name,
      failureMode: point.failure_mode,
      binIsPass: point.bin_is_pass,
      binMappingVersion: point.bin_mapping_version,
      result: point.result,
      unit: point.unit,
      lsl: point.lsl,
      usl: point.usl,
      specStatus: point.spec_status,
      specVersion: point.spec_version,
      observedCount: point.observed_count,
      failCount: point.fail_count,
      waferCount: point.wafer_count,
      zone: point.zone,
      quadrant: point.quadrant,
      memberDrilldownKeys: point.member_drilldown_keys ?? [],
      measurementValue: point.value,
    });
    const series: unknown[] = [];
    if (isWaferOverlay) {
      for (const wafer of result?.wafer_manifest ?? []) {
        if (!activeWaferKeys.has(wafer.key)) continue;
        const points = visiblePoints.filter((point) => waferKey(point) === wafer.key);
        if (points.length) series.push({ type: "scatter", name: `${wafer.lot_id} / W${wafer.wafer_id}`, symbol: "rect", symbolSize, data: points.map(chartPoint) });
      }
    } else {
      series.push({ type: "scatter", name: isZoneMap ? "Zone" : result?.parameter ?? (isCompositeStack ? "Fail Ratio" : "Bin"), symbol: "rect", symbolSize, data: visiblePoints.map(chartPoint) });
    }
    const colorSeriesCount = series.length;
    if (result?.mode === "PARAMETER_FAIL_OVERLAY") {
      series.push({
        type: "scatter",
        name: "FAIL Overlay",
        symbol: "circle",
        symbolSize: symbolSize + 5,
        data: visiblePoints.filter((point) => point.bin_is_pass === false).map((point) => ({
          value: [point.x, point.y], drilldownKey: point.drilldown_key,
          itemStyle: { color: "transparent", borderColor: "#d64545", borderWidth: 2 },
        })),
      });
    }
    if (result?.parameter && displayState.showSpecOverlay) {
      series.push({
        type: "scatter",
        name: "Spec OOS",
        symbol: "circle",
        symbolSize: symbolSize + 4,
        data: visiblePoints.filter((point) => point.spec_status === "OUT_OF_SPEC").map((point) => ({
          value: [point.x, point.y], drilldownKey: point.drilldown_key,
          itemStyle: { color: "transparent", borderColor: "#7d1f1f", borderWidth: 2 },
        })),
      });
    }
    if (isZoneMap && result?.zone_geometry) {
      const geometry = result.zone_geometry;
      const boundary = (ratio: number) => Array.from({ length: 181 }, (_, index) => {
        const angle = 2 * Math.PI * index / 180;
        return [geometry.center_x + geometry.radius * ratio * Math.cos(angle), geometry.center_y + geometry.radius * ratio * Math.sin(angle)];
      });
      for (const [name, ratio] of [["Center boundary", geometry.center_ratio], ["Mid boundary", geometry.mid_ratio], ["Wafer boundary", 1]] as const) {
        series.push({ type: "line", name, data: boundary(ratio), showSymbol: false, silent: true, lineStyle: { color: "#17212b", width: name === "Wafer boundary" ? 2 : 1, type: name === "Wafer boundary" ? "solid" : "dashed" } });
      }
      const ySign = geometry.quadrant_y_direction === "UP" ? -1 : 1;
      const approvedAxis = (rotationDegrees: number) => {
        const radians = Math.PI * rotationDegrees / 180;
        const dx = geometry.radius * Math.cos(radians);
        const dy = ySign * geometry.radius * Math.sin(radians);
        return [[geometry.center_x - dx, geometry.center_y - dy], [geometry.center_x + dx, geometry.center_y + dy]];
      };
      series.push({ type: "line", name: "Approved quadrant X axis", data: approvedAxis(geometry.quadrant_axis_rotation_degrees), showSymbol: false, silent: true, lineStyle: { color: "#6f42c1", width: 2 } });
      series.push({ type: "line", name: "Approved quadrant Y axis", data: approvedAxis(geometry.quadrant_axis_rotation_degrees + 90), showSymbol: false, silent: true, lineStyle: { color: "#6f42c1", width: 2 } });
    }
    return {
      tooltip: {
        trigger: "item",
        formatter: (payload: unknown) => {
          const data = (payload as { data?: { value?: unknown; lotId?: unknown; waferId?: unknown; binCode?: unknown; rawBinCode?: unknown; binName?: unknown; failureMode?: unknown; binIsPass?: unknown; binMappingVersion?: unknown; result?: unknown; unit?: unknown; lsl?: unknown; usl?: unknown; specStatus?: unknown; specVersion?: unknown; observedCount?: unknown; failCount?: unknown; waferCount?: unknown; zone?: unknown; quadrant?: unknown; measurementValue?: unknown } })?.data;
          const value = Array.isArray(data?.value) ? data.value : [];
          const fact = isCompositeStack
            ? `Fail Ratio ${value[2] ?? "—"}% · Fail ${String(data?.failCount ?? "—")} / Observed ${String(data?.observedCount ?? "—")}`
            : isWaferOverlay
              ? `Result ${String(data?.result ?? "—")}`
            : isZoneMap
              ? `Radial Zone ${String(data?.zone ?? "—")} · Quadrant ${String(data?.quadrant ?? "—")}${result?.parameter ? ` · ${result.parameter}: ${String(data?.measurementValue ?? "—")}${data?.unit ? ` ${String(data.unit)}` : ""}` : ""}`
            : result?.parameter
              ? `${result.parameter}: ${value[2] ?? "—"}${data?.unit ? ` ${String(data.unit)}` : ""} · Spec ${String(data?.specVersion ?? "—")} [${String(data?.lsl ?? "—")}, ${String(data?.usl ?? "—")}] · ${String(data?.specStatus ?? "—")}`
              : `Bin ${String(data?.binCode ?? "UNKNOWN")}${data?.binName ? ` / ${String(data.binName)}` : ""} · ${data?.failureMode ? `Failure ${String(data.failureMode)} · ` : ""}${data?.binIsPass === true ? "PASS" : data?.binIsPass === false ? "FAIL" : String(data?.result ?? "—")} · Mapping ${String(data?.binMappingVersion ?? "—")} · Raw ${String(data?.rawBinCode ?? "—")}`;
          return `X ${value[0] ?? "—"} · Y ${value[1] ?? "—"}<br/>${fact}<br/>Lot ${String(data?.lotId ?? "—")} · Wafer ${String(data?.waferId ?? "—")} · Wafers ${String(data?.waferCount ?? "—")}`;
        },
      },
      legend: (isWaferOverlay || result?.mode === "PARAMETER_FAIL_OVERLAY" || Boolean(result?.parameter)) ? { type: "scroll" } : undefined,
      grid: { left: 58, right: 26, top: 44, bottom: 70 },
      xAxis: { type: "value", name: "X", minInterval: 1 },
      yAxis: { type: "value", name: "Y", minInterval: 1, inverse: true, min: displayState.yAxisMin ?? undefined, max: displayState.yAxisMax ?? undefined },
      brush: displayState.brushEnabled ? { toolbox: ["rect", "polygon", "clear"], xAxisIndex: "all", yAxisIndex: "all" } : undefined,
      toolbox: { feature: { saveAsImage: { name: `spatial-${String(result?.mode ?? "map").toLowerCase()}` }, dataZoom: {} } },
      visualMap: usesContinuousColor
        ? { type: "continuous", seriesIndex: Array.from({ length: colorSeriesCount }, (_, index) => index), dimension: 2, min: rawDomain[0], max: rawDomain[1], bottom: 0, orient: "horizontal", calculable: true, inRange: { color: ["#313695", "#74add1", "#ffffbf", "#f46d43", "#a50026"] } }
        : { type: "piecewise", seriesIndex: Array.from({ length: colorSeriesCount }, (_, index) => index), dimension: 2, bottom: 0, orient: "horizontal", pieces: isWaferOverlay
          ? [{ value: 0, label: "PASS", color: "#2d9d78" }, { value: 1, label: "FAIL", color: "#d64545" }, { value: 2, label: "UNKNOWN / ABORT", color: "#607d8b" }]
          : isZoneMap
            ? ZONE_NAMES.map((zone, index) => ({ value: index, label: zone, color: ZONE_COLORS[index] }))
          : bins.map((bin, index) => ({ value: index, label: `Bin ${bin}`, color: BIN_COLORS[index % BIN_COLORS.length] })) },
      series,
    };
  }, [activeWaferKeys, bins, displayState.brushEnabled, displayState.showSpecOverlay, displayState.yAxisMax, displayState.yAxisMin, isCompositeStack, isWaferOverlay, isZoneMap, rawDomain, result, symbolSize, usesContinuousColor, visiblePoints]);
  const showMembers = (title: string, keys: readonly string[]) => {
    if (!keys.length) return;
    setSelectedMembers({ title, keys: [...keys] });
  };
  const chartEvents = useMemo<EChartEventMap>(() => ({
    click: (payload) => {
      const data = (payload as { data?: { memberDrilldownKeys?: unknown; value?: unknown } })?.data;
      if (Array.isArray(data?.memberDrilldownKeys) && data.memberDrilldownKeys.every((key) => typeof key === "string") && data.memberDrilldownKeys.length) {
        const coordinate = Array.isArray(data.value) ? `X ${String(data.value[0])} / Y ${String(data.value[1])}` : "聚合点";
        showMembers(`${coordinate} 成员 Unit`, data.memberDrilldownKeys);
        return;
      }
      const key = drilldownKeyFromChartEvent(payload);
      if (key) onOpenDrilldown(key);
    },
  }), [onOpenDrilldown]);

  const zoneColumns: ColumnsType<SpatialZoneSummary> = [
    { title: "Zone", dataIndex: "zone", width: 150, fixed: "left" },
    { title: "Unit", dataIndex: "unit_count", width: 90 },
    { title: "PASS", dataIndex: "pass_count", width: 90 },
    { title: "FAIL", dataIndex: "fail_count", width: 90 },
    { title: "UNKNOWN", dataIndex: "unknown_count", width: 105 },
    { title: "Known Yield", dataIndex: "yield_rate", width: 120, render: percent },
    { title: "Measured", dataIndex: "measured_count", width: 105 },
    { title: "Missing", dataIndex: "missing_measurement_count", width: 105 },
    { title: "Mean", dataIndex: "mean", width: 110, render: (value) => value ?? "—" },
    { title: "Min / Max", width: 150, render: (_, row) => `${row.minimum ?? "—"} / ${row.maximum ?? "—"}` },
    { title: "下钻", width: 90, fixed: "right", render: (_, row) => <Button size="small" aria-label={`打开 ${row.zone} Zone Detail`} disabled={!(row.member_drilldown_keys?.length)} onClick={() => showMembers(`${row.zone} Radial Zone 成员 Unit`, row.member_drilldown_keys ?? [])}>成员</Button> },
  ];
  const quadrantColumns: ColumnsType<SpatialQuadrantSummary> = [
    { title: "Quadrant", dataIndex: "quadrant", width: 180, fixed: "left" },
    { title: "Unit", dataIndex: "unit_count", width: 90 },
    { title: "PASS", dataIndex: "pass_count", width: 90 },
    { title: "FAIL", dataIndex: "fail_count", width: 90 },
    { title: "UNKNOWN", dataIndex: "unknown_count", width: 105 },
    { title: "Known Yield", dataIndex: "yield_rate", width: 120, render: percent },
    { title: "Measured", dataIndex: "measured_count", width: 105 },
    { title: "Missing", dataIndex: "missing_measurement_count", width: 105 },
    { title: "Mean", dataIndex: "mean", width: 110, render: (value) => value ?? "—" },
    { title: "Min / Max", width: 150, render: (_, row) => `${row.minimum ?? "—"} / ${row.maximum ?? "—"}` },
    { title: "下钻", width: 90, fixed: "right", render: (_, row) => <Button size="small" aria-label={`打开 ${row.quadrant} Quadrant Detail`} disabled={!row.member_drilldown_keys.length} onClick={() => showMembers(`${row.quadrant} Quadrant 成员 Unit`, row.member_drilldown_keys)}>成员</Button> },
  ];

  if (overviewError) return <Alert type="error" showIcon message="Spatial Context 加载失败" description={overviewError.message} />;

  const parameterOptions = selectOptions([...(overview?.options.parameters ?? []), ...context.parameters, parameter]);
  return <Space direction="vertical" size="large" style={{ width: "100%" }}>
    {!isCp && overview && <Alert type="info" showIcon message="空间分析仅适用于 CP 数据" />}
    <Card title="晶圆空间分析" extra={<Tag color="blue">CP 数据</Tag>}>
      <Row gutter={[12, 12]}>
        <Col xs={24} md={12} xl={6}><Typography.Text strong>Mode</Typography.Text><Select aria-label="Spatial Mode" value={mode} options={modeOptions} onChange={(value) => onConfigChange({ mode: value })} className="full-width" /></Col>
        {(needsParameter || acceptsOptionalParameter) && <Col xs={24} md={12} xl={6}><Typography.Text strong>{needsParameter ? "参数（必选 1 个）" : "参数（可选 0–1 个）"}</Typography.Text><Select aria-label="Spatial 参数" allowClear showSearch value={parameter || undefined} options={parameterOptions} onChange={(value) => onConfigChange({ parameter: value ?? "" })} className="full-width" /></Col>}
        <Col xs={24} md={12} xl={6}><Typography.Text strong>Max Response Items（100–50000）</Typography.Text><InputNumber aria-label="Spatial 最大点数" min={100} max={50_000} precision={0} step={100} value={maxPoints} onChange={(value) => onConfigChange({ maxPoints: value ?? 20_000 })} className="full-width" /></Col>
      </Row>
      {mode === "ZONE_COMPARISON" && <>
        <Alert style={{ marginTop: 12 }} type={ruleCode && ruleVersion ? "success" : "warning"} showIcon message={ruleCode && ruleVersion ? `当前区域规则：${ruleCode}@${ruleVersion}` : "没有可用的区域规则"} />
        <Collapse style={{ marginTop: 12 }} size="small" items={[{ key: "rule", label: "高级设置：查看或调试规则版本", children: <Row gutter={[12, 12]}>
          <Col xs={24} md={12}><Typography.Text strong>规则编号</Typography.Text><Input aria-label="Spatial Rule Code" value={ruleCode} onChange={(event) => onConfigChange({ rule: { ...config.rule, ruleCode: event.target.value.trim().toUpperCase() } })} placeholder="系统自动匹配" /></Col>
          <Col xs={24} md={12}><Typography.Text strong>规则版本</Typography.Text><Input aria-label="Spatial Rule Version" value={ruleVersion} onChange={(event) => onConfigChange({ rule: { ...config.rule, versionCode: event.target.value.trim() } })} placeholder="系统自动匹配" /></Col>
        </Row> }]} />
      </>}
      <Space wrap style={{ marginTop: 12 }}><Button type="primary" aria-label="执行 Spatial 分析" disabled={!canRun} loading={mutation.isPending} onClick={execute}>执行 Spatial 分析</Button></Space>
      {singleWaferModes.has(mode) && !explicitSingleWafer && <Alert type="warning" showIcon message="请选择 1 个 Lot 和 1 个 Wafer" style={{ marginTop: 12 }} />}
      {mode === "COMPOSITE_FAILURE" && !explicitMultiWafer && <Alert type="warning" showIcon message="请选择至少 2 个 Wafer" style={{ marginTop: 12 }} />}
      {needsBinMapping && !hasBinMapping && <Alert type="warning" showIcon message="Bin Mapping 尚未就绪" style={{ marginTop: 12 }} />}
      {isStale && <Alert type="warning" showIcon message="空间分析结果已过期，请重新执行" style={{ marginTop: 12 }} />}
      {mutation.isError && <Alert type="error" showIcon message={binMappingGate ? "Spatial Bin Mapping 未绑定" : ruleGate ? "Spatial Rule 未批准或合同无效" : "Spatial 分析失败"} description={<Space direction="vertical" size={2}><Typography.Text>{mutation.error instanceof Error ? mutation.error.message : "未知错误"}</Typography.Text><Typography.Text>错误码：{apiError?.code ?? "UNKNOWN_ERROR"}</Typography.Text><Typography.Text>建议操作：{apiError?.recommendedAction ?? (binMappingGate ? "先为 Dataset 绑定已批准的版本化 Bin Mapping" : "请核对 CP/Wafer/Coordinate/Rule 合同")}</Typography.Text></Space>} style={{ marginTop: 12 }} />}
    </Card>

    {result && <>
      {result.warnings.length > 0 && <Alert type="warning" showIcon message="服务端提示" description={result.warnings.join("、")} />}
      <Row gutter={[12, 12]}>
        <Col xs={12} md={6}><Card><Statistic title="Input Unit" value={result.data_quality.input_units} /></Card></Col>
        <Col xs={12} md={6}><Card><Statistic title="Returned Point" value={result.data_quality.returned_points} /></Card></Col>
        <Col xs={12} md={6}><Card><Statistic title="Wafer" value={result.data_quality.wafer_count} /></Card></Col>
        <Col xs={12} md={6}><Card><Statistic title="Missing Measurement" value={result.data_quality.missing_measurement_count} /></Card></Col>
      </Row>
      <Card size="small" title="Data Quality / Contract">
        <Space wrap><Tag>合同 {result.contract_version}</Tag><Tag>Mode {result.mode}</Tag><Tag>{result.capabilities[0]?.status ?? "UNKNOWN"}</Tag><Tag>Missing Coordinate {result.data_quality.missing_coordinate_count}</Tag><Tag>Duplicate Coordinate {result.data_quality.duplicate_coordinate_count}</Tag><Tag>Measured {result.data_quality.measured_count}</Tag><Tag>Layer Points {result.data_quality.layer_point_count}</Tag></Space>
      </Card>
      <Card size="small" title="纯显示控制（不改变后端事实）">
        <Row gutter={[12, 12]}>
          {usesContinuousColor && <Col xs={24} md={12} xl={6}><Typography.Text strong>自动颜色域</Typography.Text><Segmented<ColorScale> aria-label="Spatial 颜色范围" value={colorScale} options={[{ label: "P02–P98", value: "ROBUST" }, { label: "Min–Max", value: "FULL" }]} onChange={(value) => onConfigChange({ colorScale: value })} block /></Col>}
          <Col xs={24} md={12} xl={6}><Typography.Text strong>点大小</Typography.Text><Segmented<number> aria-label="Spatial 点大小" value={symbolSize} options={[{ label: "小", value: 8 }, { label: "中", value: 12 }, { label: "大", value: 18 }]} onChange={(value) => onConfigChange({ symbolSize: value as 8 | 12 | 18 })} block /></Col>
          <Col xs={12} md={6} xl={3}><Typography.Text strong>Y 最小</Typography.Text><InputNumber aria-label="Spatial Y 最小" value={displayState.yAxisMin ?? undefined} onChange={(value) => onDisplayStateChange({ yAxisMin: value })} className="full-width" /></Col>
          <Col xs={12} md={6} xl={3}><Typography.Text strong>Y 最大</Typography.Text><InputNumber aria-label="Spatial Y 最大" value={displayState.yAxisMax ?? undefined} onChange={(value) => onDisplayStateChange({ yAxisMax: value })} className="full-width" /></Col>
          {usesContinuousColor && <><Col xs={12} md={6} xl={3}><Typography.Text strong>颜色最小</Typography.Text><InputNumber aria-label="Spatial 颜色最小" value={displayState.colorMin ?? undefined} onChange={(value) => onDisplayStateChange({ colorMin: value })} className="full-width" /></Col><Col xs={12} md={6} xl={3}><Typography.Text strong>颜色最大</Typography.Text><InputNumber aria-label="Spatial 颜色最大" value={displayState.colorMax ?? undefined} onChange={(value) => onDisplayStateChange({ colorMax: value })} className="full-width" /></Col></>}
          {isComposite && <Col xs={24} md={12} xl={6}><Typography.Text strong>Composite 显示</Typography.Text><Segmented<"STACK" | "OVERLAY"> aria-label="Spatial Overlay Stack" value={displayState.spatialLayerMode} options={[{ label: "Stack 聚合", value: "STACK" }, { label: "Wafer Overlay", value: "OVERLAY" }]} onChange={(value) => onDisplayStateChange({ spatialLayerMode: value })} block /></Col>}
          {isComposite && <Col xs={24} md={12} xl={12}><Typography.Text strong>Wafer 清单显隐</Typography.Text><Select aria-label="Spatial 可见 Wafer" mode="multiple" allowClear value={[...requestedWaferKeys]} options={result.wafer_manifest.map((wafer) => ({ label: `${wafer.lot_id} / W${wafer.wafer_id}`, value: wafer.key }))} onChange={(values) => onDisplayStateChange({ visibleWaferKeys: values })} placeholder="空=全部 Wafer" className="full-width" /></Col>}
        </Row>
        <Space wrap style={{ marginTop: 12 }}>
          <Checkbox checked={displayState.brushEnabled} onChange={(event) => onDisplayStateChange({ brushEnabled: event.target.checked })}>Brush</Checkbox>
          {result.parameter && <Checkbox checked={displayState.showSpecOverlay} onChange={(event) => onDisplayStateChange({ showSpecOverlay: event.target.checked })}>Spec OOS Overlay</Checkbox>}
          {result.parameter && <Checkbox checked={showMissing} onChange={(event) => onConfigChange({ showMissing: event.target.checked })}>Missing Measurement</Checkbox>}
          {colorDomain && <Tag>颜色范围 {colorDomain.minimum} – {colorDomain.maximum}</Tag>}
        </Space>
      </Card>
      <Card title={`${result.mode}${result.parameter ? ` · ${result.parameter}` : ""}`}>
        {!zoneContractValid && <Alert type="error" showIcon message="Zone 几何合同不完整" description="服务端必须同时返回批准规则的边界和每个点的 Zone 身份；当前结果失败关闭。" />}
        {!compositeMemberContractValid && <Alert type="error" showIcon message="Composite 成员下钻合同不完整" description="聚合点 observed_count 必须与服务端返回的全部稳定 member_drilldown_keys 一一对账；当前结果失败关闭。" />}
        {visiblePoints.length && spatialContractValid ? <EChart option={option} ariaLabel={`${result.mode} Spatial Map`} onEvents={chartEvents} /> : spatialContractValid ? <Empty description="当前显示条件无点" /> : null}
      </Card>
      {selectedMembers && <Card size="small" title={`${selectedMembers.title}（${selectedMembers.keys.length}）`} extra={<Button size="small" onClick={() => setSelectedMembers(null)}>关闭成员列表</Button>}><Space wrap>{selectedMembers.keys.map((key) => <Button key={key} size="small" aria-label={`打开成员 ${key}`} onClick={() => onOpenDrilldown(key)}>{key}</Button>)}</Space></Card>}
      {result.zones.length > 0 && <Card title="径向区域对比"><Table rowKey="zone" columns={zoneColumns} dataSource={result.zones} pagination={false} scroll={{ x: 1260 }} /></Card>}
      {(result.quadrants?.length ?? 0) > 0 && result.zone_geometry && <Card title="象限对比" extra={<Space wrap><Tag>Rotation {result.zone_geometry.quadrant_axis_rotation_degrees}°</Tag><Tag>Y {result.zone_geometry.quadrant_y_direction}</Tag><Tag>CCW {result.zone_geometry.quadrant_labels_ccw.join(" → ")}</Tag></Space>}><Table rowKey="quadrant" columns={quadrantColumns} dataSource={result.quadrants} pagination={false} scroll={{ x: 1300 }} /></Card>}
    </>}
  </Space>;
}

export default AnalyticsSpatialSection;
