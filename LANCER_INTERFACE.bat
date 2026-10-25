@echo off
setlocal
cd /d "%~dp0"
py scripts\callout_dashboard.py
if errorlevel 1 pause
