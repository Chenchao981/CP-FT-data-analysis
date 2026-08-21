import { afterEach, describe, expect, it, vi } from "vitest";

import { getDatasetChartData, getDatasetGate, publishDatasetVersion } from "./datasets";

afterEach(() => vi.restoreAllMocks());

describe("datasets api", () => {
  it("loads the DQ gate for an exact dataset version", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ status: "PASS" }), { status: 200 }),
    );
    await getDatasetGate(12, 3);
    expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/datasets/12/versions/3/gate");
  });

  it("publishes with an explicit application user identity", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ status: "PUBLISHED" }), { status: 200 }),
    );
    await publishDatasetVersion(12, 3, 9);
    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toEqual({ published_by: 9 });
  });

  it("encodes exact Lot and Wafer chart filters", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ wafer_yield: [] }), { status: 200 }),
    );
    await getDatasetChartData(12, 3, "FA53-5465", "001");
    expect(fetchMock.mock.calls[0][0]).toBe(
      "/api/v1/datasets/12/versions/3/charts?lot_id=FA53-5465&wafer_id=001",
    );
  });
});
