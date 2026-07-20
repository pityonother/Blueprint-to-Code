@echo off
setlocal
cd /d "%~dp0"
set "PYTHON_EXE=%~dp0runtime\python\python.exe"
if not exist "%PYTHON_EXE%" (
  where python >nul 2>nul
  if errorlevel 1 (
    echo Could not find bundled Python or system Python.
    echo Please use the full package with runtime\python or install Python 3.10+.
    pause
    exit /b 1
  )
  set "PYTHON_EXE=python"
)
"%PYTHON_EXE%" "%~dp0scripts\diagnose_blueprint_tool.py" %*
set "DIAG_EXIT=%ERRORLEVEL%"
echo.
echo Diagnostic finished. Press any key to close this window.
pause >nul
exit /b %DIAG_EXIT%
