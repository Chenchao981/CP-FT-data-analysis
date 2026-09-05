from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.core.errors import DomainError
from app.domain.analytics import AnalyticsContextRequest
from app.domain.analytics_export_analysis import (
    analytics_export_analysis_parameters,
    resolve_analytics_export_analysis_config,
)
from app.domain.analytics_export_worker import AnalyticsExportWorkItem
from app.domain.analytics_exports import AnalyticsExportFormat, AnalyticsExportScope
from app.domain.saved_analyses import (
    saved_analysis_hashes,
    validate_analysis_presentation_config,
)
from app.infrastructure.analytics_export_files import AnalyticsExportPathPolicy
from app.infrastructure.analytics_export_renderer import AnalyticsExportRenderer
from app.infrastructure.database import check_database, get_engine
from app.infrastructure.sql_analytics_export_content import (
    SqlAnalyticsExportContentSource,
)
from app.infrastructure.sql_analytics_export_service import SqlAnalyticsExportService


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read-only real-SQL smoke for analytics export content and CSV rendering"
    )
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--test-stage", choices=("CP", "FT"))
    parser.add_argument(
        "--overview", action="store_true", help="render the overview report contract"
    )
    parser.add_argument(
        "--template",
        choices=(
            "ANALYTICS_DETAIL",
            "PARAMETER_DETAIL",
            "ANALYTICS_OVERVIEW",
            "PARAMETER_ANALYSIS",
            "PARAMETER_RELATIONSHIP",
            "SPATIAL_ANALYSIS",
            "FT_QUALITY",
            "WAFER_SUMMARY",
        ),
        help="render one registered server template; --overview remains a compatibility alias",
    )
    parser.add_argument(
        "--format",
        choices=("CSV", "XLSX", "HTML", "PDF", "PNG"),
        default="CSV",
        help="artifact format; XLSX/HTML/PDF/PNG require a REPORT template",
    )
    parser.add_argument(
        "--rule-code",
        help="exact approved Rule code; required only by FT_QUALITY smoke",
    )
    parser.add_argument(
        "--rule-version",
        help="exact approved Rule version; required only by FT_QUALITY smoke",
    )
    parser.add_argument(
        "--expect-rule-gate",
        action="store_true",
        help="treat FT_QUALITY ANALYSIS_RULE_NOT_APPROVED as the expected fail-closed result",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.output_root.is_absolute():
        raise RuntimeError("--output-root must be absolute")
    if args.overview and args.template is not None:
        raise RuntimeError("use either --overview or --template")
    template = args.template or (
        "ANALYTICS_OVERVIEW" if args.overview else "ANALYTICS_DETAIL"
    )
    report_templates = {
        "ANALYTICS_OVERVIEW",
        "PARAMETER_ANALYSIS",
        "PARAMETER_RELATIONSHIP",
        "SPATIAL_ANALYSIS",
        "FT_QUALITY",
        "WAFER_SUMMARY",
    }
    is_report = template in report_templates
    if args.format in {"XLSX", "HTML", "PDF", "PNG"} and not is_report:
        raise RuntimeError("XLSX/HTML/PDF/PNG smoke requires a REPORT template")
    required_stage = (
        "CP"
        if template in {"SPATIAL_ANALYSIS", "WAFER_SUMMARY"}
        else "FT"
        if template == "FT_QUALITY"
        else args.test_stage
    )
    database = check_database()
    if database["schema_revision"] != "sql2014_0029":
        raise RuntimeError("analytics export smoke requires sql2014_0029")
    engine = get_engine()
    with engine.connect() as connection:
        row = (
            connection.execute(
                text(
                    "SELECT TOP (1) dv.dataset_version_id,dv.dataset_id,dv.version_no,"
                    "d.test_stage,d.owner_user_id,d.supplier_id,d.product_id,"
                    "dv.spec_set_id,ss.version_code AS spec_version "
                    "FROM dataset.dataset_version dv "
                    "JOIN dataset.dataset d ON d.dataset_id=dv.dataset_id "
                    "LEFT JOIN mdm.spec_set ss ON ss.spec_set_id=dv.spec_set_id "
                    "WHERE dv.status='PUBLISHED' AND dv.is_current=1 "
                    "AND d.test_stage IN ('CP','FT') "
                    "AND (:test_stage IS NULL OR d.test_stage=:test_stage) "
                    "AND EXISTS(SELECT 1 FROM dataset.dataset_version_run dvr "
                    "JOIN test.test_run tr ON tr.processing_run_id=dvr.processing_run_id "
                    "JOIN test.unit_result ur ON ur.run_id=tr.run_id "
                    "WHERE dvr.dataset_version_id=dv.dataset_version_id) "
                    "ORDER BY CASE d.test_stage WHEN 'CP' THEN 0 ELSE 1 END,"
                    "dv.dataset_version_id"
                ),
                {"test_stage": required_stage},
            )
            .mappings()
            .one_or_none()
        )
    if row is None:
        raise RuntimeError("no Current Published CP/FT Dataset Version has unit data")
    required_parameters = {
        "PARAMETER_DETAIL": 1,
        "PARAMETER_ANALYSIS": 1,
        "PARAMETER_RELATIONSHIP": 2,
        "SPATIAL_ANALYSIS": 1,
    }.get(template, 0)
    selected_parameters: list[str] = []
    if required_parameters:
        with engine.connect() as connection:
            selected_parameters = [
                str(item[0])
                for item in connection.execute(
                    text(
                        "SELECT DISTINCT TOP (:parameter_count) tid.raw_item_name "
                        "FROM dataset.dataset_version_run dvr "
                        "JOIN test.test_run tr ON tr.processing_run_id=dvr.processing_run_id "
                        "JOIN mdm.test_item_definition tid "
                        "ON tid.program_version_id=tr.program_version_id "
                        "WHERE dvr.dataset_version_id=:dataset_version_id "
                        "AND tid.is_analysis_parameter=1 "
                        "AND tid.raw_item_name IS NOT NULL ORDER BY tid.raw_item_name"
                    ),
                    {
                        "parameter_count": required_parameters,
                        "dataset_version_id": int(row["dataset_version_id"]),
                    },
                )
            ]
        if len(selected_parameters) != required_parameters:
            raise RuntimeError("selected Dataset does not provide required parameters")
    filters: dict[str, list[str]] = {}
    if template == "SPATIAL_ANALYSIS":
        with engine.connect() as connection:
            wafer = (
                connection.execute(
                    text(
                        "SELECT TOP (1) tr.lot_id,COALESCE(ur.wafer_id,tr.wafer_id) AS wafer_id "
                        "FROM dataset.dataset_version_run dvr "
                        "JOIN test.test_run tr ON tr.processing_run_id=dvr.processing_run_id "
                        "JOIN test.unit_result ur ON ur.run_id=tr.run_id "
                        "WHERE dvr.dataset_version_id=:dataset_version_id "
                        "AND COALESCE(ur.wafer_id,tr.wafer_id) IS NOT NULL "
                        "AND ur.x_coord IS NOT NULL AND ur.y_coord IS NOT NULL "
                        "GROUP BY tr.lot_id,COALESCE(ur.wafer_id,tr.wafer_id) "
                        "ORDER BY tr.lot_id,COALESCE(ur.wafer_id,tr.wafer_id)"
                    ),
                    {"dataset_version_id": int(row["dataset_version_id"])},
                )
                .mappings()
                .one_or_none()
            )
        if wafer is None:
            raise RuntimeError("selected CP Dataset has no coordinate-complete wafer")
        filters = {
            "lot_ids": [str(wafer["lot_id"])],
            "wafer_ids": [str(wafer["wafer_id"])],
        }
    context = AnalyticsContextRequest.model_validate(
        {
            "datasets": [
                {
                    "dataset_id": int(row["dataset_id"]),
                    "version_no": int(row["version_no"]),
                }
            ],
            "filters": filters,
            "parameters": selected_parameters,
        }
    )
    hashes = saved_analysis_hashes(context)
    analysis_config_payload = None
    if template == "ANALYTICS_OVERVIEW":
        analysis_config_payload = {
            "contract_version": "ANALYTICS_EXPORT_ANALYSIS_CONFIG_V1",
            "section": "OVERVIEW",
            "overview": {"evaluations": []},
        }
    elif template == "PARAMETER_ANALYSIS":
        analysis_config_payload = {
            "contract_version": "ANALYTICS_EXPORT_ANALYSIS_CONFIG_V1",
            "section": "PARAMETER_ANALYSIS",
            "parameter_analysis": {
                "parameters": selected_parameters,
                "group_by": "DATASET",
                "analyses": ["DESCRIPTIVE"],
            },
        }
    elif template == "PARAMETER_RELATIONSHIP":
        analysis_config_payload = {
            "contract_version": "ANALYTICS_EXPORT_ANALYSIS_CONFIG_V1",
            "section": "PARAMETER_RELATIONSHIP",
            "parameter_relationship": {
                "x_parameter": selected_parameters[0],
                "y_parameters": selected_parameters[1:],
                "analyses": ["SCATTER", "TREND"],
                "group_by": "DATASET",
                "max_points": 10_000,
            },
        }
    elif template == "SPATIAL_ANALYSIS":
        analysis_config_payload = {
            "contract_version": "ANALYTICS_EXPORT_ANALYSIS_CONFIG_V1",
            "section": "SPATIAL_ANALYSIS",
            "spatial_analysis": {
                "mode": "PARAMETER_HEATMAP",
                "parameter": selected_parameters[0],
                "focus_dataset_id": int(row["dataset_id"]),
                "max_points": 50_000,
            },
        }
    elif template == "WAFER_SUMMARY":
        analysis_config_payload = {
            "contract_version": "ANALYTICS_EXPORT_ANALYSIS_CONFIG_V1",
            "section": "WAFER_SUMMARY",
            "wafer_summary": {"sort_by": "DATASET", "sort_direction": "ASC"},
        }
    elif template == "FT_QUALITY":
        if not args.rule_code or not args.rule_version:
            raise RuntimeError(
                "FT_QUALITY smoke requires --rule-code and --rule-version for an approved active Rule"
            )
        analysis_config_payload = {
            "contract_version": "ANALYTICS_EXPORT_ANALYSIS_CONFIG_V1",
            "section": "FT_QUALITY",
            "ft_quality": {
                "analysis": "SYL_GROUPED_LIMIT",
                "rule": {
                    "rule_code": args.rule_code,
                    "version_code": args.rule_version,
                },
                "group_by": "DATASET",
            },
        }
    chart_config = {"show_spec_overlay": True}
    if is_report:
        assert analysis_config_payload is not None
        chart_config["analysis"] = analysis_config_payload
    display_config = {"section": "delivery", "page": 1, "page_size": 10}
    if not is_report:
        chart_config["analysis_view_state"] = {
            "contract_version": "ANALYSIS_VIEW_STATE_V1",
            "components": {
                "detail": {
                    "view": "LONG" if template == "PARAMETER_DETAIL" else "WIDE",
                    "sortBy": "UNIT_SEQUENCE",
                    "sortDirection": "ASC",
                }
            },
        }
        display_config = {
            "section": "detail",
            "page": 1,
            "page_size": 10,
            "focus_dataset_id": int(row["dataset_id"]),
        }
    analysis_config = resolve_analytics_export_analysis_config(template, chart_config)
    rule_parameters = tuple(
        sorted(
            set(context.parameters)
            | set(analytics_export_analysis_parameters(analysis_config))
        )
    )
    rule_context_request = AnalyticsContextRequest.model_validate(
        {
            **context.model_dump(mode="python"),
            "parameters": list(rule_parameters),
        }
    )
    with engine.connect() as connection:
        rule_context = SqlAnalyticsExportService._default_rule_context(
            connection, (row,), rule_context_request
        )
    work_item = AnalyticsExportWorkItem(
        export_job_id=1,
        requested_by=1,
        export_scope=(
            AnalyticsExportScope.REPORT
            if is_report
            else AnalyticsExportScope.CURRENT_PAGE
        ),
        export_format=AnalyticsExportFormat(args.format),
        template_code=template,
        template_version="v1",
        context=context,
        dataset_version_ids=(int(row["dataset_version_id"]),),
        test_stage=str(row["test_stage"]),
        filter_hash=hashes.filter_hash,
        context_hash=hashes.context_hash,
        rule_context=rule_context,
        chart_config=chart_config,
        display_config=display_config,
        presentation_hash=validate_analysis_presentation_config(
            chart_config, display_config
        ),
        artifact_ttl_hours=1,
        page=None if is_report else 1,
        page_size=None if is_report else 10,
        requested_at_utc=datetime.now(UTC),
        lease_token=str(uuid4()),
        lease_owner="analytics-export-content-smoke",
        lease_expires_at_utc=datetime.now(UTC) + timedelta(minutes=5),
        attempt_count=1,
    )
    policy = AnalyticsExportPathPolicy(args.output_root)
    try:
        artifact = AnalyticsExportRenderer(
            policy, SqlAnalyticsExportContentSource(engine)
        ).render(work_item)
    except DomainError as exc:
        if (
            args.expect_rule_gate
            and template == "FT_QUALITY"
            and exc.code == "ANALYSIS_RULE_NOT_APPROVED"
        ):
            print(
                json.dumps(
                    {
                        "status": "EXPECTED_FAIL_CLOSED",
                        "database": database["database"],
                        "schema_revision": database["schema_revision"],
                        "test_stage": work_item.test_stage,
                        "template_code": work_item.template_code,
                        "export_format": work_item.export_format.value,
                        "dataset_id": int(row["dataset_id"]),
                        "version_no": int(row["version_no"]),
                        "filter_hash": work_item.filter_hash,
                        "context_hash": work_item.context_hash,
                        "presentation_hash": work_item.presentation_hash,
                        "failure_code": exc.code,
                        "exported_row_count": None,
                        "sha256": None,
                    },
                    ensure_ascii=False,
                )
            )
            return
        raise
    if is_report:
        if artifact.exported_row_count < 1:
            raise RuntimeError("real-SQL overview export returned no summary rows")
    elif artifact.exported_row_count != 10:
        raise RuntimeError("real-SQL current-page export did not return 10 rows")
    if args.format == "CSV":
        with artifact.path.open("r", encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.reader(stream))
        if len(rows) != artifact.exported_row_count + 1:
            raise RuntimeError("real-SQL export file row count does not reconcile")
    elif args.format == "XLSX":
        if not artifact.path.read_bytes().startswith(b"PK\x03\x04"):
            raise RuntimeError("real-SQL XLSX export has an invalid file signature")
    elif args.format == "HTML":
        if not artifact.path.read_text(encoding="utf-8").startswith("<!doctype html>"):
            raise RuntimeError("real-SQL HTML export has an invalid document signature")
    elif args.format == "PDF":
        payload = artifact.path.read_bytes()
        if not payload.startswith(b"%PDF-") or not payload.rstrip().endswith(b"%%EOF"):
            raise RuntimeError("real-SQL PDF export has an invalid file signature")
    else:
        if not artifact.path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n"):
            raise RuntimeError("real-SQL PNG export has an invalid file signature")
    digest = hashlib.sha256(artifact.path.read_bytes()).hexdigest()
    if digest != artifact.sha256:
        raise RuntimeError("rendered artifact SHA256 does not reconcile")
    print(
        json.dumps(
            {
                "status": "PASS",
                "database": database["database"],
                "schema_revision": database["schema_revision"],
                "test_stage": work_item.test_stage,
                "template_code": work_item.template_code,
                "export_format": work_item.export_format.value,
                "dataset_id": int(row["dataset_id"]),
                "version_no": int(row["version_no"]),
                "filter_hash": work_item.filter_hash,
                "context_hash": work_item.context_hash,
                "presentation_hash": work_item.presentation_hash,
                "exported_row_count": artifact.exported_row_count,
                "file_size": artifact.file_size,
                "sha256": artifact.sha256,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
