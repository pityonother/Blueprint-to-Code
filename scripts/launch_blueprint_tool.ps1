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
$DevkitContentRootFile = Join-Path $ProjectRoot "devkit_content_root.txt"
if (Test-Path -LiteralPath $DevkitContentRootFile) {
    $DevkitContentRoot = (Get-Content -LiteralPath $DevkitContentRootFile -TotalCount 1).Trim().Trim('"', "'")
    if ($DevkitContentRoot) {
        $env:BLUEPRINT_TO_CODE_DEVKIT_CONTENT_ROOT = $DevkitContentRoot
    }
}

if ($Help) {
    Write-Host "Usage: .\scripts\launch_blueprint_tool.ps1 [-Port 8765] [-NoBuild] [-NoOpen]"
    Write-Host "Builds the Vite UI when requested, starts the local Python control-center server, and opens the browser."
    exit 0
}

Push-Location $ProjectRoot
try {
    if (-not $NoBuild) {
        $npm = Get-Command npm -ErrorAction SilentlyContinue
        $distIndex = Join-Path $ProjectRoot "dist\index.html"
        $nodeModules = Join-Path $ProjectRoot "node_modules"
        if ($npm -and (Test-Path -LiteralPath $nodeModules)) {
            Write-Host "Building Blueprint Tool Control Center..."
            npm run build
        }
        elseif (Test-Path -LiteralPath $distIndex) {
            Write-Host "Using bundled prebuilt dist/ UI."
        }
        else {
            throw "dist/index.html is missing. Use the full packaged build, or run npm install and npm run build."
        }
    }

    $openArgs = @()
    if (-not $NoOpen) {
        $openArgs += "--open"
    }

    Write-Host "Starting Blueprint Tool Control Center on http://127.0.0.1:$Port/"
    if ($env:BLUEPRINT_TO_CODE_DEVKIT_CONTENT_ROOT) {
        Write-Host "DevKit Content root: $env:BLUEPRINT_TO_CODE_DEVKIT_CONTENT_ROOT"
    }
    & $PythonExe scripts\blueprint_tool_server.py --port $Port @openArgs
}
finally {
    Pop-Location
}
