@echo off
setlocal
cd /d "%~dp0"
set "APP_PYTHON=%SETUP_ORDER_PYTHON%"
if not defined APP_PYTHON if exist "%~dp0.venv\Scripts\python.exe" set "APP_PYTHON=%~dp0.venv\Scripts\python.exe"
if not defined APP_PYTHON if exist "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" set "APP_PYTHON=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
if not defined APP_PYTHON set "APP_PYTHON=python"
"%APP_PYTHON%" run.py
echo.
pause
