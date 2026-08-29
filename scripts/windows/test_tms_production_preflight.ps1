[CmdletBinding()]
param(
    [string]$RuntimeConfig,
    [string]$ExpectedServiceAccount,
    [switch]$SkipAclCheck
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'TmsRuntime.Common.ps1')

function Get-TmsEffectiveAccess {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $identitySids = New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)
    [void]$identitySids.Add($identity.User.Value)
    foreach ($group in $identity.Groups) {
        [void]$identitySids.Add($group.Value)
    }

    $allow = [Security.AccessControl.FileSystemRights]0
    $deny = [Security.AccessControl.FileSystemRights]0
    $acl = Get-Acl -LiteralPath $Path
    foreach ($rule in $acl.Access) {
        try {
            $sid = $rule.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value
        } catch {
            continue
        }
        if (-not $identitySids.Contains($sid)) {
            continue
        }
        if ($rule.AccessControlType -eq [Security.AccessControl.AccessControlType]::Deny) {
            $deny = $deny -bor $rule.FileSystemRights
        } else {
            $allow = $allow -bor $rule.FileSystemRights
        }
    }
    return ($allow -band (-bnot $deny))
}

function Test-TmsRight {
    param(
        [Security.AccessControl.FileSystemRights]$Effective,
        [Security.AccessControl.FileSystemRights]$Required
    )
    return (($Effective -band $Required) -eq $Required)
}

$workspace = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
if ([string]::IsNullOrWhiteSpace($RuntimeConfig)) {
    $RuntimeConfig = Join-Path $workspace '.env.runtime.ps1'
}
if (-not (Test-Path -LiteralPath $RuntimeConfig -PathType Leaf)) {
    throw "Runtime configuration does not exist: $RuntimeConfig"
}
$RuntimeConfig = (Resolve-Path -LiteralPath $RuntimeConfig).Path
Import-TmsRuntimeConfig -Path $RuntimeConfig
Assert-TmsRuntimeConfigContainsNoSecretLiterals -Path $RuntimeConfig
$roots = @(Assert-TmsProductionRuntime -Workspace $workspace)

$currentIdentity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
if (
    -not [string]::IsNullOrWhiteSpace($ExpectedServiceAccount) -and
    -not $currentIdentity.Equals($ExpectedServiceAccount, [StringComparison]::OrdinalIgnoreCase)
) {
    throw "ACL preflight must run as the expected service account: $ExpectedServiceAccount"
}
if (-not $SkipAclCheck) {
    $windowsIdentity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $windowsPrincipal = New-Object Security.Principal.WindowsPrincipal($windowsIdentity)
    if ($windowsPrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'The production service account must not be a local Administrator.'
    }
}

$results = foreach ($root in $roots) {
    if ($SkipAclCheck) {
        [PSCustomObject]@{
            Name = $root.Name
            Path = $root.Path
            Identity = $currentIdentity
            AclStatus = 'SKIPPED'
            Status = 'VALID'
        }
        continue
    }
    $effective = Get-TmsEffectiveAccess -Path $root.Path
    $canRead = Test-TmsRight -Effective $effective -Required ([Security.AccessControl.FileSystemRights]::ReadAndExecute)
    $writeMask = (
        [Security.AccessControl.FileSystemRights]::WriteData -bor
        [Security.AccessControl.FileSystemRights]::AppendData -bor
        [Security.AccessControl.FileSystemRights]::WriteExtendedAttributes -bor
        [Security.AccessControl.FileSystemRights]::WriteAttributes -bor
        [Security.AccessControl.FileSystemRights]::Delete -bor
        [Security.AccessControl.FileSystemRights]::DeleteSubdirectoriesAndFiles -bor
        [Security.AccessControl.FileSystemRights]::ChangePermissions -bor
        [Security.AccessControl.FileSystemRights]::TakeOwnership
    )
    $canWrite = (($effective -band $writeMask) -ne 0)
    if ($root.Kind -eq 'Source') {
        if (-not $canRead -or $canWrite) {
            throw "Source root must be readable but not writable by $currentIdentity`: $($root.Path)"
        }
        $aclStatus = 'READ_ONLY'
    } else {
        $canModify = Test-TmsRight -Effective $effective -Required ([Security.AccessControl.FileSystemRights]::Modify)
        if (-not $canRead -or -not $canModify) {
            throw "$($root.Kind) root must grant Modify access to $currentIdentity`: $($root.Path)"
        }
        $aclStatus = 'READ_WRITE'
    }
    [PSCustomObject]@{
        Name = $root.Name
        Path = $root.Path
        Identity = $currentIdentity
        AclStatus = $aclStatus
        Status = 'VALID'
    }
}

$results
[PSCustomObject]@{
    RuntimeConfig = $RuntimeConfig
    Environment = $env:TMS_ENV
    SchemaRevision = $env:TMS_EXPECTED_SCHEMA_REVISION
    ManagedRootCount = $roots.Count
    Identity = $currentIdentity
    AclChecked = -not $SkipAclCheck
    Status = 'VALID'
}
