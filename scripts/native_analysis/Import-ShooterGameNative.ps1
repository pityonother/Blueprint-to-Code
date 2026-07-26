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
    [switch]$Experimental,
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
    Write-Host "-AllowHashMismatch bypasses only toolchain pins; the actual DLL hash still selects a separate project."
    Write-Host "-Experimental permits dirty-generator evidence with non-formal trust status."
    exit 0
}

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$context = Resolve-NativeAnalysisContext -GhidraPath $GhidraPath -JavaHome $JavaHome -ToolsRoot $ToolsRoot
$config = $context.Config
Set-NativeAnalysisProcessEnvironment -Context $context -MaxMemory $MaxMemory

if (-not $AnalysisTimeoutSeconds) {
    $AnalysisTimeoutSeconds = [int]$config.workspace.analysisTimeoutSeconds
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
$identity = Get-NativeBuildIdentityObject -DllPath $DllPath -PdbPath $PdbPath -Config $config -ProjectRoot $projectRoot
$dllHash = [string]$identity.binary.sha256
$pdbHash = [string]$identity.pdb.sha256
$expectedDllHash = [string]$config.shooterGame.binarySha256
$expectedPdbHash = [string]$config.shooterGame.pdbSha256
if (-not $AllowHashMismatch) {
    if ($dllHash -ne $expectedDllHash) {
        throw "NATIVE_BINARY_HASH_UNREGISTERED: Expected $expectedDllHash, got $dllHash. Record the new DevKit version before using -AllowHashMismatch."
    }
    if ($pdbHash -ne $expectedPdbHash) {
        throw "NATIVE_PDB_HASH_MISMATCH: Expected $expectedPdbHash, got $pdbHash. Record the new DevKit version before using -AllowHashMismatch."
    }
}
elseif ($dllHash -ne $expectedDllHash -or $pdbHash -ne $expectedPdbHash) {
    Write-Warning "Toolchain hashes were bypassed, but project isolation still uses current binary hash $($identity.project.hashPrefix)."
}

$computedProjectName = [string]$identity.project.name
if ($ProjectName -and $ProjectName -ne $computedProjectName) {
    throw "NATIVE_PROJECT_PROGRAM_HASH_MISMATCH: -ProjectName must be $computedProjectName for the current DLL."
}
$ProjectName = $computedProjectName
$layout = Get-NativeProjectLayout -Identity $identity -Config $config -ToolsRoot $context.ToolsRoot -WorkspaceRoot $WorkspaceRoot
$WorkspaceRoot = $layout.WorkspaceRoot

$logDir = Join-Path $projectRoot "logs\native_analysis"
$evidenceDir = Join-Path $projectRoot "native_evidence"
New-Item -ItemType Directory -Force -Path $WorkspaceRoot, $logDir, $evidenceDir | Out-Null

$projectFile = $layout.ProjectFile
$projectManifest = $layout.ProjectManifest
$programName = [System.IO.Path]::GetFileName($DllPath)
$scriptPath = Join-Path $PSScriptRoot "ghidra"
$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$rawEvidencePath = Join-Path $evidenceDir ("shooter-game-native-targets-" + $dllHash.Substring(0, 12) + "-" + $timestamp + ".raw-v1.json")
$pendingEvidencePath = Join-Path $evidenceDir ("native-evidence-set-" + $dllHash.Substring(0, 12) + "-" + $timestamp + ".pending.json")
$logPath = Join-Path $logDir "ghidra-$timestamp.log"
$scriptLogPath = Join-Path $logDir "ghidra-script-$timestamp.log"

$projectExists = Test-Path -LiteralPath $projectFile -PathType Leaf
if ($projectExists) {
    Test-NativeProjectManifest -ManifestPath $projectManifest -DllPath $DllPath -PdbPath $PdbPath -Config $config -ProjectRoot $projectRoot
}
elseif (Test-Path -LiteralPath $projectManifest -PathType Leaf) {
    Test-NativeProjectManifest -ManifestPath $projectManifest -DllPath $DllPath -PdbPath $PdbPath -Config $config -ProjectRoot $projectRoot
}
else {
    Write-NativeProjectManifest -ManifestPath $projectManifest -DllPath $DllPath -PdbPath $PdbPath -Config $config -ProjectRoot $projectRoot
}

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
$arguments += @("-postScript", "ExportNativeTargets.java", $rawEvidencePath)
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
Write-Host "PDB ID:    $($identity.pdb.guid) age $($identity.pdb.age) (matched)"
Write-Host "Project:   $projectFile"
Write-Host "Raw export: $rawEvidencePath"
Write-Host "Mode:      $(if ($Reimport) { 'reimport' } elseif ($projectExists -and $Reanalyze) { 'process + reanalyze' } elseif ($projectExists) { 'process + export only' } else { 'import' })"

& $context.AnalyzeHeadless @arguments
if ($LASTEXITCODE -ne 0) {
    throw "Ghidra headless analysis failed (exit code $LASTEXITCODE). See $logPath"
}
$scriptFailure = Select-String -LiteralPath $logPath -Pattern "REPORT SCRIPT ERROR|SCRIPT ERROR:" -ErrorAction SilentlyContinue
if ($scriptFailure) {
    throw "A Ghidra script failed even though the headless process returned success. See $logPath"
}
if (-not (Test-Path -LiteralPath $rawEvidencePath -PathType Leaf)) {
    throw "NATIVE_EXPORT_SCHEMA_INVALID: Ghidra completed without creating the expected raw export: $rawEvidencePath"
}

$rawEvidence = Get-Content -LiteralPath $rawEvidencePath -Raw -Encoding UTF8 | ConvertFrom-Json
if ([string]$rawEvidence.binarySha256 -ne $dllHash) {
    throw "NATIVE_PROJECT_PROGRAM_HASH_MISMATCH: Ghidra currentProgram hash $($rawEvidence.binarySha256) does not match input DLL $dllHash."
}
if (-not [bool]$rawEvidence.pdbLoaded) {
    throw "NATIVE_PDB_NOT_LOADED: Ghidra did not report a loaded PDB."
}

$ghidraVersion = Get-NativeGhidraVersion -ApplicationPropertiesPath $context.GhidraApplicationProperties
$javaRuntime = Get-NativeJavaRuntimeInfo -JavaExe $context.JavaExe
$wrapArguments = @(
    "--pretty",
    "--output", $pendingEvidencePath,
    "wrap-legacy",
    "--dll", $DllPath,
    "--pdb", $PdbPath,
    "--project-prefix", [string]$config.workspace.projectNamePrefix,
    "--project-hash-length", [string]$config.workspace.projectHashLength,
    "--raw-export", $rawEvidencePath,
    "--toolchain", (Join-Path $PSScriptRoot "toolchain.json"),
    "--repository-root", $projectRoot,
    "--runner", $PSCommandPath,
    "--exporter", (Join-Path $scriptPath "ExportNativeTargets.java"),
    "--pdb-configurator", (Join-Path $scriptPath "ConfigurePdbAnalyzer.java"),
    "--ghidra-version", $ghidraVersion,
    "--java-vendor", $javaRuntime.Vendor,
    "--java-version", $javaRuntime.Version
)
if ($Experimental) {
    $wrapArguments += "--experimental"
}
Invoke-NativeIdentityTool -ProjectRoot $projectRoot -Arguments $wrapArguments | Out-Null
if (-not (Test-Path -LiteralPath $pendingEvidencePath -PathType Leaf)) {
    throw "NATIVE_EXPORT_SCHEMA_INVALID: Provenance wrapper did not create a v2 evidence manifest."
}

$evidence = Get-Content -LiteralPath $pendingEvidencePath -Raw -Encoding UTF8 | ConvertFrom-Json
$recipeHash = [string]$evidence.provenance.generator.recipeSha256
$recipeSlug = ([string]$evidence.provenance.generator.recipeId -replace '[^A-Za-z0-9._-]+', '-').Trim('-')

$validateArguments = @(
    "validate-evidence",
    "--manifest", $pendingEvidencePath,
    "--dll", $DllPath,
    "--pdb", $PdbPath,
    "--project-prefix", [string]$config.workspace.projectNamePrefix,
    "--project-hash-length", [string]$config.workspace.projectHashLength
)
if ($Experimental) {
    $validateArguments += "--experimental"
}
Invoke-NativeIdentityTool -ProjectRoot $projectRoot -Arguments $validateArguments | Out-Null

$evidencePath = Join-Path $evidenceDir ("native-evidence-set-" + $dllHash.Substring(0, 12) + "-" + $recipeSlug + "-" + $recipeHash.Substring(0, 12) + ".json")
Move-Item -LiteralPath $pendingEvidencePath -Destination $evidencePath -Force

Write-Host "Matched functions: $($evidence.selection.matchCount)"
Write-Host "PDB loaded:        $($evidence.provenance.pdb.loaded)"
Write-Host "PDB matched:       $($evidence.provenance.pdb.matchesBinary)"
Write-Host "Trust status:      $($evidence.trust.status)"
Write-Host "Native evidence:   $evidencePath"
Write-Host "Local raw export:  $rawEvidencePath"
