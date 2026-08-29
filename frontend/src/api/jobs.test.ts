import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { createJob, getJobDetails, transitionJob } from "./jobs";

beforeAll(() => {
  vi.stubGlobal("localStorage", { getItem: vi.fn(() => "mock-token"), setItem: vi.fn(), removeItem: vi.fn() });
});

afterEach(() => vi.restoreAllMocks());

describe("jobs api", () => {
  it("creates a manual parse job using the backend contract", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ job_id: 1 }), { status: 201 }));
    await createJob({ source_file_id: 8, cleaner_release_id: 3, requested_by: "tester" });
    const [, init] = fetchMock.mock.calls[0];
    expect(JSON.parse(String(init?.body))).toMatchObject({ job_type: "PARSE", trigger_type: "MANUAL", source_file_id: 8 });
    expect((init?.headers as Headers).get("Authorization")).toBe("Bearer mock-token");
  });

  it("sends a valid transition payload", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ job_id: 1 }), { status: 200 }));
    await transitionJob(1, "RUNNING");
    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toEqual({ target_status: "RUNNING" });
  });

  it("loads the safe nested Job details endpoint", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ job: { job_id: 91 }, children: [], timeline: [], actions: [] }), { status: 200 }));

    await getJobDetails(91);

    expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/jobs/91/details");
    expect((fetchMock.mock.calls[0][1]?.headers as Headers).get("Authorization")).toBe("Bearer mock-token");
  });
});
