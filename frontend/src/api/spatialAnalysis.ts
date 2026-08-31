import type {
  AnalyticsCapability,
  AnalyticsContextRequest,
  AnalyticsDatasetContext,
  AnalyticsFilterSummary,
  AnalyticsRuleContext,
} from "./analytics";
import { apiRequest } from "./auth";

export type SpatialAnalysisMode = "BIN_MAP" | "PARAMETER_HEATMAP" | "PARAMETER_FAIL_OVERLAY" | "COMPOSITE_FAILURE" | "ZONE_COMPARISON";

export interface SpatialAnalysisRequest extends AnalyticsContextRequest {
  mode: SpatialAnalysisMode;
  focus_dataset_id?: number | null;
  max_points: number;
  rule_code?: string | null;
  rule_version?: string | null;
}

export interface SpatialColorDomain {
  minimum: number;
  maximum: number;
  p02: number;
  p98: number;
}

export interface SpatialPoint {
  dataset_id: number | null;
  version_no: number | null;
  lot_id: string | null;
  wafer_id: string | null;
  x: number;
  y: number;
  bin_code: string | null;
  result: string | null;
  value: number | null;
  unit: string | null;
  lsl: number | null;
  usl: number | null;
  spec_status: string | null;
  drilldown_key: string | null;
  observed_count: number;
  fail_count: number;
  fail_ratio: number | null;
  wafer_count: number;
  zone?: string | null;
  raw_bin_code?: string | null;
  bin_mapping_set_id?: number | null;
  bin_mapping_version?: string | null;
  bin_name?: string | null;
  failure_mode?: string | null;
  bin_is_pass?: boolean | null;
  spec_set_id?: number | null;
  spec_version?: string | null;
  quadrant?: string | null;
  member_drilldown_keys?: string[];
}

export interface SpatialWaferIdentity {
  key: string;
  dataset_id: number;
  version_no: number;
  lot_id: string;
  wafer_id: string;
}

export interface SpatialZoneSummary {
  zone: string;
  unit_count: number;
  pass_count: number;
  fail_count: number;
  unknown_count: number;
  yield_rate: number | null;
  measured_count: number;
  missing_measurement_count: number;
  mean: number | null;
  minimum: number | null;
  maximum: number | null;
  drilldown_key?: string | null;
  member_drilldown_keys?: string[];
}

export interface SpatialQuadrantSummary {
  quadrant: string;
  unit_count: number;
  pass_count: number;
  fail_count: number;
  unknown_count: number;
  yield_rate: number | null;
  measured_count: number;
  missing_measurement_count: number;
  mean: number | null;
  minimum: number | null;
  maximum: number | null;
  member_drilldown_keys: string[];
}

export interface SpatialZoneGeometry {
  center_x: number;
  center_y: number;
  radius: number;
  center_ratio: number;
  mid_ratio: number;
  quadrant_axis_rotation_degrees: number;
  quadrant_y_direction: "UP" | "DOWN";
  quadrant_labels_ccw: [string, string, string, string];
}

export interface SpatialDataQuality {
  input_units: number;
  returned_points: number;
  wafer_count: number;
  missing_coordinate_count: number;
  duplicate_coordinate_count: number;
  measured_count: number;
  missing_measurement_count: number;
  layer_point_count: number;
}

export interface SpatialAnalysisResult {
  contract_version: string;
  dataset_context: AnalyticsDatasetContext;
  filter_summary: AnalyticsFilterSummary;
  rule_context: AnalyticsRuleContext;
  capabilities: AnalyticsCapability[];
  mode: string;
  parameter: string | null;
  color_domain: SpatialColorDomain | null;
  data_quality: SpatialDataQuality;
  points: SpatialPoint[];
  wafer_manifest: SpatialWaferIdentity[];
  wafer_layers: SpatialPoint[];
  zones: SpatialZoneSummary[];
  warnings: string[];
  computed_at: string;
  zone_geometry?: SpatialZoneGeometry | null;
  quadrants?: SpatialQuadrantSummary[];
}

export function analyzeSpatial(request: SpatialAnalysisRequest): Promise<SpatialAnalysisResult> {
  return apiRequest("/api/v1/analytics/spatial", {
    method: "POST",
    body: JSON.stringify(request),
  });
}
