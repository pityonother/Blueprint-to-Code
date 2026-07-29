@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\native_analysis\Start-Ghidra.ps1"
if errorlevel 1 pause
