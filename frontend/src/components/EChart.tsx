import { BarChart, BoxplotChart, HeatmapChart, LineChart, ScatterChart } from "echarts/charts";
import {
  BrushComponent,
  DataZoomComponent,
  GridComponent,
  LegendComponent,
  MarkLineComponent,
  ToolboxComponent,
  TooltipComponent,
  VisualMapComponent,
} from "echarts/components";
import { init, use, type ECharts, type EChartsCoreOption } from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import { useEffect, useRef } from "react";

use([
  BarChart,
  BoxplotChart,
  HeatmapChart,
  LineChart,
  ScatterChart,
  BrushComponent,
  DataZoomComponent,
  GridComponent,
  LegendComponent,
  MarkLineComponent,
  ToolboxComponent,
  TooltipComponent,
  VisualMapComponent,
  CanvasRenderer,
]);

/** Event payloads are display events, never trusted drilldown identities. */
export type EChartEventHandler = (payload: unknown) => void;
export type EChartEventMap = Readonly<Record<string, EChartEventHandler>>;

interface Props {
  option: EChartsCoreOption;
  className?: string;
  ariaLabel?: string;
  onEvents?: EChartEventMap;
  onReady?: (chart: ECharts) => void;
}

export function EChart({ option, className, ariaLabel, onEvents, onReady }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const chartRef = useRef<ECharts | null>(null);
  const onReadyRef = useRef(onReady);
  onReadyRef.current = onReady;

  useEffect(() => {
    if (!ref.current) return;
    const chart = init(ref.current);
    chartRef.current = chart;
    onReadyRef.current?.(chart);
    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(ref.current);
    return () => {
      observer.disconnect();
      chart.dispose();
      chartRef.current = null;
    };
  }, []);

  useEffect(() => {
    chartRef.current?.setOption(option, true);
  }, [option]);

  useEffect(() => {
    const chart = chartRef.current;
    if (!chart || !onEvents) return;
    const entries = Object.entries(onEvents).filter(([eventName]) => eventName.trim().length > 0);
    for (const [eventName, handler] of entries) chart.on(eventName, handler);
    return () => {
      for (const [eventName, handler] of entries) chart.off(eventName, handler);
    };
  }, [onEvents]);

  return <div ref={ref} className={className ?? "chart-canvas"} role={ariaLabel ? "img" : undefined} aria-label={ariaLabel} />;
}
