@echo off
setlocal EnableExtensions DisableDelayedExpansion
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_workbench.ps1" -Mode diagnostic
set "EXITCODE=%ERRORLEVEL%"
if /I not "%WORKBENCH_NO_PAUSE%"=="1" pause
endlocal & exit /b %EXITCODE%
