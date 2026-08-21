/*
  TMS G0 read-only SQL Server inventory.
  Compatible with SQL Server 2014.

  Run manually in SSMS against master. Do not save credentials or raw output
  in Git. Store evidence under evidence/private/ or an approved secure share.
*/

USE master;
GO
SET NOCOUNT ON;
GO

SELECT
    @@SERVERNAME AS server_name,
    CAST(SERVERPROPERTY('MachineName') AS nvarchar(128)) AS machine_name,
    CAST(SERVERPROPERTY('InstanceName') AS nvarchar(128)) AS instance_name,
    CAST(SERVERPROPERTY('ProductVersion') AS nvarchar(128)) AS product_version,
    CAST(SERVERPROPERTY('ProductLevel') AS nvarchar(128)) AS product_level,
    CAST(SERVERPROPERTY('Edition') AS nvarchar(256)) AS edition,
    CAST(SERVERPROPERTY('EngineEdition') AS int) AS engine_edition,
    CAST(SERVERPROPERTY('Collation') AS nvarchar(128)) AS server_collation,
    CAST(SERVERPROPERTY('IsClustered') AS int) AS is_clustered,
    CAST(SERVERPROPERTY('IsHadrEnabled') AS int) AS is_hadr_enabled,
    @@VERSION AS full_version;
GO

SELECT
    windows_release,
    windows_service_pack_level,
    windows_sku,
    os_language_version
FROM sys.dm_os_windows_info;
GO

SELECT
    DB_NAME() AS connected_database,
    ORIGINAL_LOGIN() AS original_login,
    SUSER_SNAME() AS execution_login,
    IS_SRVROLEMEMBER('sysadmin') AS is_sysadmin,
    encrypt_option,
    auth_scheme,
    net_transport,
    protocol_type
FROM sys.dm_exec_connections
WHERE session_id = @@SPID;
GO

SELECT
    name,
    state_desc,
    compatibility_level,
    recovery_model_desc,
    collation_name,
    page_verify_option_desc,
    is_auto_close_on,
    is_auto_shrink_on,
    create_date
FROM sys.databases
ORDER BY database_id;
GO

SELECT
    name,
    value_in_use,
    minimum,
    maximum
FROM sys.configurations
WHERE name IN (
    'max server memory (MB)',
    'min server memory (MB)',
    'max degree of parallelism',
    'cost threshold for parallelism',
    'backup compression default',
    'optimize for ad hoc workloads'
)
ORDER BY name;
GO

SELECT DISTINCT
    vs.volume_mount_point,
    vs.file_system_type,
    CONVERT(decimal(18,2), vs.total_bytes / 1048576.0) AS total_mb,
    CONVERT(decimal(18,2), vs.available_bytes / 1048576.0) AS available_mb
FROM sys.master_files AS mf
CROSS APPLY sys.dm_os_volume_stats(mf.database_id, mf.file_id) AS vs
ORDER BY vs.volume_mount_point;
GO

;WITH latest_backup AS (
    SELECT
        database_name,
        type,
        MAX(backup_finish_date) AS last_backup_finish_date
    FROM msdb.dbo.backupset
    GROUP BY database_name, type
)
SELECT
    database_name,
    MAX(CASE WHEN type = 'D' THEN last_backup_finish_date END) AS last_full_backup,
    MAX(CASE WHEN type = 'I' THEN last_backup_finish_date END) AS last_diff_backup,
    MAX(CASE WHEN type = 'L' THEN last_backup_finish_date END) AS last_log_backup
FROM latest_backup
GROUP BY database_name
ORDER BY database_name;
GO
