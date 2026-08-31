from __future__ import annotations

import hashlib
import json

FORMAL_PAT_ALGORITHM_CODE = "PAT_SHARED_IQR_1_35_V1"
FORMAL_PAT_ADAPTER_CONTRACT_VERSION = "FORMAL_PAT_SHARED_ENGINE_ADAPTER_V1"
FORMAL_PAT_SOURCE_REPOSITORY = "data_IGBT_multiple"
FORMAL_PAT_SOURCE_COMMIT = "ebf9c7a05b8a10987941c8dacd7e4b1295ae58c1"
FORMAL_PAT_SOURCE_FILE = "shared/pat_engine.py"
FORMAL_PAT_SOURCE_FUNCTION = "compute_pat_stats"
FORMAL_PAT_SOURCE_SHA256 = (
    "b853dd935a2adf75190f25bb664b1e2c27f1c09bb89e2c91b5245799aa9f183a"
)
FORMAL_PAT_MULTIPLIER = 6.0
FORMAL_PAT_SIGMA_DIVISOR = 1.35
FORMAL_PAT_DECIMAL_PLACES = 6

FORMAL_PAT_ADAPTER_MANIFEST = {
    "adapter_contract_version": FORMAL_PAT_ADAPTER_CONTRACT_VERSION,
    "algorithm_code": FORMAL_PAT_ALGORITHM_CODE,
    "source": {
        "repository": FORMAL_PAT_SOURCE_REPOSITORY,
        "commit": FORMAL_PAT_SOURCE_COMMIT,
        "file": FORMAL_PAT_SOURCE_FILE,
        "function": FORMAL_PAT_SOURCE_FUNCTION,
        "sha256": FORMAL_PAT_SOURCE_SHA256,
    },
    "population": "FILTERED_CANONICAL_FINITE_MEASUREMENTS",
    "quantile_method": "LINEAR_PANDAS_DEFAULT",
    "sigma": "(Q3-Q1)/1.35_OR_ZERO_WHEN_Q3_EQUALS_Q1",
    "limits": "MEDIAN_PLUS_MINUS_6_SIGMA",
    "rounding": {
        "mode": "PYTHON_ROUND_HALF_EVEN_COMPATIBLE",
        "decimal_places": FORMAL_PAT_DECIMAL_PLACES,
        "applies_to": ["Q1", "MEDIAN", "Q3", "IQR", "SIGMA", "LCL", "UCL"],
    },
    "outlier_boundary": "VALUE_LT_LCL_OR_VALUE_GT_UCL",
    "outlier_policy": "MARK_ONLY",
}
FORMAL_PAT_ADAPTER_MANIFEST_SHA256 = hashlib.sha256(
    json.dumps(
        FORMAL_PAT_ADAPTER_MANIFEST,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
).hexdigest()
