from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
for import_root in (ROOT, BACKEND):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from app.domain.auth import DEVELOPMENT_PRINCIPAL
from app.domain.cleaner_registry import CleanerRelease
from app.domain.jobs import Job, JobStatus, JobType, TriggerType
from app.domain.quick_analysis import (
    DIRECT_PATH_TOOL_CONTRACTS,
    InMemoryQuickAnalysisService,
    NewQuickAnalysisSession,
)
from app.infrastructure.direct_path_source import build_direct_path_manifest
from app.infrastructure.quick_pat_runner import QuickPatRunner
from app.infrastructure.quick_tool_runner import QuickToolRunner
from app.infrastructure.source_catalog import SourceCatalog
from app.workers.route_a_worker import QuickPatHandler

from scripts.g0.verify_quick_pat_e2e import (
    MonitoredProcessRunner,
    directory_bytes,
    file_sha256,
)


class Registry:
    def __init__(self, release: CleanerRelease) -> None:
        self.release = release

    def get_released(self, cleaner_release_id: int) -> CleanerRelease:
        if cleaner_release_id != self.release.cleaner_release_id:
            raise KeyError(cleaner_release_id)
        return self.release


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run a personal direct-path CP/FT PAT against real source data and "
            "verify that only result artifacts remain"
        )
    )
    parser.add_argument("--source", required=True)
    parser.add_argument(
        "--tool-code",
        required=True,
        choices=tuple(DIRECT_PATH_TOOL_CONTRACTS),
    )
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--package")
    parser.add_argument(
        "--runtime", default=r"D:\ProgramData\anaconda3\python.exe"
    )
    return parser.parse_args()


def _release(
    tool_code: str,
    package: Path,
    runtime: Path,
) -> CleanerRelease:
    contract = DIRECT_PATH_TOOL_CONTRACTS[tool_code]
    checksum = file_sha256(package)
    return CleanerRelease(
        1,
        1,
        str(contract["test_stage"]),
        str(contract["factory_code"]),
        str(contract["format_code"]),
        "route-a-v1",
        str(contract["cleaner_code"]),
        f"sha256-{checksum[:12]}",
        checksum,
        str(package),
        str(runtime),
        "existing released cleaner and PAT entrypoint",
        str(contract["adapter_code"]),
        str(contract["input_contract_version"]),
        str(contract["output_contract_version"]),
        None,
        7200,
        10 * 1024**3,
    )


def main() -> None:
    args = parse_args()
    contract = DIRECT_PATH_TOOL_CONTRACTS[args.tool_code]
    source = Path(args.source).resolve()
    output_root = Path(args.output_root).resolve()
    package = Path(
        args.package
        or (
            r"F:\cp_data_ansys\packaging\release\app.pyz"
            if contract["test_stage"] == "CP"
            else r"F:\data_IGBT_multiple\packaging\release\ft_data_cleaner.pyz"
        )
    ).resolve()
    runtime = Path(args.runtime).resolve()
    for required in (source, package, runtime):
        if not required.exists():
            raise FileNotFoundError(required)

    resolved_source, before = build_direct_path_manifest(
        source,
        allowed_suffixes=tuple(contract["allowed_suffixes"]),
        allowed_single_file_suffixes=tuple(contract["single_file_suffixes"]),
        path_policy=str(contract["manifest_policy"]),
    )
    release = _release(args.tool_code, package, runtime)
    service = InMemoryQuickAnalysisService()
    session = service.create(
        DEVELOPMENT_PRINCIPAL,
        NewQuickAnalysisSession(
            "QUICK_PAT",
            release.test_stage,
            release.factory_code,
            "LOCAL_AGENT",
            str(resolved_source),
            before.mode,
            before.as_json(),
            before.sha256,
            before.file_count,
            before.total_bytes,
            "RESULT_ONLY",
            release.cleaner_release_id,
            datetime.now(UTC) + timedelta(days=7),
            "PERSONAL",
            None,
        ),
    )
    job_id = time.time_ns() // 1_000_000
    service.attach_job(session.analysis_session_id, job_id)
    now = datetime.now(UTC)
    job = Job(
        job_id,
        None,
        None,
        session.analysis_session_id,
        release.cleaner_release_id,
        JobType.QUICK_PAT,
        TriggerType.MANUAL,
        DEVELOPMENT_PRINCIPAL.login_name,
        DEVELOPMENT_PRINCIPAL.user_id,
        "real personal direct-path Quick PAT verification",
        JobStatus.RUNNING,
        now,
        started_at_utc=now,
        lease_token="11111111-1111-1111-1111-111111111111",
        attempt_count=1,
    )
    attempt_root = output_root / str(job_id) / "attempt-1"
    monitor = MonitoredProcessRunner(attempt_root)
    handler = QuickPatHandler(
        Registry(release),
        service,
        SourceCatalog(()),
        runner=QuickToolRunner(pat_runner=QuickPatRunner(process_runner=monitor)),
        work_root=output_root,
    )
    started = time.perf_counter()
    handler(job)
    total_wall_seconds = time.perf_counter() - started

    _resolved_after, after = build_direct_path_manifest(
        source,
        allowed_suffixes=tuple(contract["allowed_suffixes"]),
        allowed_single_file_suffixes=tuple(contract["single_file_suffixes"]),
        path_policy=str(contract["manifest_policy"]),
    )
    if after.as_json() != before.as_json() or after.sha256 != before.sha256:
        raise RuntimeError("source manifest changed while personal PAT was running")

    completed = service.get_for_principal(
        session.analysis_session_id, DEVELOPMENT_PRINCIPAL
    )
    artifact = service.result_artifact(
        session.analysis_session_id, DEVELOPMENT_PRINCIPAL
    )
    output_files = tuple(
        path for path in attempt_root.rglob("*") if path.is_file()
    )
    unexpected = tuple(
        path
        for path in output_files
        if path.suffix.lower() not in {".xlsx", ".json"}
    )
    intermediate_present = any(
        path.name in {"cp_cleaner_intermediate", ".single-source"}
        for path in attempt_root.rglob("*")
    )
    if unexpected or intermediate_present:
        raise RuntimeError("personal PAT retained raw or cleaning intermediate files")

    result = {
        "status": completed.status.value,
        "test_stage": completed.test_stage,
        "factory_code": completed.factory_code,
        "source_file_count": before.file_count,
        "source_total_bytes": before.total_bytes,
        "source_manifest_sha256": before.sha256,
        "source_unchanged": True,
        "package_sha256": release.code_checksum,
        "parameter_count": completed.parameter_count,
        "record_count": completed.record_count,
        "calculation_elapsed_seconds": (completed.summary or {}).get(
            "elapsed_seconds"
        ),
        "total_wall_seconds": round(total_wall_seconds, 3),
        "peak_child_rss_bytes": monitor.peak_rss_bytes,
        "peak_output_bytes": monitor.peak_output_bytes,
        "final_output_bytes": directory_bytes(attempt_root),
        "retained_file_count": len(output_files),
        "retained_suffixes": sorted({path.suffix.lower() for path in output_files}),
        "cleaning_intermediate_retained": intermediate_present,
        "result_file_name": Path(artifact.path).name,
        "result_size_bytes": artifact.size_bytes,
        "result_sha256": artifact.sha256,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
