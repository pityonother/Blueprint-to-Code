param(
    [string]$DevKitRoot,
    [string]$DllPath,
    [string]$PdbPath,
    [string]$WorkspaceRoot,
    [string]$ProjectName,
    [string]$GhidraPath,
    [string]$JavaHome,
    [string]$ToolsRoot,
    [string]$MaxMemory,
    [int]$AnalysisTimeoutSeconds,
    [switch]$AllowHashMismatch,
    [switch]$Reimport,
    [switch]$Reanalyze,
    [switch]$Help
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot "NativeAnalysis.Common.ps1")

if ($Help) {
    Write-Host "Usage: .\scripts\native_analysis\Import-ShooterGameNative.ps1"
    Write-Host "Imports or reprocesses ShooterGameEditor-ShooterGame.dll, applies its matching PDB, and exports targeted native evidence."
    Write-Host "Existing projects export only by default; add -Reanalyze to rerun analyzers."
    Write-Host "Use -AllowHashMismatch only after recording a new DevKit binary/PDB pair."
    exit 0
}

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$context = Resolve-NativeAnalysisContext -GhidraPath $GhidraPath -JavaHome $JavaHome -ToolsRoot $ToolsRoot
$config = $context.Config
Set-NativeAnalysisProcessEnvironment -Context $context -MaxMemory $MaxMemory

if (-not $AnalysisTimeoutSeconds) {
    $AnalysisTimeoutSeconds = [int]$config.workspace.analysisTimeoutSeconds
}
if (-not $ProjectName) {
    $ProjectName = [string]$config.workspace.projectName
}
if (-not $WorkspaceRoot) {
    $WorkspaceRoot = Join-Path $context.ToolsRoot $config.workspace.folderName
}

$resolvedDevKitRoot = Resolve-BlueprintToCodeDevKitRoot -DevKitRoot $DevKitRoot -Config $config -ProjectRoot $projectRoot
if (-not $DllPath) {
    $DllPath = Join-Path $resolvedDevKitRoot $config.shooterGame.binaryRelativePath
}
if (-not $PdbPath) {
    $PdbPath = Join-Path $resolvedDevKitRoot $config.shooterGame.pdbRelativePath
}

$DllPath = [System.IO.Path]::GetFullPath($DllPath)
$PdbPath = [System.IO.Path]::GetFullPath($PdbPath)
$WorkspaceRoot = [System.IO.Path]::GetFullPath($WorkspaceRoot)

$dllHash = Get-LowerSha256 -Path $DllPath
$pdbHash = Get-LowerSha256 -Path $PdbPath
$expectedDllHash = [string]$config.shooterGame.binarySha256
$expectedPdbHash = [string]$config.shooterGame.pdbSha256
if (-not $AllowHashMismatch) {
    if ($dllHash -ne $expectedDllHash) {
        throw "ShooterGame DLL hash changed. Expected $expectedDllHash, got $dllHash. Record the new DevKit version before using -AllowHashMismatch."
    }
    if ($pdbHash -ne $expectedPdbHash) {
        throw "ShooterGame PDB hash changed. Expected $expectedPdbHash, got $pdbHash. Record the new DevKit version before using -AllowHashMismatch."
    }
}

$logDir = Join-Path $projectRoot "logs\native_analysis"
$evidenceDir = Join-Path $projectRoot "native_evidence"
New-Item -ItemType Directory -Force -Path $WorkspaceRoot, $logDir, $evidenceDir | Out-Null

$projectFile = Join-Path $WorkspaceRoot ($ProjectName + ".gpr")
$programName = [System.IO.Path]::GetFileName($DllPath)
$evidencePath = Join-Path $evidenceDir ("shooter-game-native-targets-" + $dllHash.Substring(0, 12) + ".json")
$scriptPath = Join-Path $PSScriptRoot "ghidra"
$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$logPath = Join-Path $logDir "ghidra-$timestamp.log"
$scriptLogPath = Join-Path $logDir "ghidra-script-$timestamp.log"

$projectExists = Test-Path -LiteralPath $projectFile -PathType Leaf
$arguments = @($WorkspaceRoot, $ProjectName)
if ($Reimport) {
    $arguments += @("-import", $DllPath, "-overwrite")
}
elseif ($projectExists) {
    $arguments += @("-process", $programName)
}
else {
    $arguments += @("-import", $DllPath)
}

$targetPatterns = @(
    "GenerateCrateItems",
    "GenerateCustomCrateItems",
    "ClampItemRating",
    "GetItemQualityIndex",
    "OverrideItemRating"
)

$arguments += @(
    "-preScript", "ConfigurePdbAnalyzer.java", ([System.IO.Path]::GetDirectoryName($PdbPath))
)
if ($projectExists -and -not $Reimport -and -not $Reanalyze) {
    $arguments += "-noanalysis"
}
else {
    $arguments += @("-analysisTimeoutPerFile", [string]$AnalysisTimeoutSeconds)
}
$arguments += @("-postScript", "ExportNativeTargets.java", $evidencePath)
$arguments += $targetPatterns
$arguments += @(
    "-scriptPath", $scriptPath,
    "-log", $logPath,
    "-scriptlog", $scriptLogPath
)

Write-Host "DLL:       $DllPath"
Write-Host "DLL SHA:   $dllHash"
Write-Host "PDB:       $PdbPath"
Write-Host "PDB SHA:   $pdbHash"
Write-Host "Project:   $projectFile"
Write-Host "Evidence:  $evidencePath"
Write-Host "Mode:      $(if ($Reimport) { 'reimport' } elseif ($projectExists -and $Reanalyze) { 'process + reanalyze' } elseif ($projectExists) { 'process + export only' } else { 'import' })"

& $context.AnalyzeHeadless @arguments
if ($LASTEXITCODE -ne 0) {
    throw "Ghidra headless analysis failed (exit code $LASTEXITCODE). See $logPath"
}
$scriptFailure = Select-String -LiteralPath $logPath -Pattern "REPORT SCRIPT ERROR|SCRIPT ERROR:" -ErrorAction SilentlyContinue
if ($scriptFailure) {
    throw "A Ghidra script failed even though the headless process returned success. See $logPath"
}
if (-not (Test-Path -LiteralPath $evidencePath -PathType Leaf)) {
    throw "Ghidra completed without creating the expected evidence file: $evidencePath"
}

$evidence = Get-Content -LiteralPath $evidencePath -Raw -Encoding UTF8 | ConvertFrom-Json
Write-Host "Matched functions: $($evidence.matchCount)"
Write-Host "PDB loaded:        $($evidence.pdbLoaded)"
Write-Host "Native evidence:   $evidencePath"
