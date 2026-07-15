@echo off
cd /d "%~dp0"
echo ===========================================
echo         SHOP INVENTORY UPDATE TOOL
echo ===========================================
echo.
echo 1. Pulling latest code changes...
git pull
echo.
if exist ".venv\Scripts\python.exe" (
  set PYTHON=.venv\Scripts\python.exe
  set PIP=.venv\Scripts\pip.exe
) else (
  set PYTHON=python
  set PIP=pip
)
echo 2. Installing any new python packages...
%PIP% install -r requirements.txt
echo.
echo 3. Running database schema migrations...
%PYTHON% manage.py migrate
echo.
echo ===========================================
echo Update completed successfully!
echo Press any key to close this window.
echo ===========================================
pause >nul
