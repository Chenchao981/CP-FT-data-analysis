from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class DatasetType(StrEnum):
    CP_DETAIL = "CP_DETAIL"
    FT_DETAIL = "FT_DETAIL"
    WAFER_SUMMARY = "WAFER_SUMMARY"
    YIELD_REPORT = "YIELD_REPORT"
    OTHER = "OTHER"


class DatasetStage(StrEnum):
    CP = "CP"
    FT = "FT"
    OTHER = "OTHER"


class CreateDatasetRequest(StrictRequest):
    dataset_code: str = Field(pattern=r"^[A-Z0-9][A-Z0-9_.-]{1,127}$")
    dataset_name: str = Field(min_length=1, max_length=300)
    dataset_type: DatasetType
    test_stage: DatasetStage
    supplier_id: int | None = Field(default=None, gt=0)
    product_id: int | None = Field(default=None, gt=0)
    project_code: str | None = Field(default=None, max_length=128)
    owner_user_id: int = Field(gt=0)

    @model_validator(mode="after")
    def stage_identity_is_complete(self) -> CreateDatasetRequest:
        if self.test_stage == DatasetStage.CP and self.supplier_id is None:
            raise ValueError(
                "CP dataset requires a wafer-fab/source identity from the file or manual input"
            )
        if self.test_stage == DatasetStage.FT and self.product_id is None:
            raise ValueError(
                "FT dataset requires a product identity from the file or manual input"
            )
        return self


class CreateDatasetVersionRequest(StrictRequest):
    input_batch_id: int = Field(gt=0)
    processing_run_ids: list[int] = Field(min_length=1, max_length=10_000)
    canonical_model_version: str = Field(
        default="1.0", pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$"
    )

    @field_validator("processing_run_ids")
    @classmethod
    def run_ids_are_unique(cls, value: list[int]) -> list[int]:
        if any(item <= 0 for item in value):
            raise ValueError("processing run identities must be positive")
        if len(value) != len(set(value)):
            raise ValueError("processing run identities must be unique")
        return value


class PublishDatasetVersionRequest(StrictRequest):
    published_by: int = Field(gt=0)


@dataclass(frozen=True, slots=True)
class DatasetRecord:
    dataset_id: int
    dataset_code: str
    dataset_name: str
    dataset_type: str
    test_stage: str
    supplier_id: int | None
    product_id: int | None
    owner_user_id: int


@dataclass(frozen=True, slots=True)
class DatasetVersionRecord:
    dataset_version_id: int
    dataset_id: int
    version_no: int
    input_batch_id: int
    canonical_model_version: str
    status: str
    is_current: bool
    run_count: int


@dataclass(frozen=True, slots=True)
class GateReason:
    code: str
    count: int
    message: str


@dataclass(frozen=True, slots=True)
class DqGateResult:
    dataset_id: int
    version_no: int
    status: str
    run_count: int
    unit_count: int
    measurement_count: int
    reasons: tuple[GateReason, ...]


@dataclass(frozen=True, slots=True)
class DatasetResultSummary:
    dataset_id: int
    dataset_code: str
    dataset_name: str
    version_no: int
    version_status: str
    is_current: bool
    run_count: int
    lot_count: int
    wafer_count: int
    unit_count: int
    pass_count: int | None
    fail_count: int | None
    yield_rate: float | None
    measurement_count: int
    bin_counts: dict[str, int]


@dataclass(frozen=True, slots=True)
class WaferOption:
    lot_id: str
    wafer_id: str


@dataclass(frozen=True, slots=True)
class WaferYieldPoint:
    lot_id: str
    wafer_id: str
    unit_count: int
    pass_count: int
    fail_count: int
    yield_rate: float


@dataclass(frozen=True, slots=True)
class BinCountPoint:
    soft_bin: str
    unit_count: int
    percent: float


@dataclass(frozen=True, slots=True)
class WaferMapPoint:
    x: int
    y: int
    soft_bin: str | None
    result: str


@dataclass(frozen=True, slots=True)
class FtParameterOption:
    name: str
    unit: str | None
    lsl: float | None
    usl: float | None
    test_condition: str | None


@dataclass(frozen=True, slots=True)
class FtParameterPoint:
    sequence: int
    lot_id: str
    source_id: str
    value: float | None
    status: str


@dataclass(frozen=True, slots=True)
class DatasetChartData:
    dataset_id: int
    version_no: int
    test_stage: str
    product_name: str | None
    selected_lot_id: str | None
    selected_wafer_id: str | None
    selected_source_id: str | None
    selected_parameter: str | None
    lot_options: tuple[str, ...]
    wafer_options: tuple[WaferOption, ...]
    source_options: tuple[str, ...]
    parameter_options: tuple[FtParameterOption, ...]
    wafer_yield: tuple[WaferYieldPoint, ...]
    bin_counts: tuple[BinCountPoint, ...]
    wafer_map: tuple[WaferMapPoint, ...]
    ft_parameter_points: tuple[FtParameterPoint, ...]
    ft_total_point_count: int
    ft_sampled: bool


class DatasetService(Protocol):
    def list_datasets(self, principal) -> tuple[DatasetRecord, ...]: ...

    def assert_dataset_access(self, dataset_id: int, principal, mode: str = "READ") -> None: ...

    def create_dataset(self, request: CreateDatasetRequest) -> DatasetRecord: ...

    def create_version(
        self, dataset_id: int, request: CreateDatasetVersionRequest
    ) -> DatasetVersionRecord: ...

    def evaluate_gate(self, dataset_id: int, version_no: int) -> DqGateResult: ...

    def publish(
        self, dataset_id: int, version_no: int, request: PublishDatasetVersionRequest
    ) -> DatasetVersionRecord: ...

    def get_summary(
        self, dataset_id: int, version_no: int
    ) -> DatasetResultSummary: ...

    def get_chart_data(
        self,
        dataset_id: int,
        version_no: int,
        lot_id: str | None = None,
        wafer_id: str | None = None,
        source_id: str | None = None,
        parameter: str | None = None,
    ) -> DatasetChartData: ...
