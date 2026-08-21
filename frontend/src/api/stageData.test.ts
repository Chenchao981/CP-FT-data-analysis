import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { downloadStageUploadFile, listStageResults, listStageUploads, reprocessStageBatch, uploadStageData } from "./stageData";

beforeAll(() => {
  vi.stubGlobal("localStorage", { getItem: vi.fn((key: string) => key === "tms_access_token" ? "mock-token" : null), setItem: vi.fn(), removeItem: vi.fn() });
});

afterEach(() => vi.restoreAllMocks());

describe("stage data api", () => {
  it("builds engineering CP endpoints from business domain and test stage", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async () => new Response(JSON.stringify([]), { status: 200 }));
    await listStageUploads("ENGINEERING", "CP");
    await listStageResults("ENGINEERING", "CP");
    expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/engineering/cp/uploads");
    expect(fetchMock.mock.calls[1][0]).toBe("/api/v1/engineering/cp/results");
  });

  it("uploads production CP files with factory metadata", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ import_batch_id: 9, status: "PROCESSED" }), { status: 201 }),
    );
    const file = new File(["sample"], "sample.zip");
    await uploadStageData("PRODUCTION", "CP", [file], "huahong", "备注");
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/v1/production/cp/uploads");
    expect(init?.method).toBe("POST");
    const body = init?.body as FormData;
    expect(body.get("factory_code")).toBe("huahong");
    expect(body.get("remark")).toBe("备注");
    expect(body.getAll("files").length).toBe(1);
  });

  it("posts reprocess to the batch endpoint", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ import_batch_id: 10, status: "PROCESSED" }), { status: 200 }),
    );
    await reprocessStageBatch("ENGINEERING", "CP", 10);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/v1/engineering/cp/uploads/10/reprocess");
    expect(init?.method).toBe("POST");
  });

  it("downloads source files with bearer authorization", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(new Blob(["zip-bytes"]), { status: 200 }),
    );
    URL.createObjectURL = vi.fn(() => "blob:mock");
    URL.revokeObjectURL = vi.fn();
    const click = vi.fn();
    vi.stubGlobal("document", { createElement: vi.fn(() => ({ click, href: "", download: "" } as unknown as HTMLAnchorElement)) });
    await downloadStageUploadFile("PRODUCTION", "FT", 13, 9001, "source.xlsx");
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/v1/production/ft/uploads/13/files/9001/download");
    expect((init?.headers as Headers).get("Authorization")).toContain("Bearer mock-token");
    expect(click).toHaveBeenCalled();
  });
});
