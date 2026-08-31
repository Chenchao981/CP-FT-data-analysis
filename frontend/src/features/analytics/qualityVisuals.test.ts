import { describe, expect, it } from "vitest";

import type {
  QualityBinCooccurrenceCell,
  QualityMarginGroupResult,
  QualitySblBinLimit,
  QualitySpcGroupResult,
  QualitySylDatasetLimit,
} from "../../api/qualityEvaluation";
import {
  cooccurrenceHeatmapOption,
  cooccurrenceParetoOption,
  marginDistributionOption,
  sblParetoOption,
  sblTrendOption,
  spcIMrOption,
  sylTrendOption,
} from "./qualityVisuals";

type Series = { name?: string; data?: Array<number | { value: unknown; drilldownKey?: string; drilldownKeys?: string[]; ruleHits?: string[] }>; markLine?: { data?: Array<Record<string, unknown>> } };

const series = (option: unknown) => (option as { series: Series[] }).series;

describe("quality visual contracts", () => {
  it("renders a synchronized Individuals and Moving Range chart from server facts", () => {
    const group: QualitySpcGroupResult = {
      dataset_id: 20, version_no: 1, group_key: "LOT-A", valid_n: 3, missing_n: 0,
      center_line: 10, lower_control_limit: 8, upper_control_limit: 12,
      mr_bar: 0.5, mr_upper_control_limit: 1.6335, boundary_reset: true,
      baseline_context_hash: "a".repeat(64), status: "ASSESSABLE",
      sampling_summary: { sampled: false, method: null, original_points: 3, returned_points: 3, preserved_out_of_spec_points: 2 },
      points: [
        { sequence: 1, value: 9.5, moving_range: null, drilldown_key: "UNIT:1", rule_hits: [] },
        { sequence: 2, value: 10.5, moving_range: 1, drilldown_key: "UNIT:2", rule_hits: ["MR_BEYOND_UPPER_CONTROL_LIMIT"] },
        { sequence: 3, value: 10, moving_range: 0.5, drilldown_key: "UNIT:3", rule_hits: ["3_POINT_MONOTONIC_RUN"] },
      ],
    };
    const option = spcIMrOption(group) as { yAxis: Array<{ min?: number }>; toolbox: { feature: Record<string, unknown> }; series: Series[] };
    expect(option.series.map((item) => item.name)).toEqual(["Individuals (I)", "Moving Range (MR)"]);
    expect(option.yAxis[1].min).toBe(0);
    expect(option.series[0].markLine?.data).toEqual(expect.arrayContaining([{ name: "I LCL", yAxis: 8 }, { name: "I UCL", yAxis: 12 }]));
    expect(option.series[1].markLine?.data).toEqual(expect.arrayContaining([{ name: "MR Bar", yAxis: 0.5 }, { name: "MR UCL", yAxis: 1.6335 }]));
    expect(option.series[1].data?.[1]).toMatchObject({ value: [2, 1], drilldownKey: "UNIT:2", ruleHits: ["MR_BEYOND_UPPER_CONTROL_LIMIT"] });
    expect(option.toolbox.feature).toHaveProperty("saveAsImage");
    expect(option.toolbox.feature).toHaveProperty("brush");
  });

  it("keeps SYL/SBL approved limits in trend charts and derives only a display Pareto", () => {
    const syl: QualitySylDatasetLimit = {
      dataset_id: 20, version_no: 1, subgroup_count: 2, mean_yield: 0.85,
      sample_stddev: 0.05, raw_lower_limit: 0.7, lower_limit: 0.7,
      rounding_policy: "NONE", rounding_step: null, status: "ASSESSABLE", below_limit_groups: ["LOT-B"],
      groups: [
        { group_key: "LOT-A", pass_unit_count: 9, fail_unit_count: 1, unknown_excluded_count: 0, abort_excluded_count: 0, other_result_excluded_count: 0, yield_rate: 0.9, drilldown_keys: ["UNIT:1"] },
        { group_key: "LOT-B", pass_unit_count: 8, fail_unit_count: 2, unknown_excluded_count: 1, abort_excluded_count: 0, other_result_excluded_count: 0, yield_rate: 0.8, drilldown_keys: ["UNIT:2"] },
      ],
    };
    const sylOption = sylTrendOption(syl, "FIXED_0_100") as { yAxis: { min?: number; max?: number }; series: Series[] };
    expect(sylOption.yAxis).toMatchObject({ min: 0, max: 100 });
    expect(sylOption.series[0].data?.[0]).toMatchObject({ value: 90, drilldownKeys: ["UNIT:1"] });
    expect(sylOption.series[0].markLine?.data).toEqual([{ name: "SYL", yAxis: 70 }]);

    const limits: QualitySblBinLimit[] = [
      { dataset_id: 20, version_no: 1, bin_code: "5", subgroup_count: 2, mean_rate: 0.15, sample_stddev: 0.01, upper_limit: 0.2, status: "ASSESSABLE", exceeding_groups: [], groups: [{ group_key: "LOT-A", physical_unit_count: 10, fail_unit_count: 3, rate: 0.3, drilldown_keys: ["UNIT:3"] }], pareto_rank: 1, fail_unit_count: 3, fail_unit_share: 0.75, cumulative_fail_unit_share: 0.75 },
      { dataset_id: 20, version_no: 1, bin_code: "7", subgroup_count: 2, mean_rate: 0.05, sample_stddev: 0.01, upper_limit: 0.1, status: "ASSESSABLE", exceeding_groups: [], groups: [{ group_key: "LOT-A", physical_unit_count: 10, fail_unit_count: 1, rate: 0.1, drilldown_keys: ["UNIT:4"] }], pareto_rank: 2, fail_unit_count: 1, fail_unit_share: 0.25, cumulative_fail_unit_share: 1 },
    ];
    const snapshot = JSON.stringify(limits);
    const trend = sblTrendOption(limits[0], "AUTO");
    expect(series(trend)[0].markLine?.data).toEqual([{ name: "SBL", yAxis: 20 }]);
    const pareto = sblParetoOption(limits);
    expect(series(pareto)[0].data).toEqual([{ value: 3, drilldownKeys: ["UNIT:3"] }, { value: 1, drilldownKeys: ["UNIT:4"] }]);
    expect(series(pareto)[1].data).toEqual([75, 100]);
    expect(JSON.stringify(limits)).toBe(snapshot);
  });

  it("shows margin/OOS evidence without recomputing OOS status", () => {
    const group: QualityMarginGroupResult = {
      dataset_id: 20, version_no: 1, group_key: "LOT-A", spec_set_id: 7, spec_version: "V1", spec_mode: "BOTH",
      lsl: 1, usl: 2, valid_n: 2, missing_n: 0, out_of_spec_count: 1, out_of_spec_rate: 0.5, minimum_margin: -0.1,
      sampling_summary: { sampled: false, method: null, original_points: 2, returned_points: 2, preserved_out_of_spec_points: 1 },
      points: [
        { dataset_id: 20, version_no: 1, unit_id: 1, measurement_id: 11, value: 1.5, lower_margin: 0.5, upper_margin: 0.5, nearest_margin: 0.5, out_of_spec: false, drilldown_key: "UNIT:1" },
        { dataset_id: 20, version_no: 1, unit_id: 2, measurement_id: 12, value: 2.1, lower_margin: 1.1, upper_margin: -0.1, nearest_margin: -0.1, out_of_spec: true, drilldown_key: "UNIT:2" },
      ],
    };
    const option = marginDistributionOption(group) as { brush: unknown; toolbox: { feature: Record<string, unknown> }; series: Series[] };
    expect(option.series[0].data).toEqual([expect.objectContaining({ value: [0.5, 1.5], drilldownKey: "UNIT:1" })]);
    expect(option.series[1].data).toEqual([expect.objectContaining({ value: [-0.1, 2.1], drilldownKey: "UNIT:2" })]);
    expect(option.series[0].markLine?.data).toEqual([{ name: "Spec Boundary", xAxis: 0 }]);
    expect(option.brush).toBeDefined();
    expect(option.toolbox.feature).toHaveProperty("saveAsImage");

    const crowded: QualityMarginGroupResult = {
      ...group,
      valid_n: 10_001,
      points: [
        ...Array.from({ length: 10_000 }, (_, index) => ({ ...group.points[0], unit_id: index + 1, measurement_id: index + 1, drilldown_key: `UNIT:${index + 1}` })),
        { ...group.points[1], unit_id: 20_001, measurement_id: 20_001, drilldown_key: "UNIT:20001" },
      ],
    };
    const crowdedOption = marginDistributionOption(crowded);
    expect(series(crowdedOption)[1].data).toContainEqual(expect.objectContaining({ drilldownKey: "UNIT:20001" }));
  });

  it("builds a scoped Bin co-occurrence heatmap and pair Pareto with exact evidence keys", () => {
    const cells: QualityBinCooccurrenceCell[] = [
      { dataset_id: 20, version_no: 1, group_key: "LOT-A", left_bin: "5", right_bin: "7", physical_unit_count: 3, denominator_units: 10, rate: 0.3, drilldown_keys: ["UNIT:1"], pareto_rank: 1, pair_count_share: 0.75, cumulative_pair_count_share: 0.75 },
      { dataset_id: 20, version_no: 1, group_key: "LOT-A", left_bin: "5", right_bin: "8", physical_unit_count: 1, denominator_units: 10, rate: 0.1, drilldown_keys: ["UNIT:2"], pareto_rank: 2, pair_count_share: 0.25, cumulative_pair_count_share: 1 },
    ];
    const heatmap = cooccurrenceHeatmapOption(cells, "FIXED_0_100") as { visualMap: { max: number }; toolbox: { feature: Record<string, unknown> }; series: Series[] };
    expect(heatmap.visualMap.max).toBe(100);
    expect(heatmap.series[0].data).toEqual(expect.arrayContaining([expect.objectContaining({ value: [0, 1, 30], drilldownKeys: ["UNIT:1"] })]));
    expect(heatmap.toolbox.feature).toHaveProperty("saveAsImage");
    const pareto = cooccurrenceParetoOption(cells);
    expect(series(pareto)[0].data).toEqual([{ value: 3, drilldownKeys: ["UNIT:1"] }, { value: 1, drilldownKeys: ["UNIT:2"] }]);
    expect(series(pareto)[1].data).toEqual([75, 100]);
  });
});
