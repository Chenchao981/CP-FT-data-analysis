// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  activateAnalysisRuleVersion,
  createAnalysisRule,
  createAnalysisRuleVersion,
  decideAnalysisRuleVersion,
  listAnalysisRules,
  listAnalysisRuleVersions,
  type AnalysisRuleSetRecord,
  type AnalysisRuleVersionRecord,
} from "../../api/analysisRules";
import { AnalysisRuleRegistryPanel } from "./AnalysisRuleRegistryPanel";

vi.mock("../../api/analysisRules", () => ({
  listAnalysisRules: vi.fn(),
  createAnalysisRule: vi.fn(),
  listAnalysisRuleVersions: vi.fn(),
  createAnalysisRuleVersion: vi.fn(),
  decideAnalysisRuleVersion: vi.fn(),
  activateAnalysisRuleVersion: vi.fn(),
}));

Object.defineProperty(window, "matchMedia", { writable: true, value: () => ({ matches: false, addListener: () => undefined, removeListener: () => undefined, addEventListener: () => undefined, removeEventListener: () => undefined }) });
vi.stubGlobal("ResizeObserver", class { observe() { return undefined; } unobserve() { return undefined; } disconnect() { return undefined; } });

const ruleSet: AnalysisRuleSetRecord = {
  evaluation_rule_set_id: 7,
  rule_code: "CP_PAT",
  rule_name: "CP PAT",
  evaluation_type: "PAT",
  business_owner_user_id: 1,
  technical_owner_user_id: 2,
  quality_validator_user_id: 3,
  active: true,
};
const version: AnalysisRuleVersionRecord = {
  evaluation_rule_version_id: 11,
  evaluation_rule_set_id: 7,
  rule_code: "CP_PAT",
  version_code: "V1",
  implementation_version: "analytics-1.0",
  status: "DRAFT",
  activation_status: "DISABLED",
  algorithm_code: "PAT_SHARED_IQR_1_35_V1",
  approvals: [],
};
const zoneRuleSet: AnalysisRuleSetRecord = {
  ...ruleSet,
  evaluation_rule_set_id: 8,
  rule_code: "CP_ZONE",
  rule_name: "CP Zone Geometry",
  evaluation_type: "ZONE",
};
const zoneVersion: AnalysisRuleVersionRecord = {
  ...version,
  evaluation_rule_version_id: 12,
  evaluation_rule_set_id: 8,
  rule_code: "CP_ZONE",
  algorithm_code: "WAFER_ZONE_GEOMETRY_V2",
};

function renderRegistry() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(<QueryClientProvider client={queryClient}><AnalysisRuleRegistryPanel /></QueryClientProvider>);
}

async function select(label: string, option: string) {
  fireEvent.mouseDown(screen.getByRole("combobox", { name: label }));
  fireEvent.click(await screen.findByTitle(option));
}

describe("AnalysisRuleRegistryPanel", () => {
  beforeEach(() => {
    vi.mocked(listAnalysisRules).mockResolvedValue([ruleSet]);
    vi.mocked(listAnalysisRuleVersions).mockResolvedValue([version]);
    vi.mocked(createAnalysisRule).mockResolvedValue(ruleSet);
    vi.mocked(createAnalysisRuleVersion).mockResolvedValue(version);
    vi.mocked(decideAnalysisRuleVersion).mockResolvedValue({ ...version, status: "APPROVED", approvals: ["QUALITY:APPROVED"] });
    vi.mocked(activateAnalysisRuleVersion).mockResolvedValue({ rule_activation_id: 21, evaluation_rule_version_id: 11, test_stage: "CP", supplier_id: null, product_id: null, parameter_pattern: "VTH*", active: true });
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("creates a Rule Set only after three distinct owners are explicit", async () => {
    renderRegistry();
    expect(await screen.findByText("CP PAT")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /新建 Rule Set/ }));
    fireEvent.change(screen.getByLabelText("Rule Set Code"), { target: { value: "CP_PAT_2" } });
    fireEvent.change(screen.getByLabelText("Rule Set Name"), { target: { value: "Second CP PAT" } });
    await select("Rule Evaluation Type", "PAT");
    fireEvent.change(screen.getByRole("spinbutton", { name: "Business Owner ID" }), { target: { value: "1" } });
    fireEvent.change(screen.getByRole("spinbutton", { name: "Technical Owner ID" }), { target: { value: "2" } });
    fireEvent.change(screen.getByRole("spinbutton", { name: "Quality Validator ID" }), { target: { value: "2" } });
    fireEvent.change(screen.getByLabelText("Rule Set Description"), { target: { value: "Explicit governed PAT policy" } });
    fireEvent.click(screen.getByRole("button", { name: /创建 Rule Set/ }));

    expect(await screen.findByText(/必须是三个不同用户/)).toBeInTheDocument();
    expect(createAnalysisRule).not.toHaveBeenCalled();

    fireEvent.change(screen.getByRole("spinbutton", { name: "Quality Validator ID" }), { target: { value: "3" } });
    fireEvent.click(screen.getByRole("button", { name: /创建 Rule Set/ }));
    await waitFor(() => expect(createAnalysisRule).toHaveBeenCalledWith({
      rule_code: "CP_PAT_2",
      rule_name: "Second CP PAT",
      evaluation_type: "PAT",
      business_owner_user_id: 1,
      technical_owner_user_id: 2,
      quality_validator_user_id: 3,
      description: "Explicit governed PAT policy",
    }));
  }, 30_000);

  it("creates an explicit DRAFT version without silently filling Golden or rule parameters", async () => {
    renderRegistry();
    await screen.findByText("CP PAT");
    fireEvent.click(screen.getByRole("button", { name: "查看版本" }));
    expect(await screen.findByText("DRAFT")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /新建 Version/ }));

    fireEvent.change(screen.getByLabelText("Rule Version Code"), { target: { value: "V2" } });
    fireEvent.change(screen.getByLabelText("Rule Implementation Version"), { target: { value: "analytics-1.1" } });
    await select("Rule Algorithm", "PAT_SHARED_IQR_1_35_V1");
    await select("Missing Value Policy", "EXCLUDE_AND_COUNT");
    await select("Retest Policy", "EACH_ATTEMPT");
    await select("Outlier Policy", "MARK_ONLY");
    fireEvent.change(screen.getByRole("spinbutton", { name: "Minimum Sample Size" }), { target: { value: "30" } });
    await select("Subgroup Dimension", "LOT");
    expect(screen.getByRole("spinbutton", { name: "Lower Multiplier" })).toHaveValue("6");
    expect(screen.getByRole("spinbutton", { name: "Upper Multiplier" })).toHaveValue("6");
    await select("Rule Test Stages", "CP");
    fireEvent.change(screen.getByLabelText("Rule Supplier IDs"), { target: { value: "2" } });
    fireEvent.change(screen.getByLabelText("Rule Product IDs"), { target: { value: "3" } });
    fireEvent.change(screen.getByLabelText("Rule Parameter Patterns"), { target: { value: "VTH*" } });
    fireEvent.change(screen.getByLabelText("Algorithm SHA-256"), { target: { value: "3564929accfae8af9745d7ed08f42bc7b08503d17373a8e45d6d7a63bff85c34" } });
    const goldenInput = screen.getByLabelText("Golden Manifest SHA-256");
    expect(goldenInput).toHaveValue("");
    fireEvent.change(goldenInput, { target: { value: "b".repeat(64) } });
    fireEvent.click(screen.getByRole("button", { name: /创建 Version/ }));

    await waitFor(() => expect(createAnalysisRuleVersion).toHaveBeenCalledWith("CP_PAT", {
      version_code: "V2",
      implementation_version: "analytics-1.1",
      algorithm_code: "PAT_SHARED_IQR_1_35_V1",
      parameters: {
        missing_value_policy: "EXCLUDE_AND_COUNT",
        retest_policy: "EACH_ATTEMPT",
        outlier_policy: "MARK_ONLY",
        minimum_sample_size: 30,
        subgroup_dimension: "LOT",
        lower_multiplier: 6,
        upper_multiplier: 6,
      },
      applicability: { test_stages: ["CP"], supplier_ids: [2], product_ids: [3], parameter_patterns: ["VTH*"] },
      algorithm_sha256: "3564929accfae8af9745d7ed08f42bc7b08503d17373a8e45d6d7a63bff85c34",
      golden_manifest_sha256: "b".repeat(64),
      effective_from_utc: null,
      effective_to_utc: null,
      supersedes_rule_version_id: null,
    }));
  }, 45_000);

  it("exposes all V2 quadrant semantics as blank Owner-entered fields", async () => {
    vi.mocked(listAnalysisRules).mockResolvedValue([zoneRuleSet]);
    vi.mocked(listAnalysisRuleVersions).mockResolvedValue([zoneVersion]);
    vi.mocked(createAnalysisRuleVersion).mockResolvedValue(zoneVersion);
    renderRegistry();
    await screen.findByText("CP Zone Geometry");
    fireEvent.click(screen.getByRole("button", { name: "查看版本" }));
    fireEvent.click(screen.getByRole("button", { name: /新建 Version/ }));

    await select("Rule Algorithm", "WAFER_ZONE_GEOMETRY_V2");
    expect(screen.getByRole("spinbutton", { name: "Quadrant Axis Rotation" })).toHaveValue("");
    expect(screen.getByRole("combobox", { name: "Quadrant Y Direction" })).not.toHaveTextContent("UP");
    expect(screen.getByRole("combobox", { name: "Quadrant Y Direction" })).not.toHaveTextContent("DOWN");
    expect(screen.getByLabelText("Quadrant Labels CCW")).toHaveValue("");
    fireEvent.click(screen.getByRole("button", { name: /创建 Version/ }));
    expect(createAnalysisRuleVersion).not.toHaveBeenCalled();
  }, 45_000);

  it("shows DRAFT/DISABLED and keeps Quality approval and Activation as separate manual actions", async () => {
    renderRegistry();
    await screen.findByText("CP PAT");
    fireEvent.click(screen.getByRole("button", { name: "查看版本" }));
    expect(await screen.findByText("DRAFT")).toBeInTheDocument();
    expect(screen.getByText("DISABLED")).toBeInTheDocument();
    expect(screen.getByText("尚无决策")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "审批 / 激活" }));

    expect(screen.queryByLabelText("Decision Golden SHA-256")).not.toBeInTheDocument();
    await select("Decision Approval Role", "QUALITY");
    await select("Rule Decision", "APPROVED");
    const decisionGolden = await screen.findByLabelText("Decision Golden SHA-256");
    expect(decisionGolden).toHaveValue("");
    fireEvent.change(screen.getByLabelText("Decision Note"), { target: { value: "Golden comparison reviewed" } });
    fireEvent.change(decisionGolden, { target: { value: "b".repeat(64) } });
    fireEvent.click(screen.getByRole("button", { name: /提交单次决策/ }));
    await waitFor(() => expect(decideAnalysisRuleVersion).toHaveBeenCalledWith(11, {
      approval_role: "QUALITY",
      decision: "APPROVED",
      decision_note: "Golden comparison reviewed",
      golden_manifest_sha256: "b".repeat(64),
    }));

    const confirmation = screen.getByLabelText("Activation Confirmation");
    expect(confirmation).toHaveValue("");
    fireEvent.change(confirmation, { target: { value: "ACTIVATE" } });
    await select("Activation Test Stage", "CP");
    fireEvent.change(screen.getByLabelText("Activation Parameter Pattern"), { target: { value: "VTH*" } });
    fireEvent.click(screen.getByRole("button", { name: /创建 Activation/ }));
    await waitFor(() => expect(activateAnalysisRuleVersion).toHaveBeenCalledWith(11, {
      confirmation: "ACTIVATE",
      test_stage: "CP",
      supplier_id: null,
      product_id: null,
      parameter_pattern: "VTH*",
      effective_from_utc: null,
      effective_to_utc: null,
    }));
  }, 45_000);
});
