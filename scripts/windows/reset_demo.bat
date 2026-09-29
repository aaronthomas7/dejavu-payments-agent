@echo off
setlocal
echo Reopening the 10 open demo cases and removing their memories. run.bat must be running.
curl.exe -s -X POST http://localhost:8000/api/admin/reset-demo
echo.
pause
