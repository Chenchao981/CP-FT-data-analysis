from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

from app.domain.cleaner_registry import CleanerRelease
from app.infrastructure.quick_pat_runner import QuickPatRunner
from app.infrastructure.source_catalog import SourceCatalog, SourceRoot
from openpyxl import Workbook


def _release(package: Path, runtime: Path) -> CleanerRelease:
    return CleanerRelease(
        21,
        8,
        "FT",
        "JIEQUN",
        "JIEQUN_FT_QUICK_PAT_EXISTING",
        "route-a-v1",
        "JIEQUN_FT_QUICK_PAT_EXISTING",
        "sha256-test",
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


def test_runner_invokes_released_package_and_builds_auditable_artifacts(
    tmp_path: Path,
) -> None:
    package = tmp_path / "ft_data_cleaner.pyz"
    runtime = tmp_path / "python.exe"
    package.write_bytes(b"released-pat")
    runtime.touch()
    source = tmp_path / "source" / "product"
    source.mkdir(parents=True)
    (source / "one.csv").write_text("x\n1\n", encoding="utf-8")
    catalog = SourceCatalog(
        (
            SourceRoot(
                "ROOT", "Root", tmp_path / "source", "FT", "JIEQUN", (".csv",)
            ),
        )
    )
    manifest = catalog.build_manifest("ROOT", "product")
    output = tmp_path / "output"

    def fake_run(command, **kwargs):
        assert command[:2] == [str(runtime), "-c"]
        assert "generate_raw_pat" in command[2]
        assert kwargs["env"]["TMS_QUICK_PAT_INPUT"] == str(source.resolve())
        report = output / "PAT_001" / "PAT_001.xlsx"
        report.parent.mkdir(parents=True)
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(["统计量", "总计数", "均值", "标准差"])
        sheet.append(["变量", "总计数", "均值", "标准差"])
        sheet.append(["VTH", 100, 4.1, 0.2])
        sheet.append(["RDON", 98, 0.01, 0.001])
        workbook.save(report)
        return subprocess.CompletedProcess(
            command,
            0,
            stdout="杰群 PAT 原始数据汇总完成: 文件=1, 解析行=100, 参数=2\n",
            stderr="",
        )

    result = QuickPatRunner(process_runner=fake_run).run_release(
        release=_release(package, runtime),
        input_directory=source,
        output_root=output,
        source_manifest_json=manifest.as_json(),
        source_manifest_sha256=manifest.sha256,
    )
    assert result.parameter_count == 2
    assert result.record_count == 100
    assert {item.role for item in result.artifacts} == {
        "pat_report",
        "pat_summary",
        "source_manifest",
    }
    assert result.summary["parameters"][1]["parameter"] == "RDON"
    assert all(len(item.sha256) == 64 for item in result.artifacts)
