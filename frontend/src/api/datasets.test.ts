import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import {
  compareDatasets,
  getDatasetChartData,
  getDatasetDetails,
} from "./datasets";

beforeAll(() => {
  vi.stubGlobal("localStorage", {
    getItem: vi.fn((key: string) => key === "tms_access_token" ? "mock-token" : null),
    setItem: vi.fn(),
    removeItem: vi.fn(),
  });
});

afterEach(() => vi.restoreAllMocks());

describe("datasets api", () => {
  it("encodes exact Lot and Wafer chart filters", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ wafer_yield: [] }), { status: 200 }),
    );
    await getDatasetChartData(12, 3, "FA53-5465", "001", "Source-1", "VTH");
    expect(fetchMock.mock.calls[0][0]).toBe(
      "/api/v1/datasets/12/versions/3/charts?lot_id=FA53-5465&wafer_id=001&source_id=Source-1&parameter=VTH",
    );
  });

  it("posts bounded multi-Dataset comparison filters", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ items: [] }), { status: 200 }),
    );

    await compareDatasets({
      datasets: [{ dataset_id: 12, version_no: 3 }, { dataset_id: 14, version_no: 1 }],
      lot_ids: ["LOT-A", "LOT-B"],
      wafer_ids: ["01"],
      bin_codes: ["1", "5"],
      parameters: ["VTH", "RDSON"],
    });

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/v1/datasets/compare");
    expect(init?.method).toBe("POST");
    expect(JSON.parse(String(init?.body))).toEqual({
      datasets: [{ dataset_id: 12, version_no: 3 }, { dataset_id: 14, version_no: 1 }],
      lot_ids: ["LOT-A", "LOT-B"],
      wafer_ids: ["01"],
      bin_codes: ["1", "5"],
      parameters: ["VTH", "RDSON"],
    });
  });

  it("encodes repeated filters for a server-paged detail query", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ items: [], total: 0 }), { status: 200 }),
    );

    await getDatasetDetails(12, 3, {
      page: 2,
      page_size: 100,
      lot_ids: ["LOT A", "LOT-B"],
      wafer_ids: ["01"],
      bin_codes: ["BIN 1"],
      parameters: ["VTH", "RDSON"],
    });

    expect(fetchMock.mock.calls[0][0]).toBe(
      "/api/v1/datasets/12/versions/3/details?page=2&page_size=100&lot_id=LOT+A&lot_id=LOT-B&wafer_id=01&bin_code=BIN+1&parameter=VTH&parameter=RDSON",
    );
  });
});
