from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from app.cleaners.huahong_archive import prepare_huahong_input
from app.cleaners.huahong_dcp import HuaHongDcpFile, HuaHongDcpParser, summarize_files


_PRODUCT_DIRECTORY = re.compile(r"^(NCE[^_]+)_")


@dataclass(frozen=True, slots=True)
class DataQualityIssue:
    code: str
    severity: str
    entity_key: str
    message: str


@dataclass(frozen=True, slots=True)
class HuaHongBatchInspection:
    files: tuple[HuaHongDcpFile, ...]
    product_candidates: dict[str, str]
    issues: tuple[DataQualityIssue, ...]

    @property
    def status(self) -> str:
        return "BLOCKED" if any(item.severity == "BLOCKER" for item in self.issues) else "PASS"

    @property
    def summary(self) -> dict[str, object]:
        result = summarize_files(self.files)
        result["status"] = self.status
        result["product_candidate_count"] = len(set(self.product_candidates.values()))
        result["issue_counts"] = {
            severity: sum(item.severity == severity for item in self.issues)
            for severity in ("BLOCKER", "ERROR", "WARNING", "INFO")
        }
        return result


def _spec_signature(file: HuaHongDcpFile) -> str:
    values: list[str] = []
    for spec in file.specs:
        values.extend(
            [
                spec.name,
                spec.lower.raw,
                spec.upper.raw,
                *spec.bias_raw,
            ]
        )
    return hashlib.sha256("\x1f".join(values).encode("utf-8")).hexdigest()


def _product_candidate(path: Path, root: Path) -> str | None:
    current = path.parent
    while current != root.parent:
        match = _PRODUCT_DIRECTORY.match(current.name)
        if match:
            return match.group(1)
        if current == root:
            break
        current = current.parent
    return None


class HuaHongBatchInspector:
    def __init__(self, parser: HuaHongDcpParser | None = None) -> None:
        self._parser = parser or HuaHongDcpParser()

    def inspect_directory(self, root: str | Path) -> HuaHongBatchInspection:
        source_root = Path(root)
        paths = sorted(source_root.rglob("*.TXT"))
        if not paths:
            raise ValueError("no HuaHong TXT files were found")
        return self._inspect_paths(paths, source_root=source_root)

    def inspect_input(self, source: str | Path) -> HuaHongBatchInspection:
        """Inspect one TXT/ZIP/7z source through the safe input boundary."""

        with prepare_huahong_input(source) as prepared:
            match = _PRODUCT_DIRECTORY.match(Path(prepared.container_name).stem)
            fallback_candidate = match.group(1) if match else None
            return self._inspect_paths(
                list(prepared.txt_files),
                source_root=prepared.root,
                fallback_candidate=fallback_candidate,
            )

    def _inspect_paths(
        self,
        paths: list[Path],
        *,
        source_root: Path,
        fallback_candidate: str | None = None,
    ) -> HuaHongBatchInspection:

        files: list[HuaHongDcpFile] = []
        product_candidates: dict[str, str] = {}
        path_by_hash: dict[str, Path] = {}
        for path in paths:
            parsed = self._parser.parse_path(path, include_units=False)
            files.append(parsed)
            path_by_hash[parsed.source_sha256] = path
            candidate = _product_candidate(path, source_root) or fallback_candidate
            if candidate:
                product_candidates[parsed.business_lot_id] = candidate

        issues: list[DataQualityIssue] = []
        by_lot: dict[str, list[HuaHongDcpFile]] = {}
        for file in files:
            by_lot.setdefault(file.business_lot_id, []).append(file)

        for lot_number, lot_files in sorted(by_lot.items()):
            products = {
                candidate
                for item in lot_files
                if (
                    candidate := (
                        _product_candidate(path_by_hash[item.source_sha256], source_root)
                        or fallback_candidate
                    )
                )
            }
            if len(products) > 1:
                issues.append(
                    DataQualityIssue(
                        code="PRODUCT_CANDIDATE_AMBIGUOUS",
                        severity="WARNING",
                        entity_key=lot_number,
                        message="Multiple optional product candidates were found; CP analysis remains Lot-scoped.",
                    )
                )
            elif products:
                product_candidates[lot_number] = next(iter(products))

            schemas = {item.schema_id for item in lot_files}
            if len(schemas) > 1:
                issues.append(
                    DataQualityIssue(
                        code="LOT_SCHEMA_MIXED",
                        severity="BLOCKER",
                        entity_key=lot_number,
                        message="A source Lot contains multiple approved parameter schemas.",
                    )
                )
            programs = {item.program_name for item in lot_files}
            if len(programs) > 1:
                issues.append(
                    DataQualityIssue(
                        code="LOT_PROGRAM_MIXED",
                        severity="BLOCKER",
                        entity_key=lot_number,
                        message="A source Lot contains multiple test program names.",
                    )
                )
            specs = {_spec_signature(item) for item in lot_files}
            if len(specs) > 1:
                issues.append(
                    DataQualityIssue(
                        code="LOT_SPEC_MIXED",
                        severity="BLOCKER",
                        entity_key=lot_number,
                        message="A source Lot contains inconsistent limits or test conditions.",
                    )
                )

            wafer_keys: set[tuple[str, str]] = set()
            for item in lot_files:
                key = (item.business_lot_id, str(int(item.wafer_number)))
                if key in wafer_keys:
                    issues.append(
                        DataQualityIssue(
                            code="DUPLICATE_LOT_WAFER",
                            severity="BLOCKER",
                            entity_key=f"{lot_number}/{key[1]}",
                            message="The same source Lot and Wafer occurs more than once.",
                        )
                    )
                wafer_keys.add(key)

        return HuaHongBatchInspection(
            files=tuple(files),
            product_candidates=product_candidates,
            issues=tuple(issues),
        )
