import type {
  AnalysisComponentState,
  ExactRuleState,
  QualityAnalysis,
} from "./analysisViewConfig";

const RULE_PREFIX = "RULE:";

export function parseContextRule(value: string): ExactRuleState | null {
  if (!value.startsWith(RULE_PREFIX)) return null;
  const parts = value.slice(RULE_PREFIX.length).split(":");
  if (parts.length !== 2 || !parts[0] || !parts[1]) return null;
  return { ruleCode: parts[0], versionCode: parts[1] };
}

const PURPOSE_MARKERS: Record<string, readonly string[]> = {
  BOX_PLOT: ["BOX"],
  HISTOGRAM: ["HIST"],
  NORMAL_FIT: ["NORMAL"],
  CAPABILITY: ["CPK", "CAPABILITY"],
  CORRELATION: ["CORRELATION", "CORR"],
  ZONE_COMPARISON: ["ZONE"],
  PAT_ROBUST_IQR: ["PAT"],
  SPC_I_MR: ["SPC"],
  MARGIN_OOS: ["MARGIN", "OOS"],
  BIN_COOCCURRENCE: ["BIN_COOCCURRENCE", "COOCCURRENCE"],
  SBL_GROUPED_LIMIT: ["SBL"],
  SYL_GROUPED_LIMIT: ["SYL"],
  PASS_FAIL_DISTRIBUTION: ["PASS_FAIL", "DISTRIBUTION"],
};

export function resolveContextRule(values: readonly string[], purpose: string): ExactRuleState | null {
  const markers = PURPOSE_MARKERS[purpose] ?? [];
  const matchesPurpose = (ruleCode: string) => markers.some((marker) => {
    const escaped = marker.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    return new RegExp(`(?:^|[^A-Z0-9])${escaped}(?:[^A-Z0-9]|$)`).test(ruleCode);
  });
  const matches = values
    .map(parseContextRule)
    .filter((item): item is ExactRuleState => item !== null)
    .filter((item) => matchesPurpose(item.ruleCode));
  const unique = Array.from(new Map(matches.map((item) => [`${item.ruleCode}:${item.versionCode}`, item])).values());
  return unique.length === 1 ? unique[0] : null;
}

function defaultRule(current: ExactRuleState, values: readonly string[], purpose: string): ExactRuleState {
  if (current.ruleCode || current.versionCode) return current;
  return resolveContextRule(values, purpose) ?? current;
}

export function applyContextRuleDefaults(
  state: AnalysisComponentState,
  values: readonly string[],
): AnalysisComponentState {
  let changed = false;
  const useRule = (current: ExactRuleState, purpose: string) => {
    const next = defaultRule(current, values, purpose);
    if (next !== current) changed = true;
    return next;
  };

  const parameterAnalysis = {
    ...state.parameterAnalysis,
    boxPlot: state.parameterAnalysis.analyses.includes("BOX_PLOT")
      ? useRule(state.parameterAnalysis.boxPlot, "BOX_PLOT")
      : state.parameterAnalysis.boxPlot,
    histogram: state.parameterAnalysis.analyses.includes("HISTOGRAM")
      ? useRule(state.parameterAnalysis.histogram, "HISTOGRAM")
      : state.parameterAnalysis.histogram,
    normalFit: state.parameterAnalysis.analyses.includes("NORMAL_FIT")
      ? useRule(state.parameterAnalysis.normalFit, "NORMAL_FIT")
      : state.parameterAnalysis.normalFit,
    capability: state.parameterAnalysis.analyses.includes("CAPABILITY")
      ? { ...state.parameterAnalysis.capability, ...useRule(state.parameterAnalysis.capability, "CAPABILITY") }
      : state.parameterAnalysis.capability,
  };
  const parameterRelationship = state.parameterRelationship.analyses.includes("CORRELATION")
    ? { ...state.parameterRelationship, correlation: { ...state.parameterRelationship.correlation, ...useRule(state.parameterRelationship.correlation, "CORRELATION") } }
    : state.parameterRelationship;
  const spatial = state.spatial.mode === "ZONE_COMPARISON"
    ? { ...state.spatial, rule: useRule(state.spatial.rule, "ZONE_COMPARISON") }
    : state.spatial;
  const qualityPurpose = state.quality.analysis as QualityAnalysis | null;
  const quality = qualityPurpose
    ? { ...state.quality, rule: useRule(state.quality.rule, qualityPurpose) }
    : state.quality;

  const overviewRisk = {
    ...state.overviewRisk,
    capability: state.overviewRisk.analyses.includes("CAPABILITY")
      ? { ...state.overviewRisk.capability, ...useRule(state.overviewRisk.capability, "CAPABILITY") }
      : state.overviewRisk.capability,
    pat: state.overviewRisk.analyses.includes("PAT_ROBUST_IQR") ? useRule(state.overviewRisk.pat, "PAT_ROBUST_IQR") : state.overviewRisk.pat,
    spc: state.overviewRisk.analyses.includes("SPC_I_MR") ? useRule(state.overviewRisk.spc, "SPC_I_MR") : state.overviewRisk.spc,
    margin: state.overviewRisk.analyses.includes("MARGIN_OOS") ? useRule(state.overviewRisk.margin, "MARGIN_OOS") : state.overviewRisk.margin,
    sbl: state.overviewRisk.analyses.includes("SBL_GROUPED_LIMIT")
      ? { ...state.overviewRisk.sbl, ...useRule(state.overviewRisk.sbl, "SBL_GROUPED_LIMIT") }
      : state.overviewRisk.sbl,
    syl: state.overviewRisk.analyses.includes("SYL_GROUPED_LIMIT") ? useRule(state.overviewRisk.syl, "SYL_GROUPED_LIMIT") : state.overviewRisk.syl,
  };

  return changed ? { ...state, parameterAnalysis, parameterRelationship, spatial, quality, overviewRisk } : state;
}
