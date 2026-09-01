from __future__ import annotations

import csv
from dataclasses import asdict
from pathlib import Path

from app.infrastructure.ft_xlsx_scatter_writer import (
    summarize_ft_xlsx_scatter_identity,
)


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
                units += int(float(row.get("Total") or row.get("Gross_die") or 0))
                passes += int(float(row.get("Pass") or row.get("Good_die") or 0))
    with cleaned_files[0].open("r", encoding="utf-8-sig", newline="") as stream:
        header = next(csv.reader(stream), [])
    base = {
        "Lot_ID", "LotID", "Wafer_ID", "WaferID", "Seq", "Bin", "X", "Y",
        "CONT", "SITE_NUM", "T_TIME", "TEST_NUM",
    }
    has_business_lot = run_result.factory.strip().lower() != "guoyu"
    return {
        "data_name": "、".join(lots) or cleaned_files[0].stem,
        "product_name": "、".join(products) or None,
        "lot_id": ("、".join(lots) or None) if has_business_lot else None,
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
    identity = summarize_ft_xlsx_scatter_identity(run_result.artifacts)
    expected_factory = {
        "riyuexin": "RIYUEXIN",
        "日月新": "RIYUEXIN",
        "riyueguang": "RIYUEGUANG",
        "日月光": "RIYUEGUANG",
        "ase": "RIYUEGUANG",
        "dianji": "DIANJI",
        "电基": "DIANJI",
    }.get(str(run_result.factory).strip().casefold())
    if expected_factory is None or identity.factory_code != expected_factory:
        raise RuntimeError("FT Cleaner 运行厂家与 manifest factory_code 不一致")
    return {
        "data_name": "、".join(identity.lots) or Path(identity.cleaned_file).stem,
        "product_name": identity.product_name,
        "lot_id": "、".join(identity.lots) or None,
        "wafer_count": None,
        "factory_code": identity.factory_code,
        "output_uri": run_result.output_root,
        "test_item_count": len(identity.parameters),
        "unit_count": identity.row_count,
        "pass_count": None,
        "yield_rate": None,
        "data_type": "FT",
        "artifacts": [asdict(item) for item in run_result.artifacts],
    }
