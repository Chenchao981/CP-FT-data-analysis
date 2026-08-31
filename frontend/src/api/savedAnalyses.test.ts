import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import type { AnalyticsContextRequest } from "./analytics";
import {
  createSavedAnalysis,
  createSavedAnalysisRevision,
  deleteSavedAnalysis,
  getSavedAnalysis,
  listSavedAnalyses,
  type CreateSavedAnalysisRequest,
} from "./savedAnalyses";

beforeAll(() => {
  vi.stubGlobal("localStorage", {
    getItem: vi.fn((key: string) => key === "tms_access_token" ? "mock-token" : null),
    setItem: vi.fn(),
    removeItem: vi.fn(),
  });
});

afterEach(() => vi.restoreAllMocks());

const context: AnalyticsContextRequest = {
  datasets: [{ dataset_id: 20, version_no: 3 }],
  filters: {
    lot_ids: ["LOT-A"], wafer_ids: ["W1"], bin_codes: ["1"], overall_results: ["FAIL"],
    source_ids: ["SRC-1"], tester_ids: ["T-1"], program_versions: ["P-1"], test_conditions: ["C-1"],
  },
  parameters: ["VTH"],
};

describe("Saved Analysis API", () => {
  it("uses the frozen state contract for create, revision and delete", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async () => new Response("{}", { status: 200 }));
    const state = {
      ...context,
      contract_version: "SAVED_ANALYSIS_V1" as const,
      rule_context: { spec_versions: ["SPEC:7"], bin_mapping_versions: ["BIN:2"], evaluation_rule_versions: ["RULE:3"] },
      chart_config: {},
      display_config: { section: "spatial", page: 2, page_size: 50 },
    };
    const createRequest: CreateSavedAnalysisRequest = { ...state, analysis_name: "VTH fail map", change_reason: "Create approved analysis" };

    await createSavedAnalysis(createRequest);
    await createSavedAnalysisRevision(41, { ...state, expected_row_version: "00000000000000AF", analysis_name: "VTH fail map v2", change_reason: "Refresh current Context" });
    await deleteSavedAnalysis(41, { expected_row_version: "00000000000000B0", reason: "Remove obsolete analysis" });

    expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/analytics/saved-analyses");
    expect(fetchMock.mock.calls[0][1]?.method).toBe("POST");
    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toEqual(createRequest);
    expect(fetchMock.mock.calls[1][0]).toBe("/api/v1/analytics/saved-analyses/41/revisions");
    expect(fetchMock.mock.calls[1][1]?.method).toBe("POST");
    expect(JSON.parse(String(fetchMock.mock.calls[1][1]?.body))).toMatchObject({ expected_row_version: "00000000000000AF", change_reason: "Refresh current Context" });
    expect(fetchMock.mock.calls[2][0]).toBe("/api/v1/analytics/saved-analyses/41");
    expect(fetchMock.mock.calls[2][1]?.method).toBe("DELETE");
    expect(JSON.parse(String(fetchMock.mock.calls[2][1]?.body))).toEqual({ expected_row_version: "00000000000000B0", reason: "Remove obsolete analysis" });
    expect(new Headers(fetchMock.mock.calls[0][1]?.headers).get("Authorization")).toBe("Bearer mock-token");
  });

  it("lists and retrieves a precise historical revision", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async () => new Response("{}", { status: 200 }));

    await listSavedAnalyses({ page: 3, page_size: 50, include_deleted: true });
    await getSavedAnalysis(41, 2);

    expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/analytics/saved-analyses?page=3&page_size=50&include_deleted=true");
    expect(fetchMock.mock.calls[1][0]).toBe("/api/v1/analytics/saved-analyses/41?revision_no=2");
  });
});
