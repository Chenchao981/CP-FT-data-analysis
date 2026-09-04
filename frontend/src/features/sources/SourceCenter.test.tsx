// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { getSourceCenterSnapshot } from "../../api/sourceCenter";
import { SourceCenter } from "./SourceCenter";

vi.mock("../../api/sourceCenter", () => ({ getSourceCenterSnapshot: vi.fn() }));

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

vi.stubGlobal("ResizeObserver", class {
  observe() { return undefined; }
  unobserve() { return undefined; }
  disconnect() { return undefined; }
});

describe("SourceCenter", () => {
  beforeEach(() => {
    vi.mocked(getSourceCenterSnapshot).mockResolvedValue({
      roots: [{
        code: "huahong_cp",
        name: "华虹工程 CP 灰度样本",
        test_stage: "CP",
        factory_code: "huahong",
        allowed_suffixes: [".zip", ".txt"],
        purpose: "FORMAL_IMPORT",
        business_domains: ["ENGINEERING", "PRODUCTION"],
        available: true,
      }],
      recentImports: [{
        import_batch_id: 18,
        sequence_no: 1,
        receipt_id: 181,
        source_file_id: 81,
        original_file_name: "LOT-18.zip",
        extension: ".zip",
        size_bytes: 1024,
        factory_code: "huahong",
        upload_time_utc: "2026-09-03T01:00:00Z",
        completion_time_utc: null,
        uploader_login: "system",
        uploader_name: "系统采集",
        source_channel: "SOURCE_CATALOG",
        is_duplicate_receipt: false,
        can_manage: true,
        can_download_source: true,
        status: "PROCESSED",
        latest_job_id: 91,
        error_code: null,
        error_message: null,
        action_required: null,
      }],
      unavailableQueries: 0,
    });
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("shows authorized server sources without engineering/production as a user classification", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const onNavigate = vi.fn();
    const onOpenJob = vi.fn();
    render(<QueryClientProvider client={client}><SourceCenter onNavigate={onNavigate} onOpenJob={onOpenJob} /></QueryClientProvider>);

    expect(await screen.findByText("华虹 CP 灰度样本")).toBeInTheDocument();
    expect(screen.queryByText("华虹工程 CP 灰度样本")).not.toBeInTheDocument();
    expect(screen.getByText("LOT-18.zip")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "浏览并入库" }));
    expect(onNavigate).toHaveBeenCalledWith("/cp");
    fireEvent.click(screen.getByRole("button", { name: "Job #91" }));
    expect(onOpenJob).toHaveBeenCalledWith(91);
  });
});
