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
if defined BLUEPRINT_TO_CODE_DEVKIT_CONTENT_ROOT echo DevKit Content root: %BLUEPRINT_TO_CODE_DEVKIT_CONTENT_ROOT%
echo Closing old Blueprint Tool servers for this project...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$root=(Resolve-Path '%~dp0').Path.TrimEnd('\'); Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*blueprint_tool_server.py*' -and $_.CommandLine -like ('*' + $root + '*') } | ForEach-Object { try { Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop } catch {} }"
"%PYTHON_EXE%" "%~dp0scripts\blueprint_tool_server.py" --port 8765 --open
if errorlevel 1 pause
endlocal
