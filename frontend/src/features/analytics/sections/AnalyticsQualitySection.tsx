import { Card } from "antd";

import { QualityEvaluationPanel } from "../QualityEvaluationPanel";
import type { QualityAnalysisViewConfig } from "../context/analysisViewConfig";
import type { AnalyticsDrilldownOpener, AnalyticsSectionContext } from "./sectionTypes";

export interface AnalyticsQualitySectionProps extends AnalyticsSectionContext, AnalyticsDrilldownOpener {
  config: QualityAnalysisViewConfig;
  onConfigChange: (patch: Partial<QualityAnalysisViewConfig>) => void;
}

export function AnalyticsQualitySection(props: AnalyticsQualitySectionProps) {
  if (props.overviewLoading && !props.overview) return <Card loading title="Quality Evaluation" />;
  return <QualityEvaluationPanel {...props} />;
}

export default AnalyticsQualitySection;
