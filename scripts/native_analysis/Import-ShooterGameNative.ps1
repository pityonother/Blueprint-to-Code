[CmdletBinding()]
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
    [string]$EvidenceDir,
    [switch]$AllowHashMismatch,
    [switch]$Experimental,
    [switch]$Reimport,
    [switch]$Reanalyze,
    [switch]$Help
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$runner = Join-Path $PSScriptRoot "Run-NativeRecipe.ps1"
$defaultRecipe = Join-Path $PSScriptRoot "recipes\ark-loot-quality.v1.json"

if ($Help) {
    Write-Host "Usage: .\scripts\native_analysis\Import-ShooterGameNative.ps1"
    Write-Host "Compatibility entry point for the versioned ark-loot-quality/v1 recipe."
    Write-Host "All native selection, validation, and Evidence Store work is delegated to Run-NativeRecipe.ps1."
    & $runner -RecipePath $defaultRecipe -Help
    exit $LASTEXITCODE
}

$arguments = @{
    RecipePath = $defaultRecipe
}
foreach ($name in @(
        "DevKitRoot",
        "DllPath",
        "PdbPath",
        "WorkspaceRoot",
        "ProjectName",
        "GhidraPath",
        "JavaHome",
        "ToolsRoot",
        "MaxMemory",
        "EvidenceDir"
    )) {
    $value = Get-Variable -Name $name -ValueOnly
    if ($value) {
        $arguments[$name] = $value
    }
}
if ($AnalysisTimeoutSeconds) {
    $arguments.AnalysisTimeoutSeconds = $AnalysisTimeoutSeconds
}
foreach ($name in @(
        "AllowHashMismatch",
        "Experimental",
        "Reimport",
        "Reanalyze"
    )) {
    if ((Get-Variable -Name $name -ValueOnly).IsPresent) {
        $arguments[$name] = $true
    }
}

& $runner @arguments
exit $LASTEXITCODE
