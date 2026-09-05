// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { PatResultView } from "./PatResultView";
vi.mock("../../components/EChart", () => ({ EChart: () => null }));
Object.defineProperty(window, "matchMedia", { writable: true, value: vi.fn().mockImplementation(query => ({ matches: false, media: query, onchange: null, addListener: vi.fn(), removeListener: vi.fn(), addEventListener: vi.fn(), removeEventListener: vi.fn(), dispatchEvent: vi.fn() })) });
afterEach(cleanup);
it("does not turn missing outlier evidence into zero", () => {
  render(<PatResultView scope="PERSONAL" rows={[{ key: "v", label: "V", count: 100 }]} />);
  expect(screen.getByText("未知")).toBeInTheDocument();
  expect(screen.getByText("个人分析结果")).toBeInTheDocument();
});
