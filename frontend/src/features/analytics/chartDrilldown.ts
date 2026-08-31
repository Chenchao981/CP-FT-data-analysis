/**
 * Chart coordinates, series indices and ECharts dataIndex values are display
 * details. Only a backend-issued key embedded in the datum is an identity.
 */
export function drilldownKeyFromChartEvent(payload: unknown): string | null {
  if (typeof payload !== "object" || payload === null || !("data" in payload)) return null;
  const data = (payload as { data?: unknown }).data;
  if (typeof data !== "object" || data === null || !("drilldownKey" in data)) return null;
  const key = (data as { drilldownKey?: unknown }).drilldownKey;
  return typeof key === "string" && /^UNIT:[1-9][0-9]{0,18}$/.test(key) ? key : null;
}
