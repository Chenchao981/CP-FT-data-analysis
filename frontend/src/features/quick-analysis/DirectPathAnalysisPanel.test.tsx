// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { browseDirectPath, createDirectPathPat, previewDirectPath } from "../../api/quickAnalysis";
import { DirectPathAnalysisPanel } from "./DirectPathAnalysisPanel";

vi.mock("../../api/quickAnalysis", () => ({
  browseDirectPath: vi.fn(),
  createDirectPathPat: vi.fn(),
  previewDirectPath: vi.fn(),
}));

Object.defineProperty(window, "matchMedia", {
  writable: true,
  value: () => ({
    matches: false,
    addListener: () => undefined,
    removeListener: () => undefined,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    dispatchEvent: () => false,
  }),
});

vi.stubGlobal("ResizeObserver", class {
  observe() { return undefined; }
  unobserve() { return undefined; }
  disconnect() { return undefined; }
});

describe("DirectPathAnalysisPanel", () => {
  beforeEach(() => {
    vi.mocked(browseDirectPath).mockResolvedValue({
      path: String.raw`F:\cp-source`,
      parent_path: "F:\\",
      allowed_suffixes: [".xls", ".xlsx", ".zip"],
      truncated: false,
      items: [
        { name: "lot-folder", path: String.raw`F:\cp-source\lot-folder`, kind: "DIRECTORY", size_bytes: null, suffix: null, is_archive: false, selectable: true, selection_hint: null },
        { name: "lot.zip", path: String.raw`F:\cp-source\lot.zip`, kind: "FILE", size_bytes: 2048, suffix: ".zip", is_archive: true, selectable: true, selection_hint: null },
      ],
    });
    vi.mocked(previewDirectPath).mockResolvedValue({
      path: String.raw`F:\cp-source\lot.zip`,
      source_label: "lot.zip",
      input_kind: "FILE",
      mode: "LOCAL_PATH_SIZE_MTIME_V1",
      recursive: true,
      file_count: 1,
      total_bytes: 2048,
      archive_count: 1,
      sample_files: ["lot.zip"],
      sample_truncated: false,
      sha: "a".repeat(64),
      allowed_suffixes: [".xls", ".xlsx", ".zip"],
      tool_code: "JETECH_CP_QUICK_PAT_EXISTING",
      tool_name: "积塔 CP 原始目录 PAT",
      test_stage: "CP",
      factory_code: "JETECH",
    });
    vi.mocked(createDirectPathPat).mockResolvedValue({} as never);
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("browses local paths, selects an archive, and previews the parsed file list", async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    render(
      <QueryClientProvider client={queryClient}>
        <DirectPathAnalysisPanel onCreated={vi.fn()} />
      </QueryClientProvider>,
    );

    fireEvent.mouseDown(screen.getByRole("combobox", { name: "分析工具" }));
    fireEvent.click(await screen.findByText("CP 工具 · 积塔原始目录 PAT"));
    fireEvent.click(screen.getByRole("button", { name: /浏览路径/ }));

    const dialog = await screen.findByRole("dialog", { name: "浏览本机 / NAS 数据来源" });
    expect(within(dialog).getByText(/\.xls、\.xlsx、\.zip/)).toBeInTheDocument();
    const archiveRow = within(dialog).getByText("lot.zip").closest("tr")!;
    fireEvent.click(within(archiveRow).getByRole("button", { name: /选\s*择/ }));

    expect(screen.getByLabelText("本机、NAS目录或源文件")).toHaveValue(String.raw`F:\cp-source\lot.zip`);
    fireEvent.click(screen.getByRole("button", { name: /预览解析范围/ }));

    await waitFor(() => expect(previewDirectPath).toHaveBeenCalledWith(
      String.raw`F:\cp-source\lot.zip`,
      "JETECH_CP_QUICK_PAT_EXISTING",
    ));
    expect(await screen.findByText("已预览：lot.zip")).toBeInTheDocument();
    expect(screen.getByText("压缩包 1 个")).toBeInTheDocument();
    expect(screen.getByText("将解析的文件")).toBeInTheDocument();
  }, 30_000);
});
