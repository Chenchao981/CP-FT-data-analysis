"""Receive credential data through stdin, never through command-line arguments."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.infrastructure.ftp_credentials import store_ftp_credential


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", required=True)
    args = parser.parse_args()
    try:
        payload = json.loads(sys.stdin.read(16384))
        store_ftp_credential(args.reference, payload["username"], payload["password"])
    except Exception:
        print("FTP credential could not be stored. Check the reference and Windows account.", file=sys.stderr)
        raise SystemExit(1) from None
    print("FTP credential stored for the current Windows account.")


if __name__ == "__main__":
    main()
