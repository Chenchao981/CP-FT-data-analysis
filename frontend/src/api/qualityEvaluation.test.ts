import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { evaluateQuality, type QualityEvaluationRequest } from "./qualityEvaluation";

beforeAll(() => {
  vi.stubGlobal("localStorage", {
    getItem: vi.fn((key: string) => key === "tms_access_token" ? "mock-token" : null),
    setItem: vi.fn(),
    removeItem: vi.fn(),
  });
});
afterEach(() => vi.restoreAllMocks());

describe("Quality Evaluation API", () => {
  it("posts exact Context, approved-rule reference, group, order and phase", async () => {
    const request: QualityEvaluationRequest = {
      datasets: [{ dataset_id: 20, version_no: 1 }],
      filters: { lot_ids: ["LOT-A"], wafer_ids: ["W1"], bin_codes: [], overall_results: ["FAIL"], source_ids: ["S1"], tester_ids: ["T1"], program_versions: ["P1"], test_conditions: ["C1"] },
      parameters: ["VTH"],
      analysis: "SPC_I_MR",
      rule: { rule_code: "CP_SPC_VTH", version_code: "1.0.0" },
      group_by: "WAFER",
      spc_order: "UNIT_SEQUENCE",
      spc_phase: "PHASE_I_BASELINE",
      bin_type: null,
    };
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ contract_version: "ANALYTICS_QUALITY_EVALUATION_V1" }), { status: 200 }));

    await evaluateQuality(request);

    expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/analytics/quality-evaluation");
    expect(fetchMock.mock.calls[0][1]?.method).toBe("POST");
    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toEqual(request);
    expect(new Headers(fetchMock.mock.calls[0][1]?.headers).get("Authorization")).toBe("Bearer mock-token");
  });
});
