import { apiRequest } from "./auth";

export type AnalysisRuleType = "PAT" | "SBL" | "SYL" | "CPK" | "SPC" | "HISTOGRAM" | "BOX_PLOT" | "NORMAL_FIT" | "CORRELATION" | "MARGIN" | "ZONE" | "BIN_COOCCURRENCE" | "PASS_FAIL_DISTRIBUTION";
export type AnalysisAlgorithmCode = "TUKEY_BOX_V1" | "EQUAL_WIDTH_HISTOGRAM_V1" | "NORMAL_FIT_MLE_V1" | "PEARSON_PAIRWISE_V1" | "SPEARMAN_PAIRWISE_V1" | "CPK_POOLED_WITHIN_RUN_V1" | "CPK_POOLED_WITHIN_LOT_WAFER_V1" | "PAT_SHARED_IQR_1_35_V1" | "SBL_GROUPED_LIMIT_V1" | "SYL_GROUPED_LIMIT_V1" | "SPC_I_MR_V1" | "SPEC_MARGIN_V1" | "WAFER_ZONE_GEOMETRY_V1" | "WAFER_ZONE_GEOMETRY_V2" | "BIN_COOCCURRENCE_UNIT_V1" | "PASS_FAIL_DISTRIBUTION_V1";
export type MissingValuePolicy = "EXCLUDE_AND_COUNT" | "PAIRWISE_EXCLUDE_AND_COUNT" | "FAIL_IF_ANY";
export type RetestPolicy = "EACH_ATTEMPT" | "LATEST_ATTEMPT" | "FIRST_ATTEMPT";
export type OutlierPolicy = "MARK_ONLY" | "EXCLUDE_WITH_AUDIT";
export type SigmaDefinition = "SAMPLE" | "POPULATION" | "POOLED_WITHIN";
export type LimitRoundingPolicy = "NONE" | "FLOOR_TO_STEP" | "CEILING_TO_STEP";
export type SpcRunRuleMode = "NONE" | "BASIC";
export type CapabilityRiskMetric = "CPK" | "PPK" | "MIN_CPK_PPK";
export type RuleApprovalRole = "BUSINESS" | "TECHNICAL" | "QUALITY";
export type RuleApprovalDecision = "APPROVED" | "REJECTED" | "REVOKED";

export interface AnalysisRuleSetRecord {
  evaluation_rule_set_id: number;
  rule_code: string;
  rule_name: string;
  evaluation_type: AnalysisRuleType;
  business_owner_user_id: number;
  technical_owner_user_id: number;
  quality_validator_user_id: number;
  active: boolean;
}

export interface AnalysisRuleVersionRecord {
  evaluation_rule_version_id: number;
  evaluation_rule_set_id: number;
  rule_code: string;
  version_code: string;
  implementation_version: string;
  status: string;
  activation_status: string;
  algorithm_code: AnalysisAlgorithmCode;
  approvals: string[];
}

export interface AnalysisRuleActivationRecord {
  rule_activation_id: number;
  evaluation_rule_version_id: number;
  test_stage: "CP" | "FT";
  supplier_id: number | null;
  product_id: number | null;
  parameter_pattern: string | null;
  active: boolean;
}

export interface CreateAnalysisRuleSetRequest {
  rule_code: string;
  rule_name: string;
  evaluation_type: AnalysisRuleType;
  business_owner_user_id: number;
  technical_owner_user_id: number;
  quality_validator_user_id: number;
  description: string;
}

export interface AnalysisRuleParameters {
  missing_value_policy: MissingValuePolicy;
  retest_policy: RetestPolicy;
  outlier_policy: OutlierPolicy;
  minimum_sample_size: number;
  histogram_bin_count?: number | null;
  whisker_multiplier?: number | null;
  sigma_definition?: SigmaDefinition | null;
  subgroup_dimension?: string | null;
  lower_multiplier?: number | null;
  upper_multiplier?: number | null;
  zone_layout_center_x?: number | null;
  zone_layout_center_y?: number | null;
  zone_layout_radius_die?: number | null;
  zone_center_ratio?: number | null;
  zone_mid_ratio?: number | null;
  quadrant_axis_rotation_degrees?: number | null;
  quadrant_y_direction?: "UP" | "DOWN" | null;
  quadrant_labels_ccw?: string[] | null;
  equality_is_in_spec?: boolean | null;
  sparse_matrix_minimum_count?: number | null;
  limit_rounding_policy?: LimitRoundingPolicy | null;
  limit_rounding_step?: number | null;
  spc_run_rule_mode?: SpcRunRuleMode | null;
  spc_consecutive_beyond_count?: number | null;
  spc_consecutive_beyond_sigma?: number | null;
  spc_same_side_run_length?: number | null;
  spc_monotonic_run_length?: number | null;
  capability_risk_metric?: CapabilityRiskMetric | null;
  capability_risk_threshold?: number | null;
}

export interface AnalysisRuleApplicability {
  test_stages: Array<"CP" | "FT">;
  supplier_ids: number[];
  product_ids: number[];
  parameter_patterns: string[];
}

export interface CreateAnalysisRuleVersionRequest {
  version_code: string;
  implementation_version: string;
  algorithm_code: AnalysisAlgorithmCode;
  parameters: AnalysisRuleParameters;
  applicability: AnalysisRuleApplicability;
  algorithm_sha256: string;
  golden_manifest_sha256: string;
  effective_from_utc?: string | null;
  effective_to_utc?: string | null;
  supersedes_rule_version_id?: number | null;
}

export interface DecideAnalysisRuleRequest {
  approval_role: RuleApprovalRole;
  decision: RuleApprovalDecision;
  decision_note: string;
  golden_manifest_sha256?: string | null;
}

export interface ActivateAnalysisRuleRequest {
  confirmation: "ACTIVATE";
  test_stage: "CP" | "FT";
  supplier_id?: number | null;
  product_id?: number | null;
  parameter_pattern?: string | null;
  effective_from_utc?: string | null;
  effective_to_utc?: string | null;
}

const basePath = "/api/v1/analysis-rules";

export const listAnalysisRules = (): Promise<AnalysisRuleSetRecord[]> => apiRequest(basePath);
export const createAnalysisRule = (request: CreateAnalysisRuleSetRequest): Promise<AnalysisRuleSetRecord> => apiRequest(basePath, { method: "POST", body: JSON.stringify(request) });
export const listAnalysisRuleVersions = (ruleCode: string): Promise<AnalysisRuleVersionRecord[]> => apiRequest(`${basePath}/${encodeURIComponent(ruleCode)}/versions`);
export const createAnalysisRuleVersion = (ruleCode: string, request: CreateAnalysisRuleVersionRequest): Promise<AnalysisRuleVersionRecord> => apiRequest(`${basePath}/${encodeURIComponent(ruleCode)}/versions`, { method: "POST", body: JSON.stringify(request) });
export const decideAnalysisRuleVersion = (ruleVersionId: number, request: DecideAnalysisRuleRequest): Promise<AnalysisRuleVersionRecord> => apiRequest(`${basePath}/versions/${encodeURIComponent(String(ruleVersionId))}/decisions`, { method: "POST", body: JSON.stringify(request) });
export const activateAnalysisRuleVersion = (ruleVersionId: number, request: ActivateAnalysisRuleRequest): Promise<AnalysisRuleActivationRecord> => apiRequest(`${basePath}/versions/${encodeURIComponent(String(ruleVersionId))}/activations`, { method: "POST", body: JSON.stringify(request) });
