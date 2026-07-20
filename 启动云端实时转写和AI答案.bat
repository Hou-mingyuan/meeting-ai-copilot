@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"

set "PYTHONUTF8=1"
set "PIP_DISABLE_PIP_VERSION_CHECK=1"
set "APP_PYTHON="
set "APP_EXE="

if exist "%~dp0MeetingAICopilot.exe" set "APP_EXE=%~dp0MeetingAICopilot.exe"
if exist "%~dp0MeetingAICopilot\MeetingAICopilot.exe" set "APP_EXE=%~dp0MeetingAICopilot\MeetingAICopilot.exe"

if /I "%~1"=="--mock" goto run_mock
if /I "%~1"=="--real" goto run_real
if /I "%~1"=="--diagnose" goto run_diagnose
if /I "%~1"=="--devices" goto run_devices
if /I "%~1"=="--smoke-test" goto run_smoke

:menu
cls
echo ============================================================
echo Meeting AI Copilot
echo ============================================================
echo 1. 零密钥 Mock 演示（固定音频，不上传真实会议）
echo 2. 开始真实会议采集（需要 BYOK，启动前再次确认隐私）
echo 3. 配置与设备诊断
echo 4. 列出系统声音和麦克风设备
echo 5. 退出
echo.
choice /C 12345 /N /M "请选择 [1-5]: "
if errorlevel 5 exit /b 0
if errorlevel 4 goto run_devices
if errorlevel 3 goto run_diagnose
if errorlevel 2 goto run_real
goto run_mock

:prepare_runtime
if defined APP_EXE exit /b 0
if exist ".venv\Scripts\python.exe" (
    set "APP_PYTHON=%~dp0.venv\Scripts\python.exe"
    goto check_deps
)
where py >nul 2>nul
if not errorlevel 1 (
    py -3.11 -c "import sys" >nul 2>nul && set "BOOTSTRAP=py -3.11"
    if not defined BOOTSTRAP py -3.12 -c "import sys" >nul 2>nul && set "BOOTSTRAP=py -3.12"
)
if not defined BOOTSTRAP (
    where python >nul 2>nul
    if not errorlevel 1 set "BOOTSTRAP=python"
)
if not defined BOOTSTRAP goto offer_python
%BOOTSTRAP% -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" >nul 2>nul
if errorlevel 1 goto offer_python
echo [1/2] 正在创建隔离环境 .venv ...
%BOOTSTRAP% -m venv .venv
if errorlevel 1 goto runtime_failed
set "APP_PYTHON=%~dp0.venv\Scripts\python.exe"

:check_deps
"%APP_PYTHON%" -c "import numpy,soundcard,websockets,volcengine_audio" >nul 2>nul
if not errorlevel 1 exit /b 0
echo [2/2] 正在安装锁定依赖 ...
"%APP_PYTHON%" -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 goto runtime_failed
exit /b 0

:offer_python
echo.
echo 未检测到 Python 3.10+。推荐使用仓库发布的便携包，无需安装 Python。
where winget >nul 2>nul
if errorlevel 1 goto no_python
choice /C YN /N /M "是否通过 winget 安装 Python 3.11？[Y/N]: "
if errorlevel 2 goto no_python
winget install -e --id Python.Python.3.11 --scope user --accept-package-agreements --accept-source-agreements
if errorlevel 1 goto no_python
echo Python 已安装。请关闭此窗口后重新双击启动。
pause
exit /b 0

:run_mock
call :prepare_runtime
if errorlevel 1 exit /b %errorlevel%
echo.
echo [Mock] 固定音频 -^> Mock ASR -^> 问题检测 -^> Mock AI -^> 会话与导出
if defined APP_EXE (
    "%APP_EXE%" --mock-demo --fixture "tests\fixtures\meeting_question.wav"
) else (
    "%APP_PYTHON%" "src\cloud_asr_volcengine.py" --mock-demo --fixture "tests\fixtures\meeting_question.wav"
)
set "RUN_CODE=%errorlevel%"
if "%~1"=="" pause
exit /b %RUN_CODE%

:run_real
call :prepare_runtime
if errorlevel 1 exit /b %errorlevel%
if not exist "config.json" (
    copy /Y "config.example.json" "config.json" >nul
    echo 已创建 config.json。请填入自有 ASR/AI Key；也可改用环境变量。
    start "" /WAIT notepad.exe "config.json"
)
echo.
echo 程序不会静默录音；下一屏会显示采集内容、数据去向并要求输入 Y。
if defined APP_EXE (
    "%APP_EXE%" --config "config.json"
) else (
    "%APP_PYTHON%" "src\cloud_asr_volcengine.py" --config "config.json"
)
set "RUN_CODE=%errorlevel%"
if "%~1"=="" pause
exit /b %RUN_CODE%

:run_diagnose
call :prepare_runtime
if errorlevel 1 exit /b %errorlevel%
if defined APP_EXE (
    "%APP_EXE%" --config "config.example.json" --diagnose
) else (
    "%APP_PYTHON%" "src\cloud_asr_volcengine.py" --config "config.example.json" --diagnose
)
set "RUN_CODE=%errorlevel%"
if "%~1"=="" pause
exit /b %RUN_CODE%

:run_devices
call :prepare_runtime
if errorlevel 1 exit /b %errorlevel%
if defined APP_EXE (
    "%APP_EXE%" --list-devices
) else (
    "%APP_PYTHON%" "src\cloud_asr_volcengine.py" --list-devices
)
set "RUN_CODE=%errorlevel%"
if "%~1"=="" pause
exit /b %RUN_CODE%

:run_smoke
call :prepare_runtime
if errorlevel 1 exit /b %errorlevel%
if defined APP_EXE (
    "%APP_EXE%" --config "config.example.json" --smoke-test
) else (
    "%APP_PYTHON%" "src\cloud_asr_volcengine.py" --config "config.example.json" --smoke-test
)
exit /b %errorlevel%

:no_python
echo.
echo ERROR: 需要 Python 3.10+，或使用免 Python 的 Windows 便携包。
echo https://www.python.org/downloads/windows/
pause
exit /b 1

:runtime_failed
echo.
echo ERROR: Python 隔离环境或依赖安装失败。请运行“配置与设备诊断”查看信息。
pause
exit /b 1
