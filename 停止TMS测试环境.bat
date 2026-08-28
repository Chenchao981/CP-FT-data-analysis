@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\windows\stop_tms_local_test.ps1"
if errorlevel 1 echo TMS local test environment was not fully stopped.
pause
