// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { getBatchEnrichments, getEnrichmentFields } from "../../api/enrichments";
import { StageIntakeWorkbench } from "./StageIntakeWorkbench";

vi.mock("../../api/enrichments", () => ({
  createFieldEnrichment: vi.fn(),
  getBatchEnrichments: vi.fn(),
  getEnrichmentFields: vi.fn(),
}));

Object.defineProperty(window, "matchMedia", {
  writable: true,
  value: (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => undefined,
    removeListener: () => undefined,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    dispatchEvent: () => false,
  }),
});

describe("StageIntakeWorkbench field actions", () => {
  beforeEach(() => {
    vi.mocked(getEnrichmentFields).mockResolvedValue([
      { field_code: "LOT_ID", label: "批次号", required_for_analysis: true, can_ignore: false, description: "正式数据必须确认 Lot" },
      { field_code: "PROJECT_CODE", label: "项目代码", required_for_analysis: false, can_ignore: true, description: "可选项目" },
    ]);
    vi.mocked(getBatchEnrichments).mockResolvedValue([]);
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("does not offer IGNORE when the selected field contract forbids it", async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    render(<QueryClientProvider client={queryClient}><StageIntakeWorkbench stage="FT" /></QueryClientProvider>);

    fireEvent.change(screen.getByLabelText("Import Batch编号"), { target: { value: "17" } });
    fireEvent.click(screen.getByRole("button", { name: "加载批次" }));
    const fieldSelect = await screen.findByRole("combobox", { name: "业务字段" });
    fireEvent.mouseDown(fieldSelect);
    fireEvent.click(await screen.findByText("批次号（分析必需）"));

    await waitFor(() => expect(screen.getByLabelText("人工填写")).toBeInTheDocument());
    expect(screen.queryByLabelText("明确忽略")).not.toBeInTheDocument();
  });
});
