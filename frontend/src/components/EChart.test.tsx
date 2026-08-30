// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { EChart } from "./EChart";

const chartMocks = vi.hoisted(() => ({
  setOption: vi.fn(),
  resize: vi.fn(),
  dispose: vi.fn(),
}));

vi.mock("echarts", () => ({
  init: vi.fn(() => chartMocks),
}));

vi.stubGlobal("ResizeObserver", class {
  observe() { return undefined; }
  unobserve() { return undefined; }
  disconnect() { return undefined; }
});

describe("EChart accessibility", () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("exposes an optional chart-specific aria label", () => {
    const view = render(<EChart option={{ xAxis: {}, yAxis: {}, series: [] }} ariaLabel="VTH 箱线图" />);

    expect(screen.getByRole("img", { name: "VTH 箱线图" })).toBeInTheDocument();
    expect(chartMocks.setOption).toHaveBeenCalledOnce();

    view.unmount();
    expect(chartMocks.dispose).toHaveBeenCalledOnce();
  });
});
