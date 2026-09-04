// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AnalyticsContextRequest } from "../../api/analytics";
import {
  createSavedAnalysis,
  createSavedAnalysisRevision,
  deleteSavedAnalysis,
  listSavedAnalyses,
  type SavedAnalysisRecord,
} from "../../api/savedAnalyses";
import { useAuth } from "../auth/AuthContext";
import { SavedAnalysesPanel } from "./SavedAnalysesPanel";
import { createDefaultAnalysisViewState } from "./context/analysisViewState";

vi.mock("../../api/savedAnalyses", () => ({
  SAVED_ANALYSIS_CONTRACT_VERSION: "SAVED_ANALYSIS_V1",
  createSavedAnalysis: vi.fn(),
  createSavedAnalysisRevision: vi.fn(),
  deleteSavedAnalysis: vi.fn(),
  listSavedAnalyses: vi.fn(),
}));
vi.mock("../auth/AuthContext", () => ({ useAuth: vi.fn() }));

Object.defineProperty(window, "matchMedia", { writable: true, value: () => ({ matches: false, addListener: () => undefined, removeListener: () => undefined, addEventListener: () => undefined, removeEventListener: () => undefined }) });
vi.stubGlobal("ResizeObserver", class { observe() { return undefined; } unobserve() { return undefined; } disconnect() { return undefined; } });

const context: AnalyticsContextRequest = {
  datasets: [{ dataset_id: 20, version_no: 1 }],
  filters: { lot_ids: ["LOT-A"], wafer_ids: ["W1"], bin_codes: ["1"], overall_results: ["FAIL"], source_ids: ["SRC-1"], tester_ids: ["T-1"], program_versions: ["P-1"], test_conditions: ["C-1"] },
  parameters: ["VTH"],
};
const ruleContext = { spec_versions: ["SPEC:7"], bin_mapping_versions: ["BIN:2"], evaluation_rule_versions: ["RULE:3"] };
const currentRecord: SavedAnalysisRecord = {
  saved_analysis_id: 41, analysis_name: "VTH fail map", owner_user_id: 7, lifecycle_status: "ACTIVE", current_revision_no: 2, row_version: "00000000000000AF", restore_status: "CURRENT",
  revision: {
    saved_analysis_revision_id: 82, revision_no: 2, contract_version: "SAVED_ANALYSIS_V1", filters: context.filters, parameters: context.parameters,
    filter_hash: "a".repeat(64), context_hash: "b".repeat(64), rule_context: ruleContext, chart_config: {}, display_config: { section: "spatial", page: 1, page_size: 50, focus_dataset_id: 20 },
    datasets: [{ dataset_version_id: 201, dataset_id: 20, version_no: 1, ordinal_no: 1, test_stage: "CP", status: "CURRENT" }], created_by_user_id: 7, created_at_utc: "2026-08-31T00:00:00Z",
  },
  created_at_utc: "2026-08-31T00:00:00Z", updated_at_utc: "2026-08-31T01:00:00Z",
};
const ruleChangedRecord: SavedAnalysisRecord = { ...currentRecord, saved_analysis_id: 42, analysis_name: "Old rules", restore_status: "RULE_CHANGED", row_version: "00000000000000B0" };

function renderPanel(onRestore = vi.fn(), permissions = ["DATASET_READ", "ANALYSIS_RUN"]) {
  vi.mocked(useAuth).mockReturnValue({
    user: { user_id: 7, login_name: "analyst", display_name: "Analyst", department_code: null, roles: ["ANALYST"], permissions },
    loading: false, login: vi.fn(), logout: vi.fn(), can: (permission) => permissions.includes(permission),
  });
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(<QueryClientProvider client={queryClient}><SavedAnalysesPanel context={context} ruleContext={ruleContext} page={3} pageSize={20} focusDatasetId={20} chartDisplayState={createDefaultAnalysisViewState().display} onRestore={onRestore} /></QueryClientProvider>);
  return onRestore;
}

describe("SavedAnalysesPanel", () => {
  beforeEach(() => {
    vi.mocked(listSavedAnalyses).mockResolvedValue({ items: [currentRecord, ruleChangedRecord], total: 2, page: 1, page_size: 10 });
    vi.mocked(createSavedAnalysis).mockResolvedValue(currentRecord);
    vi.mocked(createSavedAnalysisRevision).mockResolvedValue({ ...currentRecord, current_revision_no: 3, revision: { ...currentRecord.revision, revision_no: 3 } });
    vi.mocked(deleteSavedAnalysis).mockResolvedValue({ ...currentRecord, lifecycle_status: "DELETED" });
  });
  afterEach(() => { cleanup(); vi.clearAllMocks(); });

  it("lists restore gates, restores only CURRENT, and creates a complete frozen Context", async () => {
    const onRestore = renderPanel();
    expect(await screen.findByText("VTH fail map")).toBeInTheDocument();
    expect(screen.getByText("规则已变化")).toBeInTheDocument();
    const restoreButtons = screen.getAllByRole("button", { name: /恢\s*复/ });
    expect(restoreButtons[0]).toBeEnabled();
    expect(restoreButtons[1]).toBeDisabled();
    fireEvent.click(restoreButtons[0]);
    expect(onRestore).toHaveBeenCalledWith(currentRecord);

    fireEvent.change(screen.getByRole("textbox", { name: "Saved Analysis 名称" }), { target: { value: "Current VTH view" } });
    fireEvent.change(screen.getByRole("textbox", { name: "Saved Analysis 变更原因" }), { target: { value: "Create reviewed snapshot" } });
    fireEvent.click(screen.getByRole("button", { name: /保存当前图表组合/ }));

    await waitFor(() => expect(createSavedAnalysis).toHaveBeenCalledWith({
      ...context,
      contract_version: "SAVED_ANALYSIS_V1",
      rule_context: ruleContext,
      chart_config: expect.objectContaining({
        y_axis_min: null, y_axis_max: null, color_min: null, color_max: null,
        correlation_min_abs: 0,
        brush_enabled: true, show_spec_overlay: true, spatial_layer_mode: "STACK", visible_wafer_keys: [],
        analysis_view_state: expect.objectContaining({ contract_version: "ANALYSIS_VIEW_STATE_V1", components: expect.any(Object) }),
      }),
      display_config: { section: "overview", page: 3, page_size: 20, focus_dataset_id: 20 },
      analysis_name: "Current VTH view",
      change_reason: "Create reviewed snapshot",
    }));
  }, 20_000);

  it("creates an optimistic revision with the selected row version", async () => {
    renderPanel();
    expect(await screen.findByText("VTH fail map")).toBeInTheDocument();
    fireEvent.click(screen.getAllByRole("button", { name: /管理/ })[0]);
    fireEvent.change(screen.getByRole("textbox", { name: "Revision 变更原因" }), { target: { value: "Refresh approved Context" } });
    fireEvent.click(screen.getByRole("button", { name: /以当前 Context 新建 Revision/ }));

    await waitFor(() => expect(createSavedAnalysisRevision).toHaveBeenCalledWith(41, expect.objectContaining({
      expected_row_version: "00000000000000AF",
      analysis_name: "VTH fail map",
      change_reason: "Refresh approved Context",
      contract_version: "SAVED_ANALYSIS_V1",
      display_config: { section: "overview", page: 3, page_size: 20, focus_dataset_id: 20 },
    })));
  }, 20_000);

  it("keeps read-only roles away from write controls", async () => {
    renderPanel(vi.fn(), ["DATASET_READ"]);
    expect(await screen.findByText("只读模式")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /保存当前图表组合/ })).not.toBeInTheDocument();
    expect(createSavedAnalysis).not.toHaveBeenCalled();
  });
});
