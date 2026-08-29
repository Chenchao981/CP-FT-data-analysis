import { ArrowLeftOutlined, ReloadOutlined } from "@ant-design/icons";
import { useQuery } from "@tanstack/react-query";
import { Alert, Button, Card, Col, Empty, Row, Select, Space, Statistic, Table, Tag, Typography } from "antd";
import type { ColumnsType, TablePaginationConfig } from "antd/es/table";
import type { EChartsOption } from "echarts";
import { useMemo } from "react";

import {
  compareDatasets,
  getDatasetChartData,
  getDatasetDetails,
  type DatasetComparisonItem,
  type DatasetDetailMeasurement,
  type DatasetDetailRow,
} from "../../api/datasets";
import { EChart } from "../../components/EChart";

export interface DatasetSelection { datasetId: number; versionNo: number }

export interface AnalyticsWorkbenchProps {
  datasets: DatasetSelection[];
  searchParams: URLSearchParams;
  onSearchParamsChange: (params: URLSearchParams) => void;
  onOpenCatalog: () => void;
}

const BIN_COLORS = ["#2d9d78", "#d64545", "#f0a429", "#7b61a8", "#247ba0", "#8d6e63", "#607d8b"];
const datasetKey = (selection: DatasetSelection) => `${selection.datasetId}:${selection.versionNo}`;
const datasetLabel = (selection: DatasetSelection) => `Dataset #${selection.datasetId} / V${selection.versionNo}`;
const uniqueValues = (values: string[]) => Array.from(new Set(values.map((value) => value.trim()).filter(Boolean)));
const positiveInt = (value: string | null, fallback: number, maximum?: number) => {
  const parsed = Number(value);
  if (!Number.isInteger(parsed) || parsed <= 0) return fallback;
  return maximum == null ? parsed : Math.min(parsed, maximum);
};
const selectOptions = (values: string[]) => values.map((value) => ({ label: value, value }));
const percent = (value: number | null) => value == null ? "—" : `${(value * 100).toFixed(3)}%`;
const compatibilityLabel: Record<string, string> = {
  SINGLE_DATASET: "单数据集",
  COMPATIBLE: "规格兼容",
  NOT_EVALUATED: "选择参数后校验",
};
const measurementValue = (measurement: DatasetDetailMeasurement) => {
  const value = measurement.value_numeric ?? measurement.value_text ?? "—";
  return `${measurement.parameter}: ${value}${measurement.unit ? ` ${measurement.unit}` : ""} (${measurement.status})`;
};

export function AnalyticsWorkbench({ datasets, searchParams, onSearchParamsChange, onOpenCatalog }: AnalyticsWorkbenchProps) {
  const selectedDatasets = datasets
    .filter((item) => Number.isSafeInteger(item.datasetId) && item.datasetId > 0 && Number.isSafeInteger(item.versionNo) && item.versionNo > 0)
    .filter((item, index, items) => items.findIndex((candidate) => candidate.datasetId === item.datasetId) === index)
    .slice(0, 8);
  const selectionKeys = selectedDatasets.map(datasetKey);
  const requestedDetailKey = searchParams.get("detail_dataset");
  const detailDataset = selectedDatasets.find((item) => datasetKey(item) === requestedDetailKey) ?? selectedDatasets[0];
  const lotIds = uniqueValues(searchParams.getAll("lot_id"));
  const waferIds = uniqueValues(searchParams.getAll("wafer_id"));
  const binCodes = uniqueValues(searchParams.getAll("bin_code"));
  const parameters = uniqueValues(searchParams.getAll("parameter")).slice(0, 20);
  const sourceId = searchParams.get("source_id")?.trim() || undefined;
  const page = positiveInt(searchParams.get("page"), 1);
  const pageSize = positiveInt(searchParams.get("page_size"), 50, 200);

  const updateSearch = (mutate: (next: URLSearchParams) => void) => {
    const next = new URLSearchParams(searchParams);
    mutate(next);
    onSearchParamsChange(next);
  };
  const updateFilter = (key: "lot_id" | "wafer_id" | "bin_code" | "parameter", values: string[]) => updateSearch((next) => {
    next.delete(key);
    for (const value of uniqueValues(values)) next.append(key, value);
    next.set("page", "1");
    if (key === "lot_id") {
      next.delete("wafer_id");
      next.delete("source_id");
    }
  });

  const comparisonQuery = useQuery({
    queryKey: ["dataset-comparison", selectionKeys, lotIds, waferIds, binCodes, parameters],
    queryFn: () => compareDatasets({
      datasets: selectedDatasets.map((item) => ({ dataset_id: item.datasetId, version_no: item.versionNo })),
      lot_ids: lotIds,
      wafer_ids: waferIds,
      bin_codes: binCodes,
      parameters,
    }),
    enabled: selectedDatasets.length > 0,
  });
  const detailsQuery = useQuery({
    queryKey: ["dataset-details", detailDataset?.datasetId, detailDataset?.versionNo, page, pageSize, lotIds, waferIds, binCodes, parameters],
    queryFn: () => getDatasetDetails(detailDataset!.datasetId, detailDataset!.versionNo, {
      page,
      page_size: pageSize,
      lot_ids: lotIds,
      wafer_ids: waferIds,
      bin_codes: binCodes,
      parameters,
    }),
    enabled: Boolean(detailDataset),
  });
  const chartQuery = useQuery({
    queryKey: ["dataset-charts", detailDataset?.datasetId, detailDataset?.versionNo, lotIds[0], waferIds[0], sourceId, parameters[0]],
    queryFn: () => getDatasetChartData(detailDataset!.datasetId, detailDataset!.versionNo, lotIds[0], waferIds[0], sourceId, parameters[0]),
    enabled: Boolean(detailDataset),
  });
  const data = chartQuery.data;
  const detailOptions = detailsQuery.data;
  const waferOptions = data?.wafer_options.filter((item) => !lotIds[0] || item.lot_id === lotIds[0]) ?? [];

  const yieldOption = useMemo<EChartsOption>(() => ({
    color: ["#1167a8"],
    tooltip: { trigger: "axis", valueFormatter: (value) => value == null ? "—" : `${Number(value).toFixed(3)}%` },
    grid: { left: 56, right: 24, top: 30, bottom: 70 },
    xAxis: {
      type: "category",
      data: data?.wafer_yield.map((item) => `${item.lot_id}\nW${item.wafer_id}`) ?? [],
      axisLabel: { rotate: 45, hideOverlap: true },
    },
    yAxis: { type: "value", min: 0, max: 100, name: "Yield %" },
    dataZoom: [{ type: "inside" }, { type: "slider", height: 18, bottom: 8 }],
    series: [{
      type: "line",
      smooth: false,
      symbolSize: 7,
      lineStyle: { width: 2 },
      areaStyle: { opacity: 0.08 },
      data: data?.wafer_yield.map((item) => item.yield_rate == null ? null : Number((item.yield_rate * 100).toFixed(4))) ?? [],
    }],
  }), [data]);

  const paretoOption = useMemo<EChartsOption>(() => {
    let cumulative = 0;
    const cumulativeData = data?.bin_counts.map((item) => {
      cumulative += item.percent * 100;
      return Number(cumulative.toFixed(3));
    }) ?? [];
    return {
      color: ["#d64545", "#f0a429"],
      tooltip: { trigger: "axis" },
      legend: { data: ["Die数", "累计占比"] },
      grid: { left: 56, right: 56, top: 48, bottom: 42 },
      xAxis: { type: "category", data: data?.bin_counts.map((item) => `Bin ${item.soft_bin}`) ?? [] },
      yAxis: [
        { type: "value", name: "Die数", minInterval: 1 },
        { type: "value", name: "累计 %", min: 0, max: 100 },
      ],
      series: [
        { name: "Die数", type: "bar", data: data?.bin_counts.map((item) => item.unit_count) ?? [] },
        { name: "累计占比", type: "line", yAxisIndex: 1, data: cumulativeData },
      ],
    };
  }, [data]);

  const waferMapOption = useMemo<EChartsOption>(() => {
    const bins = Array.from(new Set(data?.wafer_map.map((item) => item.soft_bin ?? "UNKNOWN") ?? []));
    return {
      tooltip: {
        formatter: (params: unknown) => {
          const point = (params as { data: [number, number, number, string, string] }).data;
          return `X ${point[0]} · Y ${point[1]}<br/>Bin ${point[3]} · ${point[4]}`;
        },
      },
      grid: { left: 58, right: 26, top: 28, bottom: 56 },
      xAxis: { type: "value", name: "X", minInterval: 1 },
      yAxis: { type: "value", name: "Y", minInterval: 1, inverse: true },
      visualMap: {
        type: "piecewise",
        dimension: 2,
        bottom: 0,
        orient: "horizontal",
        pieces: bins.map((bin, index) => ({ value: index, label: `Bin ${bin}`, color: BIN_COLORS[index % BIN_COLORS.length] })),
      },
      series: [{
        type: "scatter",
        symbol: "rect",
        symbolSize: 12,
        data: data?.wafer_map.map((item) => [item.x, item.y, bins.indexOf(item.soft_bin ?? "UNKNOWN"), item.soft_bin ?? "UNKNOWN", item.result]) ?? [],
      }],
    };
  }, [data]);

  const selectedWafer = data?.wafer_yield.find((item) => item.lot_id === lotIds[0] && item.wafer_id === waferIds[0]);
  const selectedFtParameter = data?.parameter_options.find((item) => item.name === parameters[0]);
  const missingFtPoints = data?.ft_parameter_points.filter((item) => item.status === "MISSING").length ?? 0;
  const ftScatterOption = useMemo<EChartsOption>(() => {
    const sources = data?.source_options ?? [];
    const selected = data?.parameter_options.find((item) => item.name === parameters[0]);
    const markLineData: Array<{ name: string; yAxis: number; lineStyle: { color: string } }> = [];
    if (selected?.lsl !== null && selected?.lsl !== undefined) markLineData.push({ name: "LSL", yAxis: selected.lsl, lineStyle: { color: "#d64545" } });
    if (selected?.usl !== null && selected?.usl !== undefined) markLineData.push({ name: "USL", yAxis: selected.usl, lineStyle: { color: "#d64545" } });
    return {
      color: BIN_COLORS,
      tooltip: {
        trigger: "item",
        formatter: (item: unknown) => {
          const point = (item as { data: [number, number | null, string, string, string] }).data;
          return `${point[2]} · NUM ${point[0]}<br/>${parameters[0] ?? "参数"}: ${point[1] ?? "缺失"}<br/>Lot ${point[3]} · ${point[4]}`;
        },
      },
      legend: { data: sources, type: "scroll" as const },
      grid: { left: 72, right: 32, top: 56, bottom: 64 },
      xAxis: { type: "value", name: "NUM", minInterval: 1 },
      yAxis: { type: "value", name: selected?.unit ? `${parameters[0]} (${selected.unit})` : parameters[0] },
      dataZoom: [{ type: "inside" }, { type: "slider", height: 18, bottom: 10 }],
      series: sources.map((source, index) => ({
        name: source,
        type: "scatter",
        symbolSize: 5,
        large: true,
        data: data?.ft_parameter_points
          .filter((item) => item.source_id === source)
          .map((item) => [item.sequence, item.value, item.source_id, item.lot_id, item.status]) ?? [],
        markLine: index === 0 ? { silent: true, symbol: "none", data: markLineData } : undefined,
      })),
    };
  }, [data, parameters]);

  const comparisonColumns: ColumnsType<DatasetComparisonItem> = [
    { title: "Dataset", key: "dataset", width: 150, fixed: "left", render: (_, row) => `#${row.dataset_id} / V${row.version_no}` },
    { title: "产品", dataIndex: "product_name", width: 160, render: (value) => value || "—" },
    { title: "总数", dataIndex: "unit_count", width: 100 },
    { title: "PASS", dataIndex: "pass_count", width: 90 },
    { title: "FAIL", dataIndex: "fail_count", width: 90 },
    { title: "UNKNOWN", dataIndex: "unknown_count", width: 105 },
    { title: "ABORT", dataIndex: "abort_count", width: 90 },
    { title: "已知良率", dataIndex: "yield_rate", width: 110, render: percent },
    {
      title: "参数统计",
      dataIndex: "parameter_statistics",
      width: 340,
      render: (statistics: DatasetComparisonItem["parameter_statistics"]) => statistics.length
        ? <Space direction="vertical" size={0}>{statistics.map((item, index) => <Typography.Text key={`${item.name}-${index}`}>{item.name}：平均 {item.average ?? "—"} / 最小 {item.minimum ?? "—"} / 最大 {item.maximum ?? "—"} {item.unit ?? ""}</Typography.Text>)}</Space>
        : <Typography.Text type="secondary">选择参数后由服务端计算</Typography.Text>,
    },
  ];
  const detailColumns: ColumnsType<DatasetDetailRow> = [
    { title: "Unit", dataIndex: "logical_unit_key", width: 190, fixed: "left", ellipsis: true },
    { title: "Lot", dataIndex: "lot_id", width: 150, render: (value) => value || "—" },
    { title: "Wafer", dataIndex: "wafer_id", width: 110, render: (value) => value || "—" },
    { title: "X / Y", key: "coordinate", width: 105, render: (_, row) => row.x == null || row.y == null ? "—" : `${row.x} / ${row.y}` },
    { title: "Soft Bin", dataIndex: "soft_bin", width: 105, render: (value) => value || "—" },
    { title: "Hard Bin", dataIndex: "hard_bin", width: 105, render: (value) => value || "—" },
    { title: "结果", dataIndex: "overall_result", width: 105, render: (value) => <Tag>{value}</Tag> },
    { title: "源行", dataIndex: "source_row_no", width: 90, render: (value) => value ?? "—" },
    {
      title: "测量值",
      dataIndex: "measurements",
      width: 380,
      render: (measurements: DatasetDetailMeasurement[]) => measurements.length
        ? <Space direction="vertical" size={0}>{measurements.map((item, index) => <Typography.Text key={`${item.parameter}-${index}`}>{measurementValue(item)}</Typography.Text>)}</Space>
        : <Typography.Text type="secondary">{parameters.length ? "本 Unit 无对应测量值" : "选择参数后显示"}</Typography.Text>,
    },
  ];
  const onDetailPageChange = (pagination: TablePaginationConfig) => updateSearch((next) => {
    next.set("page", String(pagination.current ?? 1));
    next.set("page_size", String(pagination.pageSize ?? pageSize));
  });

  if (!selectedDatasets.length) {
    return <div className="workbench analytics-workbench">
      <div className="page-heading"><div><Typography.Title level={2}>正式数据分析</Typography.Title><Typography.Text type="secondary">从历史正式数据中选择 1–8 个 Dataset 后进入分析。</Typography.Text></div></div>
      <Card><Empty description="尚未选择 Dataset"><Button type="primary" icon={<ArrowLeftOutlined />} onClick={onOpenCatalog}>返回历史正式数据选择</Button></Empty></Card>
    </div>;
  }

  return (
    <div className="workbench analytics-workbench">
      <div className="page-heading">
        <div>
          <Typography.Text type="secondary">正式事实 / 服务端比较</Typography.Text>
          <Typography.Title level={2}>{data?.test_stage === "FT" ? "FT 数据分析" : data?.test_stage === "CP" ? "CP 数据分析" : "正式数据分析"}</Typography.Title>
          <Space wrap>{selectedDatasets.map((item) => <Tag color="blue" key={datasetKey(item)}>{datasetLabel(item)}</Tag>)}</Space>
        </div>
        <Space><Button icon={<ArrowLeftOutlined />} onClick={onOpenCatalog}>历史正式数据</Button><Button icon={<ReloadOutlined />} loading={comparisonQuery.isFetching || detailsQuery.isFetching || chartQuery.isFetching} onClick={() => void Promise.all([comparisonQuery.refetch(), detailsQuery.refetch(), chartQuery.refetch()])}>刷新</Button></Space>
      </div>

      <Card className="analytics-filter-card" title="比较与明细筛选">
        <Row gutter={[12, 12]}>
          <Col xs={24} lg={8}>
            <Typography.Text strong>当前图表与明细 Dataset</Typography.Text>
            <Select aria-label="当前图表与明细 Dataset" value={detailDataset ? datasetKey(detailDataset) : undefined} options={selectedDatasets.map((item) => ({ label: datasetLabel(item), value: datasetKey(item) }))} onChange={(value) => updateSearch((next) => { next.set("detail_dataset", value); next.set("page", "1"); next.delete("source_id"); })} className="full-width" />
          </Col>
          <Col xs={24} sm={12} lg={8}>
            <Typography.Text strong>Lot</Typography.Text>
            <Select aria-label="Lot 筛选" mode="multiple" allowClear value={lotIds} options={selectOptions(detailOptions?.lot_options ?? lotIds)} onChange={(values) => updateFilter("lot_id", values)} className="full-width" placeholder="全部 Lot" />
          </Col>
          <Col xs={24} sm={12} lg={8}>
            <Typography.Text strong>Wafer</Typography.Text>
            <Select aria-label="Wafer 筛选" mode="multiple" allowClear value={waferIds} options={selectOptions(detailOptions?.wafer_options ?? waferOptions.map((item) => item.wafer_id))} onChange={(values) => updateFilter("wafer_id", values)} className="full-width" placeholder="全部 Wafer" />
          </Col>
          <Col xs={24} sm={12} lg={8}>
            <Typography.Text strong>Bin</Typography.Text>
            <Select aria-label="Bin 筛选" mode="multiple" allowClear value={binCodes} options={selectOptions(detailOptions?.bin_options ?? binCodes)} onChange={(values) => updateFilter("bin_code", values)} className="full-width" placeholder="全部 Bin" />
          </Col>
          <Col xs={24} sm={12} lg={8}>
            <Typography.Text strong>参数（最多 20 个）</Typography.Text>
            <Select aria-label="参数筛选" mode="multiple" allowClear value={parameters} options={selectOptions(detailOptions?.parameter_options ?? parameters)} onChange={(values) => updateFilter("parameter", values.slice(0, 20))} className="full-width" placeholder="选择比较参数" />
          </Col>
          {data?.test_stage === "FT" && <Col xs={24} sm={12} lg={8}>
            <Typography.Text strong>图表源文件</Typography.Text>
            <Select aria-label="图表源文件" allowClear value={sourceId} options={selectOptions(data.source_options)} onChange={(value) => updateSearch((next) => { if (value) next.set("source_id", value); else next.delete("source_id"); })} className="full-width" placeholder="全部源文件" />
          </Col>}
        </Row>
        <Space wrap className="chart-filter-row">
          <Button onClick={() => updateSearch((next) => { for (const key of ["lot_id", "wafer_id", "bin_code", "parameter", "source_id"]) next.delete(key); next.set("page", "1"); })}>清空筛选</Button>
          <Typography.Text type="secondary">比较和明细使用全部筛选值；图表展示当前 Dataset，并使用 Lot、Wafer、参数筛选的首项。Bin 仅作用于比较和明细。</Typography.Text>
        </Space>
      </Card>

      {comparisonQuery.isError && <Alert type="error" showIcon message="Dataset 比较失败" description={comparisonQuery.error.message} className="review-alert" />}
      {comparisonQuery.data && <Card title={`服务端比较（${comparisonQuery.data.items.length} 个 Dataset）`} extra={<Tag color={comparisonQuery.data.spec_compatibility === "COMPATIBLE" ? "success" : "default"}>{compatibilityLabel[comparisonQuery.data.spec_compatibility] ?? comparisonQuery.data.spec_compatibility}</Tag>} className="production-table-card">
        <Table rowKey={(row) => `${row.dataset_id}-${row.version_no}`} columns={comparisonColumns} dataSource={comparisonQuery.data.items} pagination={false} scroll={{ x: 1180 }} />
      </Card>}

      {detailsQuery.isError && <Alert type="error" showIcon message="分页明细加载失败" description={detailsQuery.error.message} className="review-alert" />}
      <Card title={`${detailDataset ? datasetLabel(detailDataset) : "Dataset"} · Unit 明细`} className="production-table-card">
        <Table rowKey="unit_id" columns={detailColumns} dataSource={detailsQuery.data?.items ?? []} loading={detailsQuery.isLoading} scroll={{ x: 1340 }} pagination={{ current: detailsQuery.data?.page ?? page, pageSize: detailsQuery.data?.page_size ?? pageSize, total: detailsQuery.data?.total ?? 0, showSizeChanger: true, pageSizeOptions: [20, 50, 100, 200], showTotal: (total) => `共 ${total} 条` }} onChange={onDetailPageChange} />
      </Card>

      {chartQuery.isError && <Alert type="error" showIcon message="图表数据加载失败" description={chartQuery.error.message} className="review-alert" />}
      {data?.test_stage === "FT" && (
        <>
          <Alert type="info" showIcon message="良率仅使用明确 PASS/FAIL 作为分母" description={data.ft_sampled ? `图中显示确定性抽样数据并保留超规格点；当前参数共 ${data.ft_total_point_count.toLocaleString()} 个测量点。` : "UNKNOWN/ABORT 不会被误算为 FAIL；无已知分母时良率显示为未知。"} className="review-alert" />
          <Row gutter={[16, 16]} className="analytics-stats">
            <Col xs={12} md={6}><Card><Statistic title="产品" value={data.product_name ?? "—"} valueStyle={{ fontSize: 20, lineHeight: 1.25, overflowWrap: "anywhere", whiteSpace: "normal" }} /></Card></Col>
            <Col xs={12} md={6}><Card><Statistic title="源文件 Run" value={data.source_options.length} /></Card></Col>
            <Col xs={12} md={6}><Card><Statistic title="参数" value={data.parameter_options.length} /></Card></Col>
            <Col xs={12} md={6}><Card><Statistic title="当前参数测量点" value={data.ft_total_point_count} /></Card></Col>
          </Row>
          <Row gutter={[18, 18]}>
            <Col xs={24} xl={18}><Card title={`${parameters[0] ?? "参数"} 器件散点`} className="chart-card">{parameters[0] ? <EChart option={ftScatterOption} /> : <Empty description="选择参数后显示散点" />}</Card></Col>
            <Col xs={24} xl={6}><Card title="参数规格" className="chart-card"><Space direction="vertical" size="large"><Statistic title="LSL" value={selectedFtParameter?.lsl ?? "—"} suffix={selectedFtParameter?.unit ?? ""} /><Statistic title="USL" value={selectedFtParameter?.usl ?? "—"} suffix={selectedFtParameter?.unit ?? ""} /><Statistic title="图中缺失值" value={missingFtPoints} /><Typography.Text type="secondary">测试条件：{selectedFtParameter?.test_condition || "源文件未提供"}</Typography.Text></Space></Card></Col>
          </Row>
        </>
      )}
      {data?.test_stage !== "FT" && data && (
        <>
          <Row gutter={[16, 16]} className="analytics-stats">
            <Col xs={12} md={6}><Card><Statistic title="Lot" value={data.lot_options.length} /></Card></Col>
            <Col xs={12} md={6}><Card><Statistic title="Wafer" value={data.wafer_options.length} /></Card></Col>
            <Col xs={12} md={6}><Card><Statistic title="当前 Die" value={data.bin_counts.reduce((sum, item) => sum + item.unit_count, 0)} /></Card></Col>
            <Col xs={12} md={6}><Card><Statistic title="当前 Wafer Yield" value={selectedWafer?.yield_rate == null ? "—" : selectedWafer.yield_rate * 100} precision={selectedWafer?.yield_rate == null ? undefined : 3} suffix={selectedWafer?.yield_rate == null ? "" : "%"} /></Card></Col>
          </Row>
          <Row gutter={[18, 18]}>
            <Col xs={24} xl={14}><Card title="Wafer Yield 趋势" className="chart-card"><EChart option={yieldOption} /></Card></Col>
            <Col xs={24} xl={10}><Card title="Bin Pareto" className="chart-card"><EChart option={paretoOption} /></Card></Col>
            <Col xs={24} xl={14}><Card title="Bin Wafer Map" className="chart-card">{data.wafer_map.length ? <EChart option={waferMapOption} className="wafer-map-canvas" /> : <Empty description="选择一个 Lot 和 Wafer 后显示晶圆图" />}</Card></Col>
            <Col xs={24} xl={10}><Card title="Bin 分布" className="chart-card bin-grid-card"><div className="bin-grid">{data.bin_counts.map((item, index) => <div className="bin-tile" key={item.soft_bin} style={{ borderTopColor: BIN_COLORS[index % BIN_COLORS.length] }}><span>Bin {item.soft_bin}</span><strong>{item.unit_count.toLocaleString()}</strong><em>{(item.percent * 100).toFixed(2)}%</em></div>)}</div></Card></Col>
          </Row>
        </>
      )}
    </div>
  );
}
