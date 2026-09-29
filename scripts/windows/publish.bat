@echo off
setlocal
cd /d "%~dp0..\.."
where git >nul 2>nul || (echo Git is not installed. Get it from https://git-scm.com/download/win and run this again. & pause & exit /b 1)
git config user.email >nul 2>nul || (echo Set your git identity first:  git config --global user.name "Your Name"  and  git config --global user.email you@example.com & pause & exit /b 1)
set "MSG=%*"
if "%MSG%"=="" set "MSG=Add replay results, learning curve and recorded demo"
git add -A
git commit -m "%MSG%"
git push
pause
