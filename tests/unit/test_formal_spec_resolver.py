from __future__ import annotations

import pytest
from app.core.errors import DomainError
from app.infrastructure.formal_spec_resolver import resolve_released_formal_spec


def _row(
    *,
    run_id: int = 101,
    lot_id: str = "LOT-A",
    spec_set_id: int | None = 7,
    version_code: str | None = "V1",
    spec_item_id: int | None = 701,
    unit_code: str = "V",
    lsl: float | None = 1.0,
    usl: float | None = 5.0,
    lower_operator: str | None = ">=",
    upper_operator: str | None = "<=",
    condition_json: str = '{"text":"VGS=0V"}',
):
    return {
        "run_id": run_id,
        "run_program_version_id": 201,
        "item_program_version_id": 201,
        "test_item_id": 301,
        "lot_id": lot_id,
        "spec_set_id": spec_set_id,
        "version_code": version_code,
        "spec_item_id": spec_item_id,
        "unit_code": unit_code,
        "lsl": lsl,
        "usl": usl,
        "lower_operator": lower_operator,
        "upper_operator": upper_operator,
        "condition_json": condition_json,
    }


def _resolve(rows):
    return resolve_released_formal_spec(
        rows,
        parameter="VTH",
        identity_unit="V",
        identity_condition="VGS=0V",
    )


def test_resolves_one_released_formal_spec_across_run_and_lot_scopes() -> None:
    result = _resolve((_row(), _row(run_id=102, lot_id="LOT-B")))

    assert result.resolved is True
    assert result.spec_set_ids == (7,)
    assert result.spec_versions == ("SPEC:7:V1",)
    assert (result.lsl, result.usl) == (1.0, 5.0)
    assert (result.lower_operator, result.upper_operator) == (">=", "<=")


@pytest.mark.parametrize(
    "rows,reason",
    [
        (
            (_row(spec_set_id=None, version_code=None, spec_item_id=None),),
            "FORMAL_SPEC_SCOPE_NOT_COVERED",
        ),
        (
            (_row(), _row(spec_set_id=8, version_code="V2", spec_item_id=801)),
            "SPEC_CONTEXT_AMBIGUOUS",
        ),
        ((_row(unit_code="A"),), "SPEC_CONTEXT_AMBIGUOUS"),
        ((_row(condition_json='{"text":"VDS=5V"}'),), "SPEC_CONTEXT_AMBIGUOUS"),
    ],
    ids=("missing", "ambiguous", "unit-mismatch", "condition-mismatch"),
)
def test_missing_ambiguous_or_incompatible_scope_returns_no_spec(rows, reason) -> None:
    result = _resolve(rows)

    assert result.status == "NO_SPEC"
    assert result.lsl is None and result.usl is None
    assert result.spec_versions == ()
    assert reason in result.reason_codes


def test_reversed_formal_limits_fail_closed() -> None:
    with pytest.raises(DomainError) as error:
        _resolve((_row(lsl=6.0, usl=5.0),))

    assert error.value.code == "ANALYSIS_SPEC_DIRECTION_INVALID"


@pytest.mark.parametrize(
    "row",
    [
        _row(lower_operator=None),
        _row(upper_operator=None),
        _row(lower_operator="=="),
        _row(upper_operator="=="),
    ],
    ids=("missing-lower", "missing-upper", "invalid-lower", "invalid-upper"),
)
def test_limit_operator_is_explicit_and_whitelisted(row) -> None:
    with pytest.raises(DomainError) as error:
        _resolve((row,))

    assert error.value.code == "ANALYSIS_SPEC_OPERATOR_INVALID"


@pytest.mark.parametrize(
    "lower_operator,upper_operator",
    [(">", "<="), (">=", "<"), (">", "<")],
)
def test_equal_limit_with_any_exclusive_boundary_fails_closed(
    lower_operator: str, upper_operator: str
) -> None:
    with pytest.raises(DomainError) as error:
        _resolve(
            (
                _row(
                    lsl=2.0,
                    usl=2.0,
                    lower_operator=lower_operator,
                    upper_operator=upper_operator,
                ),
            )
        )

    assert error.value.code == "ANALYSIS_SPEC_DIRECTION_INVALID"
