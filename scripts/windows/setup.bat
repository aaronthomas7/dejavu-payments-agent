@echo off
setlocal
cd /d "%~dp0..\.."
set PYTHONUTF8=1
echo === DejaVu setup ===
if exist .venv\Scripts\python.exe goto venv_ready
set "PY="
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul && set "PY=py -3"
if not defined PY python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul && set "PY=python"
if not defined PY (
  echo Python 3.10 or newer was not found.
  echo Install it from https://www.python.org/downloads/ - tick "Add python.exe to PATH" - then run this again.
  pause
  exit /b 1
)
echo Creating a virtual environment...
%PY% -m venv .venv
if errorlevel 1 (echo Could not create the .venv folder. & pause & exit /b 1)
:venv_ready
call .venv\Scripts\activate.bat
echo Installing packages, this takes a minute or two...
python -m pip install --upgrade pip -q
python -m pip install -r requirements.txt -q
if errorlevel 1 (echo Package install failed - see the error above. & pause & exit /b 1)
if not exist .env (
  copy .env.example .env >nul
  echo Created .env - paste your HINDSIGHT_API_KEY and GROQ_API_KEY into it, then save and close Notepad.
  notepad .env
)
echo.
python scripts\check_setup.py
echo.
pause
