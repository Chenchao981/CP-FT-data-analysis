import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import {
  ANALYTICS_EXPORT_TEMPLATES,
  cancelAnalyticsExport,
  createAnalyticsExport,
  downloadAnalyticsExportArtifact,
  getAnalyticsExport,
  getAnalyticsExportDownloadMetadata,
  listAnalyticsExports,
  type CreateAnalyticsExportRequest,
} from "./analyticsExports";

beforeAll(() => {
  vi.stubGlobal("localStorage", {
    getItem: vi.fn((key: string) => key === "tms_access_token" ? "mock-token" : null),
    setItem: vi.fn(),
    removeItem: vi.fn(),
  });
});

afterEach(() => vi.restoreAllMocks());

describe("Analytics Export API", () => {
  it("mirrors the server template matrix without unsupported Spatial/FT combinations", () => {
    const spatial = ANALYTICS_EXPORT_TEMPLATES.find((item) => item.code === "SPATIAL_ANALYSIS");
    const detail = ANALYTICS_EXPORT_TEMPLATES.find((item) => item.code === "ANALYTICS_DETAIL");
    const wafer = ANALYTICS_EXPORT_TEMPLATES.find((item) => item.code === "WAFER_SUMMARY");
    expect(spatial).toMatchObject({ version: "v1", scopes: ["REPORT"], testStages: ["CP"] });
    expect(spatial?.formats).toEqual(["PNG", "CSV", "XLSX", "HTML", "PDF"]);
    expect(detail).toMatchObject({ scopes: ["CURRENT_PAGE", "FILTERED_RESULT", "FULL_DATASET"], formats: ["CSV", "XLSX", "BIN_TXT"], testStages: ["CP", "FT"] });
    expect(wafer).toMatchObject({ version: "v1", scopes: ["REPORT"], testStages: ["CP"] });
  });

  it("creates, lists, inspects metadata and cancels through the exact endpoints", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async () => new Response("{}", { status: 200 }));
    const request: CreateAnalyticsExportRequest = {
      contract_version: "ANALYTICS_EXPORT_V1",
      datasets: [{ dataset_id: 20, version_no: 3 }],
      filters: { lot_ids: ["LOT-A"], wafer_ids: ["W1"], bin_codes: [], overall_results: ["FAIL"], source_ids: [], tester_ids: ["T-1"], program_versions: [], test_conditions: [] },
      parameters: ["VTH"],
      export_scope: "CURRENT_PAGE",
      export_format: "CSV",
      template_code: "ANALYTICS_DETAIL",
      template_version: "v1",
      rule_context: { spec_versions: ["SPEC:7"], bin_mapping_versions: ["BIN:2"], evaluation_rule_versions: ["RULE:3"] },
      chart_config: { show_spec_overlay: true, correlation_min_abs: 0.5 },
      display_config: { section: "parameter", page: 2, page_size: 50, focus_dataset_id: 20 },
      artifact_ttl_hours: 24,
      idempotency_key: "analytics-export-request-0001",
      page: 2,
      page_size: 50,
      reason: "Export reviewed current page",
    };

    await createAnalyticsExport(request);
    await listAnalyticsExports({ page: 2, page_size: 20 });
    await getAnalyticsExport(81);
    await getAnalyticsExportDownloadMetadata(81);
    await cancelAnalyticsExport(81, { confirmation: "CANCEL", expected_row_version: "00000000000000AF", reason: "Cancel obsolete export" });

    expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/analytics/exports");
    expect(fetchMock.mock.calls[0][1]?.method).toBe("POST");
    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toEqual(request);
    expect(fetchMock.mock.calls[1][0]).toBe("/api/v1/analytics/exports?page=2&page_size=20");
    expect(fetchMock.mock.calls[2][0]).toBe("/api/v1/analytics/exports/81");
    expect(fetchMock.mock.calls[3][0]).toBe("/api/v1/analytics/exports/81/download-metadata");
    expect(fetchMock.mock.calls[4][0]).toBe("/api/v1/analytics/exports/81/cancel");
    expect(JSON.parse(String(fetchMock.mock.calls[4][1]?.body))).toEqual({ confirmation: "CANCEL", expected_row_version: "00000000000000AF", reason: "Cancel obsolete export" });
    expect(new Headers(fetchMock.mock.calls[0][1]?.headers).get("Authorization")).toBe("Bearer mock-token");
  });

  it("downloads an artifact from the fixed authenticated route", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(new Blob(["csv"]), { status: 200 }));
    URL.createObjectURL = vi.fn(() => "blob:analytics-export");
    URL.revokeObjectURL = vi.fn();
    const click = vi.fn();
    vi.stubGlobal("document", { createElement: vi.fn(() => ({ click, href: "", download: "" } as unknown as HTMLAnchorElement)) });

    await downloadAnalyticsExportArtifact(81, 501, "analytics.csv");

    expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/analytics/exports/81/artifacts/501/download");
    expect(new Headers(fetchMock.mock.calls[0][1]?.headers).get("Authorization")).toBe("Bearer mock-token");
    expect(click).toHaveBeenCalledOnce();
  });
});
