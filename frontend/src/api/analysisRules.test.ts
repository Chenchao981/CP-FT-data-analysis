import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { activateAnalysisRuleVersion, createAnalysisRule, createAnalysisRuleVersion, decideAnalysisRuleVersion, listAnalysisRules, listAnalysisRuleVersions, type CreateAnalysisRuleVersionRequest } from "./analysisRules";

beforeAll(() => {
  vi.stubGlobal("localStorage", { getItem: vi.fn((key: string) => key === "tms_access_token" ? "rule-token" : null), setItem: vi.fn(), removeItem: vi.fn() });
});
afterEach(() => vi.restoreAllMocks());

describe("Analysis Rule Registry API", () => {
  it("uses fixed registry, version, decision and activation routes", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async () => new Response("{}", { status: 200 }));
    const version: CreateAnalysisRuleVersionRequest = {
      version_code: "V1", implementation_version: "analytics-1.0", algorithm_code: "PAT_SHARED_IQR_1_35_V1",
      parameters: { missing_value_policy: "EXCLUDE_AND_COUNT", retest_policy: "EACH_ATTEMPT", outlier_policy: "MARK_ONLY", minimum_sample_size: 30, subgroup_dimension: "LOT", lower_multiplier: 6, upper_multiplier: 6 },
      applicability: { test_stages: ["CP"], supplier_ids: [2], product_ids: [3], parameter_patterns: ["VTH*"] },
      algorithm_sha256: "3564929accfae8af9745d7ed08f42bc7b08503d17373a8e45d6d7a63bff85c34", golden_manifest_sha256: "b".repeat(64),
    };

    await listAnalysisRules();
    await createAnalysisRule({ rule_code: "CP_PAT", rule_name: "CP PAT", evaluation_type: "PAT", business_owner_user_id: 1, technical_owner_user_id: 2, quality_validator_user_id: 3, description: "Approved CP PAT policy" });
    await listAnalysisRuleVersions("CP_PAT");
    await createAnalysisRuleVersion("CP_PAT", version);
    await decideAnalysisRuleVersion(11, { approval_role: "QUALITY", decision: "APPROVED", decision_note: "Golden reconciliation passed", golden_manifest_sha256: "b".repeat(64) });
    await activateAnalysisRuleVersion(11, { confirmation: "ACTIVATE", test_stage: "CP", supplier_id: 2, product_id: 3, parameter_pattern: "VTH*" });

    expect(fetchMock.mock.calls.map((call) => call[0])).toEqual([
      "/api/v1/analysis-rules",
      "/api/v1/analysis-rules",
      "/api/v1/analysis-rules/CP_PAT/versions",
      "/api/v1/analysis-rules/CP_PAT/versions",
      "/api/v1/analysis-rules/versions/11/decisions",
      "/api/v1/analysis-rules/versions/11/activations",
    ]);
    expect(JSON.parse(String(fetchMock.mock.calls[3][1]?.body))).toEqual(version);
    expect(JSON.parse(String(fetchMock.mock.calls[4][1]?.body))).toMatchObject({ approval_role: "QUALITY", golden_manifest_sha256: "b".repeat(64) });
    expect(JSON.parse(String(fetchMock.mock.calls[5][1]?.body))).toMatchObject({ confirmation: "ACTIVATE", test_stage: "CP" });
    expect(new Headers(fetchMock.mock.calls[0][1]?.headers).get("Authorization")).toBe("Bearer rule-token");
  });
});
