from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator


class EnrichmentStage(StrEnum):
    CP = "CP"
    FT = "FT"


class EnrichmentAction(StrEnum):
    FILL = "FILL"
    IGNORE = "IGNORE"


STAGE_FIELD_CODES: dict[EnrichmentStage, frozenset[str]] = {
    EnrichmentStage.CP: frozenset(
        {"SUPPLIER_CODE", "PRODUCT_CODE", "PROJECT_CODE"}
    ),
    EnrichmentStage.FT: frozenset(
        {"PRODUCT_CODE", "SUPPLIER_CODE", "LOT_ID", "PROJECT_CODE"}
    ),
}

STAGE_FIELD_CATALOG: dict[EnrichmentStage, tuple[dict[str, object], ...]] = {
    EnrichmentStage.CP: (
        {
            "field_code": "SUPPLIER_CODE",
            "label": "晶圆厂/测试来源",
            "required_for_analysis": True,
            "description": "源文件未提供时由用户选择或填写晶圆厂代码",
        },
        {
            "field_code": "PRODUCT_CODE",
            "label": "产品型号",
            "required_for_analysis": False,
            "description": "CP可选业务信息，不影响Lot/Wafer分析",
        },
        {
            "field_code": "PROJECT_CODE",
            "label": "项目代码",
            "required_for_analysis": False,
            "description": "可选项目或分析主题",
        },
    ),
    EnrichmentStage.FT: (
        {
            "field_code": "PRODUCT_CODE",
            "label": "产品型号",
            "required_for_analysis": True,
            "description": "源文件未提供时必须人工确认产品型号",
        },
        {
            "field_code": "SUPPLIER_CODE",
            "label": "封测厂/测试来源",
            "required_for_analysis": False,
            "description": "源文件存在或分析需要时补充",
        },
        {
            "field_code": "LOT_ID",
            "label": "批次号",
            "required_for_analysis": False,
            "description": "FT可选信息；源文件没有时可以明确忽略",
        },
        {
            "field_code": "PROJECT_CODE",
            "label": "项目代码",
            "required_for_analysis": False,
            "description": "可选项目或分析主题",
        },
    ),
}


class CreateFieldEnrichmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    import_batch_id: int = Field(gt=0)
    source_file_id: int | None = Field(default=None, gt=0)
    test_stage: EnrichmentStage
    field_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{1,63}$")
    action: EnrichmentAction
    value_text: str | None = Field(default=None, max_length=500)
    entered_by: int = Field(gt=0)
    reason: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def validate_stage_field_and_value(self) -> "CreateFieldEnrichmentRequest":
        if self.field_code not in STAGE_FIELD_CODES[self.test_stage]:
            raise ValueError(
                f"{self.field_code} is not an approved {self.test_stage.value} enrichment field"
            )
        if self.action == EnrichmentAction.FILL and not self.value_text:
            raise ValueError("FILL requires a non-empty value_text")
        if self.action == EnrichmentAction.IGNORE and self.value_text is not None:
            raise ValueError("IGNORE must not carry value_text")
        return self


@dataclass(frozen=True, slots=True)
class FieldEnrichmentRecord:
    enrichment_id: int
    import_batch_id: int
    source_file_id: int | None
    test_stage: str
    field_code: str
    action: str
    value_text: str | None
    entered_by: int
    reason: str
    is_current: bool


class FieldEnrichmentService(Protocol):
    def create(self, request: CreateFieldEnrichmentRequest) -> FieldEnrichmentRecord: ...

    def list_current(self, import_batch_id: int) -> tuple[FieldEnrichmentRecord, ...]: ...
