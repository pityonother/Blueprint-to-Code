param(
    [string]$ProjectFile,
    [string]$DevKitRoot,
    [string]$DllPath,
    [string]$PdbPath,
    [string]$WorkspaceRoot,
    [string]$GhidraPath,
    [string]$JavaHome,
    [string]$ToolsRoot,
    [string]$MaxMemory = "8G",
    [switch]$AllowHashMismatch,
    [switch]$Help
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "NativeAnalysis.Common.ps1")

if ($Help) {
    Write-Host "Usage: .\scripts\native_analysis\Start-Ghidra.ps1 [-ProjectFile <file.gpr>]"
    Write-Host "Optional overrides: -DevKitRoot, -DllPath, -PdbPath, -WorkspaceRoot, -GhidraPath, -JavaHome, -ToolsRoot, -MaxMemory."
    Write-Host "The default project is selected from the current DLL SHA-256. A different-hash project is never reused."
    exit 0
}

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$context = Resolve-NativeAnalysisContext -GhidraPath $GhidraPath -JavaHome $JavaHome -ToolsRoot $ToolsRoot
$config = $context.Config
Set-NativeAnalysisProcessEnvironment -Context $context -MaxMemory $MaxMemory
$env:GHIDRA_GUI_MAXMEM = $MaxMemory

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

if (-not $AllowHashMismatch) {
    if ($identity.binary.sha256 -ne [string]$config.shooterGame.binarySha256) {
        throw "NATIVE_BINARY_HASH_UNREGISTERED: Current DLL SHA-256 is not registered in toolchain.json."
    }
    if ($identity.pdb.sha256 -ne [string]$config.shooterGame.pdbSha256) {
        throw "NATIVE_PDB_HASH_MISMATCH: Current PDB SHA-256 is not registered in toolchain.json."
    }
}

$layout = Get-NativeProjectLayout -Identity $identity -Config $config -ToolsRoot $context.ToolsRoot -WorkspaceRoot $WorkspaceRoot
if ($ProjectFile) {
    $resolvedProject = (Resolve-Path -LiteralPath $ProjectFile).Path
    if ($resolvedProject -ne [System.IO.Path]::GetFullPath($layout.ProjectFile)) {
        throw "NATIVE_PROJECT_PROGRAM_HASH_MISMATCH: -ProjectFile does not match the current DLL hash project."
    }
    Test-NativeProjectManifest -ManifestPath $layout.ProjectManifest -DllPath $DllPath -PdbPath $PdbPath -Config $config -ProjectRoot $projectRoot
    $ProjectFile = $resolvedProject
}
elseif (Test-Path -LiteralPath $layout.ProjectFile -PathType Leaf) {
    Test-NativeProjectManifest -ManifestPath $layout.ProjectManifest -DllPath $DllPath -PdbPath $PdbPath -Config $config -ProjectRoot $projectRoot
    $ProjectFile = $layout.ProjectFile
}

Write-Host "Ghidra: $($context.GhidraPath)"
Write-Host "Java:   $($context.JavaHome)"
Write-Host "Binary: $($identity.binary.module) $($identity.binary.sha256)"
if ($ProjectFile) {
    Write-Host "Project: $ProjectFile"
    & $context.GhidraRun $ProjectFile
}
else {
    Write-Host "No verified project exists for $($identity.project.hashPrefix); opening the Ghidra project manager."
    Write-Host "Expected workspace: $($layout.WorkspaceRoot)"
    & $context.GhidraRun
}

if ($LASTEXITCODE -ne 0) {
    throw "Ghidra failed to start (exit code $LASTEXITCODE)."
}
