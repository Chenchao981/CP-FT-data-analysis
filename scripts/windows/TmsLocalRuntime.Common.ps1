Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Get-TmsLocalMutex {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Workspace
    )

    $sha256 = [Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [Text.Encoding]::UTF8.GetBytes($Workspace.ToLowerInvariant())
        $hash = ([BitConverter]::ToString($sha256.ComputeHash($bytes))).Replace('-', '')
    } finally {
        $sha256.Dispose()
    }
    $mutex = New-Object Threading.Mutex($false, "Local\NCE_TMS_LOCAL_$($hash.Substring(0, 20))")
    try {
        $acquired = $mutex.WaitOne(0)
    } catch [Threading.AbandonedMutexException] {
        $acquired = $true
    }
    if (-not $acquired) {
        $mutex.Dispose()
        throw 'Another TMS local start or stop operation is already running.'
    }
    return $mutex
}

function Exit-TmsLocalMutex {
    param([Threading.Mutex]$Mutex)
    if ($null -eq $Mutex) {
        return
    }
    try {
        $Mutex.ReleaseMutex()
    } finally {
        $Mutex.Dispose()
    }
}

function Set-TmsLocalPrivateDirectory {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    New-Item -ItemType Directory -Path $Path -Force | Out-Null
    $directory = [IO.DirectoryInfo]::new($Path)
    $acl = [Security.AccessControl.DirectorySecurity]::new()
    $acl.SetAccessRuleProtection($true, $false)
    $inheritance = [Security.AccessControl.InheritanceFlags]'ContainerInherit, ObjectInherit'
    $propagation = [Security.AccessControl.PropagationFlags]::None
    $allow = [Security.AccessControl.AccessControlType]::Allow
    $identities = @(
        [Security.Principal.WindowsIdentity]::GetCurrent().User,
        (New-Object Security.Principal.SecurityIdentifier([Security.Principal.WellKnownSidType]::LocalSystemSid, $null)),
        (New-Object Security.Principal.SecurityIdentifier([Security.Principal.WellKnownSidType]::BuiltinAdministratorsSid, $null))
    )
    foreach ($identity in $identities) {
        $rule = New-Object Security.AccessControl.FileSystemAccessRule(
            $identity,
            [Security.AccessControl.FileSystemRights]::FullControl,
            $inheritance,
            $propagation,
            $allow
        )
        [void]$acl.AddAccessRule($rule)
    }
    [IO.FileSystemAclExtensions]::SetAccessControl($directory, $acl)
}

function Write-TmsLocalState {
    param(
        [Parameter(Mandatory = $true)]
        [string]$StatePath,
        [Parameter(Mandatory = $true)]
        [PSCustomObject]$State
    )

    $stateDirectory = Split-Path -Parent $StatePath
    $temporaryPath = Join-Path $stateDirectory "processes.$PID.$([Guid]::NewGuid().ToString('N')).tmp"
    try {
        $State | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $temporaryPath -Encoding UTF8
        Move-Item -LiteralPath $temporaryPath -Destination $StatePath -Force
    } finally {
        if (Test-Path -LiteralPath $temporaryPath -PathType Leaf) {
            Remove-Item -LiteralPath $temporaryPath -Force
        }
    }
}

function Set-TmsStateProcessRecord {
    param(
        [Parameter(Mandatory = $true)]
        [PSCustomObject]$State,
        [Parameter(Mandatory = $true)]
        [PSCustomObject]$Record
    )
    $State.processes = @(
        @($State.processes | Where-Object { $_.role -ne $Record.role }) + @($Record)
    )
}

function ConvertFrom-TmsUtcText {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Value
    )

    return [DateTimeOffset]::Parse(
        $Value,
        [Globalization.CultureInfo]::InvariantCulture,
        [Globalization.DateTimeStyles]::RoundtripKind
    ).UtcDateTime
}

function Assert-TmsLocalDatabaseGuard {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Python,
        [Parameter(Mandatory = $true)]
        [string]$ExpectedDatabase
    )

    if ([string]::IsNullOrWhiteSpace($env:TMS_DATABASE_URL)) {
        throw 'TMS_DATABASE_URL is required for the local SQL test environment.'
    }
    $databaseOutput = @(
        & $Python -c "import os; from sqlalchemy.engine import make_url; print(make_url(os.environ['TMS_DATABASE_URL']).database or '')" 2>$null
    )
    if ($LASTEXITCODE -ne 0 -or $databaseOutput.Count -ne 1) {
        throw 'Unable to validate the configured TMS database target.'
    }
    $databaseName = ([string]$databaseOutput[0]).Trim()
    if (-not [string]::Equals($databaseName, $ExpectedDatabase, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Local test database guard rejected configured database '$databaseName'. Expected '$ExpectedDatabase'."
    }
    return $databaseName
}

function Get-TmsLocalRoleContract {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet('api', 'worker', 'frontend')]
        [string]$Role,
        [Parameter(Mandatory = $true)]
        [string]$Python,
        [Parameter(Mandatory = $true)]
        [string]$Node
    )

    switch ($Role) {
        'api' { return [PSCustomObject]@{ Role = $Role; Executable = $Python; Marker = 'uvicorn app.main:app' } }
        'worker' { return [PSCustomObject]@{ Role = $Role; Executable = $Python; Marker = 'run_route_a_worker.py' } }
        'frontend' { return [PSCustomObject]@{ Role = $Role; Executable = $Node; Marker = 'vite\bin\vite.js' } }
    }
}

function Find-TmsLocalRoleProcessId {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet('api', 'worker', 'frontend')]
        [string]$Role,
        [Parameter(Mandatory = $true)]
        [string]$Workspace,
        [Parameter(Mandatory = $true)]
        [string]$Python,
        [Parameter(Mandatory = $true)]
        [string]$Node
    )

    $contract = Get-TmsLocalRoleContract -Role $Role -Python $Python -Node $Node
    $matches = @(
        Get-CimInstance Win32_Process -ErrorAction Stop |
            Where-Object {
                $commandLine = [string]$_.CommandLine
                -not [string]::IsNullOrWhiteSpace($commandLine) -and
                $commandLine.IndexOf($Workspace, [StringComparison]::OrdinalIgnoreCase) -ge 0 -and
                $commandLine.IndexOf($contract.Marker, [StringComparison]::OrdinalIgnoreCase) -ge 0 -and
                [string]::Equals(
                    [string]$_.ExecutablePath,
                    $contract.Executable,
                    [StringComparison]::OrdinalIgnoreCase
                )
            } |
            Select-Object -ExpandProperty ProcessId
    )
    if ($matches.Count -gt 1) {
        throw "More than one managed-process candidate exists for role '$Role'."
    }
    if ($matches.Count -eq 1) { return [int]$matches[0] }
    return $null
}

function Test-TmsLocalProcess {
    param(
        [Parameter(Mandatory = $true)]
        [PSCustomObject]$Record,
        [Parameter(Mandatory = $true)]
        [string]$Workspace,
        [Parameter(Mandatory = $true)]
        [string]$Python,
        [Parameter(Mandatory = $true)]
        [string]$Node
    )

    if ($Record.role -notin @('api', 'worker', 'frontend')) {
        throw "Unknown role in local state: $($Record.role)"
    }
    $contract = Get-TmsLocalRoleContract -Role $Record.role -Python $Python -Node $Node
    $processId = [int]$Record.process_id
    $processInfo = Get-CimInstance Win32_Process -Filter "ProcessId = $processId" -ErrorAction SilentlyContinue
    if ($null -eq $processInfo) {
        return $false
    }
    $commandLine = [string]$processInfo.CommandLine
    if (
        [string]::IsNullOrWhiteSpace($commandLine) -or
        $commandLine.IndexOf($Workspace, [StringComparison]::OrdinalIgnoreCase) -lt 0 -or
        $commandLine.IndexOf($contract.Marker, [StringComparison]::OrdinalIgnoreCase) -lt 0 -or
        -not [string]::Equals([string]$processInfo.ExecutablePath, $contract.Executable, [StringComparison]::OrdinalIgnoreCase)
    ) {
        return $false
    }
    $process = Get-Process -Id $processId -ErrorAction SilentlyContinue
    if ($null -eq $process) {
        return $false
    }
    $recordedStart = ConvertFrom-TmsUtcText -Value ([string]$Record.started_at_utc)
    return [Math]::Abs(($process.StartTime.ToUniversalTime() - $recordedStart).TotalSeconds) -le 2
}

function New-TmsLocalProcessRecord {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet('api', 'worker', 'frontend')]
        [string]$Role,
        [Parameter(Mandatory = $true)]
        [int]$ProcessId,
        [bool]$Adopted = $false
    )

    $process = Get-Process -Id $ProcessId -ErrorAction Stop
    return [PSCustomObject]@{
        role = $Role
        process_id = $ProcessId
        started_at_utc = ([DateTimeOffset]$process.StartTime).ToUniversalTime().ToString('o')
        adopted = $Adopted
    }
}

function Invoke-WithoutTmsEnvironment {
    param(
        [Parameter(Mandatory = $true)]
        [ScriptBlock]$Action
    )

    $saved = @{}
    foreach ($item in @(Get-ChildItem Env: | Where-Object { $_.Name -like 'TMS_*' -or $_.Name -eq 'PYTHONPATH' })) {
        $saved[$item.Name] = $item.Value
        Remove-Item -LiteralPath "Env:$($item.Name)"
    }
    try {
        & $Action
    } finally {
        foreach ($name in $saved.Keys) {
            Set-Item -LiteralPath "Env:$name" -Value $saved[$name]
        }
    }
}
