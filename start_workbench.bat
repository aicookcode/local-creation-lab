@echo off
setlocal EnableExtensions DisableDelayedExpansion
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_workbench.ps1" -Mode gui
set "EXITCODE=%ERRORLEVEL%"
if not "%EXITCODE%"=="0" (
  echo [ERROR] The workbench process could not be started.
  if /I not "%WORKBENCH_NO_PAUSE%"=="1" pause
)
endlocal & exit /b %EXITCODE%
