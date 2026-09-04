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

const preview = {
  path: String.raw`F:\cp-source\lot.zip`,
  source_label: "lot.zip",
  input_kind: "FILE" as const,
  mode: "LOCAL_PATH_SIZE_MTIME_V1" as const,
  recursive: true as const,
  file_count: 1,
  total_bytes: 2048,
  archive_count: 1,
  sample_files: ["lot.zip"],
  sample_truncated: false,
  sha: "a".repeat(64),
  allowed_suffixes: [".xls", ".xlsx", ".zip"],
  tool_code: "JETECH_CP_QUICK_PAT_EXISTING" as const,
  tool_name: "积塔 CP 原始目录 PAT",
  test_stage: "CP" as const,
  factory_code: "JETECH",
};

function renderPanel() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <DirectPathAnalysisPanel onCreated={vi.fn()} />
    </QueryClientProvider>,
  );
}

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
    vi.mocked(previewDirectPath).mockResolvedValue(preview);
    vi.mocked(createDirectPathPat).mockResolvedValue({ analysis_session_id: 27 } as never);
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("follows CP factory, input/output, operation and PAT execution order", async () => {
    renderPanel();

    fireEvent.click(screen.getByRole("button", { name: /CP 工具/ }));
    fireEvent.click(screen.getByRole("button", { name: /积塔/ }));
    fireEvent.change(screen.getByLabelText("输出路径"), { target: { value: String.raw`F:\result` } });
    fireEvent.click(screen.getAllByRole("button", { name: /预览选择/ })[0]);

    const dialog = await screen.findByRole("dialog", { name: "选择 积塔 输入数据" });
    expect(within(dialog).getByText(/\.xls、\.xlsx、\.zip/)).toBeInTheDocument();
    const archiveRow = within(dialog).getByText("lot.zip").closest("tr")!;
    fireEvent.click(within(archiveRow).getByRole("button", { name: /选\s*择/ }));

    expect(screen.getByLabelText("输入路径")).toHaveValue(String.raw`F:\cp-source\lot.zip`);
    fireEvent.click(screen.getByRole("button", { name: /解析范围/ }));

    await waitFor(() => expect(previewDirectPath).toHaveBeenCalledWith(
      String.raw`F:\cp-source\lot.zip`,
      "JETECH_CP_QUICK_PAT_EXISTING",
    ));
    expect(await screen.findByText("已确认解析范围：lot.zip")).toBeInTheDocument();
    expect(screen.getByText("压缩包 1 个")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /开始后台 PAT/ }));
    await waitFor(() => expect(createDirectPathPat).toHaveBeenCalledWith(
      preview,
      String.raw`F:\result`,
    ));
  }, 30_000);

  it("separates CP and FT factories and shows honest web availability", () => {
    renderPanel();

    expect(screen.getByRole("button", { name: /日月新/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /华虹/ })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /FT 数据清洗/ }));
    expect(screen.getByText(/当前网页后台执行合同尚未接入/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /CP 工具/ }));
    expect(screen.getByRole("button", { name: /华虹/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /日月新/ })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /PAT 参数分析/ })).toHaveTextContent("可运行");
  }, 15_000);

  it("runs PAT without requiring an extra local export directory", async () => {
    renderPanel();
    fireEvent.change(screen.getByLabelText("输入路径"), {
      target: { value: String.raw`F:\ft-source` },
    });
    fireEvent.click(screen.getByRole("button", { name: /解析范围/ }));

    expect(await screen.findByText("已确认解析范围：lot.zip")).toBeInTheDocument();
    expect(screen.getByText("自动保存到个人历史")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /开始后台 PAT/ }));
    await waitFor(() => expect(createDirectPathPat).toHaveBeenCalledWith(preview, ""));
  }, 15_000);
});
