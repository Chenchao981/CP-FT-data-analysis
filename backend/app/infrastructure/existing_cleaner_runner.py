from __future__ import annotations

import hashlib
import json
import os
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

from app.domain.cleaner_registry import CleanerRelease

ProcessRunner = Callable[..., subprocess.CompletedProcess[str]]


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
        elif stage == "FT" and factory_code in {"riyuexin", "ase", "日月新"}:
            if len(normalized_inputs) != 1 or not normalized_inputs[0].is_dir():
                raise ValueError("日月新 FT DC adapter requires one input directory")
            release_dir = self.ft_release_dir
            package = release_dir / "ft_data_cleaner.pyz"
            script = _FT_RIYUEXIN_DC_SCRIPT
        else:
            raise ValueError(f"unsupported existing cleaner adapter: {stage}/{factory}")

        return self._execute(
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
        )

    def run_release(
        self,
        *,
        release: CleanerRelease,
        inputs: Sequence[str | Path],
        output_root: str | Path,
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
        }
        try:
            script = scripts[release.adapter_code]
        except KeyError as exc:
            raise ValueError(
                f"unsupported released Cleaner adapter: {release.adapter_code}"
            ) from exc
        if release.adapter_code == "RIYUEXIN_FT_PYZ" and (
            len(normalized_inputs) != 1 or not normalized_inputs[0].is_dir()
        ):
            raise ValueError("日月新 FT DC adapter requires one input directory")
        if package.is_file() and _file_sha256(package) != release.code_checksum.lower():
            raise RuntimeError(
                f"Cleaner package checksum differs from released contract: {package}"
            )
        return self._execute(
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
    ) -> ExistingCleanerRunResult:
        for required in (runtime, package):
            if not required.is_file():
                raise FileNotFoundError(f"cleaner runtime is unavailable: {required}")

        env = os.environ.copy()
        env.update(
            {
                "TMS_EXISTING_CLEANER_PACKAGE": str(package),
                "TMS_EXISTING_CLEANER_INPUTS": json.dumps(
                    [str(item) for item in normalized_inputs], ensure_ascii=False
                ),
                "TMS_EXISTING_CLEANER_OUTPUT": str(target),
                "TMS_CLEANER_EXECUTION_CONFIG": execution_config_json or "{}",
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
from factories.riyuexin.dc_cleaner import DCDataCleaner
inputs = json.loads(os.environ['TMS_EXISTING_CLEANER_INPUTS'])
cleaner = DCDataCleaner(input_dir=inputs[0], output_dir=os.environ['TMS_EXISTING_CLEANER_OUTPUT'])
if not cleaner.process_all_dc_files():
    raise SystemExit('日月新 FT DC cleaner returned false')
"""
