import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import {
  evaluateAnalyticsInstantRisk,
  getAnalyticsDetail,
  getAnalyticsDrilldown,
  getAnalyticsFeatureFlags,
  getAnalyticsOverview,
  getAnalyticsShellContext,
  type AnalyticsContextRequest,
} from "./analytics";

beforeAll(() => {
  vi.stubGlobal("localStorage", {
    getItem: vi.fn((key: string) => key === "tms_access_token" ? "mock-token" : null),
    setItem: vi.fn(),
    removeItem: vi.fn(),
  });
});

afterEach(() => vi.restoreAllMocks());

const context: AnalyticsContextRequest = {
  datasets: [{ dataset_id: 20, version_no: 1 }, { dataset_id: 21, version_no: 2 }],
  filters: {
    lot_ids: ["LOT-A", "LOT-B"],
    wafer_ids: ["W1", "W2"],
    bin_codes: ["1", "5"],
    overall_results: ["PASS", "UNKNOWN"],
    source_ids: ["SRC-A", "SRC-B"],
    tester_ids: ["TESTER-A"],
    program_versions: ["PROGRAM-1"],
    test_conditions: ["VGE=0V"],
  },
  parameters: ["BVCES", "VTH"],
};

const success = () => new Response(JSON.stringify({ contract_version: "ANALYTICS_CONTEXT_V1" }), { status: 200 });

describe("analytics API", () => {
  it("loads backend-enforced analytics feature flags", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ contract_version: "ANALYTICS_FEATURE_FLAGS_V1", groups: [] }), { status: 200 }));
    await getAnalyticsFeatureFlags();
    expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/analytics/features");
  });

  it("posts the complete multi-value Context to overview", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(success());
    await getAnalyticsOverview({ ...context, focus_dataset_id: 21, max_points: 10_000 });

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/v1/analytics/overview");
    expect(init?.method).toBe("POST");
    expect(JSON.parse(String(init?.body))).toEqual({ ...context, focus_dataset_id: 21, max_points: 10_000 });
    expect(new Headers(init?.headers).get("Authorization")).toBe("Bearer mock-token");
  });

  it("loads the shared shell Context independently from the Overview feature", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(success());
    await getAnalyticsShellContext({ ...context, focus_dataset_id: 21, max_points: 100 });

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/v1/analytics/context");
    expect(JSON.parse(String(init?.body))).toEqual({ ...context, focus_dataset_id: 21, max_points: 100 });
  });

  it("posts only explicitly selected exact-rule instant risk evaluations", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(success());
    const evaluations = [{
      analysis: "SPC_I_MR" as const,
      parameter: "VTH",
      group_by: "LOT" as const,
      rule: { rule_code: "SPC_RULE", version_code: "v2" },
      spc_order: "UNIT_SEQUENCE" as const,
      spc_phase: "PHASE_I_BASELINE" as const,
    }];

    await evaluateAnalyticsInstantRisk({ ...context, evaluations });

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/v1/analytics/instant-risk");
    expect(JSON.parse(String(init?.body))).toEqual({ ...context, evaluations });
  });

  it("posts server pagination and view with the same Context to detail", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(success());
    await getAnalyticsDetail({ ...context, focus_dataset_id: 20, page: 3, page_size: 100, view: "LONG", sort_by: "LOT", sort_direction: "DESC" });

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/v1/analytics/detail");
    expect(JSON.parse(String(init?.body))).toEqual({ ...context, focus_dataset_id: 20, page: 3, page_size: 100, view: "LONG", sort_by: "LOT", sort_direction: "DESC" });
  });

  it("posts only the backend-issued drilldown key plus the same Context", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(success());
    await getAnalyticsDrilldown({ ...context, drilldown_key: "UNIT:501" });

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/v1/analytics/drilldown");
    expect(JSON.parse(String(init?.body))).toEqual({ ...context, drilldown_key: "UNIT:501" });
    expect(String(init?.body)).not.toContain("dataIndex");
  });
});
