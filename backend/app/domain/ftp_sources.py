from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.cleaner_capabilities import FORMAL_CLEANER_CONTRACTS

FORMAL_SUFFIXES = {
    "HUAHONG": {".zip", ".7z", ".txt"},
    "JETECH": {".zip", ".xls", ".xlsx"},
    "LION": {".zip", ".xls", ".xlsx"},
    "RIYUEXIN": {".xlsx"}, "RIYUEGUANG": {".xlsx"},
    "DIANJI": {".xls", ".xlsx"},
}


def safe_component(value: str) -> str:
    if (not value or len(value) > 255 or value in {".", ".."} or value[-1:] in {".", " "}
        or any(ord(char) < 32 or char in '/\\:<>"|?*' for char in value)
        or re.fullmatch(r"(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", value)):
        raise ValueError("文件或目录名称不能安全映射到 Windows 受管目录")
    return value


class FtpSourceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_code: str = Field(pattern=r"^[A-Z][A-Z0-9_-]{1,63}$")
    source_name: str = Field(min_length=1, max_length=200)
    protocol: Literal["FTP", "FTPS"]
    host: str = Field(min_length=1, max_length=253)
    port: int = Field(default=21, ge=1, le=65535)
    remote_root: str = Field(min_length=1, max_length=900)
    credential_ref: str = Field(pattern=r"^[A-Z][A-Z0-9_-]{1,63}$")
    encoding: Literal["utf-8", "gb18030"] = "utf-8"
    test_stage: Literal["CP", "FT"]
    factory_code: str
    data_domain_id: int = Field(gt=0)
    cleaner_release_id: int = Field(gt=0)
    package_mode: Literal["SINGLE_FILE", "DIRECTORY"]
    package_depth: int = Field(default=1, ge=1, le=4)
    ready_marker: str | None = Field(default=None, max_length=100)
    allowed_suffixes: list[str] = Field(min_length=1, max_length=6)
    interval_seconds: int = Field(default=300, ge=30, le=86400)
    stable_seconds: int = Field(default=120, ge=30, le=86400)
    timeout_seconds: int = Field(default=15, ge=3, le=60)
    max_files: int = Field(default=10000, ge=1, le=100000)
    max_bytes: int = Field(default=50 * 1024**3, ge=1, le=500 * 1024**3)
    max_entries: int = Field(default=50000, ge=1, le=500000)
    max_packages_per_scan: int = Field(default=50, ge=1, le=500)

    @field_validator("host")
    @classmethod
    def host_only(cls, value: str) -> str:
        # An administrator selects an endpoint, never a credential-bearing URL.
        if value != value.strip() or any(char.isspace() or char in "/\\:@?#" for char in value):
            raise ValueError("请单独填写服务器主机名或 IPv4 地址，不要填写 URL、端口或账号")
        return value

    @field_validator("remote_root")
    @classmethod
    def absolute_root(cls, value: str) -> str:
        if not value.startswith("/") or "\\" in value or ".." in value.split("/") or any(ord(c) < 32 for c in value):
            raise ValueError("FTP 根目录必须是无上级跳转的绝对目录")
        return str(PurePosixPath(value))

    @model_validator(mode="after")
    def contract(self):
        if len(f"{self.protocol.lower()}://{self.host}:{self.port}{self.remote_root}") > 1000:
            raise ValueError("服务器地址与根目录组合超过存储长度上限")
        if (self.test_stage, self.factory_code) not in FORMAL_CLEANER_CONTRACTS:
            raise ValueError("该阶段/厂家没有已批准的正式入库合同")
        if len(set(self.allowed_suffixes)) != len(self.allowed_suffixes) or not set(self.allowed_suffixes).issubset(FORMAL_SUFFIXES[self.factory_code]):
            raise ValueError("文件类型必须属于该厂家的正式输入合同")
        if self.package_mode == "DIRECTORY":
            if not self.ready_marker:
                raise ValueError("按目录采集必须指定厂家写入完成后提供的完成标记文件")
            safe_component(self.ready_marker)
            if PurePosixPath(self.ready_marker).suffix.lower() in self.allowed_suffixes:
                raise ValueError("完成标记不能同时作为测量源文件")
        elif self.ready_marker is not None:
            raise ValueError("单文件采集不使用目录完成标记")
        if self.factory_code == "HUAHONG" and self.package_mode == "SINGLE_FILE" and ".txt" in self.allowed_suffixes:
            raise ValueError("华虹 DCP 文本需要按完整批次目录采集；单文件仅支持归档包")
        return self


class FtpSourceToggle(BaseModel):
    model_config = ConfigDict(extra="forbid")
    active: bool


@dataclass(frozen=True)
class RemoteFile:
    path: str
    size: int
    modified: str

    @property
    def modified_at(self) -> datetime:
        return datetime.strptime(self.modified.split(".")[0], "%Y%m%d%H%M%S").replace(tzinfo=UTC)


@dataclass(frozen=True)
class RemotePackage:
    path: str
    files: tuple[RemoteFile, ...]
    marker: RemoteFile | None = None
    complete: bool = True

    @property
    def key(self) -> str:
        return hashlib.sha256(self.path.encode("utf-8")).hexdigest()

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    @property
    def total_bytes(self) -> int:
        return sum(item.size for item in self.files)

    def old_enough(self, now: datetime, seconds: int) -> bool:
        entries = self.files + ((self.marker,) if self.marker else ())
        return self.complete and bool(entries) and all((now - entry.modified_at).total_seconds() >= seconds for entry in entries)
