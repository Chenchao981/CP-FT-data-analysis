from __future__ import annotations

import json
import logging
import os
import re
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from .config import AgentConfig
from .errors import AgentError, ManifestError
from .manifest import build_local_manifest, manifest_json
from .models import (
    CP_GATE_TOOL_CODE,
    FT_JIEQUN_TOOL_CODE,
    LocalManifest,
    RunRecord,
    SelectionRecord,
    ToolCapability,
    ToolRunResult,
)
from .runner import (
    FtJiequnQuickPatRunner,
    cp_capability_gate,
    file_sha256,
    ft_jiequn_capability,
)

LOGGER = logging.getLogger("tms.local_agent")


class FolderSelector(Protocol):
    def __call__(self) -> Path | str | None: ...


class ToolRunner(Protocol):
    def run(
        self,
        *,
        tool: ToolCapability,
        source_path: Path,
        output_root: Path,
        expected_source_file_count: int,
    ) -> ToolRunResult: ...


def native_folder_selector() -> Path | None:
    """Open an OS-owned picker; no browser-provided path enters this boundary."""

    try:
        import tkinter as tk
        from tkinter import filedialog
    except ImportError as exc:
        raise AgentError(
            "LOCAL_FOLDER_PICKER_UNAVAILABLE",
            "本机 Python 环境缺少目录选择组件",
            503,
        ) from exc
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        selected = filedialog.askdirectory(
            parent=root,
            title="选择需要在本机快速分析的源数据目录",
            mustexist=True,
        )
    finally:
        root.destroy()
    return Path(selected) if selected else None


class LocalAgentService:
    def __init__(
        self,
        config: AgentConfig,
        *,
        selector: FolderSelector | None = None,
        runner: ToolRunner | None = None,
    ) -> None:
        config.validate()
        self._config = config
        self._selector = selector or native_folder_selector
        self._runner = runner or FtJiequnQuickPatRunner(config)
        self._lock = threading.RLock()
        self._selections: dict[str, SelectionRecord] = {}
        self._runs: dict[str, RunRecord] = {}
        self._executor = ThreadPoolExecutor(
            max_workers=config.max_workers,
            thread_name_prefix="tms-local-analysis",
        )
        self._work_root = config.work_root.expanduser().resolve()
        self._prepare_work_root()

    def close(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=False)

    def list_tools(self) -> list[dict[str, object]]:
        return [
            ft_jiequn_capability(self._config).public_dict(),
            cp_capability_gate().public_dict(),
        ]

    def select_folder(self) -> dict[str, str]:
        selected = self._selector()
        if selected is None or not str(selected).strip():
            raise AgentError(
                "LOCAL_FOLDER_SELECTION_CANCELLED", "未选择本机源数据目录", 409
            )
        raw_path = Path(selected).expanduser().absolute()
        if _is_link_or_junction(raw_path):
            raise ManifestError(
                "LOCAL_SOURCE_LINK_UNSUPPORTED", "所选目录不能是符号链接或联接点"
            )
        source_path = raw_path.resolve()
        if not source_path.is_dir():
            raise ManifestError("LOCAL_SOURCE_NOT_FOUND", "所选本机目录不存在", 404)
        self._assert_source_is_separate(source_path)
        source_label = source_path.name or "selected-folder"
        if len(source_label) > 200 or source_label in {".", ".."}:
            raise ManifestError(
                "LOCAL_SOURCE_LABEL_INVALID", "所选目录名称不符合结果登记合同"
            )
        selection_id = str(uuid4())
        now = _utc_now()
        record = SelectionRecord(
            selection_id=selection_id,
            source_path=source_path,
            source_label=source_label,
            created_at_utc=now,
            previews={},
        )
        with self._lock:
            self._expire_selections_locked(now)
            self._selections[selection_id] = record
        return {"selection_id": selection_id, "source_label": record.source_label}

    def preview(self, selection_id: str, tool_code: str) -> dict[str, object]:
        tool = self._require_enabled_tool(tool_code)
        selection = self._get_selection(selection_id)
        manifest = self._build_manifest(selection, tool)
        with self._lock:
            selection.previews[tool.tool_code] = manifest
        return manifest.preview_dict(tool)

    def create_run(
        self,
        selection_id: str,
        tool_code: str,
        confirmed_manifest_sha256: str,
    ) -> dict[str, str]:
        tool = self._require_enabled_tool(tool_code)
        selection = self._get_selection(selection_id)
        with self._lock:
            previewed = selection.previews.get(tool.tool_code)
        if previewed is None:
            raise AgentError(
                "LOCAL_PREVIEW_REQUIRED", "请先预览并确认本机源目录清单", 409
            )
        confirmed_sha = confirmed_manifest_sha256.lower()
        if previewed.sha256 != confirmed_sha:
            raise AgentError(
                "LOCAL_MANIFEST_CONFIRMATION_MISMATCH",
                "页面确认的目录清单与最近一次预览不一致",
                409,
            )
        current = self._build_manifest(selection, tool)
        if current.sha256 != previewed.sha256:
            raise AgentError(
                "LOCAL_SOURCE_CHANGED", "本机源目录在预览后已变化，请重新预览", 409
            )
        run_id = str(uuid4())
        record = RunRecord(
            run_id=run_id,
            selection_id=selection.selection_id,
            tool=tool,
            source_path=selection.source_path,
            source_label=selection.source_label,
            confirmed_manifest=current,
            status="QUEUED",
            created_at_utc=_utc_now(),
        )
        with self._lock:
            self._runs[run_id] = record
        self._executor.submit(self._execute_run, run_id)
        return {"run_id": run_id, "status": "QUEUED"}

    def get_status(self, run_id: str) -> dict[str, object]:
        record = self._get_run(run_id)
        with self._lock:
            response: dict[str, object] = {
                "run_id": record.run_id,
                "selection_id": record.selection_id,
                "status": record.status,
                "tool_code": record.tool.tool_code,
                "source_label": record.source_label,
                "manifest_sha256": record.confirmed_manifest.sha256,
                "created_at_utc": _iso(record.created_at_utc),
                "started_at_utc": _iso(record.started_at_utc),
                "finished_at_utc": _iso(record.finished_at_utc),
                "parameter_count": (
                    record.result.parameter_count if record.result else None
                ),
                "record_count": record.result.record_count if record.result else None,
                "elapsed_seconds": (
                    record.result.elapsed_seconds if record.result else None
                ),
                "error_code": record.error_code,
                "error_message": record.error_message,
            }
            return response

    def get_receipt(self, run_id: str) -> dict[str, object]:
        record = self._get_run(run_id)
        with self._lock:
            if record.status != "SUCCESS" or record.receipt is None:
                raise AgentError(
                    "LOCAL_RESULT_NOT_READY", "本机快速分析回执尚未生成", 409
                )
            return json.loads(json.dumps(record.receipt, ensure_ascii=False))

    def get_result(self, run_id: str) -> tuple[Path, str]:
        record = self._get_run(run_id)
        with self._lock:
            if (
                record.status != "SUCCESS"
                or record.result is None
                or record.receipt is None
            ):
                raise AgentError(
                    "LOCAL_RESULT_NOT_READY", "本机快速分析结果尚未生成", 409
                )
            path = record.result.report_path.resolve()
            expected_sha = str(record.receipt["result"]["sha256"])
        self._assert_within_work_root(path)
        try:
            if not path.is_file() or file_sha256(path) != expected_sha:
                raise AgentError(
                    "LOCAL_RESULT_CHANGED", "本机快速分析结果已被修改或删除", 409
                )
        except OSError as exc:
            raise AgentError(
                "LOCAL_RESULT_UNREADABLE", "本机快速分析结果无法读取", 409
            ) from exc
        return path, path.name

    def delete_run(self, run_id: str) -> None:
        record = self._get_run(run_id)
        with self._lock:
            if record.status in {"QUEUED", "RUNNING"}:
                raise AgentError(
                    "LOCAL_RUN_ACTIVE",
                    "本机快速分析仍在运行，不能清理工作目录",
                    409,
                )
            self._discard_run_workspace(run_id)
            self._runs.pop(run_id, None)

    def _execute_run(self, run_id: str) -> None:
        record = self._get_run(run_id)
        with self._lock:
            record.status = "RUNNING"
            record.started_at_utc = _utc_now()
        run_root = self._work_root / record.run_id
        output_root = run_root / "attempt-1"
        try:
            run_root.mkdir(parents=False, exist_ok=False)
            before = build_local_manifest(
                record.source_path,
                allowed_suffixes=record.tool.allowed_suffixes,
                max_files=self._config.max_source_files,
            )
            if before.sha256 != record.confirmed_manifest.sha256:
                raise AgentError(
                    "LOCAL_SOURCE_CHANGED",
                    "本机源目录在任务开始前已变化，请重新预览",
                    409,
                )
            result = self._runner.run(
                tool=record.tool,
                source_path=record.source_path,
                output_root=output_root,
                expected_source_file_count=before.file_count,
            )
            self._assert_result_path(result.report_path, output_root)
            after = build_local_manifest(
                record.source_path,
                allowed_suffixes=record.tool.allowed_suffixes,
                max_files=self._config.max_source_files,
            )
            if after.sha256 != before.sha256:
                raise AgentError(
                    "LOCAL_SOURCE_CHANGED_DURING_RUN",
                    "本机源目录在计算期间发生变化，本次结果不予登记",
                    409,
                )
            result_sha = file_sha256(result.report_path)
            result_bytes = result.report_path.stat().st_size
            if result_bytes > self._config.max_output_bytes:
                raise AgentError(
                    "LOCAL_RESULT_TOO_LARGE", "本机快速分析结果超过大小上限", 507
                )
            finished = _utc_now()
            receipt = self._build_receipt(record, result, result_sha, result_bytes)
            _atomic_write_text(
                output_root / "source_manifest.json", manifest_json(after)
            )
            _atomic_write_text(
                output_root / "receipt.json",
                json.dumps(
                    receipt,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            )
            with self._lock:
                record.result = result
                record.receipt = receipt
                record.finished_at_utc = finished
                record.status = "SUCCESS"
        except AgentError as exc:
            LOGGER.exception("Local run %s failed with %s", run_id, exc.code)
            self._mark_failed(record, exc.code, exc.message)
            self._cleanup_failed_workspace(run_id)
        except Exception:
            LOGGER.exception("Local run %s failed unexpectedly", run_id)
            self._mark_failed(
                record,
                "LOCAL_RUN_FAILED",
                "本机快速分析失败，请检查 Agent 配置和本机日志",
            )
            self._cleanup_failed_workspace(run_id)

    def _build_receipt(
        self,
        record: RunRecord,
        result: ToolRunResult,
        result_sha: str,
        result_bytes: int,
    ) -> dict[str, object]:
        manifest = record.confirmed_manifest
        return {
            "contract_version": "TMS_LOCAL_RESULT_V1",
            "tool_code": record.tool.tool_code,
            "analysis_type": record.tool.analysis_type,
            "test_stage": record.tool.test_stage,
            "factory_code": record.tool.factory_code,
            "release_sha256": record.tool.package_sha256,
            "source_label": record.source_label,
            "manifest": {
                "mode": "LOCAL_PATH_SIZE_MTIME_V1",
                "sha256": manifest.sha256,
                "file_count": manifest.file_count,
                "total_bytes": manifest.total_bytes,
            },
            "summary": {
                "parameter_count": result.parameter_count,
                "record_count": result.record_count,
                "elapsed_seconds": result.elapsed_seconds,
            },
            "result": {
                "filename": result.report_path.name,
                "sha256": result_sha,
                "size_bytes": result_bytes,
            },
        }

    def _build_manifest(
        self, selection: SelectionRecord, tool: ToolCapability
    ) -> LocalManifest:
        return build_local_manifest(
            selection.source_path,
            allowed_suffixes=tool.allowed_suffixes,
            max_files=self._config.max_source_files,
        )

    def _require_enabled_tool(self, tool_code: str) -> ToolCapability:
        if tool_code == FT_JIEQUN_TOOL_CODE:
            tool = ft_jiequn_capability(self._config)
        elif tool_code == CP_GATE_TOOL_CODE:
            tool = cp_capability_gate()
        else:
            raise AgentError("LOCAL_TOOL_UNKNOWN", "本机快速分析工具不存在", 404)
        if not tool.enabled:
            raise AgentError(
                "LOCAL_TOOL_DISABLED",
                tool.disabled_reason or "本机快速分析工具未启用",
                409,
            )
        return tool

    def _get_selection(self, selection_id: str) -> SelectionRecord:
        now = _utc_now()
        with self._lock:
            self._expire_selections_locked(now)
            record = self._selections.get(selection_id)
        if record is None:
            raise AgentError(
                "LOCAL_SELECTION_NOT_FOUND",
                "本机目录选择会话不存在或已过期，请重新选择",
                404,
            )
        return record

    def _get_run(self, run_id: str) -> RunRecord:
        with self._lock:
            record = self._runs.get(run_id)
        if record is None:
            raise AgentError("LOCAL_RUN_NOT_FOUND", "本机快速分析任务不存在", 404)
        return record

    def _expire_selections_locked(self, now: datetime) -> None:
        ttl = self._config.selection_ttl_seconds
        expired = [
            selection_id
            for selection_id, record in self._selections.items()
            if (now - record.created_at_utc).total_seconds() > ttl
        ]
        for selection_id in expired:
            self._selections.pop(selection_id, None)

    def _mark_failed(self, record: RunRecord, code: str, message: str) -> None:
        with self._lock:
            record.error_code = code
            record.error_message = message
            record.finished_at_utc = _utc_now()
            record.status = "FAILED"

    def _prepare_work_root(self) -> None:
        self._work_root.mkdir(parents=True, exist_ok=True)
        if _is_link_or_junction(self._work_root):
            raise RuntimeError("Local Agent work_root cannot be a link or junction")
        for child in self._work_root.iterdir():
            if child.is_dir() and _is_safe_run_id(child.name):
                if _is_link_or_junction(child):
                    raise RuntimeError("Local Agent stale run directory cannot be a link")
                shutil.rmtree(child)

    def _discard_run_workspace(self, run_id: str) -> None:
        if not _is_safe_run_id(run_id):
            raise RuntimeError("refusing to remove an unsafe Local Agent run id")
        candidate = (self._work_root / run_id).resolve()
        if candidate.parent != self._work_root or candidate.name != run_id:
            raise RuntimeError("Local Agent run workspace escaped work_root")
        if candidate.exists():
            if _is_link_or_junction(candidate):
                raise RuntimeError("Local Agent run workspace cannot be a link")
            shutil.rmtree(candidate)

    def _cleanup_failed_workspace(self, run_id: str) -> None:
        try:
            self._discard_run_workspace(run_id)
        except Exception:
            LOGGER.exception("Failed to clean Local Agent run workspace %s", run_id)

    def _assert_source_is_separate(self, source_path: Path) -> None:
        source = source_path.resolve()
        if _is_relative_to(source, self._work_root) or _is_relative_to(
            self._work_root, source
        ):
            raise ManifestError(
                "LOCAL_SOURCE_WORKSPACE_OVERLAP",
                "所选源目录不能与 Local Agent 工作目录重叠",
            )

    def _assert_result_path(self, result_path: Path, output_root: Path) -> None:
        result = result_path.resolve()
        if not result.is_file() or not _is_relative_to(result, output_root.resolve()):
            raise AgentError(
                "LOCAL_RESULT_PATH_INVALID", "本机快速分析结果路径不符合合同", 500
            )
        self._assert_within_work_root(result)

    def _assert_within_work_root(self, path: Path) -> None:
        if not _is_relative_to(path.resolve(), self._work_root):
            raise AgentError(
                "LOCAL_RESULT_PATH_ESCAPE", "本机快速分析结果超出工作目录", 500
            )


def _atomic_write_text(path: Path, value: str) -> None:
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(value, encoding="utf-8", newline="\n")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _is_link_or_junction(path: Path) -> bool:
    is_junction = getattr(path, "is_junction", lambda: False)
    return path.is_symlink() or bool(is_junction())


def _is_safe_run_id(value: str) -> bool:
    return re.fullmatch(
        r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
        value,
    ) is not None


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat().replace("+00:00", "Z") if value else None
