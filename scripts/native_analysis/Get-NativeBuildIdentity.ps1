param(
    [string]$DevKitRoot,
    [string]$DllPath,
    [string]$PdbPath,
    [string]$OutputPath,
    [switch]$Pretty,
    [switch]$Help
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot "NativeAnalysis.Common.ps1")

if ($Help) {
    Write-Host "Usage: .\scripts\native_analysis\Get-NativeBuildIdentity.ps1 [-DllPath <dll>] [-PdbPath <pdb>] [-OutputPath <json>] [-Pretty]"
    Write-Host "Parses PE CodeView RSDS and MSF7 PDB stream 1, then fails unless GUID and Age match."
    exit 0
}

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$config = Get-NativeAnalysisConfig
$resolvedDevKitRoot = Resolve-BlueprintToCodeDevKitRoot -DevKitRoot $DevKitRoot -Config $config -ProjectRoot $projectRoot
if (-not $DllPath) {
    $DllPath = Join-Path $resolvedDevKitRoot $config.shooterGame.binaryRelativePath
}
if (-not $PdbPath) {
    $PdbPath = Join-Path $resolvedDevKitRoot $config.shooterGame.pdbRelativePath
}

$arguments = @()
if ($Pretty) {
    $arguments += "--pretty"
}
if ($OutputPath) {
    $arguments += @("--output", [System.IO.Path]::GetFullPath($OutputPath))
}
$arguments += @(
    "build",
    "--dll", [System.IO.Path]::GetFullPath($DllPath),
    "--pdb", [System.IO.Path]::GetFullPath($PdbPath),
    "--project-prefix", [string]$config.workspace.projectNamePrefix,
    "--project-hash-length", [string]$config.workspace.projectHashLength
)
Invoke-NativeIdentityTool -ProjectRoot $projectRoot -Arguments $arguments
