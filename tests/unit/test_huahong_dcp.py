from __future__ import annotations

import pytest

from app.cleaners.huahong_dcp import (
    HuaHongDcpParser,
    HuaHongFormatError,
    _parse_engineering_value,
)


def source_text(*, parameters: str | None = None, rows: str | None = None) -> str:
    params = parameters or "CONT\tIGSS0\tIGSS1\tIGSSR1\tVTH\tBVDSS1\tBVDSS2\tIDSS1\tIDSS2\tIGSS2\tIGSSR2"
    limits_u = "0.500V\t99.00uA\t100.0nA\t100.0nA\t3.900V\t140.0V\t140.0V\t100.0nA\t200.0nA\t200.0nA\t200.0nA"
    limits_l = "0V\t0A\t0A\t0A\t2.400V\t120.0V\t120.0V\t0A\t0A\t0A\t0A"
    data = rows or "1\t9\t5\t1\t1E-3\t2E-8\t3E-8\t4E-8\t3.1\t130\t131\t1E-8\t2E-8\t3E-8\t4E-8"
    blank_bias = "\t" * 14
    return "\n".join(
        [
            "Program name\tPROGRAM.jtf",
            "Lot number\tFA00-0001-000A-260820@203",
            "Wafer number\t1",
            "Date\t2026/08/20",
            "Time\t12:34:56",
            "",
            f"No.U\tX\tY\tBin\t{params}",
            f"LimitU\t\t\t\t{limits_u}",
            f"LimitL\t\t\t\t{limits_l}",
            f"Bias 1{blank_bias}",
            f"Bias 2{blank_bias}",
            f"Bias 3{blank_bias}",
            f"Bias 4{blank_bias}",
            f"Bias 5{blank_bias}",
            f"Bias 6{blank_bias}",
            data,
        ]
    )


def test_parses_approved_huahong_schema_without_changing_measurements() -> None:
    parsed = HuaHongDcpParser().parse_text(
        source_text(), source_name="FA00-0001-000A-260820@203_001.TXT"
    )
    assert parsed.business_lot_id == "FA00-0001"
    assert parsed.lot_number == "FA00-0001-000A-260820@203"
    assert parsed.wafer_number == "1"
    assert parsed.row_count == 1
    assert parsed.pass_count == 1
    assert parsed.units[0].measurements[0].raw == "1E-3"
    assert parsed.units[0].measurements[0].value_numeric == 1e-3
    assert parsed.specs[1].upper.unit_base == "A"
    assert parsed.specs[1].upper.value_base == pytest.approx(99e-6)


def test_fail_bin_is_retained_and_not_reclassified() -> None:
    rows = "1\t9\t5\t7\t1E-3\t\t\t\t3.1\t130\t131\t\t\t\t"
    parsed = HuaHongDcpParser().parse_text(
        source_text(rows=rows), source_name="FA00-0001-000A-260820@203_001.TXT"
    )
    assert parsed.units[0].soft_bin == 7
    assert parsed.units[0].overall_result == "FAIL"
    assert parsed.units[0].measurements[1].status == "NOT_TESTED"


def test_unknown_parameter_schema_fails_closed() -> None:
    with pytest.raises(HuaHongFormatError, match="not approved"):
        HuaHongDcpParser().parse_text(
            source_text(parameters="UNREVIEWED_PARAMETER"),
            source_name="FA00-0001-000A-260820@203_001.TXT",
        )


def test_duplicate_coordinate_is_rejected() -> None:
    row = "1\t9\t5\t1\t1E-3\t2E-8\t3E-8\t4E-8\t3.1\t130\t131\t1E-8\t2E-8\t3E-8\t4E-8"
    duplicate = "2\t9\t5\t1\t1E-3\t2E-8\t3E-8\t4E-8\t3.1\t130\t131\t1E-8\t2E-8\t3E-8\t4E-8"
    with pytest.raises(HuaHongFormatError, match="duplicate coordinate"):
        HuaHongDcpParser().parse_text(
            source_text(rows=f"{row}\n{duplicate}"),
            source_name="FA00-0001-000A-260820@203_001.TXT",
        )


def test_huahong_dash_unit_is_preserved_as_dimensionless() -> None:
    parsed = _parse_engineering_value("50.00-")
    assert parsed.raw == "50.00-"
    assert parsed.value_base == 50.0
    assert parsed.unit_base == "1"
