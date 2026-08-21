import { afterEach, describe, expect, it, vi } from "vitest";

import { createFieldEnrichment, getBatchEnrichments, getEnrichmentFields } from "./enrichments";

afterEach(() => vi.restoreAllMocks());

describe("enrichments api", () => {
  it("loads the stage-specific field contract", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify([]), { status: 200 }),
    );
    await getEnrichmentFields("CP");
    expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/enrichments/fields/CP");
  });

  it("loads current decisions for one import batch", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify([]), { status: 200 }),
    );
    await getBatchEnrichments(17);
    expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/enrichments/batches/17");
  });

  it("posts an explicit FT ignore decision", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ enrichment_id: 1 }), { status: 201 }),
    );
    await createFieldEnrichment({
      import_batch_id: 17,
      test_stage: "FT",
      field_code: "LOT_ID",
      action: "IGNORE",
      reason: "FT源文件没有Lot",
    });
    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toEqual({
      import_batch_id: 17,
      test_stage: "FT",
      field_code: "LOT_ID",
      action: "IGNORE",
      reason: "FT源文件没有Lot",
    });
  });
});
