import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { inspectHuaHongFile } from "./cleaners";

beforeAll(() => {
  vi.stubGlobal("localStorage", { getItem: vi.fn(() => "mock-token"), setItem: vi.fn(), removeItem: vi.fn() });
});

afterEach(() => vi.restoreAllMocks());

describe("HuaHong cleaner api", () => {
  it("uploads the selected file as multipart data", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ quality: { status: "PASS" } }), { status: 200 }),
    );
    const file = new File(["content"], "wafer.TXT", { type: "text/plain" });
    await inspectHuaHongFile(file);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/v1/cleaners/huahong/inspect");
    expect(init?.body).toBeInstanceOf(FormData);
    expect((init?.body as FormData).get("file")).toBe(file);
    expect((init?.headers as Headers).get("Authorization")).toBe("Bearer mock-token");
  });
});
