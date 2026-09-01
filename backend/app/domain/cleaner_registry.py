from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class CleanerRelease:
    cleaner_release_id: int
    format_profile_id: int
    test_stage: str
    factory_code: str
    format_code: str
    profile_version: str
    cleaner_code: str
    cleaner_version: str
    code_checksum: str
    artifact_uri: str
    runtime_uri: str
    entrypoint: str
    adapter_code: str
    input_contract_version: str
    output_contract_version: str
    execution_config_json: str | None
    timeout_seconds: int
    max_output_bytes: int


class CleanerRegistry(Protocol):
    def get_released(self, cleaner_release_id: int) -> CleanerRelease: ...

    def latest_released(self, test_stage: str, factory_code: str) -> CleanerRelease: ...

    def latest_released_for_contract(
        self,
        *,
        test_stage: str,
        factory_code: str,
        format_code: str,
        cleaner_code: str,
        adapter_code: str,
        input_contract_version: str,
        output_contract_version: str,
    ) -> CleanerRelease: ...
