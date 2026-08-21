from __future__ import annotations

import argparse
import json

from app.infrastructure.existing_cleaner_runner import ExistingCleanerRunner


def main() -> int:
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
