// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../../api/ftpSources";
import { FtpSourcesPanel } from "./FtpSourcesPanel";

vi.mock("../../api/ftpSources", () => ({ listFtpSources: vi.fn(), getFtpOptions: vi.fn(), createFtpSource: vi.fn(), setFtpSourceActive: vi.fn(), requestFtpScan: vi.fn(), checkFtpConnection: vi.fn(), listFtpPackages: vi.fn(), retryFtpPackage: vi.fn() }));
Object.defineProperty(window, "matchMedia", { writable: true, value: (query: string) => ({ matches: false, media: query, onchange: null, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {}, dispatchEvent: () => false }) });
vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });

const source: api.FtpSourceRow = { source_definition_id: 7, source_code: "TEST_FTP", source_name: "测试厂家 FTP", test_stage: "FT", factory_code: "RIYUEXIN", domain_name: "FT 测试域", cleaner_release_id: 12, active: false, protocol: "FTP", package_mode: "SINGLE_FILE", interval_seconds: 300, last_status: "IDLE", last_finished_at_utc: null, next_scan_at_utc: "2026-09-05T06:00:00", error_message: null, lease_expires_at_utc: null, scan_requested: false };
function show(canManage = false) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } });
  const onOpenJob = vi.fn();
  render(<QueryClientProvider client={client}><FtpSourcesPanel canManage={canManage} onOpenJob={onOpenJob} /></QueryClientProvider>);
  return onOpenJob;
}
describe("FTP source management", () => {
  beforeEach(() => {
    vi.mocked(api.listFtpSources).mockResolvedValue([source]);
    vi.mocked(api.getFtpOptions).mockResolvedValue({ domains: [], releases: [] });
    vi.mocked(api.setFtpSourceActive).mockResolvedValue({ accepted: true });
    vi.mocked(api.requestFtpScan).mockResolvedValue({ accepted: true });
    vi.mocked(api.checkFtpConnection).mockResolvedValue({ message: "连接检查通过" });
    vi.mocked(api.listFtpPackages).mockResolvedValue({ total: 1, items: [{ ftp_package_id: 9, relative_path: "LOT-A/data.xlsx", status: "SUBMITTED", attempts: 0, file_count: 1, total_bytes: 1024, job_id: 81, import_batch_id: 91, job_status: "FAILED", error_message: null }] });
  });
  afterEach(() => { cleanup(); vi.resetAllMocks(); });

  it("shows visible records without granting ordinary users collection controls", async () => {
    const openJob = show();
    await screen.findByText("测试厂家 FTP");
    expect(screen.queryByRole("button", { name: "新增 FTP 数据源" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "检查连接" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "采集记录" }));
    expect(await screen.findByText("已提交入库 / 清洗失败")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "查看入库任务" }));
    expect(openJob).toHaveBeenCalledWith(81);
  }, 20_000);

  it("keeps manual collection disabled until an administrator enables the source", async () => {
    show(true);
    await screen.findByText("测试厂家 FTP");
    expect(screen.getByRole("button", { name: "立即采集" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: /^启\s*用$/ }));
    await screen.findByText("已启用定时采集。");
    expect(api.setFtpSourceActive).toHaveBeenCalledWith(7, true);
    expect(api.requestFtpScan).not.toHaveBeenCalled();
  }, 20_000);

  it("queues an active source and surfaces connection errors", async () => {
    vi.mocked(api.listFtpSources).mockResolvedValue([{ ...source, active: true }]);
    vi.mocked(api.checkFtpConnection).mockRejectedValue(new Error("当前运行账号无法读取 FTP 凭据"));
    show(true);
    await screen.findByText("测试厂家 FTP");
    fireEvent.click(screen.getByRole("button", { name: "立即采集" }));
    await waitFor(() => expect(api.requestFtpScan).toHaveBeenCalledWith(7));
    await screen.findByText(/采集请求已登记/);
    fireEvent.click(screen.getByRole("button", { name: "检查连接" }));
    await screen.findByText("当前运行账号无法读取 FTP 凭据");
  }, 20_000);

  it("collects a credential reference and an explicit directory completion marker", async () => {
    show(true);
    await screen.findByText("测试厂家 FTP");
    fireEvent.click(screen.getByRole("button", { name: "新增 FTP 数据源" }));
    expect(await screen.findByLabelText("本机凭据引用")).toBeInTheDocument();
    expect(screen.queryByLabelText("密码")).not.toBeInTheDocument();
    expect(screen.getByLabelText("完成标记文件名")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "保存为暂停状态" })).toBeInTheDocument();
  }, 20_000);
});
