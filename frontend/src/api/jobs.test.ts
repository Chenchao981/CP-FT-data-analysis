import { afterEach, describe, expect, it, vi } from "vitest";

import { createJob, transitionJob } from "./jobs";

afterEach(() => vi.restoreAllMocks());

describe("jobs api", () => {
  it("creates a manual parse job using the backend contract", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ job_id: 1 }), { status: 201 }));
    await createJob({ source_file_id: 8, cleaner_release_id: 3, requested_by: "tester" });
    const [, init] = fetchMock.mock.calls[0];
    expect(JSON.parse(String(init?.body))).toMatchObject({ job_type: "PARSE", trigger_type: "MANUAL", source_file_id: 8 });
  });

  it("sends a valid transition payload", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ job_id: 1 }), { status: 200 }));
    await transitionJob(1, "RUNNING");
    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toEqual({ target_status: "RUNNING" });
  });
});
