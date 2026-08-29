// @vitest-environment jsdom

import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { drainWorker, getOperationsConsistency, getWorkerFleetHealth, resumeWorker } from "./operations";

beforeAll(() => {
  vi.stubGlobal("localStorage", {
    getItem: vi.fn((key: string) => key === "tms_access_token" ? "audit-token" : null),
    setItem: vi.fn(),
    removeItem: vi.fn(),
  });
});

afterEach(() => vi.restoreAllMocks());

describe("operations api", () => {
  it("loads the permission-protected consistency summary with a bounded failure limit", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ overall_state: "HEALTHY" }), { status: 200 }),
    );

    await getOperationsConsistency(8);

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/v1/operations/consistency?recent_failure_limit=8");
    expect((init?.headers as Headers).get("Authorization")).toBe("Bearer audit-token");
  });

  it("loads Worker heartbeats with an explicit stale threshold", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ workers: [] }), { status: 200 }),
    );

    await getWorkerFleetHealth(120);

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/v1/operations/workers?stale_after_seconds=120");
    expect((init?.headers as Headers).get("Authorization")).toBe("Bearer audit-token");
  });

  it("encodes Worker ids for drain and resume controls", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
      new Response(JSON.stringify({ worker_id: "worker/site 1" }), { status: 200 }),
    );

    await drainWorker("worker/site 1");
    await resumeWorker("worker/site 1");

    expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/operations/workers/worker%2Fsite%201/drain");
    expect(fetchMock.mock.calls[0][1]?.method).toBe("POST");
    expect(fetchMock.mock.calls[1][0]).toBe("/api/v1/operations/workers/worker%2Fsite%201/resume");
    expect(fetchMock.mock.calls[1][1]?.method).toBe("POST");
  });
});
