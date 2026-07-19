@echo off
REM 一键 Mock 演示：mock 服务 + 业务闭环 + dry_run 压测（零密钥）
cd /d "%~dp0.."
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0demo-mock.ps1"
exit /b %ERRORLEVEL%
