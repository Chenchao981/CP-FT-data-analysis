from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.infrastructure.existing_cleaner_runner import ExistingCleanerRunner


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Run an existing CP or FT release cleaner")
    parser.add_argument("--stage", required=True, choices=("CP", "FT"))
    parser.add_argument("--factory", required=True)
    parser.add_argument("--input", action="append", required=True, dest="inputs")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = ExistingCleanerRunner().run(
        test_stage=args.stage,
        factory=args.factory,
        inputs=args.inputs,
        output_root=args.output,
    )
    print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
