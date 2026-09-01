from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass, field, fields
from pathlib import Path
from urllib.parse import urlsplit

_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")


def _default_work_root() -> Path:
    local = os.getenv("LOCALAPPDATA", "").strip()
    if local:
        return Path(local) / "NCE" / "TMSLocalAgent" / "work"
    return Path.home() / "AppData" / "Local" / "NCE" / "TMSLocalAgent" / "work"


def _expand_path(value: str | Path) -> Path:
    return Path(os.path.expandvars(str(value))).expanduser().resolve()


@dataclass(frozen=True, slots=True)
class AgentConfig:
    bind_host: str = "127.0.0.1"
    port: int = 8765
    allowed_origins: tuple[str, ...] = (
        "http://127.0.0.1:5173",
        "http://localhost:5173",
    )
    allowed_hosts: tuple[str, ...] = ()
    work_root: Path = field(default_factory=_default_work_root)
    python_runtime: Path = field(
        default_factory=lambda: Path(sys.executable).resolve()
    )
    ft_package: Path = Path(
        r"F:\data_IGBT_multiple\packaging\release\ft_data_cleaner.pyz"
    )
    ft_package_sha256: str = ""
    run_timeout_seconds: int = 7200
    max_output_bytes: int = 64 * 1024 * 1024
    max_source_files: int = 100_000
    selection_ttl_seconds: int = 1800
    pairing_token_ttl_seconds: int = 8 * 60 * 60
    max_workers: int = 1

    @classmethod
    def from_json(cls, path: str | Path) -> AgentConfig:
        config_path = Path(path).expanduser().resolve()
        try:
            raw = json.loads(config_path.read_text(encoding="utf-8-sig"))
        except FileNotFoundError as exc:
            raise RuntimeError("Local Agent configuration file does not exist") from exc
        except json.JSONDecodeError as exc:
            raise RuntimeError("Local Agent configuration is not valid JSON") from exc
        if not isinstance(raw, dict):
            raise TypeError("Local Agent configuration must be a JSON object")
        allowed = {item.name for item in fields(cls)}
        unknown = set(raw) - allowed
        if unknown:
            raise RuntimeError(
                "Local Agent configuration contains unsupported fields: "
                + ", ".join(sorted(unknown))
            )
        values = dict(raw)
        for key in ("work_root", "python_runtime", "ft_package"):
            if key in values:
                values[key] = _expand_path(values[key])
        for key in ("allowed_origins", "allowed_hosts"):
            if key in values:
                if not isinstance(values[key], list):
                    raise TypeError(f"{key} must be a JSON array")
                values[key] = tuple(str(item).strip() for item in values[key])
        config = cls(**values)
        config.validate()
        return config

    def resolved_allowed_hosts(self) -> tuple[str, ...]:
        if self.allowed_hosts:
            return tuple(item.lower() for item in self.allowed_hosts)
        return (f"127.0.0.1:{self.port}", f"localhost:{self.port}")

    def validate(self) -> None:
        if self.bind_host != "127.0.0.1":
            raise RuntimeError("Local Agent must bind only to 127.0.0.1")
        if not 1 <= self.port <= 65535:
            raise RuntimeError("Local Agent port must be between 1 and 65535")
        if not self.allowed_origins:
            raise RuntimeError("At least one exact browser Origin is required")
        for origin in self.allowed_origins:
            parsed = urlsplit(origin)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.netloc
                or parsed.path not in {"", "/"}
                or parsed.query
                or parsed.fragment
                or "*" in origin
            ):
                raise RuntimeError(f"Local Agent Origin is not exact and valid: {origin}")
        loopback_hosts = {f"127.0.0.1:{self.port}", f"localhost:{self.port}"}
        for host in self.resolved_allowed_hosts():
            if not host or "*" in host or "/" in host or "\\" in host:
                raise RuntimeError("Local Agent Host allowlist contains an invalid value")
            if host not in loopback_hosts:
                raise RuntimeError(
                    "Local Agent Host allowlist must contain only loopback names "
                    "for the configured port"
                )
        if self.ft_package_sha256 and not _SHA256.fullmatch(
            self.ft_package_sha256
        ):
            raise RuntimeError("ft_package_sha256 must be empty or a 64-character SHA-256")
        for name, value in (
            ("run_timeout_seconds", self.run_timeout_seconds),
            ("max_output_bytes", self.max_output_bytes),
            ("max_source_files", self.max_source_files),
            ("selection_ttl_seconds", self.selection_ttl_seconds),
            ("pairing_token_ttl_seconds", self.pairing_token_ttl_seconds),
            ("max_workers", self.max_workers),
        ):
            if value < 1:
                raise RuntimeError(f"{name} must be positive")
        work = self.work_root.expanduser().resolve()
        if work.parent == work or len(work.parts) < 3:
            raise RuntimeError("Local Agent work_root is too broad")

    @classmethod
    def defaults(cls) -> AgentConfig:
        config = cls()
        config.validate()
        return config
