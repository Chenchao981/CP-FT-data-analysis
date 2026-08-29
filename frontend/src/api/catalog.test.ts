import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { listCurrentDatasets } from "./catalog";

beforeAll(() => {
  vi.stubGlobal("localStorage", { getItem: vi.fn(() => "mock-token"), setItem: vi.fn(), removeItem: vi.fn() });
});

afterEach(() => vi.restoreAllMocks());

describe("dataset current catalog api", () => {
  it("encodes server pagination and business filters", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ items: [], total: 0, page: 3, page_size: 20 }), { status: 200 }),
    );

    await listCurrentDatasets({
      page: 3,
      page_size: 20,
      product_name: "NCE Power",
      lot_id: "LOT/202608",
      factory_code: "riyuexin",
      business_domain: "PRODUCTION",
      test_stage: "FT",
      status: "PUBLISHED",
      from_utc: "2026-08-01T00:00:00Z",
      to_utc: "2026-08-31T23:59:59Z",
    });

    expect(fetchMock.mock.calls[0][0]).toBe(
      "/api/v1/catalog/datasets/current?page=3&page_size=20&product_name=NCE+Power&lot_id=LOT%2F202608&factory_code=riyuexin&business_domain=PRODUCTION&test_stage=FT&status=PUBLISHED&from_utc=2026-08-01T00%3A00%3A00Z&to_utc=2026-08-31T23%3A59%3A59Z",
    );
    expect((fetchMock.mock.calls[0][1]?.headers as Headers).get("Authorization")).toBe("Bearer mock-token");
  });
});
