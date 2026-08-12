[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$DevKitRoot
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$ProjectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$ProbeScript = Join-Path $ProjectRoot "scripts\arkdev_scripting_probe\installation_probe.py"
$OutputPath = Join-Path $ProjectRoot ".arkdev-probe\install-capabilities.json"

$PythonCommand = Get-Command py -ErrorAction SilentlyContinue
if ($PythonCommand) {
    $PythonExe = $PythonCommand.Source
    $PythonArguments = @("-3", $ProbeScript)
}
else {
    $PythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if (-not $PythonCommand) {
        [Console]::Error.WriteLine("ARKDEV_SCRIPTING_INSTALL_PROBE=PYTHON_NOT_FOUND")
        exit 2
    }
    $PythonExe = $PythonCommand.Source
    $PythonArguments = @($ProbeScript)
}

$PythonArguments += @(
    "--devkit-root", $DevKitRoot,
    "--output", $OutputPath
)
$env:PYTHONUTF8 = "1"
$env:PYTHONDONTWRITEBYTECODE = "1"

try {
    $ProbeOutput = & $PythonExe @PythonArguments 2>$null
    $ProbeExitCode = $LASTEXITCODE
}
catch {
    [Console]::Error.WriteLine("ARKDEV_SCRIPTING_INSTALL_PROBE=ERROR")
    exit 1
}
if ($ProbeExitCode -ne 0) {
    [Console]::Error.WriteLine("ARKDEV_SCRIPTING_INSTALL_PROBE=ERROR")
    exit $ProbeExitCode
}

$ExpectedLabels = @(
    "DEVKIT_FOUND",
    "DEVKIT_BUILD",
    "PYTHON_SCRIPT_PLUGIN_PRESENT",
    "EDITOR_SCRIPTING_UTILITIES_PRESENT",
    "EDITOR_UTILITY_PRESENT",
    "PYTHON_EMBEDDED_RUNTIME_PRESENT",
    "PROJECT_DESCRIPTOR_FOUND",
    "PYTHON_PLUGIN_ENABLED",
    "NEXT_MANUAL_ACTION"
)
$ObservedLabels = @()
foreach ($Line in @($ProbeOutput)) {
    $Text = [string]$Line
    if ($Text -notmatch "^([A-Z_]+)=[A-Za-z0-9_.+|-]+$") {
        [Console]::Error.WriteLine("ARKDEV_SCRIPTING_INSTALL_PROBE=UNSAFE_OUTPUT")
        exit 3
    }
    $ObservedLabels += $Matches[1]
    Write-Output $Text
}

if ($ObservedLabels.Count -ne $ExpectedLabels.Count) {
    [Console]::Error.WriteLine("ARKDEV_SCRIPTING_INSTALL_PROBE=INCOMPLETE_OUTPUT")
    exit 3
}
for ($Index = 0; $Index -lt $ExpectedLabels.Count; $Index++) {
    if ($ObservedLabels[$Index] -ne $ExpectedLabels[$Index]) {
        [Console]::Error.WriteLine("ARKDEV_SCRIPTING_INSTALL_PROBE=INCOMPLETE_OUTPUT")
        exit 3
    }
}
