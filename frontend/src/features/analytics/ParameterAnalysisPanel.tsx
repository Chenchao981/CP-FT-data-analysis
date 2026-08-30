import { ReloadOutlined } from "@ant-design/icons";
import { useMutation } from "@tanstack/react-query";
import { Alert, Button, Card, Col, Empty, Row, Select, Space, Table, Tag, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import type { EChartsOption } from "echarts";
import { useMemo, useState } from "react";

import {
  analyzeDatasetParameters,
  type DatasetAnalysisOverallResult,
  type DatasetParameterAnalysis,
  type DatasetParameterAnalysisItem,
  type DatasetParameterAnalysisRequest,
  type DatasetParameterAnalysisType,
  type DatasetReference,
} from "../../api/datasets";
import { ApiError } from "../../api/auth";
import { EChart } from "../../components/EChart";

export interface ParameterAnalysisPanelProps {
  datasets: DatasetReference[];
  parameterOptions: string[];
  lotIds: string[];
  waferIds: string[];
  binCodes: string[];
  sourceIds: string[];
}

interface AnalysisRow {
  key: string;
  datasetKey: string;
  datasetLabel: string;
  item: DatasetParameterAnalysisItem;
  analysis: DatasetParameterAnalysis;
}

const analysisOptions: Array<{ label: string; value: DatasetParameterAnalysisType }> = [
  { label: "描述统计", value: "DESCRIPTIVE" },
  { label: "箱线图", value: "BOX_PLOT" },
  { label: "直方图", value: "HISTOGRAM" },
  { label: "Capability", value: "CAPABILITY" },
];
const overallResultOptions: Array<{ label: string; value: DatasetAnalysisOverallResult }> = [
  { label: "PASS", value: "PASS" },
  { label: "FAIL", value: "FAIL" },
  { label: "UNKNOWN", value: "UNKNOWN" },
  { label: "ABORT", value: "ABORT" },
];

const datasetKey = (datasetId: number, versionNo: number) => `${datasetId}:${versionNo}`;
const datasetLabel = (datasetId: number, versionNo: number) => `Dataset #${datasetId} / V${versionNo}`;
const formatNumber = (value: number | null) => value == null ? "—" : String(value);
const selectOptions = (values: string[]) => values.map((value) => ({ label: value, value }));
const filterValues = (values: string[]) => values.length ? values.join("、") : "全部";
const cloneRequest = (request: DatasetParameterAnalysisRequest): DatasetParameterAnalysisRequest => ({
  datasets: request.datasets.map((item) => ({ ...item })),
  group_by: request.group_by,
  filters: {
    lot_ids: [...request.filters.lot_ids],
    wafer_ids: [...request.filters.wafer_ids],
    bin_codes: [...request.filters.bin_codes],
    overall_results: [...request.filters.overall_results],
    source_ids: [...request.filters.source_ids],
  },
  parameters: [...request.parameters],
  analyses: [...request.analyses],
});

const statusCounts = (analysis: DatasetParameterAnalysis) => analysis.status_counts.length
  ? <Space size={[4, 4]} wrap>{analysis.status_counts.map((item) => <Tag key={item.status}>{item.status} {item.count}</Tag>)}</Space>
  : "—";

const intervalLabel = (lower: number, upper: number, lowerInclusive: boolean, upperInclusive: boolean) =>
  `${lowerInclusive ? "[" : "("}${lower}, ${upper}${upperInclusive ? "]" : ")"}`;

export function ParameterAnalysisPanel({
  datasets,
  parameterOptions,
  lotIds,
  waferIds,
  binCodes,
  sourceIds,
}: ParameterAnalysisPanelProps) {
  const [parameters, setParameters] = useState<string[]>([]);
  const [analyses, setAnalyses] = useState<DatasetParameterAnalysisType[]>(["DESCRIPTIVE"]);
  const [overallResults, setOverallResults] = useState<DatasetAnalysisOverallResult[]>([]);
  const [submittedRequest, setSubmittedRequest] = useState<DatasetParameterAnalysisRequest | null>(null);
  const [submittedSignature, setSubmittedSignature] = useState<string | null>(null);
  const [boxParameterChoice, setBoxParameterChoice] = useState<string>();
  const [histogramDatasetChoice, setHistogramDatasetChoice] = useState<string>();
  const [histogramParameterChoice, setHistogramParameterChoice] = useState<string>();

  const currentRequest = useMemo<DatasetParameterAnalysisRequest>(() => ({
    datasets: datasets.map((item) => ({ ...item })),
    group_by: "DATASET",
    filters: {
      lot_ids: [...lotIds],
      wafer_ids: [...waferIds],
      bin_codes: [...binCodes],
      overall_results: [...overallResults],
      source_ids: [...sourceIds],
    },
    parameters: [...parameters],
    analyses: [...analyses],
  }), [analyses, binCodes, datasets, lotIds, overallResults, parameters, sourceIds, waferIds]);
  const currentSignature = JSON.stringify(currentRequest);
  const hasUnsupportedCrossDatasetSource = datasets.length > 1 && sourceIds.length > 0;
  const canRun = datasets.length >= 1 && datasets.length <= 8
    && parameters.length >= 1 && parameters.length <= 5
    && analyses.length >= 1 && analyses.length <= 4
    && !hasUnsupportedCrossDatasetSource;

  const mutation = useMutation({
    mutationFn: analyzeDatasetParameters,
    retry: false,
  });

  const execute = () => {
    if (!canRun) return;
    const snapshot = cloneRequest(currentRequest);
    setSubmittedRequest(snapshot);
    setSubmittedSignature(JSON.stringify(snapshot));
    mutation.mutate(snapshot);
  };
  const retrySubmitted = () => {
    if (submittedRequest) mutation.mutate(cloneRequest(submittedRequest));
  };

  const rows = useMemo<AnalysisRow[]>(() => mutation.data?.items.flatMap((item) =>
    item.parameters.map((analysis) => ({
      key: `${item.dataset_id}-${item.version_no}-${analysis.identity.name}`,
      datasetKey: datasetKey(item.dataset_id, item.version_no),
      datasetLabel: datasetLabel(item.dataset_id, item.version_no),
      item,
      analysis,
    }))) ?? [], [mutation.data]);
  const resultParameters = Array.from(new Set(rows.map((row) => row.analysis.identity.name)));
  const resultDatasets = Array.from(new Map(rows.map((row) => [row.datasetKey, row.datasetLabel])).entries());
  const isStale = Boolean(mutation.data && submittedSignature && submittedSignature !== currentSignature);

  const descriptiveRows = rows.filter((row) => row.analysis.descriptive !== null);
  const zeroNumericRows = descriptiveRows.filter((row) => row.analysis.descriptive?.numeric_count === 0);
  const boxParameter = resultParameters.includes(boxParameterChoice ?? "") ? boxParameterChoice! : resultParameters[0];
  const boxRows = rows.filter((row) => row.analysis.identity.name === boxParameter && row.analysis.box_plot !== null);
  const histogramDataset = resultDatasets.some(([key]) => key === histogramDatasetChoice)
    ? histogramDatasetChoice!
    : resultDatasets[0]?.[0];
  const histogramParameter = resultParameters.includes(histogramParameterChoice ?? "")
    ? histogramParameterChoice!
    : resultParameters[0];
  const histogramRow = rows.find((row) => row.datasetKey === histogramDataset
    && row.analysis.identity.name === histogramParameter
    && row.analysis.histogram !== null);
  const capabilityRows = rows.filter((row) => row.analysis.capability !== null);

  const boxOption = useMemo<EChartsOption>(() => ({
    tooltip: { trigger: "item" },
    grid: { left: 72, right: 28, top: 34, bottom: 70 },
    xAxis: {
      type: "category",
      data: boxRows.map((row) => row.datasetLabel),
      axisLabel: { rotate: boxRows.length > 3 ? 30 : 0, hideOverlap: true },
    },
    yAxis: { type: "value", name: boxRows[0]?.analysis.identity.unit ?? undefined },
    dataZoom: boxRows.length > 6 ? [{ type: "inside" }, { type: "slider", height: 18, bottom: 8 }] : undefined,
    series: [{
      name: boxParameter ?? "参数",
      type: "boxplot",
      data: boxRows.map((row) => {
        const box = row.analysis.box_plot!;
        return [box.lower_whisker, box.q1, box.median, box.q3, box.upper_whisker];
      }),
    }],
  }), [boxParameter, boxRows]);

  const histogram = histogramRow?.analysis.histogram;
  const histogramOption = useMemo<EChartsOption>(() => ({
    tooltip: { trigger: "axis" },
    grid: { left: 70, right: 24, top: 34, bottom: 92 },
    xAxis: {
      type: "category",
      name: histogramRow?.analysis.identity.unit ?? undefined,
      data: histogram?.bins.map((bin) => intervalLabel(
        bin.lower_bound,
        bin.upper_bound,
        bin.lower_inclusive,
        bin.upper_inclusive,
      )) ?? [],
      axisLabel: { rotate: 45, hideOverlap: true },
    },
    yAxis: { type: "value", name: "Count", minInterval: 1 },
    dataZoom: (histogram?.bins.length ?? 0) > 20 ? [{ type: "inside" }, { type: "slider", height: 18, bottom: 8 }] : undefined,
    series: [{
      name: "后端分箱计数",
      type: "bar",
      data: histogram?.bins.map((bin) => bin.count) ?? [],
    }],
  }), [histogram, histogramRow]);

  const descriptiveColumns: ColumnsType<AnalysisRow> = [
    { title: "Dataset", dataIndex: "datasetLabel", width: 185, fixed: "left" },
    { title: "参数", width: 150, render: (_, row) => row.analysis.identity.name },
    { title: "单位", width: 85, render: (_, row) => row.analysis.identity.unit ?? "—" },
    { title: "行数", width: 90, render: (_, row) => row.analysis.descriptive?.row_count ?? "—" },
    { title: "数值数", width: 90, render: (_, row) => row.analysis.descriptive?.numeric_count ?? "—" },
    { title: "非数值/未纳入统计", width: 155, render: (_, row) => row.analysis.descriptive?.excluded_count ?? "—" },
    { title: "最小", width: 105, render: (_, row) => formatNumber(row.analysis.descriptive?.minimum ?? null) },
    { title: "最大", width: 105, render: (_, row) => formatNumber(row.analysis.descriptive?.maximum ?? null) },
    { title: "平均", width: 105, render: (_, row) => formatNumber(row.analysis.descriptive?.average ?? null) },
    { title: "样本标准差", width: 120, render: (_, row) => formatNumber(row.analysis.descriptive?.sample_stddev ?? null) },
    { title: "测量状态", width: 300, render: (_, row) => statusCounts(row.analysis) },
  ];
  const filterSummaryColumns: ColumnsType<DatasetParameterAnalysisItem> = [
    { title: "Dataset", width: 185, fixed: "left", render: (_, item) => datasetLabel(item.dataset_id, item.version_no) },
    { title: "Lot", width: 180, render: (_, item) => filterValues(item.filter_summary.lot_ids) },
    { title: "Wafer", width: 180, render: (_, item) => filterValues(item.filter_summary.wafer_ids) },
    { title: "Bin", width: 150, render: (_, item) => filterValues(item.filter_summary.bin_codes) },
    { title: "总体结果", width: 180, render: (_, item) => filterValues(item.filter_summary.overall_results) },
    { title: "源文件", width: 200, render: (_, item) => filterValues(item.filter_summary.source_ids) },
    { title: "命中 Unit", width: 110, render: (_, item) => item.filter_summary.matched_unit_count },
    { title: "候选测量值", width: 125, render: (_, item) => item.filter_summary.candidate_measurement_count },
  ];
  const identityColumns: ColumnsType<AnalysisRow> = [
    { title: "Dataset", dataIndex: "datasetLabel", width: 185, fixed: "left" },
    { title: "参数", width: 145, render: (_, row) => row.analysis.identity.name },
    { title: "Canonical Code", width: 170, render: (_, row) => row.analysis.identity.canonical_parameter_code ?? "—" },
    { title: "单位", width: 85, render: (_, row) => row.analysis.identity.unit ?? "—" },
    { title: "Program LSL / USL", width: 175, render: (_, row) => `${formatNumber(row.analysis.identity.program_lsl)} / ${formatNumber(row.analysis.identity.program_usl)}` },
    { title: "测试条件", width: 220, render: (_, row) => row.analysis.identity.test_condition ?? "—" },
    { title: "Spec Set", width: 150, render: (_, row) => row.analysis.identity.spec_set_ids.length ? row.analysis.identity.spec_set_ids.join("、") : "—" },
    { title: "限值来源", width: 180, render: (_, row) => row.analysis.identity.limit_source || "—" },
  ];
  const boxColumns: ColumnsType<AnalysisRow> = [
    { title: "Dataset", dataIndex: "datasetLabel", width: 185, fixed: "left" },
    { title: "原始最小", width: 105, render: (_, row) => formatNumber(row.analysis.box_plot?.minimum ?? null) },
    { title: "下须", width: 95, render: (_, row) => formatNumber(row.analysis.box_plot?.lower_whisker ?? null) },
    { title: "Q1", width: 90, render: (_, row) => formatNumber(row.analysis.box_plot?.q1 ?? null) },
    { title: "中位数", width: 95, render: (_, row) => formatNumber(row.analysis.box_plot?.median ?? null) },
    { title: "Q3", width: 90, render: (_, row) => formatNumber(row.analysis.box_plot?.q3 ?? null) },
    { title: "上须", width: 95, render: (_, row) => formatNumber(row.analysis.box_plot?.upper_whisker ?? null) },
    { title: "原始最大", width: 105, render: (_, row) => formatNumber(row.analysis.box_plot?.maximum ?? null) },
    { title: "离群点数", width: 105, render: (_, row) => row.analysis.box_plot?.outlier_count ?? "—" },
    { title: "方法", width: 180, render: (_, row) => row.analysis.box_plot?.method ?? "—" },
  ];
  const capabilityColumns: ColumnsType<AnalysisRow> = [
    { title: "Dataset", dataIndex: "datasetLabel", width: 185, fixed: "left" },
    { title: "参数", width: 140, render: (_, row) => row.analysis.identity.name },
    { title: "总状态", width: 120, render: (_, row) => <Tag>{row.analysis.capability?.status ?? "—"}</Tag> },
    { title: "Ppk 状态", width: 120, render: (_, row) => row.analysis.capability?.ppk_status ?? "—" },
    { title: "Ppk", width: 90, render: (_, row) => formatNumber(row.analysis.capability?.ppk ?? null) },
    { title: "Cpk 状态", width: 125, render: (_, row) => row.analysis.capability?.cpk_status ?? "—" },
    { title: "Cpk", width: 90, render: (_, row) => formatNumber(row.analysis.capability?.cpk ?? null) },
    { title: "LSL / USL", width: 150, render: (_, row) => `${formatNumber(row.analysis.capability?.lsl ?? null)} / ${formatNumber(row.analysis.capability?.usl ?? null)}` },
    { title: "样本 / 子组", width: 125, render: (_, row) => `${row.analysis.capability?.sample_count ?? 0} / ${row.analysis.capability?.subgroup_count ?? 0}` },
    { title: "规则", width: 245, render: (_, row) => row.analysis.capability?.rule_code ?? "后端未选择 Cpk 子组规则" },
    { title: "不适用原因", width: 330, render: (_, row) => row.analysis.capability?.reason_codes.length ? row.analysis.capability.reason_codes.join("、") : "—" },
  ];

  const error = mutation.error;
  const apiError = error instanceof ApiError ? error : null;
  const ruleApprovalPending = apiError?.code === "ANALYSIS_RULE_NOT_APPROVED";
  const nonEligibleCapabilities = capabilityRows.filter((row) => row.analysis.capability?.status !== "ELIGIBLE");

  return <Card
    title="参数分析（显式执行）"
    extra={<Tag color="blue">当前 {datasets.length} 个 Dataset · 最多 5 个参数</Tag>}
    className="production-table-card"
  >
    <Row gutter={[12, 12]}>
      <Col xs={24} xl={9}>
        <Typography.Text strong>分析参数（独立选择，1–5 个）</Typography.Text>
        <Select
          aria-label="参数分析参数"
          mode="multiple"
          allowClear
          maxCount={5}
          value={parameters}
          options={selectOptions(parameterOptions)}
          onChange={(values) => setParameters(values.slice(0, 5))}
          placeholder="选择参数后手动执行"
          className="full-width"
        />
      </Col>
      <Col xs={24} sm={12} xl={7}>
        <Typography.Text strong>Unit 总体结果</Typography.Text>
        <Select
          aria-label="参数分析总体结果"
          mode="multiple"
          allowClear
          maxCount={4}
          value={overallResults}
          options={overallResultOptions}
          onChange={(values) => setOverallResults(values.slice(0, 4))}
          placeholder="全部 PASS / FAIL / UNKNOWN / ABORT"
          className="full-width"
        />
      </Col>
      <Col xs={24} sm={12} xl={8}>
        <Typography.Text strong>分析类型</Typography.Text>
        <Select
          aria-label="参数分析类型"
          mode="multiple"
          allowClear
          maxCount={4}
          value={analyses}
          options={analysisOptions}
          onChange={(values) => setAnalyses(values.slice(0, 4))}
          placeholder="至少选择一种分析"
          className="full-width"
        />
        <Typography.Text type="secondary">描述统计可直接执行；BoxPlot、Histogram 和显式 Capability Rule 必须由服务端 Rule Owner 批准，未批准时失败关闭。</Typography.Text>
      </Col>
    </Row>
    <Space wrap style={{ marginTop: 12 }}>
      <Button type="primary" aria-label="执行参数分析" loading={mutation.isPending} disabled={!canRun} onClick={execute}>执行参数分析</Button>
      <Typography.Text type="secondary">
        沿用当前 Lot、Wafer、Bin、源文件筛选；进入页面和顶部“刷新”均不会执行本分析。前端不指定 Capability 规则码。
      </Typography.Text>
    </Space>
    {!parameters.length && <Alert type="info" showIcon message="请选择 1–5 个分析参数后点击执行" style={{ marginTop: 12 }} />}
    {!analyses.length && <Alert type="warning" showIcon message="至少选择一种分析类型" style={{ marginTop: 12 }} />}
    {hasUnsupportedCrossDatasetSource && <Alert
      type="warning"
      showIcon
      message="跨 Dataset 参数分析不能沿用当前源文件筛选"
      description="当前源文件选项只属于“当前图表与明细 Dataset”，尚未建立跨 Dataset 的统一 Source 身份。请先清除源文件筛选，或只选择一个 Dataset 后再执行。"
      style={{ marginTop: 12 }}
    />}

    {isStale && <Alert
      type="warning"
      showIcon
      message="当前结果已过期"
      description="Dataset、筛选、分析参数或分析类型已变化。旧结果仍保留供核对，请点击“执行参数分析”生成当前条件结果。"
      style={{ marginTop: 12 }}
    />}

    {mutation.isError && <Alert
      type="error"
      showIcon
      message={ruleApprovalPending ? "统计口径待业务批准" : "参数分析失败"}
      description={<Space direction="vertical" size={4}>
        {ruleApprovalPending && <Typography.Text>服务端已失败关闭本次统计；请完成 Owner、Validator、批准日期和正式 Golden 记录后再启用。</Typography.Text>}
        <Typography.Text>{error instanceof Error ? error.message : "未知错误"}</Typography.Text>
        <Typography.Text>错误代码：{apiError?.code ?? "UNKNOWN_ERROR"}</Typography.Text>
        <Typography.Text>HTTP：{apiError?.httpStatus ?? "—"}</Typography.Text>
        <Typography.Text>建议操作：{apiError?.recommendedAction ?? "请核对筛选条件或联系管理员"}</Typography.Text>
        <Typography.Text>可重试：{apiError?.retryable ? "是" : "否"}</Typography.Text>
        {apiError?.retryable && submittedRequest && <Button size="small" icon={<ReloadOutlined />} aria-label="重试参数分析" onClick={retrySubmitted}>重试上次请求</Button>}
      </Space>}
      style={{ marginTop: 12 }}
    />}

    {mutation.data && <Space direction="vertical" size="large" style={{ width: "100%", marginTop: 16 }}>
      <Space wrap>
        <Tag>合同 {mutation.data.contract_version}</Tag>
        <Tag>分组 {mutation.data.group_by}</Tag>
        <Tag color={mutation.data.compatibility === "COMPATIBLE" ? "success" : "default"}>兼容性 {mutation.data.compatibility}</Tag>
        <Tag color={mutation.data.dataset_context.current_published_verified ? "success" : "error"}>
          Current+PUBLISHED {mutation.data.dataset_context.current_published_verified ? "已验证" : "未验证"}
        </Tag>
        <Tag>Filter Hash {mutation.data.filter_summary.filter_hash.slice(0, 12)}…</Tag>
        <Tag>规则 {mutation.data.rule_context.capability_rule_approval_status}</Tag>
        <Typography.Text type="secondary">返回 {mutation.data.items.length} 个 Dataset、{rows.length} 个 Dataset-参数结果</Typography.Text>
      </Space>

      <Card size="small" title="可复现上下文">
        <Space direction="vertical" size={4}>
          <Typography.Text>计算时间（UTC）：{mutation.data.computed_at}</Typography.Text>
          <Typography.Text>输入 / 纳入 / 排除 Unit：{mutation.data.counts.input_units} / {mutation.data.counts.included_units} / {mutation.data.counts.excluded_units}</Typography.Text>
          <Typography.Text>Missing Measurement：{mutation.data.counts.missing_measurements}</Typography.Text>
          <Typography.Text>Spec：{mutation.data.rule_context.spec_versions.length ? mutation.data.rule_context.spec_versions.join("、") : "未使用"}</Typography.Text>
          <Typography.Text>评价规则：{mutation.data.rule_context.evaluation_rule_versions.length ? mutation.data.rule_context.evaluation_rule_versions.join("、") : "未使用"}</Typography.Text>
        </Space>
      </Card>

      {mutation.data.warnings.length > 0 && <Alert
        type="warning"
        showIcon
        message="分析能力提示"
        description={mutation.data.warnings.join("、")}
      />}

      <Card size="small" title="Dataset 筛选与命中摘要">
        <Table
          rowKey={(item) => `${item.dataset_id}-${item.version_no}`}
          columns={filterSummaryColumns}
          dataSource={mutation.data.items}
          pagination={false}
          scroll={{ x: 1310 }}
          size="small"
        />
      </Card>

      {rows.length > 0 && <Card size="small" title="参数身份与规格来源">
        <Table rowKey="key" columns={identityColumns} dataSource={rows} pagination={false} scroll={{ x: 1310 }} size="small" />
      </Card>}

      {zeroNumericRows.length > 0 && <Alert
        type="warning"
        showIcon
        message="当前范围没有可分析数值"
        description={`${zeroNumericRows.map((row) => `${row.datasetLabel} · ${row.analysis.identity.name}`).join("；")} 的 numeric_count 为 0；请检查筛选和测量状态。`}
      />}

      {descriptiveRows.length > 0 && <Card size="small" title="描述统计">
        <Table rowKey="key" columns={descriptiveColumns} dataSource={descriptiveRows} pagination={false} scroll={{ x: 1490 }} size="small" />
      </Card>}

      {rows.some((row) => row.analysis.box_plot !== null) && <Card size="small" title="箱线图">
        <Space direction="vertical" size="middle" style={{ width: "100%" }}>
          <Select aria-label="箱线图参数" value={boxParameter} options={selectOptions(resultParameters)} onChange={setBoxParameterChoice} style={{ minWidth: 220 }} />
          {boxRows.length ? <>
            <EChart option={boxOption} ariaLabel={`${boxParameter} 按 Dataset 的箱线图`} />
            <Typography.Text type="secondary">图形五数使用下须、Q1、中位数、Q3、上须；原始最小/最大和离群点数单独列示。</Typography.Text>
            <Table rowKey="key" columns={boxColumns} dataSource={boxRows} pagination={false} scroll={{ x: 1240 }} size="small" />
          </> : <Empty description="当前参数不适用于箱线图" />}
        </Space>
      </Card>}

      {rows.some((row) => row.analysis.histogram !== null) && <Card size="small" title="直方图（后端分箱）">
        <Space direction="vertical" size="middle" style={{ width: "100%" }}>
          <Space wrap>
            <Select aria-label="直方图 Dataset" value={histogramDataset} options={resultDatasets.map(([value, label]) => ({ value, label }))} onChange={setHistogramDatasetChoice} style={{ minWidth: 220 }} />
            <Select aria-label="直方图参数" value={histogramParameter} options={selectOptions(resultParameters)} onChange={setHistogramParameterChoice} style={{ minWidth: 180 }} />
          </Space>
          {histogram ? <>
            <EChart option={histogramOption} ariaLabel={`${histogramParameter} 在 ${histogramRow?.datasetLabel} 的后端分箱直方图`} />
            <Typography.Text type="secondary">后端返回 {histogram.bin_count} 个分箱（请求 {histogram.requested_bin_count}）；范围 {formatNumber(histogram.range_min)} 至 {formatNumber(histogram.range_max)}；方法 {histogram.method}。前端未重新分箱。</Typography.Text>
          </> : <Empty description="当前 Dataset 与参数组合不适用于直方图" />}
        </Space>
      </Card>}

      {capabilityRows.length > 0 && <Card size="small" title="Capability">
        {nonEligibleCapabilities.length > 0 && <Alert
          type="warning"
          showIcon
          message={capabilityRows.some((row) => row.analysis.capability?.reason_codes.includes("CAPABILITY_RULE_REQUIRED"))
            ? "统计规则尚未批准，能力指数保持关闭"
            : "部分 Capability 指标不适用或未请求"}
          description="请以服务端返回的 Ppk/Cpk 状态、原因码和规则码为准；NOT_REQUESTED 和空指标是受控门禁结果，前端不会补成 0 或当作请求失败。"
          style={{ marginBottom: 12 }}
        />}
        <Table rowKey="key" columns={capabilityColumns} dataSource={capabilityRows} pagination={false} scroll={{ x: 1750 }} size="small" />
      </Card>}

      {!rows.length && <Empty description="当前筛选没有可分析的数据" />}
    </Space>}
  </Card>;
}
