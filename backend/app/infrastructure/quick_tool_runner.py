from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import time
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from app.domain.cleaner_registry import CleanerRelease
from app.domain.quick_analysis import QuickAnalysisArtifact
from app.infrastructure.child_process_environment import isolated_child_environment
from app.infrastructure.existing_cleaner_runner import ExistingCleanerRunner
from app.infrastructure.quick_pat_runner import (
    QuickPatRunner,
    QuickPatRunResult,
    _extract_ft_archive,
)

ProcessRunner = Callable[..., subprocess.CompletedProcess[str]]


class QuickToolRunner:
    """Run result-only personal tools through the approved CP/FT packages."""

    def __init__(
        self,
        process_runner: ProcessRunner = subprocess.run,
        cleaner_runner: ExistingCleanerRunner | None = None,
        pat_runner: QuickPatRunner | None = None,
    ) -> None:
        self._process_runner = process_runner
        self._cleaner_runner = cleaner_runner or ExistingCleanerRunner(
            process_runner=process_runner
        )
        self._pat_runner = pat_runner or QuickPatRunner(
            process_runner=process_runner,
            cleaner_runner=self._cleaner_runner,
        )

    def run_release(
        self,
        *,
        analysis_type: str,
        release: CleanerRelease,
        input_directory: str | Path,
        output_root: str | Path,
        source_manifest_json: str,
        source_manifest_sha256: str,
    ) -> QuickPatRunResult:
        if analysis_type == "QUICK_PAT":
            return self._pat_runner.run_release(
                release=release,
                input_directory=input_directory,
                output_root=output_root,
                source_manifest_json=source_manifest_json,
                source_manifest_sha256=source_manifest_sha256,
            )
        if analysis_type not in {"QUICK_CLEAN", "QUICK_CHART", "QUICK_SYL_SBL"}:
            raise ValueError(f"unsupported personal analysis type: {analysis_type}")

        source = Path(input_directory).resolve()
        target = Path(output_root).resolve()
        package = Path(release.artifact_uri).resolve()
        runtime = Path(release.runtime_uri).resolve()
        if not source.exists() or not (source.is_dir() or source.is_file()):
            raise FileNotFoundError(f"personal tool input is unavailable: {source}")
        for required in (package, runtime):
            if not required.is_file():
                raise FileNotFoundError(f"personal tool runtime is unavailable: {required}")
        if _sha256_file(package) != release.code_checksum.lower():
            raise RuntimeError("personal tool package checksum differs from released contract")
        if hashlib.sha256(source_manifest_json.encode("utf-8")).hexdigest() != source_manifest_sha256.lower():
            raise RuntimeError("source Manifest JSON does not match its SHA-256")

        target.mkdir(parents=True, exist_ok=True)
        work = target / "intermediate"
        results = target / "results"
        work.mkdir()
        results.mkdir()
        started = time.perf_counter()
        engine_source = source
        staged_source: Path | None = None
        try:
            if (
                release.test_stage == "FT"
                and analysis_type != "QUICK_SYL_SBL"
                and source.is_file()
            ):
                staged_source = work / "archive-source"
                staged_source.mkdir()
                if source.suffix.lower() in {".zip", ".7z"}:
                    _extract_ft_archive(
                        source,
                        staged_source,
                        allowed_suffixes=_factory_raw_suffixes(release.factory_code),
                        max_total_bytes=max(
                            release.max_output_bytes * 4, 1024 * 1024 * 1024
                        ),
                    )
                else:
                    staged_file = staged_source / source.name
                    try:
                        staged_file.hardlink_to(source)
                    except OSError:
                        shutil.copy2(source, staged_file)
                engine_source = staged_source

            if release.test_stage == "CP":
                generated, record_count = self._run_cp(
                    analysis_type=analysis_type,
                    release=release,
                    source=engine_source,
                    work=work,
                    results=results,
                )
            else:
                generated, record_count = self._run_ft(
                    analysis_type=analysis_type,
                    release=release,
                    source=engine_source,
                    work=work,
                    results=results,
                )
        finally:
            if staged_source is not None and staged_source.exists():
                shutil.rmtree(staged_source)

        if not generated:
            raise RuntimeError("personal tool returned success without a result")
        result_path = self._primary_result(
            analysis_type=analysis_type,
            results=results,
            target=target,
            factory_code=release.factory_code,
        )
        shutil.rmtree(work, ignore_errors=True)
        if results.exists():
            shutil.rmtree(results)

        manifest = json.loads(source_manifest_json)
        elapsed = time.perf_counter() - started
        summary: dict[str, Any] = {
            "schema_version": 1,
            "analysis_type": analysis_type,
            "test_stage": release.test_stage,
            "factory_code": release.factory_code,
            "input_contract": release.input_contract_version,
            "output_contract": _output_contract(analysis_type),
            "source_file_count": manifest.get("file_count"),
            "source_total_bytes": manifest.get("total_bytes"),
            "source_manifest_sha256": source_manifest_sha256,
            "generated_file_count": len(generated),
            "generated_files": generated,
            "record_count": record_count,
            "elapsed_seconds": round(elapsed, 3),
            "raw_source_retained": False,
        }
        manifest_path = target / "source_manifest.json"
        manifest_path.write_text(source_manifest_json, encoding="utf-8")
        summary_path = target / "analysis_summary.json"
        summary_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        artifacts = (
            _artifact("result_package", result_path),
            _artifact("analysis_summary", summary_path),
            _artifact("source_manifest", manifest_path),
        )
        output_bytes = sum(path.stat().st_size for path in target.rglob("*") if path.is_file())
        if output_bytes > release.max_output_bytes:
            raise RuntimeError(
                f"personal tool output exceeds released limit: {output_bytes}>{release.max_output_bytes}"
            )
        return QuickPatRunResult(
            parameter_count=max(1, len(generated)),
            record_count=record_count,
            summary=summary,
            artifacts=artifacts,
            stdout_tail="",
        )

    def _run_cp(
        self,
        *,
        analysis_type: str,
        release: CleanerRelease,
        source: Path,
        work: Path,
        results: Path,
    ) -> tuple[list[str], int | None]:
        if analysis_type == "QUICK_SYL_SBL":
            raise ValueError("CP personal tools do not provide SBL/SYL")
        cleaned_root = results if analysis_type == "QUICK_CLEAN" else work / "cp-cleaned"
        self._cleaner_runner.run_release(
            release=release,
            inputs=(source,),
            output_root=cleaned_root,
        )
        if analysis_type == "QUICK_CLEAN":
            files = _relative_files(results)
            return files, _count_cp_rows(results)

        env = isolated_child_environment(
            {
                "TMS_EXISTING_CLEANER_PACKAGE": str(Path(release.artifact_uri).resolve()),
                "TMS_CP_CHART_INPUT": str(cleaned_root),
                "TMS_CP_CHART_OUTPUT": str(results),
                "PYTHONUTF8": "1",
                "PYTHONIOENCODING": "utf-8",
            }
        )
        completed = self._process_runner(
            [str(Path(release.runtime_uri).resolve()), "-c", _CP_CHART_SCRIPT],
            cwd=str(Path(release.artifact_uri).resolve().parent),
            env=env,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
            timeout=release.timeout_seconds,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "unknown CP chart error")[-4000:]
            raise RuntimeError(f"released CP chart tool failed: {detail}")
        files = _relative_files(results)
        if not files or any(not name.lower().endswith(".html") for name in files):
            raise RuntimeError("CP chart output contract requires offline HTML files")
        return files, _count_cp_rows(cleaned_root)

    def _run_ft(
        self,
        *,
        analysis_type: str,
        release: CleanerRelease,
        source: Path,
        work: Path,
        results: Path,
    ) -> tuple[list[str], int | None]:
        if analysis_type == "QUICK_SYL_SBL" and (
            not source.is_file() or source.suffix.lower() not in {".xls", ".xlsx"}
        ):
            raise ValueError("SBL/SYL requires one explicitly selected Excel workbook")
        env = isolated_child_environment(
            {
                "TMS_FT_TOOL_PACKAGE": str(Path(release.artifact_uri).resolve()),
                "TMS_FT_TOOL_OPERATION": analysis_type,
                "TMS_FT_TOOL_FACTORY": release.factory_code,
                "TMS_FT_TOOL_INPUT": str(source),
                "TMS_FT_TOOL_WORK": str(work),
                "TMS_FT_TOOL_OUTPUT": str(results),
                "PYTHONUTF8": "1",
                "PYTHONIOENCODING": "utf-8",
            }
        )
        completed = self._process_runner(
            [str(Path(release.runtime_uri).resolve()), "-c", _FT_PERSONAL_TOOL_SCRIPT],
            cwd=str(Path(release.artifact_uri).resolve().parent),
            env=env,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
            timeout=release.timeout_seconds,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "unknown FT personal tool error")[-4000:]
            raise RuntimeError(f"released FT personal tool failed: {detail}")
        files = _relative_files(results)
        if analysis_type == "QUICK_CHART" and (
            not files or any(not name.lower().endswith(".html") for name in files)
        ):
            raise RuntimeError("FT scatter output contract requires offline HTML files")
        if analysis_type == "QUICK_SYL_SBL" and (
            len(files) != 1 or not files[0].lower().endswith(".xlsx")
        ):
            raise RuntimeError("SBL/SYL output contract requires one Excel workbook")
        return files, _parse_record_count(completed.stdout or "")

    @staticmethod
    def _primary_result(
        *, analysis_type: str, results: Path, target: Path, factory_code: str
    ) -> Path:
        if analysis_type == "QUICK_SYL_SBL":
            files = [path for path in results.rglob("*.xlsx") if path.is_file()]
            if len(files) != 1:
                raise RuntimeError("SBL/SYL output must contain exactly one workbook")
            destination = target / files[0].name
            shutil.copy2(files[0], destination)
            return destination
        label = {
            "QUICK_CLEAN": "清洗结果",
            "QUICK_CHART": "图表分析",
        }[analysis_type]
        destination = target / f"{factory_code}_{label}.zip"
        _zip_directory(results, destination)
        return destination


def _factory_raw_suffixes(factory_code: str) -> tuple[str, ...]:
    return {
        "RIYUEXIN": (".xlsx",),
        "RIYUEGUANG": (".xlsx",),
        "JIEQUN": (".csv",),
        "DIANJI": (".xls", ".xlsx", ".csv"),
        "JIJIA": (".csv",),
    }[factory_code]


def _output_contract(analysis_type: str) -> str:
    return {
        "QUICK_CLEAN": "PERSONAL_CLEAN_RESULT_ZIP_V1",
        "QUICK_CHART": "PERSONAL_OFFLINE_HTML_ZIP_V1",
        "QUICK_SYL_SBL": "FT_SYL_SBL_XLSX_V1",
    }[analysis_type]


def _relative_files(root: Path) -> list[str]:
    return sorted(
        (path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()),
        key=str.casefold,
    )


def _count_cp_rows(root: Path) -> int | None:
    total = 0
    found = False
    for path in root.rglob("*_cleaned_*.csv"):
        found = True
        with path.open("r", encoding="utf-8-sig", errors="replace") as handle:
            total += max(0, sum(1 for _line in handle) - 1)
    return total if found else None


def _parse_record_count(stdout: str) -> int | None:
    for line in reversed(stdout.splitlines()):
        if line.startswith("TMS_RECORD_COUNT="):
            try:
                return int(line.split("=", 1)[1])
            except ValueError:
                return None
    return None


def _zip_directory(source: Path, destination: Path) -> None:
    files = [path for path in source.rglob("*") if path.is_file()]
    if not files:
        raise RuntimeError("result directory is empty")
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(files, key=lambda item: str(item).casefold()):
            archive.write(path, path.relative_to(source).as_posix())


def _artifact(role: str, path: Path) -> QuickAnalysisArtifact:
    resolved = path.resolve()
    return QuickAnalysisArtifact(role, str(resolved), resolved.stat().st_size, _sha256_file(resolved))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


_CP_CHART_SCRIPT = r"""
import os, sys
from pathlib import Path
sys.path.insert(0, os.environ['TMS_EXISTING_CLEANER_PACKAGE'])
from frontend.charts.yield_chart import YieldChart
from frontend.charts.boxplot_chart import BoxplotChart
from frontend.charts.summary_chart.summary_chart import SummaryChart
source = Path(os.environ['TMS_CP_CHART_INPUT'])
output = Path(os.environ['TMS_CP_CHART_OUTPUT'])
output.mkdir(parents=True, exist_ok=True)
generated = []
run_dirs = sorted({path.parent for path in source.rglob('*_cleaned_*.csv')}, key=str)
for index, run_dir in enumerate(run_dirs, 1):
    run_output = output / f'{index:03d}_{run_dir.name}'
    run_output.mkdir(parents=True, exist_ok=True)
    yield_chart = YieldChart(data_dir=str(run_dir))
    if yield_chart.load_data():
        generated.extend(yield_chart.save_all_charts(output_dir=str(run_output)))
    boxplot = BoxplotChart(data_dir=str(run_dir))
    if boxplot.load_data():
        generated.extend(boxplot.save_all_charts(output_dir=str(run_output)))
    summary = SummaryChart(data_dir=str(run_dir))
    if summary.load_data():
        result = summary.save_summary_chart(output_dir=str(run_output))
        if result:
            generated.append(result)
if not generated:
    raise SystemExit('CP chart generator produced no offline HTML')
print(f'TMS_CHART_COUNT={len(generated)}')
"""


_FT_PERSONAL_TOOL_SCRIPT = r"""
import html, json, os, sys
from pathlib import Path
sys.path.insert(0, os.environ['TMS_FT_TOOL_PACKAGE'])
operation = os.environ['TMS_FT_TOOL_OPERATION']
factory = os.environ['TMS_FT_TOOL_FACTORY']
source = Path(os.environ['TMS_FT_TOOL_INPUT'])
work = Path(os.environ['TMS_FT_TOOL_WORK'])
output = Path(os.environ['TMS_FT_TOOL_OUTPUT'])
output.mkdir(parents=True, exist_ok=True)

def run_clean(target):
    target.mkdir(parents=True, exist_ok=True)
    manifest = None
    if factory == 'JIEQUN':
        name = source.name.upper()
        if name == 'DVDS':
            from factories.jiequn.dvds_cleaner import JiequnDVDSCleaner
            cleaner = JiequnDVDSCleaner(source, target)
            ok = cleaner.process_all()
        elif name == 'RG':
            from factories.jiequn.rg_cleaner import JiequnRGCleaner
            cleaner = JiequnRGCleaner(source, target)
            ok = cleaner.process_all()
        else:
            from factories.jiequn.dc_auto import run_auto_dc
            result = run_auto_dc(source, target)
            ok = bool(result)
            manifest = getattr(result, 'scatter_manifest', None)
    elif factory == 'RIYUEXIN':
        typed = {p.name.upper(): p for p in source.iterdir() if p.is_dir() and p.name.upper() in {'DC','DVDS','RG'}} if source.is_dir() else {}
        selected = typed or {source.name.upper(): source}
        ok = True
        for kind, folder in selected.items():
            if kind == 'DVDS':
                from factories.riyuexin.dvds_cleaner import DVDSCleaner
                cleaner = DVDSCleaner(base_dir=str(folder.parent.parent))
                cleaner.dvds_dir = str(folder); cleaner.output_dir = str(target)
                ok = bool(cleaner.process_all()) and ok
            elif kind == 'RG':
                from factories.riyuexin.rg_cleaner import RGCleaner
                ok = bool(RGCleaner(input_dir=str(folder), output_dir=str(target)).run()) and ok
            else:
                from factories.riyuexin.dc_cleaner import DCDataCleaner
                cleaner = DCDataCleaner(input_dir=str(folder), output_dir=str(target))
                ok = bool(cleaner.process_all_dc_files()) and ok
                manifest = cleaner.last_scatter_manifest or manifest
    elif factory == 'RIYUEGUANG':
        from factories.tms_adapters.riyueguang_dc import RiyueguangTmsDCCleaner
        cleaner = RiyueguangTmsDCCleaner(input_dir=str(source), output_dir=str(target))
        ok = bool(cleaner.process_all_dc_files())
        manifest = cleaner.last_scatter_manifest
    elif factory == 'DIANJI':
        from factories.dianji.dc_cleaner import DianjiDCCleaner
        cleaner = DianjiDCCleaner(str(source), str(target))
        ok = bool(cleaner.process_all())
        manifest = cleaner.last_scatter_manifest
    elif factory == 'JIJIA':
        from factories.jijia.dc_cleaner import JijiaFTCleaner
        cleaner = JijiaFTCleaner(str(source), str(target))
        ok = bool(cleaner.process_all())
    else:
        raise SystemExit(f'unsupported FT factory: {factory}')
    if not ok:
        raise SystemExit(f'{factory} cleaner returned false')
    if manifest is None:
        manifests = sorted(target.rglob('ft_scatter_manifest.json'))
        manifest = manifests[0] if len(manifests) == 1 else None
    return Path(manifest) if manifest else None

if operation == 'QUICK_SYL_SBL':
    if factory == 'JIEQUN':
        from factories.jiequn.yield_report import generate_report
    elif factory == 'RIYUEXIN':
        from factories.riyuexin.yield_report import generate_report
    elif factory == 'DIANJI':
        from factories.dianji.yield_report import generate_report
    else:
        raise SystemExit(f'{factory} does not provide SBL/SYL')
    result = generate_report(source_file=str(source), output_dir=str(output))
    if not result:
        raise SystemExit('SBL/SYL generator returned no result')
elif operation == 'QUICK_CLEAN':
    run_clean(output)
elif operation == 'QUICK_CHART':
    if factory not in {'JIEQUN','RIYUEXIN','DIANJI'}:
        raise SystemExit(f'{factory} does not provide FT scatter charts')
    cleaned = work / 'ft-cleaned'
    manifest_path = run_clean(cleaned)
    if manifest_path is None or not manifest_path.is_file():
        raise SystemExit('FT cleaner did not produce a scatter manifest')
    from frontend.ft_scatter import load_scatter_bundle, build_parameter_figure
    import plotly.io as pio
    manifest, data, specs = load_scatter_bundle(manifest_path)
    parts = []
    for index, parameter in enumerate(manifest.get('parameters') or []):
        figure, _stats = build_parameter_figure(data, specs, parameter)
        parts.append(pio.to_html(figure, full_html=False, include_plotlyjs=True if index == 0 else False))
    if not parts:
        raise SystemExit('FT scatter bundle contains no parameters')
    title = f"{factory} FT 散点图"
    page = '<!doctype html><html><head><meta charset="utf-8"><title>'+html.escape(title)+'</title></head><body><h1>'+html.escape(title)+'</h1>'+''.join(parts)+'</body></html>'
    (output / f'{factory}_FT_散点图.html').write_text(page, encoding='utf-8')
    print(f"TMS_RECORD_COUNT={int(manifest.get('row_count') or len(data))}")
else:
    raise SystemExit(f'unsupported operation: {operation}')
"""
