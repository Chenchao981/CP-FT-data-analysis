from __future__ import annotations

import ast
from pathlib import Path


def test_canonical_pipeline_passes_principal_to_version_read_services() -> None:
    root = Path(__file__).resolve().parents[2]
    source = (root / "scripts/g0/verify_canonical_dataset_pipeline.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    calls = {
        node.func.attr: node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "dataset_service"
        and node.func.attr in {"evaluate_gate", "get_summary"}
    }

    assert set(calls) == {"evaluate_gate", "get_summary"}
    for call in calls.values():
        assert len(call.args) == 3
        assert isinstance(call.args[2], ast.Name)
        assert call.args[2].id == "principal"
