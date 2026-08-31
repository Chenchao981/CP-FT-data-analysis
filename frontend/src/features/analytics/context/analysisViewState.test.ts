import { describe, expect, it } from "vitest";
import { ANALYSIS_COMPONENT_DEFAULTS, ANALYSIS_VIEW_CONTRACT_VERSION } from "./analysisViewConfig";

import {
  ANALYSIS_VIEW_DEFAULTS,
  ANALYSIS_VIEW_FILTER_LIMITS,
  createDefaultAnalysisViewState,
  normalizeAnalysisViewState,
  parseAnalysisViewState,
  serializeAnalysisViewState,
  type AnalysisViewState,
} from "./analysisViewState";

const fullState = (): AnalysisViewState => ({
  ...createDefaultAnalysisViewState(),
  filters: {
    lotIds: ["LOT-B", "LOT-A"],
    waferIds: ["W02", "W01"],
    binCodes: ["5", "1"],
    overallResults: ["UNKNOWN", "PASS"],
    sourceIds: ["SOURCE-B", "SOURCE-A"],
    testerIds: ["TESTER-B", "TESTER-A"],
    programVersions: ["V2", "V1"],
    testConditions: ["VGE=10V", "VGE=0V"],
    parameters: ["VTH", "BVCES"],
  },
  display: { ...ANALYSIS_VIEW_DEFAULTS, section: "parameter", page: 3, pageSize: 100, correlationMinAbs: 0.65, visibleWaferKeys: [] },
});

describe("analysisViewState URL contract", () => {
  it("creates isolated empty defaults with authority filters separate from display state", () => {
    const first = createDefaultAnalysisViewState();
    const second = createDefaultAnalysisViewState();

    expect(first.contractVersion).toBe(ANALYSIS_VIEW_CONTRACT_VERSION);
    expect(first.filters).toEqual({ lotIds: [], waferIds: [], binCodes: [], overallResults: [], sourceIds: [], testerIds: [], programVersions: [], testConditions: [], parameters: [] });
    expect(first.display).toEqual(ANALYSIS_VIEW_DEFAULTS);
    expect(first.analysis).toEqual(ANALYSIS_COMPONENT_DEFAULTS);
    expect(first.warnings).toEqual([]);
    expect(first.filters.lotIds).not.toBe(second.filters.lotIds);
    expect(first).not.toHaveProperty("datasets");
    expect(first.display).not.toHaveProperty("filters");
  });

  it("strictly parses, trims, deduplicates and deterministically orders every supported filter", () => {
    const params = new URLSearchParams();
    for (const value of [" LOT-B ", "LOT-A", "LOT-B", "", "X".repeat(201)]) params.append("lot_id", value);
    for (const value of ["W2", "W1", "W2"]) params.append("wafer_id", value);
    for (const value of ["9", "1", "9"]) params.append("bin_code", value);
    for (const value of ["UNKNOWN", "pass", "PASS", "INVALID", "ABORT", "FAIL"]) params.append("overall_result", value);
    for (const value of ["SRC-2", "SRC-1"]) params.append("source_id", value);
    for (const value of ["T-2", "T-1"]) params.append("tester_id", value);
    for (const value of ["P2", "P1"]) params.append("program_version", value);
    for (const value of [" VGE=5V ", "VGE=0V"]) params.append("test_condition", value);
    for (const value of ["VTH", "BVCES", "VTH"]) params.append("parameter", value);
    params.set("section", "spatial");
    params.set("page", "12");
    params.set("page_size", "200");

    expect(parseAnalysisViewState(params)).toMatchObject({
      filters: {
        lotIds: ["LOT-A", "LOT-B"],
        waferIds: ["W1", "W2"],
        binCodes: ["1", "9"],
        overallResults: ["PASS", "FAIL", "UNKNOWN", "ABORT"],
        sourceIds: ["SRC-1", "SRC-2"],
        testerIds: ["T-1", "T-2"],
        programVersions: ["P1", "P2"],
        testConditions: ["VGE=0V", "VGE=5V"],
        parameters: ["BVCES", "VTH"],
      },
      display: { ...ANALYSIS_VIEW_DEFAULTS, section: "spatial", page: 12, pageSize: 200, visibleWaferKeys: [] },
      contractVersion: ANALYSIS_VIEW_CONTRACT_VERSION,
      analysis: ANALYSIS_COMPONENT_DEFAULTS,
      warnings: [],
    });
  });

  it("enforces collection limits after stable ordering rather than by URL arrival order", () => {
    const ascending = new URLSearchParams();
    const descending = new URLSearchParams();
    const values = Array.from({ length: 70 }, (_, index) => `LOT-${String(index).padStart(3, "0")}`);
    for (const value of values) ascending.append("lot_id", value);
    for (const value of [...values].reverse()) descending.append("lot_id", value);

    const expected = values.slice(0, ANALYSIS_VIEW_FILTER_LIMITS.lotIds);
    expect(parseAnalysisViewState(ascending).filters.lotIds).toEqual(expected);
    expect(parseAnalysisViewState(descending).filters.lotIds).toEqual(expected);
  });

  it.each([
    ["section=unknown", ANALYSIS_VIEW_DEFAULTS],
    ["section=Overview&page=01&page_size=020", ANALYSIS_VIEW_DEFAULTS],
    ["page=0&page_size=0", ANALYSIS_VIEW_DEFAULTS],
    ["page=-1&page_size=201", ANALYSIS_VIEW_DEFAULTS],
    ["page=1.5&page_size=NaN", ANALYSIS_VIEW_DEFAULTS],
    [`page=${Number.MAX_SAFE_INTEGER + 1}&page_size=50`, ANALYSIS_VIEW_DEFAULTS],
  ])("fails closed for invalid display query %s", (query, expected) => {
    expect(parseAnalysisViewState(new URLSearchParams(query)).display).toEqual(expected);
  });

  it("serializes in a canonical key/value order and omits display defaults", () => {
    const state = fullState();
    const serialized = serializeAnalysisViewState(state);

    expect(serialized.toString()).toBe(
      "lot_id=LOT-A&lot_id=LOT-B&wafer_id=W01&wafer_id=W02&bin_code=1&bin_code=5"
      + "&overall_result=PASS&overall_result=UNKNOWN&source_id=SOURCE-A&source_id=SOURCE-B"
      + "&tester_id=TESTER-A&tester_id=TESTER-B&program_version=V1&program_version=V2"
      + "&test_condition=VGE%3D0V&test_condition=VGE%3D10V&parameter=BVCES&parameter=VTH"
      + "&section=parameter&page=3&page_size=100&chart_corr_min_abs=0.65&view_contract=ANALYSIS_VIEW_STATE_V1",
    );

    expect(serializeAnalysisViewState(createDefaultAnalysisViewState()).toString()).toBe("view_contract=ANALYSIS_VIEW_STATE_V1");
  });

  it("preserves Dataset and unrelated URL state while replacing every owned key", () => {
    const base = new URLSearchParams(
      "dataset=20%3A1&dataset=21%3A2&detail_dataset=21%3A2&job_id=91"
      + "&lot_id=STALE&overall_result=FAIL&section=quality&page=9&page_size=20&future_flag=on",
    );
    const serialized = serializeAnalysisViewState(fullState(), base);

    expect(serialized.getAll("dataset")).toEqual(["20:1", "21:2"]);
    expect(serialized.get("detail_dataset")).toBe("21:2");
    expect(serialized.get("job_id")).toBe("91");
    expect(serialized.get("future_flag")).toBe("on");
    expect(serialized.getAll("lot_id")).toEqual(["LOT-A", "LOT-B"]);
    expect(serialized.getAll("overall_result")).toEqual(["PASS", "UNKNOWN"]);
    expect(serialized.get("section")).toBe("parameter");
    expect(serialized.get("page")).toBe("3");
    expect(serialized.get("page_size")).toBe("100");
  });

  it("round-trips to the same normalized state from differently ordered mutable input", () => {
    const dirty = {
      ...fullState(),
      filters: {
        ...fullState().filters,
        lotIds: ["LOT-B", " LOT-A ", "LOT-B"],
        overallResults: ["UNKNOWN", "PASS", "UNKNOWN"],
      },
    } as AnalysisViewState;
    const normalized = normalizeAnalysisViewState(dirty);
    const roundTrip = parseAnalysisViewState(serializeAnalysisViewState(dirty));

    expect(roundTrip).toEqual(normalized);
    expect(serializeAnalysisViewState(roundTrip).toString()).toBe(serializeAnalysisViewState(normalized).toString());
  });

  it("fails closed for an invalid correlation display threshold", () => {
    expect(parseAnalysisViewState(new URLSearchParams("chart_corr_min_abs=1.1")).display.correlationMinAbs).toBe(0);
    expect(parseAnalysisViewState(new URLSearchParams("chart_corr_min_abs=-0.1")).display.correlationMinAbs).toBe(0);
    expect(parseAnalysisViewState(new URLSearchParams("chart_corr_min_abs=0.75")).display.correlationMinAbs).toBe(0.75);
  });

  it("round-trips every bounded analysis request and display selector through the URL", () => {
    const defaults = createDefaultAnalysisViewState();
    const state: AnalysisViewState = {
      ...defaults,
      analysis: {
        detail: {
          view: "LONG", sortBy: "RESULT", sortDirection: "DESC",
          evaluation_filter: { evaluation_type: "PAT", evaluation_results: ["FAIL", "NOT_EVALUATED"], rule_code: "CP_PAT", rule_version: "V2" },
          measurement_filter: { parameter: "VTH", lower_bound: 1.25, upper_bound: 2.5, lower_inclusive: false, upper_inclusive: true },
        },
        overviewRisk: { analyses: ["CAPABILITY", "SBL_GROUPED_LIMIT"], parameter: "VTH", groupBy: "LOT", capability: { method: "CPK_POOLED_WITHIN_LOT_WAFER_V1", ruleCode: "CP_CAP", versionCode: "v2" }, pat: { ruleCode: "CP_PAT", versionCode: "v1" }, spc: { ruleCode: "CP_SPC", versionCode: "v1" }, margin: { ruleCode: "CP_MARGIN", versionCode: "v1" }, sbl: { ruleCode: "FT_SBL", versionCode: "v3", binType: "SOFT_BIN" }, syl: { ruleCode: "FT_SYL", versionCode: "v1" } },
        parameterAnalysis: { ...defaults.analysis.parameterAnalysis, analyses: ["DESCRIPTIVE", "BOX_PLOT", "CAPABILITY"], boxPlot: { ruleCode: "CP_BOX", versionCode: "v1" }, capability: { method: "CPK_POOLED_WITHIN_LOT_WAFER_V1", ruleCode: "CP_CAP", versionCode: "v2" }, boxParameter: "VTH" },
        parameterRelationship: { ...defaults.analysis.parameterRelationship, xParameter: "VTH", yParameters: ["RDON", "BVCES"], analyses: ["SCATTER", "CORRELATION"], groupBy: "WAFER", maxPoints: 5000, correlation: { method: "PEARSON_PAIRWISE_V1", ruleCode: "CP_CORR", versionCode: "v4" }, scatterY: "RDON", scatterDataset: "20:1", correlationScope: "20:V1|LOT-A", displayGroups: ["LOT-A"], pointVisibility: ["OUT_OF_SPEC"] },
        spatial: { mode: "ZONE_COMPARISON", parameter: "VTH", maxPoints: 4500, rule: { ruleCode: "CP_ZONE", versionCode: "v1" }, colorScale: "FULL", symbolSize: 18, showMissing: false },
        quality: { analysis: "SPC_I_MR", parameter: "VTH", groupBy: "RUN", rule: { ruleCode: "FT_SPC", versionCode: "v2" }, spcOrder: "UNIT_SEQUENCE", spcPhase: "PHASE_I_BASELINE", binType: null, spcDisplayGroup: "20:1:RUN-1", distributionDisplayGroup: "", marginDisplayGroup: "", cooccurrenceDisplayGroup: "", sblDisplayBin: "", sylDisplayDataset: "", percentAxisMode: "FIXED_0_100" },
        waferSummary: { sortBy: "YIELD", sortDirection: "DESC" },
      },
    };
    expect(parseAnalysisViewState(serializeAnalysisViewState(state))).toEqual(state);
  });

  it("keeps optional Detail aggregate predicates through URL and rejects expressions or unpaired rules", () => {
    const valid = new URLSearchParams(
      "detail_eval_type=SPEC&detail_eval_result=CONFIG_AMBIGUOUS&detail_eval_rule=SPEC_RULE&detail_eval_version=V7"
      + "&detail_measure_parameter=VTH&detail_measure_lower=-1.5e-3&detail_measure_upper=2.5&detail_measure_lower_inclusive=0",
    );
    expect(parseAnalysisViewState(valid).analysis.detail).toMatchObject({
      evaluation_filter: { evaluation_type: "SPEC", evaluation_results: ["CONFIG_AMBIGUOUS"], rule_code: "SPEC_RULE", rule_version: "V7" },
      measurement_filter: { parameter: "VTH", lower_bound: -0.0015, upper_bound: 2.5, lower_inclusive: false, upper_inclusive: true },
    });

    const invalid = parseAnalysisViewState(new URLSearchParams(
      "detail_eval_type=SCRIPT&detail_eval_result=FAIL&detail_eval_rule=CP_PAT"
      + "&detail_measure_parameter=VTH&detail_measure_lower=1%2B1&detail_measure_upper=0",
    ));
    expect(invalid.analysis.detail.evaluation_filter).toBeNull();
    expect(invalid.analysis.detail.measurement_filter).toBeNull();
    expect(invalid.warnings).toEqual(expect.arrayContaining([
      "ANALYSIS_VIEW_INVALID_DETAIL_EVALUATION_TYPE",
      "ANALYSIS_VIEW_INVALID_DETAIL_EVALUATION_RULE_IDENTITY",
      "ANALYSIS_VIEW_INVALID_DETAIL_MEASUREMENT_LOWER",
    ]));
  });

  it("fails closed with a stable warning for unknown enums, oversized text and bad rules", () => {
    const parsed = parseAnalysisViewState(new URLSearchParams("view_contract=V999&sp_mode=SCRIPT&sp_parameter=" + "X".repeat(201) + "&q_rule=bad-code&rel_max_points=999999"));
    expect(parsed.analysis.spatial.mode).toBe("BIN_MAP");
    expect(parsed.analysis.spatial.parameter).toBe("");
    expect(parsed.analysis.quality.rule.ruleCode).toBe("");
    expect(parsed.analysis.parameterRelationship.maxPoints).toBe(10_000);
    expect(parsed.warnings).toEqual(expect.arrayContaining([
      "ANALYSIS_VIEW_INVALID_CONTRACT", "ANALYSIS_VIEW_INVALID_SP_MODE", "ANALYSIS_VIEW_INVALID_SP_PARAMETER",
      "ANALYSIS_VIEW_INVALID_Q_RULE", "ANALYSIS_VIEW_INVALID_REL_MAX_POINTS",
    ]));
    expect(parseAnalysisViewState(serializeAnalysisViewState(parsed)).warnings).toEqual(parsed.warnings);
  });

  it("preserves an explicit hide-all relationship point selection", () => {
    const defaults = createDefaultAnalysisViewState();
    const state = { ...defaults, analysis: { ...defaults.analysis, parameterRelationship: { ...defaults.analysis.parameterRelationship, pointVisibility: [] } } };
    const params = serializeAnalysisViewState(state);
    expect(params.getAll("rel_point_visibility")).toEqual(["NONE"]);
    expect(parseAnalysisViewState(params).analysis.parameterRelationship.pointVisibility).toEqual([]);
  });
});
