import { describe, expect, it } from "vitest";

import { createDefaultAnalysisViewState } from "./analysisViewState";
import { exportAnalysisConfig, exportChartConfig, savedChartConfig } from "./analysisPresentation";

describe("analysis presentation contracts", () => {
  it("maps bounded component state into each strict export analysis envelope", () => {
    const state = createDefaultAnalysisViewState();
    const configured = {
      ...state,
      analysis: {
        ...state.analysis,
        overviewRisk: { ...state.analysis.overviewRisk, analyses: ["CAPABILITY", "SPC_I_MR", "SBL_GROUPED_LIMIT"] as const, parameter: "VTH", groupBy: "RUN" as const, capability: { method: "CPK_POOLED_WITHIN_RUN_V1" as const, ruleCode: "CP_CAP", versionCode: "v1" }, spc: { ruleCode: "CP_SPC", versionCode: "v2" }, sbl: { ruleCode: "FT_SBL", versionCode: "v3", binType: "SOFT_BIN" as const } },
        parameterAnalysis: { ...state.analysis.parameterAnalysis, analyses: ["DESCRIPTIVE", "BOX_PLOT"] as const, boxPlot: { ruleCode: "CP_BOX", versionCode: "v1" } },
        parameterRelationship: { ...state.analysis.parameterRelationship, xParameter: "VTH", yParameters: ["RDON"], analyses: ["SCATTER", "CORRELATION"] as const, correlation: { method: "PEARSON_PAIRWISE_V1" as const, ruleCode: "CP_CORR", versionCode: "v1" } },
        spatial: { ...state.analysis.spatial, mode: "ZONE_COMPARISON" as const, parameter: "VTH", rule: { ruleCode: "CP_ZONE", versionCode: "v1" } },
        quality: { ...state.analysis.quality, analysis: "SPC_I_MR" as const, parameter: "VTH", groupBy: "RUN" as const, rule: { ruleCode: "FT_SPC", versionCode: "v1" }, spcOrder: "UNIT_SEQUENCE" as const, spcPhase: "PHASE_I_BASELINE" as const },
        waferSummary: { sortBy: "YIELD" as const, sortDirection: "DESC" as const },
      },
    };

    expect(exportAnalysisConfig("ANALYTICS_OVERVIEW", configured.analysis, ["VTH"], 20)).toMatchObject({
      contract_version: "ANALYTICS_EXPORT_ANALYSIS_CONFIG_V1", section: "OVERVIEW",
      overview: { evaluations: [
        { analysis: "CAPABILITY", parameter: "VTH", rule: { rule_code: "CP_CAP", version_code: "v1" } },
        { analysis: "SPC_I_MR", spc_order: "UNIT_SEQUENCE", spc_phase: "PHASE_I_BASELINE" },
        { analysis: "SBL_GROUPED_LIMIT", bin_type: "SOFT_BIN" },
      ] },
    });
    expect(exportAnalysisConfig("PARAMETER_ANALYSIS", configured.analysis, ["VTH"], 20)).toMatchObject({ section: "PARAMETER_ANALYSIS", parameter_analysis: { parameters: ["VTH"], analyses: ["DESCRIPTIVE", "BOX_PLOT"], box_plot: { rule_code: "CP_BOX", version_code: "v1" } } });
    expect(exportAnalysisConfig("PARAMETER_RELATIONSHIP", configured.analysis, ["VTH"], 20)).toMatchObject({ section: "PARAMETER_RELATIONSHIP", parameter_relationship: { x_parameter: "VTH", y_parameters: ["RDON"], correlation: { method: "PEARSON_PAIRWISE_V1", rule_code: "CP_CORR", version_code: "v1" } } });
    expect(exportAnalysisConfig("SPATIAL_ANALYSIS", configured.analysis, ["VTH"], 20)).toMatchObject({ section: "SPATIAL_ANALYSIS", spatial_analysis: { mode: "ZONE_COMPARISON", parameter: "VTH", focus_dataset_id: 20, rule_code: "CP_ZONE", rule_version: "v1" } });
    expect(exportAnalysisConfig("FT_QUALITY", configured.analysis, ["VTH"], 20)).toMatchObject({ section: "FT_QUALITY", ft_quality: { analysis: "SPC_I_MR", parameter: "VTH", group_by: "RUN", spc_order: "UNIT_SEQUENCE", spc_phase: "PHASE_I_BASELINE" } });
    expect(exportAnalysisConfig("WAFER_SUMMARY", configured.analysis, [], 20)).toEqual({ contract_version: "ANALYTICS_EXPORT_ANALYSIS_CONFIG_V1", section: "WAFER_SUMMARY", wafer_summary: { sort_by: "YIELD", sort_direction: "DESC" } });
    expect(exportChartConfig("ANALYTICS_DETAIL", configured, ["VTH"], 20)).not.toHaveProperty("analysis");
  });

  it("fails before enqueue when an exact rule or required analysis identity is absent", () => {
    const state = createDefaultAnalysisViewState();
    expect(() => exportAnalysisConfig("PARAMETER_ANALYSIS", { ...state.analysis, parameterAnalysis: { ...state.analysis.parameterAnalysis, analyses: ["BOX_PLOT"] } }, ["VTH"], 20)).toThrow("Box Plot Rule Code");
    expect(() => exportAnalysisConfig("PARAMETER_RELATIONSHIP", state.analysis, [], 20)).toThrow("Relationship X Parameter");
    expect(() => exportAnalysisConfig("SPATIAL_ANALYSIS", { ...state.analysis, spatial: { ...state.analysis.spatial, mode: "ZONE_COMPARISON" } }, [], 20)).toThrow("Spatial Zone Rule Code");
    expect(() => exportAnalysisConfig("FT_QUALITY", state.analysis, [], 20)).toThrow("FT Quality");
  });

  it("stores the complete versioned view state separately from display primitives", () => {
    const defaults = createDefaultAnalysisViewState();
    const state = {
      ...defaults,
      analysis: {
        ...defaults.analysis,
        detail: {
          ...defaults.analysis.detail,
          evaluation_filter: { evaluation_type: "PAT" as const, evaluation_results: ["FAIL" as const], rule_code: "CP_PAT", rule_version: "V2" },
          measurement_filter: { parameter: "VTH", lower_bound: null, upper_bound: 1.5, lower_inclusive: true, upper_inclusive: false },
        },
      },
    };
    expect(savedChartConfig(state)).toMatchObject({
      analysis_view_state: {
        contract_version: "ANALYSIS_VIEW_STATE_V1",
        components: expect.objectContaining({ detail: state.analysis.detail }),
      },
      brush_enabled: true,
      spatial_layer_mode: "STACK",
    });
  });
});
