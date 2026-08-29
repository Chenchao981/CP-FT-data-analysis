from __future__ import annotations

import hashlib
import json
import re
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

from app.domain.cleaner_registry import CleanerRelease
from app.domain.quick_analysis import QuickAnalysisArtifact
from app.infrastructure.child_process_environment import isolated_child_environment

ProcessRunner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True, slots=True)
class QuickPatRunResult:
    parameter_count: int
    record_count: int | None
    summary: dict[str, Any]
    artifacts: tuple[QuickAnalysisArtifact, ...]
    stdout_tail: str


class QuickPatRunner:
    """Invoke the released Jiequn low-memory raw PAT implementation."""

    def __init__(self, process_runner: ProcessRunner = subprocess.run) -> None:
        self._process_runner = process_runner

    def run_release(
        self,
        *,
        release: CleanerRelease,
        input_directory: str | Path,
        output_root: str | Path,
        source_manifest_json: str,
        source_manifest_sha256: str,
    ) -> QuickPatRunResult:
        if (
            release.test_stage != "FT"
            or release.factory_code != "JIEQUN"
            or release.adapter_code != "JIEQUN_FT_QUICK_PAT_PYZ"
            or release.input_contract_version != "JIEQUN_UNIFIED_CSV_DIRECTORY_V1"
            or release.output_contract_version != "FT_PAT_RESULT_V1"
        ):
            raise ValueError("released tool is not an approved Jiequn Quick PAT adapter")
        source = Path(input_directory).resolve()
        package = Path(release.artifact_uri).resolve()
        runtime = Path(release.runtime_uri).resolve()
        target = Path(output_root).resolve()
        if not source.is_dir():
            raise FileNotFoundError(f"Quick PAT input directory is unavailable: {source}")
        for required in (runtime, package):
            if not required.is_file():
                raise FileNotFoundError(f"Quick PAT runtime is unavailable: {required}")
        if _file_sha256(package) != release.code_checksum.lower():
            raise RuntimeError(
                f"PAT package checksum differs from released contract: {package}"
            )
        actual_manifest_sha = hashlib.sha256(
            source_manifest_json.encode("utf-8")
        ).hexdigest()
        if actual_manifest_sha != source_manifest_sha256.lower():
            raise RuntimeError("source Manifest JSON does not match its SHA-256")
        manifest = json.loads(source_manifest_json)
        target.mkdir(parents=True, exist_ok=True)

        env = isolated_child_environment(
            {
                "TMS_QUICK_PAT_PACKAGE": str(package),
                "TMS_QUICK_PAT_INPUT": str(source),
                "TMS_QUICK_PAT_OUTPUT": str(target),
                "PYTHONUTF8": "1",
                "PYTHONIOENCODING": "utf-8",
            }
        )
        started = time.perf_counter()
        try:
            completed = self._process_runner(
                [str(runtime), "-c", _JIEQUN_QUICK_PAT_SCRIPT],
                cwd=str(package.parent),
                env=env,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                check=False,
                timeout=release.timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"Quick PAT timed out after {release.timeout_seconds}s"
            ) from exc
        elapsed_seconds = time.perf_counter() - started
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "unknown PAT error")[-4000:]
            raise RuntimeError(f"released Jiequn Quick PAT failed: {detail}")

        reports = sorted(
            target.rglob("PAT_*.xlsx"), key=lambda item: str(item).casefold()
        )
        if len(reports) != 1:
            raise RuntimeError(
                f"Quick PAT output contract requires one PAT Excel, found {len(reports)}"
            )
        report = reports[0].resolve()
        parameter_rows = _read_pat_rows(report)
        counts = [
            int(row["count"])
            for row in parameter_rows
            if isinstance(row.get("count"), (int, float))
        ]
        engine_file_count, parsed_record_count, engine_parameter_count = (
            _parse_engine_summary(completed.stdout or "")
        )
        if engine_file_count != manifest.get("file_count"):
            raise RuntimeError(
                "PAT engine source count differs from the submitted Manifest: "
                f"{engine_file_count}!={manifest.get('file_count')}"
            )
        if engine_parameter_count != len(parameter_rows):
            raise RuntimeError(
                "PAT engine parameter count differs from the result workbook: "
                f"{engine_parameter_count}!={len(parameter_rows)}"
            )
        summary: dict[str, Any] = {
            "schema_version": 1,
            "analysis_type": "QUICK_PAT",
            "test_stage": "FT",
            "factory_code": "JIEQUN",
            "input_contract": release.input_contract_version,
            "output_contract": release.output_contract_version,
            "formula_contract": "SIGMA_IQR_1_35_MEDIAN_PLUS_MINUS_6SIGMA_V1",
            "manifest_mode": manifest.get("mode"),
            "source_file_count": manifest.get("file_count"),
            "source_total_bytes": manifest.get("total_bytes"),
            "source_manifest_sha256": source_manifest_sha256,
            "parameter_count": len(parameter_rows),
            "record_count": parsed_record_count,
            "record_count_basis": "PAT_ENGINE_PARSED_ROWS",
            "maximum_parameter_value_count": max(counts) if counts else None,
            "minimum_parameter_value_count": min(counts) if counts else None,
            "elapsed_seconds": round(elapsed_seconds, 3),
            "parameters": parameter_rows,
        }
        manifest_path = target / "source_manifest.json"
        manifest_path.write_text(source_manifest_json, encoding="utf-8")
        summary_path = target / "pat_summary.json"
        summary_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        artifacts = tuple(
            _artifact(role, path)
            for role, path in (
                ("pat_report", report),
                ("pat_summary", summary_path),
                ("source_manifest", manifest_path),
            )
        )
        output_bytes = sum(
            path.stat().st_size for path in target.rglob("*") if path.is_file()
        )
        if output_bytes > release.max_output_bytes:
            raise RuntimeError(
                "Quick PAT output exceeds released limit: "
                f"{output_bytes}>{release.max_output_bytes}"
            )
        return QuickPatRunResult(
            len(parameter_rows),
            parsed_record_count,
            summary,
            artifacts,
            (completed.stdout or "")[-4000:],
        )


def _read_pat_rows(path: Path) -> list[dict[str, Any]]:
    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        sheet = workbook.active
        rows: list[dict[str, Any]] = []
        keys = (
            "parameter",
            "count",
            "mean",
            "stddev",
            "minimum",
            "q1",
            "median",
            "q3",
            "maximum",
            "sigma",
            "lcl_calculated",
            "ucl_calculated",
            "lcl_before",
            "ucl_before",
            "lcl_after",
            "ucl_after",
            "updated",
        )
        for index, values in enumerate(sheet.iter_rows(values_only=True), start=1):
            if index <= 2:
                continue
            normalized = list(values[: len(keys)])
            normalized.extend([None] * (len(keys) - len(normalized)))
            parameter = str(normalized[0]).strip() if normalized[0] is not None else ""
            if not parameter:
                continue
            if not isinstance(normalized[1], (int, float)):
                raise TypeError(f"PAT Excel has an invalid count for {parameter}")
            rows.append(dict(zip(keys, normalized, strict=True)))
    finally:
        workbook.close()
    if not rows:
        raise RuntimeError("PAT Excel contains no parameter rows")
    return rows


def _parse_engine_summary(stdout: str) -> tuple[int, int, int]:
    matches = re.findall(
        r"PAT [^\r\n]*?:\s*文件=([\d,]+),\s*解析行=([\d,]+),\s*参数=(\d+)",
        stdout,
    )
    if len(matches) != 1:
        raise RuntimeError("PAT engine did not emit one auditable source summary")
    files, rows, parameters = matches[0]
    return int(files.replace(",", "")), int(rows.replace(",", "")), int(parameters)


def _artifact(role: str, path: Path) -> QuickAnalysisArtifact:
    resolved = path.resolve()
    return QuickAnalysisArtifact(
        role,
        str(resolved),
        resolved.stat().st_size,
        _file_sha256(resolved),
    )


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


_JIEQUN_QUICK_PAT_SCRIPT = """
import os, sys
from pathlib import Path
package = os.environ['TMS_QUICK_PAT_PACKAGE']
source = Path(os.environ['TMS_QUICK_PAT_INPUT']).resolve()
output = Path(os.environ['TMS_QUICK_PAT_OUTPUT']).resolve()
sys.path.insert(0, package)
from factories.jiequn.dc_auto import DC_FORMAT_UNIFIED, detect_dc_format
from factories.jiequn.pat_cleaner import generate_raw_pat
detection = detect_dc_format(source)
if detection.format_name != DC_FORMAT_UNIFIED:
    raise SystemExit(
        f'P0 Quick PAT only accepts Jiequn unified CSV, detected {detection.format_name}'
    )
detected = {path.resolve() for path in detection.files}
all_csv = {
    path.resolve() for path in source.rglob('*')
    if path.is_file() and path.suffix.lower() == '.csv'
}
if detected != all_csv:
    raise SystemExit(
        f'Jiequn unified CSV contract mismatch: detected={len(detected)}, csv={len(all_csv)}'
    )
result = generate_raw_pat(source_dir=source, output_dir=output)
if not result:
    raise SystemExit('Jiequn Quick PAT returned no result')
print(f'TMS_QUICK_PAT_RESULT={Path(result).resolve()}')
"""
