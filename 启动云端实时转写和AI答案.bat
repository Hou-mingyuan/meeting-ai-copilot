@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"

set "PYTHONUTF8=1"
set "PIP_DISABLE_PIP_VERSION_CHECK=1"
set "PIP_DEFAULT_TIMEOUT=120"
set "PIP_MIRROR_URL=https://mirrors.aliyun.com/pypi/simple"
set "PIP_MIRROR_HOST=mirrors.aliyun.com"

echo ============================================================
echo Cloud realtime transcription and AI streaming answer
echo ============================================================
echo Output folder: Desktop\实时监听
echo.

call :find_python
if errorlevel 1 call :install_python
if errorlevel 1 goto no_python

call :check_python
if errorlevel 1 call :install_python
if errorlevel 1 goto no_python

call :check_python
if errorlevel 1 goto no_python

call :ensure_venv
if errorlevel 1 goto venv_failed

call :ensure_deps
if errorlevel 1 goto deps_failed

echo.
echo Starting cloud realtime transcription...
if /I "%~1"=="--self-test" goto run_self_test
".venv\Scripts\python.exe" "src\cloud_asr_volcengine.py" --config "config.json"
goto after_run

:run_self_test
".venv\Scripts\python.exe" "src\cloud_asr_volcengine.py" --config "config.json" --test-asr-handshake

:after_run
echo.
echo Program exited.
pause
exit /b 0

:find_python
set "PY_EXE="
set "PY_ARGS="
where py >nul 2>nul
if errorlevel 1 goto find_python_cmd
py -3.11 -c "import sys" >nul 2>nul
if errorlevel 1 goto find_py312
set "PY_EXE=py"
set "PY_ARGS=-3.11"
exit /b 0

:find_py312
py -3.12 -c "import sys" >nul 2>nul
if errorlevel 1 goto find_python_cmd
set "PY_EXE=py"
set "PY_ARGS=-3.12"
exit /b 0

:find_python_cmd
where python >nul 2>nul
if errorlevel 1 exit /b 1
python -c "import sys" >nul 2>nul
if errorlevel 1 exit /b 1
set "PY_EXE=python"
set "PY_ARGS="
exit /b 0

:install_python
echo Python not found. Installing Python 3.11 with winget...
where winget >nul 2>nul
if errorlevel 1 exit /b 1
winget install -e --id Python.Python.3.11 --scope user --accept-package-agreements --accept-source-agreements
if errorlevel 1 exit /b 1
if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" goto use_python311_path
set "PY_EXE=python"
set "PY_ARGS="
exit /b 0

:use_python311_path
set "PY_EXE=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
set "PY_ARGS="
exit /b 0

:check_python
"%PY_EXE%" %PY_ARGS% -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
exit /b %errorlevel%

:ensure_venv
if exist ".venv\Scripts\python.exe" exit /b 0
echo [1/3] Creating virtual environment...
"%PY_EXE%" %PY_ARGS% -m venv .venv
exit /b %errorlevel%

:ensure_deps
echo [2/3] Checking dependencies...
".venv\Scripts\python.exe" -c "import numpy, soundcard, websockets, volcengine_audio" >nul 2>nul
if not errorlevel 1 goto deps_ok
echo [3/3] Installing dependencies...
".venv\Scripts\python.exe" -m pip install --upgrade pip -i "%PIP_MIRROR_URL%" --trusted-host "%PIP_MIRROR_HOST%" --retries 10 --timeout 120
".venv\Scripts\python.exe" -m pip install -r requirements.txt -i "%PIP_MIRROR_URL%" --trusted-host "%PIP_MIRROR_HOST%" --prefer-binary --retries 10 --timeout 120
if errorlevel 1 goto deps_official
exit /b 0

:deps_official
echo Mirror install failed. Retrying official PyPI...
".venv\Scripts\python.exe" -m pip install -r requirements.txt --prefer-binary --retries 5 --timeout 120
exit /b %errorlevel%

:deps_ok
echo Dependencies already installed.
exit /b 0

:no_python
echo.
echo ERROR: Python is not available and automatic installation failed.
echo Install Python 3.11 or 3.12 manually, then double click this file again.
echo https://www.python.org/downloads/windows/
pause
exit /b 1

:venv_failed
echo.
echo ERROR: Failed to create .venv.
pause
exit /b 1

:deps_failed
echo.
echo ERROR: Failed to install dependencies.
pause
exit /b 1
