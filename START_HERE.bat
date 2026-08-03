@echo off
setlocal
cd /d "%~dp0"
if exist "%~dp0devkit_content_root.txt" (
  set /p BLUEPRINT_TO_CODE_DEVKIT_CONTENT_ROOT=<"%~dp0devkit_content_root.txt"
)
set "PYTHON_EXE=%~dp0runtime\python\python.exe"
if not exist "%PYTHON_EXE%" (
  where python >nul 2>nul
  if errorlevel 1 (
    echo Could not find bundled Python or system Python.
    echo Please install Python 3.10+ or use the full package with runtime\python.
    pause
    exit /b 1
  )
  set "PYTHON_EXE=python"
)
echo Starting Blueprint Tool Control Center...
if defined BLUEPRINT_TO_CODE_DEVKIT_CONTENT_ROOT echo DevKit Content root configured.
echo Closing old Blueprint Tool servers for this project...
set "BLUEPRINT_TO_CODE_LAUNCH_ROOT=%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$root=(Resolve-Path -LiteralPath $env:BLUEPRINT_TO_CODE_LAUNCH_ROOT).Path.TrimEnd('\'); Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -and $_.CommandLine -like '*blueprint_tool_server.py*' -and $_.CommandLine.IndexOf($root, [StringComparison]::OrdinalIgnoreCase) -ge 0 } | ForEach-Object { try { Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop } catch {} }"
set "BLUEPRINT_TO_CODE_LAUNCH_ROOT="
"%PYTHON_EXE%" "%~dp0scripts\blueprint_tool_server.py" --port 8765 --open
if errorlevel 1 pause
endlocal
