param(
    [string]$DevKitPluginsDir
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Resolve-Path (Join-Path $ScriptDir "..\..")
$SourcePlugin = Join-Path $RepoRoot "devkit_plugins\BlueprintToCodeExporter"

if (-not (Test-Path $SourcePlugin)) {
    throw "Source plugin not found: $SourcePlugin"
}

if (-not $DevKitPluginsDir) {
    Write-Host "Paste the ARK DevKit Plugins directory, for example:"
    Write-Host "  C:\Program Files\Epic Games\ARKDevKit\Engine\Plugins"
    Write-Host "or a project/plugin folder that your ARK DevKit build loads."
    $DevKitPluginsDir = Read-Host "DevKit Plugins directory"
}

if (-not $DevKitPluginsDir) {
    throw "No DevKit Plugins directory was provided."
}

$ResolvedPluginsDir = Resolve-Path $DevKitPluginsDir -ErrorAction Stop
$DestinationPlugin = Join-Path $ResolvedPluginsDir "BlueprintToCodeExporter"

if (Test-Path $DestinationPlugin) {
    $Backup = "$DestinationPlugin.backup.$(Get-Date -Format yyyyMMdd_HHmmss)"
    Move-Item -LiteralPath $DestinationPlugin -Destination $Backup
    Write-Host "Existing plugin moved to: $Backup"
}

Copy-Item -LiteralPath $SourcePlugin -Destination $DestinationPlugin -Recurse
[Environment]::SetEnvironmentVariable("BLUEPRINT_TO_CODE_ROOT", $RepoRoot.Path, "User")

Write-Host ""
Write-Host "Installed BlueprintToCodeExporter to:"
Write-Host "  $DestinationPlugin"
Write-Host ""
Write-Host "Set user environment variable:"
Write-Host "  BLUEPRINT_TO_CODE_ROOT=$($RepoRoot.Path)"
Write-Host ""
Write-Host "Next:"
Write-Host "  1. Restart ARK DevKit."
Write-Host "  2. Enable/compile the Blueprint To Code Exporter plugin if prompted."
Write-Host "  3. Select a Blueprint asset in the Content Browser."
Write-Host "  4. Use Tools -> Blueprint to Code -> Export Selected Blueprint Graph Queue."
