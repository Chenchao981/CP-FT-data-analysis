from __future__ import annotations

import hashlib
import subprocess
import zipfile
from pathlib import Path

import pytest
from app.domain.cleaner_registry import CleanerRelease
from app.infrastructure.direct_path_source import build_direct_path_manifest
from app.infrastructure.quick_tool_runner import QuickToolRunner


def _release(package: Path, runtime: Path) -> CleanerRelease:
    return CleanerRelease(
        31,
        9,
        "FT",
        "JIEQUN",
        "JIEQUN_FT_QUICK_PAT_EXISTING",
        "route-a-v1",
        "JIEQUN_FT_QUICK_PAT_EXISTING",
        "v-test",
        hashlib.sha256(package.read_bytes()).hexdigest(),
        str(package),
        str(runtime),
        "factories.jiequn.pat_cleaner.generate_raw_pat",
        "JIEQUN_FT_QUICK_PAT_PYZ",
        "JIEQUN_UNIFIED_CSV_DIRECTORY_V1",
        "FT_PAT_RESULT_V1",
        None,
        30,
        10_000_000,
    )


@pytest.mark.parametrize(
    ("analysis_type", "result_name", "expected_suffix", "record_count"),
    [
        ("QUICK_CLEAN", "cleaned.xlsx", ".zip", None),
        ("QUICK_CHART", "scatter.html", ".zip", 123),
        ("QUICK_SYL_SBL", "yield.xlsx", ".xlsx", None),
    ],
)
def test_ft_personal_tools_keep_results_without_copying_raw_source(
    analysis_type: str,
    result_name: str,
    expected_suffix: str,
    record_count: int | None,
    tmp_path: Path,
) -> None:
    package = tmp_path / "ft_data_cleaner.pyz"
    runtime = tmp_path / "python.exe"
    package.write_bytes(b"released-ft-tool")
    runtime.touch()
    if analysis_type == "QUICK_SYL_SBL":
        source = tmp_path / "yield-source.xlsx"
        source.write_bytes(b"source-workbook")
        allowed = (".xlsx",)
    else:
        source = tmp_path / "source"
        source.mkdir()
        (source / "raw.csv").write_text("x\n1\n", encoding="utf-8")
        allowed = (".csv",)
    _, manifest = build_direct_path_manifest(
        source,
        allowed_suffixes=allowed,
        allowed_single_file_suffixes=allowed,
    )
    output = tmp_path / "output"

    def fake_run(command, **kwargs):
        assert command[:2] == [str(runtime), "-c"]
        env = kwargs["env"]
        assert env["TMS_FT_TOOL_OPERATION"] == analysis_type
        result = Path(env["TMS_FT_TOOL_OUTPUT"]) / result_name
        result.parent.mkdir(parents=True, exist_ok=True)
        result.write_bytes(b"result-only")
        stdout = "TMS_RECORD_COUNT=123\n" if analysis_type == "QUICK_CHART" else ""
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

    result = QuickToolRunner(process_runner=fake_run).run_release(
        analysis_type=analysis_type,
        release=_release(package, runtime),
        input_directory=source,
        output_root=output,
        source_manifest_json=manifest.as_json(),
        source_manifest_sha256=manifest.sha256,
    )

    primary = next(item for item in result.artifacts if item.role == "result_package")
    assert Path(primary.path).suffix == expected_suffix
    assert result.record_count == record_count
    assert result.summary["raw_source_retained"] is False
    assert not (output / "intermediate").exists()
    assert not (output / "results").exists()
    assert not any(path.name == source.name for path in output.rglob("*"))
    if expected_suffix == ".zip":
        with zipfile.ZipFile(primary.path) as archive:
            assert archive.namelist() == [result_name]
