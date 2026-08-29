from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

from app.domain.cleaner_registry import CleanerRelease
from app.infrastructure.child_process_environment import isolated_child_environment

ProcessRunner = Callable[..., subprocess.CompletedProcess[str]]
INPUT_REQUIRED_PREFIX = "TMS_INPUT_REQUIRED_JSON="
INPUT_REQUIRED_EXIT_CODE = 42


class CleanerInputRequired(RuntimeError):
    """A released Cleaner proved that user input is the only missing identity."""

    def __init__(self, *, field_code: str, files: tuple[str, ...], message: str) -> None:
        super().__init__(message)
        self.field_code = field_code
        self.files = files
        self.message = message


@dataclass(frozen=True, slots=True)
class CleanerArtifact:
    role: str
    path: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True, slots=True)
class ExistingCleanerRunResult:
    test_stage: str
    factory: str
    output_root: str
    artifacts: tuple[CleanerArtifact, ...]
    stdout_tail: str

    def as_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["artifacts"] = [asdict(item) for item in self.artifacts]
        return data


class ExistingCleanerRunner:
    """Run proven CP/FT release packages in isolated child processes."""

    def __init__(
        self,
        *,
        cp_release_dir: str | Path | None = None,
        ft_release_dir: str | Path | None = None,
        python_executable: str | Path | None = None,
        process_runner: ProcessRunner = subprocess.run,
    ) -> None:
        self.cp_release_dir = Path(
            cp_release_dir
            or os.getenv(
                "TMS_CP_CLEANER_RELEASE", r"F:\cp_data_ansys\packaging\release"
            )
        )
        self.ft_release_dir = Path(
            ft_release_dir
            or os.getenv(
                "TMS_FT_CLEANER_RELEASE",
                r"F:\data_IGBT_multiple\packaging\release",
            )
        )
        self.python_executable = Path(
            python_executable
            or os.getenv("TMS_CLEANER_PYTHON", r"D:\ProgramData\anaconda3\python.exe")
        )
        self._process_runner = process_runner

    def run(
        self,
        *,
        test_stage: str,
        factory: str,
        inputs: Sequence[str | Path],
        output_root: str | Path,
    ) -> ExistingCleanerRunResult:
        stage = test_stage.strip().upper()
        factory_code = factory.strip().lower()
        normalized_inputs = tuple(Path(item).resolve() for item in inputs)
        if not normalized_inputs:
            raise ValueError("at least one input path is required")
        missing = [str(item) for item in normalized_inputs if not item.exists()]
        if missing:
            raise FileNotFoundError(f"cleaner input does not exist: {missing[0]}")

        target = Path(output_root).resolve()
        target.mkdir(parents=True, exist_ok=True)
        cp_scripts = {
            "huahong": _CP_HUAHONG_SCRIPT,
            "hh": _CP_HUAHONG_SCRIPT,
            "华虹": _CP_HUAHONG_SCRIPT,
            "jetech": _CP_JETECH_SCRIPT,
            "jt": _CP_JETECH_SCRIPT,
            "捷特": _CP_JETECH_SCRIPT,
            "lion": _CP_LION_SCRIPT,
            "立昂微": _CP_LION_SCRIPT,
            "guoyu": _CP_GUOYU_SCRIPT,
            "国宇": _CP_GUOYU_SCRIPT,
            "国宇frd": _CP_GUOYU_SCRIPT,
        }
        if stage == "CP" and factory_code in cp_scripts:
            release_dir = self.cp_release_dir
            package = release_dir / "app.pyz"
            script = cp_scripts[factory_code]
        elif stage == "FT" and factory_code in {"riyuexin", "日月新"}:
            release_dir = self.ft_release_dir
            package = release_dir / "ft_data_cleaner.pyz"
            script = _FT_RIYUEXIN_DC_SCRIPT
        elif stage == "FT" and factory_code in {"riyueguang", "ase", "日月光"}:
            release_dir = self.ft_release_dir
            package = release_dir / "ft_data_cleaner.pyz"
            script = _FT_RIYUEGUANG_DC_SCRIPT
        else:
            raise ValueError(f"unsupported existing cleaner adapter: {stage}/{factory}")

        return self._execute_registered_inputs(
            stage=stage,
            factory_code=factory_code,
            normalized_inputs=normalized_inputs,
            target=target,
            runtime=self.python_executable,
            package=package,
            release_dir=release_dir,
            script=script,
            timeout_seconds=3600,
            max_output_bytes=10 * 1024 * 1024 * 1024,
            execution_config_json=json.dumps(
                {"outlier_method": "iqr", "convert_units": True}
            ),
            lot_overrides=None,
        )

    def run_release(
        self,
        *,
        release: CleanerRelease,
        inputs: Sequence[str | Path],
        output_root: str | Path,
        lot_overrides: dict[str, str] | None = None,
        expected_sha256: Sequence[str | None] | None = None,
    ) -> ExistingCleanerRunResult:
        normalized_inputs = tuple(Path(item).resolve() for item in inputs)
        if not normalized_inputs:
            raise ValueError("at least one input path is required")
        missing = [str(item) for item in normalized_inputs if not item.exists()]
        if missing:
            raise FileNotFoundError(f"cleaner input does not exist: {missing[0]}")
        package = Path(release.artifact_uri).resolve()
        runtime = Path(release.runtime_uri).resolve()
        target = Path(output_root).resolve()
        target.mkdir(parents=True, exist_ok=True)
        scripts = {
            "HUAHONG_CP_PYZ": _CP_HUAHONG_SCRIPT,
            "JETECH_CP_PYZ": _CP_JETECH_SCRIPT,
            "LION_CP_PYZ": _CP_LION_SCRIPT,
            "GUOYU_CP_PYZ": _CP_GUOYU_SCRIPT,
            "RIYUEXIN_FT_PYZ": _FT_RIYUEXIN_DC_SCRIPT,
            "RIYUEGUANG_FT_PYZ": _FT_RIYUEGUANG_DC_SCRIPT,
        }
        try:
            script = scripts[release.adapter_code]
        except KeyError as exc:
            raise ValueError(
                f"unsupported released Cleaner adapter: {release.adapter_code}"
            ) from exc
        if package.is_file() and _file_sha256(package) != release.code_checksum.lower():
            raise RuntimeError(
                f"Cleaner package checksum differs from released contract: {package}"
            )
        return self._execute_registered_inputs(
            stage=release.test_stage,
            factory_code=release.factory_code.lower(),
            normalized_inputs=normalized_inputs,
            target=target,
            runtime=runtime,
            package=package,
            release_dir=package.parent,
            script=script,
            timeout_seconds=release.timeout_seconds,
            max_output_bytes=release.max_output_bytes,
            execution_config_json=release.execution_config_json,
            lot_overrides=lot_overrides,
            expected_sha256=expected_sha256,
            require_registered_hash=release.test_stage == "FT",
        )

    def _execute_registered_inputs(
        self,
        *,
        stage: str,
        factory_code: str,
        normalized_inputs: tuple[Path, ...],
        target: Path,
        runtime: Path,
        package: Path,
        release_dir: Path,
        script: str,
        timeout_seconds: int,
        max_output_bytes: int,
        execution_config_json: str | None,
        lot_overrides: dict[str, str] | None,
        expected_sha256: Sequence[str | None] | None = None,
        require_registered_hash: bool = False,
    ) -> ExistingCleanerRunResult:
        validated_hashes = _verify_expected_sha256(
            normalized_inputs,
            expected_sha256,
            required=require_registered_hash,
        )
        if stage != "FT":
            return self._execute(
                stage=stage,
                factory_code=factory_code,
                normalized_inputs=normalized_inputs,
                target=target,
                runtime=runtime,
                package=package,
                release_dir=release_dir,
                script=script,
                timeout_seconds=timeout_seconds,
                max_output_bytes=max_output_bytes,
                execution_config_json=execution_config_json,
                lot_overrides=lot_overrides,
            )

        registered_files = _registered_ft_files(normalized_inputs)
        with tempfile.TemporaryDirectory(prefix="tms_ft_registered_") as temporary:
            isolated = Path(temporary)
            seen_names: set[str] = set()
            for source in registered_files:
                key = source.name.casefold()
                if key in seen_names:
                    raise ValueError(
                        f"registered FT files have a duplicate filename: {source.name}"
                    )
                seen_names.add(key)
                copied = isolated / source.name
                shutil.copy2(source, copied)
                expected = validated_hashes[source]
                if expected is not None and _file_sha256(copied) != expected:
                    raise RuntimeError(
                        f"isolated FT input checksum differs from registration: {source}"
                    )
            normalized_overrides = _normalize_lot_overrides(
                lot_overrides or {}, seen_names
            )
            return self._execute(
                stage=stage,
                factory_code=factory_code,
                normalized_inputs=(isolated,),
                target=target,
                runtime=runtime,
                package=package,
                release_dir=release_dir,
                script=script,
                timeout_seconds=timeout_seconds,
                max_output_bytes=max_output_bytes,
                execution_config_json=execution_config_json,
                lot_overrides=normalized_overrides,
            )

    def _execute(
        self,
        *,
        stage: str,
        factory_code: str,
        normalized_inputs: tuple[Path, ...],
        target: Path,
        runtime: Path,
        package: Path,
        release_dir: Path,
        script: str,
        timeout_seconds: int,
        max_output_bytes: int,
        execution_config_json: str | None,
        lot_overrides: dict[str, str] | None,
    ) -> ExistingCleanerRunResult:
        for required in (runtime, package):
            if not required.is_file():
                raise FileNotFoundError(f"cleaner runtime is unavailable: {required}")

        env = isolated_child_environment(
            {
                "TMS_EXISTING_CLEANER_PACKAGE": str(package),
                "TMS_EXISTING_CLEANER_INPUTS": json.dumps(
                    [str(item) for item in normalized_inputs], ensure_ascii=False
                ),
                "TMS_EXISTING_CLEANER_OUTPUT": str(target),
                "TMS_CLEANER_EXECUTION_CONFIG": execution_config_json or "{}",
                "TMS_LOT_OVERRIDES_JSON": json.dumps(
                    lot_overrides or {}, ensure_ascii=False, sort_keys=True
                ),
                "PYTHONIOENCODING": "utf-8",
                "PYTHONUTF8": "1",
            }
        )
        try:
            completed = self._process_runner(
                [str(runtime), "-c", script],
                cwd=str(release_dir),
                env=env,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                check=False,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"Cleaner timed out after {timeout_seconds}s ({stage}/{factory_code})"
            ) from exc
        input_required = _input_required_from_process(completed)
        if input_required is not None:
            if completed.returncode != INPUT_REQUIRED_EXIT_CODE:
                raise RuntimeError(
                    "Cleaner emitted an input-required marker with invalid exit code "
                    f"{completed.returncode}; expected {INPUT_REQUIRED_EXIT_CODE}"
                )
            raise input_required
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "unknown cleaner error")[
                -4000:
            ]
            raise RuntimeError(
                f"existing cleaner failed ({stage}/{factory_code}): {detail}"
            )

        artifacts = _discover_artifacts(stage, target)
        if not artifacts:
            raise RuntimeError(
                f"existing cleaner returned success without output artifacts: {target}"
            )
        _validate_artifact_contract(stage, artifacts)
        output_bytes = sum(
            path.stat().st_size for path in target.rglob("*") if path.is_file()
        )
        if output_bytes > max_output_bytes:
            raise RuntimeError(
                f"Cleaner output exceeds released limit: {output_bytes}>{max_output_bytes}"
            )
        return ExistingCleanerRunResult(
            test_stage=stage,
            factory=factory_code,
            output_root=str(target),
            artifacts=artifacts,
            stdout_tail=(completed.stdout or "")[-4000:],
        )


def _registered_ft_files(inputs: tuple[Path, ...]) -> tuple[Path, ...]:
    if not all(
        path.is_file()
        and path.suffix.lower() == ".xlsx"
        and not path.name.startswith("~$")
        for path in inputs
    ):
        raise ValueError("FT DC adapter requires exact registered XLSX files")
    files = inputs
    if not files:
        raise ValueError("FT DC adapter received no registered XLSX files")
    return files


def _verify_expected_sha256(
    inputs: tuple[Path, ...],
    expected_sha256: Sequence[str | None] | None,
    *,
    required: bool,
) -> dict[Path, str | None]:
    if expected_sha256 is None:
        if required:
            raise RuntimeError("registered FT input SHA256 values are required")
        return {path: None for path in inputs}
    expected_values = tuple(expected_sha256)
    if len(expected_values) != len(inputs):
        raise ValueError("registered input SHA256 count does not match input files")
    validated: dict[Path, str | None] = {}
    for path, raw_expected in zip(inputs, expected_values, strict=True):
        expected = str(raw_expected or "").strip().lower()
        if len(expected) != 64 or any(
            character not in "0123456789abcdef" for character in expected
        ):
            raise RuntimeError(f"registered input SHA256 is unavailable or invalid: {path}")
        if not path.is_file():
            raise RuntimeError(f"registered input is not a file: {path}")
        if _file_sha256(path) != expected:
            raise RuntimeError(
                f"registered input checksum differs from current bytes: {path}"
            )
        validated[path] = expected
    return validated


def _normalize_lot_overrides(
    overrides: dict[str, str], registered_names: set[str]
) -> dict[str, str]:
    normalized: dict[str, str] = {}
    normalized_by_name: dict[str, str] = {}
    for raw_name, raw_lot in overrides.items():
        name = Path(str(raw_name)).name
        name_key = name.casefold()
        lot = str(raw_lot).strip()
        if not name or name_key not in registered_names:
            raise ValueError(f"Lot override does not match a registered FT file: {raw_name}")
        if not lot or len(lot) > 128:
            raise ValueError(f"Lot override is invalid for registered FT file: {name}")
        previous = normalized_by_name.get(name_key)
        if previous is not None and previous != lot:
            raise ValueError(f"conflicting Lot overrides for registered FT file: {name}")
        normalized_by_name[name_key] = lot
        normalized[name] = lot
    return normalized


def _input_required_from_process(
    completed: subprocess.CompletedProcess[str],
) -> CleanerInputRequired | None:
    marked_lines = [
        line[len(INPUT_REQUIRED_PREFIX) :]
        for output in (completed.stderr or "", completed.stdout or "")
        for line in output.splitlines()
        if line.startswith(INPUT_REQUIRED_PREFIX)
    ]
    if not marked_lines:
        return None
    if len(marked_lines) != 1:
        raise RuntimeError("Cleaner emitted multiple input-required markers")
    try:
        payload = json.loads(marked_lines[0])
    except json.JSONDecodeError as exc:
        raise RuntimeError("Cleaner input-required marker is invalid JSON") from exc
    if not isinstance(payload, dict) or set(payload) != {
        "field_code",
        "files",
        "message",
    }:
        raise RuntimeError("Cleaner input-required marker has an invalid schema")
    field_code = str(payload["field_code"] or "").strip().upper()
    message = str(payload["message"] or "").strip()
    raw_files = payload["files"]
    if (
        field_code != "LOT_ID"
        or not message
        or not isinstance(raw_files, list)
        or not raw_files
    ):
        raise RuntimeError("Cleaner input-required marker has invalid values")
    if any(not isinstance(item, dict) or set(item) != {"original_file_name"} for item in raw_files):
        raise RuntimeError("Cleaner input-required files have an invalid schema")
    files = tuple(str(item["original_file_name"]).strip() for item in raw_files)
    if any(not item or Path(item).name != item for item in files):
        raise RuntimeError("Cleaner input-required files must be plain original filenames")
    if len({item.casefold() for item in files}) != len(files):
        raise RuntimeError("Cleaner input-required files are duplicated")
    return CleanerInputRequired(field_code=field_code, files=files, message=message)


def _discover_artifacts(stage: str, output_root: Path) -> tuple[CleanerArtifact, ...]:
    roles = {
        "CP": (
            ("cleaned", "*_cleaned_*.csv"),
            ("yield", "*_yield_*.csv"),
            ("spec", "*_spec_*.csv"),
        ),
        "FT": (
            ("cleaned", "*.xlsx"),
            ("scatter_data", "ft_scatter_data.csv.gz"),
            ("scatter_spec", "ft_scatter_spec.csv"),
            ("scatter_manifest", "ft_scatter_manifest.json"),
        ),
    }[stage]
    found: list[CleanerArtifact] = []
    for role, pattern in roles:
        for path in sorted(
            output_root.rglob(pattern), key=lambda item: str(item).casefold()
        ):
            found.append(
                CleanerArtifact(
                    role,
                    str(path.resolve()),
                    path.stat().st_size,
                    _file_sha256(path),
                )
            )
    return tuple(found)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_artifact_contract(
    stage: str, artifacts: tuple[CleanerArtifact, ...]
) -> None:
    required = {
        "CP": {"cleaned", "yield", "spec"},
        "FT": {"cleaned", "scatter_data", "scatter_spec", "scatter_manifest"},
    }[stage]
    actual = {item.role for item in artifacts}
    missing = sorted(required - actual)
    if missing:
        raise RuntimeError(
            f"Cleaner output contract is incomplete for {stage}: missing {missing}"
        )


_CP_HUAHONG_SCRIPT = """
import json, os, sys
from pathlib import Path
sys.path.insert(0, os.environ['TMS_EXISTING_CLEANER_PACKAGE'])
from cp_data_processor.processing.zip_input import prepare_dcp_input
from clean_dcp_data import process_directory
inputs = [Path(item) for item in json.loads(os.environ['TMS_EXISTING_CLEANER_INPUTS'])]
output = os.environ['TMS_EXISTING_CLEANER_OUTPUT']
config = json.loads(os.environ.get('TMS_CLEANER_EXECUTION_CONFIG', '{}'))
def run(prepared_directory):
    result = process_directory(
        str(prepared_directory),
        output_dir=output,
        outlier_method=config.get('outlier_method', 'iqr'),
        convert_units=bool(config.get('convert_units', True)),
    )
    if not result:
        raise SystemExit('华虹 CP cleaner returned no result')
if len(inputs) == 1 and inputs[0].is_dir():
    run(inputs[0])
elif all(item.is_file() and item.suffix.lower() == '.txt' for item in inputs):
    raise SystemExit(
        'raw TXT files require a source directory or archive preserving Product/Lot identity'
    )
else:
    with prepare_dcp_input(inputs, progress=print) as prepared:
        run(prepared.directory)
"""


_CP_JETECH_SCRIPT = """
import json, os, sys
from pathlib import Path
sys.path.insert(0, os.environ['TMS_EXISTING_CLEANER_PACKAGE'])
from cp_data_processor.processing.archive_input import prepare_archive_input
from jt_data_processor.jt_main_processor import process_jt_files
inputs = [Path(item) for item in json.loads(os.environ['TMS_EXISTING_CLEANER_INPUTS'])]
output = os.environ['TMS_EXISTING_CLEANER_OUTPUT']
if all(path.is_file() and path.suffix.lower() in ('.xls', '.xlsx') for path in inputs):
    result = process_jt_files([str(path) for path in inputs], output_dir=output, pass_bin=1)
else:
    with prepare_archive_input(
        inputs,
        allowed_suffixes=('.xls', '.xlsx'),
        source_label='JT Excel',
        temporary_prefix='tms_cp_jt_',
    ) as prepared:
        result = process_jt_files(
            [str(path) for path in prepared.data_files], output_dir=output, pass_bin=1
        )
if not result:
    raise SystemExit('Jetech CP cleaner returned no result')
"""


_CP_LION_SCRIPT = """
import json, os, sys
from pathlib import Path
sys.path.insert(0, os.environ['TMS_EXISTING_CLEANER_PACKAGE'])
from cp_data_processor.processing.archive_input import prepare_archive_input
from lion_batch_processor import (
    create_batch_lot,
    discover_batch_files,
    generate_lion_run_csvs,
    process_lion_batch_files,
)
inputs = [Path(item) for item in json.loads(os.environ['TMS_EXISTING_CLEANER_INPUTS'])]
output = os.environ['TMS_EXISTING_CLEANER_OUTPUT']
def run(directory):
    batches = discover_batch_files(directory)
    if not batches:
        raise SystemExit('Lion CP cleaner found no batch')
    lots = [
        create_batch_lot(process_lion_batch_files(paths))
        for paths in batches.values()
    ]
    if not generate_lion_run_csvs(lots, output):
        raise SystemExit('Lion CP cleaner returned no result')
if all(path.is_file() and path.suffix.lower() in ('.xls', '.xlsx') for path in inputs):
    individual = process_lion_batch_files([str(path) for path in inputs])
    grouped = {}
    for source_path, lot in individual.items():
        grouped.setdefault(lot.lot_id, {})[source_path] = lot
    lots = [create_batch_lot(group) for group in grouped.values()]
    if not lots or not generate_lion_run_csvs(lots, output):
        raise SystemExit('Lion CP cleaner returned no result')
else:
    with prepare_archive_input(
        inputs,
        allowed_suffixes=('.xls', '.xlsx'),
        source_label='Lion Excel',
        temporary_prefix='tms_cp_lion_',
    ) as prepared:
        run(prepared.directory)
"""


_CP_GUOYU_SCRIPT = """
import json, os, shutil, sys, tempfile
from pathlib import Path
sys.path.insert(0, os.environ['TMS_EXISTING_CLEANER_PACKAGE'])
from cp_data_processor.processing.archive_input import prepare_archive_input
from guoyu_batch_processor import process_guoyu_directory
inputs = [Path(item) for item in json.loads(os.environ['TMS_EXISTING_CLEANER_INPUTS'])]
output = os.environ['TMS_EXISTING_CLEANER_OUTPUT']
if all(path.is_file() and path.suffix.lower() in ('.xls', '.xlsx') for path in inputs):
    with tempfile.TemporaryDirectory(prefix='tms_cp_guoyu_files_') as temporary:
        directory = Path(temporary) / 'SOURCE' / 'EDS'
        directory.mkdir(parents=True)
        for path in inputs:
            shutil.copy2(path, directory / path.name)
        result = process_guoyu_directory(str(directory.parent), output)
else:
    with prepare_archive_input(
        inputs,
        allowed_suffixes=('.xls', '.xlsx'),
        source_label='Guoyu FRD Excel',
        preserve_member_paths=True,
        temporary_prefix='tms_cp_guoyu_',
    ) as prepared:
        result = process_guoyu_directory(str(prepared.directory), output)
if not result:
    raise SystemExit('Guoyu FRD CP cleaner returned no result')
"""


_FT_RIYUEXIN_DC_SCRIPT = """
import json, os, sys
sys.path.insert(0, os.environ['TMS_EXISTING_CLEANER_PACKAGE'])
from factories.tms_adapters.riyuexin_dc import RiyuexinTmsDCCleaner
from factories.tms_adapters.identity import LotOverrideRequired
inputs = json.loads(os.environ['TMS_EXISTING_CLEANER_INPUTS'])
lot_overrides = json.loads(os.environ.get('TMS_LOT_OVERRIDES_JSON', '{}'))
try:
    cleaner = RiyuexinTmsDCCleaner(
        input_dir=inputs[0],
        output_dir=os.environ['TMS_EXISTING_CLEANER_OUTPUT'],
        lot_overrides=lot_overrides,
    )
    if not cleaner.process_all_dc_files():
        raise SystemExit('日月新 FT DC cleaner returned false')
except LotOverrideRequired as exc:
    payload = {
        'field_code': 'LOT_ID',
        'files': [{'original_file_name': name} for name in exc.file_names],
        'message': str(exc),
    }
    sys.stderr.write('TMS_INPUT_REQUIRED_JSON=' + json.dumps(
        payload, ensure_ascii=False, separators=(',', ':')
    ) + '\\n')
    raise SystemExit(42)
"""


_FT_RIYUEGUANG_DC_SCRIPT = """
import json, os, sys
sys.path.insert(0, os.environ['TMS_EXISTING_CLEANER_PACKAGE'])
from factories.tms_adapters.riyueguang_dc import RiyueguangTmsDCCleaner
from factories.tms_adapters.identity import LotOverrideRequired
inputs = json.loads(os.environ['TMS_EXISTING_CLEANER_INPUTS'])
lot_overrides = json.loads(os.environ.get('TMS_LOT_OVERRIDES_JSON', '{}'))
try:
    cleaner = RiyueguangTmsDCCleaner(
        input_dir=inputs[0],
        output_dir=os.environ['TMS_EXISTING_CLEANER_OUTPUT'],
        lot_overrides=lot_overrides,
    )
    if not cleaner.process_all_dc_files():
        raise SystemExit('日月光 FT DC cleaner returned false')
except LotOverrideRequired as exc:
    payload = {
        'field_code': 'LOT_ID',
        'files': [{'original_file_name': name} for name in exc.file_names],
        'message': str(exc),
    }
    sys.stderr.write('TMS_INPUT_REQUIRED_JSON=' + json.dumps(
        payload, ensure_ascii=False, separators=(',', ':')
    ) + '\\n')
    raise SystemExit(42)
"""
