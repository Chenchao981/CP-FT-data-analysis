// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { listCurrentDatasets, type CurrentDatasetRow } from "../../api/catalog";
import {
  archiveDataset,
  createDatasetReprocess,
  createLatestExport,
  downloadLatestExportArtifact,
  getLatestExportStatus,
  type LifecycleExportStatus,
  type LifecycleJobReceipt,
} from "../../api/lifecycle";
import { useAuth } from "../auth/AuthContext";
import { DatasetCurrentCatalog } from "./DatasetCurrentCatalog";

vi.mock("../../api/catalog", () => ({ listCurrentDatasets: vi.fn() }));
vi.mock("../../api/lifecycle", () => ({
  createLatestExport: vi.fn(),
  createDatasetReprocess: vi.fn(),
  archiveDataset: vi.fn(),
  getLatestExportStatus: vi.fn(),
  downloadLatestExportArtifact: vi.fn(),
}));
vi.mock("../auth/AuthContext", () => ({ useAuth: vi.fn() }));

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

const row: CurrentDatasetRow = {
  dataset_id: 20,
  dataset_version_id: 203,
  version_no: 3,
  import_batch_id: 14,
  job_id: 91,
  processing_run_id: 51,
  product_name: "NCE-IGBT",
  lot_id: "LOT-202608",
  factory_code: "riyuexin",
  business_domain: "PRODUCTION",
  test_stage: "FT",
  status: "PUBLISHED",
  unit_count: 100,
  pass_count: null,
  yield_rate: null,
  source_file_count: 2,
  processed_at_utc: "2026-08-28T09:00:00Z",
};

const receipt = (overrides: Partial<LifecycleJobReceipt> = {}): LifecycleJobReceipt => ({
  job_id: 81,
  job_type: "EXPORT_LATEST",
  dataset_id: 20,
  dataset_version_id: 203,
  action_type: "EXPORT_LATEST",
  status: "QUEUED",
  import_batch_id: 14,
  cleaner_release_id: 9,
  parent_job_id: 91,
  idempotency_key: "export-20-request-0001",
  created: true,
  idempotent_replay: false,
  ...overrides,
});

const exportReady: LifecycleExportStatus = {
  job_id: 81,
  dataset_id: 20,
  dataset_version_id: 203,
  cleaner_release_id: 9,
  status: "SUCCESS",
  error_code: null,
  availability: "READY",
  expires_at_utc: "2026-08-30T00:00:00Z",
  artifacts: [{
    artifact_id: 3,
    role: "EXPORT",
    file_name: "latest-cleaner-result.xlsx",
    size_bytes: 2048,
    sha256: "a".repeat(64),
    physical_status: "PRESENT",
    expires_at_utc: "2026-08-30T00:00:00Z",
    download_url: "/api/v1/lifecycle/exports/81/artifacts/3/download",
  }],
};

const authFor = (permissions: string[], roles: string[] = ["DATA_OWNER"]) => ({
  user: {
    user_id: 1,
    login_name: "owner",
    display_name: "Dataset Owner",
    department_code: null,
    roles,
    permissions,
  },
  loading: false,
  login: vi.fn(async () => undefined),
  logout: vi.fn(async () => undefined),
  can: vi.fn((permission: string) => permissions.includes(permission)),
});

const renderCatalog = (searchParams = new URLSearchParams()) => {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const props = {
    searchParams,
    onSearchParamsChange: vi.fn(),
    onOpenAnalytics: vi.fn(),
    onOpenJob: vi.fn(),
  };
  render(
    <QueryClientProvider client={queryClient}>
      <DatasetCurrentCatalog {...props} />
    </QueryClientProvider>,
  );
  return props;
};

describe("DatasetCurrentCatalog", () => {
  beforeEach(() => {
    vi.mocked(useAuth).mockReturnValue(authFor(["DATASET_READ", "EXPORT_DATA", "TASK_CREATE"]));
    vi.mocked(listCurrentDatasets).mockResolvedValue({ items: [row], total: 45, page: 1, page_size: 20 });
    vi.mocked(createLatestExport).mockResolvedValue(receipt());
    vi.mocked(createDatasetReprocess).mockResolvedValue(receipt({ job_id: 82, job_type: "INITIAL_IMPORT", action_type: "REPROCESS_UPDATE" }));
    vi.mocked(archiveDataset).mockResolvedValue(receipt({ job_id: 83, job_type: "DELETE_TASK", action_type: "DELETE_TASK", cleaner_release_id: null }));
    vi.mocked(getLatestExportStatus).mockResolvedValue(exportReady);
    vi.mocked(downloadLatestExportArtifact).mockResolvedValue(undefined);
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("loads URL filters, preserves UNKNOWN yield, and opens analysis or Job details", async () => {
    const props = renderCatalog(new URLSearchParams({
      page: "2",
      page_size: "50",
      product_name: "NCE-IGBT",
      lot_id: "LOT-202608",
      factory_code: "riyuexin",
      business_domain: "PRODUCTION",
      test_stage: "FT",
      status: "PUBLISHED",
      from_utc: "2026-08-01T00:00:00Z",
      to_utc: "2026-08-31T23:59:59Z",
    }));

    expect(await screen.findByText("NCE-IGBT")).toBeInTheDocument();
    expect(listCurrentDatasets).toHaveBeenCalledWith({
      page: 2,
      page_size: 50,
      product_name: "NCE-IGBT",
      lot_id: "LOT-202608",
      factory_code: "riyuexin",
      business_domain: "PRODUCTION",
      test_stage: "FT",
      status: "PUBLISHED",
      from_utc: "2026-08-01T00:00:00Z",
      to_utc: "2026-08-31T23:59:59Z",
    });
    const dataRow = screen.getByText("NCE-IGBT").closest("tr");
    expect(dataRow).not.toBeNull();
    expect(within(dataRow!).getAllByText("—").length).toBeGreaterThanOrEqual(2);
    expect(within(dataRow!).queryByText("0.00%")).not.toBeInTheDocument();

    fireEvent.click(within(dataRow!).getByRole("button", { name: /分析$/ }));
    fireEvent.click(within(dataRow!).getByRole("button", { name: /Job #91/ }));
    expect(props.onOpenAnalytics).toHaveBeenCalledWith(20, 3);
    expect(props.onOpenJob).toHaveBeenCalledWith(91);
  }, 15_000);

  it("writes filters and pagination back to URL search state", async () => {
    const props = renderCatalog();
    await screen.findByText("NCE-IGBT");

    fireEvent.change(screen.getByLabelText("产品"), { target: { value: "NCE-MOS" } });
    fireEvent.change(screen.getByLabelText("Lot"), { target: { value: "LOT-NEW" } });
    fireEvent.click(screen.getByRole("button", { name: /检索/ }));

    await waitFor(() => expect(props.onSearchParamsChange).toHaveBeenCalled());
    let next = vi.mocked(props.onSearchParamsChange).mock.calls.at(-1)?.[0];
    expect(next?.get("page")).toBe("1");
    expect(next?.get("page_size")).toBe("20");
    expect(next?.get("product_name")).toBe("NCE-MOS");
    expect(next?.get("lot_id")).toBe("LOT-NEW");

    fireEvent.click(screen.getByTitle("2"));
    await waitFor(() => expect(props.onSearchParamsChange).toHaveBeenCalledTimes(2));
    next = vi.mocked(props.onSearchParamsChange).mock.calls.at(-1)?.[0];
    expect(next?.get("page")).toBe("2");
    expect(next?.get("page_size")).toBe("20");
  }, 15_000);

  it("gates export and reprocess by permission while preserving the backend-enforced Owner archive entry", async () => {
    vi.mocked(useAuth).mockReturnValue(authFor(["DATASET_READ"], ["READER"]));
    renderCatalog();

    const product = await screen.findByText("NCE-IGBT", {}, { timeout: 10_000 });
    const dataRow = product.closest("tr")!;
    expect(within(dataRow).queryByRole("button", { name: /导出最新/ })).not.toBeInTheDocument();
    expect(within(dataRow).queryByRole("button", { name: /显式重处理/ })).not.toBeInTheDocument();
    expect(within(dataRow).getByRole("button", { name: /逻辑归档/ })).toBeInTheDocument();
    expect(document.body).toHaveTextContent("是否为 Dataset Owner 由后端行级授权最终判定");
    expect(document.body).toHaveTextContent("不删除 FTP/NAS 原始文件");
  }, 15_000);

  it("requires explicit acknowledgement before creating a non-mutating latest export", async () => {
    const props = renderCatalog();
    const product = await screen.findByText("NCE-IGBT", {}, { timeout: 10_000 });

    fireEvent.click(within(product.closest("tr")!).getByRole("button", { name: /导出最新/ }));
    expect(await screen.findByText("非变异临时导出")).toBeInTheDocument();
    expect(document.body).toHaveTextContent("Current Dataset、Canonical 数据与补录在导出前后保持不变");
    fireEvent.click(screen.getByRole("button", { name: /创建导出 Job/ }));
    expect(await screen.findByText("请确认导出为非变异临时任务")).toBeInTheDocument();
    expect(createLatestExport).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", { name: /创建导出 Job/ }));
    await waitFor(() => expect(createLatestExport).toHaveBeenCalledWith(20, expect.stringMatching(/^export-20-/)));
    await waitFor(() => expect(props.onSearchParamsChange).toHaveBeenCalled());
    const next = props.onSearchParamsChange.mock.calls.at(-1)?.[0] as URLSearchParams;
    expect(next.get("export_job_id")).toBe("81");
    expect(props.onOpenJob).not.toHaveBeenCalled();
  }, 20_000);

  it("requires typed confirmation and a complete reason for reprocess and logical archive", async () => {
    const props = renderCatalog();
    const product = await screen.findByText("NCE-IGBT", {}, { timeout: 10_000 });
    const dataRow = product.closest("tr")!;

    fireEvent.click(within(dataRow).getByRole("button", { name: /显式重处理/ }));
    expect(await screen.findByText("将创建新版本")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("输入 REPROCESS 确认"), { target: { value: "REPROCESS" } });
    fireEvent.change(screen.getByLabelText("完整操作原因"), { target: { value: "Cleaner 发布后显式重处理" } });
    fireEvent.click(screen.getByRole("button", { name: /创建重处理 Job/ }));
    await waitFor(() => expect(createDatasetReprocess).toHaveBeenCalledWith(20, "Cleaner 发布后显式重处理", expect.stringMatching(/^reprocess-20-/)));
    await waitFor(() => expect(props.onOpenJob).toHaveBeenCalledWith(82));

    fireEvent.click(within(dataRow).getByRole("button", { name: /逻辑归档/ }));
    expect(await screen.findByText("仅逻辑归档，不删除源文件")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("输入 ARCHIVE 确认"), { target: { value: "ARCHIVE" } });
    fireEvent.change(screen.getByLabelText("完整操作原因"), { target: { value: "重复导入，已完成 Owner 核准" } });
    fireEvent.click(screen.getByRole("button", { name: /创建逻辑归档 Job/ }));
    await waitFor(() => expect(archiveDataset).toHaveBeenCalledWith(20, "重复导入，已完成 Owner 核准", expect.stringMatching(/^archive-20-/)));
    await waitFor(() => expect(props.onOpenJob).toHaveBeenCalledWith(83));
  }, 25_000);

  it("shows safe export status and downloads a registered Artifact", async () => {
    renderCatalog(new URLSearchParams({ export_job_id: "81" }));

    expect(await screen.findByText("latest-cleaner-result.xlsx", {}, { timeout: 10_000 })).toBeInTheDocument();
    expect(getLatestExportStatus).toHaveBeenCalledWith(81);
    expect(screen.getByText("READY")).toBeInTheDocument();
    expect(screen.getByText("Cleaner Release")).toBeInTheDocument();
    expect(document.body).toHaveTextContent("不修改 Canonical、Current Dataset Version 或人工补录");
    fireEvent.click(screen.getByRole("button", { name: /下载$/ }));
    await waitFor(() => expect(downloadLatestExportArtifact).toHaveBeenCalledWith(81, 3, "latest-cleaner-result.xlsx"));
    expect(screen.queryByText(/storage_uri|file:\/\/|C:\\/i)).not.toBeInTheDocument();
  }, 15_000);

  it("keeps lifecycle and Artifact failures visible but sanitized", async () => {
    vi.mocked(createDatasetReprocess).mockRejectedValueOnce(new Error("server=db;path=C:\\secret;password=x"));
    renderCatalog();
    const loadedProduct = await screen.findByText("NCE-IGBT", {}, { timeout: 10_000 });
    fireEvent.click(within(loadedProduct.closest("tr")!).getByRole("button", { name: /显式重处理/ }));
    fireEvent.change(await screen.findByLabelText("输入 REPROCESS 确认"), { target: { value: "REPROCESS" } });
    fireEvent.change(screen.getByLabelText("完整操作原因"), { target: { value: "Cleaner 发布后显式重处理" } });
    fireEvent.click(screen.getByRole("button", { name: /创建重处理 Job/ }));
    expect(await screen.findByText("生命周期操作失败")).toBeInTheDocument();
    expect(screen.queryByText(/server=db|C:\\secret|password=x/)).not.toBeInTheDocument();
  }, 20_000);

  it("shows a safe visible error when Artifact download fails", async () => {
    vi.mocked(downloadLatestExportArtifact).mockRejectedValueOnce(new Error("path=C:\\private-export;secret=x"));
    renderCatalog(new URLSearchParams({ export_job_id: "81" }));

    await screen.findByText("latest-cleaner-result.xlsx", {}, { timeout: 10_000 });
    fireEvent.click(screen.getByRole("button", { name: /下载$/ }));
    expect(await screen.findByText("Artifact 下载失败")).toBeInTheDocument();
    expect(screen.getByText(/可能已过期、已清理或完整性校验未通过/)).toBeInTheDocument();
    expect(screen.queryByText(/C:\\private-export|secret=x/)).not.toBeInTheDocument();
  }, 15_000);
});
