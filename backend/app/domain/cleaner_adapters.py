from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class CleanerAdapterDescriptor:
    adapter_code: str
    test_stage: str
    source_project: str
    source_root: str
    release_package: str
    entrypoint: str
    result_contract: str
    available: bool


class ExistingCleanerAdapterRegistry:
    """Describe the packaged CP/FT cleaners that TMS invokes as adapters."""

    def __init__(self, cp_root: str | None = None, ft_root: str | None = None) -> None:
        self._cp_root = Path(cp_root or os.getenv("TMS_CP_CLEANER_ROOT", r"F:\cp_data_ansys"))
        self._ft_root = Path(ft_root or os.getenv("TMS_FT_CLEANER_ROOT", r"F:\data_IGBT_multiple"))

    def descriptors(self) -> tuple[CleanerAdapterDescriptor, ...]:
        return (
            CleanerAdapterDescriptor(
                adapter_code="EXISTING_CP_CLEANER",
                test_stage="CP",
                source_project="cp_data_ansys",
                source_root=str(self._cp_root),
                release_package=str(self._cp_root / "packaging" / "release" / "app.pyz"),
                entrypoint="clean_dcp_data.process_directory via prepare_dcp_input",
                result_contract="cleaned CSV + yield CSV + spec CSV",
                available=(self._cp_root / "packaging" / "release" / "app.pyz").is_file(),
            ),
            CleanerAdapterDescriptor(
                adapter_code="EXISTING_FT_CLEANER",
                test_stage="FT",
                source_project="data_IGBT_multiple",
                source_root=str(self._ft_root),
                release_package=str(
                    self._ft_root / "packaging" / "release" / "ft_data_cleaner.pyz"
                ),
                entrypoint="factories.<factory> cleaner from ft_data_cleaner.pyz",
                result_contract="cleaned Excel + scatter data/spec/manifest when supported",
                available=(
                    self._ft_root / "packaging" / "release" / "ft_data_cleaner.pyz"
                ).is_file(),
            ),
        )

    def as_dicts(self) -> list[dict[str, object]]:
        return [asdict(item) for item in self.descriptors()]
