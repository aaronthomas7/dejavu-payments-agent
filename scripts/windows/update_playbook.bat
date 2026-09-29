@echo off
setlocal
cd /d "%~dp0..\.."
set PYTHONUTF8=1
if not exist .venv\Scripts\activate.bat (echo Run setup.bat first. & pause & exit /b 1)
call .venv\Scripts\activate.bat
echo Re-recording the playbook and lessons from Hindsight - 1 to 4 minutes...
python scripts\export_demo.py --only playbook
pause
