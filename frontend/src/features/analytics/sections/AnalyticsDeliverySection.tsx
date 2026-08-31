import { Alert, Card, Space } from "antd";

import type { SavedAnalysisRecord } from "../../../api/savedAnalyses";
import { AnalyticsExportPanel } from "../AnalyticsExportPanel";
import { SavedAnalysesPanel } from "../SavedAnalysesPanel";
import { WaferSummaryPanel } from "../WaferSummaryPanel";
import type { WaferSummaryViewConfig } from "../context/analysisViewConfig";
import type { AnalysisViewState } from "../context/analysisViewState";
import type { AnalyticsSectionContext } from "./sectionTypes";
import type { AnalyticsAggregateDrilldown } from "./sectionTypes";

export interface AnalyticsDeliverySectionProps extends AnalyticsSectionContext {
  page: number;
  pageSize: number;
  viewState: AnalysisViewState;
  onPaginationChange: (page: number, pageSize: number) => void;
  onRestoreSavedAnalysis: (record: SavedAnalysisRecord) => void;
  onWaferSummaryConfigChange: (patch: Partial<WaferSummaryViewConfig>) => void;
  onOpenAggregateDrilldown: (target: AnalyticsAggregateDrilldown) => void;
}

export function AnalyticsDeliverySection({ context, focusDatasetId, overview, overviewLoading, overviewError, page, pageSize, viewState, onPaginationChange, onRestoreSavedAnalysis, onWaferSummaryConfigChange, onOpenAggregateDrilldown }: AnalyticsDeliverySectionProps) {
  if (overviewError) return <Alert type="error" showIcon message="Delivery Context 加载失败" description={overviewError.message} />;
  if (overviewLoading && !overview) return <Card loading title="Delivery / Saved Analysis / Export" />;
  if (!overview) return <Alert type="warning" showIcon message="Delivery Context 尚未就绪" />;
  return <Space direction="vertical" size="large" style={{ width: "100%" }}>
    <WaferSummaryPanel context={context} testStage={overview?.dataset_context.test_stage} page={page} pageSize={pageSize} onPaginationChange={onPaginationChange} config={viewState.analysis.waferSummary} onConfigChange={onWaferSummaryConfigChange} onOpenAggregateDrilldown={onOpenAggregateDrilldown} />
    <SavedAnalysesPanel context={context} ruleContext={overview.rule_context} page={page} pageSize={pageSize} focusDatasetId={focusDatasetId} viewState={viewState} onRestore={onRestoreSavedAnalysis} />
    <AnalyticsExportPanel context={context} ruleContext={overview.rule_context} testStage={overview.dataset_context.test_stage} focusDatasetId={focusDatasetId} page={page} pageSize={pageSize} viewState={viewState} />
  </Space>;
}

export default AnalyticsDeliverySection;
