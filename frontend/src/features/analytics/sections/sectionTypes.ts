import type { AnalyticsContextRequest, AnalyticsEvaluationFilter, AnalyticsMeasurementFilter, AnalyticsShellContextResult } from "../../../api/analytics";

export interface AnalyticsSectionContext {
  context: AnalyticsContextRequest;
  focusDatasetId: number;
  overview: AnalyticsShellContextResult | undefined;
  overviewLoading: boolean;
  overviewError: Error | null;
}

export interface AnalyticsDrilldownOpener {
  onOpenDrilldown: (drilldownKey: string) => void;
}

export interface AnalyticsAggregateDrilldown {
  dataset?: { dataset_id: number; version_no: number };
  filters: Partial<AnalyticsContextRequest["filters"]>;
  parameters?: string[];
  evaluationFilter?: AnalyticsEvaluationFilter | null;
  measurementFilter?: AnalyticsMeasurementFilter | null;
}

export interface AnalyticsAggregateDrilldownOpener {
  onOpenAggregateDrilldown: (target: AnalyticsAggregateDrilldown) => void;
}

export function capabilityFor(result: AnalyticsShellContextResult | undefined, code: string) {
  return result?.capabilities.find((item) => item.code === code);
}

export function isCapabilityAvailable(result: AnalyticsShellContextResult | undefined, code: string): boolean {
  return capabilityFor(result, code)?.status === "AVAILABLE";
}
