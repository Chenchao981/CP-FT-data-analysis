from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.domain.formal_pat_contract import (
    FORMAL_PAT_ADAPTER_CONTRACT_VERSION,
    FORMAL_PAT_ADAPTER_MANIFEST_SHA256,
    FORMAL_PAT_ALGORITHM_CODE,
    FORMAL_PAT_SOURCE_COMMIT,
    FORMAL_PAT_SOURCE_SHA256,
)
from app.infrastructure.formal_pat_adapter import (
    calculate_formal_pat,
    source_engine_sha256,
)

_SOURCE_PROBE = r"""
import importlib.util
import json
import sys
from pathlib import Path

source_path = Path(sys.argv[1]).resolve()
spec = importlib.util.spec_from_file_location("tms_pat_source_probe", source_path)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
payload = json.load(sys.stdin)
rows = []
for item in payload:
    series = module.pd.Series(item["values"], name=item["name"])
    result = module.compute_pat_stats(series)
    rows.append({
        "name": item["name"],
        "q1": float(result["下四分位数"]),
        "median": float(result["中位数"]),
        "q3": float(result["上四分位数"]),
        "sigma": float(result["Sigma"]),
        "lower_limit": float(result["LCL\n计算值"]),
        "upper_limit": float(result["UCL\n计算值"]),
    })
print(json.dumps(rows, ensure_ascii=True, separators=(",", ":")))
"""


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Reconcile formal TMS PAT with the frozen shared FT PAT engine"
    )
    parser.add_argument("--source-engine", type=Path, required=True)
    parser.add_argument("--source-python", type=Path, required=True)
    parser.add_argument("--quick-pat-summary", type=Path)
    return parser.parse_args()


def _source_last_commit(source_engine: Path) -> str:
    repository = source_engine.parent.parent
    relative = source_engine.relative_to(repository).as_posix()
    result = subprocess.run(
        [
            "git",
            "-C",
            str(repository),
            "log",
            "-1",
            "--format=%H",
            "--",
            relative,
        ],
        capture_output=True,
        check=True,
        encoding="utf-8",
        errors="strict",
        text=True,
    )
    return result.stdout.strip().lower()


def _probe_source(
    source_python: Path, source_engine: Path, cases: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    completed = subprocess.run(
        [str(source_python), "-c", _SOURCE_PROBE, str(source_engine)],
        input=json.dumps(cases, ensure_ascii=True),
        capture_output=True,
        check=False,
        encoding="utf-8",
        errors="replace",
        text=True,
        timeout=120,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "shared PAT source probe failed: "
            + (completed.stderr or completed.stdout)[-4000:]
        )
    return json.loads(completed.stdout)


def _verify_vectors(
    source_rows: list[dict[str, Any]], cases: list[dict[str, Any]]
) -> int:
    if len(source_rows) != len(cases):
        raise RuntimeError("shared PAT probe returned an unexpected vector count")
    for case, source in zip(cases, source_rows, strict=True):
        formal = calculate_formal_pat(
            case["values"], lower_multiplier=6.0, upper_multiplier=6.0
        )
        fields = {
            "q1": formal.q1,
            "median": formal.median,
            "q3": formal.q3,
            "sigma": formal.robust_sigma,
            "lower_limit": formal.lower_limit,
            "upper_limit": formal.upper_limit,
        }
        for field, actual in fields.items():
            expected = float(source[field])
            if actual != expected:
                raise RuntimeError(
                    f"Golden mismatch {case['name']}/{field}: {actual}!={expected}"
                )
        expected_outliers = tuple(
            index
            for index, value in enumerate(case["values"])
            if value < float(source["lower_limit"])
            or value > float(source["upper_limit"])
        )
        if formal.outlier_indexes != expected_outliers:
            raise RuntimeError(f"Golden outlier mismatch for {case['name']}")
    return len(cases)


def _verify_quick_summary(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("formula_contract") != (
        "SIGMA_IQR_1_35_MEDIAN_PLUS_MINUS_6SIGMA_V1"
    ):
        raise RuntimeError("Quick PAT summary uses a different formula contract")
    rows = payload.get("parameters")
    if not isinstance(rows, list) or not rows:
        raise RuntimeError("Quick PAT summary has no parameter evidence")
    for row in rows:
        q1 = float(row["q1"])
        median = float(row["median"])
        q3 = float(row["q3"])
        sigma_raw = (q3 - q1) / 1.35 if q3 > q1 else 0.0
        expected = {
            "sigma": round(sigma_raw, 6),
            "lcl_calculated": round(median - 6.0 * sigma_raw, 6),
            "ucl_calculated": round(median + 6.0 * sigma_raw, 6),
        }
        for field, value in expected.items():
            if abs(float(row[field]) - value) > 0.000001:
                raise RuntimeError(
                    f"Quick PAT evidence mismatch {row.get('parameter')}/{field}"
                )
    return {
        "source_file_count": int(payload["source_file_count"]),
        "record_count": int(payload["record_count"]),
        "parameter_count": len(rows),
        "source_manifest_sha256": str(payload["source_manifest_sha256"]),
    }


def main() -> int:
    args = _arguments()
    source_engine = args.source_engine.resolve()
    source_python = args.source_python.resolve()
    if not source_engine.is_file() or not source_python.is_file():
        raise FileNotFoundError("source engine or source Python is unavailable")
    actual_sha = source_engine_sha256(source_engine.read_bytes())
    if actual_sha != FORMAL_PAT_SOURCE_SHA256:
        raise RuntimeError(
            f"shared PAT source SHA drifted: {actual_sha}!={FORMAL_PAT_SOURCE_SHA256}"
        )
    actual_commit = _source_last_commit(source_engine)
    if actual_commit != FORMAL_PAT_SOURCE_COMMIT:
        raise RuntimeError(
            f"shared PAT source commit drifted: {actual_commit}!={FORMAL_PAT_SOURCE_COMMIT}"
        )

    cases: list[dict[str, Any]] = [
        {"name": "OUTLIER", "values": [*range(10), 100]},
        {"name": "ZERO_DISPERSION", "values": [5.0] * 30},
        {"name": "EVEN_LINEAR_QUANTILE", "values": [1, 2, 3, 4, 5, 50]},
        {
            "name": "DECIMAL_AND_NEGATIVE",
            "values": [-2.25, -1.5, -0.5, 0.25, 1.5, 2.25, 30.125],
        },
    ]
    source_rows = _probe_source(source_python, source_engine, cases)
    vector_count = _verify_vectors(source_rows, cases)
    quick = (
        _verify_quick_summary(args.quick_pat_summary.resolve())
        if args.quick_pat_summary is not None
        else None
    )
    print(
        json.dumps(
            {
                "status": "PASS",
                "algorithm_code": FORMAL_PAT_ALGORITHM_CODE,
                "adapter_contract_version": FORMAL_PAT_ADAPTER_CONTRACT_VERSION,
                "adapter_manifest_sha256": FORMAL_PAT_ADAPTER_MANIFEST_SHA256,
                "source_engine_sha256": actual_sha,
                "source_engine_last_commit": actual_commit,
                "golden_vector_count": vector_count,
                "quick_pat_evidence": quick,
                "owner_gate": "NOT_BYPASSED",
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
