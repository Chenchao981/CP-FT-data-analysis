from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

LOCAL_MANIFEST_MODE = "LOCAL_PATH_SIZE_MTIME_V1"
FT_JIEQUN_TOOL_CODE = "JIEQUN_FT_QUICK_PAT_EXISTING"
CP_GATE_TOOL_CODE = "CP_RAW_QUICK_PAT"


class SelectFolderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ToolRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    tool_code: str = Field(min_length=2, max_length=128)


class RunRequest(ToolRequest):
    confirmed_manifest_sha256: str = Field(pattern=r"^[0-9a-fA-F]{64}$")


@dataclass(frozen=True, slots=True)
class ToolCapability:
    tool_code: str
    display_name: str
    test_stage: str
    factory_code: str
    analysis_type: str
    input_contract_version: str
    output_contract_version: str
    entrypoint: str
    allowed_suffixes: tuple[str, ...]
    enabled: bool
    disabled_reason: str | None
    package_sha256: str | None
    timeout_seconds: int | None
    max_output_bytes: int | None

    def public_dict(self) -> dict[str, Any]:
        return {
            "tool_code": self.tool_code,
            "display_name": self.display_name,
            "test_stage": self.test_stage,
            "factory_code": self.factory_code,
            "analysis_type": self.analysis_type,
            "input_contract_version": self.input_contract_version,
            "output_contract_version": self.output_contract_version,
            "entrypoint": self.entrypoint,
            "allowed_suffixes": list(self.allowed_suffixes),
            "enabled": self.enabled,
            "disabled_reason": self.disabled_reason,
            "package_sha256": self.package_sha256,
            "timeout_seconds": self.timeout_seconds,
            "max_output_bytes": self.max_output_bytes,
        }


@dataclass(frozen=True, slots=True)
class ManifestFile:
    relative_path: str
    size_bytes: int
    mtime_ns: int


@dataclass(frozen=True, slots=True)
class LocalManifest:
    source_label: str
    files: tuple[ManifestFile, ...]
    total_bytes: int
    sha256: str

    @property
    def file_count(self) -> int:
        return len(self.files)

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "mode": LOCAL_MANIFEST_MODE,
            "source_label": self.source_label,
            "files": [
                {
                    "relative_path": item.relative_path,
                    "size_bytes": item.size_bytes,
                    "mtime_ns": item.mtime_ns,
                }
                for item in self.files
            ],
            "file_count": self.file_count,
            "total_bytes": self.total_bytes,
        }

    def preview_dict(self, tool: ToolCapability) -> dict[str, Any]:
        return {
            "mode": LOCAL_MANIFEST_MODE,
            "file_count": self.file_count,
            "total_bytes": self.total_bytes,
            "sha256": self.sha256,
            "source_label": self.source_label,
            "tool_code": tool.tool_code,
            "allowed_suffixes": list(tool.allowed_suffixes),
        }


@dataclass(slots=True)
class SelectionRecord:
    selection_id: str
    source_path: Path
    source_label: str
    created_at_utc: datetime
    previews: dict[str, LocalManifest]


@dataclass(frozen=True, slots=True)
class ToolRunResult:
    report_path: Path
    parameter_count: int
    record_count: int
    elapsed_seconds: float
    stdout_tail: str


@dataclass(slots=True)
class RunRecord:
    run_id: str
    selection_id: str
    tool: ToolCapability
    source_path: Path
    source_label: str
    confirmed_manifest: LocalManifest
    status: str
    created_at_utc: datetime
    started_at_utc: datetime | None = None
    finished_at_utc: datetime | None = None
    error_code: str | None = None
    error_message: str | None = None
    result: ToolRunResult | None = None
    receipt: dict[str, Any] | None = None
