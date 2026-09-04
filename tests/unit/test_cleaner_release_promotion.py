from __future__ import annotations

import pytest

from scripts.g0.promote_cleaner_release import _parse_args


def test_promotion_defaults_to_read_only_validation() -> None:
    args = _parse_args(["--release-id", "41", "--expected-sha256", "A" * 64])

    assert args.release_id == 41
    assert args.expected_sha256 == "a" * 64
    assert args.promote is False


def test_promotion_requires_explicit_mutation_flag() -> None:
    args = _parse_args(
        [
            "--release-id",
            "41",
            "--expected-sha256",
            "b" * 64,
            "--promote",
        ]
    )

    assert args.promote is True


@pytest.mark.parametrize(
    "arguments",
    [
        ["--release-id", "0", "--expected-sha256", "a" * 64],
        ["--release-id", "1", "--expected-sha256", "not-a-sha"],
    ],
)
def test_promotion_rejects_invalid_identity(arguments: list[str]) -> None:
    with pytest.raises(SystemExit):
        _parse_args(arguments)
