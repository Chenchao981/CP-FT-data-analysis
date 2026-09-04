from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Literal

CleanerUseScope = Literal["FORMAL_IMPORT", "PERSONAL_ANALYSIS"]


@dataclass(frozen=True, slots=True)
class CleanerFormatMethod:
    """One independently maintained raw-format detector/parser method."""

    method_code: str
    display_name: str
    extensions: tuple[str, ...]
    detector_entrypoint: str
    parser_entrypoint: str
    fail_closed_notes: str


@dataclass(frozen=True, slots=True)
class CleanerCapability:
    """TMS-visible capability that binds a release contract to format methods."""

    capability_code: str
    display_name: str
    test_stage: str
    factory_code: str
    adapter_code: str
    cleaner_code: str
    input_contract_version: str
    output_contract_version: str
    use_scopes: tuple[CleanerUseScope, ...]
    format_method_codes: tuple[str, ...]


FORMAT_METHODS: tuple[CleanerFormatMethod, ...] = (
    CleanerFormatMethod(
        method_code="LION_V1_DYNAMIC_EXCEL",
        display_name="立昂微 V1 动态参数 Excel",
        extensions=(".xls", ".xlsx"),
        detector_entrypoint="lion.lion_reader.LionExcelReader.can_read",
        parser_entrypoint="lion.lion_reader.LionExcelReader",
        fail_closed_notes=(
            "按工作簿内容识别；允许参数增减，同名参数单位、上下限或条件冲突时拒绝"
        ),
    ),
    CleanerFormatMethod(
        method_code="LION_V2_PROFILED_OLE_XLS",
        display_name="立昂微 V2 已批准 OLE XLS",
        extensions=(".xls",),
        detector_entrypoint="lion.lion_v2_reader.LionV2Reader.can_read",
        parser_entrypoint="lion.lion_v2_reader.LionV2Reader",
        fail_closed_notes="只接受已批准的 V2 布局、身份、规格和 pass_bin=1",
    ),
    CleanerFormatMethod(
        method_code="DIANJI_POWERTECH_TEXT_XLS",
        display_name="电基 PowerTECH 文本伪 XLS",
        extensions=(".xls",),
        detector_entrypoint=(
            "factories.dianji.powertech_parser.is_powertech_text_file"
        ),
        parser_entrypoint="factories.dianji.powertech_parser.parse_powertech_file",
        fail_closed_notes="GB18030/Tab 文本；扩展名、内容签名和身份必须同时匹配",
    ),
    CleanerFormatMethod(
        method_code="DIANJI_POWERTECH_NATIVE_XLSX",
        display_name="电基 PowerTECH 原生 XLSX",
        extensions=(".xlsx",),
        detector_entrypoint=(
            "factories.dianji.powertech_xlsx_parser.is_powertech_xlsx_file"
        ),
        parser_entrypoint=(
            "factories.dianji.powertech_xlsx_parser.parse_powertech_xlsx_file"
        ),
        fail_closed_notes="只接受已验证产品、测试标签、布局和文件名组合",
    ),
    CleanerFormatMethod(
        method_code="DIANJI_STS8203_CSV",
        display_name="电基 STS8203 CSV",
        extensions=(".csv",),
        detector_entrypoint="factories.dianji.sts8203_parser.is_sts8203_csv_file",
        parser_entrypoint="factories.dianji.sts8203_parser.parse_sts8203_file",
        fail_closed_notes="校验 STS8203 签名、列数、单位、Lot 和产品映射",
    ),
    CleanerFormatMethod(
        method_code="DIANJI_TF_CSV",
        display_name="电基 TF CSV",
        extensions=(".csv",),
        detector_entrypoint="factories.dianji.tf_csv_parser.is_dianji_tf_csv_file",
        parser_entrypoint="factories.dianji.tf_csv_parser.parse_dianji_tf_file",
        fail_closed_notes="校验 TF 内容签名、元数据、参数和单位合同",
    ),
)


CLEANER_CAPABILITIES: tuple[CleanerCapability, ...] = (
    CleanerCapability(
        capability_code="LION_CP_STANDARD_CLEAN",
        display_name="立昂微 CP 标准清洗",
        test_stage="CP",
        factory_code="LION",
        adapter_code="LION_CP_PYZ",
        cleaner_code="LION_CP_EXISTING",
        input_contract_version="CP_EXCEL_OR_ZIP_V1",
        output_contract_version="CP_STANDARD_CSV_TRIPLET_V1",
        use_scopes=("FORMAL_IMPORT", "PERSONAL_ANALYSIS"),
        format_method_codes=(
            "LION_V1_DYNAMIC_EXCEL",
            "LION_V2_PROFILED_OLE_XLS",
        ),
    ),
    CleanerCapability(
        capability_code="DIANJI_FT_FORMAL_CLEAN",
        display_name="电基 FT 正式清洗",
        test_stage="FT",
        factory_code="DIANJI",
        adapter_code="DIANJI_FT_PYZ",
        cleaner_code="DIANJI_FT_POWERTECH_EXISTING",
        input_contract_version="DIANJI_POWERTECH_DIRECTORY_V1",
        output_contract_version="DIANJI_FT_SCATTER_V1",
        use_scopes=("FORMAL_IMPORT",),
        format_method_codes=(
            "DIANJI_POWERTECH_TEXT_XLS",
            "DIANJI_POWERTECH_NATIVE_XLSX",
        ),
    ),
    CleanerCapability(
        capability_code="DIANJI_FT_PERSONAL_PAT",
        display_name="电基 FT 个人目录 PAT",
        test_stage="FT",
        factory_code="DIANJI",
        adapter_code="DIANJI_FT_QUICK_PAT_PYZ",
        cleaner_code="DIANJI_FT_QUICK_PAT_EXISTING",
        input_contract_version="DIANJI_REGISTERED_RAW_DIRECTORY_V1",
        output_contract_version="FT_PAT_RESULT_V1",
        use_scopes=("PERSONAL_ANALYSIS",),
        format_method_codes=(
            "DIANJI_POWERTECH_TEXT_XLS",
            "DIANJI_POWERTECH_NATIVE_XLSX",
            "DIANJI_STS8203_CSV",
            "DIANJI_TF_CSV",
        ),
    ),
)


_METHODS_BY_CODE = {item.method_code: item for item in FORMAT_METHODS}
_CAPABILITIES_BY_ADAPTER = {
    item.adapter_code: item for item in CLEANER_CAPABILITIES
}


def cleaner_capability(adapter_code: str) -> CleanerCapability | None:
    return _CAPABILITIES_BY_ADAPTER.get(adapter_code.strip().upper())


def capability_format_methods(
    capability: CleanerCapability,
) -> tuple[CleanerFormatMethod, ...]:
    return tuple(_METHODS_BY_CODE[code] for code in capability.format_method_codes)


def capability_allowed_suffixes(adapter_code: str) -> frozenset[str] | None:
    capability = cleaner_capability(adapter_code)
    if capability is None:
        return None
    return frozenset(
        extension
        for method in capability_format_methods(capability)
        for extension in method.extensions
    )


def capability_catalog() -> list[dict[str, object]]:
    catalog: list[dict[str, object]] = []
    for capability in CLEANER_CAPABILITIES:
        payload = asdict(capability)
        payload["format_methods"] = [
            asdict(method) for method in capability_format_methods(capability)
        ]
        catalog.append(payload)
    return catalog


def validate_capability_contract(
    *,
    adapter_code: str,
    test_stage: str,
    factory_code: str,
    cleaner_code: str,
    input_contract_version: str,
    output_contract_version: str,
    execution_config_json: str | None = None,
) -> CleanerCapability | None:
    """Fail closed for known modular capabilities; ignore legacy adapters."""

    capability = cleaner_capability(adapter_code)
    if capability is None:
        return None
    actual = (
        test_stage.strip().upper(),
        factory_code.strip().upper(),
        cleaner_code.strip().upper(),
        input_contract_version.strip().upper(),
        output_contract_version.strip().upper(),
    )
    expected = (
        capability.test_stage,
        capability.factory_code,
        capability.cleaner_code,
        capability.input_contract_version,
        capability.output_contract_version,
    )
    if actual != expected:
        raise ValueError(
            "Cleaner Release does not match its modular capability contract: "
            f"{capability.capability_code}"
        )
    if execution_config_json:
        try:
            config = json.loads(execution_config_json)
        except json.JSONDecodeError as exc:
            raise ValueError("Cleaner Release execution config is invalid JSON") from exc
        declared_capability = config.get("capability_code")
        declared_methods = config.get("format_method_codes")
        has_declaration = (
            declared_capability is not None or declared_methods is not None
        )
        mismatched_declaration = (
            declared_capability != capability.capability_code
            or tuple(declared_methods or ()) != capability.format_method_codes
        )
        if has_declaration and mismatched_declaration:
            raise ValueError(
                "Cleaner Release format methods do not match its modular "
                f"capability contract: {capability.capability_code}"
            )
    return capability
