import json
from types import SimpleNamespace

import pytest

from local_agent import __main__ as entry
from local_agent.config import AgentConfig


@pytest.mark.parametrize(
    "check_runtime,enabled,probe_code,expected",
    [
        (False, False, 1, 0),
        (True, False, 0, 1),
        (True, True, 1, 1),
        (True, True, 0, 0),
    ],
)
def test_validation_distinguishes_configuration_from_execution(
    monkeypatch, capsys, check_runtime, enabled, probe_code, expected
):
    monkeypatch.setattr(
        "sys.argv",
        ["local_agent", "--validate-only"]
        + (["--check-runtime"] if check_runtime else []),
    )
    monkeypatch.delenv("TMS_LOCAL_AGENT_CONFIG", raising=False)
    monkeypatch.setattr(AgentConfig, "defaults", classmethod(lambda cls: AgentConfig()))
    monkeypatch.setattr(
        entry,
        "ft_jiequn_capability",
        lambda config: SimpleNamespace(
            enabled=enabled, public_dict=lambda: {"enabled": enabled}
        ),
    )
    monkeypatch.setattr(
        entry.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=probe_code),
    )
    assert entry.main() == expected
    result = json.loads(capsys.readouterr().out)
    assert result["valid"] is (expected == 0)
    if not check_runtime:
        assert result["runtime_ready"] is None
