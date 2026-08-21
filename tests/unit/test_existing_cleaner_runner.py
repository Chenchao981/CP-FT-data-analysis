from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app.infrastructure.existing_cleaner_runner import ExistingCleanerRunner


def _runtime(tmp_path: Path) -> tuple[Path, Path, Path]:
    python = tmp_path / "python.exe"
    cp_release = tmp_path / "cp_release"
    ft_release = tmp_path / "ft_release"
    python.touch()
    cp_release.mkdir()
    ft_release.mkdir()
    (cp_release / "app.pyz").touch()
    (ft_release / "ft_data_cleaner.pyz").touch()
    return python, cp_release, ft_release


def test_cp_adapter_invokes_release_package_and_reports_three_artifacts(tmp_path: Path) -> None:
    python, cp_release, ft_release = _runtime(tmp_path)
    source = tmp_path / "input.zip"
    source.touch()
    output = tmp_path / "output"

    def fake_run(command, **kwargs):
        assert command[:2] == [str(python), "-c"]
        assert kwargs["cwd"] == str(cp_release)
        assert kwargs["env"]["TMS_EXISTING_CLEANER_PACKAGE"].endswith("app.pyz")
        output.mkdir(exist_ok=True)
        (output / "LOT_cleaned_1.csv").write_text("Lot_ID\nLOT\n", encoding="utf-8")
        (output / "LOT_yield_1.csv").write_text("Yield\n100%\n", encoding="utf-8")
        (output / "LOT_spec_1.csv").write_text("Parameter\nVTH\n", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    result = ExistingCleanerRunner(
        cp_release_dir=cp_release,
        ft_release_dir=ft_release,
        python_executable=python,
        process_runner=fake_run,
    ).run(test_stage="CP", factory="huahong", inputs=[source], output_root=output)

    assert [item.role for item in result.artifacts] == ["cleaned", "yield", "spec"]


def test_ft_adapter_requires_one_directory(tmp_path: Path) -> None:
    python, cp_release, ft_release = _runtime(tmp_path)
    source = tmp_path / "input.xlsx"
    source.touch()
    runner = ExistingCleanerRunner(
        cp_release_dir=cp_release,
        ft_release_dir=ft_release,
        python_executable=python,
    )
    with pytest.raises(ValueError, match="one input directory"):
        runner.run(
            test_stage="FT",
            factory="riyuexin",
            inputs=[source],
            output_root=tmp_path / "output",
        )
