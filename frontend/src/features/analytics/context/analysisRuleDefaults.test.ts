import { describe, expect, it } from "vitest";

import { ANALYSIS_COMPONENT_DEFAULTS, normalizeAnalysisComponentState } from "./analysisViewConfig";
import { applyContextRuleDefaults, parseContextRule, resolveContextRule } from "./analysisRuleDefaults";

describe("analysis rule defaults", () => {
  it("parses only canonical Dataset rule references", () => {
    expect(parseContextRule("RULE:FT_PAT:V2")).toEqual({ ruleCode: "FT_PAT", versionCode: "V2" });
    expect(parseContextRule("FT_PAT:V2")).toBeNull();
    expect(parseContextRule("RULE:FT_PAT")).toBeNull();
  });

  it("returns no default when multiple matching rules would be ambiguous", () => {
    expect(resolveContextRule(["RULE:FT_PAT_A:V1", "RULE:FT_PAT_B:V1"], "PAT_ROBUST_IQR")).toBeNull();
  });

  it("does not confuse a marker embedded inside another rule word", () => {
    expect(resolveContextRule(["RULE:CP_SPATIAL_MAP:V1"], "PAT_ROBUST_IQR")).toBeNull();
  });

  it("fills empty selected methods while preserving an explicit operator override", () => {
    const state = normalizeAnalysisComponentState({
      ...ANALYSIS_COMPONENT_DEFAULTS,
      parameterAnalysis: {
        ...ANALYSIS_COMPONENT_DEFAULTS.parameterAnalysis,
        analyses: ["BOX_PLOT", "HISTOGRAM"],
        histogram: { ruleCode: "MANUAL_HIST", versionCode: "V9" },
      },
      quality: { ...ANALYSIS_COMPONENT_DEFAULTS.quality, analysis: "PAT_ROBUST_IQR" },
    });
    const result = applyContextRuleDefaults(state, [
      "RULE:CP_BOX_STANDARD:V1",
      "RULE:CP_HIST_STANDARD:V2",
      "RULE:CP_PAT_STANDARD:V3",
    ]);

    expect(result.parameterAnalysis.boxPlot).toEqual({ ruleCode: "CP_BOX_STANDARD", versionCode: "V1" });
    expect(result.parameterAnalysis.histogram).toEqual({ ruleCode: "MANUAL_HIST", versionCode: "V9" });
    expect(result.quality.rule).toEqual({ ruleCode: "CP_PAT_STANDARD", versionCode: "V3" });
  });
});
