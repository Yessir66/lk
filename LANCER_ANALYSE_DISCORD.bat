@echo off
setlocal
cd /d "%~dp0"
py scripts\discord_solana_watch.py
pause
