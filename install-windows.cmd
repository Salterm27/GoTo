@echo off
rem Double-click to install goto. Extra options pass through, e.g.: install-windows.cmd -Port 8080
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install-windows.ps1" %*
echo.
pause
