@echo off
setlocal
cd /d "%~dp0"
py scripts\callout_dashboard.py --port 8770
if errorlevel 1 pause
