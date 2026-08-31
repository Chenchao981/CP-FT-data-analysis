import type { AnalyticsEvaluationFilter, AnalyticsMeasurementFilter } from "../../../api/analytics";

export const ANALYSIS_VIEW_CONTRACT_VERSION = "ANALYSIS_VIEW_STATE_V1" as const;

export type ParameterAnalysisType = "DESCRIPTIVE" | "BOX_PLOT" | "HISTOGRAM" | "NORMAL_FIT" | "CAPABILITY";
export type ParameterCapabilityMethod = "CPK_POOLED_WITHIN_RUN_V1" | "CPK_POOLED_WITHIN_LOT_WAFER_V1";
export type RelationshipAnalysisType = "SCATTER" | "TREND" | "CORRELATION";
export type RelationshipGroupBy = "DATASET" | "TEST_BATCH" | "LOT" | "WAFER" | "SOURCE" | "TESTER" | "PROGRAM" | "CONDITION";
export type SpatialMode = "BIN_MAP" | "PARAMETER_HEATMAP" | "PARAMETER_FAIL_OVERLAY" | "COMPOSITE_FAILURE" | "ZONE_COMPARISON";
export type QualityAnalysis = "PAT_ROBUST_IQR" | "SPC_I_MR" | "MARGIN_OOS" | "BIN_COOCCURRENCE" | "SBL_GROUPED_LIMIT" | "SYL_GROUPED_LIMIT" | "PASS_FAIL_DISTRIBUTION";
export type QualityGroupBy = "DATASET" | "LOT" | "WAFER" | "RUN" | "TESTER" | "PROGRAM" | "CONDITION";
export type QualityBinType = "CP_BIN" | "SOFT_BIN" | "HARD_BIN" | "ALL_MAPPED_FAILURE";
export type OverviewRiskAnalysis = "CAPABILITY" | "PAT_ROBUST_IQR" | "SPC_I_MR" | "MARGIN_OOS" | "SBL_GROUPED_LIMIT" | "SYL_GROUPED_LIMIT";

export interface ExactRuleState { readonly ruleCode: string; readonly versionCode: string }

export interface DetailViewConfig {
  readonly view: "WIDE" | "LONG";
  readonly sortBy: "UNIT_SEQUENCE" | "LOT" | "WAFER" | "SOURCE_ROW" | "RESULT" | "SOFT_BIN" | "HARD_BIN";
  readonly sortDirection: "ASC" | "DESC";
  readonly evaluation_filter: AnalyticsEvaluationFilter | null;
  readonly measurement_filter: AnalyticsMeasurementFilter | null;
}

export interface ParameterAnalysisViewConfig {
  readonly groupBy: "DATASET";
  readonly analyses: readonly ParameterAnalysisType[];
  readonly boxPlot: ExactRuleState;
  readonly histogram: ExactRuleState;
  readonly normalFit: ExactRuleState;
  readonly capability: ExactRuleState & { readonly method: ParameterCapabilityMethod };
  readonly boxParameter: string;
  readonly histogramDataset: string;
  readonly histogramParameter: string;
  readonly normalFitDataset: string;
  readonly normalFitParameter: string;
}

export interface ParameterRelationshipViewConfig {
  readonly xParameter: string;
  readonly yParameters: readonly string[];
  readonly analyses: readonly RelationshipAnalysisType[];
  readonly groupBy: RelationshipGroupBy;
  readonly maxPoints: number;
  readonly correlation: ExactRuleState & { readonly method: "PEARSON_PAIRWISE_V1" };
  readonly scatterY: string;
  readonly scatterDataset: string;
  readonly trendParameter: string;
  readonly correlationScope: string;
  readonly displayGroups: readonly string[];
  readonly pointVisibility: readonly ("IN_SPEC" | "OUT_OF_SPEC")[];
}

export interface SpatialAnalysisViewConfig {
  readonly mode: SpatialMode;
  readonly parameter: string;
  readonly maxPoints: number;
  readonly rule: ExactRuleState;
  readonly colorScale: "ROBUST" | "FULL";
  readonly symbolSize: 8 | 12 | 18;
  readonly showMissing: boolean;
}

export interface QualityAnalysisViewConfig {
  readonly analysis: QualityAnalysis | null;
  readonly parameter: string;
  readonly groupBy: QualityGroupBy | null;
  readonly rule: ExactRuleState;
  readonly spcOrder: "UNIT_SEQUENCE" | null;
  readonly spcPhase: "PHASE_I_BASELINE" | null;
  readonly binType: QualityBinType | null;
  readonly spcDisplayGroup: string;
  readonly distributionDisplayGroup: string;
  readonly marginDisplayGroup: string;
  readonly cooccurrenceDisplayGroup: string;
  readonly sblDisplayBin: string;
  readonly sylDisplayDataset: string;
  readonly percentAxisMode: "AUTO" | "FIXED_0_100";
}

export interface WaferSummaryViewConfig {
  readonly sortBy: "DATASET" | "LOT" | "WAFER" | "UNIT_COUNT" | "YIELD";
  readonly sortDirection: "ASC" | "DESC";
}

export interface OverviewRiskViewConfig {
  readonly analyses: readonly OverviewRiskAnalysis[];
  readonly parameter: string;
  readonly groupBy: QualityGroupBy;
  readonly capability: ExactRuleState & { readonly method: ParameterCapabilityMethod };
  readonly pat: ExactRuleState;
  readonly spc: ExactRuleState;
  readonly margin: ExactRuleState;
  readonly sbl: ExactRuleState & { readonly binType: "CP_BIN" | "SOFT_BIN" | "HARD_BIN" };
  readonly syl: ExactRuleState;
}

export interface AnalysisComponentState {
  readonly overviewRisk: OverviewRiskViewConfig;
  readonly detail: DetailViewConfig;
  readonly parameterAnalysis: ParameterAnalysisViewConfig;
  readonly parameterRelationship: ParameterRelationshipViewConfig;
  readonly spatial: SpatialAnalysisViewConfig;
  readonly quality: QualityAnalysisViewConfig;
  readonly waferSummary: WaferSummaryViewConfig;
}

export const ANALYSIS_COMPONENT_DEFAULTS = Object.freeze({
  overviewRisk: {
    analyses: [], parameter: "", groupBy: "DATASET",
    capability: { method: "CPK_POOLED_WITHIN_RUN_V1", ruleCode: "", versionCode: "" },
    pat: { ruleCode: "", versionCode: "" }, spc: { ruleCode: "", versionCode: "" }, margin: { ruleCode: "", versionCode: "" },
    sbl: { ruleCode: "", versionCode: "", binType: "CP_BIN" }, syl: { ruleCode: "", versionCode: "" },
  },
  detail: { view: "WIDE", sortBy: "UNIT_SEQUENCE", sortDirection: "ASC", evaluation_filter: null, measurement_filter: null },
  parameterAnalysis: {
    groupBy: "DATASET", analyses: ["DESCRIPTIVE"],
    boxPlot: { ruleCode: "", versionCode: "" }, histogram: { ruleCode: "", versionCode: "" },
    normalFit: { ruleCode: "", versionCode: "" },
    capability: { method: "CPK_POOLED_WITHIN_RUN_V1", ruleCode: "", versionCode: "" },
    boxParameter: "", histogramDataset: "", histogramParameter: "", normalFitDataset: "", normalFitParameter: "",
  },
  parameterRelationship: {
    xParameter: "", yParameters: [], analyses: ["SCATTER"], groupBy: "DATASET", maxPoints: 10_000,
    correlation: { method: "PEARSON_PAIRWISE_V1", ruleCode: "", versionCode: "" },
    scatterY: "", scatterDataset: "", trendParameter: "", correlationScope: "", displayGroups: [],
    pointVisibility: ["IN_SPEC", "OUT_OF_SPEC"],
  },
  spatial: {
    mode: "BIN_MAP", parameter: "", maxPoints: 20_000, rule: { ruleCode: "", versionCode: "" },
    colorScale: "ROBUST", symbolSize: 12, showMissing: true,
  },
  quality: {
    analysis: null, parameter: "", groupBy: null, rule: { ruleCode: "", versionCode: "" },
    spcOrder: null, spcPhase: null, binType: null, spcDisplayGroup: "", distributionDisplayGroup: "",
    marginDisplayGroup: "", cooccurrenceDisplayGroup: "", sblDisplayBin: "", sylDisplayDataset: "", percentAxisMode: "AUTO",
  },
  waferSummary: { sortBy: "DATASET", sortDirection: "ASC" },
} as AnalysisComponentState);

export const ANALYSIS_CONFIG_QUERY_KEYS = [
  "view_contract", "or_analysis", "or_parameter", "or_group", "or_cap_method", "or_cap_rule", "or_cap_version",
  "or_pat_rule", "or_pat_version", "or_spc_rule", "or_spc_version", "or_margin_rule", "or_margin_version",
  "or_sbl_rule", "or_sbl_version", "or_sbl_bin", "or_syl_rule", "or_syl_version",
  "detail_view", "detail_sort_by", "detail_sort_direction", "detail_eval_type", "detail_eval_result", "detail_eval_rule", "detail_eval_version",
  "detail_measure_parameter", "detail_measure_lower", "detail_measure_upper", "detail_measure_lower_inclusive", "detail_measure_upper_inclusive",
  "pa_analysis", "pa_group", "pa_box_rule", "pa_box_version", "pa_hist_rule", "pa_hist_version",
  "pa_normal_rule", "pa_normal_version", "pa_cap_method", "pa_cap_rule", "pa_cap_version", "pa_box_parameter",
  "pa_hist_dataset", "pa_hist_parameter", "pa_normal_dataset", "pa_normal_parameter", "rel_x", "rel_y", "rel_analysis",
  "rel_group", "rel_max_points", "rel_corr_rule", "rel_corr_version", "rel_scatter_y", "rel_scatter_dataset",
  "rel_trend_parameter", "rel_corr_scope", "rel_display_group", "rel_point_visibility", "sp_mode", "sp_parameter",
  "sp_max_points", "sp_rule", "sp_rule_version", "sp_color_scale", "sp_symbol_size", "sp_show_missing", "q_analysis",
  "q_parameter", "q_group", "q_rule", "q_rule_version", "q_spc_order", "q_spc_phase", "q_bin_type", "q_spc_group",
  "q_distribution_group", "q_margin_group", "q_cooccurrence_group", "q_sbl_bin", "q_syl_dataset", "q_percent_axis",
  "wafer_sort_by", "wafer_sort_direction", "view_warning",
] as const;

const PARAMETER_ANALYSES = ["DESCRIPTIVE", "BOX_PLOT", "HISTOGRAM", "NORMAL_FIT", "CAPABILITY"] as const;
const CAPABILITY_METHODS = ["CPK_POOLED_WITHIN_RUN_V1", "CPK_POOLED_WITHIN_LOT_WAFER_V1"] as const;
const RELATIONSHIP_ANALYSES = ["SCATTER", "TREND", "CORRELATION"] as const;
const RELATIONSHIP_GROUPS = ["DATASET", "TEST_BATCH", "LOT", "WAFER", "SOURCE", "TESTER", "PROGRAM", "CONDITION"] as const;
const SPATIAL_MODES = ["BIN_MAP", "PARAMETER_HEATMAP", "PARAMETER_FAIL_OVERLAY", "COMPOSITE_FAILURE", "ZONE_COMPARISON"] as const;
const QUALITY_ANALYSES = ["PAT_ROBUST_IQR", "SPC_I_MR", "MARGIN_OOS", "BIN_COOCCURRENCE", "SBL_GROUPED_LIMIT", "SYL_GROUPED_LIMIT", "PASS_FAIL_DISTRIBUTION"] as const;
const QUALITY_GROUPS = ["DATASET", "LOT", "WAFER", "RUN", "TESTER", "PROGRAM", "CONDITION"] as const;
const QUALITY_BIN_TYPES = ["CP_BIN", "SOFT_BIN", "HARD_BIN", "ALL_MAPPED_FAILURE"] as const;
const OVERVIEW_RISK_ANALYSES = ["CAPABILITY", "PAT_ROBUST_IQR", "SPC_I_MR", "MARGIN_OOS", "SBL_GROUPED_LIMIT", "SYL_GROUPED_LIMIT"] as const;
const POINT_VISIBILITY = ["IN_SPEC", "OUT_OF_SPEC"] as const;
const DETAIL_EVALUATION_TYPES = ["SPEC", "PAT", "SBL", "SAFE_LAUNCH", "OTHER"] as const;
const DETAIL_EVALUATION_RESULTS = ["PASS", "FAIL", "NOT_EVALUATED", "NO_MATCH", "CONFIG_AMBIGUOUS", "INVALID_VALUE"] as const;
const RULE_CODE = /^[A-Z][A-Z0-9_]{2,127}$/;
const RULE_VERSION = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/;
const MAX_TEXT = 200;
const MAX_GROUP_KEY = 512;

type WarningSink = Set<string>;
const invalid = (warnings: WarningSink, key: string) => warnings.add(`ANALYSIS_VIEW_INVALID_${key.toUpperCase()}`);
const member = <T extends string>(value: unknown, values: readonly T[]): value is T => typeof value === "string" && values.includes(value as T);
const textValue = (value: unknown, maximum: number, warnings: WarningSink, key: string): string => {
  if (value == null || value === "") return "";
  if (typeof value !== "string" || value.trim() !== value || value.length > maximum || [...value].some((character) => character.charCodeAt(0) < 32)) { invalid(warnings, key); return ""; }
  return value;
};
const ruleCode = (value: unknown, warnings: WarningSink, key: string): string => {
  const normalized = textValue(value, 128, warnings, key);
  if (normalized && !RULE_CODE.test(normalized)) { invalid(warnings, key); return ""; }
  return normalized;
};
const ruleVersion = (value: unknown, warnings: WarningSink, key: string): string => {
  const normalized = textValue(value, 64, warnings, key);
  if (normalized && !RULE_VERSION.test(normalized)) { invalid(warnings, key); return ""; }
  return normalized;
};
const enumValue = <T extends string>(value: unknown, values: readonly T[], fallback: T, warnings: WarningSink, key: string): T => {
  if (value == null || value === "") return fallback;
  if (!member(value, values)) { invalid(warnings, key); return fallback; }
  return value;
};
const nullableEnum = <T extends string>(value: unknown, values: readonly T[], warnings: WarningSink, key: string): T | null => {
  if (value == null || value === "") return null;
  if (!member(value, values)) { invalid(warnings, key); return null; }
  return value;
};
const integer = (value: unknown, fallback: number, minimum: number, maximum: number, warnings: WarningSink, key: string): number => {
  if (value == null || value === "") return fallback;
  const parsed = typeof value === "string" && /^[1-9]\d*$/.test(value) ? Number(value) : value;
  if (!Number.isSafeInteger(parsed) || Number(parsed) < minimum || Number(parsed) > maximum) { invalid(warnings, key); return fallback; }
  return Number(parsed);
};
const bool = (value: unknown, fallback: boolean, warnings: WarningSink, key: string): boolean => {
  if (value == null || value === "") return fallback;
  if (value === true || value === "1") return true;
  if (value === false || value === "0") return false;
  invalid(warnings, key); return fallback;
};
const finiteNumber = (value: unknown, warnings: WarningSink, key: string): number | null => {
  if (value == null || value === "") return null;
  const parsed = typeof value === "string" && value.length <= 64 && /^-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?$/.test(value)
    ? Number(value)
    : value;
  if (typeof parsed !== "number" || !Number.isFinite(parsed)) { invalid(warnings, key); return null; }
  return parsed;
};
const enumList = <T extends string>(values: readonly unknown[], allowed: readonly T[], fallback: readonly T[], limit: number, warnings: WarningSink, key: string): T[] => {
  if (!values.length) return [...fallback];
  const result: T[] = [];
  for (const value of values) {
    if (!member(value, allowed)) { invalid(warnings, key); continue; }
    if (!result.includes(value)) result.push(value);
  }
  if (!result.length || result.length > limit) { invalid(warnings, key); return [...fallback]; }
  return result;
};
const textList = (values: readonly unknown[], maximum: number, limit: number, warnings: WarningSink, key: string): string[] => {
  const result: string[] = [];
  for (const value of values) {
    const normalized = textValue(value, maximum, warnings, key);
    if (normalized && !result.includes(normalized)) result.push(normalized);
  }
  if (result.length > limit) { invalid(warnings, key); return result.slice(0, limit); }
  return result;
};

export function normalizeAnalysisComponentState(raw: unknown, warnings: WarningSink = new Set()): AnalysisComponentState {
  const validRoot = raw != null && typeof raw === "object" && !Array.isArray(raw);
  const root = validRoot ? raw as Record<string, unknown> : {};
  if (raw != null && !validRoot) invalid(warnings, "ANALYSIS_FIELDS");
  if (validRoot && Object.keys(root).some((key) => !["overviewRisk", "detail", "parameterAnalysis", "parameterRelationship", "spatial", "quality", "waferSummary"].includes(key))) invalid(warnings, "ANALYSIS_FIELDS");
  const object = (value: unknown, key: string): Record<string, unknown> => {
    if (value == null) return {};
    if (typeof value !== "object" || Array.isArray(value)) { invalid(warnings, key); return {}; }
    return value as Record<string, unknown>;
  };
  const rejectUnknown = (item: Record<string, unknown>, allowed: readonly string[], key: string) => {
    if (Object.keys(item).some((field) => !allowed.includes(field))) invalid(warnings, key);
  };
  const overviewRisk = object(root.overviewRisk, "OR");
  const detail = object(root.detail, "DETAIL");
  const pa = object(root.parameterAnalysis, "PA");
  const rel = object(root.parameterRelationship, "REL");
  const sp = object(root.spatial, "SP");
  const q = object(root.quality, "Q");
  const ws = object(root.waferSummary, "WAFER");
  rejectUnknown(overviewRisk, ["analyses", "parameter", "groupBy", "capability", "pat", "spc", "margin", "sbl", "syl"], "OR_FIELDS");
  rejectUnknown(detail, ["view", "sortBy", "sortDirection", "evaluation_filter", "measurement_filter"], "DETAIL_FIELDS");
  rejectUnknown(pa, ["groupBy", "analyses", "boxPlot", "histogram", "normalFit", "capability", "boxParameter", "histogramDataset", "histogramParameter", "normalFitDataset", "normalFitParameter"], "PA_FIELDS");
  rejectUnknown(rel, ["xParameter", "yParameters", "analyses", "groupBy", "maxPoints", "correlation", "scatterY", "scatterDataset", "trendParameter", "correlationScope", "displayGroups", "pointVisibility"], "REL_FIELDS");
  rejectUnknown(sp, ["mode", "parameter", "maxPoints", "rule", "colorScale", "symbolSize", "showMissing"], "SP_FIELDS");
  rejectUnknown(q, ["analysis", "parameter", "groupBy", "rule", "spcOrder", "spcPhase", "binType", "spcDisplayGroup", "distributionDisplayGroup", "marginDisplayGroup", "cooccurrenceDisplayGroup", "sblDisplayBin", "sylDisplayDataset", "percentAxisMode"], "Q_FIELDS");
  rejectUnknown(ws, ["sortBy", "sortDirection"], "WAFER_FIELDS");
  const ref = (value: unknown, prefix: string): ExactRuleState => {
    const item = object(value, prefix);
    rejectUnknown(item, ["ruleCode", "versionCode", "method", "binType"], `${prefix}_FIELDS`);
    return { ruleCode: ruleCode(item.ruleCode, warnings, `${prefix}_RULE`), versionCode: ruleVersion(item.versionCode, warnings, `${prefix}_VERSION`) };
  };
  const capabilityRaw = object(pa.capability, "PA_CAP");
  const correlationRaw = object(rel.correlation, "REL_CORR");
  const overviewCapability = object(overviewRisk.capability, "OR_CAP");
  const overviewSbl = object(overviewRisk.sbl, "OR_SBL");
  const relationshipPointVisibility = Array.isArray(rel.pointVisibility)
    ? rel.pointVisibility.length === 0 || (rel.pointVisibility.length === 1 && rel.pointVisibility[0] === "NONE")
      ? []
      : enumList(rel.pointVisibility, POINT_VISIBILITY, ANALYSIS_COMPONENT_DEFAULTS.parameterRelationship.pointVisibility, 2, warnings, "REL_POINT_VISIBILITY")
    : [...ANALYSIS_COMPONENT_DEFAULTS.parameterRelationship.pointVisibility];
  const evaluationRaw = object(detail.evaluation_filter, "DETAIL_EVALUATION_FILTER");
  rejectUnknown(evaluationRaw, ["evaluation_type", "evaluation_results", "rule_code", "rule_version"], "DETAIL_EVALUATION_FILTER_FIELDS");
  const evaluationType = nullableEnum(evaluationRaw.evaluation_type, DETAIL_EVALUATION_TYPES, warnings, "DETAIL_EVALUATION_TYPE");
  const evaluationResults = Array.isArray(evaluationRaw.evaluation_results)
    ? enumList(evaluationRaw.evaluation_results, DETAIL_EVALUATION_RESULTS, [], DETAIL_EVALUATION_RESULTS.length, warnings, "DETAIL_EVALUATION_RESULTS")
    : [];
  const evaluationRuleCode = evaluationRaw.rule_code == null ? "" : ruleCode(evaluationRaw.rule_code, warnings, "DETAIL_EVALUATION_RULE");
  const evaluationRuleVersion = evaluationRaw.rule_version == null ? "" : ruleVersion(evaluationRaw.rule_version, warnings, "DETAIL_EVALUATION_VERSION");
  const evaluationRuleComplete = Boolean(evaluationRuleCode) === Boolean(evaluationRuleVersion);
  if (!evaluationRuleComplete) invalid(warnings, "DETAIL_EVALUATION_RULE_IDENTITY");
  const evaluationFilter = detail.evaluation_filter != null && evaluationType && evaluationResults.length && evaluationRuleComplete
    ? { evaluation_type: evaluationType, evaluation_results: evaluationResults, rule_code: evaluationRuleCode || null, rule_version: evaluationRuleVersion || null }
    : null;
  if (detail.evaluation_filter != null && !evaluationFilter) invalid(warnings, "DETAIL_EVALUATION_FILTER");
  const measurementRaw = object(detail.measurement_filter, "DETAIL_MEASUREMENT_FILTER");
  rejectUnknown(measurementRaw, ["parameter", "lower_bound", "upper_bound", "lower_inclusive", "upper_inclusive"], "DETAIL_MEASUREMENT_FILTER_FIELDS");
  const measurementParameter = textValue(measurementRaw.parameter, MAX_TEXT, warnings, "DETAIL_MEASUREMENT_PARAMETER");
  const measurementLower = finiteNumber(measurementRaw.lower_bound, warnings, "DETAIL_MEASUREMENT_LOWER");
  const measurementUpper = finiteNumber(measurementRaw.upper_bound, warnings, "DETAIL_MEASUREMENT_UPPER");
  const measurementNumbersValid = (measurementRaw.lower_bound == null || measurementRaw.lower_bound === "" || measurementLower != null)
    && (measurementRaw.upper_bound == null || measurementRaw.upper_bound === "" || measurementUpper != null);
  const measurementOrdered = measurementLower == null || measurementUpper == null || measurementLower <= measurementUpper;
  if (!measurementOrdered) invalid(warnings, "DETAIL_MEASUREMENT_BOUNDS");
  const measurementFilter = detail.measurement_filter != null && measurementParameter && measurementNumbersValid && measurementOrdered
    ? {
        parameter: measurementParameter,
        lower_bound: measurementLower,
        upper_bound: measurementUpper,
        lower_inclusive: bool(measurementRaw.lower_inclusive, true, warnings, "DETAIL_MEASUREMENT_LOWER_INCLUSIVE"),
        upper_inclusive: bool(measurementRaw.upper_inclusive, true, warnings, "DETAIL_MEASUREMENT_UPPER_INCLUSIVE"),
      }
    : null;
  if (detail.measurement_filter != null && !measurementFilter) invalid(warnings, "DETAIL_MEASUREMENT_FILTER");
  return {
    overviewRisk: {
      analyses: enumList(Array.isArray(overviewRisk.analyses) ? overviewRisk.analyses : [], OVERVIEW_RISK_ANALYSES, [], 6, warnings, "OR_ANALYSIS"),
      parameter: textValue(overviewRisk.parameter, MAX_TEXT, warnings, "OR_PARAMETER"),
      groupBy: enumValue(overviewRisk.groupBy, QUALITY_GROUPS, "DATASET", warnings, "OR_GROUP"),
      capability: { method: enumValue(overviewCapability.method, CAPABILITY_METHODS, "CPK_POOLED_WITHIN_RUN_V1", warnings, "OR_CAP_METHOD"), ...ref(overviewCapability, "OR_CAP") },
      pat: ref(overviewRisk.pat, "OR_PAT"), spc: ref(overviewRisk.spc, "OR_SPC"), margin: ref(overviewRisk.margin, "OR_MARGIN"),
      sbl: { ...ref(overviewSbl, "OR_SBL"), binType: enumValue(overviewSbl.binType, ["CP_BIN", "SOFT_BIN", "HARD_BIN"], "CP_BIN", warnings, "OR_SBL_BIN") },
      syl: ref(overviewRisk.syl, "OR_SYL"),
    },
    detail: {
      view: enumValue(detail.view, ["WIDE", "LONG"], "WIDE", warnings, "DETAIL_VIEW"),
      sortBy: enumValue(detail.sortBy, ["UNIT_SEQUENCE", "LOT", "WAFER", "SOURCE_ROW", "RESULT", "SOFT_BIN", "HARD_BIN"], "UNIT_SEQUENCE", warnings, "DETAIL_SORT_BY"),
      sortDirection: enumValue(detail.sortDirection, ["ASC", "DESC"], "ASC", warnings, "DETAIL_SORT_DIRECTION"),
      evaluation_filter: evaluationFilter,
      measurement_filter: measurementFilter,
    },
    parameterAnalysis: {
      groupBy: enumValue(pa.groupBy, ["DATASET"], "DATASET", warnings, "PA_GROUP"),
      analyses: enumList(Array.isArray(pa.analyses) ? pa.analyses : [], PARAMETER_ANALYSES, ANALYSIS_COMPONENT_DEFAULTS.parameterAnalysis.analyses, 5, warnings, "PA_ANALYSIS"),
      boxPlot: ref(pa.boxPlot, "PA_BOX"), histogram: ref(pa.histogram, "PA_HIST"), normalFit: ref(pa.normalFit, "PA_NORMAL"),
      capability: { method: enumValue(capabilityRaw.method, CAPABILITY_METHODS, "CPK_POOLED_WITHIN_RUN_V1", warnings, "PA_CAP_METHOD"), ...ref(capabilityRaw, "PA_CAP") },
      boxParameter: textValue(pa.boxParameter, MAX_TEXT, warnings, "PA_BOX_PARAMETER"),
      histogramDataset: textValue(pa.histogramDataset, MAX_GROUP_KEY, warnings, "PA_HIST_DATASET"),
      histogramParameter: textValue(pa.histogramParameter, MAX_TEXT, warnings, "PA_HIST_PARAMETER"),
      normalFitDataset: textValue(pa.normalFitDataset, MAX_GROUP_KEY, warnings, "PA_NORMAL_DATASET"),
      normalFitParameter: textValue(pa.normalFitParameter, MAX_TEXT, warnings, "PA_NORMAL_PARAMETER"),
    },
    parameterRelationship: {
      xParameter: textValue(rel.xParameter, MAX_TEXT, warnings, "REL_X"),
      yParameters: textList(Array.isArray(rel.yParameters) ? rel.yParameters : [], MAX_TEXT, 5, warnings, "REL_Y"),
      analyses: enumList(Array.isArray(rel.analyses) ? rel.analyses : [], RELATIONSHIP_ANALYSES, ANALYSIS_COMPONENT_DEFAULTS.parameterRelationship.analyses, 3, warnings, "REL_ANALYSIS"),
      groupBy: enumValue(rel.groupBy, RELATIONSHIP_GROUPS, "DATASET", warnings, "REL_GROUP"),
      maxPoints: integer(rel.maxPoints, 10_000, 100, 20_000, warnings, "REL_MAX_POINTS"),
      correlation: { method: "PEARSON_PAIRWISE_V1", ...ref(correlationRaw, "REL_CORR") },
      scatterY: textValue(rel.scatterY, MAX_TEXT, warnings, "REL_SCATTER_Y"), scatterDataset: textValue(rel.scatterDataset, MAX_GROUP_KEY, warnings, "REL_SCATTER_DATASET"),
      trendParameter: textValue(rel.trendParameter, MAX_TEXT, warnings, "REL_TREND_PARAMETER"), correlationScope: textValue(rel.correlationScope, MAX_GROUP_KEY, warnings, "REL_CORR_SCOPE"),
      displayGroups: textList(Array.isArray(rel.displayGroups) ? rel.displayGroups : [], MAX_GROUP_KEY, 50, warnings, "REL_DISPLAY_GROUP"),
      pointVisibility: relationshipPointVisibility,
    },
    spatial: {
      mode: enumValue(sp.mode, SPATIAL_MODES, "BIN_MAP", warnings, "SP_MODE"), parameter: textValue(sp.parameter, MAX_TEXT, warnings, "SP_PARAMETER"),
      maxPoints: integer(sp.maxPoints, 20_000, 100, 50_000, warnings, "SP_MAX_POINTS"), rule: ref(sp.rule, "SP"),
      colorScale: enumValue(sp.colorScale, ["ROBUST", "FULL"], "ROBUST", warnings, "SP_COLOR_SCALE"),
      symbolSize: Number(enumValue(sp.symbolSize == null ? null : String(sp.symbolSize), ["8", "12", "18"], "12", warnings, "SP_SYMBOL_SIZE")) as 8 | 12 | 18,
      showMissing: bool(sp.showMissing, true, warnings, "SP_SHOW_MISSING"),
    },
    quality: {
      analysis: nullableEnum(q.analysis, QUALITY_ANALYSES, warnings, "Q_ANALYSIS"), parameter: textValue(q.parameter, MAX_TEXT, warnings, "Q_PARAMETER"),
      groupBy: nullableEnum(q.groupBy, QUALITY_GROUPS, warnings, "Q_GROUP"), rule: ref(q.rule, "Q"),
      spcOrder: nullableEnum(q.spcOrder, ["UNIT_SEQUENCE"], warnings, "Q_SPC_ORDER"), spcPhase: nullableEnum(q.spcPhase, ["PHASE_I_BASELINE"], warnings, "Q_SPC_PHASE"),
      binType: nullableEnum(q.binType, QUALITY_BIN_TYPES, warnings, "Q_BIN_TYPE"),
      spcDisplayGroup: textValue(q.spcDisplayGroup, MAX_GROUP_KEY, warnings, "Q_SPC_GROUP"), distributionDisplayGroup: textValue(q.distributionDisplayGroup, MAX_GROUP_KEY, warnings, "Q_DISTRIBUTION_GROUP"),
      marginDisplayGroup: textValue(q.marginDisplayGroup, MAX_GROUP_KEY, warnings, "Q_MARGIN_GROUP"), cooccurrenceDisplayGroup: textValue(q.cooccurrenceDisplayGroup, MAX_GROUP_KEY, warnings, "Q_COOCCURRENCE_GROUP"),
      sblDisplayBin: textValue(q.sblDisplayBin, MAX_GROUP_KEY, warnings, "Q_SBL_BIN"), sylDisplayDataset: textValue(q.sylDisplayDataset, MAX_GROUP_KEY, warnings, "Q_SYL_DATASET"),
      percentAxisMode: enumValue(q.percentAxisMode, ["AUTO", "FIXED_0_100"], "AUTO", warnings, "Q_PERCENT_AXIS"),
    },
    waferSummary: {
      sortBy: enumValue(ws.sortBy, ["DATASET", "LOT", "WAFER", "UNIT_COUNT", "YIELD"], "DATASET", warnings, "WAFER_SORT_BY"),
      sortDirection: enumValue(ws.sortDirection, ["ASC", "DESC"], "ASC", warnings, "WAFER_SORT_DIRECTION"),
    },
  };
}

export function parseAnalysisComponentState(params: URLSearchParams, warnings: WarningSink): AnalysisComponentState {
  const contract = params.get("view_contract");
  if (contract !== null && contract !== ANALYSIS_VIEW_CONTRACT_VERSION) invalid(warnings, "CONTRACT");
  return normalizeAnalysisComponentState({
    overviewRisk: {
      analyses: params.getAll("or_analysis"), parameter: params.get("or_parameter"), groupBy: params.get("or_group"),
      capability: { method: params.get("or_cap_method"), ruleCode: params.get("or_cap_rule"), versionCode: params.get("or_cap_version") },
      pat: { ruleCode: params.get("or_pat_rule"), versionCode: params.get("or_pat_version") },
      spc: { ruleCode: params.get("or_spc_rule"), versionCode: params.get("or_spc_version") },
      margin: { ruleCode: params.get("or_margin_rule"), versionCode: params.get("or_margin_version") },
      sbl: { ruleCode: params.get("or_sbl_rule"), versionCode: params.get("or_sbl_version"), binType: params.get("or_sbl_bin") },
      syl: { ruleCode: params.get("or_syl_rule"), versionCode: params.get("or_syl_version") },
    },
    detail: {
      view: params.get("detail_view"), sortBy: params.get("detail_sort_by"), sortDirection: params.get("detail_sort_direction"),
      evaluation_filter: params.has("detail_eval_type") || params.has("detail_eval_result") || params.has("detail_eval_rule") || params.has("detail_eval_version") ? {
        evaluation_type: params.get("detail_eval_type"), evaluation_results: params.getAll("detail_eval_result"), rule_code: params.get("detail_eval_rule"), rule_version: params.get("detail_eval_version"),
      } : null,
      measurement_filter: params.has("detail_measure_parameter") || params.has("detail_measure_lower") || params.has("detail_measure_upper") ? {
        parameter: params.get("detail_measure_parameter"), lower_bound: params.get("detail_measure_lower"), upper_bound: params.get("detail_measure_upper"),
        lower_inclusive: params.get("detail_measure_lower_inclusive"), upper_inclusive: params.get("detail_measure_upper_inclusive"),
      } : null,
    },
    parameterAnalysis: {
      groupBy: params.get("pa_group"), analyses: params.getAll("pa_analysis"),
      boxPlot: { ruleCode: params.get("pa_box_rule"), versionCode: params.get("pa_box_version") },
      histogram: { ruleCode: params.get("pa_hist_rule"), versionCode: params.get("pa_hist_version") },
      normalFit: { ruleCode: params.get("pa_normal_rule"), versionCode: params.get("pa_normal_version") },
      capability: { method: params.get("pa_cap_method"), ruleCode: params.get("pa_cap_rule"), versionCode: params.get("pa_cap_version") },
      boxParameter: params.get("pa_box_parameter"), histogramDataset: params.get("pa_hist_dataset"), histogramParameter: params.get("pa_hist_parameter"),
      normalFitDataset: params.get("pa_normal_dataset"), normalFitParameter: params.get("pa_normal_parameter"),
    },
    parameterRelationship: {
      xParameter: params.get("rel_x"), yParameters: params.getAll("rel_y"), analyses: params.getAll("rel_analysis"), groupBy: params.get("rel_group"), maxPoints: params.get("rel_max_points"),
      correlation: { ruleCode: params.get("rel_corr_rule"), versionCode: params.get("rel_corr_version") }, scatterY: params.get("rel_scatter_y"), scatterDataset: params.get("rel_scatter_dataset"),
      trendParameter: params.get("rel_trend_parameter"), correlationScope: params.get("rel_corr_scope"), displayGroups: params.getAll("rel_display_group"), pointVisibility: params.has("rel_point_visibility") ? params.getAll("rel_point_visibility") : [...ANALYSIS_COMPONENT_DEFAULTS.parameterRelationship.pointVisibility],
    },
    spatial: {
      mode: params.get("sp_mode"), parameter: params.get("sp_parameter"), maxPoints: params.get("sp_max_points"),
      rule: { ruleCode: params.get("sp_rule"), versionCode: params.get("sp_rule_version") }, colorScale: params.get("sp_color_scale"),
      symbolSize: params.get("sp_symbol_size"), showMissing: params.get("sp_show_missing"),
    },
    quality: {
      analysis: params.get("q_analysis"), parameter: params.get("q_parameter"), groupBy: params.get("q_group"), rule: { ruleCode: params.get("q_rule"), versionCode: params.get("q_rule_version") },
      spcOrder: params.get("q_spc_order"), spcPhase: params.get("q_spc_phase"), binType: params.get("q_bin_type"), spcDisplayGroup: params.get("q_spc_group"),
      distributionDisplayGroup: params.get("q_distribution_group"), marginDisplayGroup: params.get("q_margin_group"), cooccurrenceDisplayGroup: params.get("q_cooccurrence_group"),
      sblDisplayBin: params.get("q_sbl_bin"), sylDisplayDataset: params.get("q_syl_dataset"), percentAxisMode: params.get("q_percent_axis"),
    },
    waferSummary: { sortBy: params.get("wafer_sort_by"), sortDirection: params.get("wafer_sort_direction") },
  }, warnings);
}

const setIf = (params: URLSearchParams, key: string, value: string, fallback = "") => { if (value !== fallback) params.set(key, value); };
const append = (params: URLSearchParams, key: string, values: readonly string[], defaults: readonly string[] = []) => {
  if (values.length === defaults.length && values.every((value, index) => value === defaults[index])) return;
  for (const value of values) params.append(key, value);
};
export function serializeAnalysisComponentState(config: AnalysisComponentState, params: URLSearchParams): void {
  const normalized = normalizeAnalysisComponentState(config);
  const or = normalized.overviewRisk; const detail = normalized.detail; const pa = normalized.parameterAnalysis; const rel = normalized.parameterRelationship; const sp = normalized.spatial; const q = normalized.quality; const ws = normalized.waferSummary;
  params.set("view_contract", ANALYSIS_VIEW_CONTRACT_VERSION);
  append(params, "or_analysis", or.analyses); setIf(params, "or_parameter", or.parameter); setIf(params, "or_group", or.groupBy, "DATASET");
  setIf(params, "or_cap_method", or.capability.method, "CPK_POOLED_WITHIN_RUN_V1"); setIf(params, "or_cap_rule", or.capability.ruleCode); setIf(params, "or_cap_version", or.capability.versionCode);
  setIf(params, "or_pat_rule", or.pat.ruleCode); setIf(params, "or_pat_version", or.pat.versionCode); setIf(params, "or_spc_rule", or.spc.ruleCode); setIf(params, "or_spc_version", or.spc.versionCode);
  setIf(params, "or_margin_rule", or.margin.ruleCode); setIf(params, "or_margin_version", or.margin.versionCode); setIf(params, "or_sbl_rule", or.sbl.ruleCode); setIf(params, "or_sbl_version", or.sbl.versionCode); setIf(params, "or_sbl_bin", or.sbl.binType, "CP_BIN"); setIf(params, "or_syl_rule", or.syl.ruleCode); setIf(params, "or_syl_version", or.syl.versionCode);
  setIf(params, "detail_view", detail.view, "WIDE"); setIf(params, "detail_sort_by", detail.sortBy, "UNIT_SEQUENCE"); setIf(params, "detail_sort_direction", detail.sortDirection, "ASC");
  if (detail.evaluation_filter) {
    params.set("detail_eval_type", detail.evaluation_filter.evaluation_type); append(params, "detail_eval_result", detail.evaluation_filter.evaluation_results);
    setIf(params, "detail_eval_rule", detail.evaluation_filter.rule_code ?? ""); setIf(params, "detail_eval_version", detail.evaluation_filter.rule_version ?? "");
  }
  if (detail.measurement_filter) {
    params.set("detail_measure_parameter", detail.measurement_filter.parameter);
    if (detail.measurement_filter.lower_bound != null) params.set("detail_measure_lower", String(detail.measurement_filter.lower_bound));
    if (detail.measurement_filter.upper_bound != null) params.set("detail_measure_upper", String(detail.measurement_filter.upper_bound));
    if (!detail.measurement_filter.lower_inclusive) params.set("detail_measure_lower_inclusive", "0");
    if (!detail.measurement_filter.upper_inclusive) params.set("detail_measure_upper_inclusive", "0");
  }
  append(params, "pa_analysis", pa.analyses, ANALYSIS_COMPONENT_DEFAULTS.parameterAnalysis.analyses); setIf(params, "pa_group", pa.groupBy, "DATASET");
  setIf(params, "pa_box_rule", pa.boxPlot.ruleCode); setIf(params, "pa_box_version", pa.boxPlot.versionCode); setIf(params, "pa_hist_rule", pa.histogram.ruleCode); setIf(params, "pa_hist_version", pa.histogram.versionCode);
  setIf(params, "pa_normal_rule", pa.normalFit.ruleCode); setIf(params, "pa_normal_version", pa.normalFit.versionCode); setIf(params, "pa_cap_method", pa.capability.method, "CPK_POOLED_WITHIN_RUN_V1"); setIf(params, "pa_cap_rule", pa.capability.ruleCode); setIf(params, "pa_cap_version", pa.capability.versionCode);
  setIf(params, "pa_box_parameter", pa.boxParameter); setIf(params, "pa_hist_dataset", pa.histogramDataset); setIf(params, "pa_hist_parameter", pa.histogramParameter); setIf(params, "pa_normal_dataset", pa.normalFitDataset); setIf(params, "pa_normal_parameter", pa.normalFitParameter);
  setIf(params, "rel_x", rel.xParameter); append(params, "rel_y", rel.yParameters); append(params, "rel_analysis", rel.analyses, ANALYSIS_COMPONENT_DEFAULTS.parameterRelationship.analyses); setIf(params, "rel_group", rel.groupBy, "DATASET"); if (rel.maxPoints !== 10_000) params.set("rel_max_points", String(rel.maxPoints));
  setIf(params, "rel_corr_rule", rel.correlation.ruleCode); setIf(params, "rel_corr_version", rel.correlation.versionCode); setIf(params, "rel_scatter_y", rel.scatterY); setIf(params, "rel_scatter_dataset", rel.scatterDataset); setIf(params, "rel_trend_parameter", rel.trendParameter); setIf(params, "rel_corr_scope", rel.correlationScope); append(params, "rel_display_group", rel.displayGroups); if (!rel.pointVisibility.length) params.append("rel_point_visibility", "NONE"); else append(params, "rel_point_visibility", rel.pointVisibility, ANALYSIS_COMPONENT_DEFAULTS.parameterRelationship.pointVisibility);
  setIf(params, "sp_mode", sp.mode, "BIN_MAP"); setIf(params, "sp_parameter", sp.parameter); if (sp.maxPoints !== 20_000) params.set("sp_max_points", String(sp.maxPoints)); setIf(params, "sp_rule", sp.rule.ruleCode); setIf(params, "sp_rule_version", sp.rule.versionCode); setIf(params, "sp_color_scale", sp.colorScale, "ROBUST"); if (sp.symbolSize !== 12) params.set("sp_symbol_size", String(sp.symbolSize)); if (!sp.showMissing) params.set("sp_show_missing", "0");
  setIf(params, "q_analysis", q.analysis ?? ""); setIf(params, "q_parameter", q.parameter); setIf(params, "q_group", q.groupBy ?? ""); setIf(params, "q_rule", q.rule.ruleCode); setIf(params, "q_rule_version", q.rule.versionCode); setIf(params, "q_spc_order", q.spcOrder ?? ""); setIf(params, "q_spc_phase", q.spcPhase ?? ""); setIf(params, "q_bin_type", q.binType ?? ""); setIf(params, "q_spc_group", q.spcDisplayGroup); setIf(params, "q_distribution_group", q.distributionDisplayGroup); setIf(params, "q_margin_group", q.marginDisplayGroup); setIf(params, "q_cooccurrence_group", q.cooccurrenceDisplayGroup); setIf(params, "q_sbl_bin", q.sblDisplayBin); setIf(params, "q_syl_dataset", q.sylDisplayDataset); setIf(params, "q_percent_axis", q.percentAxisMode, "AUTO");
  setIf(params, "wafer_sort_by", ws.sortBy, "DATASET"); setIf(params, "wafer_sort_direction", ws.sortDirection, "ASC");
}

export interface PersistedAnalysisComponentState {
  readonly contract_version: typeof ANALYSIS_VIEW_CONTRACT_VERSION;
  readonly components: AnalysisComponentState;
}

export function persistAnalysisComponentState(config: AnalysisComponentState): PersistedAnalysisComponentState {
  return {
    contract_version: ANALYSIS_VIEW_CONTRACT_VERSION,
    components: normalizeAnalysisComponentState(config),
  };
}

export function restorePersistedAnalysisComponentState(raw: unknown): { analysis: AnalysisComponentState; warnings: string[] } {
  const warnings = new Set<string>();
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
    invalid(warnings, "SAVED_ANALYSIS_STATE");
    return { analysis: normalizeAnalysisComponentState(ANALYSIS_COMPONENT_DEFAULTS), warnings: Array.from(warnings).sort() };
  }
  const record = raw as Record<string, unknown>;
  if (record.contract_version !== ANALYSIS_VIEW_CONTRACT_VERSION) invalid(warnings, "SAVED_CONTRACT");
  if (Object.keys(record).some((key) => key !== "contract_version" && key !== "components")) invalid(warnings, "SAVED_FIELDS");
  const analysis = record.contract_version === ANALYSIS_VIEW_CONTRACT_VERSION
    ? normalizeAnalysisComponentState(record.components, warnings)
    : normalizeAnalysisComponentState(ANALYSIS_COMPONENT_DEFAULTS);
  return { analysis, warnings: Array.from(warnings).sort() };
}
