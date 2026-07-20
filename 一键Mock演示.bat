@echo off
cd /d "%~dp0"
call "启动云端实时转写和AI答案.bat" --mock
exit /b %ERRORLEVEL%
