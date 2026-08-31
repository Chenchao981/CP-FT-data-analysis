import { describe, expect, it } from "vitest";

import type { SavedAnalysisRecord } from "../../api/savedAnalyses";
import { persistAnalysisComponentState } from "./context/analysisViewConfig";
import { createDefaultAnalysisViewState, parseAnalysisViewState } from "./context/analysisViewState";
import { savedAnalysisRestoreParams } from "./savedAnalysisRestore";

const record: SavedAnalysisRecord = {
  saved_analysis_id: 41,
  analysis_name: "Fail map",
  owner_user_id: 7,
  lifecycle_status: "ACTIVE",
  current_revision_no: 2,
  row_version: "00000000000000AF",
  restore_status: "CURRENT",
  revision: {
    saved_analysis_revision_id: 82,
    revision_no: 2,
    contract_version: "SAVED_ANALYSIS_V1",
    filters: { lot_ids: ["LOT-B", "LOT-A"], wafer_ids: ["W2"], bin_codes: ["2"], overall_results: ["FAIL"], source_ids: ["S2"], tester_ids: ["T2"], program_versions: ["P2"], test_conditions: ["C2"] },
    parameters: ["VTH", "RDON"],
    filter_hash: "a".repeat(64),
    context_hash: "b".repeat(64),
    rule_context: { spec_versions: ["SPEC:7"], bin_mapping_versions: ["BIN:2"], evaluation_rule_versions: ["RULE:3"] },
    chart_config: { correlation_min_abs: 0.7 },
    display_config: { section: "spatial", page: 3, page_size: 20, focus_dataset_id: 21 },
    datasets: [
      { dataset_version_id: 201, dataset_id: 20, version_no: 1, ordinal_no: 2, test_stage: "CP", status: "CURRENT" },
      { dataset_version_id: 212, dataset_id: 21, version_no: 2, ordinal_no: 1, test_stage: "CP", status: "CURRENT" },
    ],
    created_by_user_id: 7,
    created_at_utc: "2026-08-31T00:00:00Z",
  },
  created_at_utc: "2026-08-31T00:00:00Z",
  updated_at_utc: "2026-08-31T01:00:00Z",
};

describe("Saved Analysis restore codec", () => {
  it("restores pinned datasets, strict filters and display state while preserving unrelated URL state", () => {
    const params = savedAnalysisRestoreParams(record, new URLSearchParams("dataset=99%3A9&detail_dataset=99%3A9&future_flag=keep&lot_id=OLD"));
    expect(params).not.toBeNull();
    expect(params!.getAll("dataset")).toEqual(["21:2", "20:1"]);
    expect(params!.get("detail_dataset")).toBe("21:2");
    expect(params!.getAll("lot_id")).toEqual(["LOT-A", "LOT-B"]);
    expect(params!.getAll("parameter")).toEqual(["RDON", "VTH"]);
    expect(params!.get("section")).toBe("spatial");
    expect(params!.get("page")).toBe("3");
    expect(params!.get("page_size")).toBe("20");
    expect(params!.get("chart_corr_min_abs")).toBe("0.7");
    expect(params!.get("future_flag")).toBe("keep");
  });

  it.each(["NON_CURRENT", "RULE_CHANGED", "ACCESS_REVOKED"] as const)("fails closed for %s", (restoreStatus) => {
    expect(savedAnalysisRestoreParams({ ...record, restore_status: restoreStatus }, new URLSearchParams())).toBeNull();
  });

  it("restores the complete versioned component request/display state", () => {
    const defaults = createDefaultAnalysisViewState();
    const analysis = {
      ...defaults.analysis,
      detail: {
        ...defaults.analysis.detail,
        evaluation_filter: { evaluation_type: "PAT" as const, evaluation_results: ["FAIL" as const], rule_code: "CP_PAT", rule_version: "V2" },
        measurement_filter: { parameter: "VTH", lower_bound: 1.2, upper_bound: 2.4, lower_inclusive: true, upper_inclusive: false },
      },
      parameterRelationship: { ...defaults.analysis.parameterRelationship, xParameter: "VTH", yParameters: ["RDON"], analyses: ["SCATTER", "CORRELATION"] as const, correlation: { method: "PEARSON_PAIRWISE_V1" as const, ruleCode: "CP_CORR", versionCode: "v1" } },
      spatial: { ...defaults.analysis.spatial, mode: "ZONE_COMPARISON" as const, parameter: "VTH", rule: { ruleCode: "CP_ZONE", versionCode: "v2" }, showMissing: false },
      quality: { ...defaults.analysis.quality, analysis: "SPC_I_MR" as const, parameter: "VTH", groupBy: "RUN" as const, rule: { ruleCode: "FT_SPC", versionCode: "v3" }, spcOrder: "UNIT_SEQUENCE" as const, spcPhase: "PHASE_I_BASELINE" as const, percentAxisMode: "FIXED_0_100" as const },
    };
    const configured: SavedAnalysisRecord = {
      ...record,
      revision: { ...record.revision, chart_config: { ...record.revision.chart_config, analysis_view_state: persistAnalysisComponentState(analysis) } },
    };
    const restored = savedAnalysisRestoreParams(configured, new URLSearchParams());
    expect(restored).not.toBeNull();
    expect(parseAnalysisViewState(restored!).analysis).toEqual(analysis);
  });

  it("defaults and retains a stable warning for an unknown saved view-state version", () => {
    const configured: SavedAnalysisRecord = {
      ...record,
      revision: { ...record.revision, chart_config: { analysis_view_state: { contract_version: "V999", components: { spatial: { mode: "SCRIPT" } } } } },
    };
    const restored = savedAnalysisRestoreParams(configured, new URLSearchParams());
    const parsed = parseAnalysisViewState(restored!);
    expect(parsed.analysis).toEqual(createDefaultAnalysisViewState().analysis);
    expect(parsed.warnings).toContain("ANALYSIS_VIEW_INVALID_SAVED_CONTRACT");
  });
});
