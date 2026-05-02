param(
    [string]$DevKitPluginsDir,
    [switch]$ForceSourceInstall,
    [switch]$Uninstall
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

if ($Uninstall) {
    if (Test-Path $DestinationPlugin) {
        $ResolvedDestination = Resolve-Path $DestinationPlugin -ErrorAction Stop
        if ($ResolvedDestination.Path -notlike "*\BlueprintToCodeExporter") {
            throw "Refusing to remove unexpected path: $($ResolvedDestination.Path)"
        }
        Remove-Item -LiteralPath $ResolvedDestination.Path -Recurse -Force
        Write-Host "Removed BlueprintToCodeExporter from:"
        Write-Host "  $($ResolvedDestination.Path)"
    } else {
        Write-Host "BlueprintToCodeExporter is not installed in:"
        Write-Host "  $ResolvedPluginsDir"
    }
    exit 0
}

$EngineDir = $null
$PluginPathText = $ResolvedPluginsDir.Path.TrimEnd('\')
if ($PluginPathText -match "\\Engine\\Plugins$") {
    $EngineDir = Split-Path -Parent $PluginPathText
} elseif ($PluginPathText -match "^(.*\\Engine)\\Plugins(\\.*)?$") {
    $EngineDir = $Matches[1]
}

$HasPrecompiledBinary = Test-Path (Join-Path $SourcePlugin "Binaries")
$CanCompileSourcePlugin = $false
if ($EngineDir) {
    $BuildBat = Join-Path $EngineDir "Build\BatchFiles\Build.bat"
    $RulesDll = Join-Path $EngineDir "Intermediate\Build\BuildRules\UE5Rules.dll"
    $RuntimeSource = Join-Path $EngineDir "Source\Runtime"
    $CanCompileSourcePlugin = (Test-Path $BuildBat) -and ((Test-Path $RulesDll) -or (Test-Path $RuntimeSource))
}

if (-not $HasPrecompiledBinary -and -not $CanCompileSourcePlugin -and -not $ForceSourceInstall) {
    Write-Host ""
    Write-Host "This ARK DevKit install can scan plugins, but it does not look able to compile this C++ source plugin."
    if ($EngineDir) {
        Write-Host "Checked Engine directory:"
        Write-Host "  $EngineDir"
        Write-Host "Missing either:"
        Write-Host "  Engine\Intermediate\Build\BuildRules\UE5Rules.dll"
        Write-Host "or:"
        Write-Host "  Engine\Source\Runtime"
    }
    Write-Host ""
    Write-Host "Install aborted so DevKit will not show 'cannot find module BlueprintToCodeExporter' on startup."
    Write-Host "Use the Python exporter / graph-name candidate fallback for this DevKit build."
    Write-Host ""
    Write-Host "If you have a separate DevKit build environment that can compile editor plugins,"
    Write-Host "rerun with -ForceSourceInstall."
    exit 2
}

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
