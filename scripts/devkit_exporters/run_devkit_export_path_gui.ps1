param(
    [switch]$NoWait,
    [switch]$PrintCommand
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Resolve-Path (Join-Path $ScriptDir "..\..")
$GuiScript = Join-Path $ScriptDir "devkit_export_path_gui.py"

if (-not (Test-Path -LiteralPath $GuiScript)) {
    throw "GUI script not found: $GuiScript"
}

$Python = Get-Command py -ErrorAction SilentlyContinue
if ($Python) {
    $PythonExe = $Python.Source
    $PythonArgs = @("-3", $GuiScript)
} else {
    $Python = Get-Command python -ErrorAction SilentlyContinue
    if (-not $Python) {
        throw "Python was not found. Install Python or add python.exe to PATH."
    }
    $PythonExe = $Python.Source
    $PythonArgs = @($GuiScript)
}

if ($PrintCommand) {
    Write-Host "Project root: $ProjectRoot"
    Write-Host "GUI script: $GuiScript"
    Write-Host "Command: `"$PythonExe`" $($PythonArgs -join ' ')"
    exit 0
}

Set-Location -LiteralPath $ProjectRoot

if ($NoWait) {
    Start-Process -FilePath $PythonExe -ArgumentList $PythonArgs -WorkingDirectory $ProjectRoot -WindowStyle Hidden
} else {
    & $PythonExe @PythonArgs
}
