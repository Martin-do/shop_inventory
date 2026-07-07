@echo off
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  set PYTHON=.venv\Scripts\python.exe
) else (
  set PYTHON=python
)
start "Shop Inventory Server" %PYTHON% manage.py runserver 0.0.0.0:8010
timeout /t 3 /nobreak >nul
start http://127.0.0.1:8010/pos/
