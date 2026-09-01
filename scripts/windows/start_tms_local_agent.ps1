[CmdletBinding()]
param(
    [string]$ConfigPath = "$env:LOCALAPPDATA\NCE\TMSLocalAgent\config.json",
    [switch]$ValidateOnly
)

$ErrorActionPreference = "Stop"
$workspaceRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$pythonRuntime = if ($env:TMS_LOCAL_AGENT_PYTHON) {
    $env:TMS_LOCAL_AGENT_PYTHON
} else {
    "D:\ProgramData\anaconda3\python.exe"
}

if (-not (Test-Path -LiteralPath $pythonRuntime -PathType Leaf)) {
    throw "Local Agent Python runtime not found. Set TMS_LOCAL_AGENT_PYTHON."
}
if (-not (Test-Path -LiteralPath $ConfigPath -PathType Leaf)) {
    throw "Local Agent config not found. Copy local_agent\config.example.json to $ConfigPath and verify it."
}

$existingPythonPath = $env:PYTHONPATH
$env:PYTHONPATH = if ($existingPythonPath) {
    "$workspaceRoot;$existingPythonPath"
} else {
    $workspaceRoot
}

$agentArguments = @("-m", "local_agent", "--config", $ConfigPath)
if ($ValidateOnly) {
    $agentArguments += "--validate-only"
}

& $pythonRuntime @agentArguments
exit $LASTEXITCODE
