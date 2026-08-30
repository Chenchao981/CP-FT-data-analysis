from __future__ import annotations

from pathlib import Path

from scripts.g0.repair_processing_run_current import (
    LinkedRunState,
    choose_run_repair,
)


def _run(
    run_id: int,
    *,
    source_id: int = 9,
    status: str = "PUBLISHED",
    current: bool = False,
    current_versions: tuple[int, ...] = (),
    historical_versions: tuple[int, ...] = (),
) -> LinkedRunState:
    return LinkedRunState(
        processing_run_id=run_id,
        source_file_id=source_id,
        status=status,
        is_current=current,
        current_version_ids=current_versions,
        historical_version_ids=historical_versions,
    )


def test_same_source_can_back_two_independent_current_datasets() -> None:
    first = _run(10, current=True, current_versions=(20,))
    second = _run(11, current=True, current_versions=(21,))

    assert first.source_file_id == second.source_file_id
    assert choose_run_repair(first) is None
    assert choose_run_repair(second) is None


def test_same_dataset_reprocess_supersedes_only_its_historical_run() -> None:
    previous = _run(
        10,
        current=True,
        current_versions=(),
        historical_versions=(20,),
    )
    replacement = _run(11, current=True, current_versions=(21,))

    repair = choose_run_repair(previous)

    assert repair is not None
    assert repair.processing_run_id == 10
    assert repair.target_status == "SUPERSEDED"
    assert repair.target_is_current is False
    assert choose_run_repair(replacement) is None


def test_run_linked_to_current_dataset_is_promoted_without_source_winner() -> None:
    repair = choose_run_repair(
        _run(
            10,
            status="SUPERSEDED",
            current=False,
            current_versions=(20,),
        )
    )

    assert repair is not None
    assert repair.target_status == "PUBLISHED"
    assert repair.target_is_current is True


def test_unlinked_published_run_becomes_noncurrent_history() -> None:
    repair = choose_run_repair(_run(10, current=True))

    assert repair is not None
    assert repair.target_status == "SUPERSEDED"
    assert repair.target_is_current is False


def test_repair_script_has_no_source_global_winner_policy() -> None:
    script = (
        Path(__file__).resolve().parents[2]
        / "scripts"
        / "g0"
        / "repair_processing_run_current.py"
    ).read_text(encoding="utf-8-sig")

    assert "GROUP BY pr.source_file_id" not in script
    assert "ORDER BY pr.source_file_id" not in script
    assert "winner_run_id" not in script
    assert "LATEST_PUBLISHED_CURRENT_DATASET_RUN_V1" not in script
    assert "DATASET_SCOPED_PROCESSING_RUN_CURRENT_V2" in script
