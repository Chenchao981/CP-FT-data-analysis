import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { analyzeSpatial, type SpatialAnalysisRequest } from "./spatialAnalysis";

beforeAll(() => {
  vi.stubGlobal("localStorage", {
    getItem: vi.fn((key: string) => key === "tms_access_token" ? "mock-token" : null),
    setItem: vi.fn(),
    removeItem: vi.fn(),
  });
});

afterEach(() => vi.restoreAllMocks());

describe("spatial analysis API", () => {
  it("posts the exact unified Context, spatial mode, point bound and gated rule reference", async () => {
    const request: SpatialAnalysisRequest = {
      datasets: [{ dataset_id: 20, version_no: 1 }],
      filters: { lot_ids: ["LOT-A"], wafer_ids: ["W1"], bin_codes: ["1"], overall_results: ["FAIL"], source_ids: ["SRC-1"], tester_ids: ["T-1"], program_versions: ["P-1"], test_conditions: ["C-1"] },
      parameters: ["VTH"],
      mode: "ZONE_COMPARISON",
      focus_dataset_id: 20,
      max_points: 20_000,
      rule_code: "CP_WAFER_ZONE",
      rule_version: "1.0.0",
    };
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ contract_version: "ANALYTICS_SPATIAL_V1" }), { status: 200 }));

    await analyzeSpatial(request);

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/v1/analytics/spatial");
    expect(init?.method).toBe("POST");
    expect(JSON.parse(String(init?.body))).toEqual(request);
    expect(new Headers(init?.headers).get("Authorization")).toBe("Bearer mock-token");
  });
});
