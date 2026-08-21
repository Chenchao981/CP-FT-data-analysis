from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.cleaners.huahong_batch import HuaHongBatchInspector
from app.cleaners.huahong_dcp import HuaHongDcpParser, HuaHongFormatError, summarize_files


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Read-only validation of supplied HuaHong TXT samples"
    )
    parser.add_argument("--input", required=True, type=Path)
    args = parser.parse_args()

    files = sorted(args.input.rglob("*.TXT"))
    cleaner = HuaHongDcpParser()
    parsed = []
    failures: list[dict[str, str]] = []
    for path in files:
        try:
            parsed.append(cleaner.parse_path(path))
        except (OSError, UnicodeError, HuaHongFormatError) as exc:
            failures.append({"file": path.name, "error": str(exc)})

    result = summarize_files(parsed)
    result["failure_count"] = len(failures)
    result["failures"] = failures
    if not failures:
        inspection = HuaHongBatchInspector(cleaner).inspect_directory(args.input)
        result["batch_status"] = inspection.status
        result["product_candidate_count"] = len(
            set(inspection.product_candidates.values())
        )
        result["dq_issues"] = [
            {
                "code": item.code,
                "severity": item.severity,
                "entity_key": item.entity_key,
                "message": item.message,
            }
            for item in inspection.issues
        ]
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if failures:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
