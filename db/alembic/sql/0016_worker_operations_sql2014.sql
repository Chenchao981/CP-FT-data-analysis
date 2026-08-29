SET XACT_ABORT ON;
GO

IF SCHEMA_ID(N'ingestion') IS NULL
    RAISERROR('sql2014_0016 blocked: ingestion schema is missing.', 16, 1);
IF OBJECT_ID(N'ingestion.processing_job', N'U') IS NULL
    RAISERROR('sql2014_0016 blocked: ingestion.processing_job is missing.', 16, 1);
GO

/*
Current Worker registration and durable drain control. Host identity is stored
only as a one-way fingerprint; accounts, paths, connection strings and secrets
do not belong in this table.
*/
IF OBJECT_ID(N'ingestion.worker_instance', N'U') IS NULL
BEGIN
    CREATE TABLE ingestion.worker_instance (
        worker_id nvarchar(128) NOT NULL,
        worker_kind varchar(32) NOT NULL,
        state varchar(16) NOT NULL,
        desired_state varchar(8) NOT NULL
            CONSTRAINT DF_worker_instance_desired_state DEFAULT('RUN'),
        started_at_utc datetime2(3) NOT NULL,
        last_seen_at_utc datetime2(3) NOT NULL,
        stopped_at_utc datetime2(3) NULL,
        database_name nvarchar(128) NOT NULL,
        schema_revision varchar(128) NOT NULL,
        host_fingerprint char(64) NOT NULL,
        control_updated_at_utc datetime2(3) NOT NULL
            CONSTRAINT DF_worker_instance_control_updated DEFAULT(SYSUTCDATETIME()),
        row_version rowversion NOT NULL,
        CONSTRAINT PK_worker_instance PRIMARY KEY CLUSTERED(worker_id),
        CONSTRAINT CK_worker_instance_id CHECK(
            LEN(worker_id) BETWEEN 1 AND 128
            AND worker_id NOT LIKE N'% %'
            AND CHARINDEX(N'/',worker_id)=0
            AND CHARINDEX(N'\',worker_id)=0
            AND CHARINDEX(N'@',worker_id)=0
            AND CHARINDEX(N':',worker_id)=0
        ),
        CONSTRAINT CK_worker_instance_kind CHECK(worker_kind='ROUTE_A'),
        CONSTRAINT CK_worker_instance_state CHECK(
            state IN('READY','DRAINING','STOPPED','FAILED')
        ),
        CONSTRAINT CK_worker_instance_desired_state CHECK(
            desired_state IN('RUN','DRAIN')
        ),
        CONSTRAINT CK_worker_instance_lifecycle CHECK(
            (state IN('READY','DRAINING') AND stopped_at_utc IS NULL)
            OR
            (state IN('STOPPED','FAILED') AND stopped_at_utc IS NOT NULL)
        ),
        CONSTRAINT CK_worker_instance_timestamps CHECK(
            last_seen_at_utc>=started_at_utc
            AND (stopped_at_utc IS NULL OR stopped_at_utc>=started_at_utc)
        ),
        CONSTRAINT CK_worker_instance_host_fingerprint CHECK(
            LEN(host_fingerprint)=64
            AND host_fingerprint NOT LIKE '%[^0-9A-Fa-f]%'
        )
    );
END;
ELSE IF (
    SELECT COUNT(*)
    FROM sys.columns
    WHERE object_id=OBJECT_ID(N'ingestion.worker_instance')
)<>12 OR (
    SELECT COUNT(*)
    FROM sys.columns
    WHERE object_id=OBJECT_ID(N'ingestion.worker_instance')
      AND name IN(
          N'worker_id',
          N'worker_kind',
          N'state',
          N'desired_state',
          N'started_at_utc',
          N'last_seen_at_utc',
          N'stopped_at_utc',
          N'database_name',
          N'schema_revision',
          N'host_fingerprint',
          N'control_updated_at_utc',
          N'row_version'
      )
)<>12
BEGIN
    RAISERROR(
        'sql2014_0016 blocked: worker_instance has an incompatible column contract.',
        16,
        1
    );
END;
GO

IF EXISTS (
    SELECT 1
    FROM sys.columns AS c
    JOIN sys.types AS t ON t.user_type_id=c.user_type_id
    WHERE c.object_id=OBJECT_ID(N'ingestion.worker_instance')
      AND (
          (c.name=N'worker_id' AND
              (t.name<>N'nvarchar' OR c.max_length<>256 OR c.is_nullable<>0))
          OR (c.name=N'worker_kind' AND
              (t.name<>N'varchar' OR c.max_length<>32 OR c.is_nullable<>0))
          OR (c.name=N'state' AND
              (t.name<>N'varchar' OR c.max_length<>16 OR c.is_nullable<>0))
          OR (c.name=N'desired_state' AND
              (t.name<>N'varchar' OR c.max_length<>8 OR c.is_nullable<>0))
          OR (c.name=N'started_at_utc' AND
              (t.name<>N'datetime2' OR c.scale<>3 OR c.is_nullable<>0))
          OR (c.name=N'last_seen_at_utc' AND
              (t.name<>N'datetime2' OR c.scale<>3 OR c.is_nullable<>0))
          OR (c.name=N'stopped_at_utc' AND
              (t.name<>N'datetime2' OR c.scale<>3 OR c.is_nullable<>1))
          OR (c.name=N'database_name' AND
              (t.name<>N'nvarchar' OR c.max_length<>256 OR c.is_nullable<>0))
          OR (c.name=N'schema_revision' AND
              (t.name<>N'varchar' OR c.max_length<>128 OR c.is_nullable<>0))
          OR (c.name=N'host_fingerprint' AND
              (t.name<>N'char' OR c.max_length<>64 OR c.is_nullable<>0))
          OR (c.name=N'control_updated_at_utc' AND
              (t.name<>N'datetime2' OR c.scale<>3 OR c.is_nullable<>0))
          OR (c.name=N'row_version' AND
              (t.name<>N'timestamp' OR c.max_length<>8 OR c.is_nullable<>0))
      )
)
BEGIN
    RAISERROR(
        'sql2014_0016 blocked: worker_instance has an incompatible column definition.',
        16,
        1
    );
END;
GO

IF OBJECT_ID(N'ingestion.worker_instance', N'U') IS NOT NULL
   AND (
       SELECT COUNT(*)
       FROM sys.objects
       WHERE parent_object_id=OBJECT_ID(N'ingestion.worker_instance')
         AND name IN(
             N'PK_worker_instance',
             N'CK_worker_instance_id',
             N'CK_worker_instance_kind',
             N'CK_worker_instance_state',
             N'CK_worker_instance_desired_state',
             N'CK_worker_instance_lifecycle',
             N'CK_worker_instance_timestamps',
             N'CK_worker_instance_host_fingerprint'
         )
   )<>8
BEGIN
    RAISERROR(
        'sql2014_0016 blocked: worker_instance constraints are incomplete.',
        16,
        1
    );
END;
GO

IF OBJECT_ID(N'ingestion.worker_instance', N'U') IS NOT NULL
   AND (
       SELECT COUNT(*)
       FROM sys.default_constraints
       WHERE parent_object_id=OBJECT_ID(N'ingestion.worker_instance')
         AND name IN(
             N'DF_worker_instance_desired_state',
             N'DF_worker_instance_control_updated'
         )
   )<>2
BEGIN
    RAISERROR(
        'sql2014_0016 blocked: worker_instance defaults are incomplete.',
        16,
        1
    );
END;
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE object_id=OBJECT_ID(N'ingestion.worker_instance')
      AND name=N'IX_worker_instance_health'
)
    CREATE NONCLUSTERED INDEX IX_worker_instance_health
    ON ingestion.worker_instance(state,last_seen_at_utc,worker_id)
    INCLUDE(worker_kind,desired_state,database_name,schema_revision);
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE object_id=OBJECT_ID(N'ingestion.worker_instance')
      AND name=N'IX_worker_instance_control'
)
    CREATE NONCLUSTERED INDEX IX_worker_instance_control
    ON ingestion.worker_instance(desired_state,state,last_seen_at_utc)
    INCLUDE(worker_kind,worker_id);
GO
