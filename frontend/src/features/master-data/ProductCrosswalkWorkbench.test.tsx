// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { approveProductCrosswalk, listProductCrosswalks, rejectProductCrosswalk, type ProductCrosswalk } from "../../api/masterData";
import { useAuth } from "../auth/AuthContext";
import { ProductCrosswalkWorkbench } from "./ProductCrosswalkWorkbench";

vi.mock("../../api/masterData", () => ({
  listProductCrosswalks: vi.fn(),
  approveProductCrosswalk: vi.fn(),
  rejectProductCrosswalk: vi.fn(),
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

const row = (overrides: Partial<ProductCrosswalk>): ProductCrosswalk => ({
  crosswalk_id: 7,
  supplier_id: 2,
  supplier_code: "RYX",
  supplier_name: "日月新",
  test_stage: "FT",
  raw_product_code: "RAW-NCE-80V",
  product_id: 31,
  tms_product_code: "TMS-NCE-80V",
  identity_class: "SOURCE_OBSERVED",
  enterprise_system: "SAP_B1",
  enterprise_key: "SHOULD-NOT-BE-EXPOSED-AS-MAPPING",
  status: "PENDING",
  first_observed_at_utc: "2026-08-01T00:00:00Z",
  last_observed_at_utc: "2026-08-29T00:00:00Z",
  approved_by_login: null,
  approved_at_utc: null,
  decision_reason: null,
  ...overrides,
});

const rows = [
  row({}),
  row({
    crosswalk_id: 8,
    raw_product_code: "RAW-NCE-1200V",
    tms_product_code: "TMS-NCE-1200V",
    identity_class: "ENTERPRISE_MAPPED",
    enterprise_key: "SAP-MAT-1200V",
    status: "APPROVED",
    approved_by_login: "governor",
    approved_at_utc: "2026-08-28T00:00:00Z",
    decision_reason: "SAP 主数据 Owner 已核准",
  }),
];

const auth = (canGovern: boolean) => ({
  user: {
    user_id: 1,
    login_name: "manager",
    display_name: "管理者",
    department_code: null,
    roles: canGovern ? ["DATA_GOVERNOR"] : ["MANAGEMENT"],
    permissions: canGovern ? ["RULE_GOVERN"] : ["MANAGEMENT_READ"],
  },
  loading: false,
  login: vi.fn(async () => undefined),
  logout: vi.fn(async () => undefined),
  can: vi.fn((permission: string) => permission === "RULE_GOVERN" && canGovern),
});

const renderWorkbench = (canGovern: boolean, searchParams = new URLSearchParams()) => {
  vi.mocked(useAuth).mockReturnValue(auth(canGovern));
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const props = { searchParams, onSearchParamsChange: vi.fn() };
  render(
    <QueryClientProvider client={queryClient}>
      <ProductCrosswalkWorkbench {...props} />
    </QueryClientProvider>,
  );
  return props;
};

describe("ProductCrosswalkWorkbench", () => {
  beforeEach(() => {
    vi.mocked(listProductCrosswalks).mockResolvedValue({ items: rows, total: 21, page: 1, page_size: 20 });
    vi.mocked(approveProductCrosswalk).mockResolvedValue(row({ status: "APPROVED", enterprise_key: "SAP-MAT-001", identity_class: "ENTERPRISE_MAPPED" }));
    vi.mocked(rejectProductCrosswalk).mockResolvedValue(row({ status: "REJECTED", enterprise_key: null }));
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("keeps pending identities separate from approved SAP-B1 mappings for management readers", async () => {
    renderWorkbench(false);

    expect(await screen.findByText("产品映射")).toBeInTheDocument();
    expect(screen.queryByText("企业映射边界")).not.toBeInTheDocument();
    expect(screen.getAllByText("来源产品标识(TMS)").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("SAP-B1物料编码(仅已审批)").length).toBeGreaterThanOrEqual(1);
    expect(await screen.findByText("SAP-MAT-1200V")).toBeInTheDocument();
    expect(screen.queryByText("SHOULD-NOT-BE-EXPOSED-AS-MAPPING")).not.toBeInTheDocument();
    expect(document.body).toHaveTextContent("—（待审批，不可视为企业映射）");
    expect(screen.getByText("已审批企业映射")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /审批/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /拒绝/ })).not.toBeInTheDocument();
  }, 15_000);

  it("requires an SAP-B1 key, complete reason, and second confirmation before approval", async () => {
    renderWorkbench(true);

    await screen.findByText("SAP-MAT-1200V", {}, { timeout: 10_000 });
    fireEvent.click(screen.getAllByRole("button", { name: /审批/ })[0]);
    expect(await screen.findByText("审批 SAP-B1 产品映射")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /检查并确认/ }));
    expect(await screen.findByText("请输入已由 SAP 主数据 Owner 确认的物料编码")).toBeInTheDocument();
    expect(screen.getByText("请填写完整决策原因")).toBeInTheDocument();
    expect(approveProductCrosswalk).not.toHaveBeenCalled();

    fireEvent.change(screen.getByLabelText("SAP-B1物料编码(仅已审批)"), { target: { value: "SAP-MAT-001" } });
    fireEvent.change(screen.getByLabelText("决策 reason"), { target: { value: "SAP 主数据 Owner 已核准" } });
    fireEvent.click(screen.getByRole("button", { name: /检查并确认/ }));

    expect((await screen.findAllByText("确认审批 SAP-B1 产品映射？")).length).toBeGreaterThanOrEqual(1);
    fireEvent.click(screen.getByRole("button", { name: "确认审批" }));
    await waitFor(() => expect(approveProductCrosswalk).toHaveBeenCalledWith(7, {
      enterprise_system: "SAP_B1",
      enterprise_key: "SAP-MAT-001",
      reason: "SAP 主数据 Owner 已核准",
    }));
  }, 20_000);

  it("preserves server filters and controls paging through URL parameters", async () => {
    const props = renderWorkbench(false, new URLSearchParams({ status: "PENDING", supplier_code: "RYX", page: "1", page_size: "20" }));

    await waitFor(() => expect(listProductCrosswalks).toHaveBeenCalledWith(expect.objectContaining({ status: "PENDING", supplier_code: "RYX", page: 1, page_size: 20 })));
    fireEvent.change(screen.getByLabelText("原始产品标识"), { target: { value: "RAW-NCE" } });
    fireEvent.click(screen.getByRole("button", { name: /检索/ }));
    await waitFor(() => expect(props.onSearchParamsChange).toHaveBeenCalled());
    let next = props.onSearchParamsChange.mock.calls[0][0] as URLSearchParams;
    expect(next.get("status")).toBe("PENDING");
    expect(next.get("supplier_code")).toBe("RYX");
    expect(next.get("raw_product_code")).toBe("RAW-NCE");
    expect(next.get("page")).toBe("1");

    fireEvent.click(screen.getByTitle("2"));
    await waitFor(() => expect(props.onSearchParamsChange).toHaveBeenCalledTimes(2));
    next = props.onSearchParamsChange.mock.calls[1][0] as URLSearchParams;
    expect(next.get("page")).toBe("2");
    expect(next.get("page_size")).toBe("20");
  }, 15_000);
});
