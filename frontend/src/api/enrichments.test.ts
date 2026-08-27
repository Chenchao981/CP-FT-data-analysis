import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { createFieldEnrichment, getBatchEnrichments, getEnrichmentFields } from "./enrichments";

beforeAll(() => {
  vi.stubGlobal("localStorage", { getItem: vi.fn((key: string) => key === "tms_access_token" ? "mock-token" : null), setItem: vi.fn(), removeItem: vi.fn() });
});

afterEach(() => vi.restoreAllMocks());

describe("enrichments api", () => {
  it("loads the stage-specific field contract", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify([]), { status: 200 }),
    );
    await getEnrichmentFields("CP");
    expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/enrichments/fields/CP");
    expect((fetchMock.mock.calls[0][1]?.headers as Headers).get("Authorization")).toBe("Bearer mock-token");
  });

  it("loads current decisions for one import batch", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify([]), { status: 200 }),
    );
    await getBatchEnrichments(17);
    expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/enrichments/batches/17");
  });

  it("posts an explicit optional-field ignore decision", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ enrichment_id: 1 }), { status: 201 }),
    );
    await createFieldEnrichment({
      import_batch_id: 17,
      test_stage: "FT",
      field_code: "PROJECT_CODE",
      action: "IGNORE",
      reason: "本次分析不使用项目代码",
    });
    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toEqual({
      import_batch_id: 17,
      test_stage: "FT",
      field_code: "PROJECT_CODE",
      action: "IGNORE",
      reason: "本次分析不使用项目代码",
    });
  });

  it("posts an explicit Lot fill decision", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ enrichment_id: 2 }), { status: 201 }),
    );
    await createFieldEnrichment({
      import_batch_id: 18,
      test_stage: "FT",
      field_code: "LOT_ID",
      action: "FILL",
      value_text: "FA59-3997",
      reason: "根据生产记录确认",
    });
    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toMatchObject({
      field_code: "LOT_ID",
      action: "FILL",
      value_text: "FA59-3997",
    });
  });
});
