import { BarChartOutlined, CheckSquareOutlined } from "@ant-design/icons";
import { useQuery } from "@tanstack/react-query";
import { Alert, Button, Card, Checkbox, Col, Empty, Radio, Row, Select, Space, Spin, Typography, message } from "antd";
import { useEffect, useMemo, useState } from "react";

import { getAnalyticsShellContext } from "../../api/analytics";
import type { StageResultRow, TestStage } from "../../api/stageData";
import type { ParameterAnalysisType, RelationshipAnalysisType } from "../analytics/context/analysisViewConfig";
import { resolveContextRule } from "../analytics/context/analysisRuleDefaults";

export type StageAnalysisSource = "PERSONAL" | "SERVER";
export type StageChartType = ParameterAnalysisType | RelationshipAnalysisType;

export interface StageConfiguredAnalysis {
  datasets: Array<{ datasetId: number; versionNo: number }>;
  lotIds: string[];
  parameters: string[];
  chartTypes: StageChartType[];
}

export interface StageAnalysisLauncherProps {
  testStage: TestStage;
  rows: StageResultRow[];
  loading: boolean;
  currentLogin?: string;
  onDraw: (selection: StageConfiguredAnalysis) => void;
}

const MAX_DATASETS = 8;
const MAX_PARAMETERS = 20;

const chartOptions: Array<{ label: string; value: StageChartType }> = [
  { label: "描述统计", value: "DESCRIPTIVE" },
  { label: "箱线图", value: "BOX_PLOT" },
  { label: "直方图", value: "HISTOGRAM" },
  { label: "正态拟合", value: "NORMAL_FIT" },
  { label: "能力分析", value: "CAPABILITY" },
  { label: "散点图", value: "SCATTER" },
  { label: "趋势图", value: "TREND" },
  { label: "相关性矩阵", value: "CORRELATION" },
];

const hasDataset = (row: StageResultRow) => row.dataset_id != null && row.dataset_version_no != null;
const productOf = (row: StageResultRow) => row.product_name?.trim() || "未识别产品";
const lotOf = (row: StageResultRow) => row.lot_id?.trim() || `Batch #${row.import_batch_id}`;
const isServerResult = (row: StageResultRow, currentLogin?: string) => {
  if (row.source_channel === "SOURCE_CATALOG") return true;
  if (row.source_channel === "WEB") return false;
  return row.uploader_login === "SYSTEM_INGESTION" || row.uploader_login !== currentLogin;
};

export function StageAnalysisLauncher({ testStage, rows, loading, currentLogin, onDraw }: StageAnalysisLauncherProps) {
  const [source, setSource] = useState<StageAnalysisSource>("PERSONAL");
  const [product, setProduct] = useState<string>();
  const [lots, setLots] = useState<string[]>([]);
  const [parameters, setParameters] = useState<string[]>([]);
  const [chartTypes, setChartTypes] = useState<StageChartType[]>(["SCATTER"]);
  const [messageApi, contextHolder] = message.useMessage();

  const analyzableRows = useMemo(() => rows.filter(hasDataset), [rows]);
  const sourceRows = useMemo(() => analyzableRows.filter((row) => source === "SERVER"
    ? isServerResult(row, currentLogin)
    : !isServerResult(row, currentLogin) && row.uploader_login === currentLogin), [analyzableRows, currentLogin, source]);
  const products = useMemo(() => Array.from(new Set(sourceRows.map(productOf))).sort(), [sourceRows]);
  const productRows = useMemo(() => product ? sourceRows.filter((row) => productOf(row) === product) : [], [product, sourceRows]);
  const lotOptions = useMemo(() => Array.from(new Set(productRows.map(lotOf))).map((value) => ({ label: value, value })), [productRows]);
  // One Lot can have multiple historical reprocess/import results. The newest
  // result row is the intended current candidate; older Dataset versions must
  // not be mixed into the same analytics context.
  const selectedRows = useMemo(() => lots.flatMap((lot) => {
    const current = productRows.find((row) => lotOf(row) === lot);
    return current ? [current] : [];
  }), [lots, productRows]);
  const selectedDatasets = useMemo(() => selectedRows
    .map((row) => ({ dataset_id: row.dataset_id!, version_no: row.dataset_version_no! }))
    .filter((item, index, items) => items.findIndex((candidate) => candidate.dataset_id === item.dataset_id) === index)
    .slice(0, MAX_DATASETS), [selectedRows]);

  useEffect(() => {
    setProduct(undefined);
    setLots([]);
    setParameters([]);
  }, [source, testStage]);
  useEffect(() => {
    setLots([]);
    setParameters([]);
  }, [product]);

  const contextQuery = useQuery({
    queryKey: ["stage-analysis-options", testStage, selectedDatasets, lots, parameters],
    queryFn: () => getAnalyticsShellContext({
      datasets: selectedDatasets,
      filters: {
        lot_ids: [...lots], wafer_ids: [], bin_codes: [], overall_results: [], source_ids: [],
        tester_ids: [], program_versions: [], test_conditions: [],
      },
      parameters: [...parameters],
      focus_dataset_id: selectedDatasets[0].dataset_id,
      max_points: 100,
    }),
    enabled: selectedDatasets.length > 0,
    placeholderData: (previous) => previous,
    retry: false,
  });
  const availableParameters = useMemo(() => contextQuery.data?.options.parameters ?? [], [contextQuery.data?.options.parameters]);
  const ruleVersions = contextQuery.data?.rule_context?.applicable_rule_versions ?? [];
  const chartAvailable = (chartType: StageChartType) => {
    if (chartType === "DESCRIPTIVE" || chartType === "SCATTER" || chartType === "TREND") return true;
    return resolveContextRule(ruleVersions, chartType) !== null;
  };
  useEffect(() => {
    setParameters((current) => current.filter((item) => availableParameters.includes(item)).slice(0, MAX_PARAMETERS));
  }, [availableParameters]);
  useEffect(() => {
    if (!contextQuery.data) return;
    setChartTypes((current) => {
      const available = current.filter(chartAvailable);
      return available.length ? available : ["SCATTER"];
    });
  // Rule-version identity is the stable availability contract.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [contextQuery.data, ruleVersions.join("|")]);

  const relationSelected = chartTypes.some((item) => item === "SCATTER" || item === "TREND" || item === "CORRELATION");
  const allParametersSelected = availableParameters.length > 0
    && parameters.length === Math.min(availableParameters.length, MAX_PARAMETERS);
  const canDraw = selectedDatasets.length > 0 && parameters.length > 0 && chartTypes.length > 0
    && (!relationSelected || parameters.length >= 2);
  const draw = () => {
    if (!canDraw) {
      messageApi.warning(relationSelected && parameters.length < 2 ? "散点、趋势或相关性分析至少需要选择 2 个参数" : "请完成产品、批次、参数和图形选择");
      return;
    }
    onDraw({
      datasets: selectedDatasets.map((item) => ({ datasetId: item.dataset_id, versionNo: item.version_no })),
      lotIds: [...lots],
      parameters: [...parameters],
      chartTypes: [...chartTypes],
    });
  };

  const personalCount = analyzableRows.filter((row) => !isServerResult(row, currentLogin) && row.uploader_login === currentLogin).length;
  const serverCount = analyzableRows.filter((row) => isServerResult(row, currentLogin)).length;

  return <Card className="stage-analysis-launcher" title={<Space><BarChartOutlined /><span>选择数据并绘制图表</span></Space>}>
    {contextHolder}
    <div className="stage-analysis-steps">
      <section>
        <Typography.Text strong>1. 数据来源</Typography.Text>
        <Radio.Group value={source} buttonStyle="solid" onChange={(event) => setSource(event.target.value)}>
          <Radio.Button value="PERSONAL">我的本机上传（{personalCount}）</Radio.Button>
          <Radio.Button value="SERVER">服务器目录 / 自动清洗（{serverCount}）</Radio.Button>
        </Radio.Group>
      </section>
      <Row gutter={[16, 16]}>
        <Col xs={24} lg={10}>
          <Typography.Text strong>2. 产品名称</Typography.Text>
          <Select aria-label={`${testStage}分析产品`} showSearch allowClear value={product} options={products.map((value) => ({ label: value, value }))} onChange={setProduct} className="full-width" placeholder="先选择产品" notFoundContent={loading ? <Spin size="small" /> : "当前来源没有可分析数据"} />
        </Col>
        <Col xs={24} lg={14}>
          <Typography.Text strong>3. 批次号（最多 {MAX_DATASETS} 个）</Typography.Text>
          <Select aria-label={`${testStage}分析批次`} mode="multiple" allowClear maxCount={MAX_DATASETS} value={lots} options={lotOptions} onChange={(values) => setLots(values.slice(0, MAX_DATASETS))} className="full-width" placeholder={product ? "选择一个或多个批次" : "请先选择产品"} disabled={!product} />
        </Col>
      </Row>
      {selectedDatasets.length > 0 && <section>
        <Space wrap className="stage-analysis-section-title">
          <Typography.Text strong>4. 参数</Typography.Text>
          <Button size="small" icon={<CheckSquareOutlined />} disabled={!availableParameters.length} onClick={() => setParameters(availableParameters.slice(0, MAX_PARAMETERS))}>全选{availableParameters.length > MAX_PARAMETERS ? `前 ${MAX_PARAMETERS} 项` : ""}</Button>
          <Button size="small" disabled={!parameters.length} onClick={() => setParameters([])}>清空</Button>
          <Typography.Text type="secondary">已选 {parameters.length} / {Math.min(availableParameters.length, MAX_PARAMETERS)}</Typography.Text>
        </Space>
        {contextQuery.isLoading ? <Spin /> : contextQuery.isError ? <Alert type="error" showIcon message="参数读取失败" description={contextQuery.error.message} /> : availableParameters.length ? <Checkbox.Group value={parameters} onChange={(values) => setParameters((values as string[]).slice(0, MAX_PARAMETERS))} className="stage-analysis-checkbox-grid">
          {availableParameters.slice(0, MAX_PARAMETERS).map((parameter) => <Checkbox key={parameter} value={parameter}>{parameter}</Checkbox>)}
        </Checkbox.Group> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="所选数据集没有可分析参数" />}
      </section>}
      {availableParameters.length > 0 && <section>
        <Typography.Text strong>5. 选择图形</Typography.Text>
        <Checkbox.Group value={chartTypes} onChange={(values) => setChartTypes(values as StageChartType[])} className="stage-chart-checkbox-grid">
          {chartOptions.map((option) => {
            const available = chartAvailable(option.value);
            return <Checkbox key={option.value} value={option.value} disabled={!available}>{option.label}{!available ? "（不可用）" : ""}</Checkbox>;
          })}
        </Checkbox.Group>
      </section>}
    </div>
    <Space wrap className="stage-analysis-actions">
      <Button type="primary" size="large" icon={<BarChartOutlined />} disabled={!canDraw} onClick={draw}>绘制所选图表</Button>
    </Space>
  </Card>;
}
