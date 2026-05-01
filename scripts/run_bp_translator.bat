@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
set "SCRIPT=%SCRIPT_DIR%bp_clipboard_to_prompt.py"

where py >nul 2>nul
if %ERRORLEVEL% EQU 0 (
  py -3 "%SCRIPT%" %*
) else (
  python "%SCRIPT%" %*
)

if %ERRORLEVEL% NEQ 0 (
  echo.
  echo Failed. Copy Blueprint nodes with Ctrl+C, or pass --input path\to\BlueprintCopy.txt
)
echo.
if not "%BP_NO_PAUSE%"=="1" pause
