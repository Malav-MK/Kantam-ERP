@echo off
rem Double-click installer for Kantam ERP. Asks for administrator rights, then runs Install-Kantam.ps1.
rem Extra options can be passed on the command line, e.g.  Install-Kantam.cmd -Port 8443 -Demo
net session >nul 2>&1
if %errorlevel% neq 0 (
  echo Requesting administrator rights...
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -ArgumentList '%*' -Verb RunAs"
  exit /b
)
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0Install-Kantam.ps1" %*
echo.
pause
