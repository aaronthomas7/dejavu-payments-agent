@echo off
setlocal
cd /d "%~dp0..\.."
set PYTHONUTF8=1
if not exist .venv\Scripts\activate.bat (echo Run setup.bat first. & pause & exit /b 1)
call .venv\Scripts\activate.bat
if exist data\replay_results.json (
  echo Continuing the previous replay...
  python scripts\replay.py --resume
) else (
  echo Replaying 6 weeks of exceptions from an empty memory bank. This takes 20-40 minutes - leave this window open.
  python scripts\replay.py --fresh
)
if errorlevel 1 (echo. & echo The replay stopped. Double-click replay.bat again to continue where it left off. & pause & exit /b 1)
python scripts\fill_content.py
echo.
echo Recording the clickable demo from this run - about 5-10 more minutes...
python scripts\export_demo.py
if errorlevel 1 (echo. & echo Recording the demo stopped. Double-click export_demo.bat to try again. & pause & exit /b 1)
echo.
echo ALL DONE. Results: data\replay_results.json, docs\learning_curve.png and the demo in docs\demo
echo Next: run.bat opens the app, publish.bat pushes everything to GitHub.
pause
