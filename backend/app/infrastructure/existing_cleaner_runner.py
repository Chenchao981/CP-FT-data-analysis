from __future__ import annotations

import json
import os
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Sequence


ProcessRunner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True, slots=True)
class CleanerArtifact:
    role: str
    path: str
    size_bytes: int


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
            or os.getenv("TMS_CP_CLEANER_RELEASE", r"F:\cp_data_ansys\packaging\release")
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
        if stage == "CP" and factory_code in {"huahong", "hh", "华虹"}:
            release_dir = self.cp_release_dir
            package = release_dir / "app.pyz"
            script = _CP_HUAHONG_SCRIPT
        elif stage == "FT" and factory_code in {"riyuexin", "ase", "日月新"}:
            if len(normalized_inputs) != 1 or not normalized_inputs[0].is_dir():
                raise ValueError("日月新 FT DC adapter requires one input directory")
            release_dir = self.ft_release_dir
            package = release_dir / "ft_data_cleaner.pyz"
            script = _FT_RIYUEXIN_DC_SCRIPT
        else:
            raise ValueError(f"unsupported existing cleaner adapter: {stage}/{factory}")

        for required in (self.python_executable, package):
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
            }
        )
        completed = self._process_runner(
            [str(self.python_executable), "-c", script],
            cwd=str(release_dir),
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "unknown cleaner error")[-4000:]
            raise RuntimeError(f"existing cleaner failed ({stage}/{factory_code}): {detail}")

        artifacts = _discover_artifacts(stage, target)
        if not artifacts:
            raise RuntimeError(f"existing cleaner returned success without output artifacts: {target}")
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
        for path in sorted(output_root.rglob(pattern), key=lambda item: str(item).casefold()):
            found.append(CleanerArtifact(role, str(path.resolve()), path.stat().st_size))
    return tuple(found)


_CP_HUAHONG_SCRIPT = """
import json, os, sys
from pathlib import Path
sys.path.insert(0, os.environ['TMS_EXISTING_CLEANER_PACKAGE'])
from cp_data_processor.processing.zip_input import prepare_dcp_input
from clean_dcp_data import process_directory
inputs = [Path(item) for item in json.loads(os.environ['TMS_EXISTING_CLEANER_INPUTS'])]
output = os.environ['TMS_EXISTING_CLEANER_OUTPUT']
with prepare_dcp_input(inputs, progress=print) as prepared:
    result = process_directory(str(prepared.directory), output_dir=output, outlier_method='iqr', convert_units=True)
    if not result:
        raise SystemExit('华虹 CP cleaner returned no result')
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
