# Copy this file to the deployment root as .env.runtime.ps1, replace every
# __PLACEHOLDER__, and keep the copied file readable only by Administrators and
# the service account.  Secrets are deliberately not assigned in this file.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if ([string]::IsNullOrWhiteSpace($env:TMS_JWT_SECRET)) {
    throw 'TMS_JWT_SECRET must be injected by the approved Windows secret mechanism.'
}
if ([string]::IsNullOrWhiteSpace($env:TMS_HEALTH_BEARER_TOKEN)) {
    throw 'TMS_HEALTH_BEARER_TOKEN must be injected for the authenticated production probe.'
}

$env:TMS_ENV = 'production'
$env:TMS_AUTH_REQUIRED = 'true'
$env:TMS_ANALYTICS_OVERVIEW_ENABLED = 'true'
$env:TMS_ANALYTICS_DETAIL_ENABLED = 'true'
$env:TMS_ANALYTICS_PARAMETER_ENABLED = 'true'
$env:TMS_ANALYTICS_SPATIAL_ENABLED = 'true'
$env:TMS_ANALYTICS_QUALITY_ENABLED = 'true'
$env:TMS_ANALYTICS_DELIVERY_ENABLED = 'true'
$env:TMS_JOB_REPOSITORY = 'sql'
$env:TMS_ACCESS_TOKEN_MINUTES = '480'

# Integrated Security only.  Do not add UID, PWD, user-info, or password fields.
$env:TMS_DATABASE_URL = 'mssql+pyodbc://@__SQL_SERVER__/__PRODUCTION_DATABASE__?driver=ODBC+Driver+17+for+SQL+Server&trusted_connection=yes'
$env:TMS_EXPECTED_DATABASE = '__PRODUCTION_DATABASE__'
$env:TMS_EXPECTED_DATABASE_SERVER = '__SQL_SERVER__'
$env:TMS_EXPECTED_SCHEMA_REVISION = '__RELEASE_SCHEMA_HEAD__'

$env:TMS_SOURCE_ROOTS_JSON = @'
[
  {
    "code": "__SOURCE_CODE__",
    "name": "__SOURCE_NAME__",
    "path": "D:\\TMS\\source\\__SOURCE_CODE__",
    "purpose": "FORMAL_IMPORT",
    "business_domains": ["ENGINEERING", "PRODUCTION"],
    "test_stage": "FT",
    "factory_code": "__FACTORY_CODE__",
    "allowed_suffixes": [".xlsx"]
  }
]
'@
$env:TMS_UPLOAD_ROOT = 'D:\TMS\upload'
$env:TMS_WORK_ROOT = 'D:\TMS\work'
$env:TMS_QUICK_WORK_ROOT = 'D:\TMS\quick-work'
$env:TMS_ANALYTICS_EXPORT_ROOT = 'D:\TMS\analytics-exports'
$env:TMS_ANALYTICS_EXPORT_CLEANUP_STALE_MINUTES = '30'
$env:TMS_LOG_DIR = 'D:\TMS\logs'

$env:TMS_PROCESS_NAME = 'tms'
$env:TMS_LOG_LEVEL = 'INFO'
$env:TMS_LOG_MAX_BYTES = '10485760'
$env:TMS_LOG_BACKUP_COUNT = '10'
$env:TMS_LOG_RETENTION_DAYS = '30'

$env:TMS_API_HOST = '127.0.0.1'
$env:TMS_API_PORT = '8000'
$env:TMS_WORKER_ID = '__HOST_SPECIFIC_WORKER_ID__'
$env:TMS_WORKER_READY_FILE = 'D:\TMS\run\route-a-worker.ready.json'
$env:TMS_WORKER_STOP_FILE = 'D:\TMS\run\route-a-worker.stop'

$env:TMS_QUICK_CLEANUP_RETENTION_HOURS = '168'
$env:TMS_QUICK_CLEANUP_LIMIT = '500'
$env:TMS_FORMAL_ORPHAN_RETENTION_HOURS = '168'
$env:TMS_FORMAL_ORPHAN_MAX_ENTRIES = '100000'
$env:TMS_FORMAL_ORPHAN_MAX_BYTES = '53687091200'
