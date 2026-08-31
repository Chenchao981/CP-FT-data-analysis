import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { analyzeParameterRelationship, type ParameterRelationshipRequest } from "./parameterRelationship";

beforeAll(() => {
  vi.stubGlobal("localStorage", {
    getItem: vi.fn((key: string) => key === "tms_access_token" ? "mock-token" : null),
    setItem: vi.fn(),
    removeItem: vi.fn(),
  });
});

afterEach(() => vi.restoreAllMocks());

describe("parameter relationship API", () => {
  it("posts exact X/Y identities, all filters, grouping, sampling bound and correlation rule", async () => {
    const request: ParameterRelationshipRequest = {
      datasets: [{ dataset_id: 20, version_no: 1 }, { dataset_id: 21, version_no: 2 }],
      filters: {
        lot_ids: ["LOT-A"],
        wafer_ids: ["W1"],
        bin_codes: ["1"],
        overall_results: ["PASS", "FAIL"],
        source_ids: ["SRC-1"],
        tester_ids: ["T-1"],
        program_versions: ["P-1"],
        test_conditions: ["C-1"],
      },
      x_parameter: "VTH",
      y_parameters: ["RDON", "IDSS"],
      analyses: ["SCATTER", "TREND", "CORRELATION"],
      group_by: "TESTER",
      max_points: 5_000,
      correlation: {
        method: "PEARSON_PAIRWISE_V1",
        rule_code: "CORRELATION_RULE",
        version_code: "v1",
      },
    };
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ contract_version: "PARAMETER_RELATIONSHIP_V1" }), { status: 200 }));

    await analyzeParameterRelationship(request);

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/v1/analytics/parameter-relationship");
    expect(init?.method).toBe("POST");
    expect(JSON.parse(String(init?.body))).toEqual(request);
    expect(new Headers(init?.headers).get("Authorization")).toBe("Bearer mock-token");
  });
});
