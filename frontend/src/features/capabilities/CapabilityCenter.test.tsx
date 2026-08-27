// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { CapabilityCenter } from "./CapabilityCenter";
import { formalFactoryOptions, isFormalFactory } from "./capabilityCatalog";

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

describe("CapabilityCenter", () => {
  it("separates formal factories from custom tools", () => {
    render(<CapabilityCenter />);
    expect(screen.getByText("能力中心")).toBeInTheDocument();
    expect(screen.getByText("国宇 FRD Excel 清洗")).toBeInTheDocument();
    expect(screen.getByText("立昂微-管芯数")).toBeInTheDocument();
    expect(screen.getByText("日月新")).toBeInTheDocument();
    expect(screen.getByText("日月光")).toBeInTheDocument();
  });

  it("keeps the formal CP selector limited to three wafer fabs", () => {
    expect(formalFactoryOptions.CP.map((item) => item.value)).toEqual([
      "huahong",
      "jetech",
      "lion",
    ]);
    expect(isFormalFactory("CP", "guoyu")).toBe(false);
    expect(isFormalFactory("FT", "ASE")).toBe(false);
    expect(isFormalFactory("FT", "riyueguang")).toBe(true);
  });
});
