from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from sqlalchemy import Engine, bindparam, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError

from app.core.errors import DomainError
from app.domain.auth import Principal
from app.domain.datasets import (
    BinCountPoint,
    CreateDatasetRequest,
    CreateDatasetVersionRequest,
    DatasetChartData,
    DatasetRecord,
    DatasetResultSummary,
    DatasetVersionRecord,
    DqGateResult,
    FtParameterOption,
    FtParameterPoint,
    GateReason,
    PublishDatasetVersionRequest,
    WaferMapPoint,
    WaferOption,
    WaferYieldPoint,
)


def _dataset(row: Mapping[str, Any]) -> DatasetRecord:
    return DatasetRecord(
        dataset_id=int(row["dataset_id"]),
        dataset_code=str(row["dataset_code"]),
        dataset_name=str(row["dataset_name"]),
        dataset_type=str(row["dataset_type"]),
        test_stage=str(row["test_stage"]),
        supplier_id=row["supplier_id"],
        product_id=row["product_id"],
        owner_user_id=int(row["owner_user_id"]),
    )


def _version(row: Mapping[str, Any], *, run_count: int) -> DatasetVersionRecord:
    return DatasetVersionRecord(
        dataset_version_id=int(row["dataset_version_id"]),
        dataset_id=int(row["dataset_id"]),
        version_no=int(row["version_no"]),
        input_batch_id=int(row["input_batch_id"]),
        canonical_model_version=str(row["canonical_model_version"]),
        status=str(row["status"]),
        is_current=bool(row["is_current"]),
        run_count=run_count,
    )


class SqlDatasetService:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def list_datasets(self, principal: Principal) -> tuple[DatasetRecord, ...]:
        params = {
            "user_id": principal.user_id,
            "is_admin": "SYSTEM_ADMIN" in principal.roles,
        }
        with self._engine.connect() as connection:
            rows = (
                connection.execute(
                    text(
                        "SELECT d.dataset_id,d.dataset_code,d.dataset_name,d.dataset_type,d.test_stage,"
                        "d.supplier_id,d.product_id,d.owner_user_id FROM dataset.dataset d "
                        "WHERE :is_admin=1 OR d.owner_user_id=:user_id "
                        "ORDER BY d.dataset_id DESC"
                    ),
                    params,
                )
                .mappings()
                .all()
            )
        return tuple(_dataset(row) for row in rows)

    def assert_dataset_access(
        self, dataset_id: int, principal: Principal, mode: str = "READ"
    ) -> None:
        if not any(
            item.dataset_id == dataset_id for item in self.list_datasets(principal)
        ):
            raise DomainError("DATASET_ACCESS_DENIED", "无权访问该数据集", 403)

    def create_dataset(self, request: CreateDatasetRequest) -> DatasetRecord:
        try:
            with self._engine.begin() as connection:
                row = (
                    connection.execute(
                        text(
                            "INSERT dataset.dataset("
                            "dataset_code,dataset_name,dataset_type,test_stage,supplier_id,"
                            "product_id,project_code,owner_user_id) OUTPUT "
                            "INSERTED.dataset_id,INSERTED.dataset_code,INSERTED.dataset_name,"
                            "INSERTED.dataset_type,INSERTED.test_stage,INSERTED.supplier_id,"
                            "INSERTED.product_id,INSERTED.owner_user_id VALUES("
                            ":dataset_code,:dataset_name,:dataset_type,:test_stage,:supplier_id,"
                            ":product_id,:project_code,:owner_user_id)"
                        ),
                        request.model_dump(mode="json"),
                    )
                    .mappings()
                    .one()
                )
            return _dataset(row)
        except IntegrityError as exc:
            raise DomainError(
                code="DATASET_IDENTITY_INVALID",
                message="dataset code already exists or an explicit owner/MDM identity is invalid",
                status_code=409,
            ) from exc

    def get_chart_data(
        self,
        dataset_id: int,
        version_no: int,
        lot_id: str | None = None,
        wafer_id: str | None = None,
        source_id: str | None = None,
        parameter: str | None = None,
    ) -> DatasetChartData:
        if wafer_id and not lot_id:
            raise DomainError(
                "LOT_REQUIRED_FOR_WAFER",
                "wafer chart selection requires an explicit lot identity",
                422,
            )
        parameters = {
            "dataset_id": dataset_id,
            "version_no": version_no,
            "lot_id": lot_id,
            "wafer_id": wafer_id,
            "source_id": source_id,
            "parameter": parameter,
        }
        lot_filter = " AND (:lot_id IS NULL OR tr.lot_id=:lot_id)"
        wafer_filter = " AND (:wafer_id IS NULL OR tr.wafer_id=:wafer_id)"
        version_join = (
            " FROM dataset.dataset_version dv "
            "JOIN dataset.dataset_version_run dvr ON dvr.dataset_version_id=dv.dataset_version_id "
            "JOIN test.test_run tr ON tr.processing_run_id=dvr.processing_run_id "
        )
        with self._engine.connect() as connection:
            context = self._version_context(
                connection, dataset_id, version_no, lock=False
            )
            if str(context["test_stage"]) == "FT":
                return self._get_ft_chart_data(
                    connection,
                    context,
                    parameters,
                    version_join,
                )
            option_rows = (
                connection.execute(
                    text(
                        "SELECT DISTINCT tr.lot_id,tr.wafer_id"
                        + version_join
                        + "WHERE dv.dataset_id=:dataset_id AND dv.version_no=:version_no "
                        "AND tr.wafer_id IS NOT NULL ORDER BY tr.lot_id,tr.wafer_id"
                    ),
                    parameters,
                )
                .mappings()
                .all()
            )
            yield_rows = (
                connection.execute(
                    text(
                        "SELECT tr.lot_id,tr.wafer_id,COUNT_BIG(*) AS unit_count,"
                        "SUM(CASE WHEN ur.overall_result='PASS' THEN 1 ELSE 0 END) AS pass_count "
                        + version_join
                        + "JOIN test.unit_result ur ON ur.run_id=tr.run_id "
                        "WHERE dv.dataset_id=:dataset_id AND dv.version_no=:version_no"
                        + lot_filter
                        + " GROUP BY tr.lot_id,tr.wafer_id ORDER BY tr.lot_id,tr.wafer_id"
                    ),
                    parameters,
                )
                .mappings()
                .all()
            )
            bin_rows = (
                connection.execute(
                    text(
                        "SELECT ISNULL(ur.soft_bin,'UNKNOWN') AS soft_bin,COUNT_BIG(*) AS unit_count "
                        + version_join
                        + "JOIN test.unit_result ur ON ur.run_id=tr.run_id "
                        "WHERE dv.dataset_id=:dataset_id AND dv.version_no=:version_no"
                        + lot_filter
                        + wafer_filter
                        + " GROUP BY ur.soft_bin ORDER BY unit_count DESC,soft_bin"
                    ),
                    parameters,
                )
                .mappings()
                .all()
            )
            map_rows: list[Mapping[str, Any]] = []
            if lot_id and wafer_id:
                map_rows = (
                    connection.execute(
                        text(
                            "SELECT ur.x_coord,ur.y_coord,ur.soft_bin,ur.overall_result "
                            + version_join
                            + "JOIN test.unit_result ur ON ur.run_id=tr.run_id "
                            "WHERE dv.dataset_id=:dataset_id AND dv.version_no=:version_no "
                            "AND tr.lot_id=:lot_id AND tr.wafer_id=:wafer_id "
                            "AND ur.x_coord IS NOT NULL AND ur.y_coord IS NOT NULL "
                            "ORDER BY ur.y_coord,ur.x_coord"
                        ),
                        parameters,
                    )
                    .mappings()
                    .all()
                )
        total_bins = sum(int(row["unit_count"]) for row in bin_rows)
        wafer_yield = tuple(
            WaferYieldPoint(
                lot_id=str(row["lot_id"]),
                wafer_id=str(row["wafer_id"] or ""),
                unit_count=int(row["unit_count"]),
                pass_count=int(row["pass_count"] or 0),
                fail_count=int(row["unit_count"]) - int(row["pass_count"] or 0),
                yield_rate=(int(row["pass_count"] or 0) / int(row["unit_count"]))
                if int(row["unit_count"])
                else 0.0,
            )
            for row in yield_rows
        )
        return DatasetChartData(
            dataset_id=dataset_id,
            version_no=version_no,
            test_stage=str(context["test_stage"]),
            product_name=context["product_name"],
            selected_lot_id=lot_id,
            selected_wafer_id=wafer_id,
            selected_source_id=None,
            selected_parameter=None,
            lot_options=tuple(dict.fromkeys(str(row["lot_id"]) for row in option_rows)),
            wafer_options=tuple(
                WaferOption(str(row["lot_id"]), str(row["wafer_id"]))
                for row in option_rows
            ),
            source_options=(),
            parameter_options=(),
            wafer_yield=wafer_yield,
            bin_counts=tuple(
                BinCountPoint(
                    soft_bin=str(row["soft_bin"]),
                    unit_count=int(row["unit_count"]),
                    percent=int(row["unit_count"]) / total_bins if total_bins else 0.0,
                )
                for row in bin_rows
            ),
            wafer_map=tuple(
                WaferMapPoint(
                    x=int(row["x_coord"]),
                    y=int(row["y_coord"]),
                    soft_bin=str(row["soft_bin"])
                    if row["soft_bin"] is not None
                    else None,
                    result=str(row["overall_result"]),
                )
                for row in map_rows
            ),
            ft_parameter_points=(),
            ft_total_point_count=0,
            ft_sampled=False,
        )

    def _get_ft_chart_data(
        self,
        connection: Connection,
        context: Mapping[str, Any],
        parameters: dict[str, Any],
        version_join: str,
    ) -> DatasetChartData:
        all_option_rows = (
            connection.execute(
                text(
                    "SELECT DISTINCT tr.run_id,tr.lot_id,tr.tester_id,tr.program_version_id,tr.metadata_json"
                    + version_join
                    + "WHERE dv.dataset_id=:dataset_id AND dv.version_no=:version_no "
                    "ORDER BY tr.lot_id,tr.tester_id,tr.run_id"
                ),
                parameters,
            )
            .mappings()
            .all()
        )
        lot_options = tuple(
            dict.fromkeys(str(row["lot_id"]) for row in all_option_rows)
        )
        option_rows = tuple(
            row
            for row in all_option_rows
            if parameters["lot_id"] is None
            or str(row["lot_id"]) == parameters["lot_id"]
        )
        source_records = []
        for row in option_rows:
            metadata: dict[str, Any] = {}
            try:
                decoded = json.loads(row["metadata_json"] or "{}")
                if isinstance(decoded, dict):
                    metadata = decoded
            except (TypeError, ValueError):
                metadata = {}
            source_identity = str(metadata.get("source_id") or "").strip()
            if not source_identity:
                source_identity = str(row["tester_id"] or f"RUN-{row['run_id']}")
            source_records.append(
                {
                    "run_id": int(row["run_id"]),
                    "lot_id": str(row["lot_id"]),
                    "source_id": source_identity,
                }
            )
        source_records.sort(
            key=lambda item: (item["lot_id"], item["source_id"], item["run_id"])
        )
        source_options = tuple(
            dict.fromkeys(str(row["source_id"]) for row in source_records)
        )
        selected_source = parameters["source_id"]
        if selected_source and selected_source not in source_options:
            raise DomainError(
                "FT_SOURCE_NOT_FOUND", "selected FT source was not found", 404
            )
        selected_run_ids = tuple(
            int(row["run_id"])
            for row in source_records
            if not selected_source or row["source_id"] == selected_source
        )
        source_filter = ""
        source_parameters: dict[str, Any] = {}
        if selected_source:
            source_filter = "AND tr.run_id IN :source_run_ids "
            source_parameters["source_run_ids"] = selected_run_ids

        parameter_statement = text(
            "SELECT DISTINCT tid.sequence_no,tid.raw_item_name,"
            "tid.unit_code,tid.program_lsl AS lsl,tid.program_usl AS usl,tid.condition_json "
            + version_join
            + "JOIN mdm.test_item_definition tid ON tid.program_version_id=tr.program_version_id "
            "WHERE dv.dataset_id=:dataset_id AND dv.version_no=:version_no "
            "AND (:lot_id IS NULL OR tr.lot_id=:lot_id) "
            + source_filter
            + "AND tid.is_analysis_parameter=1 ORDER BY tid.sequence_no"
        )
        if selected_source:
            parameter_statement = parameter_statement.bindparams(
                bindparam("source_run_ids", expanding=True)
            )
        parameter_rows = (
            connection.execute(
                parameter_statement,
                {
                    **parameters,
                    **source_parameters,
                },
            )
            .mappings()
            .all()
        )
        names = {str(row["raw_item_name"]) for row in parameter_rows}
        selected_parameter = parameters["parameter"]
        if selected_parameter is not None and selected_parameter not in names:
            raise DomainError(
                "FT_PARAMETER_NOT_FOUND", "selected FT parameter was not found", 404
            )
        point_rows: list[Mapping[str, Any]] = []
        total_count = 0
        if selected_parameter:
            point_params = {
                **parameters,
                **source_parameters,
            }
            count_statement = text(
                "SELECT COUNT_BIG(*) "
                + version_join
                + "JOIN test.unit_result ur ON ur.run_id=tr.run_id "
                "JOIN test.measurement m ON m.unit_id=ur.unit_id "
                "JOIN mdm.test_item_definition tid ON tid.test_item_id=m.test_item_id "
                "WHERE dv.dataset_id=:dataset_id AND dv.version_no=:version_no "
                "AND (:lot_id IS NULL OR tr.lot_id=:lot_id) "
                + source_filter
                + "AND tid.raw_item_name=:parameter"
            )
            if selected_source:
                count_statement = count_statement.bindparams(
                    bindparam("source_run_ids", expanding=True)
                )
            total_count = int(
                connection.execute(
                    count_statement,
                    point_params,
                ).scalar_one()
            )
            stride = max(1, (total_count + 9_999) // 10_000)
            points_statement = text(
                ";WITH points AS (SELECT tr.run_id,ur.unit_sequence,tr.lot_id,"
                "m.value_numeric,m.measurement_status,"
                "tid.program_lsl AS lsl,tid.program_usl AS usl,"
                "ROW_NUMBER() OVER(ORDER BY tr.run_id,ur.unit_sequence,ur.unit_id) AS rn "
                + version_join
                + "JOIN test.unit_result ur ON ur.run_id=tr.run_id "
                "JOIN test.measurement m ON m.unit_id=ur.unit_id "
                "JOIN mdm.test_item_definition tid ON tid.test_item_id=m.test_item_id "
                "WHERE dv.dataset_id=:dataset_id AND dv.version_no=:version_no "
                "AND (:lot_id IS NULL OR tr.lot_id=:lot_id) "
                + source_filter
                + "AND tid.raw_item_name=:parameter) "
                "SELECT run_id,unit_sequence,lot_id,value_numeric,measurement_status "
                "FROM points WHERE (rn-1)%:stride=0 OR "
                "(value_numeric IS NOT NULL AND ((lsl IS NOT NULL AND value_numeric<lsl) "
                "OR (usl IS NOT NULL AND value_numeric>usl))) "
                "ORDER BY run_id,unit_sequence"
            )
            if selected_source:
                points_statement = points_statement.bindparams(
                    bindparam("source_run_ids", expanding=True)
                )
            point_rows = (
                connection.execute(
                    points_statement,
                    {**point_params, "stride": stride},
                )
                .mappings()
                .all()
            )
        parameter_groups: dict[str, list[Mapping[str, Any]]] = {}
        for row in parameter_rows:
            parameter_groups.setdefault(str(row["raw_item_name"]), []).append(row)
        parameter_options = []
        for name, rows in sorted(
            parameter_groups.items(),
            key=lambda item: min(int(row["sequence_no"]) for row in item[1]),
        ):
            units = {str(row["unit_code"]) for row in rows if row["unit_code"]}
            if len(units) > 1:
                raise DomainError(
                    "FT_PARAMETER_UNIT_CONFLICT",
                    f"FT parameter {name} has conflicting units across source runs",
                    409,
                )
            lsl_values = {row["lsl"] for row in rows}
            usl_values = {row["usl"] for row in rows}
            condition_texts = set()
            for row in rows:
                try:
                    condition = json.loads(row["condition_json"] or "{}")
                except (TypeError, ValueError):
                    condition = {}
                condition_texts.add(condition.get("text"))
            parameter_options.append(
                FtParameterOption(
                    name=name,
                    unit=next(iter(units)) if units else None,
                    lsl=float(next(iter(lsl_values)))
                    if len(lsl_values) == 1 and None not in lsl_values
                    else None,
                    usl=float(next(iter(usl_values)))
                    if len(usl_values) == 1 and None not in usl_values
                    else None,
                    test_condition=next(iter(condition_texts))
                    if len(condition_texts) == 1
                    else "多源文件测试条件不同，请选择单一源文件",
                )
            )
        source_by_run = {
            int(row["run_id"]): str(row["source_id"]) for row in source_records
        }
        return DatasetChartData(
            dataset_id=int(context["dataset_id"]),
            version_no=int(context["version_no"]),
            test_stage="FT",
            product_name=context["product_name"],
            selected_lot_id=parameters["lot_id"],
            selected_wafer_id=None,
            selected_source_id=parameters["source_id"],
            selected_parameter=selected_parameter,
            lot_options=lot_options,
            wafer_options=(),
            source_options=source_options,
            parameter_options=tuple(parameter_options),
            wafer_yield=(),
            bin_counts=(),
            wafer_map=(),
            ft_parameter_points=tuple(
                FtParameterPoint(
                    sequence=int(row["unit_sequence"]),
                    lot_id=str(row["lot_id"]),
                    source_id=source_by_run[int(row["run_id"])],
                    value=float(row["value_numeric"])
                    if row["value_numeric"] is not None
                    else None,
                    status=str(row["measurement_status"]),
                )
                for row in point_rows
            ),
            ft_total_point_count=total_count,
            ft_sampled=len(point_rows) < total_count,
        )

    def create_version(
        self, dataset_id: int, request: CreateDatasetVersionRequest
    ) -> DatasetVersionRecord:
        with self._engine.begin() as connection:
            dataset_exists = connection.execute(
                text(
                    "SELECT dataset_id FROM dataset.dataset WITH (UPDLOCK,HOLDLOCK) "
                    "WHERE dataset_id=:dataset_id"
                ),
                {"dataset_id": dataset_id},
            ).scalar_one_or_none()
            if dataset_exists is None:
                raise DomainError("DATASET_NOT_FOUND", "dataset was not found", 404)
            batch_exists = connection.execute(
                text(
                    "SELECT import_batch_id FROM ingestion.import_batch "
                    "WHERE import_batch_id=:input_batch_id"
                ),
                {"input_batch_id": request.input_batch_id},
            ).scalar_one_or_none()
            if batch_exists is None:
                raise DomainError(
                    "INPUT_BATCH_NOT_FOUND", "input batch was not found", 404
                )
            found_runs = self._find_runs(connection, request.processing_run_ids)
            missing = sorted(set(request.processing_run_ids) - found_runs)
            if missing:
                raise DomainError(
                    "PROCESSING_RUN_NOT_FOUND",
                    "one or more processing runs were not found",
                    404,
                    details=[{"processing_run_ids": missing}],
                )

            version_no = int(
                connection.execute(
                    text(
                        "SELECT ISNULL(MAX(version_no),0)+1 FROM dataset.dataset_version "
                        "WITH (UPDLOCK,HOLDLOCK) WHERE dataset_id=:dataset_id"
                    ),
                    {"dataset_id": dataset_id},
                ).scalar_one()
            )
            supersedes = connection.execute(
                text(
                    "SELECT dataset_version_id FROM dataset.dataset_version "
                    "WHERE dataset_id=:dataset_id AND status='PUBLISHED' AND is_current=1"
                ),
                {"dataset_id": dataset_id},
            ).scalar_one_or_none()
            row = (
                connection.execute(
                    text(
                        "INSERT dataset.dataset_version("
                        "dataset_id,version_no,input_batch_id,canonical_model_version,status,"
                        "is_current,supersedes_dataset_version_id,metadata_json) OUTPUT "
                        "INSERTED.dataset_version_id,INSERTED.dataset_id,INSERTED.version_no,"
                        "INSERTED.input_batch_id,INSERTED.canonical_model_version,"
                        "INSERTED.status,INSERTED.is_current VALUES("
                        ":dataset_id,:version_no,:input_batch_id,:canonical_model_version,"
                        "'VALIDATING',0,:supersedes,:metadata_json)"
                    ),
                    {
                        "dataset_id": dataset_id,
                        "version_no": version_no,
                        "input_batch_id": request.input_batch_id,
                        "canonical_model_version": request.canonical_model_version,
                        "supersedes": supersedes,
                        "metadata_json": json.dumps(
                            {"run_count": len(request.processing_run_ids)},
                            separators=(",", ":"),
                        ),
                    },
                )
                .mappings()
                .one()
            )
            version_id = int(row["dataset_version_id"])
            connection.execute(
                text(
                    "INSERT dataset.dataset_version_run("
                    "dataset_version_id,processing_run_id,run_role,ordinal_no) VALUES("
                    ":dataset_version_id,:processing_run_id,'PRIMARY',:ordinal_no)"
                ),
                [
                    {
                        "dataset_version_id": version_id,
                        "processing_run_id": run_id,
                        "ordinal_no": ordinal,
                    }
                    for ordinal, run_id in enumerate(
                        request.processing_run_ids, start=1
                    )
                ],
            )
        return _version(row, run_count=len(request.processing_run_ids))

    @staticmethod
    def _find_runs(connection: Connection, run_ids: list[int]) -> set[int]:
        found: set[int] = set()
        for offset in range(0, len(run_ids), 500):
            chunk = run_ids[offset : offset + 500]
            placeholders = ",".join(f":run_{index}" for index in range(len(chunk)))
            params = {f"run_{index}": value for index, value in enumerate(chunk)}
            rows = connection.execute(
                text(
                    "SELECT processing_run_id FROM ingestion.processing_run "
                    f"WHERE processing_run_id IN ({placeholders})"
                ),
                params,
            ).all()
            found.update(int(row[0]) for row in rows)
        return found

    def _version_context(
        self, connection: Connection, dataset_id: int, version_no: int, *, lock: bool
    ) -> Mapping[str, Any]:
        lock_hint = " WITH (UPDLOCK,HOLDLOCK)" if lock else ""
        row = (
            connection.execute(
                text(
                    "SELECT dv.dataset_version_id,dv.dataset_id,dv.version_no,"
                    "dv.input_batch_id,dv.canonical_model_version,dv.status,dv.is_current,"
                    "d.supplier_id,d.product_id,d.test_stage,p.product_name,dv.spec_set_id "
                    f"FROM dataset.dataset_version dv{lock_hint} "
                    "JOIN dataset.dataset d ON d.dataset_id=dv.dataset_id "
                    "LEFT JOIN mdm.product p ON p.product_id=d.product_id "
                    "WHERE dv.dataset_id=:dataset_id AND dv.version_no=:version_no"
                ),
                {"dataset_id": dataset_id, "version_no": version_no},
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise DomainError(
                "DATASET_VERSION_NOT_FOUND", "dataset version was not found", 404
            )
        return row

    def _evaluate(
        self, connection: Connection, dataset_id: int, version_no: int, *, lock: bool
    ) -> DqGateResult:
        context = self._version_context(connection, dataset_id, version_no, lock=lock)
        version_id = int(context["dataset_version_id"])
        run_rows = (
            connection.execute(
                text(
                    "SELECT pr.processing_run_id,pr.source_file_id,pr.status,"
                    "ISNULL(pr.unit_count_output,0) AS unit_count,"
                    "ISNULL(pr.measurement_count_output,0) AS measurement_count,"
                    "CASE WHEN pj.import_batch_id=:input_batch_id OR EXISTS("
                    " SELECT 1 FROM ingestion.source_file_receipt sfr "
                    " LEFT JOIN ingestion.import_batch_file ibf ON ibf.receipt_id=sfr.receipt_id "
                    " WHERE sfr.source_file_id=pr.source_file_id AND "
                    " (sfr.import_batch_id=:input_batch_id OR ibf.import_batch_id=:input_batch_id)"
                    ") THEN 1 ELSE 0 END AS lineage_matches "
                    "FROM dataset.dataset_version_run dvr "
                    "JOIN ingestion.processing_run pr ON pr.processing_run_id=dvr.processing_run_id "
                    "JOIN ingestion.processing_job pj ON pj.job_id=pr.job_id "
                    "WHERE dvr.dataset_version_id=:version_id"
                ),
                {"version_id": version_id, "input_batch_id": context["input_batch_id"]},
            )
            .mappings()
            .all()
        )
        reasons: list[GateReason] = []
        if not run_rows:
            reasons.append(
                GateReason(
                    "NO_PROCESSING_RUN", 1, "dataset version has no processing run"
                )
            )
        not_ready = sum(row["status"] not in {"READY", "PUBLISHED"} for row in run_rows)
        if not_ready:
            reasons.append(
                GateReason("RUN_NOT_READY", not_ready, "processing runs are not ready")
            )
        bad_lineage = sum(not bool(row["lineage_matches"]) for row in run_rows)
        if bad_lineage:
            reasons.append(
                GateReason(
                    "INPUT_LINEAGE_MISMATCH",
                    bad_lineage,
                    "processing runs are not attributable to the declared input batch",
                )
            )
        duplicate_sources = int(
            connection.execute(
                text(
                    "SELECT COUNT(*) FROM (SELECT pr.source_file_id FROM "
                    "dataset.dataset_version_run dvr JOIN ingestion.processing_run pr "
                    "ON pr.processing_run_id=dvr.processing_run_id "
                    "WHERE dvr.dataset_version_id=:version_id GROUP BY pr.source_file_id "
                    "HAVING COUNT(*)>1) duplicates"
                ),
                {"version_id": version_id},
            ).scalar_one()
        )
        if duplicate_sources:
            reasons.append(
                GateReason(
                    "DUPLICATE_SOURCE_RUN",
                    duplicate_sources,
                    "multiple processing runs reference the same immutable source",
                )
            )
        identity_mismatches = int(
            connection.execute(
                text(
                    "SELECT COUNT(*) FROM dataset.dataset_version_run dvr "
                    "JOIN ingestion.processing_run pr ON pr.processing_run_id=dvr.processing_run_id "
                    "WHERE dvr.dataset_version_id=:version_id AND ("
                    "NOT EXISTS(SELECT 1 FROM test.test_run tr WHERE tr.processing_run_id=pr.processing_run_id) OR "
                    "EXISTS(SELECT 1 FROM test.test_run tr WHERE tr.processing_run_id=pr.processing_run_id AND ("
                    "tr.test_stage<>:test_stage OR (:supplier_id IS NOT NULL AND tr.supplier_id<>:supplier_id) OR "
                    "(:product_id IS NOT NULL AND (tr.product_id IS NULL OR tr.product_id<>:product_id)))))"
                ),
                {
                    "version_id": version_id,
                    "test_stage": context["test_stage"],
                    "supplier_id": context["supplier_id"],
                    "product_id": context["product_id"],
                },
            ).scalar_one()
        )
        if identity_mismatches:
            reasons.append(
                GateReason(
                    "DATASET_IDENTITY_MISMATCH",
                    identity_mismatches,
                    "canonical run identity does not match the dataset scope",
                )
            )
        blocking_issues = int(
            connection.execute(
                text(
                    "SELECT COUNT(*) FROM dataset.dataset_version_run dvr "
                    "JOIN ingestion.data_quality_issue dqi ON dqi.processing_run_id=dvr.processing_run_id "
                    "JOIN ingestion.data_quality_rule dqr ON dqr.rule_id=dqi.rule_id "
                    "LEFT JOIN ingestion.dq_rule_version dqrv ON dqrv.dq_rule_version_id=dqi.dq_rule_version_id "
                    "WHERE dvr.dataset_version_id=:version_id AND ("
                    "(dqi.resolution_status='OPEN' AND (dqi.severity='BLOCKER' OR "
                    "ISNULL(dqrv.is_blocking,dqr.is_blocking)=1)) OR "
                    "(dqi.resolution_status='WAIVED' AND dqi.severity='BLOCKER'))"
                ),
                {"version_id": version_id},
            ).scalar_one()
        )
        if blocking_issues:
            reasons.append(
                GateReason(
                    "BLOCKING_DQ_ISSUE",
                    blocking_issues,
                    "open blocking quality issues prevent publication",
                )
            )
        return DqGateResult(
            dataset_id=dataset_id,
            version_no=version_no,
            status="PASS" if not reasons else "BLOCKED",
            run_count=len(run_rows),
            unit_count=sum(int(row["unit_count"]) for row in run_rows),
            measurement_count=sum(int(row["measurement_count"]) for row in run_rows),
            reasons=tuple(reasons),
        )

    def evaluate_gate(self, dataset_id: int, version_no: int) -> DqGateResult:
        with self._engine.connect() as connection:
            return self._evaluate(connection, dataset_id, version_no, lock=False)

    def publish(
        self, dataset_id: int, version_no: int, request: PublishDatasetVersionRequest
    ) -> DatasetVersionRecord:
        with self._engine.begin() as connection:
            context = self._version_context(
                connection, dataset_id, version_no, lock=True
            )
            version_id = int(context["dataset_version_id"])
            run_count = int(
                connection.execute(
                    text(
                        "SELECT COUNT(*) FROM dataset.dataset_version_run "
                        "WHERE dataset_version_id=:version_id"
                    ),
                    {"version_id": version_id},
                ).scalar_one()
            )
            if context["status"] == "PUBLISHED" and bool(context["is_current"]):
                return _version(context, run_count=run_count)
            if context["status"] not in {"DRAFT", "VALIDATING"}:
                raise DomainError(
                    "DATASET_VERSION_NOT_PUBLISHABLE",
                    "dataset version is not in a publishable state",
                    409,
                )
            user_status = connection.execute(
                text("SELECT status FROM iam.app_user WHERE user_id=:user_id"),
                {"user_id": request.published_by},
            ).scalar_one_or_none()
            if user_status != "ACTIVE":
                raise DomainError(
                    "PUBLISHER_NOT_ACTIVE",
                    "publisher must be an active application user",
                    409,
                )
            gate = self._evaluate(connection, dataset_id, version_no, lock=False)
            if gate.status != "PASS":
                raise DomainError(
                    "DQ_GATE_BLOCKED",
                    "dataset version cannot be published because the DQ gate is blocked",
                    409,
                    details=[
                        {
                            "code": reason.code,
                            "count": reason.count,
                            "message": reason.message,
                        }
                        for reason in gate.reasons
                    ],
                )
            previous_id = connection.execute(
                text(
                    "SELECT dataset_version_id FROM dataset.dataset_version WITH (UPDLOCK,HOLDLOCK) "
                    "WHERE dataset_id=:dataset_id AND status='PUBLISHED' AND is_current=1"
                ),
                {"dataset_id": dataset_id},
            ).scalar_one_or_none()
            if previous_id is not None and int(previous_id) != version_id:
                connection.execute(
                    text(
                        "UPDATE dataset.dataset_version SET status='SUPERSEDED',is_current=0 "
                        "WHERE dataset_version_id=:previous_id"
                    ),
                    {"previous_id": previous_id},
                )
            connection.execute(
                text(
                    "UPDATE prior SET prior.status='SUPERSEDED',prior.is_current=0 "
                    "FROM ingestion.processing_run prior JOIN ingestion.processing_run target "
                    "ON target.source_file_id=prior.source_file_id "
                    "JOIN dataset.dataset_version_run dvr ON dvr.processing_run_id=target.processing_run_id "
                    "WHERE dvr.dataset_version_id=:version_id AND prior.status='PUBLISHED' "
                    "AND prior.is_current=1 AND prior.processing_run_id<>target.processing_run_id"
                ),
                {"version_id": version_id},
            )
            connection.execute(
                text(
                    "UPDATE pr SET pr.status='PUBLISHED',pr.is_current=1 "
                    "FROM ingestion.processing_run pr JOIN dataset.dataset_version_run dvr "
                    "ON dvr.processing_run_id=pr.processing_run_id "
                    "WHERE dvr.dataset_version_id=:version_id"
                ),
                {"version_id": version_id},
            )
            row = (
                connection.execute(
                    text(
                        "UPDATE dataset.dataset_version SET status='PUBLISHED',is_current=1,"
                        "row_count=:unit_count,unit_count=:unit_count,"
                        "measurement_count=:measurement_count,published_by=:published_by,"
                        "published_at_utc=SYSUTCDATETIME(),supersedes_dataset_version_id=:previous_id "
                        "OUTPUT INSERTED.dataset_version_id,INSERTED.dataset_id,INSERTED.version_no,"
                        "INSERTED.input_batch_id,INSERTED.canonical_model_version,"
                        "INSERTED.status,INSERTED.is_current "
                        "WHERE dataset_version_id=:version_id"
                    ),
                    {
                        "unit_count": gate.unit_count,
                        "measurement_count": gate.measurement_count,
                        "published_by": request.published_by,
                        "previous_id": previous_id,
                        "version_id": version_id,
                    },
                )
                .mappings()
                .one()
            )
        return _version(row, run_count=run_count)

    def get_summary(self, dataset_id: int, version_no: int) -> DatasetResultSummary:
        with self._engine.connect() as connection:
            base = (
                connection.execute(
                    text(
                        "SELECT d.dataset_code,d.dataset_name,dv.status,dv.is_current,"
                        "COUNT(DISTINCT dvr.processing_run_id) AS run_count,"
                        "COUNT(DISTINCT tr.lot_id) AS lot_count,"
                        "COUNT(DISTINCT CASE WHEN tr.wafer_id IS NOT NULL "
                        "THEN tr.lot_id+'|'+tr.wafer_id END) AS wafer_count,"
                        "COUNT(ur.unit_id) AS unit_count,"
                        "SUM(CASE WHEN ur.overall_result='PASS' THEN 1 ELSE 0 END) AS pass_count,"
                        "SUM(CASE WHEN ur.overall_result='FAIL' THEN 1 ELSE 0 END) AS fail_count "
                        "FROM dataset.dataset d JOIN dataset.dataset_version dv ON dv.dataset_id=d.dataset_id "
                        "LEFT JOIN dataset.dataset_version_run dvr ON dvr.dataset_version_id=dv.dataset_version_id "
                        "LEFT JOIN test.test_run tr ON tr.processing_run_id=dvr.processing_run_id "
                        "LEFT JOIN test.unit_result ur ON ur.run_id=tr.run_id "
                        "WHERE d.dataset_id=:dataset_id AND dv.version_no=:version_no "
                        "GROUP BY d.dataset_code,d.dataset_name,dv.status,dv.is_current"
                    ),
                    {"dataset_id": dataset_id, "version_no": version_no},
                )
                .mappings()
                .one_or_none()
            )
            if base is None:
                raise DomainError(
                    "DATASET_VERSION_NOT_FOUND", "dataset version was not found", 404
                )
            measurement_count = int(
                connection.execute(
                    text(
                        "SELECT COUNT_BIG(*) FROM dataset.dataset_version dv "
                        "JOIN dataset.dataset_version_run dvr ON dvr.dataset_version_id=dv.dataset_version_id "
                        "JOIN test.test_run tr ON tr.processing_run_id=dvr.processing_run_id "
                        "JOIN test.unit_result ur ON ur.run_id=tr.run_id "
                        "JOIN test.measurement m ON m.unit_id=ur.unit_id "
                        "WHERE dv.dataset_id=:dataset_id AND dv.version_no=:version_no"
                    ),
                    {"dataset_id": dataset_id, "version_no": version_no},
                ).scalar_one()
            )
            bin_rows = (
                connection.execute(
                    text(
                        "SELECT ur.soft_bin,COUNT_BIG(*) AS unit_count FROM dataset.dataset_version dv "
                        "JOIN dataset.dataset_version_run dvr ON dvr.dataset_version_id=dv.dataset_version_id "
                        "JOIN test.test_run tr ON tr.processing_run_id=dvr.processing_run_id "
                        "JOIN test.unit_result ur ON ur.run_id=tr.run_id "
                        "WHERE dv.dataset_id=:dataset_id AND dv.version_no=:version_no "
                        "GROUP BY ur.soft_bin ORDER BY ur.soft_bin"
                    ),
                    {"dataset_id": dataset_id, "version_no": version_no},
                )
                .mappings()
                .all()
            )
        unit_count = int(base["unit_count"] or 0)
        pass_count = int(base["pass_count"] or 0)
        return DatasetResultSummary(
            dataset_id=dataset_id,
            dataset_code=str(base["dataset_code"]),
            dataset_name=str(base["dataset_name"]),
            version_no=version_no,
            version_status=str(base["status"]),
            is_current=bool(base["is_current"]),
            run_count=int(base["run_count"] or 0),
            lot_count=int(base["lot_count"] or 0),
            wafer_count=int(base["wafer_count"] or 0),
            unit_count=unit_count,
            pass_count=pass_count,
            fail_count=int(base["fail_count"] or 0),
            yield_rate=pass_count / unit_count if unit_count else 0.0,
            measurement_count=measurement_count,
            bin_counts={
                str(row["soft_bin"]): int(row["unit_count"]) for row in bin_rows
            },
        )
