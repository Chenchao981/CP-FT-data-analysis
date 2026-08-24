import { useQuery } from "@tanstack/react-query";
import { Alert, Button, Card, Col, Empty, Form, InputNumber, Row, Select, Space, Statistic, Tag, Typography } from "antd";
import type { EChartsOption } from "echarts";
import { useMemo, useState } from "react";

import { getDatasetChartData } from "../../api/datasets";
import { EChart } from "../../components/EChart";

interface DatasetSelection { datasetId: number; versionNo: number }
interface LoadForm { dataset_id: number; version_no: number }

export interface AnalyticsWorkbenchProps {
  initialSelection?: DatasetSelection;
}

const BIN_COLORS = ["#2d9d78", "#d64545", "#f0a429", "#7b61a8", "#247ba0", "#8d6e63", "#607d8b"];

export function AnalyticsWorkbench({ initialSelection }: AnalyticsWorkbenchProps) {
  const [selection, setSelection] = useState<DatasetSelection | undefined>(initialSelection);
  const [lotId, setLotId] = useState<string>();
  const [waferId, setWaferId] = useState<string>();
  const query = useQuery({
    queryKey: ["dataset-charts", selection, lotId, waferId],
    queryFn: () => getDatasetChartData(selection!.datasetId, selection!.versionNo, lotId, waferId),
    enabled: Boolean(selection),
  });
  const data = query.data;
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

  return (
    <div className="workbench analytics-workbench">
      <div className="page-heading">
        <div>
          <Typography.Title level={2}>CP / FT 分析图表</Typography.Title>
          <Typography.Text type="secondary">交互与布局依据 VDMOS Tool v8.9；当前首批覆盖 Wafer Yield、Bin Pareto 和 Bin Wafer Map。</Typography.Text>
        </div>
        <Tag color="blue">Dataset Version</Tag>
      </div>

      <Card className="analytics-filter-card">
        <Form<LoadForm>
          layout="inline"
          initialValues={initialSelection ? { dataset_id: initialSelection.datasetId, version_no: initialSelection.versionNo } : undefined}
          onFinish={(values) => {
            setLotId(undefined);
            setWaferId(undefined);
            setSelection({ datasetId: values.dataset_id, versionNo: values.version_no });
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
              onChange={(value) => { setLotId(value); setWaferId(undefined); }}
              className="chart-select"
            />
            <Select
              allowClear
              disabled={!lotId}
              placeholder="选择 Wafer 后显示 Map"
              value={waferId}
              options={waferOptions.map((item) => ({ label: item.wafer_id, value: item.wafer_id }))}
              onChange={setWaferId}
              className="chart-select"
            />
          </Space>
        )}
      </Card>

      {query.isError && <Alert type="error" showIcon message="图表数据加载失败" description={query.error.message} className="review-alert" />}
      {!selection && <Card><Empty description="输入 Dataset 编号和版本后加载分析图表" /></Card>}
      {data && (
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
