@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\verify-all.ps1"
set "VERIFY_CODE=%ERRORLEVEL%"
if not "%~1"=="--no-pause" pause
exit /b %VERIFY_CODE%
