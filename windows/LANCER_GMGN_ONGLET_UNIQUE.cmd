@echo off
chcp 65001 >nul
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0OUVRIR_CHARTS_GMGN.ps1" -SingleTab
pause
