import json

import pytest
from app.infrastructure.stage_run_details import (
    run_source_identity,
    stage_detail_values,
)


def test_ft_manufacturing_identity_is_not_the_business_lot():
    values = stage_detail_values(
        "FT",
        json.dumps(
            {
                "source_id": "source-1",
                "source_file": "source-1.xls",
                "spec_set_id": 7,
                "source_identity": {
                    "manufacturing_lot": "M01234",
                    "metadata_lot": "2026-W36",
                    "test_tag": "FINAL",
                    "source_segment": None,
                },
            }
        ),
    )
    assert values["manufacturing_lot"] == "M01234"
    assert values["metadata_lot"] == "2026-W36"
    assert values["source_segment"] is None
    assert values["source_spec_set_id"] == 7
    assert "lot_id" not in values


def test_cp_unknown_business_identity_is_not_guessed():
    values = stage_detail_values("CP", '{"raw_wafer_id":"001","source_group":"SOURCE"}')
    assert values == {
        "raw_wafer_id": "001",
        "source_group": "SOURCE",
        "source_lot_run": None,
        "source_spec_set_id": None,
    }


def test_legacy_ft_source_filename_remains_unknown():
    assert stage_detail_values("FT", '{"source_id":"known"}')["source_file"] is None


@pytest.mark.parametrize(
    "stage,payload",
    [
        ("FT", {}),
        ("CP", []),
        ("CP", {"spec_set_id": True}),
        ("CP", {"spec_set_id": -1}),
        ("CP", {"raw_wafer_id": 1}),
        ("CP", {"raw_wafer_id": "😀" * 33}),
        ("CP", {"source_group": " "}),
        ("FT", {"source_id": "a", "source_identity": {"source_id": "b"}}),
        ("FT", {"source_id": "a", "source_identity": []}),
        ("FT", {"source_id": "a", "source_identity": False}),
        ("WAT", {}),
    ],
)
def test_invalid_or_conflicting_fields_fail_closed(stage, payload):
    with pytest.raises(ValueError):
        stage_detail_values(stage, json.dumps(payload))


def test_relational_source_is_authoritative_over_metadata_snapshot():
    assert (
        run_source_identity(
            {
                "run_id": 12,
                "source_id": "relational",
                "metadata_json": '{"source_id":"old"}',
            }
        )
        == "relational"
    )
    assert (
        run_source_identity(
            {"run_id": 12, "source_id": None, "metadata_json": '{"source_id":"old"}'}
        )
        == "RUN-12"
    )
