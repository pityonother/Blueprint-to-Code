param(
    [int]$Port = 8765,
    [switch]$NoBuild,
    [switch]$NoOpen,
    [switch]$Help
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$BundledPython = Join-Path $ProjectRoot "runtime\python\python.exe"
$PythonExe = if (Test-Path -LiteralPath $BundledPython) { $BundledPython } else { "python" }

if ($Help) {
    Write-Host "Usage: .\scripts\launch_blueprint_tool.ps1 [-Port 8765] [-NoBuild] [-NoOpen]"
    Write-Host "Builds the Vite UI when requested, starts the local Python control-center server, and opens the browser."
    exit 0
}

Push-Location $ProjectRoot
try {
    if (-not $NoBuild) {
        $npm = Get-Command npm -ErrorAction SilentlyContinue
        if ($npm) {
            Write-Host "Building Blueprint Tool Control Center..."
            npm run build
        }
        elseif (Test-Path -LiteralPath (Join-Path $ProjectRoot "dist\index.html")) {
            Write-Host "npm was not found; using bundled prebuilt dist/ UI."
        }
        else {
            throw "npm was not found and dist/index.html is missing. Use the full packaged build or install Node.js."
        }
    }

    $openArgs = @()
    if (-not $NoOpen) {
        $openArgs += "--open"
    }

    Write-Host "Starting Blueprint Tool Control Center on http://127.0.0.1:$Port/"
    & $PythonExe scripts\blueprint_tool_server.py --port $Port @openArgs
}
finally {
    Pop-Location
}
