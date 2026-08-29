// @vitest-environment jsdom

import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { approveProductCrosswalk, listProductCrosswalks, rejectProductCrosswalk } from "./masterData";

beforeAll(() => {
  vi.stubGlobal("localStorage", {
    getItem: vi.fn((key: string) => key === "tms_access_token" ? "govern-token" : null),
    setItem: vi.fn(),
    removeItem: vi.fn(),
  });
});

afterEach(() => vi.restoreAllMocks());

describe("master data api", () => {
  it("uses server paging and filters through the authenticated client", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ items: [], total: 0, page: 2, page_size: 50 }), { status: 200 }),
    );

    await listProductCrosswalks({ page: 2, page_size: 50, status: "PENDING", supplier_code: "RYX", test_stage: "FT", raw_product_code: "NCE 80V" });

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/v1/master-data/product-crosswalks?page=2&page_size=50&status=PENDING&supplier_code=RYX&test_stage=FT&raw_product_code=NCE+80V");
    expect((init?.headers as Headers).get("Authorization")).toBe("Bearer govern-token");
  });

  it("sends an explicit SAP_B1 approval and complete reason", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ crosswalk_id: 7, status: "APPROVED" }), { status: 200 }),
    );

    await approveProductCrosswalk(7, { enterprise_system: "SAP_B1", enterprise_key: "NCE-MAT-001", reason: "SAP 主数据 Owner 已核准" });

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/v1/master-data/product-crosswalks/7/approve");
    expect(init?.method).toBe("POST");
    expect(JSON.parse(String(init?.body))).toEqual({ enterprise_system: "SAP_B1", enterprise_key: "NCE-MAT-001", reason: "SAP 主数据 Owner 已核准" });
    expect((init?.headers as Headers).get("Authorization")).toBe("Bearer govern-token");
  });

  it("sends the rejection reason without inventing an enterprise mapping", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ crosswalk_id: 8, status: "REJECTED" }), { status: 200 }),
    );

    await rejectProductCrosswalk(8, "来源标识无法唯一对应 SAP 物料");

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/v1/master-data/product-crosswalks/8/reject");
    expect(init?.method).toBe("POST");
    expect(JSON.parse(String(init?.body))).toEqual({ reason: "来源标识无法唯一对应 SAP 物料" });
  });
});
