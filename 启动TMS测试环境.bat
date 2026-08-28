@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\windows\start_tms_local_test.ps1"
if errorlevel 1 echo TMS local test environment failed to start.
pause
