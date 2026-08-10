param(
    [string]$CaptureRoot = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$EnvironmentPython = Join-Path $ProjectRoot ".runtime\arkdev-mcp\Scripts\python.exe"
$EntryPoint = Join-Path $ProjectRoot "scripts\run_arkdev_mcp.py"

if (-not (Test-Path -LiteralPath $EnvironmentPython -PathType Leaf)) {
    [Console]::Error.WriteLine(
        "ARK Dev MCP environment is missing. Run .\scripts\install_arkdev_mcp.ps1."
    )
    exit 2
}

$env:PYTHONUTF8 = "1"
$env:PYTHONDONTWRITEBYTECODE = "1"
$ServerArguments = @($EntryPoint)
if ($CaptureRoot) {
    $ServerArguments += @("--capture-root", $CaptureRoot)
}

$ServerExitCode = 1
Push-Location $ProjectRoot
try {
    & $EnvironmentPython @ServerArguments
    $ServerExitCode = $LASTEXITCODE
}
catch {
    [Console]::Error.WriteLine("ARK Dev MCP failed to start.")
    $ServerExitCode = 1
}
finally {
    Pop-Location
}
exit $ServerExitCode
