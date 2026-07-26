param(
    [string]$DevKitRoot,
    [string]$GhidraPath,
    [string]$JavaHome,
    [string]$ToolsRoot,
    [switch]$SkipDevKitHash
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot "NativeAnalysis.Common.ps1")

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$context = Resolve-NativeAnalysisContext -GhidraPath $GhidraPath -JavaHome $JavaHome -ToolsRoot $ToolsRoot
$config = $context.Config
$resolvedDevKitRoot = Resolve-BlueprintToCodeDevKitRoot -DevKitRoot $DevKitRoot -Config $config -ProjectRoot $projectRoot
$dllPath = Join-Path $resolvedDevKitRoot $config.shooterGame.binaryRelativePath
$pdbPath = Join-Path $resolvedDevKitRoot $config.shooterGame.pdbRelativePath

Write-Host "[PASS] Ghidra launcher: $($context.GhidraRun)"
Write-Host "[PASS] Headless analyzer: $($context.AnalyzeHeadless)"
Write-Host "[PASS] Java executable: $($context.JavaExe)"

$savedErrorActionPreference = $ErrorActionPreference
$ErrorActionPreference = "Continue"
$javaOutput = & $context.JavaExe -version 2>&1
$javaExitCode = $LASTEXITCODE
$ErrorActionPreference = $savedErrorActionPreference
if ($javaExitCode -ne 0) {
    throw "Java version check failed with exit code $javaExitCode."
}
$javaVersion = ($javaOutput | Select-Object -First 1) -join ""
if ($javaVersion -notmatch 'version "21\.') {
    throw "Ghidra 12.1.2 requires JDK 21; detected: $javaVersion"
}
Write-Host "[PASS] Java version: $javaVersion"

foreach ($devKitFile in @($dllPath, $pdbPath)) {
    if (-not (Test-Path -LiteralPath $devKitFile -PathType Leaf)) {
        throw "DevKit native file is missing: $devKitFile"
    }
    Write-Host "[PASS] DevKit file: $devKitFile"
}

if (-not $SkipDevKitHash) {
    $dllHash = Get-LowerSha256 -Path $dllPath
    $pdbHash = Get-LowerSha256 -Path $pdbPath
    if ($dllHash -ne [string]$config.shooterGame.binarySha256) {
        throw "DLL SHA-256 mismatch: $dllHash"
    }
    if ($pdbHash -ne [string]$config.shooterGame.pdbSha256) {
        throw "PDB SHA-256 mismatch: $pdbHash"
    }
    Write-Host "[PASS] DLL SHA-256: $dllHash"
    Write-Host "[PASS] PDB SHA-256: $pdbHash"
}

Write-Host "Native analysis setup is ready."
