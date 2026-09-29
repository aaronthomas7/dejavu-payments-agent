@echo off
setlocal
cd /d "%~dp0..\.."
set PYTHONUTF8=1
if not exist .venv\Scripts\activate.bat (echo Run setup.bat first. & pause & exit /b 1)
call .venv\Scripts\activate.bat
echo Recording the clickable demo from your real run. This takes about 5-10 minutes...
python scripts\export_demo.py
pause
