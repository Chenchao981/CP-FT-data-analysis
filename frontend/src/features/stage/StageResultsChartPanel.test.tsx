// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { StageResultRow } from "../../api/stageData";
import { StageResultsChartPanel } from "./StageResultsChartPanel";

vi.mock("../../components/EChart", () => ({
  EChart: ({ ariaLabel, option, onEvents }: { ariaLabel: string; option: unknown; onEvents?: { click?: (payload: unknown) => void } }) => (
    <button type="button" role="img" aria-label={ariaLabel} data-option={JSON.stringify(option)} onClick={() => onEvents?.click?.({ data: { value: 100, datasetId: 20, versionNo: 3 } })} />
  ),
}));

const rows: StageResultRow[] = [{
  result_summary_id: 1,
  import_batch_id: 14,
  data_name: "result-1",
  product_name: "PRODUCT-1",
  lot_id: "LOT-1",
  wafer_count: null,
  factory_code: "riyuexin",
  uploader_login: "operator",
  uploader_name: "操作员",
  can_manage: true,
  test_item_count: 10,
  unit_count: 100,
  pass_count: null,
  yield_rate: null,
  status: "PROCESSED",
  data_type: "FT",
  dataset_id: 20,
  dataset_version_no: 3,
  created_at_utc: "2026-09-03T00:00:00Z",
}];

describe("StageResultsChartPanel", () => {
  afterEach(cleanup);

  it("shows a visible result chart, preserves unknown yield, and opens the exact dataset", () => {
    const onOpenAnalytics = vi.fn();
    render(<StageResultsChartPanel testStage="FT" rows={rows} loading={false} canOpenAnalytics onOpenAnalytics={onOpenAnalytics} />);

    const chart = screen.getByRole("img", { name: "FT 最近清洗结果图表" });
    const option = JSON.parse(chart.getAttribute("data-option") ?? "{}");
    expect(option.series[0].data[0]).toEqual(expect.objectContaining({ value: 100, datasetId: 20, versionNo: 3 }));
    expect(option.series[1].data[0].value).toBeNull();
    expect(screen.getByText("部分结果没有已知良率")).toBeInTheDocument();

    fireEvent.click(chart);
    fireEvent.click(screen.getByRole("button", { name: /打开最新完整图表/ }));
    expect(onOpenAnalytics).toHaveBeenNthCalledWith(1, 20, 3);
    expect(onOpenAnalytics).toHaveBeenNthCalledWith(2, 20, 3);
  });
});
