import { ArrowLeftOutlined, ReloadOutlined } from "@ant-design/icons";
import { useIsFetching, useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, Button, Card, Col, Empty, Row, Select, Space, Spin, Tabs, Tag, Typography } from "antd";
import { lazy, Suspense, useCallback, useEffect, useMemo, useState } from "react";

import {
  getAnalyticsFeatureFlags,
  getAnalyticsOverview,
  getAnalyticsShellContext,
  type AnalyticsContextRequest,
  type AnalyticsFeatureGroupCode,
  type AnalyticsOverallResult,
} from "../../api/analytics";
import type { DatasetAnalysisOverallResult } from "../../api/datasets";
import type { SavedAnalysisRecord } from "../../api/savedAnalyses";
import { AnalysisDrilldownDrawer } from "./AnalysisDrilldownDrawer";
import {
  ANALYSIS_OVERALL_RESULTS,
  createDefaultAnalysisViewState,
  parseAnalysisViewState,
  serializeAnalysisViewState,
  type AnalysisAuthorityFilters,
  type AnalysisDisplayState,
  type AnalysisSection,
  type AnalysisViewState,
} from "./context/analysisViewState";
import type { AnalysisComponentState } from "./context/analysisViewConfig";
import { savedAnalysisRestoreParams } from "./savedAnalysisRestore";
import type { AnalyticsAggregateDrilldown } from "./sections/sectionTypes";

const OverviewSection = lazy(() => import("./sections/AnalyticsOverviewSection"));
const DetailSection = lazy(() => import("./sections/AnalyticsDetailSection"));
const ParameterSection = lazy(() => import("./sections/AnalyticsParameterSection"));
const SpatialSection = lazy(() => import("./sections/AnalyticsSpatialSection"));
const QualitySection = lazy(() => import("./sections/AnalyticsQualitySection"));
const DeliverySection = lazy(() => import("./sections/AnalyticsDeliverySection"));

export interface DatasetSelection { datasetId: number; versionNo: number }

export interface AnalyticsWorkbenchProps {
  datasets: DatasetSelection[];
  searchParams: URLSearchParams;
  onSearchParamsChange: (params: URLSearchParams) => void;
  onOpenCatalog: () => void;
}

type FilterKey = keyof AnalysisAuthorityFilters;

const datasetKey = (selection: DatasetSelection) => `${selection.datasetId}:${selection.versionNo}`;
const datasetLabel = (selection: DatasetSelection) => `Dataset #${selection.datasetId} / V${selection.versionNo}`;
const compareOrdinal = (left: string, right: string) => left < right ? -1 : left > right ? 1 : 0;
const selectOptions = (selected: readonly string[], available: readonly string[] = []) => Array.from(new Set([...selected, ...available]))
  .sort(compareOrdinal)
  .map((value) => ({ label: value, value }));

const SECTION_TABS: Array<{ key: AnalysisSection; label: string }> = [
  { key: "overview", label: "Overview" },
  { key: "detail", label: "Detail" },
  { key: "parameter", label: "Parameter" },
  { key: "spatial", label: "Spatial" },
  { key: "quality", label: "Quality" },
  { key: "delivery", label: "Delivery" },
];

const SECTION_FEATURES: Record<AnalysisSection, AnalyticsFeatureGroupCode> = {
  overview: "OVERVIEW",
  detail: "DETAIL",
  parameter: "PARAMETER",
  spatial: "SPATIAL",
  quality: "QUALITY",
  delivery: "DELIVERY",
};

export function AnalyticsWorkbench({ datasets, searchParams, onSearchParamsChange, onOpenCatalog }: AnalyticsWorkbenchProps) {
  const queryClient = useQueryClient();
  const analyticsFetching = useIsFetching({ queryKey: ["analytics"] });
  const selectedDatasets = useMemo(() => datasets
    .filter((item) => Number.isSafeInteger(item.datasetId) && item.datasetId > 0 && Number.isSafeInteger(item.versionNo) && item.versionNo > 0)
    .filter((item, index, items) => items.findIndex((candidate) => candidate.datasetId === item.datasetId) === index)
    .slice(0, 8), [datasets]);
  const viewState = parseAnalysisViewState(searchParams);
  const requestedFocusKey = searchParams.get("detail_dataset");
  const focusDataset = selectedDatasets.find((item) => datasetKey(item) === requestedFocusKey) ?? selectedDatasets[0];
  const featureQuery = useQuery({
    queryKey: ["analytics", "features"],
    queryFn: getAnalyticsFeatureFlags,
    enabled: selectedDatasets.length > 0,
    staleTime: 30_000,
    retry: false,
  });
  const featureFor = (group: AnalyticsFeatureGroupCode) => featureQuery.data?.groups.find((item) => item.code === group);
  const overviewEnabled = featureFor("OVERVIEW")?.enabled === true;

  const context = useMemo<AnalyticsContextRequest>(() => ({
    datasets: selectedDatasets.map((item) => ({ dataset_id: item.datasetId, version_no: item.versionNo })),
    filters: {
      lot_ids: [...viewState.filters.lotIds],
      wafer_ids: [...viewState.filters.waferIds],
      bin_codes: [...viewState.filters.binCodes],
      overall_results: [...viewState.filters.overallResults],
      source_ids: [...viewState.filters.sourceIds],
      tester_ids: [...viewState.filters.testerIds],
      program_versions: [...viewState.filters.programVersions],
      test_conditions: [...viewState.filters.testConditions],
    },
    parameters: [...viewState.filters.parameters],
  }), [selectedDatasets, viewState.filters.binCodes, viewState.filters.lotIds, viewState.filters.overallResults, viewState.filters.parameters, viewState.filters.programVersions, viewState.filters.sourceIds, viewState.filters.testConditions, viewState.filters.testerIds, viewState.filters.waferIds]);
  const contextSignature = JSON.stringify(context);
  const overviewQuery = useQuery({
    queryKey: ["analytics", "overview", context, focusDataset?.datasetId],
    queryFn: () => getAnalyticsOverview({ ...context, focus_dataset_id: focusDataset!.datasetId, max_points: 10_000 }),
    enabled: Boolean(focusDataset && overviewEnabled && viewState.display.section === "overview"),
    retry: false,
  });
  const contextQuery = useQuery({
    queryKey: ["analytics", "context", context, focusDataset?.datasetId],
    queryFn: () => getAnalyticsShellContext({ ...context, focus_dataset_id: focusDataset!.datasetId, max_points: 100 }),
    enabled: Boolean(focusDataset),
    retry: false,
  });
  const [drilldownKey, setDrilldownKey] = useState<string | null>(null);
  useEffect(() => {
    setDrilldownKey(null);
  }, [contextSignature]);
  const openDrilldown = useCallback((key: string) => {
    if (/^UNIT:[1-9][0-9]{0,18}$/.test(key)) setDrilldownKey(key);
  }, []);

  const emitState = (state: AnalysisViewState, mutateExternal?: (params: URLSearchParams) => void) => {
    const next = serializeAnalysisViewState(state, searchParams);
    mutateExternal?.(next);
    onSearchParamsChange(next);
  };
  const updateFilter = (key: FilterKey, values: readonly string[]) => emitState({
    ...viewState,
    filters: { ...viewState.filters, [key]: values } as AnalysisAuthorityFilters,
    display: { ...viewState.display, page: 1 },
    analysis: { ...viewState.analysis, detail: { ...viewState.analysis.detail, evaluation_filter: null, measurement_filter: null } },
  });
  const updateSection = (section: AnalysisSection) => {
    emitState({
      ...viewState,
      filters: viewState.filters,
      display: { ...viewState.display, section },
    });
  };
  const updatePagination = (page: number, pageSize: number) => emitState({
    ...viewState,
    filters: viewState.filters,
    display: { ...viewState.display, page, pageSize },
  });
  const updateChartDisplay = (patch: Partial<AnalysisDisplayState>) => emitState({
    ...viewState,
    filters: viewState.filters,
    display: { ...viewState.display, ...patch },
  });
  const updateAnalysisComponent = <K extends keyof AnalysisComponentState>(key: K, patch: Partial<AnalysisComponentState[K]>) => emitState({
    ...viewState,
    analysis: { ...viewState.analysis, [key]: { ...viewState.analysis[key], ...patch } } as AnalysisComponentState,
  });
  const openAggregateDrilldown = (target: AnalyticsAggregateDrilldown) => {
    const filters = target.filters;
    emitState({
      ...viewState,
      filters: {
        lotIds: filters.lot_ids ?? viewState.filters.lotIds,
        waferIds: filters.wafer_ids ?? viewState.filters.waferIds,
        binCodes: filters.bin_codes ?? viewState.filters.binCodes,
        overallResults: (filters.overall_results ?? viewState.filters.overallResults) as AnalysisAuthorityFilters["overallResults"],
        sourceIds: filters.source_ids ?? viewState.filters.sourceIds,
        testerIds: filters.tester_ids ?? viewState.filters.testerIds,
        programVersions: filters.program_versions ?? viewState.filters.programVersions,
        testConditions: filters.test_conditions ?? viewState.filters.testConditions,
        parameters: target.parameters ?? viewState.filters.parameters,
      },
      display: { ...viewState.display, section: "detail", page: 1 },
      analysis: {
        ...viewState.analysis,
        detail: {
          ...viewState.analysis.detail,
          evaluation_filter: target.evaluationFilter ?? null,
          measurement_filter: target.measurementFilter ?? null,
        },
      },
    }, (next) => {
      if (!target.dataset) return;
      next.delete("dataset");
      const key = `${target.dataset.dataset_id}:${target.dataset.version_no}`;
      next.append("dataset", key);
      next.set("detail_dataset", key);
    });
  };
  const updateFocus = (value: string) => {
    emitState({
      ...viewState,
      filters: viewState.filters,
      display: { ...viewState.display, page: 1 },
      analysis: { ...viewState.analysis, detail: { ...viewState.analysis.detail, evaluation_filter: null, measurement_filter: null } },
    }, (next) => next.set("detail_dataset", value));
  };
  const clearFilters = () => {
    const defaults = createDefaultAnalysisViewState();
    emitState({
      ...viewState,
      filters: defaults.filters,
      display: { ...viewState.display, page: 1 },
      analysis: { ...viewState.analysis, detail: { ...viewState.analysis.detail, evaluation_filter: null, measurement_filter: null } },
    });
  };
  const restoreSavedAnalysis = useCallback((record: SavedAnalysisRecord) => {
    const next = savedAnalysisRestoreParams(record, searchParams);
    if (next) onSearchParamsChange(next);
  }, [onSearchParamsChange, searchParams]);

  if (!selectedDatasets.length) {
    return <div className="workbench analytics-workbench">
      <div className="page-heading"><div><Typography.Title level={2}>正式数据分析</Typography.Title><Typography.Text type="secondary">从历史正式数据中选择 1–8 个 Dataset 后进入分析。</Typography.Text></div></div>
      <Card><Empty description="尚未选择 Dataset"><Button type="primary" icon={<ArrowLeftOutlined />} onClick={onOpenCatalog}>返回历史正式数据选择</Button></Empty></Card>
    </div>;
  }

  const options = contextQuery.data?.options;
  const sectionContext = {
    context,
    focusDatasetId: focusDataset.datasetId,
    overview: contextQuery.data,
    overviewLoading: contextQuery.isLoading,
    overviewError: contextQuery.error,
  };
  const parameterOptions = selectOptions(viewState.filters.parameters, options?.parameters).map((item) => item.value);
  const activeSection = viewState.display.section;
  const activeFeature = featureFor(SECTION_FEATURES[activeSection]);
  const sectionContent = activeFeature?.enabled === false
    ? <Alert type="warning" showIcon message={`${activeFeature.code} 分析已被发布开关关闭`} description={activeFeature.message ?? activeFeature.reason_code ?? "ANALYSIS_FEATURE_DISABLED"} />
    : activeSection === "overview"
    ? <OverviewSection {...sectionContext} overview={overviewQuery.data} overviewLoading={overviewQuery.isLoading} overviewError={overviewQuery.error} onNavigateSection={updateSection} onOpenDrilldown={openDrilldown} onOpenAggregateDrilldown={openAggregateDrilldown} riskConfig={viewState.analysis.overviewRisk} onRiskConfigChange={(patch) => updateAnalysisComponent("overviewRisk", patch)} parameterOptions={parameterOptions} />
    : activeSection === "detail"
      ? <DetailSection {...sectionContext} page={viewState.display.page} pageSize={viewState.display.pageSize} onPaginationChange={updatePagination} onOpenDrilldown={openDrilldown} config={viewState.analysis.detail} onConfigChange={(patch) => updateAnalysisComponent("detail", patch)} />
      : activeSection === "parameter"
        ? <ParameterSection
            {...sectionContext}
            parameterOptions={parameterOptions}
            parameters={[...viewState.filters.parameters]}
            onParametersChange={(values) => updateFilter("parameters", values)}
            overallResults={[...viewState.filters.overallResults] as DatasetAnalysisOverallResult[]}
            onOverallResultsChange={(values) => updateFilter("overallResults", values)}
            onOpenDrilldown={openDrilldown}
            onOpenAggregateDrilldown={openAggregateDrilldown}
            displayState={viewState.display}
            onDisplayStateChange={updateChartDisplay}
            parameterAnalysisConfig={viewState.analysis.parameterAnalysis}
            onParameterAnalysisConfigChange={(patch) => updateAnalysisComponent("parameterAnalysis", patch)}
            relationshipConfig={viewState.analysis.parameterRelationship}
            onRelationshipConfigChange={(patch) => updateAnalysisComponent("parameterRelationship", patch)}
          />
        : activeSection === "spatial"
          ? <SpatialSection {...sectionContext} onOpenDrilldown={openDrilldown} displayState={viewState.display} onDisplayStateChange={updateChartDisplay} config={viewState.analysis.spatial} onConfigChange={(patch) => updateAnalysisComponent("spatial", patch)} />
          : activeSection === "quality"
            ? <QualitySection {...sectionContext} onOpenDrilldown={openDrilldown} config={viewState.analysis.quality} onConfigChange={(patch) => updateAnalysisComponent("quality", patch)} />
            : <DeliverySection {...sectionContext} page={viewState.display.page} pageSize={viewState.display.pageSize} viewState={viewState} onPaginationChange={updatePagination} onRestoreSavedAnalysis={restoreSavedAnalysis} onWaferSummaryConfigChange={(patch) => updateAnalysisComponent("waferSummary", patch)} onOpenAggregateDrilldown={openAggregateDrilldown} />;
  const sectionTabs = SECTION_TABS.map((item) => {
    const feature = featureFor(SECTION_FEATURES[item.key]);
    return {
      ...item,
      disabled: feature?.enabled === false,
      label: feature?.enabled === false ? `${item.label}（已关闭）` : item.label,
    };
  });

  const multiFilter = (
    label: string,
    ariaLabel: string,
    key: FilterKey,
    selected: readonly string[],
    available: readonly string[] | undefined,
    placeholder: string,
    maxCount?: number,
  ) => <Col xs={24} sm={12} lg={8} xl={6}>
    <Typography.Text strong>{label}</Typography.Text>
    <Select
      aria-label={ariaLabel}
      mode="multiple"
      allowClear
      maxCount={maxCount}
      value={[...selected]}
      options={selectOptions(selected, available)}
      onChange={(values) => updateFilter(key, values)}
      className="full-width"
      placeholder={placeholder}
    />
  </Col>;

  return <div className="workbench analytics-workbench">
    <div className="page-heading">
      <div>
        <Typography.Text type="secondary">ANALYTICS_CONTEXT_V1 · 服务端权威结果</Typography.Text>
        <Typography.Title level={2}>{contextQuery.data?.dataset_context.test_stage === "FT" ? "FT 数据分析" : contextQuery.data?.dataset_context.test_stage === "CP" ? "CP 数据分析" : "正式数据分析"}</Typography.Title>
        <Space wrap>{selectedDatasets.map((item) => <Tag color="blue" key={datasetKey(item)}>{datasetLabel(item)}</Tag>)}</Space>
      </div>
      <Space>
        <Button icon={<ArrowLeftOutlined />} onClick={onOpenCatalog}>历史正式数据</Button>
        <Button icon={<ReloadOutlined />} loading={analyticsFetching > 0} onClick={() => void queryClient.invalidateQueries({ queryKey: ["analytics"] })}>刷新</Button>
      </Space>
    </div>

    <Card className="analytics-filter-card" title="统一分析 Context">
      <Row gutter={[12, 12]}>
        <Col xs={24} sm={12} lg={8} xl={6}>
          <Typography.Text strong>当前 Dataset</Typography.Text>
          <Select aria-label="当前 Dataset" value={datasetKey(focusDataset)} options={selectedDatasets.map((item) => ({ label: datasetLabel(item), value: datasetKey(item) }))} onChange={updateFocus} className="full-width" />
        </Col>
        {multiFilter("Lot", "Lot 筛选", "lotIds", viewState.filters.lotIds, options?.lot_ids, "全部 Lot", 50)}
        {multiFilter("Wafer", "Wafer 筛选", "waferIds", viewState.filters.waferIds, options?.wafer_ids, "全部 Wafer", 100)}
        {multiFilter("Bin", "Bin 筛选", "binCodes", viewState.filters.binCodes, options?.bin_codes, "全部 Bin", 50)}
        <Col xs={24} sm={12} lg={8} xl={6}>
          <Typography.Text strong>Overall Result</Typography.Text>
          <Select
            aria-label="Overall Result 筛选"
            mode="multiple"
            allowClear
            maxCount={4}
            value={[...viewState.filters.overallResults]}
            options={ANALYSIS_OVERALL_RESULTS.map((value) => ({ label: value, value }))}
            onChange={(values) => updateFilter("overallResults", values as AnalyticsOverallResult[])}
            className="full-width"
            placeholder="全部结果"
          />
        </Col>
        {multiFilter("Source", "Source 筛选", "sourceIds", viewState.filters.sourceIds, options?.source_ids, "全部 Source", 50)}
        {multiFilter("Tester", "Tester 筛选", "testerIds", viewState.filters.testerIds, options?.tester_ids, "全部 Tester", 50)}
        {multiFilter("Program", "Program 筛选", "programVersions", viewState.filters.programVersions, options?.program_versions, "全部 Program", 50)}
        {multiFilter("Test Condition", "Test Condition 筛选", "testConditions", viewState.filters.testConditions, options?.test_conditions, "全部 Condition", 50)}
        {multiFilter("参数（最多 20 个）", "参数筛选", "parameters", viewState.filters.parameters, options?.parameters, "选择参数", 20)}
      </Row>
      <Space wrap style={{ marginTop: 12 }}>
        <Button onClick={clearFilters}>清空筛选</Button>
        <Typography.Text type="secondary">Lot / Wafer / Bin / Result / Source / Tester / Program / Condition / Parameter 始终作为一个请求 Context，不使用首项替代多选。</Typography.Text>
      </Space>
    </Card>

    {contextQuery.isError && <Alert type="error" showIcon message="统一分析 Context 加载失败" description={contextQuery.error.message} className="review-alert" />}
    {overviewQuery.isError && viewState.display.section === "overview" && <Alert type="error" showIcon message="Overview 加载失败" description={overviewQuery.error.message} className="review-alert" />}
    {featureQuery.isError && <Alert type="error" showIcon message="分析开关加载失败" description={featureQuery.error.message} className="review-alert" />}
    {viewState.warnings.map((warning) => <Alert key={warning} type="warning" showIcon message="分析视图配置已安全降级" description={warning} className="review-alert" />)}
    {contextQuery.data && <Card size="small" className="production-table-card">
      <Space wrap>
        <Tag>合同 {contextQuery.data.contract_version}</Tag>
        <Tag>Context {contextQuery.data.filter_summary.context_hash.slice(0, 12)}…</Tag>
        <Tag>Filter {contextQuery.data.filter_summary.filter_hash.slice(0, 12)}…</Tag>
        <Tag>纳入 / 排除 {contextQuery.data.counts.included_units} / {contextQuery.data.counts.excluded_units}</Tag>
        <Tag color={contextQuery.data.dataset_context.current_published_verified ? "success" : "error"}>Current+PUBLISHED {contextQuery.data.dataset_context.current_published_verified ? "已验证" : "未验证"}</Tag>
        {contextQuery.data.sampling_summary.sampled && <Tag color="warning">已采样 {contextQuery.data.sampling_summary.returned_points} / {contextQuery.data.sampling_summary.original_points}</Tag>}
      </Space>
    </Card>}

    <Card className="production-table-card">
      <Tabs activeKey={activeSection} onChange={(key) => updateSection(key as AnalysisSection)} items={sectionTabs} />
      <Suspense fallback={<div className="page-loading"><Spin size="large" /></div>}>{sectionContent}</Suspense>
    </Card>

    <AnalysisDrilldownDrawer context={context} drilldownKey={drilldownKey} onClose={() => setDrilldownKey(null)} />
  </div>;
}
