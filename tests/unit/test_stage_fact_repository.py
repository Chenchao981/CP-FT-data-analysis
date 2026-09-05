import pytest
from app.infrastructure.stage_fact_repository import (
    fact_table,
    insert_measurements,
    insert_units,
)


class NoDatabase:
    def __init__(self, in_transaction=True):
        self.active = in_transaction

    def in_transaction(self):
        return self.active

    def execute(self, *args, **kwargs):
        raise AssertionError("invalid input reached SQL")


@pytest.mark.parametrize(
    "stage,kind,expected",
    [
        ("CP", "unit", "test.cp_die"),
        ("FT", "unit", "test.ft_device"),
        ("CP", "measurement", "test.cp_measurement"),
        ("FT", "measurement", "test.ft_measurement"),
    ],
)
def test_stage_selection_has_one_physical_target(stage, kind, expected):
    assert fact_table(stage, kind) == expected


@pytest.mark.parametrize(
    "stage,rows",
    [
        ("FT", [{"run_id": 1, "logical_unit_key": "a", "x_coord": None}]),
        ("CP", [{"run_id": 1, "logical_unit_key": "a", "unit_id": 123}]),
        ("CP", [{"run_id": 1}]),
        (
            "CP",
            [
                {"run_id": 1, "logical_unit_key": "a"},
                {"run_id": 1, "logical_unit_key": "b", "unit_sequence": 2},
            ],
        ),
        ("OTHER", []),
    ],
)
def test_invalid_unit_identity_is_rejected_before_allocation(stage, rows):
    with pytest.raises(ValueError):
        insert_units(NoDatabase(), stage, rows)


def test_caller_transaction_is_required():
    with pytest.raises(ValueError, match="transaction"):
        insert_units(NoDatabase(False), "CP", [{"run_id": 1, "logical_unit_key": "a"}])


def test_measurement_identity_cannot_be_supplied_by_caller():
    with pytest.raises(ValueError):
        insert_measurements(
            NoDatabase(),
            "CP",
            [
                {
                    "unit_id": 1,
                    "test_item_id": 1,
                    "measurement_status": "MEASURED",
                    "measurement_id": 123,
                }
            ],
        )


def test_empty_batches_need_no_database_access():
    assert insert_units(NoDatabase(), "CP", []) == ()
    assert insert_measurements(NoDatabase(), "FT", []) == ()
