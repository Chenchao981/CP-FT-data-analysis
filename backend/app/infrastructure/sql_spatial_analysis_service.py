from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.engine import Connection, Engine

from app.core.errors import DomainError
from app.domain.analysis_rules import QuadrantYDirection
from app.domain.analytics import AnalyticsCapability, AnalyticsRuleContext
from app.domain.spatial_analysis import (
    SpatialAnalysisMode,
    SpatialAnalysisRequest,
    SpatialAnalysisResult,
    SpatialColorDomain,
    SpatialDataQuality,
    SpatialPoint,
    SpatialQuadrantSummary,
    SpatialWaferIdentity,
    SpatialZoneGeometry,
    SpatialZoneSummary,
)
from app.infrastructure.sql_analysis_rule_service import SqlAnalysisRuleService
from app.infrastructure.sql_analytics_service import (
    SqlAnalyticsService,
    _condition_text,
    _finite_float,
    _hashes,
)

_CONTRACT_VERSION = "ANALYTICS_SPATIAL_V1"
_WORKLOAD_LIMIT = 2_000_000


def _statement(sql: str, expanding: tuple[str, ...] = ()):
    statement = text(sql)
    if expanding:
        statement = statement.bindparams(
            *(bindparam(name, expanding=True) for name in expanding)
        )
    return statement


def _quantile(sorted_values: list[float], probability: float) -> float:
    if not sorted_values:
        raise ValueError("quantile requires values")
    position = (len(sorted_values) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return sorted_values[lower]
    fraction = position - lower
    return sorted_values[lower] * (1.0 - fraction) + sorted_values[upper] * fraction


def _color_domain(values: list[float]) -> SpatialColorDomain | None:
    if not values:
        return None
    ordered = sorted(values)
    return SpatialColorDomain(
        minimum=ordered[0],
        maximum=ordered[-1],
        p02=_quantile(ordered, 0.02),
        p98=_quantile(ordered, 0.98),
    )


def _spec_status(
    value: float | None,
    measurement_status: object,
    evaluation_result: object,
) -> str:
    result = str(evaluation_result)
    if result in {"NOT_EVALUATED", "INVALID_VALUE"}:
        return "MISSING"
    if value is None or str(measurement_status) in {
        "MISSING",
        "NOT_TESTED",
        "NOT_APPLICABLE",
        "OVER_RANGE",
        "UNDER_RANGE",
        "INVALID",
    }:
        raise DomainError(
            "ANALYSIS_SPEC_EVALUATION_INVALID",
            "Formal Spec evaluation result conflicts with measurement status",
            409,
        )
    if result == "FAIL":
        return "OUT_OF_SPEC"
    if result == "PASS":
        return "IN_SPEC"
    raise DomainError(
        "ANALYSIS_SPEC_EVALUATION_INVALID",
        "Formal Spec evaluation result is not usable by spatial analysis",
        409,
    )


def _zone_name(
    x: int,
    y: int,
    *,
    center_x: float,
    center_y: float,
    radius: float,
    center_ratio: float,
    mid_ratio: float,
) -> str:
    normalized_radius = math.hypot(x - center_x, y - center_y) / radius
    if normalized_radius > 1.0 + 1e-12:
        return "OUTSIDE_LAYOUT"
    if normalized_radius <= center_ratio:
        return "CENTER"
    if normalized_radius <= mid_ratio:
        return "MID"
    return "EDGE"


def _quadrant_name(
    x: int,
    y: int,
    *,
    center_x: float,
    center_y: float,
    axis_rotation_degrees: float,
    y_direction: str,
    labels_ccw: tuple[str, str, str, str],
) -> str:
    """Assign a half-open 90 degree sector in the approved coordinate system.

    Label zero starts on the rotated positive X axis.  Axis-boundary points belong
    to the sector that starts on that axis.  ``quadrant_y_direction`` defines the
    direction of the approved positive Y basis in stored wafer coordinates.
    """

    y_sign = -1.0 if y_direction == QuadrantYDirection.UP else 1.0
    approved_x = x - center_x
    approved_y = y_sign * (y - center_y)
    angle = math.degrees(math.atan2(approved_y, approved_x))
    relative = (angle - axis_rotation_degrees) % 360.0
    return labels_ccw[min(int(relative // 90.0), 3)]


def _response_item_count(
    points: tuple[SpatialPoint, ...],
    wafer_layers: tuple[SpatialPoint, ...],
    zones: tuple[SpatialZoneSummary, ...],
    quadrants: tuple[SpatialQuadrantSummary, ...],
) -> tuple[int, int, int, int]:
    member_key_count = sum(len(point.member_drilldown_keys) for point in points)
    zone_member_key_count = sum(len(summary.member_drilldown_keys) for summary in zones)
    quadrant_member_key_count = sum(
        len(summary.member_drilldown_keys) for summary in quadrants
    )
    total = (
        len(points)
        + len(wafer_layers)
        + member_key_count
        + zone_member_key_count
        + quadrant_member_key_count
    )
    return total, member_key_count, zone_member_key_count, quadrant_member_key_count


class SqlSpatialAnalysisService:
    """Read-only CP spatial analytics over the formal Current Dataset chain."""

    def __init__(
        self,
        engine: Engine,
        *,
        rule_service: SqlAnalysisRuleService | None = None,
    ) -> None:
        self._engine = engine
        self._analytics = SqlAnalyticsService(engine)
        self._rules = rule_service

    def _approved_zone_rule(
        self,
        request: SpatialAnalysisRequest,
        contexts: tuple[Mapping[str, Any], ...],
    ) -> dict[str, Any]:
        if self._rules is None:
            raise DomainError(
                "ANALYSIS_RULE_NOT_APPROVED",
                "zone comparison requires an approved and active rule",
                409,
            )
        resolved: dict[str, Any] | None = None
        parameter = request.parameters[0] if request.parameters else None
        for context in contexts:
            current = self._rules.approved_rule_parameters(
                rule_code=request.rule_code or "",
                version_code=request.rule_version or "",
                test_stage=str(context["test_stage"]),
                expected_algorithm_code="WAFER_ZONE_GEOMETRY_V2",
                supplier_id=(
                    int(context["supplier_id"])
                    if context.get("supplier_id") is not None
                    else None
                ),
                product_id=(
                    int(context["product_id"])
                    if context.get("product_id") is not None
                    else None
                ),
                parameter=parameter,
            )
            if resolved is None:
                resolved = dict(current)
            elif resolved != dict(current):
                raise DomainError(
                    "ANALYSIS_RULE_CONTRACT_INVALID",
                    "one exact zone rule resolved to inconsistent parameters",
                    409,
                )
        if resolved is None:
            raise DomainError(
                "ANALYSIS_RULE_NOT_APPROVED",
                "zone comparison rule has no approved Dataset scope",
                409,
            )
        return resolved

    def _rows_for_context(
        self,
        connection: Connection,
        request: SpatialAnalysisRequest,
        context: Mapping[str, Any],
    ) -> tuple[Mapping[str, Any], ...]:
        source_rows = self._analytics._source_rows(connection, context)
        item_rows = self._analytics._item_rows(connection, context)
        source_run_ids = self._analytics._selected_run_ids(request, source_rows)
        condition_item_ids = self._analytics._selected_condition_item_ids(
            request, item_rows
        )
        filter_sql, filter_parameters, expanding = self._analytics._filter_sql(
            request,
            source_run_ids=source_run_ids,
            condition_item_ids=condition_item_ids,
        )
        parameters: dict[str, object] = {
            "dataset": int(context["dataset_id"]),
            "version": int(context["version_no"]),
            **filter_parameters,
        }
        base_join = self._analytics._base_join()
        unit_count = int(
            connection.execute(
                _statement(
                    "SELECT COUNT_BIG(*)"
                    + base_join
                    + "WHERE dv.dataset_id=:dataset AND dv.version_no=:version"
                    + filter_sql,
                    expanding,
                ),
                parameters,
            ).scalar_one()
        )
        if unit_count > _WORKLOAD_LIMIT:
            raise DomainError(
                "ANALYSIS_WORKLOAD_LIMIT_EXCEEDED",
                "spatial analysis exceeds the synchronous unit limit",
                413,
                details=[{"unit_count": unit_count, "limit": _WORKLOAD_LIMIT}],
            )

        select_measurement = (
            ",m.measurement_id,m.test_item_id AS measurement_test_item_id,"
            "m.value_numeric,m.measurement_status,tid.unit_code,"
            "tid.condition_json AS program_condition_json,"
            "spec_eval.spec_evaluation_count,spec_eval.spec_evaluation_id,"
            "spec_eval.formal_evaluation_result,spec_eval.spec_binding_id,"
            "spec_eval.binding_spec_set_id,spec_eval.spec_set_id,"
            "spec_eval.spec_version,spec_eval.spec_set_status,"
            "spec_eval.spec_item_id,spec_eval.spec_test_item_id,"
            "spec_eval.spec_unit_code,"
            "spec_eval.spec_condition_json,spec_eval.formal_lsl,"
            "spec_eval.formal_usl,spec_eval.formal_lower_operator,"
            "spec_eval.formal_upper_operator "
        )
        measurement_join = ""
        spec_evaluation_apply = ""
        if request.parameters:
            parameter_ids = self._analytics._parameter_ids(
                item_rows, tuple(request.parameters)
            )
            parameters["parameter_ids"] = parameter_ids
            expanding += ("parameter_ids",)
            measurement_join = (
                "LEFT JOIN mdm.test_item_definition tid "
                "ON tid.test_item_id IN :parameter_ids "
                "AND tid.program_version_id=tr.program_version_id "
                "LEFT JOIN test.measurement m ON m.unit_id=ur.unit_id "
                "AND m.test_item_id=tid.test_item_id "
            )
            spec_evaluation_apply = (
                "OUTER APPLY(SELECT TOP (1) "
                "COUNT_BIG(*) OVER() AS spec_evaluation_count,"
                "me.evaluation_id AS spec_evaluation_id,"
                "me.evaluation_result AS formal_evaluation_result,"
                "me.spec_binding_id,sb.spec_set_id AS binding_spec_set_id,"
                "si.spec_set_id,ss.version_code AS spec_version,"
                "ss.status AS spec_set_status,me.spec_item_id,"
                "si.test_item_id AS spec_test_item_id,"
                "si.unit_code AS spec_unit_code,"
                "si.condition_json AS spec_condition_json,"
                "me.lsl_applied AS formal_lsl,"
                "me.usl_applied AS formal_usl,"
                "me.lower_operator_applied AS formal_lower_operator,"
                "me.upper_operator_applied AS formal_upper_operator "
                "FROM test.measurement_evaluation me "
                "LEFT JOIN mdm.spec_item si ON si.spec_item_id=me.spec_item_id "
                "LEFT JOIN mdm.spec_set ss ON ss.spec_set_id=si.spec_set_id "
                "LEFT JOIN mdm.spec_binding sb "
                "ON sb.spec_binding_id=me.spec_binding_id "
                "WHERE me.measurement_id=m.measurement_id "
                "AND me.evaluation_type='SPEC' "
                "AND me.evaluation_scope_key=N'FORMAL_SPEC' "
                "AND me.is_current=1 "
                "ORDER BY me.evaluation_id) spec_eval "
            )
        else:
            select_measurement = (
                ",CAST(NULL AS bigint) AS measurement_id,"
                "CAST(NULL AS bigint) AS measurement_test_item_id,"
                "CAST(NULL AS decimal(38,12)) AS value_numeric,"
                "CAST(NULL AS varchar(20)) AS measurement_status,"
                "CAST(NULL AS nvarchar(64)) AS unit_code,"
                "CAST(NULL AS nvarchar(max)) AS program_condition_json,"
                "CAST(NULL AS bigint) AS spec_evaluation_count,"
                "CAST(NULL AS bigint) AS spec_evaluation_id,"
                "CAST(NULL AS varchar(32)) AS formal_evaluation_result,"
                "CAST(NULL AS bigint) AS spec_binding_id,"
                "CAST(NULL AS bigint) AS binding_spec_set_id,"
                "CAST(NULL AS bigint) AS spec_set_id,"
                "CAST(NULL AS nvarchar(128)) AS spec_version,"
                "CAST(NULL AS varchar(32)) AS spec_set_status,"
                "CAST(NULL AS bigint) AS spec_item_id,"
                "CAST(NULL AS bigint) AS spec_test_item_id,"
                "CAST(NULL AS nvarchar(64)) AS spec_unit_code,"
                "CAST(NULL AS nvarchar(max)) AS spec_condition_json,"
                "CAST(NULL AS float) AS formal_lsl,"
                "CAST(NULL AS float) AS formal_usl,"
                "CAST(NULL AS nvarchar(8)) AS formal_lower_operator,"
                "CAST(NULL AS nvarchar(8)) AS formal_upper_operator "
            )
        mapping_select = (
            ",mapped.evaluation_count,mapped.matched_mapping_count,"
            "mapped.bin_mapping_set_id,mapped.mapping_version,"
            "mapped.bin_definition_id,mapped.mapped_bin_code,mapped.bin_name,"
            "mapped.failure_mode,mapped.is_pass_snapshot "
        )
        mapping_apply = (
            "OUTER APPLY(SELECT COUNT_BIG(*) AS evaluation_count,"
            "SUM(CASE WHEN ube.mapping_status='MATCHED' "
            "AND ube.bin_mapping_set_id IS NOT NULL "
            "AND ube.bin_definition_id IS NOT NULL "
            "AND ube.is_pass_snapshot IS NOT NULL THEN CONVERT(bigint,1) ELSE 0 END) "
            "AS matched_mapping_count,"
            "MIN(CASE WHEN ube.mapping_status='MATCHED' "
            "THEN ube.bin_mapping_set_id END) AS bin_mapping_set_id,"
            "MIN(CASE WHEN ube.mapping_status='MATCHED' "
            "THEN bms.version_code END) AS mapping_version,"
            "MIN(CASE WHEN ube.mapping_status='MATCHED' "
            "THEN ube.bin_definition_id END) AS bin_definition_id,"
            "MIN(CASE WHEN ube.mapping_status='MATCHED' "
            "THEN bd.bin_code END) AS mapped_bin_code,"
            "MIN(CASE WHEN ube.mapping_status='MATCHED' "
            "THEN bd.bin_name END) AS bin_name,"
            "MIN(CASE WHEN ube.mapping_status='MATCHED' "
            "THEN ube.failure_mode_snapshot END) AS failure_mode,"
            "MIN(CASE WHEN ube.mapping_status='MATCHED' "
            "THEN CONVERT(tinyint,ube.is_pass_snapshot) END) AS is_pass_snapshot "
            "FROM test.unit_bin_evaluation ube "
            "LEFT JOIN mdm.bin_mapping_set bms "
            "ON bms.bin_mapping_set_id=ube.bin_mapping_set_id "
            "LEFT JOIN mdm.bin_definition bd "
            "ON bd.bin_definition_id=ube.bin_definition_id "
            "WHERE ube.unit_id=ur.unit_id AND ube.bin_type='CP_BIN' "
            "AND ube.raw_bin_code=COALESCE(ur.soft_bin,ur.hard_bin,N'UNKNOWN')) mapped "
        )
        rows = tuple(
            connection.execute(
                _statement(
                    "SELECT dv.dataset_id,dv.version_no,tr.lot_id,"
                    "COALESCE(ur.wafer_id,tr.wafer_id) AS wafer_id,"
                    "ur.unit_id,ur.x_coord,ur.y_coord,ur.soft_bin,ur.hard_bin,"
                    "ur.overall_result"
                    + select_measurement
                    + mapping_select
                    + base_join
                    + measurement_join
                    + spec_evaluation_apply
                    + mapping_apply
                    + "WHERE dv.dataset_id=:dataset AND dv.version_no=:version"
                    + filter_sql
                    + " ORDER BY tr.lot_id,COALESCE(ur.wafer_id,tr.wafer_id),"
                    "ur.y_coord,ur.x_coord,ur.unit_id,m.measurement_id"
                    if request.parameters
                    else "SELECT dv.dataset_id,dv.version_no,tr.lot_id,"
                    "COALESCE(ur.wafer_id,tr.wafer_id) AS wafer_id,"
                    "ur.unit_id,ur.x_coord,ur.y_coord,ur.soft_bin,ur.hard_bin,"
                    "ur.overall_result"
                    + select_measurement
                    + mapping_select
                    + base_join
                    + mapping_apply
                    + "WHERE dv.dataset_id=:dataset AND dv.version_no=:version"
                    + filter_sql
                    + " ORDER BY tr.lot_id,COALESCE(ur.wafer_id,tr.wafer_id),"
                    "ur.y_coord,ur.x_coord,ur.unit_id",
                    expanding,
                ),
                parameters,
            )
            .mappings()
            .all()
        )
        if request.parameters:
            by_unit: dict[int, int] = defaultdict(int)
            for row in rows:
                if row["measurement_id"] is not None:
                    by_unit[int(row["unit_id"])] += 1
            duplicate_measurements = sorted(
                unit_id for unit_id, count in by_unit.items() if count > 1
            )
            if duplicate_measurements:
                raise DomainError(
                    "ANALYSIS_PARAMETER_INCOMPATIBLE",
                    "selected parameter resolves to multiple measurements for one unit",
                    409,
                    details=[{"unit_ids": duplicate_measurements[:20]}],
                )
            self._validate_formal_specs(rows)
        return rows

    @staticmethod
    def _validate_formal_specs(rows: tuple[Mapping[str, Any], ...]) -> None:
        missing: list[int] = []
        duplicate: list[int] = []
        invalid_provenance: list[int] = []
        incompatible: list[int] = []
        reversed_limits: list[int] = []
        result_mismatch: list[int] = []
        for row in rows:
            unit_id = int(row["unit_id"])
            evaluation_count = int(row.get("spec_evaluation_count") or 0)
            if evaluation_count == 0 or row["spec_item_id"] is None:
                missing.append(unit_id)
                continue
            if evaluation_count != 1:
                duplicate.append(unit_id)
                continue
            spec_set_id = row.get("spec_set_id")
            binding_matches = row.get("spec_binding_id") is None or (
                row.get("binding_spec_set_id") == spec_set_id
            )
            if (
                row.get("spec_evaluation_id") is None
                or spec_set_id is None
                or row.get("spec_set_status") != "RELEASED"
                or row.get("spec_version") is None
                or row.get("measurement_test_item_id") != row.get("spec_test_item_id")
                or not binding_matches
            ):
                invalid_provenance.append(unit_id)
                continue
            program_unit = (
                str(row["unit_code"]).strip() or None
                if row["unit_code"] is not None
                else None
            )
            spec_unit = (
                str(row["spec_unit_code"]).strip() or None
                if row["spec_unit_code"] is not None
                else None
            )
            program_condition = _condition_text(row["program_condition_json"])
            spec_condition = _condition_text(row["spec_condition_json"])
            if program_unit != spec_unit or program_condition != spec_condition:
                incompatible.append(unit_id)
                continue
            lsl = _finite_float(row["formal_lsl"], field="formal LSL")
            usl = _finite_float(row["formal_usl"], field="formal USL")
            lower_operator = row.get("formal_lower_operator")
            upper_operator = row.get("formal_upper_operator")
            if lsl is None and usl is None:
                invalid_provenance.append(unit_id)
                continue
            if (lsl is not None and lower_operator not in {">=", ">"}) or (
                usl is not None and upper_operator not in {"<=", "<"}
            ):
                invalid_provenance.append(unit_id)
                continue
            if (
                lsl is not None
                and usl is not None
                and (
                    lsl > usl
                    or (lsl == usl and (lower_operator == ">" or upper_operator == "<"))
                )
            ):
                reversed_limits.append(unit_id)
                continue
            measurement_status = str(row.get("measurement_status"))
            value = _finite_float(row.get("value_numeric"), field="spatial measurement")
            if measurement_status in {"MISSING", "NOT_TESTED", "NOT_APPLICABLE"}:
                expected_result = "NOT_EVALUATED"
            elif (
                measurement_status in {"OVER_RANGE", "UNDER_RANGE", "INVALID"}
                or value is None
            ):
                expected_result = "INVALID_VALUE"
            else:
                lower_failed = lsl is not None and (
                    value <= lsl if lower_operator == ">" else value < lsl
                )
                upper_failed = usl is not None and (
                    value >= usl if upper_operator == "<" else value > usl
                )
                expected_result = "FAIL" if lower_failed or upper_failed else "PASS"
            if row.get("formal_evaluation_result") != expected_result:
                result_mismatch.append(unit_id)
        if missing:
            raise DomainError(
                "ANALYSIS_SPEC_MISSING",
                "spatial parameter analysis requires one released formal Spec per selected Unit/Lot",
                409,
                details=[{"unit_ids": sorted(set(missing))[:20]}],
            )
        if duplicate:
            raise DomainError(
                "ANALYSIS_SPEC_AMBIGUOUS",
                "spatial parameter analysis requires exactly one current Formal Spec evaluation per Measurement",
                409,
                details=[{"unit_ids": sorted(set(duplicate))[:20]}],
            )
        if invalid_provenance:
            raise DomainError(
                "ANALYSIS_SPEC_PROVENANCE_INVALID",
                "Formal Spec evaluation snapshot has invalid Released Spec provenance, limits or operators",
                409,
                details=[{"unit_ids": sorted(set(invalid_provenance))[:20]}],
            )
        if incompatible:
            raise DomainError(
                "ANALYSIS_SPEC_INCOMPATIBLE",
                "formal Spec unit or test condition differs from the exact parameter identity",
                409,
                details=[{"unit_ids": sorted(set(incompatible))[:20]}],
            )
        if reversed_limits:
            raise DomainError(
                "ANALYSIS_SPEC_DIRECTION_INVALID",
                "formal Spec LSL exceeds USL",
                409,
                details=[{"unit_ids": sorted(set(reversed_limits))[:20]}],
            )
        if result_mismatch:
            raise DomainError(
                "ANALYSIS_SPEC_EVALUATION_INVALID",
                "Formal Spec evaluation result does not match its frozen value, limits and operators",
                409,
                details=[{"unit_ids": sorted(set(result_mismatch))[:20]}],
            )

    @staticmethod
    def _validate_bin_mappings(rows: tuple[Mapping[str, Any], ...]) -> None:
        invalid = sorted(
            {
                int(row["unit_id"])
                for row in rows
                if int(row.get("evaluation_count") or 0) != 1
                or int(row.get("matched_mapping_count") or 0) != 1
                or row.get("bin_mapping_set_id") is None
                or row.get("bin_definition_id") is None
                or row.get("is_pass_snapshot") is None
            }
        )
        if invalid:
            raise DomainError(
                "ANALYSIS_BIN_MAPPING_INCOMPLETE",
                "spatial Bin analysis requires exactly one matched versioned Bin Mapping per selected Unit",
                409,
                details=[{"unit_ids": invalid[:20], "invalid_count": len(invalid)}],
            )

    @staticmethod
    def _validate_coordinates(
        rows: tuple[Mapping[str, Any], ...],
    ) -> tuple[int, int, set[tuple[int, int, str, str]]]:
        missing = sum(row["x_coord"] is None or row["y_coord"] is None for row in rows)
        coordinates: set[tuple[int, int, str, str, int, int]] = set()
        duplicates = 0
        wafers: set[tuple[int, int, str, str]] = set()
        for row in rows:
            wafer = (
                int(row["dataset_id"]),
                int(row["version_no"]),
                str(row["lot_id"]),
                str(row["wafer_id"] or ""),
            )
            wafers.add(wafer)
            if row["x_coord"] is None or row["y_coord"] is None:
                continue
            coordinate = (*wafer, int(row["x_coord"]), int(row["y_coord"]))
            if coordinate in coordinates:
                duplicates += 1
            else:
                coordinates.add(coordinate)
        return missing, duplicates, wafers

    @staticmethod
    def _single_point(
        row: Mapping[str, Any], *, use_mapped_bin: bool = False
    ) -> SpatialPoint:
        value = _finite_float(row["value_numeric"], field="spatial measurement")
        lsl = _finite_float(row["formal_lsl"], field="formal LSL")
        usl = _finite_float(row["formal_usl"], field="formal USL")
        mapped_is_pass_snapshot = (
            bool(row["is_pass_snapshot"])
            if row.get("is_pass_snapshot") is not None
            else None
        )
        mapped_is_pass = mapped_is_pass_snapshot if use_mapped_bin else None
        raw_bin_code = (
            str(row["soft_bin"] or row["hard_bin"])
            if row["soft_bin"] is not None or row["hard_bin"] is not None
            else None
        )
        return SpatialPoint(
            dataset_id=int(row["dataset_id"]),
            version_no=int(row["version_no"]),
            lot_id=str(row["lot_id"]),
            wafer_id=str(row["wafer_id"] or ""),
            x=int(row["x_coord"]),
            y=int(row["y_coord"]),
            bin_code=(
                str(row["mapped_bin_code"])
                if row.get("mapped_bin_code") is not None
                else raw_bin_code
            ),
            result=(
                "PASS"
                if mapped_is_pass is True
                else "FAIL"
                if mapped_is_pass is False
                else str(row["overall_result"])
            ),
            value=value,
            unit=str(row["unit_code"]) if row["unit_code"] is not None else None,
            lsl=lsl,
            usl=usl,
            spec_status=(
                _spec_status(
                    value,
                    row["measurement_status"],
                    row["formal_evaluation_result"],
                )
                if row["measurement_id"] is not None
                else None
            ),
            drilldown_key=f"UNIT:{int(row['unit_id'])}",
            observed_count=1,
            fail_count=int(
                (not mapped_is_pass)
                if mapped_is_pass is not None
                else str(row["overall_result"]) == "FAIL"
            ),
            fail_ratio=float(
                (not mapped_is_pass)
                if mapped_is_pass is not None
                else str(row["overall_result"]) == "FAIL"
            ),
            wafer_count=1,
            raw_bin_code=raw_bin_code,
            bin_mapping_set_id=(
                int(row["bin_mapping_set_id"])
                if row.get("bin_mapping_set_id") is not None
                else None
            ),
            bin_mapping_version=(
                str(row["mapping_version"])
                if row.get("mapping_version") is not None
                else None
            ),
            bin_name=(
                str(row["bin_name"]) if row.get("bin_name") is not None else None
            ),
            failure_mode=(
                str(row["failure_mode"])
                if row.get("failure_mode") is not None
                else None
            ),
            bin_is_pass=mapped_is_pass_snapshot,
            spec_set_id=(
                int(row["spec_set_id"]) if row.get("spec_set_id") is not None else None
            ),
            spec_version=(
                str(row["spec_version"])
                if row.get("spec_version") is not None
                else None
            ),
        )

    @staticmethod
    def _composite_points(
        rows: tuple[Mapping[str, Any], ...],
    ) -> tuple[SpatialPoint, ...]:
        grouped: dict[tuple[int, int], list[Mapping[str, Any]]] = defaultdict(list)
        for row in rows:
            grouped[(int(row["x_coord"]), int(row["y_coord"]))].append(row)
        points: list[SpatialPoint] = []
        for (x, y), members in sorted(
            grouped.items(), key=lambda item: (item[0][1], item[0][0])
        ):
            failures = sum(str(row["overall_result"]) == "FAIL" for row in members)
            member_drilldown_keys = tuple(
                sorted({f"UNIT:{int(row['unit_id'])}" for row in members})
            )
            if len(member_drilldown_keys) != len(members):
                raise DomainError(
                    "ANALYSIS_SPATIAL_DRILLDOWN_INCOMPLETE",
                    "composite coordinate members do not reconcile to stable Unit keys",
                    409,
                    details=[
                        {
                            "x": x,
                            "y": y,
                            "observed_count": len(members),
                            "member_key_count": len(member_drilldown_keys),
                        }
                    ],
                )
            wafer_count = len(
                {
                    (
                        int(row["dataset_id"]),
                        int(row["version_no"]),
                        str(row["lot_id"]),
                        str(row["wafer_id"] or ""),
                    )
                    for row in members
                }
            )
            points.append(
                SpatialPoint(
                    dataset_id=None,
                    version_no=None,
                    lot_id=None,
                    wafer_id=None,
                    x=x,
                    y=y,
                    bin_code=None,
                    result=None,
                    value=None,
                    unit=None,
                    lsl=None,
                    usl=None,
                    spec_status=None,
                    drilldown_key=None,
                    observed_count=len(members),
                    fail_count=failures,
                    fail_ratio=failures / len(members),
                    wafer_count=wafer_count,
                    member_drilldown_keys=member_drilldown_keys,
                )
            )
        return tuple(points)

    @staticmethod
    def _assign_zones(
        points: tuple[SpatialPoint, ...], geometry: SpatialZoneGeometry
    ) -> tuple[SpatialPoint, ...]:
        zoned = tuple(
            replace(
                point,
                zone=_zone_name(
                    point.x,
                    point.y,
                    center_x=geometry.center_x,
                    center_y=geometry.center_y,
                    radius=geometry.radius,
                    center_ratio=geometry.center_ratio,
                    mid_ratio=geometry.mid_ratio,
                ),
                quadrant=_quadrant_name(
                    point.x,
                    point.y,
                    center_x=geometry.center_x,
                    center_y=geometry.center_y,
                    axis_rotation_degrees=geometry.quadrant_axis_rotation_degrees,
                    y_direction=geometry.quadrant_y_direction,
                    labels_ccw=geometry.quadrant_labels_ccw,
                ),
            )
            for point in points
        )
        outside_count = sum(point.zone == "OUTSIDE_LAYOUT" for point in zoned)
        if outside_count:
            raise DomainError(
                "ANALYSIS_SPATIAL_LAYOUT_INCOMPATIBLE",
                "one or more coordinates fall outside the approved wafer layout",
                409,
                details=[{"outside_count": outside_count}],
            )
        return zoned

    @staticmethod
    def _zone_summaries(
        points: tuple[SpatialPoint, ...],
        *,
        center_x: float,
        center_y: float,
        radius: float,
        center_ratio: float,
        mid_ratio: float,
    ) -> tuple[SpatialZoneSummary, ...]:
        grouped: dict[str, list[SpatialPoint]] = defaultdict(list)
        for point in points:
            grouped[
                point.zone
                or _zone_name(
                    point.x,
                    point.y,
                    center_x=center_x,
                    center_y=center_y,
                    radius=radius,
                    center_ratio=center_ratio,
                    mid_ratio=mid_ratio,
                )
            ].append(point)
        if "OUTSIDE_LAYOUT" in grouped:
            raise DomainError(
                "ANALYSIS_SPATIAL_LAYOUT_INCOMPATIBLE",
                "one or more coordinates fall outside the approved wafer layout",
                409,
                details=[{"outside_count": len(grouped["OUTSIDE_LAYOUT"])}],
            )
        summaries: list[SpatialZoneSummary] = []
        for zone, members in sorted(grouped.items()):
            passed = sum(point.result == "PASS" for point in members)
            failed = sum(point.result == "FAIL" for point in members)
            known = passed + failed
            values = [point.value for point in members if point.value is not None]
            member_keys = tuple(
                sorted(
                    {
                        key
                        for point in members
                        for key in (
                            point.member_drilldown_keys
                            or ((point.drilldown_key,) if point.drilldown_key else ())
                        )
                    }
                )
            )
            summaries.append(
                SpatialZoneSummary(
                    zone=zone,
                    unit_count=len(members),
                    pass_count=passed,
                    fail_count=failed,
                    unknown_count=len(members) - known,
                    yield_rate=passed / known if known else None,
                    measured_count=len(values),
                    missing_measurement_count=len(members) - len(values),
                    mean=sum(values) / len(values) if values else None,
                    minimum=min(values) if values else None,
                    maximum=max(values) if values else None,
                    drilldown_key=member_keys[0] if len(member_keys) == 1 else None,
                    member_drilldown_keys=member_keys,
                )
            )
        return tuple(summaries)

    @staticmethod
    def _quadrant_summaries(
        points: tuple[SpatialPoint, ...],
        labels_ccw: tuple[str, str, str, str],
    ) -> tuple[SpatialQuadrantSummary, ...]:
        grouped: dict[str, list[SpatialPoint]] = defaultdict(list)
        for point in points:
            if point.quadrant not in labels_ccw:
                raise DomainError(
                    "ANALYSIS_RULE_CONTRACT_INVALID",
                    "approved quadrant identity is missing from a spatial point",
                    409,
                )
            grouped[point.quadrant].append(point)
        summaries: list[SpatialQuadrantSummary] = []
        for quadrant in labels_ccw:
            members = grouped.get(quadrant, [])
            passed = sum(point.result == "PASS" for point in members)
            failed = sum(point.result == "FAIL" for point in members)
            known = passed + failed
            values = [point.value for point in members if point.value is not None]
            member_keys = tuple(
                sorted(
                    {
                        key
                        for point in members
                        for key in (
                            point.member_drilldown_keys
                            or ((point.drilldown_key,) if point.drilldown_key else ())
                        )
                    }
                )
            )
            if len(member_keys) != len(members):
                raise DomainError(
                    "ANALYSIS_SPATIAL_DRILLDOWN_INCOMPLETE",
                    "quadrant aggregate members do not reconcile to stable Unit keys",
                    409,
                    details=[
                        {
                            "quadrant": quadrant,
                            "unit_count": len(members),
                            "member_key_count": len(member_keys),
                        }
                    ],
                )
            summaries.append(
                SpatialQuadrantSummary(
                    quadrant=quadrant,
                    unit_count=len(members),
                    pass_count=passed,
                    fail_count=failed,
                    unknown_count=len(members) - known,
                    yield_rate=passed / known if known else None,
                    measured_count=len(values),
                    missing_measurement_count=len(members) - len(values),
                    mean=sum(values) / len(values) if values else None,
                    minimum=min(values) if values else None,
                    maximum=max(values) if values else None,
                    member_drilldown_keys=member_keys,
                )
            )
        return tuple(summaries)

    def analyze(self, request: SpatialAnalysisRequest) -> SpatialAnalysisResult:
        with self._engine.connect() as connection:
            context_rows = self._analytics._context_rows(connection, request)
            dataset_context = self._analytics._dataset_context(context_rows)
            if dataset_context.test_stage != "CP":
                raise DomainError(
                    "ANALYSIS_STAGE_INCOMPATIBLE",
                    "spatial analysis is only available for CP datasets",
                    409,
                )
            selected_contexts = tuple(
                row
                for row in context_rows
                if request.focus_dataset_id is None
                or int(row["dataset_id"]) == request.focus_dataset_id
            )
            rows = tuple(
                row
                for context in selected_contexts
                for row in self._rows_for_context(connection, request, context)
            )
            rule_context = self._analytics._rule_context(
                connection, context_rows, request
            )

        if (
            request.mode
            in {
                SpatialAnalysisMode.BIN_MAP,
                SpatialAnalysisMode.PARAMETER_FAIL_OVERLAY,
            }
            and not rule_context.bin_mapping_versions
        ):
            raise DomainError(
                "ANALYSIS_BIN_MAPPING_REQUIRED",
                "this spatial mode requires a versioned Bin Mapping",
                409,
            )
        if request.mode in {
            SpatialAnalysisMode.BIN_MAP,
            SpatialAnalysisMode.PARAMETER_FAIL_OVERLAY,
        }:
            self._validate_bin_mappings(rows)

        missing_coordinates, duplicate_coordinates, wafers = self._validate_coordinates(
            rows
        )
        if not rows:
            raise DomainError(
                "ANALYSIS_EMPTY_SELECTION", "current spatial context has no units", 409
            )
        if missing_coordinates or duplicate_coordinates:
            raise DomainError(
                "ANALYSIS_COORDINATE_CONTRACT_INVALID",
                "CP spatial analysis requires complete and unique coordinates per wafer",
                409,
                details=[
                    {
                        "missing_coordinate_count": missing_coordinates,
                        "duplicate_coordinate_count": duplicate_coordinates,
                    }
                ],
            )

        single_wafer_modes = {
            SpatialAnalysisMode.BIN_MAP,
            SpatialAnalysisMode.PARAMETER_HEATMAP,
            SpatialAnalysisMode.PARAMETER_FAIL_OVERLAY,
            SpatialAnalysisMode.ZONE_COMPARISON,
        }
        if request.mode in single_wafer_modes and len(wafers) != 1:
            raise DomainError(
                "ANALYSIS_WAFER_SCOPE_REQUIRED",
                "this spatial mode requires exactly one selected wafer",
                409,
                details=[{"wafer_count": len(wafers)}],
            )
        if request.mode == SpatialAnalysisMode.COMPOSITE_FAILURE and len(wafers) < 2:
            raise DomainError(
                "ANALYSIS_MULTI_WAFER_SCOPE_REQUIRED",
                "composite failure requires at least two selected wafers",
                409,
            )
        if (
            request.mode == SpatialAnalysisMode.COMPOSITE_FAILURE
            and len(selected_contexts) > 1
        ):
            raise DomainError(
                "ANALYSIS_SPATIAL_LAYOUT_COMPATIBILITY_UNPROVEN",
                "cross-Dataset overlay requires a versioned compatible layout and orientation",
                409,
            )

        wafer_manifest = tuple(
            SpatialWaferIdentity(
                key=(f"{dataset_id}:V{version_no}:LOT:{lot_id}:WAFER:{wafer_id}"),
                dataset_id=dataset_id,
                version_no=version_no,
                lot_id=lot_id,
                wafer_id=wafer_id,
            )
            for dataset_id, version_no, lot_id, wafer_id in sorted(wafers)
        )
        wafer_layers: tuple[SpatialPoint, ...] = ()
        if request.mode == SpatialAnalysisMode.COMPOSITE_FAILURE:
            points = self._composite_points(rows)
            wafer_layers = tuple(self._single_point(row) for row in rows)
        else:
            use_mapped_bin = request.mode in {
                SpatialAnalysisMode.BIN_MAP,
                SpatialAnalysisMode.PARAMETER_FAIL_OVERLAY,
            }
            points = tuple(
                self._single_point(row, use_mapped_bin=use_mapped_bin) for row in rows
            )
        zones: tuple[SpatialZoneSummary, ...] = ()
        quadrants: tuple[SpatialQuadrantSummary, ...] = ()
        zone_geometry: SpatialZoneGeometry | None = None
        if request.mode == SpatialAnalysisMode.ZONE_COMPARISON:
            rule = self._approved_zone_rule(request, selected_contexts)
            required = (
                "zone_layout_center_x",
                "zone_layout_center_y",
                "zone_layout_radius_die",
                "zone_center_ratio",
                "zone_mid_ratio",
                "quadrant_axis_rotation_degrees",
            )
            if any(not isinstance(rule.get(name), (int, float)) for name in required):
                raise DomainError(
                    "ANALYSIS_RULE_CONTRACT_INVALID",
                    "approved zone V2 rule is missing versioned radial or quadrant geometry",
                    409,
                )
            raw_labels = rule.get("quadrant_labels_ccw")
            raw_y_direction = rule.get("quadrant_y_direction")
            if (
                not isinstance(raw_labels, (list, tuple))
                or len(raw_labels) != 4
                or any(
                    not isinstance(label, str) or not label.strip()
                    for label in raw_labels
                )
                or len({label.strip() for label in raw_labels}) != 4
                or raw_y_direction
                not in {
                    QuadrantYDirection.UP,
                    QuadrantYDirection.DOWN,
                    QuadrantYDirection.UP.value,
                    QuadrantYDirection.DOWN.value,
                }
            ):
                raise DomainError(
                    "ANALYSIS_RULE_CONTRACT_INVALID",
                    "approved zone V2 rule requires one Y direction and four unique CCW labels",
                    409,
                )
            center_ratio = float(rule["zone_center_ratio"])
            mid_ratio = float(rule["zone_mid_ratio"])
            radius = float(rule["zone_layout_radius_die"])
            rotation = float(rule["quadrant_axis_rotation_degrees"])
            if not (
                0 < center_ratio < mid_ratio <= 1 and radius > 0 and 0 <= rotation < 360
            ):
                raise DomainError(
                    "ANALYSIS_RULE_CONTRACT_INVALID",
                    "approved zone V2 radius, ratios or quadrant rotation are invalid",
                    409,
                )
            labels = tuple(label.strip() for label in raw_labels)
            zone_geometry = SpatialZoneGeometry(
                center_x=float(rule["zone_layout_center_x"]),
                center_y=float(rule["zone_layout_center_y"]),
                radius=radius,
                center_ratio=center_ratio,
                mid_ratio=mid_ratio,
                quadrant_axis_rotation_degrees=rotation,
                quadrant_y_direction=str(raw_y_direction),
                quadrant_labels_ccw=(labels[0], labels[1], labels[2], labels[3]),
            )
            points = self._assign_zones(points, zone_geometry)
            zones = self._zone_summaries(
                points,
                center_x=zone_geometry.center_x,
                center_y=zone_geometry.center_y,
                radius=zone_geometry.radius,
                center_ratio=zone_geometry.center_ratio,
                mid_ratio=zone_geometry.mid_ratio,
            )
            quadrants = self._quadrant_summaries(
                points, zone_geometry.quadrant_labels_ccw
            )
            rule_context = AnalyticsRuleContext(
                spec_versions=rule_context.spec_versions,
                bin_mapping_versions=rule_context.bin_mapping_versions,
                evaluation_rule_versions=(
                    f"RULE:{request.rule_code}:{request.rule_version}",
                ),
            )

        (
            response_item_count,
            member_key_count,
            zone_member_key_count,
            quadrant_member_key_count,
        ) = _response_item_count(points, wafer_layers, zones, quadrants)
        if response_item_count > request.max_points:
            raise DomainError(
                "ANALYSIS_RESULT_TOO_LARGE",
                "spatial response items exceed the synchronous response limit",
                413,
                details=[
                    {
                        "point_count": len(points),
                        "layer_point_count": len(wafer_layers),
                        "member_key_count": member_key_count,
                        "zone_member_key_count": zone_member_key_count,
                        "quadrant_member_key_count": quadrant_member_key_count,
                        "response_item_count": response_item_count,
                        "limit": request.max_points,
                    }
                ],
            )

        values = [point.value for point in points if point.value is not None]
        measured_count = len(values)
        missing_measurements = len(points) - measured_count if request.parameters else 0
        capabilities = (
            AnalyticsCapability(request.mode.value, "AVAILABLE", None, None),
        )
        warnings: list[str] = []
        if request.parameters and missing_measurements:
            warnings.append("MISSING_MEASUREMENTS_EXCLUDED_FROM_COLOR_DOMAIN")
        return SpatialAnalysisResult(
            contract_version=_CONTRACT_VERSION,
            dataset_context=dataset_context,
            filter_summary=_hashes(request),
            rule_context=rule_context,
            capabilities=capabilities,
            mode=request.mode.value,
            parameter=request.parameters[0] if request.parameters else None,
            color_domain=_color_domain(values),
            data_quality=SpatialDataQuality(
                input_units=len(rows),
                returned_points=len(points),
                wafer_count=len(wafers),
                missing_coordinate_count=missing_coordinates,
                duplicate_coordinate_count=duplicate_coordinates,
                measured_count=measured_count,
                missing_measurement_count=missing_measurements,
                layer_point_count=len(wafer_layers),
            ),
            points=points,
            wafer_manifest=wafer_manifest,
            wafer_layers=wafer_layers,
            zones=zones,
            warnings=tuple(warnings),
            computed_at=datetime.now(UTC).isoformat(),
            zone_geometry=zone_geometry,
            quadrants=quadrants,
        )
