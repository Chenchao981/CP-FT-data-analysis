import {
  ANALYSIS_COMPONENT_DEFAULTS,
  ANALYSIS_CONFIG_QUERY_KEYS,
  ANALYSIS_VIEW_CONTRACT_VERSION,
  normalizeAnalysisComponentState,
  parseAnalysisComponentState,
  serializeAnalysisComponentState,
  type AnalysisComponentState,
} from "./analysisViewConfig";

export const ANALYSIS_SECTIONS = [
  "overview",
  "detail",
  "parameter",
  "spatial",
  "quality",
  "delivery",
] as const;

export type AnalysisSection = (typeof ANALYSIS_SECTIONS)[number];

export const ANALYSIS_OVERALL_RESULTS = [
  "PASS",
  "FAIL",
  "UNKNOWN",
  "ABORT",
] as const;

export type AnalysisOverallResult = (typeof ANALYSIS_OVERALL_RESULTS)[number];

export const ANALYSIS_VIEW_FILTER_LIMITS = Object.freeze({
  lotIds: 50,
  waferIds: 100,
  binCodes: 50,
  overallResults: 4,
  sourceIds: 50,
  testerIds: 50,
  programVersions: 50,
  testConditions: 50,
  parameters: 20,
});

export const ANALYSIS_VIEW_DEFAULTS = Object.freeze({
  section: "overview" as AnalysisSection,
  page: 1,
  pageSize: 50,
  yAxisMin: null as number | null,
  yAxisMax: null as number | null,
  colorMin: null as number | null,
  colorMax: null as number | null,
  correlationMinAbs: 0,
  brushEnabled: true,
  showSpecOverlay: true,
  spatialLayerMode: "STACK" as "STACK" | "OVERLAY",
  visibleWaferKeys: [] as readonly string[],
});

const MAX_FILTER_VALUE_LENGTH = 200;
const MAX_PAGE_SIZE = 200;
const MAX_ABSOLUTE_DISPLAY_VALUE = 1e15;
const MAX_VISIBLE_WAFERS = 100;
const MAX_WAFER_KEY_LENGTH = 512;

export interface AnalysisAuthorityFilters {
  readonly lotIds: readonly string[];
  readonly waferIds: readonly string[];
  readonly binCodes: readonly string[];
  readonly overallResults: readonly AnalysisOverallResult[];
  readonly sourceIds: readonly string[];
  readonly testerIds: readonly string[];
  readonly programVersions: readonly string[];
  readonly testConditions: readonly string[];
  readonly parameters: readonly string[];
}

export interface AnalysisDisplayState {
  readonly section: AnalysisSection;
  readonly page: number;
  readonly pageSize: number;
  readonly yAxisMin: number | null;
  readonly yAxisMax: number | null;
  readonly colorMin: number | null;
  readonly colorMax: number | null;
  readonly correlationMinAbs: number;
  readonly brushEnabled: boolean;
  readonly showSpecOverlay: boolean;
  readonly spatialLayerMode: "STACK" | "OVERLAY";
  readonly visibleWaferKeys: readonly string[];
}

/**
 * Dataset selection and the active detail Dataset intentionally live outside
 * this state. They remain independently addressable while every analysis
 * section shares one authoritative filter state.
 */
export interface AnalysisViewState {
  readonly contractVersion: typeof ANALYSIS_VIEW_CONTRACT_VERSION;
  readonly filters: AnalysisAuthorityFilters;
  readonly display: AnalysisDisplayState;
  readonly analysis: AnalysisComponentState;
  readonly warnings: readonly string[];
}

export const ANALYSIS_VIEW_OWNED_QUERY_KEYS = [
  "lot_id",
  "wafer_id",
  "bin_code",
  "overall_result",
  "source_id",
  "tester_id",
  "program_version",
  "test_condition",
  "parameter",
  "section",
  "page",
  "page_size",
  "chart_y_min",
  "chart_y_max",
  "chart_color_min",
  "chart_color_max",
  "chart_corr_min_abs",
  "chart_brush",
  "chart_spec_overlay",
  "spatial_layer",
  "visible_wafer",
  ...ANALYSIS_CONFIG_QUERY_KEYS,
] as const;

const compareOrdinal = (left: string, right: string) => left < right ? -1 : left > right ? 1 : 0;

function normalizeTextValues(values: Iterable<string>, limit: number): string[] {
  const unique = new Set<string>();
  for (const rawValue of values) {
    const value = rawValue.trim();
    if (!value || value.length > MAX_FILTER_VALUE_LENGTH) continue;
    unique.add(value);
  }
  return Array.from(unique).sort(compareOrdinal).slice(0, limit);
}

function normalizeOverallResults(values: Iterable<string>): AnalysisOverallResult[] {
  const selected = new Set<string>();
  for (const value of values) selected.add(value.trim());
  return ANALYSIS_OVERALL_RESULTS.filter((value) => selected.has(value));
}

function isAnalysisSection(value: string | null): value is AnalysisSection {
  return value !== null && (ANALYSIS_SECTIONS as readonly string[]).includes(value);
}

function parseStrictPositiveInteger(value: string | null, fallback: number, maximum: number): number {
  if (value === null || !/^[1-9]\d*$/.test(value)) return fallback;
  const parsed = Number(value);
  return Number.isSafeInteger(parsed) && parsed <= maximum ? parsed : fallback;
}

function normalizePositiveInteger(value: number, fallback: number, maximum: number): number {
  return Number.isSafeInteger(value) && value > 0 && value <= maximum ? value : fallback;
}

function parseDisplayNumber(value: string | null): number | null {
  if (value === null || !/^-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?$/.test(value)) return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) && Math.abs(parsed) <= MAX_ABSOLUTE_DISPLAY_VALUE ? parsed : null;
}

function normalizeDisplayNumber(value: number | null): number | null {
  return typeof value === "number" && Number.isFinite(value) && Math.abs(value) <= MAX_ABSOLUTE_DISPLAY_VALUE ? value : null;
}

function normalizeCorrelationThreshold(value: number | null): number {
  return value !== null && value >= 0 && value <= 1 ? value : ANALYSIS_VIEW_DEFAULTS.correlationMinAbs;
}

function normalizeRange(minimum: number | null, maximum: number | null): [number | null, number | null] {
  const min = normalizeDisplayNumber(minimum);
  const max = normalizeDisplayNumber(maximum);
  return min !== null && max !== null && min >= max ? [null, null] : [min, max];
}

function parseBooleanFlag(value: string | null, fallback: boolean): boolean {
  return value === "1" ? true : value === "0" ? false : fallback;
}

function normalizeWaferKeys(values: Iterable<string>): string[] {
  const unique = new Set<string>();
  for (const raw of values) {
    const value = raw.trim();
    if (value && value.length <= MAX_WAFER_KEY_LENGTH) unique.add(value);
  }
  return Array.from(unique).sort(compareOrdinal).slice(0, MAX_VISIBLE_WAFERS);
}

export function createDefaultAnalysisViewState(): AnalysisViewState {
  return {
    contractVersion: ANALYSIS_VIEW_CONTRACT_VERSION,
    filters: {
      lotIds: [],
      waferIds: [],
      binCodes: [],
      overallResults: [],
      sourceIds: [],
      testerIds: [],
      programVersions: [],
      testConditions: [],
      parameters: [],
    },
    display: { ...ANALYSIS_VIEW_DEFAULTS, visibleWaferKeys: [] },
    analysis: normalizeAnalysisComponentState(ANALYSIS_COMPONENT_DEFAULTS),
    warnings: [],
  };
}

export function normalizeAnalysisViewState(state: AnalysisViewState): AnalysisViewState {
  const warnings = new Set((state.warnings ?? []).filter((warning) => typeof warning === "string" && /^ANALYSIS_VIEW_[A-Z0-9_]{1,100}$/.test(warning)).slice(0, 20));
  const [yAxisMin, yAxisMax] = normalizeRange(state.display.yAxisMin, state.display.yAxisMax);
  const [colorMin, colorMax] = normalizeRange(state.display.colorMin, state.display.colorMax);
  return {
    contractVersion: ANALYSIS_VIEW_CONTRACT_VERSION,
    filters: {
      lotIds: normalizeTextValues(state.filters.lotIds, ANALYSIS_VIEW_FILTER_LIMITS.lotIds),
      waferIds: normalizeTextValues(state.filters.waferIds, ANALYSIS_VIEW_FILTER_LIMITS.waferIds),
      binCodes: normalizeTextValues(state.filters.binCodes, ANALYSIS_VIEW_FILTER_LIMITS.binCodes),
      overallResults: normalizeOverallResults(state.filters.overallResults),
      sourceIds: normalizeTextValues(state.filters.sourceIds, ANALYSIS_VIEW_FILTER_LIMITS.sourceIds),
      testerIds: normalizeTextValues(state.filters.testerIds, ANALYSIS_VIEW_FILTER_LIMITS.testerIds),
      programVersions: normalizeTextValues(state.filters.programVersions, ANALYSIS_VIEW_FILTER_LIMITS.programVersions),
      testConditions: normalizeTextValues(state.filters.testConditions, ANALYSIS_VIEW_FILTER_LIMITS.testConditions),
      parameters: normalizeTextValues(state.filters.parameters, ANALYSIS_VIEW_FILTER_LIMITS.parameters),
    },
    display: {
      section: isAnalysisSection(state.display.section) ? state.display.section : ANALYSIS_VIEW_DEFAULTS.section,
      page: normalizePositiveInteger(state.display.page, ANALYSIS_VIEW_DEFAULTS.page, Number.MAX_SAFE_INTEGER),
      pageSize: normalizePositiveInteger(state.display.pageSize, ANALYSIS_VIEW_DEFAULTS.pageSize, MAX_PAGE_SIZE),
      yAxisMin,
      yAxisMax,
      colorMin,
      colorMax,
      correlationMinAbs: normalizeCorrelationThreshold(state.display.correlationMinAbs),
      brushEnabled: state.display.brushEnabled !== false,
      showSpecOverlay: state.display.showSpecOverlay !== false,
      spatialLayerMode: state.display.spatialLayerMode === "OVERLAY" ? "OVERLAY" : "STACK",
      visibleWaferKeys: normalizeWaferKeys(state.display.visibleWaferKeys),
    },
    analysis: normalizeAnalysisComponentState(state.analysis ?? ANALYSIS_COMPONENT_DEFAULTS, warnings),
    warnings: Array.from(warnings).sort(compareOrdinal).slice(0, 20),
  };
}

export function parseAnalysisViewState(params: URLSearchParams): AnalysisViewState {
  const warnings = new Set<string>(params.getAll("view_warning")
    .filter((warning) => /^ANALYSIS_VIEW_[A-Z0-9_]{1,100}$/.test(warning))
    .slice(0, 20));
  const [yAxisMin, yAxisMax] = normalizeRange(parseDisplayNumber(params.get("chart_y_min")), parseDisplayNumber(params.get("chart_y_max")));
  const [colorMin, colorMax] = normalizeRange(parseDisplayNumber(params.get("chart_color_min")), parseDisplayNumber(params.get("chart_color_max")));
  return {
    contractVersion: ANALYSIS_VIEW_CONTRACT_VERSION,
    filters: {
      lotIds: normalizeTextValues(params.getAll("lot_id"), ANALYSIS_VIEW_FILTER_LIMITS.lotIds),
      waferIds: normalizeTextValues(params.getAll("wafer_id"), ANALYSIS_VIEW_FILTER_LIMITS.waferIds),
      binCodes: normalizeTextValues(params.getAll("bin_code"), ANALYSIS_VIEW_FILTER_LIMITS.binCodes),
      overallResults: normalizeOverallResults(params.getAll("overall_result")),
      sourceIds: normalizeTextValues(params.getAll("source_id"), ANALYSIS_VIEW_FILTER_LIMITS.sourceIds),
      testerIds: normalizeTextValues(params.getAll("tester_id"), ANALYSIS_VIEW_FILTER_LIMITS.testerIds),
      programVersions: normalizeTextValues(params.getAll("program_version"), ANALYSIS_VIEW_FILTER_LIMITS.programVersions),
      testConditions: normalizeTextValues(params.getAll("test_condition"), ANALYSIS_VIEW_FILTER_LIMITS.testConditions),
      parameters: normalizeTextValues(params.getAll("parameter"), ANALYSIS_VIEW_FILTER_LIMITS.parameters),
    },
    display: {
      section: isAnalysisSection(params.get("section")) ? params.get("section") as AnalysisSection : ANALYSIS_VIEW_DEFAULTS.section,
      page: parseStrictPositiveInteger(params.get("page"), ANALYSIS_VIEW_DEFAULTS.page, Number.MAX_SAFE_INTEGER),
      pageSize: parseStrictPositiveInteger(params.get("page_size"), ANALYSIS_VIEW_DEFAULTS.pageSize, MAX_PAGE_SIZE),
      yAxisMin,
      yAxisMax,
      colorMin,
      colorMax,
      correlationMinAbs: normalizeCorrelationThreshold(parseDisplayNumber(params.get("chart_corr_min_abs"))),
      brushEnabled: parseBooleanFlag(params.get("chart_brush"), ANALYSIS_VIEW_DEFAULTS.brushEnabled),
      showSpecOverlay: parseBooleanFlag(params.get("chart_spec_overlay"), ANALYSIS_VIEW_DEFAULTS.showSpecOverlay),
      spatialLayerMode: params.get("spatial_layer") === "OVERLAY" ? "OVERLAY" : "STACK",
      visibleWaferKeys: normalizeWaferKeys(params.getAll("visible_wafer")),
    },
    analysis: parseAnalysisComponentState(params, warnings),
    warnings: Array.from(warnings).sort(compareOrdinal).slice(0, 20),
  };
}

function appendValues(params: URLSearchParams, key: string, values: readonly string[]): void {
  for (const value of values) params.append(key, value);
}

/**
 * Replaces only the query keys owned by AnalysisViewState. Dataset selections,
 * the active detail Dataset, Job drawers and future unrelated URL state are
 * preserved verbatim in the supplied base params.
 */
export function serializeAnalysisViewState(
  state: AnalysisViewState,
  baseParams: URLSearchParams = new URLSearchParams(),
): URLSearchParams {
  const normalized = normalizeAnalysisViewState(state);
  const params = new URLSearchParams(baseParams);
  for (const key of ANALYSIS_VIEW_OWNED_QUERY_KEYS) params.delete(key);

  appendValues(params, "lot_id", normalized.filters.lotIds);
  appendValues(params, "wafer_id", normalized.filters.waferIds);
  appendValues(params, "bin_code", normalized.filters.binCodes);
  appendValues(params, "overall_result", normalized.filters.overallResults);
  appendValues(params, "source_id", normalized.filters.sourceIds);
  appendValues(params, "tester_id", normalized.filters.testerIds);
  appendValues(params, "program_version", normalized.filters.programVersions);
  appendValues(params, "test_condition", normalized.filters.testConditions);
  appendValues(params, "parameter", normalized.filters.parameters);

  if (normalized.display.section !== ANALYSIS_VIEW_DEFAULTS.section) {
    params.set("section", normalized.display.section);
  }
  if (normalized.display.page !== ANALYSIS_VIEW_DEFAULTS.page) {
    params.set("page", String(normalized.display.page));
  }
  if (normalized.display.pageSize !== ANALYSIS_VIEW_DEFAULTS.pageSize) {
    params.set("page_size", String(normalized.display.pageSize));
  }
  if (normalized.display.yAxisMin !== null) params.set("chart_y_min", String(normalized.display.yAxisMin));
  if (normalized.display.yAxisMax !== null) params.set("chart_y_max", String(normalized.display.yAxisMax));
  if (normalized.display.colorMin !== null) params.set("chart_color_min", String(normalized.display.colorMin));
  if (normalized.display.colorMax !== null) params.set("chart_color_max", String(normalized.display.colorMax));
  if (normalized.display.correlationMinAbs !== ANALYSIS_VIEW_DEFAULTS.correlationMinAbs) {
    params.set("chart_corr_min_abs", String(normalized.display.correlationMinAbs));
  }
  if (!normalized.display.brushEnabled) params.set("chart_brush", "0");
  if (!normalized.display.showSpecOverlay) params.set("chart_spec_overlay", "0");
  if (normalized.display.spatialLayerMode !== ANALYSIS_VIEW_DEFAULTS.spatialLayerMode) params.set("spatial_layer", normalized.display.spatialLayerMode);
  appendValues(params, "visible_wafer", normalized.display.visibleWaferKeys);
  serializeAnalysisComponentState(normalized.analysis, params);
  appendValues(params, "view_warning", normalized.warnings);
  return params;
}
