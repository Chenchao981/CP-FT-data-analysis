import type { SavedAnalysisRecord } from "../../api/savedAnalyses";
import {
  ANALYSIS_VIEW_CONTRACT_VERSION,
  restorePersistedAnalysisComponentState,
} from "./context/analysisViewConfig";
import {
  ANALYSIS_SECTIONS,
  createDefaultAnalysisViewState,
  serializeAnalysisViewState,
  type AnalysisOverallResult,
  type AnalysisSection,
  type AnalysisViewState,
} from "./context/analysisViewState";

const isSection = (value: unknown): value is AnalysisSection => typeof value === "string"
  && (ANALYSIS_SECTIONS as readonly string[]).includes(value);
const positiveNumber = (value: unknown, fallback: number) => typeof value === "number" && Number.isSafeInteger(value) && value > 0
  ? value
  : fallback;
const displayNumber = (value: unknown): number | null => typeof value === "number" && Number.isFinite(value) && Math.abs(value) <= 1e15
  ? value
  : null;

/**
 * A one-click restore is deliberately fail-closed. NON_CURRENT, RULE_CHANGED
 * and ACCESS_REVOKED records remain visible but cannot silently become a
 * different analysis Context.
 */
export function savedAnalysisRestoreParams(
  record: SavedAnalysisRecord,
  baseParams: URLSearchParams,
): URLSearchParams | null {
  if (record.lifecycle_status !== "ACTIVE" || record.restore_status !== "CURRENT") return null;
  const defaults = createDefaultAnalysisViewState();
  const displayConfig = record.revision.display_config;
  const chartConfig = record.revision.chart_config;
  const restoredAnalysis = restorePersistedAnalysisComponentState(chartConfig.analysis_view_state);
  const warnings = new Set(restoredAnalysis.warnings);
  if (!isSection(displayConfig.section)) warnings.add("ANALYSIS_VIEW_INVALID_SAVED_SECTION");
  const state: AnalysisViewState = {
    contractVersion: ANALYSIS_VIEW_CONTRACT_VERSION,
    filters: {
      lotIds: record.revision.filters.lot_ids,
      waferIds: record.revision.filters.wafer_ids,
      binCodes: record.revision.filters.bin_codes,
      overallResults: record.revision.filters.overall_results as AnalysisOverallResult[],
      sourceIds: record.revision.filters.source_ids,
      testerIds: record.revision.filters.tester_ids,
      programVersions: record.revision.filters.program_versions,
      testConditions: record.revision.filters.test_conditions,
      parameters: record.revision.parameters,
    },
    display: {
      section: isSection(displayConfig.section) ? displayConfig.section : defaults.display.section,
      page: positiveNumber(displayConfig.page, defaults.display.page),
      pageSize: positiveNumber(displayConfig.page_size, defaults.display.pageSize),
      yAxisMin: displayNumber(chartConfig.y_axis_min),
      yAxisMax: displayNumber(chartConfig.y_axis_max),
      colorMin: displayNumber(chartConfig.color_min),
      colorMax: displayNumber(chartConfig.color_max),
      correlationMinAbs: (() => {
        const value = displayNumber(chartConfig.correlation_min_abs);
        return value !== null && value >= 0 && value <= 1 ? value : defaults.display.correlationMinAbs;
      })(),
      brushEnabled: typeof chartConfig.brush_enabled === "boolean" ? chartConfig.brush_enabled : defaults.display.brushEnabled,
      showSpecOverlay: typeof chartConfig.show_spec_overlay === "boolean" ? chartConfig.show_spec_overlay : defaults.display.showSpecOverlay,
      spatialLayerMode: chartConfig.spatial_layer_mode === "OVERLAY" ? "OVERLAY" : "STACK",
      visibleWaferKeys: Array.isArray(chartConfig.visible_wafer_keys)
        ? chartConfig.visible_wafer_keys.filter((item): item is string => typeof item === "string")
        : [],
    },
    analysis: restoredAnalysis.analysis,
    warnings: Array.from(warnings).sort(),
  };
  const next = new URLSearchParams(baseParams);
  next.delete("dataset");
  next.delete("detail_dataset");
  const datasets = [...record.revision.datasets].sort((left, right) => left.ordinal_no - right.ordinal_no);
  for (const dataset of datasets) next.append("dataset", `${dataset.dataset_id}:${dataset.version_no}`);
  const requestedFocus = typeof displayConfig.focus_dataset_id === "number" ? displayConfig.focus_dataset_id : undefined;
  const focus = datasets.find((item) => item.dataset_id === requestedFocus) ?? datasets[0];
  if (focus) next.set("detail_dataset", `${focus.dataset_id}:${focus.version_no}`);
  return serializeAnalysisViewState(state, next);
}
