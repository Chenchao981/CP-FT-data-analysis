import type { EChartsCoreOption } from "echarts/core";

import type {
  QualityBinCooccurrenceCell,
  QualityMarginGroupResult,
  QualitySblBinLimit,
  QualitySpcGroupResult,
  QualitySylDatasetLimit,
} from "../../api/qualityEvaluation";

export type QualityPercentAxisMode = "AUTO" | "FIXED_0_100";

const percentValue = (value: number | null) => value == null ? null : Number((value * 100).toFixed(6));
const scopeKey = (datasetId: number, versionNo: number, groupKey: string) => `${datasetId}:${versionNo}:${groupKey}`;

function toolbox(name: string, brush = false) {
  return {
    feature: {
      dataZoom: {},
      restore: {},
      ...(brush ? { brush: { type: ["rect", "polygon", "clear"] } } : {}),
      saveAsImage: { name },
    },
  };
}

function percentAxis(name: string, mode: QualityPercentAxisMode) {
  return {
    type: "value" as const,
    name,
    min: mode === "FIXED_0_100" ? 0 : undefined,
    max: mode === "FIXED_0_100" ? 100 : undefined,
    axisLabel: { formatter: "{value}%" },
  };
}

export function spcIMrOption(group: QualitySpcGroupResult | undefined): EChartsCoreOption {
  const points = group?.points ?? [];
  return {
    animation: false,
    tooltip: {
      trigger: "item",
      formatter: (payload: unknown) => {
        const data = (payload as { data?: { value?: unknown; ruleHits?: unknown } })?.data;
        const value = Array.isArray(data?.value) ? data.value : [];
        const hits = Array.isArray(data?.ruleHits) && data.ruleHits.length ? data.ruleHits.join(", ") : "无";
        return `Sequence ${value[0] ?? "—"}<br/>Value ${value[1] ?? "—"}<br/>Rule hits: ${hits}`;
      },
    },
    axisPointer: { link: [{ xAxisIndex: "all" }] },
    grid: [
      { left: 76, right: 34, top: 58, height: "47%" },
      { left: 76, right: 34, top: "68%", height: "18%" },
    ],
    xAxis: [
      { type: "category", name: "Unit Sequence", data: points.map((item) => item.sequence), gridIndex: 0, axisLabel: { show: false } },
      { type: "category", name: "Unit Sequence", data: points.map((item) => item.sequence), gridIndex: 1 },
    ],
    yAxis: [
      { type: "value", name: "Individuals (I)", scale: true, gridIndex: 0 },
      { type: "value", name: "Moving Range (MR)", min: 0, gridIndex: 1 },
    ],
    dataZoom: [
      { type: "inside", xAxisIndex: [0, 1] },
      { type: "slider", xAxisIndex: [0, 1], bottom: 6, height: 18 },
    ],
    brush: { toolbox: ["rect", "clear"], xAxisIndex: "all" },
    toolbox: toolbox("spc-i-mr", true),
    series: [
      {
        name: "Individuals (I)",
        type: "line",
        xAxisIndex: 0,
        yAxisIndex: 0,
        showSymbol: points.length <= 1000,
        data: points.map((item) => ({
          value: [item.sequence, item.value],
          drilldownKey: item.drilldown_key,
          ruleHits: item.rule_hits,
          itemStyle: item.rule_hits.length ? { color: "#d64545" } : undefined,
        })),
        markLine: group ? { silent: true, symbol: "none", data: [
          { name: "I CL", yAxis: group.center_line },
          { name: "I LCL", yAxis: group.lower_control_limit },
          { name: "I UCL", yAxis: group.upper_control_limit },
        ].filter((item) => item.yAxis !== null) } : undefined,
      },
      {
        name: "Moving Range (MR)",
        type: "line",
        xAxisIndex: 1,
        yAxisIndex: 1,
        connectNulls: false,
        showSymbol: points.length <= 1000,
        data: points.map((item) => ({
          value: [item.sequence, item.moving_range],
          drilldownKey: item.drilldown_key,
          ruleHits: item.rule_hits,
          itemStyle: item.rule_hits.includes("MR_BEYOND_UPPER_CONTROL_LIMIT") ? { color: "#d64545" } : undefined,
        })),
        markLine: group ? { silent: true, symbol: "none", data: [
          { name: "MR Bar", yAxis: group.mr_bar },
          { name: "MR UCL", yAxis: group.mr_upper_control_limit },
        ].filter((item) => item.yAxis !== null) } : undefined,
      },
    ],
  };
}

export function sylTrendOption(dataset: QualitySylDatasetLimit | undefined, mode: QualityPercentAxisMode): EChartsCoreOption {
  const groups = dataset?.groups ?? [];
  return {
    animation: false,
    tooltip: { trigger: "axis" },
    grid: { left: 76, right: 34, top: 50, bottom: 82 },
    xAxis: { type: "category", name: "Physical subgroup", data: groups.map((item) => item.group_key), axisLabel: { rotate: groups.length > 8 ? 28 : 0 } },
    yAxis: percentAxis("Known Yield", mode),
    dataZoom: [{ type: "inside" }, { type: "slider", bottom: 16, height: 18 }],
    brush: { toolbox: ["rect", "clear"], xAxisIndex: "all" },
    toolbox: toolbox("syl-quality-trend", true),
    series: [{
      name: "Known Yield",
      type: "line",
      showSymbol: true,
      data: groups.map((item) => ({ value: percentValue(item.yield_rate), drilldownKeys: item.drilldown_keys })),
      markLine: dataset?.lower_limit == null ? undefined : { silent: true, symbol: "none", data: [{ name: "SYL", yAxis: percentValue(dataset.lower_limit) }] },
    }],
  };
}

export function sblTrendOption(bin: QualitySblBinLimit | undefined, mode: QualityPercentAxisMode): EChartsCoreOption {
  const groups = bin?.groups ?? [];
  return {
    animation: false,
    tooltip: { trigger: "axis" },
    grid: { left: 76, right: 34, top: 50, bottom: 82 },
    xAxis: { type: "category", name: "Physical subgroup", data: groups.map((item) => item.group_key), axisLabel: { rotate: groups.length > 8 ? 28 : 0 } },
    yAxis: percentAxis(`Fail Bin ${bin?.bin_code ?? ""} Rate`, mode),
    dataZoom: [{ type: "inside" }, { type: "slider", bottom: 16, height: 18 }],
    brush: { toolbox: ["rect", "clear"], xAxisIndex: "all" },
    toolbox: toolbox(`sbl-${bin?.bin_code ?? "bin"}-trend`, true),
    series: [{
      name: `Bin ${bin?.bin_code ?? ""} Rate`,
      type: "line",
      showSymbol: true,
      data: groups.map((item) => ({ value: percentValue(item.rate), drilldownKeys: item.drilldown_keys, failUnits: item.fail_unit_count, denominatorUnits: item.physical_unit_count })),
      markLine: bin?.upper_limit == null ? undefined : { silent: true, symbol: "none", data: [{ name: "SBL", yAxis: percentValue(bin.upper_limit) }] },
    }],
  };
}

export function sblParetoOption(limits: readonly QualitySblBinLimit[]): EChartsCoreOption {
  const rows = [...limits].sort((left, right) => left.pareto_rank - right.pareto_rank);
  return {
    animation: false,
    tooltip: { trigger: "axis" },
    legend: { data: ["Fail Units", "Cumulative Fail Count Share"] },
    grid: { left: 76, right: 72, top: 56, bottom: 72 },
    xAxis: { type: "category", name: "Dataset / Fail Bin", data: rows.map((item) => `#${item.dataset_id} v${item.version_no} · ${item.bin_code}`), axisLabel: { rotate: rows.length > 6 ? 28 : 0 } },
    yAxis: [
      { type: "value", name: "Fail Units", min: 0, minInterval: 1 },
      { type: "value", name: "Cumulative Share", min: 0, max: 100, axisLabel: { formatter: "{value}%" } },
    ],
    dataZoom: [{ type: "inside" }, { type: "slider", bottom: 12, height: 18 }],
    toolbox: toolbox("sbl-fail-bin-pareto"),
    series: [
      { name: "Fail Units", type: "bar", data: rows.map((item) => ({ value: item.fail_unit_count, drilldownKeys: Array.from(new Set(item.groups.flatMap((group) => group.drilldown_keys))) })) },
      { name: "Cumulative Fail Count Share", type: "line", yAxisIndex: 1, data: rows.map((item) => percentValue(item.cumulative_fail_unit_share)) },
    ],
  };
}

export function marginDistributionOption(group: QualityMarginGroupResult | undefined): EChartsCoreOption {
  const points = group?.points ?? [];
  const makeSeries = (outOfSpec: boolean) => ({
    name: outOfSpec ? "OOS" : "In Spec",
    type: "scatter" as const,
    symbolSize: 7,
    data: points.filter((item) => item.out_of_spec === outOfSpec).map((item) => ({
      value: [item.nearest_margin, item.value],
      drilldownKey: item.drilldown_key,
      unitId: item.unit_id,
      lowerMargin: item.lower_margin,
      upperMargin: item.upper_margin,
    })),
  });
  return {
    animation: false,
    color: ["#2d9d78", "#d64545"],
    tooltip: {
      trigger: "item",
      formatter: (payload: unknown) => {
        const data = (payload as { data?: { value?: unknown; unitId?: unknown; lowerMargin?: unknown; upperMargin?: unknown } })?.data;
        const value = Array.isArray(data?.value) ? data.value : [];
        return `Unit ${String(data?.unitId ?? "—")}<br/>Nearest Margin ${value[0] ?? "—"}<br/>Value ${value[1] ?? "—"}<br/>Lower / Upper Margin ${String(data?.lowerMargin ?? "—")} / ${String(data?.upperMargin ?? "—")}`;
      },
    },
    legend: { data: ["In Spec", "OOS"] },
    grid: { left: 76, right: 34, top: 56, bottom: 72 },
    xAxis: { type: "value", name: "Nearest Spec Margin", scale: true },
    yAxis: { type: "value", name: "Measurement Value", scale: true },
    dataZoom: [{ type: "inside" }, { type: "slider", bottom: 12, height: 18 }],
    brush: { toolbox: ["rect", "polygon", "clear"], xAxisIndex: "all", yAxisIndex: "all" },
    toolbox: toolbox("spec-margin-oos-distribution", true),
    series: [
      { ...makeSeries(false), markLine: { silent: true, symbol: "none", data: [{ name: "Spec Boundary", xAxis: 0 }] } },
      makeSeries(true),
    ],
  };
}

export function cooccurrenceScopeKey(cell: QualityBinCooccurrenceCell): string {
  return scopeKey(cell.dataset_id, cell.version_no, cell.group_key);
}

export function cooccurrenceHeatmapOption(cells: readonly QualityBinCooccurrenceCell[], mode: QualityPercentAxisMode): EChartsCoreOption {
  const bins = Array.from(new Set(cells.flatMap((item) => [item.left_bin, item.right_bin]))).sort();
  const data = cells.map((item) => ({
    value: [bins.indexOf(item.left_bin), bins.indexOf(item.right_bin), percentValue(item.rate)],
    drilldownKeys: item.drilldown_keys,
    physicalUnitCount: item.physical_unit_count,
    denominatorUnits: item.denominator_units,
  }));
  const observedMaximum = Math.max(0, ...cells.map((item) => item.rate * 100));
  return {
    animation: false,
    tooltip: {
      formatter: (payload: unknown) => {
        const datum = (payload as { data?: { value?: unknown; physicalUnitCount?: unknown; denominatorUnits?: unknown } })?.data;
        const value = Array.isArray(datum?.value) ? datum.value : [];
        return `${bins[Number(value[0])] ?? "—"} × ${bins[Number(value[1])] ?? "—"}<br/>Rate ${value[2] ?? "—"}%<br/>Physical Units ${String(datum?.physicalUnitCount ?? "—")} / ${String(datum?.denominatorUnits ?? "—")}`;
      },
    },
    grid: { left: 90, right: 36, top: 48, bottom: 92 },
    xAxis: { type: "category", name: "Left Bin", data: bins },
    yAxis: { type: "category", name: "Right Bin", data: bins },
    visualMap: { min: 0, max: mode === "FIXED_0_100" ? 100 : observedMaximum || 1, calculable: true, orient: "horizontal", left: "center", bottom: 8, inRange: { color: ["#f7fbff", "#6baed6", "#08306b"] }, formatter: (value: number) => `${value.toFixed(2)}%` },
    toolbox: toolbox("bin-cooccurrence-heatmap"),
    series: [{ name: "Co-occurrence Rate", type: "heatmap", data, label: { show: bins.length <= 15, formatter: (payload: unknown) => {
      const value = (payload as { data?: { value?: unknown } })?.data?.value;
      return Array.isArray(value) ? `${Number(value[2]).toFixed(2)}%` : "";
    } } }],
  };
}

export function cooccurrenceParetoOption(cells: readonly QualityBinCooccurrenceCell[]): EChartsCoreOption {
  const rows = [...cells].sort((left, right) => left.pareto_rank - right.pareto_rank);
  return {
    animation: false,
    tooltip: { trigger: "axis" },
    legend: { data: ["Co-occurring Units", "Cumulative Pair Share"] },
    grid: { left: 76, right: 72, top: 56, bottom: 92 },
    xAxis: { type: "category", name: "Bin Pair", data: rows.map((item) => `${item.left_bin} × ${item.right_bin}`), axisLabel: { rotate: rows.length > 8 ? 28 : 0 } },
    yAxis: [
      { type: "value", name: "Pair Units", min: 0, minInterval: 1 },
      { type: "value", name: "Cumulative Pair Share", min: 0, max: 100, axisLabel: { formatter: "{value}%" } },
    ],
    dataZoom: [{ type: "inside" }, { type: "slider", bottom: 16, height: 18 }],
    toolbox: toolbox("bin-cooccurrence-pareto"),
    series: [
      { name: "Co-occurring Units", type: "bar", data: rows.map((item) => ({ value: item.physical_unit_count, drilldownKeys: item.drilldown_keys })) },
      { name: "Cumulative Pair Share", type: "line", yAxisIndex: 1, data: rows.map((item) => percentValue(item.cumulative_pair_count_share)) },
    ],
  };
}
