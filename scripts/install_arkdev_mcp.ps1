param()

$ErrorActionPreference = "Stop"
$ProjectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$EnvironmentRoot = Join-Path $ProjectRoot ".runtime\arkdev-mcp"
$EnvironmentPython = Join-Path $EnvironmentRoot "Scripts\python.exe"
$RequirementsFile = Join-Path $ProjectRoot "requirements-mcp.txt"
$BundledPython = Join-Path $ProjectRoot "runtime\python\python.exe"
$EntryPoint = Join-Path $ProjectRoot "scripts\run_arkdev_mcp.py"

function Test-Python311WithVenv {
    param([Parameter(Mandatory = $true)][string]$Executable)
    & $Executable -c "import importlib.util, sys; raise SystemExit(0 if sys.version_info >= (3, 11) and importlib.util.find_spec('venv') else 1)"
    return ($LASTEXITCODE -eq 0)
}

if (-not (Test-Path -LiteralPath $EnvironmentPython -PathType Leaf)) {
    $Candidates = @()
    if (Test-Path -LiteralPath $BundledPython -PathType Leaf) {
        $Candidates += $BundledPython
    }
    $SystemPython = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($SystemPython) {
        $Candidates += $SystemPython.Source
    }

    $Created = $false
    foreach ($Candidate in ($Candidates | Select-Object -Unique)) {
        if (-not (Test-Python311WithVenv -Executable $Candidate)) {
            continue
        }
        & $Candidate -m venv --clear $EnvironmentRoot
        if ($LASTEXITCODE -eq 0 -and (Test-Path -LiteralPath $EnvironmentPython -PathType Leaf)) {
            $Created = $true
            break
        }
    }
    if (-not $Created) {
        throw "Python 3.11+ with venv support was not found."
    }
}

& $EnvironmentPython -m pip install --quiet --disable-pip-version-check -r $RequirementsFile
if ($LASTEXITCODE -ne 0) {
    throw "ARK Dev MCP dependency installation failed."
}

Push-Location $ProjectRoot
try {
    & $EnvironmentPython $EntryPoint --self-test
    if ($LASTEXITCODE -ne 0) {
        throw "ARK Dev MCP self-test failed."
    }
}
finally {
    Pop-Location
}

Write-Host "ARK Dev MCP read-only environment is installed and self-tested."
