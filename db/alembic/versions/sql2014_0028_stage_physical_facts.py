"""Move CP/FT facts to separate physical tables, preserving every public ID."""

import sys
from pathlib import Path

from alembic import op
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from migration_helpers import irreversible_downgrade

revision = "sql2014_0028"
down_revision = "sql2014_0027"
branch_labels = None
depends_on = None

# Frozen migration contract. Do not import application models here.
UNIT = [
    ("unit_id", "bigint NOT NULL"),
    ("run_id", "bigint NOT NULL"),
    ("logical_unit_key", "nvarchar(300) NOT NULL"),
    ("attempt_no", "smallint NOT NULL DEFAULT(0)"),
    ("unit_sequence", "bigint NULL"),
    ("vendor_unit_id", "nvarchar(128) NULL"),
    ("wafer_id", "nvarchar(64) NULL"),
    ("x_coord", "int NULL"),
    ("y_coord", "int NULL"),
    ("site_no", "smallint NULL"),
    ("serial_no", "nvarchar(128) NULL"),
    ("soft_bin", "nvarchar(32) NULL"),
    ("hard_bin", "nvarchar(32) NULL"),
    ("overall_result", "varchar(16) NOT NULL DEFAULT('UNKNOWN')"),
    ("fail_test_no", "nvarchar(64) NULL"),
    ("fail_test_name", "nvarchar(200) NULL"),
    ("test_duration_ms", "bigint NULL"),
    ("source_row_no", "int NULL"),
    ("metadata_json", "nvarchar(max) NULL"),
    ("created_at_utc", "datetime2(3) NOT NULL DEFAULT(SYSUTCDATETIME())"),
]
MEASUREMENT = [
    ("measurement_id", "bigint NOT NULL"),
    ("unit_id", "bigint NOT NULL"),
    ("test_item_id", "bigint NOT NULL"),
    ("value_numeric", "float(53) NULL"),
    ("value_text", "nvarchar(256) NULL"),
    ("raw_value", "nvarchar(256) NULL"),
    ("measurement_status", "varchar(24) NOT NULL"),
    ("tester_pass_flag", "bit NULL"),
    ("source_column_index", "int NULL"),
    ("created_at_utc", "datetime2(3) NOT NULL DEFAULT(SYSUTCDATETIME())"),
]
COORDINATES = {"wafer_id", "x_coord", "y_coord"}


def _assert_equal(c, old: str, new: str, columns: list) -> None:
    key = columns[0][0]
    counts = c.execute(
        text(
            f"SELECT (SELECT COUNT_BIG(*) FROM {old}),(SELECT COUNT_BIG(*) FROM {new})"
        )
    ).one()
    if counts[0] != counts[1]:
        raise RuntimeError(f"physical migration count mismatch: {old}")
    checks = [f"b.{key} IS NULL"]
    for col, typ in columns:
        left, right = f"a.{col}", f"b.{col}"
        # Compare source text byte-for-byte, including case/trailing spaces;
        # preserve the exact stored float bit pattern as well.
        if "char" in typ or "float" in typ:
            size = "8" if "float" in typ else "max"
            left, right = (
                f"CONVERT(varbinary({size}),{left})",
                f"CONVERT(varbinary({size}),{right})",
            )
        checks.append(
            f"({left}<>{right} OR (a.{col} IS NULL AND b.{col} IS NOT NULL) OR (a.{col} IS NOT NULL AND b.{col} IS NULL))"
        )
    mismatch = c.execute(
        text(
            f"SELECT TOP(1) a.{key} FROM {old} a LEFT JOIN {new} b ON b.{key}=a.{key} WHERE "
            + " OR ".join(checks)
        )
    ).first()
    if mismatch:
        raise RuntimeError(
            f"physical migration value mismatch: {old}, ID={mismatch[0]}"
        )


def upgrade() -> None:
    c = op.get_bind()
    # Estimate copy/index/log working space on the SQL Server's volumes, not on
    # the client workstation. This guard is conservative, not a capacity SLA.
    source_mb = float(
        c.execute(
            text(
                "SELECT COALESCE(SUM(reserved_page_count),0)/128.0 FROM sys.dm_db_partition_stats "
                "WHERE object_id IN(OBJECT_ID('test.unit_result'),OBJECT_ID('test.measurement'))"
            )
        ).scalar_one()
    )
    files = (
        c.execute(
            text(
                "SELECT f.growth,(f.size-FILEPROPERTY(f.name,'SpaceUsed'))/128.0 AS free_mb,"
                "v.volume_mount_point,v.available_bytes/1048576.0 AS volume_free_mb "
                "FROM sys.database_files f CROSS APPLY sys.dm_os_volume_stats(DB_ID(),f.file_id) v"
            )
        )
        .mappings()
        .all()
    )
    growable_volumes = {
        r["volume_mount_point"]: float(r["volume_free_mb"])
        for r in files
        if r["growth"]
    }
    available_mb = sum(float(r["free_mb"]) for r in files) + sum(
        growable_volumes.values()
    )
    required_mb = source_mb * 3 + 512
    if available_mb < required_mb:
        raise RuntimeError(
            f"physical migration capacity guard: need about {required_mb:.0f} MiB, have {available_mb:.0f} MiB; provision database data/log space first"
        )
    if c.execute(
        text(
            "SELECT COUNT(*) FROM ingestion.processing_job WHERE status IN('QUEUED','RUNNING')"
        )
    ).scalar_one():
        raise RuntimeError("physical migration requires drained jobs")
    if c.execute(
        text(
            "SELECT COUNT(*) FROM ingestion.initial_import_finalize_intent WHERE status='STAGED'"
        )
    ).scalar_one():
        raise RuntimeError("physical migration requires finalized import intents")
    if c.execute(
        text("SELECT COUNT(*) FROM test.test_run WHERE test_stage NOT IN('CP','FT')")
    ).scalar_one():
        raise RuntimeError("unsupported stages require their own physical migration")
    if c.execute(
        text(
            "SELECT COUNT(*) FROM test.unit_result u JOIN test.test_run r ON r.run_id=u.run_id "
            "WHERE r.test_stage='FT' AND (u.wafer_id IS NOT NULL OR u.x_coord IS NOT NULL OR u.y_coord IS NOT NULL)"
        )
    ).scalar_one():
        raise RuntimeError("FT coordinate evidence must be resolved before migration")

    for kind, old in (("unit", "unit_result"), ("measurement", "measurement")):
        first = int(
            c.execute(
                text(f"SELECT COALESCE(MAX({kind}_id),0)+1 FROM test.{old}")
            ).scalar_one()
        )
        c.exec_driver_sql(
            f"CREATE SEQUENCE test.{kind}_id_sequence AS bigint START WITH {first} INCREMENT BY 1 NO CYCLE CACHE 1000"
        )
        c.exec_driver_sql(f"""CREATE TABLE test.{kind}_identity (
            {kind}_id bigint NOT NULL CONSTRAINT PK_{kind}_identity PRIMARY KEY CLUSTERED,
            test_stage varchar(16) NOT NULL CONSTRAINT CK_{kind}_identity_stage CHECK(test_stage IN('CP','FT')),
            CONSTRAINT UQ_{kind}_identity_stage UNIQUE({kind}_id,test_stage)
        )""")
    c.exec_driver_sql(
        "INSERT test.unit_identity SELECT u.unit_id,r.test_stage FROM test.unit_result u JOIN test.test_run r ON r.run_id=u.run_id"
    )
    c.exec_driver_sql(
        "INSERT test.measurement_identity SELECT m.measurement_id,i.test_stage FROM test.measurement m JOIN test.unit_identity i ON i.unit_id=m.unit_id"
    )
    for stage, unit in (("CP", "cp_die"), ("FT", "ft_device")):
        cols = [(n, t) for n, t in UNIT if stage == "CP" or n not in COORDINATES]
        definitions = ",".join(f"{n} {t}" for n, t in cols)
        c.exec_driver_sql(f"""CREATE TABLE test.{unit} (
            {definitions}, test_stage varchar(16) NOT NULL DEFAULT('{stage}'),
            CONSTRAINT PK_{unit} PRIMARY KEY CLUSTERED(unit_id),
            CONSTRAINT UQ_{unit}_attempt UNIQUE(run_id,logical_unit_key,attempt_no),
            CONSTRAINT CK_{unit}_stage CHECK(test_stage='{stage}'),
            CONSTRAINT CK_{unit}_result CHECK(overall_result IN('PASS','FAIL','ABORT','UNKNOWN')),
            CONSTRAINT FK_{unit}_identity FOREIGN KEY(unit_id,test_stage) REFERENCES test.unit_identity(unit_id,test_stage),
            CONSTRAINT FK_{unit}_run FOREIGN KEY(run_id,test_stage) REFERENCES test.test_run(run_id,test_stage)
        )""")
        names = ",".join(n for n, _ in cols)
        c.exec_driver_sql(
            f"INSERT test.{unit}({names}) SELECT {','.join('u.' + n for n, _ in cols)} FROM test.unit_result u JOIN test.test_run r ON r.run_id=u.run_id WHERE r.test_stage='{stage}'"
        )
        if stage == "CP":
            c.exec_driver_sql(
                f"CREATE INDEX IX_{unit}_run ON test.{unit}(run_id,wafer_id,unit_id) INCLUDE(unit_sequence,x_coord,y_coord,overall_result,soft_bin,hard_bin)"
            )
            c.exec_driver_sql(
                f"CREATE INDEX IX_{unit}_xy ON test.{unit}(run_id,x_coord,y_coord,attempt_no) WHERE x_coord IS NOT NULL AND y_coord IS NOT NULL"
            )
        else:
            c.exec_driver_sql(
                f"CREATE INDEX IX_{unit}_run ON test.{unit}(run_id,unit_sequence,unit_id) INCLUDE(overall_result,soft_bin,hard_bin)"
            )
        c.exec_driver_sql(
            f"CREATE INDEX IX_{unit}_logical ON test.{unit}(logical_unit_key,attempt_no) INCLUDE(run_id,overall_result,soft_bin,hard_bin)"
        )
        measurement = stage.lower() + "_measurement"
        definitions = ",".join(f"{n} {t}" for n, t in MEASUREMENT)
        c.exec_driver_sql(f"""CREATE TABLE test.{measurement} (
            {definitions}, test_stage varchar(16) NOT NULL DEFAULT('{stage}'),
            CONSTRAINT PK_{measurement} PRIMARY KEY CLUSTERED(measurement_id),
            CONSTRAINT CK_{measurement}_stage CHECK(test_stage='{stage}'),
            CONSTRAINT CK_{measurement}_status CHECK(measurement_status IN('MEASURED','OVER_RANGE','UNDER_RANGE','NOT_TESTED','MISSING','INVALID','NOT_APPLICABLE')),
            CONSTRAINT FK_{measurement}_identity FOREIGN KEY(measurement_id,test_stage) REFERENCES test.measurement_identity(measurement_id,test_stage),
            CONSTRAINT FK_{measurement}_unit FOREIGN KEY(unit_id) REFERENCES test.{unit}(unit_id),
            CONSTRAINT FK_{measurement}_item FOREIGN KEY(test_item_id) REFERENCES mdm.test_item_definition(test_item_id)
        )""")
        names = ",".join(n for n, _ in MEASUREMENT)
        c.exec_driver_sql(
            f"INSERT test.{measurement}({names}) SELECT {','.join('m.' + n for n, _ in MEASUREMENT)} FROM test.measurement m JOIN test.{unit} u ON u.unit_id=m.unit_id"
        )
        c.exec_driver_sql(
            f"CREATE INDEX IX_{measurement}_unit ON test.{measurement}(unit_id,test_item_id) INCLUDE(value_numeric,value_text,measurement_status,tester_pass_flag)"
        )
        c.exec_driver_sql(
            f"CREATE INDEX IX_{measurement}_item ON test.{measurement}(test_item_id,unit_id) INCLUDE(value_numeric,measurement_status)"
        )

    # Polymorphic references keep native, trusted FKs through the narrow ID registries.
    for table, name, column, kind in (
        ("test.unit_bin_evaluation", "FK_unit_bin_eval_unit", "unit_id", "unit"),
        ("trace.unit_traceability", "FK_trace_source", "source_unit_id", "unit"),
        ("trace.unit_traceability", "FK_trace_target", "target_unit_id", "unit"),
        (
            "test.measurement_evaluation",
            "FK_measurement_eval_measurement",
            "measurement_id",
            "measurement",
        ),
    ):
        c.exec_driver_sql(f"ALTER TABLE {table} DROP CONSTRAINT {name}")
        c.exec_driver_sql(
            f"ALTER TABLE {table} WITH CHECK ADD CONSTRAINT {name} FOREIGN KEY({column}) REFERENCES test.{kind}_identity({kind}_id)"
        )
    for old in ("measurement", "unit_result"):
        c.exec_driver_sql(f"EXEC sys.sp_rename N'test.{old}',N'{old}_legacy_0027'")
    unit_columns = ",".join(n for n, _ in UNIT)
    ft_columns = ",".join(
        f"CAST(NULL AS {'nvarchar(64)' if n == 'wafer_id' else 'int'}) AS {n}"
        if n in COORDINATES
        else n
        for n, _ in UNIT
    )
    c.exec_driver_sql(
        f"CREATE VIEW test.unit_result AS SELECT {unit_columns} FROM test.cp_die UNION ALL SELECT {ft_columns} FROM test.ft_device"
    )
    names = ",".join(n for n, _ in MEASUREMENT)
    c.exec_driver_sql(
        f"CREATE VIEW test.measurement AS SELECT {names} FROM test.cp_measurement UNION ALL SELECT {names} FROM test.ft_measurement"
    )
    _assert_equal(c, "test.unit_result_legacy_0027", "test.unit_result", UNIT)
    _assert_equal(c, "test.measurement_legacy_0027", "test.measurement", MEASUREMENT)

    for old, kind, targets in (
        ("unit_result", "unit", ("cp_die", "ft_device")),
        ("measurement", "measurement", ("cp_measurement", "ft_measurement")),
    ):
        c.exec_driver_sql(f"""CREATE TRIGGER test.TR_{old}_legacy_0027_readonly ON test.{old}_legacy_0027
            INSTEAD OF INSERT,UPDATE,DELETE AS BEGIN SET NOCOUNT ON;
            THROW 51028,'Migration archive is read-only',1; END""")
        deletes = " ".join(
            f"DELETE p FROM test.{table} p JOIN deleted d ON d.{kind}_id=p.{kind}_id;"
            for table in targets
        )
        c.exec_driver_sql(f"""CREATE TRIGGER test.TR_{old}_delete ON test.{old}
            INSTEAD OF DELETE AS BEGIN SET NOCOUNT ON; {deletes}
            DELETE i FROM test.{kind}_identity i JOIN deleted d ON d.{kind}_id=i.{kind}_id; END""")

    for view, old, new in (
        ("test.v_cp_die", "FROM test.unit_result u", "FROM test.cp_die u"),
        ("test.v_ft_device", "FROM test.unit_result u", "FROM test.ft_device u"),
        (
            "test.v_cp_measurement",
            "FROM test.measurement m",
            "FROM test.cp_measurement m",
        ),
        (
            "test.v_ft_measurement",
            "FROM test.measurement m",
            "FROM test.ft_measurement m",
        ),
    ):
        definition = c.execute(
            text("SELECT OBJECT_DEFINITION(OBJECT_ID(:view))"), {"view": view}
        ).scalar_one()
        if old not in definition or "CREATE VIEW" not in definition:
            raise RuntimeError(f"unexpected stage view definition: {view}")
        c.exec_driver_sql(
            definition.replace("CREATE VIEW", "ALTER VIEW", 1).replace(old, new)
        )
    # Rebind existing views after sp_rename. They must never retain archive object IDs.
    for view in (
        "analytics.v_current_dataset_version",
        "analytics.v_current_test_run",
        "analytics.v_current_unit_result",
        "analytics.v_current_measurement",
        "test.v_cp_die",
        "test.v_ft_device",
        "test.v_cp_measurement",
        "test.v_ft_measurement",
    ):
        c.exec_driver_sql(f"EXEC sys.sp_refreshview N'{view}'")
    c.exec_driver_sql("SET NOCOUNT OFF")


def downgrade() -> None:
    irreversible_downgrade()
