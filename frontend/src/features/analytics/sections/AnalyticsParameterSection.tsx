import { Space } from "antd";

import type { DatasetAnalysisOverallResult } from "../../../api/datasets";
import { ParameterAnalysisPanel } from "../ParameterAnalysisPanel";
import { ParameterRelationshipPanel } from "../ParameterRelationshipPanel";
import type { ParameterAnalysisViewConfig, ParameterRelationshipViewConfig } from "../context/analysisViewConfig";
import type { AnalysisDisplayState } from "../context/analysisViewState";
import type { AnalyticsAggregateDrilldownOpener, AnalyticsDrilldownOpener, AnalyticsSectionContext } from "./sectionTypes";

export interface AnalyticsParameterSectionProps extends AnalyticsSectionContext, AnalyticsDrilldownOpener, AnalyticsAggregateDrilldownOpener {
  parameterOptions: string[];
  parameters: string[];
  onParametersChange: (parameters: string[]) => void;
  overallResults: DatasetAnalysisOverallResult[];
  onOverallResultsChange: (results: DatasetAnalysisOverallResult[]) => void;
  displayState: AnalysisDisplayState;
  onDisplayStateChange: (patch: Partial<AnalysisDisplayState>) => void;
  parameterAnalysisConfig: ParameterAnalysisViewConfig;
  onParameterAnalysisConfigChange: (patch: Partial<ParameterAnalysisViewConfig>) => void;
  relationshipConfig: ParameterRelationshipViewConfig;
  onRelationshipConfigChange: (patch: Partial<ParameterRelationshipViewConfig>) => void;
  parameterAutoRunKey?: string;
  relationshipAutoRunKey?: string;
}

export function AnalyticsParameterSection({
  context,
  parameterOptions,
  parameters,
  onParametersChange,
  overallResults,
  onOverallResultsChange,
  onOpenDrilldown,
  onOpenAggregateDrilldown,
  displayState,
  onDisplayStateChange,
  parameterAnalysisConfig,
  onParameterAnalysisConfigChange,
  relationshipConfig,
  onRelationshipConfigChange,
  parameterAutoRunKey,
  relationshipAutoRunKey,
}: AnalyticsParameterSectionProps) {
  return <Space direction="vertical" size="large" style={{ width: "100%" }}>
    <ParameterRelationshipPanel
      context={context}
      parameterOptions={parameterOptions}
      suggestedParameters={parameters}
      onOpenDrilldown={onOpenDrilldown}
      displayState={displayState}
      onDisplayStateChange={onDisplayStateChange}
      config={relationshipConfig}
      onConfigChange={onRelationshipConfigChange}
      autoRunKey={relationshipAutoRunKey}
    />
    <ParameterAnalysisPanel
      datasets={context.datasets}
      parameterOptions={parameterOptions}
      parameters={parameters}
      onParametersChange={onParametersChange}
      lotIds={context.filters.lot_ids}
      waferIds={context.filters.wafer_ids}
      binCodes={context.filters.bin_codes}
      overallResults={overallResults}
      onOverallResultsChange={onOverallResultsChange}
      sourceIds={context.filters.source_ids}
      testerIds={context.filters.tester_ids}
      programVersions={context.filters.program_versions}
      testConditions={context.filters.test_conditions}
      onOpenDrilldown={onOpenDrilldown}
      onOpenAggregateDrilldown={onOpenAggregateDrilldown}
      displayState={displayState}
      onDisplayStateChange={onDisplayStateChange}
      config={parameterAnalysisConfig}
      onConfigChange={onParameterAnalysisConfigChange}
      autoRunKey={parameterAutoRunKey}
    />
  </Space>;
}

export default AnalyticsParameterSection;
