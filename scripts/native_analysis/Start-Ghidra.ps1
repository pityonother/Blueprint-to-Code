param(
    [string]$ProjectFile,
    [string]$GhidraPath,
    [string]$JavaHome,
    [string]$ToolsRoot,
    [string]$MaxMemory = "8G",
    [switch]$Help
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "NativeAnalysis.Common.ps1")

if ($Help) {
    Write-Host "Usage: .\scripts\native_analysis\Start-Ghidra.ps1 [-ProjectFile <file.gpr>]"
    Write-Host "Optional overrides: -GhidraPath, -JavaHome, -ToolsRoot, -MaxMemory."
    exit 0
}

$context = Resolve-NativeAnalysisContext -GhidraPath $GhidraPath -JavaHome $JavaHome -ToolsRoot $ToolsRoot
Set-NativeAnalysisProcessEnvironment -Context $context -MaxMemory $MaxMemory
$env:GHIDRA_GUI_MAXMEM = $MaxMemory

if (-not $ProjectFile) {
    $workspaceRoot = Join-Path $context.ToolsRoot $context.Config.workspace.folderName
    $defaultProject = Join-Path $workspaceRoot ($context.Config.workspace.projectName + ".gpr")
    if (Test-Path -LiteralPath $defaultProject -PathType Leaf) {
        $ProjectFile = $defaultProject
    }
}

Write-Host "Ghidra: $($context.GhidraPath)"
Write-Host "Java:   $($context.JavaHome)"
if ($ProjectFile) {
    $resolvedProject = (Resolve-Path -LiteralPath $ProjectFile).Path
    Write-Host "Project: $resolvedProject"
    & $context.GhidraRun $resolvedProject
}
else {
    Write-Host "No existing native project was found; opening the Ghidra project manager."
    & $context.GhidraRun
}

if ($LASTEXITCODE -ne 0) {
    throw "Ghidra failed to start (exit code $LASTEXITCODE)."
}
