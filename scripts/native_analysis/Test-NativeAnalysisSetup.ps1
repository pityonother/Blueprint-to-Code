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

$ghidraVersion = Get-NativeGhidraVersion -ApplicationPropertiesPath $context.GhidraApplicationProperties
if ($ghidraVersion -ne [string]$config.ghidra.version) {
    throw "NATIVE_TOOL_MISSING: Expected Ghidra $($config.ghidra.version), detected $ghidraVersion."
}
Write-Host "[PASS] Ghidra version: $ghidraVersion"

$javaRuntime = Get-NativeJavaRuntimeInfo -JavaExe $context.JavaExe
if ($javaRuntime.Version -ne [string]$config.java.runtimeVersion) {
    throw "NATIVE_JAVA_VERSION_MISMATCH: Expected Java runtime $($config.java.runtimeVersion), detected $($javaRuntime.Version)."
}
if ($javaRuntime.Vendor -ne [string]$config.java.vendor) {
    throw "NATIVE_JAVA_VERSION_MISMATCH: Expected Java vendor $($config.java.vendor), detected $($javaRuntime.Vendor)."
}
Write-Host "[PASS] Java runtime: $($javaRuntime.Vendor) $($javaRuntime.Version)"

foreach ($devKitFile in @($dllPath, $pdbPath)) {
    if (-not (Test-Path -LiteralPath $devKitFile -PathType Leaf)) {
        throw "DevKit native file is missing: $devKitFile"
    }
    Write-Host "[PASS] DevKit file: $devKitFile"
}

$identity = Get-NativeBuildIdentityObject -DllPath $dllPath -PdbPath $pdbPath -Config $config -ProjectRoot $projectRoot
Write-Host "[PASS] PE CodeView: $($identity.binary.codeView.guid) age $($identity.binary.codeView.age)"
Write-Host "[PASS] PDB identity matched: $($identity.pdb.guid) age $($identity.pdb.age)"
Write-Host "[PASS] Dynamic project: $($identity.project.name)"

if (-not $SkipDevKitHash) {
    if ($identity.binary.sha256 -ne [string]$config.shooterGame.binarySha256) {
        throw "NATIVE_BINARY_HASH_UNREGISTERED: DLL SHA-256 mismatch: $($identity.binary.sha256)"
    }
    if ($identity.pdb.sha256 -ne [string]$config.shooterGame.pdbSha256) {
        throw "NATIVE_PDB_HASH_MISMATCH: PDB SHA-256 mismatch: $($identity.pdb.sha256)"
    }
    Write-Host "[PASS] DLL SHA-256: $($identity.binary.sha256)"
    Write-Host "[PASS] PDB SHA-256: $($identity.pdb.sha256)"
}

Write-Host "Native analysis setup is ready."
