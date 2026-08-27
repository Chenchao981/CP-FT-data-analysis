// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { getStageInputRequests, resolveStageInputRequests } from "../../api/stageData";
import { LotEnrichmentModal } from "./LotEnrichmentModal";

vi.mock("../../api/stageData", () => ({
  getStageInputRequests: vi.fn(),
  resolveStageInputRequests: vi.fn(),
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

const requests = {
  import_batch_id: 42,
  status: "NEEDS_INPUT",
  field_code: "LOT_ID" as const,
  prompt: "两个文件没有取得 Lot，请人工确认。",
  latest_job_id: 51,
  requests: [
    { input_request_id: 801, source_file_id: 101, original_file_name: "第一份数据.xlsx", current_value: null },
    { input_request_id: 802, source_file_id: 102, original_file_name: "第二份数据.xlsx", current_value: null },
  ],
};

function renderModal(onResolved = vi.fn()) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <LotEnrichmentModal
        open
        businessDomain="PRODUCTION"
        testStage="FT"
        importBatchId={42}
        onClose={vi.fn()}
        onResolved={onResolved}
      />
    </QueryClientProvider>,
  );
}

describe("LotEnrichmentModal", () => {
  beforeEach(() => {
    vi.mocked(getStageInputRequests).mockResolvedValue(requests);
    vi.mocked(resolveStageInputRequests).mockResolvedValue({ import_batch_id: 42, job_id: 52, status: "QUEUED" });
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("lists files without exposing internal request or source identifiers and keeps Lot values separate by default", async () => {
    renderModal();

    expect(await screen.findByText("第一份数据.xlsx 的 Lot")).toBeInTheDocument();
    expect(screen.getByText("第二份数据.xlsx 的 Lot")).toBeInTheDocument();
    expect(screen.queryByText("801")).not.toBeInTheDocument();
    expect(screen.queryByText("101")).not.toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "这些文件属于同一个 Lot" })).not.toBeChecked();
    expect(screen.getAllByPlaceholderText("输入经确认的批次号")).toHaveLength(2);
  });

  it("submits one explicit Lot per file and records the confirmation basis", async () => {
    const onResolved = vi.fn();
    renderModal(onResolved);
    const lotInputs = await screen.findAllByPlaceholderText("输入经确认的批次号");
    fireEvent.change(lotInputs[0], { target: { value: " LOT-A " } });
    fireEvent.change(lotInputs[1], { target: { value: "LOT-B" } });
    fireEvent.click(screen.getByLabelText("从生产或测试记录确认"));
    fireEvent.click(screen.getByRole("button", { name: "保存并重新处理" }));

    await waitFor(() => expect(resolveStageInputRequests).toHaveBeenCalledWith(
      "PRODUCTION",
      "FT",
      42,
      {
        resolutions: [
          { input_request_id: 801, lot_id: "LOT-A" },
          { input_request_id: 802, lot_id: "LOT-B" },
        ],
        reason: "根据生产或测试记录人工确认 Lot",
      },
    ));
    expect(onResolved).toHaveBeenCalledWith({ import_batch_id: 42, job_id: 52, status: "QUEUED" });
  });

  it("uses one Lot only after the user explicitly confirms all files belong together", async () => {
    renderModal();
    await screen.findByText("第一份数据.xlsx 的 Lot");
    fireEvent.click(screen.getByRole("checkbox", { name: "这些文件属于同一个 Lot" }));
    const sharedInput = await screen.findByLabelText("这些文件共同的 Lot");
    fireEvent.change(sharedInput, { target: { value: "LOT-SAME" } });
    fireEvent.click(screen.getByLabelText("从文件名确认"));
    fireEvent.click(screen.getByRole("button", { name: "保存并重新处理" }));

    await waitFor(() => expect(resolveStageInputRequests).toHaveBeenCalledWith(
      "PRODUCTION",
      "FT",
      42,
      {
        resolutions: [
          { input_request_id: 801, lot_id: "LOT-SAME" },
          { input_request_id: 802, lot_id: "LOT-SAME" },
        ],
        reason: "根据源文件名人工确认 Lot",
      },
    ));
  });
});
