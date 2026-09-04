from __future__ import annotations

import json
import shutil
from pathlib import Path

from app.core.errors import DomainError


class QuickResultExportStore:
    """Persist the requested local output directory until the Worker finishes."""

    def __init__(self, work_root: str | Path) -> None:
        self._request_root = Path(work_root).resolve() / "_output_requests"

    def register(self, analysis_session_id: int, output_directory: str | Path) -> Path:
        target = Path(str(output_directory).strip().strip('"')).expanduser().absolute()
        try:
            target.mkdir(parents=True, exist_ok=True)
            target = target.resolve()
        except OSError as exc:
            raise DomainError(
                "QUICK_OUTPUT_UNAVAILABLE",
                "所选输出文件夹无法创建或写入",
                422,
            ) from exc
        if not target.is_dir():
            raise DomainError(
                "QUICK_OUTPUT_INVALID",
                "所选输出路径必须是文件夹",
                422,
            )

        self._request_root.mkdir(parents=True, exist_ok=True)
        request_path = self._request_path(analysis_session_id)
        temporary = request_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(
                {"output_directory": str(target)},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        temporary.replace(request_path)
        return target

    def export_report(
        self, analysis_session_id: int, report_path: str | Path
    ) -> Path | None:
        request_path = self._request_path(analysis_session_id)
        if not request_path.is_file():
            return None
        try:
            payload = json.loads(request_path.read_text(encoding="utf-8"))
            output_directory = Path(payload["output_directory"]).resolve()
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError("personal tool output request is invalid") from exc
        if not output_directory.is_dir():
            raise RuntimeError("personal tool output directory is no longer available")

        source = Path(report_path).resolve()
        if not source.is_file() or source.suffix.lower() not in {".xlsx", ".zip", ".html"}:
            raise RuntimeError("personal tool result is unavailable for local export")
        target = self._unique_target(output_directory, source.name)
        shutil.copy2(source, target)
        return target.resolve()

    def discard(self, analysis_session_id: int) -> None:
        request_path = self._request_path(analysis_session_id)
        request_path.unlink(missing_ok=True)

    def _request_path(self, analysis_session_id: int) -> Path:
        if analysis_session_id < 1:
            raise ValueError("analysis_session_id must be positive")
        return self._request_root / f"{analysis_session_id}.json"

    @staticmethod
    def _unique_target(output_directory: Path, file_name: str) -> Path:
        candidate = output_directory / Path(file_name).name
        if not candidate.exists():
            return candidate
        stem = candidate.stem
        suffix = candidate.suffix
        for serial in range(1, 10_000):
            numbered = output_directory / f"{stem}_{serial:03d}{suffix}"
            if not numbered.exists():
                return numbered
        raise RuntimeError("personal tool output directory has too many duplicate results")
