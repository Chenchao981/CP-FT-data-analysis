from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.errors import DomainError


CODE_PATTERN = r"^[A-Z][A-Z0-9_]{1,127}$"
VERSION_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class TestStage(StrEnum):
    CP = "CP"
    FT = "FT"
    WAT = "WAT"
    SLT = "SLT"
    QA = "QA"
    ORT = "ORT"
    SUMMARY = "SUMMARY"
    OTHER = "OTHER"


class FileRole(StrEnum):
    DETAIL = "DETAIL"
    YIELD = "YIELD"
    SPEC = "SPEC"
    PAT = "PAT"
    EXPORT = "EXPORT"
    REPORT = "REPORT"
    MANIFEST = "MANIFEST"
    OTHER = "OTHER"


class SignatureRuleKind(StrEnum):
    FILE_EXTENSION = "FILE_EXTENSION"
    MAGIC_BYTES = "MAGIC_BYTES"
    ARCHIVE_MEMBER = "ARCHIVE_MEMBER"
    SHEET_NAME = "SHEET_NAME"
    HEADER_TOKEN = "HEADER_TOKEN"
    FILE_NAME_PATTERN = "FILE_NAME_PATTERN"


class SignatureOperator(StrEnum):
    EQUALS = "EQUALS"
    CONTAINS = "CONTAINS"
    MATCHES = "MATCHES"
    STARTS_WITH = "STARTS_WITH"


class SignatureRule(StrictModel):
    kind: SignatureRuleKind
    operator: SignatureOperator
    value: str = Field(min_length=1, max_length=500)
    case_sensitive: bool = False


class FormatSignatureContract(StrictModel):
    schema_version: str = Field(default="1.0", pattern=r"^1\.0$")
    match_mode: str = Field(default="ALL", pattern=r"^(ALL|ANY)$")
    ambiguity_policy: str = Field(default="BLOCK", pattern=r"^BLOCK$")
    rules: list[SignatureRule] = Field(min_length=1, max_length=50)


class FileRoleSpec(StrictModel):
    role: FileRole
    required: bool = True
    min_files: int = Field(default=1, ge=0, le=1000)
    max_files: int = Field(default=1, ge=1, le=1000)

    @model_validator(mode="after")
    def validate_range(self) -> "FileRoleSpec":
        if self.min_files > self.max_files:
            raise ValueError("min_files must not exceed max_files")
        if self.required and self.min_files == 0:
            raise ValueError("required role must have min_files >= 1")
        return self


class FileRoleContract(StrictModel):
    schema_version: str = Field(default="1.0", pattern=r"^1\.0$")
    roles: list[FileRoleSpec] = Field(min_length=1, max_length=20)

    @field_validator("roles")
    @classmethod
    def roles_are_unique(cls, value: list[FileRoleSpec]) -> list[FileRoleSpec]:
        roles = [item.role for item in value]
        if len(set(roles)) != len(roles):
            raise ValueError("file roles must be unique")
        if not any(item.required for item in value):
            raise ValueError("at least one file role must be required")
        return value


class FormatProfileDraft(StrictModel):
    supplier_id: int | None = Field(default=None, gt=0)
    test_stage: TestStage
    format_code: str = Field(pattern=CODE_PATTERN)
    profile_version: str = Field(pattern=VERSION_PATTERN)
    signature: FormatSignatureContract
    file_role_contract: FileRoleContract


class CleanerReleaseDraft(StrictModel):
    format_profile_id: int = Field(gt=0)
    cleaner_code: str = Field(pattern=CODE_PATTERN)
    cleaner_version: str = Field(pattern=VERSION_PATTERN)
    code_checksum: str = Field(pattern=r"^[0-9a-fA-F]{64}$")
    artifact_uri: str | None = Field(default=None, max_length=1000)

    @field_validator("code_checksum")
    @classmethod
    def normalize_checksum(cls, value: str) -> str:
        return value.lower()


class GovernedStatus(StrEnum):
    DRAFT = "DRAFT"
    RELEASED = "RELEASED"
    OBSOLETE = "OBSOLETE"


GOVERNED_TRANSITIONS: dict[GovernedStatus, set[GovernedStatus]] = {
    GovernedStatus.DRAFT: {GovernedStatus.RELEASED},
    GovernedStatus.RELEASED: {GovernedStatus.OBSOLETE},
    GovernedStatus.OBSOLETE: set(),
}


def transition_governed_status(
    current: GovernedStatus, target: GovernedStatus
) -> GovernedStatus:
    if target not in GOVERNED_TRANSITIONS[current]:
        raise DomainError(
            code="INVALID_GOVERNANCE_TRANSITION",
            message=f"cannot transition {current} to {target}",
            status_code=409,
        )
    return target
