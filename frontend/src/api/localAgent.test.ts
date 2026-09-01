import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  clearLocalAgentBrowserState,
  clearLocalAgentRunReference,
  getLocalAgentHealth,
  deleteLocalRun,
  listLocalAgentTools,
  previewLocalSelection,
  runLocalSelection,
  saveLocalAgentRunReference,
  saveLocalAgentToken,
  selectLocalFolder,
  storedLocalAgentRunReference,
} from "./localAgent";

describe("local Agent browser bridge", () => {
  beforeEach(() => {
    vi.stubGlobal("sessionStorage", {
      getItem: vi.fn(() => "pairing-token"),
      setItem: vi.fn(),
      removeItem: vi.fn(),
    });
    vi.stubGlobal("localStorage", {
      getItem: vi.fn(() => null),
      setItem: vi.fn(),
      removeItem: vi.fn(),
    });
  });

  afterEach(() => vi.restoreAllMocks());

  it("never sends the TMS bearer token to the loopback Agent", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ tools: [] }), { status: 200 }),
    );

    await listLocalAgentTools();

    const [url, init] = fetchMock.mock.calls[0];
    const headers = new Headers(init?.headers);
    expect(url).toBe("http://127.0.0.1:8765/v1/tools");
    expect(headers.get("X-TMS-Agent-Token")).toBe("pairing-token");
    expect(headers.has("Authorization")).toBe(false);
    expect(init?.credentials).toBe("omit");
  });

  it("uses only opaque selection and run ids after the native picker", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async () => (
      new Response(JSON.stringify({ run_id: "run-1" }), { status: 200 })
    ));

    await selectLocalFolder();
    await previewLocalSelection("selection-1", "JIEQUN_FT_QUICK_PAT_EXISTING");
    await runLocalSelection("selection-1", "JIEQUN_FT_QUICK_PAT_EXISTING", "a".repeat(64));

    expect(fetchMock.mock.calls[0][0]).toBe("http://127.0.0.1:8765/v1/select-folder");
    expect(String(fetchMock.mock.calls[0][1]?.body)).toBe("{}");
    expect(fetchMock.mock.calls[1][0]).toBe("http://127.0.0.1:8765/v1/selections/selection-1/preview");
    expect(fetchMock.mock.calls[2][0]).toBe("http://127.0.0.1:8765/v1/selections/selection-1/runs");
    expect(JSON.parse(String(fetchMock.mock.calls[2][1]?.body))).toEqual({
      tool_code: "JIEQUN_FT_QUICK_PAT_EXISTING",
      confirmed_manifest_sha256: "a".repeat(64),
    });
    expect(String(fetchMock.mock.calls[2][1]?.body)).not.toContain(":\\");
  });

  it("stores a pairing token in session storage rather than persistent local storage", () => {
    saveLocalAgentToken(" token ");
    expect(sessionStorage.setItem).toHaveBeenCalledWith("tms_local_agent_token", "token");
  });

  it("persists only an opaque recoverable run reference for the loopback Agent origin", () => {
    const runId = "123e4567-e89b-42d3-a456-426614174000";
    saveLocalAgentRunReference(runId, 91);

    expect(localStorage.setItem).toHaveBeenCalledWith(
      "tms_local_agent_run:http://127.0.0.1:8765",
      JSON.stringify({ run_id: runId, registered_session_id: 91 }),
    );
    vi.mocked(localStorage.getItem).mockReturnValue(
      JSON.stringify({ run_id: runId, registered_session_id: 91 }),
    );
    expect(storedLocalAgentRunReference()).toEqual({
      run_id: runId,
      registered_session_id: 91,
    });

    clearLocalAgentRunReference(runId);
    expect(localStorage.removeItem).toHaveBeenCalledWith(
      "tms_local_agent_run:http://127.0.0.1:8765",
    );
  });

  it("clears the pairing token and pending run reference as one browser state", () => {
    clearLocalAgentBrowserState();

    expect(sessionStorage.removeItem).toHaveBeenCalledWith("tms_local_agent_token");
    expect(localStorage.removeItem).toHaveBeenCalledWith(
      "tms_local_agent_run:http://127.0.0.1:8765",
    );
  });

  it("allows health probing without sending a pairing token", async () => {
    vi.mocked(sessionStorage.getItem).mockReturnValue(null);
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ status: "ok" }), { status: 200 }),
    );

    await getLocalAgentHealth();

    const headers = new Headers(fetchMock.mock.calls[0][1]?.headers);
    expect(headers.has("X-TMS-Agent-Token")).toBe(false);
  });

  it("cleans a completed run through its opaque id", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(null, { status: 204 }),
    );

    await deleteLocalRun("run-1");

    expect(fetchMock.mock.calls[0][0]).toBe("http://127.0.0.1:8765/v1/runs/run-1");
    expect(fetchMock.mock.calls[0][1]?.method).toBe("DELETE");
  });
});
