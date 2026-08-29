import { useQuery } from "@tanstack/react-query";
import { Alert, Button, Card, Col, Empty, Form, InputNumber, Row, Select, Space, Statistic, Tag, Typography } from "antd";
import type { EChartsOption } from "echarts";
import { useEffect, useMemo, useState } from "react";

import { getDatasetChartData } from "../../api/datasets";
import { EChart } from "../../components/EChart";

interface DatasetSelection { datasetId: number; versionNo: number }
interface LoadForm { dataset_id: number; version_no: number }

export interface AnalyticsWorkbenchProps {
  initialSelection?: DatasetSelection;
  onSelectionChange?: (datasetId: number, versionNo: number) => void;
}

const BIN_COLORS = ["#2d9d78", "#d64545", "#f0a429", "#7b61a8", "#247ba0", "#8d6e63", "#607d8b"];

export function AnalyticsWorkbench({ initialSelection, onSelectionChange }: AnalyticsWorkbenchProps) {
  const [loadForm] = Form.useForm<LoadForm>();
  const [selection, setSelection] = useState<DatasetSelection | undefined>(initialSelection);
  const [lotId, setLotId] = useState<string>();
  const [waferId, setWaferId] = useState<string>();
  const [sourceId, setSourceId] = useState<string>();
  const [parameter, setParameter] = useState<string>();
  const query = useQuery({
    queryKey: ["dataset-charts", selection, lotId, waferId, sourceId, parameter],
    queryFn: () => getDatasetChartData(selection!.datasetId, selection!.versionNo, lotId, waferId, sourceId, parameter),
    enabled: Boolean(selection),
  });
  const data = query.data;
  useEffect(() => {
    setSelection(initialSelection);
    setLotId(undefined);
    setWaferId(undefined);
    setSourceId(undefined);
    setParameter(undefined);
    loadForm.resetFields();
    if (initialSelection) loadForm.setFieldsValue({ dataset_id: initialSelection.datasetId, version_no: initialSelection.versionNo });
  }, [initialSelection?.datasetId, initialSelection?.versionNo, loadForm]);
  useEffect(() => {
    if (data?.test_stage === "FT" && !parameter && data.parameter_options.length) {
      setParameter(data.parameter_options[0].name);
    }
  }, [data, parameter]);
  const waferOptions = data?.wafer_options.filter((item) => !lotId || item.lot_id === lotId) ?? [];

  const yieldOption = useMemo<EChartsOption>(() => ({
    color: ["#1167a8"],
    tooltip: { trigger: "axis", valueFormatter: (value) => `${Number(value).toFixed(3)}%` },
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
      data: data?.wafer_yield.map((item) => Number((item.yield_rate * 100).toFixed(4))) ?? [],
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

  const selectedWafer = data?.wafer_yield.find((item) => item.lot_id === lotId && item.wafer_id === waferId);
  const selectedFtParameter = data?.parameter_options.find((item) => item.name === parameter);
  const missingFtPoints = data?.ft_parameter_points.filter((item) => item.status === "MISSING").length ?? 0;
  const ftScatterOption = useMemo<EChartsOption>(() => {
    const sources = data?.source_options ?? [];
    const selected = data?.parameter_options.find((item) => item.name === parameter);
    const markLineData: Array<{ name: string; yAxis: number; lineStyle: { color: string } }> = [];
    if (selected?.lsl !== null && selected?.lsl !== undefined) markLineData.push({ name: "LSL", yAxis: selected.lsl, lineStyle: { color: "#d64545" } });
    if (selected?.usl !== null && selected?.usl !== undefined) markLineData.push({ name: "USL", yAxis: selected.usl, lineStyle: { color: "#d64545" } });
    return {
      color: BIN_COLORS,
      tooltip: {
        trigger: "item",
        formatter: (item: unknown) => {
          const point = (item as { data: [number, number | null, string, string, string] }).data;
          return `${point[2]} · NUM ${point[0]}<br/>${parameter ?? "参数"}: ${point[1] ?? "缺失"}<br/>Lot ${point[3]} · ${point[4]}`;
        },
      },
      legend: { data: sources, type: "scroll" as const },
      grid: { left: 72, right: 32, top: 56, bottom: 64 },
      xAxis: { type: "value", name: "NUM", minInterval: 1 },
      yAxis: { type: "value", name: selected?.unit ? `${parameter} (${selected.unit})` : parameter },
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
  }, [data, parameter]);

  return (
    <div className="workbench analytics-workbench">
      <div className="page-heading">
        <div>
          <Typography.Title level={2}>{data?.test_stage === "FT" ? "FT 参数分析" : data ? "CP 分析图表" : "CP / FT 分析图表"}</Typography.Title>
          <Typography.Text type="secondary">CP 显示 Yield、Bin 和 Wafer Map；FT 显示器件级参数散点、测试条件和规格线。</Typography.Text>
        </div>
        <Tag color="blue">Dataset Version</Tag>
      </div>

      <Card className="analytics-filter-card">
        <Form<LoadForm>
          form={loadForm}
          layout="inline"
          initialValues={initialSelection ? { dataset_id: initialSelection.datasetId, version_no: initialSelection.versionNo } : undefined}
          onFinish={(values) => {
            setLotId(undefined);
            setWaferId(undefined);
            setSourceId(undefined);
            setParameter(undefined);
            setSelection({ datasetId: values.dataset_id, versionNo: values.version_no });
            onSelectionChange?.(values.dataset_id, values.version_no);
          }}
        >
          <Form.Item label="Dataset编号" name="dataset_id" rules={[{ required: true }]}><InputNumber min={1} precision={0} /></Form.Item>
          <Form.Item label="版本" name="version_no" rules={[{ required: true }]}><InputNumber min={1} precision={0} /></Form.Item>
          <Button type="primary" htmlType="submit" loading={query.isFetching}>加载图表</Button>
        </Form>
        {data && (
          <Space wrap className="chart-filter-row">
            <Typography.Text strong>范围</Typography.Text>
            <Select
              allowClear
              placeholder="全部 Lot"
              value={lotId}
              options={data.lot_options.map((value) => ({ label: value, value }))}
              onChange={(value) => {
                setLotId(value);
                setWaferId(undefined);
                setSourceId(undefined);
                setParameter(undefined);
              }}
              className="chart-select"
            />
            {data.test_stage === "FT" ? (
              <>
                <Select
                  allowClear
                  placeholder="全部源文件"
                  value={sourceId}
                  options={data.source_options.map((value) => ({ label: value, value }))}
                  onChange={(value) => {
                    setSourceId(value);
                    setParameter(undefined);
                  }}
                  className="chart-select"
                />
                <Select showSearch placeholder="选择参数" value={parameter} options={data.parameter_options.map((item) => ({ label: item.unit ? `${item.name} (${item.unit})` : item.name, value: item.name }))} onChange={setParameter} className="chart-select" />
              </>
            ) : (
              <Select
                allowClear
                disabled={!lotId}
                placeholder="选择 Wafer 后显示 Map"
                value={waferId}
                options={waferOptions.map((item) => ({ label: item.wafer_id, value: item.wafer_id }))}
                onChange={setWaferId}
                className="chart-select"
              />
            )}
          </Space>
        )}
      </Card>

      {query.isError && <Alert type="error" showIcon message="图表数据加载失败" description={query.error.message} className="review-alert" />}
      {!selection && <Card><Empty description="输入 Dataset 编号和版本后加载分析图表" /></Card>}
      {data?.test_stage === "FT" && (
        <>
          <Alert type="info" showIcon message="当前 FT 源数据未提供可发布的 PASS/FAIL 或 Bin，系统不计算良率" description={data.ft_sampled ? `图中显示确定性抽样数据并保留超规格点；当前参数共 ${data.ft_total_point_count.toLocaleString()} 个测量点。` : "图中显示当前参数的全部测量点。"} className="review-alert" />
          <Row gutter={[16, 16]} className="analytics-stats">
            <Col xs={12} md={6}>
              <Card>
                <Statistic
                  title="产品"
                  value={data.product_name ?? "-"}
                  valueStyle={{ fontSize: 20, lineHeight: 1.25, overflowWrap: "anywhere", whiteSpace: "normal" }}
                />
              </Card>
            </Col>
            <Col xs={12} md={6}><Card><Statistic title="源文件 Run" value={data.source_options.length} /></Card></Col>
            <Col xs={12} md={6}><Card><Statistic title="参数" value={data.parameter_options.length} /></Card></Col>
            <Col xs={12} md={6}><Card><Statistic title="当前参数测量点" value={data.ft_total_point_count} /></Card></Col>
          </Row>
          <Row gutter={[18, 18]}>
            <Col xs={24} xl={18}><Card title={`${parameter ?? "参数"} 器件散点`} className="chart-card">{parameter ? <EChart option={ftScatterOption} /> : <Empty description="请选择参数" />}</Card></Col>
            <Col xs={24} xl={6}>
              <Card title="参数规格" className="chart-card">
                <Space direction="vertical" size="large">
                  <Statistic title="LSL" value={selectedFtParameter?.lsl ?? "-"} suffix={selectedFtParameter?.unit ?? ""} />
                  <Statistic title="USL" value={selectedFtParameter?.usl ?? "-"} suffix={selectedFtParameter?.unit ?? ""} />
                  <Statistic title="图中缺失值" value={missingFtPoints} />
                  <Typography.Text type="secondary">测试条件：{selectedFtParameter?.test_condition || "源文件未提供"}</Typography.Text>
                </Space>
              </Card>
            </Col>
          </Row>
        </>
      )}
      {data?.test_stage !== "FT" && data && (
        <>
          <Row gutter={[16, 16]} className="analytics-stats">
            <Col xs={12} md={6}><Card><Statistic title="Lot" value={data.lot_options.length} /></Card></Col>
            <Col xs={12} md={6}><Card><Statistic title="Wafer" value={data.wafer_options.length} /></Card></Col>
            <Col xs={12} md={6}><Card><Statistic title="当前 Die" value={data.bin_counts.reduce((sum, item) => sum + item.unit_count, 0)} /></Card></Col>
            <Col xs={12} md={6}><Card><Statistic title="当前 Wafer Yield" value={selectedWafer ? selectedWafer.yield_rate * 100 : undefined} precision={3} suffix={selectedWafer ? "%" : ""} /></Card></Col>
          </Row>
          <Row gutter={[18, 18]}>
            <Col xs={24} xl={14}><Card title="Wafer Yield 趋势" className="chart-card"><EChart option={yieldOption} /></Card></Col>
            <Col xs={24} xl={10}><Card title="Bin Pareto" className="chart-card"><EChart option={paretoOption} /></Card></Col>
            <Col xs={24} xl={14}>
              <Card title="Bin Wafer Map" className="chart-card">
                {data.wafer_map.length ? <EChart option={waferMapOption} className="wafer-map-canvas" /> : <Empty description="选择一个 Lot 和 Wafer 后显示晶圆图" />}
              </Card>
            </Col>
            <Col xs={24} xl={10}>
              <Card title="Bin 分布" className="chart-card bin-grid-card">
                <div className="bin-grid">
                  {data.bin_counts.map((item, index) => (
                    <div className="bin-tile" key={item.soft_bin} style={{ borderTopColor: BIN_COLORS[index % BIN_COLORS.length] }}>
                      <span>Bin {item.soft_bin}</span><strong>{item.unit_count.toLocaleString()}</strong><em>{(item.percent * 100).toFixed(2)}%</em>
                    </div>
                  ))}
                </div>
              </Card>
            </Col>
          </Row>
        </>
      )}
    </div>
  );
}
