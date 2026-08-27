from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest
from app.domain.cleaner_registry import CleanerRelease
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


def test_cp_adapter_invokes_release_package_and_reports_three_artifacts(
    tmp_path: Path,
) -> None:
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


def test_released_contract_verifies_checksum_and_execution_config(
    tmp_path: Path,
) -> None:
    python, cp_release, _ft_release = _runtime(tmp_path)
    package = cp_release / "app.pyz"
    package.write_bytes(b"released-cleaner")
    checksum = hashlib.sha256(package.read_bytes()).hexdigest()
    source = tmp_path / "input.zip"
    source.touch()
    output = tmp_path / "output"
    release = CleanerRelease(
        9,
        3,
        "CP",
        "HUAHONG",
        "HUAHONG_DCP_EXISTING",
        "route-a-v1",
        "HUAHONG_CP_EXISTING",
        "sha256-test",
        checksum,
        str(package),
        str(python),
        "test-entrypoint",
        "HUAHONG_CP_PYZ",
        "CP_ARCHIVE_OR_TXT_V1",
        "CP_CSV_TRIPLET_V1",
        '{"outlier_method":"none","convert_units":false}',
        30,
        10000,
    )

    def fake_run(command, **kwargs):
        assert kwargs["timeout"] == 30
        assert (
            '"outlier_method":"none"' in kwargs["env"]["TMS_CLEANER_EXECUTION_CONFIG"]
        )
        output.mkdir(exist_ok=True)
        (output / "LOT_cleaned_1.csv").write_text("Lot_ID\nLOT\n", encoding="utf-8")
        (output / "LOT_yield_1.csv").write_text("Yield\n100%\n", encoding="utf-8")
        (output / "LOT_spec_1.csv").write_text("Parameter\nVTH\n", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    result = ExistingCleanerRunner(process_runner=fake_run).run_release(
        release=release,
        inputs=[source],
        output_root=output,
    )
    assert {item.role for item in result.artifacts} == {"cleaned", "yield", "spec"}
    assert all(len(item.sha256) == 64 for item in result.artifacts)


def test_released_contract_rejects_changed_package(tmp_path: Path) -> None:
    python, cp_release, _ft_release = _runtime(tmp_path)
    package = cp_release / "app.pyz"
    package.write_bytes(b"changed")
    source = tmp_path / "input.zip"
    source.touch()
    release = CleanerRelease(
        9,
        3,
        "CP",
        "HUAHONG",
        "HUAHONG",
        "v1",
        "CP",
        "v1",
        "0" * 64,
        str(package),
        str(python),
        "entry",
        "HUAHONG_CP_PYZ",
        "input-v1",
        "output-v1",
        None,
        30,
        10000,
    )
    with pytest.raises(RuntimeError, match="checksum"):
        ExistingCleanerRunner().run_release(
            release=release,
            inputs=[source],
            output_root=tmp_path / "output",
        )


@pytest.mark.parametrize(
    ("factory", "adapter_code", "module_name"),
    [
        ("RIYUEXIN", "RIYUEXIN_FT_PYZ", "tms_adapters.riyuexin_dc"),
        ("RIYUEGUANG", "RIYUEGUANG_FT_PYZ", "tms_adapters.riyueguang_dc"),
    ],
)
def test_released_ft_contract_uses_independent_factory_adapter(
    tmp_path: Path, factory: str, adapter_code: str, module_name: str
) -> None:
    python, _cp_release, ft_release = _runtime(tmp_path)
    package = ft_release / "ft_data_cleaner.pyz"
    package.write_bytes(b"released-ft-cleaner")
    source = tmp_path / "input"
    source.mkdir()
    output = tmp_path / "output"
    release = CleanerRelease(
        19,
        8,
        "FT",
        factory,
        f"{factory}_DC_EXISTING",
        "route-a-v1",
        f"{factory}_FT_EXISTING",
        "sha256-test",
        hashlib.sha256(package.read_bytes()).hexdigest(),
        str(package),
        str(python),
        "entry",
        adapter_code,
        "FT_DIRECTORY_XLSX_V1",
        "FT_XLSX_SCATTER_V1",
        None,
        30,
        10000,
    )

    def fake_run(command, **kwargs):
        assert module_name in command[2]
        output.mkdir(exist_ok=True)
        (output / "result.xlsx").write_bytes(b"xlsx")
        (output / "ft_scatter_data.csv.gz").write_bytes(b"data")
        (output / "ft_scatter_spec.csv").write_bytes(b"spec")
        (output / "ft_scatter_manifest.json").write_bytes(b"manifest")
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    result = ExistingCleanerRunner(process_runner=fake_run).run_release(
        release=release,
        inputs=[source],
        output_root=output,
    )

    assert {artifact.role for artifact in result.artifacts} == {
        "cleaned",
        "scatter_data",
        "scatter_spec",
        "scatter_manifest",
    }
