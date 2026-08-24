from __future__ import annotations

import csv
import json
from dataclasses import asdict
from pathlib import Path


def summarize_existing_cleaner_result(run_result) -> dict[str, object]:
    if run_result.test_stage == "CP":
        return _read_cp_summary(run_result)
    if run_result.test_stage == "FT":
        return _read_ft_summary(run_result)
    raise ValueError(f"unsupported Cleaner result stage: {run_result.test_stage}")


def _read_cp_summary(run_result) -> dict[str, object]:
    yield_files = [
        Path(item.path) for item in run_result.artifacts if item.role == "yield"
    ]
    cleaned_files = [
        Path(item.path) for item in run_result.artifacts if item.role == "cleaned"
    ]
    if not yield_files or not cleaned_files:
        raise RuntimeError("现有 CP Cleaner 没有生成 cleaned/yield 标准文件")
    lots: list[str] = []
    products: list[str] = []
    wafers: set[tuple[str, str]] = set()
    units = passes = 0
    for path in yield_files:
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream):
                lot = (row.get("Lot_ID") or "").strip()
                product = (row.get("Product_Name") or "").strip()
                wafer = (row.get("Wafer_ID") or "").strip()
                if lot.upper() == "ALL" or wafer.upper() == "ALL":
                    continue
                if lot and lot not in lots:
                    lots.append(lot)
                if product and product not in products:
                    products.append(product)
                if lot or wafer:
                    wafers.add((lot, wafer))
                units += int(float(row.get("Total") or 0))
                passes += int(float(row.get("Pass") or 0))
    with cleaned_files[0].open("r", encoding="utf-8-sig", newline="") as stream:
        header = next(csv.reader(stream), [])
    base = {"Lot_ID", "Wafer_ID", "Seq", "Bin", "X", "Y"}
    return {
        "data_name": "、".join(lots) or cleaned_files[0].stem,
        "product_name": "、".join(products) or None,
        "lot_id": "、".join(lots) or None,
        "wafer_count": len(wafers),
        "factory_code": run_result.factory,
        "output_uri": run_result.output_root,
        "test_item_count": sum(
            1 for name in header if name not in base and name != "CONT"
        ),
        "unit_count": units,
        "pass_count": passes,
        "yield_rate": passes / units if units else None,
        "data_type": "CP",
        "artifacts": [asdict(item) for item in run_result.artifacts],
    }


def _read_ft_summary(run_result) -> dict[str, object]:
    manifest_files = [
        Path(item.path)
        for item in run_result.artifacts
        if item.role == "scatter_manifest"
    ]
    spec_files = [
        Path(item.path) for item in run_result.artifacts if item.role == "scatter_spec"
    ]
    cleaned_files = [
        Path(item.path) for item in run_result.artifacts if item.role == "cleaned"
    ]
    if not manifest_files or not cleaned_files:
        raise RuntimeError("现有 FT Cleaner 没有生成 cleaned/scatter 标准文件")
    lots: list[str] = []
    products: list[str] = []
    parameters: list[str] = []
    unit_count = 0
    for path in manifest_files:
        manifest = json.loads(path.read_text(encoding="utf-8"))
        for lot in manifest.get("lots") or []:
            if lot and lot not in lots:
                lots.append(lot)
        for parameter in manifest.get("parameters") or []:
            if parameter and parameter not in parameters:
                parameters.append(parameter)
        unit_count += int(manifest.get("row_count") or 0)
    for path in spec_files:
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream):
                source_file = (row.get("Source_File") or "").strip()
                product = source_file.split("_")[1] if "_" in source_file else ""
                if product and product not in products:
                    products.append(product)
    return {
        "data_name": "、".join(lots) or cleaned_files[0].stem,
        "product_name": "、".join(products) or None,
        "lot_id": "、".join(lots) or None,
        "wafer_count": None,
        "factory_code": run_result.factory,
        "output_uri": run_result.output_root,
        "test_item_count": len(parameters),
        "unit_count": unit_count,
        "pass_count": None,
        "yield_rate": None,
        "data_type": "FT",
        "artifacts": [asdict(item) for item in run_result.artifacts],
    }
