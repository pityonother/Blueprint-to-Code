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
    $javaExe = Join-Path $JavaHome "bin\java.exe"

    foreach ($requiredFile in @($ghidraRun, $analyzeHeadless, $javaExe)) {
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
