@echo off
rem Double-click to uninstall goto. Add -Purge to also delete your links.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0uninstall-windows.ps1" %*
echo.
pause
