from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from scripts.g0.repair_processing_run_current import (
    PublishedRun,
    choose_source_repair,
)


def _run(
    run_id: int,
    *,
    minutes: int,
    current: bool = False,
    versions: tuple[int, ...] = (),
) -> PublishedRun:
    return PublishedRun(
        processing_run_id=run_id,
        source_file_id=9,
        finished_at_utc=datetime(2026, 8, 29, tzinfo=UTC)
        + timedelta(minutes=minutes),
        started_at_utc=None,
        is_current=current,
        current_version_ids=versions,
    )


def test_repair_chooses_latest_run_backing_a_current_dataset() -> None:
    repair = choose_source_repair(
        (
            _run(10, minutes=1, versions=(20,)),
            _run(11, minutes=2, versions=(21,)),
            _run(12, minutes=0),
        )
    )

    assert repair is not None
    assert repair.winner_run_id == 11
    assert repair.loser_run_ids == (12, 10)
    assert repair.superseded_current_version_ids == (20,)
    assert repair.predecessor_run_id == 10


def test_repair_is_noop_for_one_healthy_current_run() -> None:
    assert (
        choose_source_repair((_run(10, minutes=1, current=True, versions=(20,)),))
        is None
    )


def test_repair_skips_published_history_without_dataset_current() -> None:
    assert choose_source_repair((_run(10, minutes=1), _run(11, minutes=2))) is None


def test_repair_rejects_newer_published_run_without_dataset_current() -> None:
    with pytest.raises(ValueError, match="newer published run"):
        choose_source_repair(
            (_run(10, minutes=1, versions=(20,)), _run(11, minutes=2))
        )
