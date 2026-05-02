param(
    [int]$Port = 8765,
    [switch]$NoBuild,
    [switch]$NoOpen,
    [switch]$Help
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")

if ($Help) {
    Write-Host "Usage: .\scripts\launch_blueprint_tool.ps1 [-Port 8765] [-NoBuild] [-NoOpen]"
    Write-Host "Builds the Vite UI, starts the local Python control-center server, and opens the browser."
    exit 0
}

Push-Location $ProjectRoot
try {
    if (-not $NoBuild) {
        Write-Host "Building Blueprint Tool Control Center..."
        npm run build
    }

    $openArgs = @()
    if (-not $NoOpen) {
        $openArgs += "--open"
    }

    Write-Host "Starting Blueprint Tool Control Center on http://127.0.0.1:$Port/"
    python scripts\blueprint_tool_server.py --port $Port @openArgs
}
finally {
    Pop-Location
}
