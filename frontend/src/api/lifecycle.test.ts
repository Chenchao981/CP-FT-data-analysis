// @vitest-environment jsdom

import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import {
  archiveDataset,
  createDatasetReprocess,
  createLatestExport,
  downloadLatestExportArtifact,
  getLatestExportStatus,
} from "./lifecycle";

beforeAll(() => {
  vi.stubGlobal("localStorage", {
    getItem: vi.fn((key: string) => key === "tms_access_token" ? "lifecycle-token" : null),
    setItem: vi.fn(),
    removeItem: vi.fn(),
  });
});

afterEach(() => vi.restoreAllMocks());

describe("lifecycle api", () => {
  it("creates a non-mutating latest export with an explicit idempotency key", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ job_id: 81, action_type: "EXPORT_LATEST" }), { status: 202 }),
    );

    await createLatestExport(20, "export-20-request-0001");

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/v1/lifecycle/exports");
    expect(init?.method).toBe("POST");
    expect(JSON.parse(String(init?.body))).toEqual({ dataset_id: 20, idempotency_key: "export-20-request-0001" });
    expect((init?.headers as Headers).get("Authorization")).toBe("Bearer lifecycle-token");
  });

  it("sends the backend-required typed confirmations for reprocess and logical archive", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
      new Response(JSON.stringify({ job_id: 82 }), { status: 202 }),
    );

    await createDatasetReprocess(20, "Cleaner 发布后显式重处理", "reprocess-20-request-0001");
    await archiveDataset(20, "重复导入，已完成 Owner 核准", "archive-20-request-0001");

    expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/lifecycle/datasets/20/reprocess");
    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toEqual({
      confirmation: "REPROCESS",
      reason: "Cleaner 发布后显式重处理",
      idempotency_key: "reprocess-20-request-0001",
    });
    expect(fetchMock.mock.calls[1][0]).toBe("/api/v1/lifecycle/datasets/20/archive");
    expect(JSON.parse(String(fetchMock.mock.calls[1][1]?.body))).toEqual({
      confirmation: "ARCHIVE",
      reason: "重复导入，已完成 Owner 核准",
      idempotency_key: "archive-20-request-0001",
    });
  });

  it("loads export status through the authenticated client", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ job_id: 81, availability: "READY", artifacts: [] }), { status: 200 }),
    );

    await getLatestExportStatus(81);

    expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/lifecycle/exports/81");
    expect((fetchMock.mock.calls[0][1]?.headers as Headers).get("Authorization")).toBe("Bearer lifecycle-token");
  });

  it("downloads an Artifact from the fixed lifecycle route with bearer authorization", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(new Blob(["xlsx"]), { status: 200 }),
    );
    URL.createObjectURL = vi.fn(() => "blob:lifecycle");
    URL.revokeObjectURL = vi.fn();
    const click = vi.fn();
    vi.stubGlobal("document", { createElement: vi.fn(() => ({ click, href: "", download: "" } as unknown as HTMLAnchorElement)) });

    await downloadLatestExportArtifact(81, 3, "latest.xlsx");

    expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/lifecycle/exports/81/artifacts/3/download");
    expect((fetchMock.mock.calls[0][1]?.headers as Headers).get("Authorization")).toBe("Bearer lifecycle-token");
    expect(click).toHaveBeenCalledTimes(1);
  });
});
