@echo off
setlocal
cd /d "%~dp0..\.."
set PYTHONUTF8=1
echo === DejaVu setup ===
if exist .venv\Scripts\python.exe goto venv_ready

rem Find Python 3.10+ (the py launcher picks the newest installed version, so an old 3.9 on PATH is fine)
set "PY="
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" <nul >nul 2>nul && set "PY=py -3"
if not defined PY python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" <nul >nul 2>nul && set "PY=python"
for %%V in (313 312 311 310 314) do if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python%%V\python.exe" set PY="%LOCALAPPDATA%\Programs\Python\Python%%V\python.exe"
for %%V in (313 312 311 310 314) do if not defined PY if exist "%ProgramFiles%\Python%%V\python.exe" set PY="%ProgramFiles%\Python%%V\python.exe"
if not defined PY (
  echo Python 3.10 or newer was not found - this PC only has an older Python.
  echo Install Python 3.12 by running this in PowerShell:
  echo.
  echo     winget install -e --id Python.Python.3.12
  echo.
  echo or download Python 3.12 from https://www.python.org/downloads/windows/
  echo Then double-click setup.bat again. The old Python can stay installed.
  pause
  exit /b 1
)
echo Using %PY%
%PY% --version
echo Creating a virtual environment...
%PY% -m venv .venv
if errorlevel 1 (echo Could not create the .venv folder. & pause & exit /b 1)

:venv_ready
call .venv\Scripts\activate.bat
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)"
if errorlevel 1 (echo The .venv folder was made with an old Python. Delete the .venv folder and run setup.bat again. & pause & exit /b 1)
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
