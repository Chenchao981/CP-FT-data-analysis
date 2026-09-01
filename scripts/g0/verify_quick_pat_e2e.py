from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psutil

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domain.auth import DEVELOPMENT_PRINCIPAL
from app.domain.cleaner_registry import CleanerRelease
from app.domain.jobs import Job, JobStatus, JobType, TriggerType
from app.domain.quick_analysis import (
    InMemoryQuickAnalysisService,
    NewQuickAnalysisSession,
)
from app.infrastructure.quick_pat_runner import QuickPatRunner
from app.infrastructure.source_catalog import SourceCatalog, SourceRoot
from app.workers.route_a_worker import QuickPatHandler


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the TMS Quick PAT path against an approved real directory"
    )
    parser.add_argument("--source-root", required=True)
    parser.add_argument("--relative-path", default=".")
    parser.add_argument("--output-root", required=True)
    parser.add_argument(
        "--package",
        default=r"F:\data_IGBT_multiple\packaging\release\ft_data_cleaner.pyz",
    )
    parser.add_argument(
        "--runtime", default=r"D:\ProgramData\anaconda3\python.exe"
    )
    return parser.parse_args()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def directory_bytes(root: Path) -> int:
    total = 0
    for path in root.rglob("*"):
        try:
            if path.is_file():
                total += path.stat().st_size
        except OSError:
            # PAT spool files are intentionally removed as each parameter ends.
            continue
    return total


class MonitoredProcessRunner:
    def __init__(self, output_root: Path) -> None:
        self.output_root = output_root
        self.peak_rss_bytes = 0
        self.peak_output_bytes = 0
        self.wall_seconds = 0.0

    def __call__(self, command, **kwargs) -> subprocess.CompletedProcess[str]:
        timeout = float(kwargs.pop("timeout"))
        kwargs.pop("check", None)
        kwargs.pop("capture_output", None)
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            **kwargs,
        )
        tracked = psutil.Process(process.pid)
        started = time.perf_counter()
        stdout = ""
        stderr = ""
        try:
            while True:
                self._sample(tracked)
                remaining = timeout - (time.perf_counter() - started)
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(command, timeout)
                try:
                    stdout, stderr = process.communicate(timeout=min(0.25, remaining))
                    break
                except subprocess.TimeoutExpired:
                    continue
        except BaseException:
            for child in tracked.children(recursive=True):
                child.kill()
            process.kill()
            process.communicate()
            raise
        finally:
            self.wall_seconds = time.perf_counter() - started
            self.peak_output_bytes = max(
                self.peak_output_bytes, directory_bytes(self.output_root)
            )
        return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)

    def _sample(self, process: psutil.Process) -> None:
        rss = 0
        for item in (process, *process.children(recursive=True)):
            try:
                rss += item.memory_info().rss
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        self.peak_rss_bytes = max(self.peak_rss_bytes, rss)
        self.peak_output_bytes = max(
            self.peak_output_bytes, directory_bytes(self.output_root)
        )


class Registry:
    def __init__(self, release: CleanerRelease) -> None:
        self.release = release

    def get_released(self, cleaner_release_id: int) -> CleanerRelease:
        if cleaner_release_id != self.release.cleaner_release_id:
            raise KeyError(cleaner_release_id)
        return self.release


def formula_check(parameters: list[dict]) -> tuple[int, int]:
    checked = 0
    passed = 0
    for row in parameters:
        values = [row.get(key) for key in ("q1", "q3", "median", "sigma")]
        if not all(isinstance(value, (int, float)) for value in values):
            continue
        checked += 1
        q1, q3, median, sigma = (float(value) for value in values)
        expected_sigma = (q3 - q1) / 1.35
        expected_lcl = median - 6 * expected_sigma
        expected_ucl = median + 6 * expected_sigma
        if (
            abs(sigma - expected_sigma) <= 0.0001
            and abs(float(row["lcl_calculated"]) - expected_lcl) <= 0.0001
            and abs(float(row["ucl_calculated"]) - expected_ucl) <= 0.0001
        ):
            passed += 1
    return checked, passed


def main() -> None:
    args = parse_args()
    source_root = Path(args.source_root).resolve()
    output_root = Path(args.output_root).resolve()
    package = Path(args.package).resolve()
    runtime = Path(args.runtime).resolve()
    for required in (source_root, package, runtime):
        if not required.exists():
            raise FileNotFoundError(required)

    catalog = SourceCatalog(
        (
            SourceRoot(
                "JIEQUN_E2E",
                "Jiequn E2E",
                source_root,
                "FT",
                "JIEQUN",
                (".csv",),
                data_domain_code="JIEQUN_FT_E2E",
            ),
        )
    )
    manifest = catalog.build_manifest("JIEQUN_E2E", args.relative_path)
    release = CleanerRelease(
        1,
        1,
        "FT",
        "JIEQUN",
        "JIEQUN_FT_QUICK_PAT_EXISTING",
        "route-a-v1",
        "JIEQUN_FT_QUICK_PAT_EXISTING",
        f"sha256-{file_sha256(package)[:12]}",
        file_sha256(package),
        str(package),
        str(runtime),
        "factories.jiequn.pat_cleaner.generate_raw_pat",
        "JIEQUN_FT_QUICK_PAT_PYZ",
        "JIEQUN_UNIFIED_CSV_DIRECTORY_V1",
        "FT_PAT_RESULT_V1",
        None,
        3600,
        10 * 1024 * 1024 * 1024,
    )
    service = InMemoryQuickAnalysisService(
        domain_grant_checker=lambda user_id, domain_id: (
            user_id == DEVELOPMENT_PRINCIPAL.user_id and domain_id == 1
        )
    )
    session = service.create(
        DEVELOPMENT_PRINCIPAL,
        NewQuickAnalysisSession(
            "QUICK_PAT",
            "FT",
            "JIEQUN",
            manifest.root_code,
            manifest.selected_relative_path,
            manifest.mode,
            manifest.as_json(),
            manifest.sha256,
            manifest.file_count,
            manifest.total_bytes,
            "RESULT_ONLY",
            release.cleaner_release_id,
            datetime.now(UTC) + timedelta(days=7),
            "DOMAIN",
            1,
            "JIEQUN_FT_E2E",
        ),
    )
    job_id = int(datetime.now(UTC).timestamp())
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
        "real Quick PAT E2E verification",
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
        catalog,
        runner=QuickPatRunner(process_runner=monitor),
        work_root=output_root,
    )
    handler(job)
    completed = service.get_for_principal(
        session.analysis_session_id, DEVELOPMENT_PRINCIPAL
    )
    parameters = list((completed.summary or {}).get("parameters", []))
    formula_rows, formula_passed = formula_check(parameters)
    artifact = service.result_artifact(
        session.analysis_session_id, DEVELOPMENT_PRINCIPAL
    )
    result = {
        "status": completed.status.value,
        "source_file_count": manifest.file_count,
        "source_total_bytes": manifest.total_bytes,
        "source_manifest_sha256": manifest.sha256,
        "package_sha256": release.code_checksum,
        "parameter_count": completed.parameter_count,
        "record_count": completed.record_count,
        "formula_rows_checked": formula_rows,
        "formula_rows_passed": formula_passed,
        "calculation_elapsed_seconds": (completed.summary or {}).get(
            "elapsed_seconds"
        ),
        "monitored_wall_seconds": round(monitor.wall_seconds, 3),
        "peak_child_rss_bytes": monitor.peak_rss_bytes,
        "peak_output_bytes": monitor.peak_output_bytes,
        "final_output_bytes": directory_bytes(attempt_root),
        "result_path": artifact.path,
        "result_size_bytes": artifact.size_bytes,
        "result_sha256": artifact.sha256,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
