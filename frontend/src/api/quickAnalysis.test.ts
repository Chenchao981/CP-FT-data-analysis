import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import {
  browseDirectPath,
  createDirectPathPat,
  createQuickPat,
  downloadQuickPat,
  listQuickAnalysisSessions,
  listQuickSourceDirectories,
  previewDirectPath,
  previewQuickSourceManifest,
} from "./quickAnalysis";

beforeAll(() => {
  vi.stubGlobal("localStorage", { getItem: vi.fn(() => "mock-token"), setItem: vi.fn(), removeItem: vi.fn() });
});

afterEach(() => vi.restoreAllMocks());

describe("quick analysis api", () => {
  it("uses a root code and encoded relative directory instead of an absolute path", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ directories: [] }), { status: 200 }),
    );
    await listQuickSourceDirectories("JIEQUN_SHARED", "产品 A/批次 1");
    expect(fetchMock.mock.calls[0][0]).toBe(
      "/api/v1/quick-analysis/source-roots/JIEQUN_SHARED/directories?relative_path=%E4%BA%A7%E5%93%81+A%2F%E6%89%B9%E6%AC%A1+1",
    );
  });

  it("queues Quick PAT without creating a file upload body", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ analysis_session_id: 9 }), { status: 201 }),
    );
    await createQuickPat("JIEQUN_SHARED", "520data/NCEAP020N10LL", "PATH_SIZE_MTIME_V1", "a".repeat(64));
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/v1/quick-analysis/pat");
    expect(init?.method).toBe("POST");
    expect(JSON.parse(String(init?.body))).toEqual({
      source_root_code: "JIEQUN_SHARED",
      source_relative_path: "520data/NCEAP020N10LL",
      source_manifest_mode: "PATH_SIZE_MTIME_V1",
      source_manifest_sha256: "a".repeat(64),
    });
  });

  it("previews and queues a directly accessible directory without file upload", async () => {
    const preview = {
      path: String.raw`F:\data\520data`,
      source_label: "520data",
      input_kind: "DIRECTORY" as const,
      mode: "LOCAL_PATH_SIZE_MTIME_V1" as const,
      recursive: true as const,
      file_count: 520,
      total_bytes: 1234,
      archive_count: 0,
      sample_files: ["one.csv"],
      sample_truncated: false,
      sha: "b".repeat(64),
      allowed_suffixes: [".csv"],
      tool_code: "JIEQUN_FT_QUICK_PAT_EXISTING" as const,
      tool_name: "杰群 FT 原始目录低内存 PAT",
      test_stage: "FT" as const,
      factory_code: "JIEQUN",
    };
    const fetchMock = vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(new Response(JSON.stringify(preview), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ analysis_session_id: 10 }), { status: 201 }));

    await previewDirectPath(preview.path, preview.tool_code);
    await createDirectPathPat(preview);

    expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/quick-analysis/direct-path/preview");
    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toEqual({
      path: preview.path,
      tool_code: preview.tool_code,
    });
    expect(fetchMock.mock.calls[1][0]).toBe("/api/v1/quick-analysis/direct-path/pat");
    expect(JSON.parse(String(fetchMock.mock.calls[1][1]?.body))).toEqual({
      path: preview.path,
      tool_code: preview.tool_code,
      source_manifest_mode: preview.mode,
      source_manifest_sha256: preview.sha,
    });
  });

  it("browses a directly accessible local path using the selected tool contract", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ path: String.raw`F:\data`, items: [] }), { status: 200 }),
    );

    await browseDirectPath(String.raw`F:\data`, "HUAHONG_CP_QUICK_PAT_EXISTING");

    expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/quick-analysis/direct-path/browse");
    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toEqual({
      path: String.raw`F:\data`,
      tool_code: "HUAHONG_CP_QUICK_PAT_EXISTING",
    });
  });

  it("previews the recursive manifest and requests a server-side session page", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async () => (
      new Response(JSON.stringify({ items: [], total: 0, page: 2, page_size: 50 }), { status: 200 })
    ));
    await previewQuickSourceManifest("JIEQUN_SHARED", "产品 A/批次 1");
    expect(fetchMock.mock.calls[0][0]).toBe(
      "/api/v1/quick-analysis/source-roots/JIEQUN_SHARED/manifest-preview?relative_path=%E4%BA%A7%E5%93%81+A%2F%E6%89%B9%E6%AC%A1+1",
    );
    await listQuickAnalysisSessions({ page: 2, page_size: 50, status: "FAILED", access_scope: "PERSONAL" });
    expect(fetchMock.mock.calls[1][0]).toBe(
      "/api/v1/quick-analysis/sessions?page=2&page_size=50&status=FAILED&access_scope=PERSONAL",
    );
  });

  it("downloads PAT with bearer authorization", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(new Blob(["xlsx"]), { status: 200 }),
    );
    URL.createObjectURL = vi.fn(() => "blob:pat");
    URL.revokeObjectURL = vi.fn();
    const click = vi.fn();
    vi.stubGlobal("document", { createElement: vi.fn(() => ({ click, href: "", download: "" } as unknown as HTMLAnchorElement)) });
    await downloadQuickPat(9, "PAT_001.xlsx");
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/v1/quick-analysis/sessions/9/download");
    expect((init?.headers as Headers).get("Authorization")).toBe("Bearer mock-token");
    expect(click).toHaveBeenCalled();
  });
});
