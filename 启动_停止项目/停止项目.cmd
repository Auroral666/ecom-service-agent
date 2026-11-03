@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0.."
title Ecom Agent - Stop
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\scripts\project-services.ps1" -Action Stop
set "RESULT=%ERRORLEVEL%"
echo.
if /I not "%~1"=="--no-pause" pause
exit /b %RESULT%
