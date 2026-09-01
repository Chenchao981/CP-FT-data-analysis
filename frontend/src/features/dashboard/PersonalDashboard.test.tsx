// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { PersonalDashboard } from "./PersonalDashboard";

vi.mock("../../components/EChart", () => ({
  EChart: ({ ariaLabel }: { ariaLabel?: string }) => <div role="img" aria-label={ariaLabel} />,
}));

Object.defineProperty(window, "matchMedia", {
  writable: true,
  value: (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => undefined,
    removeListener: () => undefined,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    dispatchEvent: () => false,
  }),
});

describe("PersonalDashboard demo", () => {
  afterEach(cleanup);

  it("labels all metrics as demo data and exposes the planned cockpit sections", () => {
    render(<PersonalDashboard userName="测试员" onNavigate={vi.fn()} />);

    expect(screen.getByText("早上好，测试员")).toBeInTheDocument();
    expect(screen.getByText("演示数据 · 未连接生产")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "CP FT 良率与处理量演示趋势" })).toBeInTheDocument();
    expect(screen.getByText("我的今日关注")).toBeInTheDocument();
    expect(screen.getByText("快速进入")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /进入质量总览/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: /快速分析/ })).toBeDisabled();
  });

  it("uses explicit product routes for cockpit shortcuts", () => {
    const navigate = vi.fn();
    render(<PersonalDashboard userName="测试员" onNavigate={navigate} />);

    fireEvent.click(screen.getByRole("button", { name: /量产 FT/ }));
    expect(navigate).toHaveBeenCalledWith("/production/ft");
    fireEvent.click(screen.getByRole("button", { name: /查看正式数据/ }));
    expect(navigate).toHaveBeenCalledWith("/datasets/current");
  });
});
