from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass

from app.domain.formal_pat_contract import (
    FORMAL_PAT_DECIMAL_PLACES,
    FORMAL_PAT_MULTIPLIER,
    FORMAL_PAT_SIGMA_DIVISOR,
)


@dataclass(frozen=True, slots=True)
class FormalPatResult:
    q1: float
    median: float
    q3: float
    iqr: float
    robust_sigma: float
    lower_limit: float
    upper_limit: float
    outlier_indexes: tuple[int, ...]


def _finite_values(values: list[float] | tuple[float, ...]) -> tuple[float, ...]:
    normalized = tuple(float(value) for value in values)
    if not normalized or any(not math.isfinite(value) for value in normalized):
        raise ValueError("formal PAT values must be non-empty and finite")
    return normalized


def _linear_quantile(values: tuple[float, ...], probability: float) -> float:
    """Match pandas Series.quantile(..., interpolation='linear') without pandas."""
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * probability
    lower_index = math.floor(position)
    upper_index = math.ceil(position)
    if lower_index == upper_index:
        return ordered[lower_index]
    fraction = position - lower_index
    return ordered[lower_index] + fraction * (
        ordered[upper_index] - ordered[lower_index]
    )


def calculate_formal_pat(
    values: list[float] | tuple[float, ...],
    *,
    lower_multiplier: float,
    upper_multiplier: float,
) -> FormalPatResult:
    """Execute the frozen shared-engine PAT contract over Canonical values.

    The mature FT engine has a fixed multiplier of six.  A formal Rule may pin
    this Adapter, but it cannot silently turn the Adapter into a different PAT
    formula by supplying another multiplier.
    """
    normalized = _finite_values(values)
    multipliers = (float(lower_multiplier), float(upper_multiplier))
    if any(not math.isfinite(value) for value in multipliers):
        raise ValueError("formal PAT multipliers must be finite")
    if multipliers != (FORMAL_PAT_MULTIPLIER, FORMAL_PAT_MULTIPLIER):
        raise ValueError(
            "PAT_SHARED_IQR_1_35_V1 requires lower_multiplier=6 and upper_multiplier=6"
        )

    q1_raw = _linear_quantile(normalized, 0.25)
    median_raw = _linear_quantile(normalized, 0.50)
    q3_raw = _linear_quantile(normalized, 0.75)
    iqr_raw = q3_raw - q1_raw
    sigma_raw = iqr_raw / FORMAL_PAT_SIGMA_DIVISOR if q3_raw > q1_raw else 0.0
    lower_raw = median_raw - FORMAL_PAT_MULTIPLIER * sigma_raw
    upper_raw = median_raw + FORMAL_PAT_MULTIPLIER * sigma_raw

    q1 = round(q1_raw, FORMAL_PAT_DECIMAL_PLACES)
    median = round(median_raw, FORMAL_PAT_DECIMAL_PLACES)
    q3 = round(q3_raw, FORMAL_PAT_DECIMAL_PLACES)
    iqr = round(iqr_raw, FORMAL_PAT_DECIMAL_PLACES)
    sigma = round(sigma_raw, FORMAL_PAT_DECIMAL_PLACES)
    lower = round(lower_raw, FORMAL_PAT_DECIMAL_PLACES)
    upper = round(upper_raw, FORMAL_PAT_DECIMAL_PLACES)
    outliers = tuple(
        index
        for index, value in enumerate(normalized)
        if value < lower or value > upper
    )
    return FormalPatResult(
        q1,
        median,
        q3,
        iqr,
        sigma,
        lower,
        upper,
        outliers,
    )


def source_engine_sha256(source: bytes) -> str:
    """Return the content hash used by release/Golden reconciliation tooling."""
    return hashlib.sha256(source).hexdigest()
