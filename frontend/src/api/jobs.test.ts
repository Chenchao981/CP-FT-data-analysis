import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { getJobDetails } from "./jobs";

beforeAll(() => {
  vi.stubGlobal("localStorage", { getItem: vi.fn(() => "mock-token"), setItem: vi.fn(), removeItem: vi.fn() });
});

afterEach(() => vi.restoreAllMocks());

describe("jobs api", () => {
  it("loads the safe nested Job details endpoint", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ job: { job_id: 91 }, children: [], timeline: [], actions: [] }), { status: 200 }));

    await getJobDetails(91);

    expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/jobs/91/details");
    expect((fetchMock.mock.calls[0][1]?.headers as Headers).get("Authorization")).toBe("Bearer mock-token");
  });
});
