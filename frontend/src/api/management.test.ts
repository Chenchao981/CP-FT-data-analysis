// @vitest-environment jsdom

import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { getQualityManagementSummary } from "./management";

beforeAll(() => {
  vi.stubGlobal("localStorage", {
    getItem: vi.fn((key: string) => key === "tms_access_token" ? "management-token" : null),
    setItem: vi.fn(),
    removeItem: vi.fn(),
  });
});

afterEach(() => vi.restoreAllMocks());

describe("management api", () => {
  it("loads the filtered quality summary through the authenticated client", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ methodology: {}, kpis: {} }), { status: 200 }),
    );

    await getQualityManagementSummary({
      access_scope: "DOMAIN",
      data_domain_id: 42,
      from_utc: "2026-08-01T00:00:00Z",
      to_utc: "2026-09-01T00:00:00Z",
      business_domain: "PRODUCTION",
      test_stage: "FT",
      factory_code: "riyuexin",
      product_name: "NCE MOS",
      lot_id: "LOT/01",
      recent_limit: 15,
    });

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/v1/management/quality-summary?access_scope=DOMAIN&data_domain_id=42&from_utc=2026-08-01T00%3A00%3A00Z&to_utc=2026-09-01T00%3A00%3A00Z&business_domain=PRODUCTION&test_stage=FT&factory_code=riyuexin&product_name=NCE+MOS&lot_id=LOT%2F01&recent_limit=15");
    expect((init?.headers as Headers).get("Authorization")).toBe("Bearer management-token");
  });
});
