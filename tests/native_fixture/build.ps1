[CmdletBinding()]
param(
    [string]$OutputDir = (Join-Path $PSScriptRoot "build")
)

$ErrorActionPreference = "Stop"

$fixtureRoot = [System.IO.Path]::GetFullPath($PSScriptRoot)
$resolvedOutput = [System.IO.Path]::GetFullPath($OutputDir)
if (-not $resolvedOutput.StartsWith($fixtureRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "OutputDir must stay inside tests/native_fixture."
}

$vswhere = Join-Path ${env:ProgramFiles(x86)} "Microsoft Visual Studio\Installer\vswhere.exe"
$vcvars = $null
if (Test-Path -LiteralPath $vswhere) {
    $installation = & $vswhere -latest -products * `
        -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 `
        -property installationPath
    if ($LASTEXITCODE -eq 0 -and $installation) {
        $candidate = Join-Path ($installation | Select-Object -First 1) `
            "VC\Auxiliary\Build\vcvars64.bat"
        if (Test-Path -LiteralPath $candidate) {
            $vcvars = $candidate
        }
    }
}

if (-not $vcvars) {
    $candidateRoots = @(
        (Join-Path $env:ProgramFiles "Microsoft Visual Studio\2022\Community"),
        (Join-Path $env:ProgramFiles "Microsoft Visual Studio\2022\Professional"),
        (Join-Path $env:ProgramFiles "Microsoft Visual Studio\2022\Enterprise"),
        (Join-Path $env:ProgramFiles "Microsoft Visual Studio\2022\BuildTools")
    )
    foreach ($root in $candidateRoots) {
        $candidate = Join-Path $root "VC\Auxiliary\Build\vcvars64.bat"
        if (Test-Path -LiteralPath $candidate) {
            $vcvars = $candidate
            break
        }
    }
}

if (-not $vcvars) {
    throw "Visual Studio C++ x64 build tools were not found."
}

New-Item -ItemType Directory -Path $resolvedOutput -Force | Out-Null

$dllPath = Join-Path $resolvedOutput "blueprint_native_fixture.dll"
$pdbPath = Join-Path $resolvedOutput "blueprint_native_fixture.pdb"
$objectPath = Join-Path $resolvedOutput "fixture.obj"
$compilerPdbPath = Join-Path $resolvedOutput "fixture-compiler.pdb"
$importLibraryPath = Join-Path $resolvedOutput "blueprint_native_fixture.lib"
$sourcePath = Join-Path $fixtureRoot "fixture.cpp"

$arguments = @(
    "/nologo",
    "/std:c++20",
    "/EHsc",
    "/Od",
    "/Zi",
    "/LD",
    "/Fd`"$compilerPdbPath`"",
    "/Fo`"$objectPath`"",
    "`"$sourcePath`"",
    "/link",
    "/DEBUG:FULL",
    "/INCREMENTAL:NO",
    "/OPT:NOREF",
    "/OPT:NOICF",
    "/IMPLIB:`"$importLibraryPath`"",
    "/PDB:`"$pdbPath`"",
    "/OUT:`"$dllPath`""
)
$compilerCommand = "cl.exe " + ($arguments -join " ")
$command = "call `"$vcvars`" >nul && $compilerCommand"

& cmd.exe /d /s /c $command
if ($LASTEXITCODE -ne 0) {
    throw "Native fixture compilation failed with exit code $LASTEXITCODE."
}

foreach ($required in @($dllPath, $pdbPath)) {
    if (-not (Test-Path -LiteralPath $required)) {
        throw "Native fixture build did not produce $required."
    }
}

$manifest = [ordered]@{
    schema = "blueprint-to-code-native-fixture-build/v1"
    binary = [ordered]@{
        fileName = [System.IO.Path]::GetFileName($dllPath)
        sha256 = (Get-FileHash -LiteralPath $dllPath -Algorithm SHA256).Hash.ToLowerInvariant()
    }
    pdb = [ordered]@{
        fileName = [System.IO.Path]::GetFileName($pdbPath)
        sha256 = (Get-FileHash -LiteralPath $pdbPath -Algorithm SHA256).Hash.ToLowerInvariant()
    }
}
$manifestPath = Join-Path $resolvedOutput "build_manifest.json"
$manifest | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $manifestPath `
    -Encoding UTF8

Write-Host "Native fixture DLL: $dllPath"
Write-Host "Native fixture PDB: $pdbPath"
Write-Host "Build manifest: $manifestPath"
