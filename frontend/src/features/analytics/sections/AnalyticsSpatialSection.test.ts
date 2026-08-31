// @vitest-environment jsdom

import { describe, expect, it } from "vitest";

import { drilldownKeyFromChartEvent } from "../chartDrilldown";

describe("drilldownKeyFromChartEvent", () => {
  it("accepts only the backend key embedded in the chart datum", () => {
    expect(drilldownKeyFromChartEvent({ data: { drilldownKey: "UNIT:501" }, dataIndex: 99, unit_id: 99 })).toBe("UNIT:501");
    expect(drilldownKeyFromChartEvent({ data: { drilldownKey: "UNIT:0001" } })).toBeNull();
    expect(drilldownKeyFromChartEvent({ data: { drilldown_key: "UNIT:501" } })).toBeNull();
    expect(drilldownKeyFromChartEvent({ dataIndex: 501, unit_id: 501 })).toBeNull();
    expect(drilldownKeyFromChartEvent(null)).toBeNull();
  });
});
