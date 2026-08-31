import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { getWaferSummary, type WaferSummaryRequest } from "./waferSummary";

beforeAll(() => {
  vi.stubGlobal("localStorage", {
    getItem: vi.fn((key: string) => key === "tms_access_token" ? "mock-token" : null),
    setItem: vi.fn(),
    removeItem: vi.fn(),
  });
});

afterEach(() => vi.restoreAllMocks());

describe("wafer summary API", () => {
  it("posts server pagination, sorting, dynamic parameters and all Context filters", async () => {
    const request: WaferSummaryRequest = {
      datasets: [{ dataset_id: 20, version_no: 1 }],
      filters: { lot_ids: ["LOT-A"], wafer_ids: ["W1"], bin_codes: ["1"], overall_results: ["PASS", "UNKNOWN"], source_ids: ["SRC-1"], tester_ids: ["T-1"], program_versions: ["P-1"], test_conditions: ["C-1"] },
      parameters: ["RDON", "VTH"],
      page: 3,
      page_size: 100,
      sort_by: "YIELD",
      sort_direction: "DESC",
    };
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ contract_version: "WAFER_SUMMARY_V1" }), { status: 200 }));

    await getWaferSummary(request);

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/v1/analytics/wafer-summary");
    expect(init?.method).toBe("POST");
    expect(JSON.parse(String(init?.body))).toEqual(request);
    expect(new Headers(init?.headers).get("Authorization")).toBe("Bearer mock-token");
  });
});
