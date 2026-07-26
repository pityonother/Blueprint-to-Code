[CmdletBinding()]
param(
    [Alias("Recipe")]
    [string]$RecipePath,
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
    [string]$EvidenceDir,
    [switch]$AllowHashMismatch,
    [switch]$Experimental,
    [switch]$Reimport,
    [switch]$Reanalyze,
    [switch]$Help
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot "NativeAnalysis.Common.ps1")

trap {
    $rawMessage = [string]$_.Exception.Message
    $safeMessage = $rawMessage
    foreach ($pattern in @(
            '(?i)\b(authorization|proxy-authorization)\s*[:=]\s*[^\s,;]+',
            '(?i)\b(api[_-]?key|token|password|secret)\s*[:=]\s*[^\s,;]+'
        )) {
        $safeMessage = [regex]::Replace(
            $safeMessage,
            $pattern,
            '$1=[REDACTED]'
        )
    }
    if ($safeMessage.Length -gt 4000) {
        $safeMessage = $safeMessage.Substring(0, 4000) + " [truncated]"
    }
    $codeMatch = [regex]::Match($safeMessage, '\b(NATIVE_[A-Z0-9_]+)\b')
    $diagnosticCode = if ($codeMatch.Success) {
        $codeMatch.Groups[1].Value
    }
    else {
        "NATIVE_RUN_FAILED"
    }
    $diagnosticRoot = [System.IO.Path]::GetFullPath(
        (Join-Path $PSScriptRoot "..\..\logs\native_analysis")
    )
    $diagnosticPath = Join-Path $diagnosticRoot (
        "native-diagnostic-" +
        (Get-Date -Format "yyyyMMdd-HHmmss-fff") + "-" +
        [Guid]::NewGuid().ToString("N") + ".json"
    )
    $diagnostic = [ordered]@{
        schema = "blueprint-to-code-native-run-diagnostic/v1"
        ok = $false
        code = $diagnosticCode
        message = $safeMessage
        time = [DateTime]::UtcNow.ToString("o")
    }
    try {
        New-Item -ItemType Directory -Force -Path $diagnosticRoot | Out-Null
        $diagnostic | ConvertTo-Json -Depth 6 |
            Set-Content -LiteralPath $diagnosticPath -Encoding UTF8
        [Console]::Error.WriteLine(
            "$diagnosticCode`: Native recipe run failed. Diagnostic: $diagnosticPath"
        )
    }
    catch {
        [Console]::Error.WriteLine(
            "$diagnosticCode`: Native recipe run failed; diagnostic write also failed."
        )
    }
    exit 1
}

if ($Help) {
    Write-Host "Usage: .\scripts\native_analysis\Run-NativeRecipe.ps1 -Recipe <recipe.json>"
    Write-Host "Runs one exact native-analysis recipe against a matching DLL/PDB, validates Native Evidence v2, and imports it into the bounded Evidence Store."
    Write-Host "When -DllPath/-PdbPath are omitted, the configured ARK DevKit binary and PDB are used."
    Write-Host "-AllowHashMismatch bypasses only the registered ARK toolchain hashes; binary-hash project isolation remains mandatory."
    Write-Host "-Experimental allows a dirty local generator and marks the output non-formal."
    exit 0
}
if (-not $RecipePath) {
    throw "NATIVE_RECIPE_SCHEMA_INVALID: -Recipe is required."
}
if ($AllowHashMismatch -and -not $Experimental) {
    throw "NATIVE_HASH_BYPASS_REQUIRES_EXPERIMENTAL: -AllowHashMismatch may only be used with -Experimental."
}

function Copy-NativeRunInput {
    param(
        [Parameter(Mandatory)]
        [string]$Source,
        [Parameter(Mandatory)]
        [string]$Destination
    )

    try {
        New-Item -ItemType HardLink -Path $Destination -Target $Source -ErrorAction Stop | Out-Null
    }
    catch {
        Copy-Item -LiteralPath $Source -Destination $Destination
    }
}

function Invoke-NativePythonCommand {
    param(
        [Parameter(Mandatory)]
        [string]$PythonExe,
        [Parameter(Mandatory)]
        [string[]]$Arguments,
        [Parameter(Mandatory)]
        [string]$FailureMessage
    )

    $savedErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $output = (& $PythonExe @Arguments 2>&1) -join [Environment]::NewLine
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $savedErrorActionPreference
    }
    if ($exitCode -ne 0) {
        throw "$FailureMessage`n$output"
    }
    return $output
}

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$context = Resolve-NativeAnalysisContext -GhidraPath $GhidraPath -JavaHome $JavaHome -ToolsRoot $ToolsRoot
$config = $context.Config
$toolchainPath = Join-Path $PSScriptRoot "toolchain.json"
$toolchainArguments = @(
    "verify-toolchain",
    "--toolchain", $toolchainPath,
    "--ghidra-home", $context.GhidraPath,
    "--java-home", $context.JavaHome
)
Invoke-NativeIdentityTool -ProjectRoot $projectRoot -Arguments $toolchainArguments | Out-Null
Set-NativeAnalysisProcessEnvironment -Context $context -MaxMemory $MaxMemory
if (-not $AnalysisTimeoutSeconds) {
    $AnalysisTimeoutSeconds = [int]$config.workspace.analysisTimeoutSeconds
}

$RecipePath = [System.IO.Path]::GetFullPath($RecipePath)
$registeredModule = [System.IO.Path]::GetFileName([string]$config.shooterGame.binaryRelativePath)
$recipeArguments = @(
    "recipe-info",
    "--recipe", $RecipePath,
    "--registered-module", $registeredModule
)
if ($Experimental) {
    $recipeArguments += "--experimental"
}
$recipeDocument = (Invoke-NativeIdentityTool -ProjectRoot $projectRoot -Arguments $recipeArguments) |
    ConvertFrom-Json
$recipe = $recipeDocument.recipe
$recipeHash = [string]$recipeDocument.sha256
$requiresRegisteredHashes = [bool]$recipeDocument.hashPolicy.requiresRegisteredHashes

if ([bool]$DllPath -xor [bool]$PdbPath) {
    throw "NATIVE_TOOL_MISSING: -DllPath and -PdbPath must be provided together."
}
elseif (-not $DllPath -and -not $PdbPath) {
    $inputPaths = Resolve-NativeRecipeInputPaths `
        -DevKitRoot $DevKitRoot `
        -Config $config `
        -ProjectRoot $projectRoot
}
else {
    $inputPaths = [pscustomobject]@{
        DllPath = [System.IO.Path]::GetFullPath($DllPath)
        PdbPath = [System.IO.Path]::GetFullPath($PdbPath)
        Source = "explicit"
    }
}
$DllPath = [string]$inputPaths.DllPath
$PdbPath = [string]$inputPaths.PdbPath

$identity = Get-NativeBuildIdentityObject -DllPath $DllPath -PdbPath $PdbPath -Config $config -ProjectRoot $projectRoot
$dllHash = [string]$identity.binary.sha256
$pdbHash = [string]$identity.pdb.sha256
if (-not [string]::Equals(
        [string]$recipe.binaryModule,
        [string]$identity.binary.module,
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
    throw "NATIVE_PROJECT_PROGRAM_HASH_MISMATCH: Recipe module $($recipe.binaryModule) does not match input module $($identity.binary.module)."
}

$expectedDllHash = [string]$config.shooterGame.binarySha256
$expectedPdbHash = [string]$config.shooterGame.pdbSha256
if ($requiresRegisteredHashes -and -not $AllowHashMismatch) {
    if ($dllHash -ne $expectedDllHash) {
        throw "NATIVE_BINARY_HASH_UNREGISTERED: Expected $expectedDllHash, got $dllHash. Record the new DevKit version before using -AllowHashMismatch."
    }
    if ($pdbHash -ne $expectedPdbHash) {
        throw "NATIVE_PDB_HASH_MISMATCH: Expected $expectedPdbHash, got $pdbHash. Record the new DevKit version before using -AllowHashMismatch."
    }
}
elseif ($requiresRegisteredHashes -and
        ($dllHash -ne $expectedDllHash -or $pdbHash -ne $expectedPdbHash)) {
    Write-Warning "Toolchain hashes were bypassed, but project isolation still uses current binary hash $($identity.project.hashPrefix)."
}
elseif (-not $requiresRegisteredHashes) {
    Write-Host "Registered ARK hashes: not applicable to public module $($recipe.binaryModule)"
}
if ($Experimental -and [bool]$recipe.requirements.formalProvenanceRequired) {
    Write-Warning "This recipe requires formal provenance for publishable claims; this local run will remain experimental."
}

$computedProjectName = [string]$identity.project.name
if ($ProjectName -and $ProjectName -ne $computedProjectName) {
    throw "NATIVE_PROJECT_PROGRAM_HASH_MISMATCH: -ProjectName must be $computedProjectName for the current DLL."
}
$ProjectName = $computedProjectName
$layout = Get-NativeProjectLayout -Identity $identity -Config $config -ToolsRoot $context.ToolsRoot -WorkspaceRoot $WorkspaceRoot
$WorkspaceRoot = $layout.WorkspaceRoot
$projectFile = $layout.ProjectFile
$projectManifest = $layout.ProjectManifest

if (-not $EvidenceDir) {
    $EvidenceDir = Join-Path $projectRoot "native_evidence"
}
$EvidenceDir = [System.IO.Path]::GetFullPath($EvidenceDir)
$logDir = Join-Path $projectRoot "logs\native_analysis"
New-Item -ItemType Directory -Force -Path $WorkspaceRoot, $EvidenceDir, $logDir | Out-Null

$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$runNonce = [Guid]::NewGuid().ToString("N")
$recipeSlug = ([string]$recipe.recipeId -replace '[^A-Za-z0-9._-]+', '-').Trim('-')
$localTempBase = Join-Path ([System.IO.Path]::GetTempPath()) "BlueprintToCodeNative"
$runRoot = Join-Path $localTempBase (
    $dllHash.Substring(0, 12) + "-" + $recipeHash.Substring(0, 12) + "-" +
    $timestamp + "-" + $runNonce
)
$runScriptDir = Join-Path $runRoot "scripts"
$runInputDir = Join-Path $runRoot "input"
$runRootCreated = $false
try {
    New-Item -ItemType Directory -Force -Path $localTempBase | Out-Null
    New-Item -ItemType Directory -Path $runRoot -ErrorAction Stop | Out-Null
    $runRootCreated = $true
    New-Item -ItemType Directory -Path $runScriptDir, $runInputDir -ErrorAction Stop | Out-Null

$stagedDll = Join-Path $runInputDir ([System.IO.Path]::GetFileName($DllPath))
$stagedPdb = Join-Path $runInputDir ([System.IO.Path]::GetFileName($PdbPath))
$stagedRecipe = Join-Path $runInputDir "recipe.json"
Copy-NativeRunInput -Source $DllPath -Destination $stagedDll
Copy-NativeRunInput -Source $PdbPath -Destination $stagedPdb
Copy-Item -LiteralPath $RecipePath -Destination $stagedRecipe
$exporterPath = Join-Path $PSScriptRoot "ghidra\ExportNativeRecipe.java"
$configuratorPath = Join-Path $PSScriptRoot "ghidra\ConfigurePdbAnalyzer.java"
Copy-Item -LiteralPath $exporterPath, $configuratorPath -Destination $runScriptDir

if ((Get-LowerSha256 -Path $stagedDll) -ne $dllHash -or
        (Get-LowerSha256 -Path $stagedPdb) -ne $pdbHash -or
        (Get-LowerSha256 -Path $stagedRecipe) -ne $recipeHash) {
    throw "NATIVE_EVIDENCE_PROVENANCE_MISMATCH: No-space staging changed a native-analysis input."
}

$rawTempPath = Join-Path $runRoot ("recipe-export-" + $runNonce + ".raw-v1.json")
$pendingTempPath = Join-Path $runRoot ("evidence-" + $runNonce + ".pending-v2.json")
$logTempPath = Join-Path $runRoot ("ghidra-" + $runNonce + ".log")
$scriptLogTempPath = Join-Path $runRoot ("ghidra-script-" + $runNonce + ".log")
$rawEvidencePath = Join-Path $EvidenceDir (
    "native-recipe-raw-" + $dllHash.Substring(0, 12) + "-" +
    $recipeSlug + "-" + $recipeHash.Substring(0, 12) + "-" + $timestamp + "-" +
    $runNonce + ".json"
)
$evidencePath = Join-Path $EvidenceDir (
    "native-evidence-set-" + $dllHash.Substring(0, 12) + "-" +
    $recipeSlug + "-" + $recipeHash.Substring(0, 12) + "-" + $timestamp + "-" +
    $runNonce + ".json"
)
$logPath = Join-Path $logDir (
    "ghidra-recipe-" + $timestamp + "-" + $runNonce + ".log"
)
$scriptLogPath = Join-Path $logDir (
    "ghidra-recipe-script-" + $timestamp + "-" + $runNonce + ".log"
)

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
    $arguments += @("-import", $stagedDll, "-overwrite")
}
elseif ($projectExists) {
    $arguments += @("-process", [System.IO.Path]::GetFileName($DllPath))
}
else {
    $arguments += @("-import", $stagedDll)
}
$arguments += @("-preScript", "ConfigurePdbAnalyzer.java", $runInputDir)
if ($projectExists -and -not $Reimport -and -not $Reanalyze) {
    $arguments += "-noanalysis"
}
else {
    $arguments += @("-analysisTimeoutPerFile", [string]$AnalysisTimeoutSeconds)
}
$mode = if ($Experimental) { "experimental" } else { "formal" }
$arguments += @(
    "-postScript", "ExportNativeRecipe.java",
    $rawTempPath, $stagedRecipe, $recipeHash, $mode, [string]$AnalysisTimeoutSeconds,
    "-scriptPath", $runScriptDir,
    "-log", $logTempPath,
    "-scriptlog", $scriptLogTempPath
)

Write-Host "Recipe:     $($recipe.recipeId)"
Write-Host "Recipe SHA: $recipeHash"
Write-Host "Targets:    $($recipe.targets.Count)"
Write-Host "DLL:        $DllPath"
Write-Host "DLL SHA:    $dllHash"
Write-Host "PDB:        $PdbPath"
Write-Host "PDB SHA:    $pdbHash"
Write-Host "PDB ID:     $($identity.pdb.guid) age $($identity.pdb.age) (matched)"
Write-Host "Project:    $projectFile"
Write-Host "Mode:       $mode"

& $context.AnalyzeHeadless @arguments
$ghidraExitCode = $LASTEXITCODE
if (Test-Path -LiteralPath $logTempPath -PathType Leaf) {
    Copy-Item -LiteralPath $logTempPath -Destination $logPath
}
if (Test-Path -LiteralPath $scriptLogTempPath -PathType Leaf) {
    Copy-Item -LiteralPath $scriptLogTempPath -Destination $scriptLogPath
}
$analysisTimedOut = Select-String -LiteralPath $logTempPath `
    -SimpleMatch "REPORT: Analysis timed out" `
    -ErrorAction SilentlyContinue
if ($analysisTimedOut) {
    throw "NATIVE_ANALYSIS_TIMEOUT: Ghidra reported that analysis timed out. See $logPath"
}
if ($ghidraExitCode -ne 0) {
    throw "Ghidra headless analysis failed (exit code $ghidraExitCode). See $logPath"
}
$scriptFailure = Select-String -LiteralPath $logTempPath -Pattern "REPORT SCRIPT ERROR|SCRIPT ERROR:" -ErrorAction SilentlyContinue
if ($scriptFailure) {
    throw "A Ghidra script failed even though the headless process returned success. See $logPath"
}
if (-not (Test-Path -LiteralPath $rawTempPath -PathType Leaf)) {
    throw "NATIVE_EXPORT_SCHEMA_INVALID: Ghidra completed without creating the recipe export."
}
Copy-Item -LiteralPath $rawTempPath -Destination $rawEvidencePath

$rawEvidence = Get-Content -LiteralPath $rawTempPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ([string]$rawEvidence.binarySha256 -ne $dllHash) {
    throw "NATIVE_PROJECT_PROGRAM_HASH_MISMATCH: Ghidra currentProgram hash $($rawEvidence.binarySha256) does not match input DLL $dllHash."
}
if ([bool]$recipe.requirements.pdbRequired -and -not [bool]$rawEvidence.pdbLoaded) {
    throw "NATIVE_PDB_NOT_LOADED: The recipe requires PDB evidence, but Ghidra did not report a loaded PDB."
}

$ghidraVersion = Get-NativeGhidraVersion -ApplicationPropertiesPath $context.GhidraApplicationProperties
$javaRuntime = Get-NativeJavaRuntimeInfo -JavaExe $context.JavaExe
$wrapArguments = @(
    "--pretty",
    "--output", $pendingTempPath,
    "wrap-recipe",
    "--dll", $DllPath,
    "--pdb", $PdbPath,
    "--project-prefix", [string]$config.workspace.projectNamePrefix,
    "--project-hash-length", [string]$config.workspace.projectHashLength,
    "--recipe", $RecipePath,
    "--raw-export", $rawTempPath,
    "--toolchain", $toolchainPath,
    "--ghidra-home", $context.GhidraPath,
    "--java-home", $context.JavaHome,
    "--repository-root", $projectRoot,
    "--runner", $PSCommandPath,
    "--exporter", $exporterPath,
    "--pdb-configurator", $configuratorPath,
    "--ghidra-version", $ghidraVersion,
    "--java-vendor", $javaRuntime.Vendor,
    "--java-version", $javaRuntime.Version
)
if ($Experimental) {
    $wrapArguments += "--experimental"
}
Invoke-NativeIdentityTool -ProjectRoot $projectRoot -Arguments $wrapArguments | Out-Null
if (-not (Test-Path -LiteralPath $pendingTempPath -PathType Leaf)) {
    throw "NATIVE_EXPORT_SCHEMA_INVALID: Provenance wrapper did not create a v2 evidence manifest."
}

$validateArguments = @(
    "validate-evidence",
    "--manifest", $pendingTempPath,
    "--dll", $DllPath,
    "--pdb", $PdbPath,
    "--project-prefix", [string]$config.workspace.projectNamePrefix,
    "--project-hash-length", [string]$config.workspace.projectHashLength
)
if ($Experimental) {
    $validateArguments += "--experimental"
}
Invoke-NativeIdentityTool -ProjectRoot $projectRoot -Arguments $validateArguments | Out-Null
Copy-Item -LiteralPath $pendingTempPath -Destination $evidencePath

$evidence = Get-Content -LiteralPath $evidencePath -Raw -Encoding UTF8 | ConvertFrom-Json
$storeDir = Join-Path $EvidenceDir (
    "stores\" + $dllHash.Substring(0, 12) + "\" +
    $recipeSlug + "-" + $recipeHash.Substring(0, 12) + "-" + $timestamp + "-" +
    $runNonce
)
$pythonExe = Resolve-NativeIdentityPython -ProjectRoot $projectRoot
$importArguments = @(
    (Join-Path $projectRoot "scripts\import_native_evidence.py"),
    "--source", $evidencePath,
    "--evidence-dir", $storeDir,
    "--pretty"
)
if ($Experimental) {
    $importArguments += "--allow-experimental"
}
$importOutput = Invoke-NativePythonCommand -PythonExe $pythonExe -Arguments $importArguments `
    -FailureMessage "Native Evidence Store import failed."
$storeValidateArguments = @(
    (Join-Path $projectRoot "scripts\validate_native_evidence.py"),
    "--evidence-dir", $storeDir,
    "--dll", $DllPath,
    "--pdb", $PdbPath,
    "--pretty"
)
if ($Experimental) {
    $storeValidateArguments += "--experimental"
}
$storeValidationOutput = Invoke-NativePythonCommand -PythonExe $pythonExe `
    -Arguments $storeValidateArguments `
    -FailureMessage "Native Evidence v2 store validation failed."
$queryArguments = @(
    (Join-Path $projectRoot "scripts\query_native_evidence.py"),
    "--evidence-dir", $storeDir,
    "overview",
    "--budget", "700"
)
$overviewOutput = Invoke-NativePythonCommand -PythonExe $pythonExe -Arguments $queryArguments `
    -FailureMessage "Native Evidence Store overview query failed."

Write-Host "Target results:"
foreach ($targetResult in $rawEvidence.targetResults) {
    Write-Host (
        "  {0}: expected={1} resolved={2} status={3}" -f
        $targetResult.targetId,
        $targetResult.expectedMatches,
        $targetResult.matchCount,
        $targetResult.status
    )
}
Write-Host "Recipe targets:      $($evidence.selection.targetCount)"
Write-Host "Exported functions:  $($evidence.selection.resolvedFunctionCount)"
Write-Host "Field queries:       $($rawEvidence.fieldQueryResults.Count)"
Write-Host "Vtable queries:      $($rawEvidence.vtableQueryResults.Count)"
Write-Host "PDB loaded:          $($evidence.provenance.pdb.loaded)"
Write-Host "Trust status:        $($evidence.trust.status)"
Write-Host "Native evidence:     $evidencePath"
Write-Host "Local raw export:    $rawEvidencePath"
Write-Host "Evidence Store:      $storeDir"
Write-Host "Store import result:"
Write-Host $importOutput
Write-Host "Store v2 validation:"
Write-Host $storeValidationOutput
Write-Host "Store overview:"
Write-Host $overviewOutput
}
finally {
    if ($runRootCreated) {
        try {
            Remove-NativeRunDirectory -RunRoot $runRoot -TempBase $localTempBase
        }
        catch {
            throw "NATIVE_TEMP_CLEANUP_FAILED: $($_.Exception.Message)"
        }
    }
}
