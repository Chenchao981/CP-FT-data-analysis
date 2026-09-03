from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from threading import Lock
from typing import Annotated, Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.errors import DomainError
from app.domain.auth import Principal
from app.domain.quick_capacity import QuickCapacityPolicy


class QuickAnalysisStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


class CreateQuickPatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    source_root_code: str = Field(min_length=2, max_length=128)
    source_relative_path: str = Field(default=".", max_length=1000)
    source_manifest_mode: str = Field(min_length=1, max_length=64)
    source_manifest_sha256: str = Field(pattern=r"^[0-9a-fA-F]{64}$")


class DirectPathPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    path: str = Field(min_length=1, max_length=1000)
    tool_code: Literal[
        "JIEQUN_FT_QUICK_PAT_EXISTING",
        "RIYUEXIN_FT_QUICK_PAT_EXISTING",
        "RIYUEGUANG_FT_QUICK_PAT_EXISTING",
        "DIANJI_FT_QUICK_PAT_EXISTING",
        "JIJIA_FT_QUICK_PAT_EXISTING",
        "HUAHONG_CP_QUICK_PAT_EXISTING",
        "JETECH_CP_QUICK_PAT_EXISTING",
        "LION_CP_QUICK_PAT_EXISTING",
        "GUOYU_CP_QUICK_PAT_EXISTING",
    ] = "JIEQUN_FT_QUICK_PAT_EXISTING"


class DirectPathBrowseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    path: str = Field(default="", max_length=1000)
    tool_code: Literal[
        "JIEQUN_FT_QUICK_PAT_EXISTING",
        "RIYUEXIN_FT_QUICK_PAT_EXISTING",
        "RIYUEGUANG_FT_QUICK_PAT_EXISTING",
        "DIANJI_FT_QUICK_PAT_EXISTING",
        "JIJIA_FT_QUICK_PAT_EXISTING",
        "HUAHONG_CP_QUICK_PAT_EXISTING",
        "JETECH_CP_QUICK_PAT_EXISTING",
        "LION_CP_QUICK_PAT_EXISTING",
        "GUOYU_CP_QUICK_PAT_EXISTING",
    ] = "JIEQUN_FT_QUICK_PAT_EXISTING"


class CreateDirectPathPatRequest(DirectPathPreviewRequest):
    source_manifest_mode: Literal["LOCAL_PATH_SIZE_MTIME_V1"]
    source_manifest_sha256: str = Field(pattern=r"^[0-9a-fA-F]{64}$")


class TemporaryFtpPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    protocol: Literal["FTP", "FTPS"] = "FTP"
    server: str = Field(min_length=1, max_length=255)
    port: int | None = Field(default=None, ge=1, le=65535)
    username: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=1, max_length=1000)
    remote_path: str = Field(default="/", min_length=1, max_length=1000)


LOCAL_RESULT_CONTRACT_VERSION = "TMS_LOCAL_RESULT_V1"
LOCAL_QUICK_PAT_TOOL_CODE = "JIEQUN_FT_QUICK_PAT_EXISTING"
LOCAL_QUICK_PAT_ADAPTER_CODE = "JIEQUN_FT_QUICK_PAT_PYZ"
LOCAL_QUICK_PAT_INPUT_CONTRACT = "JIEQUN_UNIFIED_CSV_DIRECTORY_V1"
LOCAL_QUICK_PAT_OUTPUT_CONTRACT = "FT_PAT_RESULT_V1"

DIRECT_PATH_TOOL_CONTRACTS: dict[str, dict[str, object]] = {
    LOCAL_QUICK_PAT_TOOL_CODE: {
        "test_stage": "FT",
        "factory_code": "JIEQUN",
        "format_code": LOCAL_QUICK_PAT_TOOL_CODE,
        "cleaner_code": LOCAL_QUICK_PAT_TOOL_CODE,
        "adapter_code": LOCAL_QUICK_PAT_ADAPTER_CODE,
        "input_contract_version": LOCAL_QUICK_PAT_INPUT_CONTRACT,
        "output_contract_version": LOCAL_QUICK_PAT_OUTPUT_CONTRACT,
        "allowed_suffixes": (".csv",),
        "single_file_suffixes": (".csv", ".zip", ".7z"),
        "manifest_policy": "ALL_MATCHING_SUFFIXES_V1",
        "tool_name": "杰群 FT 原始目录低内存 PAT",
    },
    "RIYUEXIN_FT_QUICK_PAT_EXISTING": {
        "test_stage": "FT",
        "factory_code": "RIYUEXIN",
        "format_code": "RIYUEXIN_FT_QUICK_PAT_EXISTING",
        "cleaner_code": "RIYUEXIN_FT_QUICK_PAT_EXISTING",
        "adapter_code": "RIYUEXIN_FT_QUICK_PAT_PYZ",
        "input_contract_version": "RIYUEXIN_RAW_XLSX_DIRECTORY_V1",
        "output_contract_version": "FT_PAT_RESULT_V1",
        "allowed_suffixes": (".xlsx",),
        "single_file_suffixes": (".xlsx", ".zip", ".7z"),
        "manifest_policy": "RIYUEXIN_RAW_DIRECTORY_V1",
        "tool_name": "日月新 FT 原始目录低内存 PAT",
    },
    "RIYUEGUANG_FT_QUICK_PAT_EXISTING": {
        "test_stage": "FT",
        "factory_code": "RIYUEGUANG",
        "format_code": "RIYUEGUANG_FT_QUICK_PAT_EXISTING",
        "cleaner_code": "RIYUEGUANG_FT_QUICK_PAT_EXISTING",
        "adapter_code": "RIYUEGUANG_FT_QUICK_PAT_PYZ",
        "input_contract_version": "RIYUEGUANG_RAW_XLSX_DIRECTORY_V1",
        "output_contract_version": "FT_PAT_RESULT_V1",
        "allowed_suffixes": (".xlsx",),
        "single_file_suffixes": (".xlsx", ".zip", ".7z"),
        "manifest_policy": "RIYUEGUANG_RAW_DIRECTORY_V1",
        "tool_name": "日月光 FT 原始目录低内存 PAT",
    },
    "DIANJI_FT_QUICK_PAT_EXISTING": {
        "test_stage": "FT",
        "factory_code": "DIANJI",
        "format_code": "DIANJI_FT_QUICK_PAT_EXISTING",
        "cleaner_code": "DIANJI_FT_QUICK_PAT_EXISTING",
        "adapter_code": "DIANJI_FT_QUICK_PAT_PYZ",
        "input_contract_version": "DIANJI_REGISTERED_RAW_DIRECTORY_V1",
        "output_contract_version": "FT_PAT_RESULT_V1",
        "allowed_suffixes": (".xls", ".xlsx", ".csv"),
        "single_file_suffixes": (".xls", ".xlsx", ".csv", ".zip", ".7z"),
        "manifest_policy": "DIANJI_RAW_DIRECTORY_V1",
        "tool_name": "电基 FT 原始目录低内存 PAT",
    },
    "JIJIA_FT_QUICK_PAT_EXISTING": {
        "test_stage": "FT",
        "factory_code": "JIJIA",
        "format_code": "JIJIA_FT_QUICK_PAT_EXISTING",
        "cleaner_code": "JIJIA_FT_QUICK_PAT_EXISTING",
        "adapter_code": "JIJIA_FT_QUICK_PAT_PYZ",
        "input_contract_version": "JIJIA_STS8203_CSV_DIRECTORY_V1",
        "output_contract_version": "FT_PAT_RESULT_V1",
        "allowed_suffixes": (".csv",),
        "single_file_suffixes": (".csv", ".zip", ".7z"),
        "manifest_policy": "ALL_MATCHING_SUFFIXES_V1",
        "tool_name": "集佳 FT 原始目录低内存 PAT",
    },
    "HUAHONG_CP_QUICK_PAT_EXISTING": {
        "test_stage": "CP",
        "factory_code": "HUAHONG",
        "format_code": "HUAHONG_DCP_EXISTING",
        "cleaner_code": "HUAHONG_CP_EXISTING",
        "adapter_code": "HUAHONG_CP_PYZ",
        "input_contract_version": "CP_ARCHIVE_OR_TXT_V1",
        "output_contract_version": "CP_CSV_TRIPLET_V1",
        "allowed_suffixes": (".txt",),
        "single_file_suffixes": (".zip", ".7z"),
        "manifest_policy": "ALL_MATCHING_SUFFIXES_V1",
        "tool_name": "华虹 CP 原始目录 PAT",
    },
    "JETECH_CP_QUICK_PAT_EXISTING": {
        "test_stage": "CP",
        "factory_code": "JETECH",
        "format_code": "JETECH_CP_EXISTING",
        "cleaner_code": "JETECH_CP_EXISTING",
        "adapter_code": "JETECH_CP_PYZ",
        "input_contract_version": "CP_EXCEL_OR_ZIP_V1",
        "output_contract_version": "CP_STANDARD_CSV_TRIPLET_V1",
        "allowed_suffixes": (".xls", ".xlsx"),
        "single_file_suffixes": (".xls", ".xlsx", ".zip"),
        "manifest_policy": "ALL_MATCHING_SUFFIXES_V1",
        "tool_name": "积塔 CP 原始目录 PAT",
    },
    "LION_CP_QUICK_PAT_EXISTING": {
        "test_stage": "CP",
        "factory_code": "LION",
        "format_code": "LION_CP_EXISTING",
        "cleaner_code": "LION_CP_EXISTING",
        "adapter_code": "LION_CP_PYZ",
        "input_contract_version": "CP_EXCEL_OR_ZIP_V1",
        "output_contract_version": "CP_STANDARD_CSV_TRIPLET_V1",
        "allowed_suffixes": (".xls", ".xlsx"),
        "single_file_suffixes": (".xls", ".xlsx", ".zip"),
        "manifest_policy": "ALL_MATCHING_SUFFIXES_V1",
        "tool_name": "立昂微 CP 原始目录 PAT",
    },
    "GUOYU_CP_QUICK_PAT_EXISTING": {
        "test_stage": "CP",
        "factory_code": "GUOYU",
        "format_code": "GUOYU_FRD_CP_EXISTING",
        "cleaner_code": "GUOYU_FRD_CP_EXISTING",
        "adapter_code": "GUOYU_CP_PYZ",
        "input_contract_version": "CP_EXCEL_OR_ZIP_V1",
        "output_contract_version": "CP_STANDARD_CSV_TRIPLET_V1",
        "allowed_suffixes": (".xls", ".xlsx"),
        "single_file_suffixes": (".xls", ".xlsx", ".zip"),
        "manifest_policy": "ALL_MATCHING_SUFFIXES_V1",
        "tool_name": "国宇 CP 原始目录 PAT",
    },
}

Sha256 = Annotated[str, Field(pattern=r"^[0-9a-fA-F]{64}$")]


class LocalSourceManifestReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)

    mode: Literal["LOCAL_PATH_SIZE_MTIME_V1"]
    sha256: Sha256
    file_count: int = Field(gt=0, le=1_000_000)
    total_bytes: int = Field(ge=0, le=9_223_372_036_854_775_807)

    @field_validator("sha256")
    @classmethod
    def normalize_sha256(cls, value: str) -> str:
        return value.lower()


class LocalPatSummaryReceipt(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        str_strip_whitespace=True,
        allow_inf_nan=False,
    )

    parameter_count: int = Field(gt=0, le=100_000)
    record_count: int = Field(gt=0, le=9_223_372_036_854_775_807)
    elapsed_seconds: float = Field(gt=0)


class LocalPatResultReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)

    filename: str = Field(min_length=6, max_length=255)
    size_bytes: int = Field(gt=0, le=9_223_372_036_854_775_807)
    sha256: Sha256

    @field_validator("filename")
    @classmethod
    def validate_filename(cls, value: str) -> str:
        windows_device_name = value.split(".", 1)[0].upper()
        if (
            value in {".", ".."}
            or "/" in value
            or "\\" in value
            or ":" in value
            or any(character in '<>"|?*' for character in value)
            or any(ord(character) < 32 for character in value)
            or not value.lower().endswith(".xlsx")
            or windows_device_name
            in {
                "CON",
                "PRN",
                "AUX",
                "NUL",
                *(f"COM{number}" for number in range(1, 10)),
                *(f"LPT{number}" for number in range(1, 10)),
            }
        ):
            raise ValueError("result filename must be one safe .xlsx basename")
        return value

    @field_validator("sha256")
    @classmethod
    def normalize_sha256(cls, value: str) -> str:
        return value.lower()


class LocalQuickPatResultReceipt(BaseModel):
    """Strict, path-free receipt emitted by the user-side Local Agent."""

    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)

    contract_version: Literal["TMS_LOCAL_RESULT_V1"]
    tool_code: Literal["JIEQUN_FT_QUICK_PAT_EXISTING"]
    analysis_type: Literal["QUICK_PAT"]
    test_stage: Literal["FT"]
    factory_code: Literal["JIEQUN"]
    release_sha256: Sha256
    source_label: str = Field(min_length=1, max_length=200)
    manifest: LocalSourceManifestReceipt
    summary: LocalPatSummaryReceipt
    result: LocalPatResultReceipt

    @field_validator("release_sha256")
    @classmethod
    def normalize_release_sha256(cls, value: str) -> str:
        return value.lower()

    @field_validator("source_label")
    @classmethod
    def validate_source_label(cls, value: str) -> str:
        if (
            value in {".", ".."}
            or "/" in value
            or "\\" in value
            or ":" in value
            or any(ord(character) < 32 for character in value)
        ):
            raise ValueError("source_label must be a desensitized label, not a path")
        return value


@dataclass(frozen=True, slots=True)
class NewQuickAnalysisSession:
    analysis_type: str
    test_stage: str
    factory_code: str
    source_root_code: str
    source_relative_path: str
    source_manifest_mode: str
    source_manifest_json: str
    source_manifest_sha256: str
    source_file_count: int
    source_total_bytes: int
    retention_mode: str
    cleaner_release_id: int
    expires_at_utc: datetime
    access_scope: Literal["PERSONAL", "DOMAIN"]
    data_domain_id: int | None
    data_domain_code: str | None = None
    reserved_bytes: int = 0


@dataclass(frozen=True, slots=True)
class QuickAnalysisSession:
    analysis_session_id: int
    owner_user_id: int
    owner_login: str
    owner_name: str
    access_scope: Literal["PERSONAL", "DOMAIN"]
    data_domain_id: int | None
    data_domain_code: str | None
    analysis_type: str
    test_stage: str
    factory_code: str
    source_root_code: str
    source_relative_path: str
    source_manifest_mode: str
    source_manifest_sha256: str
    source_file_count: int
    source_total_bytes: int
    retention_mode: str
    cleaner_release_id: int
    status: QuickAnalysisStatus
    job_id: int | None
    job_status: str | None
    parameter_count: int | None
    record_count: int | None
    summary: dict[str, Any] | None
    result_file_name: str | None
    result_size_bytes: int | None
    error_code: str | None
    error_message: str | None
    expires_at_utc: datetime
    created_at_utc: datetime
    started_at_utc: datetime | None = None
    finished_at_utc: datetime | None = None
    reserved_bytes: int = 0
    cleanup_status: str = "RETAINED"


@dataclass(frozen=True, slots=True)
class QuickAnalysisPage:
    items: tuple[QuickAnalysisSession, ...]
    total: int
    page: int
    page_size: int


@dataclass(frozen=True, slots=True)
class QuickAnalysisWorkItem:
    analysis_session_id: int
    owner_user_id: int
    access_scope: Literal["PERSONAL", "DOMAIN"]
    data_domain_id: int | None
    data_domain_code: str | None
    analysis_type: str
    test_stage: str
    factory_code: str
    source_root_code: str
    source_relative_path: str
    source_manifest_mode: str
    source_manifest_json: str
    source_manifest_sha256: str
    cleaner_release_id: int
    expires_at_utc: datetime
    status: QuickAnalysisStatus


@dataclass(frozen=True, slots=True)
class QuickAnalysisArtifact:
    role: str
    path: str
    size_bytes: int
    sha256: str


class InMemoryQuickAnalysisService:
    def __init__(
        self,
        capacity: QuickCapacityPolicy | None = None,
        *,
        domain_grant_checker: Callable[[int, int], bool] | None = None,
    ) -> None:
        self._items: dict[int, QuickAnalysisSession] = {}
        self._work: dict[int, QuickAnalysisWorkItem] = {}
        self._artifacts: dict[int, tuple[QuickAnalysisArtifact, ...]] = {}
        self._next_id = 1
        self._lock = Lock()
        self._capacity = capacity
        self._domain_grant_checker = domain_grant_checker or (
            lambda _user_id, _data_domain_id: False
        )

    def create(
        self, principal: Principal, request: NewQuickAnalysisSession
    ) -> QuickAnalysisSession:
        with self._lock:
            self._assert_new_session_access(principal, request)
            if self._capacity is not None:
                active = tuple(
                    item
                    for item in self._items.values()
                    if item.status
                    in {
                        QuickAnalysisStatus.QUEUED,
                        QuickAnalysisStatus.RUNNING,
                    }
                )
                self._capacity.ensure_quota(
                    global_used_bytes=sum(item.reserved_bytes for item in active),
                    user_used_bytes=sum(
                        item.reserved_bytes
                        for item in active
                        if item.owner_user_id == principal.user_id
                    ),
                    reservation_bytes=request.reserved_bytes,
                )
            now = datetime.now(UTC)
            item = QuickAnalysisSession(
                analysis_session_id=self._next_id,
                owner_user_id=principal.user_id,
                owner_login=principal.login_name,
                owner_name=principal.display_name,
                access_scope=request.access_scope,
                data_domain_id=request.data_domain_id,
                data_domain_code=request.data_domain_code,
                analysis_type=request.analysis_type,
                test_stage=request.test_stage,
                factory_code=request.factory_code,
                source_root_code=request.source_root_code,
                source_relative_path=request.source_relative_path,
                source_manifest_mode=request.source_manifest_mode,
                source_manifest_sha256=request.source_manifest_sha256,
                source_file_count=request.source_file_count,
                source_total_bytes=request.source_total_bytes,
                retention_mode=request.retention_mode,
                cleaner_release_id=request.cleaner_release_id,
                status=QuickAnalysisStatus.QUEUED,
                job_id=None,
                job_status="QUEUED",
                parameter_count=None,
                record_count=None,
                summary=None,
                result_file_name=None,
                result_size_bytes=None,
                error_code=None,
                error_message=None,
                expires_at_utc=request.expires_at_utc,
                created_at_utc=now,
                reserved_bytes=request.reserved_bytes,
            )
            self._items[item.analysis_session_id] = item
            self._work[item.analysis_session_id] = QuickAnalysisWorkItem(
                item.analysis_session_id,
                item.owner_user_id,
                item.access_scope,
                item.data_domain_id,
                item.data_domain_code,
                item.analysis_type,
                item.test_stage,
                item.factory_code,
                item.source_root_code,
                item.source_relative_path,
                request.source_manifest_mode,
                request.source_manifest_json,
                request.source_manifest_sha256,
                request.cleaner_release_id,
                request.expires_at_utc,
                QuickAnalysisStatus.QUEUED,
            )
            self._next_id += 1
            return item

    def attach_job(self, analysis_session_id: int, job_id: int) -> None:
        with self._lock:
            item = self._required(analysis_session_id)
            self._items[analysis_session_id] = replace(item, job_id=job_id)

    def list_for_principal(
        self,
        principal: Principal,
        *,
        access_scope: Literal["PERSONAL", "DOMAIN"] | None = None,
    ) -> tuple[QuickAnalysisSession, ...]:
        items = (
            item
            for item in self._items.values()
            if self._can_read(item, principal)
            and (access_scope is None or item.access_scope == access_scope)
        )
        return tuple(
            self._effective(item)
            for item in sorted(
                items, key=lambda item: item.analysis_session_id, reverse=True
            )
        )

    def list_page_for_principal(
        self,
        principal: Principal,
        *,
        page: int,
        page_size: int,
        status: QuickAnalysisStatus | None = None,
        from_utc: datetime | None = None,
        to_utc: datetime | None = None,
        access_scope: Literal["PERSONAL", "DOMAIN"] | None = None,
    ) -> QuickAnalysisPage:
        items = self.list_for_principal(principal, access_scope=access_scope)
        filtered = tuple(
            item
            for item in items
            if (status is None or item.status == status)
            and (from_utc is None or item.created_at_utc >= from_utc)
            and (to_utc is None or item.created_at_utc < to_utc)
        )
        offset = (page - 1) * page_size
        return QuickAnalysisPage(
            items=filtered[offset : offset + page_size],
            total=len(filtered),
            page=page,
            page_size=page_size,
        )

    def get_for_principal(
        self, analysis_session_id: int, principal: Principal
    ) -> QuickAnalysisSession:
        item = self._required(analysis_session_id)
        if not self._can_read(item, principal):
            raise DomainError(
                "QUICK_ANALYSIS_NOT_FOUND", "快速分析会话不存在或无权访问", 404
            )
        return self._effective(item)

    def worker_session_info(self, analysis_session_id: int) -> QuickAnalysisWorkItem:
        try:
            return self._work[analysis_session_id]
        except KeyError as exc:
            raise DomainError(
                "QUICK_ANALYSIS_NOT_FOUND", "快速分析会话不存在", 404
            ) from exc

    def mark_running(self, analysis_session_id: int) -> None:
        with self._lock:
            now = datetime.now(UTC)
            item = self._required(analysis_session_id)
            self._assert_execution_authorized(item)
            self._items[analysis_session_id] = replace(
                item,
                status=QuickAnalysisStatus.RUNNING,
                job_status="RUNNING",
                started_at_utc=item.started_at_utc or now,
                error_code=None,
                error_message=None,
            )
            self._work[analysis_session_id] = replace(
                self._work[analysis_session_id], status=QuickAnalysisStatus.RUNNING
            )

    def record_success(
        self,
        analysis_session_id: int,
        job_id: int,
        *,
        parameter_count: int,
        record_count: int | None,
        summary: dict[str, Any],
        artifacts: tuple[QuickAnalysisArtifact, ...],
    ) -> None:
        with self._lock:
            item = self._required(analysis_session_id)
            self._assert_execution_authorized(item)
            report = next(
                (artifact for artifact in artifacts if artifact.role == "pat_report"),
                None,
            )
            if report is None:
                raise ValueError("pat_report artifact is required")
            self._artifacts[analysis_session_id] = artifacts
            self._items[analysis_session_id] = replace(
                item,
                job_id=job_id,
                status=QuickAnalysisStatus.SUCCESS,
                job_status="SUCCESS",
                parameter_count=parameter_count,
                record_count=record_count,
                summary=summary,
                result_file_name=Path(report.path).name,
                result_size_bytes=report.size_bytes,
                finished_at_utc=datetime.now(UTC),
                error_code=None,
                error_message=None,
            )
            self._work[analysis_session_id] = replace(
                self._work[analysis_session_id], status=QuickAnalysisStatus.SUCCESS
            )

    def mark_failed(
        self, analysis_session_id: int, error_code: str, error_message: str
    ) -> None:
        with self._lock:
            item = self._required(analysis_session_id)
            self._items[analysis_session_id] = replace(
                item,
                status=QuickAnalysisStatus.FAILED,
                job_status="FAILED",
                error_code=error_code,
                error_message=error_message,
                finished_at_utc=datetime.now(UTC),
            )
            self._work[analysis_session_id] = replace(
                self._work[analysis_session_id], status=QuickAnalysisStatus.FAILED
            )

    def mark_failed_cleaned(
        self, analysis_session_id: int, error_code: str, error_message: str
    ) -> None:
        """Mark a failure after bounded files were removed or before any existed."""
        with self._lock:
            item = self._required(analysis_session_id)
            self._items[analysis_session_id] = replace(
                item,
                status=QuickAnalysisStatus.FAILED,
                job_status="FAILED",
                error_code=error_code,
                error_message=error_message,
                finished_at_utc=datetime.now(UTC),
                reserved_bytes=0,
                cleanup_status="CLEANED",
            )
            self._work[analysis_session_id] = replace(
                self._work[analysis_session_id], status=QuickAnalysisStatus.FAILED
            )
            self._artifacts.pop(analysis_session_id, None)

    def result_artifact(
        self, analysis_session_id: int, principal: Principal
    ) -> QuickAnalysisArtifact:
        session = self.get_for_principal(analysis_session_id, principal)
        if session.status == QuickAnalysisStatus.EXPIRED:
            raise DomainError("QUICK_RESULT_EXPIRED", "PAT 结果已过期", 410)
        artifact = next(
            (
                item
                for item in self._artifacts.get(analysis_session_id, ())
                if item.role == "pat_report"
            ),
            None,
        )
        if artifact is None:
            raise DomainError("QUICK_RESULT_NOT_FOUND", "PAT 结果尚不可下载", 404)
        return artifact

    def _required(self, analysis_session_id: int) -> QuickAnalysisSession:
        try:
            return self._items[analysis_session_id]
        except KeyError as exc:
            raise DomainError(
                "QUICK_ANALYSIS_NOT_FOUND", "快速分析会话不存在", 404
            ) from exc

    def _can_read(self, item: QuickAnalysisSession, principal: Principal) -> bool:
        if item.access_scope == "PERSONAL":
            return item.owner_user_id == principal.user_id
        return bool(
            item.data_domain_id is not None
            and self._domain_grant_checker(principal.user_id, item.data_domain_id)
        )

    def _assert_execution_authorized(self, item: QuickAnalysisSession) -> None:
        if item.access_scope == "PERSONAL":
            return
        if item.data_domain_id is not None and self._domain_grant_checker(
            item.owner_user_id, item.data_domain_id
        ):
            return
        raise DomainError(
            "QUICK_DATA_DOMAIN_ACCESS_REVOKED",
            "快速分析发起人的数据域授权已失效，任务已停止",
            409,
        )

    def _assert_new_session_access(
        self, principal: Principal, request: NewQuickAnalysisSession
    ) -> None:
        if (
            request.access_scope == "PERSONAL"
            and request.source_root_code == "LOCAL_AGENT"
            and request.data_domain_id is None
            and request.data_domain_code is None
        ):
            return
        if (
            request.access_scope == "DOMAIN"
            and request.source_root_code != "LOCAL_AGENT"
            and request.data_domain_id is not None
            and request.data_domain_code
            and self._domain_grant_checker(principal.user_id, request.data_domain_id)
        ):
            return
        raise DomainError(
            "QUICK_ACCESS_SCOPE_INVALID",
            "快速分析来源与数据权限范围不一致，已停止创建",
            409,
        )

    @staticmethod
    def _effective(item: QuickAnalysisSession) -> QuickAnalysisSession:
        if (
            item.status == QuickAnalysisStatus.SUCCESS
            and item.expires_at_utc <= datetime.now(UTC)
        ):
            return replace(item, status=QuickAnalysisStatus.EXPIRED)
        return item


class QuickAnalysisService(Protocol):
    def create(
        self, principal: Principal, request: NewQuickAnalysisSession
    ) -> QuickAnalysisSession: ...

    def attach_job(self, analysis_session_id: int, job_id: int) -> None: ...

    def list_for_principal(
        self,
        principal: Principal,
        *,
        access_scope: Literal["PERSONAL", "DOMAIN"] | None = None,
    ) -> tuple[QuickAnalysisSession, ...]: ...

    def list_page_for_principal(
        self,
        principal: Principal,
        *,
        page: int,
        page_size: int,
        status: QuickAnalysisStatus | None = None,
        from_utc: datetime | None = None,
        to_utc: datetime | None = None,
        access_scope: Literal["PERSONAL", "DOMAIN"] | None = None,
    ) -> QuickAnalysisPage: ...

    def get_for_principal(
        self, analysis_session_id: int, principal: Principal
    ) -> QuickAnalysisSession: ...

    def worker_session_info(
        self, analysis_session_id: int
    ) -> QuickAnalysisWorkItem: ...

    def mark_running(self, analysis_session_id: int) -> None: ...

    def record_success(
        self,
        analysis_session_id: int,
        job_id: int,
        *,
        parameter_count: int,
        record_count: int | None,
        summary: dict[str, Any],
        artifacts: tuple[QuickAnalysisArtifact, ...],
    ) -> None: ...

    def mark_failed(
        self, analysis_session_id: int, error_code: str, error_message: str
    ) -> None: ...

    def mark_failed_cleaned(
        self, analysis_session_id: int, error_code: str, error_message: str
    ) -> None: ...

    def result_artifact(
        self, analysis_session_id: int, principal: Principal
    ) -> QuickAnalysisArtifact: ...
