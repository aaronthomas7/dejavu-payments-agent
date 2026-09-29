@echo off
setlocal
cd /d "%~dp0..\.."
set PYTHONUTF8=1
if not exist .venv\Scripts\activate.bat (echo Run setup.bat first. & pause & exit /b 1)
call .venv\Scripts\activate.bat
echo Starting DejaVu at http://localhost:8000  - close this window to stop it.
start "" /b cmd /c "ping -n 5 127.0.0.1 >nul & start http://localhost:8000"
python -m uvicorn app.main:app --port 8000
pause
