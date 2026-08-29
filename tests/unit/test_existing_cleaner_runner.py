from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest
from app.domain.cleaner_registry import CleanerRelease
from app.infrastructure.existing_cleaner_runner import (
    _FT_RIYUEGUANG_DC_SCRIPT,
    _FT_RIYUEXIN_DC_SCRIPT,
    CleanerInputRequired,
    ExistingCleanerRunner,
)


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
    monkeypatch, tmp_path: Path,
) -> None:
    python, cp_release, ft_release = _runtime(tmp_path)
    source = tmp_path / "input.zip"
    source.touch()
    output = tmp_path / "output"
    monkeypatch.setenv("TMS_DATABASE_URL", "must-not-reach-cleaner")
    monkeypatch.setenv("TMS_JWT_SECRET", "must-not-reach-cleaner")

    def fake_run(command, **kwargs):
        assert command[:2] == [str(python), "-c"]
        assert kwargs["cwd"] == str(cp_release)
        assert kwargs["env"]["TMS_EXISTING_CLEANER_PACKAGE"].endswith("app.pyz")
        assert kwargs["env"]["PYTHONIOENCODING"] == "utf-8"
        assert kwargs["env"]["PYTHONUTF8"] == "1"
        assert "TMS_DATABASE_URL" not in kwargs["env"]
        assert "TMS_JWT_SECRET" not in kwargs["env"]
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


def test_ft_adapter_requires_registered_xlsx_files(tmp_path: Path) -> None:
    python, cp_release, ft_release = _runtime(tmp_path)
    source = tmp_path / "input.csv"
    source.touch()
    runner = ExistingCleanerRunner(
        cp_release_dir=cp_release,
        ft_release_dir=ft_release,
        python_executable=python,
    )
    with pytest.raises(ValueError, match="exact registered XLSX"):
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
    registered = source / "registered.xlsx"
    registered.write_bytes(b"source-data")
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
        isolated = Path(json.loads(kwargs["env"]["TMS_EXISTING_CLEANER_INPUTS"])[0])
        assert isolated != source
        assert [path.name for path in isolated.glob("*.xlsx")] == ["registered.xlsx"]
        assert kwargs["env"]["TMS_LOT_OVERRIDES_JSON"] == "{}"
        output.mkdir(exist_ok=True)
        (output / "result.xlsx").write_bytes(b"xlsx")
        (output / "ft_scatter_data.csv.gz").write_bytes(b"data")
        (output / "ft_scatter_spec.csv").write_bytes(b"spec")
        (output / "ft_scatter_manifest.json").write_bytes(b"manifest")
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    result = ExistingCleanerRunner(process_runner=fake_run).run_release(
        release=release,
        inputs=[registered],
        output_root=output,
        expected_sha256=[hashlib.sha256(registered.read_bytes()).hexdigest()],
    )

    assert {artifact.role for artifact in result.artifacts} == {
        "cleaned",
        "scatter_data",
        "scatter_spec",
        "scatter_manifest",
    }


def test_released_ft_runner_isolates_only_registered_files_and_passes_lot_override(
    tmp_path: Path,
) -> None:
    python, _cp_release, ft_release = _runtime(tmp_path)
    package = ft_release / "ft_data_cleaner.pyz"
    package.write_bytes(b"released-ft-cleaner")
    checksum = hashlib.sha256(package.read_bytes()).hexdigest()
    registered = tmp_path / "registered.xlsx"
    neighbour = tmp_path / "neighbour.xlsx"
    registered.write_bytes(b"registered-source")
    neighbour.write_bytes(b"must-not-be-seen")
    source_hash = hashlib.sha256(registered.read_bytes()).hexdigest()
    output = tmp_path / "output"
    release = CleanerRelease(
        19,
        8,
        "FT",
        "RIYUEXIN",
        "RIYUEXIN_DC_EXISTING",
        "route-a-v1",
        "RIYUEXIN_FT_EXISTING",
        "sha256-test",
        checksum,
        str(package),
        str(python),
        "entry",
        "RIYUEXIN_FT_PYZ",
        "FT_DIRECTORY_XLSX_V1",
        "FT_XLSX_SCATTER_V1",
        None,
        30,
        10000,
    )

    process_calls = 0

    def fake_run(command, **kwargs):
        nonlocal process_calls
        process_calls += 1
        isolated = Path(json.loads(kwargs["env"]["TMS_EXISTING_CLEANER_INPUTS"])[0])
        assert [path.name for path in isolated.glob("*.xlsx")] == ["registered.xlsx"]
        assert json.loads(kwargs["env"]["TMS_LOT_OVERRIDES_JSON"]) == {
            "registered.xlsx": "MANUAL-LOT"
        }
        output.mkdir(exist_ok=True)
        (output / "result.xlsx").write_bytes(b"xlsx")
        (output / "ft_scatter_data.csv.gz").write_bytes(b"data")
        (output / "ft_scatter_spec.csv").write_bytes(b"spec")
        (output / "ft_scatter_manifest.json").write_bytes(b"manifest")
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    ExistingCleanerRunner(process_runner=fake_run).run_release(
        release=release,
        inputs=[registered],
        output_root=output,
        lot_overrides={"registered.xlsx": "MANUAL-LOT"},
        expected_sha256=[source_hash],
    )
    assert hashlib.sha256(registered.read_bytes()).hexdigest() == source_hash
    assert neighbour.read_bytes() == b"must-not-be-seen"

    registered.write_bytes(b"tampered-while-waiting-for-lot")
    with pytest.raises(RuntimeError, match="checksum differs from current bytes"):
        ExistingCleanerRunner(process_runner=fake_run).run_release(
            release=release,
            inputs=[registered],
            output_root=tmp_path / "tampered-output",
            lot_overrides={"registered.xlsx": "MANUAL-LOT"},
            expected_sha256=[source_hash],
        )
    assert process_calls == 1


def test_runner_converts_exact_input_required_marker(tmp_path: Path) -> None:
    python, cp_release, _ft_release = _runtime(tmp_path)
    package = cp_release / "app.pyz"
    package.write_bytes(b"released-cleaner")
    source = tmp_path / "input.zip"
    source.touch()
    release = CleanerRelease(
        9,
        3,
        "CP",
        "HUAHONG",
        "HUAHONG_DCP_EXISTING",
        "route-a-v1",
        "HUAHONG_CP_EXISTING",
        "sha256-test",
        hashlib.sha256(package.read_bytes()).hexdigest(),
        str(package),
        str(python),
        "entry",
        "HUAHONG_CP_PYZ",
        "input-v1",
        "CP_CSV_TRIPLET_V1",
        None,
        30,
        10000,
    )
    marker = {
        "field_code": "LOT_ID",
        "files": [{"original_file_name": "missing-lot.xlsx"}],
        "message": "请确认批次号",
    }

    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(
            command,
            42,
            stdout="",
            stderr="TMS_INPUT_REQUIRED_JSON=" + json.dumps(marker, ensure_ascii=False),
        )

    with pytest.raises(CleanerInputRequired) as captured:
        ExistingCleanerRunner(process_runner=fake_run).run_release(
            release=release,
            inputs=[source],
            output_root=tmp_path / "output",
        )
    assert captured.value.field_code == "LOT_ID"
    assert captured.value.files == ("missing-lot.xlsx",)


@pytest.mark.parametrize("returncode", (0, 1, 41, 43))
def test_runner_rejects_marker_without_reserved_exit_code(
    tmp_path: Path, returncode: int
) -> None:
    python, cp_release, _ft_release = _runtime(tmp_path)
    source = tmp_path / "missing-lot.xlsx"
    source.touch()
    marker = {
        "field_code": "LOT_ID",
        "files": [{"original_file_name": source.name}],
        "message": "请确认批次号",
    }

    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(
            command,
            returncode,
            stdout="",
            stderr="TMS_INPUT_REQUIRED_JSON="
            + json.dumps(marker, ensure_ascii=False),
        )

    with pytest.raises(RuntimeError, match="expected 42"):
        ExistingCleanerRunner(process_runner=fake_run)._execute(
            stage="CP",
            factory_code="huahong",
            normalized_inputs=(source,),
            target=tmp_path / "output",
            runtime=python,
            package=cp_release / "app.pyz",
            release_dir=cp_release,
            script="pass",
            timeout_seconds=30,
            max_output_bytes=10_000,
            execution_config_json=None,
            lot_overrides=None,
        )


def test_real_child_process_preserves_chinese_input_required_prompt(
    tmp_path: Path,
) -> None:
    release_directory = tmp_path / "release"
    release_directory.mkdir()
    package = release_directory / "cleaner.pyz"
    package.touch()
    source = tmp_path / "missing-lot.xlsx"
    source.touch()
    message = "未从文件名识别到批次号，请人工确认"
    script = (
        "import json,sys\n"
        f"payload={{'field_code':'LOT_ID','files':[{{'original_file_name':'{source.name}'}}],"
        f"'message':'{message}'}}\n"
        "sys.stderr.write('TMS_INPUT_REQUIRED_JSON=' + "
        "json.dumps(payload,ensure_ascii=False,separators=(',',':')) + '\\n')\n"
        "raise SystemExit(42)\n"
    )

    with pytest.raises(CleanerInputRequired) as captured:
        ExistingCleanerRunner()._execute(
            stage="CP",
            factory_code="test",
            normalized_inputs=(source,),
            target=tmp_path / "output",
            runtime=Path(sys.executable),
            package=package,
            release_dir=release_directory,
            script=script,
            timeout_seconds=30,
            max_output_bytes=10_000,
            execution_config_json=None,
            lot_overrides=None,
        )

    assert captured.value.message == message
    assert captured.value.files == (source.name,)


@pytest.mark.parametrize(
    "script",
    (_FT_RIYUEXIN_DC_SCRIPT, _FT_RIYUEGUANG_DC_SCRIPT),
)
def test_ft_subprocess_script_freezes_lot_input_contract(script: str) -> None:
    compile(script, "<existing-ft-cleaner>", "exec")
    assert "TMS_LOT_OVERRIDES_JSON" in script
    assert "lot_overrides=lot_overrides" in script
    assert "except LotOverrideRequired as exc" in script
    assert "TMS_INPUT_REQUIRED_JSON=" in script
    assert "'original_file_name': name" in script
