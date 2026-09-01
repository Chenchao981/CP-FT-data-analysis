from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from local_agent.config import AgentConfig
from local_agent.models import FT_JIEQUN_TOOL_CODE
from local_agent.service import LocalAgentService


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the released Jiequn FT PAT package through the Local Agent "
            "service without uploading source files."
        )
    )
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--package-sha256", required=True)
    parser.add_argument("--python-runtime", type=Path, default=Path(sys.executable))
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    parser.add_argument("--timeout-seconds", type=int, default=7200)
    parser.add_argument(
        "--server-url",
        help=(
            "Optional TMS API base URL for registering the result. "
            "Authentication may be supplied through TMS_ACCEPTANCE_ACCESS_TOKEN."
        ),
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    source = args.source.expanduser().resolve()
    package = args.package.expanduser().resolve()
    runtime = args.python_runtime.expanduser().resolve()
    if not source.is_dir():
        raise SystemExit("source directory does not exist")
    if not package.is_file():
        raise SystemExit("released FT package does not exist")
    if not runtime.is_file():
        raise SystemExit("Python runtime does not exist")

    temporary_root = Path(tempfile.mkdtemp(prefix="tms-local-agent-acceptance-"))
    service: LocalAgentService | None = None
    started = time.monotonic()
    try:
        config = AgentConfig(
            work_root=temporary_root / "work",
            python_runtime=runtime,
            ft_package=package,
            ft_package_sha256=args.package_sha256.lower(),
            run_timeout_seconds=args.timeout_seconds,
        )
        service = LocalAgentService(config, selector=lambda: source)
        selection = service.select_folder()
        preview = service.preview(
            selection["selection_id"], FT_JIEQUN_TOOL_CODE
        )
        queued = service.create_run(
            selection["selection_id"],
            FT_JIEQUN_TOOL_CODE,
            str(preview["sha256"]),
        )
        run_id = queued["run_id"]
        deadline = time.monotonic() + args.timeout_seconds + 60
        while True:
            status = service.get_status(run_id)
            if status["status"] in {"SUCCESS", "FAILED"}:
                break
            if time.monotonic() >= deadline:
                raise SystemExit("Local Agent acceptance run polling timed out")
            time.sleep(max(args.poll_seconds, 0.1))
        if status["status"] != "SUCCESS":
            raise SystemExit(
                f"Local Agent acceptance run failed: {status['error_code']}"
            )
        receipt = service.get_receipt(run_id)
        report_path, _ = service.get_result(run_id)
        result = {
            "status": "PASS",
            "source_upload_bytes": 0,
            "source_file_count": preview["file_count"],
            "source_total_bytes": preview["total_bytes"],
            "manifest_mode": preview["mode"],
            "manifest_sha256": preview["sha256"],
            "tool_code": receipt["tool_code"],
            "release_sha256": receipt["release_sha256"],
            "record_count": receipt["summary"]["record_count"],
            "parameter_count": receipt["summary"]["parameter_count"],
            "engine_elapsed_seconds": receipt["summary"]["elapsed_seconds"],
            "wall_elapsed_seconds": round(time.monotonic() - started, 3),
            "result_size_bytes": receipt["result"]["size_bytes"],
            "result_sha256": receipt["result"]["sha256"],
            "result_exists_before_ack": report_path.is_file(),
        }
        if args.server_url:
            headers: dict[str, str] = {}
            access_token = os.getenv("TMS_ACCEPTANCE_ACCESS_TOKEN", "").strip()
            if access_token:
                headers["Authorization"] = f"Bearer {access_token}"
            with report_path.open("rb") as result_stream:
                response = httpx.post(
                    args.server_url.rstrip("/")
                    + "/api/v1/quick-analysis/local-results",
                    headers=headers,
                    files={
                        "receipt_json": (
                            None,
                            json.dumps(
                                receipt,
                                ensure_ascii=False,
                                separators=(",", ":"),
                            ),
                            "application/json",
                        ),
                        "result_file": (
                            report_path.name,
                            result_stream,
                            (
                                "application/vnd.openxmlformats-officedocument."
                                "spreadsheetml.sheet"
                            ),
                        ),
                    },
                    timeout=120.0,
                )
            response.raise_for_status()
            registration = response.json()
            result["registered_analysis_session_id"] = registration[
                "analysis_session_id"
            ]
            result["registered_status"] = registration["status"]
        service.delete_run(run_id)
        result["local_workspace_removed_after_ack"] = not (
            config.work_root / run_id
        ).exists()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    finally:
        if service is not None:
            service.close()
        shutil.rmtree(temporary_root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
