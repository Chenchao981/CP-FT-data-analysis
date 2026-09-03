from pathlib import Path

from app.infrastructure.quick_result_export import QuickResultExportStore


def test_quick_result_export_creates_output_and_avoids_overwrite(
    tmp_path: Path,
) -> None:
    work_root = tmp_path / "work"
    output = tmp_path / "output"
    report = tmp_path / "PAT_001.xlsx"
    report.write_bytes(b"new-result")
    output.mkdir()
    (output / report.name).write_bytes(b"old-result")
    store = QuickResultExportStore(work_root)

    assert store.register(7, output) == output.resolve()
    exported = store.export_report(7, report)
    store.discard(7)

    assert exported == (output / "PAT_001_001.xlsx").resolve()
    assert exported.read_bytes() == b"new-result"
    assert (output / report.name).read_bytes() == b"old-result"
    assert not (work_root / "_output_requests" / "7.json").exists()
