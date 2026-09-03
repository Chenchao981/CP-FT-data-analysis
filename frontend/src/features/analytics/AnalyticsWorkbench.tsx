import { ArrowLeftOutlined, DownOutlined, ReloadOutlined, UpOutlined } from "@ant-design/icons";
import { useIsFetching, useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, Button, Card, Col, Collapse, Empty, Row, Select, Space, Spin, Tabs, Tag, Typography } from "antd";
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
import { applyContextRuleDefaults } from "./context/analysisRuleDefaults";
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

type AnalysisGroup = "summary" | "parameter" | "spatial" | "quality" | "report";

const ANALYSIS_GROUPS: Array<{ key: AnalysisGroup; label: string; description: string; primarySection: AnalysisSection; sections: AnalysisSection[] }> = [
  { key: "summary", label: "分析总览", description: "良率、Bin、帕累托和风险摘要", primarySection: "overview", sections: ["overview"] },
  { key: "parameter", label: "参数图表", description: "分布、箱线、散点、趋势和能力", primarySection: "parameter", sections: ["parameter"] },
  { key: "spatial", label: "晶圆空间", description: "Wafer Map、热力、叠加和区域", primarySection: "spatial", sections: ["spatial"] },
  { key: "quality", label: "质量管控", description: "PAT、SPC、裕度、SBL和SYL", primarySection: "quality", sections: ["quality"] },
  { key: "report", label: "报告与数据", description: "明细、晶圆汇总、保存和导出", primarySection: "delivery", sections: ["detail", "delivery"] },
];

const groupForSection = (section: AnalysisSection): AnalysisGroup => ANALYSIS_GROUPS.find((item) => item.sections.includes(section))?.key ?? "summary";

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
  const advancedFilterCount = [
    viewState.filters.binCodes,
    viewState.filters.overallResults,
    viewState.filters.sourceIds,
    viewState.filters.testerIds,
    viewState.filters.programVersions,
    viewState.filters.testConditions,
  ].filter((values) => values.length > 0).length;
  const [advancedFiltersOpen, setAdvancedFiltersOpen] = useState(advancedFilterCount > 0);
  useEffect(() => {
    if (advancedFilterCount > 0) setAdvancedFiltersOpen(true);
  }, [advancedFilterCount]);
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
  const declaredRuleVersions = contextQuery.data?.rule_context.evaluation_rule_versions ?? [];
  const declaredRuleSignature = declaredRuleVersions.join("|");
  const analysisSignature = JSON.stringify(viewState.analysis);
  useEffect(() => {
    if (!declaredRuleVersions.length) return;
    const nextAnalysis = applyContextRuleDefaults(viewState.analysis, declaredRuleVersions);
    if (nextAnalysis === viewState.analysis) return;
    emitState({ ...viewState, analysis: nextAnalysis });
  // Signatures intentionally prevent a URL-state object from retriggering an equivalent default application.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [analysisSignature, declaredRuleSignature]);
  const resolvedStage = contextQuery.data?.dataset_context.test_stage;
  useEffect(() => {
    if (resolvedStage === "FT" && viewState.display.section === "spatial") updateSection("overview");
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [resolvedStage, viewState.display.section]);
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
  const activeSection = resolvedStage === "FT" && viewState.display.section === "spatial" ? "overview" : viewState.display.section;
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
            parameterAutoRunKey={searchParams.get("draw_parameter") === "1" ? searchParams.get("draw_request") ?? undefined : undefined}
            relationshipAutoRunKey={searchParams.get("draw_relationship") === "1" ? searchParams.get("draw_request") ?? undefined : undefined}
          />
        : activeSection === "spatial"
          ? <SpatialSection {...sectionContext} onOpenDrilldown={openDrilldown} displayState={viewState.display} onDisplayStateChange={updateChartDisplay} config={viewState.analysis.spatial} onConfigChange={(patch) => updateAnalysisComponent("spatial", patch)} />
          : activeSection === "quality"
            ? <QualitySection {...sectionContext} onOpenDrilldown={openDrilldown} config={viewState.analysis.quality} onConfigChange={(patch) => updateAnalysisComponent("quality", patch)} />
            : <DeliverySection {...sectionContext} page={viewState.display.page} pageSize={viewState.display.pageSize} viewState={viewState} onPaginationChange={updatePagination} onRestoreSavedAnalysis={restoreSavedAnalysis} onWaferSummaryConfigChange={(patch) => updateAnalysisComponent("waferSummary", patch)} onOpenAggregateDrilldown={openAggregateDrilldown} />;
  const activeGroup = groupForSection(activeSection);
  const groupTabs = ANALYSIS_GROUPS.filter((item) => item.key !== "spatial" || resolvedStage === "CP").map((item) => {
    const disabled = item.sections.every((section) => featureFor(SECTION_FEATURES[section])?.enabled === false);
    return {
      ...item,
      disabled,
      label: disabled ? `${item.label}（已关闭）` : item.label,
    };
  });
  const updateGroup = (group: AnalysisGroup) => {
    const item = ANALYSIS_GROUPS.find((candidate) => candidate.key === group)!;
    updateSection(group === "report" && item.sections.includes(activeSection) ? activeSection : item.primarySection);
  };

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
        <Typography.Text type="secondary">清洗结果 · 服务端统一分析</Typography.Text>
        <Typography.Title level={2}>{contextQuery.data?.dataset_context.test_stage === "FT" ? "FT 数据分析" : contextQuery.data?.dataset_context.test_stage === "CP" ? "CP 数据分析" : "正式数据分析"}</Typography.Title>
        <Space wrap>{selectedDatasets.map((item) => <Tag color="blue" key={datasetKey(item)}>{datasetLabel(item)}</Tag>)}</Space>
      </div>
      <Space>
        <Button icon={<ArrowLeftOutlined />} onClick={onOpenCatalog}>历史正式数据</Button>
        <Button icon={<ReloadOutlined />} loading={analyticsFetching > 0} onClick={() => void queryClient.invalidateQueries({ queryKey: ["analytics"] })}>刷新</Button>
      </Space>
    </div>

    <Card className="analytics-filter-card" title="分析范围" extra={<Typography.Text type="secondary">切换图表时自动沿用当前范围</Typography.Text>}>
      <Row gutter={[12, 12]}>
        <Col xs={24} sm={12} lg={8} xl={6}>
          <Typography.Text strong>当前数据集</Typography.Text>
          <Select aria-label="当前 Dataset" value={datasetKey(focusDataset)} options={selectedDatasets.map((item) => ({ label: datasetLabel(item), value: datasetKey(item) }))} onChange={updateFocus} className="full-width" />
        </Col>
        {multiFilter("批次号", "Lot 筛选", "lotIds", viewState.filters.lotIds, options?.lot_ids, "全部批次", 50)}
        {multiFilter("晶圆", "Wafer 筛选", "waferIds", viewState.filters.waferIds, options?.wafer_ids, "全部晶圆", 100)}
        {multiFilter("测试参数（最多 20 个）", "参数筛选", "parameters", viewState.filters.parameters, options?.parameters, "选择参数", 20)}
      </Row>
      {advancedFiltersOpen && <div className="analytics-advanced-filters">
        <Row gutter={[12, 12]}>
          {multiFilter("Bin 编码", "Bin 筛选", "binCodes", viewState.filters.binCodes, options?.bin_codes, "全部 Bin", 50)}
          <Col xs={24} sm={12} lg={8} xl={6}>
            <Typography.Text strong>测试结果</Typography.Text>
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
          {multiFilter("数据源", "Source 筛选", "sourceIds", viewState.filters.sourceIds, options?.source_ids, "全部数据源", 50)}
          {multiFilter("测试机", "Tester 筛选", "testerIds", viewState.filters.testerIds, options?.tester_ids, "全部测试机", 50)}
          {multiFilter("程序版本", "Program 筛选", "programVersions", viewState.filters.programVersions, options?.program_versions, "全部程序", 50)}
          {multiFilter("测试条件", "Test Condition 筛选", "testConditions", viewState.filters.testConditions, options?.test_conditions, "全部条件", 50)}
        </Row>
      </div>}
      <Space wrap className="analytics-filter-actions">
        <Button aria-expanded={advancedFiltersOpen} icon={advancedFiltersOpen ? <UpOutlined /> : <DownOutlined />} onClick={() => setAdvancedFiltersOpen((open) => !open)}>
          更多筛选{advancedFilterCount ? `（已启用 ${advancedFilterCount} 类）` : ""}
        </Button>
        <Button onClick={clearFilters}>清空筛选</Button>
        <Typography.Text type="secondary">所有图表、表格和导出使用同一分析范围。</Typography.Text>
      </Space>
    </Card>

    {contextQuery.isError && <Alert type="error" showIcon message="统一分析 Context 加载失败" description={contextQuery.error.message} className="review-alert" />}
    {overviewQuery.isError && viewState.display.section === "overview" && <Alert type="error" showIcon message="Overview 加载失败" description={overviewQuery.error.message} className="review-alert" />}
    {featureQuery.isError && <Alert type="error" showIcon message="分析开关加载失败" description={featureQuery.error.message} className="review-alert" />}
    {viewState.warnings.map((warning) => <Alert key={warning} type="warning" showIcon message="分析视图配置已安全降级" description={warning} className="review-alert" />)}
    {contextQuery.data && <Collapse className="analytics-technical-details" items={[{
      key: "technical",
      label: "高级信息（数据版本、规则和计算范围）",
      children: <Space wrap>
        <Tag>合同 {contextQuery.data.contract_version}</Tag>
        <Tag>Context {contextQuery.data.filter_summary.context_hash.slice(0, 12)}…</Tag>
        <Tag>Filter {contextQuery.data.filter_summary.filter_hash.slice(0, 12)}…</Tag>
        <Tag>纳入 / 排除 {contextQuery.data.counts.included_units} / {contextQuery.data.counts.excluded_units}</Tag>
        <Tag color={contextQuery.data.dataset_context.current_published_verified ? "success" : "error"}>当前正式版本 {contextQuery.data.dataset_context.current_published_verified ? "已验证" : "未验证"}</Tag>
        {contextQuery.data.sampling_summary.sampled && <Tag color="warning">已采样 {contextQuery.data.sampling_summary.returned_points} / {contextQuery.data.sampling_summary.original_points}</Tag>}
        {declaredRuleVersions.map((rule) => <Tag color="purple" key={rule}>{rule}</Tag>)}
      </Space>,
    }]} />}

    <Card className="production-table-card analytics-result-card">
      <Tabs
        className="analytics-primary-tabs"
        activeKey={activeGroup}
        onChange={(key) => updateGroup(key as AnalysisGroup)}
        items={groupTabs.map((item) => ({ key: item.key, label: item.label, disabled: item.disabled }))}
      />
      <Typography.Paragraph type="secondary" className="analytics-group-description">
        {ANALYSIS_GROUPS.find((item) => item.key === activeGroup)?.description}
      </Typography.Paragraph>
      {activeGroup === "report" && <Tabs
        size="small"
        activeKey={activeSection}
        onChange={(key) => updateSection(key as AnalysisSection)}
        items={[
          { key: "detail", label: "数据明细", disabled: featureFor("DETAIL")?.enabled === false },
          { key: "delivery", label: "汇总、保存与导出", disabled: featureFor("DELIVERY")?.enabled === false },
        ]}
      />}
      <Suspense fallback={<div className="page-loading"><Spin size="large" /></div>}>{sectionContent}</Suspense>
    </Card>

    <AnalysisDrilldownDrawer context={context} drilldownKey={drilldownKey} onClose={() => setDrilldownKey(null)} />
  </div>;
}
