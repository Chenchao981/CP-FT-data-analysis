from __future__ import annotations

import hashlib
import subprocess
from dataclasses import replace
from pathlib import Path

from openpyxl import Workbook

from local_agent.config import AgentConfig
from local_agent.runner import FtJiequnQuickPatRunner, ft_jiequn_capability


def test_ft_runner_pins_release_and_calls_isolated_process(
    tmp_path: Path, monkeypatch
) -> None:
    package = tmp_path / "ft_data_cleaner.pyz"
    package.write_bytes(b"published-ft-package")
    runtime = tmp_path / "python.exe"
    runtime.write_bytes(b"runtime-marker")
    package_sha = hashlib.sha256(package.read_bytes()).hexdigest()
    config = replace(
        AgentConfig.defaults(),
        work_root=tmp_path / "work",
        python_runtime=runtime,
        ft_package=package,
        ft_package_sha256=package_sha,
    )
    config.validate()
    source = tmp_path / "source"
    source.mkdir()
    (source / "one.csv").write_text("one", encoding="utf-8")
    (source / "two.csv").write_text("two", encoding="utf-8")
    observed: dict[str, object] = {}
    monkeypatch.setenv("TMS_DATABASE_URL", "must-not-cross-boundary")
    monkeypatch.setenv("JWT_SECRET", "must-not-cross-boundary")

    def fake_process(args, **kwargs):
        observed["args"] = args
        observed["env"] = kwargs["env"]
        observed["cwd"] = kwargs["cwd"]
        output = Path(kwargs["env"]["TMS_LOCAL_PAT_OUTPUT"])
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(["title"])
        sheet.append(["headers"])
        sheet.append(["VTH", 20])
        workbook.save(output / "PAT_result.xlsx")
        return subprocess.CompletedProcess(
            args,
            0,
            stdout="杰群 PAT 原始数据汇总完成: 文件=2, 解析行=1,234, 参数=1\n",
            stderr="",
        )

    tool = ft_jiequn_capability(config)
    assert tool.timeout_seconds == config.run_timeout_seconds
    assert tool.max_output_bytes == config.max_output_bytes
    result = FtJiequnQuickPatRunner(
        config, process_runner=fake_process
    ).run(
        tool=tool,
        source_path=source,
        output_root=tmp_path / "run" / "attempt-1",
        expected_source_file_count=2,
    )

    assert result.parameter_count == 1
    assert result.record_count == 1234
    assert result.report_path.name == "PAT_result.xlsx"
    assert observed["args"][1:3][0] == "-c"
    environment = observed["env"]
    assert "TMS_DATABASE_URL" not in environment
    assert "JWT_SECRET" not in environment
    assert environment["TMS_LOCAL_PAT_PACKAGE"] == str(package)
    assert Path(observed["cwd"]) == Path(environment["TMS_LOCAL_PAT_OUTPUT"])


def test_ft_capability_fails_closed_on_sha_mismatch(tmp_path: Path) -> None:
    package = tmp_path / "ft_data_cleaner.pyz"
    package.write_bytes(b"changed")
    runtime = tmp_path / "python.exe"
    runtime.write_bytes(b"runtime")
    config = replace(
        AgentConfig.defaults(),
        work_root=tmp_path / "work",
        python_runtime=runtime,
        ft_package=package,
        ft_package_sha256="0" * 64,
    )
    capability = ft_jiequn_capability(config)
    assert capability.enabled is False
    assert "SHA-256" in str(capability.disabled_reason)
