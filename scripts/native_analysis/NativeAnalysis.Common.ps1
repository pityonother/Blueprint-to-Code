Set-StrictMode -Version Latest

function Get-NativeAnalysisConfig {
    $configPath = Join-Path $PSScriptRoot "toolchain.json"
    if (-not (Test-Path -LiteralPath $configPath -PathType Leaf)) {
        throw "Native analysis toolchain config is missing: $configPath"
    }

    return Get-Content -LiteralPath $configPath -Raw -Encoding UTF8 | ConvertFrom-Json
}

function Resolve-NativeAnalysisContext {
    param(
        [string]$GhidraPath,
        [string]$JavaHome,
        [string]$ToolsRoot
    )

    $config = Get-NativeAnalysisConfig

    if (-not $ToolsRoot) {
        $ToolsRoot = $env:BLUEPRINT_TO_CODE_TOOLS_ROOT
    }
    if (-not $ToolsRoot) {
        if (-not $env:USERPROFILE) {
            throw "USERPROFILE is unavailable. Pass -ToolsRoot explicitly."
        }
        $ToolsRoot = Join-Path $env:USERPROFILE "tools-projects"
    }

    if (-not $GhidraPath) {
        $GhidraPath = $env:BLUEPRINT_TO_CODE_GHIDRA_HOME
    }
    if (-not $GhidraPath) {
        $GhidraPath = Join-Path $ToolsRoot $config.ghidra.folderName
    }

    if (-not $JavaHome) {
        $JavaHome = $env:BLUEPRINT_TO_CODE_JAVA_HOME
    }
    if (-not $JavaHome) {
        $JavaHome = Join-Path $ToolsRoot $config.java.folderName
    }

    $ghidraRun = Join-Path $GhidraPath "ghidraRun.bat"
    $analyzeHeadless = Join-Path $GhidraPath "support\analyzeHeadless.bat"
    $ghidraApplicationProperties = Join-Path $GhidraPath "Ghidra\application.properties"
    $javaExe = Join-Path $JavaHome "bin\java.exe"

    foreach ($requiredFile in @($ghidraRun, $analyzeHeadless, $ghidraApplicationProperties, $javaExe)) {
        if (-not (Test-Path -LiteralPath $requiredFile -PathType Leaf)) {
            throw "Required native-analysis tool is missing: $requiredFile"
        }
    }

    [pscustomobject]@{
        Config = $config
        ToolsRoot = [System.IO.Path]::GetFullPath($ToolsRoot)
        GhidraPath = [System.IO.Path]::GetFullPath($GhidraPath)
        JavaHome = [System.IO.Path]::GetFullPath($JavaHome)
        GhidraRun = $ghidraRun
        AnalyzeHeadless = $analyzeHeadless
        GhidraApplicationProperties = $ghidraApplicationProperties
        JavaExe = $javaExe
    }
}

function Set-NativeAnalysisProcessEnvironment {
    param(
        [Parameter(Mandatory)]
        [pscustomobject]$Context,
        [string]$MaxMemory
    )

    if (-not $MaxMemory) {
        $MaxMemory = [string]$Context.Config.workspace.headlessMaxMemory
    }

    $env:JAVA_HOME = $Context.JavaHome
    $javaBin = Join-Path $Context.JavaHome "bin"
    $pathParts = @($env:Path -split ";" | Where-Object { $_ })
    if ($pathParts -notcontains $javaBin) {
        $env:Path = $javaBin + ";" + $env:Path
    }
    $env:GHIDRA_HEADLESS_MAXMEM = $MaxMemory
}

function Get-LowerSha256 {
    param(
        [Parameter(Mandatory)]
        [string]$Path
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "File does not exist: $Path"
    }
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Resolve-NativeIdentityPython {
    param(
        [Parameter(Mandatory)]
        [string]$ProjectRoot
    )

    $bundledPython = Join-Path $ProjectRoot "runtime\python\python.exe"
    if (Test-Path -LiteralPath $bundledPython -PathType Leaf) {
        return [System.IO.Path]::GetFullPath($bundledPython)
    }
    $systemPython = Get-Command python -ErrorAction SilentlyContinue
    if ($systemPython) {
        return [System.IO.Path]::GetFullPath($systemPython.Source)
    }
    throw "NATIVE_TOOL_MISSING: Python is required for native identity validation."
}

function Invoke-NativeIdentityTool {
    param(
        [Parameter(Mandatory)]
        [string]$ProjectRoot,
        [Parameter(Mandatory)]
        [string[]]$Arguments
    )

    $pythonExe = Resolve-NativeIdentityPython -ProjectRoot $ProjectRoot
    $identityCli = Join-Path $ProjectRoot "scripts\native_analysis\native_identity.py"
    if (-not (Test-Path -LiteralPath $identityCli -PathType Leaf)) {
        throw "NATIVE_TOOL_MISSING: Native identity CLI is missing: $identityCli"
    }

    $savedErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $output = (& $pythonExe $identityCli @Arguments 2>&1) -join [Environment]::NewLine
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $savedErrorActionPreference
    }
    if ($exitCode -ne 0) {
        throw $output.Trim()
    }
    return $output
}

function Get-NativeBuildIdentityObject {
    param(
        [Parameter(Mandatory)]
        [string]$DllPath,
        [Parameter(Mandatory)]
        [string]$PdbPath,
        [Parameter(Mandatory)]
        [pscustomobject]$Config,
        [Parameter(Mandatory)]
        [string]$ProjectRoot
    )

    $arguments = @(
        "build",
        "--dll", [System.IO.Path]::GetFullPath($DllPath),
        "--pdb", [System.IO.Path]::GetFullPath($PdbPath),
        "--project-prefix", [string]$Config.workspace.projectNamePrefix,
        "--project-hash-length", [string]$Config.workspace.projectHashLength
    )
    $json = Invoke-NativeIdentityTool -ProjectRoot $ProjectRoot -Arguments $arguments
    return $json | ConvertFrom-Json
}

function Get-NativeProjectLayout {
    param(
        [Parameter(Mandatory)]
        [pscustomobject]$Identity,
        [Parameter(Mandatory)]
        [pscustomobject]$Config,
        [Parameter(Mandatory)]
        [string]$ToolsRoot,
        [string]$WorkspaceRoot
    )

    $workspaceBase = $WorkspaceRoot
    if (-not $workspaceBase) {
        $workspaceBase = Join-Path $ToolsRoot $Config.workspace.folderName
    }
    $workspaceBase = [System.IO.Path]::GetFullPath($workspaceBase)
    $resolvedWorkspace = Join-Path $workspaceBase ([string]$Identity.project.workspaceSlug)
    $projectName = [string]$Identity.project.name
    [pscustomobject]@{
        WorkspaceBase = $workspaceBase
        WorkspaceRoot = $resolvedWorkspace
        ProjectName = $projectName
        ProjectFile = Join-Path $resolvedWorkspace ($projectName + ".gpr")
        ProjectManifest = Join-Path $resolvedWorkspace ($projectName + ".manifest.json")
    }
}

function Test-NativeProjectManifest {
    param(
        [Parameter(Mandatory)]
        [string]$ManifestPath,
        [Parameter(Mandatory)]
        [string]$DllPath,
        [Parameter(Mandatory)]
        [string]$PdbPath,
        [Parameter(Mandatory)]
        [pscustomobject]$Config,
        [Parameter(Mandatory)]
        [string]$ProjectRoot
    )

    if (-not (Test-Path -LiteralPath $ManifestPath -PathType Leaf)) {
        throw "NATIVE_PROJECT_PROGRAM_HASH_MISMATCH: Project manifest is missing: $ManifestPath"
    }
    $arguments = @(
        "validate-project",
        "--manifest", [System.IO.Path]::GetFullPath($ManifestPath),
        "--dll", [System.IO.Path]::GetFullPath($DllPath),
        "--pdb", [System.IO.Path]::GetFullPath($PdbPath),
        "--project-prefix", [string]$Config.workspace.projectNamePrefix,
        "--project-hash-length", [string]$Config.workspace.projectHashLength
    )
    Invoke-NativeIdentityTool -ProjectRoot $ProjectRoot -Arguments $arguments | Out-Null
}

function Write-NativeProjectManifest {
    param(
        [Parameter(Mandatory)]
        [string]$ManifestPath,
        [Parameter(Mandatory)]
        [string]$DllPath,
        [Parameter(Mandatory)]
        [string]$PdbPath,
        [Parameter(Mandatory)]
        [pscustomobject]$Config,
        [Parameter(Mandatory)]
        [string]$ProjectRoot
    )

    $arguments = @(
        "--pretty",
        "--output", [System.IO.Path]::GetFullPath($ManifestPath),
        "project-manifest",
        "--dll", [System.IO.Path]::GetFullPath($DllPath),
        "--pdb", [System.IO.Path]::GetFullPath($PdbPath),
        "--project-prefix", [string]$Config.workspace.projectNamePrefix,
        "--project-hash-length", [string]$Config.workspace.projectHashLength
    )
    Invoke-NativeIdentityTool -ProjectRoot $ProjectRoot -Arguments $arguments | Out-Null
}

function Get-NativeGhidraVersion {
    param(
        [Parameter(Mandatory)]
        [string]$ApplicationPropertiesPath
    )

    $match = Select-String -LiteralPath $ApplicationPropertiesPath -Pattern '^application\.version=(.+)$' | Select-Object -First 1
    if (-not $match) {
        throw "NATIVE_TOOL_MISSING: Could not read the installed Ghidra version."
    }
    return $match.Matches[0].Groups[1].Value.Trim()
}

function Get-NativeJavaRuntimeInfo {
    param(
        [Parameter(Mandatory)]
        [string]$JavaExe
    )

    $savedErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $javaOutput = & $JavaExe -XshowSettings:properties -version 2>&1
        $javaExitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $savedErrorActionPreference
    }
    if ($javaExitCode -ne 0) {
        throw "NATIVE_JAVA_VERSION_MISMATCH: Java version check failed with exit code $javaExitCode."
    }
    $runtimeLine = $javaOutput | Select-String -Pattern '^\s*java\.runtime\.version\s*=\s*(.+)$' | Select-Object -First 1
    $vendorLine = $javaOutput | Select-String -Pattern '^\s*java\.vendor\s*=\s*(.+)$' | Select-Object -First 1
    if (-not $runtimeLine -or -not $vendorLine) {
        throw "NATIVE_JAVA_VERSION_MISMATCH: Java runtime properties were not available."
    }
    [pscustomobject]@{
        Version = $runtimeLine.Matches[0].Groups[1].Value.Trim()
        Vendor = $vendorLine.Matches[0].Groups[1].Value.Trim()
    }
}

function Resolve-BlueprintToCodeDevKitRoot {
    param(
        [string]$DevKitRoot,
        [Parameter(Mandatory)]
        [pscustomobject]$Config,
        [Parameter(Mandatory)]
        [string]$ProjectRoot
    )

    if ($DevKitRoot) {
        return [System.IO.Path]::GetFullPath($DevKitRoot)
    }
    if ($env:BLUEPRINT_TO_CODE_DEVKIT_ROOT) {
        return [System.IO.Path]::GetFullPath($env:BLUEPRINT_TO_CODE_DEVKIT_ROOT)
    }

    $contentRootFile = Join-Path $ProjectRoot "devkit_content_root.txt"
    if (Test-Path -LiteralPath $contentRootFile -PathType Leaf) {
        $contentRoot = (Get-Content -LiteralPath $contentRootFile -TotalCount 1 -Encoding UTF8).Trim().Trim('"', "'")
        if ($contentRoot) {
            $candidate = [System.IO.Path]::GetFullPath((Join-Path $contentRoot "..\..\.."))
            $candidateBinary = Join-Path $candidate $Config.shooterGame.binaryRelativePath
            if (Test-Path -LiteralPath $candidateBinary -PathType Leaf) {
                return $candidate
            }
        }
    }

    return [string]$Config.shooterGame.defaultDevKitRoot
}

function Resolve-NativeRecipeInputPaths {
    param(
        [string]$DllPath,
        [string]$PdbPath,
        [string]$DevKitRoot,
        [Parameter(Mandatory)]
        [pscustomobject]$Config,
        [Parameter(Mandatory)]
        [string]$ProjectRoot
    )

    if ([bool]$DllPath -xor [bool]$PdbPath) {
        throw "NATIVE_TOOL_MISSING: -DllPath and -PdbPath must be provided together."
    }
    if ($DllPath -and $PdbPath) {
        return [pscustomobject]@{
            DllPath = [System.IO.Path]::GetFullPath($DllPath)
            PdbPath = [System.IO.Path]::GetFullPath($PdbPath)
            Source = "explicit"
        }
    }

    $resolvedDevKitRoot = Resolve-BlueprintToCodeDevKitRoot `
        -DevKitRoot $DevKitRoot `
        -Config $Config `
        -ProjectRoot $ProjectRoot
    return [pscustomobject]@{
        DllPath = [System.IO.Path]::GetFullPath(
            (Join-Path $resolvedDevKitRoot $Config.shooterGame.binaryRelativePath)
        )
        PdbPath = [System.IO.Path]::GetFullPath(
            (Join-Path $resolvedDevKitRoot $Config.shooterGame.pdbRelativePath)
        )
        Source = "devkit"
    }
}

function Remove-NativeRunDirectory {
    param(
        [Parameter(Mandatory)]
        [string]$RunRoot,
        [Parameter(Mandatory)]
        [string]$TempBase
    )

    $resolvedTempBase = [System.IO.Path]::GetFullPath($TempBase).
        TrimEnd(
            [System.IO.Path]::DirectorySeparatorChar,
            [System.IO.Path]::AltDirectorySeparatorChar
        )
    $resolvedRunRoot = [System.IO.Path]::GetFullPath($RunRoot).
        TrimEnd(
            [System.IO.Path]::DirectorySeparatorChar,
            [System.IO.Path]::AltDirectorySeparatorChar
        )
    $requiredPrefix = $resolvedTempBase + [System.IO.Path]::DirectorySeparatorChar
    if ($resolvedRunRoot -eq $resolvedTempBase -or -not $resolvedRunRoot.StartsWith(
            $requiredPrefix,
            [System.StringComparison]::OrdinalIgnoreCase
        )) {
        throw "NATIVE_TEMP_PATH_INVALID: Refusing to remove a run path outside the native task temp root."
    }
    $runParent = [System.IO.Path]::GetDirectoryName($resolvedRunRoot)
    $runName = [System.IO.Path]::GetFileName($resolvedRunRoot)
    if (-not $runParent.Equals(
            $resolvedTempBase,
            [System.StringComparison]::OrdinalIgnoreCase
        ) -or $runName -notmatch '^[0-9a-f]{12}-[0-9a-f]{12}-[0-9]{8}-[0-9]{6}-[0-9a-f]{32}$') {
        throw "NATIVE_TEMP_PATH_INVALID: Refusing to remove a path that is not an exact native run directory."
    }
    if (Test-Path -LiteralPath $resolvedRunRoot -PathType Leaf) {
        throw "NATIVE_TEMP_PATH_INVALID: Refusing to remove a regular file in place of a native run directory."
    }
    if (Test-Path -LiteralPath $resolvedRunRoot -PathType Container) {
        $item = Get-Item -LiteralPath $resolvedRunRoot -Force
        if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "NATIVE_TEMP_PATH_INVALID: Refusing to recursively remove a reparse-point run directory."
        }
        $reparseChild = Get-ChildItem -LiteralPath $resolvedRunRoot -Directory -Force |
            Where-Object {
                ($_.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0
            } |
            Select-Object -First 1
        if ($reparseChild) {
            throw "NATIVE_TEMP_PATH_INVALID: Refusing to recurse through a reparse-point child directory."
        }
        Remove-Item -LiteralPath $resolvedRunRoot -Recurse -Force -ErrorAction Stop
    }
}
