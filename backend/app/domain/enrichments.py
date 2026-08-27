from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.input_requests import normalize_lot_id


class EnrichmentStage(StrEnum):
    CP = "CP"
    FT = "FT"


class EnrichmentAction(StrEnum):
    FILL = "FILL"
    IGNORE = "IGNORE"


STAGE_FIELD_CODES: dict[EnrichmentStage, frozenset[str]] = {
    EnrichmentStage.CP: frozenset(
        {"SUPPLIER_CODE", "PRODUCT_CODE", "LOT_ID", "PROJECT_CODE"}
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
            "can_ignore": False,
            "description": "源文件未提供时由用户选择或填写晶圆厂代码",
        },
        {
            "field_code": "PRODUCT_CODE",
            "label": "产品型号",
            "required_for_analysis": False,
            "can_ignore": True,
            "description": "CP可选业务信息，不影响Lot/Wafer分析",
        },
        {
            "field_code": "LOT_ID",
            "label": "批次号",
            "required_for_analysis": True,
            "required_for_formal_import": True,
            "can_ignore": False,
            "description": "通用正式入库必须确认Lot_ID；缺失时暂停并人工补录",
        },
        {
            "field_code": "PROJECT_CODE",
            "label": "项目代码",
            "required_for_analysis": False,
            "can_ignore": True,
            "description": "可选项目或分析主题",
        },
    ),
    EnrichmentStage.FT: (
        {
            "field_code": "PRODUCT_CODE",
            "label": "产品型号",
            "required_for_analysis": True,
            "can_ignore": False,
            "description": "源文件未提供时必须人工确认产品型号",
        },
        {
            "field_code": "SUPPLIER_CODE",
            "label": "封测厂/测试来源",
            "required_for_analysis": False,
            "can_ignore": True,
            "description": "源文件存在或分析需要时补充",
        },
        {
            "field_code": "LOT_ID",
            "label": "批次号",
            "required_for_analysis": True,
            "required_for_formal_import": True,
            "can_ignore": False,
            "description": "通用正式入库必须确认Lot_ID；缺失时暂停并人工补录",
        },
        {
            "field_code": "PROJECT_CODE",
            "label": "项目代码",
            "required_for_analysis": False,
            "can_ignore": True,
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
    entered_by: int | None = Field(default=None, gt=0)
    reason: str = Field(min_length=1, max_length=500)

    @field_validator("value_text")
    @classmethod
    def normalize_lot_value(cls, value: str | None, info) -> str | None:
        if value is not None and info.data.get("field_code") == "LOT_ID":
            return normalize_lot_id(value)
        return value

    @model_validator(mode="after")
    def validate_stage_field_and_value(self) -> CreateFieldEnrichmentRequest:
        if self.field_code not in STAGE_FIELD_CODES[self.test_stage]:
            raise ValueError(
                f"{self.field_code} is not an approved {self.test_stage.value} enrichment field"
            )
        if self.action == EnrichmentAction.FILL and not self.value_text:
            raise ValueError("FILL requires a non-empty value_text")
        if self.action == EnrichmentAction.IGNORE and self.value_text is not None:
            raise ValueError("IGNORE must not carry value_text")
        field_contract = next(
            item
            for item in STAGE_FIELD_CATALOG[self.test_stage]
            if item["field_code"] == self.field_code
        )
        if self.action == EnrichmentAction.IGNORE and not field_contract["can_ignore"]:
            raise ValueError(f"{self.field_code} cannot be ignored")
        if self.field_code == "LOT_ID" and len(self.value_text or "") > 128:
            raise ValueError("LOT_ID cannot exceed 128 characters")
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
    def create(
        self, request: CreateFieldEnrichmentRequest, principal
    ) -> FieldEnrichmentRecord: ...

    def list_current(
        self, import_batch_id: int, principal
    ) -> tuple[FieldEnrichmentRecord, ...]: ...
