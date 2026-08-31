from __future__ import annotations

import math
import statistics
from collections import Counter
from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from itertools import combinations, pairwise

_I_MR_D2_FACTOR = 2.66
_MR_D4_FACTOR = 3.267
_MR_D2_CONSTANT = 1.128


@dataclass(frozen=True, slots=True)
class OrderedKernelValue:
    sequence: int
    value: float
    drilldown_key: str


@dataclass(frozen=True, slots=True)
class SpcKernelPoint:
    sequence: int
    value: float
    moving_range: float | None
    drilldown_key: str
    rule_hits: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SpcKernelResult:
    center_line: float
    lower_control_limit: float
    upper_control_limit: float
    mr_bar: float
    mr_upper_control_limit: float
    points: tuple[SpcKernelPoint, ...]


@dataclass(frozen=True, slots=True)
class MarginKernelResult:
    lower_margin: float | None
    upper_margin: float | None
    nearest_margin: float
    out_of_spec: bool


@dataclass(frozen=True, slots=True)
class SblKernelResult:
    mean_rate: float
    sample_stddev: float
    upper_limit: float
    exceeding_groups: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SylKernelResult:
    mean_yield: float
    sample_stddev: float
    raw_lower_limit: float
    lower_limit: float
    below_limit_groups: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DistributionKernelBin:
    bin_index: int
    lower: float
    upper: float
    pass_count: int
    fail_count: int
    pass_drilldown_keys: tuple[str, ...]
    fail_drilldown_keys: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PassFailDistributionKernelResult:
    pass_mean: float | None
    fail_mean: float | None
    minimum: float
    maximum: float
    bins: tuple[DistributionKernelBin, ...]


def spc_i_mr(
    values: list[OrderedKernelValue] | tuple[OrderedKernelValue, ...],
    *,
    run_rule_mode: str = "NONE",
    consecutive_beyond_count: int | None = None,
    consecutive_beyond_sigma: float | None = None,
    same_side_run_length: int | None = None,
    monotonic_run_length: int | None = None,
) -> SpcKernelResult:
    if len(values) < 2:
        raise ValueError("SPC I-MR requires at least two ordered values")
    ordered = tuple(sorted(values, key=lambda item: item.sequence))
    sequences = [item.sequence for item in ordered]
    if len(sequences) != len(set(sequences)):
        raise ValueError("SPC order values must be unique within each reset group")
    if any(not math.isfinite(item.value) for item in ordered):
        raise ValueError("SPC values must be finite")
    center_line = statistics.fmean(item.value for item in ordered)
    moving_ranges = tuple(
        abs(current.value - previous.value) for previous, current in pairwise(ordered)
    )
    mr_bar = statistics.fmean(moving_ranges)
    lower = center_line - _I_MR_D2_FACTOR * mr_bar
    upper = center_line + _I_MR_D2_FACTOR * mr_bar
    mr_upper = _MR_D4_FACTOR * mr_bar
    if run_rule_mode not in {"NONE", "BASIC"}:
        raise ValueError("SPC run_rule_mode must be NONE or BASIC")
    basic_values = (
        consecutive_beyond_count,
        consecutive_beyond_sigma,
        same_side_run_length,
        monotonic_run_length,
    )
    if run_rule_mode == "NONE" and any(value is not None for value in basic_values):
        raise ValueError("SPC NONE run rules cannot carry thresholds")
    if run_rule_mode == "BASIC" and any(value is None for value in basic_values):
        raise ValueError("SPC BASIC run rules require all thresholds")
    hits_by_index: list[list[str]] = [[] for _ in ordered]
    for index, item in enumerate(ordered):
        moving_range = None if index == 0 else moving_ranges[index - 1]
        if item.value < lower or item.value > upper:
            hits_by_index[index].append("I_BEYOND_CONTROL_LIMIT")
        if moving_range is not None and moving_range > mr_upper:
            hits_by_index[index].append("MR_BEYOND_UPPER_CONTROL_LIMIT")
    if run_rule_mode == "BASIC":
        beyond_count = int(consecutive_beyond_count)
        beyond_sigma = float(consecutive_beyond_sigma)
        side_length = int(same_side_run_length)
        trend_length = int(monotonic_run_length)
        estimated_sigma = mr_bar / _MR_D2_CONSTANT
        beyond_delta = beyond_sigma * estimated_sigma
        for end in range(beyond_count - 1, len(ordered)):
            window = ordered[end - beyond_count + 1 : end + 1]
            if all(item.value > center_line + beyond_delta for item in window) or all(
                item.value < center_line - beyond_delta for item in window
            ):
                hits_by_index[end].append(
                    f"{beyond_count}_CONSECUTIVE_BEYOND_{beyond_sigma:g}_SIGMA_SAME_SIDE"
                )
        for end in range(side_length - 1, len(ordered)):
            window = ordered[end - side_length + 1 : end + 1]
            if all(item.value > center_line for item in window) or all(
                item.value < center_line for item in window
            ):
                hits_by_index[end].append(f"{side_length}_POINTS_SAME_SIDE")
        for end in range(trend_length - 1, len(ordered)):
            window = ordered[end - trend_length + 1 : end + 1]
            if all(
                current.value > previous.value for previous, current in pairwise(window)
            ) or all(
                current.value < previous.value for previous, current in pairwise(window)
            ):
                hits_by_index[end].append(f"{trend_length}_POINT_MONOTONIC_RUN")
    points: list[SpcKernelPoint] = []
    for index, item in enumerate(ordered):
        moving_range = None if index == 0 else moving_ranges[index - 1]
        points.append(
            SpcKernelPoint(
                item.sequence,
                item.value,
                moving_range,
                item.drilldown_key,
                tuple(hits_by_index[index]),
            )
        )
    return SpcKernelResult(center_line, lower, upper, mr_bar, mr_upper, tuple(points))


def margin_oos(
    value: float,
    *,
    lsl: float | None,
    usl: float | None,
    equality_is_in_spec: bool,
) -> MarginKernelResult:
    value = float(value)
    if not math.isfinite(value):
        raise ValueError("margin value must be finite")
    if lsl is None and usl is None:
        raise ValueError("margin requires at least one specification limit")
    if lsl is not None and not math.isfinite(lsl):
        raise ValueError("LSL must be finite")
    if usl is not None and not math.isfinite(usl):
        raise ValueError("USL must be finite")
    if lsl is not None and usl is not None and lsl > usl:
        raise ValueError("LSL cannot exceed USL")
    lower_margin = value - lsl if lsl is not None else None
    upper_margin = usl - value if usl is not None else None
    margins = [item for item in (lower_margin, upper_margin) if item is not None]
    nearest = min(margins)
    if equality_is_in_spec:
        out_of_spec = nearest < 0.0
    else:
        out_of_spec = nearest <= 0.0
    return MarginKernelResult(lower_margin, upper_margin, nearest, out_of_spec)


def bin_cooccurrence(
    unit_bins: dict[str, set[str]],
) -> tuple[tuple[str, str, int, tuple[str, ...]], ...]:
    counts: Counter[tuple[str, str]] = Counter()
    evidence: dict[tuple[str, str], list[str]] = {}
    for drilldown_key, raw_bins in sorted(unit_bins.items()):
        bins = sorted({item.strip() for item in raw_bins if item.strip()})
        for bin_code in bins:
            key = (bin_code, bin_code)
            counts[key] += 1
            evidence.setdefault(key, []).append(drilldown_key)
        for left, right in combinations(bins, 2):
            key = (left, right)
            counts[key] += 1
            evidence.setdefault(key, []).append(drilldown_key)
    return tuple(
        (left, right, count, tuple(evidence[(left, right)]))
        for (left, right), count in sorted(counts.items())
    )


def sbl_grouped_limit(
    group_rates: dict[str, float], *, upper_multiplier: float
) -> SblKernelResult:
    if len(group_rates) < 2:
        raise ValueError("SBL requires at least two physical subgroups")
    if not math.isfinite(upper_multiplier) or upper_multiplier <= 0.0:
        raise ValueError("SBL upper multiplier must be finite and positive")
    normalized = {key: float(value) for key, value in sorted(group_rates.items())}
    if any(
        not math.isfinite(value) or value < 0.0 or value > 1.0
        for value in normalized.values()
    ):
        raise ValueError("SBL subgroup rates must be finite values in [0, 1]")
    mean = statistics.fmean(normalized.values())
    sigma = statistics.stdev(normalized.values())
    upper = mean + upper_multiplier * sigma
    exceeding = tuple(key for key, value in normalized.items() if value > upper)
    return SblKernelResult(mean, sigma, upper, exceeding)


def syl_grouped_limit(
    group_yields: dict[str, float],
    *,
    lower_multiplier: float,
    rounding_policy: str,
    rounding_step: float | None,
) -> SylKernelResult:
    if len(group_yields) < 2:
        raise ValueError("SYL requires at least two physical subgroups")
    if not math.isfinite(lower_multiplier) or lower_multiplier <= 0.0:
        raise ValueError("SYL lower multiplier must be finite and positive")
    normalized = {key: float(value) for key, value in sorted(group_yields.items())}
    if any(
        not math.isfinite(value) or value < 0.0 or value > 1.0
        for value in normalized.values()
    ):
        raise ValueError("SYL subgroup yields must be finite values in [0, 1]")
    mean = statistics.fmean(normalized.values())
    sigma = statistics.stdev(normalized.values())
    raw_lower = mean - lower_multiplier * sigma
    if rounding_policy == "NONE":
        if rounding_step is not None:
            raise ValueError("SYL NONE rounding cannot carry a step")
        lower = raw_lower
    elif rounding_policy in {"FLOOR_TO_STEP", "CEILING_TO_STEP"}:
        if (
            rounding_step is None
            or not math.isfinite(rounding_step)
            or rounding_step <= 0
        ):
            raise ValueError("SYL step rounding requires a finite positive step")
        raw_decimal = Decimal(str(raw_lower))
        step_decimal = Decimal(str(rounding_step))
        mode = ROUND_FLOOR if rounding_policy == "FLOOR_TO_STEP" else ROUND_CEILING
        lower = float(
            (raw_decimal / step_decimal).to_integral_value(rounding=mode) * step_decimal
        )
    else:
        raise ValueError("SYL rounding policy is unsupported")
    below = tuple(key for key, value in normalized.items() if value < lower)
    return SylKernelResult(mean, sigma, raw_lower, lower, below)


def pass_fail_distribution(
    pass_values: list[tuple[float, str]] | tuple[tuple[float, str], ...],
    fail_values: list[tuple[float, str]] | tuple[tuple[float, str], ...],
    *,
    bin_count: int,
) -> PassFailDistributionKernelResult:
    if not pass_values and not fail_values:
        raise ValueError("Pass/Fail distribution requires at least one measured value")
    if bin_count < 5 or bin_count > 100:
        raise ValueError("Pass/Fail distribution bin_count must be between 5 and 100")
    normalized_pass = tuple((float(value), key) for value, key in pass_values)
    normalized_fail = tuple((float(value), key) for value, key in fail_values)
    all_values = normalized_pass + normalized_fail
    if any(not math.isfinite(value) for value, _ in all_values):
        raise ValueError("Pass/Fail distribution values must be finite")
    minimum = min(value for value, _ in all_values)
    maximum = max(value for value, _ in all_values)
    effective_count = 1 if minimum == maximum else bin_count
    width = (maximum - minimum) / effective_count if effective_count > 1 else 0.0
    pass_buckets: list[list[str]] = [[] for _ in range(effective_count)]
    fail_buckets: list[list[str]] = [[] for _ in range(effective_count)]

    def bucket_index(value: float) -> int:
        if effective_count == 1 or value == maximum:
            return effective_count - 1
        return min(int((value - minimum) / width), effective_count - 1)

    for value, key in normalized_pass:
        pass_buckets[bucket_index(value)].append(key)
    for value, key in normalized_fail:
        fail_buckets[bucket_index(value)].append(key)
    bins = tuple(
        DistributionKernelBin(
            index,
            minimum + width * index if effective_count > 1 else minimum,
            minimum + width * (index + 1) if effective_count > 1 else maximum,
            len(pass_buckets[index]),
            len(fail_buckets[index]),
            tuple(pass_buckets[index]),
            tuple(fail_buckets[index]),
        )
        for index in range(effective_count)
    )
    return PassFailDistributionKernelResult(
        statistics.fmean(value for value, _ in normalized_pass)
        if normalized_pass
        else None,
        statistics.fmean(value for value, _ in normalized_fail)
        if normalized_fail
        else None,
        minimum,
        maximum,
        bins,
    )
