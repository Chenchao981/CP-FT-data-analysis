import type { AnalyticsExportTemplateCode } from "../../../api/analyticsExports";
import { persistAnalysisComponentState, type AnalysisComponentState } from "./analysisViewConfig";
import type { AnalysisDisplayState, AnalysisViewState } from "./analysisViewState";

export const ANALYTICS_EXPORT_ANALYSIS_CONFIG_VERSION = "ANALYTICS_EXPORT_ANALYSIS_CONFIG_V1" as const;

const chartDisplay = (display: AnalysisDisplayState) => ({
  y_axis_min: display.yAxisMin,
  y_axis_max: display.yAxisMax,
  color_min: display.colorMin,
  color_max: display.colorMax,
  correlation_min_abs: display.correlationMinAbs,
  brush_enabled: display.brushEnabled,
  show_spec_overlay: display.showSpecOverlay,
  spatial_layer_mode: display.spatialLayerMode,
  visible_wafer_keys: [...display.visibleWaferKeys],
});

export function savedChartConfig(state: AnalysisViewState): Record<string, unknown> {
  return {
    ...chartDisplay(state.display),
    analysis_view_state: persistAnalysisComponentState(state.analysis),
  };
}

const requiredText = (value: string, label: string) => {
  if (!value) throw new Error(`${label} 未配置；请在对应分析面板完成精确配置后再导出。`);
  return value;
};
const exactRule = (rule: { ruleCode: string; versionCode: string }, label: string) => ({
  rule_code: requiredText(rule.ruleCode, `${label} Rule Code`),
  version_code: requiredText(rule.versionCode, `${label} Rule Version`),
});

export function exportAnalysisConfig(
  template: AnalyticsExportTemplateCode,
  analysis: AnalysisComponentState,
  contextParameters: readonly string[],
  focusDatasetId: number,
): Record<string, unknown> | undefined {
  if (template === "ANALYTICS_DETAIL" || template === "PARAMETER_DETAIL") return undefined;
  const base = { contract_version: ANALYTICS_EXPORT_ANALYSIS_CONFIG_VERSION };
  if (template === "ANALYTICS_OVERVIEW") {
    const config = analysis.overviewRisk;
    const evaluations = config.analyses.map((item) => {
      if (item === "CAPABILITY") return {
        analysis: item,
        rule: exactRule(config.capability, "Overview Capability"),
        parameter: requiredText(config.parameter, "Overview Risk Parameter"),
        capability_method: config.capability.method,
      };
      if (item === "PAT_ROBUST_IQR") return {
        analysis: item, rule: exactRule(config.pat, "Overview PAT"), parameter: requiredText(config.parameter, "Overview Risk Parameter"), group_by: config.groupBy,
      };
      if (item === "SPC_I_MR") return {
        analysis: item, rule: exactRule(config.spc, "Overview SPC"), parameter: requiredText(config.parameter, "Overview Risk Parameter"), group_by: config.groupBy,
        spc_order: "UNIT_SEQUENCE", spc_phase: "PHASE_I_BASELINE",
      };
      if (item === "MARGIN_OOS") return {
        analysis: item, rule: exactRule(config.margin, "Overview Margin"), parameter: requiredText(config.parameter, "Overview Risk Parameter"), group_by: config.groupBy,
      };
      if (item === "SBL_GROUPED_LIMIT") return {
        analysis: item, rule: exactRule(config.sbl, "Overview SBL"), group_by: config.groupBy, bin_type: config.sbl.binType,
      };
      return { analysis: item, rule: exactRule(config.syl, "Overview SYL"), group_by: config.groupBy };
    });
    return { ...base, section: "OVERVIEW", overview: { evaluations } };
  }
  if (template === "PARAMETER_ANALYSIS") {
    const config = analysis.parameterAnalysis;
    const parameters = [...new Set(contextParameters)];
    if (!parameters.length || parameters.length > 5) throw new Error("Parameter Analysis 导出需要 1–5 个精确参数。");
    const selected = new Set(config.analyses);
    return {
      ...base,
      section: "PARAMETER_ANALYSIS",
      parameter_analysis: {
        parameters,
        group_by: config.groupBy,
        analyses: [...config.analyses],
        box_plot: selected.has("BOX_PLOT") ? exactRule(config.boxPlot, "Box Plot") : {},
        histogram: selected.has("HISTOGRAM") ? exactRule(config.histogram, "Histogram") : {},
        normal_fit: selected.has("NORMAL_FIT") ? exactRule(config.normalFit, "Normal Fit") : {},
        capability: selected.has("CAPABILITY") ? { method: config.capability.method, ...exactRule(config.capability, "Capability") } : {},
      },
    };
  }
  if (template === "PARAMETER_RELATIONSHIP") {
    const config = analysis.parameterRelationship;
    const correlationSelected = config.analyses.includes("CORRELATION");
    const xParameter = requiredText(config.xParameter, "Relationship X Parameter");
    if (!config.yParameters.length || config.yParameters.includes(xParameter)) throw new Error("Relationship 导出需要 1–5 个与 X 不同的 Y 参数。");
    return {
      ...base,
      section: "PARAMETER_RELATIONSHIP",
      parameter_relationship: {
        x_parameter: xParameter,
        y_parameters: [...config.yParameters],
        analyses: [...config.analyses],
        group_by: config.groupBy,
        max_points: config.maxPoints,
        correlation: correlationSelected ? { method: config.correlation.method, ...exactRule(config.correlation, "Correlation") } : {},
      },
    };
  }
  if (template === "SPATIAL_ANALYSIS") {
    const config = analysis.spatial;
    const parameterRequired = config.mode === "PARAMETER_HEATMAP" || config.mode === "PARAMETER_FAIL_OVERLAY";
    const parameterAllowed = parameterRequired || config.mode === "ZONE_COMPARISON";
    return {
      ...base,
      section: "SPATIAL_ANALYSIS",
      spatial_analysis: {
        mode: config.mode,
        ...(parameterAllowed && config.parameter ? { parameter: config.parameter } : {}),
        ...(parameterRequired ? { parameter: requiredText(config.parameter, "Spatial Parameter") } : {}),
        focus_dataset_id: focusDatasetId,
        max_points: config.maxPoints,
        ...(config.mode === "ZONE_COMPARISON" ? (() => {
          const rule = exactRule(config.rule, "Spatial Zone");
          return { rule_code: rule.rule_code, rule_version: rule.version_code };
        })() : {}),
      },
    };
  }
  if (template === "FT_QUALITY") {
    const config = analysis.quality;
    if (!config.analysis || !config.groupBy) throw new Error("FT Quality 导出需要显式选择方法和 Group By。");
    const parameterRequired = ["PAT_ROBUST_IQR", "SPC_I_MR", "MARGIN_OOS", "PASS_FAIL_DISTRIBUTION"].includes(config.analysis);
    const binRequired = ["BIN_COOCCURRENCE", "SBL_GROUPED_LIMIT"].includes(config.analysis);
    if ((binRequired || config.analysis === "SYL_GROUPED_LIMIT") && config.groupBy === "CONDITION") throw new Error("当前 Quality 方法不允许 CONDITION 分组。");
    if (config.analysis === "SBL_GROUPED_LIMIT" && config.binType === "ALL_MAPPED_FAILURE") throw new Error("SBL 导出需要精确物理 Bin Type。");
    return {
      ...base,
      section: "FT_QUALITY",
      ft_quality: {
        analysis: config.analysis,
        ...(parameterRequired ? { parameter: requiredText(config.parameter, "Quality Parameter") } : {}),
        rule: exactRule(config.rule, "Quality"),
        group_by: config.groupBy,
        ...(config.analysis === "SPC_I_MR" ? {
          spc_order: config.spcOrder ?? (() => { throw new Error("SPC 导出缺少 Order。"); })(),
          spc_phase: config.spcPhase ?? (() => { throw new Error("SPC 导出缺少 Phase。"); })(),
        } : {}),
        ...(binRequired ? { bin_type: config.binType ?? (() => { throw new Error("Bin Quality 导出缺少 Bin Type。"); })() } : {}),
      },
    };
  }
  if (template === "WAFER_SUMMARY") return {
    ...base,
    section: "WAFER_SUMMARY",
    wafer_summary: { sort_by: analysis.waferSummary.sortBy, sort_direction: analysis.waferSummary.sortDirection },
  };
  throw new Error(`不支持的分析导出模板：${template}`);
}

export function exportChartConfig(
  template: AnalyticsExportTemplateCode,
  state: AnalysisViewState,
  contextParameters: readonly string[],
  focusDatasetId: number,
): Record<string, unknown> {
  const analysis = exportAnalysisConfig(template, state.analysis, contextParameters, focusDatasetId);
  return {
    ...chartDisplay(state.display),
    analysis_view_state: persistAnalysisComponentState(state.analysis),
    ...(analysis ? { analysis } : {}),
  };
}
