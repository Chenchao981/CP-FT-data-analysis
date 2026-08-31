// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { EChart } from "./EChart";

const chartMocks = vi.hoisted(() => {
  const handlers = new Map<string, (payload: unknown) => void>();
  const chart = {
    setOption: vi.fn(),
    resize: vi.fn(),
    dispose: vi.fn(),
    on: vi.fn((eventName: string, handler: (payload: unknown) => void) => {
      handlers.set(eventName, handler);
    }),
    off: vi.fn((eventName: string, handler: (payload: unknown) => void) => {
      if (handlers.get(eventName) === handler) handlers.delete(eventName);
    }),
  };
  return {
    chart,
    handlers,
    init: vi.fn(() => chart),
    use: vi.fn(),
  };
});

vi.mock("echarts/core", () => ({
  init: chartMocks.init,
  use: chartMocks.use,
}));
vi.mock("echarts/charts", () => ({
  BarChart: { type: "bar" },
  BoxplotChart: { type: "boxplot" },
  HeatmapChart: { type: "heatmap" },
  LineChart: { type: "line" },
  ScatterChart: { type: "scatter" },
}));
vi.mock("echarts/components", () => ({
  BrushComponent: { type: "brush" },
  DataZoomComponent: { type: "dataZoom" },
  GridComponent: { type: "grid" },
  LegendComponent: { type: "legend" },
  MarkLineComponent: { type: "markLine" },
  ToolboxComponent: { type: "toolbox" },
  TooltipComponent: { type: "tooltip" },
  VisualMapComponent: { type: "visualMap" },
}));
vi.mock("echarts/renderers", () => ({ CanvasRenderer: { type: "canvas" } }));

let resizeCallback: ResizeObserverCallback | undefined;
const observerMocks = {
  observe: vi.fn(),
  disconnect: vi.fn(),
};

describe("EChart wrapper", () => {
  beforeEach(() => {
    resizeCallback = undefined;
    vi.stubGlobal("ResizeObserver", class {
      constructor(callback: ResizeObserverCallback) { resizeCallback = callback; }
      observe = observerMocks.observe;
      unobserve() { return undefined; }
      disconnect = observerMocks.disconnect;
    });
  });

  afterEach(() => {
    cleanup();
    chartMocks.handlers.clear();
    vi.clearAllMocks();
  });

  it("exposes an optional chart-specific aria label", () => {
    const view = render(<EChart option={{ xAxis: {}, yAxis: {}, series: [] }} ariaLabel="VTH 箱线图" />);

    expect(screen.getByRole("img", { name: "VTH 箱线图" })).toBeInTheDocument();
    expect(chartMocks.init).toHaveBeenCalledOnce();
    expect(chartMocks.chart.setOption).toHaveBeenCalledOnce();

    view.unmount();
    expect(chartMocks.chart.dispose).toHaveBeenCalledOnce();
    expect(observerMocks.disconnect).toHaveBeenCalledOnce();
  });

  it("initializes once, updates options in place and reports the stable chart through onReady", () => {
    const onReady = vi.fn();
    const firstOption = { xAxis: {}, yAxis: {}, series: [{ type: "line" as const, data: [1] }] };
    const secondOption = { xAxis: {}, yAxis: {}, series: [{ type: "line" as const, data: [2] }] };
    const view = render(<EChart option={firstOption} onReady={onReady} />);

    expect(onReady).toHaveBeenCalledOnce();
    expect(onReady).toHaveBeenCalledWith(chartMocks.chart);
    expect(chartMocks.chart.setOption).toHaveBeenLastCalledWith(firstOption, true);

    view.rerender(<EChart option={secondOption} onReady={onReady} />);

    expect(chartMocks.init).toHaveBeenCalledOnce();
    expect(chartMocks.chart.dispose).not.toHaveBeenCalled();
    expect(chartMocks.chart.setOption).toHaveBeenCalledTimes(2);
    expect(chartMocks.chart.setOption).toHaveBeenLastCalledWith(secondOption, true);
    expect(onReady).toHaveBeenCalledOnce();
  });

  it("rebinds event handlers without recreating the chart and forwards an opaque payload", () => {
    const firstClick = vi.fn();
    const secondClick = vi.fn();
    const payload = { seriesIndex: 0, dataIndex: 2, untrustedUnitId: 999 };
    const option = { xAxis: {}, yAxis: {}, series: [] };
    const view = render(<EChart option={option} onEvents={{ click: firstClick }} />);

    expect(chartMocks.handlers.get("click")).toBe(firstClick);
    chartMocks.handlers.get("click")?.(payload);
    expect(firstClick).toHaveBeenCalledWith(payload);

    view.rerender(<EChart option={option} onEvents={{ click: secondClick }} />);

    expect(chartMocks.chart.off).toHaveBeenCalledWith("click", firstClick);
    expect(chartMocks.handlers.get("click")).toBe(secondClick);
    expect(chartMocks.init).toHaveBeenCalledOnce();
    expect(chartMocks.chart.dispose).not.toHaveBeenCalled();
  });

  it("ignores blank event names and resizes through ResizeObserver", () => {
    render(<EChart option={{}} onEvents={{ " ": vi.fn() }} />);

    expect(chartMocks.chart.on).not.toHaveBeenCalled();
    expect(resizeCallback).toBeTypeOf("function");
    resizeCallback?.([], {} as ResizeObserver);
    expect(chartMocks.chart.resize).toHaveBeenCalledOnce();
  });
});
