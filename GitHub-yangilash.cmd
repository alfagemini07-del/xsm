@echo off
chcp 65001 >nul
set "PYTHONUTF8=1"
cd /d "%~dp0"
where py >nul 2>nul
if not errorlevel 1 (
  py -3 publish_server.py 
) else (
  python publish_server.py 
)
pause
