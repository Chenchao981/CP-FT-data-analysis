from __future__ import annotations

import hashlib
import json
import time
from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient

from local_agent.app import create_app
from local_agent.config import AgentConfig
from local_agent.models import FT_JIEQUN_TOOL_CODE, ToolRunResult

TOKEN = "unit-test-pairing-token-with-more-than-32-characters"
ORIGIN = "http://127.0.0.1:5173"


class FakeRunner:
    def run(
        self,
        *,
        tool,
        source_path: Path,
        output_root: Path,
        expected_source_file_count: int,
    ) -> ToolRunResult:
        assert tool.tool_code == FT_JIEQUN_TOOL_CODE
        assert source_path.is_dir()
        assert expected_source_file_count == 2
        output_root.mkdir(parents=True, exist_ok=False)
        result = output_root / "PAT_local.xlsx"
        result.write_bytes(b"local-result-only")
        return ToolRunResult(
            report_path=result,
            parameter_count=5,
            record_count=1234,
            elapsed_seconds=0.25,
            stdout_tail="internal-only",
        )


def _fixture(tmp_path: Path, *, pairing_token_ttl_seconds: int = 28_800):
    source = tmp_path / "source-private"
    source.mkdir()
    (source / "one.csv").write_text("one", encoding="utf-8")
    (source / "two.csv").write_text("two", encoding="utf-8")
    package = tmp_path / "ft_data_cleaner.pyz"
    package.write_bytes(b"release")
    runtime = tmp_path / "python.exe"
    runtime.write_bytes(b"runtime")
    config = replace(
        AgentConfig.defaults(),
        port=8765,
        allowed_origins=(ORIGIN, "http://localhost:5173"),
        allowed_hosts=("127.0.0.1:8765", "localhost:8765"),
        work_root=tmp_path / "agent-work",
        python_runtime=runtime,
        ft_package=package,
        ft_package_sha256=hashlib.sha256(package.read_bytes()).hexdigest(),
        pairing_token_ttl_seconds=pairing_token_ttl_seconds,
    )
    app = create_app(
        config,
        pairing_token=TOKEN,
        selector=lambda: source,
        runner=FakeRunner(),
    )
    return source, app


def _headers(token: str = TOKEN) -> dict[str, str]:
    return {"Origin": ORIGIN, "X-TMS-Agent-Token": token}


def test_security_and_capability_gate(tmp_path: Path) -> None:
    source, app = _fixture(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        health = client.get("/v1/health")
        assert health.status_code == 200
        assert health.json()["bind_scope"] == "loopback-only"
        assert health.json()["pairing_required"] is True
        assert health.json()["pairing_token_ttl_seconds"] == 28_800

        missing_origin = client.get(
            "/v1/tools", headers={"X-TMS-Agent-Token": TOKEN}
        )
        assert missing_origin.status_code == 403
        assert client.get("/v1/tools", headers=_headers("wrong-token")).status_code == 401
        assert client.get(
            "/v1/tools",
            headers={**_headers(), "Host": "malicious.example"},
        ).status_code == 400
        assert client.get(
            "/v1/tools",
            headers={**_headers(), "Origin": "https://malicious.example"},
        ).status_code == 403

        tools = client.get("/v1/tools", headers=_headers()).json()["tools"]
        ft = next(item for item in tools if item["test_stage"] == "FT")
        assert ft["timeout_seconds"] == 7200
        assert ft["max_output_bytes"] == 64 * 1024 * 1024
        cp = next(item for item in tools if item["test_stage"] == "CP")
        assert cp["enabled"] is False
        assert "尚无已批准" in cp["disabled_reason"]

        leaked = client.post(
            "/v1/select-folder",
            headers=_headers(),
            json={"path": str(source)},
        )
        assert leaked.status_code == 422
        assert str(source) not in leaked.text

        preflight = client.options(
            "/v1/tools",
            headers={
                "Origin": ORIGIN,
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "X-TMS-Agent-Token",
                "Access-Control-Request-Private-Network": "true",
            },
        )
        assert preflight.status_code == 204
        assert preflight.headers["access-control-allow-private-network"] == "true"


def test_full_local_run_never_returns_absolute_source_path(tmp_path: Path) -> None:
    source, app = _fixture(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        selected = client.post(
            "/v1/select-folder", headers=_headers(), json={}
        )
        assert selected.status_code == 200
        selection = selected.json()
        assert set(selection) == {"selection_id", "source_label"}
        assert selection["source_label"] == source.name

        preview = client.post(
            f"/v1/selections/{selection['selection_id']}/preview",
            headers=_headers(),
            json={"tool_code": FT_JIEQUN_TOOL_CODE},
        )
        assert preview.status_code == 200
        manifest = preview.json()
        assert manifest["mode"] == "LOCAL_PATH_SIZE_MTIME_V1"
        assert manifest["file_count"] == 2

        started = client.post(
            f"/v1/selections/{selection['selection_id']}/runs",
            headers=_headers(),
            json={
                "tool_code": FT_JIEQUN_TOOL_CODE,
                "confirmed_manifest_sha256": manifest["sha256"],
            },
        )
        assert started.status_code == 200
        run_id = started.json()["run_id"]
        status = None
        for _ in range(100):
            status = client.get(f"/v1/runs/{run_id}", headers=_headers())
            if status.json()["status"] in {"SUCCESS", "FAILED"}:
                break
            time.sleep(0.01)
        assert status is not None
        assert status.json()["status"] == "SUCCESS"
        assert status.json()["parameter_count"] == 5
        assert status.json()["record_count"] == 1234
        assert status.json()["elapsed_seconds"] == 0.25
        assert status.json()["error_code"] is None

        receipt_response = client.get(
            f"/v1/runs/{run_id}/receipt", headers=_headers()
        )
        assert receipt_response.status_code == 200
        receipt = receipt_response.json()
        assert set(receipt) == {
            "contract_version",
            "tool_code",
            "analysis_type",
            "test_stage",
            "factory_code",
            "release_sha256",
            "source_label",
            "manifest",
            "summary",
            "result",
        }
        assert receipt["contract_version"] == "TMS_LOCAL_RESULT_V1"
        assert receipt["manifest"]["sha256"] == manifest["sha256"]
        assert receipt["tool_code"] == FT_JIEQUN_TOOL_CODE
        assert receipt["summary"]["parameter_count"] == 5
        assert receipt["result"]["sha256"]
        assert str(source.resolve()) not in json.dumps(receipt, ensure_ascii=False)
        assert str(source.resolve()) not in status.text

        result = client.get(f"/v1/runs/{run_id}/result", headers=_headers())
        assert result.status_code == 200
        assert result.content == b"local-result-only"
        assert "PAT_local.xlsx" in result.headers["content-disposition"]

        deleted = client.delete(f"/v1/runs/{run_id}", headers=_headers())
        assert deleted.status_code == 204
        assert not (app.state.agent_config.work_root / run_id).exists()
        assert client.get(f"/v1/runs/{run_id}", headers=_headers()).status_code == 404


def test_pairing_token_expires(tmp_path: Path) -> None:
    _, app = _fixture(tmp_path, pairing_token_ttl_seconds=1)
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        assert client.get("/v1/tools", headers=_headers()).status_code == 200
        time.sleep(1.05)
        expired = client.get("/v1/tools", headers=_headers())
        assert expired.status_code == 401
        assert expired.json()["error"]["code"] == "LOCAL_TOKEN_EXPIRED"


def test_run_requires_current_confirmed_manifest(tmp_path: Path) -> None:
    source, app = _fixture(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        selection_id = client.post(
            "/v1/select-folder", headers=_headers(), json={}
        ).json()["selection_id"]
        preview = client.post(
            f"/v1/selections/{selection_id}/preview",
            headers=_headers(),
            json={"tool_code": FT_JIEQUN_TOOL_CODE},
        ).json()
        mismatch = client.post(
            f"/v1/selections/{selection_id}/runs",
            headers=_headers(),
            json={
                "tool_code": FT_JIEQUN_TOOL_CODE,
                "confirmed_manifest_sha256": "0" * 64,
            },
        )
        assert mismatch.status_code == 409
        assert mismatch.json()["error"]["code"] == (
            "LOCAL_MANIFEST_CONFIRMATION_MISMATCH"
        )

        (source / "one.csv").write_text("changed", encoding="utf-8")
        changed = client.post(
            f"/v1/selections/{selection_id}/runs",
            headers=_headers(),
            json={
                "tool_code": FT_JIEQUN_TOOL_CODE,
                "confirmed_manifest_sha256": preview["sha256"],
            },
        )
        assert changed.status_code == 409
        assert changed.json()["error"]["code"] == "LOCAL_SOURCE_CHANGED"
