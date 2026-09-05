[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][ValidatePattern('^[A-Z][A-Z0-9_-]{1,63}$')][string]$Reference,
    [string]$PythonPath
)
$ErrorActionPreference = 'Stop'
$workspace = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$python = if ($PythonPath) { (Resolve-Path -LiteralPath $PythonPath).Path } else { Join-Path $workspace '.conda-env\python.exe' }
$entry = Join-Path $workspace 'scripts\configure_ftp_credential.py'
$credential = Get-Credential -Message 'Save FTP credential for this Windows runtime account'
if ($null -eq $credential) { throw 'Credential entry was cancelled.' }
$ftpPreviousOutputEncoding = $OutputEncoding
try {
    $OutputEncoding = [System.Text.UTF8Encoding]::new($false)
    $payload = @{ username = $credential.UserName; password = $credential.GetNetworkCredential().Password } | ConvertTo-Json -Compress
    $payload | & $python -X utf8 $entry --reference $Reference
    if ($LASTEXITCODE -ne 0) { throw 'FTP credential storage failed.' }
} finally {
    $OutputEncoding = $ftpPreviousOutputEncoding
    $payload = $null
    $credential = $null
}
